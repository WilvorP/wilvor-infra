"""Import isolation for Phase 3A.2 historical specialist runtime."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
SPECIALIST = SHARED_DIR / "wilvor_ai" / "historical_specialist.py"
RUNTIME_CONTRACTS = SHARED_DIR / "wilvor_ai" / "specialist_runtime_contracts.py"
VERIFIER = SHARED_DIR / "wilvor_ai" / "historical_evidence_verifier.py"
RENDERER = SHARED_DIR / "wilvor_ai" / "historical_answer_renderer.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_operational",
    "wilvor_ai.live_ops",
    "wilvor_historical_query",
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


def test_import_wilvor_ai_still_does_not_load_specialist_runtime():
    script = """
import sys
import wilvor_ai
assert 'wilvor_ai.historical_specialist' not in sys.modules
assert 'wilvor_ai.specialist_runtime_contracts' not in sys.modules
assert 'wilvor_ai.historical_evidence_verifier' not in sys.modules
assert 'wilvor_ai.historical_answer_renderer' not in sys.modules
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert not hasattr(wilvor_ai, 'HistoricalAnalyticsSpecialist')
assert not hasattr(wilvor_ai, 'HISTORICAL_ANALYTICS_TOOLS')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_specialist_runtime_stays_offline_and_historical_only():
    script = """
import sys
import wilvor_ai.historical_specialist as historical_specialist
assert hasattr(historical_specialist, 'HistoricalAnalyticsSpecialist')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'LIVE_OPS_TOOLS' not in dir(historical_specialist)
assert not hasattr(historical_specialist, 'SpecialistResult')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_historical_query_package_still_does_not_import_wilvor_ai():
    script = """
import sys
import wilvor_historical_query
assert 'wilvor_ai' not in sys.modules
assert hasattr(wilvor_historical_query, 'HistoricalAnalyticsOperations')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_verifier_and_renderer_stay_offline():
    script = """
import sys
import wilvor_ai.historical_evidence_verifier as verifier
import wilvor_ai.historical_answer_renderer as renderer
assert hasattr(verifier, 'verify_historical_specialist_run')
assert hasattr(renderer, 'finalize_historical_specialist_run')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'wilvor_ai.live_ops' not in sys.modules
assert 'wilvor_historical_query' not in sys.modules
assert 'ModelProvider' not in dir(renderer)
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_specialist_sources_exclude_aws_live_ops_and_clocks():
    for path in (SPECIALIST, RUNTIME_CONTRACTS, VERIFIER, RENDERER):
        imported = _imported_modules(path)
        source = path.read_text(encoding="utf-8")
        assert imported.isdisjoint(_FORBIDDEN)
        assert "AthenaExecutor" not in source
        assert "CoverageStore" not in source
        assert "CoverageGate" not in source
        assert "datetime.now" not in source
        assert "datetime.utcnow" not in source
        assert "time.time" not in source
        assert "LIVE_OPS_TOOLS" not in source
        assert "MasterAgent" not in source
        assert "boto3.client" not in source
