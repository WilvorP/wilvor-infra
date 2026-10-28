"""The runtime budget stays free of AWS, providers, journals, and dispatch."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_runtime_budget.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "threading",
    "wilvor_ai.contracts",
    "wilvor_ai.live_ops_model_contracts",
    "wilvor_ai.live_ops_public_contracts",
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
    "wilvor_ai.master_execution_fanout",
    "wilvor_ai.master_execution_grant",
    "wilvor_ai.master_execution_integration",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_runtime_read_journal",
    "wilvor_ai.master_specialist_adapters",
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
    "time.time",
    "table.scan",
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


def test_runtime_budget_import_graph_is_closed():
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
    assert "LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT = False" in source
    assert "DEPLOYED_HOST_ENABLED = False" in source
    assert "READ_EXECUTOR_SEAM" in source
    assert "MA3_ADAPTER_SEAM" in source


def test_importing_runtime_budget_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_runtime_budget as budget
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert budget.MASTER_RUNTIME_BUDGET_SCHEMA_VERSION == 'wilvor.ai.master_runtime_budget.v1'
assert budget.PRODUCTION_GRANT_AVAILABLE is False
assert budget.PRODUCTION_PRINCIPAL_AVAILABLE is False
assert budget.PRODUCTION_LEDGER_AVAILABLE is False
assert budget.DEPLOYED_HOST_ENABLED is False
assert budget.LOCAL_FAKE_JOURNAL_IS_AWS_ENFORCEMENT is False
assert budget.CLOCK_IS_PRODUCTION_PROOF is False
assert not hasattr(budget, 'dispatch')
assert not hasattr(budget, 'execute')
assert not hasattr(budget, 'grant')
assert not hasattr(budget, 'DispatchPermit')
assert not hasattr(budget, 'ProductionPermit')
seam = budget.integration_seam()
assert 'MasterRuntimeReadJournal' in seam.read_executor
assert 'describe_planned_adapters' in seam.ma3_adapter
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
