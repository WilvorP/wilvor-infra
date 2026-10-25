"""Readiness for a Master Live Ops invocation. The specialist is not run."""

from __future__ import annotations

import importlib
import inspect
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from tests.fixtures.ma4_specialist_evidence_examples import (
    live_ops_all_verified_result,
    live_ops_context,
    live_ops_request,
)
from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    TemporalScope,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_adapter import LiveOpsAdapter, _validate_selectors
from wilvor_ai.live_ops_model_contracts import LiveOpsModelProvider, LiveOpsValidationCode
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LiveOpsRunRequest,
    LiveOpsSpecialistPort,
    LiveOpsTrustedContext,
)
from wilvor_ai.live_ops_specialist import LiveOpsSpecialist, decode_and_run_live_ops
from wilvor_ai.live_ops_specialist_runtime_contracts import LiveOpsSpecialistTrustedContext
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import MasterEvidenceCorroborationInput
from wilvor_ai.master_execution_preflight import evaluate_execution_preflight
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_live_ops_execution_readiness import (
    LIVE_OPS_ADAPTER_SELECTOR_RULES,
    LIVE_OPS_READ_ONLY_TOOL_NAMES,
    LIVE_OPS_REAL_INVOCATIONS,
    MasterLiveOpsExecutionBlocker,
    MasterLiveOpsExecutionReadiness,
    MasterLiveOpsInterfaceConflict,
    assess_master_live_ops_execution_readiness,
)
from wilvor_ai.master_parallel_contracts import (
    MasterExecutionPreflightState,
    MasterParallelContractBundle,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_adapters import describe_planned_adapters
from wilvor_ai import live_ops as live_ops_tools
from wilvor_operational.context import (
    build_aircraft_operational_context,
    build_airport_operational_context,
    build_hazard_operational_context,
)
from wilvor_operational.discovery import (
    discover_aircraft_by_callsign,
    discover_current_hazards,
)
from wilvor_operational.geospatial import discover_current_hazards_in_region
from wilvor_operational.query import (
    get_observed_network_state as query_observed_network_state,
)
from wilvor_operational.query import (
    search_current_encounters as query_search_current_encounters,
)
from wilvor_operational.query import (
    search_current_impacts as query_search_current_impacts,
)
from wilvor_operational.readers import OperationalTables
from wilvor_operational.regions import resolve_region


TEXT = "Which aircraft are currently affected by hazards?"
SIGMET_TEXT = "Which current SIGMETs cover California right now?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    REPO_ROOT
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "master_live_ops_execution_readiness.py"
)
_ASSIGNED_PATHS = {
    "functions/shared/wilvor_ai/master_live_ops_execution_readiness.py",
    "tests/contracts/test_master_live_ops_execution_readiness.py",
    "tests/contracts/test_master_live_ops_execution_readiness_import_isolation.py",
}

_REAL_CALLABLES = (
    LiveOpsSpecialist.__init__,
    LiveOpsSpecialist.run,
    LiveOpsSpecialistPort.run,
    LiveOpsModelProvider.complete,
    LiveOpsTrustedContext,
    LiveOpsSpecialistTrustedContext,
    LiveOpsAdapter.__init__,
    LiveOpsAdapter.dispatch,
    live_ops_tools.LiveOpsCall,
    OperationalTables,
    live_ops_tools.search_current_hazards,
    live_ops_tools.search_current_impacts,
    live_ops_tools.search_current_encounters,
    live_ops_tools.get_observed_network_state,
    live_ops_tools.get_aircraft_operational_context,
    live_ops_tools.get_hazard_operational_context,
    live_ops_tools.get_airport_operational_context,
    live_ops_tools.find_aircraft_by_callsign,
    discover_current_hazards_in_region,
    discover_current_hazards,
    discover_aircraft_by_callsign,
    query_search_current_impacts,
    query_search_current_encounters,
    query_observed_network_state,
    build_aircraft_operational_context,
    build_hazard_operational_context,
    build_airport_operational_context,
    resolve_region,
    decode_and_run_live_ops,
    LiveOpsSpecialistPort,
)


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-live-ready-1",
        "correlation_id": "corr-live-ready-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _proposal(*steps: MasterProposedStep, user_text: str = TEXT) -> MasterRouteProposal:
    capabilities = tuple(
        dict.fromkeys(
            MasterProposedCapability(step.capability, step.requirement) for step in steps
        )
    )
    return MasterRouteProposal(user_text=user_text, capabilities=capabilities, steps=steps)


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "capability": LIVE,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _history(step_id: str = "hist-1") -> MasterProposedStep:
    return MasterProposedStep(
        step_id=step_id,
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORY,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc="2026-01-01T00:00:00Z",
        historical_end_utc="2026-01-02T00:00:00Z",
    )


