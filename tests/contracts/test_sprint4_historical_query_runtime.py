"""Sprint 4 Agent B: historical query runtime stays offline, bounded, and registry-only."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from tests.contracts.sprint4_historical_harness import (
    AS_OF,
    DATABASE,
    ELIGIBLE,
    FACTS_BUCKET,
    RESULTS_PREFIX,
    WORKGROUP,
    FakeClock,
    FakeHistoricalS3,
    ScriptedHistoricalAthena,
    compose_stack,
    deployment_config,
    encounter_success_pages,
    encounter_zero_pages,
    seed_evaluable,
)
from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.historical_analytics import HISTORICAL_ANALYTICS_TOOLS
from wilvor_ai.sprint4_historical_query_runtime import (
    CALLER_OUTPUT_LOCATION_PERMITTED,
    CALLER_SQL_PERMITTED,
    CALLER_WORKGROUP_PERMITTED,
    COVERAGE_BLOCKED_IS_ZERO,
    EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO,
    HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS,
    HISTORICAL_QUERY_IAM_ATTACHED,
    LIVE_ATHENA_QUERY_PERMITTED,
    PRODUCTION_HISTORICAL_ATHENA_ENABLED,
    SPRINT4_HISTORICAL_AWS_DEPENDENCIES,
    SPRINT4_HISTORICAL_INTEGRATOR_SEAMS,
    SPRINT4_HISTORICAL_QUERY_RUNTIME_SCHEMA_VERSION,
    UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT,
    Sprint4HistoricalDeploymentConfig,
    Sprint4OfflineAthenaAdapter,
    bind_offline_athena_client,
    bind_offline_s3_client,
    compose_sprint4_historical_query_runtime,
    issue_production_historical_query,
    open_production_historical_athena,
)
from wilvor_historical.query_windows import MAX_QUERY_WINDOW, MAX_QUERY_WINDOW_DAYS
from wilvor_historical_query.executor import (
    ATHENA_STATE_QUEUED,
    ATHENA_STATE_RUNNING,
    DEFAULT_TIMEOUT_SECONDS,
)
from wilvor_historical_query.operations import HistoricalAnalyticsOperations
from wilvor_historical_query.query_registry import public_query_ids
from wilvor_historical.query_contracts import (
    HistoricalQueryStatus,
    SummarizeHistoricalEncountersRequest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "sprint4_historical_query_runtime.py"

FORBIDDEN_SOURCE = (
    "import boto3",
    "from boto3",
    "boto3.client",
    "import botocore",
    "datetime.now",
    "os.environ",
    "getenv(",
    "wilvor_operational",
    "wilvor_ai.live_ops",
    "wilvor_ai.providers",
    "sprint3d_native_specialists",
    "sprint3d_integration_host",
    "sprint3d_source_evidence",
)


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_closed_flags_and_catalog_alignment():
    assert PRODUCTION_HISTORICAL_ATHENA_ENABLED is False
    assert LIVE_ATHENA_QUERY_PERMITTED is False
    assert CALLER_SQL_PERMITTED is False
    assert CALLER_WORKGROUP_PERMITTED is False
    assert CALLER_OUTPUT_LOCATION_PERMITTED is False
    assert HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS is False
    assert EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO is False
    assert COVERAGE_BLOCKED_IS_ZERO is False
    assert UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT is False
    assert HISTORICAL_QUERY_IAM_ATTACHED is False
    assert MAX_QUERY_WINDOW == timedelta(days=7)
    assert MAX_QUERY_WINDOW_DAYS == 7
    assert public_query_ids() == {item.name for item in HISTORICAL_ANALYTICS_TOOLS}
    assert SPRINT4_HISTORICAL_QUERY_RUNTIME_SCHEMA_VERSION.endswith(".v1")
    assert len(SPRINT4_HISTORICAL_INTEGRATOR_SEAMS) >= 5
    assert any("unattached" in item or "IAM" in item for item in SPRINT4_HISTORICAL_INTEGRATOR_SEAMS)
    assert any("athena:" in item for item in SPRINT4_HISTORICAL_AWS_DEPENDENCIES)


def test_production_entrypoints_stay_closed():
    with pytest.raises(ContractValidationError, match="production_historical_query_not_enabled"):
        open_production_historical_athena()
    with pytest.raises(ContractValidationError, match="production_historical_query_not_enabled"):
        issue_production_historical_query()


def test_invalid_deployment_config_is_rejected():
    with pytest.raises(ContractValidationError, match="invalid_historical_deployment_config"):
        Sprint4HistoricalDeploymentConfig(
            workgroup="bad workgroup",
            database=DATABASE,
            expected_results_prefix=RESULTS_PREFIX,
            coverage_bucket_name=FACTS_BUCKET,
        )
    with pytest.raises(ContractValidationError, match="invalid_historical_deployment_config"):
        Sprint4HistoricalDeploymentConfig(
            workgroup=WORKGROUP,
            database=DATABASE,
            expected_results_prefix="s3://bucket/not-athena-results/",
            coverage_bucket_name=FACTS_BUCKET,
        )
    with pytest.raises(ContractValidationError, match="invalid_historical_deployment_config"):
        Sprint4HistoricalDeploymentConfig(
            workgroup=WORKGROUP,
            database=DATABASE,
            expected_results_prefix=RESULTS_PREFIX,
            coverage_bucket_name="INVALID_BUCKET",
        )


def test_live_sdk_shaped_clients_cannot_be_sealed():
    class LiveAthena:
        __module__ = "botocore.client"

        def start_query_execution(self, **kwargs):
            raise AssertionError("live client invoked")

        def get_query_execution(self, **kwargs):
            raise AssertionError("live client invoked")

        def get_query_results(self, **kwargs):
            raise AssertionError("live client invoked")

        def stop_query_execution(self, **kwargs):
            raise AssertionError("live client invoked")

    class LiveS3:
        __module__ = "boto3.s3.inject"

        def list_objects_v2(self, **kwargs):
            raise AssertionError("live client invoked")

        def get_object(self, **kwargs):
            raise AssertionError("live client invoked")

    config = deployment_config()
    with pytest.raises(ContractValidationError, match="live_athena_client_not_permitted"):
        bind_offline_athena_client(LiveAthena(), config=config)
    with pytest.raises(ContractValidationError, match="live_s3_client_not_permitted"):
        bind_offline_s3_client(LiveS3(), config=config)
    with pytest.raises(ContractValidationError, match="offline_athena_client_required"):
        Sprint4OfflineAthenaAdapter(ScriptedHistoricalAthena(), config=config, _seal=object())


def test_compose_builds_operations_without_live_aws():
    runtime, _port, athena, s3, _clock = compose_stack()
    assert isinstance(runtime.operations, HistoricalAnalyticsOperations)
    assert runtime.live_athena_initiated is False
    assert runtime.live_s3_initiated is False
    assert runtime.production_query_enabled is False
    assert runtime.config.workgroup == WORKGROUP
    assert runtime.config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert athena.start_calls == []
    assert s3.list_calls == []


def test_executor_uses_deployment_workgroup_database_and_registry_sql():
    runtime, _port, athena, _s3, _clock = compose_stack(pages=encounter_success_pages())
    request = SummarizeHistoricalEncountersRequest(
        start_utc=ELIGIBLE[0],
        end_utc=ELIGIBLE[1],
        aircraft_id="abc123",
    )
    response = runtime.operations.summarize_historical_encounters(
        request,
        as_of_utc=AS_OF,
    )
    assert response.status is HistoricalQueryStatus.SUCCEEDED
    assert len(athena.start_calls) == 1
    started = athena.start_calls[0]
    assert started["WorkGroup"] == WORKGROUP
    assert started["QueryExecutionContext"]["Database"] == DATABASE
    assert "ResultConfiguration" not in started
    assert started["ResultReuseConfiguration"]["ResultReuseByAgeConfiguration"]["Enabled"] is False
    sql = started["QueryString"]
    assert "year = '2026'" in sql
    assert "month = '09'" in sql
    assert "day = '11'" in sql
    assert "SELECT *" not in sql
    assert "abc123" in sql
    assert "DROP TABLE" not in sql


def test_coverage_store_unavailable_does_not_start_athena():
    runtime, _port, athena, s3, _clock = compose_stack(seed=False, pages=encounter_success_pages())
    request = SummarizeHistoricalEncountersRequest(
        start_utc=ELIGIBLE[0],
        end_utc=ELIGIBLE[1],
    )
    response = runtime.operations.summarize_historical_encounters(
        request,
        as_of_utc=AS_OF,
    )
    assert response.status is HistoricalQueryStatus.COVERAGE_STORE_UNAVAILABLE
    assert athena.start_calls == []
    assert s3.list_calls
    assert response.result is None


def test_empty_certified_page_is_verified_zero_only_after_coverage():
    runtime, _port, athena, _s3, _clock = compose_stack(pages=encounter_zero_pages())
    request = SummarizeHistoricalEncountersRequest(
        start_utc=ELIGIBLE[0],
        end_utc=ELIGIBLE[1],
    )
    response = runtime.operations.summarize_historical_encounters(
        request,
        as_of_utc=AS_OF,
    )
    assert response.status is HistoricalQueryStatus.VERIFIED_ZERO
    assert response.coverage is not None
    assert response.coverage.evaluability.value == "EVALUABLE"
    assert response.evidence.semantic_match_count == 0
    assert athena.start_calls
    assert EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO is False


def test_timeout_stops_query_and_does_not_certify_a_zero():
    clock = FakeClock()
    athena = ScriptedHistoricalAthena(
        pages=encounter_success_pages(),
        states=[ATHENA_STATE_QUEUED, ATHENA_STATE_RUNNING],
    )
    s3 = FakeHistoricalS3()
    seed_evaluable(s3)
    runtime = compose_sprint4_historical_query_runtime(
        athena_client=athena,
        s3_client=s3,
        config=deployment_config(timeout_seconds=4, poll_interval_seconds=2),
        monotonic_clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    response = runtime.operations.summarize_historical_encounters(
        SummarizeHistoricalEncountersRequest(
            start_utc=ELIGIBLE[0],
            end_utc=ELIGIBLE[1],
        ),
        as_of_utc=AS_OF,
    )
    assert response.status is HistoricalQueryStatus.QUERY_TIMEOUT
    assert response.result is None
    assert athena.stop_calls
    assert athena.get_results_calls == []
    assert clock.sleeps == [2, 2]


def test_coverage_writes_are_refused_on_the_s3_adapter():
    runtime, _port, _athena, _s3, _clock = compose_stack()
    with pytest.raises(ContractValidationError, match="historical_coverage_writes_not_permitted"):
        runtime.s3.put_object(Bucket=FACTS_BUCKET, Key="metadata/epoch/x.json", Body=b"{}")


def test_runtime_source_has_no_live_aws_or_foreign_specialists():
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
            "wilvor_ai.providers",
        }
    )
    for marker in FORBIDDEN_SOURCE:
        assert marker not in source
    assert "execute_fixed" in source
    assert "render_fixed_query" not in source


def test_import_runtime_does_not_load_aws_or_models():
    script = """
import sys
import wilvor_ai.sprint4_historical_query_runtime as runtime
assert runtime.PRODUCTION_HISTORICAL_ATHENA_ENABLED is False
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
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
