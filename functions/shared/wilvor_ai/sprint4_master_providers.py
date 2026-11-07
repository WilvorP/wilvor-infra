"""Sprint 4 Agent D: injected Master/provider composition.

This module binds the existing Bedrock Converse and Anthropic Messages
adapters, plus specialist-specific Anthropic adapters, behind a host-owned
budget and an AWS-tool rejection gate. Clients stay injected. Live model
API invocation, SDK construction, and production provider minting stay
closed.

A model may propose catalog specialist tools. It may not authorize AWS
reads or writes, mint source authenticity, or replace MA2/MA4/MA5.
"""

from __future__ import annotations

import functools
import types
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_authorized_scheduler_adapter import ScriptedTestWorker
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.model_contracts import ModelDecision, ModelDecisionKind, ProposedToolCall
from wilvor_ai.providers.master_instructions import (
    MASTER_PLANNING_INSTRUCTION_REF,
    resolve_master_instruction,
)


SPRINT4_MASTER_PROVIDERS_SCHEMA_VERSION = "wilvor.ai.sprint4.master_providers.v1"
SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE = False
SPRINT4_MODEL_AUTHORIZES_AWS_TOOLS = False
SPRINT4_MODEL_MANUFACTURES_SOURCE_AUTHENTICITY = False
SPRINT4_PRODUCTION_PROVIDER_CONSTRUCTION_AVAILABLE = False
MASTER_PLANNER_MAX_TURNS = 1
MASTER_PLANNER_MAX_TOOL_CALLS = 0

_VENDOR_SDK_MARKERS = ("boto3", "botocore", "openai")
_VENDOR_SDK_MODULES = frozenset(
    {
        "anthropic",
        "boto3",
        "botocore",
        "openai",
    }
)
_APPROVED_OFFLINE_ADAPTERS = frozenset(
    {
        ("wilvor_ai.providers.bedrock_converse", "BedrockConverseModelProvider"),
        ("wilvor_ai.providers.anthropic_messages", "AnthropicMessagesModelProvider"),
        (
            "wilvor_ai.providers.anthropic_live_ops_messages",
            "AnthropicLiveOpsMessagesProvider",
        ),
        (
            "wilvor_ai.providers.anthropic_decision_messages",
            "AnthropicDecisionMessagesProvider",
        ),
    }
)
_FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "getitem",
        "get_item",
        "query",
        "scan",
        "putitem",
        "put_item",
        "deleteitem",
        "delete_item",
        "updateitem",
        "update_item",
        "batchgetitem",
        "batch_get_item",
        "batchwriteitem",
        "batch_write_item",
        "transactwriteitems",
        "transact_write_items",
        "executestatement",
        "execute_statement",
        "sql",
        "athena",
        "glue",
        "iam",
        "sts",
        "s3",
        "dynamodb",
        "lambda",
        "assumerole",
        "assume_role",
        "invokefunction",
        "invoke_function",
        "converse",
        "messages_create",
    }
)
_FORBIDDEN_ARGUMENT_NAMES = frozenset(
    {
        "sql",
        "statement",
        "query_string",
        "filterexpression",
        "tablearn",
        "table_arn",
        "resourcearn",
        "resource_arn",
    }
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, denied: object, code: str) -> None:
    if value is not denied:
        _fail(code)


def _assert_closed_surface() -> None:
    _closed(SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE, False, "live_model_invocation_unavailable")
    _closed(SPRINT4_MODEL_AUTHORIZES_AWS_TOOLS, False, "model_aws_authorization_rejected")
    _closed(
        SPRINT4_MODEL_MANUFACTURES_SOURCE_AUTHENTICITY,
        False,
        "source_authenticity_not_established",
    )
    _closed(
        SPRINT4_PRODUCTION_PROVIDER_CONSTRUCTION_AVAILABLE,
        False,
        "production_provider_construction_unavailable",
    )


def _adapter_identity(provider: object) -> tuple[str, str]:
    return (
        getattr(type(provider), "__module__", "") or "",
        type(provider).__name__,
    )


def _reject_callable_as_provider(provider: object) -> None:
    if isinstance(
        provider,
        (types.FunctionType, types.MethodType, types.BuiltinFunctionType, functools.partial),
    ):
        _fail("model_origin_dispatch_rejected")
    if isinstance(provider, (str, bytes, bytearray, bool, int, float, type, Mapping)):
        _fail("model_origin_dispatch_rejected")


