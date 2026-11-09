"""Offline fake-S3 tests for the Phase 2B.3 coverage store and gate."""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from wilvor_historical.contracts import Dataset
from wilvor_historical.coverage import (
    DEFAULT_COLLECTION_EVALUATION_POLICY,
    ENCOUNTER_HORIZON_SECONDS,
    RISK_HORIZON_SECONDS,
    HAZARD_VERSION_HORIZON_SECONDS,
    evaluate_collection_window,
    required_streams_for,
)
from wilvor_historical.coverage_contracts import (
    CONTROL_SCHEMA_VERSION,
    CollectionActivationRecord,
    CollectionDeactivationRecord,
    CollectionEpochRecord,
    CollectionGapRecord,
    CollectionGapResolutionRecord,
    ControlRecordType,
    CoverageIntervalRecord,
    CoverageStream,
    Evaluability,
    EvaluabilityResult,
    GapDomain,
    RecoveryState,
    STAGING_STATE,
    UncertaintyClass,
    UnboundCollectionIncident,
)
from wilvor_historical.query_contracts import (
    COVERAGE_REASON_EPOCH_AMBIGUOUS,
    COVERAGE_STORE_METADATA_PREFIXES,
)
from wilvor_historical.time import canonicalize_utc_z
from wilvor_historical_query.coverage_gate import (
    REASON_EVALUATOR_INCONSISTENT,
    REASON_PAGINATION_MALFORMED,
    REASON_STORE_UNAVAILABLE,
    CoverageGate,
    CoverageGateResult,
)
from wilvor_historical_query.coverage_store import (
    CoverageStore,
    CoverageStoreConfig,
    CoverageStoreError,
    CoverageStoreErrorCode,
)
from wilvor_historical_query import executor


UTC = timezone.utc
BUCKET = "wilvor-dev-historical-facts-123456789012"
EPOCH_A = "epoch-a"
EPOCH_B = "epoch-b"
WINDOW_START = "2026-09-11T10:00:00Z"
WINDOW_END = "2026-09-11T11:00:00Z"
POLICY = DEFAULT_COLLECTION_EVALUATION_POLICY


def _z(value: datetime) -> str:
    return canonicalize_utc_z(value)


def _as_of_closed(end: str, datasets: tuple[str, ...] = ("encounter",)) -> str:
    return _z(
        datetime.fromisoformat(end.replace("Z", "+00:00"))
        + timedelta(seconds=POLICY.required_horizon_seconds(datasets) + 1)
    )


def _as_of_open(end: str, datasets: tuple[str, ...] = ("encounter",)) -> str:
    return _z(
        datetime.fromisoformat(end.replace("Z", "+00:00"))
        + timedelta(seconds=POLICY.required_horizon_seconds(datasets) - 1)
    )


class FakeBody:
    def __init__(self, data: bytes, error: Exception | None = None) -> None:
        self._data = data
        self._error = error

    def read(self) -> bytes:
        if self._error is not None:
            raise self._error
        return self._data


