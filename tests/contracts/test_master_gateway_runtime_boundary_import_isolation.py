"""The runtime boundary checker stays free of network, AWS, and JWT decoding."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_gateway_runtime_boundary.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "re",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_gateway_identity",
    "wilvor_ai.master_routing",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "base64",
    "binascii",
    "boto3",
    "botocore",
    "http",
    "jose",
    "json",
    "jwt",
    "langgraph",
    "openai",
    "requests",
    "socket",
    "ssl",
    "terraform",
    "urllib",
    "urllib3",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
}

# Stdlib modules such as json can be imported by baseline contracts. This
# checker must not load a network or cloud client of its own.
NETWORK_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "http",
    "jose",
    "jwt",
    "langgraph",
    "openai",
    "requests",
    "socket",
    "ssl",
    "terraform",
    "urllib",
    "urllib3",
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
    "b64decode",
    "decode",
    "dispatch",
    "execute",
    "get_unverified",
    "issue_grant",
    "loads",
    "open_authenticated",
    "redeem",
    "urlopen",
    "verify_jwt",
}

_CLOSED_DEFINITIONS = {
    "ApplicationOwnedPrincipal",
    "ExecutionGrant",
    "ProductionPrincipal",
    "decode_jwt",
    "dispatch",
    "execute",
    "issue_application_principal",
    "issue_grant",
    "open_authenticated",
    "redeem",
    "verify_jwt",
}

_BANNED_SOURCE = (
    "boto3",
    "botocore",
    "b64decode",
    "urlopen",
    "PyJWT",
    "jwt.decode",
    "get_unverified",
    "requests",
    "urllib",
    "socket",
    "base64",
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


def test_runtime_boundary_import_graph_is_closed():
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
    assert _FORBIDDEN_CALLS.isdisjoint(_called_names(MODULE))
    for banned in _BANNED_SOURCE:
        assert banned not in source
    assert "RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE = False" in source
    assert "RUNTIME_ADMISSION_ATTESTATION_AVAILABLE = False" in source
    assert "EVENT_INSPECTION_ESTABLISHES_PROVENANCE = False" in source


def test_importing_runtime_boundary_does_not_load_network_modules():
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
    absent = sorted(NETWORK_MODULES)
    script = f"""
import sys
import wilvor_ai.master_gateway_runtime_boundary as boundary
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert boundary.MASTER_GATEWAY_RUNTIME_BOUNDARY_SCHEMA_VERSION == (
    'wilvor.ai.master_gateway_runtime_boundary.v1'
)
assert boundary.RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is False
assert boundary.RUNTIME_ADMISSION_ATTESTATION_AVAILABLE is False
assert boundary.EVENT_INSPECTION_ESTABLISHES_PROVENANCE is False
assert boundary.UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is False
report = boundary.assess_gateway_runtime_boundary(
    boundary.current_disabled_route_manifest(name_prefix='wilvor-dev')
)
assert report.invocation_standing.value == 'NOT_ATTESTED'
assert report.production_issuance_available is False
assert report.allows_production_issuance() is False
assert not report
assert [item.value for item in boundary.gateway_identity.InvocationProvenanceStanding] == [
    'NOT_ATTESTED'
]
assert not hasattr(boundary, 'issue_application_principal')
assert not hasattr(boundary, 'decode_jwt')
assert not hasattr(boundary, 'verify_jwt')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
