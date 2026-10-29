"""Host evidence integration stays free of AWS, providers, and response rendering."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_host_evidence_integration.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.master_authenticated_read_receipt",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_provenance_trace",
    "wilvor_ai.master_read_resource_guard",
    "wilvor_ai.master_routing",
    "wilvor_ai.master_source_read_trace_boundary",
    "wilvor_ai.master_trusted_read_executor",
    "wilvor_ai.specialist_runtime_contracts",
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
    "wilvor_ai.master_response_policy",
    "wilvor_ai.master_response_renderer",
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


def test_host_evidence_integration_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in ("boto3", "botocore", "anthropic", "openai", "langgraph", "terraform", "pickle"):
        assert banned not in source


def test_importing_host_evidence_integration_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_host_evidence_integration as integration
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert integration.PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is False
assert integration.LOCAL_READ_IS_PRODUCTION_EVIDENCE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
