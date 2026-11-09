"""The Master contract module stays free of LangGraph and Live Ops internals."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_contracts.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.live_ops_claims",
    "wilvor_ai.live_ops_evidence_verifier",
    "wilvor_ai.live_ops_mapping",
    "wilvor_ai.live_ops_public_export",
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


def test_master_contract_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "wilvor_ai.contracts",
        "wilvor_ai.decision_specialist_runtime_contracts",
        "wilvor_ai.live_ops_public_contracts",
        "wilvor_ai.specialist_contracts",
        "wilvor_ai.specialist_runtime_contracts",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "invoke_historical" not in source
    assert "invoke_decision" not in source
    assert "invoke_live_ops" not in source


def test_importing_master_contracts_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_contracts as master
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert master.MASTER_INVOCATION_SCHEMA_VERSION == 'wilvor.ai.master_invocation.v1'
assert master.MasterProductionAuthority.PRODUCTION.value == 'PRODUCTION'
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
