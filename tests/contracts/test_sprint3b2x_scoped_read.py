"""Scope, budget, and guarded read join stays offline and fail-closed."""

from __future__ import annotations

from dataclasses import replace
from threading import Barrier, Thread

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    bind_local_test_host,
    promote_effective_scope_to_execution_grant,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_host_scoped_read_integration import (
    BLOCKED_IO_IS_INTERRUPTIBLE,
    BLOCKED_IO_NOT_INTERRUPTIBLE,
    LOCAL_READ_IS_FLIGHT_SAFETY,
    LOCAL_READ_IS_SOURCE_AUTHENTICATION,
    LOCAL_READ_IS_VERIFIED_ZERO,
    PRODUCTION_HOST_SCOPED_READ_ENABLED,
    SCOPED_READ_ENFORCEMENT_SEQUENCE,
    SCOPE_DESCRIPTION_IS_A_GRANT,
    WITHIN_ALLOWLIST_IS_A_PERMIT,
    NonproductionScopedReadHarness,
    ScopedReadJoinRefusal,
    allowlist_status_is_not_a_permit,
    issue_production_scoped_read,
    open_production_scoped_read,
)
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    MasterReadCheckStatus,
    ReadOperation,
    promote_read_check_to_dispatch_permit,
    server_read_budget,
)
from wilvor_ai.master_runtime_budget import (
    BudgetDenialReason,
    BudgetEvaluation,
    StartedTaskDisposition,
    bind_deployed_host_budget,
    promote_budget_decision_to_production_grant,
)
from wilvor_ai.master_trusted_read_executor import (
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadBoundaryDenial,
    ReadExecutionOutcome,
    bind_nonproduction_read_registry,
    issue_production_read_invocation,
    open_production_executor,
)


HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
LIVE = MasterSpecialist.LIVE_OPS
DECISION = MasterSpecialist.DECISION
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RETRIEVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
ACCEPTED = "2026-10-27T16:00:00Z"
OBSERVED = "2026-10-27T17:30:00Z"
EVALUATED = "2026-10-27T18:00:00Z"
DEADLINE_HINT = "2026-10-27T20:00:00Z"
HARNESS_NOW = "2026-10-27T18:30:00Z"
SOURCE_OBSERVED = "2026-10-27T12:00:00Z"
HIST_START = "2026-10-20T18:00:00Z"
HIST_END = "2026-10-27T18:00:00Z"
EIGHT_DAY_START = "2026-10-19T18:00:00Z"
UNBOUND_START = "2026-09-01T00:00:00Z"
UNBOUND_END = "2026-10-01T00:00:00Z"
SECRET_TOKEN = "secret-page-token"


class _Explosive:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError(name)


def _historical_hints(**overrides: object) -> EffectiveScopeRequestHints:
    payload: dict[str, object] = {
        "master_request_id": "req-hist",
        "planning_session_id": "plan-hist",
        "step_id": "step-hist",
        "invocation_id": "inv-hist",
        "correlation_id": "corr-hist",
        "idempotency_key": "idem-hist",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "tool_name": "list_historical_encounters",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE_HINT,
        "arguments": (
            ("aircraft_id", "AC1"),
            ("end_utc", HIST_END),
            ("start_utc", HIST_START),
        ),
        "location_hint": "California",
        "census_region_name": "California",
        "sigmet_region_string": "Oakland FIR",
        "entity_id": "AC1",
        "aircraft_id": "AC1",
        "historical_start_utc": HIST_START,
        "historical_end_utc": HIST_END,
        "unbound_plan_window_start_utc": UNBOUND_START,
        "unbound_plan_window_end_utc": UNBOUND_END,
    }
    payload.update(overrides)
    return EffectiveScopeRequestHints(**payload)  # type: ignore[arg-type]


def _historical_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA", "NV"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "historical_interval_id": "hist-interval-1",
        "historical_start_utc": HIST_START,
        "historical_end_utc": HIST_END,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _live_hints(**overrides: object) -> EffectiveScopeRequestHints:
    payload: dict[str, object] = {
        "master_request_id": "req-live",
        "planning_session_id": "plan-live",
        "step_id": "step-live",
        "invocation_id": "inv-live",
        "correlation_id": "corr-live",
        "idempotency_key": "idem-live",
        "specialist": LIVE,
        "purpose": MasterPurpose.ANSWER,
        "capability": RETRIEVE,
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE_HINT,
        "arguments": (("aircraft_id", "AC1"),),
        "location_hint": "California",
        "census_region_name": "California",
        "sigmet_region_string": "Oakland FIR",
        "entity_id": "AC1",
        "aircraft_id": "AC1",
    }
    payload.update(overrides)
    return EffectiveScopeRequestHints(**payload)  # type: ignore[arg-type]


