"""Decision-specific model turn and terminal decision contracts.

These types are siblings of the historical ModelTurnRequest and ModelDecision.
They do not call a model, verify claims, or import a provider SDK.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    FreshnessStatus,
    JsonValue,
    SourceRecord,
    TemporalScope,
    ToolInputValueType,
    ToolResultStatus,
    _is_json_value,
    _parse_enum,
    _required,
    _sequence,
    _validate_bounded_text,
    _validate_string_tuple,
)
from wilvor_ai.decision_claims import (
    DecisionClaim,
    _is_decision_claim,
    _reject_forbidden_tree,
    _require_evidence_ref,
    decision_claim_from_dict,
)
from wilvor_ai.decision_contracts import DecisionEvidence, DecisionEvidenceKind
from wilvor_ai.persisted_airport_contracts import PersistedAirportEvidence
from wilvor_ai.decision_tool_projection import (
    DecisionProjectedEvidence,
    DecisionToolProjection,
)
from wilvor_ai.model_contracts import (
    INSTRUCTION_REF_PATTERN,
    ModelDecisionKind,
    ProposedToolCall,
    ValidationFeedback,
)
from wilvor_ai.specialist_contracts import (
    CLAIM_CODE_MAX_LENGTH,
    CLAIM_CODE_PATTERN,
    FORBIDDEN_CLAIM_PROSE_KEYS,
    SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
)
from wilvor_ai.tool_schema import ToolSchema


DECISION_MODEL_DECISION_SCHEMA_VERSION = "wilvor.ai.decision_model_decision.v1"
DECISION_MODEL_TOOL_NAMES = (
    "get_current_decision_context",
    "get_current_risk_evidence",
    "get_current_recommendation",
    "get_persisted_airport_candidate_evidence",
)
DECISION_MODEL_TOOL_SIGNATURES: Mapping[str, str] = MappingProxyType(
    {
        "get_current_decision_context": "aircraft_id",
        "get_current_risk_evidence": "aircraft_id",
        "get_current_recommendation": "aircraft_id",
        "get_persisted_airport_candidate_evidence": "recommendation_id",
    }
)
_CURRENT_PROJECTION_KINDS = MappingProxyType(
    {
        "get_current_decision_context": DecisionEvidenceKind.DECISION_CONTEXT,
        "get_current_risk_evidence": DecisionEvidenceKind.RISK_EVIDENCE,
        "get_current_recommendation": DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
    }
)
_PERSISTED_PROJECTION_TOOL = "get_persisted_airport_candidate_evidence"

_KIND_FIELDS = {
    "TOOL_CALLS": frozenset({"tool_calls"}),
    "FINAL_CLAIMS": frozenset({"claims"}),
    "UNSUPPORTED": frozenset({"unsupported_reason"}),
    "REFUSAL": frozenset({"refusal_code"}),
}
_ALWAYS_ALLOWED_DECISION_KEYS = frozenset({"schema_version", "kind"})
_TURN_KEYS = frozenset(
    {
        "user_text",
        "instruction_ref",
        "tools",
        "evidence_snapshots",
        "validation_feedback",
    }
)
_VENDOR_TURN_KEYS = frozenset(
    {
        "role",
        "messages",
        "system",
        "assistant",
        "tool_use_id",
        "content",
        "chat_completions",
    }
)
_TRUSTED_TURN_KEYS = frozenset(
    {
        "tables",
        "now_epoch",
        "query_timestamp_utc",
        "correlation_id",
        "tool_call_id",
        "as_of_utc",
        "operations",
        "sql",
    }
)
_DECISION_PROSE_KEYS = frozenset(
    key.casefold() for key in (*FORBIDDEN_CLAIM_PROSE_KEYS, "explanation")
)
_PROJECTION_KEYS = frozenset(
    {
        "tool_name",
        "status",
        "temporal_scope",
        "as_of_utc",
        "data",
        "limitations",
        "evidence",
    }
)
_PROJECTED_EVIDENCE_KEYS = frozenset(
    {
        "source",
        "source_records",
        "temporal_scope",
        "freshness_status",
        "confidence",
        "limitations",
    }
)
_SOURCE_RECORD_KEYS = frozenset(
    {"record_id", "source_version", "event_timestamp_utc"}
)
_AUDIT_KEYS = frozenset(
    {
        "tool_call_id",
        "correlation_id",
        "tables",
        "now_epoch",
        "query_timestamp_utc",
    }
)
_TOOL_SCHEMA_KEYS = frozenset({"name", "description", "input_fields"})
_TOOL_INPUT_KEYS = frozenset({"name", "required", "value_type", "description"})
_VALIDATION_FEEDBACK_KEYS = frozenset({"code", "tool_name", "argument_name"})


class DecisionUnsupportedReason(str, Enum):
    """Closed Decision capability boundaries. Not model prose and not REFUSAL."""

    LIVE_OPS = "LIVE_OPS"
    HISTORICAL_ANALYTICS = "HISTORICAL_ANALYTICS"
    ROUTE_GENERATION_NOT_IMPLEMENTED = "ROUTE_GENERATION_NOT_IMPLEMENTED"
    SELECTED_DIVERSION_NOT_SUPPORTED = "SELECTED_DIVERSION_NOT_SUPPORTED"
    ACTION_REQUEST = "ACTION_REQUEST"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    OUT_OF_CATALOG = "OUT_OF_CATALOG"


def _exact_keys(
    data: Mapping[str, Any],
    allowed: frozenset[str],
    label: str,
    *,
    runtime_keys: frozenset[str] = _AUDIT_KEYS,
) -> None:
    extra = set(data) - allowed
    if extra & runtime_keys or any(
        isinstance(key, str) and key.casefold() in runtime_keys for key in extra
    ):
        raise ContractValidationError("forbidden_decision_field")
    if extra:
        raise ContractValidationError(f"unexpected_{label}_field")


def _reject_audit_tree(value: Any) -> None:
    """Reject runtime audit keys without treating stored projection facts as claims."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and key.casefold() in _AUDIT_KEYS:
                raise ContractValidationError("forbidden_decision_field")
            _reject_audit_tree(item)
        return
    if isinstance(value, list):
        for item in value:
            _reject_audit_tree(item)


