"""Dependency-free contracts for the Wilvor AI Operations Copilot.

These types describe data exchanged by future AI components. They do not
query operational systems, calculate operational facts, or enforce runtime
authorization.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, TypeVar


EVIDENCE_SCHEMA_VERSION = "wilvor.ai.evidence.v1"
TOOL_RESULT_SCHEMA_VERSION = "wilvor.ai.tool_result.v1"
FINAL_RESPONSE_SCHEMA_VERSION = "wilvor.ai.response.v1"
AUTHORITY_SCHEMA_VERSION = "wilvor.ai.authority.v1"

# Field-allowlist names and provenance tokens are bounded identifiers.
# They are not provider JSON Schema, SQL, or AWS configuration.
TOOL_INPUT_FIELD_NAME_MAX_LENGTH = 64
TOOL_INPUT_FIELD_DESCRIPTION_MAX_LENGTH = 512
PROVENANCE_TOKEN_MAX_LENGTH = 256
TOOL_INPUT_FIELD_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

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


class ToolInputValueType(str, Enum):
    """Generic catalog value type for later provider-neutral schema generation.

    This is not JSON Schema, a provider type, an enum allowlist, or domain
    validation. Domain request contracts remain authoritative.
    """

    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    BOOLEAN = "BOOLEAN"
    OBJECT = "OBJECT"
    ARRAY = "ARRAY"


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
    *,
    max_length: int | None = None,
) -> list[str]:
    if not isinstance(value, tuple):
        return [f"invalid_{field_name}"]

    if any(not isinstance(item, str) or not item.strip() for item in value):
        return [f"invalid_{field_name}"]

    if max_length is not None and any(len(item) > max_length for item in value):
        return [f"invalid_{field_name}"]

    return []


def _validate_bounded_text(
    value: Any,
    field_name: str,
    *,
    optional: bool = False,
    max_length: int = PROVENANCE_TOKEN_MAX_LENGTH,
) -> list[str]:
    errors = _validate_text(value, field_name, optional=optional)
    if errors or value is None:
        return errors

    if len(value) > max_length:
        return [f"invalid_{field_name}"]

    return []


def _validate_optional_count(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []

    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return [f"invalid_{field_name}"]

    return []


def _optional_nested(
    data: Mapping[str, Any],
    field_name: str,
    factory: Any,
) -> Any:
    if field_name not in data:
        return None

    value = data[field_name]
    if value is None:
        return None

    if not isinstance(value, Mapping):
        raise ContractValidationError(f"invalid_{field_name}")

    return factory(value)


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
class ToolInputField:
    """AI-visible field allowlist entry for a deterministic tool.

    This is the catalog field name a future model may supply. It is not
    OpenAI function schema, JSON Schema, a provider schema, a type
    validator, or a replacement for domain request validation.

    ``value_type`` and ``description`` are optional generic schema metadata
    for a later provider-neutral builder. They do not encode min/max, enum
    values, regex, or domain request validation. Default ``STRING`` and a
    missing description omit those keys so established ``{name, required}``
    payloads stay compatible.

    Future provider-schema generation must use a trusted catalog or bound
    adapter surface. It must not introspect unbound functions that accept
    trusted runtime context.
    """

    name: str
    required: bool
    value_type: ToolInputValueType = ToolInputValueType.STRING
    description: str | None = None

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

        if not isinstance(self.required, bool):
            errors.append("invalid_required")

        if not isinstance(self.value_type, ToolInputValueType):
            errors.append("invalid_value_type")

        errors.extend(
            _validate_bounded_text(
                self.description,
                "description",
                optional=True,
                max_length=TOOL_INPUT_FIELD_DESCRIPTION_MAX_LENGTH,
            )
        )

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
            "name": self.name,
            "required": self.required,
        }
        # Default STRING and unset description are omitted so established
        # allowlist objects keep their existing {name, required} wire shape.
        if self.value_type is not ToolInputValueType.STRING:
            payload["value_type"] = self.value_type.value
        if self.description is not None:
            payload["description"] = self.description
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ToolInputField":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_tool_input_field")

        if "value_type" in data:
            value_type = _parse_enum(
                ToolInputValueType,
                data["value_type"],
                "value_type",
            )
        else:
            value_type = ToolInputValueType.STRING

        return cls(
            name=_required(data, "name"),
            required=_required(data, "required"),
            value_type=value_type,
            description=data["description"] if "description" in data else None,
        )


@dataclass(frozen=True)
class SourceCompleteness:
    """Fitness of a deterministic source for a requested evaluation window.

    Completeness is not freshness. Status is a generic source vocabulary,
    not a historical Evaluability import.
    """

    status: str | None = None
    reason: str | None = None
    epoch_ids: tuple[str, ...] = ()
    evaluated_as_of_utc: str | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(self.status, "status", optional=True)
        errors.extend(_validate_bounded_text(self.reason, "reason", optional=True))
        errors.extend(
            _validate_string_tuple(
                self.epoch_ids,
                "epoch_ids",
                max_length=PROVENANCE_TOKEN_MAX_LENGTH,
            )
        )
        errors.extend(
            _validate_utc(
                self.evaluated_as_of_utc,
                "evaluated_as_of_utc",
                optional=True,
            )
        )

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "status": self.status,
            "reason": self.reason,
            "epoch_ids": list(self.epoch_ids),
            "evaluated_as_of_utc": self.evaluated_as_of_utc,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceCompleteness":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_source_completeness")

        epoch_ids = data["epoch_ids"] if "epoch_ids" in data else ()
        return cls(
            status=data["status"] if "status" in data else None,
            reason=data["reason"] if "reason" in data else None,
            epoch_ids=tuple(_sequence(epoch_ids, "epoch_ids")),
            evaluated_as_of_utc=(
                data["evaluated_as_of_utc"]
                if "evaluated_as_of_utc" in data
                else None
            ),
        )


@dataclass(frozen=True)
class MatchCardinality:
    """Exact or bounded match count established by a deterministic source.

    UNKNOWN: all fields None.
    EXACT: is_exact True, exact_count >= 0, minimum_count None.
    LOWER BOUND: is_exact False, exact_count None, minimum_count >= 0.
    """

    exact_count: int | None = None
    is_exact: bool | None = None
    minimum_count: int | None = None

    def __post_init__(self) -> None:
        errors = _validate_optional_count(self.exact_count, "exact_count")
        errors.extend(
            _validate_optional_count(self.minimum_count, "minimum_count")
        )

        if self.is_exact is not None and not isinstance(self.is_exact, bool):
            errors.append("invalid_is_exact")

        if self.is_exact is True:
            if self.exact_count is None:
                errors.append("exact_requires_exact_count")
            if self.minimum_count is not None:
                errors.append("exact_forbids_minimum_count")
        elif self.is_exact is False:
            if self.exact_count is not None:
                errors.append("inexact_forbids_exact_count")
            if self.minimum_count is None:
                errors.append("inexact_requires_minimum_count")
        elif self.exact_count is not None or self.minimum_count is not None:
            errors.append("unknown_cardinality_forbids_counts")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "exact_count": self.exact_count,
            "is_exact": self.is_exact,
            "minimum_count": self.minimum_count,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "MatchCardinality":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_match_cardinality")

        return cls(
            exact_count=data["exact_count"] if "exact_count" in data else None,
            is_exact=data["is_exact"] if "is_exact" in data else None,
            minimum_count=(
                data["minimum_count"] if "minimum_count" in data else None
            ),
        )


@dataclass(frozen=True)
class QueryExecutionTrace:
    """One deterministic query execution consulted to produce evidence.

    query_id is a closed identity. This is not an Athena client contract
    and must not carry SQL, OutputLocation, or database credentials.
    """

    query_id: str
    execution_id: str | None = None
    rows_returned: int | None = None
    bytes_scanned: int | None = None
    engine_scope: str | None = None

    def __post_init__(self) -> None:
        errors = _validate_bounded_text(self.query_id, "query_id")
        errors.extend(
            _validate_bounded_text(
                self.execution_id,
                "execution_id",
                optional=True,
            )
        )
        errors.extend(_validate_optional_count(self.rows_returned, "rows_returned"))
        errors.extend(_validate_optional_count(self.bytes_scanned, "bytes_scanned"))
        errors.extend(
            _validate_bounded_text(
                self.engine_scope,
                "engine_scope",
                optional=True,
            )
        )

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "query_id": self.query_id,
            "execution_id": self.execution_id,
            "rows_returned": self.rows_returned,
            "bytes_scanned": self.bytes_scanned,
            "engine_scope": self.engine_scope,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "QueryExecutionTrace":
        if not isinstance(data, Mapping):
            raise ContractValidationError("invalid_query_execution_trace")

        return cls(
            query_id=_required(data, "query_id"),
            execution_id=data["execution_id"] if "execution_id" in data else None,
            rows_returned=(
                data["rows_returned"] if "rows_returned" in data else None
            ),
            bytes_scanned=(
                data["bytes_scanned"] if "bytes_scanned" in data else None
            ),
            engine_scope=data["engine_scope"] if "engine_scope" in data else None,
        )


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
    completeness: SourceCompleteness | None = None
    match_cardinality: MatchCardinality | None = None
    query_executions: tuple[QueryExecutionTrace, ...] = ()
    error_code: str | None = None

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
        errors.extend(
            _validate_bounded_text(
                self.error_code,
                "error_code",
                optional=True,
            )
        )

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

        if not isinstance(self.query_executions, tuple) or any(
            not isinstance(item, QueryExecutionTrace)
            for item in self.query_executions
        ):
            errors.append("invalid_query_executions")

        if (
            self.freshness_status
            in {FreshnessStatus.UNKNOWN, FreshnessStatus.UNAVAILABLE}
            and not self.limitations
        ):
            errors.append("uncertain_freshness_requires_limitations")

        if errors:
            raise ContractValidationError(errors)

    def to_dict(self) -> dict[str, JsonValue]:
        payload: dict[str, JsonValue] = {
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
        # Unset optional provenance is omitted so established v1 Evidence
        # wire objects stay byte-compatible with existing fixtures.
        if self.completeness is not None:
            payload["completeness"] = self.completeness.to_dict()
        if self.match_cardinality is not None:
            payload["match_cardinality"] = self.match_cardinality.to_dict()
        if self.query_executions:
            payload["query_executions"] = [
                item.to_dict() for item in self.query_executions
            ]
        if self.error_code is not None:
            payload["error_code"] = self.error_code
        return payload

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
        if "query_executions" in data:
            executions = tuple(
                QueryExecutionTrace.from_dict(item)
                for item in _sequence(
                    data["query_executions"],
                    "query_executions",
                )
            )
        else:
            executions = ()

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
            completeness=_optional_nested(
                data,
                "completeness",
                SourceCompleteness.from_dict,
            ),
            match_cardinality=_optional_nested(
                data,
                "match_cardinality",
                MatchCardinality.from_dict,
            ),
            query_executions=executions,
            error_code=data["error_code"] if "error_code" in data else None,
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
