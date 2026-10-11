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


def _validate_instruction_ref(value: Any) -> list[str]:
    errors = _validate_bounded_text(value, "instruction_ref", optional=True)
    if errors or value is None:
        return errors
    if INSTRUCTION_REF_PATTERN.fullmatch(value) is None:
        return ["invalid_instruction_ref"]
    return []


@dataclass(frozen=True)
class LiveOpsModelTurnRequest:
    """Provider-neutral Live Ops turn. Not a vendor transcript."""

    user_text: str
    instruction_ref: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    tool_results: tuple[Any, ...] = ()
    validation_feedback: LiveOpsValidationFailure | None = None

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
        if errors:
            raise ContractValidationError(errors)

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
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsModelTurnRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_model_turn_request")
        tools: tuple[ToolSchema, ...] = ()
        tool_results: tuple[Any, ...] = ()
        validation_feedback = None
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
        return cls(
            user_text=_required(data, "user_text"),
            instruction_ref=(
                data["instruction_ref"] if "instruction_ref" in data else None
            ),
            tools=tools,
            tool_results=tool_results,
            validation_feedback=validation_feedback,
        )


@dataclass(frozen=True)
class LiveOpsModelDecision:
    """Exactly one provider-neutral Live Ops decision kind.

    REFUSAL is a model refusal. It is not a provider transport failure.
    FINAL_CLAIMS is reserved; LO1 does not attach operational claims.
    """

    kind: LiveOpsModelDecisionKind
    tool_calls: tuple[ProposedToolCall, ...] = ()
    unsupported_reason: LiveOpsUnsupportedReason | None = None
    refusal_code: str | None = None
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
            return errors
        if self.kind is LiveOpsModelDecisionKind.FINAL_CLAIMS:
            errors = []
            if self.tool_calls:
                errors.append("final_claims_forbids_tool_calls")
            if self.unsupported_reason is not None:
                errors.append("final_claims_forbids_unsupported_reason")
            if self.refusal_code is not None:
                errors.append("final_claims_forbids_refusal_code")
            return errors
        if self.kind is LiveOpsModelDecisionKind.UNSUPPORTED:
            errors = []
            if self.unsupported_reason is None:
                errors.append("unsupported_requires_reason")
            if self.tool_calls:
                errors.append("unsupported_forbids_tool_calls")
            if self.refusal_code is not None:
                errors.append("unsupported_forbids_refusal_code")
            return errors
        errors = []
        if not self.refusal_code:
            errors.append("refusal_requires_code")
        if self.tool_calls:
            errors.append("refusal_forbids_tool_calls")
        if self.unsupported_reason is not None:
            errors.append("refusal_forbids_unsupported_reason")
        return errors

    def to_dict(self) -> dict[str, JsonValue]:
        return {
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

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveOpsModelDecision":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_live_ops_model_decision")
        unsupported = data.get("unsupported_reason")
        return cls(
            kind=_parse_enum(
                LiveOpsModelDecisionKind,
                _required(data, "kind"),
                "kind",
            ),
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
