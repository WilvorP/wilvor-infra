"""Bounded Decision specialist loop. No AWS and no model provider."""

from __future__ import annotations

import json

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import AircraftIdentityClaim
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionChainGap,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    HazardSourceVersionLink,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
)
from wilvor_ai.decision_evidence_verifier import NOT_CURRENT_LIMITATION
from wilvor_ai.decision_model_contracts import (
    DecisionModelDecision,
    DecisionUnsupportedReason,
)
from wilvor_ai.decision_specialist import (
    DECISION_SPECIALIST_INSTRUCTION_REF,
    MAX_IDENTICAL_TOOL_CALL_EXECUTIONS,
    MAX_INVALID_TOOL_CALL_CORRECTIONS,
    MAX_MODEL_TURNS,
    MAX_PERSISTED_AIRPORT_CALLS,
    MAX_TOOL_CALLS,
    DecisionSpecialist,
    SequentialDecisionToolCallIdFactory,
    canonical_tool_call_key,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionCollectionPartialReason,
    DecisionDispatchOrigin,
    DecisionRuntimeErrorCode,
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.decision_tools import DecisionToolInvocation, DecisionToolsRuntime
from wilvor_ai.model_contracts import (
    ModelDecisionKind,
    ProposedToolCall,
    ValidationFeedbackCode,
)
from wilvor_ai.persisted_airport_contracts import PersistedAirportEvidence


AS_OF = "2026-09-29T00:00:00Z"
AIRCRAFT = "ac-1"
CONTEXT = "get_current_decision_context"
RISK = "get_current_risk_evidence"
RECOMMENDATION = "get_current_recommendation"
PERSISTED = "get_persisted_airport_candidate_evidence"


class ScriptedProvider:
    def __init__(self, decisions) -> None:
        self._decisions = list(decisions)
        self.turns = []

    def complete(self, turn):
        self.turns.append(turn)
        if not self._decisions:
            raise AssertionError("unexpected provider turn")
        item = self._decisions.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _runtime() -> DecisionToolsRuntime:
    return DecisionToolsRuntime(
        tables=object(),
        now_epoch=1,
        query_timestamp_utc=AS_OF,
    )


def _request(**overrides) -> DecisionSpecialistRequest:
    values = {
        "user_text": "Summarize the current decision evidence.",
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": AIRCRAFT,
        "request_id": "req-1",
    }
    values.update(overrides)
    return DecisionSpecialistRequest(**values)


def _calls(*pairs: tuple[str, dict]) -> DecisionModelDecision:
    return DecisionModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=tuple(ProposedToolCall(name, arguments) for name, arguments in pairs),
    )


def _claims(*claims) -> DecisionModelDecision:
    return DecisionModelDecision(kind=ModelDecisionKind.FINAL_CLAIMS, claims=claims)


def _identity(ref: str = "de-1", aircraft_id: str = AIRCRAFT) -> AircraftIdentityClaim:
    return AircraftIdentityClaim(ref, TemporalScope.CURRENT, aircraft_id)


def _packet(tool_call_id: str, scope: TemporalScope, limitations: tuple[str, ...] = ()) -> Evidence:
    return Evidence(
        source="wilvor.decision.specialist.test",
        source_records=(
            SourceRecord(record_id="row-1", source_version=None, event_timestamp_utc=None),
        ),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.FRESH,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=limitations,
        tool_call_id=tool_call_id,
        temporal_scope=scope,
    )


def _present_risk(encounter_id: str | None = None) -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id="risk-1",
        encounter_id=encounter_id,
        risk_level=StoredRiskLevel.HIGH,
        risk_score=80,
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


def _hazard() -> HazardSourceVersionLink:
    return HazardSourceVersionLink(
        hazard_id="haz-1",
        state=DecisionReportedLinkState.PRESENT,
        persisted_source_version="sv-1",
        current_source_version="sv-1",
    )


def _encounter(encounter_id: str, recommendation_id: str) -> DecisionEncounterLink:
    return DecisionEncounterLink(
        encounter_id=encounter_id,
        projection_id="proj-1",
        aircraft_state_version="state-1",
        hazard=_hazard(),
        risk=_present_risk(encounter_id),
        recommendations=_recommendation_set((recommendation_id,)),
    )