class FakeS3:
    def __init__(
        self,
        objects: dict[str, bytes] | None = None,
        *,
        page_size: int | None = None,
        list_error: Exception | None = None,
        get_error: Exception | None = None,
        reverse_list: bool = False,
        truncated_without_token: bool = False,
        prefix_pages: dict[str, dict[str | None, dict[str, Any]]] | None = None,
    ) -> None:
        self.objects = dict(objects or {})
        self.page_size = page_size
        self.list_error = list_error
        self.get_error = get_error
        self.reverse_list = reverse_list
        self.truncated_without_token = truncated_without_token
        self.prefix_pages = prefix_pages or {}
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.objects[key] = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        if self.list_error is not None:
            raise self.list_error
        prefix = kwargs["Prefix"]
        if prefix in self.prefix_pages:
            token = kwargs.get("ContinuationToken")
            pages = self.prefix_pages[prefix]
            if token not in pages:
                raise AssertionError(f"unexpected ContinuationToken {token!r} for {prefix}")
            return dict(pages[token])
        keys = [key for key in self.objects if key.startswith(prefix)]
        keys.sort()
        if self.reverse_list:
            keys.reverse()
        if self.truncated_without_token:
            return {
                "Contents": [{"Key": key} for key in keys],
                "IsTruncated": True,
            }
        token = kwargs.get("ContinuationToken")
        start = int(token) if token is not None else 0
        size = self.page_size if self.page_size is not None else max(len(keys), 1)
        page = keys[start : start + size]
        next_index = start + size
        truncated = next_index < len(keys)
        response: dict[str, Any] = {
            "Contents": [{"Key": key} for key in page],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = str(next_index)
        return response

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        key = kwargs["Key"]
        data = self.objects[key]
        if isinstance(data, FakeBody):
            return {"Body": data}
        return {"Body": FakeBody(data)}


def _config() -> CoverageStoreConfig:
    return CoverageStoreConfig(bucket_name=BUCKET)


def _store(client: FakeS3) -> CoverageStore:
    return CoverageStore(s3_client=client, config=_config())


def _gate(client: FakeS3, **kwargs: Any) -> CoverageGate:
    return CoverageGate(store=_store(client), **kwargs)


def _epoch(epoch_id: str, *, created: str = "2026-09-01T00:00:00Z") -> CollectionEpochRecord:
    return CollectionEpochRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_EPOCH,
        collection_epoch_id=epoch_id,
        bucket_name=BUCKET,
        created_at_utc=created,
        dedup_id=f"epoch|{epoch_id}",
    )


def _activation(epoch_id: str, enabled_at: str) -> CollectionActivationRecord:
    return CollectionActivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_ACTIVATION,
        collection_epoch_id=epoch_id,
        enabled_at_utc=enabled_at,
        created_at_utc=enabled_at,
        dedup_id=f"activation|{epoch_id}|{enabled_at}",
    )


def _deactivation(epoch_id: str, deactivated_at: str) -> CollectionDeactivationRecord:
    return CollectionDeactivationRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_DEACTIVATION,
        collection_epoch_id=epoch_id,
        deactivated_at_utc=deactivated_at,
        created_at_utc=deactivated_at,
        dedup_id=f"deactivation|{epoch_id}|{deactivated_at}",
    )


def _coverage(
    epoch_id: str,
    start: str,
    end: str,
    stream: CoverageStream = CoverageStream.FACTS,
) -> CoverageIntervalRecord:
    return CoverageIntervalRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COVERAGE_INTERVAL,
        collection_epoch_id=epoch_id,
        stream=stream,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=end,
        dedup_id=f"coverage|{epoch_id}|{stream.value}|{start}",
    )


def _gap(
    epoch_id: str,
    datasets: tuple[str, ...],
    start: str,
    end: str,
    *,
    dedup_id: str = "gap-1",
) -> CollectionGapRecord:
    return CollectionGapRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP,
        collection_epoch_id=epoch_id,
        affected_datasets=datasets,
        gap_domain=GapDomain.DOMAIN_1,
        reason="PRODUCER_PUT_EVENTS_FAILURE",
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        recovery_state=RecoveryState.OPEN,
        detected_at_utc=start,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=start,
        dedup_id=dedup_id,
    )


def _resolution(epoch_id: str, gap_dedup_id: str) -> CollectionGapResolutionRecord:
    return CollectionGapResolutionRecord(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.COLLECTION_GAP_RESOLUTION,
        collection_epoch_id=epoch_id,
        gap_dedup_id=gap_dedup_id,
        recovery_state=RecoveryState.RESOLVED_PROVEN,
        created_at_utc=WINDOW_END,
        dedup_id=f"resolution|{epoch_id}|{gap_dedup_id}",
    )


