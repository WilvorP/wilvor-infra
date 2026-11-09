"""DE0 Decision claim contract tests. No verifier, provider, or tool runtime."""

from __future__ import annotations

from dataclasses import fields

import pytest

from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import (
    DECISION_EVIDENCE_REF_MAX_LENGTH,
    DECISION_EVIDENCE_REF_PATTERN,
    AircraftIdentityClaim,
    CapabilityGapClaim,
    CurrentPersistedLinkClaim,
    DecisionClaimKind,
    EncounterSetClaim,
    LimitationClaim,
    PersistedAssessmentStatus,
    PersistedCandidateClaim,
    PersistedCandidateCollectionReason,
    PersistedCandidateStatusClaim,
    PersistedCapabilityEvidenceStatus,
    RecommendationActionClaim,
    RecommendationSetClaim,
    RiskAbsentClaim,
    RiskPresentClaim,
    ToolStatusClaim,
    decision_claim_from_dict,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    PersistedEvaluationScope,
    RecommendationActionType,
    StoredRiskLevel,
)
from wilvor_ai.persisted_airport_contracts import (
    COMPLETE,
    UNAVAILABLE,
    WAITING_FOR_WEATHER,
)


def assert_validation_error(expected_error: str, factory) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()
    assert expected_error in exc_info.value.errors


def _round_trip(payload: dict) -> None:
    claim = decision_claim_from_dict(payload)
    assert claim.to_dict() == payload
    assert decision_claim_from_dict(claim.to_dict()) == claim


def _with(payload: dict, **updates) -> dict:
    cloned = dict(payload)
    cloned.update(updates)
    return cloned


def _aircraft() -> dict:
    return {
        "kind": "AIRCRAFT_IDENTITY",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "aircraft_id": "N123",
    }


def _evaluation() -> dict:
    return {
        "kind": "EVALUATION_STATE",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "evaluation_state": "ESTABLISHED",
    }


def _encounters() -> dict:
    return {
        "kind": "ENCOUNTER_SET",
        "evidence_ref": "de-2",
        "evidence_scope": "CURRENT",
        "encounter_ids": ["enc-a", "enc-b"],
    }


def _risk_absent() -> dict:
    return {
        "kind": "RISK_ABSENT",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "encounter_id": "enc-a",
    }


def _risk_present() -> dict:
    return {
        "kind": "RISK_PRESENT",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "encounter_id": "enc-a",
        "risk_id": "risk-1",
        "risk_level": "LOW",
        "risk_score": 0,
    }


def _recommendations() -> dict:
    return {
        "kind": "RECOMMENDATION_SET",
        "evidence_ref": "de-3",
        "evidence_scope": "CURRENT",
        "recommendation_ids": ["rec-a", "rec-b"],
        "absence_state": "PRESENT",
    }


def _action() -> dict:
    return {
        "kind": "RECOMMENDATION_ACTION",
        "evidence_ref": "de-3",
        "evidence_scope": "CURRENT",
        "recommendation_id": "rec-a",
        "primary_action_type": "EVALUATE_DIVERSION",
        "advisory_authority": "ADVISORY_ONLY",
    }


def _gap() -> dict:
    return {
        "kind": "CHAIN_GAP",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "chain_gap": "RISK_ABSENT",
    }


def _capability() -> dict:
    return {
        "kind": "CAPABILITY_GAP",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "capability_gap": "ROUTE_ALTERNATIVE_NOT_IMPLEMENTED",
    }


def _persisted_evaluation() -> dict:
    return {
        "kind": "PERSISTED_EVALUATION",
        "evidence_ref": "de-4",
        "evidence_scope": "PERSISTED",
        "recommendation_id": "rec-a",
        "airport_evaluation_id": "eval-1",
        "evaluation_scope": "PERSISTED_EVALUATION_EVIDENCE",
    }


def _candidate() -> dict:
    return {
        "kind": "PERSISTED_CANDIDATE",
        "evidence_ref": "de-4",
        "evidence_scope": "PERSISTED",
        "recommendation_id": "rec-a",
        "airport_id": "KSFO",
        "airport_assessment_id": "assess-1",
        "assessment_status": "COMPLETE",
        "route_safety_status": "UNAVAILABLE",
        "runway_evidence_status": "UNAVAILABLE",
        "congestion_evidence_status": "UNAVAILABLE",
    }


