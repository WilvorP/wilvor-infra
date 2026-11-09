"""Coverage-control epoch, activation, deactivation, and exact probe confirm."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from typing import Any, Callable

import pytest
from botocore.exceptions import ClientError

from wilvor_historical.coverage import (
    bound_gap_dedup_id,
    bound_gap_from_incident,
    evaluate_collection_window,
    missing_probe_gap_dedup_id,
    missing_probe_resolution_dedup_id,
)
from wilvor_historical.coverage_contracts import (
    CONTROL_SCHEMA_VERSION,
    STAGING_STATE,
    CollectionActivationRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    CollectionProbeRecord,
    ControlRecordType,
    CoverageIntervalRecord,
    CoverageStream,
    COLLECTION_CONTROL_DATASET,
    Evaluability,
    GapDomain,
    RecoveryState,
    UncertaintyClass,
    UnboundCollectionIncident,
)


def _precondition_failed() -> ClientError:
    return ClientError(
        {
            "Error": {
                "Code": "PreconditionFailed",
                "Message": "At least one of the pre-conditions you specified did not hold",
            },
            "ResponseMetadata": {"HTTPStatusCode": 412},
        },
        "PutObject",
    )


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.versions: dict[str, list[bytes]] = {}
        self.put_calls: list[dict[str, Any]] = []

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "",
        IfNoneMatch: str | None = None,
        **kwargs,
    ):
        body = Body if isinstance(Body, (bytes, bytearray)) else bytes(Body)
        self.put_calls.append(
            {
                "Bucket": Bucket,
                "Key": Key,
                "Body": body,
                "ContentType": ContentType,
                "IfNoneMatch": IfNoneMatch,
                **kwargs,
            }
        )
        if IfNoneMatch == "*" and Key in self.objects:
            raise _precondition_failed()
        self.objects[Key] = body
        self.versions.setdefault(Key, []).append(body)
        return {}

    def get_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": SimpleNamespace(read=lambda: self.objects[Key])}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, ContinuationToken=None):
        keys = [key for key in sorted(self.objects) if key.startswith(Prefix)]
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }

    def head_object(self, *, Bucket: str, Key: str):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}


class FakeEvents:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def put_events(self, *, Entries):
        self.entries.extend(Entries)
        return {"FailedEntryCount": 0}


class FakeFirehose:
    def __init__(self) -> None:
        self.records: list[bytes] = []

    def put_record(self, *, DeliveryStreamName: str, Record):
        self.records.append(Record["Data"])
        return {}


class FakeCloudWatch:
    def __init__(self, results: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.results = list(results or [])
        self.error: Exception | None = None

    def get_metric_data(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"MetricDataResults": self.results}


@pytest.fixture
def coverage_control(
    load_repo_module: Callable[[str, str], ModuleType],
    monkeypatch: pytest.MonkeyPatch,
) -> ModuleType:
    monkeypatch.setenv("HISTORICAL_FACTS_BUCKET_NAME", "test-historical")
    monkeypatch.setenv("EVENT_BUS_NAME", "default")
    monkeypatch.setenv(
        "HISTORICAL_FACTS_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-facts",
    )
    monkeypatch.setenv(
        "HISTORICAL_GEOMETRY_FIREHOSE_STREAM_NAME",
        "wilvor-dev-historical-geometry",
    )
    return load_repo_module(
        "historical_facts_coverage_control_app",
        "functions/historical_facts/coverage_control/app.py",
    )


def test_deactivate_writes_same_epoch_and_returns_key(coverage_control):
    s3 = FakeS3()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=now,
    )
    deactivated = coverage_control.deactivate(s3_client=s3, now=now + timedelta(hours=1))
    assert deactivated["collection_epoch_id"] == first["collection_epoch_id"]
    assert deactivated["deactivation_key"] in s3.objects
    assert deactivated["deactivation_key"].startswith("metadata/deactivation/")
    later = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=now + timedelta(hours=2),
    )
    assert later["collection_epoch_id"] == first["collection_epoch_id"]
    activations = [key for key in s3.objects if key.startswith("metadata/activation/")]
    assert len(activations) == 2


def test_failed_deactivation_write_cannot_yield_verified_coverage(coverage_control):
    class FailingS3(FakeS3):
        def put_object(self, **kwargs):
            if kwargs["Key"].startswith("metadata/deactivation/"):
                raise RuntimeError("s3 down")
            return super().put_object(**kwargs)

    s3 = FailingS3()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=now,
    )
    with pytest.raises(RuntimeError):
        coverage_control.deactivate(s3_client=s3, now=now)


def test_probe_confirmation_requires_gzip_jsonl_exact_match(coverage_control):
    s3 = FakeS3()
    events = FakeEvents()
    firehose = FakeFirehose()
    now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    coverage_control.collect(
        s3_client=s3,
        events_client=events,
        firehose_client=firehose,
        now=now,
    )
    assert events.entries[0]["Source"] == "wilvor.historical.control"
    assert events.entries[0]["DetailType"] == "collection.probe"
    assert firehose.records
    probe = CollectionProbeRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_PROBE,
        collection_epoch_id="epoch-1",
        stream=CoverageStream.FACTS,
        probe_id="probe-old",
        interval_start_utc="2026-07-16T12:00:00Z",
        interval_end_utc="2026-07-16T12:15:00Z",
        observed_at_utc="2026-07-16T12:15:00Z",
        dataset=COLLECTION_CONTROL_DATASET,
        event_year="2026",
        event_month="07",
        event_day="16",
        dedup_id="probe|facts|2026-07-16T12:00:00Z|probe-old",
    )
    earlier_key = (
        "dataset=_collection_control/year=2026/month=07/day=16/aaa.json.gz"
    )
    later_key = "dataset=_collection_control/year=2026/month=07/day=16/zzz.json.gz"
    s3.objects[later_key] = gzip.compress(b'{"probe_id":"other"}\n')
    s3.objects[earlier_key] = gzip.compress(
        (json.dumps(probe.to_dict()) + "\n").encode("utf-8")
    )
    found = coverage_control.confirm_probes_from_bucket(
        s3,
        [probe],
        scan_as_of=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
    )
    assert ("probe-old", "facts", "2026-07-16T12:00:00Z", "epoch-1") in found
    assert later_key > earlier_key


def test_later_collect_confirms_persisted_pending_probe(coverage_control):
    s3 = FakeS3()
    first_now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now,
    )
    pending_keys = [key for key in s3.objects if key.startswith("metadata/probes/pending/")]
    assert pending_keys
    first_facts = next(
        key for key in pending_keys if "/facts/" in key
    )
    payload = json.loads(s3.objects[first_facts].decode("utf-8"))
    control_key = (
        "dataset=_collection_control/year=2026/month=07/day=18/probe.json.gz"
    )
    s3.objects[control_key] = gzip.compress(
        (json.dumps(payload) + "\n").encode("utf-8")
    )
    later = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now + timedelta(seconds=1800),
    )
    assert later["collection_epoch_id"] == first["collection_epoch_id"]
    coverage_keys = [key for key in s3.objects if key.startswith("metadata/coverage/")]
    assert coverage_keys


def test_malformed_jsonl_is_not_confirmation(coverage_control):
    s3 = FakeS3()
    s3.objects[
        "dataset=_collection_control/year=2026/month=07/day=18/bad.json.gz"
    ] = gzip.compress(b'{"ok":true}\nnot-json\n')
    probe = CollectionProbeRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_PROBE,
        collection_epoch_id="epoch-1",
        stream=CoverageStream.FACTS,
        probe_id="probe-1",
        interval_start_utc="2026-07-18T12:00:00Z",
        interval_end_utc="2026-07-18T12:15:00Z",
        observed_at_utc="2026-07-18T12:15:00Z",
        dataset=COLLECTION_CONTROL_DATASET,
        event_year="2026",
        event_month="07",
        event_day="18",
        dedup_id="probe|facts|2026-07-18T12:00:00Z|probe-1",
    )
    with pytest.raises(Exception):
        coverage_control.confirm_probes_from_bucket(
            s3,
            [probe],
            scan_as_of=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
        )


def _staging_incident(
    *,
    datasets: tuple[str, ...] = ("encounter",),
    start: str = "2026-07-18T12:00:00Z",
    end: str = "2026-07-18T12:00:01Z",
    dedup_id: str = "domain1|encounter|enc-1|2026-07-18T12:00:00Z",
    reason: str = "PRODUCER_PUT_EVENTS_FAILURE",
    domain: GapDomain = GapDomain.DOMAIN_1,
) -> UnboundCollectionIncident:
    return UnboundCollectionIncident(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.UNBOUND_COLLECTION_INCIDENT,
        staging_state=STAGING_STATE,
        affected_datasets=datasets,
        gap_domain=domain,
        reason=reason,
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        detected_at_utc=start,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=start,
        dedup_id=dedup_id,
        identity="enc-1",
        source_subsystem="encounter",
        producer_source="wilvor.encounter",
        producer_detail_type="encounter.updated",
    )


def _put_incident(s3: FakeS3, incident: UnboundCollectionIncident) -> str:
    key = (
        "metadata/incidents/year=2026/month=07/day=18/"
        f"{incident.dedup_id}.json"
    )
    s3.objects[key] = json.dumps(incident.to_dict(), separators=(",", ":")).encode(
        "utf-8"
    )
    return key


def _persist_pending_probe(s3: FakeS3) -> dict[str, Any]:
    pending_keys = [
        key for key in s3.objects if key.startswith("metadata/probes/pending/")
    ]
    first_facts = next(key for key in pending_keys if "/facts/" in key)
    payload = json.loads(s3.objects[first_facts].decode("utf-8"))
    s3.objects[
        "dataset=_collection_control/year=2026/month=07/day=18/probe.json.gz"
    ] = gzip.compress((json.dumps(payload) + "\n").encode("utf-8"))
    return payload


def test_coverage_control_binds_incident_to_current_epoch(coverage_control):
    s3 = FakeS3()
    first_now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now,
    )
    incident = _staging_incident()
    incident_key = _put_incident(s3, incident)
    _persist_pending_probe(s3)
    later = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now + timedelta(seconds=1800),
    )
    assert later["collection_epoch_id"] == first["collection_epoch_id"]
    bound_dedup = bound_gap_dedup_id(first["collection_epoch_id"], incident.dedup_id)
    bound_keys = [
        key
        for key in s3.objects
        if key.startswith("metadata/gaps/") and bound_dedup in key
    ]
    assert len(bound_keys) == 1
    bound_payload = json.loads(s3.objects[bound_keys[0]].decode("utf-8"))
    assert bound_payload["record_type"] == "COLLECTION_GAP"
    assert bound_payload["collection_epoch_id"] == first["collection_epoch_id"]
    assert bound_payload["dedup_id"] == bound_dedup
    assert bound_payload["created_at_utc"] == incident.created_at_utc
    assert incident_key in s3.objects
    original = json.loads(s3.objects[incident_key].decode("utf-8"))
    assert original["record_type"] == "UNBOUND_COLLECTION_INCIDENT"
    assert "collection_epoch_id" not in original
    coverage_keys = [key for key in s3.objects if key.startswith("metadata/coverage/")]
    assert coverage_keys


def test_repeated_binding_is_deterministic(coverage_control):
    s3 = FakeS3()
    now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=now,
    )
    incident = _staging_incident()
    _put_incident(s3, incident)
    coverage_control.bind_unbound_incidents(
        s3, epoch_id=first["collection_epoch_id"]
    )
    first_bound = {
        key: s3.objects[key]
        for key in s3.objects
        if key.startswith("metadata/gaps/") and "bound|" in key
    }
    coverage_control.bind_unbound_incidents(
        s3, epoch_id=first["collection_epoch_id"]
    )
    second_bound = {
        key: s3.objects[key]
        for key in s3.objects
        if key.startswith("metadata/gaps/") and "bound|" in key
    }
    assert first_bound == second_bound
    expected = bound_gap_from_incident(
        incident, collection_epoch_id=first["collection_epoch_id"]
    )
    assert list(first_bound) == [
        (
            "metadata/gaps/year=2026/month=07/day=18/"
            f"{expected.dedup_id}.json"
        )
    ]
    assert json.loads(next(iter(first_bound.values()))) == expected.to_dict()
    bound_key = next(iter(first_bound))
    assert len(s3.versions[bound_key]) == 1
    bound_puts = [call for call in s3.put_calls if call["Key"] == bound_key]
    assert bound_puts
    assert all(call["IfNoneMatch"] == "*" for call in bound_puts)


def test_repeated_binding_does_not_create_another_version(coverage_control):
    s3 = FakeS3()
    now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=now,
    )
    incident = _staging_incident()
    incident_key = _put_incident(s3, incident)
    coverage_control.bind_unbound_incidents(
        s3, epoch_id=first["collection_epoch_id"]
    )
    bound_keys = [key for key in s3.objects if key.startswith("metadata/gaps/")]
    assert len(bound_keys) == 1
    bound_key = bound_keys[0]
    puts_after_first = len(s3.put_calls)
    coverage_control.bind_unbound_incidents(
        s3, epoch_id=first["collection_epoch_id"]
    )
    retry_puts = s3.put_calls[puts_after_first:]
    retry_bound_puts = [call for call in retry_puts if call["Key"] == bound_key]
    assert retry_bound_puts
    assert all(call["IfNoneMatch"] == "*" for call in retry_bound_puts)
    assert len(s3.versions[bound_key]) == 1
    assert incident_key in s3.objects
    unconditional = [
        call
        for call in retry_bound_puts
        if call["IfNoneMatch"] != "*"
    ]
    assert unconditional == []


def test_bind_failure_prevents_healthy_coverage_interval(coverage_control, monkeypatch):
    s3 = FakeS3()
    first_now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now,
    )
    _put_incident(s3, _staging_incident())
    _persist_pending_probe(s3)

    def boom(*args, **kwargs):
        raise RuntimeError("bind failed")

    monkeypatch.setattr(coverage_control, "write_bound_gap", boom)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now + timedelta(seconds=1800),
    )
    coverage_keys = [key for key in s3.objects if key.startswith("metadata/coverage/")]
    assert coverage_keys == []
    incident_keys = [
        key for key in s3.objects if key.startswith("metadata/incidents/")
    ]
    assert incident_keys


def test_geometry_staging_bind_failure_blocks_geometry_coverage_only(
    coverage_control,
    monkeypatch,
):
    s3 = FakeS3()
    first_now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now,
    )
    _put_incident(
        s3,
        _staging_incident(
            datasets=("hazard_geometry",),
            reason="GEOMETRY_PUT_FAILURE",
            domain=GapDomain.GEOMETRY,
            dedup_id="sigmet|GEOMETRY_PUT_FAILURE|hazard-1#v1|2026-07-18T12:00:00Z",
        ),
    )
    pending_keys = [
        key for key in s3.objects if key.startswith("metadata/probes/pending/")
    ]
    for pending in pending_keys:
        payload = json.loads(s3.objects[pending].decode("utf-8"))
        stream = payload["stream"]
        s3.objects[
            f"dataset=_collection_control/year=2026/month=07/day=18/{stream}.json.gz"
        ] = gzip.compress((json.dumps(payload) + "\n").encode("utf-8"))

    def boom(*args, **kwargs):
        raise RuntimeError("bind failed")

    monkeypatch.setattr(coverage_control, "write_bound_gap", boom)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now + timedelta(seconds=1800),
    )
    coverage_keys = [key for key in s3.objects if key.startswith("metadata/coverage/")]
    assert any("/stream=facts/" in key for key in coverage_keys)
    assert not any("/stream=geometry/" in key for key in coverage_keys)


def test_epoch_creation_retry_reuses_existing_epoch(coverage_control):
    s3 = FakeS3()
    now = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
    first = coverage_control.load_or_create_epoch(
        s3, now_utc="2026-07-18T12:00:00Z"
    )
    epoch_puts = [
        call for call in s3.put_calls if call["Key"].startswith("metadata/epoch/")
    ]
    assert len(epoch_puts) == 1
    assert epoch_puts[0]["IfNoneMatch"] == "*"
    second = coverage_control.load_or_create_epoch(
        s3, now_utc="2026-07-18T12:15:00Z"
    )
    assert second == first
    later_epoch_puts = [
        call for call in s3.put_calls if call["Key"].startswith("metadata/epoch/")
    ]
    assert later_epoch_puts == epoch_puts
    epoch_key = epoch_puts[0]["Key"]
    assert len(s3.versions[epoch_key]) == 1
    retry_same_payload = coverage_control._put_json(
        s3,
        epoch_key,
        json.loads(s3.objects[epoch_key].decode("utf-8")),
    )
    assert retry_same_payload == epoch_key
    assert len(s3.versions[epoch_key]) == 1


def test_activation_and_deactivation_retry_is_idempotent(coverage_control):
    s3 = FakeS3()
    now_utc = "2026-07-18T13:00:00Z"
    epoch_id = coverage_control.load_or_create_epoch(s3, now_utc=now_utc)
    first = coverage_control.write_deactivation(
        s3, epoch_id=epoch_id, now_utc=now_utc
    )
    second = coverage_control.write_deactivation(
        s3, epoch_id=epoch_id, now_utc=now_utc
    )
    assert first == second
    assert len(s3.versions[first]) == 1
    deactivation_puts = [call for call in s3.put_calls if call["Key"] == first]
    assert all(call["IfNoneMatch"] == "*" for call in deactivation_puts)
    activation = coverage_control.ensure_activation(
        s3, epoch_id=epoch_id, now_utc="2026-07-18T14:00:00Z"
    )
    assert activation
    again = coverage_control._put_json(
        s3,
        activation,
        json.loads(s3.objects[activation].decode("utf-8")),
    )
    assert again == activation
    assert len(s3.versions[activation]) == 1


def test_binding_conflict_prevents_healthy_coverage_interval(coverage_control):
    s3 = FakeS3()
    first_now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now,
    )
    incident = _staging_incident()
    _put_incident(s3, incident)
    expected = bound_gap_from_incident(
        incident, collection_epoch_id=first["collection_epoch_id"]
    )
    conflict_key = (
        "metadata/gaps/year=2026/month=07/day=18/"
        f"{expected.dedup_id}.json"
    )
    s3.objects[conflict_key] = b'{"record_type":"COLLECTION_GAP","dedup_id":"other"}'
    s3.versions[conflict_key] = [s3.objects[conflict_key]]
    _persist_pending_probe(s3)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=first_now + timedelta(seconds=1800),
    )
    assert len(s3.versions[conflict_key]) == 1
    coverage_keys = [key for key in s3.objects if key.startswith("metadata/coverage/")]
    assert coverage_keys == []


FACTS_HORIZON = 173760
PROBE_CONFIRM_DELAY = 1800
FIRST_COLLECT = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
MISSING_DUE = FIRST_COLLECT + timedelta(seconds=PROBE_CONFIRM_DELAY)
HORIZON_CLOSED = datetime(2026, 7, 20, 12, 32, tzinfo=timezone.utc)


def _facts_probe_payload(s3: FakeS3, *, start: str = "2026-07-18T12:00:00Z") -> dict[str, Any]:
    for key in sorted(s3.objects):
        if not key.startswith("metadata/probes/pending/facts/"):
            continue
        payload = json.loads(s3.objects[key].decode("utf-8"))
        if payload.get("interval_start_utc") == start:
            return payload
    raise AssertionError("facts pending probe not found")


def _put_control_jsonl(s3: FakeS3, payload: dict[str, Any], name: str) -> None:
    s3.objects[
        f"dataset=_collection_control/year=2026/month=07/day=18/{name}"
    ] = gzip.compress((json.dumps(payload) + "\n").encode("utf-8"))


def _missing_gap_payloads(s3: FakeS3) -> list[dict[str, Any]]:
    gaps = []
    for key, body in s3.objects.items():
        if not key.startswith("metadata/gaps/") or "missing-probe|" not in key:
            continue
        gaps.append(json.loads(body.decode("utf-8")))
    return gaps


def _open_domain_gap(
    *,
    domain: GapDomain,
    reason: str,
    datasets: tuple[str, ...],
    epoch: str,
) -> CollectionGapRecord:
    return CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=epoch,
        affected_datasets=datasets,
        gap_domain=domain,
        reason=reason,
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc="2026-07-18T12:00:00Z",
        interval_start_utc="2026-07-18T12:00:00Z",
        interval_end_utc="2026-07-18T12:00:01Z",
        created_at_utc="2026-07-18T12:00:00Z",
        dedup_id=f"independent|{domain.value}|{reason}",
    )


def test_missing_probe_writes_open_gap(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    facts_gaps = [
        gap
        for gap in _missing_gap_payloads(s3)
        if gap["dedup_id"]
        == missing_probe_gap_dedup_id(CoverageStream.FACTS, "2026-07-18T12:00:00Z")
    ]
    assert facts_gaps
    gap = facts_gaps[0]
    assert gap["reason"] == "MISSING_PROBE"
    assert gap["recovery_state"] == "OPEN"
    assert gap["collection_epoch_id"] == first["collection_epoch_id"]
    assert first["facts_probe_id"] in gap["evidence_refs"]
    assert not any(key.startswith("metadata/resolutions/") for key in s3.objects)


def test_late_probe_before_horizon_does_not_resolve(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    original_gap_key = next(
        key for key in s3.objects if "missing-probe|facts|" in key
    )
    original_gap = s3.objects[original_gap_key]
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE + timedelta(minutes=15),
    )
    assert s3.objects[original_gap_key] == original_gap
    assert json.loads(original_gap)["recovery_state"] == "OPEN"
    assert any(key.startswith("metadata/coverage/") for key in s3.objects)
    assert not any(key.startswith("metadata/resolutions/") for key in s3.objects)
    result = evaluate_collection_window(
        datasets=("encounter",),
        start_utc="2026-07-18T12:00:00Z",
        end_utc="2026-07-18T12:15:00Z",
        as_of_utc="2026-07-20T12:31:00Z",
        activations=[
            CollectionActivationRecord(
                control_schema_version=CONTROL_SCHEMA_VERSION,
                record_type=ControlRecordType.COLLECTION_ACTIVATION,
                collection_epoch_id=first["collection_epoch_id"],
                enabled_at_utc="2026-07-18T12:00:00Z",
                created_at_utc="2026-07-18T12:00:00Z",
                dedup_id="activation-test",
            )
        ],
        coverage_intervals=[
            CoverageIntervalRecord(
                control_schema_version=CONTROL_SCHEMA_VERSION,
                record_type=ControlRecordType.COVERAGE_INTERVAL,
                collection_epoch_id=first["collection_epoch_id"],
                stream=CoverageStream.FACTS,
                interval_start_utc="2026-07-18T12:00:00Z",
                interval_end_utc="2026-07-18T12:15:00Z",
                created_at_utc="2026-07-18T12:46:00Z",
                dedup_id="coverage|facts|2026-07-18T12:00:00Z",
            )
        ],
        gap_records=[collection_gap_record_from_payload(json.loads(original_gap))],
        collection_epoch_id=first["collection_epoch_id"],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_confirmed_probe_after_horizon_writes_resolved_proven(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    gap_key = next(key for key in s3.objects if "missing-probe|facts|" in key)
    original_gap = s3.objects[gap_key]
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    later = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=HORIZON_CLOSED,
    )
    assert later["collection_epoch_id"] == first["collection_epoch_id"]
    assert s3.objects[gap_key] == original_gap
    gap = json.loads(original_gap)
    resolution_dedup = missing_probe_resolution_dedup_id(
        first["collection_epoch_id"], gap["dedup_id"]
    )
    resolution_keys = [
        key for key in s3.objects if key.startswith("metadata/resolutions/")
    ]
    assert len(resolution_keys) == 1
    resolution = json.loads(s3.objects[resolution_keys[0]])
    assert resolution["record_type"] == "COLLECTION_GAP_RESOLUTION"
    assert resolution["recovery_state"] == "RESOLVED_PROVEN"
    assert resolution["gap_dedup_id"] == gap["dedup_id"]
    assert resolution["collection_epoch_id"] == first["collection_epoch_id"]
    assert resolution["dedup_id"] == resolution_dedup
    assert len(s3.versions[resolution_keys[0]]) == 1
    result = evaluate_collection_window(
        datasets=("encounter",),
        start_utc="2026-07-18T12:00:00Z",
        end_utc="2026-07-18T12:15:00Z",
        as_of_utc="2026-07-20T12:31:00Z",
        activations=[
            CollectionActivationRecord(
                control_schema_version=CONTROL_SCHEMA_VERSION,
                record_type=ControlRecordType.COLLECTION_ACTIVATION,
                collection_epoch_id=first["collection_epoch_id"],
                enabled_at_utc="2026-07-18T12:00:00Z",
                created_at_utc="2026-07-18T12:00:00Z",
                dedup_id="activation-test",
            )
        ],
        coverage_intervals=[
            CoverageIntervalRecord(
                control_schema_version=CONTROL_SCHEMA_VERSION,
                record_type=ControlRecordType.COVERAGE_INTERVAL,
                collection_epoch_id=first["collection_epoch_id"],
                stream=CoverageStream.FACTS,
                interval_start_utc="2026-07-18T12:00:00Z",
                interval_end_utc="2026-07-18T12:15:00Z",
                created_at_utc="2026-07-20T12:16:00Z",
                dedup_id="coverage|facts|2026-07-18T12:00:00Z",
            )
        ],
        gap_records=[
            collection_gap_record_from_payload(gap)
        ],
        resolutions=[
            CollectionGapResolutionRecord(
                control_schema_version=resolution["control_schema_version"],
                record_type=ControlRecordType.COLLECTION_GAP_RESOLUTION,
                collection_epoch_id=resolution["collection_epoch_id"],
                gap_dedup_id=resolution["gap_dedup_id"],
                recovery_state=RecoveryState.RESOLVED_PROVEN,
                created_at_utc=resolution["created_at_utc"],
                dedup_id=resolution["dedup_id"],
            )
        ],
        collection_epoch_id=first["collection_epoch_id"],
    )
    assert result.evaluability is Evaluability.EVALUABLE


def collection_gap_record_from_payload(payload: dict[str, Any]) -> CollectionGapRecord:
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
        evidence_refs=tuple(payload.get("evidence_refs") or ()),
    )


def test_unrelated_or_foreign_probe_cannot_resolve(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    original = _facts_probe_payload(s3)
    unrelated = dict(original)
    unrelated["probe_id"] = "other-probe"
    unrelated["dedup_id"] = "probe|facts|2026-07-18T12:00:00Z|other-probe"
    foreign = dict(original)
    foreign["collection_epoch_id"] = "other-epoch"
    _put_control_jsonl(s3, unrelated, "unrelated.json.gz")
    _put_control_jsonl(s3, foreign, "foreign.json.gz")
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=HORIZON_CLOSED,
    )
    assert first["facts_probe_id"] == original["probe_id"]
    assert not any(key.startswith("metadata/resolutions/") for key in s3.objects)


def test_independent_open_gap_prevents_missing_probe_resolution(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    blockers = (
        (GapDomain.DOMAIN_1, "PRODUCER_PUT_EVENTS_FAILURE", ("encounter",)),
        (GapDomain.DOMAIN_2, "EVENTBRIDGE_TARGET_FAILURE", ("encounter",)),
        (GapDomain.DOMAIN_3A, "TRANSFORM_PROCESSING_FAILED", ("encounter",)),
        (GapDomain.DOMAIN_3B, "DESTINATION_FAILURE", ("encounter",)),
        (GapDomain.GEOMETRY, "GEOMETRY_PUT_FAILURE", ("hazard_geometry", "encounter")),
    )
    for domain, reason, datasets in blockers:
        local = FakeS3()
        local.objects = dict(s3.objects)
        local.versions = {key: list(bodies) for key, bodies in s3.versions.items()}
        local.put_calls = list(s3.put_calls)
        independent = _open_domain_gap(
            domain=domain,
            reason=reason,
            datasets=datasets,
            epoch=first["collection_epoch_id"],
        )
        key = (
            "metadata/gaps/year=2026/month=07/day=18/"
            f"{independent.dedup_id}.json"
        )
        local.objects[key] = json.dumps(independent.to_dict(), separators=(",", ":")).encode(
            "utf-8"
        )
        local.versions[key] = [local.objects[key]]
        coverage_control.collect(
            s3_client=local,
            events_client=FakeEvents(),
            firehose_client=FakeFirehose(),
            now=HORIZON_CLOSED,
        )
        assert not any(
            obj_key.startswith("metadata/resolutions/") for obj_key in local.objects
        ), reason


def test_repeated_resolution_is_create_once(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    gap_key = next(key for key in s3.objects if "missing-probe|facts|" in key)
    original_gap = s3.objects[gap_key]
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=HORIZON_CLOSED,
    )
    resolution_keys = [
        key for key in s3.objects if key.startswith("metadata/resolutions/")
    ]
    assert len(resolution_keys) == 1
    resolution_key = resolution_keys[0]
    puts_after_first = len(s3.put_calls)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=HORIZON_CLOSED + timedelta(minutes=15),
    )
    assert s3.objects[gap_key] == original_gap
    assert len(s3.versions[resolution_key]) == 1
    retry_resolution_puts = [
        call
        for call in s3.put_calls[puts_after_first:]
        if call["Key"] == resolution_key
    ]
    assert all(call["IfNoneMatch"] == "*" for call in retry_resolution_puts)
    assert first["collection_epoch_id"]


def test_conflicting_resolution_fails_closed(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    gap = json.loads(
        next(body for key, body in s3.objects.items() if "missing-probe|facts|" in key)
    )
    resolution_dedup = missing_probe_resolution_dedup_id(
        first["collection_epoch_id"], gap["dedup_id"]
    )
    conflict_key = (
        "metadata/resolutions/year=2026/month=07/day=18/"
        f"{resolution_dedup}.json"
    )
    s3.objects[conflict_key] = b'{"record_type":"COLLECTION_GAP_RESOLUTION","gap_dedup_id":"other"}'
    s3.versions[conflict_key] = [s3.objects[conflict_key]]
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=HORIZON_CLOSED,
    )
    assert len(s3.versions[conflict_key]) == 1
    stored = json.loads(s3.objects[conflict_key])
    assert stored["gap_dedup_id"] == "other"


METRIC_TIME = datetime(2026, 7, 18, 12, 15, tzinfo=timezone.utc)


class Domain3BFailingS3(FakeS3):
    def __init__(self) -> None:
        super().__init__()
        self.fail_domain3b = True

    def put_object(self, **kwargs):
        if self.fail_domain3b and "domain3b|" in kwargs.get("Key", ""):
            raise RuntimeError("s3 down")
        return super().put_object(**kwargs)


def _cw_metric(query_id: str, timestamps, values) -> dict[str, Any]:
    return {"Id": query_id, "Timestamps": list(timestamps), "Values": list(values)}


def _stream_metrics(
    stream: str,
    *,
    freshness=None,
    incoming=None,
    success=None,
    timestamp=METRIC_TIME,
) -> list[dict[str, Any]]:
    results = []
    if freshness is not None:
        results.append(_cw_metric(f"{stream}_freshness", [timestamp], [freshness]))
    if incoming is not None:
        results.append(_cw_metric(f"{stream}_incoming", [timestamp], [incoming]))
    if success is not None:
        results.append(_cw_metric(f"{stream}_success", [timestamp], [success]))
    return results


def _domain_3b_payloads(s3: FakeS3) -> list[dict[str, Any]]:
    gaps = []
    for key, body in s3.objects.items():
        if not key.startswith("metadata/gaps/") or "domain3b|" not in key:
            continue
        gaps.append(json.loads(body.decode("utf-8")))
    return gaps


def test_data_freshness_over_threshold_writes_current_epoch_domain_3b(
    coverage_control,
):
    s3 = FakeS3()
    cloudwatch = FakeCloudWatch(_stream_metrics("facts", freshness=1801.0))
    now = datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc)
    result = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=now,
    )
    gaps = _domain_3b_payloads(s3)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap["collection_epoch_id"] == result["collection_epoch_id"]
    assert gap["gap_domain"] == "DOMAIN_3B"
    assert gap["uncertainty_class"] == "POTENTIAL_GAP"
    assert gap["reason"] == "S3_DESTINATION_FAILURE"
    assert gap["recovery_state"] == "OPEN"
    assert tuple(gap["affected_datasets"]) == (
        "encounter",
        "risk",
        "hazard_version",
    )
    start = datetime.fromisoformat(gap["interval_start_utc"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(gap["interval_end_utc"].replace("Z", "+00:00"))
    oldest = METRIC_TIME - timedelta(seconds=1801 + 60 + 86400)
    assert start <= oldest
    assert end >= METRIC_TIME
    assert start.minute % 15 == 0
    assert "unbound" not in gap["dedup_id"]
    assert gap["dedup_id"].startswith(f"domain3b|{result['collection_epoch_id']}|facts|")
    assert cloudwatch.calls
    lookback = cloudwatch.calls[0]
    assert lookback["EndTime"] == now
    assert (lookback["EndTime"] - lookback["StartTime"]).total_seconds() == 173760
    metric_names = {
        query["MetricStat"]["Metric"]["MetricName"]
        for query in lookback["MetricDataQueries"]
    }
    assert metric_names == {
        "DeliveryToS3.DataFreshness",
        "DeliveryToS3.Success",
        "IncomingRecords",
    }
    assert all(call["IfNoneMatch"] == "*" for call in s3.put_calls if "domain3b|" in call["Key"])


def test_geometry_firehose_domain_3b_affects_hazard_geometry_only(coverage_control):
    s3 = FakeS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(
            _stream_metrics("geometry", freshness=1801.0)
        ),
        now=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
    )
    gaps = _domain_3b_payloads(s3)
    assert len(gaps) == 1
    assert gaps[0]["affected_datasets"] == ["hazard_geometry"]
    assert "|geometry|" in gaps[0]["dedup_id"]


def test_repeated_domain_3b_observation_is_create_once(coverage_control):
    s3 = FakeS3()
    cloudwatch = FakeCloudWatch(_stream_metrics("facts", freshness=1801.0))
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
    )
    gap_key = next(key for key in s3.objects if "domain3b|" in key)
    original = s3.objects[gap_key]
    puts_after_first = len(s3.put_calls)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=datetime(2026, 7, 18, 12, 31, tzinfo=timezone.utc),
    )
    assert s3.objects[gap_key] == original
    assert len(s3.versions[gap_key]) == 1
    retry_puts = [
        call for call in s3.put_calls[puts_after_first:] if call["Key"] == gap_key
    ]
    assert retry_puts
    assert all(call["IfNoneMatch"] == "*" for call in retry_puts)
    assert len(_domain_3b_payloads(s3)) == 1
    assert _domain_3b_payloads(s3)[0]["collection_epoch_id"] == first["collection_epoch_id"]


def test_domain_3b_does_not_use_a_foreign_epoch(coverage_control):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", freshness=1801.0)),
        now=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
    )
    gap = _domain_3b_payloads(s3)[0]
    assert gap["collection_epoch_id"] == first["collection_epoch_id"]
    assert gap["collection_epoch_id"] != "foreign-epoch"
    assert "foreign-epoch" not in gap["dedup_id"]


def test_s3_domain_3b_write_failure_prevents_corresponding_coverage(coverage_control):
    s3 = Domain3BFailingS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    _persist_pending_probe(s3)
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", freshness=1801.0)),
        now=MISSING_DUE,
    )
    assert _domain_3b_payloads(s3) == []
    assert [key for key in s3.objects if key.startswith("metadata/coverage/")] == []


def test_retrospective_collect_materializes_domain_3b_after_s3_returns(
    coverage_control,
):
    s3 = Domain3BFailingS3()
    cloudwatch = FakeCloudWatch(_stream_metrics("facts", freshness=1801.0))
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=FIRST_COLLECT,
    )
    assert _domain_3b_payloads(s3) == []
    s3.fail_domain3b = False
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=FIRST_COLLECT + timedelta(minutes=15),
    )
    gaps = _domain_3b_payloads(s3)
    assert len(gaps) == 1
    assert gaps[0]["collection_epoch_id"] == first["collection_epoch_id"]
    assert gaps[0]["gap_domain"] == "DOMAIN_3B"


def test_absent_incoming_records_do_not_create_live_domain_3b(coverage_control):
    s3 = FakeS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(
            _stream_metrics("facts", incoming=0.0) + _stream_metrics("geometry", incoming=0.0)
        ),
        now=datetime(2026, 7, 18, 12, 16, tzinfo=timezone.utc),
    )
    assert _domain_3b_payloads(s3) == []


def test_buffering_period_without_success_does_not_create_live_domain_3b(
    coverage_control,
):
    s3 = FakeS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", incoming=3.0)),
        now=METRIC_TIME + timedelta(seconds=300),
    )
    assert _domain_3b_payloads(s3) == []
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", incoming=3.0)),
        now=METRIC_TIME + timedelta(seconds=1799),
    )
    assert _domain_3b_payloads(s3) == []


def test_success_before_threshold_does_not_create_fallback_domain_3b(coverage_control):
    s3 = FakeS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(
            _stream_metrics("facts", incoming=3.0)
            + _stream_metrics(
                "facts",
                success=1.0,
                timestamp=METRIC_TIME + timedelta(seconds=900),
            )
        ),
        now=METRIC_TIME + timedelta(seconds=1800),
    )
    assert _domain_3b_payloads(s3) == []


def test_healthy_freshness_clears_missing_freshness_fallback(coverage_control):
    s3 = FakeS3()
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(
            _stream_metrics("facts", incoming=3.0)
            + _stream_metrics(
                "facts",
                freshness=900.0,
                timestamp=METRIC_TIME + timedelta(seconds=600),
            )
        ),
        now=METRIC_TIME + timedelta(seconds=1800),
    )
    assert _domain_3b_payloads(s3) == []


def test_unresolved_incoming_through_threshold_creates_live_domain_3b(
    coverage_control,
):
    s3 = FakeS3()
    result = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", incoming=3.0)),
        now=METRIC_TIME + timedelta(seconds=1800),
    )
    gaps = _domain_3b_payloads(s3)
    assert len(gaps) == 1
    assert gaps[0]["collection_epoch_id"] == result["collection_epoch_id"]
    assert gaps[0]["gap_domain"] == "DOMAIN_3B"
    assert gaps[0]["reason"] == "S3_DESTINATION_FAILURE"
    assert gaps[0]["uncertainty_class"] == "POTENTIAL_GAP"
    start = datetime.fromisoformat(gaps[0]["interval_start_utc"].replace("Z", "+00:00"))
    assert start <= METRIC_TIME - timedelta(seconds=60 + 86400)


def test_retrospective_collect_materializes_unresolved_missing_freshness(
    coverage_control,
):
    s3 = Domain3BFailingS3()
    cloudwatch = FakeCloudWatch(_stream_metrics("facts", incoming=3.0))
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=METRIC_TIME + timedelta(seconds=1800),
    )
    assert _domain_3b_payloads(s3) == []
    s3.fail_domain3b = False
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=cloudwatch,
        now=METRIC_TIME + timedelta(seconds=1800, minutes=15),
    )
    gaps = _domain_3b_payloads(s3)
    assert len(gaps) == 1
    assert gaps[0]["collection_epoch_id"] == first["collection_epoch_id"]
    assert gaps[0]["gap_domain"] == "DOMAIN_3B"


def test_late_probe_cannot_resolve_missing_probe_while_live_domain_3b_open(
    coverage_control,
):
    s3 = FakeS3()
    first = coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=FIRST_COLLECT,
    )
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        now=MISSING_DUE,
    )
    probe = _facts_probe_payload(s3)
    _put_control_jsonl(s3, probe, "late-facts.json.gz")
    coverage_control.collect(
        s3_client=s3,
        events_client=FakeEvents(),
        firehose_client=FakeFirehose(),
        cloudwatch_client=FakeCloudWatch(_stream_metrics("facts", freshness=1801.0)),
        now=HORIZON_CLOSED,
    )
    assert _domain_3b_payloads(s3)
    assert not any(key.startswith("metadata/resolutions/") for key in s3.objects)
    gap = collection_gap_record_from_payload(_domain_3b_payloads(s3)[0])
    result = evaluate_collection_window(
        datasets=("encounter",),
        start_utc="2026-07-18T12:00:00Z",
        end_utc="2026-07-18T12:15:00Z",
        as_of_utc="2026-07-20T12:32:00Z",
        activations=[
            CollectionActivationRecord(
                control_schema_version=CONTROL_SCHEMA_VERSION,
                record_type=ControlRecordType.COLLECTION_ACTIVATION,
                collection_epoch_id=first["collection_epoch_id"],
                enabled_at_utc="2026-07-18T12:00:00Z",
                created_at_utc="2026-07-18T12:00:00Z",
                dedup_id=f"activation|{first['collection_epoch_id']}|2026-07-18T12:00:00Z",
            )
        ],
        coverage_intervals=[],
        gap_records=[gap],
        collection_epoch_id=first["collection_epoch_id"],
    )
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert not any(
        json.loads(body).get("gap_dedup_id", "").startswith("domain3b|")
        for key, body in s3.objects.items()
        if key.startswith("metadata/resolutions/")
    )
