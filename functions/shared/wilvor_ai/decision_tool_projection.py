"""Model-facing view of a validated Decision Tool result.

The raw ToolResult stays the audit and verifier record. This module copies
that result into a separate projection. It does not rescore, rerank, choose
a winner, truncate evidence, or treat persisted rows as current.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    JsonValue,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
    _validate_string_tuple,
    _validate_text,
    _validate_utc,
    ContractValidationError,
)


@dataclass(frozen=True)
class DecisionProjectedEvidence:
    """Evidence facts a model may see. Runtime clocks and trace fields stay raw."""

    source: str
    source_records: tuple[SourceRecord, ...]
    temporal_scope: TemporalScope
    freshness_status: FreshnessStatus
    confidence: ConfidenceLevel
    limitations: tuple[str, ...]

    def __post_init__(self) -> None:
        errors = _validate_text(self.source, "source")
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if not isinstance(self.source_records, tuple) or any(
            not isinstance(item, SourceRecord) for item in self.source_records
        ):
            errors.append("invalid_source_records")
        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")
        if not isinstance(self.freshness_status, FreshnessStatus):
            errors.append("invalid_freshness_status")
        if not isinstance(self.confidence, ConfidenceLevel):
            errors.append("invalid_confidence")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "source": self.source,
            "source_records": [item.to_dict() for item in self.source_records],
            "temporal_scope": self.temporal_scope.value,
            "freshness_status": self.freshness_status.value,
            "confidence": self.confidence.value,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True)
class DecisionToolProjection:
    """Safe copy of one Decision Tool result. Not a second evidence authority."""

    tool_name: str
    status: ToolResultStatus
    temporal_scope: TemporalScope
    as_of_utc: str | None
    data: JsonValue
    limitations: tuple[str, ...]
    evidence: tuple[DecisionProjectedEvidence, ...]

    def __post_init__(self) -> None:
        errors = _validate_text(self.tool_name, "tool_name")
        errors.extend(_validate_utc(self.as_of_utc, "as_of_utc", optional=True))
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))
        if not isinstance(self.status, ToolResultStatus):
            errors.append("invalid_status")
        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")
        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, DecisionProjectedEvidence) for item in self.evidence
        ):
            errors.append("invalid_evidence")
        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "temporal_scope": self.temporal_scope.value,
            "as_of_utc": self.as_of_utc,
            "data": copy.deepcopy(self.data),
            "limitations": list(self.limitations),
            "evidence": [item.to_dict() for item in self.evidence],
        }


def project_decision_tool_result(result: ToolResult) -> DecisionToolProjection:
    """Copy a validated ToolResult without mutating it or dropping rows."""

    if not isinstance(result, ToolResult):
        raise TypeError("result must be a ToolResult")
    return DecisionToolProjection(
        tool_name=result.tool_name,
        status=result.status,
        temporal_scope=result.temporal_scope,
        as_of_utc=result.as_of_utc,
        data=copy.deepcopy(result.data),
        limitations=tuple(result.limitations),
        evidence=tuple(_project_evidence(item) for item in result.evidence),
    )


def _project_evidence(evidence: Evidence) -> DecisionProjectedEvidence:
    return DecisionProjectedEvidence(
        source=evidence.source,
        source_records=tuple(
            SourceRecord(
                record_id=item.record_id,
                source_version=item.source_version,
                event_timestamp_utc=item.event_timestamp_utc,
            )
            for item in evidence.source_records
        ),
        temporal_scope=evidence.temporal_scope,
        freshness_status=evidence.freshness_status,
        confidence=evidence.confidence,
        limitations=tuple(evidence.limitations),
    )
