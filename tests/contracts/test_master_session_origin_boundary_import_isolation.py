"""The session-origin boundary stays free of runtimes, grants, and siblings."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_session_origin_boundary.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "typing",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_routing",
    "wilvor_ai.model_contracts",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "terraform",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_local_capability",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_provenance_trace",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = {
    "authorize_route",
    "open_session",
    "associate_invocation",
    "retain_route",
    "close",
    "invalidate",
    "dispatch",
    "execute",
    "open_authenticated",
    "issue_grant",
    "redeem",
}

_CLOSED_DEFINITIONS = {
    "open_authenticated",
    "dispatch",
    "execute",
    "issue_grant",
    "redeem",
    "ApplicationPrincipal",
    "ProductionPrincipal",
    "ExecutionGrant",
    "TrustedFanout",
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


def _called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            called.add(func.id)
        elif isinstance(func, ast.Attribute):
            called.add(func.attr)
    return called


def test_session_origin_boundary_import_graph_is_closed():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert _CLOSED_DEFINITIONS.isdisjoint(defined)
    assert _FORBIDDEN_CALLS.isdisjoint(_called_names(MODULE))
    source = MODULE.read_text(encoding="utf-8")
    assert "def open_authenticated" not in source
    assert "def dispatch" not in source
    assert "boto3" not in source
    assert "langgraph" not in source


def test_importing_session_origin_boundary_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_session_origin_boundary as boundary
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert boundary.MASTER_SESSION_ORIGIN_BOUNDARY_SCHEMA_VERSION == (
    'wilvor.ai.master_session_origin_boundary.v1'
)
assert boundary.AUTHENTICATED_APPLICATION_PRINCIPAL_AVAILABLE is False
assert boundary.AUTHORIZED_MULTI_INVOCATION_EXECUTION_AVAILABLE is False
assert boundary.INVOCATION_SELECTION_LIMIT == 1
assert boundary.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(boundary.MasterRouteExecutionAuthority, 'PRODUCTION')
assert boundary.MasterProductionAuthority.DENIED.value == 'DENIED'
assert boundary.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert not hasattr(boundary, 'open_authenticated')
assert not hasattr(boundary, 'dispatch')
assert not hasattr(boundary.MasterPlanningSessionRegistry, 'open_authenticated')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
