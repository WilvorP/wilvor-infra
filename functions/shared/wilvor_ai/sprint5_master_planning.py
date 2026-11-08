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
from wilvor_ai.master_contracts import MasterRequirement
from wilvor_ai.master_routing import (
    EXECUTABLE_ROUTE_KIND_TABLE,
    MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION,
    MasterPlanningContext,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    _PAIR_CAPABILITIES,
    _SYNTHESIS_CAPABILITIES,
    _VISUALIZATION,
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


def _closed_enum(values: object) -> dict[str, object]:
    return {"type": "string", "enum": [str(item) for item in values]}


def _const(value: str) -> dict[str, object]:
    return {"const": value}


def _sorted_names(values: object) -> list[str]:
    return sorted(str(item) for item in values)


_STEP_IDENTITY_FIELDS = ("specialist", "purpose", "capability")
# Anthropic structured outputs reject a request whose strict schemas exceed
# 24 optional parameters or 16 union-typed parameters, and they also reject
# a compiled grammar that is too large. Five copies of this step object put
# the same optional fields on every anyOf branch and cross that limit.
ANTHROPIC_MAX_OPTIONAL_PARAMETERS = 24
ANTHROPIC_MAX_UNION_TYPED_PARAMETERS = 16


def _proposal_capability_names() -> list[str]:
    """Capabilities the proposal may name. Route selection is not this list.

    Synthesis and visualization stay out of ``route_kind``. The proposal
    array can still name them so the host can defer synthesis.
    """

    executable = {
        capability.value
        for capabilities in _PAIR_CAPABILITIES.values()
        for capability in capabilities
    }
    synthesis = {item.value for item in _SYNTHESIS_CAPABILITIES}
    return _sorted_names(executable | synthesis | {_VISUALIZATION.value})


def _step_schema(
    *,
    string: dict[str, object],
    requirement: dict[str, object],
    temporal_intent: dict[str, object],
    hints: dict[str, object],
) -> dict[str, object]:
    """One step object. ``route_kind`` is the only executable combination."""

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["step_id", "route_kind", "requirement"],
        "properties": {
            "step_id": string,
            "route_kind": _closed_enum(EXECUTABLE_ROUTE_KIND_TABLE),
            "requirement": requirement,
            "depends_on": {"type": "array", "items": string},
            "temporal_intent": temporal_intent,
            "historical_start_utc": string,
            "historical_end_utc": string,
            "hints": hints,
            "needs_persisted_evidence": {"type": "boolean"},
        },
    }


def mismatched_executable_step_tokens(proposal: MasterRouteProposal) -> tuple[str, ...]:
    """Sanitized tokens for steps whose capability is outside the pair table.

    Each mismatched allowed pair contributes specialist, purpose, and
    capability. Step ids, hints, prompts, and aircraft identifiers are omitted.
    """

    if not isinstance(proposal, MasterRouteProposal):
        _fail("invalid_master_route_proposal")
    tokens: list[str] = []
    for step in proposal.steps:
        allowed = _PAIR_CAPABILITIES.get((step.specialist, step.purpose))
        if allowed is not None and step.capability not in allowed:
            tokens.extend(
                (
                    "specialist:%s" % step.specialist.value,
                    "purpose:%s" % step.purpose.value,
                    "capability:%s" % step.capability.value,
                )
            )
    return tuple(tokens)


def master_planning_json_schema() -> dict[str, object]:
    """Structured output for Master planning. Tools are not in this schema.

    Each executable step carries one ``route_kind`` from
    ``EXECUTABLE_ROUTE_KIND_TABLE``. The host decodes that token into
    specialist, purpose, and capability. Synthesis capabilities stay on the
    proposal ``capabilities`` array and are not route kinds. ``user_text``
    stays host-owned. ``authorize_route`` remains the authorization step.
    """

    string = {"type": "string"}
    requirement = _closed_enum(item.value for item in MasterRequirement)
    temporal_intent = _closed_enum(item.value for item in MasterRouteTemporalIntent)
    capability = {
        "type": "object",
        "additionalProperties": False,
        "required": ["capability", "requirement"],
        "properties": {
            "capability": _closed_enum(_proposal_capability_names()),
            "requirement": requirement,
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
    step = _step_schema(
        string=string,
        requirement=requirement,
        temporal_intent=temporal_intent,
        hints=hints,
    )
    return {
        "anyOf": [
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["schema_version", "capabilities", "steps"],
                "properties": {
                    "schema_version": {"const": MASTER_ROUTE_PROPOSAL_SCHEMA_VERSION},
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


def _expand_model_step(step: object) -> dict[str, Any]:
    """Map one model step's route_kind onto the host triple.

    Specialist, purpose, and capability are not accepted from the model.
    Supplying them beside route_kind, or omitting route_kind, fails closed.
    """

    if not isinstance(step, Mapping):
        _fail("invalid_master_route_proposal")
    has_identity = any(name in step for name in _STEP_IDENTITY_FIELDS)
    if "route_kind" in step and has_identity:
        _fail("invalid_route_kind_conflict")
    if has_identity or "route_kind" not in step:
        _fail("invalid_route_kind")
    token = step.get("route_kind")
    resolved = EXECUTABLE_ROUTE_KIND_TABLE.get(token) if isinstance(token, str) else None
    if resolved is None:
        _fail("invalid_route_kind")
    specialist, purpose, capability = resolved
    expanded = {key: value for key, value in step.items() if key != "route_kind"}
    expanded["specialist"] = specialist.value
    expanded["purpose"] = purpose.value
    expanded["capability"] = capability.value
    return expanded


def expand_model_route_proposal(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Decode route_kind tokens. This does not authorize the proposal."""

    if not isinstance(payload, Mapping):
        _fail("invalid_master_route_proposal")
    steps = payload.get("steps")
    if not isinstance(steps, list):
        _fail("invalid_master_route_proposal")
    expanded = dict(payload)
    expanded["steps"] = [_expand_model_step(step) for step in steps]
    return expanded


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
            steps = payload.get("steps")
            if isinstance(steps, list) and any(
                isinstance(step, Mapping) and "route_kind" in step for step in steps
            ):
                payload = expand_model_route_proposal(payload)
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
        return expand_model_route_proposal(payload)
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
    "ANTHROPIC_MAX_OPTIONAL_PARAMETERS",
    "ANTHROPIC_MAX_UNION_TYPED_PARAMETERS",
    "decode_master_planning_json",
    "decode_planner_model_output",
    "expand_model_route_proposal",
    "looks_like_route_proposal",
    "master_planning_json_schema",
    "mismatched_executable_step_tokens",
    "parse_master_planning_text_blocks",
]
