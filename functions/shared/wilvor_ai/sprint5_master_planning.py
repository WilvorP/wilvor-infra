"""Sprint 5 Master planner decode: ModelDecision to MasterRouteProposal.

Bedrock/Anthropic Master turns may emit a route-proposal mapping, or a
planner UNSUPPORTED/REFUSAL ModelDecision. Specialist FINAL_CLAIMS and
TOOL_CALLS stay rejected. A decoded proposal is still untrusted. MA2
``authorize_route`` remains the only planner-to-plan function.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_routing import (
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    MasterPlanningContext,
    MasterRouteProposal,
)
from wilvor_ai.model_contracts import (
    MODEL_DECISION_SCHEMA_VERSION,
    ModelDecision,
    ModelDecisionKind,
    ModelTurnRequest,
)
from wilvor_ai.providers.master_instructions import (
    MASTER_PLANNING_INSTRUCTION_REF,
    resolve_master_instruction,
)
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.sprint4_master_planning import Sprint4PlanningStatus
from wilvor_ai.sprint4_master_providers import (
    inspect_untrusted_tool_calls,
    reject_master_planner_tools,
)


SPRINT5_MASTER_PLANNING_SCHEMA = "wilvor.ai.sprint5.master_planning.v1"
MASTER_PLANNING_OUTPUT_SCHEMA_NAME = "wilvor_master_route_proposal_v1"
MODEL_PROPOSAL_IS_AUTHORIZATION = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def master_planning_json_schema() -> dict[str, object]:
    """Structured output for Master planning. Tools are not in this schema."""

    string = {"type": "string"}
    capability = {
        "type": "object",
        "additionalProperties": False,
        "required": ["capability", "requirement"],
        "properties": {
            "capability": string,
            "requirement": string,
        },
    }
    hints = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "callsign": string,
            "typed_aircraft_id": string,
            "recommendation_id": string,
            "region_text": string,
            "population_aircraft_ids": {
                "type": "array",
                "items": string,
            },
        },
    }
    step = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "step_id",
            "specialist",
            "purpose",
            "requirement",
            "capability",
        ],
        "properties": {
            "step_id": string,
            "specialist": string,
            "purpose": string,
            "requirement": string,
            "capability": string,
            "depends_on": {"type": "array", "items": string},
            "temporal_intent": string,
            "historical_start_utc": string,
            "historical_end_utc": string,
            "hints": hints,
            "needs_persisted_evidence": {"type": "boolean"},
        },
    }
    return {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "user_text", "capabilities", "steps"],
                "properties": {
                    "schema_version": {"const": MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION},
                    "user_text": string,
                    "capabilities": {"type": "array", "items": capability},
                    "steps": {"type": "array", "items": step},
                },
            },
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "kind", "unsupported_reason"],
                "properties": {
                    "schema_version": {"const": MODEL_DECISION_SCHEMA_VERSION},
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
                    "schema_version": {"const": MODEL_DECISION_SCHEMA_VERSION},
                    "kind": {"const": ModelDecisionKind.REFUSAL.value},
                    "refusal_code": string,
                },
            },
        ]
    }


def looks_like_route_proposal(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    if value.get("kind") in {
        ModelDecisionKind.TOOL_CALLS.value,
        ModelDecisionKind.FINAL_CLAIMS.value,
        ModelDecisionKind.UNSUPPORTED.value,
        ModelDecisionKind.REFUSAL.value,
    }:
        return False
    return "capabilities" in value and "steps" in value


def decode_planner_model_output(
    raw: object,
    context: MasterPlanningContext,
) -> MasterRouteProposal | Sprint4PlanningStatus:
    """Decode an injected planner output. Decoding is not authorization."""

    if MODEL_PROPOSAL_IS_AUTHORIZATION is not False:
        _fail("proposal_is_not_authorization")
    if not isinstance(context, MasterPlanningContext):
        _fail("invalid_planning_context")
    if isinstance(raw, MasterRouteProposal):
        if raw.user_text != context.canonical_user_text:
            _fail("user_text_mismatch")
        return raw
    if isinstance(raw, ModelDecision):
        inspect_untrusted_tool_calls(raw)
        if raw.kind is ModelDecisionKind.TOOL_CALLS:
            reject_master_planner_tools(raw)
        if raw.kind is ModelDecisionKind.FINAL_CLAIMS:
            _fail("master_planner_claims_rejected")
        if raw.kind is ModelDecisionKind.UNSUPPORTED:
            return Sprint4PlanningStatus.PLANNER_UNSUPPORTED
        if raw.kind is ModelDecisionKind.REFUSAL:
            return Sprint4PlanningStatus.PLANNER_REFUSED
        _fail("invalid_master_route_proposal")
    if isinstance(raw, Mapping):
        if raw.get("kind") in {
            ModelDecisionKind.UNSUPPORTED.value,
            ModelDecisionKind.REFUSAL.value,
            ModelDecisionKind.FINAL_CLAIMS.value,
            ModelDecisionKind.TOOL_CALLS.value,
        }:
            return decode_planner_model_output(ModelDecision.from_dict(raw), context)
        if looks_like_route_proposal(raw):
            payload = dict(raw)
            payload.setdefault("schema_version", MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION)
            payload.setdefault("user_text", context.canonical_user_text)
            proposal = MasterRouteProposal.from_dict(payload)
            if proposal.user_text != context.canonical_user_text:
                _fail("user_text_mismatch")
            return proposal
    _fail("invalid_master_route_proposal")
    raise AssertionError("unreachable")


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


def decode_master_planning_json(payload: Mapping[str, Any]) -> object:
    """Parse terminal Master JSON into a proposal mapping or ModelDecision."""

    if not isinstance(payload, Mapping):
        _fail("invalid_master_route_proposal")
    if looks_like_route_proposal(payload):
        return dict(payload)
    if payload.get("kind") in {
        ModelDecisionKind.UNSUPPORTED.value,
        ModelDecisionKind.REFUSAL.value,
        ModelDecisionKind.FINAL_CLAIMS.value,
        ModelDecisionKind.TOOL_CALLS.value,
    }:
        return ModelDecision.from_dict(payload)
    _fail("invalid_master_route_proposal")
    raise AssertionError("unreachable")


def build_master_planning_converse_kwargs(
    request: ModelTurnRequest,
    model_id: str,
) -> dict[str, object]:
    """Bedrock Converse kwargs for Master planning. No tool catalog."""

    if not isinstance(request, ModelTurnRequest):
        raise TypeError("request must be a ModelTurnRequest")
    if request.instruction_ref != MASTER_PLANNING_INSTRUCTION_REF:
        _fail("unknown_instruction_ref")
    if request.tools:
        _fail("master_planner_tools_rejected")
    schema = json.dumps(
        master_planning_json_schema(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return {
        "modelId": model_id,
        "system": [{"text": resolve_master_instruction(request.instruction_ref)}],
        "messages": [
            {"role": "user", "content": [{"text": request.user_text}]},
        ],
        "inferenceConfig": {
            "temperature": 0,
            "maxTokens": 2048,
        },
        "outputConfig": {
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "schema": schema,
                        "name": MASTER_PLANNING_OUTPUT_SCHEMA_NAME,
                        "description": (
                            "Wilvor MasterRouteProposal or planner "
                            "UNSUPPORTED/REFUSAL. TOOL_CALLS are forbidden."
                        ),
                    }
                },
            }
        },
    }


def build_master_planning_messages_kwargs(
    request: ModelTurnRequest,
    model_id: str,
) -> dict[str, object]:
    """Anthropic Messages kwargs for Master planning. No tool catalog."""

    if not isinstance(request, ModelTurnRequest):
        raise TypeError("request must be a ModelTurnRequest")
    if request.instruction_ref != MASTER_PLANNING_INSTRUCTION_REF:
        _fail("unknown_instruction_ref")
    if request.tools:
        _fail("master_planner_tools_rejected")
    return {
        "model": model_id,
        "max_tokens": 2048,
        "temperature": 0,
        "system": resolve_master_instruction(request.instruction_ref),
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": request.user_text}],
            }
        ],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": master_planning_json_schema(),
            }
        },
    }


def parse_master_planning_text_blocks(blocks: list[Mapping[str, Any]]) -> object:
    text_blocks = [block for block in blocks if "text" in block]
    if len(text_blocks) != 1:
        from wilvor_ai.providers.errors import ModelProviderMalformedDecisionError

        raise ModelProviderMalformedDecisionError("malformed_terminal_json")
    parsed = _parse_json_object(text_blocks[0].get("text"))
    if parsed is None:
        from wilvor_ai.providers.errors import ModelProviderMalformedDecisionError

        raise ModelProviderMalformedDecisionError("malformed_terminal_json")
    try:
        return decode_master_planning_json(parsed)
    except ContractValidationError as exc:
        from wilvor_ai.providers.errors import ModelProviderMalformedDecisionError

        raise ModelProviderMalformedDecisionError("invalid_master_route_proposal") from exc


__all__ = [
    "MASTER_PLANNING_OUTPUT_SCHEMA_NAME",
    "MODEL_PROPOSAL_IS_AUTHORIZATION",
    "SPRINT5_MASTER_PLANNING_SCHEMA",
    "build_master_planning_converse_kwargs",
    "build_master_planning_messages_kwargs",
    "decode_master_planning_json",
    "decode_planner_model_output",
    "looks_like_route_proposal",
    "master_planning_json_schema",
    "parse_master_planning_text_blocks",
]
