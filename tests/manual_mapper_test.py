import json
from datetime import datetime, timezone

from wilvor_aircraft.bad_records import build_bad_record
from wilvor_aircraft.opensky_mapper import map_raw_event_to_current_state


now = datetime.now(timezone.utc)
current_epoch = int(now.timestamp())

raw_event = {
    "schema_version": "opensky_aircraft_raw.v1",
    "source": "opensky",
    "poll_id": "test-poll-001",
    "fetched_at_utc": now.isoformat(),
    "raw_s3_bucket": "wilvor-test-aircraft-archive",
    "raw_s3_key": (
        "raw/source=opensky/"
        "year=2026/month=07/day=29/hour=00/"
        "test-poll-001.json.gz"
    ),
    "raw_index": 0,
    "raw_state_vector": [
        "a1b2c3",              # icao24
        "UAL123  ",            # callsign
        "United States",       # origin_country
        current_epoch - 5,     # time_position
        current_epoch,         # last_contact
        -122.4194,             # longitude
        37.7749,               # latitude
        10000.0,               # baro_altitude
        False,                 # on_ground
        230.0,                 # velocity
        270.0,                 # true_track
        0.0,                   # vertical_rate
        None,                  # sensors
        10200.0,               # geo_altitude
        "1200",                # squawk
        False,                 # spi
        0,                     # position_source
    ],
}

item, reasons = map_raw_event_to_current_state(
    raw_event,
    ttl_seconds=1800,
    h3_resolution=4,
    fresh_seconds=60,
    acceptable_seconds=180,
)

if reasons:
    bad_record = build_bad_record(
        source="opensky",
        poll_id=raw_event.get("poll_id"),
        raw_index=raw_event.get("raw_index"),
        reasons=reasons,
        raw_record=raw_event,
        stage="manual_mapper_test",
    )

    print(json.dumps(bad_record, indent=2))
else:
    assert item is not None
    assert item["aircraft_id"] == "a1b2c3"
    assert item["schema_version"] == "aircraft_current_state.v2"
    assert item["has_position"] is True
    assert item["current_h3_cell"]
    assert item["h3_resolution"] == 4
    assert item["state_version"] == (
        f"a1b2c3#{current_epoch - 5}"
    )
    assert item["idempotency_key"] == item["state_version"]
    assert item["source_system"] == "OPEN_SKY"
    assert item["raw_s3_uri"].startswith("s3://")
    assert "expires_at_epoch" in item

    assert "icao24" not in item
    assert "ttl_epoch" not in item

    print(json.dumps(item, indent=2))