def _live_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": LIVE,
        "purpose": MasterPurpose.ANSWER,
        "capability": RETRIEVE,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA", "NV"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _decision_hints(**overrides: object) -> EffectiveScopeRequestHints:
    payload: dict[str, object] = {
        "master_request_id": "req-dec",
        "planning_session_id": "plan-dec",
        "step_id": "step-dec",
        "invocation_id": "inv-dec",
        "correlation_id": "corr-dec",
        "idempotency_key": "idem-dec",
        "specialist": DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": RISK,
        "tool_name": "get_current_risk_evidence",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE_HINT,
        "arguments": (("aircraft_id", "AC1"),),
        "aircraft_id": "AC1",
        "entity_id": "AC1",
    }
    payload.update(overrides)
    return EffectiveScopeRequestHints(**payload)  # type: ignore[arg-type]


def _decision_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": "principal-1",
        "policy_revision": "policy-1",
        "specialist": DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": RISK,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "validated_aircraft_ids": frozenset({"AC1"}),
        "row_entity_id": "AC1",
        "row_present": True,
        "risk_presence": RiskPresence.PRESENT,
        "stored_risk_level": StoredRiskLevel.MEDIUM,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _page(**overrides: object) -> NonproductionReadPage:
    values: dict[str, object] = {
        "record_ids": ("row-1", "row-2"),
        "source_name": "historical.encounters",
        "source_version": "version-1",
        "query_id": "query-1",
        "page_index": 0,
        "source_observed_at_utc": SOURCE_OBSERVED,
    }
    values.update(overrides)
    return NonproductionReadPage(**values)  # type: ignore[arg-type]


def _harness(read: object, **spec: object) -> NonproductionScopedReadHarness:
    values: dict[str, object] = {
        "specialist": HISTORY,
        "tool_name": "list_historical_encounters",
        "capability": HISTORICAL,
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
    }
    values.update(spec)
    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=values["specialist"],  # type: ignore[arg-type]
                tool_name=values["tool_name"],  # type: ignore[arg-type]
                capability=values["capability"],  # type: ignore[arg-type]
                resource_id=values["resource_id"],  # type: ignore[arg-type]
                operation=values["operation"],  # type: ignore[arg-type]
                read=read,
            ),
        )
    )
    return NonproductionScopedReadHarness(registry, now_utc=HARNESS_NOW)


def _codes(result: object) -> set[str]:
    scope = getattr(result, "scope")
    return {reason.value for reason in scope.denial_reasons}


def _closed(result: object) -> None:
    assert result.flight_safety_conclusion is False
    assert result.verified_zero is False
    assert result.source_authenticity_established is False
    assert result.production_grant_issued is False
    assert result.production_authority_enabled is False
    assert result.geographic_coverage_established is False
    assert result.sigmet_polygon_intersection_established is False
    assert result.within_allowlist_used_as_permit is False
    assert result.scope_description_is_grant is False
    assert result.blocked_io_interruptible is False
    with pytest.raises(ContractValidationError, match="scoped_read_is_not_a_flight_safety_conclusion"):
        bool(result)
    if result.scope is not None:
        assert result.scope.execution_granted is False
        assert result.scope.authenticated_source_read is False
        assert result.scope.region_scope.sigmet_polygon_intersection_established is False
        assert result.scope.region_scope.geographic_coverage_established is False
        assert result.scope.decision_target_scope.missing_risk_recorded_as_low is False
        assert result.scope.decision_target_scope.target_authorization_granted is False
        with pytest.raises(ContractValidationError, match="effective_scope_is_not_execution_grant"):
            bool(result.scope)


