"""Source-read trace boundary stays free of AWS, providers, and dispatch runtimes."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_source_read_trace_boundary.py"

ALLOWED_IMPORTS = {
    "__future__",
    "dataclasses",
    "enum",
    "wilvor_ai.contracts",
    "wilvor_ai.master_contracts",
    "wilvor_ai.master_evidence_admission",
    "wilvor_ai.master_parallel_contracts",
    "wilvor_ai.master_provenance_trace",
    "wilvor_ai.master_routing",
    "wilvor_ai.specialist_contracts",
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
    "wilvor_ai.master_capability_registry",
    "wilvor_ai.master_execution_scope",
    "wilvor_ai.master_gate_resolution",
    "wilvor_ai.master_local_capability",
    "wilvor_ai.master_orchestration",
    "wilvor_ai.master_planning_session",
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


def test_source_read_trace_boundary_has_no_aws_or_provider_dependency():
    imported = _imported_modules(MODULE)
    assert imported == ALLOWED_IMPORTS
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in (
        "boto3",
        "botocore",
        "anthropic",
        "openai",
        "langgraph",
        "terraform",
        "AthenaExecutor",
        "LocalCapabilityRegistry",
        "VerifiedZeroClaim(",
    ):
        assert banned not in source


def test_importing_source_read_trace_boundary_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_source_read_trace_boundary as boundary
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert boundary.TRUSTED_DISPATCHER_IMPLEMENTED is False
assert boundary.TRUSTED_RUNTIME_OBSERVATION_AVAILABLE is False
assert boundary.CALLER_SUPPLIED_TRACE_AUTHENTICATES_READ is False
assert boundary.MATCHING_TIMESTAMP_AUTHENTICATES_READ is False
assert boundary.MATCHING_SOURCE_VERSION_AUTHENTICATES_READ is False
assert boundary.MATCHING_REQUEST_ID_AUTHENTICATES_READ is False
assert boundary.CAPABILITY_CONSUMPTION_IS_DISPATCH_PERMISSION is False
assert boundary.EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
assert boundary.AUTHENTICATED_SOURCE_READ_AVAILABLE is False
assert boundary.MasterSourceAuthenticity.NOT_ESTABLISHED.value == 'NOT_ESTABLISHED'
assert boundary.MasterProductionAuthority.DENIED.value == 'DENIED'
assert boundary.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert boundary.MasterSourceReadTraceAssessment.__dataclass_fields__['dispatch_permitted'].default is False
assert boundary.MasterSourceReadTraceAssessment.__dataclass_fields__['production_authority'].default.value == 'DENIED'
assert boundary.MasterSourceReadTraceAssessment.__dataclass_fields__['source_authenticity'].default.value == 'NOT_ESTABLISHED'
assert boundary.MasterSourceReadTraceAssessment.__dataclass_fields__['observed_trusted_runtime_events'].default == ()
assert boundary.MasterSourceReadTraceAssessment.__dataclass_fields__['verified_zero_established'].default is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
