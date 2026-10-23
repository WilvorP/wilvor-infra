"""Offline tests for Master cross-specialist comparability.

Fixtures exercise real contracts. They are not production authority.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_UTC,
    completed_all_verified,
    evidence,
    snapshot_claims,
)
from tests.fixtures.ma4_specialist_evidence_examples import (
    decision_absent_risk_case,
    decision_authorization,
    decision_offline_consistent_run,
    decision_present_risk_case,
    historical_authorization,
    historical_exact_count_run,
    historical_truncated_lower_bound_run,
    historical_verified_zero_run,
    live_ops_authorization,
    live_ops_context,
    live_ops_none_verified_result,
    live_ops_partially_verified_result,
    live_ops_request,
    live_ops_request_mismatch_result,
    live_ops_unique_callsign_result,
)
from tests.contracts.test_decision_evidence_verifier import AS_OF as DECISION_AS_OF
from tests.contracts.test_historical_verification import CALL_A
from tests.contracts.test_master_contracts import _mixed_scope_decision_result
from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import RecommendationActionClaim
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    RecommendationActionType,
)
from wilvor_ai.decision_evidence_verifier import verify_decision_evidence
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionDispatchOrigin,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
    DecisionToolInvocationAudit,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicFlag,
    LiveOpsPublicObservedField,
    LiveOpsPublicRecordedFlag,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
)
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    admit_master_evidence,
)
from wilvor_ai.master_evidence_comparability import (
    MasterComparisonAssessment,
    MasterComparisonCategory,
    MasterComparisonEligibility,
    MasterComparisonPrerequisite,
    MasterComparisonReason,
    MasterComparisonRequest,
    assess_master_evidence_comparability,
)
from wilvor_ai.master_routing import MasterRequestCoverage
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import RecordIdentityClaim


MASTER = "master-req-compare-1"
FORBIDDEN_OUTCOMES = frozenset(
    {
        "VERIFIED",
        "OPERATIONALLY_COMPARABLE",
        "FACTS_AGREE",
        "FACTS_CONFLICT",
        "SAFE_TO_ACT",
        "PRODUCTION_READY",
        "COMPATIBLE",
        "CONFLICT",
        "CONFLICTING",
    }
)


class SyntheticWorkerResult:
    """Name matches the MA3 synthetic family. This object is not that runtime."""

    evidence_verified = True


def _live(invocation_id: str, result=None, request=None, **overrides: object):
    values = {
        "master_request_id": MASTER,
        "invocation_id": invocation_id,
        "requested_evaluation_time_utc": None,
    }
    values.update(overrides)
    return MasterEvidenceAdmissionInput(
        live_ops_authorization(**values),
        LiveOpsAdmissionSources(
            request=live_ops_request() if request is None else request,
            context=live_ops_context(),
            result=completed_all_verified() if result is None else result,
        ),
    )


def _decision(invocation_id: str, run=None, **overrides: object):
    artifact = decision_offline_consistent_run() if run is None else run
    aircraft_id = artifact.aircraft_id
    values = {
        "master_request_id": MASTER,
        "invocation_id": invocation_id,
        "correlation_id": None,
        "specialist_request_id": None,
        "requested_evaluation_time_utc": DECISION_AS_OF,
        "expected_aircraft_id": aircraft_id,
    }
    values.update(overrides)
    return MasterEvidenceAdmissionInput(decision_authorization(**values), artifact)


def _historical(invocation_id: str, run=None, **overrides: object):
    artifact = finalize_historical_specialist_run(
        historical_exact_count_run() if run is None else run
    )
    values = {
        "master_request_id": MASTER,
        "invocation_id": invocation_id,
        "correlation_id": None,
        "specialist_request_id": None,
        "requested_evaluation_time_utc": artifact.evaluated_as_of_utc,
    }
    values.update(overrides)
    return MasterEvidenceAdmissionInput(historical_authorization(**values), artifact)


def _request(category: MasterComparisonCategory, **overrides: object) -> MasterComparisonRequest:
    values = {
        "master_request_id": MASTER,
        "left_invocation_id": "inv-left",
        "right_invocation_id": "inv-right",
        "category": category,
    }
    values.update(overrides)
    return MasterComparisonRequest(**values)  # type: ignore[arg-type]


def _decision_run(claims, bindings, aircraft_id: str = "ac-1") -> DecisionSpecialistRunResult:
    tool_results = tuple(item.raw_tool_result for item in bindings)
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    persisted = sum(item.temporal_scope is TemporalScope.PERSISTED for item in tool_results)
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.COMPLETED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=aircraft_id,
        recommendation_id=None,
        tool_results=tool_results,
        evidence_snapshots=tuple(
            DecisionEvidenceSnapshot(
                item.evidence_ref,
                project_decision_tool_result(item.raw_tool_result),
            )
            for item in bindings
        ),
        evidence_bindings=bindings,
        invocations=tuple(
            DecisionToolInvocationAudit(
                tool_name=item.raw_tool_result.tool_name,
                tool_call_id=item.raw_tool_result.tool_call_id,
                evidence_ref=item.evidence_ref,
                requested_arguments={"aircraft_id": aircraft_id},
                executed_arguments={"aircraft_id": aircraft_id},
                canonical_key=f"{item.raw_tool_result.tool_name}:{item.raw_tool_result.tool_call_id}",
                dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
            )
            for item in bindings
        ),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        unsupported_reason=None,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=len(tool_results),
        executed_persisted_call_count=persisted,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def _count_result(metric: LiveOpsPublicCountMetric, mode: LiveOpsPublicCountMode, region: str):
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        metric=metric,
        count_mode=mode,
        count_value=4,
        scope=LiveOpsPublicScope(region=region, hazard_ids=("HZ-1",)),
    )
    return replace(completed_all_verified(), verified_claims=(claim,))


def _risk_result(level: str):
    claim = replace(snapshot_claims()["RISK_STATE"], risk_level=level)
    return replace(completed_all_verified(), verified_claims=(claim,))


def _position_result(position_time: str):
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.POSITION_TIME_UTC,
        observed_value=position_time,
        retained_not_current=False,
    )
    return replace(
        completed_all_verified(),
        verified_claims=(claim,),
        evidence_references=(evidence("call-aircraft-1", "get_aircraft_operational_context"),),
    )


def _region_request(region: str):
    request = live_ops_request()
    return replace(request, selector_hints=replace(request.selector_hints, region=region))


def _assess(category, left, right, **request_overrides):
    return assess_master_evidence_comparability(
        _request(category, **request_overrides),
        left,
        right,
    )


def _deny(result: MasterComparisonAssessment) -> None:
    assert result.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert result.evidence_verified is False
    assert result.production_authority is MasterProductionAuthority.DENIED
    assert result.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert result.end_to_end_fulfilled is False
    assert result.established_prerequisites == ()
    assert MasterComparisonPrerequisite.SOURCE_AUTHENTICITY in result.missing_prerequisites
    assert MasterComparisonReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in result.reason_codes
    assert result.eligibility.value not in FORBIDDEN_OUTCOMES
    assert "answer" not in MasterComparisonAssessment.__dataclass_fields__


def test_valid_pair_uses_distinct_invocations_without_authenticating():
    left = _live("inv-left")
    right = _decision("inv-right")
    result = _assess(MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT, left, right)

    assert left.authorization.invocation_id != right.authorization.invocation_id
    assert left.authorization.master_request_id == right.authorization.master_request_id
    assert result.left_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert result.right_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.CONTRACT_ONLY_IS_NOT_SOURCE_AUTHENTICATION in result.reason_codes
    assert MasterComparisonReason.DECISION_EXECUTION_NOT_AUTHORIZED in result.reason_codes
    _deny(result)


def test_duplicate_invocation_id_is_rejected():
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        _assess(
            MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
            _live("inv-same"),
            _live("inv-same"),
        )


def test_different_master_requests_are_rejected():
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        _assess(
            MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
            _live("inv-left"),
            _decision("inv-right", master_request_id="master-req-other"),
        )


def test_request_that_names_other_invocations_is_blocked():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _decision("inv-right"),
        left_invocation_id="inv-unbound",
    )
    assert result.eligibility is MasterComparisonEligibility.BLOCKED
    assert MasterComparisonReason.INVOCATION_NOT_BOUND in result.reason_codes
    _deny(result)


def test_rejected_admission_blocks_comparison():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left", result=live_ops_request_mismatch_result()),
        _decision("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.BLOCKED
    assert result.left_admission_status is MasterAdmissionStatus.REJECTED
    assert MasterComparisonReason.ADMISSION_REJECTED in result.reason_codes
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in result.left_admission_reasons
    _deny(result)


def test_unresolved_admission_stays_visible():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left", requested_evaluation_time_utc=SYNTHETIC_UTC),
        _decision("inv-right"),
    )
    assert result.left_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.ADMISSION_BINDING_UNRESOLVED in result.reason_codes
    assert MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT in result.left_admission_reasons
    _deny(result)


def test_matching_aircraft_id_strings_do_not_authenticate_a_join():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _decision("inv-right", run=decision_offline_consistent_run(aircraft_id="AC-1")),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN in result.reason_codes
    assert MasterComparisonReason.EXPECTED_AIRCRAFT_IS_NOT_AUTHENTICATED_IDENTITY in result.reason_codes
    assert MasterComparisonPrerequisite.TYPED_AIRCRAFT_IDENTITY in result.missing_prerequisites
    _deny(result)


def test_different_aircraft_id_strings_are_not_an_operational_outcome():
    matched = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _decision("inv-right", run=decision_offline_consistent_run(aircraft_id="AC-1")),
    )
    different = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _decision("inv-right", run=decision_offline_consistent_run(aircraft_id="ac-2")),
    )
    assert different.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.AIRCRAFT_ID_STRINGS_ARE_DIFFERENT in different.reason_codes
    assert MasterComparisonReason.SHARED_AIRCRAFT_ID_STRING_NOT_AUTHENTICATED_JOIN not in different.reason_codes
    assert matched.eligibility is different.eligibility
    assert different.eligibility.value not in FORBIDDEN_OUTCOMES
    _deny(different)


def test_callsign_discovery_does_not_authorize_a_decision_target():
    result = _assess(
        MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET,
        _live("inv-left", result=live_ops_unique_callsign_result()),
        _decision("inv-right", run=decision_offline_consistent_run(aircraft_id="AC-1")),
    )
    absent = _assess(
        MasterComparisonCategory.CALLSIGN_DISCOVERY_VS_DECISION_TARGET,
        _live("inv-left"),
        _decision("inv-right", run=decision_offline_consistent_run(aircraft_id="AC-1")),
    )
    assert result.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.CALLSIGN_DOES_NOT_AUTHORIZE_DECISION_TARGET in result.reason_codes
    assert MasterComparisonReason.DECISION_EXECUTION_NOT_AUTHORIZED in result.reason_codes
    assert MasterAdmissionReason.CALLSIGN_DISCOVERY_NOT_TARGET in result.left_admission_reasons
    assert MasterComparisonReason.CALLSIGN_DISCOVERY_ABSENT in absent.reason_codes
    assert absent.eligibility is MasterComparisonEligibility.UNSUPPORTED
    _deny(result)


def test_source_version_presence_changes_the_reason_without_a_join():
    shared = _assess(
        MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS,
        _live("inv-left"),
        _live("inv-right"),
    )
    changed_claim = replace(completed_all_verified().verified_claims[0], source_version="SV-2")
    changed = replace(completed_all_verified(), verified_claims=(changed_claim,))
    conflicting = _assess(
        MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS,
        _live("inv-left"),
        _live("inv-right", result=changed),
    )
    missing = _assess(
        MasterComparisonCategory.SAME_HAZARD_DIFFERENT_SOURCE_VERSIONS,
        _live("inv-left", result=_position_result(SYNTHETIC_UTC)),
        _live("inv-right", result=_position_result(SYNTHETIC_UTC)),
    )
    assert shared.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.SHARED_SOURCE_VERSION_STRING_NOT_AUTHENTICATED in shared.reason_codes
    assert MasterComparisonReason.SOURCE_VERSION_STRINGS_ARE_DIFFERENT in conflicting.reason_codes
    assert MasterComparisonReason.HAZARD_SOURCE_VERSION_ABSENT in missing.reason_codes
    assert conflicting.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    _deny(conflicting)


def test_missing_observation_time_stays_unresolved():
    result = _assess(
        MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
        _live("inv-left"),
        _live("inv-right"),
    )
    assert MasterComparisonReason.OBSERVATION_TIME_ABSENT in result.reason_codes
    assert MasterComparisonReason.SOURCE_EVENT_TIME_ABSENT in result.reason_codes
    assert MasterComparisonPrerequisite.OBSERVATION_TIME_SEMANTICS in result.missing_prerequisites
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    _deny(result)


def test_identical_time_strings_are_not_an_atomic_observation():
    same = _assess(
        MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
        _live("inv-left", result=_position_result(SYNTHETIC_UTC)),
        _live("inv-right", result=_position_result(SYNTHETIC_UTC)),
    )
    different = _assess(
        MasterComparisonCategory.SAME_AIRCRAFT_DIFFERENT_OBSERVATION_TIMES,
        _live("inv-left", result=_position_result(SYNTHETIC_UTC)),
        _live("inv-right", result=_position_result("2025-10-15T01:00:00Z")),
    )
    assert MasterComparisonReason.SHARED_POSITION_TIME_STRING_NOT_ATOMIC in same.reason_codes
    assert MasterComparisonReason.IDENTICAL_UTC_STRING_NOT_ATOMIC_OBSERVATION in same.reason_codes
    assert MasterComparisonReason.OBSERVATION_TIME_STRINGS_ARE_DIFFERENT in different.reason_codes
    assert same.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert different.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    _deny(same)


def test_historical_and_live_clocks_keep_different_scope_semantics():
    result = _assess(
        MasterComparisonCategory.HISTORICAL_COUNT_VS_CURRENT_POPULATION,
        _historical("inv-left"),
        _live("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.HISTORICAL_WINDOW_IS_NOT_CURRENT_OBSERVATION in result.reason_codes
    assert MasterComparisonReason.HISTORICAL_COUNT_IS_NOT_CURRENT_POPULATION in result.reason_codes
    assert MasterComparisonReason.TEMPORAL_SCOPES_ARE_DIFFERENT not in result.reason_codes
    assert MasterComparisonPrerequisite.CURRENT_VERSUS_HISTORICAL_SCOPE in result.missing_prerequisites
    _deny(result)


def test_query_time_is_not_source_observation_time():
    result = _assess(
        MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
        _live("inv-left"),
        _historical("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.QUERY_TIME_IS_NOT_SOURCE_EVENT_TIME in result.reason_codes
    assert MasterComparisonReason.LIVE_OPS_REFERENCE_TIME_IS_NOT_OBSERVATION_TIME in result.reason_codes
    assert MasterComparisonReason.IDENTICAL_UTC_STRING_NOT_ATOMIC_OBSERVATION in result.reason_codes
    _deny(result)


def test_exact_count_and_lower_bound_are_structurally_distinct():
    exact_and_lower = _assess(
        MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND,
        _historical("inv-left"),
        _historical("inv-right", run=historical_truncated_lower_bound_run()),
    )
    both_exact = _assess(
        MasterComparisonCategory.EXACT_COUNT_VS_LOWER_BOUND,
        _historical("inv-left"),
        _historical("inv-right"),
    )
    assert exact_and_lower.eligibility is MasterComparisonEligibility.CONTRACT_CHECKABLE_ONLY
    assert MasterComparisonReason.EXACT_COUNT_IS_NOT_LOWER_BOUND in exact_and_lower.reason_codes
    assert both_exact.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.LOWER_BOUND_CLAIM_ABSENT in both_exact.reason_codes
    _deny(exact_and_lower)


def test_historical_exact_count_is_not_a_live_ops_subset():
    subset = _count_result(
        LiveOpsPublicCountMetric.ENCOUNTER_RECORD_COUNT,
        LiveOpsPublicCountMode.CONFIRMED_SUBSET,
        "California",
    )
    result = _assess(
        MasterComparisonCategory.HISTORICAL_EXACT_VS_LIVE_OPS_SUBSET,
        _historical("inv-left"),
        _live("inv-right", result=subset),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.EXACT_COUNT_IS_NOT_SUBSET_COUNT in result.reason_codes
    assert MasterComparisonReason.METRIC_DEFINITIONS_NOT_EQUIVALENT in result.reason_codes
    _deny(result)


def test_verified_zero_is_not_an_unknown_current_outcome():
    unknown = _assess(
        MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN,
        _historical("inv-left", run=historical_verified_zero_run()),
        _live("inv-right", result=live_ops_none_verified_result()),
    )
    current = _assess(
        MasterComparisonCategory.HISTORICAL_VERIFIED_ZERO_VS_LIVE_OPS_UNKNOWN,
        _historical("inv-left", run=historical_verified_zero_run()),
        _live("inv-right"),
    )
    assert unknown.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.VERIFIED_ZERO_IS_NOT_UNKNOWN in unknown.reason_codes
    assert MasterComparisonReason.VERIFIED_ZERO_IS_NOT_A_CURRENT_OUTCOME in current.reason_codes
    assert current.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    _deny(unknown)


def test_network_count_is_not_a_regional_subset():
    regional = _count_result(
        LiveOpsPublicCountMetric.ENCOUNTER_RECORD_COUNT,
        LiveOpsPublicCountMode.CONFIRMED_SUBSET,
        "California",
    )
    network = _count_result(
        LiveOpsPublicCountMetric.NETWORK_ENCOUNTER_COUNT,
        LiveOpsPublicCountMode.EXACT_RETURNED_SET,
        "California",
    )
    result = _assess(
        MasterComparisonCategory.CALIFORNIA_SUBSET_VS_NETWORK_COUNT,
        _live("inv-left", result=regional),
        _live("inv-right", result=network),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.NETWORK_SCOPE_IS_NOT_REGIONAL_SUBSET in result.reason_codes
    assert MasterComparisonReason.ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE in result.reason_codes
    assert MasterComparisonReason.CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY in result.reason_codes
    _deny(result)


def test_california_text_stays_untrusted_when_the_label_changes():
    california = _assess(
        MasterComparisonCategory.CALIFORNIA_USER_REGION_TEXT,
        _live("inv-left", request=_region_request("California")),
        _historical("inv-right"),
    )
    nevada = _assess(
        MasterComparisonCategory.CALIFORNIA_USER_REGION_TEXT,
        _live("inv-left", request=_region_request("Nevada")),
        _historical("inv-right"),
        region_text_hint="Nevada",
    )
    assert california.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY in california.reason_codes
    assert MasterComparisonReason.REGION_TEXT_UNTRUSTED in california.reason_codes
    assert MasterComparisonReason.CALIFORNIA_TEXT_IS_NOT_AN_OPERATIONAL_BOUNDARY not in nevada.reason_codes
    assert MasterComparisonReason.REGION_TEXT_UNTRUSTED in nevada.reason_codes
    assert MasterComparisonReason.SELECTOR_HINT_NOT_EVIDENCE in nevada.reason_codes
    _deny(california)


def test_census_geometry_is_not_operational_airspace():
    result = _assess(
        MasterComparisonCategory.CENSUS_GEOMETRY_VS_OPERATIONAL_AIRSPACE,
        _live("inv-left"),
        _historical("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.CENSUS_GEOMETRY_IS_NOT_FAA_AIRSPACE in result.reason_codes
    assert MasterComparisonReason.FIR_ARTCC_BOUNDARY_NOT_PRESENT in result.reason_codes
    assert MasterComparisonReason.OPERATIONAL_AIRSPACE_SCOPE_ABSENT in result.reason_codes
    assert MasterComparisonReason.NON_SPECIALIST_DOMAIN_NOT_IMPLEMENTED in result.reason_codes
    _deny(result)


def test_intersection_flag_does_not_prove_geometry():
    encounter = snapshot_claims()["CONFIRMED_ENCOUNTER"]
    flagged = replace(
        encounter,
        recorded_flags=(
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.EXACT_INTERSECTION_CONFIRMED, True),
        ),
    )
    flagged_result = replace(
        completed_all_verified(),
        verified_claims=(flagged,),
        evidence_references=(evidence("call-encounters-1", "search_current_encounters"),),
    )
    missing = _assess(
        MasterComparisonCategory.MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION,
        _live("inv-left"),
        _historical("inv-right"),
    )
    present = _assess(
        MasterComparisonCategory.MISSING_GEOMETRY_VS_CLAIMED_INTERSECTION,
        _live("inv-left", result=flagged_result),
        _historical("inv-right"),
    )
    assert missing.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.GEOGRAPHIC_GEOMETRY_ABSENT in missing.reason_codes
    assert MasterComparisonReason.INTERSECTION_FLAG_IS_NOT_GEOMETRY_PROOF in present.reason_codes
    assert present.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.POLYGON_PROVENANCE_ABSENT in present.reason_codes
    _deny(present)


def test_two_decision_invocations_cannot_bypass_the_step_limit():
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _assess(
            MasterComparisonCategory.CURRENT_VS_PERSISTED_EVIDENCE,
            _decision("inv-left", run=_mixed_scope_decision_result()),
            _decision("inv-right"),
        )
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        _assess(
            MasterComparisonCategory.DECISION_LOW_VS_MISSING_RISK,
            _decision("inv-left"),
            _decision("inv-right"),
        )


def test_current_and_persisted_scopes_stay_inside_one_decision_artifact():
    result = _assess(
        MasterComparisonCategory.CURRENT_VS_PERSISTED_EVIDENCE,
        _decision("inv-left", run=_mixed_scope_decision_result()),
        _live("inv-right"),
    )
    absence = _assess(
        MasterComparisonCategory.DECISION_LOW_VS_MISSING_RISK,
        _decision("inv-left"),
        _live("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.CROSS_INVOCATION_DECISION_COMPARISON_NOT_ADMITTED in result.reason_codes
    assert MasterComparisonReason.CURRENT_AND_PERSISTED_SCOPES_BELONG_TO_ONE_ARTIFACT in result.reason_codes
    assert absence.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.ABSENCE_AND_LOW_BELONG_TO_ONE_ARTIFACT in absence.reason_codes
    _deny(result)
    _deny(absence)


def test_live_ops_risk_token_is_not_a_decision_enum():
    _claims, bindings, _payload = decision_present_risk_case()
    decision_run = _decision_run(_claims, bindings)
    matched = _assess(
        MasterComparisonCategory.LIVE_OPS_RISK_VS_DECISION_RISK,
        _live("inv-left", result=_risk_result("HIGH")),
        _decision("inv-right", run=decision_run),
    )
    unmatched = _assess(
        MasterComparisonCategory.LIVE_OPS_RISK_VS_DECISION_RISK,
        _live("inv-left", result=_risk_result("LOW")),
        _decision("inv-right", run=decision_run),
    )
    assert matched.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.RISK_TOKEN_IS_NOT_STORED_RISK_LEVEL in matched.reason_codes
    assert MasterComparisonReason.MATCHING_RISK_TOKEN_IS_NOT_STORED_LEVEL in matched.reason_codes
    assert MasterComparisonReason.MATCHING_RISK_TOKEN_IS_NOT_STORED_LEVEL not in unmatched.reason_codes
    assert MasterComparisonReason.RISK_TOKEN_IS_NOT_STORED_RISK_LEVEL in unmatched.reason_codes
    assert MasterComparisonReason.STORED_RISK_IS_NOT_CURRENT_WITHOUT_PROOF in matched.reason_codes
    _deny(matched)


def test_live_ops_risk_absence_does_not_become_decision_absence_equivalence():
    claims, bindings, _payload = decision_absent_risk_case()
    live_claim = replace(
        snapshot_claims()["RISK_STATE"],
        risk_mode=LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        risk_id=None,
        risk_level=None,
        risk_score=None,
    )
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_RISK_ABSENCE_VS_DECISION_RISK_ABSENCE,
        _live("inv-left", result=replace(completed_all_verified(), verified_claims=(live_claim,))),
        _decision("inv-right", run=_decision_run(claims, bindings)),
    )
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.RECORDED_ABSENCE_IS_NOT_LOW in result.reason_codes
    assert MasterComparisonReason.RISK_SEMANTIC_EQUIVALENCE_NOT_ESTABLISHED in result.reason_codes
    _deny(result)


def test_recommendation_output_stays_advisory_and_is_not_an_atc_instruction():
    result = _assess(
        MasterComparisonCategory.RECOMMENDATION_RECORD_VS_PILOT_INSTRUCTION,
        _decision("inv-left"),
        _live("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.ADVISORY_OUTPUT_IS_NOT_PILOT_INSTRUCTION in result.reason_codes
    assert MasterComparisonReason.ADVISORY_OUTPUT_IS_NOT_ATC_INSTRUCTION in result.reason_codes
    assert result.eligibility.value not in FORBIDDEN_OUTCOMES
    _deny(result)


def test_partial_live_ops_evidence_is_not_network_complete():
    partial = _assess(
        MasterComparisonCategory.PARTIAL_SET_VS_NETWORK_CONCLUSION,
        _live("inv-left", result=live_ops_partially_verified_result()),
        _live("inv-right"),
    )
    assert partial.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.PARTIAL_EVIDENCE_IS_NOT_NETWORK_COMPLETE in partial.reason_codes
    assert MasterComparisonReason.ALL_VERIFIED_IS_NOT_NETWORK_COMPLETE in partial.reason_codes
    assert MasterComparisonPrerequisite.EVIDENCE_COMPLETENESS in partial.missing_prerequisites
    _deny(partial)


def _historical_artifact_input(invocation_id: str, artifact):
    return MasterEvidenceAdmissionInput(
        historical_authorization(
            master_request_id=MASTER,
            invocation_id=invocation_id,
            correlation_id=None,
            specialist_request_id=None,
            requested_evaluation_time_utc=artifact.evaluated_as_of_utc,
        ),
        artifact,
    )


def test_historical_record_id_is_not_a_current_aircraft_id():
    artifact = _historical("inv-left").artifact
    matching = replace(
        artifact,
        verified_claims=artifact.verified_claims + (RecordIdentityClaim(CALL_A, "AC-1"),),
    )
    other = replace(
        artifact,
        verified_claims=artifact.verified_claims + (RecordIdentityClaim(CALL_A, "record-9"),),
    )
    joined = _assess(
        MasterComparisonCategory.HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT,
        _historical_artifact_input("inv-left", matching),
        _live("inv-right"),
    )
    separate = _assess(
        MasterComparisonCategory.HISTORICAL_RECORD_VS_CURRENT_AIRCRAFT,
        _historical_artifact_input("inv-left", other),
        _live("inv-right"),
    )
    assert joined.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.HISTORICAL_RECORD_ID_NOT_AIRCRAFT_ID in joined.reason_codes
    assert MasterComparisonReason.SHARED_RECORD_ID_STRING_NOT_AN_AIRCRAFT_JOIN in joined.reason_codes
    assert MasterComparisonReason.SHARED_RECORD_ID_STRING_NOT_AN_AIRCRAFT_JOIN not in separate.reason_codes
    _deny(joined)


def test_aircraft_position_does_not_prove_a_boundary_crossing():
    latitude = snapshot_claims()["AIRCRAFT_OBSERVED_STATE"]
    positioned = replace(
        completed_all_verified(),
        verified_claims=(latitude,),
        evidence_references=(evidence("call-aircraft-1", "get_aircraft_operational_context"),),
    )
    present = _assess(
        MasterComparisonCategory.AIRCRAFT_POSITION_VS_BOUNDARY_CROSSING,
        _live("inv-left", result=positioned),
        _historical("inv-right"),
    )
    absent = _assess(
        MasterComparisonCategory.AIRCRAFT_POSITION_VS_BOUNDARY_CROSSING,
        _live("inv-left"),
        _historical("inv-right"),
    )
    assert present.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.AIRCRAFT_POSITION_IS_NOT_BOUNDARY_CROSSING in present.reason_codes
    assert MasterComparisonReason.AIRCRAFT_POSITION_ABSENT in absent.reason_codes
    assert absent.eligibility is MasterComparisonEligibility.UNSUPPORTED
    _deny(present)


def test_current_exposure_is_not_a_historical_count_or_sigmet_risk():
    exposure = _assess(
        MasterComparisonCategory.LIVE_OPS_HAZARD_EXPOSURE_VS_HISTORICAL_COUNT,
        _live("inv-left"),
        _historical("inv-right"),
    )
    sigmet = _assess(
        MasterComparisonCategory.CURRENT_SIGMET_VS_HISTORICAL_HAZARD_VERSIONS,
        _live("inv-left"),
        _historical("inv-right"),
    )
    assert exposure.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.CURRENT_EXPOSURE_IS_NOT_HISTORICAL_COUNT in exposure.reason_codes
    assert sigmet.eligibility is MasterComparisonEligibility.UNSUPPORTED
    assert MasterComparisonReason.SIGMET_EXPOSURE_IS_NOT_RISK_CLASSIFICATION in sigmet.reason_codes
    assert MasterComparisonReason.SIGMET_GEOMETRY_NOT_PRESENT in sigmet.reason_codes
    _deny(exposure)


def test_wrong_specialist_pair_is_blocked():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _live("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.BLOCKED
    assert MasterComparisonReason.SPECIALIST_PAIR_NOT_APPLICABLE in result.reason_codes
    _deny(result)


def test_selector_hint_cannot_override_comparability():
    left = _live("inv-left")
    right = _decision("inv-right")
    plain = _assess(MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT, left, right)
    hinted = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        left,
        right,
        aircraft_id_hint="AC-1",
        hazard_id_hint="HZ-1",
        metric_name_hint="distinct_encounter_count",
    )
    assert hinted.eligibility is plain.eligibility
    assert hinted.source_authenticity is plain.source_authenticity
    assert MasterComparisonReason.SELECTOR_HINT_NOT_EVIDENCE in hinted.reason_codes
    assert "facts_agree" not in MasterComparisonRequest.__dataclass_fields__
    with pytest.raises(TypeError):
        MasterComparisonRequest(
            master_request_id=MASTER,
            left_invocation_id="inv-left",
            right_invocation_id="inv-right",
            category=MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
            facts_agree=True,  # type: ignore[call-arg]
        )
    _deny(hinted)


def test_synthetic_ma3_result_cannot_become_comparable_evidence():
    synthetic = MasterEvidenceAdmissionInput(
        live_ops_authorization(
            master_request_id=MASTER,
            invocation_id="inv-left",
            requested_evaluation_time_utc=None,
        ),
        SyntheticWorkerResult(),
    )
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        synthetic,
        _decision("inv-right"),
    )
    assert result.eligibility is MasterComparisonEligibility.BLOCKED
    assert result.left_admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in result.left_admission_reasons
    assert MasterComparisonReason.SYNTHETIC_ARTIFACT_NOT_COMPARABLE_EVIDENCE in result.reason_codes
    assert result.evidence_verified is False
    _deny(result)


def test_caller_built_admission_is_not_accepted():
    admission = admit_master_evidence((_live("inv-left"),)).admissions[0]
    assert isinstance(admission, MasterEvidenceAdmission)
    with pytest.raises(ContractValidationError, match="invalid_admission_inputs"):
        assess_master_evidence_comparability(
            _request(MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT),
            admission,  # type: ignore[arg-type]
            _decision("inv-right"),
        )


def test_live_ops_and_decision_are_admitted_as_one_structural_pair():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left"),
        _decision("inv-right"),
    )
    assert result.left_invocation_id == "inv-left"
    assert result.right_invocation_id == "inv-right"
    assert result.left_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert result.right_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in result.left_admission_reasons
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in result.right_admission_reasons
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION not in result.left_admission_reasons
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    _deny(result)


def test_historical_and_decision_keep_separate_bindings():
    result = _assess(
        MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
        _historical("inv-left", correlation_id="corr-offline-historical"),
        _decision("inv-right"),
    )
    assert result.left_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert result.right_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in result.left_admission_reasons
    assert MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION in result.left_admission_reasons
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT not in result.right_admission_reasons
    assert MasterAdmissionReason.EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE in result.right_admission_reasons
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in result.right_admission_reasons
    assert MasterComparisonReason.ADMISSION_BINDING_UNRESOLVED in result.reason_codes
    _deny(result)


def test_input_order_keeps_left_and_right_admissions_associated():
    historical = _historical("inv-hist", correlation_id="corr-offline-historical")
    decision = _decision("inv-dec")
    forward = assess_master_evidence_comparability(
        _request(
            MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
            left_invocation_id="inv-hist",
            right_invocation_id="inv-dec",
        ),
        historical,
        decision,
    )
    reverse = assess_master_evidence_comparability(
        _request(
            MasterComparisonCategory.QUERY_TIME_VS_OBSERVATION_TIME,
            left_invocation_id="inv-dec",
            right_invocation_id="inv-hist",
        ),
        decision,
        historical,
    )
    assert forward.left_invocation_id == "inv-hist"
    assert forward.right_invocation_id == "inv-dec"
    assert forward.left_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert forward.right_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert reverse.left_invocation_id == "inv-dec"
    assert reverse.right_invocation_id == "inv-hist"
    assert reverse.left_admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert reverse.right_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in reverse.right_admission_reasons
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT not in reverse.left_admission_reasons
    _deny(forward)
    _deny(reverse)


def test_unresolved_admission_does_not_become_verified():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_AIRCRAFT_VS_DECISION_AIRCRAFT,
        _live("inv-left", requested_evaluation_time_utc=SYNTHETIC_UTC),
        _decision("inv-right"),
    )
    assert result.left_admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert result.left_admission_status is not MasterAdmissionStatus.CONTRACT_ONLY
    assert result.eligibility is MasterComparisonEligibility.NOT_ESTABLISHED
    assert MasterComparisonReason.ADMISSION_BINDING_UNRESOLVED in result.reason_codes
    assert result.evidence_verified is False
    _deny(result)


def test_closed_vocabulary_has_no_operational_outcome():
    assert {item.value for item in MasterComparisonEligibility} == {
        "BLOCKED",
        "UNSUPPORTED",
        "NOT_ESTABLISHED",
        "CONTRACT_CHECKABLE_ONLY",
    }
    assert {item.value for item in MasterComparisonEligibility}.isdisjoint(FORBIDDEN_OUTCOMES)
    assert {item.value for item in MasterComparisonReason}.isdisjoint(FORBIDDEN_OUTCOMES)
    assert MasterSpecialist.DECISION.value == "DECISION"


def test_denial_invariants_cannot_be_overwritten():
    result = _assess(
        MasterComparisonCategory.LIVE_OPS_RECOMMENDATIONS_VS_DECISION_RECOMMENDATIONS,
        _live("inv-left"),
        _decision("inv-right"),
    )
    assert MasterComparisonReason.RECOMMENDATION_SET_EQUIVALENCE_NOT_ESTABLISHED in result.reason_codes
    with pytest.raises(ContractValidationError):
        replace(result, evidence_verified=True)
    with pytest.raises(ContractValidationError):
        replace(result, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError):
        replace(result, source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED, established_prerequisites=(
            MasterComparisonPrerequisite.SOURCE_AUTHENTICITY,
        ))
    _deny(result)


def test_advisory_claim_constant_remains_advisory_only():
    assert DecisionAdvisoryAuthority.ADVISORY_ONLY.value == "ADVISORY_ONLY"
    assert RecommendationActionType.EVALUATE_DIVERSION.value == "EVALUATE_DIVERSION"
    assert RecommendationActionClaim.__dataclass_fields__["advisory_authority"]
