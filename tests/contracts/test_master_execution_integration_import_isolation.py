"""The execution handoff stays free of specialist runtimes and response generation.

``evaluate_execution_preflight`` imports ``MasterParallelContractBundle``.
That existing module imports evidence corroboration, which imports the
historical verifier. Importing the handoff therefore loads ``wilvor_historical``
transitively. The handoff module does not import or call that package. The
direct import allowlist below is the boundary for this module.
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_execution_integration.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_execution_preflight",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_planning_session",
    "wilvor_ai.master_routing",
    "wilvor_ai.master_specialist_adapters",
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
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_provenance_trace",
    "wilvor_ai.master_response_policy",
    "wilvor_ai.master_response_renderer",
    "wilvor_operational",
}

_REQUIRED_CALLS = (
    "describe_planned_adapters(",
    "bind_master_execution_scope(",
    "inspect_route_gates(",
    "evaluate_execution_preflight(",
    "MasterParallelContractBundle(",
)

_FORBIDDEN_CALLS = (
    "authorize_route(",
    "bind_planned_execution_scopes(",
    "run_synthetic_master_workflow(",
    "boto3",
    "botocore",
    "anthropic",
    "openai",
    "langgraph",
    "invoke_historical",
    "invoke_decision",
    "invoke_live_ops",
    "AthenaExecutor",
    "LiveOpsSpecialist(",
    "LiveOpsAdapter(",
    "finalize_historical_specialist_run(",
    "verify_historical_specialist_run(",
    "render_decision_verification(",
)

_CLOSED_DEFINITIONS = {
    "dispatch",
    "redeem",
    "execute",
    "grant",
    "authorize_route",
    "render_response",
    "open_authenticated",
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


def test_execution_integration_import_graph_is_closed():
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert _CLOSED_DEFINITIONS.isdisjoint(defined)
    source = MODULE.read_text(encoding="utf-8")
    for required in _REQUIRED_CALLS:
        assert required in source
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source
    assert "evidence_inputs=()" in source
    assert "comparison_requests=()" in source


def test_importing_execution_integration_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_execution_integration as handoff
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert handoff.MASTER_EXECUTION_INTEGRATION_SCHEMA_VERSION == 'wilvor.ai.master_execution_integration.v1'
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['execution_authorized'].default is False
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['executable'].default is False
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['dispatch_permitted'].default is False
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['provenance_authenticated'].default is False
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['factual_response_available'].default is False
assert handoff.MasterExecutionHandoffInspection.__dataclass_fields__['multi_invocation_execution_supported'].default is False
assert handoff.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(handoff.MasterRouteExecutionAuthority, 'PRODUCTION')
assert handoff.MasterProductionAuthority.DENIED.value == 'DENIED'
assert handoff.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert not hasattr(handoff, 'dispatch')
assert not hasattr(handoff, 'execute')
assert not hasattr(handoff, 'authorize_route')
assert not hasattr(handoff.MasterExecutionHandoffInspection, 'dispatch')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
