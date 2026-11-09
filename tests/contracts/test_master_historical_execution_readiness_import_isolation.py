"""Import isolation for the non-executing Historical execution-readiness assessment."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_historical_execution_readiness.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_answer_renderer",
    "wilvor_ai.historical_evidence_verifier",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.providers",
    "wilvor_historical_query",
    "wilvor_operational",
}

# master_parallel_contracts already imports corroboration, which imports the
# finalizer. Readiness must not call it. The direct import graph stays closed.
TRANSITIVE_MODULES_NOT_EXECUTED = {
    "wilvor_ai.historical_answer_renderer",
    "wilvor_ai.historical_evidence_verifier",
}

DIRECT_IMPORTS = {
    "__future__",
    "dataclasses",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_historical_binding",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_routing",
    "wilvor_ai.master_specialist_adapters",
    "wilvor_ai.specialist_contracts",
    "wilvor_ai.specialist_runtime_contracts",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_historical_execution_readiness_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == DIRECT_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "finalize_historical_specialist_run(" not in source
    assert "HistoricalAnalyticsSpecialist(" not in source
    assert "HistoricalAnalyticsOperations" not in source
    assert "HistoricalAnalyticsAdapter" not in source
    assert "HistoricalSpecialistTrustedContext(" not in source
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "boto3" not in source
    assert "botocore" not in source
    assert "AthenaExecutor" not in source
    assert "CoverageStore" not in source


def test_importing_historical_execution_readiness_does_not_load_runtimes_or_providers():
    absent = sorted(FORBIDDEN_MODULES - TRANSITIVE_MODULES_NOT_EXECUTED)
    script = f"""
import sys
import wilvor_ai.master_historical_execution_readiness as readiness
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
fields = readiness.MasterHistoricalExecutionReadiness.__dataclass_fields__
assert fields['dispatch_permitted'].default is False
assert fields['authenticated_source_evaluation_time_utc'].default is None
assert fields['historical_run'].default is None
assert fields['specialist_result'].default is None
assert fields['trusted_context'].default is None
assert fields['inferred_count'].default is None
assert fields['source_version'].default is None
assert fields['query_id'].default is None
assert fields['query_result'].default is None
assert fields['finalization_performed'].default is False
assert fields['finalization_complete'].default is False
assert fields['finalization_entrypoint'].default == 'finalize_historical_specialist_run'
assert fields['historical_window_bound_to_native_tool_arguments'].default is False
assert not hasattr(readiness, 'finalize_historical_specialist_run')
assert not hasattr(readiness, 'HistoricalAnalyticsSpecialist')
assert not hasattr(readiness, 'HistoricalAnalyticsAdapter')
assert not hasattr(readiness, 'HistoricalAnalyticsOperations')
assert 'wilvor_ai.historical_specialist' not in sys.modules
assert 'wilvor_ai.historical_analytics' not in sys.modules
assert not callable(getattr(readiness, 'finalize_historical_specialist_run', None))
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'openai' not in sys.modules
assert 'langgraph' not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