def _current_evidence(tool_name: str, aircraft_id: str, profile: dict) -> DecisionEvidence:
    if tool_name == CONTEXT:
        return DecisionEvidence(
            kind=DecisionEvidenceKind.DECISION_CONTEXT,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id=aircraft_id,
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            aircraft_state_version="state-1",
            encounters=(_encounter("enc-1", "rec-1"),),
            chain_gaps=(),
            limitation_codes=(),
        )
    if tool_name == RISK:
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RISK_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id=aircraft_id,
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            risk=_present_risk(),
            chain_gaps=(),
            limitation_codes=(),
        )
    if profile.get("not_in_set"):
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=False,
            projection_state=DecisionReportedLinkState.MISSING,
            risk=_present_risk(),
            recommendations=_recommendation_set(("rec-9",)),
            chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
            limitation_codes=(),
        )
    if profile.get("empty"):
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id=aircraft_id,
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
    encounters = profile.get("encounters")
    if encounters:
        return DecisionEvidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            evaluation_state=DecisionEvaluationState.ESTABLISHED,
            aircraft_in_current_set=True,
            aircraft_id=aircraft_id,
            projection_state=DecisionReportedLinkState.PRESENT,
            projection_id="proj-1",
            aircraft_state_version="state-1",
            encounters=tuple(
                _encounter(encounter_id, recommendation_id)
                for encounter_id, recommendation_id in encounters
            ),
            chain_gaps=(),
            limitation_codes=(),
        )
    return DecisionEvidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        evaluation_state=DecisionEvaluationState.ESTABLISHED,
        aircraft_in_current_set=True,
        aircraft_id=aircraft_id,
        projection_state=DecisionReportedLinkState.PRESENT,
        projection_id="proj-1",
        risk=_present_risk(),
        recommendations=_recommendation_set(tuple(profile.get("ids", ("rec-1",)))),
        chain_gaps=(),
        limitation_codes=(),
    )


def _tool_result(tool_name: str, arguments: dict, tool_call_id: str, profile: dict) -> ToolResult:
    if tool_name == PERSISTED:
        payload = PersistedAirportEvidence(
            recommendation_id=arguments["recommendation_id"],
            airport_evaluation_id=None,
            candidates=(),
        )
        return ToolResult(
            tool_name=tool_name,
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
    evidence = _current_evidence(tool_name, arguments["aircraft_id"], profile)
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


def _install(monkeypatch, profile: dict, *, fail_on: int | None = None):
    calls = []

    class FakeAdapter:
        def __init__(self, runtime) -> None:
            self.runtime = runtime

        def invoke(self, tool_name, arguments, *, tool_call_id):
            calls.append((tool_name, dict(arguments), tool_call_id))
            if fail_on is not None and len(calls) == fail_on:
                raise RuntimeError("adapter-secret-text")
            raw = _tool_result(tool_name, arguments, tool_call_id, profile)
            return DecisionToolInvocation(
                raw_tool_result=raw,
                model_projection=project_decision_tool_result(raw),
            )

    monkeypatch.setattr(
        "wilvor_ai.decision_specialist.DecisionToolsAdapter",
        FakeAdapter,
    )
    return calls


def _run(monkeypatch, decisions, **kwargs):
    profile = kwargs.pop("profile", {})
    fail_on = kwargs.pop("fail_on", None)
    factory = kwargs.pop("factory", SequentialDecisionToolCallIdFactory())
    calls = _install(monkeypatch, profile, fail_on=fail_on)
    provider = ScriptedProvider(decisions)
    specialist = DecisionSpecialist(provider=provider, tool_call_id_factory=factory)
    result = specialist.run(_request(**kwargs), _runtime())
    return result, calls, provider


def test_bounds_are_the_approved_decision_limits():
    assert MAX_MODEL_TURNS == 4
    assert MAX_TOOL_CALLS == 4
    assert MAX_PERSISTED_AIRPORT_CALLS == 3
    assert MAX_INVALID_TOOL_CALL_CORRECTIONS == 1
    assert MAX_IDENTICAL_TOOL_CALL_EXECUTIONS == 1
    assert DECISION_SPECIALIST_INSTRUCTION_REF == "wilvor.decision.specialist.v1"


def test_turn_carries_the_instruction_token_and_not_the_request_id(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [_claims(_identity())],
    )
    turn = provider.turns[0]
    assert turn.instruction_ref == DECISION_SPECIALIST_INSTRUCTION_REF
    payload = turn.to_dict()
    assert "request_id" not in payload
    assert "req-1" not in json.dumps(payload)
    assert {item.name for item in turn.tools} == {CONTEXT, RISK, RECOMMENDATION, PERSISTED}
    assert result.status is DecisionSpecialistRunStatus.COMPLETED
    assert calls == []


def test_user_text_is_not_a_target(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls((CONTEXT, {"aircraft_id": "N999"})),
            _claims(_identity()),
        ],
        user_text="Look at aircraft N999",
    )
    assert calls == []
    assert provider.turns[1].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT
    assert provider.turns[1].validation_feedback.argument_name == "aircraft_id"
    assert result.executed_tool_call_count == 0


