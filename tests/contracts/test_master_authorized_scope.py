"""Effective-scope intersection stays descriptive and fail-closed."""

from __future__ import annotations

from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_HAZARD_IDS_MAX
from wilvor_ai.master_authorized_scope import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    EXECUTION_GRANT_AVAILABLE,
    HAZARD_IDS_MAX,
    PRODUCTION_PRINCIPAL_AVAILABLE,
    POLICY_INTEGRATION_HANDOFF,
    TARGET_RESOLVER_INTEGRATION_HANDOFF,
    CandidateKind,
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    GeographicMatchKind,
    HistoricalArgumentSource,
    LocalTestHostScope,
    ScopeInputOrigin,
    SourceCoverage,
    bind_local_test_host,
    promote_effective_scope_to_execution_grant,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget, server_read_resources
from wilvor_ai.master_routing import MAX_DECISION_STEPS, MAX_HISTORICAL_WINDOW, MAX_ROUTE_STEPS


ACCEPTED = "2026-10-27T16:00:00Z"
OBSERVED = "2026-10-27T17:30:00Z"
EVALUATED = "2026-10-27T18:00:00Z"
DEADLINE = "2026-10-27T20:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"
UNBOUND_START = "2026-09-01T00:00:00Z"
UNBOUND_END = "2026-10-01T00:00:00Z"
LONG_START = "2026-10-01T18:00:00Z"
LONG_END = "2026-10-27T18:00:00Z"
EXPIRES = "2026-10-27T17:00:00Z"

_LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
_HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
_RISK = AgentCapability.EXPLAIN_EXISTING_RISK
_RECOMMENDATIONS = AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS


def _hints(**overrides: object) -> EffectiveScopeRequestHints:
    payload: dict[str, object] = {
        "master_request_id": "req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "correlation_id": "corr-1",
        "idempotency_key": "idem-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": _LIVE,
        "tool_name": "search_current_hazards",
        "resource_id": "hazards",
        "operation": ReadOperation.QUERY,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "arguments": (("region", "CA"),),
        "location_hint": "California",
        "census_region_name": "California",
        "sigmet_region_string": "Oakland FIR",
        "entity_id": "AC1",
    }
    payload.update(overrides)
    return EffectiveScopeRequestHints(**payload)


def _host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": _LIVE,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)


def _decision_hints(**overrides: object) -> EffectiveScopeRequestHints:
    payload: dict[str, object] = {
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": _RISK,
        "tool_name": "get_current_risk_evidence",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "arguments": (("aircraft_id", "AC1"),),
        "aircraft_id": "AC1",
        "entity_id": "AC1",
        "model_target_id": "AC1",
        "location_hint": None,
        "census_region_name": None,
        "sigmet_region_string": None,
    }
    payload.update(overrides)
    return _hints(**payload)


def _decision_host(**overrides: object):
    payload: dict[str, object] = {
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": _RISK,
        "validated_aircraft_ids": frozenset({"AC1"}),
        "row_entity_id": "AC1",
        "row_present": True,
        "risk_presence": RiskPresence.PRESENT,
        "stored_risk_level": StoredRiskLevel.MEDIUM,
    }
    payload.update(overrides)
    return _host(**payload)


def test_module_constants_stay_closed():
    assert PRODUCTION_PRINCIPAL_AVAILABLE is False
    assert AUTHENTICATED_SOURCE_READ_AVAILABLE is False
    assert EXECUTION_GRANT_AVAILABLE is False
    assert HAZARD_IDS_MAX == LIVE_OPS_HAZARD_IDS_MAX == 25
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert MAX_HISTORICAL_WINDOW.days == 7
    assert "assess_master_execution_policy" in POLICY_INTEGRATION_HANDOFF
    assert "resolve_master_decision_target" in TARGET_RESOLVER_INTEGRATION_HANDOFF
    assert "MasterExecutionScope" in POLICY_INTEGRATION_HANDOFF


