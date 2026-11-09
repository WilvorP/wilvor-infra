"""Import isolation for Phase 2C.2 historical analytics adapters."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
ADAPTER = SHARED_DIR / "wilvor_ai" / "historical_analytics.py"
MAPPING = SHARED_DIR / "wilvor_ai" / "historical_analytics_mapping.py"


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
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


def test_import_wilvor_ai_stays_contracts_only():
    script = """
import sys
import wilvor_ai
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert 'wilvor_ai.historical_analytics_mapping' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'wilvor_historical_query' not in sys.modules
assert 'wilvor_historical_query.operations' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert hasattr(wilvor_ai, 'ToolResult')
assert hasattr(wilvor_ai, 'SpecialistRequest')
assert hasattr(wilvor_ai, 'ModelDecision')
assert 'wilvor_ai.tool_schema' not in sys.modules
assert 'wilvor_ai.tool_result_projection' not in sys.modules
assert not hasattr(wilvor_ai, 'HISTORICAL_ANALYTICS_TOOLS')
assert not hasattr(wilvor_ai, 'HistoricalAnalyticsAdapter')
assert not hasattr(wilvor_ai, 'map_historical_query_response')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_historical_analytics_does_not_load_aws_live_ops_or_models():
    script = """
import sys
import wilvor_ai.historical_analytics as historical_analytics
assert hasattr(historical_analytics, 'HISTORICAL_ANALYTICS_TOOLS')
assert hasattr(historical_analytics, 'HistoricalAnalyticsAdapter')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'LIVE_OPS_TOOLS' not in dir(historical_analytics)
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_historical_query_package_still_does_not_import_wilvor_ai():
    script = """
import sys
import wilvor_historical_query
assert 'wilvor_ai' not in sys.modules
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert hasattr(wilvor_historical_query, 'HistoricalAnalyticsOperations')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_adapter_source_imports_only_the_allowed_runtime_surface():
    imported = _imported_modules(ADAPTER)
    source = ADAPTER.read_text(encoding="utf-8")
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
    assert "wilvor_historical.query_contracts" in imported
    assert "wilvor_ai.historical_analytics_mapping" in imported
    assert "wilvor_historical_query.operations" in imported
    assert "wilvor_historical_query.executor" not in imported
    assert "wilvor_historical_query.coverage_gate" not in imported
    assert "wilvor_historical_query.coverage_store" not in imported
    assert "wilvor_historical_query.query_registry" not in imported
    assert "wilvor_historical_query.query_sql" not in imported
    assert "AthenaExecutor" not in source
    assert "CoverageStore" not in source
    assert "CoverageGate" not in source
    assert "InternalQueryId" not in source
    assert "render_fixed_query" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "boto3.client" not in source
    assert "LIVE_OPS_TOOLS" not in source


def test_mapping_source_still_excludes_operations_and_aws():
    imported = _imported_modules(MAPPING)
    source = MAPPING.read_text(encoding="utf-8")
    assert imported.isdisjoint(
        {
            "boto3",
            "botocore",
            "wilvor_operational",
            "wilvor_historical_query",
            "wilvor_ai.live_ops",
        }
    )
    assert "HistoricalAnalyticsOperations" not in source
    assert "datetime.now" not in source
    assert "time.time" not in source
