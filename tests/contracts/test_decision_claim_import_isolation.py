"""Import isolation for DE0 Decision claim contracts."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
CLAIMS = SHARED_DIR / "wilvor_ai" / "decision_claims.py"
MODEL = SHARED_DIR / "wilvor_ai" / "decision_model_contracts.py"

_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
    "wilvor_operational",
    "wilvor_ai.providers",
    "wilvor_ai.decision_tools",
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


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    return subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_decision_contract_sources_do_not_import_runtime_or_providers() -> None:
    for path in (CLAIMS, MODEL):
        imported = _imported_modules(path)
        for forbidden in _FORBIDDEN:
            assert forbidden not in imported
            assert not any(
                item == forbidden or item.startswith(forbidden + ".")
                for item in imported
            )


def test_import_wilvor_ai_does_not_load_decision_claim_contracts() -> None:
    script = """
import sys
import wilvor_ai
assert "wilvor_ai.decision_claims" not in sys.modules
assert "wilvor_ai.decision_model_contracts" not in sys.modules
assert not hasattr(wilvor_ai, "DecisionModelDecision")
assert not hasattr(wilvor_ai, "decision_claim_from_dict")
assert "boto3" not in sys.modules
assert "anthropic" not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_importing_decision_contracts_stays_offline() -> None:
    script = """
import sys
import wilvor_ai.decision_claims
import wilvor_ai.decision_model_contracts
assert "boto3" not in sys.modules
assert "botocore" not in sys.modules
assert "anthropic" not in sys.modules
assert "openai" not in sys.modules
assert "langgraph" not in sys.modules
assert "wilvor_operational" not in sys.modules
assert "wilvor_ai.decision_tools" not in sys.modules
assert "wilvor_ai.providers" not in sys.modules
assert "wilvor_ai.providers.anthropic_messages" not in sys.modules
assert "wilvor_ai.historical_specialist" not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
