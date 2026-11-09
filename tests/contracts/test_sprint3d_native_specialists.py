"""Sprint 3D Agent E: native Historical and Decision execution.

Offline only. The specialists are the real Historical and Decision runtimes.
Injected providers and Agent C's read port stand in for network clients.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.contracts.test_decision_tools_integration import (
    NOW as SOURCE_EPOCH,
)
from tests.contracts.test_decision_tools_integration import (
    _operational,
)
from tests.contracts.test_sprint3c_specialist_orchestration import (
    BEFORE,
    DEADLINE,
    HISTORY,
    POLICY,
    PRINCIPAL,
    RISK,
    Harness,
    _decision,
    _history,
    _work,
)
from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.decision_claims import AircraftIdentityClaim
from wilvor_ai.decision_contracts import RiskPresence, StoredRiskLevel
from wilvor_ai.decision_model_contracts import DecisionModelDecision
from wilvor_ai.decision_specialist import SequentialDecisionToolCallIdFactory
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionTargetMode,
)
from wilvor_ai.historical_specialist import SequentialToolCallIdFactory
from wilvor_ai.master_authorized_scheduler_adapter import ScriptedTestWorker
from wilvor_ai.master_authorized_scope import bind_local_test_host
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterSpecialist,
    observe_population,
    observe_typed_aircraft_id,
)
from wilvor_ai.master_decision_binding import prepare_master_decision_binding
from wilvor_ai.master_decision_execution_readiness import (
    assess_master_decision_execution_readiness,
)
from wilvor_ai.master_decision_target_resolver import (
    REQUIRED_HOST_PROOFS,
    FakeAircraftRead,
    FakeDecisionReadCatalog,
    FakeRiskRecord,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_historical_binding import prepare_master_historical_binding
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.specialist_contracts import ExactCountClaim, SpecialistResult, SpecialistStatus
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from wilvor_ai.sprint3c_scoped_read import (
    Sprint3CScopedReadPort,
    bind_offline_aws_read_client,
)
from wilvor_ai.sprint3c_specialist_orchestration import (
    Sprint3CNativeSpecialistOrchestrator,
)
from wilvor_ai.sprint3d_native_specialists import (
    NATIVE_EXECUTION_IS_SOURCE_EVIDENCE,
    NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH,
    SCRIPTED_WORKER_IS_NATIVE_EXECUTION,
    SPRINT3D_HISTORICAL_CONTRACT_MIGRATION,
    execute_decision_native,
    execute_historical_native,
)
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    CoverageEvidence,
    EncounterSummaryResult,
    HistoricalOperation,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
)
from tests.fakes.recording_historical_operations import RecordingHistoricalOperations


PLAN = ("2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
ELIGIBLE = ("2026-01-01T00:00:00Z", "2026-01-01T06:00:00Z")
OBSERVED = "2026-01-15T00:05:00Z"
EVALUATED = "2026-01-15T00:12:00Z"
SOURCE_EXPIRES = "2026-01-15T00:25:00Z"
QUERY_TIMESTAMP = "2026-09-30T20:00:00Z"
AIRCRAFT = "abc123"
COPIED_AIRCRAFT = "AC-2"
PERMITTED_RECOMMENDATION = "rec-host-1"
COPIED_RECOMMENDATION = "rec-copied"
READ_VERSION = "sv-read-1"
MODULE = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint3d_native_specialists.py"
)
ORCHESTRATOR = (
    Path(__file__).resolve().parents[2]
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint3c_specialist_orchestration.py"
)


class OfflineProvider:
    """Injected complete(). Not a scheduler worker and not a network client."""

    def __init__(self, steps) -> None:
        self.steps = list(steps)
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        if not self.steps:
            raise RuntimeError("script exhausted")
        step = self.steps.pop(0)
        if callable(step):
            return step(turn)
        return step


def _historical_response() -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=ELIGIBLE[0],
            end_utc=ELIGIBLE[1],
        ),
        coverage=CoverageEvidence(
            evaluability=Evaluability.EVALUABLE,
            reason="window_evaluable",
            required_horizon_seconds=21600,
            required_streams=("facts",),
            collection_epoch_ids=("epoch-1",),
        ),
        evidence=QueryEvidence(
            query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            requested_start_utc=ELIGIBLE[0],
            requested_end_utc=ELIGIBLE[1],
            coverage_state=Evaluability.EVALUABLE,
            collection_epoch_ids=("epoch-1",),
            semantic_match_count=2,
            semantic_match_count_is_exact=True,
            query_executions=(
                QueryExecutionEvidence(
                    query_id=INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
                    query_execution_id="exec-sprint3d-1",
                    rows_returned=2,
                    data_scanned_bytes=64,
                    workgroup="historical-analytics",
                ),
            ),
            evaluated_as_of_utc=EVALUATED,
        ),
        result=EncounterSummaryResult(
            physical_record_count=2,
            distinct_encounter_count=2,
            distinct_aircraft_count=1,
            distinct_hazard_count=1,
            distinct_dedup_count=2,
            min_event_time_utc=ELIGIBLE[0],
            max_event_time_utc="2026-01-01T03:00:00Z",
        ),
    )


def _historical_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": PRINCIPAL,
        "policy_revision": POLICY,
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORY,
        "population_entity_ids": frozenset({"AC1"}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "historical_interval_id": "hist-interval-sprint3d",
        "historical_start_utc": ELIGIBLE[0],
        "historical_end_utc": ELIGIBLE[1],
        "source_expires_utc": SOURCE_EXPIRES,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _decision_host(**overrides: object):
    payload: dict[str, object] = {
        "principal_ref": PRINCIPAL,
        "policy_revision": POLICY,
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "capability": RISK,
        "population_entity_ids": frozenset({AIRCRAFT}),
        "region_state_codes": frozenset({"CA"}),
        "observation_utc": OBSERVED,
        "evaluation_utc": EVALUATED,
        "validated_aircraft_ids": frozenset({AIRCRAFT}),
        "permitted_recommendation_ids": frozenset({PERMITTED_RECOMMENDATION}),
        "row_entity_id": AIRCRAFT,
        "row_present": True,
        "risk_presence": RiskPresence.PRESENT,
        "stored_risk_level": StoredRiskLevel.HIGH,
        "source_expires_utc": SOURCE_EXPIRES,
    }
    payload.update(overrides)
    return bind_local_test_host(**payload)  # type: ignore[arg-type]


def _historical_port() -> Sprint3CScopedReadPort:
    client = bind_offline_aws_read_client(
        query_pages={
            "historical.encounters": [
                {
                    "Items": [
                        {
                            "record_id": "encounter-row-1",
                            "source_version": READ_VERSION,
                            "source_observed_at_utc": "2026-01-01T02:00:00Z",
                        }
                    ],
                    "query_id": "source-query-sprint3d",
                }
            ]
        }
    )
    return Sprint3CScopedReadPort(client, now_utc=EVALUATED)


def _decision_port() -> Sprint3CScopedReadPort:
    client = bind_offline_aws_read_client(
        get_items={
            ("aircraft", AIRCRAFT): {
                "aircraft_id": AIRCRAFT,
                "source_version": READ_VERSION,
                "source_observed_at_utc": OBSERVED,
            }
        }
    )
    return Sprint3CScopedReadPort(client, now_utc=EVALUATED)


def _historical_claims(turn):
    return ModelDecision(
        kind=ModelDecisionKind.FINAL_CLAIMS,
        claims=(
            ExactCountClaim(
                tool_call_id=turn.tool_results[-1].tool_call_id,
                metric_id="distinct_encounter_count",
                value=2,
            ),
        ),
    )


def _historical_call(window: tuple[str, str]) -> ModelDecision:
    return ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(
            ProposedToolCall(
                name="summarize_historical_encounters",
                arguments={
                    "aircraft_id": "AC1",
                    "start_utc": window[0],
                    "end_utc": window[1],
                },
            ),
        ),
    )


def _catalog() -> FakeDecisionReadCatalog:
    return FakeDecisionReadCatalog(
        aircraft=(
            FakeAircraftRead(
                aircraft_id=AIRCRAFT,
                callsign=None,
                state_fresh=True,
                population_member=True,
                in_request_scope=True,
                risk=FakeRiskRecord(RiskPresence.PRESENT, StoredRiskLevel.HIGH),
            ),
        )
    )


def _decision_harness(**context: object) -> Harness:
    values: dict[str, object] = {"target_candidate_aircraft_id": AIRCRAFT}
    values.update(context)
    return Harness(_work(RISK), (_decision(),), **values)


def _readiness(harness: Harness, *, typed: bool = True):
    observations = (observe_typed_aircraft_id(AIRCRAFT),) if typed else ()
    return assess_master_decision_execution_readiness(
        harness.bundle(),
        harness.descriptor("dec-1"),
        typed_aircraft_observations=observations,
        population_observations=(observe_population((AIRCRAFT,)),) if typed else (),
    )


def _decision_provider() -> OfflineProvider:
    def claims(turn):
        return DecisionModelDecision(
            kind=ModelDecisionKind.FINAL_CLAIMS,
            claims=(
                AircraftIdentityClaim(
                    "de-sprint3d-1",
                    TemporalScope.CURRENT,
                    AIRCRAFT,
                ),
            ),
        )

    return OfflineProvider(
        [
            DecisionModelDecision(
                kind=ModelDecisionKind.TOOL_CALLS,
                tool_calls=(
                    ProposedToolCall(
                        "get_current_risk_evidence",
                        {"aircraft_id": AIRCRAFT},
                    ),
                ),
            ),
            claims,
        ]
    )


def test_closed_flags_and_migration_stay_explicit():
    assert NATIVE_EXECUTION_IS_SOURCE_EVIDENCE is False
    assert NATIVE_OFFLINE_EXECUTION_IS_PRODUCTION_DISPATCH is False
    assert SCRIPTED_WORKER_IS_NATIVE_EXECUTION is False
    text = " ".join(SPRINT3D_HISTORICAL_CONTRACT_MIGRATION)
    assert "SpecialistRequest" in text
    assert "HistoricalEncounterRecord" in text
    assert "QueryExecutionTrace" in text
    source = MODULE.read_text(encoding="utf-8")
    assert "bind_offline_aws_read_client(" not in source
    assert "import boto3" not in source
    assert "AthenaExecutor(" not in source
    orchestrator = ORCHESTRATOR.read_text(encoding="utf-8")
    assert "HistoricalAnalyticsSpecialist(" not in orchestrator
    assert "DecisionSpecialist(" not in orchestrator
    assert "finalize_historical_specialist_run(" not in orchestrator
    assert "construct_decision_native_request(" not in orchestrator


def test_historical_native_run_preserves_identity_lineage_and_query_id():
    harness = Harness(_work(HISTORY), (_history(),))
    step = harness.step("hist-1")
    binding = prepare_master_historical_binding(harness.bundle(), harness.descriptor("hist-1"))
    provider = OfflineProvider([_historical_call(ELIGIBLE), _historical_claims])
    execution = execute_historical_native(
        harness.orchestrator,
        step.invocation,
        binding,
        harness.authority(step),
        now_utc=BEFORE,
        host=_historical_host(),
        port=_historical_port(),
        provider=provider,
        operations=RecordingHistoricalOperations(
            {"summarize_historical_encounters": _historical_response()}
        ),
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    assert isinstance(execution.intermediate_run, HistoricalSpecialistRunResult)
    assert isinstance(execution.final_result, SpecialistResult)
    assert execution.final_result.status is SpecialistStatus.ANSWERED
    assert execution.final_result.tool_results == execution.intermediate_run.tool_results
    assert execution.intermediate_run is not execution.final_result
    assert execution.identity.invocation_id == step.invocation.invocation_id
    assert execution.identity.specialist_request_id == binding.specialist_request_id
    assert execution.identity.correlation_id == binding.correlation_id
    assert execution.specialist_request is binding.specialist_request
    assert "start_utc" not in execution.specialist_request.to_dict()
    assert execution.unbound_plan_start_utc == PLAN[0]
    assert execution.eligible_start_utc == ELIGIBLE[0]
    assert execution.plan_window_used_as_tool_arguments is False
    assert execution.native_query_ids == (INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,)
    assert execution.read_query_ids == ("source-query-sprint3d",)
    assert execution.read_source_versions == (READ_VERSION,)
    assert execution.native_source_versions == ()
    assert execution.source_version_authenticated is False
    assert execution.query_id_authenticated is False
    assert execution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert execution.production_dispatch is False
    assert execution.native_result_carries_host_correlation is False
    assert provider.requests
    assert type(provider) is not ScriptedTestWorker


def test_plan_window_is_not_a_historical_tool_argument():
    harness = Harness(_work(HISTORY), (_history(),))
    step = harness.step("hist-1")
    binding = prepare_master_historical_binding(harness.bundle(), harness.descriptor("hist-1"))
    provider = OfflineProvider([_historical_call(PLAN)])
    with pytest.raises(ContractValidationError, match="historical_window_not_authorized"):
        execute_historical_native(
            harness.orchestrator,
            step.invocation,
            binding,
            harness.authority(step),
            now_utc=BEFORE,
            host=_historical_host(),
            port=_historical_port(),
            provider=provider,
            operations=RecordingHistoricalOperations(
                {"summarize_historical_encounters": _historical_response()}
            ),
        )
    assert provider.requests


def _historical_parts():
    harness = Harness(_work(HISTORY), (_history(),))
    step = harness.step("hist-1")
    binding = prepare_master_historical_binding(harness.bundle(), harness.descriptor("hist-1"))
    return harness, step, binding


def test_historical_rejects_scripted_worker_deadline_and_stale_fence():
    harness, step, binding = _historical_parts()
    with pytest.raises(ContractValidationError, match="specialist_execution_not_granted"):
        execute_historical_native(
            harness.orchestrator,
            step.invocation,
            binding,
            harness.authority(step),
            now_utc=BEFORE,
            host=_historical_host(),
            port=_historical_port(),
            provider=ScriptedTestWorker(),
            operations=RecordingHistoricalOperations({}),
        )
    late_harness, late_step, late_binding = _historical_parts()
    with pytest.raises(ContractValidationError, match="deadline_expired"):
        execute_historical_native(
            late_harness.orchestrator,
            late_step.invocation,
            late_binding,
            late_harness.authority(late_step),
            now_utc=DEADLINE,
            host=_historical_host(),
            port=_historical_port(),
            provider=OfflineProvider([_historical_call(ELIGIBLE)]),
            operations=RecordingHistoricalOperations({}),
        )
    stale_harness, stale_step, stale_binding = _historical_parts()
    with pytest.raises(ContractValidationError, match="stale_fence"):
        execute_historical_native(
            stale_harness.orchestrator,
            stale_step.invocation,
            stale_binding,
            stale_harness.authority(stale_step, fence_epoch=99),
            now_utc=BEFORE,
            host=_historical_host(),
            port=_historical_port(),
            provider=OfflineProvider([_historical_call(ELIGIBLE)]),
            operations=RecordingHistoricalOperations({}),
        )


def test_historical_cancellation_and_unconsumed_grant_do_not_run():
    harness, step, binding = _historical_parts()
    provider = OfflineProvider([_historical_call(ELIGIBLE)])
    with pytest.raises(ContractValidationError, match="schedule_cancelled"):
        execute_historical_native(
            harness.orchestrator,
            step.invocation,
            binding,
            harness.authority(step),
            now_utc=BEFORE,
            host=_historical_host(),
            port=_historical_port(),
            provider=provider,
            operations=RecordingHistoricalOperations({}),
            cancelled=lambda: True,
        )
    assert provider.requests == []
    fresh, fresh_step, fresh_binding = _historical_parts()
    with pytest.raises(ContractValidationError, match="consumed_grant_required"):
        execute_historical_native(
            fresh.orchestrator,
            fresh_step.invocation,
            fresh_binding,
            fresh.authority(fresh_step, consume=False),
            now_utc=BEFORE,
            host=_historical_host(),
            port=_historical_port(),
            provider=OfflineProvider([_historical_call(ELIGIBLE)]),
            operations=RecordingHistoricalOperations({}),
        )


def test_decision_native_request_uses_user_aircraft_not_comparison_copy():
    harness = _decision_harness()
    step = harness.step("dec-1")
    binding = prepare_master_decision_binding(harness.bundle(), harness.descriptor("dec-1"))
    provider = _decision_provider()
    execution = execute_decision_native(
        harness.orchestrator,
        step.invocation,
        _readiness(harness),
        _catalog(),
        harness.authority(step),
        now_utc=BEFORE,
        user_text=harness.planning().canonical_user_text,
        host=_decision_host(),
        port=_decision_port(),
        provider=provider,
        tables=_operational(),
        source_epoch=SOURCE_EPOCH,
        query_timestamp_utc=QUERY_TIMESTAMP,
        binding=binding,
        tool_call_id_factory=SequentialDecisionToolCallIdFactory(),
    )
    assert isinstance(execution.request, DecisionSpecialistRequest)
    assert execution.request.mode is DecisionTargetMode.AIRCRAFT
    assert execution.request.aircraft_id == AIRCRAFT
    assert execution.request.recommendation_id is None
    assert execution.request.request_id == step.invocation.specialist_request_id
    assert execution.native_result.aircraft_id == AIRCRAFT
    assert execution.native_result.recommendation_id is None
    assert isinstance(execution.native_result, DecisionSpecialistRunResult)
    assert execution.native_result.executed_tool_call_count == 1
    assert execution.native_result.tool_results[0].tool_name == "get_current_risk_evidence"
    assert execution.comparison_id_used_as_target is False
    assert execution.comparison_aircraft_id == AIRCRAFT
    assert execution.permitted_recommendation_id == PERMITTED_RECOMMENDATION
    assert execution.host_proofs_satisfied == REQUIRED_HOST_PROOFS
    assert execution.resolution.decision_request is None
    assert execution.read_source_versions == (READ_VERSION,)
    assert execution.read_query_ids == ()
    assert execution.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert execution.production_dispatch is False
    assert provider.requests


def test_comparison_aircraft_alone_is_not_a_decision_target():
    harness = _decision_harness(target_candidate_aircraft_id=COPIED_AIRCRAFT)
    step = harness.step("dec-1")
    binding = prepare_master_decision_binding(harness.bundle(), harness.descriptor("dec-1"))
    provider = _decision_provider()
    with pytest.raises(ContractValidationError, match="comparison_candidate_not_a_target"):
        execute_decision_native(
            harness.orchestrator,
            step.invocation,
            _readiness(harness, typed=False),
            _catalog(),
            harness.authority(step),
            now_utc=BEFORE,
            user_text=harness.planning().canonical_user_text,
            host=_decision_host(),
            port=_decision_port(),
            provider=provider,
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            binding=binding,
        )
    assert provider.requests == []


def test_copied_recommendation_id_is_not_separate_authorization():
    harness = Harness(
        _work(RISK),
        (_decision(),),
        target_candidate_recommendation_id=COPIED_RECOMMENDATION,
    )
    step = harness.step("dec-1")
    binding = prepare_master_decision_binding(harness.bundle(), harness.descriptor("dec-1"))
    with pytest.raises(
        ContractValidationError,
        match="persisted_recommendation_not_separately_authorized",
    ):
        execute_decision_native(
            harness.orchestrator,
            step.invocation,
            _readiness(harness),
            _catalog(),
            harness.authority(step),
            now_utc=BEFORE,
            user_text=harness.planning().canonical_user_text,
            host=_decision_host(permitted_recommendation_ids=frozenset({COPIED_RECOMMENDATION})),
            port=_decision_port(),
            provider=_decision_provider(),
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            binding=binding,
        )


def test_decision_without_permitted_recommendation_does_not_build_a_request():
    harness = _decision_harness()
    step = harness.step("dec-1")
    with pytest.raises(
        ContractValidationError,
        match="persisted_recommendation_not_separately_authorized",
    ):
        execute_decision_native(
            harness.orchestrator,
            step.invocation,
            _readiness(harness),
            _catalog(),
            harness.authority(step),
            now_utc=BEFORE,
            user_text=harness.planning().canonical_user_text,
            host=_decision_host(permitted_recommendation_ids=frozenset()),
            port=_decision_port(),
            provider=_decision_provider(),
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            binding=prepare_master_decision_binding(harness.bundle(), harness.descriptor("dec-1")),
        )


def test_decision_rejects_a_second_read_client_and_a_cancelled_dispatch():
    harness = _decision_harness()
    step = harness.step("dec-1")
    client = bind_offline_aws_read_client()
    with pytest.raises(ContractValidationError, match="second_read_client_rejected"):
        execute_decision_native(
            harness.orchestrator,
            step.invocation,
            _readiness(harness),
            _catalog(),
            harness.authority(step),
            now_utc=BEFORE,
            user_text=harness.planning().canonical_user_text,
            host=_decision_host(),
            port=_decision_port(),
            provider=_decision_provider(),
            tables=client,
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
        )
    cancelled = _decision_harness()
    cancelled_step = cancelled.step("dec-1")
    provider = _decision_provider()
    with pytest.raises(ContractValidationError, match="schedule_cancelled"):
        execute_decision_native(
            cancelled.orchestrator,
            cancelled_step.invocation,
            _readiness(cancelled),
            _catalog(),
            cancelled.authority(cancelled_step),
            now_utc=BEFORE,
            user_text=cancelled.planning().canonical_user_text,
            host=_decision_host(),
            port=_decision_port(),
            provider=provider,
            tables=_operational(),
            source_epoch=SOURCE_EPOCH,
            query_timestamp_utc=QUERY_TIMESTAMP,
            cancelled=lambda: True,
        )
    assert provider.requests == []
    assert Sprint3CNativeSpecialistOrchestrator is not None