def _incident(
    datasets: tuple[str, ...] = ("encounter",),
    start: str = WINDOW_START,
    end: str = "2026-09-11T10:15:00Z",
    dedup_id: str = "incident-1",
) -> UnboundCollectionIncident:
    return UnboundCollectionIncident(
        control_schema_version=CONTROL_SCHEMA_VERSION,
        record_type=ControlRecordType.UNBOUND_COLLECTION_INCIDENT,
        staging_state=STAGING_STATE,
        affected_datasets=datasets,
        gap_domain=GapDomain.DOMAIN_1,
        reason="PRODUCER_PUT_EVENTS_FAILURE",
        uncertainty_class=UncertaintyClass.KNOWN_MISSING,
        detected_at_utc=start,
        interval_start_utc=start,
        interval_end_utc=end,
        created_at_utc=start,
        dedup_id=dedup_id,
    )


def _put(client: FakeS3, key: str, record: Any) -> None:
    client.put(key, record.to_dict())


def _seed_evaluable(
    client: FakeS3,
    *,
    epoch_id: str = EPOCH_A,
    start: str = WINDOW_START,
    end: str = WINDOW_END,
    enabled_at: str = "2026-09-11T00:00:00Z",
) -> None:
    _put(client, f"metadata/epoch/{epoch_id}.json", _epoch(epoch_id))
    _put(
        client,
        f"metadata/activation/year=2026/month=09/day=11/{epoch_id}.json",
        _activation(epoch_id, enabled_at),
    )
    _put(
        client,
        f"metadata/coverage/stream=facts/year=2026/month=09/day=11/{epoch_id}.json",
        _coverage(epoch_id, start, end),
    )


def _evaluate(client: FakeS3, **kwargs: Any) -> CoverageGateResult:
    values = {
        "datasets": (Dataset.ENCOUNTER,),
        "start_utc": WINDOW_START,
        "end_utc": WINDOW_END,
        "as_of_utc": _as_of_closed(WINDOW_END),
    }
    values.update(kwargs)
    return _gate(client).evaluate(**values)


def test_v1_dataset_requirements_are_phase_2a1_constants():
    assert POLICY.horizon_for("encounter") == ENCOUNTER_HORIZON_SECONDS
    assert POLICY.horizon_for("risk") == RISK_HORIZON_SECONDS
    assert POLICY.horizon_for("hazard_version") == HAZARD_VERSION_HORIZON_SECONDS
    assert required_streams_for(("encounter",)) == (CoverageStream.FACTS,)
    assert required_streams_for(("risk",)) == (CoverageStream.FACTS,)
    assert required_streams_for(("hazard_version",)) == (CoverageStream.FACTS,)
    assert required_streams_for(("hazard_geometry",)) == (CoverageStream.GEOMETRY,)


def test_config_rejects_invalid_bucket_and_prefix_subset():
    with pytest.raises(CoverageStoreError) as captured:
        CoverageStoreConfig(bucket_name="")
    assert captured.value.code is CoverageStoreErrorCode.INVALID_REQUEST
    with pytest.raises(CoverageStoreError) as captured:
        CoverageStoreConfig(
            bucket_name=BUCKET,
            metadata_prefixes=("metadata/gaps/",),
        )
    assert captured.value.code is CoverageStoreErrorCode.INVALID_REQUEST


def test_one_epoch_evaluable_allows_query():
    client = FakeS3()
    _seed_evaluable(client)
    result = _evaluate(client)
    assert result.allowed_to_query is True
    assert result.evaluability is Evaluability.EVALUABLE
    assert result.coverage.collection_epoch_ids == (EPOCH_A,)
    assert result.slice_evaluations[0].reason == "window_evaluable"
    assert {call["Prefix"] for call in client.list_calls} == set(
        COVERAGE_STORE_METADATA_PREFIXES
    )
    assert all("year=" not in call["Prefix"] for call in client.list_calls)
    assert all(call["Bucket"] == BUCKET for call in client.list_calls)