def test_two_persisted_triggers_reject_the_whole_batch(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls(
                (PERSISTED, {"recommendation_id": "rec-1"}),
                (PERSISTED, {"recommendation_id": "rec-2"}),
            ),
            _claims(_identity()),
        ],
    )
    assert calls == []
    feedback = provider.turns[1].validation_feedback
    assert feedback.code is ValidationFeedbackCode.DUPLICATE_TOOL_CALL
    assert feedback.tool_name == PERSISTED
    assert result.status is not DecisionSpecialistRunStatus.INVALID_REQUEST
    assert result.validation_feedback[0].code is ValidationFeedbackCode.DUPLICATE_TOOL_CALL


def test_second_invalid_batch_is_invalid_request(monkeypatch):
    result, calls, _provider = _run(
        monkeypatch,
        [
            _calls((CONTEXT, {"aircraft_id": "other"})),
            _calls(("missing_tool", {"aircraft_id": AIRCRAFT})),
        ],
    )
    assert calls == []
    assert result.status is DecisionSpecialistRunStatus.INVALID_REQUEST
    assert result.verification is None
    assert result.render is None


def test_same_turn_recommendation_and_persisted_executes_nothing(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls(
                (RECOMMENDATION, {"aircraft_id": AIRCRAFT}),
                (PERSISTED, {"recommendation_id": "rec-1"}),
            ),
            _claims(_identity()),
        ],
    )
    assert calls == []
    assert provider.turns[1].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT
    assert result.executed_tool_call_count == 0


def test_mixed_batch_over_expanded_budget_executes_nothing(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls(
                (RISK, {"aircraft_id": AIRCRAFT}),
                (PERSISTED, {"recommendation_id": "rec-1"}),
            ),
            _claims(_identity()),
        ],
        profile={"ids": ("rec-1", "rec-2", "rec-3")},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION]
    assert provider.turns[2].validation_feedback.code is (
        ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED
    )
    assert result.collection_partial_reason is (
        DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET
    )
    assert result.status is DecisionSpecialistRunStatus.PARTIAL
    assert result.render.outcome.value == "FACTUAL"
    assert result.verification.outcome.value == "PASSED"