def _signature_mismatch(schema: ToolSchema) -> bool:
    expected = DECISION_MODEL_TOOL_SIGNATURES.get(schema.name)
    if expected is None or len(schema.input_fields) != 1:
        return True
    field = schema.input_fields[0]
    return (
        field.name != expected
        or field.required is not True
        or field.value_type is not ToolInputValueType.STRING
    )


def _validate_projection_contents(projection: DecisionToolProjection) -> None:
    """Structurally check model-visible data. This does not verify a ToolResult."""

    if projection.tool_name in _CURRENT_PROJECTION_KINDS:
        expected_scope = TemporalScope.CURRENT
    elif projection.tool_name == _PERSISTED_PROJECTION_TOOL:
        expected_scope = TemporalScope.PERSISTED
    else:
        raise ContractValidationError("invalid_decision_tool_name")
    if (
        projection.temporal_scope is not expected_scope
        or projection.temporal_scope in {TemporalScope.HISTORICAL, TemporalScope.HYBRID}
    ):
        raise ContractValidationError("invalid_projection_temporal_scope")
    if any(
        item.temporal_scope is not projection.temporal_scope
        for item in projection.evidence
    ):
        raise ContractValidationError("invalid_projected_evidence_scope")
    _reject_audit_tree(projection.data)
    if not isinstance(projection.data, Mapping):
        raise ContractValidationError("invalid_projection_data")
    if projection.tool_name == _PERSISTED_PROJECTION_TOOL:
        PersistedAirportEvidence.from_dict(projection.data)
        return
    evidence = DecisionEvidence.from_dict(projection.data)
    if evidence.kind is not _CURRENT_PROJECTION_KINDS[projection.tool_name]:
        raise ContractValidationError("invalid_projection_evidence_kind")


def _decision_tool_schema_from_dict(data: Any) -> ToolSchema:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_tool_schema")
    _exact_keys(data, _TOOL_SCHEMA_KEYS, "tool_schema", runtime_keys=_TRUSTED_TURN_KEYS)
    for item in _sequence(_required(data, "input_fields"), "input_fields"):
        if not isinstance(item, Mapping):
            raise ContractValidationError("invalid_tool_input_field")
        _exact_keys(
            item,
            _TOOL_INPUT_KEYS,
            "tool_input_field",
            runtime_keys=_TRUSTED_TURN_KEYS,
        )
    return ToolSchema.from_dict(data)


def _validation_feedback_from_dict(data: Any) -> ValidationFeedback:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_validation_feedback")
    _exact_keys(
        data,
        _VALIDATION_FEEDBACK_KEYS,
        "validation_feedback",
        runtime_keys=_TRUSTED_TURN_KEYS,
    )
    return ValidationFeedback.from_dict(data)


def _string_tuple(data: Mapping[str, Any], field_name: str) -> tuple[str, ...]:
    values = tuple(_sequence(_required(data, field_name), field_name))
    errors = _validate_string_tuple(values, field_name)
    if errors:
        raise ContractValidationError(errors)
    return values