def test_one_epoch_gap_blocks():
    client = FakeS3()
    _seed_evaluable(client)
    _put(
        client,
        "metadata/gaps/year=2026/month=09/day=11/gap.json",
        _gap(EPOCH_A, ("encounter",), WINDOW_START, WINDOW_END),
    )
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert result.reason == "unresolved_gap_overlaps_window"


def test_one_epoch_not_yet_evaluable_blocks():
    client = FakeS3()
    _seed_evaluable(client)
    result = _evaluate(client, as_of_utc=_as_of_open(WINDOW_END))
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.NOT_YET_EVALUABLE
    assert result.reason == "late_arrival_horizon_not_closed"


def test_request_partly_before_activation_is_not_active():
    client = FakeS3()
    _seed_evaluable(client, enabled_at="2026-09-11T10:15:00Z")
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.NOT_ACTIVE
    assert result.slice_evaluations == ()


def test_intentional_inactive_gap_between_epochs_is_not_active():
    client = FakeS3()
    _put(client, "metadata/epoch/a.json", _epoch(EPOCH_A))
    _put(client, "metadata/epoch/b.json", _epoch(EPOCH_B, created="2026-09-11T10:45:00Z"))
    _put(
        client,
        "metadata/activation/year=2026/month=09/day=11/a.json",
        _activation(EPOCH_A, "2026-09-11T10:00:00Z"),
    )
    _put(
        client,
        "metadata/deactivation/year=2026/month=09/day=11/a.json",
        _deactivation(EPOCH_A, "2026-09-11T10:30:00Z"),
    )
    _put(
        client,
        "metadata/activation/year=2026/month=09/day=11/b.json",
        _activation(EPOCH_B, "2026-09-11T10:45:00Z"),
    )
    _put(
        client,
        "metadata/coverage/a.json",
        _coverage(EPOCH_A, "2026-09-11T10:00:00Z", "2026-09-11T10:30:00Z"),
    )
    _put(
        client,
        "metadata/coverage/b.json",
        _coverage(EPOCH_B, "2026-09-11T10:45:00Z", WINDOW_END),
    )
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.NOT_ACTIVE


def test_sequential_epochs_evaluate_each_owned_slice():
    client = FakeS3()
    _put(client, "metadata/epoch/a.json", _epoch(EPOCH_A))
    _put(client, "metadata/epoch/b.json", _epoch(EPOCH_B, created="2026-09-11T11:00:00Z"))
    _put(
        client,
        "metadata/activation/a.json",
        _activation(EPOCH_A, "2026-09-11T10:00:00Z"),
    )
    _put(
        client,
        "metadata/deactivation/a.json",
        _deactivation(EPOCH_A, "2026-09-11T11:00:00Z"),
    )
    _put(
        client,
        "metadata/activation/b.json",
        _activation(EPOCH_B, "2026-09-11T11:00:00Z"),
    )
    _put(
        client,
        "metadata/coverage/a.json",
        _coverage(EPOCH_A, "2026-09-11T10:30:00Z", "2026-09-11T11:00:00Z"),
    )
    _put(
        client,
        "metadata/coverage/b.json",
        _coverage(EPOCH_B, "2026-09-11T11:00:00Z", "2026-09-11T11:30:00Z"),
    )
    result = _evaluate(
        client,
        start_utc="2026-09-11T10:30:00Z",
        end_utc="2026-09-11T11:30:00Z",
        as_of_utc=_as_of_closed("2026-09-11T11:30:00Z"),
    )
    assert result.allowed_to_query is True
    assert result.evaluability is Evaluability.EVALUABLE
    assert result.coverage.collection_epoch_ids == (EPOCH_A, EPOCH_B)
    assert [
        (item.collection_epoch_id, item.start_utc, item.end_utc)
        for item in result.slice_evaluations
    ] == [
        (EPOCH_A, "2026-09-11T10:30:00Z", "2026-09-11T11:00:00Z"),
        (EPOCH_B, "2026-09-11T11:00:00Z", "2026-09-11T11:30:00Z"),
    ]