def _candidate_status() -> dict:
    return {
        "kind": "PERSISTED_CANDIDATE_STATUS",
        "evidence_ref": "de-4",
        "evidence_scope": "PERSISTED",
        "recommendation_id": "rec-a",
        "candidate_count": 0,
        "collection_reason": "EMPTY_ASSESSMENTS",
    }


def _tool_status(scope: str = "CURRENT") -> dict:
    return {
        "kind": "TOOL_STATUS",
        "evidence_ref": "de-1",
        "evidence_scope": scope,
        "status": "NOT_FOUND",
    }


def _limitation() -> dict:
    return {
        "kind": "LIMITATION",
        "evidence_ref": "de-1",
        "evidence_scope": "CURRENT",
        "limitation_code": "NO_SNAPSHOT_LIMITATION",
    }


def _link() -> dict:
    return {
        "kind": "CURRENT_PERSISTED_LINK",
        "current_evidence_ref": "de-1",
        "persisted_evidence_ref": "de-2",
        "recommendation_id": "rec-a",
    }


_CURRENT_PAYLOADS = (
    _aircraft,
    _evaluation,
    _encounters,
    _risk_absent,
    _risk_present,
    _recommendations,
    _action,
    _gap,
    _capability,
    _limitation,
)
_PERSISTED_PAYLOADS = (_persisted_evaluation, _candidate, _candidate_status)


def test_every_claim_round_trips() -> None:
    for factory in (
        *_CURRENT_PAYLOADS,
        *_PERSISTED_PAYLOADS,
        _tool_status,
        _link,
    ):
        _round_trip(factory())
    _round_trip(_tool_status("PERSISTED"))
    _round_trip(_with(_risk_absent(), encounter_id=None))
    _round_trip(_with(_risk_present(), risk_level="MEDIUM", risk_score=4))
    _round_trip(_with(_risk_present(), risk_level="HIGH", risk_score=9))
    _round_trip(_with(_candidate(), assessment_status="WAITING_FOR_WEATHER"))
    _round_trip(_with(_candidate_status(), candidate_count=2, collection_reason=None))
    _round_trip(
        _with(_persisted_evaluation(), airport_evaluation_id=None)
    )
    _round_trip(
        _with(_limitation(), limitation_code="RECOMMENDATION_ABSENCE_LIMITATION")
    )
    for status in ToolResultStatus:
        _round_trip(_with(_tool_status(), status=status.value))
        _round_trip(_with(_tool_status("PERSISTED"), status=status.value))


def test_claim_kinds_are_closed() -> None:
    assert {item.value for item in DecisionClaimKind} == {
        "AIRCRAFT_IDENTITY",
        "EVALUATION_STATE",
        "ENCOUNTER_SET",
        "RISK_ABSENT",
        "RISK_PRESENT",
        "RECOMMENDATION_SET",
        "RECOMMENDATION_ACTION",
        "CHAIN_GAP",
        "CAPABILITY_GAP",
        "PERSISTED_EVALUATION",
        "PERSISTED_CANDIDATE",
        "PERSISTED_CANDIDATE_STATUS",
        "TOOL_STATUS",
        "LIMITATION",
        "CURRENT_PERSISTED_LINK",
    }


def test_unknown_kind_and_extra_field_fail() -> None:
    assert_validation_error(
        "invalid_kind",
        lambda: decision_claim_from_dict(_with(_aircraft(), kind="EXACT_COUNT")),
    )
    assert_validation_error(
        "unexpected_claim_field",
        lambda: decision_claim_from_dict(_with(_aircraft(), note="later")),
    )
    assert_validation_error(
        "invalid_decision_claim",
        lambda: decision_claim_from_dict([]),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "key",
    [
        "answer",
        "answer_text",
        "claim_text",
        "statement",
        "candidate_answer",
        "factual_text",
        "explanation",
        "Explanation",
    ],
)
def test_factual_prose_keys_are_rejected(key: str) -> None:
    assert_validation_error(
        "unexpected_claim_prose",
        lambda: decision_claim_from_dict(_with(_aircraft(), **{key: "risk is low"})),
    )


