"""MA3 dispatch integration stays offline and does not widen the adapter import graph."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
ADAPTER = SHARED_DIR / "wilvor_ai" / "master_authorized_scheduler_adapter.py"
INTEGRATION = SHARED_DIR / "wilvor_ai" / "master_host_scheduler_integration.py"
RUNTIME = SHARED_DIR / "wilvor_ai" / "master_specialist_runtime_contract.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_durable_execution_ledger",
    "wilvor_ai.master_execution_policy_permit",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_historical_native_bridge",
    "wilvor_ai.master_host_execution_grant",
    "wilvor_ai.master_live_ops_native_bridge",
    "wilvor_ai.master_orchestration",
    "wilvor_historical",
    "wilvor_operational",
}

ADAPTER_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "functools",
    "threading",
    "types",
    "typing",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_execution_fanout",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_routing",
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


def test_adapter_import_graph_stays_closed():
    assert _imported_modules(ADAPTER) == ADAPTER_IMPORTS
    source = ADAPTER.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "boto3" not in source
    assert "lambda_handler" not in source
    assert "admit_master_evidence(" not in source


def test_host_scheduler_integration_does_not_import_specialist_runtimes():
    imported = _imported_modules(INTEGRATION)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    assert "wilvor_ai.master_execution_grants" in imported
    assert "wilvor_ai.master_execution_ledger" in imported
    assert "wilvor_ai.master_authorized_scheduler_adapter" in imported
    source = INTEGRATION.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "boto3" not in source
    assert "lambda_handler" not in source
    assert "issue_production_execution_grant(" not in source
    assert "admit_master_evidence(" not in source
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert ".dispatch(" not in runtime
    assert "LiveOpsSpecialist(" not in runtime
    assert "PRODUCTION_HOST: ReviewedApplicationHostPort | None = None" in runtime


def test_importing_the_dispatch_port_does_not_enable_production_or_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_host_scheduler_integration as integration
import wilvor_ai.master_authorized_scheduler_adapter as adapter
import wilvor_ai.master_specialist_runtime_contract as runtime
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert adapter.PRODUCTION_DISPATCH_ENABLED is False
assert integration.PRODUCTION_HOST is None
assert runtime.PRODUCTION_HOST is None
assert runtime.SPECIALIST_DISPATCH_AVAILABLE is False
assert runtime.GRANT_ISSUANCE_AVAILABLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
