"""The execution ledger imports no AWS client, specialist runtime, or fanout."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_execution_ledger.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "json",
    "secrets",
    "threading",
    "typing",
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
    "requests",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_fanout",
    "wilvor_ai.master_local_capability",
    "wilvor_ai.master_planning_session",
    "wilvor_operational",
}

PROHIBITED_CALLS = {
    "scan",
    "query",
    "delete_item",
    "update_item",
    "batch_write_item",
    "transact_write_items",
    "create_table",
    "describe_table",
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


def _called_attributes(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            called.add(node.func.attr)
    return called


def test_execution_ledger_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "botocore" not in source
    assert "datetime.now" not in source
    assert "os.environ" not in source
    assert "def dispatch" not in source
    assert "def execute" not in source
    assert "def invoke" not in source
    assert "VerifiedZeroClaim(" not in source
    assert "langgraph" not in source
    assert PROHIBITED_CALLS.isdisjoint(_called_attributes(MODULE))
    assert "get_item" in _called_attributes(MODULE)
    assert "put_item" in _called_attributes(MODULE)


def test_importing_execution_ledger_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["WILVOR_TEST_MODE"] = "1"
    env["WILVOR_EXECUTION_LEDGER_DURABLE"] = "true"
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_execution_ledger as ledger
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert ledger.MASTER_EXECUTION_LEDGER_SCHEMA_VERSION == "wilvor.ai.master_execution_ledger.v1"
assert ledger.MAX_ROUTE_STEPS == 4
assert ledger.MAX_DECISION_STEPS == 1
assert ledger.MAX_HISTORICAL_WINDOW.days == 7
assert ledger.IN_MEMORY_IS_PRODUCTION_CROSS_INSTANCE_AUTHORITY is False
assert ledger.GRANT_NONCE_IS_PRODUCTION_GRANT is False
assert ledger.TTL_DELETION_IS_CORRECTNESS is False
assert ledger.LEDGER_WRITES_ARE_SPECIALIST_READ_PERMISSIONS is False
assert ledger.PRINCIPAL_REF_IS_AUTHENTICATED_BY_THIS_MODULE is False
assert ledger.PROCESS_LOCAL_LOCK_IS_DISTRIBUTED_ATOMICITY is False
assert ledger.DYNAMODB_LEDGER_SCHEMA["provisioned_by_this_module"] is False
assert ledger.MasterExecutionLedgerResult.__dataclass_fields__["specialist_execution_permitted"].default is False
assert ledger.MasterExecutionLedgerResult.__dataclass_fields__["dispatch_permitted"].default is False
assert ledger.MasterExecutionLedgerResult.__dataclass_fields__["production_cross_instance_authority"].default is False
assert ledger.MasterRouteExecutionAuthority.DENIED.value == "DENIED"
assert not hasattr(ledger.MasterRouteExecutionAuthority, "PRODUCTION")
assert ledger.MasterProductionAuthority.DENIED.value == "DENIED"
assert ledger.MasterSourceAuthenticity.NOT_ESTABLISHED.value == "NOT_ESTABLISHED"
assert not hasattr(ledger.MasterExecutionLedger, "dispatch")
assert not hasattr(ledger, "boto3")
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