def test_live_ops_intersection_describes_bounds_without_coverage_or_grant():
    decision = resolve_master_authorized_scope(_hints(), _host())

    assert decision.status is EffectiveScopeStatus.BOUNDED_DESCRIBED
    assert decision.denial_reasons == ()
    assert decision.master_request_id == "req-1"
    assert decision.planning_session_id == "plan-1"
    assert decision.step_id == "step-1"
    assert decision.invocation_id == "inv-1"
    assert decision.correlation_id == "corr-1"
    assert decision.idempotency_key == "idem-1"
    assert decision.principal_ref == "principal-1"
    assert decision.policy_revision == "policy-1"
    assert decision.accepted_at_utc == ACCEPTED
    assert decision.deadline_utc == DEADLINE
    assert decision.observation_utc == OBSERVED
    assert decision.evaluation_utc == EVALUATED
    assert decision.observation_utc != decision.accepted_at_utc
    assert decision.evaluation_utc != decision.deadline_utc
    budget = server_read_budget(MasterSpecialist.LIVE_OPS)
    assert (decision.max_executions, decision.max_model_turns, decision.max_tool_calls) == budget
    assert decision.max_route_steps == MAX_ROUTE_STEPS
    assert decision.max_decision_steps == MAX_DECISION_STEPS
    assert decision.resource_scope.approved_operation is ReadOperation.QUERY
    assert decision.resource_scope.requested_operation is ReadOperation.QUERY
    assert ("hazards", ReadOperation.QUERY) in server_read_resources(
        MasterSpecialist.LIVE_OPS, "search_current_hazards"
    )
    assert decision.resource_scope.population_entity_ids == ("AC1",)
    assert decision.resource_scope.population_ceiling == ("AC1",)
    assert decision.resource_scope.all_aircraft_scan is False
    assert decision.resource_scope.keyed_operations_only is True
    assert decision.region_scope.normalized_location_code == "CA"
    assert decision.region_scope.effective_state_codes == ("CA",)
    assert decision.region_scope.approved_state_codes == ("CA",)
    assert len(decision.region_scope.approved_state_codes) != 50
    assert decision.region_scope.geographic_matches == (
        GeographicMatchKind.LOCATION_NAME_NORMALIZED,
        GeographicMatchKind.REGIONAL_STRING,
        GeographicMatchKind.CENSUS_NAME,
    )
    assert decision.region_scope.geographic_coverage_established is False
    assert decision.region_scope.sigmet_polygon_intersection_established is False
    assert decision.region_scope.normalization_establishes_coverage is False
    assert decision.region_scope.source_coverage is SourceCoverage.UNKNOWN
    assert decision.region_scope.broad_us_default_applied is False
    assert decision.historical_window.eligible_start_utc is None
    assert decision.historical_window.tool_argument_source is HistoricalArgumentSource.NONE
    assert decision.historical_window.copied_into_specialist_request is False
    assert decision.specialist_request is None
    assert decision.input_origins.request_hints is ScopeInputOrigin.DESCRIPTIVE
    assert decision.input_origins.intersection is ScopeInputOrigin.EVALUATED
    assert decision.input_origins.host_material is ScopeInputOrigin.HOST_ISSUED
    assert decision.input_origins.consumed_grant is None
    assert decision.input_origins.source_authentication is None
    assert decision.production_principal_issued is False
    assert decision.authenticated_source_read is False
    assert decision.execution_granted is False
    assert decision.dispatch_permitted is False
    with pytest.raises(ContractValidationError, match="effective_scope_is_not_execution_grant"):
        bool(decision)
    with pytest.raises(ContractValidationError, match="effective_scope_is_not_execution_grant"):
        promote_effective_scope_to_execution_grant(decision)


def test_california_normalization_and_sigmet_string_are_not_polygon_coverage():
    decision = resolve_master_authorized_scope(_hints(), _host())

    assert decision.region_scope.normalized_location_code == "CA"
    assert decision.region_scope.source_coverage is SourceCoverage.UNKNOWN
    assert GeographicMatchKind.CENSUS_NAME in decision.region_scope.geographic_matches
    assert GeographicMatchKind.REGIONAL_STRING in decision.region_scope.geographic_matches
    assert decision.region_scope.sigmet_polygon_intersection_established is False


def test_false_geographic_coverage_claim_denies():
    hints = _hints(claims_geographic_coverage=True, claims_exact_sigmet_polygon=True)
    decision = resolve_master_authorized_scope(hints, _host())

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "FALSE_GEOGRAPHIC_COVERAGE_CLAIM" in {reason.value for reason in decision.denial_reasons}
    assert decision.region_scope.normalized_location_code == "CA"
    assert decision.region_scope.effective_state_codes == ()
    assert decision.region_scope.geographic_coverage_established is False
    assert decision.region_scope.sigmet_polygon_intersection_established is False
    assert decision.region_scope.source_coverage is SourceCoverage.UNKNOWN
    assert decision.execution_granted is False


