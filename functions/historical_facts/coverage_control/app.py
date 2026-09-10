"""15-minute coverage control and explicit DEACTIVATE.

Observation timestamps are supplied by the runtime clock only for control
records. They are never reused as operational fact event_time_utc.

A probe is confirmed only after List → GetObject → gzip → JSONL exact
match. Prefix existence and object count are never confirmation.
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wilvor_historical.coverage import (
    DEFAULT_COLLECTION_EVALUATION_POLICY,
    FIREHOSE_METRIC_PERIOD_SECONDS,
    FirehoseDeliveryMetrics,
    blocking_gaps,
    blocking_incidents,
    bound_gap_from_incident,
    collection_control_prefixes,
    collection_gap_from_dict,
    collection_gap_resolution_from_dict,
    confirm_probes_in_jsonl,
    coverage_interval_from_dict,
    covers_window,
    domain_3b_gap_record,
    domain_3b_impairments_from_metrics,
    incident_blocks_stream,
    intervals_overlap,
    missing_probe_gap_dedup_id,
    missing_probe_resolution_dedup_id,
    parse_control_jsonl,
    probe_dedup_id,
    unbound_incident_from_dict,
)
from wilvor_historical.coverage_contracts import (
    COLLECTION_CONTROL_DATASET,
    CONTROL_SCHEMA_VERSION,
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    CollectionEpochRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    CollectionProbeRecord,
    ControlRecordType,
    CoverageIntervalRecord,
    CoverageStream,
    GapDomain,
    RecoveryState,
    UncertaintyClass,
)
from wilvor_historical.time import canonicalize_utc_z, partition_date_utc


_RUNTIME_DIR = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_DIR))

from gap_writer import (  # noqa: E402
    ImmutableControlConflictError,
    put_immutable_json,
    write_bound_gap,
    write_collection_gap_fail_open,
    write_gap_resolution,
)


POLICY = DEFAULT_COLLECTION_EVALUATION_POLICY


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_utc(now: datetime | None = None) -> str:
    return canonicalize_utc_z(now or _now())


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _bucket() -> str:
    return _env("HISTORICAL_FACTS_BUCKET_NAME")


def _align_interval_start(value: datetime) -> datetime:
    minute = (value.minute // 15) * 15
    return value.replace(minute=minute, second=0, microsecond=0)


def _put_json(s3_client: Any, key: str, payload: dict[str, Any]) -> str:
    return put_immutable_json(
        s3_client=s3_client,
        bucket_name=_bucket(),
        key=key,
        payload=payload,
    ).key


def _list_keys(s3_client: Any, prefix: str) -> list[str]:
    keys: list[str] = []
    token = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": _bucket(), "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        response = s3_client.list_objects_v2(**kwargs)
        for item in response.get("Contents") or []:
            key = item.get("Key")
            if key:
                keys.append(key)
        if not response.get("IsTruncated"):
            break
        token = response.get("NextContinuationToken")
    return keys


def _get_json(s3_client: Any, key: str) -> dict[str, Any] | None:
    try:
        body = s3_client.get_object(Bucket=_bucket(), Key=key)["Body"].read()
        return json.loads(body.decode("utf-8"))
    except Exception:
        return None


def load_or_create_epoch(s3_client: Any, *, now_utc: str) -> str:
    keys = _list_keys(s3_client, "metadata/epoch/")
    for key in sorted(keys):
        payload = _get_json(s3_client, key)
        if payload and payload.get("collection_epoch_id"):
            return str(payload["collection_epoch_id"])
    epoch_id = str(uuid.uuid4())
    record = CollectionEpochRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_EPOCH,
        collection_epoch_id=epoch_id,
        bucket_name=_bucket(),
        created_at_utc=now_utc,
        dedup_id=f"epoch|{epoch_id}",
    )
    _put_json(s3_client, f"metadata/epoch/{epoch_id}.json", record.to_dict())
    return epoch_id


def _latest_period_state(s3_client: Any, epoch_id: str) -> str:
    activations = _list_keys(s3_client, "metadata/activation/")
    deactivations = _list_keys(s3_client, "metadata/deactivation/")
    latest_activation = None
    latest_deactivation = None
    for key in activations:
        payload = _get_json(s3_client, key)
        if payload and payload.get("collection_epoch_id") == epoch_id:
            latest_activation = payload.get("enabled_at_utc")
    for key in deactivations:
        payload = _get_json(s3_client, key)
        if payload and payload.get("collection_epoch_id") == epoch_id:
            latest_deactivation = payload.get("deactivated_at_utc")
    if latest_activation is None:
        return "missing"
    if latest_deactivation is None or latest_deactivation < latest_activation:
        return "open"
    return "closed"


def ensure_activation(s3_client: Any, *, epoch_id: str, now_utc: str) -> str | None:
    if _latest_period_state(s3_client, epoch_id) == "open":
        return None
    record = CollectionActivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_ACTIVATION,
        collection_epoch_id=epoch_id,
        enabled_at_utc=now_utc,
        created_at_utc=now_utc,
        dedup_id=f"activation|{epoch_id}|{now_utc}",
    )
    created = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    year, month, day = partition_date_utc(created)
    return _put_json(
        s3_client,
        f"metadata/activation/year={year}/month={month}/day={day}/{record.dedup_id}.json",
        record.to_dict(),
    )


def write_deactivation(s3_client: Any, *, epoch_id: str, now_utc: str) -> str:
    record = CollectionDeactivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_DEACTIVATION,
        collection_epoch_id=epoch_id,
        deactivated_at_utc=now_utc,
        created_at_utc=now_utc,
        dedup_id=f"deactivation|{epoch_id}|{now_utc}",
    )
    created = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    year, month, day = partition_date_utc(created)
    return _put_json(
        s3_client,
        f"metadata/deactivation/year={year}/month={month}/day={day}/{record.dedup_id}.json",
        record.to_dict(),
    )


def build_probe(
    *,
    stream: CoverageStream,
    epoch_id: str,
    interval_start: datetime,
    interval_end: datetime,
    now: datetime,
) -> CollectionProbeRecord:
    probe_id = str(uuid.uuid4())
    year, month, day = partition_date_utc(now)
    start = canonicalize_utc_z(interval_start)
    end = canonicalize_utc_z(interval_end)
    return CollectionProbeRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_PROBE,
        collection_epoch_id=epoch_id,
        stream=stream,
        probe_id=probe_id,
        interval_start_utc=start,
        interval_end_utc=end,
        observed_at_utc=canonicalize_utc_z(now),
        dataset=COLLECTION_CONTROL_DATASET,
        event_year=year,
        event_month=month,
        event_day=day,
        dedup_id=probe_dedup_id(stream, start, probe_id),
    )


def emit_facts_probe(events_client: Any, probe: CollectionProbeRecord) -> None:
    events_client.put_events(
        Entries=[
            {
                "EventBusName": _env("EVENT_BUS_NAME") or "default",
                "Source": "wilvor.historical.control",
                "DetailType": "collection.probe",
                "Detail": json.dumps(probe.to_dict(), separators=(",", ":")),
            }
        ]
    )


def emit_geometry_probe(firehose_client: Any, probe: CollectionProbeRecord) -> None:
    stream_name = _env("HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME")
    if not stream_name:
        raise RuntimeError("geometry stream name is required")
    firehose_client.put_record(
        DeliveryStreamName=stream_name,
        Record={"Data": json.dumps(probe.to_dict(), separators=(",", ":")).encode("utf-8")},
    )


def confirm_probes_from_bucket(
    s3_client: Any,
    expected: list[CollectionProbeRecord],
    *,
    scan_as_of: datetime,
) -> set[tuple[str, str, str, str]]:
    found: set[tuple[str, str, str, str]] = set()
    prefixes: list[str] = []
    for stream in (CoverageStream.FACTS, CoverageStream.GEOMETRY):
        lookback = POLICY.probe_lookback_seconds(stream)
        prefixes.extend(collection_control_prefixes(scan_as_of, lookback))
    seen_prefixes = []
    for prefix in prefixes:
        if prefix in seen_prefixes:
            continue
        seen_prefixes.append(prefix)
        for key in _list_keys(s3_client, prefix):
            try:
                raw = s3_client.get_object(Bucket=_bucket(), Key=key)["Body"].read()
                try:
                    text = gzip.decompress(raw).decode("utf-8")
                except OSError:
                    text = raw.decode("utf-8")
                found |= confirm_probes_in_jsonl(text, expected)
            except Exception:
                raise
    return found


def write_coverage_interval(
    s3_client: Any,
    *,
    stream: CoverageStream,
    epoch_id: str,
    start: datetime,
    end: datetime,
    now_utc: str,
) -> str:
    start_utc = canonicalize_utc_z(start)
    end_utc = canonicalize_utc_z(end)
    record = CoverageIntervalRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COVERAGE_INTERVAL,
        collection_epoch_id=epoch_id,
        stream=stream,
        interval_start_utc=start_utc,
        interval_end_utc=end_utc,
        created_at_utc=now_utc,
        dedup_id=f"coverage|{stream.value}|{start_utc}",
    )
    created = datetime.fromisoformat(now_utc.replace("Z", "+00:00"))
    year, month, day = partition_date_utc(created)
    return _put_json(
        s3_client,
        (
            f"metadata/coverage/stream={stream.value}/"
            f"year={year}/month={month}/day={day}/{record.dedup_id}.json"
        ),
        record.to_dict(),
    )


def write_missing_probe_gap(
    s3_client: Any,
    *,
    probe: CollectionProbeRecord,
    epoch_id: str,
    now_utc: str,
) -> str | None:
    datasets = (
        ("encounter", "risk", "hazard_version")
        if probe.stream is CoverageStream.FACTS
        else ("hazard_geometry",)
    )
    gap = CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=epoch_id,
        affected_datasets=datasets,
        gap_domain=GapDomain.CONTROL,
        reason="MISSING_PROBE",
        uncertainty_class=UncertaintyClass.TRANSPORT_OR_CONTROL_OUTAGE,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc=now_utc,
        interval_start_utc=probe.interval_start_utc,
        interval_end_utc=probe.interval_end_utc,
        created_at_utc=now_utc,
        dedup_id=missing_probe_gap_dedup_id(
            probe.stream, probe.interval_start_utc
        ),
        evidence_refs=(probe.probe_id, probe.dedup_id),
    )
    return write_collection_gap_fail_open(gap, s3_client=s3_client, bucket_name=_bucket())


def scan_processing_failed_errors(
    s3_client: Any,
    *,
    epoch_id: str,
    now_utc: str,
) -> int:
    written = 0
    for key in _list_keys(s3_client, "errors/result=processing-failed/"):
        try:
            raw = s3_client.get_object(Bucket=_bucket(), Key=key)["Body"].read()
        except Exception:
            continue
        interval_start = now_utc
        interval_end = canonicalize_utc_z(
            datetime.fromisoformat(now_utc.replace("Z", "+00:00")) + timedelta(days=1)
        )
        uncertainty = UncertaintyClass.POTENTIAL_GAP
        try:
            text = gzip.decompress(raw).decode("utf-8") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8")
            for payload in parse_control_jsonl(text) if text.startswith("{") else []:
                event_time = payload.get("event_time_utc") or (
                    payload.get("detail") or {}
                ).get("detected_at_utc")
                if isinstance(event_time, str) and event_time.endswith("Z"):
                    interval_start = event_time
                    interval_end = canonicalize_utc_z(
                        datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                        + timedelta(seconds=1)
                    )
                    uncertainty = UncertaintyClass.KNOWN_MISSING
                    break
        except Exception:
            uncertainty = UncertaintyClass.POTENTIAL_GAP
        gap = CollectionGapRecord(
            control_schema_version=CONTROL_SCHEMA_VERSION,
            record_type=ControlRecordType.COLLECTION_GAP,
            collection_epoch_id=epoch_id,
            affected_datasets=("encounter", "risk", "hazard_version", "hazard_geometry"),
            gap_domain=GapDomain.DOMAIN_3A,
            reason="TRANSFORM_PROCESSING_FAILED",
            uncertainty_class=uncertainty,
            recovery_state=RecoveryState.OPEN,
            detected_at_utc=now_utc,
            interval_start_utc=interval_start,
            interval_end_utc=interval_end,
            created_at_utc=now_utc,
            dedup_id=f"domain3a|{key}",
            evidence_refs=(key,),
        )
        if write_collection_gap_fail_open(gap, s3_client=s3_client, bucket_name=_bucket()):
            written += 1
    return written


def deactivate(*, s3_client: Any, now: datetime) -> dict[str, Any]:
    now_utc = _now_utc(now)
    epoch_id = load_or_create_epoch(s3_client, now_utc=now_utc)
    key = write_deactivation(s3_client, epoch_id=epoch_id, now_utc=now_utc)
    return {
        "action": "DEACTIVATE",
        "collection_epoch_id": epoch_id,
        "deactivation_key": key,
    }


def pending_probe_key(probe: CollectionProbeRecord) -> str:
    return f"metadata/probes/pending/{probe.stream.value}/{probe.dedup_id}.json"


def write_pending_probe(s3_client: Any, probe: CollectionProbeRecord) -> str:
    return _put_json(s3_client, pending_probe_key(probe), probe.to_dict())


def load_unbound_incidents(
    s3_client: Any,
) -> tuple[list[Any], bool]:
    incidents: list[Any] = []
    load_failed = False
    for key in _list_keys(s3_client, "metadata/incidents/"):
        payload = _get_json(s3_client, key)
        if not payload:
            load_failed = True
            continue
        try:
            incidents.append(unbound_incident_from_dict(payload))
        except Exception:
            load_failed = True
    return incidents, load_failed


def bind_unbound_incidents(
    s3_client: Any,
    *,
    epoch_id: str,
) -> tuple[list[Any], bool]:
    """Bind staging incidents to the current epoch. Original incidents stay.

    Returns (failed_incidents, unknown_failure). unknown_failure means at
    least one incident object could not be loaded; coverage must fail closed
    for every stream this cycle.
    """

    incidents, load_failed = load_unbound_incidents(s3_client)
    failed: list[Any] = []
    for incident in incidents:
        try:
            write_bound_gap(
                bound_gap_from_incident(incident, collection_epoch_id=epoch_id),
                s3_client=s3_client,
                bucket_name=_bucket(),
            )
        except Exception:
            failed.append(incident)
    return failed, load_failed


def _coverage_blocked_by_bind_failure(
    *,
    stream: CoverageStream,
    start: datetime,
    end: datetime,
    failed_incidents: list[Any],
    unknown_failure: bool,
) -> bool:
    if unknown_failure:
        return True
    return any(
        incident_blocks_stream(incident, stream, start, end)
        for incident in failed_incidents
    )


def _coverage_blocked_by_domain_3b_write_failure(
    *,
    stream: CoverageStream,
    start: datetime,
    end: datetime,
    failed_impairments: list[Any],
    metrics_query_failed: bool,
) -> bool:
    if metrics_query_failed:
        return True
    return any(
        impairment.stream is stream
        and intervals_overlap(
            start, end, impairment.interval_start, impairment.interval_end
        )
        for impairment in failed_impairments
    )


def _coverage_blocked_this_cycle(
    *,
    stream: CoverageStream,
    start: datetime,
    end: datetime,
    failed_incidents: list[Any],
    unknown_failure: bool,
    failed_3b: list[Any],
    metrics_query_failed: bool,
) -> bool:
    return _coverage_blocked_by_bind_failure(
        stream=stream,
        start=start,
        end=end,
        failed_incidents=failed_incidents,
        unknown_failure=unknown_failure,
    ) or _coverage_blocked_by_domain_3b_write_failure(
        stream=stream,
        start=start,
        end=end,
        failed_impairments=failed_3b,
        metrics_query_failed=metrics_query_failed,
    )


def _utc_metric_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _metric_points(results: list[dict[str, Any]], query_id: str) -> tuple[tuple[datetime, float], ...]:
    timestamps: list[Any] = []
    values: list[Any] = []
    for item in results:
        if item.get("Id") != query_id:
            continue
        timestamps.extend(item.get("Timestamps") or [])
        values.extend(item.get("Values") or [])
    points: list[tuple[datetime, float]] = []
    for timestamp, value in zip(timestamps, values):
        parsed = _utc_metric_timestamp(timestamp)
        if parsed is None:
            continue
        try:
            points.append((parsed, float(value)))
        except (TypeError, ValueError):
            continue
    return tuple(points)


def _firehose_metric_query(
    query_id: str,
    metric_name: str,
    stream_name: str,
    stat: str,
) -> dict[str, Any]:
    return {
        "Id": query_id,
        "MetricStat": {
            "Metric": {
                "Namespace": "AWS/Firehose",
                "MetricName": metric_name,
                "Dimensions": [
                    {"Name": "DeliveryStreamName", "Value": stream_name},
                ],
            },
            "Period": FIREHOSE_METRIC_PERIOD_SECONDS,
            "Stat": stat,
        },
        "ReturnData": True,
    }


def query_firehose_delivery_metrics(
    cloudwatch_client: Any,
    now: datetime,
) -> list[FirehoseDeliveryMetrics]:
    lookback = POLICY.probe_lookback_seconds(CoverageStream.FACTS)
    start = now - timedelta(seconds=lookback)
    streams: list[tuple[CoverageStream, str, str]] = []
    facts_name = _env("HISTORICAL_FACTS_FIREHOSE_STREAM_NAME")
    geometry_name = _env("HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME")
    if facts_name:
        streams.append((CoverageStream.FACTS, "facts", facts_name))
    if geometry_name:
        streams.append((CoverageStream.GEOMETRY, "geometry", geometry_name))
    queries = []
    for _stream, prefix, name in streams:
        queries.extend(
            [
                _firehose_metric_query(
                    f"{prefix}_freshness",
                    "DeliveryToS3.DataFreshness",
                    name,
                    "Maximum",
                ),
                _firehose_metric_query(
                    f"{prefix}_success",
                    "DeliveryToS3.Success",
                    name,
                    "Sum",
                ),
                _firehose_metric_query(
                    f"{prefix}_incoming",
                    "IncomingRecords",
                    name,
                    "Sum",
                ),
            ]
        )
    if not queries:
        return []
    results: list[dict[str, Any]] = []
    token = None
    while True:
        kwargs: dict[str, Any] = {
            "MetricDataQueries": queries,
            "StartTime": start,
            "EndTime": now,
            "ScanBy": "TimestampAscending",
        }
        if token:
            kwargs["NextToken"] = token
        response = cloudwatch_client.get_metric_data(**kwargs)
        results.extend(response.get("MetricDataResults") or [])
        token = response.get("NextToken")
        if not token:
            break
    series: list[FirehoseDeliveryMetrics] = []
    for stream, prefix, _name in streams:
        series.append(
            FirehoseDeliveryMetrics(
                stream=stream,
                freshness=_metric_points(results, f"{prefix}_freshness"),
                incoming=_metric_points(results, f"{prefix}_incoming"),
                success=_metric_points(results, f"{prefix}_success"),
            )
        )
    return series


def materialize_domain_3b_gaps(
    s3_client: Any,
    *,
    epoch_id: str,
    now: datetime,
    metrics_by_stream: list[FirehoseDeliveryMetrics],
) -> list[Any]:
    """Create-once current-epoch DOMAIN_3B gaps. Write failure is returned, not ignored."""

    failed: list[Any] = []
    for metrics in metrics_by_stream:
        for impairment in domain_3b_impairments_from_metrics(metrics, as_of=now):
            gap = domain_3b_gap_record(
                impairment=impairment,
                collection_epoch_id=epoch_id,
            )
            try:
                write_bound_gap(
                    gap,
                    s3_client=s3_client,
                    bucket_name=_bucket(),
                )
            except Exception:
                failed.append(impairment)
    return failed


def load_pending_probes(
    s3_client: Any,
    *,
    epoch_id: str,
) -> list[CollectionProbeRecord]:
    probes: list[CollectionProbeRecord] = []
    for key in _list_keys(s3_client, "metadata/probes/pending/"):
        payload = _get_json(s3_client, key)
        if not payload or payload.get("collection_epoch_id") != epoch_id:
            continue
        try:
            probes.append(
                CollectionProbeRecord(
                    control_schema_version=payload["control_schema_version"],
                    record_type=ControlRecordType(payload["record_type"]),
                    collection_epoch_id=payload["collection_epoch_id"],
                    stream=CoverageStream(payload["stream"]),
                    probe_id=payload["probe_id"],
                    interval_start_utc=payload["interval_start_utc"],
                    interval_end_utc=payload["interval_end_utc"],
                    observed_at_utc=payload["observed_at_utc"],
                    dataset=payload["dataset"],
                    event_year=payload["event_year"],
                    event_month=payload["event_month"],
                    event_day=payload["event_day"],
                    dedup_id=payload["dedup_id"],
                )
            )
        except Exception:
            continue
    return probes


def _load_json_records(
    s3_client: Any,
    prefix: str,
    parser: Any,
) -> tuple[list[Any], bool]:
    records: list[Any] = []
    load_failed = False
    for key in _list_keys(s3_client, prefix):
        payload = _get_json(s3_client, key)
        if not payload:
            load_failed = True
            continue
        try:
            records.append(parser(payload))
        except Exception:
            load_failed = True
    return records, load_failed


def _probe_for_missing_gap(
    gap: CollectionGapRecord,
    pending: list[CollectionProbeRecord],
) -> CollectionProbeRecord | None:
    expected_probe_id = None
    if gap.evidence_refs:
        expected_probe_id = gap.evidence_refs[0]
    matches = [
        probe
        for probe in pending
        if probe.collection_epoch_id == gap.collection_epoch_id
        and probe.interval_start_utc == gap.interval_start_utc
        and probe.interval_end_utc == gap.interval_end_utc
        and missing_probe_gap_dedup_id(probe.stream, probe.interval_start_utc)
        == gap.dedup_id
    ]
    if expected_probe_id:
        matches = [probe for probe in matches if probe.probe_id == expected_probe_id]
    if len(matches) != 1:
        return None
    return matches[0]


def write_missing_probe_resolution(
    s3_client: Any,
    *,
    gap: CollectionGapRecord,
    epoch_id: str,
    now_utc: str,
) -> Any:
    record = CollectionGapResolutionRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP_RESOLUTION,
        collection_epoch_id=epoch_id,
        gap_dedup_id=gap.dedup_id,
        recovery_state=RecoveryState.RESOLVED_PROVEN,
        created_at_utc=now_utc,
        dedup_id=missing_probe_resolution_dedup_id(epoch_id, gap.dedup_id),
    )
    return write_gap_resolution(
        record,
        s3_client=s3_client,
        bucket_name=_bucket(),
        partition_utc=gap.created_at_utc,
    )


def resolve_proven_missing_probes(
    s3_client: Any,
    *,
    epoch_id: str,
    now: datetime,
    now_utc: str,
    failed_incidents: list[Any],
    incident_load_failed: bool,
    failed_3b: list[Any] | None = None,
    metrics_query_failed: bool = False,
) -> int:
    """Append RESOLVED_PROVEN only for later-confirmed MISSING_PROBE gaps."""

    if incident_load_failed:
        return 0
    gaps, gaps_failed = _load_json_records(
        s3_client, "metadata/gaps/", collection_gap_from_dict
    )
    resolutions, resolutions_failed = _load_json_records(
        s3_client, "metadata/resolutions/", collection_gap_resolution_from_dict
    )
    coverage, coverage_failed = _load_json_records(
        s3_client, "metadata/coverage/", coverage_interval_from_dict
    )
    incidents, _ = load_unbound_incidents(s3_client)
    if gaps_failed or resolutions_failed or coverage_failed:
        return 0
    resolved_ids = {
        record.gap_dedup_id
        for record in resolutions
        if record.collection_epoch_id == epoch_id
        and record.recovery_state is RecoveryState.RESOLVED_PROVEN
    }
    pending = load_pending_probes(s3_client, epoch_id=epoch_id)
    candidates = [
        gap
        for gap in gaps
        if gap.collection_epoch_id == epoch_id
        and gap.reason == "MISSING_PROBE"
        and gap.gap_domain is GapDomain.CONTROL
        and gap.recovery_state is RecoveryState.OPEN
        and gap.dedup_id not in resolved_ids
    ]
    expected: list[CollectionProbeRecord] = []
    by_identity: dict[tuple[str, str, str, str], CollectionGapRecord] = {}
    for gap in candidates:
        probe = _probe_for_missing_gap(gap, pending)
        if probe is None:
            continue
        identity = (
            probe.probe_id,
            probe.stream.value,
            probe.interval_start_utc,
            probe.collection_epoch_id,
        )
        expected.append(probe)
        by_identity[identity] = gap
    if not expected:
        return 0
    found = confirm_probes_from_bucket(s3_client, expected, scan_as_of=now)
    written = 0
    for identity, gap in by_identity.items():
        if identity not in found:
            continue
        probe_id, stream_value, start_utc, _epoch = identity
        stream = CoverageStream(stream_value)
        start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        end = datetime.fromisoformat(gap.interval_end_utc.replace("Z", "+00:00"))
        if _coverage_blocked_this_cycle(
            stream=stream,
            start=start,
            end=end,
            failed_incidents=failed_incidents,
            unknown_failure=False,
            failed_3b=failed_3b or [],
            metrics_query_failed=metrics_query_failed,
        ):
            continue
        write_coverage_interval(
            s3_client,
            stream=stream,
            epoch_id=epoch_id,
            start=start,
            end=end,
            now_utc=now_utc,
        )
        horizon = POLICY.probe_lookback_seconds(stream)
        if now < end + timedelta(seconds=horizon):
            continue
        stream_coverage = [
            (
                datetime.fromisoformat(record.interval_start_utc.replace("Z", "+00:00")),
                datetime.fromisoformat(record.interval_end_utc.replace("Z", "+00:00")),
            )
            for record in coverage
            if record.collection_epoch_id == epoch_id and record.stream is stream
        ]
        # Include the interval just written this cycle.
        stream_coverage.append((start, end))
        if not covers_window(start, end, stream_coverage):
            continue
        other_gaps = [item for item in gaps if item.dedup_id != gap.dedup_id]
        if blocking_gaps(
            gaps=other_gaps,
            resolutions=resolutions,
            datasets=gap.affected_datasets,
            window_start=start,
            window_end=end,
        ) or blocking_incidents(
            incidents=incidents,
            datasets=gap.affected_datasets,
            window_start=start,
            window_end=end,
        ):
            continue
        try:
            write_missing_probe_resolution(
                s3_client,
                gap=gap,
                epoch_id=epoch_id,
                now_utc=now_utc,
            )
        except ImmutableControlConflictError:
            continue
        written += 1
    return written


def collect(
    *,
    s3_client: Any,
    events_client: Any,
    firehose_client: Any,
    now: datetime,
    pending_probes: list[CollectionProbeRecord] | None = None,
    cloudwatch_client: Any | None = None,
) -> dict[str, Any]:
    now_utc = _now_utc(now)
    epoch_id = load_or_create_epoch(s3_client, now_utc=now_utc)
    ensure_activation(s3_client, epoch_id=epoch_id, now_utc=now_utc)

    interval_end = _align_interval_start(now)
    interval_start = interval_end - timedelta(seconds=POLICY.coverage_interval_seconds)
    facts_probe = build_probe(
        stream=CoverageStream.FACTS,
        epoch_id=epoch_id,
        interval_start=interval_start,
        interval_end=interval_end,
        now=now,
    )
    geometry_probe = build_probe(
        stream=CoverageStream.GEOMETRY,
        epoch_id=epoch_id,
        interval_start=interval_start,
        interval_end=interval_end,
        now=now,
    )
    emit_facts_probe(events_client, facts_probe)
    emit_geometry_probe(firehose_client, geometry_probe)
    write_pending_probe(s3_client, facts_probe)
    write_pending_probe(s3_client, geometry_probe)

    failed_incidents, incident_load_failed = bind_unbound_incidents(
        s3_client,
        epoch_id=epoch_id,
    )

    failed_3b: list[Any] = []
    metrics_query_failed = False
    if cloudwatch_client is not None:
        try:
            metrics_by_stream = query_firehose_delivery_metrics(
                cloudwatch_client,
                now,
            )
            failed_3b = materialize_domain_3b_gaps(
                s3_client,
                epoch_id=epoch_id,
                now=now,
                metrics_by_stream=metrics_by_stream,
            )
        except Exception:
            metrics_query_failed = True
            failed_3b = []

    expected = list(pending_probes) if pending_probes is not None else load_pending_probes(
        s3_client,
        epoch_id=epoch_id,
    )
    due = [
        probe
        for probe in expected
        if now
        >= datetime.fromisoformat(probe.interval_end_utc.replace("Z", "+00:00"))
        + timedelta(seconds=POLICY.probe_confirm_delay_seconds())
    ]
    if due:
        found = confirm_probes_from_bucket(s3_client, due, scan_as_of=now)
        for probe in due:
            identity = (
                probe.probe_id,
                probe.stream.value,
                probe.interval_start_utc,
                probe.collection_epoch_id,
            )
            start = datetime.fromisoformat(probe.interval_start_utc.replace("Z", "+00:00"))
            end = datetime.fromisoformat(probe.interval_end_utc.replace("Z", "+00:00"))
            if identity in found:
                if not _coverage_blocked_this_cycle(
                    stream=probe.stream,
                    start=start,
                    end=end,
                    failed_incidents=failed_incidents,
                    unknown_failure=incident_load_failed,
                    failed_3b=failed_3b,
                    metrics_query_failed=metrics_query_failed,
                ):
                    write_coverage_interval(
                        s3_client,
                        stream=probe.stream,
                        epoch_id=epoch_id,
                        start=start,
                        end=end,
                        now_utc=now_utc,
                    )
            else:
                write_missing_probe_gap(
                    s3_client,
                    probe=probe,
                    epoch_id=epoch_id,
                    now_utc=now_utc,
                )

    scan_processing_failed_errors(s3_client, epoch_id=epoch_id, now_utc=now_utc)
    resolve_proven_missing_probes(
        s3_client,
        epoch_id=epoch_id,
        now=now,
        now_utc=now_utc,
        failed_incidents=failed_incidents,
        incident_load_failed=incident_load_failed,
        failed_3b=failed_3b,
        metrics_query_failed=metrics_query_failed,
    )
    return {
        "action": "COLLECT",
        "collection_epoch_id": epoch_id,
        "facts_probe_id": facts_probe.probe_id,
        "geometry_probe_id": geometry_probe.probe_id,
    }


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    import boto3

    s3_client = boto3.client("s3")
    now = _now()
    action = str((event or {}).get("action") or "COLLECT").upper()
    try:
        if action == "DEACTIVATE":
            result = deactivate(s3_client=s3_client, now=now)
            if not result.get("deactivation_key"):
                raise RuntimeError("deactivation write was not confirmed")
            return result
        return collect(
            s3_client=s3_client,
            events_client=boto3.client("events"),
            firehose_client=boto3.client("firehose"),
            cloudwatch_client=boto3.client("cloudwatch"),
            now=now,
        )
    except Exception:
        print(
            json.dumps(
                {
                    "metric": "CoverageControlFailure",
                    "action": action,
                }
            )
        )
        raise
