OPENSKY_RAW_SCHEMA_VERSION = "opensky_aircraft_raw.v1"
AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION = "aircraft_current_state.v1"
AIRCRAFT_BAD_RECORD_SCHEMA_VERSION = "aircraft_bad_record.v1"

OPENSKY_STATE_VECTOR_COLUMNS = [
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
]

METERS_TO_FEET = 3.28084
MPS_TO_KNOTS = 1.94384
MPS_TO_FPM = 196.8504

OPENSKY_REQUIRED_CLEAN_FIELDS = [
    "icao24",
    "callsign",
    "longitude",
    "latitude",
    "geo_altitude",
    "velocity",
    "true_track",
    "vertical_rate",
    "on_ground",
    "last_contact",
]