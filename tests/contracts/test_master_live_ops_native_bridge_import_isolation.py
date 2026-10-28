"""The Live Ops native bridge does not import runtimes, providers, or AWS clients."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_live_ops_native_bridge.py"

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
    "wilvor_ai.providers",
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


def test_live_ops_native_bridge_import_graph_excludes_runtimes():
    imported = _imported_modules(MODULE)
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    source = MODULE.read_text(encoding="utf-8")
    for banned in _FORBIDDEN_CALLS:
        assert banned not in source


def test_importing_live_ops_native_bridge_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_live_ops_native_bridge as bridge
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert not hasattr(bridge, "LiveOpsSpecialist")
assert not hasattr(bridge, "LiveOpsAdapter")
assert not hasattr(bridge, "LiveOpsTrustedContext")
assert not hasattr(bridge, "ToolResult")
assert not hasattr(bridge, "resolve_region")
assert bridge.LIVE_OPS_MISSING_HOST_EXECUTION_GRANT == "trusted_host_execution_grant"
fields = bridge.MasterLiveOpsNativeBridge.__dataclass_fields__
assert fields["dispatch_permitted"].default is False
assert fields["executable"].default is False
assert fields["specialist_invoked"].default is False
assert fields["selectors_authorized"].default is False
assert fields["geospatial_coverage_established"].default is False
assert fields["regional_string_match_is_sigmet_coverage"].default is False
assert fields["census_region_resolution_is_sigmet_coverage"].default is False
assert fields["application_request_time_is_source_clock"].default is False
assert fields["authenticated_source_evidence"].default is False
assert fields["native_execution_is_source_evidence"].default is False
assert fields["aircraft_impact_count"].default is None
assert fields["verified_zero"].default is False
assert fields["absence_counted_as_zero"].default is False
assert fields["production_authority"].default.value == "DENIED"
assert fields["execution_authority"].default.value == "DENIED"
assert fields["source_authenticity"].default.value == "NOT_ESTABLISHED"
assert fields["request_coverage"].default.value == "NOT_ESTABLISHED"
assert fields["missing_integration_dependency"].default == "trusted_host_execution_grant"
assert "dispatch" not in fields
assert "run" not in fields
assert "tables" not in fields
assert "provider" not in fields
assert not hasattr(bridge.prepare_master_live_ops_native_bridge, "dispatch")
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