@pytest.mark.parametrize(
    "key",
    [
        "route",
        "routes",
        "waypoint",
        "waypoints",
        "trajectory",
        "validated_route",
        "flight_plan",
        "selected_recommendation",
        "best_recommendation",
        "primary_recommendation",
        "selected_encounter",
        "primary_encounter",
        "selected_airport",
        "safest_airport",
        "best_airport",
        "safe_diversion",
        "selected_diversion",
        "diversion_clearance",
        "rank",
        "score",
        "total_airport_score",
        "distance_nm",
        "eta_minutes",
        "atc_clearance",
        "dispatch_instruction",
        "landing_instruction",
        "tool_call_id",
        "correlation_id",
        "Selected_Recommendation",
    ],
)
def test_forbidden_decision_semantics_are_rejected(key: str) -> None:
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(_with(_aircraft(), **{key: "invented"})),
    )


def test_absent_risk_cannot_carry_level_score_or_risk_id() -> None:
    names = {item.name for item in fields(RiskAbsentClaim)}
    assert "risk_level" not in names
    assert "risk_score" not in names
    assert "risk_id" not in names
    for key in ("risk_level", "risk_score", "risk_id"):
        assert_validation_error(
            "unexpected_claim_field",
            lambda key=key: decision_claim_from_dict(
                _with(_risk_absent(), **{key: "LOW"})
            ),
        )


def test_present_risk_requires_stored_level_and_score() -> None:
    claim = decision_claim_from_dict(_risk_present())
    assert isinstance(claim, RiskPresentClaim)
    assert claim.risk_level is StoredRiskLevel.LOW
    assert claim.risk_score == 0
    assert_validation_error(
        "invalid_risk_level",
        lambda: decision_claim_from_dict(_with(_risk_present(), risk_level="UNKNOWN")),
    )
    assert_validation_error(
        "invalid_risk_score",
        lambda: decision_claim_from_dict(_with(_risk_present(), risk_score=True)),
    )
    assert_validation_error(
        "invalid_risk_score",
        lambda: decision_claim_from_dict(_with(_risk_present(), risk_score=-1)),
    )
    assert_validation_error(
        "invalid_risk_score",
        lambda: decision_claim_from_dict(_with(_risk_present(), risk_score="12")),
    )
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: RiskPresentClaim(
            evidence_ref="de-1",
            evidence_scope=TemporalScope.PERSISTED,
            risk_id="risk-1",
            risk_level=StoredRiskLevel.HIGH,
            risk_score=1,
        ),
    )


