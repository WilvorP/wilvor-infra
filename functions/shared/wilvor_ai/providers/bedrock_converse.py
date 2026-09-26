"""Offline Amazon Bedrock Converse adapter for Phase 3A.4.

This module maps provider-neutral ModelTurnRequest values to Converse
kwargs and Converse mappings to ModelDecision. It does not import boto3,
create AWS clients, execute tools, or retain vendor conversation state.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import (
    ContractValidationError,
    ToolInputField,
    ToolInputValueType,
)
from wilvor_ai.model_contracts import (
    MODEL_DECISION_SCHEMA_VERSION,
    ModelDecision,
    ModelDecisionKind,
    ModelTurnRequest,
    ProposedToolCall,
)
from wilvor_ai.providers.errors import (
    ModelProviderContextLengthError,
    ModelProviderError,
    ModelProviderMalformedDecisionError,
)
from wilvor_ai.providers.instructions import resolve_instruction
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES, ToolSchema


DEFAULT_MODEL_ID = "us.anthropic.claude-sonnet-4-6"
APPROVED_MODEL_IDS = frozenset({DEFAULT_MODEL_ID})
MAX_TOKENS = 2048
TEMPERATURE = 0
TERMINAL_SCHEMA_NAME = "wilvor_model_decision_v1"
TERMINAL_SCHEMA_DESCRIPTION = (
    "Wilvor terminal ModelDecision. TOOL_CALLS use native toolUse blocks."
)
REFUSAL_CONTENT_FILTERED = "content_filtered"
REFUSAL_GUARDRAIL_INTERVENED = "guardrail_intervened"
_TERMINAL_KINDS = frozenset(
    {
        ModelDecisionKind.FINAL_CLAIMS.value,
        ModelDecisionKind.UNSUPPORTED.value,
        ModelDecisionKind.REFUSAL.value,
    }
)
_JSON_TYPES = {
    ToolInputValueType.STRING: "string",
    ToolInputValueType.INTEGER: "integer",
    ToolInputValueType.NUMBER: "number",
    ToolInputValueType.BOOLEAN: "boolean",
    ToolInputValueType.OBJECT: "object",
    ToolInputValueType.ARRAY: "array",
}
_CONTENT_KEYS = frozenset({"text", "toolUse"})


class BedrockConverseClient(Protocol):
    """Injected Converse client. 3A.4 does not construct AWS SDK clients."""

    def converse(self, **kwargs: Any) -> Mapping[str, object]: ...


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _json_type(value_type: ToolInputValueType) -> str:
    try:
        return _JSON_TYPES[value_type]
    except KeyError as exc:
        raise ModelProviderError("unsupported_field_value_type") from exc


def tool_spec_from_schema(schema: ToolSchema) -> dict[str, object]:
    """Convert one provider-neutral ToolSchema into a Bedrock toolSpec."""

    if not isinstance(schema, ToolSchema):
        raise ModelProviderError("invalid_tool_schema")
    properties: dict[str, object] = {}
    required: list[str] = []
    for field in schema.input_fields:
        if not isinstance(field, ToolInputField):
            raise ModelProviderError("invalid_tool_input_field")
        if field.name.casefold() in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            raise ModelProviderError("forbidden_tool_schema_field")
        property_schema: dict[str, object] = {"type": _json_type(field.value_type)}
        if field.description is not None:
            property_schema["description"] = field.description
        properties[field.name] = property_schema
        if field.required:
            required.append(field.name)
    return {
        "toolSpec": {
            "name": schema.name,
            "description": schema.description,
            "strict": True,
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                }
            },
        }
    }


def tool_specs_from_schemas(schemas: tuple[ToolSchema, ...]) -> list[dict[str, object]]:
    return [tool_spec_from_schema(item) for item in schemas]


def _claim_schemas() -> list[dict[str, object]]:
    string = {"type": "string"}
    integer = {"type": "integer"}
    return [
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "metric_id", "value"],
            "properties": {
                "kind": {"const": "EXACT_COUNT"},
                "tool_call_id": string,
                "metric_id": string,
                "value": integer,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "metric_id", "minimum_value"],
            "properties": {
                "kind": {"const": "LOWER_BOUND_COUNT"},
                "tool_call_id": string,
                "metric_id": string,
                "minimum_value": integer,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id"],
            "properties": {
                "kind": {"const": "VERIFIED_ZERO"},
                "tool_call_id": string,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "start_utc", "end_utc"],
            "properties": {
                "kind": {"const": "HISTORICAL_WINDOW"},
                "tool_call_id": string,
                "start_utc": string,
                "end_utc": string,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "record_id"],
            "properties": {
                "kind": {"const": "RECORD_IDENTITY"},
                "tool_call_id": string,
                "record_id": string,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "limitation_code"],
            "properties": {
                "kind": {"const": "LIMITATION"},
                "tool_call_id": string,
                "limitation_code": string,
            },
        },
        {
            "type": "object",
            "additionalProperties": False,
            "required": ["kind", "tool_call_id", "error_or_coverage_code"],
            "properties": {
                "kind": {"const": "UNAVAILABLE"},
                "tool_call_id": string,
                "error_or_coverage_code": string,
            },
        },
    ]


def terminal_decision_json_schema() -> dict[str, object]:
    """Adapter-owned terminal JSON Schema. Does not include TOOL_CALLS."""

    version = {"const": MODEL_DECISION_SCHEMA_VERSION}
    return {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "kind", "claims"],
                "properties": {
                    "schema_version": version,
                    "kind": {"const": ModelDecisionKind.FINAL_CLAIMS.value},
                    "claims": {
                        "type": "array",
                        "items": {"anyOf": _claim_schemas()},
                    },
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "kind", "unsupported_reason"],
                "properties": {
                    "schema_version": version,
                    "kind": {"const": ModelDecisionKind.UNSUPPORTED.value},
                    "unsupported_reason": {
                        "enum": [item.value for item in UnsupportedReason]
                    },
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "kind"],
                "properties": {
                    "schema_version": version,
                    "kind": {"const": ModelDecisionKind.REFUSAL.value},
                    "refusal_code": {"type": "string"},
                },
            },
        ]
    }


def serialize_terminal_schema() -> str:
    return _stable_json(terminal_decision_json_schema())


def build_converse_kwargs(request: ModelTurnRequest, model_id: str) -> dict[str, object]:
    """Reconstruct one Converse request from a provider-neutral snapshot."""

    if not isinstance(request, ModelTurnRequest):
        raise TypeError("request must be a ModelTurnRequest")
    messages: list[dict[str, object]] = [
        {"role": "user", "content": [{"text": request.user_text}]},
    ]
    if request.tool_results:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "text": _stable_json(
                            {
                                "tool_results": [
                                    item.to_dict() for item in request.tool_results
                                ]
                            }
                        )
                    }
                ],
            }
        )
    if request.validation_feedback is not None:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "text": _stable_json(
                            {
                                "validation_feedback": (
                                    request.validation_feedback.to_dict()
                                )
                            }
                        )
                    }
                ],
            }
        )
    return {
        "modelId": model_id,
        "system": [{"text": resolve_instruction(request.instruction_ref)}],
        "messages": messages,
        "inferenceConfig": {
            "temperature": TEMPERATURE,
            "maxTokens": MAX_TOKENS,
        },
        "toolConfig": {
            "tools": tool_specs_from_schemas(request.tools),
            "toolChoice": {"auto": {}},
        },
        "outputConfig": {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": serialize_terminal_schema(),
                        "name": TERMINAL_SCHEMA_NAME,
                        "description": TERMINAL_SCHEMA_DESCRIPTION,
                    }
                },
            }
        },
    }


def _malformed(code: str) -> ModelProviderMalformedDecisionError:
    return ModelProviderMalformedDecisionError(code)


def _require_mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _malformed(code)
    return value


def _content_blocks(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    output = response.get("output")
    if "output" not in response:
        raise _malformed("missing_output")
    output_map = _require_mapping(output, "missing_output")
    if "message" not in output_map:
        raise _malformed("missing_message")
    message = _require_mapping(output_map.get("message"), "missing_message")
    raw_content = message.get("content")
    if not isinstance(raw_content, list):
        raise _malformed("malformed_content")
    blocks: list[Mapping[str, Any]] = []
    for item in raw_content:
        block = _require_mapping(item, "malformed_content")
        if "json" in block:
            raise _malformed("malformed_content")
        present = _CONTENT_KEYS.intersection(block)
        if len(present) != 1:
            raise _malformed("malformed_content")
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


def _looks_like_terminal_decision(value: Mapping[str, Any]) -> bool:
    return value.get("kind") in _TERMINAL_KINDS


def _model_decision_from_mapping(data: Mapping[str, Any]) -> ModelDecision:
    try:
        decision = ModelDecision.from_dict(data)
    except ContractValidationError as exc:
        raise _malformed("invalid_model_decision") from exc
    if decision.kind.value not in _TERMINAL_KINDS:
        raise _malformed("invalid_model_decision")
    return decision


def _tool_calls_from_blocks(
    blocks: list[Mapping[str, Any]],
) -> tuple[ProposedToolCall, ...]:
    calls: list[ProposedToolCall] = []
    for block in blocks:
        if "toolUse" not in block:
            continue
        tool_use = _require_mapping(block.get("toolUse"), "malformed_tool_use")
        if "toolUseId" not in tool_use:
            raise _malformed("malformed_tool_use")
        tool_use_id = tool_use.get("toolUseId")
        if not isinstance(tool_use_id, str) or not tool_use_id.strip():
            raise _malformed("malformed_tool_use")
        arguments = tool_use.get("input")
        if not isinstance(arguments, Mapping):
            raise _malformed("malformed_tool_use")
        try:
            calls.append(
                ProposedToolCall(
                    name=tool_use.get("name"),
                    arguments=dict(arguments),
                )
            )
        except ContractValidationError as exc:
            raise _malformed("invalid_proposed_tool_call") from exc
    return tuple(calls)


def _text_json_objects(blocks: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    objects: list[Mapping[str, Any]] = []
    for block in blocks:
        if "text" not in block:
            continue
        parsed = _parse_json_object(block.get("text"))
        if parsed is not None:
            objects.append(parsed)
    return objects


def _end_turn_decision(blocks: list[Mapping[str, Any]]) -> ModelDecision:
    text_blocks = [block for block in blocks if "text" in block]
    if len(text_blocks) != 1:
        raise _malformed("malformed_terminal_json")
    parsed = _parse_json_object(text_blocks[0].get("text"))
    if parsed is None:
        raise _malformed("malformed_terminal_json")
    return _model_decision_from_mapping(parsed)


def parse_converse_response(response: object) -> ModelDecision:
    """Map one Bedrock Converse mapping to exactly one ModelDecision."""

    payload = _require_mapping(response, "malformed_content")
    stop_reason = payload.get("stopReason")
    if not isinstance(stop_reason, str) or not stop_reason:
        raise _malformed("unexpected_stop_reason")

    if stop_reason == "content_filtered":
        return ModelDecision(
            kind=ModelDecisionKind.REFUSAL,
            refusal_code=REFUSAL_CONTENT_FILTERED,
        )
    if stop_reason == "guardrail_intervened":
        return ModelDecision(
            kind=ModelDecisionKind.REFUSAL,
            refusal_code=REFUSAL_GUARDRAIL_INTERVENED,
        )
    if stop_reason == "model_context_window_exceeded":
        raise ModelProviderContextLengthError("model_context_window_exceeded")
    if stop_reason == "max_tokens":
        raise _malformed("max_tokens")
    if stop_reason == "malformed_model_output":
        raise _malformed("malformed_model_output")
    if stop_reason == "malformed_tool_use":
        raise _malformed("malformed_tool_use")
    if stop_reason == "stop_sequence":
        raise _malformed("stop_sequence_not_configured")
    if stop_reason not in {"tool_use", "end_turn"}:
        raise _malformed("unexpected_stop_reason")

    blocks = _content_blocks(payload)
    tool_calls = _tool_calls_from_blocks(blocks)

    if stop_reason == "tool_use":
        if not tool_calls:
            raise _malformed("tool_use_without_tool_use_blocks")
        for candidate in _text_json_objects(blocks):
            if _looks_like_terminal_decision(candidate):
                raise _malformed("contradictory_terminal_decision")
        return ModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=tool_calls,
        )

    if tool_calls:
        raise _malformed("end_turn_contains_tool_use")
    return _end_turn_decision(blocks)


class BedrockConverseModelProvider:
    """Stateless Bedrock Converse ModelProvider. Client is injected."""

    def __init__(
        self,
        *,
        client: BedrockConverseClient,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        if client is None or not callable(getattr(client, "converse", None)):
            raise TypeError("client must implement BedrockConverseClient.converse")
        if model_id not in APPROVED_MODEL_IDS:
            raise ValueError("unknown_model_id")
        self._client = client
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(self, request: ModelTurnRequest) -> ModelDecision:
        """Return exactly one ModelDecision for this reconstructed turn."""

        kwargs = build_converse_kwargs(request, self._model_id)
        response = self._client.converse(**kwargs)
        return parse_converse_response(response)


__all__ = [
    "APPROVED_MODEL_IDS",
    "DEFAULT_MODEL_ID",
    "MAX_TOKENS",
    "REFUSAL_CONTENT_FILTERED",
    "REFUSAL_GUARDRAIL_INTERVENED",
    "TEMPERATURE",
    "TERMINAL_SCHEMA_DESCRIPTION",
    "TERMINAL_SCHEMA_NAME",
    "BedrockConverseClient",
    "BedrockConverseModelProvider",
    "build_converse_kwargs",
    "parse_converse_response",
    "serialize_terminal_schema",
    "terminal_decision_json_schema",
    "tool_spec_from_schema",
    "tool_specs_from_schemas",
]