def test_overlapping_epochs_are_epoch_ambiguous():
    client = FakeS3()
    _put(client, "metadata/epoch/a.json", _epoch(EPOCH_A))
    _put(client, "metadata/epoch/b.json", _epoch(EPOCH_B))
    _put(client, "metadata/activation/a.json", _activation(EPOCH_A, WINDOW_START))
    _put(client, "metadata/activation/b.json", _activation(EPOCH_B, "2026-09-11T10:30:00Z"))
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.evaluability is None
    assert result.reason == COVERAGE_REASON_EPOCH_AMBIGUOUS
    assert result.slice_evaluations == ()


def test_zero_epochs_is_store_unavailable():
    result = _evaluate(FakeS3())
    assert result.allowed_to_query is False
    assert result.evaluability is None
    assert result.reason == REASON_STORE_UNAVAILABLE


def test_evaluator_not_active_on_owned_slice_is_inconsistent():
    client = FakeS3()
    _seed_evaluable(client)

    def _not_active(**kwargs: Any) -> EvaluabilityResult:
        return EvaluabilityResult(
            evaluability=Evaluability.NOT_ACTIVE,
            required_horizon_seconds=ENCOUNTER_HORIZON_SECONDS,
            required_streams=("facts",),
            reason="window_not_in_active_collection_period",
        )

    result = CoverageGate(store=_store(client), evaluator=_not_active).evaluate(
        datasets=("encounter",),
        start_utc=WINDOW_START,
        end_utc=WINDOW_END,
        as_of_utc=_as_of_closed(WINDOW_END),
    )
    assert result.allowed_to_query is False
    assert result.reason == REASON_EVALUATOR_INCONSISTENT
    assert result.evaluability is None


def test_late_written_gap_on_later_date_prefix_is_loaded():
    client = FakeS3()
    _seed_evaluable(client)
    _put(
        client,
        "metadata/gaps/year=2026/month=09/day=13/late.json",
        _gap(EPOCH_A, ("encounter",), WINDOW_START, WINDOW_END, dedup_id="late-gap"),
    )
    result = _evaluate(client)
    assert any(call["Prefix"] == "metadata/gaps/" for call in client.list_calls)
    assert all(not call["Prefix"].startswith("metadata/gaps/year=") for call in client.list_calls)
    assert "metadata/gaps/year=2026/month=09/day=13/late.json" in {
        call["Key"] for call in client.get_calls
    }
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_unbound_incident_is_supplied_to_every_owned_slice():
    client = FakeS3()
    _put(client, "metadata/epoch/a.json", _epoch(EPOCH_A))
    _put(client, "metadata/epoch/b.json", _epoch(EPOCH_B, created="2026-09-11T11:00:00Z"))
    _put(client, "metadata/activation/a.json", _activation(EPOCH_A, "2026-09-11T10:00:00Z"))
    _put(client, "metadata/deactivation/a.json", _deactivation(EPOCH_A, "2026-09-11T11:00:00Z"))
    _put(client, "metadata/activation/b.json", _activation(EPOCH_B, "2026-09-11T11:00:00Z"))
    _put(
        client,
        "metadata/coverage/a.json",
        _coverage(EPOCH_A, "2026-09-11T10:30:00Z", "2026-09-11T11:00:00Z"),
    )
    _put(
        client,
        "metadata/coverage/b.json",
        _coverage(EPOCH_B, "2026-09-11T11:00:00Z", "2026-09-11T11:30:00Z"),
    )
    incident = _incident(
        start="2026-09-11T10:45:00Z",
        end="2026-09-11T11:15:00Z",
    )
    assert not hasattr(incident, "collection_epoch_id")
    _put(client, "metadata/incidents/year=2026/month=09/day=13/incident.json", incident)
    seen: list[int] = []

    def _counting_evaluator(**kwargs: Any) -> EvaluabilityResult:
        seen.append(len(kwargs["unbound_incidents"]))
        return evaluate_collection_window(**kwargs)

    result = CoverageGate(store=_store(client), evaluator=_counting_evaluator).evaluate(
        datasets=("encounter",),
        start_utc="2026-09-11T10:30:00Z",
        end_utc="2026-09-11T11:30:00Z",
        as_of_utc=_as_of_closed("2026-09-11T11:30:00Z"),
    )
    assert seen == [1, 1]
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN
    assert result.reason == "unbound_incident_overlaps_window"


