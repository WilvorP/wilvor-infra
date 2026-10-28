"""Import isolation for the Sprint 3B.2 MA7 trusted-host boundary tests.

The boundary tests import the current Master contracts, including the Sprint
3B.1 gateway, scheduler, read guard, journal, and native bridges. They must
not import Sprint 3B.2 modules other agents are still writing, and they must
not load a specialist runner, a model provider, or AWS.

This file checks the imports of the boundary module. It does not assert that
another agent's file is absent from disk.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
BOUNDARY = (
    REPO_ROOT / "tests" / "contracts" / "test_sprint3b2_ma7_trusted_host_boundary.py"
)

UNMERGED_SPRINT3B2_MODULES = {
    "wilvor_ai.master_application_principal",
    "wilvor_ai.master_authenticated_read_receipt",
    "wilvor_ai.master_authorized_scheduler_adapter",
    "wilvor_ai.master_authorized_scope",
    "wilvor_ai.master_execution_grants",
    "wilvor_ai.master_execution_ledger",
    "wilvor_ai.master_gateway_runtime_boundary",
    "wilvor_ai.master_runtime_budget",
    "wilvor_ai.master_runtime_policy",
    "wilvor_ai.master_specialist_runtime_contract",
    "wilvor_ai.master_trusted_read_executor",
}

REQUIRED_BASELINE_MODULES = {
    "wilvor_ai.master_decision_target_resolver",
    "wilvor_ai.master_execution_fanout",
    "wilvor_ai.master_gateway_identity",
    "wilvor_ai.master_historical_native_bridge",
    "wilvor_ai.master_live_ops_native_bridge",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_runtime_read_journal",
}

FORBIDDEN_MODULES = UNMERGED_SPRINT3B2_MODULES | {
    "anthropic",
    "boto3",
    "botocore",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
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


def test_boundary_imports_baseline_contracts_only():
    imported = _imported_modules(BOUNDARY)
    assert REQUIRED_BASELINE_MODULES <= imported
    assert UNMERGED_SPRINT3B2_MODULES.isdisjoint(imported)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = BOUNDARY.read_text(encoding="utf-8")
    for banned in ("boto3", "botocore", "anthropic", "openai", "terraform"):
        assert banned not in source
    assert "run_synthetic_master_workflow" not in source
    assert "def dispatch" not in source


def test_importing_boundary_does_not_load_unmerged_modules():
    env = os.environ.copy()
    pythonpath = str(REPO_ROOT) + os.pathsep + str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    absent = sorted(FORBIDDEN_MODULES)
    required = sorted(REQUIRED_BASELINE_MODULES)
    script = f"""
import sys
import tests.contracts.test_sprint3b2_ma7_trusted_host_boundary as boundary
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
missing = [name for name in {required!r} if name not in sys.modules]
assert missing == [], missing
assert boundary.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(boundary.MasterRouteExecutionAuthority, 'GRANTED')
assert boundary.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert boundary.MasterProductionAuthority.DENIED.value == 'DENIED'
assert boundary.MasterRequestCoverage.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert boundary.PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is False
assert boundary.DISPATCH_GRANT_AVAILABLE is False
assert boundary.AUTHENTICATED_SOURCE_READ_AVAILABLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
