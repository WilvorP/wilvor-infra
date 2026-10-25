"""Decision-target readiness stays free of runtimes, providers, and AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_decision_execution_readiness.py"

FORBIDDEN_MODULES = {
    "anthropic",
    "boto3",
    "botocore",
    "langgraph",
    "openai",
    "wilvor_ai.decision_context",
    "wilvor_ai.decision_specialist",
    "wilvor_ai.decision_specialist_runtime_contracts",
    "wilvor_ai.decision_tools",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.live_ops",
    "wilvor_ai.live_ops_adapter",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.persisted_airport_evidence",
    "wilvor_operational",
}

# The existing Decision binding imports admission, which loads the Decision
# run-contract module. That transitive load is not a direct import and it
# does not construct a request. The runtime check below follows the binding
# boundary and does not treat that transitive module as a new dependency.
RUNTIME_FORBIDDEN_MODULES = FORBIDDEN_MODULES - {
    "wilvor_ai.decision_specialist_runtime_contracts",
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


def test_decision_readiness_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "langgraph" not in source
    assert "boto3" not in source
    assert "AthenaExecutor" not in source
    assert "DecisionToolsRuntime(" not in source
    assert "DecisionSpecialistRequest(" not in source
    assert "DecisionSpecialist(" not in source


def test_importing_decision_readiness_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(RUNTIME_FORBIDDEN_MODULES)
    script = f"""
import dataclasses
import sys
import wilvor_ai.master_decision_execution_readiness as readiness
import wilvor_ai.master_routing as routing
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert readiness.DecisionTargetReadinessStatus.TRUSTED_TARGET_UNAVAILABLE.value == 'TRUSTED_TARGET_UNAVAILABLE'
assert readiness.MAX_DECISION_STEPS is routing.MAX_DECISION_STEPS
fields = readiness.MasterDecisionExecutionReadiness.__dataclass_fields__
assert fields['dispatch_permitted'].default is False
assert fields['executable'].default is False
assert fields['target_authorization_granted'].default is False
assert fields['trusted_decision_target_available'].default is False
assert fields['decision_invocation_count'].default is dataclasses.MISSING
assert fields['recorded_risk_level'].default is None
assert fields['authorization_token'].default is None
assert 'bundle' not in fields
assert 'evidence_inputs' not in fields
assert 'decision_request' not in fields
assert 'DecisionSpecialistRequest' not in readiness.__dict__
assert 'DecisionToolsRuntime' not in readiness.__dict__
assert 'DecisionSpecialist' not in readiness.__dict__
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