def test_cross_region_escalation_does_not_expand_the_approved_set():
    hints = _hints(claimed_state_codes=("TX",), location_hint="California")
    decision = resolve_master_authorized_scope(hints, _host())

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "CROSS_REGION_ESCALATION" in {reason.value for reason in decision.denial_reasons}
    assert decision.region_scope.effective_state_codes == ()
    assert "TX" not in decision.region_scope.effective_state_codes
    assert decision.region_scope.approved_state_codes == ("CA",)
    assert decision.region_scope.broad_us_default_applied is False
    assert len(decision.region_scope.approved_state_codes) != 50


def test_broad_us_token_and_absent_permission_do_not_default_to_all_states():
    claimed = resolve_master_authorized_scope(_hints(claimed_state_codes=("US",)), _host())
    absent = resolve_master_authorized_scope(_hints(), None)

    assert "BROAD_US_ACCESS_REFUSED" in {reason.value for reason in claimed.denial_reasons}
    assert claimed.region_scope.approved_state_codes == ("CA",)
    assert claimed.region_scope.effective_state_codes == ()
    assert claimed.region_scope.broad_us_default_applied is False
    assert absent.status is EffectiveScopeStatus.DENIED
    assert "PERMISSION_SCOPE_ABSENT" in {reason.value for reason in absent.denial_reasons}
    assert "ENTITLEMENT_ABSENT" in {reason.value for reason in absent.denial_reasons}
    assert absent.region_scope.approved_state_codes == ()
    assert absent.region_scope.effective_state_codes == ()
    assert absent.principal_ref is None
    assert absent.input_origins.host_material is None
    assert len(absent.region_scope.approved_state_codes) != 50


def test_invalid_region_constraint_cannot_be_sealed():
    with pytest.raises(ContractValidationError, match="broad_us_access_refused"):
        _host(region_state_codes=frozenset({"US"}))
    with pytest.raises(ContractValidationError, match="invalid_region_constraint"):
        _host(region_state_codes=frozenset())


def test_wildcard_scan_and_all_aircraft_request_do_not_open_a_scan():
    wildcard = resolve_master_authorized_scope(_hints(resource_id="*"), _host())
    scanned = resolve_master_authorized_scope(
        _hints(operation=ReadOperation.SCAN, resource_id="hazards"),
        _host(),
    )
    fleet = resolve_master_authorized_scope(_hints(requested_all_aircraft=True), _host())
    network = resolve_master_authorized_scope(
        _hints(tool_name="get_observed_network_state", resource_id="aircraft", arguments=()),
        _host(),
    )

    for decision in (wildcard, scanned, fleet, network):
        assert decision.status is EffectiveScopeStatus.DENIED
        assert decision.resource_scope.approved_operation is None
        assert decision.resource_scope.all_aircraft_scan is False
        assert decision.resource_scope.population_entity_ids == ()
        assert decision.execution_granted is False
    assert "WILDCARD_RESOURCE" in {reason.value for reason in wildcard.denial_reasons}
    assert "SCAN_NOT_PERMITTED" in {reason.value for reason in scanned.denial_reasons}
    assert "ALL_AIRCRAFT_SCAN_REFUSED" in {reason.value for reason in fleet.denial_reasons}
    assert "ALL_AIRCRAFT_SCAN_REFUSED" in {reason.value for reason in network.denial_reasons}


