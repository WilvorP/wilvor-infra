"""The gateway identity parser stays free of JWT decoding, AWS, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_gateway_identity.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "re",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
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
    "requests",
    "terraform",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_grant",
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
    "redeem",
    "b64decode",
    "urlopen",
    "loads",
}

_CLOSED_DEFINITIONS = {
    "open_authenticated",
    "dispatch",
    "execute",
    "issue_grant",
    "redeem",
    "decode_jwt",
    "verify_jwt",
    "ApplicationOwnedPrincipal",
    "ProductionPrincipal",
    "ExecutionGrant",
}

_BANNED_SOURCE = (
    "boto3",
    "botocore",
    "b64decode",
    "urlopen",
    "PyJWT",
    "jwt.decode",
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


def test_gateway_identity_import_graph_is_closed():
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
    for banned in _BANNED_SOURCE:
        assert banned not in source


def test_importing_gateway_identity_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_ACCESS_KEY_ID", "testing")
    env.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    env.setdefault("AWS_SESSION_TOKEN", "testing")
    env.setdefault("AWS_DEFAULT_REGION", "us-west-1")
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_gateway_identity as identity
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert identity.MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION == (
    'wilvor.ai.master_gateway_identity.v1'
)
assert identity.PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is False
assert identity.GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is False
assert identity.APPLICATION_OWNED_PRINCIPAL_AVAILABLE is False
assert identity.UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is False
assert identity.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(identity.MasterRouteExecutionAuthority, 'PRODUCTION')
assert identity.MasterProductionAuthority.DENIED.value == 'DENIED'
assert identity.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert [item.value for item in identity.InvocationProvenanceStanding] == ['NOT_ATTESTED']
assert [item.value for item in identity.IngressAttestationStanding] == ['UNRESOLVED']
assert [item.value for item in identity.ApplicationPrincipalStanding] == ['NOT_ISSUED']
assert not hasattr(identity, 'open_authenticated')
assert not hasattr(identity, 'dispatch')
assert not hasattr(identity, 'decode_jwt')
assert not hasattr(identity.MasterPlanningSessionRegistry, 'open_authenticated')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