def test_recommendation_set_has_no_winner_and_empty_is_absence() -> None:
    empty = decision_claim_from_dict(
        _with(
            _recommendations(),
            recommendation_ids=[],
            absence_state="ABSENT_FROM_CURRENT_CANDIDATES",
        )
    )
    assert isinstance(empty, RecommendationSetClaim)
    assert empty.recommendation_ids == ()
    assert (
        empty.absence_state
        is DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    assert "primary_action_type" not in empty.to_dict()
    assert_validation_error(
        "empty_recommendations_require_absence",
        lambda: decision_claim_from_dict(
            _with(_recommendations(), recommendation_ids=[], absence_state="PRESENT")
        ),
    )
    assert_validation_error(
        "current_recommendations_require_present_link",
        lambda: decision_claim_from_dict(
            _with(
                _recommendations(),
                absence_state="ABSENT_FROM_CURRENT_CANDIDATES",
            )
        ),
    )
    assert_validation_error(
        "unsorted_recommendation_ids",
        lambda: decision_claim_from_dict(
            _with(_recommendations(), recommendation_ids=["rec-b", "rec-a"])
        ),
    )
    assert_validation_error(
        "duplicate_recommendation_ids",
        lambda: decision_claim_from_dict(
            _with(_recommendations(), recommendation_ids=["rec-a", "rec-a"])
        ),
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(
            _with(_recommendations(), selected_recommendation="rec-a")
        ),
    )


def test_recommendation_action_stays_advisory() -> None:
    claim = decision_claim_from_dict(_action())
    assert isinstance(claim, RecommendationActionClaim)
    assert claim.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION
    assert claim.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    for action in RecommendationActionType:
        _round_trip(_with(_action(), primary_action_type=action.value))
    assert_validation_error(
        "invalid_advisory_authority",
        lambda: decision_claim_from_dict(
            _with(_action(), advisory_authority="CLEARANCE")
        ),
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(_with(_action(), route="KSFO")),
    )


def test_encounter_set_rejects_selection_and_unsorted_ids() -> None:
    claim = decision_claim_from_dict(_encounters())
    assert isinstance(claim, EncounterSetClaim)
    assert claim.encounter_ids == ("enc-a", "enc-b")
    _round_trip(_with(_encounters(), encounter_ids=[]))
    assert_validation_error(
        "unsorted_encounter_ids",
        lambda: decision_claim_from_dict(
            _with(_encounters(), encounter_ids=["enc-b", "enc-a"])
        ),
    )
    assert_validation_error(
        "duplicate_encounter_ids",
        lambda: decision_claim_from_dict(
            _with(_encounters(), encounter_ids=["enc-a", "enc-a"])
        ),
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(
            _with(_encounters(), primary_encounter="enc-a")
        ),
    )


def test_persisted_candidate_statuses_and_rejected_rank() -> None:
    assert PersistedAssessmentStatus.COMPLETE.value == COMPLETE
    assert PersistedAssessmentStatus.WAITING_FOR_WEATHER.value == WAITING_FOR_WEATHER
    assert PersistedCapabilityEvidenceStatus.UNAVAILABLE.value == UNAVAILABLE
    claim = decision_claim_from_dict(_candidate())
    assert isinstance(claim, PersistedCandidateClaim)
    assert claim.route_safety_status is PersistedCapabilityEvidenceStatus.UNAVAILABLE
    assert claim.runway_evidence_status is PersistedCapabilityEvidenceStatus.UNAVAILABLE
    assert (
        claim.congestion_evidence_status is PersistedCapabilityEvidenceStatus.UNAVAILABLE
    )
    assert_validation_error(
        "invalid_route_safety_status",
        lambda: decision_claim_from_dict(
            _with(_candidate(), route_safety_status="AVAILABLE")
        ),
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(_with(_candidate(), rank=1)),
    )
    assert_validation_error(
        "forbidden_decision_field",
        lambda: decision_claim_from_dict(_with(_candidate(), safe_diversion=True)),
    )


def test_collection_reason_is_symbolic_not_prose() -> None:
    assert {item.value for item in PersistedCandidateCollectionReason} == {
        "EMPTY_ASSESSMENTS",
        "NO_COMPLETE_ASSESSMENT",
    }
    for item in PersistedCandidateCollectionReason:
        assert " " not in item.value
    status = decision_claim_from_dict(_candidate_status())
    assert isinstance(status, PersistedCandidateStatusClaim)
    assert status.candidate_count == 0
    assert (
        status.collection_reason
        is PersistedCandidateCollectionReason.EMPTY_ASSESSMENTS
    )
    assert_validation_error(
        "invalid_collection_reason",
        lambda: decision_claim_from_dict(
            _with(
                _candidate_status(),
                collection_reason="No candidate airport assessment was available.",
            )
        ),
    )
    assert_validation_error(
        "invalid_candidate_count",
        lambda: decision_claim_from_dict(
            _with(_candidate_status(), candidate_count=True)
        ),
    )


def test_current_claims_reject_persisted_and_other_scopes() -> None:
    for factory in _CURRENT_PAYLOADS:
        assert_validation_error(
            "invalid_evidence_scope",
            lambda factory=factory: decision_claim_from_dict(
                _with(factory(), evidence_scope="PERSISTED")
            ),
        )
        for scope in ("HISTORICAL", "HYBRID"):
            assert_validation_error(
                "invalid_evidence_scope",
                lambda factory=factory, scope=scope: decision_claim_from_dict(
                    _with(factory(), evidence_scope=scope)
                ),
            )


def test_persisted_claims_reject_current_historical_and_hybrid() -> None:
    for factory in _PERSISTED_PAYLOADS:
        for scope in ("CURRENT", "HISTORICAL", "HYBRID"):
            assert_validation_error(
                "invalid_evidence_scope",
                lambda factory=factory, scope=scope: decision_claim_from_dict(
                    _with(factory(), evidence_scope=scope)
                ),
            )


def test_tool_status_allows_current_or_persisted_only() -> None:
    current = decision_claim_from_dict(_tool_status("CURRENT"))
    persisted = decision_claim_from_dict(_tool_status("PERSISTED"))
    assert isinstance(current, ToolStatusClaim)
    assert isinstance(persisted, ToolStatusClaim)
    assert current.status is ToolResultStatus.NOT_FOUND
    for scope in ("HISTORICAL", "HYBRID"):
        assert_validation_error(
            "invalid_evidence_scope",
            lambda scope=scope: decision_claim_from_dict(_tool_status(scope)),
        )


def test_limitation_is_current_only() -> None:
    claim = decision_claim_from_dict(_limitation())
    assert isinstance(claim, LimitationClaim)
    assert claim.evidence_scope is TemporalScope.CURRENT
    assert claim.limitation_code is DecisionLimitationCode.NO_SNAPSHOT_LIMITATION
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: decision_claim_from_dict(_with(_limitation(), evidence_scope="PERSISTED")),
    )
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: decision_claim_from_dict(
            _with(_limitation(), evidence_scope="HISTORICAL")
        ),
    )
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: decision_claim_from_dict(_with(_limitation(), evidence_scope="HYBRID")),
    )
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: LimitationClaim(
            evidence_ref="de-1",
            evidence_scope=TemporalScope.PERSISTED,
            limitation_code=DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
        ),
    )


