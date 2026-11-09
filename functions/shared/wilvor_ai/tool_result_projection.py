"""Model-visible projections of audit-grade ToolResult values.

Projection is a derived view. The original ToolResult remains the audit and
verifier authority. This module does not verify claims, render answers,
map statuses, or synthesize freshness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    Evidence,
    JsonValue,
    MatchCardinality,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _parse_enum,
    _required,
    _sequence,
    _validate_string_tuple,
    _validate_text,
    _validate_utc,
)


MODEL_VISIBLE_RECORD_MAX = 25
FORBIDDEN_APPLICATION_DATA_KEYS = frozenset(
    {
        "coverage",
        "evidence",
        "traceback",
        "exception",
        "sql",
        "query_string",
        "workgroup",
        "database",
        "output_location",
        "bucket",
        "s3_bucket",
        "athena",
    }
)
AUDIT_ONLY_TRACE_KEYS = frozenset(
    {
        "query_id",
        "execution_id",
        "rows_returned",
        "bytes_scanned",
        "engine_scope",
        "workgroup",
        "database",
        "output_location",
        "query_executions",
    }
)


class ToolResultProjectionError(ContractValidationError):
    """Raised when a ToolResult cannot be safely projected for a model."""


@dataclass(frozen=True)
class ProjectedEvidence:
    """Safety-critical evidence visible to a model. No query executions."""

    source: str
    completeness: SourceCompleteness | None
    match_cardinality: MatchCardinality | None
    error_code: str | None
    source_records: tuple[SourceRecord, ...]
    limitations: tuple[str, ...]
    temporal_scope: TemporalScope

    def __post_init__(self) -> None:
        errors = _validate_text(self.source, "source")
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if self.completeness is not None and not isinstance(
            self.completeness,
            SourceCompleteness,
        ):
            errors.append("invalid_completeness")
        if self.match_cardinality is not None and not isinstance(
            self.match_cardinality,
            MatchCardinality,
        ):
            errors.append("invalid_match_cardinality")
        if not isinstance(self.source_records, tuple) or any(
            not isinstance(item, SourceRecord) for item in self.source_records
        ):
            errors.append("invalid_source_records")
        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")
        if (
            isinstance(self.error_code, str) is False
            and self.error_code is not None
        ):
            errors.append("invalid_error_code")
        if errors:
            raise ToolResultProjectionError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "source": self.source,
            "error_code": self.error_code,
            "source_records": [item.to_dict() for item in self.source_records],
            "limitations": list(self.limitations),
            "temporal_scope": self.temporal_scope.value,
        }
        if self.completeness is not None:
            payload["completeness"] = self.completeness.to_dict()
        if self.match_cardinality is not None:
            payload["match_cardinality"] = self.match_cardinality.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ProjectedEvidence":
        if not isinstance(data, Mapping):
            raise ToolResultProjectionError("invalid_projected_evidence")
        records = _sequence(_required(data, "source_records"), "source_records")
        limitations = _sequence(_required(data, "limitations"), "limitations")
        return cls(
            source=_required(data, "source"),
            completeness=(
                SourceCompleteness.from_dict(data["completeness"])
                if data.get("completeness") is not None
                else None
            ),
            match_cardinality=(
                MatchCardinality.from_dict(data["match_cardinality"])
                if data.get("match_cardinality") is not None
                else None
            ),
            error_code=data["error_code"] if "error_code" in data else None,
            source_records=tuple(SourceRecord.from_dict(item) for item in records),
            limitations=tuple(limitations),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
        )


@dataclass(frozen=True)
class ToolResultProjection:
    """Model-visible ToolResult view. Not a new evidence authority."""

    tool_name: str
    tool_call_id: str
    status: ToolResultStatus
    temporal_scope: TemporalScope
    as_of_utc: str | None
    operation: str | None
    requested_scope: JsonValue
    result: JsonValue
    limitations: tuple[str, ...]
    error_code: str | None
    evidence: tuple[ProjectedEvidence, ...]

    def __post_init__(self) -> None:
        errors = _validate_text(self.tool_name, "tool_name")
        errors.extend(_validate_text(self.tool_call_id, "tool_call_id"))
        errors.extend(_validate_utc(self.as_of_utc, "as_of_utc", optional=True))
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if self.operation is not None:
            errors.extend(_validate_text(self.operation, "operation"))
        if not isinstance(self.status, ToolResultStatus):
            errors.append("invalid_status")
        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, ProjectedEvidence) for item in self.evidence
        ):
            errors.append("invalid_evidence")
        if errors:
            raise ToolResultProjectionError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "status": self.status.value,
            "temporal_scope": self.temporal_scope.value,
            "as_of_utc": self.as_of_utc,
            "operation": self.operation,
            "requested_scope": self.requested_scope,
            "result": self.result,
            "limitations": list(self.limitations),
            "error_code": self.error_code,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolResultProjection":
        if not isinstance(data, Mapping):
            raise ToolResultProjectionError("invalid_tool_result_projection")
        evidence = _sequence(_required(data, "evidence"), "evidence")
        limitations = _sequence(_required(data, "limitations"), "limitations")
        return cls(
            tool_name=_required(data, "tool_name"),
            tool_call_id=_required(data, "tool_call_id"),
            status=_parse_enum(
                ToolResultStatus,
                _required(data, "status"),
                "status",
            ),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            as_of_utc=_required(data, "as_of_utc"),
            operation=data["operation"] if "operation" in data else None,
            requested_scope=_required(data, "requested_scope"),
            result=_required(data, "result"),
            limitations=tuple(limitations),
            error_code=data["error_code"] if "error_code" in data else None,
            evidence=tuple(ProjectedEvidence.from_dict(item) for item in evidence),
        )


def project_tool_result(result: ToolResult) -> ToolResultProjection:
    """Derive a model-visible view without mutating the audit ToolResult."""

    if not isinstance(result, ToolResult):
        raise TypeError("result must be a ToolResult")

    errors: list[str] = []
    if result.temporal_scope is not TemporalScope.HISTORICAL:
        errors.append("projection_requires_historical_scope")
    if not isinstance(result.tool_call_id, str) or not result.tool_call_id.strip():
        errors.append("invalid_tool_call_id")

    data = result.data
    if data is not None and not isinstance(data, dict):
        errors.append("invalid_application_data")
    application = data if isinstance(data, dict) else {}
    forbidden = FORBIDDEN_APPLICATION_DATA_KEYS.intersection(application)
    if forbidden:
        errors.append("forbidden_application_data_key")

    source_records = tuple(
        record for item in result.evidence for record in item.source_records
    )
    application_records = _application_records(application)
    if len(source_records) > MODEL_VISIBLE_RECORD_MAX:
        errors.append("projection_record_limit_exceeded")
    if (
        application_records is not None
        and len(application_records) > MODEL_VISIBLE_RECORD_MAX
    ):
        errors.append("projection_record_limit_exceeded")
    errors.extend(_record_correspondence_errors(application_records, source_records))
    errors.extend(_as_of_coherence_errors(result))

    evidence_error_code: str | None = None
    try:
        evidence_error_code = _authoritative_error_code(result, application)
    except ToolResultProjectionError as exc:
        errors.extend(list(exc.errors))

    if errors:
        raise ToolResultProjectionError(errors)

    return ToolResultProjection(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        status=result.status,
        temporal_scope=result.temporal_scope,
        as_of_utc=result.as_of_utc,
        operation=_optional_text(application.get("operation")),
        requested_scope=application.get("requested_scope"),
        result=application.get("result"),
        limitations=result.limitations,
        error_code=evidence_error_code,
        evidence=tuple(_project_evidence(item) for item in result.evidence),
    )


def _project_evidence(evidence: Evidence) -> ProjectedEvidence:
    return ProjectedEvidence(
        source=evidence.source,
        completeness=evidence.completeness,
        match_cardinality=evidence.match_cardinality,
        error_code=evidence.error_code,
        source_records=evidence.source_records,
        limitations=evidence.limitations,
        temporal_scope=evidence.temporal_scope,
    )


def _application_records(application: Mapping[str, Any]) -> list[Any] | None:
    payload = application.get("result")
    if not isinstance(payload, dict) or "records" not in payload:
        return None
    records = payload["records"]
    if not isinstance(records, list):
        raise ToolResultProjectionError("invalid_result_records")
    return records


def _application_error_code(application: Mapping[str, Any]) -> str | None:
    error = application.get("error")
    if error is None:
        return None
    if not isinstance(error, dict):
        raise ToolResultProjectionError("invalid_application_error")
    code = error.get("code")
    if code is None:
        return None
    if not isinstance(code, str) or not code.strip():
        raise ToolResultProjectionError("invalid_application_error")
    return code


def _authoritative_error_code(
    result: ToolResult,
    application: Mapping[str, Any],
) -> str | None:
    """Evidence.error_code is the only model-visible error authority."""

    evidence_codes = tuple(
        dict.fromkeys(
            item.error_code for item in result.evidence if item.error_code is not None
        )
    )
    if len(evidence_codes) > 1:
        raise ToolResultProjectionError("projection_error_code_mismatch")
    evidence_code = evidence_codes[0] if evidence_codes else None
    application_code = _application_error_code(application)
    if (
        evidence_code is not None
        and application_code is not None
        and evidence_code != application_code
    ):
        raise ToolResultProjectionError("projection_error_code_mismatch")
    return evidence_code


def _record_correspondence_errors(
    application_records: list[Any] | None,
    source_records: tuple[SourceRecord, ...],
) -> list[str]:
    if application_records is None:
        return []
    if len(application_records) != len(source_records):
        return ["projection_record_count_mismatch"]
    for data_record, source in zip(application_records, source_records, strict=True):
        if not isinstance(data_record, dict):
            return ["projection_record_identity_mismatch"]
        if (
            data_record.get("record_id") != source.record_id
            or data_record.get("event_time_utc") != source.event_timestamp_utc
        ):
            return ["projection_record_identity_mismatch"]
    return []


def _as_of_coherence_errors(result: ToolResult) -> list[str]:
    populated = []
    if result.as_of_utc is not None:
        populated.append(result.as_of_utc)
    for item in result.evidence:
        if item.query_timestamp_utc is not None:
            populated.append(item.query_timestamp_utc)
        evaluated = (
            None
            if item.completeness is None
            else item.completeness.evaluated_as_of_utc
        )
        if evaluated is not None:
            populated.append(evaluated)
    if populated and len(set(populated)) > 1:
        return ["projection_as_of_mismatch"]
    return []


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ToolResultProjectionError("invalid_operation")
    return value


__all__ = [
    "AUDIT_ONLY_TRACE_KEYS",
    "FORBIDDEN_APPLICATION_DATA_KEYS",
    "MODEL_VISIBLE_RECORD_MAX",
    "ProjectedEvidence",
    "ToolResultProjection",
    "ToolResultProjectionError",
    "project_tool_result",
]
