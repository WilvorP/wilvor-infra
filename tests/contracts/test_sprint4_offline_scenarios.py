"""Sprint 4 offline end-to-end scenarios. Injected fixtures are not AWS evidence."""

from __future__ import annotations

import pytest

from tests.contracts.sprint4_historical_harness import AS_OF, ELIGIBLE, PLAN, compose_stack
from tests.contracts.test_sprint4_copilot_contract_map import _payload
from tests.contracts.test_sprint4_decision_live import AIRCRAFT, QUERY, _full_tables
from tests.contracts.test_sprint4_live_ops_tools import _binding, _call
from tests.contracts.test_sprint4_master_runtime import ScriptedPlanner, _handle, _proposal
from tests.contracts.test_sprint4_security_ledger import _admit, _issue, _port
from tests.contracts.test_sprint3c_specialist_orchestration import (
    BEFORE,
    LIVE,
    FakeProvider,
    Harness,
    _live,
    _network_from_turn,
    _tools,
    _work,
)
from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import ContractValidationError, TemporalScope, ToolResultStatus
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_COMPLETENESS_NOTICE
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_response_policy import MasterResponsePolicyState
from wilvor_ai.master_response_renderer import MasterResponseRender
from wilvor_ai.sprint3c_grant_ledger import agent_a_identity_fixture
from wilvor_ai.sprint4_decision_live import Sprint4DecisionLiveCall
from wilvor_ai.sprint4_decision_reconciliation import RecordObservation
from wilvor_ai.sprint4_historical_tool_port import HostAuthorizedHistoricalWindow
from wilvor_ai.sprint4_integration_host import Sprint4IntegrationHost
from wilvor_ai.sprint4_live_ops_source import (
    EMPTY_PAGE_NOT_VERIFIED_ZERO,
    LiveOpsGeographicCoverage,
)
from wilvor_ai.sprint4_master_providers import reject_aws_tool_proposal
from wilvor_ai.sprint4_master_runtime import Sprint4TerminalStatus
from wilvor_ai.sprint4_security_ledger import Sprint4AdmissionKind


def _historical_window() -> HostAuthorizedHistoricalWindow:
    return HostAuthorizedHistoricalWindow(
        eligible_start_utc=ELIGIBLE[0],
        eligible_end_utc=ELIGIBLE[1],
        unbound_plan_start_utc=PLAN[0],
        unbound_plan_end_utc=PLAN[1],
        as_of_utc=AS_OF,
    )


def _ma5() -> MasterResponseRender:
    return MasterResponseRender(
        state=MasterResponsePolicyState.INSUFFICIENT_EVIDENCE,
        status_text="Supplied evidence is not sufficient for an operational claim.",
        limitation_codes=(
            "SOURCE_AUTHENTICITY_NOT_ESTABLISHED",
            "PRODUCTION_AUTHORITY_DENIED",
            "REQUEST_COVERAGE_NOT_ESTABLISHED",
            "NO_OPERATIONAL_CLAIM",
            "EXECUTION_DENIED",
        ),
    )


def test_scenario_1_current_aircraft_operational_state():
    """Exercised: Sprint 4 authorized aircraft GET. Blocked: live AWS Query beyond aircraft."""

    host = Sprint4IntegrationHost()
    tables = fixtures.california_tables()
    outcome = host.execute_live_ops_tool(
        _binding(
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
        ),
        tables=tables,
        call=_call(tables),
        tool_name="get_aircraft_operational_context",
        arguments={"aircraft_id": "a"},
    )
    assert outcome.tool_result.status is ToolResultStatus.SUCCESS
    assert outcome.tool_result.data["found"] is True
    assert outcome.tool_result.data["aircraft_id"] == "a"
    assert outcome.live_aws_initiated is False
    assert outcome.source_report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_scenario_2_sigmet_intersection_with_observed_aircraft():
    """Exercised: computed-geometry intersection. Blocked: California-text coverage."""

    host = Sprint4IntegrationHost()
    tables = fixtures.california_tables()
    hazards = host.execute_live_ops_tool(
        _binding(
            tool_name="search_current_hazards",
            resource_id="hazards",
            operation=ReadOperation.QUERY,
        ),
        tables=tables,
        call=_call(tables, tool_call_id="tool-hazards"),
        tool_name="search_current_hazards",
        arguments={"region": "California", "product_type": "SIGMET"},
    )
    impacts = host.execute_live_ops_tool(
        _binding(
            tool_name="search_current_impacts",
            resource_id="hazards",
            operation=ReadOperation.QUERY,
            idempotency_key="idem-impacts",
            payload_fingerprint="fingerprint-impacts",
        ),
        tables=tables,
        call=_call(tables, tool_call_id="tool-impacts"),
        tool_name="search_current_impacts",
        arguments={"region": "California", "hazard_ids": ["H1"]},
    )
    assert hazards.source_report.geographic_coverage is LiveOpsGeographicCoverage.INTERSECTION
    assert "H1" in hazards.source_report.intersecting_hazard_ids
    assert hazards.source_report.region_label_is_polygon is False
    assert impacts.verified_zero is False
    slice_result = host.compose_hybrid_slice(live_ops=hazards)
    assert slice_result.california_coverage_fabricated is False
    assert slice_result.claims_admitted is False


