"""Sprint 3D authenticated source evidence.

A factual claim is admitted only for a receipt this host sealed after an
approved adapter callable returned. The receipt is bound to one consumed
grant and the invocation that was actually executed. A dict, a local read
receipt, a nonproduction candidate, a source label, or a California string
cannot mint that seal.

Authenticity, freshness, correctness, clocks, completeness, and geographic
coverage stay separate axes. An empty page is not a verified zero. Missing
risk is not LOW. Aircraft and SIGMET impact claims require a point and a
ring that this module intersects. Operational advice stays rejected.

Shared ``MasterSourceAuthenticity`` is unchanged and stays
``NOT_ESTABLISHED``. Closed admission constants are not flipped.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    FreshnessStatus,
    _validate_bounded_text,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_ledger import MasterExecutionLedgerState
from wilvor_ai.master_parallel_contracts import MasterResponseLimitation
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_grant_ledger import DurableConsumedAttempt


SPRINT3D_SOURCE_EVIDENCE_SCHEMA_VERSION = "wilvor.ai.sprint3d.source_evidence.v1"
SPRINT3D_SOURCE_ASSESSMENT_SCHEMA_VERSION = "wilvor.ai.sprint3d.source_assessment.v1"

GLOBAL_EVIDENCE_ADMISSION_ENABLED = False
EMPTY_PAGE_CREATES_VERIFIED_ZERO = False
ABSENT_RISK_IS_LOW_RISK = False
SOURCE_LABEL_ESTABLISHES_GEOGRAPHY = False
REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION = False
OPERATIONAL_ADVICE_ENABLED = False
IMPACT_COUNT_ADMITTED = False

_CLOCK_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_LOW_RISK_LABELS = frozenset({"LOW", "LOW_RISK"})
_PLACE_MARKERS = frozenset({"california", "sigmet", "region", "census"})
_ADVICE_MARKERS = (
    "recommend",
    "divert",
    "diversion",
    "evacuate",
    "descend",
    "climb immediately",
    "avoid the area",
    "safety instruction",
)
_FORBIDDEN_RESPONSE_FRAGMENTS = _ADVICE_MARKERS + (
    "low risk",
    "verified zero",
    "impacted aircraft",
)
_CURRENT_FRESHNESS = frozenset({FreshnessStatus.FRESH, FreshnessStatus.ACCEPTABLE})
_MAX_RING_POINTS = 64

_LIVE_HOSTS: set[int] = set()
_ISSUED: dict[int, tuple[object, ...]] = {}
_HOST_LOCK = threading.Lock()


class SourceAuthenticity(str, Enum):
    """Whether this host executed the approved adapter for this grant.

    This is not ``MasterSourceAuthenticity`` and it does not grant production
    authority.
    """

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    ESTABLISHED = "ESTABLISHED"


class FreshnessVerdict(str, Enum):
    """Source freshness copied from the page. Not an authenticity result."""

    FRESH = "FRESH"
    ACCEPTABLE = "ACCEPTABLE"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class CorrectnessVerdict(str, Enum):
    """Whether the returned rows agree. Not a freshness or geometry result."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    CONSISTENT = "CONSISTENT"
    CONFLICTING = "CONFLICTING"


class ClockVerdict(str, Enum):
    """Request, observation, evaluation, and query clocks. Not freshness."""

    DISTINCT = "DISTINCT"
    COLLISION = "COLLISION"
    INCOMPLETE = "INCOMPLETE"


class CompletenessVerdict(str, Enum):
    """Pagination and population. An empty page is not a verified zero."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    EMPTY_PAGE = "EMPTY_PAGE"


class GeographicCoverage(str, Enum):
    """Coverage from computed geometry only."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    INTERSECTION = "INTERSECTION"
    NON_INTERSECTION = "NON_INTERSECTION"


class GeographicBasis(str, Enum):
    """How coverage was decided. A label is not a basis this module can set."""

    NOT_ESTABLISHED = "NOT_ESTABLISHED"
    COMPUTED_GEOMETRY = "COMPUTED_GEOMETRY"


class EvidenceVerdict(str, Enum):
    ADMITTED = "ADMITTED"
    WITHHELD = "WITHHELD"
    REJECTED = "REJECTED"
    REPLAYED = "REPLAYED"
    FORGED = "FORGED"


class ProofObligation(str, Enum):
    """What a supported factual claim has to satisfy."""

    APPROVED_EXECUTED_ADAPTER = "APPROVED_EXECUTED_ADAPTER"
    CONSUMED_GRANT_BOUND = "CONSUMED_GRANT_BOUND"
    INVOCATION_BOUND = "INVOCATION_BOUND"
    SOURCE_IDENTITY = "SOURCE_IDENTITY"
    QUERY_OR_RECORD_IDENTITY = "QUERY_OR_RECORD_IDENTITY"
    SOURCE_VERSION_PRESENT = "SOURCE_VERSION_PRESENT"
    SOURCE_VERSION_CONSISTENT = "SOURCE_VERSION_CONSISTENT"
    CLOCKS_DISTINCT = "CLOCKS_DISTINCT"
    SCOPE_MATCH = "SCOPE_MATCH"
    PAGINATION_COMPLETE = "PAGINATION_COMPLETE"
    POPULATION_COMPLETE = "POPULATION_COMPLETE"
    FRESHNESS_CURRENT = "FRESHNESS_CURRENT"
    RECORDS_CONSISTENT = "RECORDS_CONSISTENT"
    GEOMETRY_COMPUTED = "GEOMETRY_COMPUTED"
    GEOMETRY_INTERSECTION = "GEOMETRY_INTERSECTION"
    GEOMETRY_NON_INTERSECTION = "GEOMETRY_NON_INTERSECTION"


class SupportedFactualClaim(str, Enum):
    OBSERVED_SOURCE_RECORD = "OBSERVED_SOURCE_RECORD"
    GEOMETRY_INTERSECTION = "GEOMETRY_INTERSECTION"
    GEOMETRY_NON_INTERSECTION = "GEOMETRY_NON_INTERSECTION"


class RejectedClaim(str, Enum):
    VERIFIED_ZERO = "VERIFIED_ZERO"
    LOW_RISK = "LOW_RISK"
    OPERATIONAL_ADVICE = "OPERATIONAL_ADVICE"
    IMPACT_COUNT = "IMPACT_COUNT"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    PARTIAL_OBSERVATION = "PARTIAL_OBSERVATION"
    CONFLICTING_OBSERVATION = "CONFLICTING_OBSERVATION"
    REGIONAL_TEXT_GEOGRAPHY = "REGIONAL_TEXT_GEOGRAPHY"
    SOURCE_LABEL_GEOGRAPHY = "SOURCE_LABEL_GEOGRAPHY"
    EMPTY_PAGE_ZERO = "EMPTY_PAGE_ZERO"
    FORGED_EVIDENCE = "FORGED_EVIDENCE"


FACTUAL_RECORD_ADMISSION_PREREQUISITES = (
    ProofObligation.APPROVED_EXECUTED_ADAPTER,
    ProofObligation.CONSUMED_GRANT_BOUND,
    ProofObligation.INVOCATION_BOUND,
    ProofObligation.SOURCE_IDENTITY,
    ProofObligation.QUERY_OR_RECORD_IDENTITY,
    ProofObligation.SOURCE_VERSION_PRESENT,
    ProofObligation.SOURCE_VERSION_CONSISTENT,
    ProofObligation.CLOCKS_DISTINCT,
    ProofObligation.SCOPE_MATCH,
    ProofObligation.PAGINATION_COMPLETE,
    ProofObligation.POPULATION_COMPLETE,
    ProofObligation.FRESHNESS_CURRENT,
    ProofObligation.RECORDS_CONSISTENT,
)