def test_foreign_epoch_gap_does_not_change_same_epoch_filtering():
    client = FakeS3()
    _seed_evaluable(client)
    _put(client, "metadata/epoch/b.json", _epoch(EPOCH_B))
    _put(
        client,
        "metadata/gaps/year=2026/month=09/day=11/foreign.json",
        _gap(EPOCH_B, ("encounter",), WINDOW_START, WINDOW_END, dedup_id="foreign"),
    )
    result = _evaluate(client)
    assert result.allowed_to_query is True
    assert result.evaluability is Evaluability.EVALUABLE
    assert EPOCH_B not in result.coverage.collection_epoch_ids


def test_multi_dataset_all_evaluable_allows():
    client = FakeS3()
    _seed_evaluable(client)
    result = _evaluate(client, datasets=(Dataset.ENCOUNTER, Dataset.RISK))
    assert result.allowed_to_query is True
    assert {item.dataset for item in result.slice_evaluations} == {"encounter", "risk"}


def test_multi_dataset_risk_gap_blocks_even_if_encounter_is_evaluable():
    client = FakeS3()
    _seed_evaluable(client)
    _put(
        client,
        "metadata/gaps/risk.json",
        _gap(EPOCH_A, ("risk",), WINDOW_START, WINDOW_END, dedup_id="risk-gap"),
    )
    result = _evaluate(client, datasets=("encounter", "risk"))
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_multi_dataset_risk_not_yet_blocks():
    client = FakeS3()
    _seed_evaluable(client)
    policy = POLICY.__class__(
        risk_producer_to_eventbridge_max_delay_seconds=200000,
    )
    as_of = _z(
        datetime.fromisoformat(WINDOW_END.replace("Z", "+00:00"))
        + timedelta(seconds=ENCOUNTER_HORIZON_SECONDS + 1)
    )
    result = CoverageGate(store=_store(client), policy=policy).evaluate(
        datasets=("encounter", "risk"),
        start_utc=WINDOW_START,
        end_utc=WINDOW_END,
        as_of_utc=as_of,
    )
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.NOT_YET_EVALUABLE
    assert {item.dataset: item.evaluability for item in result.slice_evaluations} == {
        "encounter": Evaluability.EVALUABLE,
        "risk": Evaluability.NOT_YET_EVALUABLE,
    }


def test_geometry_gap_does_not_block_encounter_only():
    client = FakeS3()
    _seed_evaluable(client)
    _put(
        client,
        "metadata/gaps/geometry.json",
        _gap(
            EPOCH_A,
            ("hazard_geometry",),
            WINDOW_START,
            WINDOW_END,
            dedup_id="geometry-gap",
        ),
    )
    result = _evaluate(client, datasets=(Dataset.ENCOUNTER,))
    assert result.allowed_to_query is True
    assert result.evaluability is Evaluability.EVALUABLE


def test_listing_is_paginated_and_order_independent():
    forward = FakeS3(page_size=1)
    reverse = FakeS3(page_size=1, reverse_list=True)
    for client in (forward, reverse):
        _seed_evaluable(client)
        _put(client, "metadata/epoch/extra.json", _epoch("epoch-extra"))
    first = _evaluate(forward)
    second = _evaluate(reverse)
    assert first.coverage.to_dict() == second.coverage.to_dict()
    assert first.slice_evaluations == second.slice_evaluations
    assert any(call.get("ContinuationToken") for call in forward.list_calls)