def test_historical_policy_window_is_eligible_and_unbound_plan_window_is_not_copied():
    hints = _hints(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=_HISTORY,
        tool_name="summarize_historical_encounters",
        resource_id="historical.encounters",
        arguments=(
            ("aircraft_id", "AC1"),
            ("end_utc", HIST_END),
            ("start_utc", HIST_START),
        ),
        aircraft_id="AC1",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
        unbound_plan_window_start_utc=UNBOUND_START,
        unbound_plan_window_end_utc=UNBOUND_END,
    )
    host = _host(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=_HISTORY,
        historical_interval_id="hist-interval-1",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
    )
    decision = resolve_master_authorized_scope(hints, host)

    assert decision.status is EffectiveScopeStatus.BOUNDED_DESCRIBED
    assert decision.historical_window.eligible_start_utc == HIST_START
    assert decision.historical_window.eligible_end_utc == HIST_END
    assert decision.historical_window.tool_argument_source is HistoricalArgumentSource.HOST_APPROVED_POLICY
    assert decision.historical_window.unbound_plan_start_utc == UNBOUND_START
    assert decision.historical_window.unbound_plan_end_utc == UNBOUND_END
    assert (decision.historical_window.eligible_start_utc, decision.historical_window.eligible_end_utc) != (
        UNBOUND_START,
        UNBOUND_END,
    )
    assert decision.historical_window.plan_window_used_as_tool_arguments is False
    assert decision.historical_window.copied_into_specialist_request is False
    assert decision.specialist_request is None
    assert decision.historical_window.within_seven_days is True
    assert decision.region_scope.geographic_coverage_established is False
    assert decision.execution_granted is False


def test_unbounded_historical_window_is_not_clipped_to_a_short_policy():
    hints = _hints(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=_HISTORY,
        tool_name="summarize_historical_encounters",
        resource_id="historical.encounters",
        arguments=(
            ("aircraft_id", "AC1"),
            ("end_utc", HIST_END),
            ("start_utc", HIST_START),
        ),
        aircraft_id="AC1",
        historical_start_utc=LONG_START,
        historical_end_utc=LONG_END,
        location_hint=None,
        census_region_name=None,
        sigmet_region_string=None,
    )
    host = _host(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=_HISTORY,
        historical_interval_id="hist-interval-1",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
    )
    decision = resolve_master_authorized_scope(hints, host)

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "UNBOUNDED_HISTORICAL_WINDOW" in {reason.value for reason in decision.denial_reasons}
    assert decision.historical_window.eligible_start_utc is None
    assert decision.historical_window.eligible_end_utc is None
    assert decision.historical_window.tool_argument_source is HistoricalArgumentSource.NONE
    assert decision.specialist_request is None
    assert decision.historical_window.copied_into_specialist_request is False


def test_unbound_plan_window_without_host_policy_is_not_a_tool_interval():
    hints = _hints(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=_HISTORY,
        tool_name="summarize_historical_encounters",
        resource_id="historical.encounters",
        arguments=(
            ("aircraft_id", "AC1"),
            ("end_utc", HIST_END),
            ("start_utc", HIST_START),
        ),
        aircraft_id="AC1",
        historical_start_utc=HIST_START,
        historical_end_utc=HIST_END,
        unbound_plan_window_start_utc=HIST_START,
        unbound_plan_window_end_utc=HIST_END,
        copy_unbound_plan_window=True,
        location_hint=None,
        census_region_name=None,
        sigmet_region_string=None,
    )
    decision = resolve_master_authorized_scope(
        hints,
        _host(specialist=MasterSpecialist.HISTORICAL_ANALYTICS, capability=_HISTORY),
    )

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "HISTORICAL_WINDOW_NOT_HOST_APPROVED" in {reason.value for reason in decision.denial_reasons}
    assert "UNBOUND_PLAN_WINDOW_NOT_TOOL_ARGUMENTS" in {
        reason.value for reason in decision.denial_reasons
    }
    assert decision.historical_window.eligible_start_utc is None
    assert decision.specialist_request is None
    assert decision.historical_window.plan_window_used_as_tool_arguments is False


def test_wrong_entity_is_denied():
    hints = _hints(
        tool_name="get_aircraft_operational_context",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", "AC9"),),
        aircraft_id="AC9",
        entity_id="AC9",
        location_hint=None,
        census_region_name=None,
        sigmet_region_string=None,
    )
    decision = resolve_master_authorized_scope(hints, _host())

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "WRONG_ENTITY" in {reason.value for reason in decision.denial_reasons}
    assert decision.resource_scope.population_entity_ids == ()
    assert decision.decision_target_scope.described_aircraft_id is None


def test_ambiguous_callsign_is_not_resolved_to_one_aircraft():
    hints = _hints(
        tool_name="find_aircraft_by_callsign",
        resource_id="aircraft",
        arguments=(("callsign", "UAL1"),),
        callsign="UAL1",
        entity_id=None,
        location_hint=None,
        census_region_name=None,
        sigmet_region_string=None,
    )
    host = _host(
        population_entity_ids=frozenset({"AC1", "AC2"}),
        callsign_aircraft_ids=frozenset({"AC1", "AC2"}),
    )
    decision = resolve_master_authorized_scope(hints, host)

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "AMBIGUOUS_CALLSIGN" in {reason.value for reason in decision.denial_reasons}
    assert decision.decision_target_scope.described_aircraft_id is None
    assert CandidateKind.CALLSIGN in decision.decision_target_scope.candidate_kinds
    assert decision.decision_target_scope.promoted_from_callsign is False


