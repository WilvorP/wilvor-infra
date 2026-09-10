"""Firehose transform mapping, partitions, and ProcessingFailed behavior."""

from __future__ import annotations

import ast
import base64
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import pytest

from wilvor_historical.contracts import FactKind
from wilvor_historical.from_events import (
    GEOMETRY_IN_MEMORY_DETAIL_TYPE,
    build_hazard_geometry_fact,
)


TRANSFORM_PATH = (
    Path(__file__).resolve().parents[3]
    / "functions"
    / "historical_facts"
    / "transform"
    / "app.py"
)


@pytest.fixture
def transform(
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    return load_repo_module(
        "historical_facts_transform_app",
        "functions/historical_facts/transform/app.py",
    )


def _encounter_detail(**overrides: Any) -> dict[str, Any]:
    detail = {
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "aircraft_state_version": "state-v1",
        "projection_id": "proj-1",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "hazard_version_key": "hazard-1#v1",
        "encounter_state": "DETECTED",
        "geometry_overlap_status": "INSIDE_NOW",
        "time_overlap_status": "OVERLAP",
        "altitude_overlap_status": "OVERLAP",
        "exact_intersection_confirmed": True,
        "detected_at_epoch": 1_700_000_000,
        "detected_at_utc": "2023-11-14T22:13:20Z",
        "geometry_hash": "geom-1",
        "hazard_type": "CONVECTION",
        "inside_now": True,
        "corridor_intersects": True,
        "schema_version": "wilvor.aircraft_hazard_encounter.v4.0",
    }
    detail.update(overrides)
    return detail


def _risk_detail(**overrides: Any) -> dict[str, Any]:
    detail = {
        "risk_id": "risk#same",
        "encounter_id": "proj-1#hazard-1#v1",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_source_version": "v1",
        "projection_id": "proj-1",
        "risk_score": 70,
        "risk_level": "HIGH",
        "generated_at_epoch": 1_700_000_000,
        "generated_at_utc": "2023-11-14T22:13:20Z",
        "scoring_ruleset_version": "wilvor.risk.ruleset.v2",
        "scoring_config_version": "wilvor.risk.config.dev.v1",
        "hazard_type": "CONVECTION",
        "encounter_state": "DETECTED",
        "schema_version": "wilvor.risk_results.v4.0",
    }
    detail.update(overrides)
    return detail


def _hazard_detail(**overrides: Any) -> dict[str, Any]:
    detail = {
        "hazard_id": "sigmet-aaa",
        "source_version": "v1",
        "hazard_version_key": "sigmet-aaa#v1",
        "materialization_id": "hazard-materialization-1",
        "status": "ACTIVE",
        "geometry_hash": "copied-hash-v1",
        "geometry_type": "POLYGON",
        "geometry_point_count": 5,
        "product_type": "SIGMET",
        "hazard_type": "TURBULENCE",
        "valid_from_utc": "2026-07-18T12:00:00+00:00",
        "valid_to_utc": "2026-07-18T18:00:00+00:00",
        "materialized_at_utc": "2026-07-18T12:30:00+00:00",
        "schema_version": "wilvor.active_hazards.v4.0",
    }
    detail.update(overrides)
    return detail


def _eventbridge_event(
    source: str,
    detail_type: str,
    detail: dict[str, Any],
    *,
    envelope_time: str = "2099-01-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "version": "0",
        "id": "evt-1",
        "source": source,
        "detail-type": detail_type,
        "time": envelope_time,
        "region": "us-west-1",
        "detail": detail,
    }


def _geometry_points() -> list[dict[str, Any]]:
    return [
        {
            "polygon_index": 0,
            "ring_index": 0,
            "sequence_number": index,
            "longitude": lon,
            "latitude": lat,
            "geometry_type": "POLYGON",
        }
        for index, (lon, lat) in enumerate(
            [
                (-75.0, 40.0),
                (-74.0, 40.0),
                (-74.0, 41.0),
                (-75.0, 41.0),
                (-75.0, 40.0),
            ]
        )
    ]


def _encode_record(record_id: str, payload: Any, *, arrival_ms: int = 9_999_999_999_999) -> dict[str, Any]:
    if isinstance(payload, (bytes, bytearray)):
        data = base64.b64encode(payload).decode("ascii")
    else:
        data = base64.b64encode(
            json.dumps(payload).encode("utf-8")
        ).decode("ascii")
    return {
        "recordId": record_id,
        "approximateArrivalTimestamp": arrival_ms,
        "data": data,
    }


def _decode_ok(record: dict[str, Any]) -> dict[str, Any]:
    assert record["result"] == "Ok"
    raw = base64.b64decode(record["data"])
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 1
    return json.loads(raw.decode("utf-8"))


def test_encounter_updated_maps_to_observed_fact(transform):
    event = {
        "records": [
            _encode_record(
                "r1",
                _eventbridge_event(
                    "wilvor.encounter",
                    "encounter.updated",
                    _encounter_detail(),
                ),
            )
        ]
    }
    output = transform.lambda_handler(event, None)
    assert output["records"][0]["recordId"] == "r1"
    fact = _decode_ok(output["records"][0])
    assert fact["fact_kind"] == FactKind.ENCOUNTER_OBSERVED.value
    assert fact["dataset"] == "encounter"
    assert fact["event_year"] == "2023"
    assert fact["event_month"] == "11"
    assert fact["event_day"] == "14"
    assert output["records"][0]["metadata"]["partitionKeys"] == {
        "dataset": "encounter",
        "year": "2023",
        "month": "11",
        "day": "14",
    }


def test_encounter_resolved_maps_to_terminal_fact(transform):
    event = {
        "records": [
            _encode_record(
                "r2",
                _eventbridge_event(
                    "wilvor.encounter",
                    "encounter.resolved",
                    _encounter_detail(
                        encounter_state="RESOLVED",
                        resolved_at_epoch=1_700_000_200,
                        resolved_at_utc="2023-11-14T22:16:40Z",
                    ),
                ),
            )
        ]
    }
    fact = _decode_ok(transform.lambda_handler(event, None)["records"][0])
    assert fact["fact_kind"] == FactKind.ENCOUNTER_TERMINAL.value
    assert fact["resolved_at_utc"] == "2023-11-14T22:16:40Z"


def test_risk_updated_and_resolved_share_identity(transform):
    updated = transform.lambda_handler(
        {
            "records": [
                _encode_record(
                    "u",
                    _eventbridge_event(
                        "wilvor.risk",
                        "risk.updated",
                        _risk_detail(),
                    ),
                )
            ]
        },
        None,
    )
    resolved = transform.lambda_handler(
        {
            "records": [
                _encode_record(
                    "r",
                    _eventbridge_event(
                        "wilvor.risk",
                        "risk.resolved",
                        _risk_detail(encounter_state="SUPERSEDED"),
                    ),
                )
            ]
        },
        None,
    )
    updated_fact = _decode_ok(updated["records"][0])
    resolved_fact = _decode_ok(resolved["records"][0])
    assert updated_fact["fact_kind"] == FactKind.RISK_RESULT.value
    assert resolved_fact["fact_kind"] == FactKind.RISK_RESULT.value
    assert updated_fact["record_id"] == resolved_fact["record_id"] == "risk#same"
    assert updated_fact["dedup_id"] == resolved_fact["dedup_id"] == "risk#same"
    assert updated_fact["producer_detail_type"] == "risk.updated"
    assert resolved_fact["producer_detail_type"] == "risk.resolved"


def test_hazard_materialized_maps_to_version_fact(transform):
    event = {
        "records": [
            _encode_record(
                "h1",
                _eventbridge_event(
                    "wilvor.weather",
                    "hazard.materialized",
                    _hazard_detail(),
                ),
            )
        ]
    }
    fact = _decode_ok(transform.lambda_handler(event, None)["records"][0])
    assert fact["dataset"] == "hazard_version"
    assert fact["record_id"] == "sigmet-aaa#v1"
    assert fact["event_time_utc"] == "2026-07-18T12:30:00Z"
    assert fact["geometry_hash"] == "copied-hash-v1"


def test_canonical_geometry_passthrough(transform):
    geometry_fact = build_hazard_geometry_fact(
        active_hazard=_hazard_detail(),
        geometry_points=_geometry_points(),
    )
    event = {
        "records": [
            _encode_record("g1", geometry_fact.to_dict()),
        ]
    }
    record = transform.lambda_handler(event, None)["records"][0]
    fact = _decode_ok(record)
    assert fact["dataset"] == "hazard_geometry"
    assert fact["producer_detail_type"] == GEOMETRY_IN_MEMORY_DETAIL_TYPE
    assert fact["geometry"]["type"] == "Polygon"
    assert fact["geometry"]["coordinates"][0][0] == [-75.0, 40.0]
    assert record["metadata"]["partitionKeys"]["dataset"] == "hazard_geometry"
    assert record["metadata"]["partitionKeys"]["day"] == "18"


def test_partition_keys_use_canonical_event_date_not_arrival(transform):
    event = {
        "records": [
            _encode_record(
                "late",
                _eventbridge_event(
                    "wilvor.encounter",
                    "encounter.updated",
                    _encounter_detail(
                        detected_at_utc="2023-11-14T23:59:59Z",
                        detected_at_epoch=1_700_006_399,
                    ),
                    envelope_time="2023-11-15T06:00:00Z",
                ),
                arrival_ms=1_700_100_000_000,
            )
        ]
    }
    record = transform.lambda_handler(event, None)["records"][0]
    fact = _decode_ok(record)
    assert fact["event_day"] == "14"
    assert record["metadata"]["partitionKeys"]["day"] == "14"
    assert fact["event_time_utc"] == "2023-11-14T23:59:59Z"


def test_midnight_utc_is_next_calendar_day(transform):
    event = {
        "records": [
            _encode_record(
                "midnight",
                _eventbridge_event(
                    "wilvor.encounter",
                    "encounter.updated",
                    _encounter_detail(
                        detected_at_utc="2023-11-15T00:00:00Z",
                        detected_at_epoch=1_700_006_400,
                    ),
                ),
            )
        ]
    }
    record = transform.lambda_handler(event, None)["records"][0]
    fact = _decode_ok(record)
    assert fact["event_day"] == "15"
    assert record["metadata"]["partitionKeys"] == {
        "dataset": "encounter",
        "year": "2023",
        "month": "11",
        "day": "15",
    }


def test_duplicate_input_keeps_same_dedup_id(transform):
    payload = _eventbridge_event(
        "wilvor.encounter",
        "encounter.updated",
        _encounter_detail(),
    )
    event = {
        "records": [
            _encode_record("a", payload),
            _encode_record("b", payload),
        ]
    }
    records = transform.lambda_handler(event, None)["records"]
    first = _decode_ok(records[0])
    second = _decode_ok(records[1])
    assert first["dedup_id"] == second["dedup_id"]
    assert records[0]["recordId"] == "a"
    assert records[1]["recordId"] == "b"


def test_malformed_and_unknown_records_are_processing_failed(transform):
    event = {
        "records": [
            {
                "recordId": "bad-b64",
                "data": "%%%not-base64%%%",
            },
            _encode_record("bad-json", b"{not-json"),
            _encode_record(
                "unknown",
                _eventbridge_event(
                    "wilvor.weather",
                    "HazardCoordinates.materialized",
                    {"hazard_id": "x"},
                ),
            ),
            _encode_record(
                "missing-time",
                _eventbridge_event(
                    "wilvor.encounter",
                    "encounter.updated",
                    _encounter_detail(detected_at_utc=""),
                ),
            ),
        ]
    }
    records = transform.lambda_handler(event, None)["records"]
    assert [record["recordId"] for record in records] == [
        "bad-b64",
        "bad-json",
        "unknown",
        "missing-time",
    ]
    assert all(record["result"] == "ProcessingFailed" for record in records)
    assert all(record.get("result") != "Dropped" for record in records)
    assert all("data" not in record for record in records)


def test_mixed_batch_preserves_record_ids_and_never_drops(transform):
    event = {
        "records": [
            _encode_record(
                "ok",
                _eventbridge_event(
                    "wilvor.risk",
                    "risk.updated",
                    _risk_detail(),
                ),
            ),
            _encode_record(
                "fail",
                _eventbridge_event("other.source", "other.type", {}),
            ),
        ]
    }
    records = transform.lambda_handler(event, None)["records"]
    assert records[0]["recordId"] == "ok"
    assert records[0]["result"] == "Ok"
    assert records[1]["recordId"] == "fail"
    assert records[1]["result"] == "ProcessingFailed"
    assert "Dropped" not in {record["result"] for record in records}


def test_invalid_geometry_passthrough_is_processing_failed(transform):
    payload = build_hazard_geometry_fact(
        active_hazard=_hazard_detail(),
        geometry_points=_geometry_points(),
    ).to_dict()
    payload["geometry_type"] = "POINT"
    event = {"records": [_encode_record("bad-geo", payload)]}
    record = transform.lambda_handler(event, None)["records"][0]
    assert record["result"] == "ProcessingFailed"
    assert record["recordId"] == "bad-geo"


def test_transform_source_has_no_forbidden_imports():
    tree = ast.parse(TRANSFORM_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert "boto3" not in imported
    assert "botocore" not in imported
    assert "wilvor_ai" not in imported
    assert "wilvor_operational" not in imported
    assert "time" not in imported


def test_transform_does_not_import_runtime_forbidden_modules():
    import os
    import subprocess
    import sys

    shared = TRANSFORM_PATH.parents[2] / "shared"
    script = f"""
import importlib.util
import sys
from pathlib import Path
sys.path.insert(0, {str(shared)!r})
spec = importlib.util.spec_from_file_location(
    "historical_facts_transform_isolated",
    {str(TRANSFORM_PATH)!r},
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
assert "wilvor_ai" not in sys.modules
assert "wilvor_operational.current_set" not in sys.modules
assert module.RESULT_OK == "Ok"
assert module.RESULT_PROCESSING_FAILED == "ProcessingFailed"
assert module.RESULT_PROCESSING_FAILED != "Dropped"
"""
    env = os.environ.copy()
    pythonpath = str(shared)
    existing = env.get("PYTHONPATH", "")
    if existing:
        pythonpath = pythonpath + os.pathsep + existing
    env["PYTHONPATH"] = pythonpath
    completed = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout
    source = TRANSFORM_PATH.read_text(encoding="utf-8")
    assert 'result = "Dropped"' not in source
    assert '"Dropped"' not in source


def _probe_payload() -> dict[str, Any]:
    return {
        "control_schema_version": "wilvor.historical.collection_control.v1",
        "record_type": "COLLECTION_PROBE",
        "collection_epoch_id": "epoch-1",
        "stream": "facts",
        "probe_id": "probe-1",
        "interval_start_utc": "2026-07-18T12:00:00Z",
        "interval_end_utc": "2026-07-18T12:15:00Z",
        "observed_at_utc": "2026-07-18T12:15:00Z",
        "dataset": "_collection_control",
        "event_year": "2026",
        "event_month": "07",
        "event_day": "18",
        "dedup_id": "probe|facts|2026-07-18T12:00:00Z|probe-1",
    }


def test_control_probe_is_ok_with_collection_control_partitions(transform):
    envelope = _eventbridge_event(
        "wilvor.historical.control",
        "collection.probe",
        _probe_payload(),
    )
    record = transform.lambda_handler(
        {"records": [_encode_record("probe", envelope)]},
        None,
    )["records"][0]
    fact = _decode_ok(record)
    assert fact["dataset"] == "_collection_control"
    assert fact["probe_id"] == "probe-1"
    assert record["metadata"]["partitionKeys"]["dataset"] == "_collection_control"
    assert record["metadata"]["partitionKeys"]["year"] == "2026"


def test_unknown_control_record_is_processing_failed_never_dropped(transform):
    record = transform.lambda_handler(
        {
            "records": [
                _encode_record(
                    "bad-control",
                    {
                        "source": "wilvor.historical.control",
                        "detail-type": "collection.probe",
                        "detail": {"record_type": "COLLECTION_PROBE"},
                    },
                )
            ]
        },
        None,
    )["records"][0]
    assert record["result"] == "ProcessingFailed"
    assert record.get("result") != "Dropped"