def _bundle(
    *steps: MasterProposedStep,
    user_text: str = TEXT,
    context: MasterPlanningContext | None = None,
    evidence: tuple[MasterEvidenceCorroborationInput, ...] = (),
) -> MasterParallelContractBundle:
    planning = context or _context(canonical_user_text=user_text)
    decision = authorize_route(_proposal(*steps, user_text=user_text), planning)
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    return MasterParallelContractBundle(
        context=planning,
        plan=decision.plan,
        evidence_inputs=evidence,
    )


def _descriptor(bundle: MasterParallelContractBundle, step_id: str):
    described = describe_planned_adapters(bundle)
    matches = [item for item in described.descriptors if item.step_id == step_id]
    assert len(matches) == 1
    return matches[0]


def _assess(bundle: MasterParallelContractBundle, step_id: str = "live-1"):
    return assess_master_live_ops_execution_readiness(bundle, _descriptor(bundle, step_id))


def _unbound(readiness: MasterLiveOpsExecutionReadiness, module: str, qualname: str, parameter: str):
    matches = [
        item
        for item in readiness.unbound_parameters
        if item.module == module and item.qualname == qualname and item.parameter == parameter
    ]
    assert len(matches) == 1
    return matches[0]


def _assert_blocked(readiness: MasterLiveOpsExecutionReadiness) -> None:
    assert readiness.public_request_constructible is True
    assert isinstance(readiness.request, LiveOpsRunRequest)
    assert readiness.request is readiness.binding.request
    assert readiness.request.schema_version == LIVE_OPS_PUBLIC_SCHEMA_VERSION
    assert readiness.execution_blocked is True
    assert readiness.dispatch_permitted is False
    assert readiness.executable is False
    assert readiness.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert readiness.production_authority is MasterProductionAuthority.DENIED
    assert readiness.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert readiness.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert readiness.source_read_authorized is False
    assert readiness.trusted_region_authorized is False
    assert readiness.trusted_runtime_present is False
    assert readiness.source_observation_clock_present is False
    assert readiness.geospatial_coverage_established is False
    assert readiness.claimed_scope_establishes_geospatial_coverage is False
    assert readiness.census_region_resolution_is_sigmet_coverage is False
    assert readiness.application_request_time_is_source_clock is False
    assert readiness.adapter_selector_rules_satisfied is False
    assert readiness.selectors_authorized is False
    assert readiness.specialist_invoked is False
    assert readiness.tool_result_created is False
    assert readiness.aircraft_impact_count is None
    assert readiness.verified_zero is False
    assert readiness.request.selector_hints is None
    assert readiness.request.entity_hints == ()
    assert callable(readiness) is False
    assert not hasattr(readiness, "run")
    assert not hasattr(readiness, "dispatch")
    for blocker in (
        MasterLiveOpsExecutionBlocker.EXECUTION_DENIED,
        MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_CONTEXT_ABSENT,
        MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_TABLES_ABSENT,
        MasterLiveOpsExecutionBlocker.SOURCE_OBSERVATION_CLOCK_ABSENT,
        MasterLiveOpsExecutionBlocker.READ_ONLY_SOURCE_ADAPTERS_NOT_AUTHORIZED,
        MasterLiveOpsExecutionBlocker.AUTHORIZED_SELECTORS_ABSENT,
        MasterLiveOpsExecutionBlocker.GEOSPATIAL_COVERAGE_NOT_ESTABLISHED,
        MasterLiveOpsExecutionBlocker.MODEL_PROVIDER_ABSENT,
    ):
        assert blocker in readiness.blockers
    assert MasterLiveOpsInterfaceConflict.CENSUS_REGION_RESOLUTION_IS_NOT_SIGMET_COVERAGE in (
        readiness.interface_conflicts
    )
    assert MasterLiveOpsInterfaceConflict.OPTIONAL_REGION_IS_NOT_AN_AUTHORIZED_SELECTOR in (
        readiness.interface_conflicts
    )
    assert MasterLiveOpsInterfaceConflict.READ_ONLY_ADVISORY_IS_NOT_SOURCE_READ_AUTHORITY in (
        readiness.interface_conflicts
    )


