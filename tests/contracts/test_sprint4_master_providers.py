"""Sprint 4 Agent D: injected provider composition. Offline only. No model APIs."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_authorized_scheduler_adapter import ScriptedTestWorker
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ModelTurnRequest, ProposedToolCall
from wilvor_ai.providers.bedrock_converse import BedrockConverseModelProvider
from wilvor_ai.providers.master_instructions import (
    MASTER_PLANNING_INSTRUCTION_REF,
    resolve_master_instruction,
)
from wilvor_ai.sprint4_master_providers import (
    MASTER_PLANNER_MAX_TOOL_CALLS,
    MASTER_PLANNER_MAX_TURNS,
    SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE,
    SPRINT4_MODEL_AUTHORIZES_AWS_TOOLS,
    SPRINT4_MODEL_MANUFACTURES_SOURCE_AUTHENTICITY,
    SPRINT4_PRODUCTION_PROVIDER_CONSTRUCTION_AVAILABLE,
    assert_sprint4_provider_surface_closed,
    bind_injected_master_provider,
    bind_master_planning_provider,
    bind_provider_identity,
    bind_specialist_model_provider,
    inspect_untrusted_tool_calls,
    master_planning_instruction,
    refuse_live_model_invocation,
    refuse_production_provider_construction,
    reject_aws_tool_proposal,
    reject_master_planner_tools,
    reject_vendor_sdk_provider,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
PROVIDERS_MODULE = SHARED_DIR / "wilvor_ai" / "sprint4_master_providers.py"
INSTRUCTIONS = SHARED_DIR / "wilvor_ai" / "providers" / "master_instructions.py"


class FakeComplete:
    def __init__(self, decision):
        self.decision = decision
        self.requests = []

    def complete(self, turn):
        self.requests.append(turn)
        return self.decision


class FakeConverse:
    def converse(self, **kwargs):
        raise AssertionError("live converse must not be called")


def test_closed_flags_and_refusals_stay_closed():
    assert SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE is False
    assert SPRINT4_MODEL_AUTHORIZES_AWS_TOOLS is False
    assert SPRINT4_MODEL_MANUFACTURES_SOURCE_AUTHENTICITY is False
    assert SPRINT4_PRODUCTION_PROVIDER_CONSTRUCTION_AVAILABLE is False
    assert MASTER_PLANNER_MAX_TURNS == 1
    assert MASTER_PLANNER_MAX_TOOL_CALLS == 0
    assert_sprint4_provider_surface_closed()
    with pytest.raises(ContractValidationError, match="live_model_invocation_unavailable"):
        refuse_live_model_invocation()
    with pytest.raises(ContractValidationError, match="production_provider_construction_unavailable"):
        refuse_production_provider_construction()


def test_aws_tool_names_and_sql_arguments_are_rejected():
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("GetItem")
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("query")
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("scan")
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        reject_aws_tool_proposal("summarize_historical_encounters", {"sql": "SELECT 1"})
    reject_aws_tool_proposal("get_aircraft_operational_context", {"aircraft_id": "AC-1"})
    names = inspect_untrusted_tool_calls(
        ModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=(ProposedToolCall("get_observed_network_state", {}),),
        )
    )
    assert names == ("get_observed_network_state",)
    with pytest.raises(ContractValidationError, match="model_aws_authorization_rejected"):
        inspect_untrusted_tool_calls(
            ModelDecision(
                kind=ModelDecisionKind.TOOL_CALLS,
                tool_calls=(ProposedToolCall("TransactWriteItems", {"TableName": "x"}),),
            )
        )


def test_master_planner_cannot_emit_tools():
    decision = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall("get_observed_network_state", {}),),
    )
    with pytest.raises(ContractValidationError, match="master_planner_tools_rejected"):
        reject_master_planner_tools(decision)
    planner = bind_master_planning_provider(FakeComplete(decision))
    with pytest.raises(ContractValidationError, match="master_planner_tools_rejected"):
        planner.complete(ModelTurnRequest(user_text="Which aircraft are currently affected?"))


def test_scripted_worker_and_vendor_sdk_types_are_rejected():
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        reject_vendor_sdk_provider(ScriptedTestWorker())
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        reject_vendor_sdk_provider(lambda turn: None)

    class anthropic:  # noqa: N801 - module-shaped impostor
        class Client:
            def complete(self, turn):
                return turn

    impostor = anthropic.Client()
    impostor.__class__.__module__ = "anthropic"
    with pytest.raises(ContractValidationError, match="model_origin_dispatch_rejected"):
        reject_vendor_sdk_provider(impostor)


def test_existing_bedrock_adapter_binds_without_live_invocation():
    provider = BedrockConverseModelProvider(client=FakeConverse())
    binding = bind_provider_identity(provider)
    assert binding.approved_offline_adapter is True
    assert binding.live_model_invocation is False
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    wrapped = bind_injected_master_provider(
        provider,
        max_turns=1,
        max_tool_calls=0,
        allow_specialist_tools=False,
    )
    assert wrapped.inner is provider


def test_specialist_provider_enforces_server_budget():
    decision = ModelDecision(
        kind=ModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall("get_observed_network_state", {}),),
    )
    inner = FakeComplete(decision)
    wrapped = bind_specialist_model_provider(inner, MasterSpecialist.LIVE_OPS)
    first = wrapped.complete(ModelTurnRequest(user_text="count current aircraft"))
    assert first is decision
    with pytest.raises(ContractValidationError, match="server_budget_exceeded"):
        while True:
            wrapped.complete(ModelTurnRequest(user_text="count current aircraft"))


def test_master_instruction_is_code_owned_and_forbids_aws():
    text = master_planning_instruction()
    assert resolve_master_instruction(MASTER_PLANNING_INSTRUCTION_REF) == text
    assert "Do not call AWS APIs" in text
    assert "Do not authorize tools" in text
    with pytest.raises(Exception):
        resolve_master_instruction("wilvor.master.planning.v9")


def test_provider_module_stays_sdk_free():
    for path in (PROVIDERS_MODULE, INSTRUCTIONS):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert imported.isdisjoint({"boto3", "botocore", "anthropic", "openai"})
        source = path.read_text(encoding="utf-8")
        assert "boto3.client" not in source
        assert "ANTHROPIC_API_KEY" not in source


def test_importing_sprint4_providers_does_not_load_sdks():
    env = os.environ.copy()
    pythonpath = str(SHARED_DIR)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    script = """
import sys
import wilvor_ai.sprint4_master_providers as providers
assert 'boto3' not in sys.modules
assert 'botocore' not in sys.modules
assert 'anthropic' not in sys.modules
assert 'openai' not in sys.modules
assert providers.SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE is False
"""
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
