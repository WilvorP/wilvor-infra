"""The planning session stays free of specialist runtimes and other Sprint 2 modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_planning_session.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_local_capability",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_provenance_trace",
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


def test_planning_session_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
        "__future__",
        "enum",
        "secrets",
        "threading",
        "typing",
        "wilvor_ai.contracts",
        "wilvor_ai.master_contracts",
        "wilvor_ai.master_evidence_admission",
        "wilvor_ai.master_routing",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "def open_authenticated" not in source
    assert "def dispatch" not in source
    assert "def execute" not in source
    assert "def issue_grant" not in source
    assert "invoke_historical" not in source
    assert "invoke_decision" not in source
    assert "invoke_live_ops" not in source
    assert "langgraph" not in source
    assert "boto3" not in source
    assert "wilvor_ai.master_execution_scope" not in source


def test_importing_planning_session_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_planning_session as session
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert session.MASTER_PLANNING_SESSION_SCHEMA_VERSION == 'wilvor.ai.master_planning_session.v1'
assert session.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(session.MasterRouteExecutionAuthority, 'PRODUCTION')
assert session.MasterProductionAuthority.DENIED.value == 'DENIED'
assert session.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert not hasattr(session, 'open_authenticated')
assert not hasattr(session.MasterPlanningSessionRegistry, 'dispatch')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
