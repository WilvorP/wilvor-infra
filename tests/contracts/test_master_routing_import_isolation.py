"""The Master routing module stays free of execution runtimes."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
MODULE = SHARED_DIR / "wilvor_ai" / "master_routing.py"

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


def test_master_routing_import_graph_is_closed():
    imported = _imported_modules(MODULE)
    assert imported == {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "typing",
        "wilvor_ai.contracts",
        "wilvor_ai.master_contracts",
        "wilvor_ai.specialist_contracts",
    }
    assert FORBIDDEN_MODULES.isdisjoint(imported)
    assert "wilvor_ai.decision_specialist_runtime_contracts" not in imported
    source = MODULE.read_text(encoding="utf-8")
    assert "DecisionSpecialistRequest" not in source
    assert "langgraph" not in source
    assert "invoke_historical" not in source
    assert "invoke_decision" not in source
    assert "invoke_live_ops" not in source


def test_importing_master_routing_does_not_load_forbidden_modules():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    absent = sorted(FORBIDDEN_MODULES)
    script = f"""
import sys
import wilvor_ai.master_routing as routing
loaded = [name for name in {absent!r} if name in sys.modules]
assert loaded == [], loaded
assert routing.MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION == 'wilvor.ai.master_route_proposal.v1'
assert routing.MasterRouteExecutionAuthority.DENIED.value == 'DENIED'
assert not hasattr(routing.MasterRouteExecutionAuthority, 'PRODUCTION')
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
