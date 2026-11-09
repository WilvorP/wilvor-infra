"""Sprint 5 runtime composition. Offline only. No live AWS."""

from __future__ import annotations

import pytest

from tests.contracts.sprint4_historical_harness import AS_OF, ELIGIBLE, PLAN, compose_stack
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
from tests.contracts.test_sprint4_decision_live import (
    AIRCRAFT,
    AIRPORT,
    QUERY,
    _execute,
    _full_tables,
    _persisted_tables,
    _taf,
)
from tests.contracts.test_sprint4_live_ops_tools import _binding, _call
from tests.contracts.test_sprint4_master_planning import (
    TEXT,
    CompleteOnlyPlanner,
    ScriptedPlanner,
    _context,
    _live_step,
    _proposal,
)
from tests.contracts.test_sprint4_master_runtime import _handle, _proposal as _runtime_proposal
from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_live_ops_binding import prepare_master_live_ops_binding
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.sprint4_decision_live import Sprint4DecisionLiveCall
from wilvor_ai.sprint4_decision_weather_context import WeatherContextStatus
from wilvor_ai.sprint4_historical_tool_port import HostAuthorizedHistoricalWindow
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus, plan_master_request
from wilvor_ai.sprint4_master_providers import bind_specialist_model_provider
from wilvor_ai.sprint4_master_runtime import run_sprint4_master_runtime
from wilvor_ai.sprint5_decision_reads import (
    bounded_decision_context_builder,
    wrap_decision_tables_reject_scan,
)
from wilvor_ai.sprint5_identity import (
    client_labels_from_copilot,
    host_planning_context,
    mint_host_owned_identities,
)
from wilvor_ai.sprint5_integration_host import Sprint5IntegrationHost, remaining_sprint5_blockers
from wilvor_ai.sprint5_master_planning import decode_planner_model_output
from wilvor_ai.sprint5_verification import Sprint5VerificationStatus, classify_sprint5_operations
from wilvor_ai.sprint4_copilot_contract_map import map_copilot_session_query


class FakeComplete:
    def __init__(self, decision):
        self.decision = decision

    def complete(self, turn):
        return self.decision


def _window() -> HostAuthorizedHistoricalWindow:
    return HostAuthorizedHistoricalWindow(
        eligible_start_utc=ELIGIBLE[0],
        eligible_end_utc=ELIGIBLE[1],
        unbound_plan_start_utc=PLAN[0],
        unbound_plan_end_utc=PLAN[1],
        as_of_utc=AS_OF,
    )


def test_complete_only_planner_mapping_becomes_authorized_plan():
    proposal = _proposal(_live_step())
    result = plan_master_request(_context(), CompleteOnlyPlanner(proposal.to_dict()))
    assert result.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert result.proposal is not None
    assert result.execution_authority.value == "DENIED"


def test_decode_planner_unsupported_model_decision():
    from wilvor_ai.specialist_contracts import UnsupportedReason

    decoded = decode_planner_model_output(
        ModelDecision(
            kind=ModelDecisionKind.UNSUPPORTED,
            unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
        ),
        _context(),
    )
    assert decoded is Sprint4PlanningStatus.PLANNER_UNSUPPORTED


def test_host_mints_six_distinct_identities_and_rejects_browser_ids():
    identities = mint_host_owned_identities()
    assert len(set(identities.as_tuple())) == 6
    mapped = map_copilot_session_query(
        {
            "schemaVersion": "wilvor.ai.copilot.session_query.v1",
            "liveOpsSchemaVersion": "wilvor.ai.live_ops_public.v1",
            "masterRequestId": "browser-master",
            "planningSessionId": "browser-session",
            "conversationId": "browser-conversation",
            "correlationId": "browser-corr",
            "applicationRequestTimeUtc": "2026-01-15T00:00:01Z",
            "userText": TEXT,
            "priorTurns": [],
        }
    )
    labels = client_labels_from_copilot(mapped)
    assert labels.host_identity is False
    assert labels.planning_session_id == "browser-session"
    with pytest.raises(ContractValidationError, match="browser_ids_are_not_host_identity"):
        host_planning_context(
            mapped,
            identities.__class__(
                master_request_id="browser-master",
                planning_session_id=identities.planning_session_id,
                step_id=identities.step_id,
                invocation_id=identities.invocation_id,
                specialist_request_id=identities.specialist_request_id,
                correlation_id=identities.correlation_id,
            ),
        )


