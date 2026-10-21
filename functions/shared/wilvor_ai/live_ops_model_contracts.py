"""Provider-neutral Live Ops model-turn contracts.

These types describe Live Ops decisions, validation failures, and turn
requests. They do not dispatch tools, call a provider, verify claims, or
render answers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
)
from wilvor_ai.live_ops_claims import (
    LIVE_OPS_MAX_CLAIMS_PER_RESPONSE,
    LiveOpsClaim,
    is_live_ops_claim,
    live_ops_claim_from_dict,
)
from wilvor_ai.model_contracts import (
    INSTRUCTION_REF_PATTERN,
    ProposedToolCall,
)
from wilvor_ai.specialist_contracts import SPECIALIST_REQUEST_TEXT_MAX_LENGTH
from wilvor_ai.tool_schema import ToolSchema


LIVE_OPS_INSTRUCTION_REF = "wilvor.live_ops.specialist.v1"
LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION = "wilvor.ai.live_ops_model_decision.v1"
LIVE_OPS_ARGUMENT_STRING_MAX = 512
LIVE_OPS_HAZARD_IDS_MAX = 25
LIVE_OPS_MODEL_VISIBLE_LIST_MAX = 25
LIVE_OPS_PROJECTED_STRING_MAX = 256
LIVE_OPS_PROJECTION_JSON_MAX_BYTES = 32768
MAX_TOOL_CALLS_PER_BATCH = 2
_PRIOR_TURN_MAX = 4
_PRIOR_TURN_TEXT_MAX = 2048
_PRIOR_TURNS_TOTAL_MAX = 8192
_SELECTOR_HINT_TEXT_MAX = 512
_ENTITY_HINT_MAX = 4
_SELECTOR_HINT_NAMES = frozenset(
    {
        "region",
        "product_type",
        "hazard_type",
        "hazard_ids",
        "aircraft_id",
        "callsign",
        "airport_id",
        "hazard_id",
    }
)
_PRIOR_TURN_ROLES = frozenset({"user", "assistant"})


class LiveOpsValidationCode(str, Enum):
    """Closed adapter validation codes. Not exception text or model prose."""

    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    UNKNOWN_ARGUMENT = "UNKNOWN_ARGUMENT"
    TRUSTED_ARGUMENT_FORBIDDEN = "TRUSTED_ARGUMENT_FORBIDDEN"
    MISSING_REQUIRED_ARGUMENT = "MISSING_REQUIRED_ARGUMENT"
    INVALID_ARGUMENT_TYPE = "INVALID_ARGUMENT_TYPE"
    INVALID_ARGUMENT_VALUE = "INVALID_ARGUMENT_VALUE"
    EMPTY_OR_WHITESPACE = "EMPTY_OR_WHITESPACE"
    STRING_TOO_LONG = "STRING_TOO_LONG"
    ARRAY_TOO_LARGE = "ARRAY_TOO_LARGE"
    EMPTY_HAZARD_IDS = "EMPTY_HAZARD_IDS"
    DUPLICATE_HAZARD_ID = "DUPLICATE_HAZARD_ID"
    MISSING_SELECTOR = "MISSING_SELECTOR"
    UNBOUNDED_HAZARD_SEARCH = "UNBOUNDED_HAZARD_SEARCH"
    NESTED_VALUE_FORBIDDEN = "NESTED_VALUE_FORBIDDEN"
    DUPLICATE_TOOL_CALL = "DUPLICATE_TOOL_CALL"
    BOOLEAN_INTEGER_CONFUSION = "BOOLEAN_INTEGER_CONFUSION"
    EMPTY_BATCH = "EMPTY_BATCH"
    BATCH_TOO_LARGE = "BATCH_TOO_LARGE"
    CANONICAL_KEY_TOO_LARGE = "CANONICAL_KEY_TOO_LARGE"


class LiveOpsUnsupportedReason(str, Enum):
    """Closed Live Ops capability boundaries. Not model prose and not REFUSAL."""

    HISTORICAL_ANALYTICS = "HISTORICAL_ANALYTICS"
    ROUTE_GENERATION_NOT_IMPLEMENTED = "ROUTE_GENERATION_NOT_IMPLEMENTED"
    SELECTED_DIVERSION_NOT_SUPPORTED = "SELECTED_DIVERSION_NOT_SUPPORTED"
    ACTION_REQUEST = "ACTION_REQUEST"
    ATC_OR_CONTROL_INSTRUCTION = "ATC_OR_CONTROL_INSTRUCTION"
    NEW_RISK_OR_RECOMMENDATION = "NEW_RISK_OR_RECOMMENDATION"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_CATALOG = "OUT_OF_CATALOG"


class LiveOpsProviderFailureKind(str, Enum):
    """Provider/transport failures. Not a model REFUSAL."""

    TRANSPORT_OR_API = "TRANSPORT_OR_API"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    CONTEXT_LENGTH = "CONTEXT_LENGTH"


class LiveOpsModelDecisionKind(str, Enum):
    TOOL_CALLS = "TOOL_CALLS"
    FINAL_CLAIMS = "FINAL_CLAIMS"
    UNSUPPORTED = "UNSUPPORTED"
    REFUSAL = "REFUSAL"


@dataclass(frozen=True)
class LiveOpsValidationFailure:
    """Rejected model tool-call batch. No tool executed for this failure."""

    code: LiveOpsValidationCode
    tool_name: str | None = None
    argument_name: str | None = None

    def __post_init__(self) -> None:
        errors: list[str] = []
        if not isinstance(self.code, LiveOpsValidationCode):
            errors.append("invalid_code")
        errors.extend(
            _validate_bounded_text(self.tool_name, "tool_name", optional=True)
        )
        errors.extend(
            _validate_bounded_text(
                self.argument_name,
                "argument_name",
                optional=True,
            )
        )
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"code": self.code.value}
        if self.tool_name is not None:
            payload["tool_name"] = self.tool_name
        if self.argument_name is not None:
            payload["argument_name"] = self.argument_name
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsValidationFailure":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_validation_failure")
        return cls(
            code=_parse_enum(LiveOpsValidationCode, _required(data, "code"), "code"),
            tool_name=data["tool_name"] if "tool_name" in data else None,
            argument_name=(
                data["argument_name"] if "argument_name" in data else None
            ),
        )


def _stripped_token(value: Any, field_name: str, *, max_length: int) -> list[str]:
    errors = _validate_bounded_text(value, field_name, max_length=max_length)
    if errors or not isinstance(value, str):
        return errors or [f"invalid_{field_name}"]
    if value != value.strip():
        return [f"invalid_{field_name}"]
    return []


def _freeze_prior_turns(
    value: Any,
) -> tuple[list[str], tuple[tuple[str, str], ...]]:
    """Copy untrusted turns. Roles stay labels, not instructions."""

    if not isinstance(value, tuple):
        return ["invalid_prior_turns"], ()
    if len(value) > _PRIOR_TURN_MAX:
        return ["too_many_prior_turns"], ()
    frozen: list[tuple[str, str]] = []
    total = 0
    for item in value:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
        ):
            return ["invalid_prior_turns"], ()
        role, text = item
        if role not in _PRIOR_TURN_ROLES:
            return ["invalid_prior_turn_role"], ()
        if _stripped_token(text, "prior_turn_text", max_length=_PRIOR_TURN_TEXT_MAX):
            return ["invalid_prior_turn_text"], ()
        total += len(text)
        frozen.append((role, text))
    if total > _PRIOR_TURNS_TOTAL_MAX:
        return ["prior_turns_too_large"], ()
    return [], tuple(frozen)


def _freeze_hazard_ids(value: Any) -> tuple[list[str], tuple[str, ...]]:
    if isinstance(value, list):
        return ["invalid_selector_hint_items"], ()
    if not isinstance(value, tuple) or not value:
        return ["invalid_selector_hint_items"], ()
    if len(value) > LIVE_OPS_HAZARD_IDS_MAX:
        return ["invalid_selector_hint_items"], ()
    frozen: list[str] = []
    for item in value:
        if _stripped_token(item, "hazard_ids", max_length=_SELECTOR_HINT_TEXT_MAX):
            return ["invalid_selector_hint_items"], ()
        frozen.append(item)
    if len(frozen) != len(set(frozen)) or frozen != sorted(frozen):
        return ["invalid_selector_hint_items"], ()
    return [], tuple(frozen)


def _freeze_selector_hints(
    value: Any,
) -> tuple[list[str], tuple[tuple[str, str | tuple[str, ...]], ...]]:
    """Copy untrusted selector text. These values do not authorize a tool."""

    if not isinstance(value, tuple):
        return ["invalid_selector_hint_items"], ()
    if len(value) > len(_SELECTOR_HINT_NAMES):
        return ["invalid_selector_hint_items"], ()
    frozen: list[tuple[str, str | tuple[str, ...]]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, tuple) or len(item) != 2 or not isinstance(item[0], str):
            return ["invalid_selector_hint_items"], ()
        name, raw = item
        if name not in _SELECTOR_HINT_NAMES or name in seen:
            return ["invalid_selector_hint_items"], ()
        seen.add(name)
        if name == "hazard_ids":
            errors, hazard_ids = _freeze_hazard_ids(raw)
            if errors:
                return errors, ()
            frozen.append((name, hazard_ids))
            continue
        if _stripped_token(raw, name, max_length=_SELECTOR_HINT_TEXT_MAX):
            return ["invalid_selector_hint_items"], ()
        frozen.append((name, raw))
    return [], tuple(frozen)


def _freeze_entity_hints(
    value: Any,
) -> tuple[list[str], tuple[tuple[str, str, str | None], ...]]:
    """Copy untrusted identity hints. They carry no entity-ref authority."""

    if not isinstance(value, tuple):
        return ["invalid_entity_hint_items"], ()
    if len(value) > _ENTITY_HINT_MAX:
        return ["too_many_entity_hints"], ()
    frozen: list[tuple[str, str, str | None]] = []
    for item in value:
        if not isinstance(item, tuple) or len(item) != 3:
            return ["invalid_entity_hint_items"], ()
        kind, entity_id, callsign = item
        if kind != "AIRCRAFT":
            return ["invalid_entity_kind"], ()
        if _stripped_token(entity_id, "entity_id", max_length=PROVENANCE_TOKEN_MAX_LENGTH):
            return ["invalid_entity_hint_items"], ()
        if callsign is not None and _stripped_token(
            callsign,
            "callsign",
            max_length=_SELECTOR_HINT_TEXT_MAX,
        ):
            return ["invalid_entity_hint_items"], ()
        frozen.append((kind, entity_id, callsign))
    return [], tuple(frozen)


def _mapping_items(value: Any, field_name: str) -> list[Mapping[str, Any]] | None:
    try:
        items = _sequence(value, field_name)
    except ContractValidationError:
        return None
    if any(not isinstance(item, Mapping) for item in items):
        return None
    return items


def _validate_instruction_ref(value: Any) -> list[str]:
    errors = _validate_bounded_text(value, "instruction_ref", optional=True)
    if errors or value is None:
        return errors
    if INSTRUCTION_REF_PATTERN.fullmatch(value) is None:
        return ["invalid_instruction_ref"]
    return []


@dataclass(frozen=True)
class LiveOpsModelTurnRequest:
    """Provider-neutral Live Ops turn. Not a vendor transcript.

    ``prior_turns``, ``selector_hint_items``, and ``entity_hint_items`` are
    untrusted conversational context. They are not evidence, not tool
    authority, and not a place for server instructions or raw tool results.
    Empty values stay out of ``to_dict`` so older payloads still round-trip.
    """

    user_text: str
    instruction_ref: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    tool_results: tuple[Any, ...] = ()
    validation_feedback: LiveOpsValidationFailure | None = None
    prior_turns: tuple[tuple[str, str], ...] = ()
    selector_hint_items: tuple[tuple[str, str | tuple[str, ...]], ...] = ()
    entity_hint_items: tuple[tuple[str, str, str | None], ...] = ()

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.user_text,
            "user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        errors.extend(_validate_instruction_ref(self.instruction_ref))
        if not isinstance(self.tools, tuple):
            errors.append("invalid_tools")
        elif self.tools:
            if any(not isinstance(item, ToolSchema) for item in self.tools):
                errors.append("invalid_tools")
        if not isinstance(self.tool_results, tuple):
            errors.append("invalid_tool_results")
        elif self.tool_results:
            from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection

            if any(
                not isinstance(item, LiveOpsToolProjection)
                for item in self.tool_results
            ):
                errors.append("invalid_tool_results")
        if (
            self.validation_feedback is not None
            and not isinstance(self.validation_feedback, LiveOpsValidationFailure)
        ):
            errors.append("invalid_validation_feedback")
        prior_errors, prior_turns = _freeze_prior_turns(self.prior_turns)
        selector_errors, selector_items = _freeze_selector_hints(self.selector_hint_items)
        entity_errors, entity_items = _freeze_entity_hints(self.entity_hint_items)
        errors.extend(prior_errors)
        errors.extend(selector_errors)
        errors.extend(entity_errors)
        if errors:
            raise ContractValidationError(errors)
        object.__setattr__(self, "prior_turns", prior_turns)
        object.__setattr__(self, "selector_hint_items", selector_items)
        object.__setattr__(self, "entity_hint_items", entity_items)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"user_text": self.user_text}
        if self.instruction_ref is not None:
            payload["instruction_ref"] = self.instruction_ref
        if self.tools:
            payload["tools"] = [item.to_dict() for item in self.tools]
        if self.tool_results:
            payload["tool_results"] = [item.to_dict() for item in self.tool_results]
        if self.validation_feedback is not None:
            payload["validation_feedback"] = self.validation_feedback.to_dict()
        if self.prior_turns:
            payload["prior_turns"] = [
                {"role": role, "text": text} for role, text in self.prior_turns
            ]
        if self.selector_hint_items:
            payload["selector_hint_items"] = [
                {
                    "name": name,
                    "value": list(value) if isinstance(value, tuple) else value,
                }
                for name, value in self.selector_hint_items
            ]
        if self.entity_hint_items:
            entities: list[dict[str, JsonValue]] = []
            for kind, entity_id, callsign in self.entity_hint_items:
                item: dict[str, JsonValue] = {
                    "entity_kind": kind,
                    "entity_id": entity_id,
                }
                if callsign is not None:
                    item["callsign"] = callsign
                entities.append(item)
            payload["entity_hint_items"] = entities
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsModelTurnRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_model_turn_request")
        tools: tuple[ToolSchema, ...] = ()
        tool_results: tuple[Any, ...] = ()
        validation_feedback = None
        prior_turns: tuple[tuple[str, str], ...] = ()
        selector_items: tuple[tuple[str, str | tuple[str, ...]], ...] = ()
        entity_items: tuple[tuple[str, str, str | None], ...] = ()
        if "tools" in data:
            tools = tuple(
                ToolSchema.from_dict(item)
                for item in _sequence(data["tools"], "tools")
            )
        if "tool_results" in data:
            from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection

            tool_results = tuple(
                LiveOpsToolProjection.from_dict(item)
                for item in _sequence(data["tool_results"], "tool_results")
            )
        if "validation_feedback" in data and data["validation_feedback"] is not None:
            validation_feedback = LiveOpsValidationFailure.from_dict(
                data["validation_feedback"]
            )
        if "prior_turns" in data:
            parsed_turns = _mapping_items(data["prior_turns"], "prior_turns")
            if parsed_turns is None:
                raise ContractValidationError("invalid_prior_turns")
            turns: list[tuple[str, str]] = []
            for item in parsed_turns:
                if set(item) - {"role", "text"}:
                    raise ContractValidationError("unexpected_prior_turn_field")
                if "role" not in item or "text" not in item:
                    raise ContractValidationError("invalid_prior_turns")
                turns.append((item["role"], item["text"]))
            prior_turns = tuple(turns)
        if "selector_hint_items" in data:
            parsed_hints = _mapping_items(data["selector_hint_items"], "selector_hint_items")
            if parsed_hints is None:
                raise ContractValidationError("invalid_selector_hint_items")
            hints: list[tuple[str, str | tuple[str, ...]]] = []
            for item in parsed_hints:
                if set(item) - {"name", "value"}:
                    raise ContractValidationError("unexpected_selector_field")
                if "name" not in item or "value" not in item:
                    raise ContractValidationError("invalid_selector_hint_items")
                raw_value = item["value"]
                if item["name"] == "hazard_ids":
                    if not isinstance(raw_value, list) or any(
                        not isinstance(part, str) for part in raw_value
                    ):
                        raise ContractValidationError("invalid_selector_hint_items")
                    hints.append((item["name"], tuple(raw_value)))
                else:
                    hints.append((item["name"], raw_value))
            selector_items = tuple(hints)
        if "entity_hint_items" in data:
            parsed_entities = _mapping_items(data["entity_hint_items"], "entity_hint_items")
            if parsed_entities is None:
                raise ContractValidationError("invalid_entity_hint_items")
            entities: list[tuple[str, str, str | None]] = []
            for item in parsed_entities:
                if set(item) - {"entity_kind", "entity_id", "callsign"}:
                    raise ContractValidationError("unexpected_entity_hint_field")
                if "entity_kind" not in item or "entity_id" not in item:
                    raise ContractValidationError("invalid_entity_hint_items")
                callsign = item["callsign"] if "callsign" in item else None
                entities.append((item["entity_kind"], item["entity_id"], callsign))
            entity_items = tuple(entities)
        return cls(
            user_text=_required(data, "user_text"),
            instruction_ref=(
                data["instruction_ref"] if "instruction_ref" in data else None
            ),
            tools=tools,
            tool_results=tool_results,
            validation_feedback=validation_feedback,
            prior_turns=prior_turns,
            selector_hint_items=selector_items,
            entity_hint_items=entity_items,
        )


_DECISION_KEYS = frozenset(
    {
        "schema_version",
        "kind",
        "tool_calls",
        "unsupported_reason",
        "refusal_code",
        "claims",
    }
)


@dataclass(frozen=True)
class LiveOpsModelDecision:
    """Exactly one provider-neutral Live Ops decision kind.

    REFUSAL is a model refusal. It is not a provider transport failure.
    FINAL_CLAIMS requires 1 to 32 Live Ops claims. A FINAL_CLAIMS value
    with no claims was valid in LO1 and is rejected. TOOL_CALLS,
    UNSUPPORTED, and REFUSAL still omit claims from ``to_dict``.
    Schema version remains ``wilvor.ai.live_ops_model_decision.v1``.
    """

    kind: LiveOpsModelDecisionKind
    tool_calls: tuple[ProposedToolCall, ...] = ()
    unsupported_reason: LiveOpsUnsupportedReason | None = None
    refusal_code: str | None = None
    claims: tuple[LiveOpsClaim, ...] = ()
    schema_version: str = LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.kind, LiveOpsModelDecisionKind):
            errors.append("invalid_kind")
        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(item, ProposedToolCall) for item in self.tool_calls
        ):
            errors.append("invalid_tool_calls")
        if not isinstance(self.claims, tuple) or any(
            not is_live_ops_claim(item) for item in self.claims
        ):
            errors.append("invalid_claims")
        elif len(self.claims) > LIVE_OPS_MAX_CLAIMS_PER_RESPONSE:
            errors.append("too_many_claims")
        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, LiveOpsUnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")
        errors.extend(
            _validate_bounded_text(
                self.refusal_code,
                "refusal_code",
                optional=True,
                max_length=PROVENANCE_TOKEN_MAX_LENGTH,
            )
        )
        if isinstance(self.kind, LiveOpsModelDecisionKind):
            errors.extend(self._kind_errors())
        if errors:
            raise ContractValidationError(errors)

    def _kind_errors(self) -> list[str]:
        if self.kind is LiveOpsModelDecisionKind.TOOL_CALLS:
            errors: list[str] = []
            if not self.tool_calls:
                errors.append("tool_calls_required")
            if self.unsupported_reason is not None:
                errors.append("tool_calls_forbids_unsupported_reason")
            if self.refusal_code is not None:
                errors.append("tool_calls_forbids_refusal_code")
            if self.claims:
                errors.append("tool_calls_forbid_claims")
            return errors
        if self.kind is LiveOpsModelDecisionKind.FINAL_CLAIMS:
            errors = []
            if self.tool_calls:
                errors.append("final_claims_forbids_tool_calls")
            if self.unsupported_reason is not None:
                errors.append("final_claims_forbids_unsupported_reason")
            if self.refusal_code is not None:
                errors.append("final_claims_forbids_refusal_code")
            if not self.claims:
                errors.append("final_claims_require_claims")
            return errors
        if self.kind is LiveOpsModelDecisionKind.UNSUPPORTED:
            errors = []
            if self.unsupported_reason is None:
                errors.append("unsupported_requires_reason")
            if self.tool_calls:
                errors.append("unsupported_forbids_tool_calls")
            if self.refusal_code is not None:
                errors.append("unsupported_forbids_refusal_code")
            if self.claims:
                errors.append("unsupported_forbids_claims")
            return errors
        errors = []
        if not self.refusal_code:
            errors.append("refusal_requires_code")
        if self.tool_calls:
            errors.append("refusal_forbids_tool_calls")
        if self.unsupported_reason is not None:
            errors.append("refusal_forbids_unsupported_reason")
        if self.claims:
            errors.append("refusal_forbids_claims")
        return errors

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
            "tool_calls": [item.to_dict() for item in self.tool_calls],
            "unsupported_reason": (
                None
                if self.unsupported_reason is None
                else self.unsupported_reason.value
            ),
            "refusal_code": self.refusal_code,
        }
        if self.kind is LiveOpsModelDecisionKind.FINAL_CLAIMS:
            payload["claims"] = [item.to_dict() for item in self.claims]
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsModelDecision":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_model_decision")
        if set(data) - _DECISION_KEYS:
            raise ContractValidationError("unexpected_live_ops_decision_field")
        kind = _parse_enum(
            LiveOpsModelDecisionKind,
            _required(data, "kind"),
            "kind",
        )
        if kind is LiveOpsModelDecisionKind.FINAL_CLAIMS and "claims" not in data:
            raise ContractValidationError("final_claims_require_claims")
        claims: tuple[LiveOpsClaim, ...] = ()
        if "claims" in data:
            claims = tuple(
                live_ops_claim_from_dict(item)
                for item in _sequence(data["claims"], "claims")
            )
        unsupported = data.get("unsupported_reason")
        return cls(
            kind=kind,
            tool_calls=tuple(
                ProposedToolCall.from_dict(item)
                for item in _sequence(data.get("tool_calls", ()), "tool_calls")
            ),
            unsupported_reason=(
                None
                if unsupported is None
                else _parse_enum(
                    LiveOpsUnsupportedReason,
                    unsupported,
                    "unsupported_reason",
                )
            ),
            refusal_code=data["refusal_code"] if "refusal_code" in data else None,
            claims=claims,
            schema_version=_required(data, "schema_version"),
        )


class LiveOpsModelProvider(Protocol):
    """Provider-neutral Live Ops completion. Not implemented in LO1."""

    def complete(self, request: LiveOpsModelTurnRequest) -> LiveOpsModelDecision: ...


__all__ = [
    "LIVE_OPS_ARGUMENT_STRING_MAX",
    "LIVE_OPS_HAZARD_IDS_MAX",
    "LIVE_OPS_INSTRUCTION_REF",
    "LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION",
    "LIVE_OPS_MODEL_VISIBLE_LIST_MAX",
    "LIVE_OPS_PROJECTED_STRING_MAX",
    "LIVE_OPS_PROJECTION_JSON_MAX_BYTES",
    "MAX_TOOL_CALLS_PER_BATCH",
    "LiveOpsModelDecision",
    "LiveOpsModelDecisionKind",
    "LiveOpsModelProvider",
    "LiveOpsModelTurnRequest",
    "LiveOpsProviderFailureKind",
    "LiveOpsUnsupportedReason",
    "LiveOpsValidationCode",
    "LiveOpsValidationFailure",
]
