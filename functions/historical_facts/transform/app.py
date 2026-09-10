"""Firehose transform: EventBridge/geometry payloads -> canonical historical facts.

This function is deterministic. It does not read DynamoDB, S3, or the wall
clock for fact or partition semantics. Firehose acceptance of a transformed
record is ACCEPTED_FOR_BOUNDED_DELIVERY_RETRY, not DURABLY_PERSISTED.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from wilvor_historical.contracts import (
    Dataset,
    FactKind,
    HazardGeometryFact,
    HistoricalFactError,
)
from wilvor_historical.coverage_contracts import (
    COLLECTION_CONTROL_DATASET,
    CollectionProbeRecord,
    ControlRecordType,
    CoverageStream,
    HistoricalCoverageError,
)
from wilvor_historical.from_events import HistoricalMappingError, fact_from_event


RESULT_OK = "Ok"
RESULT_PROCESSING_FAILED = "ProcessingFailed"


def _compact_json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def _processing_failed(record_id: str) -> dict[str, Any]:
    return {
        "recordId": record_id,
        "result": RESULT_PROCESSING_FAILED,
    }


def _accepted(record_id: str, fact: Any) -> dict[str, Any]:
    dataset = fact.dataset.value if hasattr(fact.dataset, "value") else str(fact.dataset)
    line = _compact_json_line(fact.to_dict())
    return {
        "recordId": record_id,
        "result": RESULT_OK,
        "data": base64.b64encode(line).decode("ascii"),
        "metadata": {
            "partitionKeys": {
                "dataset": dataset,
                "year": fact.event_year,
                "month": fact.event_month,
                "day": fact.event_day,
            }
        },
    }


def _rehydrate_geometry_fact(payload: dict[str, Any]) -> HazardGeometryFact:
    fields = HazardGeometryFact.__dataclass_fields__
    missing = [name for name in fields if name not in payload]
    if missing:
        raise HistoricalFactError(f"missing geometry fact fields: {missing}")
    return HazardGeometryFact(
        dataset=Dataset(payload["dataset"]),
        fact_schema_version=payload["fact_schema_version"],
        fact_kind=FactKind(payload["fact_kind"]),
        record_id=payload["record_id"],
        dedup_id=payload["dedup_id"],
        event_time_utc=payload["event_time_utc"],
        event_time_epoch=payload["event_time_epoch"],
        event_year=payload["event_year"],
        event_month=payload["event_month"],
        event_day=payload["event_day"],
        source_system=payload["source_system"],
        producer_source=payload["producer_source"],
        producer_detail_type=payload["producer_detail_type"],
        producer_schema_version=payload["producer_schema_version"],
        correlation_id=payload["correlation_id"],
        hazard_id=payload["hazard_id"],
        source_version=payload["source_version"],
        hazard_version_key=payload["hazard_version_key"],
        materialization_id=payload["materialization_id"],
        geometry_hash=payload["geometry_hash"],
        geometry_type=payload["geometry_type"],
        geometry_point_count=payload["geometry_point_count"],
        materialized_at_utc=payload["materialized_at_utc"],
        coordinates_axis=payload["coordinates_axis"],
        geometry=payload["geometry"],
    )


def _control_detail(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("source") == "wilvor.historical.control":
        detail = payload.get("detail")
        if isinstance(detail, str):
            detail = json.loads(detail)
        if not isinstance(detail, dict):
            raise HistoricalCoverageError("control detail is not an object")
        return detail
    return payload


def _rehydrate_probe(payload: dict[str, Any]) -> CollectionProbeRecord:
    fields = CollectionProbeRecord.__dataclass_fields__
    missing = [name for name in fields if name not in payload]
    if missing:
        raise HistoricalCoverageError(f"missing probe fields: {missing}")
    return CollectionProbeRecord(
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


def fact_from_firehose_payload(payload: Any):
    if not isinstance(payload, dict):
        raise HistoricalMappingError("record payload is not an object")

    if (
        payload.get("source") == "wilvor.historical.control"
        or payload.get("record_type") == ControlRecordType.COLLECTION_PROBE.value
        or payload.get("dataset") == COLLECTION_CONTROL_DATASET
    ):
        return _rehydrate_probe(_control_detail(payload))

    if payload.get("dataset") == Dataset.HAZARD_GEOMETRY.value:
        return _rehydrate_geometry_fact(payload)

    return fact_from_event(
        payload.get("source"),
        payload.get("detail-type"),
        payload.get("detail"),
    )


def _process_record(record: dict[str, Any]) -> dict[str, Any]:
    record_id = record.get("recordId")
    if not record_id:
        return _processing_failed("")

    try:
        raw = base64.b64decode(record.get("data") or "", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        fact = fact_from_firehose_payload(payload)
        return _accepted(record_id, fact)
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        HistoricalFactError,
        HistoricalCoverageError,
        HistoricalMappingError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return _processing_failed(record_id)


def lambda_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    records_out: list[dict[str, Any]] = []
    for record in event.get("records") or []:
        if not isinstance(record, dict):
            records_out.append(_processing_failed(""))
            continue
        records_out.append(_process_record(record))
    return {"records": records_out}