def test_callsign_model_discovery_and_recommendation_are_not_promoted():
    callsign_only = resolve_master_authorized_scope(
        _decision_hints(
            aircraft_id=None,
            entity_id=None,
            model_target_id=None,
            arguments=(("aircraft_id", "AC1"),),
            callsign="UAL1",
        ),
        _decision_host(callsign_aircraft_ids=frozenset({"AC1"}), row_callsign="UAL1"),
    )
    model_conflict = resolve_master_authorized_scope(
        _decision_hints(model_target_id="AC2"),
        _decision_host(),
    )
    discovery_conflict = resolve_master_authorized_scope(
        _decision_hints(live_ops_discovery_id="AC3"),
        _decision_host(),
    )
    recommendation = resolve_master_authorized_scope(
        _decision_hints(
            capability=_RECOMMENDATIONS,
            tool_name="get_persisted_airport_candidate_evidence",
            resource_id="recommendations",
            arguments=(("recommendation_id", "R1"),),
            recommendation_id="R1",
            model_target_id=None,
        ),
        _decision_host(
            capability=_RECOMMENDATIONS,
            permitted_recommendation_ids=frozenset(),
            validated_aircraft_ids=frozenset({"AC1", "R1"}),
        ),
    )

    assert callsign_only.decision_target_scope.described_aircraft_id is None
    assert "CANDIDATE_PROMOTION_REFUSED" in {reason.value for reason in callsign_only.denial_reasons}
    assert callsign_only.decision_target_scope.promoted_from_callsign is False
    assert model_conflict.decision_target_scope.described_aircraft_id is None
    assert CandidateKind.MODEL_TARGET in model_conflict.decision_target_scope.candidate_kinds
    assert model_conflict.decision_target_scope.promoted_from_model_target is False
    assert discovery_conflict.decision_target_scope.promoted_from_live_ops_discovery is False
    assert "CANDIDATE_PROMOTION_REFUSED" in {reason.value for reason in discovery_conflict.denial_reasons}
    assert recommendation.decision_target_scope.described_aircraft_id is None
    assert recommendation.decision_target_scope.described_recommendation_id is None
    assert "PERSISTED_RECOMMENDATION_NOT_SEPARATELY_PERMITTED" in {
        reason.value for reason in recommendation.denial_reasons
    }
    assert recommendation.decision_target_scope.promoted_from_persisted_recommendation is False


def test_decision_intersection_keeps_aircraft_and_recommendation_permissions_separate():
    decision = resolve_master_authorized_scope(
        _decision_hints(
            capability=_RECOMMENDATIONS,
            tool_name="get_persisted_airport_candidate_evidence",
            resource_id="recommendations",
            arguments=(("recommendation_id", "R1"),),
            recommendation_id="R1",
            model_target_id="AC1",
        ),
        _decision_host(
            capability=_RECOMMENDATIONS,
            permitted_recommendation_ids=frozenset({"R1"}),
        ),
    )

    assert decision.status is EffectiveScopeStatus.BOUNDED_DESCRIBED
    assert decision.decision_target_scope.described_aircraft_id == "AC1"
    assert decision.decision_target_scope.described_recommendation_id == "R1"
    assert decision.decision_target_scope.aircraft_id_in_validated_intersection is True
    assert decision.decision_target_scope.separate_recommendation_permission_in_intersection is True
    assert decision.decision_target_scope.promoted_from_model_target is False
    assert decision.decision_target_scope.promoted_from_persisted_recommendation is False
    assert decision.decision_target_scope.target_authorization_granted is False
    assert decision.resource_scope.approved_operation is ReadOperation.GET_ITEM
    assert decision.execution_granted is False
    assert decision.authenticated_source_read is False