def reject_vendor_sdk_provider(provider: object) -> None:
    """Refuse boto3/Anthropic/OpenAI SDK types. Offline adapters stay allowed."""

    _reject_callable_as_provider(provider)
    if type(provider) is ScriptedTestWorker:
        _fail("model_origin_dispatch_rejected")
    module, name = _adapter_identity(provider)
    lowered = module.casefold()
    root = lowered.split(".", 1)[0]
    if root in _VENDOR_SDK_MODULES or any(
        marker == lowered or lowered.startswith(marker + ".")
        for marker in _VENDOR_SDK_MODULES
    ):
        _fail("model_origin_dispatch_rejected")
    if any(marker in lowered for marker in _VENDOR_SDK_MARKERS):
        _fail("model_origin_dispatch_rejected")
    if (module, name) in _APPROVED_OFFLINE_ADAPTERS:
        return
    if "langgraph" in lowered:
        _fail("model_origin_dispatch_rejected")


def _normalize_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().casefold().replace("-", "_")


def inspect_untrusted_tool_calls(decision: object) -> tuple[str, ...]:
    """Return proposed tool names. Presence is not host authorization."""

    calls = getattr(decision, "tool_calls", ())
    if calls in (None, ()):
        return ()
    if not isinstance(calls, tuple):
        _fail("invalid_proposed_tool_call")
    names: list[str] = []
    for item in calls:
        name = getattr(item, "name", None)
        if not isinstance(name, str) or not name.strip():
            if isinstance(item, Mapping):
                name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                _fail("invalid_proposed_tool_call")
        names.append(name)
        arguments = getattr(item, "arguments", None)
        if arguments is None and isinstance(item, Mapping):
            arguments = item.get("arguments")
        reject_aws_tool_proposal(name, arguments)
    return tuple(names)


def reject_aws_tool_proposal(name: object, arguments: object = None) -> None:
    """Models cannot authorize AWS data-plane or control-plane tools."""

    normalized = _normalize_name(name)
    if normalized in _FORBIDDEN_TOOL_NAMES:
        _fail("model_aws_authorization_rejected")
    if isinstance(arguments, Mapping):
        for key in arguments:
            if _normalize_name(key) in _FORBIDDEN_ARGUMENT_NAMES:
                _fail("model_aws_authorization_rejected")
            if isinstance(key, str) and key.casefold() in {"sql", "statement", "query_string"}:
                _fail("model_aws_authorization_rejected")


def reject_master_planner_tools(decision: object) -> None:
    """The Master planner has no tool catalog."""

    kind = getattr(decision, "kind", None)
    calls = getattr(decision, "tool_calls", ())
    if kind is ModelDecisionKind.TOOL_CALLS or calls not in (None, ()):
        inspect_untrusted_tool_calls(decision)
        _fail("master_planner_tools_rejected")


class Sprint4BoundedModelProvider:
    """Host-bounded ``complete()`` wrapper. Not a production client factory."""

    def __init__(
        self,
        inner: object,
        *,
        max_turns: int,
        max_tool_calls: int,
        allow_specialist_tools: bool,
    ) -> None:
        _assert_closed_surface()
        reject_vendor_sdk_provider(inner)
        if not callable(getattr(inner, "complete", None)):
            _fail("model_origin_dispatch_rejected")
        if type(max_turns) is not int or max_turns < 1:
            _fail("server_budget_exceeded")
        if type(max_tool_calls) is not int or max_tool_calls < 0:
            _fail("server_budget_exceeded")
        if type(allow_specialist_tools) is not bool:
            _fail("invalid_tool_policy")
        self._inner = inner
        self._max_turns = max_turns
        self._max_tool_calls = max_tool_calls
        self._allow_specialist_tools = allow_specialist_tools
        self.turns = 0
        self.tool_calls = 0
        self.rejection: str | None = None

    @property
    def inner(self) -> object:
        return self._inner

    def complete(self, turn: object) -> object:
        _assert_closed_surface()
        if self.turns >= self._max_turns:
            self.rejection = "server_budget_exceeded"
            _fail(self.rejection)
        self.turns += 1
        decision = getattr(self._inner, "complete")(turn)
        names = inspect_untrusted_tool_calls(decision)
        if names:
            if not self._allow_specialist_tools:
                self.rejection = "master_planner_tools_rejected"
                _fail(self.rejection)
            next_count = self.tool_calls + len(names)
            if next_count > self._max_tool_calls:
                self.rejection = "server_budget_exceeded"
                _fail(self.rejection)
            self.tool_calls = next_count
        if isinstance(decision, ModelDecision) and decision.kind is ModelDecisionKind.TOOL_CALLS:
            if not self._allow_specialist_tools:
                self.rejection = "master_planner_tools_rejected"
                _fail(self.rejection)
        return decision