def test_valid_public_request_is_constructible_and_execution_stays_blocked():
    bundle = _bundle(_live(), _history())
    readiness = _assess(bundle)
    binding = prepare_master_live_ops_binding(bundle, _descriptor(bundle, "live-1"))
    restored = LiveOpsRunRequest.from_dict(readiness.request.to_dict())

    assert readiness.request == binding.request
    assert readiness.request.user_text == TEXT
    assert readiness.request.request_id == binding.specialist_request_id
    assert readiness.specialist_request_id == binding.specialist_request_id
    assert readiness.correlation_id == binding.correlation_id
    assert readiness.invocation_id == binding.invocation_id
    assert readiness.master_request_id == binding.master_request_id
    assert readiness.application_request_time_utc == binding.application_request_time_utc
    assert restored.user_text == readiness.request.user_text
    assert restored.request_id == readiness.request.request_id
    assert restored.selector_hints is None
    assert "now_epoch" not in readiness.request.to_dict()
    assert "tables" not in readiness.request.to_dict()
    _assert_blocked(readiness)
    tables = _unbound(
        readiness,
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsTrustedContext",
        "tables",
    )
    clock = _unbound(
        readiness,
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsTrustedContext",
        "now_epoch",
    )
    correlation = _unbound(
        readiness,
        "wilvor_ai.live_ops_public_contracts",
        "LiveOpsTrustedContext",
        "correlation_id",
    )
    region = _unbound(
        readiness,
        "wilvor_ai.live_ops",
        "search_current_hazards",
        "region",
    )
    assert tables.blocker is MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_TABLES_ABSENT
    assert tables.preserved_plan_value is None
    assert clock.blocker is MasterLiveOpsExecutionBlocker.SOURCE_OBSERVATION_CLOCK_ABSENT
    assert clock.preserved_plan_value is None
    assert correlation.preserved_plan_value == readiness.correlation_id
    assert correlation.blocker is MasterLiveOpsExecutionBlocker.TRUSTED_RUNTIME_CONTEXT_ABSENT
    assert region.blocker is MasterLiveOpsExecutionBlocker.AUTHORIZED_SELECTORS_ABSENT
    assert region.preserved_plan_value is None
    assert (
        "wilvor_ai.live_ops_specialist",
        "LiveOpsSpecialist.run",
    ) in readiness.must_connect_interfaces
    assert (
        "wilvor_ai.live_ops_specialist",
        "decode_and_run_live_ops",
    ) in readiness.not_master_connection_interfaces
    assert (
        "wilvor_operational.regions",
        "resolve_region",
    ) in readiness.not_coverage_authority_interfaces
    hazards = next(
        item
        for item in readiness.invocation_interfaces
        if item.qualname == "search_current_hazards"
    )
    region_parameter = next(item for item in hazards.parameters if item.name == "region")
    assert region_parameter.required is False
    assert hazards.authority_mode == AgentAuthorityMode.READ_ONLY_ADVISORY.value
    assert readiness.binding.capability is LIVE
    assert readiness.binding.temporal_scopes == (TemporalScope.CURRENT,)