def test_mixed_batch_that_fits_executes_current_then_full_fanout(monkeypatch):
    result, calls, _provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls(
                (RISK, {"aircraft_id": AIRCRAFT}),
                (PERSISTED, {"recommendation_id": "rec-2"}),
            ),
            _claims(_identity()),
        ],
        profile={"ids": ("rec-2", "rec-1")},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION, RISK, PERSISTED, PERSISTED]
    assert [item[1] for item in calls[2:]] == [
        {"recommendation_id": "rec-1"},
        {"recommendation_id": "rec-2"},
    ]
    fanout = result.invocations[2:]
    assert all(
        item.dispatch_origin is DecisionDispatchOrigin.DETERMINISTIC_PERSISTED_FANOUT
        for item in fanout
    )
    assert all(item.requested_arguments == {"recommendation_id": "rec-2"} for item in fanout)
    assert [item.executed_arguments["recommendation_id"] for item in fanout] == [
        "rec-1",
        "rec-2",
    ]
    assert [item.canonical_key for item in fanout] == [
        canonical_tool_call_key(PERSISTED, {"recommendation_id": "rec-1"}),
        canonical_tool_call_key(PERSISTED, {"recommendation_id": "rec-2"}),
    ]
    assert result.executed_tool_call_count == 4
    assert result.executed_persisted_call_count == 2
    assert [item.evidence_ref for item in result.evidence_snapshots] == [
        "de-1",
        "de-2",
        "de-3",
        "de-4",
    ]


def test_later_trigger_does_not_rerun_a_completed_fanout_subset(monkeypatch):
    _result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-1"})),
            _calls((PERSISTED, {"recommendation_id": "rec-2"})),
            _claims(_identity()),
        ],
        profile={"ids": ("rec-1", "rec-2")},
    )
    persisted = [item for item in calls if item[0] == PERSISTED]
    assert [item[1]["recommendation_id"] for item in persisted] == ["rec-1", "rec-2"]
    assert provider.turns[3].validation_feedback.code is ValidationFeedbackCode.DUPLICATE_TOOL_CALL
    assert len(calls) == 3


def test_fanout_cap_precedes_budget_shortfall(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-4"})),
            _claims(_identity()),
        ],
        profile={"ids": ("rec-4", "rec-2", "rec-3", "rec-1")},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION]
    assert result.collection_partial_reason is (
        DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert result.collection_partial_reason is not (
        DecisionCollectionPartialReason.PERSISTED_FANOUT_BUDGET
    )
    assert provider.turns[2].validation_feedback.code is (
        ValidationFeedbackCode.TOOL_CALL_BUDGET_EXCEEDED
    )
    assert result.status is DecisionSpecialistRunStatus.PARTIAL


def test_completed_can_carry_failed_verification(monkeypatch):
    result, recorded, provider = _run(
        monkeypatch,
        [
            _calls((RISK, {"aircraft_id": AIRCRAFT})),
            _claims(_identity(aircraft_id="other")),
        ],
    )
    assert result.status is DecisionSpecialistRunStatus.COMPLETED
    assert result.collection_partial_reason is None
    assert result.verification.outcome.value == "FAILED"
    assert result.render.outcome.value == "VERIFICATION_FAILED"
    restored = DecisionSpecialistRunResult.from_dict(result.to_dict())
    assert restored == result


def test_skipped_fanout_then_provider_exception_stays_provider_failed(monkeypatch):
    result, calls, _provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-1"})),
            RuntimeError("provider-secret-text"),
        ],
        profile={"ids": ("rec-1", "rec-2", "rec-3", "rec-4")},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION]
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.status is not DecisionSpecialistRunStatus.PARTIAL
    assert result.collection_partial_reason is (
        DecisionCollectionPartialReason.PERSISTED_FANOUT_CAP
    )
    assert result.runtime_errors == (DecisionRuntimeErrorCode.PROVIDER_EXCEPTION,)
    assert result.verification is None
    assert result.render is None
    assert "provider-secret-text" not in json.dumps(result.to_dict())


def test_evidence_refs_restart_each_run(monkeypatch):
    first, recorded, provider = _run(
        monkeypatch,
        [
            _calls((RISK, {"aircraft_id": AIRCRAFT})),
            _claims(_identity()),
        ],
    )
    second, recorded, provider = _run(
        monkeypatch,
        [
            _calls((RISK, {"aircraft_id": AIRCRAFT})),
            _claims(_identity()),
        ],
    )
    assert first.evidence_snapshots[0].evidence_ref == "de-1"
    assert second.evidence_snapshots[0].evidence_ref == "de-1"