def test_scenario_3_historical_encounter_risk_analysis():
    """Exercised: Athena catalog tool. Blocked: live Athena and historical DynamoDB Query."""

    host = Sprint4IntegrationHost()
    runtime, port, athena, _s3, _clock = compose_stack()
    host.compose_historical_athena_runtime(
        athena_client=athena,
        s3_client=_s3,
        config=runtime.config,
        monotonic_clock=_clock.monotonic,
        sleeper=_clock.sleep,
    )
    outcome = host.execute_historical_athena_tool(
        port,
        tool_name="summarize_historical_encounters",
        arguments={"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
        tool_call_id="hist-enc-1",
        window=_historical_window(),
    )
    assert outcome.refusal is None
    assert outcome.live_athena_initiated is False
    assert outcome.production_query is False
    assert outcome.provenance.temporal_scope is TemporalScope.HISTORICAL
    assert outcome.provenance.verified_zero_from_empty_uncertified is False


def test_scenario_4_decision_risk_and_recommendation_evidence():
    """Exercised: advisory Decision join. Blocked: invented LOW risk and host proofs."""

    host = Sprint4IntegrationHost()
    present = host.execute_decision_live(
        Sprint4DecisionLiveCall(
            tables=_full_tables(),
            now_epoch=1_788_661_800,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            recommendation_id="rec-1",
        )
    )
    absent = host.execute_decision_live(
        Sprint4DecisionLiveCall(
            tables=_full_tables(include_risk=False),
            now_epoch=1_788_661_800,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
        )
    )
    assert present.missing.risk is RecordObservation.PRESENT
    assert present.missing.synthesized_low_risk is False
    assert absent.missing.risk is RecordObservation.ABSENT
    assert absent.missing.synthesized_low_risk is False
    assert present.advisory.advisory_only is True
    binding = host.specialist_runtime_binding()
    assert binding.decision_host_proofs_issued is False


def test_scenario_5_multi_specialist_current_and_historical():
    """Exercised: side-by-side current + historical artifacts. Blocked: fused operational claim."""

    host = Sprint4IntegrationHost()
    tables = fixtures.california_tables()
    live_ops = host.execute_live_ops_tool(
        _binding(
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
        ),
        tables=tables,
        call=_call(tables),
        tool_name="get_aircraft_operational_context",
        arguments={"aircraft_id": "a"},
    )
    _runtime, port, _athena, _s3, _clock = compose_stack()
    historical = host.execute_historical_athena_tool(
        port,
        tool_name="summarize_historical_encounters",
        arguments={"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
        tool_call_id="hist-hybrid-1",
        window=_historical_window(),
    )
    hybrid = host.compose_hybrid_slice(live_ops=live_ops, historical=historical)
    assert TemporalScope.CURRENT in hybrid.temporal_scopes
    assert TemporalScope.HISTORICAL in hybrid.temporal_scopes
    assert hybrid.claims_admitted is False
    assert hybrid.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert hybrid.production_dispatch is False


def test_scenario_6_unsupported_or_missing_evidence():
    """Exercised: missing aircraft empty page. Blocked: verified-zero minting."""

    host = Sprint4IntegrationHost()
    tables = fixtures.tables()
    outcome = host.execute_live_ops_tool(
        _binding(
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
        ),
        tables=tables,
        call=_call(tables),
        tool_name="get_aircraft_operational_context",
        arguments={"aircraft_id": "missing"},
    )
    assert outcome.tool_result.status is ToolResultStatus.NOT_FOUND
    assert outcome.tool_result.data["found"] is False
    assert outcome.verified_zero is False
    assert EMPTY_PAGE_NOT_VERIFIED_ZERO in outcome.source_report.limitations or (
        outcome.source_report.verified_zero is False
    )


def test_scenario_7_replay_unauthorized_scope_and_invalid_tool_call():
    """Exercised: ledger replay, unallowlisted Live Ops scope, AWS tool rejection."""

    host = Sprint4IntegrationHost()
    port, issuer, _client, envelope, principal = _port()
    grant = _issue(issuer, envelope, principal)
    first = host.__class__(port).admit_started(
        grant,
        envelope=envelope,
        principal=principal,
        identity=agent_a_identity_fixture(),
        owner_id="worker-a",
        now_utc="2026-10-27T20:00:00Z",
        lease_seconds=60,
    )
    replay = _admit(port, grant, envelope, principal)
    assert first.kind is Sprint4AdmissionKind.STARTED
    assert first.audit is not None
    assert first.audit.verified_aws_provenance is False
    assert replay.kind is Sprint4AdmissionKind.REPLAYED

    tables = fixtures.california_tables()
    unauthorized = host.execute_live_ops_tool(
        _binding(
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation=ReadOperation.GET_ITEM,
        ),
        tables=tables,
        call=_call(tables),
        tool_name="get_observed_network_state",
        arguments={},
    )
    assert unauthorized.refusal_code in {
        "identity_mismatch",
        "unbounded_population_refused",
        "tool_not_allowlisted",
    }
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("GetItem")


def test_scenario_8_copilot_injected_backend_compatible_response():
    """Exercised: Copilot mapping of MA5. Blocked: POST /ai/query and fixture authenticity."""

    host = Sprint4IntegrationHost()
    mapped = host.map_copilot_query(_payload())
    result = host.project_master_to_copilot(mapped_request=mapped, render=_ma5())
    assert result.assistant_text == result.status_text
    assert result.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert result.claims_admitted is False
    assert result.route_enabled is False
    assert result.production_ai_active is False
    assert result.fixture_evidence_authenticated is False
    assert result.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    master = host.run_master(
        harness.planning(),
        ScriptedPlanner(_proposal()),
        orchestrator=harness.orchestrator,
        handles={step.invocation.invocation_id: _handle(harness, step, binding, provider)},
        now_utc=BEFORE,
        retained_decision=harness.registry.decision_for(harness.record),
    )
    assert master.workflow.terminal_status is Sprint4TerminalStatus.NATIVE_COMPLETED
    copilot = host.project_master_to_copilot(
        mapped_request=mapped,
        render=master.synthesis.rendered,
    )
    assert copilot.assistant_text == master.synthesis.rendered.status_text
    assert copilot.claims_admitted is False
