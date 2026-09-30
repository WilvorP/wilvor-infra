"""DT1 decision contracts stay free of processors, AWS, and model SDKs."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
DECISION = SHARED_DIR / "wilvor_ai" / "decision_contracts.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_operational",
    "wilvor_historical_query",
    "wilvor_ai.live_ops",
    "wilvor_ai.providers",
    "wilvor_ai.historical_analytics",
    "wilvor_ai.historical_specialist",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_decision_contract_source_does_not_import_processors_or_sdks():
    imported = _imported_modules(DECISION)
    source = DECISION.read_text(encoding="utf-8")

    assert imported.isdisjoint(_FORBIDDEN)
    assert "boto3" not in source
    assert "functions.risk" not in source
    assert "functions.recommendations" not in source
    assert "DECISION_TOOLS" not in source
    assert "datetime.now" not in source
    assert "time.time" not in source


def test_import_decision_contracts_stays_offline():
    script = """
import sys
import wilvor_ai.decision_contracts as decision_contracts
assert hasattr(decision_contracts, "DecisionEvidence")
assert hasattr(decision_contracts, "validate_decision_tool_result")
assert not hasattr(decision_contracts, "DECISION_TOOLS")
assert "boto3" not in sys.modules
assert "botocore" not in sys.modules
assert "anthropic" not in sys.modules
assert "langgraph" not in sys.modules
assert "wilvor_operational" not in sys.modules
assert "wilvor_ai.live_ops" not in sys.modules
assert "wilvor_ai.historical_specialist" not in sys.modules
"""
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
