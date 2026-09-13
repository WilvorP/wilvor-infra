"""Import isolation for Phase 3A-preflight specialist contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
SPECIALIST = SHARED_DIR / "wilvor_ai" / "specialist_contracts.py"
MODEL = SHARED_DIR / "wilvor_ai" / "model_contracts.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_operational",
    "wilvor_historical_query",
    "wilvor_ai.live_ops",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_analytics_mapping",
}


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


def test_import_wilvor_ai_loads_preflight_contracts_only():
    script = """
import sys
import wilvor_ai
assert hasattr(wilvor_ai, 'SpecialistRequest')
assert hasattr(wilvor_ai, 'HistoricalSpecialistTrustedContext')
assert hasattr(wilvor_ai, 'ModelDecision')
assert hasattr(wilvor_ai, 'ModelProvider')
assert hasattr(wilvor_ai, 'ToolInputValueType')
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert 'wilvor_ai.historical_analytics_mapping' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'wilvor_historical_query' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert not hasattr(wilvor_ai, 'HISTORICAL_ANALYTICS_TOOLS')
assert not hasattr(wilvor_ai, 'HistoricalAnalyticsAdapter')
assert not hasattr(wilvor_ai, 'HistoricalAnalyticsOperations')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_specialist_and_model_contracts_stay_offline():
    script = """
import sys
import wilvor_ai.specialist_contracts as specialist_contracts
import wilvor_ai.model_contracts as model_contracts
assert hasattr(specialist_contracts, 'SpecialistResult')
assert hasattr(model_contracts, 'ModelDecision')
assert 'wilvor_historical_query' not in sys.modules
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'HistoricalAnalyticsOperations' not in dir(specialist_contracts)
assert 'HistoricalAnalyticsOperations' not in dir(model_contracts)
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_preflight_sources_do_not_import_runtime_or_sdks():
    for path in (SPECIALIST, MODEL):
        imported = _imported_modules(path)
        source = path.read_text(encoding="utf-8")
        assert imported.isdisjoint(_FORBIDDEN)
        assert "import HistoricalAnalyticsOperations" not in source
        assert "wilvor_historical_query" not in imported
        assert "datetime.now" not in source
        assert "datetime.utcnow" not in source
        assert "time.time" not in source
        assert "boto3" not in imported
        assert "LIVE_OPS_TOOLS" not in source
        assert "HISTORICAL_ANALYTICS_TOOLS" not in source
