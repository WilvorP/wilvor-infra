"""Pure historical collection evaluability.

This module does not import boto3, read the wall clock, or query AWS.
Callers supply every timestamp, including as_of_utc and scan_as_of.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from .coverage_contracts import (
    COLLECTION_CONTROL_DATASET,
    CONTROL_SCHEMA_VERSION,
    DATASET_STREAM,
    OPERATIONAL_DATASETS,
    RESERVED_EPOCH_TOKENS,
    STAGING_STATE,
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    CollectionProbeRecord,
    ControlRecordType,
    CoverageIntervalRecord,
    CoverageStream,
    Evaluability,
    EvaluabilityResult,
    GapDomain,
    HistoricalCoverageError,
    RecoveryState,
    UncertaintyClass,
    UnboundCollectionIncident,
)
from .time import HistoricalTimeError, canonicalize_utc_z, parse_utc_datetime


UTC = timezone.utc

ENCOUNTER_HORIZON_SECONDS = 173760
RISK_HORIZON_SECONDS = 173730
HAZARD_VERSION_HORIZON_SECONDS = 173760
HAZARD_GEOMETRY_HORIZON_SECONDS = 87360
DATA_FRESHNESS_THRESHOLD_SECONDS = 1800
FIREHOSE_METRIC_PERIOD_SECONDS = 300
S3_DESTINATION_FAILURE = "S3_DESTINATION_FAILURE"


@dataclass(frozen=True)
class CollectionEvaluationPolicy:
    encounter_producer_to_eventbridge_max_delay_seconds: int = 60
    risk_producer_to_eventbridge_max_delay_seconds: int = 30
    hazard_version_producer_to_eventbridge_max_delay_seconds: int = 60
    hazard_geometry_producer_to_firehose_max_delay_seconds: int = 60
    eventbridge_max_event_age_seconds: int = 86400
    firehose_directput_retention_seconds: int = 86400
    firehose_buffering_interval_seconds: int = 900
    coverage_interval_seconds: int = 900

    def horizon_for(self, dataset: str) -> int:
        if dataset not in OPERATIONAL_DATASETS:
            raise HistoricalCoverageError(f"unknown dataset {dataset}")
        if dataset == "encounter":
            return (
                self.encounter_producer_to_eventbridge_max_delay_seconds
                + self.eventbridge_max_event_age_seconds
                + self.firehose_directput_retention_seconds
                + self.coverage_interval_seconds
            )
        if dataset == "risk":
            return (
                self.risk_producer_to_eventbridge_max_delay_seconds
                + self.eventbridge_max_event_age_seconds
                + self.firehose_directput_retention_seconds
                + self.coverage_interval_seconds
            )
        if dataset == "hazard_version":
            return (
                self.hazard_version_producer_to_eventbridge_max_delay_seconds
                + self.eventbridge_max_event_age_seconds
                + self.firehose_directput_retention_seconds
                + self.coverage_interval_seconds
            )
        return (
            self.hazard_geometry_producer_to_firehose_max_delay_seconds
            + self.firehose_directput_retention_seconds
            + self.coverage_interval_seconds
        )

    def required_horizon_seconds(self, datasets: Iterable[str]) -> int:
        return max(self.horizon_for(dataset) for dataset in datasets)

    def probe_lookback_seconds(self, stream: CoverageStream | str) -> int:
        stream_value = (
            stream.value if isinstance(stream, CoverageStream) else stream
        )
        mapped = [
            dataset
            for dataset, mapped_stream in DATASET_STREAM.items()
            if mapped_stream.value == stream_value
        ]
        if not mapped:
            raise HistoricalCoverageError(f"unknown stream {stream_value}")
        return max(self.horizon_for(dataset) for dataset in mapped)

    def probe_confirm_delay_seconds(self) -> int:
        return (
            self.firehose_buffering_interval_seconds
            + self.coverage_interval_seconds
        )


DEFAULT_COLLECTION_EVALUATION_POLICY = CollectionEvaluationPolicy()


def required_streams_for(datasets: Iterable[str]) -> tuple[CoverageStream, ...]:
    streams: set[CoverageStream] = set()
    for dataset in datasets:
        if dataset not in OPERATIONAL_DATASETS:
            raise HistoricalCoverageError(f"unknown dataset {dataset}")
        streams.add(DATASET_STREAM[dataset])
    return tuple(sorted(streams, key=lambda item: item.value))


def datasets_for_stream(stream: CoverageStream | str) -> tuple[str, ...]:
    stream_value = stream.value if isinstance(stream, CoverageStream) else stream
    mapped = tuple(
        dataset
        for dataset, mapped_stream in DATASET_STREAM.items()
        if mapped_stream.value == stream_value
    )
    if not mapped:
        raise HistoricalCoverageError(f"unknown stream {stream_value}")
    return mapped


def _coverage_stream(stream: CoverageStream | str) -> CoverageStream:
    if isinstance(stream, CoverageStream):
        return stream
    try:
        return CoverageStream(stream)
    except ValueError as exc:
        raise HistoricalCoverageError(f"unknown stream {stream}") from exc


def align_coverage_interval_start(
    value: datetime,
    interval_seconds: int = 900,
) -> datetime:
    if interval_seconds <= 0:
        raise HistoricalCoverageError("interval_seconds must be > 0")
    utc_value = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if interval_seconds == 900:
        minute = (utc_value.minute // 15) * 15
        return utc_value.replace(minute=minute, second=0, microsecond=0)
    epoch = int(utc_value.timestamp())
    aligned = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(aligned, tz=UTC)


def pre_firehose_bound_seconds(
    stream: CoverageStream | str,
    policy: CollectionEvaluationPolicy = DEFAULT_COLLECTION_EVALUATION_POLICY,
) -> int:
    stream_value = _coverage_stream(stream)
    if stream_value is CoverageStream.FACTS:
        return (
            max(
                policy.encounter_producer_to_eventbridge_max_delay_seconds,
                policy.risk_producer_to_eventbridge_max_delay_seconds,
                policy.hazard_version_producer_to_eventbridge_max_delay_seconds,
            )
            + policy.eventbridge_max_event_age_seconds
        )
    return policy.hazard_geometry_producer_to_firehose_max_delay_seconds


def domain_3b_canonical_interval(
    *,
    stream: CoverageStream | str,
    metric_time: datetime,
    freshness_seconds: float,
    policy: CollectionEvaluationPolicy = DEFAULT_COLLECTION_EVALUATION_POLICY,
) -> tuple[datetime, datetime]:
    """Map Firehose transport-time freshness onto a conservative event-time interval."""

    interval_seconds = policy.coverage_interval_seconds
    freshness = max(float(freshness_seconds), 0.0)
    oldest_ingress = metric_time - timedelta(seconds=freshness)
    oldest_event = oldest_ingress - timedelta(
        seconds=pre_firehose_bound_seconds(stream, policy)
    )
    start = align_coverage_interval_start(oldest_event, interval_seconds)
    end = align_coverage_interval_start(metric_time, interval_seconds) + timedelta(
        seconds=interval_seconds
    )
    if end <= start:
        end = start + timedelta(seconds=interval_seconds)
    return start, end


def domain_3b_gap_dedup_id(
    collection_epoch_id: str,
    stream: CoverageStream | str,
    interval_start_utc: str,
    interval_end_utc: str,
) -> str:
    epoch = str(collection_epoch_id).strip()
    if not epoch or epoch.lower() in RESERVED_EPOCH_TOKENS:
        raise HistoricalCoverageError("collection_epoch_id must not be a staging token")
    stream_value = _coverage_stream(stream).value
    start = str(interval_start_utc).strip()
    end = str(interval_end_utc).strip()
    if not start or not end:
        raise HistoricalCoverageError("domain 3B interval bounds are required")
    return f"domain3b|{epoch}|{stream_value}|{start}|{end}"


@dataclass(frozen=True)
class FirehoseDeliveryMetrics:
    stream: CoverageStream
    freshness: tuple[tuple[datetime, float], ...] = ()
    incoming: tuple[tuple[datetime, float], ...] = ()
    success: tuple[tuple[datetime, float], ...] = ()


@dataclass(frozen=True)
class Domain3BImpairment:
    stream: CoverageStream
    metric_time: datetime
    freshness_seconds: float
    interval_start: datetime
    interval_end: datetime
    reason: str = S3_DESTINATION_FAILURE
    detection: str = "DATA_FRESHNESS_EXCEEDED"


def _metric_period_index(
    points: Sequence[tuple[datetime, float]],
    *,
    period_seconds: int,
    combine,
) -> dict[datetime, float]:
    indexed: dict[datetime, float] = {}
    for timestamp, value in points:
        key = align_coverage_interval_start(timestamp, period_seconds)
        if key in indexed:
            indexed[key] = combine(indexed[key], float(value))
        else:
            indexed[key] = float(value)
    return indexed


def _missing_freshness_fallback_starts(
    *,
    incoming_periods: dict[datetime, float],
    freshness_periods: dict[datetime, float],
    success_periods: dict[datetime, float],
    as_of: datetime,
    threshold_seconds: float,
) -> tuple[datetime, ...]:
    """Incoming starts whose S3 delivery stayed unresolved for the impairment window.

    A single buffering period without an S3 PUT is not Domain 3B. Fallback
    requires as_of >= start + threshold with no Success in [start, start+threshold)
    and no DataFreshness at or after the candidate start.
    """

    window = timedelta(seconds=threshold_seconds)
    freshness_times = tuple(sorted(freshness_periods))
    success_times = tuple(
        timestamp
        for timestamp, value in sorted(success_periods.items())
        if value > 0
    )
    starts: list[datetime] = []
    for t0, incoming_value in sorted(incoming_periods.items()):
        if incoming_value <= 0:
            continue
        if as_of < t0 + window:
            continue
        if any(timestamp >= t0 for timestamp in freshness_times):
            continue
        if any(t0 <= timestamp < t0 + window for timestamp in success_times):
            continue
        starts.append(t0)
        break
    return tuple(starts)


def domain_3b_impairments_from_metrics(
    metrics: FirehoseDeliveryMetrics,
    *,
    as_of: datetime | None = None,
    policy: CollectionEvaluationPolicy = DEFAULT_COLLECTION_EVALUATION_POLICY,
    freshness_threshold_seconds: int = DATA_FRESHNESS_THRESHOLD_SECONDS,
    metric_period_seconds: int = FIREHOSE_METRIC_PERIOD_SECONDS,
) -> tuple[Domain3BImpairment, ...]:
    """Detect POTENTIAL DOMAIN_3B impairments from Firehose delivery telemetry.

    CloudWatch is not durable historical truth. DataFreshness > 1800 is the
    primary rule. Absent DataFreshness is not healthy, but a single
    IncomingRecords period without Success is normal Firehose buffering.
    IncomingRecords == 0 does not by itself create a gap.
    """

    stream = metrics.stream
    threshold = float(freshness_threshold_seconds)
    freshness_periods = _metric_period_index(
        metrics.freshness, period_seconds=metric_period_seconds, combine=max
    )
    incoming_periods = _metric_period_index(
        metrics.incoming, period_seconds=metric_period_seconds, combine=lambda a, b: a + b
    )
    success_periods = _metric_period_index(
        metrics.success, period_seconds=metric_period_seconds, combine=lambda a, b: a + b
    )
    by_interval: dict[tuple[datetime, datetime], Domain3BImpairment] = {}

    def _add(
        metric_time: datetime,
        freshness_seconds: float,
        detection: str,
    ) -> None:
        start, end = domain_3b_canonical_interval(
            stream=stream,
            metric_time=metric_time,
            freshness_seconds=freshness_seconds,
            policy=policy,
        )
        key = (start, end)
        existing = by_interval.get(key)
        if existing is None or freshness_seconds > existing.freshness_seconds:
            by_interval[key] = Domain3BImpairment(
                stream=stream,
                metric_time=metric_time,
                freshness_seconds=freshness_seconds,
                interval_start=start,
                interval_end=end,
                reason=S3_DESTINATION_FAILURE,
                detection=detection,
            )

    for timestamp, value in metrics.freshness:
        if float(value) > threshold:
            _add(timestamp, float(value), "DATA_FRESHNESS_EXCEEDED")

    if as_of is not None:
        for t0 in _missing_freshness_fallback_starts(
            incoming_periods=incoming_periods,
            freshness_periods=freshness_periods,
            success_periods=success_periods,
            as_of=as_of,
            threshold_seconds=threshold,
        ):
            _add(
                t0 + timedelta(seconds=threshold),
                threshold,
                "MISSING_DELIVERY_TELEMETRY",
            )

    return tuple(
        sorted(
            by_interval.values(),
            key=lambda item: (item.interval_start, item.interval_end, item.stream.value),
        )
    )


def domain_3b_gap_record(
    *,
    impairment: Domain3BImpairment,
    collection_epoch_id: str,
) -> CollectionGapRecord:
    start_utc = canonicalize_utc_z(impairment.interval_start)
    end_utc = canonicalize_utc_z(impairment.interval_end)
    return CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=collection_epoch_id,
        affected_datasets=datasets_for_stream(impairment.stream),
        gap_domain=GapDomain.DOMAIN_3B,
        reason=S3_DESTINATION_FAILURE,
        uncertainty_class=UncertaintyClass.POTENTIAL_GAP,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc=start_utc,
        interval_start_utc=start_utc,
        interval_end_utc=end_utc,
        created_at_utc=start_utc,
        dedup_id=domain_3b_gap_dedup_id(
            collection_epoch_id,
            impairment.stream,
            start_utc,
            end_utc,
        ),
        source_subsystem="AWS/Firehose",
        evidence_refs=(
            f"firehose|{impairment.stream.value}|DeliveryToS3.DataFreshness",
            f"interval|{start_utc}|{end_utc}",
        ),
    )


def intervals_overlap(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    return start_a < end_b and start_b < end_a


def merge_intervals(
    intervals: Sequence[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda item: (item[0], item[1]))
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def uncovered_slices(
    window_start: datetime,
    window_end: datetime,
    intervals: Sequence[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    remaining = [(window_start, window_end)]
    for start, end in merge_intervals(intervals):
        next_remaining: list[tuple[datetime, datetime]] = []
        for rstart, rend in remaining:
            if end <= rstart or start >= rend:
                next_remaining.append((rstart, rend))
                continue
            if rstart < start:
                next_remaining.append((rstart, min(rend, start)))
            if rend > end:
                next_remaining.append((max(rstart, end), rend))
        remaining = [(start, end) for start, end in next_remaining if start < end]
    return remaining


def covers_window(
    window_start: datetime,
    window_end: datetime,
    intervals: Sequence[tuple[datetime, datetime]],
) -> bool:
    return not uncovered_slices(window_start, window_end, intervals)


def utc_date_prefixes_for_lookback(
    scan_as_of: datetime,
    lookback_seconds: int,
) -> list[str]:
    if scan_as_of.tzinfo is None or scan_as_of.utcoffset() != timedelta(0):
        raise HistoricalCoverageError("scan_as_of must be UTC")
    if lookback_seconds < 0:
        raise HistoricalCoverageError("lookback_seconds must be >= 0")
    start = scan_as_of - timedelta(seconds=lookback_seconds)
    prefixes: list[str] = []
    day = start.date()
    end_day = scan_as_of.date()
    while day <= end_day:
        prefixes.append(
            f"year={day.year:04d}/month={day.month:02d}/day={day.day:02d}"
        )
        day += timedelta(days=1)
    return prefixes


def collection_control_prefixes(
    scan_as_of: datetime,
    lookback_seconds: int,
) -> list[str]:
    return [
        f"dataset={COLLECTION_CONTROL_DATASET}/{prefix}/"
        for prefix in utc_date_prefixes_for_lookback(scan_as_of, lookback_seconds)
    ]


def _require_utc(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HistoricalCoverageError(f"{field_name} must be canonical UTC Z")
    try:
        return parse_utc_datetime(value)
    except HistoricalTimeError as exc:
        raise HistoricalCoverageError(f"invalid {field_name}") from exc


def _normalize_datasets(datasets: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for dataset in datasets:
        if dataset not in OPERATIONAL_DATASETS:
            raise HistoricalCoverageError(f"unknown dataset {dataset}")
        if dataset not in seen:
            ordered.append(dataset)
            seen.add(dataset)
    if not ordered:
        raise HistoricalCoverageError("datasets is required")
    return tuple(ordered)


def _same_epoch(record: Any, collection_epoch_id: str) -> bool:
    return getattr(record, "collection_epoch_id", None) == collection_epoch_id


def _build_active_periods(
    activations: Sequence[CollectionActivationRecord],
    deactivations: Sequence[CollectionDeactivationRecord],
) -> list[tuple[datetime, datetime | None]]:
    starts = sorted(parse_utc_datetime(item.enabled_at_utc) for item in activations)
    ends = sorted(
        parse_utc_datetime(item.deactivated_at_utc) for item in deactivations
    )
    periods: list[tuple[datetime, datetime | None]] = []
    end_index = 0
    for start in starts:
        period_end: datetime | None = None
        while end_index < len(ends) and ends[end_index] <= start:
            end_index += 1
        if end_index < len(ends):
            period_end = ends[end_index]
            end_index += 1
        periods.append((start, period_end))
    return periods


def _active_intervals(
    periods: Sequence[tuple[datetime, datetime | None]],
) -> list[tuple[datetime, datetime]]:
    far_future = datetime.max.replace(tzinfo=UTC)
    return [(start, end or far_future) for start, end in periods]


def _effective_recovery(
    gap: CollectionGapRecord,
    resolutions: Sequence[CollectionGapResolutionRecord],
) -> RecoveryState:
    proven = any(
        resolution.gap_dedup_id == gap.dedup_id
        and resolution.collection_epoch_id == gap.collection_epoch_id
        and resolution.recovery_state is RecoveryState.RESOLVED_PROVEN
        for resolution in resolutions
    )
    if proven:
        return RecoveryState.RESOLVED_PROVEN
    return gap.recovery_state


def _gap_applies(
    gap: CollectionGapRecord,
    datasets: Sequence[str],
) -> bool:
    return bool(set(gap.affected_datasets) & set(datasets))


def _blocking_gaps(
    *,
    gaps: Sequence[CollectionGapRecord],
    resolutions: Sequence[CollectionGapResolutionRecord],
    datasets: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> list[CollectionGapRecord]:
    blocking: list[CollectionGapRecord] = []
    for gap in gaps:
        if not _gap_applies(gap, datasets):
            continue
        if _effective_recovery(gap, resolutions) is RecoveryState.RESOLVED_PROVEN:
            continue
        gap_start = parse_utc_datetime(gap.interval_start_utc)
        gap_end = parse_utc_datetime(gap.interval_end_utc)
        if intervals_overlap(window_start, window_end, gap_start, gap_end):
            blocking.append(gap)
    return blocking


def bound_gap_dedup_id(collection_epoch_id: str, incident_dedup_id: str) -> str:
    epoch = str(collection_epoch_id).strip()
    incident = str(incident_dedup_id).strip()
    if not epoch or epoch.lower() in RESERVED_EPOCH_TOKENS:
        raise HistoricalCoverageError("collection_epoch_id must not be a staging token")
    if not incident:
        raise HistoricalCoverageError("incident_dedup_id is required")
    return f"bound|{epoch}|{incident}"


def missing_probe_gap_dedup_id(stream: CoverageStream | str, interval_start_utc: str) -> str:
    stream_value = stream.value if isinstance(stream, CoverageStream) else stream
    return f"missing-probe|{stream_value}|{interval_start_utc}"


def missing_probe_resolution_dedup_id(
    collection_epoch_id: str, gap_dedup_id: str
) -> str:
    epoch = str(collection_epoch_id).strip()
    gap = str(gap_dedup_id).strip()
    if not epoch or epoch.lower() in RESERVED_EPOCH_TOKENS:
        raise HistoricalCoverageError("collection_epoch_id must not be a staging token")
    if not gap:
        raise HistoricalCoverageError("gap_dedup_id is required")
    return f"resolved|{epoch}|{gap}|RESOLVED_PROVEN"


def collection_gap_from_dict(payload: dict[str, Any]) -> CollectionGapRecord:
    return CollectionGapRecord(
        control_schema_version=payload["control_schema_version"],
        record_type=ControlRecordType(payload["record_type"]),
        collection_epoch_id=payload["collection_epoch_id"],
        affected_datasets=tuple(payload["affected_datasets"]),
        gap_domain=GapDomain(payload["gap_domain"]),
        reason=payload["reason"],
        uncertainty_class=UncertaintyClass(payload["uncertainty_class"]),
        recovery_state=RecoveryState(payload["recovery_state"]),
        detected_at_utc=payload["detected_at_utc"],
        interval_start_utc=payload["interval_start_utc"],
        interval_end_utc=payload["interval_end_utc"],
        created_at_utc=payload["created_at_utc"],
        dedup_id=payload["dedup_id"],
        identity=payload.get("identity"),
        source_subsystem=payload.get("source_subsystem"),
        producer_source=payload.get("producer_source"),
        producer_detail_type=payload.get("producer_detail_type"),
        evidence_refs=tuple(payload.get("evidence_refs") or ()),
    )


def collection_gap_resolution_from_dict(
    payload: dict[str, Any],
) -> CollectionGapResolutionRecord:
    return CollectionGapResolutionRecord(
        control_schema_version=payload["control_schema_version"],
        record_type=ControlRecordType(payload["record_type"]),
        collection_epoch_id=payload["collection_epoch_id"],
        gap_dedup_id=payload["gap_dedup_id"],
        recovery_state=RecoveryState(payload["recovery_state"]),
        created_at_utc=payload["created_at_utc"],
        dedup_id=payload["dedup_id"],
    )


def coverage_interval_from_dict(payload: dict[str, Any]) -> CoverageIntervalRecord:
    return CoverageIntervalRecord(
        control_schema_version=payload["control_schema_version"],
        record_type=ControlRecordType(payload["record_type"]),
        collection_epoch_id=payload["collection_epoch_id"],
        stream=CoverageStream(payload["stream"]),
        interval_start_utc=payload["interval_start_utc"],
        interval_end_utc=payload["interval_end_utc"],
        created_at_utc=payload["created_at_utc"],
        dedup_id=payload["dedup_id"],
    )


def streams_for_incident(incident: UnboundCollectionIncident) -> tuple[CoverageStream, ...]:
    return required_streams_for(incident.affected_datasets)


def incident_overlaps_window(
    incident: UnboundCollectionIncident,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    start = parse_utc_datetime(incident.interval_start_utc)
    end = parse_utc_datetime(incident.interval_end_utc)
    return intervals_overlap(window_start, window_end, start, end)


def incident_blocks_stream(
    incident: UnboundCollectionIncident,
    stream: CoverageStream,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    if stream not in streams_for_incident(incident):
        return False
    return incident_overlaps_window(incident, window_start, window_end)


def _blocking_incidents(
    *,
    incidents: Sequence[UnboundCollectionIncident],
    datasets: Sequence[str],
    window_start: datetime,
    window_end: datetime,
) -> list[UnboundCollectionIncident]:
    requested = set(datasets)
    blocking: list[UnboundCollectionIncident] = []
    for incident in incidents:
        if not (set(incident.affected_datasets) & requested):
            continue
        if incident_overlaps_window(incident, window_start, window_end):
            blocking.append(incident)
    return blocking


blocking_gaps = _blocking_gaps
blocking_incidents = _blocking_incidents


def bound_gap_from_incident(
    incident: UnboundCollectionIncident,
    *,
    collection_epoch_id: str,
) -> CollectionGapRecord:
    return CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=collection_epoch_id,
        affected_datasets=incident.affected_datasets,
        gap_domain=incident.gap_domain,
        reason=incident.reason,
        uncertainty_class=incident.uncertainty_class,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc=incident.detected_at_utc,
        interval_start_utc=incident.interval_start_utc,
        interval_end_utc=incident.interval_end_utc,
        created_at_utc=incident.created_at_utc,
        dedup_id=bound_gap_dedup_id(collection_epoch_id, incident.dedup_id),
        identity=incident.identity,
        source_subsystem=incident.source_subsystem,
        producer_source=incident.producer_source,
        producer_detail_type=incident.producer_detail_type,
        evidence_refs=tuple(incident.evidence_refs)
        + (f"incident|{incident.dedup_id}",),
    )


def unbound_incident_from_dict(payload: dict[str, Any]) -> UnboundCollectionIncident:
    if payload.get("collection_epoch_id"):
        raise HistoricalCoverageError("staging incident must not carry an epoch")
    return UnboundCollectionIncident(
        control_schema_version=payload["control_schema_version"],
        record_type=ControlRecordType(payload["record_type"]),
        staging_state=payload.get("staging_state", STAGING_STATE),
        affected_datasets=tuple(payload["affected_datasets"]),
        gap_domain=GapDomain(payload["gap_domain"]),
        reason=payload["reason"],
        uncertainty_class=UncertaintyClass(payload["uncertainty_class"]),
        detected_at_utc=payload["detected_at_utc"],
        interval_start_utc=payload["interval_start_utc"],
        interval_end_utc=payload["interval_end_utc"],
        created_at_utc=payload["created_at_utc"],
        dedup_id=payload["dedup_id"],
        identity=payload.get("identity"),
        source_subsystem=payload.get("source_subsystem"),
        producer_source=payload.get("producer_source"),
        producer_detail_type=payload.get("producer_detail_type"),
        evidence_refs=tuple(payload.get("evidence_refs") or ()),
    )


def evaluate_collection_window(
    *,
    datasets: Iterable[str],
    start_utc: str,
    end_utc: str,
    as_of_utc: str,
    activations: Sequence[CollectionActivationRecord] = (),
    deactivations: Sequence[CollectionDeactivationRecord] = (),
    coverage_intervals: Sequence[CoverageIntervalRecord] = (),
    gap_records: Sequence[CollectionGapRecord] = (),
    resolutions: Sequence[CollectionGapResolutionRecord] = (),
    unbound_incidents: Sequence[UnboundCollectionIncident] = (),
    policy: CollectionEvaluationPolicy = DEFAULT_COLLECTION_EVALUATION_POLICY,
    collection_epoch_id: str,
) -> EvaluabilityResult:
    requested = _normalize_datasets(datasets)
    if not collection_epoch_id or not str(collection_epoch_id).strip():
        raise HistoricalCoverageError("collection_epoch_id is required")
    if str(collection_epoch_id).strip().lower() in RESERVED_EPOCH_TOKENS:
        raise HistoricalCoverageError(
            "collection_epoch_id must not be a staging token"
        )

    start = _require_utc(start_utc, "start_utc")
    end = _require_utc(end_utc, "end_utc")
    as_of = _require_utc(as_of_utc, "as_of_utc")
    if start >= end:
        raise HistoricalCoverageError("start_utc must precede end_utc")

    required_horizon = policy.required_horizon_seconds(requested)
    required_streams = required_streams_for(requested)
    stream_values = tuple(stream.value for stream in required_streams)

    epoch_activations = [
        record
        for record in activations
        if _same_epoch(record, collection_epoch_id)
    ]
    epoch_deactivations = [
        record
        for record in deactivations
        if _same_epoch(record, collection_epoch_id)
    ]
    epoch_coverage = [
        record
        for record in coverage_intervals
        if _same_epoch(record, collection_epoch_id)
    ]
    epoch_gaps = [
        record for record in gap_records if _same_epoch(record, collection_epoch_id)
    ]
    epoch_resolutions = [
        record for record in resolutions if _same_epoch(record, collection_epoch_id)
    ]

    if end > as_of:
        return EvaluabilityResult(
            evaluability=Evaluability.NOT_YET_EVALUABLE,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="window_ends_after_as_of",
        )

    periods = _build_active_periods(epoch_activations, epoch_deactivations)
    active_slices = _active_intervals(periods)
    inactive_in_window = uncovered_slices(start, end, active_slices)
    active_in_window = uncovered_slices(start, end, inactive_in_window)
    fully_inactive = not active_in_window
    fully_active = not inactive_in_window
    mixed = (not fully_active) and (not fully_inactive)

    if fully_inactive:
        return EvaluabilityResult(
            evaluability=Evaluability.NOT_ACTIVE,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="window_not_in_active_collection_period",
        )

    if mixed:
        for slice_start, slice_end in active_in_window:
            if _blocking_gaps(
                gaps=epoch_gaps,
                resolutions=epoch_resolutions,
                datasets=requested,
                window_start=slice_start,
                window_end=slice_end,
            ) or _blocking_incidents(
                incidents=unbound_incidents,
                datasets=requested,
                window_start=slice_start,
                window_end=slice_end,
            ):
                return EvaluabilityResult(
                    evaluability=Evaluability.GAP_OR_UNCERTAIN,
                    required_horizon_seconds=required_horizon,
                    required_streams=stream_values,
                    reason="open_gap_overlaps_active_slice",
                )
        return EvaluabilityResult(
            evaluability=Evaluability.NOT_ACTIVE,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="window_mixes_active_and_intentionally_inactive",
        )

    if as_of < end + timedelta(seconds=required_horizon):
        return EvaluabilityResult(
            evaluability=Evaluability.NOT_YET_EVALUABLE,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="late_arrival_horizon_not_closed",
        )

    for stream in required_streams:
        stream_intervals = [
            (
                parse_utc_datetime(record.interval_start_utc),
                parse_utc_datetime(record.interval_end_utc),
            )
            for record in epoch_coverage
            if record.stream is stream
        ]
        if not covers_window(start, end, stream_intervals):
            return EvaluabilityResult(
                evaluability=Evaluability.GAP_OR_UNCERTAIN,
                required_horizon_seconds=required_horizon,
                required_streams=stream_values,
                reason=f"missing_coverage_interval_{stream.value}",
            )

    if _blocking_gaps(
        gaps=epoch_gaps,
        resolutions=epoch_resolutions,
        datasets=requested,
        window_start=start,
        window_end=end,
    ):
        return EvaluabilityResult(
            evaluability=Evaluability.GAP_OR_UNCERTAIN,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="unresolved_gap_overlaps_window",
        )

    if _blocking_incidents(
        incidents=unbound_incidents,
        datasets=requested,
        window_start=start,
        window_end=end,
    ):
        return EvaluabilityResult(
            evaluability=Evaluability.GAP_OR_UNCERTAIN,
            required_horizon_seconds=required_horizon,
            required_streams=stream_values,
            reason="unbound_incident_overlaps_window",
        )

    return EvaluabilityResult(
        evaluability=Evaluability.EVALUABLE,
        required_horizon_seconds=required_horizon,
        required_streams=stream_values,
        reason="window_evaluable",
    )


def parse_control_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoricalCoverageError("malformed control JSONL") from exc
        if not isinstance(payload, dict):
            raise HistoricalCoverageError("malformed control JSONL")
        records.append(payload)
    return records


def probe_matches_payload(
    expected: CollectionProbeRecord,
    payload: dict[str, Any],
) -> bool:
    return (
        payload.get("record_type") == expected.record_type.value
        and payload.get("collection_epoch_id") == expected.collection_epoch_id
        and payload.get("probe_id") == expected.probe_id
        and payload.get("stream") == expected.stream.value
        and payload.get("interval_start_utc") == expected.interval_start_utc
        and payload.get("interval_end_utc") == expected.interval_end_utc
    )


def confirm_probes_in_jsonl(
    text: str,
    expected_probes: Sequence[CollectionProbeRecord],
) -> set[tuple[str, str, str, str]]:
    """Return exact-match keys found in already-decompressed JSONL.

    A prefix or object count is never confirmation. Callers must GetObject
    and gzip-decompress before invoking this function.
    """

    found: set[tuple[str, str, str, str]] = set()
    for payload in parse_control_jsonl(text):
        for expected in expected_probes:
            if probe_matches_payload(expected, payload):
                found.add(
                    (
                        expected.probe_id,
                        expected.stream.value,
                        expected.interval_start_utc,
                        expected.collection_epoch_id,
                    )
                )
    return found


def probe_dedup_id(
    stream: CoverageStream | str,
    interval_start_utc: str,
    probe_id: str,
) -> str:
    stream_value = stream.value if isinstance(stream, CoverageStream) else stream
    return f"probe|{stream_value}|{interval_start_utc}|{probe_id}"
