"""The Live Ops readiness assessment does not import runtimes or AWS clients."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_live_ops_execution_readiness.py"

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
    "wilvor_ai.live_ops_model_contracts",
    "wilvor_ai.live_ops_specialist",
    "wilvor_ai.master_orchestration",
    "wilvor_operational",
    "wilvor_operational.access",
    "wilvor_operational.context",
    "wilvor_operational.discovery",
    "wilvor_operational.geospatial",
    "wilvor_operational.query",
    "wilvor_operational.readers",
    "wilvor_operational.regions",
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
    "LiveOpsSpecialistTrustedContext(",
    "LiveOpsCall(",
    "OperationalTables(",
    "ToolResult(",
    "resolve_region(",
    "decode_and_run_live_ops(",
    "discover_current_hazards_in_region(",
    ".dispatch(",
    ".run(",
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


def test_live_ops_execution_readiness_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source


def test_importing_live_ops_execution_readiness_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_live_ops_execution_readiness as readiness
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert not hasattr(readiness, "LiveOpsSpecialist")
assert not hasattr(readiness, "LiveOpsAdapter")
assert not hasattr(readiness, "LiveOpsTrustedContext")
assert not hasattr(readiness, "ToolResult")
assert not hasattr(readiness, "resolve_region")
fields = readiness.MasterLiveOpsExecutionReadiness.__dataclass_fields__
assert fields["dispatch_permitted"].default is False
assert fields["executable"].default is False
assert fields["execution_blocked"].default is True
assert fields["public_request_constructible"].default is True
assert fields["production_authority"].default.value == "DENIED"
assert fields["execution_authority"].default.value == "DENIED"
assert fields["source_authenticity"].default.value == "NOT_ESTABLISHED"
assert fields["request_coverage"].default.value == "NOT_ESTABLISHED"
assert fields["source_read_authorized"].default is False
assert fields["trusted_region_authorized"].default is False
assert fields["trusted_runtime_present"].default is False
assert fields["source_observation_clock_present"].default is False
assert fields["geospatial_coverage_established"].default is False
assert fields["claimed_scope_establishes_geospatial_coverage"].default is False
assert fields["specialist_invoked"].default is False
assert fields["tool_result_created"].default is False
assert fields["aircraft_impact_count"].default is None
assert fields["verified_zero"].default is False
assert "dispatch" not in fields
assert "tables" not in fields
assert "run" not in fields
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