@dataclass(frozen=True)
class Sprint4MasterProviderBinding:
    """Record of an injected adapter. Possession is not authenticity."""

    adapter_module: str
    adapter_name: str
    approved_offline_adapter: bool
    live_model_invocation: bool = False
    model_authorizes_aws_tools: bool = False
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    schema_version: str = SPRINT4_MASTER_PROVIDERS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT4_MASTER_PROVIDERS_SCHEMA_VERSION:
            _fail("invalid_schema_version")
        _closed(self.live_model_invocation, False, "live_model_invocation_unavailable")
        _closed(self.model_authorizes_aws_tools, False, "model_aws_authorization_rejected")
        _closed(
            self.source_authenticity,
            MasterSourceAuthenticity.NOT_ESTABLISHED,
            "source_authenticity_not_established",
        )
        identity = (self.adapter_module, self.adapter_name)
        if self.approved_offline_adapter is not (identity in _APPROVED_OFFLINE_ADAPTERS):
            _fail("unapproved_provider_adapter")


def bind_injected_master_provider(
    provider: object,
    *,
    max_turns: int,
    max_tool_calls: int,
    allow_specialist_tools: bool,
) -> Sprint4BoundedModelProvider:
    """Wrap one injected ``complete()`` provider. No SDK client is constructed."""

    _assert_closed_surface()
    reject_vendor_sdk_provider(provider)
    bind_provider_identity(provider)
    return Sprint4BoundedModelProvider(
        provider,
        max_turns=max_turns,
        max_tool_calls=max_tool_calls,
        allow_specialist_tools=allow_specialist_tools,
    )


def bind_specialist_model_provider(provider: object, specialist: Any) -> Sprint4BoundedModelProvider:
    """Bind a specialist provider to the server turn and tool ceilings."""

    _executions, turns, calls = server_read_budget(specialist)
    if _executions < 1:
        _fail("server_budget_exceeded")
    return bind_injected_master_provider(
        provider,
        max_turns=turns,
        max_tool_calls=calls,
        allow_specialist_tools=True,
    )


def bind_master_planning_provider(provider: object) -> Sprint4BoundedModelProvider:
    """Bind a planner. The planner may not emit tools."""

    return bind_injected_master_provider(
        provider,
        max_turns=MASTER_PLANNER_MAX_TURNS,
        max_tool_calls=MASTER_PLANNER_MAX_TOOL_CALLS,
        allow_specialist_tools=False,
    )


def bind_provider_identity(provider: object) -> Sprint4MasterProviderBinding:
    """Describe the adapter. Description is not production construction."""

    reject_vendor_sdk_provider(provider)
    module, name = _adapter_identity(provider)
    return Sprint4MasterProviderBinding(
        adapter_module=module,
        adapter_name=name,
        approved_offline_adapter=(module, name) in _APPROVED_OFFLINE_ADAPTERS,
    )


def refuse_live_model_invocation(*_args: object, **_kwargs: object) -> None:
    """Sprint 4 does not call a real model API."""

    _fail("live_model_invocation_unavailable")


def refuse_production_provider_construction(*_args: object, **_kwargs: object) -> None:
    """Production Bedrock/Anthropic clients stay unwired."""

    _fail("production_provider_construction_unavailable")


def master_planning_instruction() -> str:
    return resolve_master_instruction(MASTER_PLANNING_INSTRUCTION_REF)


def refuse_aws_tool_proposal(name: object, arguments: object = None) -> None:
    reject_aws_tool_proposal(name, arguments)


def assert_sprint4_provider_surface_closed() -> None:
    """Keep live invocation, AWS authorization, and authenticity closed."""

    _assert_closed_surface()
    try:
        refuse_live_model_invocation()
    except ContractValidationError as exc:
        if exc.errors != ("live_model_invocation_unavailable",):
            raise
    else:
        _fail("live_model_invocation_unavailable")
    try:
        refuse_production_provider_construction()
    except ContractValidationError as exc:
        if exc.errors != ("production_provider_construction_unavailable",):
            raise
    else:
        _fail("production_provider_construction_unavailable")


__all__ = [
    "MASTER_PLANNER_MAX_TOOL_CALLS",
    "MASTER_PLANNER_MAX_TURNS",
    "SPRINT4_LIVE_MODEL_INVOCATION_AVAILABLE",
    "SPRINT4_MASTER_PROVIDERS_SCHEMA_VERSION",
    "SPRINT4_MODEL_AUTHORIZES_AWS_TOOLS",
    "SPRINT4_MODEL_MANUFACTURES_SOURCE_AUTHENTICITY",
    "SPRINT4_PRODUCTION_PROVIDER_CONSTRUCTION_AVAILABLE",
    "Sprint4BoundedModelProvider",
    "Sprint4MasterProviderBinding",
    "assert_sprint4_provider_surface_closed",
    "bind_injected_master_provider",
    "bind_master_planning_provider",
    "bind_provider_identity",
    "bind_specialist_model_provider",
    "inspect_untrusted_tool_calls",
    "master_planning_instruction",
    "refuse_aws_tool_proposal",
    "refuse_live_model_invocation",
    "refuse_production_provider_construction",
    "reject_aws_tool_proposal",
    "reject_master_planner_tools",
    "reject_vendor_sdk_provider",
]
