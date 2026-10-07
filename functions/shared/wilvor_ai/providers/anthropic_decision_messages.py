"""Offline Anthropic Messages adapter for the Decision Expert.

This module maps DecisionModelTurnRequest values to Anthropic Messages
kwargs and Messages mappings to DecisionModelDecision. It does not import
the Anthropic SDK, the Historical provider, API keys, or a network client.
It does not execute tools or retain a vendor transcript.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    ToolInputField,
    ToolInputValueType,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import (
    DecisionClaimKind,
    PersistedAssessmentStatus,
    PersistedCandidateCollectionReason,
    PersistedCapabilityEvidenceStatus,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEvaluationState,
    DecisionLimitationCode,
    DecisionReportedLinkState,
    PersistedEvaluationScope,
    RecommendationActionType,
    StoredRiskLevel,
)
from wilvor_ai.decision_model_contracts import (
    DECISION_MODEL_DECISION_SCHEMA_VERSION,
    DecisionModelDecision,
    DecisionModelTurnRequest,
    DecisionUnsupportedReason,
)
from wilvor_ai.model_contracts import ModelDecisionKind, ProposedToolCall
from wilvor_ai.providers.decision_instructions import resolve_decision_instruction
from wilvor_ai.providers.errors import (
    ModelProviderContextLengthError,
    ModelProviderError,
    ModelProviderMalformedDecisionError,
)
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES, ToolSchema


DEFAULT_MODEL_ID = "claude-sonnet-4-6"
APPROVED_MODEL_IDS = frozenset({DEFAULT_MODEL_ID})
MAX_TOKENS = 2048
TEMPERATURE = 0
REFUSAL_PROVIDER = "refusal"

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
_ALLOWED_BLOCK_TYPES = frozenset({"text", "tool_use"})
_STRING = {"type": "string"}
_INTEGER = {"type": "integer"}
_STRING_ARRAY = {"type": "array", "items": {"type": "string"}}
_CURRENT = {"const": TemporalScope.CURRENT.value}
_PERSISTED = {"const": TemporalScope.PERSISTED.value}
_CURRENT_OR_PERSISTED = {
    "enum": [TemporalScope.CURRENT.value, TemporalScope.PERSISTED.value]
}


class AnthropicDecisionMessagesClient(Protocol):
    """Injected Messages client. This adapter does not construct one."""

    def messages_create(self, **kwargs: Any) -> Mapping[str, object]: ...


def _stable_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _enum_schema(enum_type: type) -> dict[str, object]:
    return {"enum": [item.value for item in enum_type]}


def _nullable(schema: dict[str, object]) -> dict[str, object]:
    return {"anyOf": [schema, {"type": "null"}]}


def _claim(
    kind: DecisionClaimKind,
    properties: dict[str, object],
) -> dict[str, object]:
    fields = {"kind": {"const": kind.value}, **properties}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(fields),
        "properties": fields,
    }


def _claim_schemas() -> list[dict[str, object]]:
    """Exact DE0 claim objects. Python contracts remain the stronger check."""

    return [
        _claim(
            DecisionClaimKind.AIRCRAFT_IDENTITY,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "aircraft_id": _STRING,
            },
        ),
        _claim(
            DecisionClaimKind.EVALUATION_STATE,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "evaluation_state": _enum_schema(DecisionEvaluationState),
            },
        ),
        _claim(
            DecisionClaimKind.ENCOUNTER_SET,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "encounter_ids": _STRING_ARRAY,
            },
        ),
        _claim(
            DecisionClaimKind.RISK_ABSENT,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "encounter_id": _nullable(_STRING),
            },
        ),
        _claim(
            DecisionClaimKind.RISK_PRESENT,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "encounter_id": _nullable(_STRING),
                "risk_id": _STRING,
                "risk_level": _enum_schema(StoredRiskLevel),
                "risk_score": _INTEGER,
            },
        ),
        _claim(
            DecisionClaimKind.RECOMMENDATION_SET,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "recommendation_ids": _STRING_ARRAY,
                "absence_state": _enum_schema(DecisionReportedLinkState),
            },
        ),
        _claim(
            DecisionClaimKind.RECOMMENDATION_ACTION,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "recommendation_id": _STRING,
                "primary_action_type": _enum_schema(RecommendationActionType),
                "advisory_authority": {
                    "const": DecisionAdvisoryAuthority.ADVISORY_ONLY.value
                },
            },
        ),
        _claim(
            DecisionClaimKind.CHAIN_GAP,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "chain_gap": _enum_schema(DecisionChainGap),
            },
        ),
        _claim(
            DecisionClaimKind.CAPABILITY_GAP,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "capability_gap": _enum_schema(DecisionCapabilityGap),
            },
        ),
        _claim(
            DecisionClaimKind.PERSISTED_EVALUATION,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _PERSISTED,
                "recommendation_id": _STRING,
                "airport_evaluation_id": _nullable(_STRING),
                "evaluation_scope": {
                    "const": PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE.value
                },
            },
        ),
        _claim(
            DecisionClaimKind.PERSISTED_CANDIDATE,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _PERSISTED,
                "recommendation_id": _STRING,
                "airport_id": _STRING,
                "airport_assessment_id": _STRING,
                "assessment_status": _enum_schema(PersistedAssessmentStatus),
                "route_safety_status": _enum_schema(PersistedCapabilityEvidenceStatus),
                "runway_evidence_status": _enum_schema(
                    PersistedCapabilityEvidenceStatus
                ),
                "congestion_evidence_status": _enum_schema(
                    PersistedCapabilityEvidenceStatus
                ),
            },
        ),
        _claim(
            DecisionClaimKind.PERSISTED_CANDIDATE_STATUS,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _PERSISTED,
                "recommendation_id": _STRING,
                "candidate_count": _INTEGER,
                "collection_reason": _nullable(
                    _enum_schema(PersistedCandidateCollectionReason)
                ),
            },
        ),
        _claim(
            DecisionClaimKind.TOOL_STATUS,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT_OR_PERSISTED,
                "status": _enum_schema(ToolResultStatus),
            },
        ),
        _claim(
            DecisionClaimKind.LIMITATION,
            {
                "evidence_ref": _STRING,
                "evidence_scope": _CURRENT,
                "limitation_code": _enum_schema(DecisionLimitationCode),
            },
        ),
        _claim(
            DecisionClaimKind.CURRENT_PERSISTED_LINK,
            {
                "current_evidence_ref": _STRING,
                "persisted_evidence_ref": _STRING,
                "recommendation_id": _STRING,
            },
        ),
    ]


def decision_terminal_json_schema() -> dict[str, object]:
    """Terminal JSON Schema. TOOL_CALLS stays on native tool_use blocks."""

    version = {"const": DECISION_MODEL_DECISION_SCHEMA_VERSION}
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
                    "unsupported_reason": _enum_schema(DecisionUnsupportedReason),
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "kind"],
                "properties": {
                    "schema_version": version,
                    "kind": {"const": ModelDecisionKind.REFUSAL.value},
                    "refusal_code": _STRING,
                },
            },
        ]
    }


def _json_type(value_type: ToolInputValueType) -> str:
    try:
        return _JSON_TYPES[value_type]
    except KeyError as exc:
        raise ModelProviderError("unsupported_field_value_type") from exc


def decision_tool_def_from_schema(schema: ToolSchema) -> dict[str, object]:
    """Convert one provider-neutral ToolSchema into a strict Anthropic tool."""

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


def build_decision_messages_kwargs(
    request: DecisionModelTurnRequest,
    model_id: str,
) -> dict[str, object]:
    """Reconstruct one Messages request. Unknown instructions fail first."""

    if not isinstance(request, DecisionModelTurnRequest):
        raise TypeError("request must be a DecisionModelTurnRequest")
    system = resolve_decision_instruction(request.instruction_ref)
    messages: list[dict[str, object]] = [
        {
            "role": "user",
            "content": [{"type": "text", "text": request.user_text}],
        }
    ]
    if request.evidence_snapshots:
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": _stable_json(
                            {
                                "evidence_snapshots": [
                                    item.to_dict()
                                    for item in request.evidence_snapshots
                                ]
                            }
                        ),
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
                        "type": "text",
                        "text": _stable_json(
                            {
                                "validation_feedback": (
                                    request.validation_feedback.to_dict()
                                )
                            }
                        ),
                    }
                ],
            }
        )
    return {
        "model": model_id,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "system": system,
        "messages": messages,
        "tools": [
            decision_tool_def_from_schema(item) for item in request.tools
        ],
        "tool_choice": {"type": "auto"},
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": decision_terminal_json_schema(),
            }
        },
    }


def _malformed(code: str) -> ModelProviderMalformedDecisionError:
    return ModelProviderMalformedDecisionError(code)


def _require_mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _malformed(code)
    return value


def _validate_envelope(response: Mapping[str, Any], expected_model_id: str) -> None:
    if response.get("type") != "message":
        raise _malformed("unexpected_response_type")
    if response.get("role") != "assistant":
        raise _malformed("unexpected_response_role")
    if response.get("model") != expected_model_id:
        raise _malformed("unexpected_response_model")
    if "id" in response:
        message_id = response.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise _malformed("malformed_message_id")


def _content_blocks(response: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_content = response.get("content")
    if not isinstance(raw_content, list):
        raise _malformed("malformed_content")
    blocks: list[Mapping[str, Any]] = []
    for item in raw_content:
        block = _require_mapping(item, "malformed_content")
        block_type = block.get("type")
        if block_type not in _ALLOWED_BLOCK_TYPES:
            raise _malformed("unexpected_content_block")
        if block_type == "text" and "text" not in block:
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


def _decision_from_mapping(data: Mapping[str, Any]) -> DecisionModelDecision:
    try:
        decision = DecisionModelDecision.from_dict(data)
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
        if block.get("type") != "tool_use":
            continue
        tool_use_id = block.get("id")
        if not isinstance(tool_use_id, str) or not tool_use_id.strip():
            raise _malformed("malformed_tool_use")
        name = block.get("name")
        if not isinstance(name, str) or not name.strip():
            raise _malformed("malformed_tool_use")
        arguments = block.get("input")
        if not isinstance(arguments, Mapping):
            raise _malformed("malformed_tool_use")
        try:
            calls.append(ProposedToolCall(name=name, arguments=dict(arguments)))
        except ContractValidationError as exc:
            raise _malformed("invalid_proposed_tool_call") from exc
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


def _end_turn_decision(blocks: list[Mapping[str, Any]]) -> DecisionModelDecision:
    text_blocks = [block for block in blocks if block.get("type") == "text"]
    if len(text_blocks) != 1:
        raise _malformed("malformed_terminal_json")
    parsed = _parse_json_object(text_blocks[0].get("text"))
    if parsed is None:
        raise _malformed("malformed_terminal_json")
    return _decision_from_mapping(parsed)


def parse_decision_messages_response(
    response: object,
    expected_model_id: str,
) -> DecisionModelDecision:
    """Map one Messages response to exactly one DecisionModelDecision."""

    payload = _require_mapping(response, "malformed_content")
    _validate_envelope(payload, expected_model_id)
    stop_reason = payload.get("stop_reason")
    if not isinstance(stop_reason, str) or not stop_reason:
        raise _malformed("unexpected_stop_reason")
    if stop_reason == "refusal":
        if "content" in payload:
            _content_blocks(payload)
        return DecisionModelDecision(
            kind=ModelDecisionKind.REFUSAL,
            refusal_code=REFUSAL_PROVIDER,
        )
    if stop_reason == "model_context_window_exceeded":
        raise ModelProviderContextLengthError("model_context_window_exceeded")
    if stop_reason == "max_tokens":
        raise _malformed("max_tokens")
    if stop_reason == "stop_sequence":
        raise _malformed("stop_sequence_not_configured")
    if stop_reason == "pause_turn":
        raise _malformed("pause_turn")
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
        return DecisionModelDecision(
            kind=ModelDecisionKind.TOOL_CALLS,
            tool_calls=tool_calls,
        )
    if tool_calls:
        raise _malformed("end_turn_contains_tool_use")
    return _end_turn_decision(blocks)


class AnthropicDecisionMessagesProvider:
    """Stateless Decision Messages provider. The client is injected."""

    def __init__(
        self,
        *,
        client: AnthropicDecisionMessagesClient,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> None:
        if client is None or not callable(getattr(client, "messages_create", None)):
            raise TypeError(
                "client must implement AnthropicDecisionMessagesClient.messages_create"
            )
        if model_id not in APPROVED_MODEL_IDS:
            raise ValueError("unknown_model_id")
        self._client = client
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def complete(self, request: DecisionModelTurnRequest) -> DecisionModelDecision:
        """Return exactly one DecisionModelDecision for this turn."""

        kwargs = build_decision_messages_kwargs(request, self._model_id)
        response = self._client.messages_create(**kwargs)
        return parse_decision_messages_response(response, self._model_id)
