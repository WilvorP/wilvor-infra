"""Import isolation for LO1 Live Ops specialist contracts and adapter."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
NEW_MODULES = (
    SHARED_DIR / "wilvor_ai" / "live_ops_model_contracts.py",
    SHARED_DIR / "wilvor_ai" / "live_ops_specialist_runtime_contracts.py",
    SHARED_DIR / "wilvor_ai" / "live_ops_adapter.py",
    SHARED_DIR / "wilvor_ai" / "live_ops_tool_projection.py",
    SHARED_DIR / "wilvor_ai" / "live_ops_claims.py",
    SHARED_DIR / "wilvor_ai" / "live_ops_evidence_verifier.py",
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


def _assert_clean(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_import_wilvor_ai_does_not_load_live_ops_lo1_modules():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai
absent = [
    'wilvor_ai.live_ops',
    'wilvor_ai.live_ops_adapter',
    'wilvor_ai.live_ops_model_contracts',
    'wilvor_ai.live_ops_specialist_runtime_contracts',
    'wilvor_ai.live_ops_tool_projection',
    'wilvor_ai.live_ops_claims',
    'wilvor_ai.live_ops_evidence_verifier',
    'wilvor_ai.historical_specialist',
    'wilvor_ai.decision_specialist',
    'boto3',
    'anthropic',
    'openai',
    'langgraph',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_model_contracts_and_projection_stay_offline():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.live_ops_model_contracts
import wilvor_ai.live_ops_tool_projection
absent = [
    'wilvor_ai.live_ops',
    'wilvor_ai.live_ops_adapter',
    'wilvor_ai.historical_specialist',
    'wilvor_ai.decision_specialist',
    'wilvor_ai.providers',
    'wilvor_operational',
    'boto3',
    'anthropic',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_adapter_does_not_load_other_specialists_or_providers():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.live_ops_adapter
absent = [
    'wilvor_ai.historical_specialist',
    'wilvor_ai.decision_specialist',
    'wilvor_ai.providers.anthropic_messages',
    'wilvor_ai.providers.anthropic_decision_messages',
    'anthropic',
    'openai',
    'langgraph',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
assert 'wilvor_ai.live_ops' in sys.modules
"""
        )
    )


def test_new_modules_have_no_clock_or_aws_imports():
    forbidden = {
        "boto3",
        "botocore",
        "time",
        "anthropic",
        "openai",
        "langgraph",
        "wilvor_ai.historical_specialist",
        "wilvor_ai.decision_specialist",
    }
    for path in NEW_MODULES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        overlap = forbidden.intersection(imported)
        assert overlap == set(), (path.name, overlap)
        source = path.read_text(encoding="utf-8")
        assert "time.time" not in source
        assert "os.environ" not in source
        assert "boto3" not in source
