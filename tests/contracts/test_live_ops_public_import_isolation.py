"""The public Live Ops contract stays independent of specialist internals."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PUBLIC = SHARED_DIR / "wilvor_ai" / "live_ops_public_contracts.py"

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


def test_public_contract_import_graph_is_closed():
    imported = _imported_modules(PUBLIC)
    assert imported == {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "math",
        "typing",
        "wilvor_ai.contracts",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = PUBLIC.read_text(encoding="utf-8")
    assert "time.time" not in source
    assert "live_ops_public_export" not in source


def test_importing_public_contract_does_not_load_specialist_internals():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.live_ops_public_contracts as public
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert public.LIVE_OPS_PUBLIC_SCHEMA_VERSION == 'wilvor.ai.live_ops_public.v1'
assert not hasattr(public.LiveOpsTrustedContext, 'to_dict')
assert not hasattr(public.LiveOpsTrustedContext, 'from_dict')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
