"""Native Live Ops bridge tests.

The production bridge does not invoke the specialist. These tests call the
real LiveOpsSpecialist with a fake provider, recording fake read handlers,
and fake operational tables, then pass that result through the bridge.
Those runs are not authenticated source evidence.
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.live_ops_claims import (
    CallsignMatchSetClaim,
    ConfirmedImpactClaim,
    ContextRootAbsentClaim,
    HazardRegionIntersectionClaim,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsExecutedScope,
    LiveOpsRiskMode,
    QueryResultZeroClaim,
    RiskStateClaim,
    ScopedCountClaim,
)
from wilvor_ai.live_ops_model_contracts import (
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsProviderFailureKind,
)
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsEntityHint,
    LiveOpsFailureReason,
    LiveOpsPublicClaimKind,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunStatus,
    LiveOpsSelectorHints,
    LiveOpsTrustedContext,
    live_ops_reference_utc,
)
from wilvor_ai.live_ops_specialist import (
    LiveOpsProviderError,
    LiveOpsProviderTimeoutError,
    LiveOpsSpecialist,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_live_ops_native_bridge import (
    LIVE_OPS_MISSING_HOST_EXECUTION_GRANT,
    LiveOpsNativeCountState,
    LiveOpsNativeHostInputSource,
    LiveOpsNativeResultDisposition,
    accept_native_live_ops_result,
    prepare_master_live_ops_native_bridge,
    reject_unauthorized_selectors,
)
from wilvor_ai.master_parallel_contracts import MasterParallelContractBundle
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai import live_ops as live_ops_module


TEXT = "Which aircraft are currently affected by hazards?"
CALIFORNIA_TEXT = (
    "Which aircraft are impacted by California SIGMETs, and what are their recorded risk levels?"
)
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "master_live_ops_native_bridge.py"
)
_TOOL_NAMES = (
    "search_current_hazards",
    "search_current_impacts",
    "search_current_encounters",
    "get_observed_network_state",
    "get_aircraft_operational_context",
    "get_hazard_operational_context",
    "get_airport_operational_context",
    "find_aircraft_by_callsign",
)


class FakeProvider:
    """Scripted test provider. It is not a vendor client."""

    def __init__(self, steps):
        self.steps = list(steps)
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not self.steps:
            raise RuntimeError("script exhausted")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        if callable(step):
            return step(turn)
        return step


class RecordingFakeHandlers(dict):
    """Test-only handlers over fake tables. Delegation is not source authenticity."""

    def __init__(self, log):
        super().__init__()
        self.log = log
        for name in _TOOL_NAMES:
            self[name] = self._wrap(getattr(live_ops_module, name), name)

    def _wrap(self, function, name):
        log = self.log

        def handler(call, **kwargs):
            log.append((name, dict(kwargs)))
            return function(call, **kwargs)

        return handler


def _ids():
    state = {"n": 0}

    def factory():
        state["n"] += 1
        return "tool-" + str(state["n"])

    return factory


def _binding(
    *,
    user_text: str = TEXT,
    hints: MasterRouteHints | None = None,
    master_request_id: str = "master-req-live-bridge-1",
    correlation_id: str = "corr-live-bridge-1",
    application_request_time_utc: str = "2026-01-15T00:00:01Z",
):
    step = MasterProposedStep(
        step_id="live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
        hints=MasterRouteHints() if hints is None else hints,
    )
    proposal = MasterRouteProposal(
        user_text=user_text,
        capabilities=(MasterProposedCapability(step.capability, step.requirement),),
        steps=(step,),
    )
    context = MasterPlanningContext(
        master_request_id=master_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=application_request_time_utc,
        canonical_user_text=user_text,
    )
    decision = authorize_route(proposal, context)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    bundle = MasterParallelContractBundle(
        context=context,
        plan=decision.plan,
        evidence_inputs=(),
    )
    described = describe_planned_adapters(bundle)
    return prepare_master_live_ops_binding(bundle, described.descriptors[0])


def _execute(
    binding,
    steps,
    *,
    handlers=None,
    log=None,
    tables=None,
    correlation_id=None,
    request=None,
    **ctx,
):
    if log is None:
        log = []
    if handlers is None:
        handlers = RecordingFakeHandlers(log)
    provider = FakeProvider(steps)
    specialist = LiveOpsSpecialist(
        provider=provider,
        tool_call_id_factory=_ids(),
        handlers=handlers,
    )
    payload = {
        "tables": fixtures.california_tables() if tables is None else tables,
        "now_epoch": fixtures.NOW,
        "correlation_id": binding.correlation_id if correlation_id is None else correlation_id,
    }
    payload.update(ctx)
    result = specialist.run(
        binding.request if request is None else request,
        LiveOpsTrustedContext(**payload),
    )
    return result, provider, log


def _tools(*calls):
    proposed = tuple(
        item if isinstance(item, ProposedToolCall) else ProposedToolCall(item[0], item[1])
        for item in calls
    )
    return LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.TOOL_CALLS, tool_calls=proposed)


def _claims(*items):
    return LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.FINAL_CLAIMS, claims=tuple(items))


def _network_claim(projection):
    return ScopedCountClaim(
        tool_call_id=projection.tool_call_id,
        tool_name=projection.tool_name,
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=projection.data["current_aircraft_count"],
        scope=LiveOpsExecutedScope(),
    )


def _network_from_turn(turn):
    return _claims(_network_claim(turn.tool_results[-1]))


def _callsign_claim(projection):
    matches = projection.data["matches"]
    callsign = projection.data["callsign"]
    if not matches:
        return QueryResultZeroClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            scope=LiveOpsExecutedScope(callsign=callsign),
        )
    return CallsignMatchSetClaim(
        tool_call_id=projection.tool_call_id,
        callsign=callsign,
        aircraft_ids=tuple(sorted(row["aircraft_id"] for row in matches)),
    )


def _impact_claims(projection):
    built = []
    for row in projection.data["impacts"]:
        built.append(
            ConfirmedImpactClaim(
                tool_call_id=projection.tool_call_id,
                tool_name=projection.tool_name,
                aircraft_id=row["aircraft_id"],
                hazard_id=row["hazard_id"],
                source_version=row["source_version"],
                encounter_id=row["encounter_id"],
            )
        )
        context = row.get("encounter_context") or {}
        risk = context.get("risk")
        state = (context.get("risk_link") or {}).get("state")
        if state == "PRESENT" and isinstance(risk, Mapping):
            built.append(
                RiskStateClaim(
                    tool_call_id=projection.tool_call_id,
                    tool_name=projection.tool_name,
                    mode=LiveOpsRiskMode.PRESENT,
                    encounter_id=row["encounter_id"],
                    aircraft_id=row["aircraft_id"],
                    hazard_id=row["hazard_id"],
                    risk_id=risk["risk_id"],
                    risk_level=risk["risk_level"],
                    risk_score=risk["risk_score"],
                )
            )
    return tuple(built)


def _unique_tables():
    item = fixtures.aircraft("n1", callsign="N123AA")
    return fixtures.tables(
        aircraft=fixtures.FlexibleTable(records={"n1": item}, query_items={"N123AA": [item]})
    )


def _assert_unauthenticated(mapped) -> None:
    assert mapped.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert mapped.production_authority is MasterProductionAuthority.DENIED
    assert mapped.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert mapped.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert mapped.dispatch_permitted is False
    assert mapped.executable is False
    assert mapped.specialist_invoked_by_bridge is False
    assert mapped.selectors_authorized is False
    assert mapped.callsign_authenticated is False
    assert mapped.native_entity_ref_authorizes_selector is False
    assert mapped.authenticated_source_evidence is False
    assert mapped.native_execution_is_source_evidence is False
    assert mapped.geospatial_coverage_established is False
    assert mapped.regional_string_match_is_sigmet_coverage is False
    assert mapped.census_region_resolution_is_sigmet_coverage is False
    assert mapped.application_request_time_is_source_clock is False
    assert mapped.verified_claims_are_complete_coverage is False
    assert mapped.aircraft_impact_count is None
    assert mapped.verified_zero is False
    assert mapped.absence_counted_as_zero is False
    assert mapped.unobserved_metrics_recorded_as_zero == ()
    assert mapped.verified_claims is mapped.native_result.verified_claims


def test_real_specialist_preserves_request_identity_for_a_fake_backed_run():
    binding = _binding()
    bridge = prepare_master_live_ops_native_bridge(binding)
    result, provider, log = _execute(
        binding,
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    mapped = accept_native_live_ops_result(
        bridge,
        result,
        source_observation_epoch=fixtures.NOW,
    )

    assert isinstance(provider, FakeProvider)
    assert type(log[0][1]).__name__ != "OperationalTables"
    assert bridge.request is binding.request
    assert bridge.specialist_request_id == binding.specialist_request_id
    assert bridge.invocation_id == binding.invocation_id
    assert bridge.step_id == binding.step_id
    assert bridge.correlation_id == binding.correlation_id
    assert mapped.request_id == binding.specialist_request_id
    assert mapped.correlation_id == binding.correlation_id
    assert mapped.invocation_id == binding.invocation_id
    assert result.request_id == binding.request.request_id
    assert result.correlation_id == binding.correlation_id
    assert mapped.source_observation_epoch == fixtures.NOW
    assert mapped.application_request_time_utc == binding.application_request_time_utc
    assert mapped.reference_utc == live_ops_reference_utc(fixtures.NOW)
    assert mapped.application_request_time_utc != mapped.reference_utc
    assert mapped.disposition is LiveOpsNativeResultDisposition.ALL_VERIFIED
    assert mapped.count_state is LiveOpsNativeCountState.SCOPED_COUNT
    assert mapped.native_query_result_zero_claims == ()
    assert mapped.scoped_count_claims[0] is result.verified_claims[0]
    _assert_unauthenticated(mapped)

    run_names = {name for name in inspect.signature(LiveOpsSpecialist.run).parameters if name != "self"}
    init_names = {
        name for name in inspect.signature(LiveOpsSpecialist.__init__).parameters if name != "self"
    }
    context_names = set(inspect.signature(LiveOpsTrustedContext).parameters)
    host_names = {item.parameter for item in bridge.host_inputs}
    assert host_names == run_names | init_names | context_names | {"execution_grant"}
    by_name = {item.parameter: item for item in bridge.host_inputs}
    assert by_name["request"].value_preserved is True
    assert by_name["request"].preserved_value == binding.specialist_request_id
    assert by_name["request"].satisfied is False
    assert by_name["correlation_id"].preserved_value == binding.correlation_id
    assert by_name["now_epoch"].source is LiveOpsNativeHostInputSource.FUTURE_TRUSTED_HOST
    assert by_name["now_epoch"].preserved_value is None
    assert by_name["execution_grant"].source is LiveOpsNativeHostInputSource.MISSING_EXECUTION_GRANT
    assert by_name["execution_grant"].satisfied is False
    assert bridge.missing_integration_dependency == LIVE_OPS_MISSING_HOST_EXECUTION_GRANT
    assert all(item.satisfied is False for item in bridge.host_inputs)
    assert "callsign" not in host_names
    assert "aircraft_id" not in host_names
    assert "region" not in host_names
    with pytest.raises(ContractValidationError, match="invalid_source_observation_epoch"):
        accept_native_live_ops_result(
            bridge,
            result,
            source_observation_epoch=binding.application_request_time_utc,  # type: ignore[arg-type]
        )


def test_result_identity_mismatch_is_rejected_without_admitting_claims():
    binding = _binding()
    bridge = prepare_master_live_ops_native_bridge(binding)
    result, _provider, _log = _execute(
        binding,
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    assert result.verified_claims
    foreign, _provider, _log = _execute(
        binding,
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        correlation_id="corr-other-live-bridge",
        max_model_turns=2,
    )

    with pytest.raises(ContractValidationError, match="correlation_mismatch"):
        accept_native_live_ops_result(bridge, foreign, source_observation_epoch=fixtures.NOW)
    with pytest.raises(ContractValidationError, match="request_id_mismatch"):
        accept_native_live_ops_result(
            bridge,
            replace(result, request_id="other-request-1"),
            source_observation_epoch=fixtures.NOW,
        )
    with pytest.raises(ContractValidationError, match="conversation_id_mismatch"):
        accept_native_live_ops_result(
            bridge,
            replace(result, conversation_id="other-conv-1"),
            source_observation_epoch=fixtures.NOW,
        )
    with pytest.raises(ContractValidationError, match="source_observation_epoch_mismatch"):
        accept_native_live_ops_result(bridge, result, source_observation_epoch=fixtures.NOW + 5)
    with pytest.raises(ContractValidationError, match="invalid_source_observation_epoch"):
        accept_native_live_ops_result(bridge, result, source_observation_epoch=True)  # type: ignore[arg-type]
    assert not hasattr(bridge, "run")
    assert not hasattr(bridge, "dispatch")


def test_model_refusal_is_not_a_provider_failure_or_a_verified_zero():
    binding = _binding()
    result, provider, log = _execute(
        binding,
        [
            LiveOpsModelDecision(
                kind=LiveOpsModelDecisionKind.REFUSAL,
                refusal_code="model_refused",
            )
        ],
    )
    _bridge, mapped = _map(binding, result)
    assert mapped.disposition is LiveOpsNativeResultDisposition.MODEL_REFUSAL
    assert mapped.failure_reason is LiveOpsFailureReason.MODEL_REFUSAL
    assert mapped.refusal_code == "model_refused"
    assert mapped.verification_outcome is None
    assert mapped.count_state is LiveOpsNativeCountState.ABSENT
    assert mapped.verified_claims == ()
    assert mapped.native_query_result_zero_claims == ()
    assert log == []
    assert provider.requests
    _assert_unauthenticated(mapped)


def test_provider_failure_stays_a_provider_failure():
    binding = _binding()
    result, _provider, log = _execute(
        binding,
        [LiveOpsProviderError(LiveOpsProviderFailureKind.TRANSPORT_OR_API)],
    )
    _bridge, mapped = _map(binding, result)
    assert mapped.disposition is LiveOpsNativeResultDisposition.PROVIDER_FAILURE
    assert mapped.failure_reason is LiveOpsFailureReason.TRANSPORT_OR_API
    assert mapped.disposition is not LiveOpsNativeResultDisposition.MODEL_REFUSAL
    assert mapped.verified_claims == ()
    assert mapped.count_state is LiveOpsNativeCountState.ABSENT
    assert log == []
    _assert_unauthenticated(mapped)


def test_timeout_and_cancellation_stay_distinct():
    binding = _binding()
    provider_timeout, _provider, log = _execute(binding, [LiveOpsProviderTimeoutError()])
    runtime_timeout, _provider, runtime_log = _execute(
        binding,
        [_tools(("get_observed_network_state", {}))],
        clock=lambda: fixtures.NOW,
        deadline_epoch=fixtures.NOW,
    )
    cancelled, _provider, cancelled_log = _execute(
        binding,
        [_tools(("get_observed_network_state", {}))],
        cancelled=lambda: True,
        clock=lambda: fixtures.NOW - 1,
        deadline_epoch=fixtures.NOW + 10,
    )

    timeout_mapped = _map(binding, provider_timeout)[1]
    runtime_mapped = _map(binding, runtime_timeout)[1]
    cancelled_mapped = _map(binding, cancelled)[1]
    assert timeout_mapped.disposition is LiveOpsNativeResultDisposition.PROVIDER_TIMEOUT
    assert timeout_mapped.failure_reason is LiveOpsFailureReason.PROVIDER_TIMEOUT
    assert runtime_mapped.disposition is LiveOpsNativeResultDisposition.RUNTIME_TIMEOUT
    assert runtime_mapped.failure_reason is LiveOpsFailureReason.RUNTIME_TIMEOUT
    assert cancelled_mapped.disposition is LiveOpsNativeResultDisposition.CANCELLED
    assert cancelled_mapped.failure_reason is LiveOpsFailureReason.CANCELLED
    assert {
        timeout_mapped.disposition,
        runtime_mapped.disposition,
        cancelled_mapped.disposition,
    } == {
        LiveOpsNativeResultDisposition.PROVIDER_TIMEOUT,
        LiveOpsNativeResultDisposition.RUNTIME_TIMEOUT,
        LiveOpsNativeResultDisposition.CANCELLED,
    }
    for mapped in (timeout_mapped, runtime_mapped, cancelled_mapped):
        assert mapped.verified_claims == ()
        assert mapped.count_state is LiveOpsNativeCountState.ABSENT
        assert mapped.native_query_result_zero_claims == ()
        _assert_unauthenticated(mapped)
    assert log == []
    assert runtime_log == []
    assert cancelled_log == []
    assert timeout_mapped.source_observation_epoch != binding.application_request_time_utc


def test_tool_failure_does_not_become_verified_evidence():
    binding = _binding()
    log = []
    handlers = RecordingFakeHandlers(log)

    def boom(call, **_kwargs):
        log.append(("find_aircraft_by_callsign", {"callsign": "UAL123"}))
        raise RuntimeError("handler-secret")

    handlers["find_aircraft_by_callsign"] = boom
    result, _provider, used = _execute(
        binding,
        [
            _tools(
                ("get_observed_network_state", {}),
                ("find_aircraft_by_callsign", {"callsign": "UAL123"}),
            )
        ],
        handlers=handlers,
        log=log,
        max_model_turns=2,
    )
    _bridge, mapped = _map(binding, result)
    assert result.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert mapped.disposition is LiveOpsNativeResultDisposition.TOOL_FAILURE
    assert mapped.failure_reason is LiveOpsFailureReason.TOOL_EXECUTION_FAILED
    assert mapped.failed_tool_name == "find_aircraft_by_callsign"
    assert mapped.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert mapped.verified_claims == ()
    assert mapped.count_state is LiveOpsNativeCountState.ABSENT
    assert mapped.confirmed_impact_claims == ()
    assert "handler-secret" not in str(mapped.failure_reason)
    assert [item[0] for item in used] == [
        "get_observed_network_state",
        "find_aircraft_by_callsign",
    ]
    _assert_unauthenticated(mapped)


def test_partial_verification_keeps_only_the_native_verified_claims():
    def finish(turn):
        projection = turn.tool_results[0]
        row = next(item for item in projection.data["impacts"] if item["encounter_id"] == "H1-A")
        good = ConfirmedImpactClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            aircraft_id=row["aircraft_id"],
            hazard_id=row["hazard_id"],
            source_version=row["source_version"],
            encounter_id=row["encounter_id"],
        )
        bad = ConfirmedImpactClaim(
            tool_call_id=projection.tool_call_id,
            tool_name=projection.tool_name,
            aircraft_id="missing",
            hazard_id=row["hazard_id"],
            source_version=row["source_version"],
            encounter_id=row["encounter_id"],
        )
        return _claims(good, bad)

    binding = _binding()
    result, _provider, log = _execute(
        binding,
        [_tools(("search_current_impacts", {"hazard_ids": ["H1"]})), finish],
        max_model_turns=2,
    )
    _bridge, mapped = _map(binding, result)
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert mapped.disposition is LiveOpsNativeResultDisposition.PARTIALLY_VERIFIED
    assert mapped.verified_claims is result.verified_claims
    assert [item.aircraft_id for item in mapped.confirmed_impact_claims] == ["a"]
    assert "missing" not in [item.aircraft_id for item in mapped.verified_claims]
    assert mapped.aircraft_impact_count is None
    assert mapped.aircraft_impact_count != len(mapped.confirmed_impact_claims)
    assert mapped.count_state is LiveOpsNativeCountState.ABSENT
    assert mapped.native_query_result_zero_claims == ()
    assert mapped.verified_claims_are_complete_coverage is False
    assert len(log) == 1
    _assert_unauthenticated(mapped)


def test_absent_context_is_not_a_verified_zero():
    binding = _binding()
    zero_result, _provider, zero_log = _execute(
        binding,
        [
            _tools(("find_aircraft_by_callsign", {"callsign": "NONE"})),
            lambda turn: _claims(_callsign_claim(turn.tool_results[0])),
        ],
        max_model_turns=2,
    )
    absent_result, _provider, absent_log = _execute(
        binding,
        [
            _tools(("get_aircraft_operational_context", {"aircraft_id": "missing-ac"})),
            lambda turn: _claims(
                ContextRootAbsentClaim(
                    tool_call_id=turn.tool_results[0].tool_call_id,
                    tool_name="get_aircraft_operational_context",
                    entity_id="missing-ac",
                )
            ),
        ],
        max_model_turns=2,
    )
    zero_mapped = _map(binding, zero_result)[1]
    absent_mapped = _map(binding, absent_result)[1]

    assert zero_result.verified_claims[0].kind is LiveOpsPublicClaimKind.QUERY_RESULT_ZERO
    assert zero_mapped.count_state is LiveOpsNativeCountState.VERIFIED_ZERO
    assert zero_mapped.native_query_result_zero_claims[0] is zero_result.verified_claims[0]
    assert zero_mapped.context_root_absent_claims == ()
    assert zero_mapped.verified_zero is False
    assert zero_mapped.absence_counted_as_zero is False
    assert absent_result.verified_claims[0].kind is LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT
    assert absent_mapped.count_state is LiveOpsNativeCountState.CONTEXT_ROOT_ABSENT
    assert absent_mapped.native_query_result_zero_claims == ()
    assert absent_mapped.context_root_absent_claims[0] is absent_result.verified_claims[0]
    assert absent_mapped.verified_zero is False
    assert absent_mapped.absence_counted_as_zero is False
    assert zero_log[-1][0] == "find_aircraft_by_callsign"
    assert absent_log[-1][0] == "get_aircraft_operational_context"
    _assert_unauthenticated(zero_mapped)
    _assert_unauthenticated(absent_mapped)


def test_unauthorized_aircraft_and_callsign_selectors_stay_untrusted():
    hints = MasterRouteHints(callsign="N123AA", typed_aircraft_id="ac-9")
    binding = _binding(hints=hints)
    bridge = prepare_master_live_ops_native_bridge(binding)
    assert bridge.request.selector_hints is None
    assert bridge.request.entity_hints == ()
    assert bridge.selectors_authorized is False
    with pytest.raises(ContractValidationError, match="callsign_not_authenticated_aircraft"):
        reject_unauthorized_selectors(bridge, callsign="N123AA")
    with pytest.raises(ContractValidationError, match="callsign_not_authenticated_aircraft"):
        reject_unauthorized_selectors(
            bridge,
            selector_hints=LiveOpsSelectorHints(callsign="N123AA"),
        )
    with pytest.raises(ContractValidationError, match="callsign_not_authenticated_aircraft"):
        reject_unauthorized_selectors(
            bridge,
            entity_hints=(
                LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id="ac-9", callsign="N123AA"),
            ),
        )
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        reject_unauthorized_selectors(bridge, aircraft_id="ac-9")
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        reject_unauthorized_selectors(bridge, population_aircraft_ids=("ac-9",))
    reject_unauthorized_selectors(bridge)

    result, _provider, log = _execute(
        binding,
        [
            _tools(("find_aircraft_by_callsign", {"callsign": "N123AA"})),
            lambda turn: _claims(_callsign_claim(turn.tool_results[0])),
        ],
        tables=_unique_tables(),
        max_model_turns=2,
    )
    mapped = accept_native_live_ops_result(
        bridge,
        result,
        source_observation_epoch=fixtures.NOW,
    )
    assert result.verified_entity_refs[0].callsign == "N123AA"
    assert result.discoveries[0].selection_authorized is False
    assert log[0][1]["callsign"] == "N123AA"
    assert mapped.selectors_authorized is False
    assert mapped.callsign_authenticated is False
    assert mapped.native_entity_ref_authorizes_selector is False
    assert bridge.request.selector_hints is None
    _assert_unauthenticated(mapped)


def test_california_sigmet_region_string_is_not_geographic_coverage():
    binding = _binding(
        user_text=CALIFORNIA_TEXT,
        hints=MasterRouteHints(region_text="California"),
    )
    bridge = prepare_master_live_ops_native_bridge(binding)
    assert bridge.request.selector_hints is None
    assert "California" not in bridge.request.to_dict().values()
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        reject_unauthorized_selectors(bridge, region="California")
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        reject_unauthorized_selectors(
            bridge,
            selector_hints=LiveOpsSelectorHints(region="California", product_type="SIGMET"),
        )

    def decide(turn):
        if not turn.tool_results:
            return _tools(
                ("search_current_hazards", {"region": "California", "product_type": "SIGMET"})
            )
        latest = turn.tool_results[-1]
        if latest.tool_name == "search_current_hazards":
            selected = []
            for row in latest.data["hazards"]:
                hazard = row.get("hazard") or {}
                if (
                    hazard.get("product_type") == "SIGMET"
                    and row.get("spatial_status") == "INTERSECTS"
                ):
                    selected.append(hazard["hazard_id"])
            return _tools(("search_current_impacts", {"hazard_ids": sorted(set(selected))}))
        hazard_projection = next(
            item for item in turn.tool_results if item.tool_name == "search_current_hazards"
        )
        region = hazard_projection.data["region"]
        intersections = []
        for row in hazard_projection.data["hazards"]:
            hazard = row.get("hazard") or {}
            if hazard.get("product_type") == "SIGMET" and row.get("spatial_status") == "INTERSECTS":
                intersections.append(
                    HazardRegionIntersectionClaim(
                        tool_call_id=hazard_projection.tool_call_id,
                        hazard_id=hazard["hazard_id"],
                        source_version=hazard["source_version"],
                        region_code=region["code"],
                        region_name=region["name"],
                        region_query=region["query"],
                    )
                )
        impact = next(item for item in turn.tool_results if item.tool_name == "search_current_impacts")
        return _claims(*intersections, *_impact_claims(impact))

    result, _provider, log = _execute(
        binding,
        [decide, decide, decide],
        max_model_turns=3,
        max_tool_calls=2,
    )
    mapped = accept_native_live_ops_result(
        bridge,
        result,
        source_observation_epoch=fixtures.NOW,
    )
    assert log[0] == (
        "search_current_hazards",
        {"region": "California", "product_type": "SIGMET"},
    )
    assert mapped.regional_string_matches
    assert {item.region_query for item in mapped.regional_string_matches} == {"California"}
    assert any(item.region_code == "CA" for item in mapped.regional_string_matches)
    assert all(item.sigmet_geographic_coverage is False for item in mapped.regional_string_matches)
    assert mapped.regional_match_repeats_withheld_region_text is True
    assert mapped.geospatial_coverage_established is False
    assert mapped.regional_string_match_is_sigmet_coverage is False
    assert mapped.census_region_resolution_is_sigmet_coverage is False
    assert mapped.confirmed_impact_claims
    assert mapped.aircraft_impact_count is None
    assert mapped.aircraft_impact_count != len(mapped.confirmed_impact_claims)
    assert mapped.count_state is not LiveOpsNativeCountState.VERIFIED_ZERO
    assert mapped.native_execution_is_source_evidence is False
    _assert_unauthenticated(mapped)


def test_bridge_does_not_construct_clients_or_grant_execution():
    binding = _binding()
    bridge = prepare_master_live_ops_native_bridge(binding)
    source = MODULE.read_text(encoding="utf-8")
    for banned in (
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "LiveOpsSpecialist(",
        "LiveOpsAdapter(",
        "LiveOpsTrustedContext(",
        "OperationalTables(",
        "ToolResult(",
        "resolve_region(",
        ".dispatch(",
        ".run(",
    ):
        assert banned not in source
    signature = inspect.signature(prepare_master_live_ops_native_bridge)
    assert list(signature.parameters) == ["binding"]
    with pytest.raises(TypeError):
        prepare_master_live_ops_native_bridge(binding, provider=FakeProvider([]))  # type: ignore[call-arg]
    assert bridge.missing_integration_dependency == "trusted_host_execution_grant"
    assert bridge.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert bridge.production_authority is MasterProductionAuthority.DENIED
    assert bridge.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert bridge.dispatch_permitted is False
    assert bridge.executable is False
    assert bridge.specialist_invoked is False
    assert callable(bridge) is False
    names = set(type(bridge).__dataclass_fields__)
    assert "provider" not in names
    assert "tables" not in names
    assert "client" not in names
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(bridge, executable=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(bridge, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(bridge, specialist_invoked=True)
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(bridge, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(bridge, authenticated_source_evidence=True)
    with pytest.raises(ContractValidationError, match="geospatial_coverage_not_established"):
        replace(bridge, geospatial_coverage_established=True)
    with pytest.raises(ContractValidationError, match="verified_zero_not_established"):
        replace(bridge, verified_zero=True)
    with pytest.raises(ContractValidationError, match="aircraft_impact_count_not_authoritative"):
        replace(bridge, aircraft_impact_count=2)
    with pytest.raises(ContractValidationError, match="selection_hints_not_trusted"):
        replace(bridge, selectors_authorized=True)


def test_concurrent_native_results_do_not_exchange_identity():
    left = _binding(
        master_request_id="master-req-live-bridge-left",
        correlation_id="corr-live-bridge-left",
    )
    right = _binding(
        master_request_id="master-req-live-bridge-right",
        correlation_id="corr-live-bridge-right",
    )
    left_result, _provider, _log = _execute(
        left,
        [_tools(("get_observed_network_state", {})), _network_from_turn],
        max_model_turns=2,
    )
    right_result, _provider, _log = _execute(
        right,
        [
            LiveOpsModelDecision(
                kind=LiveOpsModelDecisionKind.REFUSAL,
                refusal_code="model_refused",
            )
        ],
    )
    left_bridge = prepare_master_live_ops_native_bridge(left)
    right_bridge = prepare_master_live_ops_native_bridge(right)
    found = {}
    errors = []

    def accept(name, bridge, result):
        try:
            found[name] = accept_native_live_ops_result(
                bridge,
                result,
                source_observation_epoch=fixtures.NOW,
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=accept, args=("left", left_bridge, left_result)),
        threading.Thread(target=accept, args=("right", right_bridge, right_result)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert found["left"].correlation_id == "corr-live-bridge-left"
    assert found["left"].disposition is LiveOpsNativeResultDisposition.ALL_VERIFIED
    assert found["right"].correlation_id == "corr-live-bridge-right"
    assert found["right"].disposition is LiveOpsNativeResultDisposition.MODEL_REFUSAL
    with pytest.raises(ContractValidationError, match="request_id_mismatch"):
        accept_native_live_ops_result(
            left_bridge,
            right_result,
            source_observation_epoch=fixtures.NOW,
        )


def _map(binding, result):
    bridge = prepare_master_live_ops_native_bridge(binding)
    return bridge, accept_native_live_ops_result(
        bridge,
        result,
        source_observation_epoch=fixtures.NOW,
    )
