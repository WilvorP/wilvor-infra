"""The Live Ops binder stays free of providers, tools, and AWS clients."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_live_ops_binding.py"

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
    "wilvor_ai.live_ops_claims",
    "wilvor_ai.live_ops_evidence_verifier",
    "wilvor_ai.live_ops_specialist",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
}

_FORBIDDEN_CALLS = (
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "LiveOpsSpecialist(",
    "LiveOpsAdapter(",
    "LiveOpsTrustedContext(",
    "LiveOpsAdmissionSources(",
    "AthenaExecutor",
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


def test_live_ops_binding_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source


def test_importing_live_ops_binding_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_live_ops_binding as binding
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert not hasattr(binding, "LiveOpsSpecialist")
assert not hasattr(binding, "LiveOpsAdapter")
assert not hasattr(binding, "LiveOpsTrustedContext")
assert not hasattr(binding, "LiveOpsAdmissionSources")
fields = binding.MasterLiveOpsBinding.__dataclass_fields__
assert fields["dispatch_permitted"].default is False
assert fields["executable"].default is False
assert fields["production_authority"].default.value == "DENIED"
assert fields["execution_authority"].default.value == "DENIED"
assert fields["source_authenticity"].default.value == "NOT_ESTABLISHED"
assert fields["aircraft_impact_count"].default is None
assert fields["verified_zero"].default is False
assert fields["trusted_context_created"].default is False
assert fields["admission_sources_created"].default is False
assert "dispatch" not in fields
assert "tables" not in fields
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
