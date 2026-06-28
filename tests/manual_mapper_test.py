import json

from wilvor_aircraft.bad_records import build_bad_record 
from wilvor_aircraft.opensky_mapper import map_raw_event_to_current_state


raw_event = {
    "schema_version": "opensky_aircraft_raw.v1",
    "source": "opensky",
    "poll_id": "test-poll-001",
    "raw_index": 0,
    "raw_state_vector": [
        "a1b2c3",
        "UAL123  ",
        "United States",
        1710000000,
        1710000005,
        -122.4194,
        37.7749,
        10000.0,
        False,
        230.0,
        270.0,
        0.0,
        None,
        10200.0,
        "1200",
        False,
        0,
    ],
}

item, reasons = map_raw_event_to_current_state(raw_event)

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
    print(json.dumps(item, indent=2))