def test_absent_row_is_not_a_stored_low_risk():
    absent = resolve_master_authorized_scope(
        _decision_hints(),
        _decision_host(row_present=False, risk_presence=RiskPresence.ABSENT, stored_risk_level=None),
    )
    missing = resolve_master_authorized_scope(
        _decision_hints(missing_risk_means_low=True),
        _decision_host(risk_presence=RiskPresence.ABSENT, stored_risk_level=None),
    )
    stored_low = resolve_master_authorized_scope(
        _decision_hints(),
        _decision_host(stored_risk_level=StoredRiskLevel.LOW),
    )

    assert absent.status is EffectiveScopeStatus.DENIED
    assert "ABSENT_ROW" in {reason.value for reason in absent.denial_reasons}
    assert absent.decision_target_scope.risk_presence is RiskPresence.ABSENT
    assert absent.decision_target_scope.stored_risk_level is None
    assert absent.decision_target_scope.missing_risk_recorded_as_low is False
    assert missing.decision_target_scope.stored_risk_level is None
    assert "MISSING_RISK_RECORD" in {reason.value for reason in missing.denial_reasons}
    assert "MISSING_RISK_IS_NOT_LOW" in {reason.value for reason in missing.denial_reasons}
    assert missing.decision_target_scope.missing_risk_recorded_as_low is False
    assert stored_low.status is EffectiveScopeStatus.BOUNDED_DESCRIBED
    assert stored_low.decision_target_scope.risk_presence is RiskPresence.PRESENT
    assert stored_low.decision_target_scope.stored_risk_level is StoredRiskLevel.LOW
    assert stored_low.decision_target_scope.missing_risk_recorded_as_low is False
    with pytest.raises(ContractValidationError, match="absent_risk_is_not_low_risk"):
        _decision_host(risk_presence=RiskPresence.ABSENT, stored_risk_level=StoredRiskLevel.LOW)


def test_expired_source_data_denies_without_authenticating_the_row():
    decision = resolve_master_authorized_scope(
        _decision_hints(),
        _decision_host(source_expires_utc=EXPIRES),
    )

    assert decision.status is EffectiveScopeStatus.DENIED
    assert "EXPIRED_SOURCE_DATA" in {reason.value for reason in decision.denial_reasons}
    assert decision.authenticated_source_read is False
    assert decision.region_scope.source_coverage is SourceCoverage.UNKNOWN
    assert decision.execution_granted is False


def test_production_input_cannot_seal_a_host_or_describe_a_scope(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("WILVOR_TEST_MODE", "1")
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user"}}}},
        "headers": {"Authorization": "Bearer forged"},
        "ENVIRONMENT": "test",
    }

    for host in (event, True, __import__("os").environ, "principal-1"):
        decision = resolve_master_authorized_scope(_hints(), host)
        assert decision.status is EffectiveScopeStatus.DENIED
        assert "PRODUCTION_INPUT_NOT_A_HOST" in {reason.value for reason in decision.denial_reasons}
        assert decision.production_principal_issued is False
        assert decision.execution_granted is False
        assert decision.region_scope.approved_state_codes == ()
    with pytest.raises(ContractValidationError, match="production_input_not_a_host"):
        _host(region_state_codes={"CA": True})
    untouched = resolve_master_authorized_scope(_hints(), None)
    assert untouched.status is EffectiveScopeStatus.DENIED
    assert "PERMISSION_SCOPE_ABSENT" in {reason.value for reason in untouched.denial_reasons}


def test_closed_flags_and_direct_construction_fail():
    decision = resolve_master_authorized_scope(_hints(), _host())
    with pytest.raises(ContractValidationError):
        replace(decision, execution_granted=True)
    with pytest.raises(ContractValidationError):
        replace(decision, authenticated_source_read=True)
    with pytest.raises(ContractValidationError, match="host_not_issued"):
        LocalTestHostScope(
            principal_ref="principal-1",
            policy_revision="policy-1",
            specialist=MasterSpecialist.LIVE_OPS,
            purpose=MasterPurpose.ANSWER,
            capability=_LIVE,
            population_entity_ids=frozenset({"AC1"}),
            region_state_codes=frozenset({"CA"}),
            observation_utc=OBSERVED,
            evaluation_utc=EVALUATED,
        )
    with pytest.raises(ContractValidationError, match="geographic_coverage_not_established"):
        replace(decision.region_scope, geographic_coverage_established=True)
    with pytest.raises(ContractValidationError, match="sigmet_polygon_intersection_not_established"):
        replace(decision.region_scope, sigmet_polygon_intersection_established=True)
