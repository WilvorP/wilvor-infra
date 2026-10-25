"""Import isolation for the Sprint 3A MA7 baseline safety review.

The regression imports the existing Master contracts. It must not import the
seven Sprint 3A modules that are still being written on other branches, and
it must not load a specialist runner, a model provider, or AWS.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
AI_DIR = SHARED_DIR / "wilvor_ai"
REGRESSION = REPO_ROOT / "tests" / "contracts" / "test_sprint3a_ma7_safety_regression.py"

UNFINISHED_MODULES = {
    "wilvor_ai.master_decision_execution_readiness",
    "wilvor_ai.master_execution_integration",
    "wilvor_ai.master_execution_policy_boundary",
    "wilvor_ai.master_historical_execution_readiness",
    "wilvor_ai.master_live_ops_execution_readiness",
    "wilvor_ai.master_session_origin_boundary",
    "wilvor_ai.master_source_read_trace_boundary",
}

UNFINISHED_FILES = {
    "master_decision_execution_readiness.py",
    "master_execution_integration.py",
    "master_execution_policy_boundary.py",
    "master_historical_execution_readiness.py",
    "master_live_ops_execution_readiness.py",
    "master_session_origin_boundary.py",
    "master_source_read_trace_boundary.py",
}

FORBIDDEN_MODULES = UNFINISHED_MODULES | {
    "anthropic",
    "boto3",
    "botocore",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_operational",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_baseline_regression_does_not_import_unfinished_sprint3a_modules():
    imported = _imported_modules(REGRESSION)
    assert UNFINISHED_MODULES.isdisjoint(imported)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    for name in UNFINISHED_FILES:
        assert not (AI_DIR / name).exists()
    source = REGRESSION.read_text(encoding="utf-8")
    for banned in ("boto3", "botocore", "anthropic", "openai", "terraform"):
        assert banned not in source
    assert "run_synthetic_master_workflow" not in source
    assert "def dispatch" not in source


def test_importing_baseline_regression_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(REPO_ROOT) + os.pathsep + str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import tests.contracts.test_sprint3a_ma7_safety_regression as regression
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert regression.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(regression.MasterRouteExecutionAuthority, 'GRANTED')
assert regression.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert regression.MasterProductionAuthority.DENIED.value == 'DENIED'
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
