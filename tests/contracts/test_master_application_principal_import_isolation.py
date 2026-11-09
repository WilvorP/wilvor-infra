"""The application principal boundary stays free of JWT decoding, AWS, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_application_principal.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "re",
    "threading",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_gateway_identity",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_routing",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "jose",
    "jwt",
    "langgraph",
    "openai",
    "os",
    "requests",
    "terraform",
    "urllib",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_execution_policy_boundary",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = {
    "authorize_route",
    "open_session",
    "associate_invocation",
    "retain_route",
    "dispatch",
    "execute",
    "open_authenticated",
    "issue_grant",
    "issue_application_principal",
    "promote_to_execution_permission",
    "redeem",
    "b64decode",
    "urlopen",
    "loads",
    "getenv",
}

_CLOSED_DEFINITIONS = {
    "open_authenticated",
    "dispatch",
    "execute",
    "issue_grant",
    "redeem",
    "decode_jwt",
    "verify_jwt",
    "ProductionPrincipal",
    "ExecutionGrant",
    "from_event",
    "from_dict",
    "from_environ",
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
    "langgraph",
    "decode_jwt",
    "def open_authenticated",
    "def dispatch",
    "def execute",
)


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
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def test_application_principal_import_graph_is_closed():
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
    issuer = _function(tree, "issue_production_application_principal")
    executable = [node for node in issuer.body if not isinstance(node, ast.Expr)]
    assert [type(node) for node in executable] == [ast.Raise]
    assert not any(isinstance(node, (ast.If, ast.Return)) for node in ast.walk(issuer))
    issue_calls = _called_names(issuer)
    assert "admit" not in issue_calls
    assert "mint_nonproduction_contract_seal" not in issue_calls
    assert "open_nonproduction_principal_proof_port" not in issue_calls
    assert "PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE" not in {
        node.id for node in ast.walk(issuer) if isinstance(node, ast.Name)
    }


def test_importing_application_principal_does_not_load_forbidden_modules():
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
    absent = sorted(name for name in FORBIDDEN_MODULES if name != "os")
    script = f"""
import sys
import wilvor_ai.master_application_principal as principal
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert principal.MASTER_APPLICATION_PRINCIPAL_SCHEMA_VERSION == (
    'wilvor.ai.master_application_principal.v1'
)
assert principal.PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False
assert principal.PRODUCTION_PRINCIPAL_AVAILABLE is False
assert principal.TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE is False
assert principal.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(principal.MasterRouteExecutionAuthority, 'PRODUCTION')
assert principal.MasterProductionAuthority.DENIED.value == 'DENIED'
assert principal.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert [item.value for item in principal.SourceClaimPresence] == ['ABSENT']
assert [item.value for item in principal.HostProofStanding] == ['UNRESOLVED']
assert not hasattr(principal, 'open_authenticated')
assert not hasattr(principal, 'dispatch')
assert not hasattr(principal, 'decode_jwt')
assert not hasattr(principal, 'os')
from wilvor_ai.master_gateway_identity import APPLICATION_OWNED_PRINCIPAL_AVAILABLE
assert APPLICATION_OWNED_PRINCIPAL_AVAILABLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