def test_decision_scan_is_rejected_and_bounded_query_preserves_absent_risk():
    tables = wrap_decision_tables_reject_scan(_full_tables())
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        tables.risks.scan()
    missing = _execute(
        tables=_full_tables(include_risk=False),
        recommendation_id=None,
        context_builder=bounded_decision_context_builder(),
    )
    assert missing.missing.synthesized_low_risk is False
    assert missing.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_weather_uses_airport_status_station_id_not_airport_id_fallback():
    tables = _persisted_tables(
        airport_records={"KDEN": {"airport_id": "KDEN", "station_id": "KDEN2"}},
        metar_records={"KDEN2": {"station_id": "KDEN2", "metar_version": "m1", "observed_time_utc": "2026-09-30T16:55:00Z"}},
        taf_records={"KDEN2": {"station_id": "KDEN2", "taf_version": "t1", "source_version": "t1", "issued_at_utc": "2026-09-30T16:00:00Z"}},
    )
    result = _execute(tables=tables, aircraft_id=None)
    weather = result.weather[0]
    assert weather.metar.station_id == "KDEN2"
    assert weather.metar.airport_id == AIRPORT
    assert weather.metar.status is WeatherContextStatus.MATCHED
    assert "METAR_STATION_MISMATCH" not in {item.value for item in result.entity_links.defects}
    unresolved = _execute(
        tables=_persisted_tables(airport_records={}, taf_records={AIRPORT: _taf()}),
        aircraft_id=None,
    )
    assert unresolved.weather[0].metar.status is WeatherContextStatus.STATION_UNRESOLVED


def test_specialist_alias_is_not_a_catalog_tool():
    decision = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall("get_aircraft_current_state", {"aircraft_id": "AC-1"}),),
    )
    wrapped = bind_specialist_model_provider(FakeComplete(decision), MasterSpecialist.LIVE_OPS)
    with pytest.raises(ContractValidationError, match="unknown_tool"):
        wrapped.complete(type("Turn", (), {"user_text": "x"})())


def test_three_sprint4_specialists_coordinate_offline():
    host = Sprint5IntegrationHost()
    tables = fixtures.california_tables()
    live = host.execute_live_ops_tool(
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
    runtime, port, athena, _s3, _clock = compose_stack()
    historical = host.execute_historical_athena_tool(
        port,
        tool_name="summarize_historical_encounters",
        arguments={"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
        tool_call_id="historical-call-sprint5",
        window=_window(),
    )
    decision = host.execute_decision_live(
        Sprint4DecisionLiveCall(
            tables=_full_tables(),
            now_epoch=1_788_661_800,
            query_timestamp_utc=QUERY,
            aircraft_id=AIRCRAFT,
            recommendation_id="rec-1",
        )
    )
    slice_ = host.coordinate(
        context=_context(),
        planner=ScriptedPlanner(_proposal(_live_step())),
        live_ops=live,
        historical=historical,
        decision=decision,
    )
    assert slice_.planning is not None
    assert slice_.planning.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert slice_.live_ops is live
    assert slice_.historical is historical
    assert slice_.decision is decision
    assert slice_.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert slice_.claims_admitted is False
    assert athena.start_calls
    assert remaining_sprint5_blockers()
    labels = classify_sprint5_operations()
    assert labels["three_specialist_coordination"] == Sprint5VerificationStatus.OFFLINE_VERIFIED.value
    assert labels["post_ai_query_route"] == Sprint5VerificationStatus.BLOCKED.value
    assert labels["ledger_atomic_started_admission"] == Sprint5VerificationStatus.BLOCKED.value


def test_langgraph_live_ops_path_still_grounded():
    harness = Harness(_work(LIVE), (_live(),))
    step = harness.step("live-1")
    binding = prepare_master_live_ops_binding(harness.bundle(), harness.descriptor("live-1"))
    provider = FakeProvider([_tools(("get_observed_network_state", {})), _network_from_turn])
    handle = _handle(harness, step, binding, provider)
    result = run_sprint4_master_runtime(
        harness.planning(),
        ScriptedPlanner(_runtime_proposal()),
        orchestrator=harness.orchestrator,
        handles={step.invocation.invocation_id: handle},
        now_utc=BEFORE,
        retained_decision=harness.registry.decision_for(harness.record),
    )
    assert result.planning.status is Sprint4PlanningStatus.AUTHORIZED_PLAN
    assert result.synthesis is not None
    assert result.synthesis.claims_admitted is False
