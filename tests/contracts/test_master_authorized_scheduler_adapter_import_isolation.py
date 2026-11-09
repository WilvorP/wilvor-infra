"""The scheduler adapter does not import specialist runtimes, providers, or AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_authorized_scheduler_adapter.py"

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


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_scheduler_adapter_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
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
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "os.environ" not in source
    assert "getenv" not in source
    assert "boto3" not in source
    assert "langgraph" not in source
    assert "admit_master_evidence(" not in source
    assert "VerifiedZeroClaim(" not in source
    assert "run_synthetic_master_workflow" not in source
    assert "prepare_master_live_ops_native_bridge(" not in source
    assert "prepare_master_historical_native_invocation(" not in source
    assert "lambda_handler" not in source
    assert "def handler" not in source


def test_importing_scheduler_adapter_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_authorized_scheduler_adapter as adapter
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert adapter.PRODUCTION_DISPATCH_ENABLED is False
assert adapter.MAX_ROUTE_STEPS == 4
assert adapter.MAX_DECISION_STEPS == 1
assert adapter.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert adapter.MasterProductionAuthority.DENIED.value == 'DENIED'
assert adapter.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert all(item.imported is False for item in adapter.INTEGRATION_SEAMS)
assert all(item.invoked is False for item in adapter.SPECIALIST_RUNNER_SEAMS)
assert not hasattr(adapter, 'run_synthetic_master_workflow')
assert 'VerifiedZeroClaim' not in adapter.__dict__
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
