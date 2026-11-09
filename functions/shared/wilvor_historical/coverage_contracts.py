"""Versioned historical collection-control contracts.

These records qualify operational fact datasets. They are not EncounterFact,
RiskFact, HazardVersionFact, or HazardGeometryFact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from .contracts import HistoricalFactError, json_safe
from .time import HistoricalTimeError, parse_utc_datetime


CONTROL_SCHEMA_VERSION = "wilvor.historical.collection_control.v1"
COLLECTION_CONTROL_DATASET = "_collection_control"


class ControlRecordType(str, Enum):
    COLLECTION_EPOCH = "COLLECTION_EPOCH"
    COLLECTION_ACTIVATION = "COLLECTION_ACTIVATION"
    COLLECTION_DEACTIVATION = "COLLECTION_DEACTIVATION"
    COVERAGE_INTERVAL = "COVERAGE_INTERVAL"
    COLLECTION_GAP = "COLLECTION_GAP"
    COLLECTION_GAP_RESOLUTION = "COLLECTION_GAP_RESOLUTION"
    COLLECTION_PROBE = "COLLECTION_PROBE"
    UNBOUND_COLLECTION_INCIDENT = "UNBOUND_COLLECTION_INCIDENT"


STAGING_STATE = "STAGING"
RESERVED_EPOCH_TOKENS = frozenset({"unbound", "staging"})


class CoverageStream(str, Enum):
    FACTS = "facts"
    GEOMETRY = "geometry"


class GapDomain(str, Enum):
    DOMAIN_1 = "DOMAIN_1"
    DOMAIN_2 = "DOMAIN_2"
    DOMAIN_3A = "DOMAIN_3A"
    DOMAIN_3B = "DOMAIN_3B"
    GEOMETRY = "GEOMETRY"
    CONTROL = "CONTROL"


class UncertaintyClass(str, Enum):
    KNOWN_MISSING = "KNOWN_MISSING"
    POTENTIAL_GAP = "POTENTIAL_GAP"
    TRANSPORT_OR_CONTROL_OUTAGE = "TRANSPORT_OR_CONTROL_OUTAGE"
    INTENTIONALLY_INACTIVE = "INTENTIONALLY_INACTIVE"


class RecoveryState(str, Enum):
    OPEN = "OPEN"
    REPLAY_ATTEMPTED = "REPLAY_ATTEMPTED"
    REPLAY_PERSISTED = "REPLAY_PERSISTED"
    RESOLVED_PROVEN = "RESOLVED_PROVEN"
    UNABLE_TO_PROVE_COMPLETE = "UNABLE_TO_PROVE_COMPLETE"


class Evaluability(str, Enum):
    NOT_ACTIVE = "NOT_ACTIVE"
    NOT_YET_EVALUABLE = "NOT_YET_EVALUABLE"
    EVALUABLE = "EVALUABLE"
    GAP_OR_UNCERTAIN = "GAP_OR_UNCERTAIN"


OPERATIONAL_DATASETS = frozenset(
    {
        "encounter",
        "risk",
        "hazard_version",
        "hazard_geometry",
    }
)

DATASET_STREAM = {
    "encounter": CoverageStream.FACTS,
    "risk": CoverageStream.FACTS,
    "hazard_version": CoverageStream.FACTS,
    "hazard_geometry": CoverageStream.GEOMETRY,
}


class HistoricalCoverageError(HistoricalFactError):
    """Raised when a collection-control record is structurally invalid."""


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HistoricalCoverageError(f"missing {field_name}")
    return value.strip()


def _require_utc(value: Any, field_name: str) -> str:
    text = _require_text(value, field_name)
    if not text.endswith("Z"):
        raise HistoricalCoverageError(f"{field_name} must be canonical UTC Z")
    try:
        parse_utc_datetime(text)
    except HistoricalTimeError as exc:
        raise HistoricalCoverageError(f"invalid {field_name}") from exc
    return text


def _optional_utc(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_utc(value, field_name)


def _require_datasets(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise HistoricalCoverageError("affected_datasets is required")
    datasets = []
    for item in value:
        text = _require_text(item, "affected_datasets")
        if text not in OPERATIONAL_DATASETS:
            raise HistoricalCoverageError(f"unknown dataset {text}")
        datasets.append(text)
    return tuple(datasets)


def _control_dict(record: Any) -> dict[str, Any]:
    payload = asdict(record)
    for key, item in list(payload.items()):
        if isinstance(item, Enum):
            payload[key] = item.value
        elif isinstance(item, tuple):
            payload[key] = list(item)
    return {key: json_safe(value) for key, value in payload.items()}


@dataclass(frozen=True)
class CollectionEpochRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    bucket_name: str
    created_at_utc: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_EPOCH:
            raise HistoricalCoverageError("invalid record_type")
        _require_text(self.collection_epoch_id, "collection_epoch_id")
        _require_text(self.bucket_name, "bucket_name")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class CollectionActivationRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    enabled_at_utc: str
    created_at_utc: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_ACTIVATION:
            raise HistoricalCoverageError("invalid record_type")
        _require_text(self.collection_epoch_id, "collection_epoch_id")
        _require_utc(self.enabled_at_utc, "enabled_at_utc")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class CollectionDeactivationRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    deactivated_at_utc: str
    created_at_utc: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_DEACTIVATION:
            raise HistoricalCoverageError("invalid record_type")
        _require_text(self.collection_epoch_id, "collection_epoch_id")
        _require_utc(self.deactivated_at_utc, "deactivated_at_utc")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class CoverageIntervalRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    stream: CoverageStream
    interval_start_utc: str
    interval_end_utc: str
    created_at_utc: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COVERAGE_INTERVAL:
            raise HistoricalCoverageError("invalid record_type")
        _require_text(self.collection_epoch_id, "collection_epoch_id")
        if not isinstance(self.stream, CoverageStream):
            raise HistoricalCoverageError("invalid stream")
        start = parse_utc_datetime(
            _require_utc(self.interval_start_utc, "interval_start_utc")
        )
        end = parse_utc_datetime(
            _require_utc(self.interval_end_utc, "interval_end_utc")
        )
        if start >= end:
            raise HistoricalCoverageError("interval_start_utc must precede end")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class CollectionGapRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    affected_datasets: tuple[str, ...]
    gap_domain: GapDomain
    reason: str
    uncertainty_class: UncertaintyClass
    recovery_state: RecoveryState
    detected_at_utc: str
    interval_start_utc: str
    interval_end_utc: str
    created_at_utc: str
    dedup_id: str
    identity: str | None = None
    source_subsystem: str | None = None
    producer_source: str | None = None
    producer_detail_type: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_GAP:
            raise HistoricalCoverageError("invalid record_type")
        epoch_id = _require_text(self.collection_epoch_id, "collection_epoch_id")
        if epoch_id.lower() in RESERVED_EPOCH_TOKENS:
            raise HistoricalCoverageError(
                "collection_epoch_id must not be a staging token"
            )
        object.__setattr__(
            self,
            "affected_datasets",
            _require_datasets(self.affected_datasets),
        )
        if not isinstance(self.gap_domain, GapDomain):
            raise HistoricalCoverageError("invalid gap_domain")
        _require_text(self.reason, "reason")
        if not isinstance(self.uncertainty_class, UncertaintyClass):
            raise HistoricalCoverageError("invalid uncertainty_class")
        if not isinstance(self.recovery_state, RecoveryState):
            raise HistoricalCoverageError("invalid recovery_state")
        start = parse_utc_datetime(
            _require_utc(self.interval_start_utc, "interval_start_utc")
        )
        end = parse_utc_datetime(
            _require_utc(self.interval_end_utc, "interval_end_utc")
        )
        if start >= end:
            raise HistoricalCoverageError("interval_start_utc must precede end")
        _require_utc(self.detected_at_utc, "detected_at_utc")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(self.evidence_refs or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class UnboundCollectionIncident:
    """Producer/control staging record. Not a collection epoch and not a gap."""

    control_schema_version: str
    record_type: ControlRecordType
    staging_state: str
    affected_datasets: tuple[str, ...]
    gap_domain: GapDomain
    reason: str
    uncertainty_class: UncertaintyClass
    detected_at_utc: str
    interval_start_utc: str
    interval_end_utc: str
    created_at_utc: str
    dedup_id: str
    identity: str | None = None
    source_subsystem: str | None = None
    producer_source: str | None = None
    producer_detail_type: str | None = None
    evidence_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.UNBOUND_COLLECTION_INCIDENT:
            raise HistoricalCoverageError("invalid record_type")
        if self.staging_state != STAGING_STATE:
            raise HistoricalCoverageError("staging_state must be STAGING")
        object.__setattr__(
            self,
            "affected_datasets",
            _require_datasets(self.affected_datasets),
        )
        if not isinstance(self.gap_domain, GapDomain):
            raise HistoricalCoverageError("invalid gap_domain")
        _require_text(self.reason, "reason")
        if not isinstance(self.uncertainty_class, UncertaintyClass):
            raise HistoricalCoverageError("invalid uncertainty_class")
        start = parse_utc_datetime(
            _require_utc(self.interval_start_utc, "interval_start_utc")
        )
        end = parse_utc_datetime(
            _require_utc(self.interval_end_utc, "interval_end_utc")
        )
        if start >= end:
            raise HistoricalCoverageError("interval_start_utc must precede end")
        _require_utc(self.detected_at_utc, "detected_at_utc")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")
        object.__setattr__(
            self,
            "evidence_refs",
            tuple(self.evidence_refs or ()),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = _control_dict(self)
        if "collection_epoch_id" in payload:
            raise HistoricalCoverageError("staging incident must not carry an epoch")
        return payload


@dataclass(frozen=True)
class CollectionGapResolutionRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    gap_dedup_id: str
    recovery_state: RecoveryState
    created_at_utc: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_GAP_RESOLUTION:
            raise HistoricalCoverageError("invalid record_type")
        epoch_id = _require_text(self.collection_epoch_id, "collection_epoch_id")
        if epoch_id.lower() in RESERVED_EPOCH_TOKENS:
            raise HistoricalCoverageError(
                "collection_epoch_id must not be a staging token"
            )
        _require_text(self.gap_dedup_id, "gap_dedup_id")
        if not isinstance(self.recovery_state, RecoveryState):
            raise HistoricalCoverageError("invalid recovery_state")
        _require_utc(self.created_at_utc, "created_at_utc")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class CollectionProbeRecord:
    control_schema_version: str
    record_type: ControlRecordType
    collection_epoch_id: str
    stream: CoverageStream
    probe_id: str
    interval_start_utc: str
    interval_end_utc: str
    observed_at_utc: str
    dataset: str
    event_year: str
    event_month: str
    event_day: str
    dedup_id: str

    def __post_init__(self) -> None:
        if self.control_schema_version != CONTROL_SCHEMA_VERSION:
            raise HistoricalCoverageError("invalid control_schema_version")
        if self.record_type is not ControlRecordType.COLLECTION_PROBE:
            raise HistoricalCoverageError("invalid record_type")
        _require_text(self.collection_epoch_id, "collection_epoch_id")
        if not isinstance(self.stream, CoverageStream):
            raise HistoricalCoverageError("invalid stream")
        _require_text(self.probe_id, "probe_id")
        start = parse_utc_datetime(
            _require_utc(self.interval_start_utc, "interval_start_utc")
        )
        end = parse_utc_datetime(
            _require_utc(self.interval_end_utc, "interval_end_utc")
        )
        if start >= end:
            raise HistoricalCoverageError("interval_start_utc must precede end")
        _require_utc(self.observed_at_utc, "observed_at_utc")
        if self.dataset != COLLECTION_CONTROL_DATASET:
            raise HistoricalCoverageError("probe dataset must be _collection_control")
        _require_text(self.event_year, "event_year")
        _require_text(self.event_month, "event_month")
        _require_text(self.event_day, "event_day")
        _require_text(self.dedup_id, "dedup_id")

    def to_dict(self) -> dict[str, Any]:
        return _control_dict(self)


@dataclass(frozen=True)
class EvaluabilityResult:
    evaluability: Evaluability
    required_horizon_seconds: int
    required_streams: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "evaluability": self.evaluability.value,
            "required_horizon_seconds": self.required_horizon_seconds,
            "required_streams": list(self.required_streams),
            "reason": self.reason,
        }