def test_default_tool_call_ids_are_not_model_visible(monkeypatch):
    calls = _install(monkeypatch, {})
    provider = ScriptedProvider(
        [
            _calls((RISK, {"aircraft_id": AIRCRAFT})),
            _claims(_identity()),
        ]
    )
    result = DecisionSpecialist(provider=provider).run(_request(), _runtime())
    tool_call_id = calls[0][2]
    assert tool_call_id.startswith("decision-call-")
    assert len(tool_call_id) > len("decision-call-")
    snapshot_text = json.dumps(result.evidence_snapshots[0].to_dict())
    turn_text = json.dumps(provider.turns[1].to_dict())
    assert tool_call_id not in snapshot_text
    assert tool_call_id not in turn_text
    assert result.tool_results[0].tool_call_id == tool_call_id


def test_direct_persisted_does_not_fan_out_or_accept_current_tools(monkeypatch):
    result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RISK, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-1"})),
            _claims(_identity("de-1", "ac-1")),
        ],
        mode=DecisionTargetMode.DIRECT_PERSISTED,
        aircraft_id=None,
        recommendation_id="rec-1",
    )
    assert [item[0] for item in calls] == [PERSISTED]
    assert calls[0][1] == {"recommendation_id": "rec-1"}
    assert result.invocations[0].dispatch_origin is DecisionDispatchOrigin.MODEL_REQUEST
    assert provider.turns[1].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT
    assert result.executed_persisted_call_count == 1


def test_known_empty_set_does_not_invent_a_recommendation(monkeypatch):
    _result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-1"})),
            _claims(_identity()),
        ],
        profile={"empty": True},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION]
    assert provider.turns[2].validation_feedback.code is ValidationFeedbackCode.UNKNOWN_ARGUMENT


def test_not_in_current_set_does_not_become_known_empty_or_known_ids(monkeypatch):
    _result, calls, provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-9"})),
            _claims(_identity()),
        ],
        profile={"not_in_set": True},
    )
    assert [item[0] for item in calls] == [RECOMMENDATION]
    assert calls[0][0] == RECOMMENDATION
    assert provider.turns[2].validation_feedback.argument_name == "recommendation_id"


def test_retained_encounters_fan_out_in_lexical_order(monkeypatch):
    _result, calls, _provider = _run(
        monkeypatch,
        [
            _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
            _calls((PERSISTED, {"recommendation_id": "rec-b"})),
            _claims(_identity()),
        ],
        profile={"encounters": (("enc-2", "rec-b"), ("enc-1", "rec-a"))},
    )
    assert [item[1]["recommendation_id"] for item in calls[1:]] == ["rec-a", "rec-b"]


def test_every_executed_binding_reaches_the_verifier(monkeypatch):
    seen = {}
    real = __import__(
        "wilvor_ai.decision_specialist",
        fromlist=["verify_decision_evidence"],
    ).verify_decision_evidence

    def spy(claims, bindings):
        seen["count"] = len(bindings)
        seen["refs"] = tuple(item.evidence_ref for item in bindings)
        return real(claims, bindings)

    monkeypatch.setattr("wilvor_ai.decision_specialist.verify_decision_evidence", spy)
    result, recorded, provider = _run(
        monkeypatch,
        [
            _calls(
                (CONTEXT, {"aircraft_id": AIRCRAFT}),
                (RISK, {"aircraft_id": AIRCRAFT}),
            ),
            _claims(_identity()),
        ],
    )
    assert seen["count"] == 2
    assert seen["refs"] == ("de-1", "de-2")
    assert len(result.evidence_bindings) == 2


def test_turn_limit_never_requests_a_fifth_completion(monkeypatch):
    decisions = [
        _calls((CONTEXT, {"aircraft_id": AIRCRAFT})),
        _calls((RISK, {"aircraft_id": AIRCRAFT})),
        _calls((RECOMMENDATION, {"aircraft_id": AIRCRAFT})),
        _calls((PERSISTED, {"recommendation_id": "rec-1"})),
        _calls((CONTEXT, {"aircraft_id": AIRCRAFT})),
    ]
    result, calls, provider = _run(monkeypatch, decisions)
    assert len(provider.turns) == 4
    assert len(calls) == 4
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.runtime_errors == (DecisionRuntimeErrorCode.MODEL_TURN_LIMIT_EXCEEDED,)
    assert result.verification is None