def test_evidence_ref_format() -> None:
    assert DECISION_EVIDENCE_REF_MAX_LENGTH == 12
    assert DECISION_EVIDENCE_REF_PATTERN.fullmatch("de-1")
    assert DECISION_EVIDENCE_REF_PATTERN.fullmatch("de-2")
    assert DECISION_EVIDENCE_REF_PATTERN.fullmatch("de-999999999")
    _round_trip(_with(_aircraft(), evidence_ref="de-999999999"))
    for ref in ("de-0", "de-01", "DE-1", "de- 1", "tool-call-1", "N123", "", "de-1 "):
        assert_validation_error(
            "invalid_evidence_ref",
            lambda ref=ref: decision_claim_from_dict(
                _with(_aircraft(), evidence_ref=ref)
            ),
        )
    assert_validation_error(
        "invalid_evidence_ref",
        lambda: AircraftIdentityClaim(
            evidence_ref="de-01",
            evidence_scope=TemporalScope.CURRENT,
            aircraft_id="N123",
        ),
    )


def test_link_requires_two_different_refs_and_no_scope() -> None:
    claim = decision_claim_from_dict(_link())
    assert isinstance(claim, CurrentPersistedLinkClaim)
    assert "evidence_scope" not in claim.to_dict()
    assert_validation_error(
        "evidence_refs_must_differ",
        lambda: decision_claim_from_dict(
            _with(_link(), persisted_evidence_ref="de-1")
        ),
    )
    assert_validation_error(
        "unexpected_claim_field",
        lambda: decision_claim_from_dict(_with(_link(), evidence_scope="HYBRID")),
    )
    assert_validation_error(
        "evidence_refs_must_differ",
        lambda: CurrentPersistedLinkClaim(
            current_evidence_ref="de-4",
            persisted_evidence_ref="de-4",
            recommendation_id="rec-a",
        ),
    )


def test_direct_constructors_enforce_scope() -> None:
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: AircraftIdentityClaim(
            evidence_ref="de-1",
            evidence_scope=TemporalScope.HISTORICAL,
            aircraft_id="N123",
        ),
    )
    assert_validation_error(
        "invalid_evidence_scope",
        lambda: CapabilityGapClaim(
            evidence_ref="de-1",
            evidence_scope=TemporalScope.HYBRID,
            capability_gap=DecisionCapabilityGap.ROUTE_SAFETY_EVIDENCE_UNAVAILABLE,
        ),
    )
    persisted = decision_claim_from_dict(_persisted_evaluation())
    assert persisted.evaluation_scope is (
        PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
    )
    gap = decision_claim_from_dict(_gap())
    assert gap.chain_gap is DecisionChainGap.RISK_ABSENT
    state = decision_claim_from_dict(_evaluation())
    assert state.evaluation_state is DecisionEvaluationState.ESTABLISHED
    assert_validation_error(
        "invalid_aircraft_id",
        lambda: decision_claim_from_dict(_with(_aircraft(), aircraft_id="N 123")),
    )