def test_enforcement_sequence_and_production_surface_stay_closed():
    assert PRODUCTION_HOST_SCOPED_READ_ENABLED is False
    assert WITHIN_ALLOWLIST_IS_A_PERMIT is False
    assert SCOPE_DESCRIPTION_IS_A_GRANT is False
    assert LOCAL_READ_IS_SOURCE_AUTHENTICATION is False
    assert LOCAL_READ_IS_VERIFIED_ZERO is False
    assert LOCAL_READ_IS_FLIGHT_SAFETY is False
    assert BLOCKED_IO_IS_INTERRUPTIBLE is False
    assert "cannot interrupt" in BLOCKED_IO_NOT_INTERRUPTIBLE
    assert "downstream_timeout_s" in BLOCKED_IO_NOT_INTERRUPTIBLE
    assert "TERMINAL_PARTIAL" in BLOCKED_IO_NOT_INTERRUPTIBLE
    assert "not a MasterExecutionLedger nonce" in BLOCKED_IO_NOT_INTERRUPTIBLE
    for name in (
        "resolve_master_authorized_scope",
        "server_read_tools",
        "server_read_argument_names",
        "server_read_resources",
        "server_read_budget",
        "check_master_read_resource",
        "within_allowlist_is_not_a_permit",
        "RuntimeBudgetSession.begin_read",
        "RuntimeBudgetSession.mark_entered",
        "MasterTrustedReadExecutor.execute",
        "RuntimeBudgetSession.finish_started",
    ):
        assert name in SCOPED_READ_ENFORCEMENT_SEQUENCE
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_scoped_read()
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_scoped_read()
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_read_invocation()
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_executor()
    deployed = bind_deployed_host_budget(
        host_event={"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "forged"}}}}},
        clock=_Explosive(),
        remaining_runtime=_Explosive(),
    )
    assert deployed.enabled is False
    assert deployed.deadline_utc is None
    with pytest.raises(ContractValidationError, match="deployed_host_budget_is_not_a_grant"):
        bool(deployed)


def test_historical_query_runs_the_registry_adapter_inside_scope_and_budget():
    seen: dict[str, object] = {}
    calls: list[int] = []

    def read(arguments: dict[str, object], _cancellation: object) -> NonproductionReadPage:
        calls.append(1)
        seen.update(arguments)
        return _page()

    hints = _historical_hints()
    result = _harness(read).run(
        hints,
        _historical_host(),
        tool_call_id="tool-hist",
        action_id="read-hist",
        policy_check=_Explosive(),
    )

    assert calls == [1]
    assert result.refusal is None
    assert result.adapter_invoked is True
    assert result.guard_check is not None
    assert result.guard_check.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert result.guard_check.execution_permitted is False
    assert result.within_allowlist_used_as_permit is False
    with pytest.raises(ContractValidationError, match="within_allowlist_is_not_a_permit"):
        allowlist_status_is_not_a_permit(result.guard_check)
    with pytest.raises(ContractValidationError, match="policy_check_is_not_dispatch_permit"):
        promote_read_check_to_dispatch_permit(result.guard_check)
    assert result.scope is not None
    assert result.scope.resource_scope.tool_name == "list_historical_encounters"
    assert result.scope.resource_scope.resource_id == "historical.encounters"
    assert result.scope.resource_scope.approved_operation is ReadOperation.QUERY
    assert result.scope.capability is HISTORICAL
    assert result.scope.region_scope.effective_state_codes == ("CA",)
    assert "NV" not in result.scope.region_scope.effective_state_codes
    assert result.scope.region_scope.approved_state_codes == ("CA", "NV")
    assert result.scope.historical_window.eligible_start_utc == HIST_START
    assert result.scope.historical_window.eligible_end_utc == HIST_END
    assert (result.scope.historical_window.eligible_start_utc, result.scope.historical_window.eligible_end_utc) != (
        UNBOUND_START,
        UNBOUND_END,
    )
    assert result.scope.specialist_request is None
    assert result.budget is not None
    assert result.session is not None
    assert result.budget.disposition is StartedTaskDisposition.COMPLETED_BOUNDED
    assert result.budget.production_grant_issued is False
    assert result.budget.clock_is_production_proof is False
    assert result.session.production_grant_issued is False
    assert result.session.clock_is_production_proof is False
    assert result.session.accepted_at_utc == HARNESS_NOW
    assert result.session.accepted_at_utc != ACCEPTED
    assert result.budget.deadline_utc == result.session.deadline_utc
    assert result.budget.deadline_utc != DEADLINE_HINT
    assert result.budget.source_observed_at_utc == SOURCE_OBSERVED
    assert result.budget.source_observed_at_utc != result.budget.deadline_utc
    assert ("tool_calls", 1) in result.budget.consumed_by_this_action
    usage = result.session.usage(invocation_id="inv-hist")
    assert usage.tool_calls == 1
    assert usage.tool_calls <= server_read_budget(HISTORY)[2]
    observation = result.session.source_observations()[-1]
    assert observation.rows == 2
    assert observation.verified_zero_claim is False
    assert observation.source_authenticated is False
    assert result.receipt is not None
    assert result.receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert result.receipt.requested_arguments == hints.arguments
    assert "limit" not in dict(result.receipt.requested_arguments)
    assert result.receipt.executed_arguments[-1] == ("limit", 25)
    assert result.receipt.executed_arguments[-1] == ("limit", HISTORICAL_LIST_MAX)
    assert result.receipt.scope_injected_argument_names == ("limit",)
    assert seen["limit"] == 25
    assert seen["start_utc"] == HIST_START
    assert seen["aircraft_id"] == "AC1"
    assert result.receipt.effective_region_codes == ("CA",)
    assert result.receipt.accepted_at_utc == HARNESS_NOW
    assert result.receipt.deadline_utc == result.session.deadline_utc
    assert result.receipt.resource_id == "historical.encounters"
    assert result.receipt.operation is ReadOperation.QUERY
    assert result.receipt.capability is HISTORICAL
    assert result.receipt.record_ids == ("row-1", "row-2")
    assert result.receipt.source_authenticity.value == "NOT_ESTABLISHED"
    assert result.receipt.verified_zero_minted is False
    assert result.receipt.aviation_evidence_admitted is False
    assert result.receipt.geographic_coverage == "NOT_ESTABLISHED"
    assert result.receipt.geographic_impact_verified is False
    assert result.receipt.approved_source_adapter is False
    assert result.receipt.production_grant_issued is False
    assert result.receipt.policy_check_used is False
    assert result.receipt.policy_check_supplied is True
    assert result.receipt.grant_nonce == "nonproduction-not-a-ledger-read-hist"
    assert result.receipt.effects[0].operational_data_mutation is False
    assert result.receipt.effects[1].operation == "CONSUME"
    assert result.receipt.effects[1].operational_data_mutation is False
    assert result.observation is not None
    assert result.observation["authentic_source_read"] is False
    assert "next_page_token" not in result.observation
    assert "tool_result" not in result.observation
    _closed(result)
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_executor()
    with pytest.raises(ContractValidationError, match="effective_scope_is_not_execution_grant"):
        promote_effective_scope_to_execution_grant(result.scope)
    with pytest.raises(ContractValidationError, match="descriptive_allow_is_not_a_production_grant"):
        promote_budget_decision_to_production_grant(result.budget)


def test_live_ops_get_item_keeps_requested_arguments_and_does_not_widen_region():
    seen: dict[str, object] = {}

    def read(arguments: dict[str, object], _cancellation: object) -> NonproductionReadPage:
        seen.update(arguments)
        return _page(record_ids=("AC1",), source_name="aircraft")

    hints = _live_hints()
    result = _harness(
        read,
        specialist=LIVE,
        tool_name="get_aircraft_operational_context",
        capability=RETRIEVE,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
    ).run(hints, _live_host(), tool_call_id="tool-live", action_id="read-live")

    assert result.refusal is None
    assert result.receipt is not None
    assert result.receipt.operation is ReadOperation.GET_ITEM
    assert result.receipt.resource_id == "aircraft"
    assert result.receipt.requested_arguments == (("aircraft_id", "AC1"),)
    assert result.receipt.executed_arguments == (("aircraft_id", "AC1"),)
    assert result.receipt.scope_injected_argument_names == ()
    assert "limit" not in seen
    assert seen["aircraft_id"] == "AC1"
    assert result.receipt.effective_region_codes == ("CA",)
    assert "NV" not in result.receipt.effective_region_codes
    assert result.scope is not None
    assert result.scope.region_scope.sigmet_polygon_intersection_established is False
    assert result.receipt.geographic_coverage == "NOT_ESTABLISHED"
    assert result.receipt.aviation_evidence_admitted is False
    assert result.receipt.source_authenticity.value == "NOT_ESTABLISHED"
    assert result.session is not None
    assert result.session.usage(invocation_id="inv-live").tool_calls == 1
    _closed(result)


def test_caller_clock_region_and_callback_are_not_authority():
    calls: list[str] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append("adapter")
        return _page()

    def smuggled() -> None:
        calls.append("smuggled")

    harness = _harness(read)
    hints = _historical_hints()
    host = _historical_host()
    clock = harness.run(
        hints,
        host,
        tool_call_id="tool-clock",
        action_id="read-clock",
        caller_clock=_Explosive(),
    )
    region = harness.run(
        hints,
        host,
        tool_call_id="tool-region",
        action_id="read-region",
        caller_region=_Explosive(),
    )
    callback = harness.run(
        hints,
        host,
        tool_call_id="tool-callback",
        action_id="read-callback",
        read_callback=smuggled,
    )
    hinted_deadline = harness.run(
        replace(hints, caller_deadline_utc="2026-10-27T19:00:00Z"),
        host,
        tool_call_id="tool-deadline",
        action_id="read-deadline",
    )
    hinted_budget = harness.run(
        replace(hints, caller_max_tool_calls=99),
        host,
        tool_call_id="tool-budget",
        action_id="read-budget",
    )

    assert calls == []
    assert clock.refusal is ScopedReadJoinRefusal.CALLER_CLOCK_NOT_AUTHORITY
    assert region.refusal is ScopedReadJoinRefusal.CALLER_REGION_NOT_AUTHORITY
    assert callback.refusal is ScopedReadJoinRefusal.CALLER_CALLABLE_REJECTED
    assert hinted_deadline.refusal is ScopedReadJoinRefusal.SCOPE_DENIED
    assert "CALLER_DEADLINE_NOT_AUTHORITY" in _codes(hinted_deadline)
    assert hinted_budget.refusal is ScopedReadJoinRefusal.SCOPE_DENIED
    assert "CALLER_BUDGET_NOT_AUTHORITY" in _codes(hinted_budget)
    _closed(clock)
    _closed(region)


def test_scan_put_update_and_arbitrary_tool_do_not_run():
    calls: list[int] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append(1)
        return _page()

    harness = _harness(read)
    host = _historical_host()
    hints = _historical_hints()
    denied = (
        harness.run(replace(hints, operation=ReadOperation.SCAN), host, tool_call_id="tool-scan", action_id="scan"),
        harness.run(replace(hints, tool_name="put_item"), host, tool_call_id="tool-put", action_id="put"),
        harness.run(replace(hints, tool_name="update_item"), host, tool_call_id="tool-update", action_id="update"),
        harness.run(replace(hints, tool_name="arbitrary_table_read"), host, tool_call_id="tool-any", action_id="any"),
        harness.run(
            replace(hints, arguments=hints.arguments + (("update_item", "1"),)),
            host,
            tool_call_id="tool-arg",
            action_id="arg",
        ),
    )

    assert calls == []
    assert "SCAN_NOT_PERMITTED" in _codes(denied[0])
    assert denied[0].scope is not None
    assert denied[0].scope.resource_scope.approved_operation is None
    assert denied[0].scope.resource_scope.all_aircraft_scan is False
    for result in denied[1:]:
        assert result.adapter_invoked is False
        assert result.refusal is ScopedReadJoinRefusal.SCOPE_DENIED
        assert "TOOL_SELECTION_OUT_OF_BOUNDS" in _codes(result)
        _closed(result)


def test_wrong_specialist_does_not_call_the_live_adapter():
    calls: list[int] = []
    harness = _harness(
        lambda *_args: calls.append(1),
        specialist=LIVE,
        tool_name="get_aircraft_operational_context",
        capability=RETRIEVE,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
    )
    result = harness.run(
        _live_hints(),
        _historical_host(),
        tool_call_id="tool-wrong",
        action_id="read-wrong",
    )

    assert calls == []
    assert result.adapter_invoked is False
    assert "ENTITLEMENT_MISMATCH" in _codes(result)
    _closed(result)


def test_over_wide_ca_tx_scope_is_not_expanded():
    calls: list[int] = []
    result = _harness(lambda *_args: calls.append(1)).run(
        _historical_hints(claimed_state_codes=("CA", "TX")),
        _historical_host(region_state_codes=frozenset({"CA"})),
        tool_call_id="tool-wide",
        action_id="read-wide",
    )

    assert calls == []
    assert "CROSS_REGION_ESCALATION" in _codes(result)
    assert result.scope is not None
    assert result.scope.region_scope.approved_state_codes == ("CA",)
    assert "TX" not in result.scope.region_scope.approved_state_codes
    assert result.scope.region_scope.effective_state_codes == ()
    assert len(result.scope.region_scope.approved_state_codes) != 50
    assert result.scope.region_scope.broad_us_default_applied is False
    _closed(result)


def test_california_text_claimed_as_sigmet_polygon_is_denied():
    calls: list[int] = []
    result = _harness(lambda *_args: calls.append(1)).run(
        _historical_hints(claims_exact_sigmet_polygon=True, claims_geographic_coverage=True),
        _historical_host(),
        tool_call_id="tool-poly",
        action_id="read-poly",
    )

    assert calls == []
    assert "FALSE_GEOGRAPHIC_COVERAGE_CLAIM" in _codes(result)
    assert result.scope is not None
    assert result.scope.region_scope.sigmet_polygon_intersection_established is False
    assert result.scope.region_scope.geographic_coverage_established is False
    assert result.scope.region_scope.source_coverage.value == "UNKNOWN"
    _closed(result)


def test_eight_day_historical_interval_is_not_clipped_to_the_host_window():
    calls: list[int] = []
    hints = _historical_hints(
        historical_start_utc=EIGHT_DAY_START,
        arguments=(
            ("aircraft_id", "AC1"),
            ("end_utc", HIST_END),
            ("start_utc", EIGHT_DAY_START),
        ),
    )
    result = _harness(lambda *_args: calls.append(1)).run(
        hints,
        _historical_host(),
        tool_call_id="tool-eight",
        action_id="read-eight",
    )

    assert calls == []
    assert "UNBOUNDED_HISTORICAL_WINDOW" in _codes(result)
    assert result.scope is not None
    assert result.scope.historical_window.eligible_start_utc is None
    assert result.scope.historical_window.eligible_end_utc is None
    assert result.scope.historical_window.within_seven_days is False
    assert result.receipt is None
    _closed(result)


def test_ambiguous_callsign_and_missing_decision_target_are_not_promoted():
    calls: list[int] = []
    harness = _harness(
        lambda *_args: calls.append(1),
        specialist=LIVE,
        tool_name="find_aircraft_by_callsign",
        capability=RETRIEVE,
        resource_id="aircraft",
        operation=ReadOperation.QUERY,
    )
    ambiguous = harness.run(
        _live_hints(
            tool_name="find_aircraft_by_callsign",
            operation=ReadOperation.QUERY,
            arguments=(("callsign", "UAL1"),),
            callsign="UAL1",
            aircraft_id=None,
            entity_id=None,
            location_hint=None,
            census_region_name=None,
            sigmet_region_string=None,
        ),
        _live_host(
            population_entity_ids=frozenset({"AC1", "AC2"}),
            callsign_aircraft_ids=frozenset({"AC1", "AC2"}),
        ),
        tool_call_id="tool-callsign",
        action_id="read-callsign",
    )
    missing_target = _harness(
        lambda *_args: calls.append(1),
        specialist=DECISION,
        tool_name="get_current_risk_evidence",
        capability=RISK,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
    ).run(
        _decision_hints(aircraft_id=None, entity_id=None, arguments=()),
        _decision_host(),
        tool_call_id="tool-target",
        action_id="read-target",
    )

    assert calls == []
    assert "AMBIGUOUS_CALLSIGN" in _codes(ambiguous)
    assert ambiguous.scope is not None
    assert ambiguous.scope.decision_target_scope.described_aircraft_id is None
    assert ambiguous.scope.decision_target_scope.promoted_from_callsign is False
    assert "DECISION_AIRCRAFT_NOT_INDEPENDENTLY_VALIDATED" in _codes(missing_target)
    assert missing_target.scope is not None
    assert missing_target.scope.decision_target_scope.described_aircraft_id is None
    assert missing_target.scope.decision_target_scope.target_authorization_granted is False
    _closed(ambiguous)
    _closed(missing_target)


def test_missing_risk_is_not_recorded_as_low():
    calls: list[int] = []
    result = _harness(
        lambda *_args: calls.append(1),
        specialist=DECISION,
        tool_name="get_current_risk_evidence",
        capability=RISK,
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
    ).run(
        _decision_hints(missing_risk_means_low=True),
        _decision_host(risk_presence=RiskPresence.ABSENT, stored_risk_level=None),
        tool_call_id="tool-risk",
        action_id="read-risk",
    )

    assert calls == []
    assert "MISSING_RISK_IS_NOT_LOW" in _codes(result)
    assert "MISSING_RISK_RECORD" in _codes(result)
    assert result.scope is not None
    assert result.scope.decision_target_scope.stored_risk_level is None
    assert result.scope.decision_target_scope.risk_presence is RiskPresence.ABSENT
    assert result.scope.decision_target_scope.missing_risk_recorded_as_low is False
    assert result.flight_safety_conclusion is False
    _closed(result)


def test_over_limit_page_is_not_a_completed_read():
    calls: list[int] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append(1)
        return _page(record_ids=tuple(f"row-{index}" for index in range(HISTORICAL_LIST_MAX + 1)))

    result = _harness(read).run(_historical_hints(), _historical_host(), tool_call_id="tool-limit", action_id="read-limit")

    assert calls == [1]
    assert result.refusal is ScopedReadJoinRefusal.READ_NOT_COMPLETED
    assert result.receipt is not None
    assert result.receipt.outcome is ReadExecutionOutcome.QUERY_LIMIT_EXCEEDED
    assert result.receipt.adapter_invoked is True
    assert result.receipt.record_ids == ()
    assert result.receipt.verified_zero_minted is False
    assert result.receipt.aviation_evidence_admitted is False
    assert result.budget is not None
    assert result.budget.disposition is not StartedTaskDisposition.COMPLETED_BOUNDED
    _closed(result)


def test_pagination_token_is_not_retained_or_followed():
    calls: list[int] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append(1)
        return _page(has_more=True, next_page_token=SECRET_TOKEN)

    result = _harness(read).run(_historical_hints(), _historical_host(), tool_call_id="tool-page", action_id="read-page")

    assert calls == [1]
    assert result.receipt is not None
    assert result.receipt.partial_pagination is True
    assert result.receipt.next_page_token_present is True
    assert result.observation is not None
    assert SECRET_TOKEN not in repr(result.observation)
    assert SECRET_TOKEN not in repr(result.receipt)
    assert "next_page_token" not in result.observation
    assert result.receipt.verified_zero_minted is False
    _closed(result)


def test_empty_page_is_not_a_verified_zero_or_flight_safety_conclusion():
    result = _harness(lambda *_args: _page(record_ids=(), has_more=False)).run(
        _historical_hints(),
        _historical_host(),
        tool_call_id="tool-empty",
        action_id="read-empty",
    )

    assert result.refusal is None
    assert result.receipt is not None
    assert result.receipt.page_empty is True
    assert result.receipt.rows_returned == 0
    assert result.receipt.verified_zero_minted is False
    assert result.receipt.geographic_impact_verified is False
    assert result.receipt.aviation_evidence_admitted is False
    assert result.session is not None
    observed = result.session.source_observations()[-1]
    assert observed.rows == 0
    assert observed.verified_zero_claim is False
    assert result.flight_safety_conclusion is False
    _closed(result)


def test_cancellation_before_entry_does_not_call_the_adapter():
    calls: list[int] = []
    harness = _harness(lambda *_args: calls.append(1))
    prepared = harness.prepare(_historical_hints(), _historical_host(), tool_call_id="tool-cancel", action_id="read-cancel")
    assert prepared.session is not None
    prepared.session.cancel()
    result = harness.enter(prepared)

    assert calls == []
    assert result.adapter_invoked is False
    assert result.refusal is ScopedReadJoinRefusal.BUDGET_NOT_RESERVED
    assert result.guard_check is not None
    assert result.guard_check.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert result.guard_check.execution_permitted is False
    assert result.within_allowlist_used_as_permit is False
    assert result.budget is not None
    assert result.budget.evaluation is BudgetEvaluation.CANCELLED
    assert result.budget.disposition is StartedTaskDisposition.NOT_STARTED
    assert BudgetDenialReason.CANCELLED in result.budget.denial_reasons
    assert result.session is not None
    assert result.session.usage(invocation_id="inv-hist").tool_calls == 0
    _closed(result)


def test_cancellation_during_the_call_is_terminal_partial_and_discards_rows():
    calls: list[int] = []

    def read(_arguments: dict[str, object], cancellation: object) -> NonproductionReadPage:
        calls.append(1)
        cancellation.cancel()
        return _page()

    result = _harness(read).run(_historical_hints(), _historical_host(), tool_call_id="tool-mid", action_id="read-mid")

    assert calls == [1]
    assert result.receipt is not None
    assert result.receipt.outcome is ReadExecutionOutcome.CANCELLED
    assert ReadBoundaryDenial.CANCELLED in result.receipt.denial_reasons
    assert result.receipt.record_ids == ()
    assert result.receipt.verified_zero_minted is False
    assert result.budget is not None
    assert result.budget.disposition is StartedTaskDisposition.TERMINAL_PARTIAL
    assert result.budget.evaluation is BudgetEvaluation.CANCELLED
    assert result.refusal is ScopedReadJoinRefusal.READ_NOT_COMPLETED
    _closed(result)


def test_time_budget_expiry_before_entry_does_not_call_the_adapter():
    calls: list[int] = []
    harness = _harness(lambda *_args: calls.append(1))
    prepared = harness.prepare(_historical_hints(), _historical_host(), tool_call_id="tool-expire", action_id="read-expire")
    harness.advance_seconds(120)
    result = harness.enter(prepared)

    assert calls == []
    assert result.adapter_invoked is False
    assert result.guard_check is not None
    assert result.budget is not None
    assert result.budget.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert result.budget.disposition is StartedTaskDisposition.NOT_STARTED
    assert result.session is not None
    assert result.session.usage(invocation_id="inv-hist").tool_calls == 0
    _closed(result)


def test_time_budget_expiry_during_the_call_is_terminal_partial():
    calls: list[int] = []

    def read(_arguments: dict[str, object], _cancellation: object) -> NonproductionReadPage:
        calls.append(1)
        harness.advance_seconds(120)
        return _page()

    harness = _harness(read)
    result = harness.run(_historical_hints(), _historical_host(), tool_call_id="tool-late", action_id="read-late")

    assert calls == [1]
    assert result.receipt is not None
    assert result.receipt.outcome is ReadExecutionOutcome.TIMEOUT
    assert result.receipt.record_ids == ()
    assert result.budget is not None
    assert result.budget.disposition is StartedTaskDisposition.TERMINAL_PARTIAL
    assert result.budget.evaluation is BudgetEvaluation.DEADLINE_EXPIRED
    assert result.downstream_timeout_seconds is not None
    assert result.downstream_timeout_seconds >= 1
    assert result.blocked_io_interruptible is False
    _closed(result)


def test_concurrent_duplicate_idempotency_invokes_the_adapter_once():
    calls: list[int] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append(1)
        return _page()

    harness = _harness(read)
    hints = _historical_hints()
    host = _historical_host()
    first = harness.prepare(hints, host, tool_call_id="tool-a", action_id="read-a")
    second = harness.prepare(hints, host, tool_call_id="tool-b", action_id="read-b", session=first.session)
    barrier = Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []

    def worker(prepared: object) -> None:
        try:
            results.append(harness.enter(prepared, before_execute=lambda: barrier.wait(timeout=5)))
        except BaseException as exc:
            errors.append(exc)
            barrier.abort()

    threads = [Thread(target=worker, args=(prepared,)) for prepared in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert calls == [1]
    assert len(results) == 2
    outcomes = {result.receipt.outcome if result.receipt is not None else None for result in results}
    assert ReadExecutionOutcome.COMPLETED in outcomes
    duplicate = next(
        result
        for result in results
        if result.receipt is not None and result.receipt.outcome is not ReadExecutionOutcome.COMPLETED
    )
    assert duplicate.receipt.adapter_invoked is False
    assert ReadBoundaryDenial.DUPLICATE_CONSUMPTION in duplicate.receipt.denial_reasons
    assert duplicate.flight_safety_conclusion is False
    for result in results:
        _closed(result)


def test_unbounded_retry_does_not_reset_counters_or_read_again():
    calls: list[int] = []
    result = _harness(lambda *_args: calls.append(1) or _page()).run(
        _historical_hints(),
        _historical_host(),
        tool_call_id="tool-retry",
        action_id="read-retry",
    )
    session = result.session
    assert session is not None
    first = session.begin_retry(invocation_id="inv-hist", action_id="retry-1")
    second = session.begin_retry(invocation_id="inv-hist", action_id="retry-2")

    assert calls == [1]
    assert first.disposition is StartedTaskDisposition.RESERVED
    assert first.evaluation is BudgetEvaluation.EVALUATED_WITHIN_CEILING
    assert second.evaluation is BudgetEvaluation.EXHAUSTED
    assert BudgetDenialReason.BUDGET_EXHAUSTED in second.denial_reasons
    with pytest.raises(ContractValidationError, match="counter_reset_rejected"):
        session.reset_for_retry("inv-hist")
    usage = session.usage(invocation_id="inv-hist")
    assert usage.tool_calls == 1
    assert usage.retries == 1
    assert session.deadline_utc == result.budget.deadline_utc
    _closed(result)


def test_tool_call_ceiling_stops_another_read_without_calling_the_adapter():
    calls: list[int] = []
    harness = _harness(lambda *_args: calls.append(1) or _page())
    hints = _historical_hints()
    result = harness.run(hints, _historical_host(), tool_call_id="tool-cap", action_id="read-cap")
    session = result.session
    assert session is not None
    reserved = session.begin_read(
        invocation_id="inv-hist",
        action_id="read-extra",
        tool_name="list_historical_encounters",
        operation=ReadOperation.QUERY,
        arguments=hints.arguments,
        resource_id="historical.encounters",
        historical_window=(HIST_START, HIST_END),
    )
    blocked = harness.run(
        hints,
        _historical_host(),
        tool_call_id="tool-cap-3",
        action_id="read-cap-3",
        session=session,
    )

    assert reserved.disposition is StartedTaskDisposition.RESERVED
    assert calls == [1]
    assert blocked.adapter_invoked is False
    assert blocked.guard_check is not None
    assert blocked.budget is not None
    assert blocked.budget.evaluation is BudgetEvaluation.EXHAUSTED
    assert session.usage(invocation_id="inv-hist").tool_calls == server_read_budget(HISTORY)[2] == 2
    _closed(blocked)


def test_forged_event_cannot_seal_the_host_or_run_a_read():
    calls: list[int] = []
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user"}}}},
        "headers": {"Authorization": "Bearer forged"},
    }
    result = _harness(lambda *_args: calls.append(1)).run(
        _historical_hints(),
        event,
        tool_call_id="tool-event",
        action_id="read-event",
    )

    assert calls == []
    assert "PRODUCTION_INPUT_NOT_A_HOST" in _codes(result)
    assert result.scope is not None
    assert result.scope.execution_granted is False
    assert result.scope.principal_ref is None
    _closed(result)
