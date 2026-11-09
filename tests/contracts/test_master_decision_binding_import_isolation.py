"""The Decision binding boundary stays free of runtimes, providers, and AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_decision_binding.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_context",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.persisted_airport_evidence",
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


def test_decision_binding_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "langgraph" not in source
    assert "boto3" not in source
    assert "AthenaExecutor" not in source
    assert "DecisionToolsRuntime(" not in source
    assert "DecisionSpecialistRequest(" not in source
    assert "DecisionSpecialist(" not in source


def test_importing_decision_binding_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_decision_binding as binding
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert binding.MasterDecisionBindingStatus.BLOCKED_MISSING_TRUSTED_TARGET.value == 'BLOCKED_MISSING_TRUSTED_TARGET'
assert binding.MasterDecisionBinding.__dataclass_fields__['dispatch_permitted'].default is False
assert binding.MasterDecisionBinding.__dataclass_fields__['executable'].default is False
assert binding.MasterDecisionBinding.__dataclass_fields__['target_authorization_granted'].default is False
assert binding.MasterDecisionBinding.__dataclass_fields__['decision_target_trusted'].default is False
assert binding.MasterDecisionBinding.__dataclass_fields__['trusted_aircraft_id'].default is None
assert 'bundle' not in binding.MasterDecisionBinding.__dataclass_fields__
assert 'evidence_inputs' not in binding.MasterDecisionBinding.__dataclass_fields__
assert 'supplied_run_status' not in binding.MasterDecisionBinding.__dataclass_fields__
assert 'DecisionSpecialistRequest' not in binding.__dict__
assert 'DecisionToolsRuntime' not in binding.__dict__
assert 'DecisionSpecialist' not in binding.__dict__
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