def test_distinct_invocations_preserve_request_and_correlation_ids():
    bundle = _bundle(_live("live-west"), _live("live-east"))
    west = _assess(bundle, "live-west")
    east = _assess(bundle, "live-east")
    west_step = next(item for item in bundle.plan.steps if item.step_id == "live-west")
    east_step = next(item for item in bundle.plan.steps if item.step_id == "live-east")
    other_context = _context(
        master_request_id="master-req-live-ready-2",
        correlation_id="corr-live-ready-2",
    )
    other = _assess(
        _bundle(_live(), context=other_context),
    )

    assert west.request.request_id == west_step.invocation.specialist_request_id
    assert east.request.request_id == east_step.invocation.specialist_request_id
    assert west.specialist_request_id != east.specialist_request_id
    assert west.invocation_id != east.invocation_id
    assert west.correlation_id == bundle.context.correlation_id
    assert east.correlation_id == bundle.context.correlation_id
    assert west.correlation_id == east.correlation_id
    assert west.request.request_id != west.correlation_id
    assert west.request.request_id != west.master_request_id
    assert other.correlation_id == "corr-live-ready-2"
    assert other.specialist_request_id != west.specialist_request_id
    assert other.request.request_id == other.specialist_request_id
    assert other.master_request_id == "master-req-live-ready-2"
    _assert_blocked(west)
    _assert_blocked(east)
    _assert_blocked(other)


def test_california_sigmet_claim_does_not_authorize_region_or_coverage():
    hints = MasterRouteHints(region_text="California")
    bundle = _bundle(_live(hints=hints), user_text=SIGMET_TEXT)
    readiness = _assess(bundle)
    resolved = resolve_region("California")
    sigmet_name = resolve_region("SIGMET")
    payload = readiness.request.to_dict()

    assert resolved.resolved is True
    assert resolved.region is not None
    assert resolved.region.code == "CA"
    assert resolved.region.region_type == "US_STATE"
    assert "SIGMET" not in resolved.region.dataset_product.upper()
    assert sigmet_name.resolved is False
    assert readiness.request.user_text == SIGMET_TEXT
    assert readiness.request.selector_hints is None
    assert payload["selector_hints"] is None
    assert readiness.binding.withheld_untrusted_hints.region_text == "California"
    assert MasterRouteGate.REGION_VALIDATION_PENDING in readiness.binding.gates
    assert readiness.structural_ready_to_execute is False
    assert readiness.trusted_region_authorized is False
    assert readiness.geospatial_coverage_established is False
    assert (
        MasterLiveOpsExecutionBlocker.UNTRUSTED_REGION_HINT_NOT_SELECTOR
        in readiness.blockers
    )
    region = _unbound(readiness, "wilvor_ai.live_ops", "search_current_hazards", "region")
    region_query = _unbound(
        readiness,
        "wilvor_operational.geospatial",
        "discover_current_hazards_in_region",
        "region_query",
    )
    claimed = _unbound(
        readiness,
        "wilvor_operational.regions",
        "resolve_region",
        "text",
    )
    product = _unbound(
        readiness,
        "wilvor_ai.live_ops",
        "search_current_hazards",
        "product_type",
    )
    assert region.blocker is MasterLiveOpsExecutionBlocker.UNTRUSTED_REGION_HINT_NOT_SELECTOR
    assert region.preserved_plan_value is None
    assert region_query.preserved_plan_value is None
    assert claimed.preserved_plan_value is None
    assert product.blocker is MasterLiveOpsExecutionBlocker.UNTRUSTED_FILTER_NOT_AUTHORIZED
    assert product.preserved_plan_value is None
    preserved = " ".join(
        item.preserved_plan_value or "" for item in readiness.unbound_parameters
    )
    assert "California" not in preserved
    assert "SIGMET" not in preserved
    assert "California" in payload["user_text"]
    assert "SIGMET" in payload["user_text"]
    assert payload["selector_hints"] is None
    _assert_blocked(readiness)


def test_user_text_is_not_parsed_into_selectors_or_sigmet_coverage():
    bundle = _bundle(_live(), user_text=SIGMET_TEXT)
    readiness = _assess(bundle)
    region = _unbound(readiness, "wilvor_ai.live_ops", "search_current_hazards", "region")
    product = _unbound(readiness, "wilvor_ai.live_ops", "search_current_hazards", "product_type")

    assert readiness.request.user_text == SIGMET_TEXT
    assert readiness.request.selector_hints is None
    assert readiness.binding.withheld_untrusted_hints == MasterRouteHints()
    assert region.blocker is MasterLiveOpsExecutionBlocker.AUTHORIZED_SELECTORS_ABSENT
    assert product.preserved_plan_value is None
    assert (
        MasterLiveOpsExecutionBlocker.UNTRUSTED_REGION_HINT_NOT_SELECTOR
        not in readiness.blockers
    )
    assert readiness.geospatial_coverage_established is False
    _assert_blocked(readiness)