_SAFE_MA5_LIMITATIONS = (
    MasterResponseLimitation.PRODUCTION_AUTHORITY_DENIED,
    MasterResponseLimitation.NO_OPERATIONAL_CLAIM,
    MasterResponseLimitation.EXECUTION_DENIED,
)


class _HostToken:
    __slots__ = ()

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class _ApprovalSeal:
    __slots__ = ("host_token", "adapter_id", "spent")

    def __init__(self, host_token: _HostToken, adapter_id: str) -> None:
        if type(host_token) is not _HostToken or id(host_token) not in _LIVE_HOSTS:
            raise ContractValidationError("adapter_not_approved")
        self.host_token = host_token
        self.adapter_id = adapter_id
        self.spent = False

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


class _ReceiptSeal:
    __slots__ = ("host_token", "fingerprint", "spent")

    def __init__(self, host_token: _HostToken) -> None:
        if type(host_token) is not _HostToken or id(host_token) not in _LIVE_HOSTS:
            raise ContractValidationError("receipt_not_host_issued")
        self.host_token = host_token
        self.fingerprint: tuple[object, ...] | None = None
        self.spent = False

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")


def _assert_closed_constants() -> None:
    if list(MasterSourceAuthenticity) != [MasterSourceAuthenticity.NOT_ESTABLISHED]:
        raise ContractValidationError("source_authenticity_enum_broadened")
    closed = (
        GLOBAL_EVIDENCE_ADMISSION_ENABLED,
        EMPTY_PAGE_CREATES_VERIFIED_ZERO,
        ABSENT_RISK_IS_LOW_RISK,
        SOURCE_LABEL_ESTABLISHES_GEOGRAPHY,
        REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION,
        OPERATIONAL_ADVICE_ENABLED,
        IMPACT_COUNT_ADMITTED,
    )
    if any(item is not False for item in closed):
        raise ContractValidationError("evidence_admission_constant_flipped")


