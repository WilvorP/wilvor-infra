"""Whole Decision Expert import boundary. Fresh interpreters only."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"


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


def test_layer_a_claim_contracts_do_not_load_runtime():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.decision_claims
import wilvor_ai.decision_model_contracts
absent = [
    'wilvor_ai.providers',
    'wilvor_ai.providers.anthropic_decision_messages',
    'wilvor_ai.decision_tools',
    'boto3',
    'botocore',
    'wilvor_operational',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_layer_b_verifier_renderer_and_runtime_stay_offline():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.decision_evidence_verifier
import wilvor_ai.decision_answer_renderer
import wilvor_ai.decision_specialist_runtime_contracts
absent = [
    'wilvor_ai.providers.anthropic_decision_messages',
    'wilvor_ai.historical_specialist',
    'boto3',
    'anthropic',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_layer_c_specialist_loads_tools_but_not_providers():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.decision_specialist as specialist
assert hasattr(specialist, 'DecisionSpecialist')
assert 'wilvor_ai.decision_tools' in sys.modules
absent = [
    'wilvor_ai.providers',
    'anthropic',
    'wilvor_ai.historical_specialist',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_layer_d_decision_provider_does_not_load_the_specialist():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai.providers.anthropic_decision_messages as provider
assert hasattr(provider, 'AnthropicDecisionMessagesProvider')
absent = [
    'wilvor_ai.decision_specialist',
    'wilvor_ai.decision_tools',
    'wilvor_ai.decision_evidence_verifier',
    'wilvor_ai.decision_answer_renderer',
    'wilvor_ai.historical_specialist',
    'anthropic',
    'boto3',
]
loaded = [name for name in absent if name in sys.modules]
assert loaded == [], loaded
"""
        )
    )


def test_package_imports_do_not_autoload_the_decision_expert():
    _assert_clean(
        _run_isolated(
            """
import sys
import wilvor_ai
import wilvor_ai.providers as providers
assert 'wilvor_ai.providers.anthropic_decision_messages' not in sys.modules
assert 'wilvor_ai.decision_specialist' not in sys.modules
assert not hasattr(wilvor_ai, 'DecisionSpecialist')
assert not hasattr(providers, 'AnthropicDecisionMessagesProvider')
"""
        )
    )