def test_callsign_and_typed_identifiers_are_not_authorized_selectors():
    hints = MasterRouteHints(
        callsign="UAL123",
        typed_aircraft_id="ac-9",
        population_aircraft_ids=("ac-9", "ac-10"),
    )
    bundle = _bundle(_live(hints=hints))
    readiness = _assess(bundle)
    payload = readiness.request.to_dict()
    callsign = _unbound(readiness, "wilvor_ai.live_ops", "find_aircraft_by_callsign", "callsign")
    aircraft = _unbound(
        readiness,
        "wilvor_ai.live_ops",
        "get_aircraft_operational_context",
        "aircraft_id",
    )

    assert callsign.blocker is MasterLiveOpsExecutionBlocker.UNTRUSTED_CALLSIGN_NOT_SELECTOR
    assert callsign.preserved_plan_value is None
    assert aircraft.blocker is MasterLiveOpsExecutionBlocker.UNTRUSTED_TYPED_AIRCRAFT_ID_NOT_SELECTOR
    assert aircraft.preserved_plan_value is None
    assert MasterLiveOpsExecutionBlocker.UNTRUSTED_POPULATION_NOT_SELECTOR in readiness.blockers
    assert "UAL123" not in payload.values()
    assert "ac-9" not in payload.values()
    assert readiness.aircraft_impact_count is None
    assert readiness.aircraft_impact_count != len(hints.population_aircraft_ids)
    _assert_blocked(readiness)

    population_only = _assess(
        _bundle(_live(hints=MasterRouteHints(population_aircraft_ids=("ac-1",))))
    )
    population_aircraft = _unbound(
        population_only,
        "wilvor_ai.live_ops",
        "get_aircraft_operational_context",
        "aircraft_id",
    )
    assert (
        population_aircraft.blocker
        is MasterLiveOpsExecutionBlocker.UNTRUSTED_POPULATION_NOT_SELECTOR
    )
    assert population_only.aircraft_impact_count is None
    assert population_only.verified_zero is False

    recommendation = _assess(
        _bundle(_live(hints=MasterRouteHints(recommendation_id="rec-9")))
    )
    assert (
        MasterLiveOpsExecutionBlocker.UNTRUSTED_RECOMMENDATION_ID_NOT_SELECTOR
        in recommendation.blockers
    )
    assert "rec-9" not in recommendation.request.to_dict().values()
    assert recommendation.aircraft_impact_count is None


def test_structural_readiness_is_not_source_read_or_dispatch_permission():
    bundle = _bundle(_live(hints=MasterRouteHints(callsign="UAL123")))
    readiness = _assess(bundle)
    assert readiness.structural_ready_to_execute is True
    assert readiness.binding.structural_ready_to_execute is True
    assert readiness.execution_blocked is True
    assert readiness.dispatch_permitted is False
    assert readiness.source_read_authorized is False
    assert readiness.trusted_runtime_present is False
    _assert_blocked(readiness)


def test_supplied_evidence_and_forged_authority_do_not_open_execution():
    bundle = _bundle(_live())
    descriptor = _descriptor(bundle, "live-1")
    supplied_request = live_ops_request()
    supplied_context = live_ops_context()
    supplied_result = live_ops_all_verified_result()
    authorization = bundle.plan.steps[0].invocation
    evidence = (
        MasterEvidenceCorroborationInput(
            MasterEvidenceAdmissionInput(
                authorization,
                LiveOpsAdmissionSources(supplied_request, supplied_context, supplied_result),
            )
        ),
    )
    supplied = _bundle(_live(), evidence=evidence)
    readiness = _assess(supplied)

    assert readiness.request is not supplied_request
    assert readiness.request.user_text == TEXT
    assert readiness.request.selector_hints is None
    assert not isinstance(readiness.request, type(supplied_result))
    for item in readiness.__dict__.values():
        assert item is not supplied_context
        assert item is not supplied_result
        assert item is not supplied_request
    _assert_blocked(readiness)

    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            execution_authority="GRANTED",
        )
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            source_authenticity="AUTHENTICATED",
        )
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        assess_master_live_ops_execution_readiness(bundle, descriptor, executable=True)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        assess_master_live_ops_execution_readiness(bundle, descriptor, dispatch_permitted=True)
    historical = _bundle(_history())
    with pytest.raises(ContractValidationError, match="specialist_mismatch"):
        _assess(historical, "hist-1")


