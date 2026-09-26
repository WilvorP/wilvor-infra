"""Import isolation for Phase 3A.4 Bedrock Converse adapter."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PROVIDERS_INIT = SHARED_DIR / "wilvor_ai" / "providers" / "__init__.py"
ERRORS = SHARED_DIR / "wilvor_ai" / "providers" / "errors.py"
INSTRUCTIONS = SHARED_DIR / "wilvor_ai" / "providers" / "instructions.py"
BEDROCK = SHARED_DIR / "wilvor_ai" / "providers" / "bedrock_converse.py"
SPECIALIST = SHARED_DIR / "wilvor_ai" / "historical_specialist.py"
VERIFIER = SHARED_DIR / "wilvor_ai" / "historical_evidence_verifier.py"
RENDERER = SHARED_DIR / "wilvor_ai" / "historical_answer_renderer.py"

_SDK_FORBIDDEN = {
    "boto3",
    "botocore",
    "openai",
    "anthropic",
    "langgraph",
    "bedrock",
}


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


def test_import_wilvor_ai_does_not_load_providers_or_sdks():
    script = """
import sys
import wilvor_ai
assert 'wilvor_ai.providers' not in sys.modules
assert 'wilvor_ai.providers.bedrock_converse' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert not hasattr(wilvor_ai, 'BedrockConverseModelProvider')
assert not hasattr(wilvor_ai, 'BedrockConverseClient')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_providers_package_does_not_load_bedrock_or_sdks():
    script = """
import sys
import wilvor_ai.providers as providers
assert 'wilvor_ai.providers.bedrock_converse' not in sys.modules
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert not hasattr(providers, 'BedrockConverseModelProvider')
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_bedrock_converse_stays_sdk_free():
    script = """
import sys
import wilvor_ai.providers.bedrock_converse as bedrock_converse
assert hasattr(bedrock_converse, 'BedrockConverseModelProvider')
assert hasattr(bedrock_converse, 'BedrockConverseClient')
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'openai' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'langgraph' not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    imported = _imported_modules(BEDROCK)
    assert imported.isdisjoint(_SDK_FORBIDDEN)
    tree = ast.parse(BEDROCK.read_text(encoding="utf-8"), filename=str(BEDROCK))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "boto3.client":
            raise AssertionError("bedrock adapter must not construct boto3 clients")


def test_provider_sources_exclude_sdks_and_safety_core_excludes_adapter():
    for path in (PROVIDERS_INIT, ERRORS, INSTRUCTIONS, BEDROCK):
        imported = _imported_modules(path)
        assert imported.isdisjoint(_SDK_FORBIDDEN)
        source = path.read_text(encoding="utf-8")
        assert "boto3.client" not in source
        assert "datetime.now" not in source
    for path in (SPECIALIST, VERIFIER, RENDERER):
        imported = _imported_modules(path)
        assert "wilvor_ai.providers" not in imported
        assert "wilvor_ai.providers.bedrock_converse" not in imported
        source = path.read_text(encoding="utf-8")
        assert "BedrockConverseModelProvider" not in source
