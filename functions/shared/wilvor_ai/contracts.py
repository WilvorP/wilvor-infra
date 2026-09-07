"""Dependency-free contracts for the Wilvor AI Operations Copilot.

These types describe data exchanged by future AI components. They do not
query operational systems, calculate operational facts, or enforce runtime
authorization.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, TypeVar


EVIDENCE_SCHEMA_VERSION = "wilvor.ai.evidence.v1"
TOOL_RESULT_SCHEMA_VERSION = "wilvor.ai.tool_result.v1"
FINAL_RESPONSE_SCHEMA_VERSION = "wilvor.ai.response.v1"
AUTHORITY_SCHEMA_VERSION = "wilvor.ai.authority.v1"

JsonScalar = str | int | float | bool | None
JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class ContractValidationError(ValueError):
    """Raised when a Wilvor AI contract is structurally invalid."""

    def __init__(self, errors: tuple[str, ...] | list[str] | str):
        if isinstance(errors, str):
            normalized = (errors,)
        else:
            normalized = tuple(dict.fromkeys(errors))

        self.errors = normalized
        super().__init__("Invalid Wilvor AI contract: " + ", ".join(normalized))


class TemporalScope(str, Enum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    HYBRID = "HYBRID"


class ConfidenceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class FreshnessStatus(str, Enum):
    FRESH = "FRESH"
    ACCEPTABLE = "ACCEPTABLE"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class ToolResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    NOT_FOUND = "NOT_FOUND"
    STALE = "STALE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


class AgentAuthorityMode(str, Enum):
    READ_ONLY_ADVISORY = "READ_ONLY_ADVISORY"


class AgentCapability(str, Enum):
    RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT = (
        "RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT"
    )
    RETRIEVE_HISTORICAL_ANALYTICS = "RETRIEVE_HISTORICAL_ANALYTICS"
    EXPLAIN_EXISTING_RISK = "EXPLAIN_EXISTING_RISK"
    EXPLAIN_EXISTING_RECOMMENDATIONS = "EXPLAIN_EXISTING_RECOMMENDATIONS"
    SUMMARIZE_OPERATIONAL_STATE = "SUMMARIZE_OPERATIONAL_STATE"
    COMPARE_VERIFIED_EVIDENCE = "COMPARE_VERIFIED_EVIDENCE"
    GENERATE_BUSINESS_READABLE_SUMMARIES = (
        "GENERATE_BUSINESS_READABLE_SUMMARIES"
    )
    GENERATE_VISUALIZATION_SPECIFICATIONS = (
        "GENERATE_VISUALIZATION_SPECIFICATIONS"
    )

    MUTATE_OPERATIONAL_STATE = "MUTATE_OPERATIONAL_STATE"
    MUTATE_ALERTS = "MUTATE_ALERTS"
    CREATE_OR_OVERWRITE_DETERMINISTIC_RECOMMENDATIONS = (
        "CREATE_OR_OVERWRITE_DETERMINISTIC_RECOMMENDATIONS"
    )
    ALTER_RISK_RESULTS = "ALTER_RISK_RESULTS"
    ALTER_AIRCRAFT_PROJECTIONS = "ALTER_AIRCRAFT_PROJECTIONS"
    ALTER_HAZARD_RECORDS = "ALTER_HAZARD_RECORDS"
    EXECUTE_REROUTES = "EXECUTE_REROUTES"
    ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS = (
        "ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS"
    )
    ISSUE_LANDING_INSTRUCTIONS = "ISSUE_LANDING_INSTRUCTIONS"
    ISSUE_ALTITUDE_INSTRUCTIONS = "ISSUE_ALTITUDE_INSTRUCTIONS"
    ISSUE_ATC_INSTRUCTIONS = "ISSUE_ATC_INSTRUCTIONS"
    MUTATE_AWS_INFRASTRUCTURE = "MUTATE_AWS_INFRASTRUCTURE"


ALLOWED_V1_CAPABILITIES = frozenset(
    {
        AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        AgentCapability.EXPLAIN_EXISTING_RISK,
        AgentCapability.EXPLAIN_EXISTING_RECOMMENDATIONS,
        AgentCapability.SUMMARIZE_OPERATIONAL_STATE,
        AgentCapability.COMPARE_VERIFIED_EVIDENCE,
        AgentCapability.GENERATE_BUSINESS_READABLE_SUMMARIES,
        AgentCapability.GENERATE_VISUALIZATION_SPECIFICATIONS,
    }
)

PROHIBITED_V1_CAPABILITIES = frozenset(
    {
        AgentCapability.MUTATE_OPERATIONAL_STATE,
        AgentCapability.MUTATE_ALERTS,
        AgentCapability.CREATE_OR_OVERWRITE_DETERMINISTIC_RECOMMENDATIONS,
        AgentCapability.ALTER_RISK_RESULTS,
        AgentCapability.ALTER_AIRCRAFT_PROJECTIONS,
        AgentCapability.ALTER_HAZARD_RECORDS,
        AgentCapability.EXECUTE_REROUTES,
        AgentCapability.ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS,
        AgentCapability.ISSUE_LANDING_INSTRUCTIONS,
        AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS,
        AgentCapability.ISSUE_ATC_INSTRUCTIONS,
        AgentCapability.MUTATE_AWS_INFRASTRUCTURE,
    }
)


EnumType = TypeVar("EnumType", bound=Enum)


def _required(data: Mapping[str, Any], field_name: str) -> Any:
    if field_name not in data:
        raise ContractValidationError(f"missing_{field_name}")

    return data[field_name]


def _parse_enum(
    enum_type: type[EnumType],
    value: Any,
    field_name: str,
) -> EnumType:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc


def _validate_text(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> list[str]:
    if value is None and optional:
        return []

    if not isinstance(value, str) or not value.strip():
        return [f"invalid_{field_name}"]

    return []


def _validate_string_tuple(
    value: Any,
    field_name: str,
) -> list[str]:
    if not isinstance(value, tuple):
        return [f"invalid_{field_name}"]

    if any(not isinstance(item, str) or not item.strip() for item in value):
        return [f"invalid_{field_name}"]

    return []


def _validate_utc(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
) -> list[str]:
    if value is None and optional:
        return []

    if not isinstance(value, str) or not value.strip():
        return [f"invalid_{field_name}"]

    text = value.strip()

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return [f"invalid_{field_name}"]

    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(None):
        return [f"invalid_{field_name}"]

    return []


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True

    if isinstance(value, float):
        return math.isfinite(value)

    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)

    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_value(item)
            for key, item in value.items()
        )

    return False


def _sequence(
    value: Any,
    field_name: str,
) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        raise ContractValidationError(f"invalid_{field_name}")

    return list(value)


@dataclass(frozen=True)
class SourceRecord:
    record_id: str
    source_version: str | None
    event_timestamp_utc: str | None

    def __post_init__(self) -> None:
        errors = _validate_text(self.record_id, "record_id")
        errors.extend(
            _validate_text(
                self.source_version,
                "source_version",
                optional=True,
            )
        )
        errors.extend(
            _validate_utc(
                self.event_timestamp_utc,
                "event_timestamp_utc",
                optional=True,
            )
        )

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "record_id": self.record_id,
            "source_version": self.source_version,
            "event_timestamp_utc": self.event_timestamp_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceRecord":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_source_record")

        return cls(
            record_id=_required(data, "record_id"),
            source_version=_required(data, "source_version"),
            event_timestamp_utc=_required(data, "event_timestamp_utc"),
        )


@dataclass(frozen=True)
class Evidence:
    source: str
    source_records: tuple[SourceRecord, ...]
    query_timestamp_utc: str
    freshness_status: FreshnessStatus
    confidence: ConfidenceLevel
    limitations: tuple[str, ...]
    tool_call_id: str
    temporal_scope: TemporalScope
    schema_version: str = EVIDENCE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        errors.extend(_validate_text(self.source, "source"))
        errors.extend(_validate_text(self.tool_call_id, "tool_call_id"))
        errors.extend(
            _validate_utc(
                self.query_timestamp_utc,
                "query_timestamp_utc",
            )
        )
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))

        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.source_records, tuple) or any(
            not isinstance(item, SourceRecord) for item in self.source_records
        ):
            errors.append("invalid_source_records")

        if not isinstance(self.freshness_status, FreshnessStatus):
            errors.append("invalid_freshness_status")

        if not isinstance(self.confidence, ConfidenceLevel):
            errors.append("invalid_confidence")

        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")

        if (
            self.freshness_status
            in {FreshnessStatus.UNKNOWN, FreshnessStatus.UNAVAILABLE}
            and not self.limitations
        ):
            errors.append("uncertain_freshness_requires_limitations")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "source_records": [
                record.to_dict() for record in self.source_records
            ],
            "query_timestamp_utc": self.query_timestamp_utc,
            "freshness_status": self.freshness_status.value,
            "confidence": self.confidence.value,
            "limitations": list(self.limitations),
            "tool_call_id": self.tool_call_id,
            "temporal_scope": self.temporal_scope.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Evidence":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_evidence")

        records = _sequence(
            _required(data, "source_records"),
            "source_records",
        )
        limitations = _sequence(
            _required(data, "limitations"),
            "limitations",
        )

        return cls(
            source=_required(data, "source"),
            source_records=tuple(
                SourceRecord.from_dict(record) for record in records
            ),
            query_timestamp_utc=_required(data, "query_timestamp_utc"),
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
            limitations=tuple(limitations),
            tool_call_id=_required(data, "tool_call_id"),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            schema_version=_required(data, "schema_version"),
        )


def _validate_evidence_scopes(
    temporal_scope: TemporalScope,
    evidence: tuple[Evidence, ...],
) -> list[str]:
    scopes = {item.temporal_scope for item in evidence}

    if temporal_scope is TemporalScope.HYBRID:
        if not {
            TemporalScope.CURRENT,
            TemporalScope.HISTORICAL,
        }.issubset(scopes):
            return ["hybrid_requires_current_and_historical_evidence"]

        return []

    incompatible = {
        item_scope
        for item_scope in scopes
        if item_scope is not temporal_scope
    }

    if incompatible:
        return ["evidence_temporal_scope_mismatch"]

    return []


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    tool_call_id: str
    status: ToolResultStatus
    temporal_scope: TemporalScope
    data: JsonValue
    evidence: tuple[Evidence, ...]
    as_of_utc: str | None
    limitations: tuple[str, ...]
    correlation_id: str | None = None
    schema_version: str = TOOL_RESULT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        errors.extend(_validate_text(self.tool_name, "tool_name"))
        errors.extend(_validate_text(self.tool_call_id, "tool_call_id"))
        errors.extend(
            _validate_utc(
                self.as_of_utc,
                "as_of_utc",
                optional=True,
            )
        )
        errors.extend(
            _validate_text(
                self.correlation_id,
                "correlation_id",
                optional=True,
            )
        )
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))

        if self.schema_version != TOOL_RESULT_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.status, ToolResultStatus):
            errors.append("invalid_status")

        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")

        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            errors.append("invalid_evidence")
        else:
            if any(
                item.tool_call_id != self.tool_call_id
                for item in self.evidence
            ):
                errors.append("evidence_tool_call_id_mismatch")

            if isinstance(self.temporal_scope, TemporalScope):
                errors.extend(
                    _validate_evidence_scopes(
                        self.temporal_scope,
                        self.evidence,
                    )
                )

        if not _is_json_value(self.data):
            errors.append("data_must_be_json_compatible")

        if self.status is ToolResultStatus.SUCCESS:
            if self.data is None:
                errors.append("success_requires_data")
            if not self.evidence:
                errors.append("success_requires_evidence")

        if self.status is ToolResultStatus.PARTIAL:
            if self.data is None:
                errors.append("partial_requires_data")
            if not self.evidence:
                errors.append("partial_requires_evidence")
            if not self.limitations:
                errors.append("partial_requires_limitations")

        if self.status is ToolResultStatus.STALE:
            if self.data is None:
                errors.append("stale_requires_data")
            if not self.limitations:
                errors.append("stale_requires_limitations")
            if not any(
                item.freshness_status is FreshnessStatus.STALE
                for item in self.evidence
            ):
                errors.append("stale_requires_stale_evidence")

        if self.status in {
            ToolResultStatus.UNAVAILABLE,
            ToolResultStatus.UNKNOWN,
        } and not self.limitations:
            errors.append("uncertain_status_requires_limitations")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "tool_name": self.tool_name,
            "tool_call_id": self.tool_call_id,
            "status": self.status.value,
            "temporal_scope": self.temporal_scope.value,
            "data": self.data,
            "evidence": [item.to_dict() for item in self.evidence],
            "as_of_utc": self.as_of_utc,
            "limitations": list(self.limitations),
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolResult":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_tool_result")

        evidence = _sequence(_required(data, "evidence"), "evidence")
        limitations = _sequence(
            _required(data, "limitations"),
            "limitations",
        )

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
            data=_required(data, "data"),
            evidence=tuple(Evidence.from_dict(item) for item in evidence),
            as_of_utc=_required(data, "as_of_utc"),
            limitations=tuple(limitations),
            correlation_id=_required(data, "correlation_id"),
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class FinalAIResponse:
    answer: str
    evidence: tuple[Evidence, ...]
    as_of_utc: str
    confidence: ConfidenceLevel
    limitations: tuple[str, ...]
    visualizations: tuple[dict[str, JsonValue], ...]
    navigation: tuple[dict[str, JsonValue], ...]
    human_review_required: bool
    temporal_scope: TemporalScope
    correlation_id: str | None = None
    schema_version: str = FINAL_RESPONSE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []
        errors.extend(_validate_text(self.answer, "answer"))
        errors.extend(_validate_utc(self.as_of_utc, "as_of_utc"))
        errors.extend(
            _validate_text(
                self.correlation_id,
                "correlation_id",
                optional=True,
            )
        )
        errors.extend(_validate_string_tuple(self.limitations, "limitations"))

        if self.schema_version != FINAL_RESPONSE_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.confidence, ConfidenceLevel):
            errors.append("invalid_confidence")

        if not isinstance(self.temporal_scope, TemporalScope):
            errors.append("invalid_temporal_scope")

        if not isinstance(self.human_review_required, bool):
            errors.append("human_review_required_must_be_boolean")

        if not isinstance(self.evidence, tuple) or any(
            not isinstance(item, Evidence) for item in self.evidence
        ):
            errors.append("invalid_evidence")
        elif isinstance(self.temporal_scope, TemporalScope):
            errors.extend(
                _validate_evidence_scopes(
                    self.temporal_scope,
                    self.evidence,
                )
            )

        if (
            isinstance(self.confidence, ConfidenceLevel)
            and self.confidence is not ConfidenceLevel.UNKNOWN
            and not self.evidence
        ):
            errors.append("known_confidence_requires_evidence")

        if not isinstance(self.visualizations, tuple) or any(
            not isinstance(item, dict) or not _is_json_value(item)
            for item in self.visualizations
        ):
            errors.append("invalid_visualizations")

        if not isinstance(self.navigation, tuple) or any(
            not isinstance(item, dict) or not _is_json_value(item)
            for item in self.navigation
        ):
            errors.append("invalid_navigation")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "answer": self.answer,
            "evidence": [item.to_dict() for item in self.evidence],
            "as_of_utc": self.as_of_utc,
            "confidence": self.confidence.value,
            "limitations": list(self.limitations),
            "visualizations": list(self.visualizations),
            "navigation": list(self.navigation),
            "human_review_required": self.human_review_required,
            "temporal_scope": self.temporal_scope.value,
            "correlation_id": self.correlation_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "FinalAIResponse":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_final_response")

        evidence = _sequence(_required(data, "evidence"), "evidence")
        limitations = _sequence(
            _required(data, "limitations"),
            "limitations",
        )
        visualizations = _sequence(
            _required(data, "visualizations"),
            "visualizations",
        )
        navigation = _sequence(
            _required(data, "navigation"),
            "navigation",
        )

        return cls(
            answer=_required(data, "answer"),
            evidence=tuple(Evidence.from_dict(item) for item in evidence),
            as_of_utc=_required(data, "as_of_utc"),
            confidence=_parse_enum(
                ConfidenceLevel,
                _required(data, "confidence"),
                "confidence",
            ),
            limitations=tuple(limitations),
            visualizations=tuple(visualizations),
            navigation=tuple(navigation),
            human_review_required=_required(data, "human_review_required"),
            temporal_scope=_parse_enum(
                TemporalScope,
                _required(data, "temporal_scope"),
                "temporal_scope",
            ),
            correlation_id=_required(data, "correlation_id"),
            schema_version=_required(data, "schema_version"),
        )


@dataclass(frozen=True)
class AgentAuthority:
    mode: AgentAuthorityMode
    allowed_capabilities: frozenset[AgentCapability]
    prohibited_capabilities: frozenset[AgentCapability]
    schema_version: str = AUTHORITY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        errors: list[str] = []

        if self.mode is not AgentAuthorityMode.READ_ONLY_ADVISORY:
            errors.append("invalid_authority_mode")

        if self.schema_version != AUTHORITY_SCHEMA_VERSION:
            errors.append("invalid_schema_version")

        if not isinstance(self.allowed_capabilities, frozenset) or any(
            not isinstance(item, AgentCapability)
            for item in self.allowed_capabilities
        ):
            errors.append("invalid_allowed_capabilities")

        if not isinstance(self.prohibited_capabilities, frozenset) or any(
            not isinstance(item, AgentCapability)
            for item in self.prohibited_capabilities
        ):
            errors.append("invalid_prohibited_capabilities")

        if (
            isinstance(self.allowed_capabilities, frozenset)
            and isinstance(self.prohibited_capabilities, frozenset)
            and self.allowed_capabilities & self.prohibited_capabilities
        ):
            errors.append("authority_capabilities_overlap")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "mode": self.mode.value,
            "allowed_capabilities": sorted(
                item.value for item in self.allowed_capabilities
            ),
            "prohibited_capabilities": sorted(
                item.value for item in self.prohibited_capabilities
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AgentAuthority":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_agent_authority")

        allowed = _sequence(
            _required(data, "allowed_capabilities"),
            "allowed_capabilities",
        )
        prohibited = _sequence(
            _required(data, "prohibited_capabilities"),
            "prohibited_capabilities",
        )

        return cls(
            mode=_parse_enum(
                AgentAuthorityMode,
                _required(data, "mode"),
                "authority_mode",
            ),
            allowed_capabilities=frozenset(
                _parse_enum(
                    AgentCapability,
                    value,
                    "allowed_capability",
                )
                for value in allowed
            ),
            prohibited_capabilities=frozenset(
                _parse_enum(
                    AgentCapability,
                    value,
                    "prohibited_capability",
                )
                for value in prohibited
            ),
            schema_version=_required(data, "schema_version"),
        )


V1_AUTHORITY = AgentAuthority(
    mode=AgentAuthorityMode.READ_ONLY_ADVISORY,
    allowed_capabilities=ALLOWED_V1_CAPABILITIES,
    prohibited_capabilities=PROHIBITED_V1_CAPABILITIES,
)