def test_runtime_selectors_and_clocks_cannot_be_injected():
    bundle = _bundle(_live())
    descriptor = _descriptor(bundle, "live-1")
    signature = inspect.signature(assess_master_live_ops_execution_readiness)
    prepare_signature = inspect.signature(prepare_master_live_ops_binding)
    assert list(signature.parameters) == list(prepare_signature.parameters)
    for banned in (
        "tables",
        "region",
        "now_epoch",
        "clock",
        "provider",
        "handlers",
        "trusted_context",
        "product_type",
        "callsign",
        "coverage",
    ):
        assert banned not in signature.parameters
    with pytest.raises(TypeError):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            tables=object(),  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            region="California",  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            now_epoch=1_767_225_601,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        assess_master_live_ops_execution_readiness(
            bundle,
            descriptor,
            product_type="SIGMET",  # type: ignore[call-arg]
        )


def test_readiness_cannot_be_promoted():
    readiness = _assess(_bundle(_live()))
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(readiness, execution_blocked=False)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(readiness, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="source_read_not_authorized"):
        replace(readiness, source_read_authorized=True)
    with pytest.raises(ContractValidationError, match="trusted_region_not_authorized"):
        replace(readiness, trusted_region_authorized=True)
    with pytest.raises(ContractValidationError, match="trusted_runtime_absent"):
        replace(readiness, trusted_runtime_present=True)
    with pytest.raises(ContractValidationError, match="source_observation_clock_absent"):
        replace(readiness, source_observation_clock_present=True)
    with pytest.raises(ContractValidationError, match="geospatial_coverage_not_established"):
        replace(readiness, geospatial_coverage_established=True)
    with pytest.raises(ContractValidationError, match="geospatial_coverage_not_established"):
        replace(readiness, claimed_scope_establishes_geospatial_coverage=True)
    with pytest.raises(ContractValidationError, match="geospatial_coverage_not_established"):
        replace(readiness, census_region_resolution_is_sigmet_coverage=True)
    with pytest.raises(ContractValidationError, match="application_time_is_not_source_clock"):
        replace(readiness, application_request_time_is_source_clock=True)
    with pytest.raises(ContractValidationError, match="specialist_not_invoked"):
        replace(readiness, specialist_invoked=True)
    with pytest.raises(ContractValidationError, match="tool_result_not_created"):
        replace(readiness, tool_result_created=True)
    with pytest.raises(ContractValidationError, match="aircraft_impact_count_not_authoritative"):
        replace(readiness, aircraft_impact_count=0)
    with pytest.raises(ContractValidationError, match="verified_zero_not_established"):
        replace(readiness, verified_zero=True)
    with pytest.raises(ContractValidationError, match="missing_execution_blocker"):
        replace(readiness, blockers=())
    with pytest.raises(ContractValidationError, match="interface_conflict_mismatch"):
        replace(readiness, interface_conflicts=())
    _assert_blocked(readiness)


