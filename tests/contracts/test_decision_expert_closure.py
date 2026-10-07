"""Offline closure of the assembled Decision Expert. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes.fake_anthropic_messages import (
    FakeAnthropicMessages,
    end_turn_response,
    stop_reason_response,
    tool_use_response,
)
from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_answer_renderer import DecisionRenderOutcome, DecisionRenderResult
from wilvor_ai.decision_claims import (
    CurrentPersistedLinkClaim,
    PersistedAssessmentStatus,
    PersistedCandidateClaim,
    PersistedCandidateStatusClaim,
    PersistedCapabilityEvidenceStatus,
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    PersistedEvaluationScope,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_evidence_verifier import (
    NOT_CURRENT_LIMITATION,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import DECISION_MODEL_DECISION_SCHEMA_VERSION
from wilvor_ai.decision_specialist import (
    MAX_IDENTICAL_TOOL_CALL_EXECUTIONS,
    MAX_INVALID_TOOL_CALL_CORRECTIONS,
    MAX_MODEL_TURNS,
    MAX_PERSISTED_AIRPORT_CALLS,
    MAX_TOOL_CALLS,
    DecisionSpecialist,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionDispatchOrigin,
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.decision_tools import (
    DecisionToolInvocation,
    DecisionToolsRuntime,
    decision_tool_schemas,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportCandidate,
    PersistedAirportEvidence,
)
from wilvor_ai.providers.anthropic_decision_messages import (
    AnthropicDecisionMessagesProvider,
)
from wilvor_ai.specialist_contracts import VerifierOutcome


AS_OF = "2026-09-29T00:00:00Z"
README = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "README.md"
)
_AUDIT_KEYS = (
    "tool_call_id",
    "correlation_id",
    "tables",
    "now_epoch",
    "query_timestamp_utc",
)
_TOOL_SIGNATURES = (
    ("get_current_decision_context", "aircraft_id"),
    ("get_current_risk_evidence", "aircraft_id"),
    ("get_current_recommendation", "aircraft_id"),
    ("get_persisted_airport_candidate_evidence", "recommendation_id"),
)
_CLAIM_RANKING_KEYS = frozenset(
    {
        "rank",
        "score",
        "scores",
        "total_score",
        "total_airport_score",
        "distance_nm",
        "eta_minutes",
    }
)
_FAILED_ANSWER = (
    "Wilvor could not verify the proposed decision claims against deterministic "
    "evidence, so no factual decision answer is being returned."
)


def _request() -> DecisionSpecialistRequest:
    return DecisionSpecialistRequest(
        user_text="status",
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id="ac-1",
    )


def _runtime() -> DecisionToolsRuntime:
    return DecisionToolsRuntime(
        tables=object(),
        now_epoch=1,
        query_timestamp_utc=AS_OF,
    )


def _packet(
    tool_call_id: str,
    scope: TemporalScope,
    limitations: tuple[str, ...] = (),
) -> Evidence:
    return Evidence(
        source="wilvor.decision.closure.test",
        source_records=(
            SourceRecord(
                record_id="ac-1",
                source_version=None,
                event_timestamp_utc=None,
            ),
        ),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=limitations,
        tool_call_id=tool_call_id,
        temporal_scope=scope,
    )


def _recommendation(recommendation_id: str) -> DecisionRecommendationEvidence:
    return DecisionRecommendationEvidence(
        recommendation_id=recommendation_id,
        primary_action_type=RecommendationActionType.MONITOR,
        advisory_authority=DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )


def _recommendation_set(ids: tuple[str, ...]) -> DecisionRecommendationSet:
    return DecisionRecommendationSet(
        current=tuple(_recommendation(item) for item in ids),
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if ids
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
    )


def _risk_evidence(presence: RiskPresence) -> DecisionEvidence:
    if presence is RiskPresence.ABSENT:
        risk = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
        gaps = (DecisionChainGap.RISK_ABSENT,)
    else:
        risk = DecisionRiskEvidence(
            presence=RiskPresence.PRESENT,
            risk_id="risk-1",
            risk_level=StoredRiskLevel.HIGH,
            risk_score=80,
        )
        gaps = ()
    return DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="ac-1",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        risk=risk,
        chain_gaps=gaps,
        limitation_codes=(),
    )


def _recommendation_evidence(ids: tuple[str, ...]) -> DecisionEvidence:
    if ids:
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id="ac-1",
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=DecisionRiskEvidence(
                presence=RiskPresence.PRESENT,
                risk_id="risk-1",
                risk_level=StoredRiskLevel.HIGH,
                risk_score=80,
            ),
            recommendations=_recommendation_set(ids),
            chain_gaps=(),
            limitation_codes=(),
        )
    return DecisionEvidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id="ac-1",
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        risk=DecisionRiskEvidence(presence=RiskPresence.ABSENT),
        recommendations=_recommendation_set(()),
        chain_gaps=(
            DecisionChainGap.RISK_ABSENT,
            DecisionChainGap.RECOMMENDATION_ABSENT,
        ),
        limitation_codes=(DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,),
    )


def _current_result(
    tool_name: str,
    aircraft_id: str,
    tool_call_id: str,
    evidence: DecisionEvidence,
) -> ToolResult:
    status = expected_decision_status(evidence)
    limitations = ()
    if status is ToolResultStatus.PARTIAL:
        limitations = ("Stored limitation.",)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=(_packet(tool_call_id, TemporalScope.CURRENT),),
        limitations=limitations,
        as_of_utc=AS_OF,
    )


def _candidate() -> PersistedAirportCandidate:
    return PersistedAirportCandidate(
        airport_id="KDEN",
        airport_assessment_id="aa-1",
        risk_id="risk-1",
        assessment_status="COMPLETE",
        route_safety_status="UNAVAILABLE",
        runway_evidence_status="UNAVAILABLE",
        congestion_evidence_status="UNAVAILABLE",
        created_at_utc=AS_OF,
        created_at_epoch=1,
        expires_at_epoch=2,
        evaluation_version="ruleset-1",
        assessment_ruleset_version="ruleset-1",
        schema_version="wilvor.airport_assessment.v1",
        distance_nm=40,
        eta_minutes=20,
        candidate_reason="Within diversion search radius.",
        known_limitations=("Route hazard evaluation is not implemented yet.",),
        rank=1,
        total_airport_score=80,
        distance_score=70,
        weather_score=60,
        taf_score=50,
    )


def _persisted_result(
    recommendation_id: str,
    tool_call_id: str,
    *,
    complete: bool,
) -> ToolResult:
    payload = PersistedAirportEvidence(
        recommendation_id=recommendation_id,
        airport_evaluation_id="eval-1" if complete else None,
        candidates=(_candidate(),) if complete else (),
    )
    return ToolResult(
        tool_name="get_persisted_airport_candidate_evidence",
        tool_call_id=tool_call_id,
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.PERSISTED,
        data=payload.to_dict(),
        evidence=(
            _packet(
                tool_call_id,
                TemporalScope.PERSISTED,
                (NOT_CURRENT_LIMITATION,),
            ),
        ),
        as_of_utc=AS_OF,
        limitations=(),
    )


def _final(claims: list[dict]) -> dict:
    return {
        "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
        "kind": ModelDecisionKind.FINAL_CLAIMS.value,
        "claims": claims,
    }


def _risk_present(level: str, score: int) -> dict:
    return {
        "kind": "RISK_PRESENT",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "encounter_id": None,
        "risk_id": "risk-1",
        "risk_level": level,
        "risk_score": score,
    }


def _risk_absent() -> dict:
    return {
        "kind": "RISK_ABSENT",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "encounter_id": None,
    }


def _recommendation_claim(ids: list[str], absence: str) -> dict:
    return {
        "kind": "RECOMMENDATION_SET",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "recommendation_ids": ids,
        "absence_state": absence,
    }


def _snapshot_texts(request: dict) -> list[str]:
    texts: list[str] = []
    for message in request["messages"]:
        text = message["content"][0]["text"]
        if '"evidence_snapshots"' in text:
            texts.append(text)
    return texts


def _assert_snapshots_omit_audit_fields(client: FakeAnthropicMessages) -> None:
    snapshots = [
        text
        for request in client.requests
        for text in _snapshot_texts(request)
    ]
    assert snapshots
    for text in snapshots:
        for key in _AUDIT_KEYS:
            assert key not in text


def _run(monkeypatch, script, handler):
    class _Adapter:
        def __init__(self, runtime) -> None:
            self.runtime = runtime

        def invoke(self, tool_name, arguments, *, tool_call_id):
            raw = handler(tool_name, arguments, tool_call_id)
            return DecisionToolInvocation(
                raw_tool_result=raw,
                model_projection=project_decision_tool_result(raw),
            )

    monkeypatch.setattr(
        "wilvor_ai.decision_specialist.DecisionToolsAdapter",
        _Adapter,
    )
    client = FakeAnthropicMessages(script)
    result = DecisionSpecialist(
        provider=AnthropicDecisionMessagesProvider(client=client)
    ).run(_request(), _runtime())
    return result, client


def _risk_handler(presence: RiskPresence):
    evidence = _risk_evidence(presence)

    def handle(tool_name, arguments, tool_call_id):
        assert tool_name == "get_current_risk_evidence"
        assert arguments == {"aircraft_id": "ac-1"}
        return _current_result(tool_name, "ac-1", tool_call_id, evidence)

    return handle


def _recommendation_handler(ids: tuple[str, ...], *, complete_persisted: bool):
    evidence = _recommendation_evidence(ids)

    def handle(tool_name, arguments, tool_call_id):
        if tool_name == "get_current_recommendation":
            assert arguments == {"aircraft_id": "ac-1"}
            return _current_result(tool_name, "ac-1", tool_call_id, evidence)
        assert tool_name == "get_persisted_airport_candidate_evidence"
        return _persisted_result(
            arguments["recommendation_id"],
            tool_call_id,
            complete=complete_persisted,
        )

    return handle


def test_execution_bounds_stay_at_the_closed_values():
    assert MAX_MODEL_TURNS == 4
    assert MAX_TOOL_CALLS == 4
    assert MAX_PERSISTED_AIRPORT_CALLS == 3
    assert MAX_INVALID_TOOL_CALL_CORRECTIONS == 1
    assert MAX_IDENTICAL_TOOL_CALL_EXECUTIONS == 1


def test_catalog_is_exactly_the_four_decision_tools():
    schemas = decision_tool_schemas()
    assert [
        (schema.name, schema.input_fields[0].name) for schema in schemas
    ] == list(_TOOL_SIGNATURES)
    names = {schema.name for schema in schemas}
    assert "list_historical_encounters" not in names
    assert "get_live_aircraft_state" not in names


def test_scenario_a_current_high_risk_is_verified_and_rendered(monkeypatch):
    result, client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"}),
                extra_text="MODEL_PROSE_SENTINEL",
            ),
            end_turn_response(_final([_risk_present("HIGH", 80)])),
        ),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert result.status is DecisionSpecialistRunStatus.COMPLETED
    assert result.verification.outcome is VerifierOutcome.PASSED
    assert result.render.outcome is DecisionRenderOutcome.FACTUAL
    assert isinstance(result.proposed_claims[0], RiskPresentClaim)
    assert "stored current risk level" in result.render.answer
    assert "HIGH" in result.render.answer
    encoded = json.dumps(result.to_dict())
    assert "MODEL_PROSE_SENTINEL" not in result.render.answer
    assert "MODEL_PROSE_SENTINEL" not in encoded
    _assert_snapshots_omit_audit_fields(client)
    tools = client.requests[0]["tools"]
    assert [item["name"] for item in tools] == [name for name, _field in _TOOL_SIGNATURES]


def test_scenario_b_risk_absence_stays_distinct_from_low(monkeypatch):
    legitimate, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(_final([_risk_absent()])),
        ),
        _risk_handler(RiskPresence.ABSENT),
    )
    assert legitimate.verification.outcome is VerifierOutcome.PASSED
    assert legitimate.render.outcome is DecisionRenderOutcome.FACTUAL
    assert isinstance(legitimate.proposed_claims[0], RiskAbsentClaim)
    assert "Risk absence is not a LOW risk classification." in legitimate.render.answer

    adversarial, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(_final([_risk_present("LOW", 10)])),
        ),
        _risk_handler(RiskPresence.ABSENT),
    )
    assert adversarial.status is DecisionSpecialistRunStatus.COMPLETED
    assert adversarial.verification.outcome is VerifierOutcome.FAILED
    assert adversarial.render.outcome is DecisionRenderOutcome.VERIFICATION_FAILED
    assert adversarial.render.answer == _FAILED_ANSWER
    assert "LOW" not in adversarial.render.answer


def test_scenario_c_missing_recommendation_does_not_become_monitor(monkeypatch):
    legitimate, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_recommendation", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(
                _final(
                    [
                        _recommendation_claim(
                            [],
                            "ABSENT_FROM_CURRENT_CANDIDATES",
                        )
                    ]
                )
            ),
        ),
        _recommendation_handler((), complete_persisted=False),
    )
    assert legitimate.verification.outcome is VerifierOutcome.PASSED
    assert legitimate.render.outcome is DecisionRenderOutcome.FACTUAL
    assert isinstance(legitimate.proposed_claims[0], RecommendationSetClaim)
    assert legitimate.proposed_claims[0].recommendation_ids == ()
    assert (
        legitimate.proposed_claims[0].absence_state
        is DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    assert "This is not a MONITOR recommendation." in legitimate.render.answer

    adversarial, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_recommendation", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(
                _final(
                    [
                        {
                            "kind": "RECOMMENDATION_ACTION",
                            "evidence_ref": "de-1",
                            "evidence_scope": "CURRENT",
                            "recommendation_id": "rec-1",
                            "primary_action_type": "MONITOR",
                            "advisory_authority": "ADVISORY_ONLY",
                        }
                    ]
                )
            ),
        ),
        _recommendation_handler((), complete_persisted=False),
    )
    assert adversarial.status is DecisionSpecialistRunStatus.COMPLETED
    assert adversarial.verification.outcome is VerifierOutcome.FAILED
    assert adversarial.render.outcome is DecisionRenderOutcome.VERIFICATION_FAILED
    assert isinstance(adversarial.proposed_claims[0], RecommendationActionClaim)
    assert "MONITOR" not in adversarial.render.answer


def test_scenario_d_multiple_recommendations_fan_out_without_a_winner(monkeypatch):
    result, client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_recommendation", {"aircraft_id": "ac-1"})
            ),
            tool_use_response(
                (
                    "get_persisted_airport_candidate_evidence",
                    {"recommendation_id": "rec-2"},
                )
            ),
            end_turn_response(
                _final([_recommendation_claim(["rec-1", "rec-2"], "PRESENT")])
            ),
        ),
        _recommendation_handler(("rec-1", "rec-2"), complete_persisted=False),
    )
    snapshot = "\n".join(_snapshot_texts(client.requests[1]))
    assert "rec-1" in snapshot
    assert "rec-2" in snapshot
    assert "winner" not in snapshot
    assert "preferred" not in snapshot
    _assert_snapshots_omit_audit_fields(client)
    fanout = result.invocations[1:]
    assert [item.executed_arguments["recommendation_id"] for item in fanout] == [
        "rec-1",
        "rec-2",
    ]
    assert all(
        item.dispatch_origin is DecisionDispatchOrigin.DETERMINISTIC_PERSISTED_FANOUT
        for item in fanout
    )
    assert all(
        item.requested_arguments == {"recommendation_id": "rec-2"} for item in fanout
    )
    assert result.verification.outcome is VerifierOutcome.PASSED
    assert result.render.outcome is DecisionRenderOutcome.FACTUAL
    assert result.proposed_claims[0].recommendation_ids == ("rec-1", "rec-2")
    assert "rec-1 and rec-2" in result.render.answer
    assert "No recommendation is selected by this answer." in result.render.answer


def test_scenario_e_persisted_complete_does_not_select_an_airport(monkeypatch):
    result, client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_recommendation", {"aircraft_id": "ac-1"})
            ),
            tool_use_response(
                (
                    "get_persisted_airport_candidate_evidence",
                    {"recommendation_id": "rec-1"},
                )
            ),
            end_turn_response(
                _final(
                    [
                        _recommendation_claim(["rec-1"], "PRESENT"),
                        {
                            "kind": "PERSISTED_EVALUATION",
                            "evidence_ref": "de-2",
                            "evidence_scope": "PERSISTED",
                            "recommendation_id": "rec-1",
                            "airport_evaluation_id": "eval-1",
                            "evaluation_scope": "PERSISTED_EVALUATION_EVIDENCE",
                        },
                        {
                            "kind": "PERSISTED_CANDIDATE_STATUS",
                            "evidence_ref": "de-2",
                            "evidence_scope": "PERSISTED",
                            "recommendation_id": "rec-1",
                            "candidate_count": 1,
                            "collection_reason": None,
                        },
                        {
                            "kind": "PERSISTED_CANDIDATE",
                            "evidence_ref": "de-2",
                            "evidence_scope": "PERSISTED",
                            "recommendation_id": "rec-1",
                            "airport_id": "KDEN",
                            "airport_assessment_id": "aa-1",
                            "assessment_status": "COMPLETE",
                            "route_safety_status": "UNAVAILABLE",
                            "runway_evidence_status": "UNAVAILABLE",
                            "congestion_evidence_status": "UNAVAILABLE",
                        },
                        {
                            "kind": "CURRENT_PERSISTED_LINK",
                            "current_evidence_ref": "de-1",
                            "persisted_evidence_ref": "de-2",
                            "recommendation_id": "rec-1",
                        },
                    ]
                )
            ),
        ),
        _recommendation_handler(("rec-1",), complete_persisted=True),
    )
    assert result.verification.outcome is VerifierOutcome.PASSED
    assert result.render.outcome is DecisionRenderOutcome.FACTUAL
    evaluation = result.proposed_claims[1]
    assert isinstance(evaluation, PersistedEvaluationClaim)
    assert (
        evaluation.evaluation_scope
        is PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
    )
    assert isinstance(result.proposed_claims[2], PersistedCandidateStatusClaim)
    candidate = result.proposed_claims[3]
    assert isinstance(candidate, PersistedCandidateClaim)
    assert candidate.evidence_scope is TemporalScope.PERSISTED
    assert candidate.assessment_status is PersistedAssessmentStatus.COMPLETE
    assert (
        candidate.route_safety_status
        is PersistedCapabilityEvidenceStatus.UNAVAILABLE
    )
    link = result.proposed_claims[4]
    assert isinstance(link, CurrentPersistedLinkClaim)
    assert link.current_evidence_ref == "de-1"
    assert link.persisted_evidence_ref == "de-2"
    assert link.recommendation_id == "rec-1"
    persisted_snapshot = "\n".join(_snapshot_texts(client.requests[2]))
    assert '"evidence_ref":"de-2"' in persisted_snapshot or '"evidence_ref": "de-2"' in persisted_snapshot
    assert "rank" in persisted_snapshot
    assert "distance_nm" in persisted_snapshot
    _assert_snapshots_omit_audit_fields(client)
    claim_keys = {
        key
        for claim in result.proposed_claims
        for key in claim.to_dict()
    }
    assert claim_keys.isdisjoint(_CLAIM_RANKING_KEYS)
    answer = result.render.answer
    assert (
        "COMPLETE does not mean a safe airport, current suitability, "
        "a selected airport, or a selected diversion."
    ) in answer
    assert "Route-safety evidence is unavailable." in answer
    assert "Runway evidence is unavailable." in answer
    assert "Congestion evidence is unavailable." in answer
    assert "Persisted airport-evaluation evidence is not current airport suitability." in answer
    for phrase in (
        "best airport",
        "preferred airport",
        "safest airport",
        "cleared",
    ):
        assert phrase not in answer.casefold()


def test_scenario_f_malformed_provider_output_fails_closed(monkeypatch):
    result, _client = _run(
        monkeypatch,
        (
            {
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-4-6",
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": "not-json SECRET_PROVIDER_PROSE"}
                ],
            },
        ),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.runtime_errors == (DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,)
    assert result.verification is None
    assert result.render is None
    encoded = json.dumps(result.to_dict())
    assert "SECRET_PROVIDER_PROSE" not in encoded
    assert "malformed_terminal_json" not in encoded


def test_scenario_g_completed_run_can_still_fail_verification(monkeypatch):
    result, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"})
            ),
            end_turn_response(_final([_risk_present("LOW", 10)])),
        ),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert result.status is DecisionSpecialistRunStatus.COMPLETED
    assert result.verification.outcome is VerifierOutcome.FAILED
    assert result.render.outcome is DecisionRenderOutcome.VERIFICATION_FAILED
    assert result.status is not VerifierOutcome.PASSED


def test_scenario_h_unsupported_stays_distinct_from_refusal(monkeypatch):
    unsupported, _client = _run(
        monkeypatch,
        (
            end_turn_response(
                {
                    "schema_version": DECISION_MODEL_DECISION_SCHEMA_VERSION,
                    "kind": "UNSUPPORTED",
                    "unsupported_reason": "OUT_OF_CATALOG",
                }
            ),
        ),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert unsupported.status is DecisionSpecialistRunStatus.UNSUPPORTED
    assert unsupported.unsupported_reason.value == "OUT_OF_CATALOG"
    assert DecisionRuntimeErrorCode.REFUSAL not in unsupported.runtime_errors
    assert unsupported.render is None

    refusal, _client = _run(
        monkeypatch,
        (stop_reason_response("refusal"),),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert refusal.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert refusal.runtime_errors == (DecisionRuntimeErrorCode.REFUSAL,)
    assert refusal.render is None
    assert refusal.status is not unsupported.status


def test_scenario_i_final_artifacts_reject_valid_but_wrong_replacements(monkeypatch):
    result, _client = _run(
        monkeypatch,
        (
            tool_use_response(
                ("get_current_risk_evidence", {"aircraft_id": "ac-1"}),
                extra_text="MODEL_PROSE_SENTINEL",
            ),
            end_turn_response(_final([_risk_present("HIGH", 80)])),
        ),
        _risk_handler(RiskPresence.PRESENT),
    )
    assert DecisionSpecialistRunResult.from_dict(result.to_dict()) == result
    wrong_verification = verify_decision_evidence(
        (
            RiskPresentClaim(
                "de-1",
                TemporalScope.CURRENT,
                "risk-1",
                StoredRiskLevel.LOW,
                10,
            ),
        ),
        result.evidence_bindings,
    )
    assert wrong_verification.outcome is VerifierOutcome.FAILED
    assert wrong_verification != result.verification
    tampered_verification = result.to_dict()
    tampered_verification["verification"] = wrong_verification.to_dict()
    with pytest.raises(ContractValidationError, match="verification_integrity_mismatch"):
        DecisionSpecialistRunResult.from_dict(tampered_verification)

    tampered_render = result.to_dict()
    replacement = DecisionRenderResult(
        DecisionRenderOutcome.FACTUAL,
        "A different factual answer that is not the deterministic render.",
    )
    assert replacement != result.render
    tampered_render["render"] = {
        "outcome": replacement.outcome.value,
        "answer": replacement.answer,
    }
    with pytest.raises(ContractValidationError, match="render_integrity_mismatch"):
        DecisionSpecialistRunResult.from_dict(tampered_render)


def test_decision_expert_readme_section_names_the_closed_boundary():
    text = README.read_text(encoding="utf-8")
    start = text.index("## Decision Expert complete")
    rest = text[start + len("## Decision Expert complete") :]
    section = " ".join(
        text[
            start : start + len("## Decision Expert complete") + rest.index("\n## ")
        ].split()
    )
    for phrase in (
        "get_current_decision_context",
        "get_current_risk_evidence",
        "get_current_recommendation",
        "get_persisted_airport_candidate_evidence",
        "aircraft_id",
        "recommendation_id",
        "DE3 controls execution",
        "DE1 verifies",
        "DE2 renders",
        "AnthropicDecisionMessagesProvider",
        "claude-sonnet-4-6",
        "Master Agent remains future work",
        "Agent API remains future work",
        "is not complete",
    ):
        assert phrase in section
    assert "AI Operations Copilot is complete" not in section
