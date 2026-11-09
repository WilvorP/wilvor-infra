"""The local capability registry stays free of specialist runtimes and Sprint 2 modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_local_capability.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "datetime",
    "enum",
    "secrets",
    "threading",
    "typing",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_routing",
    "wilvor_ai.model_contracts",
}

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "requests",
    "socket",
    "subprocess",
    "terraform",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_grant_registry",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_provenance",
    "wilvor_ai.specialist_dispatcher",
    "wilvor_operational",
}

BANNED_SOURCE_FRAGMENTS = (
    "invoke_historical",
    "invoke_decision",
    "invoke_live_ops",
    "datetime.now",
    "boto3",
    "botocore",
    "terraform",
    "def dispatch",
    "def invoke",
    "def execute",
    "class ExecutionAuthority",
    "class GrantRegistry",
    "class SpecialistDispatcher",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_provenance",
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


def test_local_capability_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for fragment in BANNED_SOURCE_FRAGMENTS:
        assert fragment not in source
    assert "PROPOSED" in source
    assert "threading.Lock" in source


def test_importing_local_capability_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_local_capability as capability
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert capability.LOCAL_CAPABILITY_SCHEMA_VERSION == 'wilvor.ai.master_local_capability.v1'
assert capability.LocalCapabilityRegistry.__module__ == 'wilvor_ai.master_local_capability'
assert capability.PROCESS_LOCAL_RANDOM_TOKEN_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION is False
assert capability.PROCESS_LOCAL_OBJECT_IDENTITY_IS_DISTRIBUTED_PRODUCTION_AUTHORIZATION is False
assert not hasattr(capability, 'dispatch')
assert not hasattr(capability, 'invoke')
assert not hasattr(capability.LocalCapabilityRegistry, 'dispatch')
assert not hasattr(capability.LocalCapabilityRegistry, 'invoke')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
