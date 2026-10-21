"""Import isolation for the Live Ops Anthropic adapter."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
INSTRUCTIONS = SHARED_DIR / "wilvor_ai" / "providers" / "live_ops_instructions.py"
PROVIDER = SHARED_DIR / "wilvor_ai" / "providers" / "anthropic_live_ops_messages.py"

_FORBIDDEN_MODULES = frozenset(
    {
        "anthropic",
        "openai",
        "langgraph",
        "boto3",
        "botocore",
        "socket",
        "urllib",
        "urllib.request",
        "wilvor_operational",
        "wilvor_ai.live_ops",
        "wilvor_ai.live_ops_adapter",
        "wilvor_ai.live_ops_answer_renderer",
        "wilvor_ai.live_ops_evidence_verifier",
        "wilvor_ai.live_ops_public_export",
        "wilvor_ai.live_ops_specialist",
        "wilvor_ai.decision_specialist",
        "wilvor_ai.historical_specialist",
        "wilvor_ai.providers.anthropic_messages",
        "wilvor_ai.providers.anthropic_decision_messages",
        "wilvor_ai.providers.bedrock_converse",
    }
)


def _run_isolated(script: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    env.pop("ANTHROPIC_API_KEY", None)
    return subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
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


def _specialist_imports_are_function_local(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    for line in source.splitlines():
        if "live_ops_specialist" not in line:
            continue
        assert line[:1] in {" ", "\t"}
        assert not line.startswith("from ")
        assert not line.startswith("import ")


def test_provider_sources_do_not_import_closed_modules():
    instruction_imports = _imported_modules(INSTRUCTIONS) - {"__future__"}
    assert instruction_imports <= {
        "json",
        "wilvor_ai.live_ops_claims",
        "wilvor_ai.live_ops_model_contracts",
    }
    provider_imports = _imported_modules(PROVIDER) - {
        "wilvor_ai.live_ops_specialist",
    }
    assert "wilvor_ai.live_ops_specialist" in _imported_modules(PROVIDER)
    assert provider_imports.isdisjoint(_FORBIDDEN_MODULES)
    _specialist_imports_are_function_local(PROVIDER)
    _specialist_imports_are_function_local(INSTRUCTIONS)
    for path in (PROVIDER, INSTRUCTIONS):
        source = path.read_text(encoding="utf-8")
        assert "ANTHROPIC_API_KEY" not in source
        assert "os.environ" not in source
        assert "anthropic.Anthropic" not in source
        assert "boto3.client" not in source
        assert "socket." not in source


def test_providers_package_does_not_autoload_the_live_ops_adapter():
    script = """
import sys
import wilvor_ai.providers as providers
assert 'wilvor_ai.providers.anthropic_live_ops_messages' not in sys.modules
assert 'wilvor_ai.providers.live_ops_instructions' not in sys.modules
assert not hasattr(providers, 'AnthropicLiveOpsMessagesProvider')
assert 'anthropic' not in sys.modules
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_importing_the_live_ops_provider_stays_offline():
    script = """
import sys
import wilvor_ai.providers.anthropic_live_ops_messages as provider
assert hasattr(provider, 'AnthropicLiveOpsMessagesProvider')
forbidden = [
    'anthropic',
    'openai',
    'langgraph',
    'boto3',
    'botocore',
    'wilvor_operational',
    'wilvor_ai.live_ops',
    'wilvor_ai.live_ops_adapter',
    'wilvor_ai.live_ops_answer_renderer',
    'wilvor_ai.live_ops_evidence_verifier',
    'wilvor_ai.live_ops_public_export',
    'wilvor_ai.live_ops_specialist',
    'wilvor_ai.decision_specialist',
    'wilvor_ai.historical_specialist',
]
loaded = [name for name in forbidden if name in sys.modules]
assert loaded == [], loaded
assert 'ANTHROPIC_API_KEY' not in __import__('os').environ
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_successful_complete_does_not_import_the_specialist():
    script = """
import sys
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_INSTRUCTION_REF,
    LiveOpsModelTurnRequest,
)
from wilvor_ai.providers.anthropic_live_ops_messages import (
    AnthropicLiveOpsMessagesProvider,
)

class Client:
    def messages_create(self, **kwargs):
        return {
            'id': 'msg_test',
            'type': 'message',
            'role': 'assistant',
            'model': 'claude-sonnet-4-6',
            'stop_reason': 'tool_use',
            'content': [
                {
                    'type': 'tool_use',
                    'id': 'toolu_vendor_1',
                    'name': 'get_observed_network_state',
                    'input': {},
                }
            ],
        }

provider = AnthropicLiveOpsMessagesProvider(client=Client())
decision = provider.complete(
    LiveOpsModelTurnRequest(
        user_text='Which aircraft are currently observed?',
        instruction_ref=LIVE_OPS_INSTRUCTION_REF,
    )
)
assert decision.kind.value == 'TOOL_CALLS'
assert decision.tool_calls[0].name == 'get_observed_network_state'
assert 'toolu_vendor_1' not in decision.tool_calls[0].to_dict().get('arguments', {})
forbidden = [
    'anthropic',
    'langgraph',
    'boto3',
    'wilvor_operational',
    'wilvor_ai.live_ops_specialist',
    'wilvor_ai.live_ops',
]
loaded = [name for name in forbidden if name in sys.modules]
assert loaded == [], loaded
"""
    completed = _run_isolated(script)
    assert completed.returncode == 0, completed.stderr + completed.stdout
