"""Offline Anthropic Messages adapter for the Live Ops Specialist.

This module maps ``LiveOpsModelTurnRequest`` values to Anthropic Messages
kwargs and Messages mappings to ``LiveOpsModelDecision``. It does not
import the Anthropic SDK, read API keys, construct a network client,
execute tools, or retain a vendor transcript.

``MAX_OUTPUT_TOKENS`` is 4096. A valid claim set can still exceed that
cap. ``stop_reason`` ``max_tokens`` is a closed malformed-output failure,
not a repaired decision. ``MAX_REQUEST_BYTES`` is a UTF-8 size check of
the complete outgoing payload, not a provider token count and not a
license to drop limitations. Temperature is 0. Sampling temperature does
not make model choices deterministic and is not the safety boundary.

LO3C provider exceptions are imported only inside the functions that
raise them, so importing this module does not load the specialist.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import ContractValidationError, ToolInputField, ToolInputValueType
from wilvor_ai.live_ops_model_contracts import (
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsModelTurnRequest,
    LiveOpsProviderFailureKind,
)
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.providers.live_ops_instructions import (
    LIVE_OPS_TERMINAL_TRANSPORT_VERSION,
    LiveOpsInstructionError,
    live_ops_terminal_transport_schema,
    resolve_live_ops_instruction,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES, ToolSchema


DEFAULT_MODEL_ID = "claude-sonnet-4-6"
APPROVED_MODEL_IDS = frozenset({DEFAULT_MODEL_ID})
MAX_OUTPUT_TOKENS = 4096
TEMPERATURE = 0
MAX_REQUEST_BYTES = 120_000
REFUSAL_CODE = "refusal"

_JSON_TYPES = {
    ToolInputValueType.STRING: "string",
    ToolInputValueType.INTEGER: "integer",
    ToolInputValueType.NUMBER: "number",
    ToolInputValueType.BOOLEAN: "boolean",
    ToolInputValueType.OBJECT: "object",
    ToolInputValueType.ARRAY: "array",
}
_ALLOWED_BLOCK_TYPES = frozenset({"text", "tool_use"})
_TERMINAL_KINDS = frozenset(
    {
        LiveOpsModelDecisionKind.FINAL_CLAIMS.value,
        LiveOpsModelDecisionKind.UNSUPPORTED.value,
        LiveOpsModelDecisionKind.REFUSAL.value,
    }
)
_TRANSPORT_OUTER_KEYS = frozenset({"transport_version", "kind", "decision_json"})
_TRANSPORT_NAMES = frozenset(
    {
        "APIConnectionError",
        "APIError",
        "APIStatusError",
        "AuthenticationError",
        "BadRequestError",
        "ConnectionError",
        "InternalServerError",
        "NotFoundError",
        "OSError",
        "PermissionDeniedError",
        "RateLimitError",
    }
)
_CONTEXT_CODES = frozenset(
    {
        "context_length_exceeded",
        "prompt_too_long",
        "request_too_large",
    }
)


class AnthropicLiveOpsMessagesClient(Protocol):
    """Injected Messages client. This adapter does not construct one."""

    def messages_create(self, **kwargs: Any) -> Mapping[str, object]: ...


def _failure(kind: LiveOpsProviderFailureKind) -> Exception:
    from wilvor_ai.live_ops_specialist import LiveOpsProviderError

    return LiveOpsProviderError(kind)


def _timeout() -> Exception:
    from wilvor_ai.live_ops_specialist import LiveOpsProviderTimeoutError

    return LiveOpsProviderTimeoutError()


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def live_ops_messages_payload_size(payload: Mapping[str, object]) -> int:
    """UTF-8 size of one complete Messages payload.

    This is a request-size safeguard. It is not a token count.
    """

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return len(encoded.encode("utf-8"))


def live_ops_tool_def_from_schema(schema: ToolSchema) -> dict[str, object]:
    """Convert one provider-neutral ToolSchema into a strict Anthropic tool."""

    if not isinstance(schema, ToolSchema):
        raise TypeError("invalid_tool_schema")
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in schema.input_fields:
        if not isinstance(field, ToolInputField):
            raise TypeError("invalid_tool_input_field")
        if field.name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            raise _failure(LiveOpsProviderFailureKind.TRANSPORT_OR_API)
        try:
            json_type = _JSON_TYPES[field.value_type]
        except KeyError:
            raise _failure(LiveOpsProviderFailureKind.TRANSPORT_OR_API) from None
        property_schema: dict[str, object] = {"type": json_type}
        if field.value_type is ToolInputValueType.ARRAY:
            property_schema["items"] = {"type": "string"}
        if field.description is not None:
            property_schema["description"] = field.description
        properties[field.name] = property_schema
        if field.required:
            required.append(field.name)
    return {
        "name": schema.name,
        "description": schema.description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


def _user_json(payload: Mapping[str, object]) -> dict[str, object]:
    return {
        "role": "user",
        "content": [{"type": "text", "text": _stable_json(payload)}],
    }


def build_live_ops_messages_kwargs(
    request: LiveOpsModelTurnRequest,
    model_id: str,
) -> dict[str, object]:
    """Reconstruct one Messages request. Unknown instructions fail first."""

    if not isinstance(request, LiveOpsModelTurnRequest):
        raise TypeError("request must be a LiveOpsModelTurnRequest")
    system = resolve_live_ops_instruction(request.instruction_ref)
    visible = request.to_dict()
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": request.user_text}],
        }
    ]
    if "prior_turns" in visible:
        messages.append(_user_json({"untrusted_prior_turns": visible["prior_turns"]}))
    untrusted_hints: dict[str, object] = {}
    if "selector_hint_items" in visible:
        untrusted_hints["untrusted_selector_hint_items"] = visible["selector_hint_items"]
    if "entity_hint_items" in visible:
        untrusted_hints["untrusted_entity_hint_items"] = visible["entity_hint_items"]
    if untrusted_hints:
        messages.append(_user_json(untrusted_hints))
    if "tool_results" in visible:
        messages.append(_user_json({"tool_results": visible["tool_results"]}))
    if "validation_feedback" in visible:
        messages.append(
            _user_json({"validation_feedback": visible["validation_feedback"]})
        )
    return {
        "model": model_id,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": TEMPERATURE,
        "system": system,
        "messages": messages,
        "tools": [live_ops_tool_def_from_schema(item) for item in request.tools],
        "tool_choice": {"type": "auto"},
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": live_ops_terminal_transport_schema(),
            }
        },
    }


def _malformed() -> Exception:
    return _failure(LiveOpsProviderFailureKind.MALFORMED_OUTPUT)


def _context_length() -> Exception:
    return _failure(LiveOpsProviderFailureKind.CONTEXT_LENGTH)


def _require_mapping(value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _malformed()
    return value


def _validate_envelope(response: Mapping[str, Any], expected_model_id: str) -> None:
    if response.get("type") != "message":
        raise _malformed()
    if response.get("role") != "assistant":
        raise _malformed()
    if response.get("model") != expected_model_id:
        raise _malformed()
    if "id" in response:
        message_id = response.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise _malformed()


def _content_blocks(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_content = response.get("content")
    if not isinstance(raw_content, list):
        raise _malformed()
    blocks: list[Mapping[str, Any]] = []
    for item in raw_content:
        block = _require_mapping(item)
        block_type = block.get("type")
        if block_type not in _ALLOWED_BLOCK_TYPES:
            raise _malformed()
        if block_type == "text" and "text" not in block:
            raise _malformed()
        blocks.append(block)
    return blocks


def _parse_json_object(text: object) -> Mapping[str, Any] | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(loaded, Mapping):
        return loaded
    return None


def _looks_like_terminal(value: Mapping[str, Any]) -> bool:
    return value.get("kind") in _TERMINAL_KINDS


def _decision_from_mapping(data: Mapping[str, Any]) -> LiveOpsModelDecision:
    try:
        decision = LiveOpsModelDecision.from_dict(data)
    except ContractValidationError:
        raise _malformed() from None
    if decision.kind.value not in _TERMINAL_KINDS:
        raise _malformed()
    return decision


def _tool_calls_from_blocks(
    blocks: list[Mapping[str, Any]],
) -> tuple[ProposedToolCall, ...]:
    calls: list[ProposedToolCall] = []
    for block in blocks:
        if block.get("type") != "tool_use":
            continue
        tool_use_id = block.get("id")
        if not isinstance(tool_use_id, str) or not tool_use_id.strip():
            raise _malformed()
        name = block.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _malformed()
        arguments = block.get("input")
        if not isinstance(arguments, Mapping):
            raise _malformed()
        try:
            calls.append(ProposedToolCall(name=name, arguments=dict(arguments)))
        except ContractValidationError:
            raise _malformed() from None
    return tuple(calls)


def _text_json_objects(blocks: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    objects: list[Mapping[str, Any]] = []
    for block in blocks:
        if block.get("type") != "text":
            continue
        parsed = _parse_json_object(block.get("text"))
        if parsed is not None:
            objects.append(parsed)
    return objects


def _load_json_value(text: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise _malformed() from None


def _end_turn_decision(blocks: list[Mapping[str, Any]]) -> LiveOpsModelDecision:
    text_blocks = [block for block in blocks if block.get("type") == "text"]
    if len(text_blocks) != 1:
        raise _malformed()
    text = text_blocks[0].get("text")
    if not isinstance(text, str):
        raise _malformed()
    loaded = _load_json_value(text)
    if not isinstance(loaded, dict):
        raise _malformed()
    if set(loaded) != _TRANSPORT_OUTER_KEYS:
        raise _malformed()
    if loaded.get("transport_version") != LIVE_OPS_TERMINAL_TRANSPORT_VERSION:
        raise _malformed()
    if loaded.get("kind") not in _TERMINAL_KINDS:
        raise _malformed()
    decision_json = loaded.get("decision_json")
    if not isinstance(decision_json, str) or not decision_json.strip():
        raise _malformed()
    inner = _load_json_value(decision_json)
    if not isinstance(inner, dict):
        raise _malformed()
    decision = _decision_from_mapping(inner)
    if loaded["kind"] != decision.kind.value:
        raise _malformed()
    return decision


def parse_live_ops_messages_response(
    response: object,
    expected_model_id: str,
) -> LiveOpsModelDecision:
    """Map one Messages response to exactly one LiveOpsModelDecision."""

    payload = _require_mapping(response)
    _validate_envelope(payload, expected_model_id)
    stop_reason = payload.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason:
        raise _malformed()
    if stop_reason == "refusal":
        if "content" in payload:
            blocks = _content_blocks(payload)
            if any(block.get("type") == "tool_use" for block in blocks):
                raise _malformed()
        return LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.REFUSAL,
            refusal_code=REFUSAL_CODE,
        )
    if stop_reason == "model_context_window_exceeded":
        raise _context_length()
    if stop_reason == "max_tokens":
        raise _malformed()
    if stop_reason not in {"tool_use", "end_turn"}:
        raise _malformed()

    blocks = _content_blocks(payload)
    tool_calls = _tool_calls_from_blocks(blocks)
    if stop_reason == "tool_use":
        if not tool_calls:
            raise _malformed()
        for candidate in _text_json_objects(blocks):
            if _looks_like_terminal(candidate):
                raise _malformed()
        return LiveOpsModelDecision(
            kind=LiveOpsModelDecisionKind.TOOL_CALLS,
            tool_calls=tool_calls,
        )
    if tool_calls:
        raise _malformed()
    return _end_turn_decision(blocks)


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _error_codes(exc: BaseException) -> tuple[str, str]:
    body = getattr(exc, "body", None)
    if not isinstance(body, Mapping):
        return "", ""
    error = body.get("error")
    source = error if isinstance(error, Mapping) else body
    if not isinstance(source, Mapping):
        return "", ""
    error_type = source.get("type")
    error_code = source.get("code")
    return (
        error_type if isinstance(error_type, str) else "",
        error_code if isinstance(error_code, str) else "",
    )


def _is_context_failure(exc: BaseException) -> bool:
    if "context" in type(exc).__name__.casefold():
        return True
    error_type, error_code = _error_codes(exc)
    blob = f"{error_type} {error_code}".casefold()
    return any(code in blob for code in _CONTEXT_CODES)


def _is_transport_failure(exc: BaseException) -> bool:
    name = type(exc).__name__
    if name in _TRANSPORT_NAMES or name.endswith("ConnectionError"):
        return True
    status = _status_code(exc)
    if status is None:
        return False
    return status in {401, 403, 429} or status >= 500


def _classify_client_exception(exc: BaseException) -> BaseException:
    if "Timeout" in type(exc).__name__:
        return _timeout()
    if _is_context_failure(exc):
        return _context_length()
    if _is_transport_failure(exc):
        return _failure(LiveOpsProviderFailureKind.TRANSPORT_OR_API)
    return exc


class AnthropicLiveOpsMessagesProvider:
    """Stateless Live Ops Messages provider. The client is injected."""

    def __init__(
        self,
        *,
        client: AnthropicLiveOpsMessagesClient,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        if client is None or not callable(getattr(client, "messages_create", None)):
            raise TypeError(
                "client must implement AnthropicLiveOpsMessagesClient.messages_create"
            )
        if model_id not in APPROVED_MODEL_IDS:
            raise ValueError("unknown_model_id")
        self._client = client
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(self, request: LiveOpsModelTurnRequest) -> LiveOpsModelDecision:
        """Return exactly one LiveOpsModelDecision for this turn.

        Performs one ``messages_create`` call. A request that fails the
        instruction or byte gate does not call the client.
        """

        if not isinstance(request, LiveOpsModelTurnRequest):
            raise TypeError("request must be a LiveOpsModelTurnRequest")
        try:
            kwargs = build_live_ops_messages_kwargs(request, self._model_id)
        except LiveOpsInstructionError:
            raise _failure(LiveOpsProviderFailureKind.TRANSPORT_OR_API) from None
        if live_ops_messages_payload_size(kwargs) > MAX_REQUEST_BYTES:
            raise _failure(LiveOpsProviderFailureKind.CONTEXT_LENGTH)
        try:
            response = self._client.messages_create(**kwargs)
        except Exception as exc:
            classified = _classify_client_exception(exc)
            if classified is not exc:
                raise classified from None
            raise
        return parse_live_ops_messages_response(response, self._model_id)


__all__ = [
    "APPROVED_MODEL_IDS",
    "DEFAULT_MODEL_ID",
    "MAX_OUTPUT_TOKENS",
    "MAX_REQUEST_BYTES",
    "REFUSAL_CODE",
    "TEMPERATURE",
    "AnthropicLiveOpsMessagesClient",
    "AnthropicLiveOpsMessagesProvider",
    "build_live_ops_messages_kwargs",
    "live_ops_messages_payload_size",
    "live_ops_tool_def_from_schema",
    "parse_live_ops_messages_response",
]
