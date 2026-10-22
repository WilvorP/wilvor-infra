"""Synthetic orchestration may use LangGraph. Existing routing must not."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_orchestration.py"
ROUTING = SHARED_DIR / "wilvor_ai" / "master_routing.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_historical",
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


def test_orchestration_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
        "__future__",
        "dataclasses",
        "enum",
        "importlib.metadata",
        "secrets",
        "typing",
        "langgraph.errors",
        "langgraph.graph",
        "langgraph.types",
        "wilvor_ai.contracts",
        "wilvor_ai.master_contracts",
        "wilvor_ai.master_routing",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    assert "wilvor_ai.decision_specialist_runtime_contracts" not in imported
    source = MODULE.read_text(encoding="utf-8")
    assert "DecisionSpecialistRequest" not in source
    assert "invoke_historical" not in source
    assert "invoke_decision" not in source
    assert "invoke_live_ops" not in source
    assert "boto3" not in source
    routing_source = ROUTING.read_text(encoding="utf-8")
    assert "langgraph" not in routing_source


def test_importing_orchestration_does_not_load_specialist_or_provider_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_orchestration as orchestration
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert orchestration.SYNTHETIC_RECURSION_LIMIT == 14
assert 'langgraph' in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_importing_master_routing_does_not_load_langgraph():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = """
import sys
import wilvor_ai.master_routing as routing
assert 'langgraph' not in sys.modules
assert 'wilvor_ai.master_orchestration' not in sys.modules
assert routing.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
