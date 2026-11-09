"""Provider-neutral specialist contracts for Phase 3A-preflight.

These types describe specialist request, trusted invocation context, typed
claims, and result envelopes. They do not run a specialist, call a model,
verify evidence, render answers, or import historical/query runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    TOOL_INPUT_FIELD_NAME_MAX_LENGTH,
    TOOL_INPUT_FIELD_NAME_PATTERN,
    ContractValidationError,
    JsonValue,
    TemporalScope,
    ToolResult,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_optional_count,
    _validate_string_tuple,
    _validate_utc,
)


SPECIALIST_RESULT_SCHEMA_VERSION = "wilvor.ai.specialist_result.v1"
SPECIALIST_REQUEST_TEXT_MAX_LENGTH = 8192
SPECIALIST_ANSWER_MAX_LENGTH = 8192
CLAIM_CODE_MAX_LENGTH = PROVENANCE_TOKEN_MAX_LENGTH
CLAIM_CODE_PATTERN = TOOL_INPUT_FIELD_NAME_PATTERN
FORBIDDEN_CLAIM_PROSE_KEYS = frozenset(
    {
        "claim_text",
        "statement",
        "answer",
        "answer_text",
        "candidate_answer",
        "factual_text",
    }
)


class SpecialistStatus(str, Enum):
    """Closed specialist outcome statuses.

    ANSWERED: verified deterministic historical factual response.
    PARTIAL: verified response whose evidence is explicitly incomplete.
    UNSUPPORTED: this specialist cannot support the request.
    UNAVAILABLE: required evidence was unavailable or integrity failed.
    INVALID_REQUEST: request/tool arguments could not form a valid operation.
    PROVIDER_FAILED: the model/provider produced no valid ModelDecision.
    """

    ANSWERED = "ANSWERED"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_REQUEST = "INVALID_REQUEST"
    PROVIDER_FAILED = "PROVIDER_FAILED"


class UnsupportedReason(str, Enum):
    """Closed capability reasons. Not model-authored prose."""

    CURRENT_STATE = "CURRENT_STATE"
    GEOGRAPHY = "GEOGRAPHY"
    FORECAST = "FORECAST"
    ACTION_REQUEST = "ACTION_REQUEST"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_CATALOG = "OUT_OF_CATALOG"


class SpecialistClaimKind(str, Enum):
    """Closed operational claim kinds. UNSUPPORTED is a status, not a claim."""

    EXACT_COUNT = "EXACT_COUNT"
    LOWER_BOUND_COUNT = "LOWER_BOUND_COUNT"
    VERIFIED_ZERO = "VERIFIED_ZERO"
    HISTORICAL_WINDOW = "HISTORICAL_WINDOW"
    RECORD_IDENTITY = "RECORD_IDENTITY"
    LIMITATION = "LIMITATION"
    UNAVAILABLE = "UNAVAILABLE"


class VerifierOutcome(str, Enum):
    """Claim-verification state. This module does not run a verifier.

    NOT_RUN: no claim verification occurred.
    PASSED: claims used for factual rendering were deterministically verified.
    FAILED: the proposed claim set failed deterministic verification.
    """

    NOT_RUN = "NOT_RUN"
    PASSED = "PASSED"
    FAILED = "FAILED"


def _validate_identifier(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
    max_length: int = CLAIM_CODE_MAX_LENGTH,
) -> list[str]:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=max_length,
    )
    if errors or value is None:
        return errors
    if CLAIM_CODE_PATTERN.fullmatch(value) is None:
        return [f"invalid_{field_name}"]
    return []


def _validate_metric_id(value: Any) -> list[str]:
    return _validate_identifier(
        value,
        "metric_id",
        max_length=TOOL_INPUT_FIELD_NAME_MAX_LENGTH,
    )


def _validate_tool_call_id(value: Any) -> list[str]:
    return _validate_bounded_text(value, "tool_call_id")


def _validate_required_count(value: Any, field_name: str) -> list[str]:
    if value is None:
        return [f"invalid_{field_name}"]
    return _validate_optional_count(value, field_name)


def _reject_prose_keys(data: Mapping[str, Any], envelope: str) -> None:
    present = FORBIDDEN_CLAIM_PROSE_KEYS.intersection(data)
    if present:
        raise ContractValidationError(f"unexpected_{envelope}_prose")


def _parse_utc_instant(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class SpecialistRequest:
    """Single-turn specialist request. Not historical evidence.

    ``request_id`` identifies the caller/invocation for audit. It is not an
    evaluation instant and not trusted tool context.
    """

    text: str
    request_id: str | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.text,
            "text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        errors.extend(
            _validate_bounded_text(
                self.request_id,
                "request_id",
                optional=True,
            )
        )
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {"text": self.text}
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpecialistRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_specialist_request")
        return cls(
            text=_required(data, "text"),
            request_id=data["request_id"] if "request_id" in data else None,
        )


@dataclass(frozen=True)
class HistoricalSpecialistTrustedContext:
    """Per-invocation trusted values. Constructor dependencies are not here.

    ``as_of_utc`` is the requested deterministic evaluation instant that a
    future specialist will inject into historical tools IF they execute. It
    is not automatically SpecialistResult evidence.

    Future composition binds provider and HistoricalAnalyticsOperations on
    the specialist constructor, not this context. ``run(request, context)``
    does not accept operations, provider, or AWS clients.
    """

    as_of_utc: str

    def __post_init__(self) -> None:
        errors = _validate_utc(self.as_of_utc, "as_of_utc")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"as_of_utc": self.as_of_utc}

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
    ) -> "HistoricalSpecialistTrustedContext":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_trusted_context")
        return cls(as_of_utc=_required(data, "as_of_utc"))


@dataclass(frozen=True)
class ExactCountClaim:
    tool_call_id: str
    metric_id: str
    value: int

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(_validate_metric_id(self.metric_id))
        errors.extend(_validate_required_count(self.value, "value"))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.EXACT_COUNT.value,
            "tool_call_id": self.tool_call_id,
            "metric_id": self.metric_id,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ExactCountClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            metric_id=_required(data, "metric_id"),
            value=_required(data, "value"),
        )


@dataclass(frozen=True)
class LowerBoundCountClaim:
    tool_call_id: str
    metric_id: str
    minimum_value: int

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(_validate_metric_id(self.metric_id))
        errors.extend(_validate_required_count(self.minimum_value, "minimum_value"))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.LOWER_BOUND_COUNT.value,
            "tool_call_id": self.tool_call_id,
            "metric_id": self.metric_id,
            "minimum_value": self.minimum_value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LowerBoundCountClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            metric_id=_required(data, "metric_id"),
            minimum_value=_required(data, "minimum_value"),
        )


@dataclass(frozen=True)
class VerifiedZeroClaim:
    tool_call_id: str

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.VERIFIED_ZERO.value,
            "tool_call_id": self.tool_call_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "VerifiedZeroClaim":
        _reject_prose_keys(data, "claim")
        return cls(tool_call_id=_required(data, "tool_call_id"))


@dataclass(frozen=True)
class HistoricalWindowClaim:
    """Requested historical window represented by an executed tool.

    Interval semantics are half-open ``[start_utc, end_utc)``. Strings are
    validated with the Phase 0 UTC helper and must satisfy start < end.
    This module does not import historical query-window canonicalization.
    """

    tool_call_id: str
    start_utc: str
    end_utc: str

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(_validate_utc(self.start_utc, "start_utc"))
        errors.extend(_validate_utc(self.end_utc, "end_utc"))
        if not errors:
            if _parse_utc_instant(self.start_utc) >= _parse_utc_instant(self.end_utc):
                errors.append("start_utc_must_precede_end_utc")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.HISTORICAL_WINDOW.value,
            "tool_call_id": self.tool_call_id,
            "start_utc": self.start_utc,
            "end_utc": self.end_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HistoricalWindowClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            start_utc=_required(data, "start_utc"),
            end_utc=_required(data, "end_utc"),
        )


@dataclass(frozen=True)
class RecordIdentityClaim:
    tool_call_id: str
    record_id: str

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(
            _validate_bounded_text(
                self.record_id,
                "record_id",
                max_length=CLAIM_CODE_MAX_LENGTH,
            )
        )
        if (
            isinstance(self.record_id, str)
            and self.record_id.strip()
            and any(character.isspace() for character in self.record_id)
        ):
            errors.append("invalid_record_id")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.RECORD_IDENTITY.value,
            "tool_call_id": self.tool_call_id,
            "record_id": self.record_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RecordIdentityClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            record_id=_required(data, "record_id"),
        )


@dataclass(frozen=True)
class LimitationClaim:
    """Optional model reference to a limitation code.

    Model LIMITATION claims do not control whether mandatory safety
    limitations reach a future final answer. A later deterministic
    specialist MUST independently propagate applicable ToolResult
    limitations, including RESULT_TRUNCATED,
    HAZARD_VERSION_WINDOW_LIMITATION, coverage/query unavailability, and
    mapping integrity failures. Omitting a LIMITATION claim cannot suppress
    those. This module does not implement that renderer.
    """

    tool_call_id: str
    limitation_code: str

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(_validate_identifier(self.limitation_code, "limitation_code"))
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.LIMITATION.value,
            "tool_call_id": self.tool_call_id,
            "limitation_code": self.limitation_code,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LimitationClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            limitation_code=_required(data, "limitation_code"),
        )


@dataclass(frozen=True)
class UnavailableClaim:
    tool_call_id: str
    error_or_coverage_code: str

    def __post_init__(self) -> None:
        errors = _validate_tool_call_id(self.tool_call_id)
        errors.extend(
            _validate_identifier(
                self.error_or_coverage_code,
                "error_or_coverage_code",
            )
        )
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "kind": SpecialistClaimKind.UNAVAILABLE.value,
            "tool_call_id": self.tool_call_id,
            "error_or_coverage_code": self.error_or_coverage_code,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "UnavailableClaim":
        _reject_prose_keys(data, "claim")
        return cls(
            tool_call_id=_required(data, "tool_call_id"),
            error_or_coverage_code=_required(data, "error_or_coverage_code"),
        )


SpecialistClaim = (
    ExactCountClaim
    | LowerBoundCountClaim
    | VerifiedZeroClaim
    | HistoricalWindowClaim
    | RecordIdentityClaim
    | LimitationClaim
    | UnavailableClaim
)

_CLAIM_FACTORIES: dict[SpecialistClaimKind, Any] = {
    SpecialistClaimKind.EXACT_COUNT: ExactCountClaim.from_dict,
    SpecialistClaimKind.LOWER_BOUND_COUNT: LowerBoundCountClaim.from_dict,
    SpecialistClaimKind.VERIFIED_ZERO: VerifiedZeroClaim.from_dict,
    SpecialistClaimKind.HISTORICAL_WINDOW: HistoricalWindowClaim.from_dict,
    SpecialistClaimKind.RECORD_IDENTITY: RecordIdentityClaim.from_dict,
    SpecialistClaimKind.LIMITATION: LimitationClaim.from_dict,
    SpecialistClaimKind.UNAVAILABLE: UnavailableClaim.from_dict,
}


def specialist_claim_from_dict(data: Mapping[str, Any]) -> SpecialistClaim:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_specialist_claim")
    _reject_prose_keys(data, "claim")
    kind = _parse_enum(
        SpecialistClaimKind,
        _required(data, "kind"),
        "kind",
    )
    return _CLAIM_FACTORIES[kind](data)


def _is_specialist_claim(value: object) -> bool:
    return isinstance(
        value,
        (
            ExactCountClaim,
            LowerBoundCountClaim,
            VerifiedZeroClaim,
            HistoricalWindowClaim,
            RecordIdentityClaim,
            LimitationClaim,
            UnavailableClaim,
        ),
    )


@dataclass(frozen=True)
class SpecialistResult:
    """Minimal specialist result. Answer is not model-authored operational prose.

    In V1, ``answer`` will be deterministic renderer output (3A.3). This
    contract does not render, verify, or copy trusted ``as_of_utc`` onto
    ``evaluated_as_of_utc``.

    ``evaluated_as_of_utc`` is the evidence-backed evaluation instant from
    executed ToolResults, or None when no historical evaluation occurred.
    It is not invocation time, provider time, or requested trusted as_of.

    ``temporal_scope`` is a generic field so a future Live Ops specialist can
    reuse this envelope. Historical runtime will later require HISTORICAL.
    HYBRID behavior is not introduced here.

    Mandatory safety limitations remain deterministic runtime/renderer
    concerns. Model claim omission cannot suppress them.
    """

    status: SpecialistStatus
    answer: str
    temporal_scope: TemporalScope
    evaluated_as_of_utc: str | None
    tool_results: tuple[ToolResult, ...]
    verified_claims: tuple[SpecialistClaim, ...]
    limitations: tuple[str, ...]
    unsupported_reason: UnsupportedReason | None
    verifier_outcome: VerifierOutcome
    schema_version: str = SPECIALIST_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        errors.extend(
            _validate_bounded_text(
                self.answer,
                "answer",
                max_length=SPECIALIST_ANSWER_MAX_LENGTH,
            )
        )
        errors.extend(
            _validate_utc(
                self.evaluated_as_of_utc,
                "evaluated_as_of_utc",
                optional=True,
            )
        )
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))

        if self.schema_version != SPECIALIST_RESULT_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.status, SpecialistStatus):
            errors.append("invalid_status")

        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")

        if not isinstance(self.verifier_outcome, VerifierOutcome):
            errors.append("invalid_verifier_outcome")

        if not isinstance(self.tool_results, tuple) or any(
            not isinstance(item, ToolResult) for item in self.tool_results
        ):
            errors.append("invalid_tool_results")

        if not isinstance(self.verified_claims, tuple) or any(
            not _is_specialist_claim(item) for item in self.verified_claims
        ):
            errors.append("invalid_verified_claims")

        if self.status is SpecialistStatus.UNSUPPORTED:
            if self.unsupported_reason is None:
                errors.append("unsupported_requires_reason")
            if self.verified_claims:
                errors.append("unsupported_forbids_verified_claims")
        else:
            if self.unsupported_reason is not None:
                errors.append("unsupported_reason_requires_unsupported_status")

        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, UnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "status": self.status.value,
            "answer": self.answer,
            "temporal_scope": self.temporal_scope.value,
            "evaluated_as_of_utc": self.evaluated_as_of_utc,
            "tool_results": [item.to_dict() for item in self.tool_results],
            "verified_claims": [item.to_dict() for item in self.verified_claims],
            "limitations": list(self.limitations),
            "unsupported_reason": (
                None
                if self.unsupported_reason is None
                else self.unsupported_reason.value
            ),
            "verifier_outcome": self.verifier_outcome.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SpecialistResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_specialist_result")

        tool_results = _sequence(_required(data, "tool_results"), "tool_results")
        claims = _sequence(_required(data, "verified_claims"), "verified_claims")
        limitations = _sequence(_required(data, "limitations"), "limitations")
        unsupported = data["unsupported_reason"] if "unsupported_reason" in data else None

        return cls(
            status=_parse_enum(
                SpecialistStatus,
                _required(data, "status"),
                "status",
            ),
            answer=_required(data, "answer"),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            evaluated_as_of_utc=_required(data, "evaluated_as_of_utc"),
            tool_results=tuple(ToolResult.from_dict(item) for item in tool_results),
            verified_claims=tuple(specialist_claim_from_dict(item) for item in claims),
            limitations=tuple(limitations),
            unsupported_reason=(
                None
                if unsupported is None
                else _parse_enum(
                    UnsupportedReason,
                    unsupported,
                    "unsupported_reason",
                )
            ),
            verifier_outcome=_parse_enum(
                VerifierOutcome,
                _required(data, "verifier_outcome"),
                "verifier_outcome",
            ),
            schema_version=_required(data, "schema_version"),
        )


__all__ = [
    "CLAIM_CODE_MAX_LENGTH",
    "CLAIM_CODE_PATTERN",
    "ExactCountClaim",
    "HistoricalSpecialistTrustedContext",
    "HistoricalWindowClaim",
    "LimitationClaim",
    "LowerBoundCountClaim",
    "RecordIdentityClaim",
    "SPECIALIST_ANSWER_MAX_LENGTH",
    "SPECIALIST_REQUEST_TEXT_MAX_LENGTH",
    "SPECIALIST_RESULT_SCHEMA_VERSION",
    "SpecialistClaim",
    "SpecialistClaimKind",
    "SpecialistRequest",
    "SpecialistResult",
    "SpecialistStatus",
    "UnavailableClaim",
    "UnsupportedReason",
    "VerifiedZeroClaim",
    "VerifierOutcome",
    "specialist_claim_from_dict",
]
