"""Provider-neutral model turn and decision contracts for Phase 3A-preflight.

These types describe what a future ModelProvider must return. They do not
call a model, implement retries, or import vendor SDKs.

REFUSAL means the provider/model did not perform the requested valid turn.
UNSUPPORTED is a domain/capability judgment on ModelDecision and must not be
used by a provider to redefine Wilvor capability boundaries. A future
runtime should normally map REFUSAL to SpecialistStatus.PROVIDER_FAILED.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol

from wilvor_ai.contracts import (
    TOOL_INPUT_FIELD_NAME_MAX_LENGTH,
    TOOL_INPUT_FIELD_NAME_PATTERN,
    ContractValidationError,
    JsonValue,
    _is_json_value,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
)
from wilvor_ai.specialist_contracts import (
    SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
    SpecialistClaim,
    UnsupportedReason,
    _is_specialist_claim,
    _reject_prose_keys,
    _validate_identifier,
    specialist_claim_from_dict,
)


MODEL_DECISION_SCHEMA_VERSION = "wilvor.ai.model_decision.v1"
FORBIDDEN_DECISION_PROSE_KEYS = frozenset(
    {
        "answer",
        "candidate_answer",
        "answer_text",
        "factual_text",
    }
)
_KIND_FIELDS = {
    "TOOL_CALLS": frozenset({"tool_calls"}),
    "FINAL_CLAIMS": frozenset({"claims"}),
    "UNSUPPORTED": frozenset({"unsupported_reason"}),
    "REFUSAL": frozenset({"refusal_code"}),
}
_ALWAYS_ALLOWED_DECISION_KEYS = frozenset({"schema_version", "kind"})


class ModelDecisionKind(str, Enum):
    TOOL_CALLS = "TOOL_CALLS"
    FINAL_CLAIMS = "FINAL_CLAIMS"
    UNSUPPORTED = "UNSUPPORTED"
    REFUSAL = "REFUSAL"


@dataclass(frozen=True)
class ProposedToolCall:
    """Provider-neutral proposed tool call. Not a vendor response object.

    ``name`` and ``arguments`` are model-proposed only. Trusted runtime
    values (as_of_utc, operations, tool_call_id) are not part of this
    object. A future specialist runtime rejects unapproved names/arguments
    before dispatch.

    Future 3A.2 list policy for list_historical_encounters (not enforced
    here; Phase 2B LIST_DEFAULT_LIMIT=100 and LIST_MAX_LIMIT=200 stay
    unchanged):

    - omitted limit: specialist dispatcher may apply explicit default 20
    - supplied 1..25: execute unchanged
    - supplied >25 or invalid: reject the proposed call, execute zero
      historical operations for it, and allow one bounded correction
    - never silently clamp
    """

    name: str
    arguments: dict[str, JsonValue]

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.name,
            "name",
            max_length=TOOL_INPUT_FIELD_NAME_MAX_LENGTH,
        )
        if (
            isinstance(self.name, str)
            and self.name.strip()
            and TOOL_INPUT_FIELD_NAME_PATTERN.fullmatch(self.name) is None
        ):
            errors.append("invalid_name")

        if not isinstance(self.arguments, dict):
            errors.append("invalid_arguments")
        elif any(
            not isinstance(key, str) or not _is_json_value(value)
            for key, value in self.arguments.items()
        ):
            errors.append("invalid_arguments")

        if errors:
            raise ContractValidationError(errors)

        object.__setattr__(self, "arguments", dict(self.arguments))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "name": self.name,
            "arguments": dict(self.arguments),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProposedToolCall":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_proposed_tool_call")
        arguments = _required(data, "arguments")
        if not isinstance(arguments, Mapping):
            raise ContractValidationError("invalid_arguments")
        return cls(
            name=_required(data, "name"),
            arguments=dict(arguments),
        )


@dataclass(frozen=True)
class ModelDecision:
    """Exactly one provider-neutral decision kind.

    V1 does not carry model-authored operational prose. Factual user text
    is produced later by a deterministic renderer from verified claims.
    """

    kind: ModelDecisionKind
    tool_calls: tuple[ProposedToolCall, ...] = ()
    claims: tuple[SpecialistClaim, ...] = ()
    unsupported_reason: UnsupportedReason | None = None
    refusal_code: str | None = None
    schema_version: str = MODEL_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []

        if self.schema_version != MODEL_DECISION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.kind, ModelDecisionKind):
            errors.append("invalid_kind")

        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(item, ProposedToolCall) for item in self.tool_calls
        ):
            errors.append("invalid_tool_calls")

        if not isinstance(self.claims, tuple) or any(
            not _is_specialist_claim(item) for item in self.claims
        ):
            errors.append("invalid_claims")

        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, UnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")

        errors.extend(
            _validate_identifier(
                self.refusal_code,
                "refusal_code",
                optional=True,
            )
        )

        if isinstance(self.kind, ModelDecisionKind):
            errors.extend(self._kind_errors())

        if errors:
            raise ContractValidationError(errors)

    def _kind_errors(self) -> list[str]:
        if self.kind is ModelDecisionKind.TOOL_CALLS:
            errors: list[str] = []
            if not self.tool_calls:
                errors.append("tool_calls_required")
            if self.claims:
                errors.append("tool_calls_forbids_claims")
            if self.unsupported_reason is not None:
                errors.append("tool_calls_forbids_unsupported_reason")
            if self.refusal_code is not None:
                errors.append("tool_calls_forbids_refusal_code")
            return errors

        if self.kind is ModelDecisionKind.FINAL_CLAIMS:
            errors = []
            if not self.claims:
                errors.append("claims_required")
            if self.tool_calls:
                errors.append("final_claims_forbids_tool_calls")
            if self.unsupported_reason is not None:
                errors.append("final_claims_forbids_unsupported_reason")
            if self.refusal_code is not None:
                errors.append("final_claims_forbids_refusal_code")
            return errors

        if self.kind is ModelDecisionKind.UNSUPPORTED:
            errors = []
            if self.unsupported_reason is None:
                errors.append("unsupported_requires_reason")
            if self.tool_calls:
                errors.append("unsupported_forbids_tool_calls")
            if self.claims:
                errors.append("unsupported_forbids_claims")
            if self.refusal_code is not None:
                errors.append("unsupported_forbids_refusal_code")
            return errors

        errors = []
        if self.tool_calls:
            errors.append("refusal_forbids_tool_calls")
        if self.claims:
            errors.append("refusal_forbids_claims")
        if self.unsupported_reason is not None:
            errors.append("refusal_forbids_unsupported_reason")
        return errors

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "kind": self.kind.value,
        }
        if self.kind is ModelDecisionKind.TOOL_CALLS:
            payload["tool_calls"] = [item.to_dict() for item in self.tool_calls]
        elif self.kind is ModelDecisionKind.FINAL_CLAIMS:
            payload["claims"] = [item.to_dict() for item in self.claims]
        elif self.kind is ModelDecisionKind.UNSUPPORTED:
            payload["unsupported_reason"] = (
                None
                if self.unsupported_reason is None
                else self.unsupported_reason.value
            )
        elif self.refusal_code is not None:
            payload["refusal_code"] = self.refusal_code
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelDecision":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_model_decision")
        _reject_prose_keys(data, "decision")
        unexpected = FORBIDDEN_DECISION_PROSE_KEYS.intersection(data)
        if unexpected:
            raise ContractValidationError("unexpected_decision_prose")

        kind_value = _required(data, "kind")
        kind = _parse_enum(ModelDecisionKind, kind_value, "kind")
        allowed = _ALWAYS_ALLOWED_DECISION_KEYS | _KIND_FIELDS[kind.value]
        extra = set(data) - allowed
        if extra:
            raise ContractValidationError("unexpected_decision_field")

        tool_calls = ()
        claims = ()
        unsupported_reason = None
        refusal_code = None

        if kind is ModelDecisionKind.TOOL_CALLS:
            tool_calls = tuple(
                ProposedToolCall.from_dict(item)
                for item in _sequence(_required(data, "tool_calls"), "tool_calls")
            )
        elif kind is ModelDecisionKind.FINAL_CLAIMS:
            claims = tuple(
                specialist_claim_from_dict(item)
                for item in _sequence(_required(data, "claims"), "claims")
            )
        elif kind is ModelDecisionKind.UNSUPPORTED:
            unsupported_reason = _parse_enum(
                UnsupportedReason,
                _required(data, "unsupported_reason"),
                "unsupported_reason",
            )
        elif "refusal_code" in data:
            refusal_code = data["refusal_code"]

        return cls(
            kind=kind,
            tool_calls=tool_calls,
            claims=claims,
            unsupported_reason=unsupported_reason,
            refusal_code=refusal_code,
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class ModelTurnRequest:
    """Narrow provider-neutral turn request.

    Only stable fields exist in 3A-preflight. ToolSchema objects and
    model-visible ToolResult projections do not exist yet; 3A.1 can add
    optional fields with defaults without changing ``ModelProvider.complete``.
    This is not a vendor chat-message transcript and not a prompt document.
    """

    user_text: str
    instruction_ref: str | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.user_text,
            "user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        errors.extend(
            _validate_identifier(
                self.instruction_ref,
                "instruction_ref",
                optional=True,
            )
        )
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"user_text": self.user_text}
        if self.instruction_ref is not None:
            payload["instruction_ref"] = self.instruction_ref
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelTurnRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_model_turn_request")
        return cls(
            user_text=_required(data, "user_text"),
            instruction_ref=(
                data["instruction_ref"] if "instruction_ref" in data else None
            ),
        )


class ModelProvider(Protocol):
    """Provider-neutral completion protocol. No SDK, HTTP, or credentials."""

    def complete(self, request: ModelTurnRequest) -> ModelDecision:
        """Return exactly one ModelDecision for this turn."""


__all__ = [
    "FORBIDDEN_DECISION_PROSE_KEYS",
    "MODEL_DECISION_SCHEMA_VERSION",
    "ModelDecision",
    "ModelDecisionKind",
    "ModelProvider",
    "ModelTurnRequest",
    "ProposedToolCall",
]
