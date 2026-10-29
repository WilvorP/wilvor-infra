"""The ingress policy seam stays free of JWT decoding, AWS, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_host_ingress_policy_integration.py"
OWNED = (
    MODULE,
    SHARED_DIR / "wilvor_ai" / "master_gateway_runtime_boundary.py",
    SHARED_DIR / "wilvor_ai" / "master_application_principal.py",
    SHARED_DIR / "wilvor_ai" / "master_runtime_policy.py",
)

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.master_application_principal",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_gateway_identity",
    "wilvor_ai.master_gateway_runtime_boundary",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_routing",
    "wilvor_ai.master_runtime_policy",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "base64",
    "boto3",
    "botocore",
    "jose",
    "json",
    "jwt",
    "langgraph",
    "openai",
    "os",
    "pickle",
    "requests",
    "terraform",
    "urllib",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_execution_grants",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_specialist_runtime_contract",
    "wilvor_ai.master_trusted_read_executor",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = {
    "_evaluate_hypothetical_runtime_policy",
    "authorize_route",
    "b64decode",
    "decode",
    "dispatch",
    "execute",
    "getenv",
    "get_unverified",
    "issue_application_principal",
    "issue_grant",
    "issue_production_application_principal",
    "issue_production_execution_grant",
    "loads",
    "open_authenticated",
    "open_production_executor",
    "redeem",
    "urlopen",
    "verify_jwt",
}

_CLOSED_DEFINITIONS = {
    "ProductionPrincipal",
    "ExecutionGrant",
    "decode_jwt",
    "dispatch",
    "execute",
    "from_dict",
    "from_environ",
    "from_event",
    "issue_grant",
    "open_authenticated",
    "verify_jwt",
}

_BANNED_SOURCE = (
    "boto3",
    "botocore",
    "b64decode",
    "urlopen",
    "PyJWT",
    "jwt.decode",
    "os.environ",
    "getenv",
    "get_unverified",
    "pickle",
    "_evaluate_hypothetical_runtime_policy",
    "WILVOR_TEST_MODE",
    "WILVOR_PRODUCTION_PRINCIPAL",
)

_REQUIRED_BIND_CALLS = {
    "admit",
    "assess_gateway_runtime_boundary",
    "assess_master_gateway_identity",
    "evaluate_master_runtime_policy",
    "lifecycle",
    "observe_untrusted_gateway_claims",
    "present",
    "retain_runtime_policy_session",
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


def _called_names(tree: ast.AST) -> set[str]:
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


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(name)


def test_ingress_policy_import_graph_is_closed():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert _CLOSED_DEFINITIONS.isdisjoint(defined)
    assert _FORBIDDEN_CALLS.isdisjoint(_called_names(tree))
    for banned in _BANNED_SOURCE:
        assert banned not in source
    issuer = _function(tree, "issue_production_ingress_policy")
    executable = [node for node in issuer.body if not isinstance(node, ast.Expr)]
    assert [type(node) for node in executable] == [ast.Raise]
    assert not any(isinstance(node, (ast.If, ast.Return)) for node in ast.walk(issuer))
    bind = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "bind":
            bind = node
            break
    assert bind is not None
    bind_calls = _called_names(bind)
    assert _REQUIRED_BIND_CALLS <= bind_calls
    assert "_evaluate_hypothetical_runtime_policy" not in bind_calls
    assert "issue_production_ingress_policy" not in bind_calls
    for path in OWNED[1:]:
        assert "master_host_ingress_policy_integration" not in _imported_modules(path)


def test_importing_ingress_policy_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["WILVOR_TEST_MODE"] = "1"
    env["WILVOR_PRODUCTION_PRINCIPAL"] = "true"
    env["WILVOR_ALLOW_SYNTHETIC_PRINCIPAL"] = "1"
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_ACCESS_KEY_ID", "testing")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    env.setdefault("AWS_SESSION_TOKEN", "testing")
    env.setdefault("AWS_DEFAULT_REGION", "us-west-1")
    # base64 and json are loaded by existing contract modules this seam imports.
    # The AST check above still forbids this file from importing them itself.
    transitive = {"base64", "json", "os"}
    absent = sorted(name for name in FORBIDDEN_MODULES if name not in transitive)
    script = f"""
import sys
import wilvor_ai.master_host_ingress_policy_integration as integration
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert integration.PRODUCTION_INGRESS_INTEGRATION_AVAILABLE is False
assert integration.NONPRODUCTION_BINDING_REDEEMABLE is False
assert integration.IngressBindingDisposition.NONPRODUCTION.value == 'NONPRODUCTION'
assert not hasattr(integration.IngressBindingDisposition, 'PRODUCTION')
assert not hasattr(integration, 'from_event')
assert not hasattr(integration, 'decode_jwt')
assert not hasattr(integration, 'os')
from wilvor_ai.master_runtime_policy import HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE
from wilvor_ai.master_application_principal import PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE
from wilvor_ai.master_gateway_runtime_boundary import RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE
assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
assert PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False
assert RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
