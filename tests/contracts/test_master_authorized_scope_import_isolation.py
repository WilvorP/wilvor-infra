"""The authorized-scope resolver stays free of AWS, providers, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_authorized_scope.py"

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.decision_contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_routing",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "terraform",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.live_ops_specialist",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_execution_integration",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
    "wilvor_historical",
    "wilvor_historical_query",
}

_FORBIDDEN_CALLS = (
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "datetime.now",
    "os.environ",
    "getenv",
    "table.scan",
    "scan_all(",
    "authorize_route(",
    "assess_master_execution_policy(",
    "resolve_master_decision_target(",
    "resolve_region(",
    "SpecialistRequest(",
    "def dispatch",
    "def execute",
    "def grant",
    "def permit",
)

_CLOSED_DEFINITIONS = {
    "dispatch",
    "redeem",
    "execute",
    "grant",
    "authorize",
    "permit",
    "ApplicationPrincipal",
    "AuthenticatedPrincipal",
    "PolicyEngine",
    "ExecutionGrant",
    "DispatchPermit",
    "ProductionPermit",
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


def test_authorized_scope_import_graph_is_closed():
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
    assert "PROPOSED" in source
    assert "not a production principal" in source
    assert "assess_master_execution_policy" in source
    assert "resolve_master_decision_target" in source


def test_importing_authorized_scope_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["ENVIRONMENT"] = "test"
    env["WILVOR_TEST_MODE"] = "1"
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_authorized_scope as scope
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert scope.PRODUCTION_PRINCIPAL_AVAILABLE is False
assert scope.AUTHENTICATED_SOURCE_READ_AVAILABLE is False
assert scope.EXECUTION_GRANT_AVAILABLE is False
assert scope.SourceCoverage.UNKNOWN.value == 'UNKNOWN'
assert not hasattr(scope.SourceCoverage, 'VERIFIED')
assert not hasattr(scope, 'dispatch')
assert not hasattr(scope, 'execute')
assert not hasattr(scope, 'grant')
assert not hasattr(scope, 'ExecutionGrant')
assert not hasattr(scope, 'ProductionPermit')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