def _token(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None:
        if optional:
            return None
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _phrase(value: object, field_name: str, *, optional: bool = False, limit: int = 200) -> str | None:
    if value is None:
        if optional:
            return None
        raise ContractValidationError(f"invalid_{field_name}")
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    if len(value) > limit or any(character in value for character in "\r\n\t"):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _clock(value: object, field_name: str, *, optional: bool = False) -> str | None:
    if value is None:
        if optional:
            return None
        raise ContractValidationError(f"invalid_{field_name}")
    if not isinstance(value, str):
        raise ContractValidationError(f"invalid_{field_name}")
    try:
        datetime.strptime(value, _CLOCK_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ContractValidationError(f"invalid_{field_name}") from exc
    return value


def _count(value: object, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _coordinate(value: object, field_name: str, *, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractValidationError(f"invalid_{field_name}")
    number = float(value)
    if not math.isfinite(number) or number < low or number > high:
        raise ContractValidationError(f"invalid_{field_name}")
    return number


def _ordered(enum_type: type[Enum], selected: set[Enum]) -> tuple[Enum, ...]:
    return tuple(item for item in enum_type if item in selected)


def _place_text(value: str | None) -> bool:
    if not value:
        return False
    folded = value.casefold()
    return any(marker in folded for marker in _PLACE_MARKERS) or folded in {"ca", "california"}


def _low_label(value: str | None) -> bool:
    return value is not None and value.strip().upper() in _LOW_RISK_LABELS


def _arn_or_wildcard(value: str) -> bool:
    folded = value.casefold()
    if folded.startswith("arn:") or folded.startswith("dynamodb:") or ":table/" in folded:
        return True
    return "*" in value or folded in {"all", "all_tables", "any", "unrestricted", "table", "tables"}


def _freeze(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, ObservedSourceRecord):
        return tuple(_freeze(getattr(value, name)) for name in _RECORD_FIELDS)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _material(source: object) -> tuple[object, ...]:
    present = {item.name for item in fields(source)} if hasattr(source, "__dataclass_fields__") else None
    values: list[object] = []
    for name in TRUSTED_AWS_RECEIPT_FIELDS:
        if present is not None and name not in present:
            raise ContractValidationError("receipt_fingerprint_incomplete")
        values.append(_freeze(getattr(source, name)))
    return tuple(values)


def _clock_reader(clock: object) -> str:
    reader = getattr(clock, "now_utc", None)
    if not callable(reader):
        raise ContractValidationError("invalid_evidence_clock")
    return _clock(reader(), "observation_clock_utc") or ""


def _distinct_identities(*values: str) -> None:
    if len(set(values)) != len(values):
        raise ContractValidationError("request_identity_not_distinct")


@dataclass(frozen=True)
class ObservedSourceRecord:
    """One row copied from the adapter return. A label is not a geometry."""

    record_id: str
    source_version: str | None = None
    aircraft_id: str | None = None
    hazard_id: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    ring: tuple[tuple[float, float], ...] = ()
    risk_level: str | None = None

    def __post_init__(self) -> None:
        _token(self.record_id, "record_id")
        _token(self.source_version, "source_version", optional=True)
        _token(self.aircraft_id, "aircraft_id", optional=True)
        _token(self.hazard_id, "hazard_id", optional=True)
        _token(self.risk_level, "risk_level", optional=True)
        has_lon = self.longitude is not None
        has_lat = self.latitude is not None
        if has_lon != has_lat:
            raise ContractValidationError("invalid_aircraft_position")
        if has_lon:
            object.__setattr__(self, "longitude", _coordinate(self.longitude, "longitude", low=-180.0, high=180.0))
            object.__setattr__(self, "latitude", _coordinate(self.latitude, "latitude", low=-90.0, high=90.0))
        if not isinstance(self.ring, tuple):
            raise ContractValidationError("invalid_geometry_ring")
        if len(self.ring) > _MAX_RING_POINTS:
            raise ContractValidationError("invalid_geometry_ring")
        checked: list[tuple[float, float]] = []
        for pair in self.ring:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise ContractValidationError("invalid_geometry_ring")
            checked.append(
                (
                    _coordinate(pair[0], "longitude", low=-180.0, high=180.0),
                    _coordinate(pair[1], "latitude", low=-90.0, high=90.0),
                )
            )
        object.__setattr__(self, "ring", tuple(checked))


_RECORD_FIELDS = tuple(item.name for item in fields(ObservedSourceRecord))


@dataclass(frozen=True)
class AwsSourcePage:
    """Return value of an approved adapter. Claimed flags are not proof."""

    source_name: str
    records: tuple[ObservedSourceRecord, ...] = ()
    source_version: str | None = None
    query_id: str | None = None
    evaluation_clock_utc: str | None = None
    query_clock_utc: str | None = None
    freshness: FreshnessStatus | None = None
    page_index: int = 0
    page_count: int | None = None
    truncated: bool = False
    has_more: bool = False
    rows_returned: int | None = None
    resource_id: str | None = None
    region_text: str | None = None
    claimed_polygon_intersection: bool = False
    claimed_impacted_aircraft_count: int | None = None
    claimed_risk_level: str | None = None
    claimed_verified_zero: bool = False

    def __post_init__(self) -> None:
        _token(self.source_name, "source_name")
        _token(self.source_version, "source_version", optional=True)
        _token(self.query_id, "query_id", optional=True)
        _token(self.resource_id, "resource_id", optional=True)
        _phrase(self.region_text, "region_text", optional=True, limit=80)
        _phrase(self.claimed_risk_level, "claimed_risk_level", optional=True, limit=32)
        _clock(self.evaluation_clock_utc, "evaluation_clock_utc", optional=True)
        _clock(self.query_clock_utc, "query_clock_utc", optional=True)
        _count(self.page_index, "page_index")
        if self.page_count is not None:
            _count(self.page_count, "page_count")
        if self.rows_returned is not None:
            _count(self.rows_returned, "rows_returned")
        if self.claimed_impacted_aircraft_count is not None:
            _count(self.claimed_impacted_aircraft_count, "claimed_impacted_aircraft_count")
        if not isinstance(self.truncated, bool) or not isinstance(self.has_more, bool):
            raise ContractValidationError("invalid_pagination")
        if not isinstance(self.claimed_polygon_intersection, bool) or not isinstance(
            self.claimed_verified_zero,
            bool,
        ):
            raise ContractValidationError("invalid_source_page")
        if self.freshness is not None and not isinstance(self.freshness, FreshnessStatus):
            raise ContractValidationError("invalid_freshness_status")
        if not isinstance(self.records, tuple) or any(
            type(item) is not ObservedSourceRecord for item in self.records
        ):
            raise ContractValidationError("invalid_source_page")


@dataclass(frozen=True)
class SourceReadInvocation:
    """The invocation the host will execute. It must match the consumed grant."""

    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    tool_call_id: str
    principal_ref: str
    policy_revision: str
    resource_id: str
    operation: ReadOperation
    correlation_id: str
    requested_record_id: str | None = None
    requested_query_id: str | None = None
    expected_source_version: str | None = None
    expected_aircraft_id: str | None = None
    expected_hazard_id: str | None = None
    requested_advice: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "master_request_id",
            "planning_session_id",
            "step_id",
            "invocation_id",
            "tool_call_id",
            "principal_ref",
            "policy_revision",
            "resource_id",
            "correlation_id",
        ):
            _token(getattr(self, name), name)
        for name in (
            "requested_record_id",
            "requested_query_id",
            "expected_source_version",
            "expected_aircraft_id",
            "expected_hazard_id",
        ):
            _token(getattr(self, name), name, optional=True)
        _phrase(self.requested_advice, "requested_advice", optional=True)
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if self.operation is ReadOperation.SCAN or self.operation not in {
            ReadOperation.GET_ITEM,
            ReadOperation.QUERY,
        }:
            raise ContractValidationError("scan_not_permitted")
        if self.operation is ReadOperation.GET_ITEM and self.requested_record_id is None:
            raise ContractValidationError("record_identity_required")
        if self.operation is ReadOperation.QUERY and self.requested_query_id is None:
            raise ContractValidationError("query_identity_required")
        if _arn_or_wildcard(self.resource_id):
            raise ContractValidationError("wildcard_resource")
        _distinct_identities(
            self.master_request_id,
            self.planning_session_id,
            self.step_id,
            self.invocation_id,
            self.tool_call_id,
            self.correlation_id,
        )


@dataclass(frozen=True)
class ApprovedAwsAdapter:
    """Host approval of one callable. A boolean or a source label is not approval."""

    adapter_id: str
    source_name: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        _token(self.adapter_id, "adapter_id")
        _token(self.source_name, "source_name")
        if type(self._seal) is not _ApprovalSeal or self._seal.adapter_id != self.adapter_id:
            raise ContractValidationError("adapter_not_approved")

    @property
    def approved_by_label(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False


TRUSTED_AWS_RECEIPT_FIELDS = (
    "schema_version",
    "receipt_id",
    "adapter_id",
    "source_name",
    "reported_source_name",
    "source_identity_match",
    "source_version",
    "query_id",
    "records",
    "master_request_id",
    "planning_session_id",
    "step_id",
    "invocation_id",
    "tool_call_id",
    "principal_ref",
    "policy_revision",
    "grant_nonce",
    "attempt_token",
    "resource_id",
    "echoed_resource_id",
    "scope_match",
    "operation",
    "grant_region_text",
    "accepted_at_utc",
    "observation_clock_utc",
    "evaluation_clock_utc",
    "query_clock_utc",
    "freshness",
    "page_index",
    "page_count",
    "truncated",
    "has_more",
    "rows_returned",
    "contradictory_row_count",
    "adapter_executed",
    "consumed_grant_bound",
    "invocation_bound",
    "expected_source_version",
    "expected_aircraft_id",
    "expected_hazard_id",
    "requested_record_id",
    "requested_query_id",
    "requested_advice",
    "untrusted_region_text",
    "untrusted_claimed_intersection",
    "untrusted_claimed_impact_count",
    "untrusted_claimed_risk",
    "untrusted_claimed_verified_zero",
)


@dataclass(frozen=True)
class TrustedAwsSourceReceipt:
    """Sealed observation of one executed adapter call. Not a caller-built claim."""

    schema_version: str
    receipt_id: str
    adapter_id: str
    source_name: str
    reported_source_name: str
    source_identity_match: bool
    source_version: str | None
    query_id: str | None
    records: tuple[ObservedSourceRecord, ...]
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    tool_call_id: str
    principal_ref: str
    policy_revision: str
    grant_nonce: str
    attempt_token: str
    resource_id: str
    echoed_resource_id: str | None
    scope_match: bool
    operation: ReadOperation
    grant_region_text: str | None
    accepted_at_utc: str
    observation_clock_utc: str
    evaluation_clock_utc: str | None
    query_clock_utc: str | None
    freshness: FreshnessStatus | None
    page_index: int
    page_count: int | None
    truncated: bool
    has_more: bool
    rows_returned: int
    contradictory_row_count: bool
    adapter_executed: bool
    consumed_grant_bound: bool
    invocation_bound: bool
    expected_source_version: str | None
    expected_aircraft_id: str | None
    expected_hazard_id: str | None
    requested_record_id: str | None
    requested_query_id: str | None
    requested_advice: str | None
    untrusted_region_text: str | None
    untrusted_claimed_intersection: bool
    untrusted_claimed_impact_count: int | None
    untrusted_claimed_risk: str | None
    untrusted_claimed_verified_zero: bool
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.schema_version != SPRINT3D_SOURCE_EVIDENCE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if type(self._seal) is not _ReceiptSeal:
            raise ContractValidationError("receipt_not_host_issued")
        names = tuple(item.name for item in fields(self) if item.name != "_seal")
        if names != TRUSTED_AWS_RECEIPT_FIELDS:
            raise ContractValidationError("receipt_fingerprint_incomplete")
        if _material(self) != self._seal.fingerprint or _ISSUED.get(id(self._seal)) != self._seal.fingerprint:
            raise ContractValidationError("receipt_not_host_issued")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")

    def __getstate__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")

    def __reduce__(self) -> object:
        raise ContractValidationError("receipt_not_serializable")

    def __bool__(self) -> bool:
        return False

    @property
    def source_authenticity(self) -> MasterSourceAuthenticity:
        return MasterSourceAuthenticity.NOT_ESTABLISHED

    @property
    def production_authority(self) -> MasterProductionAuthority:
        return MasterProductionAuthority.DENIED

    @property
    def verified_zero_established(self) -> bool:
        return False

    @property
    def low_risk_admitted(self) -> bool:
        return False

    @property
    def polygon_intersection_established(self) -> bool:
        return False

    @property
    def impacted_aircraft_count_established(self) -> bool:
        return False

    @property
    def operational_advice_admitted(self) -> bool:
        return False


@dataclass(frozen=True)
class SourceEvidenceAssessment:
    """Per-receipt result. Global admission constants stay false."""

    schema_version: str
    verdict: EvidenceVerdict
    authenticity: SourceAuthenticity
    freshness: FreshnessVerdict
    correctness: CorrectnessVerdict
    clocks: ClockVerdict
    completeness: CompletenessVerdict
    geographic_coverage: GeographicCoverage
    geographic_basis: GeographicBasis
    admitted_claims: tuple[SupportedFactualClaim, ...]
    rejected_claims: tuple[RejectedClaim, ...]
    met_obligations: tuple[ProofObligation, ...]
    unmet_obligations: tuple[ProofObligation, ...]
    ignored_non_proofs: tuple[str, ...]
    ma5_limitations: tuple[str, ...]
    status_text: str
    shared_source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    factual_claims_admitted: bool = False
    operational_claim_admitted: bool = False
    verified_zero_established: bool = False
    low_risk_admitted: bool = False
    impact_count_admitted: bool = False
    source_label_used_as_geography: bool = False
    regional_text_used_as_geography: bool = False
    receipt: TrustedAwsSourceReceipt | None = None

    def __post_init__(self) -> None:
        _assert_closed_constants()
        if self.schema_version != SPRINT3D_SOURCE_ASSESSMENT_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if not isinstance(self.verdict, EvidenceVerdict):
            raise ContractValidationError("invalid_evidence_verdict")
        if not isinstance(self.authenticity, SourceAuthenticity):
            raise ContractValidationError("invalid_source_authenticity")
        if not isinstance(self.freshness, FreshnessVerdict):
            raise ContractValidationError("invalid_freshness_verdict")
        if not isinstance(self.correctness, CorrectnessVerdict):
            raise ContractValidationError("invalid_correctness_verdict")
        if not isinstance(self.clocks, ClockVerdict):
            raise ContractValidationError("invalid_clock_verdict")
        if not isinstance(self.completeness, CompletenessVerdict):
            raise ContractValidationError("invalid_completeness_verdict")
        if not isinstance(self.geographic_coverage, GeographicCoverage):
            raise ContractValidationError("invalid_geographic_coverage")
        if not isinstance(self.geographic_basis, GeographicBasis):
            raise ContractValidationError("invalid_geographic_basis")
        if self.geographic_basis is GeographicBasis.COMPUTED_GEOMETRY:
            if self.geographic_coverage is GeographicCoverage.NOT_ESTABLISHED:
                raise ContractValidationError("geospatial_coverage_not_established")
        elif self.geographic_coverage is not GeographicCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("geospatial_coverage_not_established")
        if self.shared_source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        closed = (
            self.operational_claim_admitted,
            self.verified_zero_established,
            self.low_risk_admitted,
            self.impact_count_admitted,
            self.source_label_used_as_geography,
            self.regional_text_used_as_geography,
        )
        if any(item is not False for item in closed):
            raise ContractValidationError("operational_claim_not_admitted")
        if self.factual_claims_admitted is not bool(self.admitted_claims):
            raise ContractValidationError("factual_claim_not_supported")
        if self.admitted_claims and self.authenticity is not SourceAuthenticity.ESTABLISHED:
            raise ContractValidationError("factual_claim_not_supported")
        if self.admitted_claims and self.verdict is not EvidenceVerdict.ADMITTED:
            raise ContractValidationError("factual_claim_not_supported")
        if not self.admitted_claims and self.verdict is EvidenceVerdict.ADMITTED:
            raise ContractValidationError("factual_claim_not_supported")
        _ordered_check(self.admitted_claims, SupportedFactualClaim, "admitted_claims")
        _ordered_check(self.rejected_claims, RejectedClaim, "rejected_claims")
        _ordered_check(self.met_obligations, ProofObligation, "met_obligations")
        _ordered_check(self.unmet_obligations, ProofObligation, "unmet_obligations")
        if set(self.met_obligations) & set(self.unmet_obligations):
            raise ContractValidationError("obligation_conflict")
        if self.admitted_claims and any(
            item not in self.met_obligations for item in FACTUAL_RECORD_ADMISSION_PREREQUISITES
        ):
            raise ContractValidationError("factual_claim_not_supported")
        if (
            SupportedFactualClaim.GEOMETRY_INTERSECTION in self.admitted_claims
            and self.geographic_coverage is not GeographicCoverage.INTERSECTION
        ):
            raise ContractValidationError("geospatial_coverage_not_established")
        if (
            SupportedFactualClaim.GEOMETRY_NON_INTERSECTION in self.admitted_claims
            and self.geographic_coverage is not GeographicCoverage.NON_INTERSECTION
        ):
            raise ContractValidationError("geospatial_coverage_not_established")
        folded = self.status_text.casefold()
        if any(item in folded for item in _FORBIDDEN_RESPONSE_FRAGMENTS):
            raise ContractValidationError("safety_instructions_not_enabled")
        if self.receipt is not None and type(self.receipt) is not TrustedAwsSourceReceipt:
            raise ContractValidationError("receipt_not_host_issued")

    def __bool__(self) -> bool:
        return False


def _ordered_check(value: tuple[object, ...], enum_type: type[Enum], field_name: str) -> None:
    if not isinstance(value, tuple) or any(not isinstance(item, enum_type) for item in value):
        raise ContractValidationError(f"invalid_{field_name}")
    if value != tuple(item for item in enum_type if item in set(value)):
        raise ContractValidationError(f"{field_name}_order_not_deterministic")


def valid_admission_prerequisites() -> tuple[str, ...]:
    """Obligations a supported record claim must meet. Geometry adds its own."""

    _assert_closed_constants()
    return tuple(item.value for item in FACTUAL_RECORD_ADMISSION_PREREQUISITES)


def shared_contract_blockers() -> tuple[str, ...]:
    """Changes the integration owner still has to make. Constants stay closed."""

    _assert_closed_constants()
    return (
        "MasterSourceAuthenticity has only NOT_ESTABLISHED",
        "MasterAdmissionStatus has no ADMITTED member",
        "MasterResponsePolicyState has no factual member",
        "render_master_response keeps claims_admitted false",
        "PRODUCTION_MA4_ADMISSION_AVAILABLE stays false",
        "SOURCE_AUTHENTICATED_READ_AVAILABLE stays false",
        "PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED stays false",
        "Agent C approved_source_adapter stays false",
        "verified zero still requires VERIFIED_ZERO_ALWAYS_MISSING",
        "HistoricalSpecialistRunResult still omits source_version and query_id",
        "an empty page is not a verified zero",
        "missing risk is not LOW",
        "a source label or California text is not geographic proof",
    )


def trusted_receipt_interface() -> tuple[str, ...]:
    """Field names of a host-issued receipt, excluding the non-serializable seal."""

    return TRUSTED_AWS_RECEIPT_FIELDS


class SourceEvidenceHost:
    """In-process host. It approves one adapter and executes it once per grant."""

    def __init__(self, clock: object) -> None:
        _assert_closed_constants()
        if not callable(getattr(clock, "now_utc", None)):
            raise ContractValidationError("invalid_evidence_clock")
        self._clock = clock
        self._lock = threading.Lock()
        self._token = _HostToken()
        self._executors: dict[int, Callable[[SourceReadInvocation], AwsSourcePage]] = {}
        self._used_attempts: set[str] = set()
        self._receipts = 0
        with _HOST_LOCK:
            _LIVE_HOSTS.add(id(self._token))

    def approve_aws_adapter(
        self,
        adapter_id: str,
        source_name: str,
        execute: Callable[[SourceReadInvocation], AwsSourcePage],
    ) -> ApprovedAwsAdapter:
        """Seal a callable. The approval object does not carry the callable."""

        _token(adapter_id, "adapter_id")
        _token(source_name, "source_name")
        if not callable(execute):
            raise ContractValidationError("invalid_read_adapter")
        if _place_text(source_name) and SOURCE_LABEL_ESTABLISHES_GEOGRAPHY is not False:
            raise ContractValidationError("source_label_not_geographic_proof")
        seal = _ApprovalSeal(self._token, adapter_id)
        with self._lock:
            self._executors[id(seal)] = execute
        return ApprovedAwsAdapter(adapter_id=adapter_id, source_name=source_name, _seal=seal)

    def execute(
        self,
        approval: object,
        grant: object,
        invocation: object,
    ) -> TrustedAwsSourceReceipt:
        """Call the approved adapter and seal whatever page it returns."""

        if type(approval) is not ApprovedAwsAdapter or type(approval._seal) is not _ApprovalSeal:
            raise ContractValidationError("adapter_not_approved")
        if approval._seal.host_token is not self._token:
            raise ContractValidationError("adapter_not_approved")
        if type(grant) is not DurableConsumedAttempt:
            raise ContractValidationError("consumed_grant_required")
        if type(invocation) is not SourceReadInvocation:
            raise ContractValidationError("invalid_source_invocation")
        self._require_grant(grant, invocation)
        with self._lock:
            if approval._seal.spent:
                raise ContractValidationError("adapter_replayed")
            if grant.attempt_token in self._used_attempts:
                raise ContractValidationError("grant_replayed")
            executor = self._executors.get(id(approval._seal))
            if executor is None:
                raise ContractValidationError("adapter_not_approved")
            approval._seal.spent = True
            self._used_attempts.add(grant.attempt_token)
        try:
            page = executor(invocation)
        except ContractValidationError:
            raise
        except Exception as exc:
            raise ContractValidationError("source_adapter_failed") from exc
        if type(page) is not AwsSourcePage:
            raise ContractValidationError("source_page_rejected")
        observation_clock = _clock_reader(self._clock)
        return self._seal_page(approval, grant, invocation, page, observation_clock)

    def _require_grant(self, grant: DurableConsumedAttempt, invocation: SourceReadInvocation) -> None:
        if grant.state is not MasterExecutionLedgerState.STARTED:
            raise ContractValidationError("consumed_grant_required")
        if (
            grant.production_grant is not False
            or grant.verified_aws_provenance is not False
            or grant.execution_permitted is not False
        ):
            raise ContractValidationError("consumed_grant_required")
        for name in (
            "owner_id",
            "attempt_token",
            "grant_nonce",
            "idempotency_key",
            "payload_fingerprint",
            "master_request_id",
            "planning_session_id",
            "step_id",
            "invocation_id",
            "principal_ref",
            "policy_revision",
            "resource_scope",
            "correlation_id",
            "accepted_at_utc",
            "deadline_utc",
        ):
            value = getattr(grant, name)
            if name.endswith("_utc"):
                _clock(value, name)
            else:
                _token(value, name)
        _phrase(grant.region_scope, "region_scope", optional=True, limit=80)
        _clock(grant.accepted_at_utc, "accepted_at_utc")
        accepted = datetime.strptime(grant.accepted_at_utc, _CLOCK_FORMAT).replace(tzinfo=timezone.utc)
        deadline = datetime.strptime(grant.deadline_utc, _CLOCK_FORMAT).replace(tzinfo=timezone.utc)
        if deadline <= accepted:
            raise ContractValidationError("invalid_deadline_utc")
        if _arn_or_wildcard(grant.resource_scope):
            raise ContractValidationError("wildcard_resource")
        if not isinstance(grant.specialist, MasterSpecialist) or not isinstance(grant.purpose, MasterPurpose):
            raise ContractValidationError("invalid_consumed_grant")
        if not isinstance(grant.capability, AgentCapability):
            raise ContractValidationError("invalid_consumed_grant")
        _distinct_identities(
            grant.master_request_id,
            grant.planning_session_id,
            grant.step_id,
            grant.invocation_id,
            grant.attempt_token,
            grant.grant_nonce,
            grant.correlation_id,
        )
        pairs = (
            (invocation.master_request_id, grant.master_request_id),
            (invocation.planning_session_id, grant.planning_session_id),
            (invocation.step_id, grant.step_id),
            (invocation.invocation_id, grant.invocation_id),
            (invocation.principal_ref, grant.principal_ref),
            (invocation.policy_revision, grant.policy_revision),
            (invocation.resource_id, grant.resource_scope),
            (invocation.correlation_id, grant.correlation_id),
        )
        if any(left != right for left, right in pairs):
            raise ContractValidationError("invocation_mismatch")

    def _seal_page(
        self,
        approval: ApprovedAwsAdapter,
        grant: DurableConsumedAttempt,
        invocation: SourceReadInvocation,
        page: AwsSourcePage,
        observation_clock: str,
    ) -> TrustedAwsSourceReceipt:
        source_match = page.source_name == approval.source_name
        scope_match = page.resource_id in {None, invocation.resource_id}
        contradictory = page.rows_returned is not None and page.rows_returned != len(page.records)
        rows_returned = len(page.records)
        with self._lock:
            self._receipts += 1
            receipt_id = f"receipt-{self._receipts}"
        payload = {
            "schema_version": SPRINT3D_SOURCE_EVIDENCE_SCHEMA_VERSION,
            "receipt_id": receipt_id,
            "adapter_id": approval.adapter_id,
            "source_name": approval.source_name,
            "reported_source_name": page.source_name,
            "source_identity_match": source_match,
            "source_version": page.source_version,
            "query_id": page.query_id,
            "records": page.records,
            "master_request_id": grant.master_request_id,
            "planning_session_id": grant.planning_session_id,
            "step_id": grant.step_id,
            "invocation_id": grant.invocation_id,
            "tool_call_id": invocation.tool_call_id,
            "principal_ref": grant.principal_ref,
            "policy_revision": grant.policy_revision,
            "grant_nonce": grant.grant_nonce,
            "attempt_token": grant.attempt_token,
            "resource_id": invocation.resource_id,
            "echoed_resource_id": page.resource_id,
            "scope_match": scope_match,
            "operation": invocation.operation,
            "grant_region_text": grant.region_scope,
            "accepted_at_utc": grant.accepted_at_utc,
            "observation_clock_utc": observation_clock,
            "evaluation_clock_utc": page.evaluation_clock_utc,
            "query_clock_utc": page.query_clock_utc,
            "freshness": page.freshness,
            "page_index": page.page_index,
            "page_count": page.page_count,
            "truncated": page.truncated,
            "has_more": page.has_more,
            "rows_returned": rows_returned,
            "contradictory_row_count": contradictory,
            "adapter_executed": True,
            "consumed_grant_bound": True,
            "invocation_bound": True,
            "expected_source_version": invocation.expected_source_version,
            "expected_aircraft_id": invocation.expected_aircraft_id,
            "expected_hazard_id": invocation.expected_hazard_id,
            "requested_record_id": invocation.requested_record_id,
            "requested_query_id": invocation.requested_query_id,
            "requested_advice": invocation.requested_advice,
            "untrusted_region_text": page.region_text,
            "untrusted_claimed_intersection": page.claimed_polygon_intersection,
            "untrusted_claimed_impact_count": page.claimed_impacted_aircraft_count,
            "untrusted_claimed_risk": page.claimed_risk_level,
            "untrusted_claimed_verified_zero": page.claimed_verified_zero,
        }
        from types import SimpleNamespace

        fingerprint = _material(SimpleNamespace(**payload))
        seal = _ReceiptSeal(self._token)
        seal.fingerprint = fingerprint
        with _HOST_LOCK:
            _ISSUED[id(seal)] = fingerprint
        return TrustedAwsSourceReceipt(**payload, _seal=seal)


def evaluate_source_evidence(host: object, subject: object) -> SourceEvidenceAssessment:
    """Admit supported claims only when every prerequisite for that claim holds."""

    _assert_closed_constants()
    if type(host) is not SourceEvidenceHost:
        raise ContractValidationError("invalid_evidence_host")
    if type(subject) is not TrustedAwsSourceReceipt:
        return _forged()
    seal = subject._seal
    if (
        type(seal) is not _ReceiptSeal
        or seal.host_token is not host._token
        or _material(subject) != seal.fingerprint
        or _ISSUED.get(id(seal)) != seal.fingerprint
    ):
        return _forged()
    if seal.spent:
        return _replayed(subject)
    seal.spent = True
    return _assess(subject)


def _forged() -> SourceEvidenceAssessment:
    limitations = _SAFE_MA5_LIMITATIONS + (
        MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED,
    )
    return _assessment(
        verdict=EvidenceVerdict.FORGED,
        authenticity=SourceAuthenticity.NOT_ESTABLISHED,
        freshness=FreshnessVerdict.UNKNOWN,
        correctness=CorrectnessVerdict.NOT_ESTABLISHED,
        clocks=ClockVerdict.INCOMPLETE,
        completeness=CompletenessVerdict.NOT_ESTABLISHED,
        geographic_coverage=GeographicCoverage.NOT_ESTABLISHED,
        geographic_basis=GeographicBasis.NOT_ESTABLISHED,
        admitted=(),
        rejected=(RejectedClaim.FORGED_EVIDENCE,),
        met=(),
        unmet=tuple(ProofObligation),
        ignored=(),
        limitations=limitations,
        status_text=_withheld_text(),
        receipt=None,
    )


def _replayed(receipt: TrustedAwsSourceReceipt) -> SourceEvidenceAssessment:
    limitations = _SAFE_MA5_LIMITATIONS + (
        MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,
        MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED,
    )
    return _assessment(
        verdict=EvidenceVerdict.REPLAYED,
        authenticity=SourceAuthenticity.NOT_ESTABLISHED,
        freshness=FreshnessVerdict.UNKNOWN,
        correctness=CorrectnessVerdict.NOT_ESTABLISHED,
        clocks=ClockVerdict.INCOMPLETE,
        completeness=CompletenessVerdict.NOT_ESTABLISHED,
        geographic_coverage=GeographicCoverage.NOT_ESTABLISHED,
        geographic_basis=GeographicBasis.NOT_ESTABLISHED,
        admitted=(),
        rejected=(RejectedClaim.FORGED_EVIDENCE,),
        met=(),
        unmet=tuple(ProofObligation),
        ignored=(),
        limitations=limitations,
        status_text=_withheld_text(),
        receipt=receipt,
    )


def _assess(receipt: TrustedAwsSourceReceipt) -> SourceEvidenceAssessment:
    authentic = (
        receipt.adapter_executed is True
        and receipt.consumed_grant_bound is True
        and receipt.invocation_bound is True
        and receipt.source_identity_match is True
        and receipt.scope_match is True
    )
    authenticity = SourceAuthenticity.ESTABLISHED if authentic else SourceAuthenticity.NOT_ESTABLISHED
    freshness = _freshness(receipt.freshness)
    clocks = _clocks(receipt)
    conflicting = _conflicting(receipt)
    correctness = (
        CorrectnessVerdict.CONFLICTING
        if conflicting
        else CorrectnessVerdict.CONSISTENT
        if authentic
        else CorrectnessVerdict.NOT_ESTABLISHED
    )
    completeness = _completeness(receipt)
    coverage, basis = _geography(receipt, conflicting)
    met = _met(
        receipt,
        authentic=authentic,
        freshness=freshness,
        clocks=clocks,
        correctness=correctness,
        completeness=completeness,
        coverage=coverage,
        basis=basis,
    )
    unmet = tuple(item for item in ProofObligation if item not in met)
    admitted = _admit_claims(met, coverage)
    rejected = _reject_claims(receipt, freshness, completeness, correctness, coverage, basis)
    ignored = _ignored(receipt, basis)
    limitations = _limitations(authenticity)
    if admitted:
        verdict = EvidenceVerdict.ADMITTED
        status = _admitted_text(receipt, admitted)
    elif authentic:
        verdict = EvidenceVerdict.WITHHELD
        status = _withheld_text()
    else:
        verdict = EvidenceVerdict.REJECTED
        status = _withheld_text()
    return _assessment(
        verdict=verdict,
        authenticity=authenticity,
        freshness=freshness,
        correctness=correctness,
        clocks=clocks,
        completeness=completeness,
        geographic_coverage=coverage,
        geographic_basis=basis,
        admitted=admitted,
        rejected=rejected,
        met=met,
        unmet=unmet,
        ignored=ignored,
        limitations=limitations,
        status_text=status,
        receipt=receipt,
    )


def _freshness(value: FreshnessStatus | None) -> FreshnessVerdict:
    if value is FreshnessStatus.FRESH:
        return FreshnessVerdict.FRESH
    if value is FreshnessStatus.ACCEPTABLE:
        return FreshnessVerdict.ACCEPTABLE
    if value is FreshnessStatus.STALE:
        return FreshnessVerdict.STALE
    return FreshnessVerdict.UNKNOWN


def _clocks(receipt: TrustedAwsSourceReceipt) -> ClockVerdict:
    values = (
        receipt.accepted_at_utc,
        receipt.observation_clock_utc,
        receipt.evaluation_clock_utc,
        receipt.query_clock_utc,
    )
    if any(item is None for item in values):
        return ClockVerdict.INCOMPLETE
    if len(set(values)) != len(values):
        return ClockVerdict.COLLISION
    return ClockVerdict.DISTINCT


def _conflicting(receipt: TrustedAwsSourceReceipt) -> bool:
    if receipt.contradictory_row_count:
        return True
    if receipt.expected_source_version is not None and receipt.source_version not in {
        None,
        receipt.expected_source_version,
    }:
        return True
    seen: dict[str, ObservedSourceRecord] = {}
    versions: set[str] = set()
    if receipt.source_version is not None:
        versions.add(receipt.source_version)
    aircraft: dict[str, tuple[float, float]] = {}
    hazards: dict[str, tuple[tuple[float, float], ...]] = {}
    for record in receipt.records:
        if record.source_version is not None:
            versions.add(record.source_version)
            if receipt.source_version is not None and record.source_version != receipt.source_version:
                return True
        previous = seen.get(record.record_id)
        if previous is not None and previous != record:
            return True
        seen[record.record_id] = record
        if record.aircraft_id is not None and record.longitude is not None and record.latitude is not None:
            point = (record.longitude, record.latitude)
            if record.aircraft_id in aircraft and aircraft[record.aircraft_id] != point:
                return True
            aircraft[record.aircraft_id] = point
        if record.hazard_id is not None and record.ring:
            if record.hazard_id in hazards and hazards[record.hazard_id] != record.ring:
                return True
            hazards[record.hazard_id] = record.ring
    if receipt.expected_source_version is not None and versions and receipt.expected_source_version not in versions:
        return True
    return len(versions) > 1


def _completeness(receipt: TrustedAwsSourceReceipt) -> CompletenessVerdict:
    bounded = (
        receipt.truncated is False
        and receipt.has_more is False
        and receipt.page_index == 0
        and receipt.page_count == 1
        and receipt.contradictory_row_count is False
    )
    if bounded and receipt.records and receipt.rows_returned == len(receipt.records):
        return CompletenessVerdict.COMPLETE
    if bounded and not receipt.records and receipt.rows_returned == 0:
        return CompletenessVerdict.EMPTY_PAGE
    return CompletenessVerdict.PARTIAL


def _geography(
    receipt: TrustedAwsSourceReceipt,
    conflicting: bool,
) -> tuple[GeographicCoverage, GeographicBasis]:
    if conflicting or receipt.expected_aircraft_id is None or receipt.expected_hazard_id is None:
        return GeographicCoverage.NOT_ESTABLISHED, GeographicBasis.NOT_ESTABLISHED
    point = None
    ring: tuple[tuple[float, float], ...] | None = None
    for record in receipt.records:
        if (
            record.aircraft_id == receipt.expected_aircraft_id
            and record.longitude is not None
            and record.latitude is not None
        ):
            point = (record.longitude, record.latitude)
        if record.hazard_id == receipt.expected_hazard_id and record.ring:
            ring = record.ring
    if point is None or not ring:
        return GeographicCoverage.NOT_ESTABLISHED, GeographicBasis.NOT_ESTABLISHED
    hit = _ring_intersects_point(ring, point[0], point[1])
    if hit is None:
        return GeographicCoverage.NOT_ESTABLISHED, GeographicBasis.NOT_ESTABLISHED
    coverage = GeographicCoverage.INTERSECTION if hit else GeographicCoverage.NON_INTERSECTION
    return coverage, GeographicBasis.COMPUTED_GEOMETRY


def _ring_intersects_point(
    ring: tuple[tuple[float, float], ...],
    longitude: float,
    latitude: float,
) -> bool | None:
    if len(ring) < 4 or ring[0] != ring[-1]:
        return None
    distinct = set(ring[:-1])
    if len(distinct) < 3:
        return None
    try:
        from shapely.geometry import Point, Polygon
    except ImportError:
        return _ray_intersects(ring, longitude, latitude)
    try:
        polygon = Polygon(ring)
        point = Point(longitude, latitude)
    except Exception:
        return None
    if polygon.is_empty or not polygon.is_valid or not point.is_valid:
        return None
    try:
        return True if polygon.intersects(point) else False
    except Exception:
        return None


def _ray_intersects(
    ring: tuple[tuple[float, float], ...],
    longitude: float,
    latitude: float,
) -> bool | None:
    inside = False
    for start, end in zip(ring, ring[1:]):
        if _on_segment(longitude, latitude, start, end):
            return True
        crosses = (start[1] > latitude) != (end[1] > latitude)
        if not crosses or end[1] == start[1]:
            continue
        cross_x = (end[0] - start[0]) * (latitude - start[1]) / (end[1] - start[1]) + start[0]
        if longitude < cross_x:
            inside = not inside
    return inside


def _on_segment(
    longitude: float,
    latitude: float,
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    cross = (longitude - start[0]) * (end[1] - start[1]) - (latitude - start[1]) * (end[0] - start[0])
    if abs(cross) > 1e-9:
        return False
    return min(start[0], end[0]) - 1e-9 <= longitude <= max(start[0], end[0]) + 1e-9 and min(
        start[1],
        end[1],
    ) - 1e-9 <= latitude <= max(start[1], end[1]) + 1e-9


def _identity_met(receipt: TrustedAwsSourceReceipt) -> bool:
    record_ids = {item.record_id for item in receipt.records}
    if receipt.operation is ReadOperation.GET_ITEM:
        return receipt.requested_record_id in record_ids
    if receipt.operation is ReadOperation.QUERY:
        return bool(receipt.query_id) and receipt.query_id == receipt.requested_query_id
    return False


def _version_consistent(receipt: TrustedAwsSourceReceipt) -> bool:
    versions = {receipt.source_version} if receipt.source_version is not None else set()
    versions.update(item.source_version for item in receipt.records if item.source_version is not None)
    if receipt.expected_source_version is not None and versions and receipt.expected_source_version not in versions:
        return False
    if receipt.source_version is not None and any(
        item.source_version not in {None, receipt.source_version} for item in receipt.records
    ):
        return False
    return len(versions) <= 1


def _met(
    receipt: TrustedAwsSourceReceipt,
    *,
    authentic: bool,
    freshness: FreshnessVerdict,
    clocks: ClockVerdict,
    correctness: CorrectnessVerdict,
    completeness: CompletenessVerdict,
    coverage: GeographicCoverage,
    basis: GeographicBasis,
) -> set[ProofObligation]:
    met: set[ProofObligation] = set()
    if receipt.adapter_executed and authentic:
        met.add(ProofObligation.APPROVED_EXECUTED_ADAPTER)
    if receipt.consumed_grant_bound and authentic:
        met.add(ProofObligation.CONSUMED_GRANT_BOUND)
    if receipt.invocation_bound and authentic:
        met.add(ProofObligation.INVOCATION_BOUND)
    if receipt.source_identity_match and receipt.source_name:
        met.add(ProofObligation.SOURCE_IDENTITY)
    if _identity_met(receipt):
        met.add(ProofObligation.QUERY_OR_RECORD_IDENTITY)
    if receipt.source_version is not None:
        met.add(ProofObligation.SOURCE_VERSION_PRESENT)
    if _version_consistent(receipt) and receipt.source_version is not None:
        met.add(ProofObligation.SOURCE_VERSION_CONSISTENT)
    if clocks is ClockVerdict.DISTINCT:
        met.add(ProofObligation.CLOCKS_DISTINCT)
    if receipt.scope_match:
        met.add(ProofObligation.SCOPE_MATCH)
    if completeness in {CompletenessVerdict.COMPLETE, CompletenessVerdict.EMPTY_PAGE}:
        met.add(ProofObligation.PAGINATION_COMPLETE)
    if completeness is CompletenessVerdict.COMPLETE:
        met.add(ProofObligation.POPULATION_COMPLETE)
    if freshness in {FreshnessVerdict.FRESH, FreshnessVerdict.ACCEPTABLE}:
        met.add(ProofObligation.FRESHNESS_CURRENT)
    if correctness is CorrectnessVerdict.CONSISTENT:
        met.add(ProofObligation.RECORDS_CONSISTENT)
    if basis is GeographicBasis.COMPUTED_GEOMETRY:
        met.add(ProofObligation.GEOMETRY_COMPUTED)
    if coverage is GeographicCoverage.INTERSECTION:
        met.add(ProofObligation.GEOMETRY_INTERSECTION)
    if coverage is GeographicCoverage.NON_INTERSECTION:
        met.add(ProofObligation.GEOMETRY_NON_INTERSECTION)
    return met


def _admit_claims(
    met: set[ProofObligation],
    coverage: GeographicCoverage,
) -> tuple[SupportedFactualClaim, ...]:
    if any(item not in met for item in FACTUAL_RECORD_ADMISSION_PREREQUISITES):
        return ()
    selected = {SupportedFactualClaim.OBSERVED_SOURCE_RECORD}
    if coverage is GeographicCoverage.INTERSECTION and ProofObligation.GEOMETRY_INTERSECTION in met:
        selected.add(SupportedFactualClaim.GEOMETRY_INTERSECTION)
    if coverage is GeographicCoverage.NON_INTERSECTION and ProofObligation.GEOMETRY_NON_INTERSECTION in met:
        selected.add(SupportedFactualClaim.GEOMETRY_NON_INTERSECTION)
    return tuple(item for item in SupportedFactualClaim if item in selected)


def _reject_claims(
    receipt: TrustedAwsSourceReceipt,
    freshness: FreshnessVerdict,
    completeness: CompletenessVerdict,
    correctness: CorrectnessVerdict,
    coverage: GeographicCoverage,
    basis: GeographicBasis,
) -> tuple[RejectedClaim, ...]:
    selected: set[RejectedClaim] = set()
    if completeness is CompletenessVerdict.EMPTY_PAGE or receipt.untrusted_claimed_verified_zero:
        selected.add(RejectedClaim.VERIFIED_ZERO)
        selected.add(RejectedClaim.EMPTY_PAGE_ZERO)
    if (
        _low_label(receipt.untrusted_claimed_risk)
        or any(_low_label(item.risk_level) for item in receipt.records)
        or any(item.risk_level is None for item in receipt.records)
    ):
        selected.add(RejectedClaim.LOW_RISK)
    if receipt.requested_advice:
        selected.add(RejectedClaim.OPERATIONAL_ADVICE)
    if receipt.untrusted_claimed_impact_count is not None:
        selected.add(RejectedClaim.IMPACT_COUNT)
    if freshness is FreshnessVerdict.STALE:
        selected.add(RejectedClaim.STALE_OBSERVATION)
    if completeness is CompletenessVerdict.PARTIAL:
        selected.add(RejectedClaim.PARTIAL_OBSERVATION)
    if correctness is CorrectnessVerdict.CONFLICTING:
        selected.add(RejectedClaim.CONFLICTING_OBSERVATION)
    text_present = bool(receipt.untrusted_region_text) or _place_text(receipt.grant_region_text)
    label_present = _place_text(receipt.source_name) or _place_text(receipt.reported_source_name)
    if basis is not GeographicBasis.COMPUTED_GEOMETRY and (text_present or receipt.untrusted_claimed_intersection):
        if text_present or receipt.grant_region_text:
            selected.add(RejectedClaim.REGIONAL_TEXT_GEOGRAPHY)
        if label_present or receipt.untrusted_claimed_intersection:
            selected.add(RejectedClaim.SOURCE_LABEL_GEOGRAPHY)
    if coverage is GeographicCoverage.NOT_ESTABLISHED and receipt.untrusted_claimed_intersection:
        selected.add(RejectedClaim.SOURCE_LABEL_GEOGRAPHY)
    return tuple(item for item in RejectedClaim if item in selected)


def _ignored(receipt: TrustedAwsSourceReceipt, basis: GeographicBasis) -> tuple[str, ...]:
    ignored: list[str] = []
    if receipt.grant_region_text:
        ignored.append("grant_region_text_ignored")
    if receipt.untrusted_region_text:
        ignored.append("regional_text_ignored")
    if _place_text(receipt.source_name) or _place_text(receipt.reported_source_name) or basis is GeographicBasis.COMPUTED_GEOMETRY:
        ignored.append("source_label_ignored")
    if receipt.untrusted_claimed_intersection:
        ignored.append("claimed_intersection_ignored")
    if receipt.untrusted_claimed_impact_count is not None:
        ignored.append("claimed_impact_count_ignored")
    if receipt.untrusted_claimed_risk is not None:
        ignored.append("claimed_risk_ignored")
    if receipt.untrusted_claimed_verified_zero:
        ignored.append("claimed_verified_zero_ignored")
    return tuple(ignored)


def _limitations(authenticity: SourceAuthenticity) -> tuple[MasterResponseLimitation, ...]:
    selected = set(_SAFE_MA5_LIMITATIONS)
    if authenticity is not SourceAuthenticity.ESTABLISHED:
        selected.add(MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED)
        selected.add(MasterResponseLimitation.SUPPLIED_EVIDENCE_NOT_AUTHENTICATED)
    return tuple(item for item in MasterResponseLimitation if item in selected)


def _withheld_text() -> str:
    return "Supplied evidence is not sufficient for a verified factual claim."


def _admitted_text(
    receipt: TrustedAwsSourceReceipt,
    admitted: tuple[SupportedFactualClaim, ...],
) -> str:
    sentences: list[str] = []
    if SupportedFactualClaim.OBSERVED_SOURCE_RECORD in admitted:
        ids = ", ".join(item.record_id for item in receipt.records)
        noun = "record" if len(receipt.records) == 1 else "records"
        sentences.append(f"Observed source {noun} {ids} from {receipt.source_name}.")
    if SupportedFactualClaim.GEOMETRY_INTERSECTION in admitted:
        sentences.append(
            "Observed geometry intersection for aircraft "
            f"{receipt.expected_aircraft_id} and hazard {receipt.expected_hazard_id}."
        )
    if SupportedFactualClaim.GEOMETRY_NON_INTERSECTION in admitted:
        sentences.append(
            "Observed geometry non-intersection for aircraft "
            f"{receipt.expected_aircraft_id} and hazard {receipt.expected_hazard_id}."
        )
    return " ".join(sentences)


def _assessment(
    *,
    verdict: EvidenceVerdict,
    authenticity: SourceAuthenticity,
    freshness: FreshnessVerdict,
    correctness: CorrectnessVerdict,
    clocks: ClockVerdict,
    completeness: CompletenessVerdict,
    geographic_coverage: GeographicCoverage,
    geographic_basis: GeographicBasis,
    admitted: tuple[SupportedFactualClaim, ...],
    rejected: tuple[RejectedClaim, ...],
    met: tuple[ProofObligation, ...] | set[ProofObligation],
    unmet: tuple[ProofObligation, ...],
    ignored: tuple[str, ...],
    limitations: tuple[MasterResponseLimitation, ...],
    status_text: str,
    receipt: TrustedAwsSourceReceipt | None,
) -> SourceEvidenceAssessment:
    met_tuple = tuple(item for item in ProofObligation if item in set(met))
    return SourceEvidenceAssessment(
        schema_version=SPRINT3D_SOURCE_ASSESSMENT_SCHEMA_VERSION,
        verdict=verdict,
        authenticity=authenticity,
        freshness=freshness,
        correctness=correctness,
        clocks=clocks,
        completeness=completeness,
        geographic_coverage=geographic_coverage,
        geographic_basis=geographic_basis,
        admitted_claims=admitted,
        rejected_claims=rejected,
        met_obligations=met_tuple,
        unmet_obligations=unmet,
        ignored_non_proofs=ignored,
        ma5_limitations=tuple(item.value for item in limitations),
        status_text=status_text,
        factual_claims_admitted=bool(admitted),
        receipt=receipt,
    )


_assert_closed_constants()


__all__ = [
    "ABSENT_RISK_IS_LOW_RISK",
    "EMPTY_PAGE_CREATES_VERIFIED_ZERO",
    "FACTUAL_RECORD_ADMISSION_PREREQUISITES",
    "GLOBAL_EVIDENCE_ADMISSION_ENABLED",
    "IMPACT_COUNT_ADMITTED",
    "OPERATIONAL_ADVICE_ENABLED",
    "REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION",
    "SOURCE_LABEL_ESTABLISHES_GEOGRAPHY",
    "SPRINT3D_SOURCE_ASSESSMENT_SCHEMA_VERSION",
    "SPRINT3D_SOURCE_EVIDENCE_SCHEMA_VERSION",
    "TRUSTED_AWS_RECEIPT_FIELDS",
    "ApprovedAwsAdapter",
    "AwsSourcePage",
    "ClockVerdict",
    "CompletenessVerdict",
    "CorrectnessVerdict",
    "EvidenceVerdict",
    "FreshnessVerdict",
    "GeographicBasis",
    "GeographicCoverage",
    "ObservedSourceRecord",
    "ProofObligation",
    "RejectedClaim",
    "SourceAuthenticity",
    "SourceEvidenceAssessment",
    "SourceEvidenceHost",
    "SourceReadInvocation",
    "SupportedFactualClaim",
    "TrustedAwsSourceReceipt",
    "evaluate_source_evidence",
    "shared_contract_blockers",
    "trusted_receipt_interface",
    "valid_admission_prerequisites",
]
