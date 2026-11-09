"""Sprint 4 Agent B: four historical tools stay host-windowed and non-current."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.contracts.sprint4_historical_harness import (
    AS_OF,
    ELIGIBLE,
    PLAN,
    WORKGROUP,
    compose_stack,
    encounter_success_pages,
    encounter_zero_pages,
    hazard_success_pages,
    host_window,
    list_success_pages,
    risk_success_pages,
    seed_gap,
    FakeHistoricalS3,
    ScriptedHistoricalAthena,
)
from tests.fakes.scripted_model_provider import ScriptedModelProvider
from wilvor_ai.contracts import (
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics_mapping import HISTORICAL_FRESHNESS_NOT_ESTABLISHED
from wilvor_ai.historical_specialist import (
    AI_LIST_DEFAULT,
    AI_LIST_MAX,
    HistoricalAnalyticsSpecialist,
    SequentialToolCallIdFactory,
)
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalSpecialistTrustedContext,
    SpecialistRequest,
)
from wilvor_ai.specialist_runtime_contracts import SpecialistRunStatus
from wilvor_ai.sprint4_historical_tool_port import (
    HistoricalToolRefusal,
    HostAuthorizedHistoricalWindow,
    inspect_historical_tool_provenance,
)
from wilvor_historical.query_contracts import HAZARD_VERSION_WINDOW_LIMITATION


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "sprint4_historical_tool_port.py"
CALL_ID = "historical-call-sprint4-1"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _invoke(port, tool_name, arguments, *, window=None, tool_call_id=CALL_ID):
    return port.invoke(
        tool_name=tool_name,
        arguments=arguments,
        tool_call_id=tool_call_id,
        window=window or host_window(),
    )


def test_host_window_rejects_seven_day_escalation_and_equal_plan():
    with pytest.raises(ContractValidationError, match="historical_window_exceeds_seven_days"):
        HostAuthorizedHistoricalWindow(
            eligible_start_utc="2026-09-11T00:00:00Z",
            eligible_end_utc="2026-09-19T00:00:00Z",
            unbound_plan_start_utc="2026-09-11T00:00:00Z",
            unbound_plan_end_utc="2026-09-19T00:00:00Z",
            as_of_utc=AS_OF,
        )
    with pytest.raises(ContractValidationError, match="unbound_plan_window_is_not_tool_argument"):
        HostAuthorizedHistoricalWindow(
            eligible_start_utc=PLAN[0],
            eligible_end_utc=PLAN[1],
            unbound_plan_start_utc=PLAN[0],
            unbound_plan_end_utc=PLAN[1],
            as_of_utc=AS_OF,
        )
    with pytest.raises(ContractValidationError, match="historical_window_outside_host"):
        HostAuthorizedHistoricalWindow(
            eligible_start_utc="2026-09-10T10:00:00Z",
            eligible_end_utc="2026-09-10T11:00:00Z",
            unbound_plan_start_utc=PLAN[0],
            unbound_plan_end_utc=PLAN[1],
            as_of_utc=AS_OF,
        )


def test_summarize_encounters_uses_registry_sql_and_historical_provenance():
    runtime, port, athena, _s3, _clock = compose_stack()
    outcome = _invoke(
        port,
        "summarize_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "aircraft_id": "abc123",
        },
    )
    assert outcome.refusal is None
    result = outcome.tool_result
    assert result is not None
    assert result.status is ToolResultStatus.SUCCESS
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert result.as_of_utc == AS_OF
    assert result.data["status"] == "SUCCEEDED"
    assert result.data["result"]["physical_record_count"] == 2
    provenance = outcome.provenance
    assert provenance is not None
    assert provenance.temporal_scope is TemporalScope.HISTORICAL
    assert provenance.freshness_status is FreshnessStatus.UNKNOWN
    assert HISTORICAL_FRESHNESS_NOT_ESTABLISHED in provenance.freshness_limitations
    assert provenance.completeness_status == "EVALUABLE"
    assert provenance.query_ids == ("summarize_historical_encounters",)
    assert provenance.engine_scopes == (WORKGROUP,)
    assert provenance.represented_as_current_observation is False
    assert provenance.verified_zero_from_empty_uncertified is False
    assert outcome.live_athena_initiated is False
    assert outcome.production_query is False
    sql = athena.start_calls[0]["QueryString"]
    assert "year = '2026'" in sql
    assert "aircraft_id = 'abc123'" in sql


def test_all_four_tools_execute_closed_queries():
    runtime, port, athena, _s3, _clock = compose_stack(
        pages=(
            encounter_success_pages()
            + risk_success_pages()
            + hazard_success_pages()
            + list_success_pages()
        )
    )
    window = host_window()
    encounters = _invoke(
        port,
        "summarize_historical_encounters",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
        window=window,
        tool_call_id="call-encounters",
    )
    risks = _invoke(
        port,
        "summarize_historical_risks",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1], "aircraft_id": "abc123"},
        window=window,
        tool_call_id="call-risks",
    )
    hazards = _invoke(
        port,
        "summarize_historical_hazard_versions",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1], "hazard_id": "hazard-1"},
        window=window,
        tool_call_id="call-hazards",
    )
    listed = _invoke(
        port,
        "list_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "aircraft_id": "abc123",
            "limit": 5,
        },
        window=window,
        tool_call_id="call-list",
    )
    assert encounters.tool_result.status is ToolResultStatus.SUCCESS
    assert risks.tool_result.status is ToolResultStatus.SUCCESS
    assert hazards.tool_result.status is ToolResultStatus.SUCCESS
    assert listed.tool_result.status is ToolResultStatus.SUCCESS
    assert HAZARD_VERSION_WINDOW_LIMITATION in hazards.tool_result.limitations
    assert listed.provenance.source_versions == (None,)
    assert "hazard-1#v1" not in (listed.provenance.source_versions)
    query_ids = [
        started["QueryString"][:40]
        for started in athena.start_calls
    ]
    assert len(athena.start_calls) == 5
    internal_ids = [risks.provenance.query_ids, hazards.provenance.query_ids]
    assert "summarize_historical_risks" in risks.provenance.query_ids
    assert "summarize_historical_risks_by_level" in risks.provenance.query_ids
    assert hazards.provenance.query_ids == ("summarize_historical_hazard_versions",)
    assert listed.provenance.query_ids == ("list_historical_encounters",)
    assert query_ids


def test_truncated_list_is_partial_not_exact_and_not_current():
    runtime, port, athena, _s3, _clock = compose_stack(pages=list_success_pages(rows=3))
    outcome = _invoke(
        port,
        "list_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "aircraft_id": "abc123",
            "limit": 2,
        },
    )
    result = outcome.tool_result
    assert result.status is ToolResultStatus.PARTIAL
    assert result.data["status"] == "RESULT_TRUNCATED"
    assert result.data["result"]["truncated"] is True
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert outcome.provenance.completeness_status == "EVALUABLE"
    assert len(athena.start_calls) == 1
    sql = athena.start_calls[0]["QueryString"]
    assert "LIMIT 3" in sql


def test_coverage_gap_is_unavailable_and_does_not_start_athena():
    s3 = FakeHistoricalS3()
    seed_gap(s3)
    runtime, port, athena, _s3, _clock = compose_stack(
        athena=ScriptedHistoricalAthena(encounter_success_pages()),
        s3=s3,
        seed=False,
    )
    outcome = _invoke(
        port,
        "summarize_historical_encounters",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
    )
    result = outcome.tool_result
    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.data["status"] == "COVERAGE_BLOCKED"
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert athena.start_calls == []
    assert outcome.provenance.verified_zero_from_empty_uncertified is False
    assert outcome.provenance.represented_as_current_observation is False


def test_empty_uncertified_store_is_not_verified_zero():
    runtime, port, athena, _s3, _clock = compose_stack(seed=False)
    outcome = _invoke(
        port,
        "summarize_historical_encounters",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
    )
    result = outcome.tool_result
    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.data["status"] == "COVERAGE_STORE_UNAVAILABLE"
    assert result.status is not ToolResultStatus.NOT_FOUND
    assert athena.start_calls == []


def test_certified_zero_requires_evaluable_coverage():
    runtime, port, athena, _s3, _clock = compose_stack(pages=encounter_zero_pages())
    outcome = _invoke(
        port,
        "summarize_historical_encounters",
        {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]},
    )
    result = outcome.tool_result
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.data["status"] == "VERIFIED_ZERO"
    assert outcome.provenance.completeness_status == "EVALUABLE"
    assert outcome.provenance.verified_zero_from_empty_uncertified is False
    assert athena.start_calls


def test_plan_window_and_oversize_window_are_not_clipped():
    runtime, port, athena, _s3, _clock = compose_stack()
    plan_outcome = _invoke(
        port,
        "summarize_historical_encounters",
        {"start_utc": PLAN[0], "end_utc": PLAN[1]},
    )
    assert plan_outcome.refusal is HistoricalToolRefusal.HISTORICAL_WINDOW_NOT_AUTHORIZED
    assert plan_outcome.tool_result is None
    oversize = _invoke(
        port,
        "summarize_historical_encounters",
        {
            "start_utc": "2026-09-11T00:00:00Z",
            "end_utc": "2026-09-19T00:00:00Z",
        },
    )
    assert oversize.refusal is HistoricalToolRefusal.HISTORICAL_WINDOW_NOT_AUTHORIZED
    assert athena.start_calls == []


def test_sql_workgroup_and_trusted_arguments_are_rejected():
    runtime, port, athena, _s3, _clock = compose_stack()
    sql = _invoke(
        port,
        "summarize_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "sql": "SELECT * FROM encounter",
        },
    )
    assert sql.refusal is HistoricalToolRefusal.ARBITRARY_SQL
    workgroup = _invoke(
        port,
        "summarize_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "workgroup": "attacker-workgroup",
        },
    )
    assert workgroup.refusal is HistoricalToolRefusal.ARBITRARY_SQL
    as_of = _invoke(
        port,
        "summarize_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "as_of_utc": "2026-09-20T00:00:00Z",
        },
    )
    assert as_of.refusal is HistoricalToolRefusal.TRUSTED_ARGUMENT_FORBIDDEN
    unknown = _invoke(port, "scan_historical_encounters", {"start_utc": ELIGIBLE[0], "end_utc": ELIGIBLE[1]})
    assert unknown.refusal is HistoricalToolRefusal.UNKNOWN_HISTORICAL_TOOL
    assert athena.start_calls == []


def test_list_limit_is_capped_not_widened_and_defaults_applied():
    runtime, port, athena, _s3, _clock = compose_stack(pages=list_success_pages())
    exceeded = _invoke(
        port,
        "list_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "aircraft_id": "abc123",
            "limit": AI_LIST_MAX + 1,
        },
    )
    assert exceeded.refusal is HistoricalToolRefusal.LIST_LIMIT_EXCEEDED
    defaulted = _invoke(
        port,
        "list_historical_encounters",
        {
            "start_utc": ELIGIBLE[0],
            "end_utc": ELIGIBLE[1],
            "aircraft_id": "abc123",
        },
        tool_call_id="historical-call-default-limit",
    )
    assert defaulted.refusal is None
    assert defaulted.executed_arguments["limit"] == AI_LIST_DEFAULT
    sql = athena.start_calls[0]["QueryString"]
    assert f"LIMIT {AI_LIST_DEFAULT + 1}" in sql


def test_specialist_can_consume_composed_operations():
    runtime, _port, athena, _s3, _clock = compose_stack()
    provider = ScriptedModelProvider(
        [
            ModelDecision(
                kind=ModelDecisionKind.TOOL_CALLS,
                tool_calls=(
                    ProposedToolCall(
                        name="summarize_historical_encounters",
                        arguments={
                            "start_utc": ELIGIBLE[0],
                            "end_utc": ELIGIBLE[1],
                        },
                    ),
                ),
            ),
            ModelDecision(
                kind=ModelDecisionKind.FINAL_CLAIMS,
                claims=(
                    ExactCountClaim(
                        tool_call_id="historical-call-1",
                        metric_id="physical_record_count",
                        value=2,
                    ),
                ),
            ),
        ]
    )
    specialist = HistoricalAnalyticsSpecialist(
        provider=provider,
        operations=runtime.operations,
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    run = specialist.run(
        SpecialistRequest(text="summarize historical encounters for last hour"),
        HistoricalSpecialistTrustedContext(as_of_utc=AS_OF),
    )
    assert run.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert len(run.tool_results) == 1
    result = run.tool_results[0]
    assert result.temporal_scope is TemporalScope.HISTORICAL
    assert result.status is ToolResultStatus.SUCCESS
    provenance = inspect_historical_tool_provenance(result)
    assert provenance.query_ids == ("summarize_historical_encounters",)
    assert provenance.represented_as_current_observation is False
    assert athena.start_calls
    assert run.evaluated_as_of_utc == AS_OF


def test_port_source_stays_offline_and_does_not_invent_sql():
    imported = _imported_modules(MODULE)
    source = MODULE.read_text(encoding="utf-8")
    assert imported.isdisjoint(
        {
            "boto3",
            "botocore",
            "openai",
            "anthropic",
            "langgraph",
            "wilvor_operational",
            "wilvor_ai.live_ops",
        }
    )
    assert "datetime.now" not in source
    assert "render_fixed_query" not in source
    assert "StartQueryExecution" not in source
    assert "sprint3d_native_specialists" not in source


def test_import_tool_port_does_not_load_aws_or_models():
    script = """
import sys
import wilvor_ai.sprint4_historical_tool_port as port
assert port.SPRINT4_HISTORICAL_TOOL_PORT_SCHEMA_VERSION.endswith('.v1')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
"""
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
