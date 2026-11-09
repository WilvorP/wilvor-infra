"""The runtime policy evaluator stays free of AWS, providers, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_runtime_policy.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "secrets",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
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
    "table.scan",
    "scan_all(",
    "authorize_route(",
    "assess_master_execution_policy(",
    "describe_planned_adapters(",
    "invoke_historical",
    "invoke_decision",
    "invoke_live_ops",
    "def dispatch",
    "def execute",
    "def grant",
    "def permit",
    "eval(",
    "exec(",
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


def _calls_in(function_name: str) -> set[str]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == function_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    names.add(child.func.id)
    return names


def test_runtime_policy_import_graph_is_closed():
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
    assert "Agent 4" in source
    assert "not a permit" in source.casefold()
    assert "_evaluate_hypothetical_runtime_policy" not in _calls_in("evaluate_master_runtime_policy")
    assert "os.environ" not in source


def test_importing_runtime_policy_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env.pop("WILVOR_POLICY_TEST_MODE", None)
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_runtime_policy as policy
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert policy.MASTER_RUNTIME_POLICY_SCHEMA_VERSION == 'wilvor.ai.master_runtime_policy.v1'
assert policy.POLICY_REVISION == policy.MASTER_RUNTIME_POLICY_SCHEMA_VERSION
assert policy.HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
assert policy.HOST_GRANT_ISSUER_AVAILABLE is False
assert policy.MasterRuntimePolicyDecision.ALLOW.value == 'ALLOW'
assert policy.MasterRuntimePolicyDecision.DENY.value == 'DENY'
assert list(policy.MasterRuntimePolicyDecision) == [
    policy.MasterRuntimePolicyDecision.ALLOW,
    policy.MasterRuntimePolicyDecision.DENY,
]
assert '_evaluate_hypothetical_runtime_policy' not in policy.__all__
assert not hasattr(policy, 'dispatch')
assert not hasattr(policy, 'execute')
assert not hasattr(policy, 'grant')
assert not hasattr(policy, 'DispatchPermit')
assert not hasattr(policy, 'ProductionPermit')
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_resources, server_read_tools
for specialist in MasterSpecialist:
    for tool_name in server_read_tools(specialist):
        for _resource, operation in server_read_resources(specialist, tool_name):
            assert operation is not ReadOperation.SCAN
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
