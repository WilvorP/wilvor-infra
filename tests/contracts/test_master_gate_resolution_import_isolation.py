"""Gate resolution stays free of runtimes and other Sprint 2 modules."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_gate_resolution.py"

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
    "wilvor_ai.master_capability_registry",
    "wilvor_ai.master_dispatch",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_provenance",
    "wilvor_ai.master_provenance_binding",
    "wilvor_historical",
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


def test_gate_resolution_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
        "__future__",
        "dataclasses",
        "enum",
        "typing",
        "wilvor_ai.contracts",
        "wilvor_ai.decision_specialist_runtime_contracts",
        "wilvor_ai.live_ops_public_contracts",
        "wilvor_ai.master_contracts",
        "wilvor_ai.master_evidence_admission",
        "wilvor_ai.master_routing",
        "wilvor_ai.specialist_contracts",
        "wilvor_ai.specialist_runtime_contracts",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    assert "_without_omitted_dependencies" not in source
    assert "authorize_route" not in source
    assert "langgraph" not in source
    assert "boto3" not in source
    assert "invoke_historical" not in source
    assert "invoke_decision" not in source
    assert "invoke_live_ops" not in source
    assert "DispatchPermit" not in source
    assert "PlanningSession" not in source


def test_importing_gate_resolution_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_gate_resolution as resolution
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_routing import MasterRouteExecutionAuthority
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert resolution.MASTER_GATE_RESOLUTION_SCHEMA_VERSION == 'wilvor.ai.master_gate_resolution.v1'
assert resolution.inspect_route_gates.__name__ == 'inspect_route_gates'
assert resolution.dependency_verdict.__name__ == 'dependency_verdict'
assert MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert MasterProductionAuthority.DENIED.value == 'DENIED'
assert MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert 'SATISFIED' not in {{item.value for item in resolution.MasterGateEffectiveState}}
assert resolution.MasterTrustedGateEvidenceWriter.ABSENT.value == 'ABSENT'
assert not hasattr(resolution.MasterTrustedGateEvidenceWriter, 'PRESENT')
assert resolution.MasterDependencyVerdict.COMPLETED.value == 'COMPLETED'
assert (
    resolution.MasterDependencyVerdict.COMPLETED_WITH_INSUFFICIENT_EVIDENCE.value
    == 'COMPLETED_WITH_INSUFFICIENT_EVIDENCE'
)
assert all(item.startswith('PROPOSED:') for item in resolution.PROPOSED_INTERFACES)
assert not hasattr(resolution, 'DispatchPermit')
assert not hasattr(resolution, 'PlanningSession')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
