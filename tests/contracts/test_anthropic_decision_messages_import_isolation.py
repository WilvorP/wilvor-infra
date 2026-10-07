"""Import isolation for the Decision Anthropic adapter."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
INSTRUCTIONS = SHARED_DIR / "wilvor_ai" / "providers" / "decision_instructions.py"
PROVIDER = SHARED_DIR / "wilvor_ai" / "providers" / "anthropic_decision_messages.py"

_FORBIDDEN_MODULES = frozenset(
    {
        "anthropic",
        "openai",
        "langgraph",
        "boto3",
        "botocore",
        "wilvor_operational",
        "wilvor_ai.persisted_airport_evidence",
        "wilvor_ai.decision_specialist",
        "wilvor_ai.decision_tools",
        "wilvor_ai.decision_context",
        "wilvor_ai.decision_evidence_verifier",
        "wilvor_ai.decision_answer_renderer",
        "wilvor_ai.historical_specialist",
        "wilvor_ai.historical_evidence_verifier",
        "wilvor_ai.historical_answer_renderer",
        "wilvor_ai.providers.anthropic_messages",
    }
)


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


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_decision_provider_sources_do_not_import_closed_modules():
    instruction_imports = _imported_modules(INSTRUCTIONS) - {"__future__"}
    assert instruction_imports == {"wilvor_ai.providers.errors"}
    provider_imports = _imported_modules(PROVIDER)
    assert provider_imports.isdisjoint(_FORBIDDEN_MODULES)
    source = PROVIDER.read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in source
    assert "anthropic.Anthropic" not in source
    assert "boto3.client" not in source


def test_providers_package_does_not_autoload_the_decision_adapter():
    script = """
import sys
import wilvor_ai.providers as providers
assert 'wilvor_ai.providers.anthropic_decision_messages' not in sys.modules
assert 'wilvor_ai.providers.decision_instructions' not in sys.modules
assert not hasattr(providers, 'AnthropicDecisionMessagesProvider')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_importing_the_decision_provider_stays_offline():
    script = """
import sys
import wilvor_ai.providers.anthropic_decision_messages as provider
assert hasattr(provider, 'AnthropicDecisionMessagesProvider')
forbidden = [
    'anthropic',
    'openai',
    'langgraph',
    'boto3',
    'botocore',
    'wilvor_ai.persisted_airport_evidence',
    'wilvor_ai.decision_specialist',
    'wilvor_ai.decision_tools',
    'wilvor_ai.decision_context',
    'wilvor_ai.decision_evidence_verifier',
    'wilvor_ai.decision_answer_renderer',
    'wilvor_ai.historical_specialist',
    'wilvor_ai.historical_evidence_verifier',
    'wilvor_ai.historical_answer_renderer',
    'wilvor_ai.providers.anthropic_messages',
]
loaded = [name for name in forbidden if name in sys.modules]
assert loaded == [], loaded
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
