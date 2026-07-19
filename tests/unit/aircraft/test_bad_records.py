from __future__ import annotations

from wilvor_aircraft import bad_records
from wilvor_aircraft.schemas import AIRCRAFT_BAD_RECORD_SCHEMA_VERSION


def test_build_bad_record_preserves_rejection_context(monkeypatch):
    monkeypatch.setattr(
        bad_records,
        "now_utc_iso",
        lambda: "2026-07-18T12:00:00+00:00",
    )

    result = bad_records.build_bad_record(
        source="opensky",
        poll_id="poll-123",
        raw_index=7,
        reasons=["missing_required_latitude"],
        raw_record={"raw_state_vector": ["abc123"]},
        stage="aircraft_raw_processor.validate_and_map",
    )

    assert result == {
        "schema_version": AIRCRAFT_BAD_RECORD_SCHEMA_VERSION,
        "source": "opensky",
        "poll_id": "poll-123",
        "raw_index": 7,
        "stage": "aircraft_raw_processor.validate_and_map",
        "reasons": ["missing_required_latitude"],
        "rejected_at_utc": "2026-07-18T12:00:00+00:00",
        "raw_record": {"raw_state_vector": ["abc123"]},
    }
