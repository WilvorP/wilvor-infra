"""The execution-grant module stays free of specialist runtimes, AWS, and unmerged hosts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_execution_grants.py"

EXPECTED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "hmac",
    "json",
    "secrets",
    "threading",
    "typing",
    "wilvor_ai.contracts",
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
    "os",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_application_principal",
    "wilvor_ai.master_authorized_scheduler_adapter",
    "wilvor_ai.master_authorized_scope",
    "wilvor_ai.master_execution_ledger",
    "wilvor_ai.master_execution_policy_boundary",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_runtime_budget",
    "wilvor_ai.master_runtime_policy",
    "wilvor_operational",
    "wilvor_historical",
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


def test_execution_grant_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == EXPECTED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "make_authorized_grant" not in source
    assert "os.environ" not in source
    assert "boto3" not in source
    assert "VerifiedZeroClaim" not in source
    assert "def dispatch" not in source
    assert "def execute" not in source
    assert "issue_atc_instructions(" not in source
    assert "wilvor_ai.master_runtime_policy" in source
    assert "wilvor_ai.master_execution_ledger" in source
    assert "wilvor_ai.master_runtime_budget" in source
    assert "wilvor_ai.master_authorized_scheduler_adapter" in source


def test_importing_execution_grants_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES - {"os"})
    script = f"""
import sys
import wilvor_ai.master_execution_grants as grants
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert grants.PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
assert grants.PRODUCTION_PRINCIPAL_HOST_INTEGRATED is False
assert grants.PRODUCTION_POLICY_HOST_INTEGRATED is False
assert grants.PRODUCTION_CONSUMPTION_LEDGER_INTEGRATED is False
assert grants.HMAC_PREVENTS_REPLAY is False
assert grants.LOCAL_HARNESS_IS_PRODUCTION_AUTHORIZATION is False
assert grants.MAX_ROUTE_STEPS == 4
assert grants.MAX_DECISION_STEPS == 1
assert grants.SOURCE_AUTHENTICITY_NOT_ESTABLISHED == 'NOT_ESTABLISHED'
assert grants.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(grants, 'make_authorized_grant')
assert not hasattr(grants, 'dispatch')
assert not hasattr(grants, 'execute')
assert 'VerifiedZeroClaim' not in grants.__dict__
assert all(item.status == 'NOT_INTEGRATED' for item in grants.INTEGRATION_OBLIGATIONS)
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
