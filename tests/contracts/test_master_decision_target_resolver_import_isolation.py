"""Decision target resolution stays free of runtimes, providers, and AWS."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_decision_target_resolver.py"

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

# Readiness imports the Decision binding, which loads the run-contract module
# transitively. That load is not a direct import and does not construct a
# request. The runtime check follows the readiness boundary.
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


def test_decision_resolver_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "langgraph" not in source
    assert "boto3" not in source
    assert "AthenaExecutor" not in source
    assert "DecisionToolsRuntime(" not in source
    assert "DecisionSpecialistRequest(" not in source
    assert "DecisionSpecialist(" not in source


def test_importing_decision_resolver_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(RUNTIME_FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_decision_target_resolver as resolver
import wilvor_ai.master_routing as routing
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert resolver.DecisionResolutionStatus.UNIQUE_CANDIDATE_NOT_ELIGIBLE.value == 'UNIQUE_CANDIDATE_NOT_ELIGIBLE'
assert resolver.MAX_DECISION_STEPS is routing.MAX_DECISION_STEPS
assert resolver.MAX_DECISION_STEPS == 1
assert resolver.MAX_PERSISTED_RECOMMENDATION_FANOUT == 3
fields = resolver.MasterDecisionTargetResolution.__dataclass_fields__
assert fields['dispatch_permitted'].default is False
assert fields['execution_eligible'].default is False
assert fields['trusted_target_issued'].default is False
assert fields['decision_request'].default is None
assert fields['authenticated_evidence'].default is False
assert fields['observed_recommendation_ids'].default == ()
assert fields['host_proofs_satisfied'].default == ()
assert 'DecisionSpecialistRequest' not in resolver.__dict__
assert 'DecisionSpecialist' not in resolver.__dict__
assert 'DecisionToolsRuntime' not in resolver.__dict__
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
