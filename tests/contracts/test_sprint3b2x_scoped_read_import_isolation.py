"""The scoped-read harness does not import AWS, providers, or specialist runners."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_host_scoped_read_integration.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.master_authorized_scope",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_runtime_budget",
    "wilvor_ai.master_trusted_read_executor",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "terraform",
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
    "pickle",
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


def test_scoped_read_harness_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_SOURCE:
        assert banned not in source
    assert "PRODUCTION_HOST_SCOPED_READ_ENABLED = False" in source
    assert "WITHIN_ALLOWLIST_IS_A_PERMIT = False" in source
    assert "def open_production_scoped_read" in source
    assert "MasterTrustedReadExecutor.execute" in source


def test_importing_scoped_read_harness_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_host_scoped_read_integration as harness
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert harness.PRODUCTION_HOST_SCOPED_READ_ENABLED is False
assert harness.WITHIN_ALLOWLIST_IS_A_PERMIT is False
assert harness.LOCAL_READ_IS_FLIGHT_SAFETY is False
assert harness.BLOCKED_IO_IS_INTERRUPTIBLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
