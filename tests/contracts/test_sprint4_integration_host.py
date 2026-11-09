"""Sprint 4 shared-host composition. Offline success is not production authority."""

from __future__ import annotations

import pytest

from tests.contracts.sprint4_historical_harness import AS_OF, ELIGIBLE, PLAN, compose_stack
from tests.contracts.test_sprint4_decision_live import AIRCRAFT, QUERY, _execute, _full_tables
from tests.contracts.test_sprint4_live_ops_tools import _binding, _call
from tests.contracts.test_sprint4_master_runtime import ScriptedPlanner, _handle, _proposal
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
from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.historical_specialist import HistoricalAnalyticsSpecialist, SequentialToolCallIdFactory
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint4_decision_live import Sprint4DecisionLiveCall, issue_production_decision_live_aws
from wilvor_ai.sprint4_historical_query_runtime import issue_production_historical_query
from wilvor_ai.sprint4_historical_tool_port import HostAuthorizedHistoricalWindow
from wilvor_ai.sprint4_integration_host import (
    SPRINT4_CLAIMS_ADMITTED_GLOBALLY,
    SPRINT4_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED,
    SPRINT4_OFFLINE_LEDGER_IS_AWS_VALIDATION,
    SPRINT4_PRODUCTION_INTEGRATION_AVAILABLE,
    SPRINT4_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES,
    Sprint4IntegrationHost,
    assert_sprint4_integration_closed,
    refuse_closed_production_controls,
    sprint4_iam_reconciliation,
    unresolved_integration_seams,
    unresolved_production_dependencies,
)
from wilvor_ai.sprint4_live_ops_authorization import issue_production_live_ops_aws_read
from wilvor_ai.sprint4_live_ops_source import RELATED_RESOURCE_NOT_ALLOWLISTED
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus
from wilvor_ai.sprint4_master_providers import reject_aws_tool_proposal
from wilvor_ai.sprint4_security_ledger import enable_sprint4_ledger_writes


class _BindOnlyProvider:
    def complete(self, request):
        raise AssertionError("model complete is unused in the bind test")


def _window() -> HostAuthorizedHistoricalWindow:
    return HostAuthorizedHistoricalWindow(
        eligible_start_utc=ELIGIBLE[0],
        eligible_end_utc=ELIGIBLE[1],
        unbound_plan_start_utc=PLAN[0],
        unbound_plan_end_utc=PLAN[1],
        as_of_utc=AS_OF,
    )


def test_production_gates_stay_closed():
    assert SPRINT4_PRODUCTION_INTEGRATION_AVAILABLE is False
    assert SPRINT4_CLAIMS_ADMITTED_GLOBALLY is False
    assert SPRINT4_SOURCE_AUTHENTICITY_FROM_OFFLINE_DOUBLES is False
    assert SPRINT4_OFFLINE_LEDGER_IS_AWS_VALIDATION is False
    assert SPRINT4_HISTORICAL_DYNAMODB_QUERY_SUBSTITUTED is False
    assert_sprint4_integration_closed()
    with pytest.raises(ContractValidationError, match="sprint4_production_controls_closed"):
        refuse_closed_production_controls()
    with pytest.raises(ContractValidationError, match="production_live_ops_aws_read_not_enabled"):
        issue_production_live_ops_aws_read()
    with pytest.raises(ContractValidationError, match="production_historical_query_not_enabled"):
        issue_production_historical_query()
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_decision_live_aws()
    with pytest.raises(ContractValidationError, match="sprint4_ledger_writes_disabled"):
        enable_sprint4_ledger_writes()


def test_runtime_binding_uses_athena_not_historical_dynamodb():
    host = Sprint4IntegrationHost()
    binding = host.specialist_runtime_binding()
    assert binding.historical_uses_athena is True
    assert binding.historical_uses_dynamodb_query is False
    assert binding.decision_host_proofs_issued is False
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.claims_admitted is False


def test_iam_reconciliation_rejects_historical_dynamodb_query_remainder():
    report = sprint4_iam_reconciliation()
    assert report["applied"] is False
    assert report["historical_dynamodb_query_forbidden"] is True
    assert report["agent_f_historical_dynamodb_query_remainder_rejected"] is True
    assert any("hazard_coordinates" in item for item in report["live_ops_query_resources_needed"])
    assert any("athena" in item for item in report["historical_athena_resources_needed"])
    seams = unresolved_integration_seams()
    deps = unresolved_production_dependencies()
    assert any("POST /ai/query" in item for item in seams)
    assert any("Athena" in item or "athena" in item for item in deps)


def test_live_ops_aircraft_context_does_not_query_unallowlisted_parents():
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
    assert outcome.tool_result.data["found"] is True
    assert outcome.tool_result.data["encounters"] == []
    assert RELATED_RESOURCE_NOT_ALLOWLISTED in outcome.source_report.limitations
    assert outcome.source_report.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert outcome.verified_zero is False


def test_historical_athena_binds_existing_specialist_operations():
    host = Sprint4IntegrationHost()
    runtime, port, athena, _s3, _clock = compose_stack()
    bound = host.bind_historical_tool_port(runtime)
    assert bound is port or bound.runtime is runtime
    specialist = host.compose_historical_specialist(
        runtime,
        provider=_BindOnlyProvider(),
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    assert isinstance(specialist, HistoricalAnalyticsSpecialist)
    outcome = host.execute_historical_athena_tool(
        port,
        tool_name="summarize_historical_encounters",
        arguments={"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
        tool_call_id="historical-call-1",
        window=_window(),
    )
    assert outcome.refusal is None
    assert outcome.production_query is False
    assert outcome.provenance is not None
    assert outcome.provenance.temporal_scope is TemporalScope.HISTORICAL
    assert outcome.provenance.represented_as_current_observation is False
    assert athena.start_calls


def test_decision_live_keeps_missing_risk_absent_and_proofs_unissued():
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
    assert present.missing.synthesized_low_risk is False
    assert present.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    missing = _execute(tables=_full_tables(include_risk=False), recommendation_id=None)
    assert missing.missing.synthesized_low_risk is False


def test_master_langgraph_path_stays_grounded_and_closed():
    host = Sprint4IntegrationHost()
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = _handle(harness, step, binding, provider)
    result = host.run_master(
        harness.planning(),
        ScriptedPlanner(_proposal()),
        orchestrator=harness.orchestrator,
        handles={step.invocation.invocation_id: handle},
        now_utc=BEFORE,
        retained_decision=harness.registry.decision_for(harness.record),
    )
    assert result.planning.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert result.synthesis is not None
    assert result.synthesis.claims_admitted is False
    assert result.synthesis.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_invalid_aws_tool_call_is_rejected_before_dispatch():
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("GetItem", {"TableName": "wilvor-dev-aircraft-current-state"})
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("summarize_historical_encounters", {"sql": "SELECT 1"})