def test_real_invocation_interfaces_match_live_signatures():
    assert len(LIVE_OPS_REAL_INVOCATIONS) == len(_REAL_CALLABLES)
    assert tuple(item.name for item in LIVE_OPS_TOOLS) == LIVE_OPS_READ_ONLY_TOOL_NAMES
    for spec in LIVE_OPS_TOOLS:
        assert spec.authority_mode is AgentAuthorityMode.READ_ONLY_ADVISORY
    for entry, target in zip(LIVE_OPS_REAL_INVOCATIONS, _REAL_CALLABLES, strict=True):
        assert entry.module == target.__module__, entry.qualname
        assert entry.qualname == target.__qualname__, entry.qualname
        assert entry.authorized_to_invoke is False
        if entry.inspect_via == "class":
            assert inspect.isclass(target)
        else:
            assert not inspect.isclass(target)
        signature = inspect.signature(target)
        actual_return = signature.return_annotation
        if entry.returns is None:
            assert actual_return is inspect.Signature.empty
        elif entry.returns == "None":
            assert actual_return == "None" or actual_return is None
        else:
            assert actual_return == entry.returns
        assert len(signature.parameters) == len(entry.parameters)
        for actual, expected in zip(signature.parameters.values(), entry.parameters, strict=True):
            assert actual.name == expected.name, (entry.qualname, actual.name)
            assert actual.kind.name == expected.kind, (entry.qualname, actual.name)
            assert (actual.default is inspect.Parameter.empty) is expected.required
            if expected.annotation is None:
                assert actual.annotation is inspect.Parameter.empty
            else:
                assert actual.annotation == expected.annotation, (
                    entry.qualname,
                    expected.name,
                    actual.annotation,
                )

    hazard_rule = LIVE_OPS_ADAPTER_SELECTOR_RULES[0]
    impact_rule = LIVE_OPS_ADAPTER_SELECTOR_RULES[1]
    encounter_rule = LIVE_OPS_ADAPTER_SELECTOR_RULES[2]
    empty_hazards = _validate_selectors("search_current_hazards", {})
    empty_impacts = _validate_selectors("search_current_impacts", {})
    empty_encounters = _validate_selectors("search_current_encounters", {})
    assert empty_hazards is not None
    assert empty_hazards.code is LiveOpsValidationCode.UNBOUNDED_HAZARD_SEARCH
    assert hazard_rule.validation_code == empty_hazards.code.value
    assert empty_impacts is not None
    assert empty_impacts.code is LiveOpsValidationCode.MISSING_SELECTOR
    assert impact_rule.validation_code == empty_impacts.code.value
    assert empty_encounters is not None
    assert empty_encounters.code is LiveOpsValidationCode.MISSING_SELECTOR
    assert encounter_rule.validation_code == empty_encounters.code.value
    assert _validate_selectors("search_current_hazards", {"region": "California"}) is None
    readiness = _assess(_bundle(_live(hints=MasterRouteHints(region_text="California"))))
    assert readiness.trusted_region_authorized is False
    assert readiness.adapter_selector_rules_satisfied is False
    assert (
        _unbound(readiness, "wilvor_ai.live_ops", "search_current_hazards", "region").preserved_plan_value
        is None
    )


def test_assessment_does_not_call_specialist_tools_or_region_resolution(monkeypatch: pytest.MonkeyPatch):
    def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live ops execution was invoked")

    monkeypatch.setattr(LiveOpsSpecialist, "run", _forbidden)
    monkeypatch.setattr(
        "wilvor_ai.live_ops_specialist.decode_and_run_live_ops",
        _forbidden,
    )
    monkeypatch.setattr(live_ops_tools, "search_current_hazards", _forbidden)
    monkeypatch.setattr(live_ops_tools, "find_aircraft_by_callsign", _forbidden)
    monkeypatch.setattr(
        importlib.import_module("wilvor_operational.regions"),
        "resolve_region",
        _forbidden,
    )
    monkeypatch.setattr(
        importlib.import_module("wilvor_operational.geospatial"),
        "discover_current_hazards_in_region",
        _forbidden,
    )
    readiness = _assess(
        _bundle(
            _live(hints=MasterRouteHints(region_text="California", callsign="UAL123")),
            user_text=SIGMET_TEXT,
        )
    )
    _assert_blocked(readiness)
    assert readiness.request.user_text == SIGMET_TEXT


def test_existing_master_preflight_still_denies_dispatch():
    bundle = _bundle(_live(), _history())
    readiness = _assess(bundle)
    evaluation = evaluate_execution_preflight(bundle)
    assert evaluation.dispatch_permitted is False
    assert evaluation.stops_before_execution_authorization is True
    assert evaluation.report.executable is False
    assert evaluation.report.state is MasterExecutionPreflightState.BLOCKED_BY_AUTHORIZATION
    assert evaluation.report.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert evaluation.report.production_authority is MasterProductionAuthority.DENIED
    assert evaluation.report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert readiness.dispatch_permitted is False
    assert readiness.executable is False


def test_git_file_scope_is_only_the_readiness_files():
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    paths = set()
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        paths.add(line[3:].replace("\\", "/"))
    assert paths == _ASSIGNED_PATHS
