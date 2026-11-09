"""The trusted read executor does not import AWS, catalogs, or unmerged agents."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_trusted_read_executor.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "hmac",
    "re",
    "secrets",
    "threading",
    "types",
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
    "terraform",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_authenticated_read_receipt",
    "wilvor_ai.master_authorized_scope",
    "wilvor_ai.master_execution_grants",
    "wilvor_ai.master_runtime_budget",
    "wilvor_ai.master_runtime_policy",
    "wilvor_historical",
    "wilvor_historical_query",
    "wilvor_operational",
}

_FORBIDDEN_SOURCE = (
    "boto3",
    "botocore",
    "datetime.now",
    "os.environ",
    "getenv(",
    ".scan(",
    "scan_all(",
    "execute_statement(",
    "VerifiedZeroClaim(",
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


def test_trusted_read_executor_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_SOURCE:
        assert banned not in source
    assert "PRODUCTION_READ_EXECUTOR_ENABLED = False" in source
    assert "WITHIN_ALLOWLIST_IS_EXECUTION_GRANT = False" in source
    assert "def execute" in source


def test_importing_trusted_read_executor_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_trusted_read_executor as executor
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert executor.PRODUCTION_READ_EXECUTOR_ENABLED is False
assert executor.APPROVED_SOURCE_ADAPTER_INTEGRATED is False
assert executor.WITHIN_ALLOWLIST_IS_EXECUTION_GRANT is False
assert executor.FAKE_RESULT_IS_AVIATION_EVIDENCE is False
assert not hasattr(executor, 'issue_production_read_grant')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
