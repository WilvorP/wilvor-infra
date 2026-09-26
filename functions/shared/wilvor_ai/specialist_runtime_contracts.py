"""Provider-neutral 3A.2 orchestration result contracts.

These types describe an unfinished specialist run. They do not verify
claims, render factual answers, or import AWS/provider SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    JsonValue,
    ToolResult,
    _is_json_value,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_optional_count,
    _validate_string_tuple,
    _validate_utc,
)
from wilvor_ai.model_contracts import (
    ModelDecisionKind,
    ValidationFeedback,
)
from wilvor_ai.specialist_contracts import (
    SpecialistClaim,
    UnsupportedReason,
    _is_specialist_claim,
    specialist_claim_from_dict,
)
from wilvor_ai.tool_result_projection import ToolResultProjection


SPECIALIST_RUN_RESULT_SCHEMA_VERSION = "wilvor.ai.specialist_run_result.v1"


class SpecialistRunStatus(str, Enum):
    """3A.2 orchestration outcomes. Not a verified SpecialistResult.status.

    PROPOSED_CLAIMS: the provider emitted FINAL_CLAIMS; claims are unverified.
    UNSUPPORTED: the provider declared a closed capability reason.
    INVALID_REQUEST: a second structurally invalid TOOL_CALLS decision.
    UNAVAILABLE: projection or evaluated as_of integrity failed.
    PROVIDER_FAILED: refusal, provider exception, invalid decision, or
    model-turn budget exhausted without a valid terminal decision.
    """

    PROPOSED_CLAIMS = "PROPOSED_CLAIMS"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_REQUEST = "INVALID_REQUEST"
    UNAVAILABLE = "UNAVAILABLE"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class SpecialistRuntimeErrorCode(str, Enum):
    """Closed runtime error codes. Not exception text or model prose."""

    MODEL_TURN_LIMIT_EXCEEDED = "model_turn_limit_exceeded"
    PROVIDER_EXCEPTION = "provider_exception"
    INVALID_MODEL_DECISION = "invalid_model_decision"
    PROJECTION_INTEGRITY_FAILED = "projection_integrity_failed"
    AS_OF_MISMATCH = "as_of_mismatch"
    REFUSAL = "refusal"
    ADAPTER_EXECUTION_FAILED = "adapter_execution_failed"


@dataclass(frozen=True)
class ExecutedToolInvocation:
    """Audit of one adapter dispatch. Not a model-authored claim."""

    tool_name: str
    tool_call_id: str
    requested_arguments: dict[str, JsonValue]
    executed_arguments: dict[str, JsonValue]
    list_limit_default_applied: bool
    canonical_key: str

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(self.tool_name, "tool_name")
        errors.extend(_validate_bounded_text(self.tool_call_id, "tool_call_id"))
        errors.extend(_validate_bounded_text(self.canonical_key, "canonical_key"))
        if not isinstance(self.requested_arguments, dict) or any(
            not isinstance(key, str) or not _is_json_value(value)
            for key, value in self.requested_arguments.items()
        ):
            errors.append("invalid_requested_arguments")
        if not isinstance(self.executed_arguments, dict) or any(
            not isinstance(key, str) or not _is_json_value(value)
            for key, value in self.executed_arguments.items()
        ):
            errors.append("invalid_executed_arguments")
        if not isinstance(self.list_limit_default_applied, bool):
            errors.append("invalid_list_limit_default_applied")
        if errors:
            raise ContractValidationError(errors)
        object.__setattr__(self, "requested_arguments", dict(self.requested_arguments))
        object.__setattr__(self, "executed_arguments", dict(self.executed_arguments))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "requested_arguments": dict(self.requested_arguments),
            "executed_arguments": dict(self.executed_arguments),
            "list_limit_default_applied": self.list_limit_default_applied,
            "canonical_key": self.canonical_key,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExecutedToolInvocation":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_executed_tool_invocation")
        requested = _required(data, "requested_arguments")
        executed = _required(data, "executed_arguments")
        if not isinstance(requested, Mapping) or not isinstance(executed, Mapping):
            raise ContractValidationError("invalid_executed_tool_invocation")
        return cls(
            tool_name=_required(data, "tool_name"),
            tool_call_id=_required(data, "tool_call_id"),
            requested_arguments=dict(requested),
            executed_arguments=dict(executed),
            list_limit_default_applied=_required(
                data,
                "list_limit_default_applied",
            ),
            canonical_key=_required(data, "canonical_key"),
        )


@dataclass(frozen=True)
class HistoricalSpecialistRunResult:
    """Internal 3A.2 orchestration result. Not a factual SpecialistResult.

    This envelope has no ``answer`` and no ``verified_claims``. 3A.3 consumes
    it and produces the final verified specialist result.
    """

    status: SpecialistRunStatus
    tool_results: tuple[ToolResult, ...]
    tool_result_projections: tuple[ToolResultProjection, ...]
    executed_invocations: tuple[ExecutedToolInvocation, ...]
    proposed_claims: tuple[SpecialistClaim, ...]
    unsupported_reason: UnsupportedReason | None
    evaluated_as_of_utc: str | None
    runtime_errors: tuple[str, ...]
    provider_turn_count: int
    executed_tool_call_count: int
    validation_feedback: tuple[ValidationFeedback, ...]
    terminal_kind: ModelDecisionKind | None = None
    refusal_code: str | None = None
    schema_version: str = SPECIALIST_RUN_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != SPECIALIST_RUN_RESULT_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.status, SpecialistRunStatus):
            errors.append("invalid_status")
        if (
            self.terminal_kind is not None
            and not isinstance(self.terminal_kind, ModelDecisionKind)
        ):
            errors.append("invalid_terminal_kind")
        if not isinstance(self.tool_results, tuple) or any(
            not isinstance(item, ToolResult) for item in self.tool_results
        ):
            errors.append("invalid_tool_results")
        if not isinstance(self.tool_result_projections, tuple) or any(
            not isinstance(item, ToolResultProjection)
            for item in self.tool_result_projections
        ):
            errors.append("invalid_tool_result_projections")
        if not isinstance(self.executed_invocations, tuple) or any(
            not isinstance(item, ExecutedToolInvocation)
            for item in self.executed_invocations
        ):
            errors.append("invalid_executed_invocations")
        if not isinstance(self.proposed_claims, tuple) or any(
            not _is_specialist_claim(item) for item in self.proposed_claims
        ):
            errors.append("invalid_proposed_claims")
        if not isinstance(self.validation_feedback, tuple) or any(
            not isinstance(item, ValidationFeedback)
            for item in self.validation_feedback
        ):
            errors.append("invalid_validation_feedback")
        errors.extend(_validate_string_tuple(self.runtime_errors, "runtime_errors"))
        errors.extend(
            _validate_utc(
                self.evaluated_as_of_utc,
                "evaluated_as_of_utc",
                optional=True,
            )
        )
        errors.extend(
            _validate_bounded_text(
                self.refusal_code,
                "refusal_code",
                optional=True,
            )
        )
        errors.extend(
            _validate_optional_count(
                self.provider_turn_count,
                "provider_turn_count",
            )
        )
        errors.extend(
            _validate_optional_count(
                self.executed_tool_call_count,
                "executed_tool_call_count",
            )
        )
        if self.provider_turn_count is None:
            errors.append("invalid_provider_turn_count")
        if self.executed_tool_call_count is None:
            errors.append("invalid_executed_tool_call_count")

        if self.status is SpecialistRunStatus.UNSUPPORTED:
            if self.unsupported_reason is None:
                errors.append("unsupported_requires_reason")
        elif self.unsupported_reason is not None:
            errors.append("unsupported_reason_requires_unsupported_status")
        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, UnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")

        if (
            self.status is SpecialistRunStatus.PROPOSED_CLAIMS
            and not self.proposed_claims
        ):
            errors.append("proposed_claims_required")
        if (
            self.status is not SpecialistRunStatus.PROPOSED_CLAIMS
            and self.proposed_claims
        ):
            errors.append("proposed_claims_require_proposed_claims_status")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "tool_results": [item.to_dict() for item in self.tool_results],
            "tool_result_projections": [
                item.to_dict() for item in self.tool_result_projections
            ],
            "executed_invocations": [
                item.to_dict() for item in self.executed_invocations
            ],
            "proposed_claims": [item.to_dict() for item in self.proposed_claims],
            "unsupported_reason": (
                None
                if self.unsupported_reason is None
                else self.unsupported_reason.value
            ),
            "evaluated_as_of_utc": self.evaluated_as_of_utc,
            "runtime_errors": list(self.runtime_errors),
            "provider_turn_count": self.provider_turn_count,
            "executed_tool_call_count": self.executed_tool_call_count,
            "validation_feedback": [
                item.to_dict() for item in self.validation_feedback
            ],
        }
        if self.terminal_kind is not None:
            payload["terminal_kind"] = self.terminal_kind.value
        if self.refusal_code is not None:
            payload["refusal_code"] = self.refusal_code
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistoricalSpecialistRunResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_specialist_run_result")
        tool_results = _sequence(_required(data, "tool_results"), "tool_results")
        projections = _sequence(
            _required(data, "tool_result_projections"),
            "tool_result_projections",
        )
        invocations = _sequence(
            _required(data, "executed_invocations"),
            "executed_invocations",
        )
        claims = _sequence(_required(data, "proposed_claims"), "proposed_claims")
        feedback = _sequence(
            _required(data, "validation_feedback"),
            "validation_feedback",
        )
        limitations = _sequence(_required(data, "runtime_errors"), "runtime_errors")
        unsupported = (
            data["unsupported_reason"] if "unsupported_reason" in data else None
        )
        terminal = data["terminal_kind"] if "terminal_kind" in data else None
        return cls(
            status=_parse_enum(
                SpecialistRunStatus,
                _required(data, "status"),
                "status",
            ),
            tool_results=tuple(ToolResult.from_dict(item) for item in tool_results),
            tool_result_projections=tuple(
                ToolResultProjection.from_dict(item) for item in projections
            ),
            executed_invocations=tuple(
                ExecutedToolInvocation.from_dict(item) for item in invocations
            ),
            proposed_claims=tuple(specialist_claim_from_dict(item) for item in claims),
            unsupported_reason=(
                None
                if unsupported is None
                else _parse_enum(
                    UnsupportedReason,
                    unsupported,
                    "unsupported_reason",
                )
            ),
            evaluated_as_of_utc=_required(data, "evaluated_as_of_utc"),
            runtime_errors=tuple(limitations),
            provider_turn_count=_required(data, "provider_turn_count"),
            executed_tool_call_count=_required(data, "executed_tool_call_count"),
            validation_feedback=tuple(
                ValidationFeedback.from_dict(item) for item in feedback
            ),
            terminal_kind=(
                None
                if terminal is None
                else _parse_enum(ModelDecisionKind, terminal, "terminal_kind")
            ),
            refusal_code=data["refusal_code"] if "refusal_code" in data else None,
            schema_version=_required(data, "schema_version"),
        )


__all__ = [
    "SPECIALIST_RUN_RESULT_SCHEMA_VERSION",
    "ExecutedToolInvocation",
    "HistoricalSpecialistRunResult",
    "SpecialistRunStatus",
    "SpecialistRuntimeErrorCode",
]