def test_truncated_list_without_token_fails_closed():
    client = FakeS3(truncated_without_token=True)
    _seed_evaluable(client)
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == REASON_PAGINATION_MALFORMED


def test_list_and_get_failures_fail_closed():
    listed = FakeS3(list_error=RuntimeError("list failed"))
    _seed_evaluable(listed)
    listed_result = _evaluate(listed)
    assert listed_result.allowed_to_query is False
    assert listed_result.reason == REASON_STORE_UNAVAILABLE
    gotten = FakeS3(get_error=RuntimeError("get failed"))
    _seed_evaluable(gotten)
    gotten_result = _evaluate(gotten)
    assert gotten_result.allowed_to_query is False
    assert gotten_result.reason == REASON_STORE_UNAVAILABLE


def test_malformed_json_and_utf8_fail_closed():
    bad_json = FakeS3()
    _seed_evaluable(bad_json)
    bad_json.objects["metadata/gaps/year=2026/month=09/day=11/bad.json"] = b"{not-json"
    result = _evaluate(bad_json)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value
    bad_utf8 = FakeS3()
    _seed_evaluable(bad_utf8)
    bad_utf8.objects["metadata/gaps/year=2026/month=09/day=11/bad.json"] = b"\xff\xfe"
    result = _evaluate(bad_utf8)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value


def test_empty_object_and_body_read_failure_fail_closed():
    empty = FakeS3()
    _seed_evaluable(empty)
    empty.objects["metadata/gaps/empty.json"] = b""
    result = _evaluate(empty)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value
    unreadable = FakeS3()
    _seed_evaluable(unreadable)
    unreadable.objects["metadata/gaps/boom.json"] = FakeBody(
        b"{}",
        error=RuntimeError("read failed"),
    )
    result = _evaluate(unreadable)
    assert result.allowed_to_query is False
    assert result.reason == REASON_STORE_UNAVAILABLE


def test_trailing_slash_zero_byte_object_fails_closed():
    client = FakeS3()
    _seed_evaluable(client)
    client.objects["metadata/gaps/"] = b""
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value
    assert all(call["Key"] != "metadata/gaps/" for call in client.get_calls)


def test_trailing_slash_nonzero_object_fails_closed():
    client = FakeS3()
    _seed_evaluable(client)
    client.objects["metadata/gaps/"] = json.dumps(
        _gap(EPOCH_A, ("encounter",), WINDOW_START, WINDOW_END).to_dict()
    ).encode("utf-8")
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value
    assert all(call["Key"] != "metadata/gaps/" for call in client.get_calls)