def _source_record_from_dict(data: Any) -> SourceRecord:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_source_record")
    _exact_keys(data, _SOURCE_RECORD_KEYS, "source_record")
    return SourceRecord.from_dict(data)


def _projected_evidence_from_dict(data: Any) -> DecisionProjectedEvidence:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_projected_evidence")
    _exact_keys(data, _PROJECTED_EVIDENCE_KEYS, "projected_evidence")
    records = tuple(
        _source_record_from_dict(item)
        for item in _sequence(_required(data, "source_records"), "source_records")
    )
    return DecisionProjectedEvidence(
        source=_required(data, "source"),
        source_records=records,
        temporal_scope=_parse_enum(
            TemporalScope,
            _required(data, "temporal_scope"),
            "temporal_scope",
        ),
        freshness_status=_parse_enum(
            FreshnessStatus,
            _required(data, "freshness_status"),
            "freshness_status",
        ),
        confidence=_parse_enum(
            ConfidenceLevel,
            _required(data, "confidence"),
            "confidence",
        ),
        limitations=_string_tuple(data, "limitations"),
    )


def _projection_from_dict(data: Any) -> DecisionToolProjection:
    if not isinstance(data, Mapping):
        raise ContractValidationError("invalid_decision_tool_projection")
    _exact_keys(data, _PROJECTION_KEYS, "projection")
    payload = _required(data, "data")
    if not _is_json_value(payload):
        raise ContractValidationError("invalid_projection_data")
    evidence = tuple(
        _projected_evidence_from_dict(item)
        for item in _sequence(_required(data, "evidence"), "evidence")
    )
    return DecisionToolProjection(
        tool_name=_required(data, "tool_name"),
        status=_parse_enum(ToolResultStatus, _required(data, "status"), "status"),
        temporal_scope=_parse_enum(
            TemporalScope,
            _required(data, "temporal_scope"),
            "temporal_scope",
        ),
        as_of_utc=_required(data, "as_of_utc"),
        data=copy.deepcopy(payload),
        limitations=_string_tuple(data, "limitations"),
        evidence=evidence,
    )


def _validate_instruction_ref(value: Any) -> list[str]:
    errors = _validate_bounded_text(value, "instruction_ref", optional=True)
    if errors or value is None:
        return errors
    if INSTRUCTION_REF_PATTERN.fullmatch(value) is None:
        return ["invalid_instruction_ref"]
    return []


def _validate_refusal_code(value: Any) -> list[str]:
    errors = _validate_bounded_text(
        value,
        "refusal_code",
        optional=True,
        max_length=CLAIM_CODE_MAX_LENGTH,
    )
    if errors or value is None:
        return errors
    if CLAIM_CODE_PATTERN.fullmatch(value) is None:
        return ["invalid_refusal_code"]
    return []


def _reject_decision_prose(data: Mapping[str, Any]) -> None:
    for key in data:
        if isinstance(key, str) and key.casefold() in _DECISION_PROSE_KEYS:
            raise ContractValidationError("unexpected_decision_prose")


@dataclass(frozen=True)
class DecisionEvidenceSnapshot:
    """Model-visible evidence handle plus the existing Decision projection.

    ``evidence_ref`` is not a tool_call_id, correlation id, or proof of a claim.
    """

    evidence_ref: str
    projection: DecisionToolProjection

    def __post_init__(self) -> None:
        _require_evidence_ref(self.evidence_ref, "evidence_ref")
        if not isinstance(self.projection, DecisionToolProjection):
            raise ContractValidationError("invalid_model_projection")
        _validate_projection_contents(self.projection)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "evidence_ref": self.evidence_ref,
            "projection": self.projection.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionEvidenceSnapshot":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_evidence_snapshot")
        _reject_audit_tree(data)
        _exact_keys(data, frozenset({"evidence_ref", "projection"}), "snapshot")
        return cls(
            evidence_ref=_required(data, "evidence_ref"),
            projection=_projection_from_dict(_required(data, "projection")),
        )


