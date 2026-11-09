"""Authenticated read receipt stays free of AWS, providers, and unmerged modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_authenticated_read_receipt.py"

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "dataclasses",
    "datetime",
    "enum",
    "threading",
    "types",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_provenance_trace",
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
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_capability_registry",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_historical_native_bridge",
    "wilvor_ai.master_local_capability",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_source_read_trace_boundary",
    "wilvor_ai.providers",
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


def test_authenticated_read_receipt_has_no_aws_or_provider_dependency():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in (
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "langgraph",
        "terraform",
        "AthenaExecutor",
        "os.environ",
        "VerifiedZeroClaim(",
        "from_event",
    ):
        assert banned not in source


def test_importing_authenticated_read_receipt_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_authenticated_read_receipt as receipt
import wilvor_ai.master_evidence_admission as admission
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert receipt.SOURCE_AUTHENTICATED_READ_AVAILABLE is False
assert receipt.PRODUCTION_ORIGIN_RESOLVED is False
assert receipt.LOCAL_TRUSTED_ADAPTER_IS_PRODUCTION_AUTHENTICITY is False
assert receipt.TOOL_RESULT_IS_READ_RECEIPT is False
assert receipt.EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
assert receipt.LIVE_READS_ENABLED is False
assert receipt.AWS_CONNECTION_IMPLEMENTED is False
assert receipt.PRODUCTION_GRANT_CONSUMPTION_AVAILABLE is False
assert receipt.PRODUCTION_LEDGER_CONSUMPTION_AVAILABLE is False
assert admission.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert list(admission.MasterSourceAuthenticity) == [admission.MasterSourceAuthenticity.NOT_ESTABLISHED]
assert 'source_authenticity' not in receipt.HostIssuedReadReceipt.__dataclass_fields__
assert receipt.HostIssuedReadReceipt.source_authenticity.fget(None).value == 'NOT_ESTABLISHED'
assert receipt.HostIssuedReadReceipt.production_authority.fget(None).value == 'DENIED'
assert receipt.HostIssuedReadReceipt.evidence_admitted.fget(None) is False
assert receipt.HostIssuedReadReceipt.verified_zero_established.fget(None) is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