def test_repeated_pagination_token_fails_closed():
    client = FakeS3(
        prefix_pages={
            "metadata/epoch/": {
                None: {
                    "Contents": [{"Key": "metadata/epoch/a.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "A",
                },
                "A": {
                    "Contents": [{"Key": "metadata/epoch/b.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "A",
                },
            }
        }
    )
    _seed_evaluable(client)
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == REASON_PAGINATION_MALFORMED
    epoch_calls = [call for call in client.list_calls if call["Prefix"] == "metadata/epoch/"]
    assert len(epoch_calls) == 2


def test_cyclic_pagination_tokens_fail_closed():
    client = FakeS3(
        prefix_pages={
            "metadata/epoch/": {
                None: {
                    "Contents": [{"Key": "metadata/epoch/a.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "A",
                },
                "A": {
                    "Contents": [{"Key": "metadata/epoch/b.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "B",
                },
                "B": {
                    "Contents": [{"Key": "metadata/epoch/c.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "A",
                },
            }
        }
    )
    _seed_evaluable(client)
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == REASON_PAGINATION_MALFORMED
    epoch_calls = [call for call in client.list_calls if call["Prefix"] == "metadata/epoch/"]
    assert len(epoch_calls) == 3


def test_duplicate_object_key_across_pages_fails_closed():
    client = FakeS3(
        prefix_pages={
            "metadata/gaps/": {
                None: {
                    "Contents": [{"Key": "metadata/gaps/year=2026/month=09/day=11/gap.json"}],
                    "IsTruncated": True,
                    "NextContinuationToken": "page-2",
                },
                "page-2": {
                    "Contents": [{"Key": "metadata/gaps/year=2026/month=09/day=11/gap.json"}],
                    "IsTruncated": False,
                },
            }
        }
    )
    _seed_evaluable(client)
    _put(
        client,
        "metadata/gaps/year=2026/month=09/day=11/gap.json",
        _gap(EPOCH_A, ("encounter",), WINDOW_START, WINDOW_END),
    )
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == REASON_PAGINATION_MALFORMED
    gap_gets = [
        call for call in client.get_calls if call["Key"] == "metadata/gaps/year=2026/month=09/day=11/gap.json"
    ]
    assert gap_gets == []


def test_similar_records_at_different_keys_are_not_deduplicated():
    client = FakeS3()
    _seed_evaluable(client)
    gap = _gap(EPOCH_A, ("encounter",), WINDOW_START, WINDOW_END, dedup_id="gap-shared")
    _put(client, "metadata/gaps/year=2026/month=09/day=11/one.json", gap)
    _put(client, "metadata/gaps/year=2026/month=09/day=13/two.json", gap)
    snapshot = _store(client).load()
    assert len(snapshot.gaps) == 2
    assert snapshot.object_keys.count("metadata/gaps/year=2026/month=09/day=11/one.json") == 1
    assert snapshot.object_keys.count("metadata/gaps/year=2026/month=09/day=13/two.json") == 1
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.evaluability is Evaluability.GAP_OR_UNCERTAIN


def test_malformed_record_construction_fails_closed():
    client = FakeS3()
    _seed_evaluable(client)
    client.put(
        "metadata/gaps/bad.json",
        {"record_type": "COLLECTION_GAP", "collection_epoch_id": EPOCH_A},
    )
    result = _evaluate(client)
    assert result.allowed_to_query is False
    assert result.reason == CoverageStoreErrorCode.COVERAGE_METADATA_MALFORMED.value


def test_gate_does_not_read_wall_clock_or_call_executor():
    source = inspect.getsource(CoverageGate.evaluate) + inspect.getsource(CoverageStore.load)
    assert "datetime.now" not in source
    assert "datetime.utcnow" not in source
    assert "time.time" not in source
    assert "AthenaExecutor" not in inspect.getsource(CoverageGate)
    assert "execute_fixed" not in inspect.getsource(CoverageGate)
    assert "evaluate_collection_window" not in inspect.getsource(executor)
    gate_module = inspect.getsource(inspect.getmodule(CoverageGate))
    assert "wilvor_historical_query.executor" not in gate_module
    assert "AthenaExecutor" not in inspect.getsource(inspect.getmodule(CoverageStore))


def test_as_of_is_injected_not_derived():
    client = FakeS3()
    _seed_evaluable(client)
    closed = _evaluate(client, as_of_utc=_as_of_closed(WINDOW_END))
    opened = _evaluate(client, as_of_utc=_as_of_open(WINDOW_END))
    assert closed.evaluability is Evaluability.EVALUABLE
    assert opened.evaluability is Evaluability.NOT_YET_EVALUABLE


def test_gate_result_does_not_compute_verified_zero():
    client = FakeS3()
    _seed_evaluable(client)
    result = _evaluate(client)
    assert not hasattr(result, "verified_zero")
    assert "is_verified_zero" not in inspect.getsource(CoverageGate)