@dataclass(frozen=True)
class DecisionModelTurnRequest:
    """Provider-neutral Decision turn. Not a vendor transcript."""

    user_text: str
    instruction_ref: str | None = None
    tools: tuple[ToolSchema, ...] = ()
    evidence_snapshots: tuple[DecisionEvidenceSnapshot, ...] = ()
    validation_feedback: ValidationFeedback | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(
            self.user_text,
            "user_text",
            max_length=SPECIALIST_REQUEST_TEXT_MAX_LENGTH,
        )
        errors.extend(_validate_instruction_ref(self.instruction_ref))
        if not isinstance(self.tools, tuple) or any(
            not isinstance(item, ToolSchema) for item in self.tools
        ):
            errors.append("invalid_tools")
        elif self.tools:
            names = tuple(item.name for item in self.tools)
            if len(names) != len(set(names)) or set(names) != set(
                DECISION_MODEL_TOOL_NAMES
            ):
                errors.append("invalid_decision_tools")
            if any(_signature_mismatch(item) for item in self.tools):
                errors.append("invalid_decision_tool_signature")
        if not isinstance(self.evidence_snapshots, tuple) or any(
            not isinstance(item, DecisionEvidenceSnapshot)
            for item in self.evidence_snapshots
        ):
            errors.append("invalid_evidence_snapshots")
        elif len({item.evidence_ref for item in self.evidence_snapshots}) != len(
            self.evidence_snapshots
        ):
            errors.append("duplicate_evidence_ref")
        if (
            self.validation_feedback is not None
            and not isinstance(self.validation_feedback, ValidationFeedback)
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
        if self.evidence_snapshots:
            payload["evidence_snapshots"] = [
                item.to_dict() for item in self.evidence_snapshots
            ]
        if self.validation_feedback is not None:
            payload["validation_feedback"] = self.validation_feedback.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionModelTurnRequest":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_model_turn_request")
        if _VENDOR_TURN_KEYS.intersection(data):
            raise ContractValidationError("unexpected_vendor_turn_field")
        if _TRUSTED_TURN_KEYS.intersection(data):
            raise ContractValidationError("trusted_field_forbidden")
        _reject_decision_prose(data)
        extra = set(data) - _TURN_KEYS
        if extra:
            raise ContractValidationError("unexpected_turn_field")
        tools: tuple[ToolSchema, ...] = ()
        snapshots: tuple[DecisionEvidenceSnapshot, ...] = ()
        validation_feedback = None
        if "tools" in data:
            tools = tuple(
                _decision_tool_schema_from_dict(item)
                for item in _sequence(data["tools"], "tools")
            )
        if "evidence_snapshots" in data:
            snapshots = tuple(
                DecisionEvidenceSnapshot.from_dict(item)
                for item in _sequence(data["evidence_snapshots"], "evidence_snapshots")
            )
        if "validation_feedback" in data and data["validation_feedback"] is not None:
            validation_feedback = _validation_feedback_from_dict(
                data["validation_feedback"]
            )
        return cls(
            user_text=_required(data, "user_text"),
            instruction_ref=(
                data["instruction_ref"] if "instruction_ref" in data else None
            ),
            tools=tools,
            evidence_snapshots=snapshots,
            validation_feedback=validation_feedback,
        )


@dataclass(frozen=True)
class DecisionModelDecision:
    """Exactly one Decision terminal kind. Not a historical ModelDecision."""

    kind: ModelDecisionKind
    tool_calls: tuple[ProposedToolCall, ...] = ()
    claims: tuple[DecisionClaim, ...] = ()
    unsupported_reason: DecisionUnsupportedReason | None = None
    refusal_code: str | None = None
    schema_version: str = DECISION_MODEL_DECISION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        if self.schema_version != DECISION_MODEL_DECISION_SCHEMA_VERSION:
            errors.append("invalid_schema_version")
        if not isinstance(self.kind, ModelDecisionKind):
            errors.append("invalid_kind")
        if not isinstance(self.tool_calls, tuple) or any(
            not isinstance(item, ProposedToolCall) for item in self.tool_calls
        ):
            errors.append("invalid_tool_calls")
        if not isinstance(self.claims, tuple) or any(
            not _is_decision_claim(item) for item in self.claims
        ):
            errors.append("invalid_claims")
        if (
            self.unsupported_reason is not None
            and not isinstance(self.unsupported_reason, DecisionUnsupportedReason)
        ):
            errors.append("invalid_unsupported_reason")
        errors.extend(_validate_refusal_code(self.refusal_code))
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
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionModelDecision":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_decision_model_decision")
        _reject_forbidden_tree(data)
        _reject_decision_prose(data)
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
                decision_claim_from_dict(item)
                for item in _sequence(_required(data, "claims"), "claims")
            )
        elif kind is ModelDecisionKind.UNSUPPORTED:
            unsupported_reason = _parse_enum(
                DecisionUnsupportedReason,
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


__all__ = [
    "DECISION_MODEL_DECISION_SCHEMA_VERSION",
    "DECISION_MODEL_TOOL_NAMES",
    "DECISION_MODEL_TOOL_SIGNATURES",
    "DecisionEvidenceSnapshot",
    "DecisionModelDecision",
    "DecisionModelTurnRequest",
    "DecisionUnsupportedReason",
]
