"""The execution-scope contract stays free of runtimes, providers, and siblings."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_execution_scope.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_routing",
    "wilvor_ai.master_specialist_adapters",
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
    "wilvor_ai.master_capability_registry",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_provenance",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = (
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "AthenaExecutor",
    "invoke_historical",
    "invoke_decision",
    "invoke_live_ops",
    "LiveOpsSpecialist(",
    "LiveOpsAdapter(",
    "LiveOpsTrustedContext(",
    "authorize_route(",
    "describe_planned_adapters(",
)

_CLOSED_DEFINITIONS = {
    "dispatch",
    "redeem",
    "execute",
    "grant",
    "authorize",
    "MasterPlanningSession",
    "ExecutionGrant",
    "RuntimeContext",
    "ApplicationPrincipal",
    "ExecutionPolicy",
    "ReadResourceScope",
    "ExecutionDeadline",
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


def test_execution_scope_import_graph_is_closed():
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
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source
    assert "MasterParallelContractBundle" not in {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }


def test_importing_execution_scope_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_execution_scope as scope
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert scope.MASTER_EXECUTION_SCOPE_SCHEMA_VERSION == 'wilvor.ai.master_execution_scope.v1'
assert scope.MasterExecutionScope.__dataclass_fields__['execution_permission_granted'].default is False
assert scope.MasterExecutionScope.__dataclass_fields__['redeemable'].default is False
assert scope.MasterExecutionScope.__dataclass_fields__['executable'].default is False
assert scope.MasterExecutionScope.__dataclass_fields__['dispatch_permitted'].default is False
assert scope.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(scope, 'dispatch')
assert not hasattr(scope, 'redeem')
assert not hasattr(scope.MasterExecutionScope, 'dispatch')
assert not hasattr(scope.MasterExecutionScope, 'redeem')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
