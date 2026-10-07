"""Direct-import isolation for the Decision specialist runtime."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
RUNTIME_CONTRACTS = (
    SHARED_DIR / "wilvor_ai" / "decision_specialist_runtime_contracts.py"
)
SPECIALIST = SHARED_DIR / "wilvor_ai" / "decision_specialist.py"

_FORBIDDEN = {
    "anthropic",
    "openai",
    "langgraph",
    "boto3",
    "botocore",
    "wilvor_operational",
    "wilvor_ai.persisted_airport_evidence",
    "persisted_airport_evidence",
    "wilvor_ai.historical_specialist",
    "wilvor_ai.historical_answer_renderer",
    "wilvor_ai.historical_evidence_verifier",
    "wilvor_ai.providers",
    "wilvor_ai.providers.anthropic_messages",
    "wilvor_ai.providers.bedrock_converse",
}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(
                f"{node.module}.{alias.name}" for alias in node.names
            )
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


def test_runtime_contracts_do_not_import_tools_or_providers():
    imported = _imported_modules(RUNTIME_CONTRACTS)
    assert imported.isdisjoint(_FORBIDDEN)
    assert "wilvor_ai.decision_tools" not in imported
    source = RUNTIME_CONTRACTS.read_text(encoding="utf-8")
    assert "decision_tools" not in source
    assert "HistoricalAnalyticsSpecialist" not in source
    assert "anthropic" not in source.casefold()


def test_specialist_may_import_decision_tools_but_not_providers():
    imported = _imported_modules(SPECIALIST)
    assert "wilvor_ai.decision_tools" in imported
    forbidden = set(_FORBIDDEN)
    assert imported.isdisjoint(forbidden)
    source = SPECIALIST.read_text(encoding="utf-8")
    assert "HistoricalAnalyticsSpecialist" not in source
    assert "anthropic" not in source.casefold()
    assert "openai" not in source.casefold()


def test_runtime_contract_import_stays_free_of_decision_tools():
    script = """
import sys
import wilvor_ai.decision_specialist_runtime_contracts as contracts
assert hasattr(contracts, 'DecisionSpecialistRequest')
assert hasattr(contracts, 'DecisionModelProvider')
assert 'wilvor_ai.decision_tools' not in sys.modules
assert 'wilvor_ai.persisted_airport_evidence' not in sys.modules
assert 'wilvor_operational' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'openai' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_ai.historical_specialist' not in sys.modules
assert 'wilvor_ai.providers' not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_specialist_import_distinguishes_transitive_tool_dependencies():
    script = """
import sys
import wilvor_ai.decision_specialist as specialist
assert hasattr(specialist, 'DecisionSpecialist')
assert 'wilvor_ai.decision_tools' in sys.modules
assert 'anthropic' not in sys.modules
assert 'openai' not in sys.modules
assert 'langgraph' not in sys.modules
assert 'wilvor_ai.historical_specialist' not in sys.modules
assert 'wilvor_ai.historical_answer_renderer' not in sys.modules
assert 'wilvor_ai.historical_evidence_verifier' not in sys.modules
assert 'wilvor_ai.providers' not in sys.modules
assert 'wilvor_ai.providers.anthropic_messages' not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
