"""Phase 2B-preflight historical analytics query contracts.

Pure domain types only. This module does not import AWS, render SQL, or
evaluate collection coverage. Phase 2A.1 ``evaluate_collection_window``
remains completeness authority.

Future AWS runtime belongs in ``wilvor_historical_query``. Phase 2C owns
``wilvor_ai`` adapters.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .contracts import (
    Dataset,
    HistoricalFactError,
    as_json_number,
    json_safe,
)
from .coverage_contracts import Evaluability
from .query_windows import (
    QueryWindowError,
    parse_query_window,
)
from .time import HistoricalTimeError, canonicalize_utc_z, parse_utc_datetime


QUERY_CONTRACT_SCHEMA_VERSION = "wilvor.historical.query.v1"

IDENTIFIER_MAX_LENGTH = 256
STORED_LITERAL_MAX_LENGTH = 64

# Caller-supplied V1 filters only. Shapes from stored/tested facts:
# aircraft_id = OpenSky/icao24 token (tests: abc123); no '#' or '|'.
# hazard_id = 'sigmet-{hash}' / tests 'hazard-1'; hyphen, no '#'.
# encounter_id = '{projection_id}#{hazard_id}#{source_version}'.
AIRCRAFT_ID_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)
HAZARD_ID_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
)
ENCOUNTER_ID_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-#"
)
# Output-only composite keys share the encounter identity alphabet.
STORED_COMPOSITE_ID_CHARACTERS = ENCOUNTER_ID_CHARACTERS
# dedup_id = '{encounter_id}|{fact_kind}|{epoch}|{state}[|{resolved}]'
# fact_kind / encounter_state use '_' (ENCOUNTER_OBSERVED).
STORED_DEDUP_ID_CHARACTERS = ENCOUNTER_ID_CHARACTERS | frozenset("|_")
EVIDENCE_TOKEN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
)
STORED_LITERAL_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)

LIST_DEFAULT_LIMIT = 100
LIST_MIN_LIMIT = 1
LIST_MAX_LIMIT = 200

# Chronological list key is the fixed-width canonical UTC form
# (whole seconds sort as .000000Z). record_id / dedup_id break ties
# only when the stored timestamp is identical. Not caller-configurable.
LIST_HISTORICAL_ENCOUNTERS_ORDERING = (
    "canonical_event_time_utc ASC",
    "record_id ASC",
    "dedup_id ASC",
)

# Locked 2B.3 loader rule: metadata keys are write/created-time partitioned,
# so V1 loads all control evidence under these prefixes and filters in memory.
COVERAGE_STORE_METADATA_PREFIXES = (
    "metadata/epoch/",
    "metadata/activation/",
    "metadata/deactivation/",
    "metadata/coverage/",
    "metadata/gaps/",
    "metadata/resolutions/",
    "metadata/incidents/",
)

COVERAGE_REASON_EPOCH_AMBIGUOUS = "EPOCH_AMBIGUOUS"
QUERY_RESULT_MALFORMED = "RESULT_MALFORMED"
HAZARD_VERSION_WINDOW_LIMITATION = (
    "requested window is materialization/event time, not validity-interval overlap"
)

FORBIDDEN_QUERY_REQUEST_FIELDS = frozenset(
    {
        "airport",
        "airport_id",
        "callsign",
        "state",
        "region",
        "california",
        "geometry",
        "sql",
        "query",
        "columns",
        "group_by",
        "order_by",
        "workgroup",
        "output_location",
    }
)


class HistoricalQueryError(HistoricalFactError):
    """Raised when a historical analytics query contract is invalid."""


class HistoricalOperation(str, Enum):
    SUMMARIZE_HISTORICAL_ENCOUNTERS = "summarize_historical_encounters"
    SUMMARIZE_HISTORICAL_RISKS = "summarize_historical_risks"
    SUMMARIZE_HISTORICAL_HAZARD_VERSIONS = (
        "summarize_historical_hazard_versions"
    )
    LIST_HISTORICAL_ENCOUNTERS = "list_historical_encounters"


V1_HISTORICAL_OPERATIONS = frozenset(
    (
        HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS,
        HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS,
        HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS,
    )
)

INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS = (
    HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS.value
)
INTERNAL_QUERY_ID_SUMMARIZE_RISKS = (
    HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS.value
)
INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL = (
    "summarize_historical_risks_by_level"
)
INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS = (
    HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS.value
)
INTERNAL_QUERY_ID_LIST_ENCOUNTERS = (
    HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS.value
)

V1_INTERNAL_QUERY_IDS = frozenset(
    {
        INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
        INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
        INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS,
        INTERNAL_QUERY_ID_LIST_ENCOUNTERS,
    }
)

OPERATION_INTERNAL_QUERY_IDS = {
    HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS: frozenset(
        {INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS}
    ),
    HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS: frozenset(
        {
            INTERNAL_QUERY_ID_SUMMARIZE_RISKS,
            INTERNAL_QUERY_ID_SUMMARIZE_RISKS_BY_LEVEL,
        }
    ),
    HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: frozenset(
        {INTERNAL_QUERY_ID_SUMMARIZE_HAZARD_VERSIONS}
    ),
    HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS: frozenset(
        {INTERNAL_QUERY_ID_LIST_ENCOUNTERS}
    ),
}

FORBIDDEN_OPERATION_NAMES = frozenset(
    {
        "run_sql",
        "execute_query",
        "query_table",
        "group_by",
        "order_by",
        "aggregate_by",
        "run_historical_query",
        "execute_athena",
        "query_geometry",
        "search_historical_region",
        "search_historical_california",
        "search_historical_airport",
        "find_historical_callsign",
    }
)


class HistoricalQueryStatus(str, Enum):
    """Deterministic domain outcomes for a historical analytics operation.

    Query-execution values name the bounded-query outcome, not an Athena
    API. Coverage evaluability remains the Phase 2A.1 ``Evaluability`` enum.
    """

    SUCCEEDED = "SUCCEEDED"
    VERIFIED_ZERO = "VERIFIED_ZERO"
    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    COVERAGE_BLOCKED = "COVERAGE_BLOCKED"
    INVALID_REQUEST = "INVALID_REQUEST"
    QUERY_FAILED = "QUERY_FAILED"
    QUERY_CANCELED = "QUERY_CANCELED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    COVERAGE_STORE_UNAVAILABLE = "COVERAGE_STORE_UNAVAILABLE"


def _contract_dict(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    for key, item in list(payload.items()):
        if isinstance(item, Enum):
            payload[key] = item.value
        elif isinstance(item, tuple):
            payload[key] = [
                _contract_dict(entry) if hasattr(entry, "__dataclass_fields__") else entry
                for entry in item
            ]
    return {key: json_safe(item) for key, item in payload.items()}


def _require_token(
    value: Any,
    field_name: str,
    allowed: frozenset[str],
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalQueryError(f"missing {field_name}")
    text = value.strip()
    if len(text) > IDENTIFIER_MAX_LENGTH:
        raise HistoricalQueryError(f"{field_name} exceeds maximum length")
    if any(character not in allowed for character in text):
        raise HistoricalQueryError(f"invalid {field_name}")
    return text


def _optional_token(
    value: Any,
    field_name: str,
    allowed: frozenset[str],
) -> str | None:
    if value is None:
        return None
    return _require_token(value, field_name, allowed)


def _optional_aircraft_id(value: Any) -> str | None:
    return _optional_token(value, "aircraft_id", AIRCRAFT_ID_CHARACTERS)


def _optional_hazard_id(value: Any) -> str | None:
    return _optional_token(value, "hazard_id", HAZARD_ID_CHARACTERS)


def _optional_encounter_id(value: Any) -> str | None:
    text = _optional_token(value, "encounter_id", ENCOUNTER_ID_CHARACTERS)
    if text is not None and "#" not in text:
        raise HistoricalQueryError("invalid encounter_id")
    return text


def _require_stored_composite_id(value: Any, field_name: str) -> str:
    return _require_token(value, field_name, STORED_COMPOSITE_ID_CHARACTERS)


def _require_stored_dedup_id(value: Any) -> str:
    return _require_token(value, "dedup_id", STORED_DEDUP_ID_CHARACTERS)


def _optional_evidence_token(value: Any, field_name: str) -> str | None:
    return _optional_token(value, field_name, EVIDENCE_TOKEN_CHARACTERS)


def _optional_stored_literal(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HistoricalQueryError(f"missing {field_name}")
    text = value.strip()
    if len(text) > STORED_LITERAL_MAX_LENGTH:
        raise HistoricalQueryError(f"{field_name} exceeds maximum length")
    if any(character not in STORED_LITERAL_CHARACTERS for character in text):
        raise HistoricalQueryError(f"invalid {field_name}")
    return text


def _require_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HistoricalQueryError(f"invalid {field_name}")
    return value


def _optional_utc(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise HistoricalQueryError(f"missing {field_name}")
    text = value.strip()
    if not text.endswith("Z"):
        raise HistoricalQueryError(f"{field_name} must be canonical UTC Z")
    try:
        return canonicalize_utc_z(parse_utc_datetime(text))
    except HistoricalTimeError as exc:
        raise HistoricalQueryError(f"invalid {field_name}") from exc


def _canonicalize_window(start_utc: Any, end_utc: Any) -> tuple[str, str]:
    try:
        window = parse_query_window(start_utc, end_utc)
    except QueryWindowError as exc:
        raise HistoricalQueryError(str(exc)) from exc
    return window.start_utc, window.end_utc


def is_verified_zero(
    *,
    coverage: Evaluability,
    semantic_match_count: int | None,
) -> bool:
    """Return True only for EVALUABLE coverage and an exact semantic count of 0.

    A missing/unknown semantic count can never be verified zero. Executor
    ``rows_returned`` is not an input.
    """

    if coverage is not Evaluability.EVALUABLE:
        return False
    if semantic_match_count is None:
        return False
    if isinstance(semantic_match_count, bool) or not isinstance(
        semantic_match_count, int
    ):
        raise HistoricalQueryError("invalid semantic_match_count")
    if semantic_match_count < 0:
        raise HistoricalQueryError("invalid semantic_match_count")
    return semantic_match_count == 0


def status_for_evaluable_result(
    *,
    semantic_match_count: int | None,
    truncated: bool = False,
) -> HistoricalQueryStatus:
    """Map an EVALUABLE result onto SUCCEEDED, VERIFIED_ZERO, or truncated."""

    if truncated:
        return HistoricalQueryStatus.RESULT_TRUNCATED
    if is_verified_zero(
        coverage=Evaluability.EVALUABLE,
        semantic_match_count=semantic_match_count,
    ):
        return HistoricalQueryStatus.VERIFIED_ZERO
    if semantic_match_count is None:
        raise HistoricalQueryError(
            "non-truncated EVALUABLE result requires an exact semantic count"
        )
    return HistoricalQueryStatus.SUCCEEDED


def status_for_unevaluable_coverage(
    *,
    reason: str | None = None,
) -> HistoricalQueryStatus:
    """Blocked coverage, including ``EPOCH_AMBIGUOUS``, is never VERIFIED_ZERO."""

    del reason
    return HistoricalQueryStatus.COVERAGE_BLOCKED


@dataclass(frozen=True)
class SummarizeHistoricalEncountersRequest:
    start_utc: str
    end_utc: str
    aircraft_id: str | None = None
    hazard_id: str | None = None
    hazard_type: str | None = None

    def __post_init__(self) -> None:
        start_utc, end_utc = _canonicalize_window(self.start_utc, self.end_utc)
        object.__setattr__(self, "start_utc", start_utc)
        object.__setattr__(self, "end_utc", end_utc)
        object.__setattr__(self, "aircraft_id", _optional_aircraft_id(self.aircraft_id))
        object.__setattr__(self, "hazard_id", _optional_hazard_id(self.hazard_id))
        object.__setattr__(
            self,
            "hazard_type",
            _optional_stored_literal(self.hazard_type, "hazard_type"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class SummarizeHistoricalRisksRequest:
    start_utc: str
    end_utc: str
    aircraft_id: str | None = None
    hazard_id: str | None = None
    encounter_id: str | None = None
    risk_level: str | None = None

    def __post_init__(self) -> None:
        start_utc, end_utc = _canonicalize_window(self.start_utc, self.end_utc)
        object.__setattr__(self, "start_utc", start_utc)
        object.__setattr__(self, "end_utc", end_utc)
        object.__setattr__(self, "aircraft_id", _optional_aircraft_id(self.aircraft_id))
        object.__setattr__(self, "hazard_id", _optional_hazard_id(self.hazard_id))
        object.__setattr__(
            self, "encounter_id", _optional_encounter_id(self.encounter_id)
        )
        object.__setattr__(
            self,
            "risk_level",
            _optional_stored_literal(self.risk_level, "risk_level"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class SummarizeHistoricalHazardVersionsRequest:
    start_utc: str
    end_utc: str
    hazard_id: str | None = None
    hazard_type: str | None = None
    product_type: str | None = None

    def __post_init__(self) -> None:
        start_utc, end_utc = _canonicalize_window(self.start_utc, self.end_utc)
        object.__setattr__(self, "start_utc", start_utc)
        object.__setattr__(self, "end_utc", end_utc)
        object.__setattr__(self, "hazard_id", _optional_hazard_id(self.hazard_id))
        object.__setattr__(
            self,
            "hazard_type",
            _optional_stored_literal(self.hazard_type, "hazard_type"),
        )
        object.__setattr__(
            self,
            "product_type",
            _optional_stored_literal(self.product_type, "product_type"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class ListHistoricalEncountersRequest:
    start_utc: str
    end_utc: str
    aircraft_id: str | None = None
    hazard_id: str | None = None
    limit: int = LIST_DEFAULT_LIMIT

    def __post_init__(self) -> None:
        start_utc, end_utc = _canonicalize_window(self.start_utc, self.end_utc)
        object.__setattr__(self, "start_utc", start_utc)
        object.__setattr__(self, "end_utc", end_utc)
        aircraft_id = _optional_aircraft_id(self.aircraft_id)
        hazard_id = _optional_hazard_id(self.hazard_id)
        if aircraft_id is None and hazard_id is None:
            raise HistoricalQueryError(
                "list_historical_encounters requires aircraft_id or hazard_id"
            )
        object.__setattr__(self, "aircraft_id", aircraft_id)
        object.__setattr__(self, "hazard_id", hazard_id)
        if isinstance(self.limit, bool) or not isinstance(self.limit, int):
            raise HistoricalQueryError("invalid limit")
        if self.limit < LIST_MIN_LIMIT or self.limit > LIST_MAX_LIMIT:
            raise HistoricalQueryError("invalid limit")

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class CoverageEvidence:
    """Carrier for Phase 2A.1 evaluator output. Does not evaluate coverage."""

    evaluability: Evaluability | None
    reason: str
    required_horizon_seconds: int
    required_streams: tuple[str, ...]
    collection_epoch_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.evaluability is not None and not isinstance(
            self.evaluability, Evaluability
        ):
            raise HistoricalQueryError("invalid evaluability")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise HistoricalQueryError("missing coverage reason")
        if (
            isinstance(self.required_horizon_seconds, bool)
            or not isinstance(self.required_horizon_seconds, int)
            or self.required_horizon_seconds < 0
        ):
            raise HistoricalQueryError("invalid required_horizon_seconds")
        if not isinstance(self.required_streams, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.required_streams
        ):
            raise HistoricalQueryError("invalid required_streams")
        if not isinstance(self.collection_epoch_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.collection_epoch_ids
        ):
            raise HistoricalQueryError("invalid collection_epoch_ids")
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(
            self,
            "required_streams",
            tuple(item.strip() for item in self.required_streams),
        )
        object.__setattr__(
            self,
            "collection_epoch_ids",
            tuple(item.strip() for item in self.collection_epoch_ids),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _contract_dict(self)
        payload["evaluability"] = (
            self.evaluability.value if self.evaluability is not None else None
        )
        return payload


@dataclass(frozen=True)
class QueryExecutionEvidence:
    """One Athena execution. Diagnostic only; not domain match semantics.

    ``query_id`` is a fixed internal identity and may be the risk-level
    distribution query. It is not a fifth public ``HistoricalOperation``.
    """

    query_id: str
    query_execution_id: str | None = None
    rows_returned: int | None = None
    data_scanned_bytes: int | None = None
    workgroup: str | None = None

    def __post_init__(self) -> None:
        if self.query_id not in V1_INTERNAL_QUERY_IDS:
            raise HistoricalQueryError("invalid query_id")
        if self.query_execution_id is not None:
            object.__setattr__(
                self,
                "query_execution_id",
                _optional_evidence_token(self.query_execution_id, "query_execution_id"),
            )
        if self.rows_returned is not None:
            object.__setattr__(
                self,
                "rows_returned",
                _require_count(self.rows_returned, "rows_returned"),
            )
        if self.data_scanned_bytes is not None:
            object.__setattr__(
                self,
                "data_scanned_bytes",
                _require_count(self.data_scanned_bytes, "data_scanned_bytes"),
            )
        if self.workgroup is not None:
            object.__setattr__(
                self,
                "workgroup",
                _optional_stored_literal(self.workgroup, "workgroup"),
            )

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class QueryEvidence:
    query_name: HistoricalOperation
    datasets: tuple[str, ...]
    requested_start_utc: str
    requested_end_utc: str
    coverage_state: Evaluability | None
    collection_epoch_ids: tuple[str, ...]
    semantic_match_count: int | None = None
    semantic_match_count_is_exact: bool | None = None
    minimum_match_count: int | None = None
    query_executions: tuple[QueryExecutionEvidence, ...] = ()
    evaluated_as_of_utc: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.query_name, HistoricalOperation):
            raise HistoricalQueryError("invalid query_name")
        if self.query_name not in V1_HISTORICAL_OPERATIONS:
            raise HistoricalQueryError("invalid query_name")
        if not isinstance(self.datasets, tuple) or any(
            item not in {dataset.value for dataset in Dataset} for item in self.datasets
        ):
            raise HistoricalQueryError("invalid datasets")
        start_utc, end_utc = _canonicalize_window(
            self.requested_start_utc, self.requested_end_utc
        )
        object.__setattr__(self, "requested_start_utc", start_utc)
        object.__setattr__(self, "requested_end_utc", end_utc)
        if self.coverage_state is not None and not isinstance(
            self.coverage_state, Evaluability
        ):
            raise HistoricalQueryError("invalid coverage_state")
        if not isinstance(self.collection_epoch_ids, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.collection_epoch_ids
        ):
            raise HistoricalQueryError("invalid collection_epoch_ids")
        object.__setattr__(
            self,
            "collection_epoch_ids",
            tuple(item.strip() for item in self.collection_epoch_ids),
        )
        if self.semantic_match_count_is_exact is not None and not isinstance(
            self.semantic_match_count_is_exact, bool
        ):
            raise HistoricalQueryError("invalid semantic_match_count_is_exact")
        if self.semantic_match_count is not None:
            object.__setattr__(
                self,
                "semantic_match_count",
                _require_count(self.semantic_match_count, "semantic_match_count"),
            )
        if self.minimum_match_count is not None:
            object.__setattr__(
                self,
                "minimum_match_count",
                _require_count(self.minimum_match_count, "minimum_match_count"),
            )
        if self.semantic_match_count_is_exact is True:
            if self.semantic_match_count is None:
                raise HistoricalQueryError(
                    "exact semantic_match_count is required when marked exact"
                )
            if self.minimum_match_count is not None:
                raise HistoricalQueryError(
                    "exact semantic count cannot carry a minimum_match_count"
                )
        if self.semantic_match_count_is_exact is False:
            if self.semantic_match_count is not None:
                raise HistoricalQueryError(
                    "inexact semantic count cannot claim semantic_match_count"
                )
            if self.minimum_match_count is None:
                raise HistoricalQueryError(
                    "inexact semantic count requires minimum_match_count"
                )
        if not isinstance(self.query_executions, tuple) or any(
            not isinstance(item, QueryExecutionEvidence)
            for item in self.query_executions
        ):
            raise HistoricalQueryError("invalid query_executions")
        allowed = OPERATION_INTERNAL_QUERY_IDS[self.query_name]
        if any(item.query_id not in allowed for item in self.query_executions):
            raise HistoricalQueryError(
                "query execution does not belong to this operation"
            )
        object.__setattr__(
            self,
            "evaluated_as_of_utc",
            _optional_utc(self.evaluated_as_of_utc, "evaluated_as_of_utc"),
        )

    @property
    def data_scanned_bytes(self) -> int | None:
        """Sum of per-execution bytes. None if any execution omits bytes."""

        if not self.query_executions:
            return 0
        totals = [item.data_scanned_bytes for item in self.query_executions]
        if any(item is None for item in totals):
            return None
        return sum(totals)

    def to_dict(self) -> dict[str, Any]:
        payload = _contract_dict(self)
        payload["query_name"] = self.query_name.value
        payload["coverage_state"] = (
            self.coverage_state.value if self.coverage_state is not None else None
        )
        payload["data_scanned_bytes"] = self.data_scanned_bytes
        return payload


@dataclass(frozen=True)
class EncounterSummaryResult:
    physical_record_count: int
    distinct_encounter_count: int
    distinct_aircraft_count: int
    distinct_hazard_count: int
    distinct_dedup_count: int
    min_event_time_utc: str | None = None
    max_event_time_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "physical_record_count",
            _require_count(self.physical_record_count, "physical_record_count"),
        )
        object.__setattr__(
            self,
            "distinct_encounter_count",
            _require_count(self.distinct_encounter_count, "distinct_encounter_count"),
        )
        object.__setattr__(
            self,
            "distinct_aircraft_count",
            _require_count(self.distinct_aircraft_count, "distinct_aircraft_count"),
        )
        object.__setattr__(
            self,
            "distinct_hazard_count",
            _require_count(self.distinct_hazard_count, "distinct_hazard_count"),
        )
        object.__setattr__(
            self,
            "distinct_dedup_count",
            _require_count(self.distinct_dedup_count, "distinct_dedup_count"),
        )
        object.__setattr__(
            self,
            "min_event_time_utc",
            _optional_utc(self.min_event_time_utc, "min_event_time_utc"),
        )
        object.__setattr__(
            self,
            "max_event_time_utc",
            _optional_utc(self.max_event_time_utc, "max_event_time_utc"),
        )
        if self.physical_record_count == 0:
            if (
                self.min_event_time_utc is not None
                or self.max_event_time_utc is not None
            ):
                raise HistoricalQueryError("empty encounter summary cannot carry times")
        elif self.min_event_time_utc is None or self.max_event_time_utc is None:
            raise HistoricalQueryError("non-empty encounter summary requires event times")

    @property
    def semantic_match_count(self) -> int:
        return self.physical_record_count

    @property
    def semantic_match_count_is_exact(self) -> bool:
        return True

    @property
    def minimum_match_count(self) -> int | None:
        return None

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class RiskLevelBucket:
    risk_level: str
    physical_record_count: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "risk_level",
            _optional_stored_literal(self.risk_level, "risk_level"),
        )
        if self.risk_level is None:
            raise HistoricalQueryError("missing risk_level")
        object.__setattr__(
            self,
            "physical_record_count",
            _require_count(self.physical_record_count, "physical_record_count"),
        )

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class RiskSummaryResult:
    physical_record_count: int
    distinct_risk_count: int
    distinct_encounter_count: int
    distinct_aircraft_count: int
    min_risk_score: int | float | None = None
    max_risk_score: int | float | None = None
    risk_level_distribution: tuple[RiskLevelBucket, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "physical_record_count",
            _require_count(self.physical_record_count, "physical_record_count"),
        )
        object.__setattr__(
            self,
            "distinct_risk_count",
            _require_count(self.distinct_risk_count, "distinct_risk_count"),
        )
        object.__setattr__(
            self,
            "distinct_encounter_count",
            _require_count(self.distinct_encounter_count, "distinct_encounter_count"),
        )
        object.__setattr__(
            self,
            "distinct_aircraft_count",
            _require_count(self.distinct_aircraft_count, "distinct_aircraft_count"),
        )
        if self.min_risk_score is not None:
            object.__setattr__(
                self, "min_risk_score", as_json_number(self.min_risk_score, "min_risk_score")
            )
        if self.max_risk_score is not None:
            object.__setattr__(
                self, "max_risk_score", as_json_number(self.max_risk_score, "max_risk_score")
            )
        if not isinstance(self.risk_level_distribution, tuple) or any(
            not isinstance(item, RiskLevelBucket)
            for item in self.risk_level_distribution
        ):
            raise HistoricalQueryError("invalid risk_level_distribution")
        if self.physical_record_count == 0:
            if self.min_risk_score is not None or self.max_risk_score is not None:
                raise HistoricalQueryError("empty risk summary cannot carry scores")
        elif self.min_risk_score is None or self.max_risk_score is None:
            raise HistoricalQueryError("non-empty risk summary requires scores")

    @property
    def semantic_match_count(self) -> int:
        return self.physical_record_count

    @property
    def semantic_match_count_is_exact(self) -> bool:
        return True

    @property
    def minimum_match_count(self) -> int | None:
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "physical_record_count": self.physical_record_count,
            "distinct_risk_count": self.distinct_risk_count,
            "distinct_encounter_count": self.distinct_encounter_count,
            "distinct_aircraft_count": self.distinct_aircraft_count,
            "min_risk_score": json_safe(self.min_risk_score),
            "max_risk_score": json_safe(self.max_risk_score),
            "risk_level_distribution": [
                item.to_dict() for item in self.risk_level_distribution
            ],
        }


@dataclass(frozen=True)
class HazardVersionSummaryResult:
    physical_record_count: int
    distinct_hazard_count: int
    distinct_hazard_version_count: int
    min_materialized_at_utc: str | None = None
    max_materialized_at_utc: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "physical_record_count",
            _require_count(self.physical_record_count, "physical_record_count"),
        )
        object.__setattr__(
            self,
            "distinct_hazard_count",
            _require_count(self.distinct_hazard_count, "distinct_hazard_count"),
        )
        object.__setattr__(
            self,
            "distinct_hazard_version_count",
            _require_count(
                self.distinct_hazard_version_count, "distinct_hazard_version_count"
            ),
        )
        object.__setattr__(
            self,
            "min_materialized_at_utc",
            _optional_utc(self.min_materialized_at_utc, "min_materialized_at_utc"),
        )
        object.__setattr__(
            self,
            "max_materialized_at_utc",
            _optional_utc(self.max_materialized_at_utc, "max_materialized_at_utc"),
        )
        if self.physical_record_count == 0:
            if (
                self.min_materialized_at_utc is not None
                or self.max_materialized_at_utc is not None
            ):
                raise HistoricalQueryError(
                    "empty hazard-version summary cannot carry times"
                )
        elif (
            self.min_materialized_at_utc is None
            or self.max_materialized_at_utc is None
        ):
            raise HistoricalQueryError(
                "non-empty hazard-version summary requires materialized times"
            )

    @property
    def semantic_match_count(self) -> int:
        return self.physical_record_count

    @property
    def semantic_match_count_is_exact(self) -> bool:
        return True

    @property
    def minimum_match_count(self) -> int | None:
        return None

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class HistoricalEncounterRecord:
    """One stored historical encounter fact row. No live DynamoDB enrichment."""

    encounter_id: str
    record_id: str
    dedup_id: str
    aircraft_id: str
    hazard_id: str
    hazard_version_key: str
    fact_kind: str
    encounter_state: str
    event_time_utc: str
    hazard_type: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "encounter_id",
            _require_stored_composite_id(self.encounter_id, "encounter_id"),
        )
        object.__setattr__(
            self,
            "record_id",
            _require_stored_composite_id(self.record_id, "record_id"),
        )
        object.__setattr__(self, "dedup_id", _require_stored_dedup_id(self.dedup_id))
        object.__setattr__(
            self,
            "aircraft_id",
            _require_token(self.aircraft_id, "aircraft_id", AIRCRAFT_ID_CHARACTERS),
        )
        object.__setattr__(
            self,
            "hazard_id",
            _require_token(self.hazard_id, "hazard_id", HAZARD_ID_CHARACTERS),
        )
        object.__setattr__(
            self,
            "hazard_version_key",
            _require_stored_composite_id(
                self.hazard_version_key, "hazard_version_key"
            ),
        )
        object.__setattr__(
            self,
            "fact_kind",
            _optional_stored_literal(self.fact_kind, "fact_kind"),
        )
        object.__setattr__(
            self,
            "encounter_state",
            _optional_stored_literal(self.encounter_state, "encounter_state"),
        )
        if self.fact_kind is None or self.encounter_state is None:
            raise HistoricalQueryError("missing encounter record field")
        object.__setattr__(
            self, "event_time_utc", _optional_utc(self.event_time_utc, "event_time_utc")
        )
        if self.event_time_utc is None:
            raise HistoricalQueryError("missing event_time_utc")
        object.__setattr__(
            self,
            "hazard_type",
            _optional_stored_literal(self.hazard_type, "hazard_type"),
        )
        if self.hazard_type is None:
            raise HistoricalQueryError("missing hazard_type")

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class ListEncountersResult:
    records: tuple[HistoricalEncounterRecord, ...]
    truncated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.records, tuple) or any(
            not isinstance(item, HistoricalEncounterRecord) for item in self.records
        ):
            raise HistoricalQueryError("invalid encounter records")
        if not isinstance(self.truncated, bool):
            raise HistoricalQueryError("invalid truncated")
        if self.truncated and not self.records:
            raise HistoricalQueryError("truncated list cannot be empty")

    @property
    def semantic_match_count(self) -> int | None:
        if self.truncated:
            return None
        return len(self.records)

    @property
    def semantic_match_count_is_exact(self) -> bool:
        return not self.truncated

    @property
    def minimum_match_count(self) -> int | None:
        if self.truncated:
            return len(self.records) + 1
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [item.to_dict() for item in self.records],
            "truncated": self.truncated,
            "semantic_match_count": self.semantic_match_count,
            "semantic_match_count_is_exact": self.semantic_match_count_is_exact,
            "minimum_match_count": self.minimum_match_count,
        }


def request_field_names(request_type: type) -> frozenset[str]:
    return frozenset(request_type.__dataclass_fields__)


_SUCCESS_QUERY_STATUSES = frozenset(
    {
        HistoricalQueryStatus.SUCCEEDED,
        HistoricalQueryStatus.VERIFIED_ZERO,
        HistoricalQueryStatus.RESULT_TRUNCATED,
    }
)

_RESULT_TYPES = {
    HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS: EncounterSummaryResult,
    HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS: RiskSummaryResult,
    HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: HazardVersionSummaryResult,
    HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS: ListEncountersResult,
}

_REQUEST_TYPES = {
    HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS: SummarizeHistoricalEncountersRequest,
    HistoricalOperation.SUMMARIZE_HISTORICAL_RISKS: SummarizeHistoricalRisksRequest,
    HistoricalOperation.SUMMARIZE_HISTORICAL_HAZARD_VERSIONS: SummarizeHistoricalHazardVersionsRequest,
    HistoricalOperation.LIST_HISTORICAL_ENCOUNTERS: ListHistoricalEncountersRequest,
}


@dataclass(frozen=True)
class HistoricalQueryErrorEvidence:
    """Stable failure diagnostics. Never carries a traceback or raw SQL."""

    code: str
    message: str
    query_id: str | None = None
    query_execution_id: str | None = None
    athena_state: str | None = None
    athena_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise HistoricalQueryError("missing error code")
        if not isinstance(self.message, str) or not self.message.strip():
            raise HistoricalQueryError("missing error message")
        object.__setattr__(self, "code", self.code.strip())
        object.__setattr__(self, "message", self.message.strip())
        if self.query_id is not None:
            object.__setattr__(
                self,
                "query_id",
                _optional_stored_literal(self.query_id, "query_id"),
            )
        if self.query_execution_id is not None:
            object.__setattr__(
                self,
                "query_execution_id",
                _optional_evidence_token(self.query_execution_id, "query_execution_id"),
            )
        if self.athena_state is not None:
            object.__setattr__(
                self,
                "athena_state",
                _optional_stored_literal(self.athena_state, "athena_state"),
            )
        if self.athena_reason is not None:
            if not isinstance(self.athena_reason, str) or not self.athena_reason.strip():
                raise HistoricalQueryError("invalid athena_reason")
            object.__setattr__(self, "athena_reason", self.athena_reason.strip())

    def to_dict(self) -> dict[str, Any]:
        return _contract_dict(self)


@dataclass(frozen=True)
class HistoricalQueryResponse:
    """Deterministic V1 historical analytics response. Not an AI ToolResult."""

    status: HistoricalQueryStatus
    operation: HistoricalOperation
    requested_scope: (
        SummarizeHistoricalEncountersRequest
        | SummarizeHistoricalRisksRequest
        | SummarizeHistoricalHazardVersionsRequest
        | ListHistoricalEncountersRequest
    )
    coverage: CoverageEvidence | None
    evidence: QueryEvidence
    result: (
        EncounterSummaryResult
        | RiskSummaryResult
        | HazardVersionSummaryResult
        | ListEncountersResult
        | None
    ) = None
    limitations: tuple[str, ...] = ()
    error: HistoricalQueryErrorEvidence | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, HistoricalQueryStatus):
            raise HistoricalQueryError("invalid status")
        if not isinstance(self.operation, HistoricalOperation):
            raise HistoricalQueryError("invalid operation")
        expected_request = _REQUEST_TYPES[self.operation]
        if not isinstance(self.requested_scope, expected_request):
            raise HistoricalQueryError("requested_scope does not match operation")
        if not isinstance(self.evidence, QueryEvidence):
            raise HistoricalQueryError("invalid evidence")
        if self.evidence.query_name is not self.operation:
            raise HistoricalQueryError("evidence operation mismatch")
        if self.coverage is not None and not isinstance(self.coverage, CoverageEvidence):
            raise HistoricalQueryError("invalid coverage")
        if not isinstance(self.limitations, tuple) or any(
            not isinstance(item, str) or not item.strip() for item in self.limitations
        ):
            raise HistoricalQueryError("invalid limitations")
        object.__setattr__(
            self,
            "limitations",
            tuple(item.strip() for item in self.limitations),
        )
        if self.error is not None and not isinstance(
            self.error, HistoricalQueryErrorEvidence
        ):
            raise HistoricalQueryError("invalid error")
        if self.status in _SUCCESS_QUERY_STATUSES:
            if self.result is None:
                raise HistoricalQueryError("successful historical response requires a result")
            if self.error is not None:
                raise HistoricalQueryError(
                    "successful historical response cannot carry an error"
                )
            if not isinstance(self.result, _RESULT_TYPES[self.operation]):
                raise HistoricalQueryError("result does not match operation")
        elif self.result is not None:
            raise HistoricalQueryError(
                "unsuccessful historical response cannot carry a result"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "operation": self.operation.value,
            "requested_scope": self.requested_scope.to_dict(),
            "coverage": None if self.coverage is None else self.coverage.to_dict(),
            "result": None if self.result is None else self.result.to_dict(),
            "evidence": self.evidence.to_dict(),
            "limitations": list(self.limitations),
            "error": None if self.error is None else self.error.to_dict(),
        }