def test_unsupported_and_refusal_do_not_render(monkeypatch):
    unsupported, recorded, provider = _run(
        monkeypatch,
        [
            DecisionModelDecision(
                kind=ModelDecisionKind.UNSUPPORTED,
                unsupported_reason=DecisionUnsupportedReason.LIVE_OPS,
            )
        ],
    )
    assert unsupported.status is DecisionSpecialistRunStatus.UNSUPPORTED
    assert unsupported.unsupported_reason is DecisionUnsupportedReason.LIVE_OPS
    assert unsupported.render is None
    refusal, recorded, provider = _run(
        monkeypatch,
        [
            DecisionModelDecision(
                kind=ModelDecisionKind.REFUSAL,
                refusal_code="model_declined",
            )
        ],
    )
    assert refusal.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert refusal.runtime_errors == (DecisionRuntimeErrorCode.REFUSAL,)
    assert refusal.refusal_code == "model_declined"
    assert refusal.render is None
    assert "explanation" not in refusal.to_dict()


def test_invalid_model_decision_stops_without_rendering(monkeypatch):
    result, calls, _provider = _run(monkeypatch, [object()])
    assert calls == []
    assert result.status is DecisionSpecialistRunStatus.PROVIDER_FAILED
    assert result.runtime_errors == (DecisionRuntimeErrorCode.INVALID_MODEL_DECISION,)
    assert result.render is None


def test_adapter_failure_preserves_earlier_reads_and_hides_the_exception(monkeypatch):
    result, calls, _provider = _run(
        monkeypatch,
        [
            _calls(
                (CONTEXT, {"aircraft_id": AIRCRAFT}),
                (RISK, {"aircraft_id": AIRCRAFT}),
            )
        ],
        fail_on=2,
    )
    assert [item[0] for item in calls] == [CONTEXT, RISK]
    assert result.status is DecisionSpecialistRunStatus.UNAVAILABLE
    assert result.runtime_errors == (DecisionRuntimeErrorCode.ADAPTER_EXECUTION_FAILED,)
    assert result.executed_tool_call_count == 1
    assert result.tool_results[0].tool_name == CONTEXT
    assert result.verification is None
    assert result.render is None
    assert "adapter-secret-text" not in json.dumps(result.to_dict())


def test_structural_tool_errors_execute_nothing(monkeypatch):
    cases = [
        _calls(("not_a_tool", {"aircraft_id": AIRCRAFT})),
        _calls((RISK, {"aircraft_id": AIRCRAFT, "tool_call_id": "hidden"})),
        _calls((RISK, {})),
        _calls((RISK, {"aircraft_id": 12})),
        _calls(
            (CONTEXT, {"aircraft_id": AIRCRAFT}),
            (CONTEXT, {"aircraft_id": AIRCRAFT}),
        ),
    ]
    expected = [
        ValidationFeedbackCode.UNKNOWN_TOOL,
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
        ValidationFeedbackCode.MISSING_REQUIRED_ARGUMENT,
        ValidationFeedbackCode.INVALID_ARGUMENT_TYPE,
        ValidationFeedbackCode.DUPLICATE_TOOL_CALL,
    ]
    for decision, code in zip(cases, expected):
        result, calls, provider = _run(
            monkeypatch,
            [decision, _claims(_identity())],
        )
        assert calls == []
        assert provider.turns[1].validation_feedback.code is code
        assert result.executed_tool_call_count == 0


def test_request_target_whitespace_is_rejected():
    for aircraft_id in (" ac-1", "ac-1 ", "ac 1"):
        with pytest.raises(Exception):
            _request(aircraft_id=aircraft_id)
    for recommendation_id in (" rec-1", "rec-1 ", "rec 1"):
        with pytest.raises(Exception):
            DecisionSpecialistRequest(
                user_text="status",
                mode=DecisionTargetMode.DIRECT_PERSISTED,
                recommendation_id=recommendation_id,
            )
