"""The grant/ledger composition stays offline and does not import a deployed host."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_host_grant_ledger_integration.py"

EXPECTED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "threading",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_execution_grants",
    "wilvor_ai.master_execution_ledger",
    "wilvor_ai.master_routing",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "os",
    "pickle",
    "requests",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_authorized_scheduler_adapter",
    "wilvor_ai.master_execution_fanout",
    "wilvor_ai.master_orchestration",
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


def test_grant_ledger_integration_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == EXPECTED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "boto3" not in source
    assert "os.environ" not in source
    assert "def dispatch" not in source
    assert "def execute" not in source
    assert "issue_atc_instructions(" not in source
    assert "CONSUME_ACCEPTS_MASTER_EXECUTION_LEDGER = True" not in source
    assert "DISTRIBUTED_EXACTLY_ONCE_ACHIEVED = False" in source


def test_importing_grant_ledger_integration_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env["WILVOR_PRODUCTION_GRANT"] = "true"
    absent = sorted(FORBIDDEN_MODULES - {"os"})
    script = f"""
import sys
import wilvor_ai.master_host_grant_ledger_integration as integration
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert integration.DISTRIBUTED_EXACTLY_ONCE_ACHIEVED is False
assert integration.LOCAL_SUCCESS_IS_LOCAL_ONLY is True
assert integration.NonproductionHostGrantLedger.distributed_exactly_once_achieved is not None
boundary = integration.CONSUMPTION_STARTED_BOUNDARY
assert boundary.startswith("NonproductionHostGrantLedger.consume_local_and_mark_started(")
assert "local_consumption_ledger: object" in boundary
assert "attempt_token: str" in boundary
assert "fence_epoch: int" in boundary
interface = integration.required_atomic_single_use_interface()
assert interface.implemented is False
assert interface.distributed_exactly_once is False
assert bool(interface) is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
