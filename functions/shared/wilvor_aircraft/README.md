# wilvor_aircraft

**Canonical page:** [`docs/ingestion/aircraft.md`](../../../docs/ingestion/aircraft.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** CODE, UNIT
**Production authorization:** no grant found in this package

## Purpose

Shared mapping, schema constants, bad-record envelope, and EMF helper used by the OpenSky poller, aircraft raw processor, and current-state writer. Pipeline behavior is on the canonical page.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | this directory |
| Terraform | this package is copied into Lambda zips; it is not a Terraform root | `scripts/build_aircraft_lambdas.ps1` (packaging only; not re-documented here) |
| Unit tests | UNIT | `tests/unit/aircraft/test_opensky_mapper.py`, `test_bad_records.py`, `test_monitoring.py` |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | this package |

## Behavior

| File | Symbols |
| --- | --- |
| `schemas.py` | `OPENSKY_RAW_SCHEMA_VERSION` (`opensky_aircraft_raw.v1`), `AIRCRAFT_CURRENT_STATE_SCHEMA_VERSION` (`aircraft_current_state.v2`), `OPENSKY_STATE_VECTOR_COLUMNS`, `OPENSKY_REQUIRED_CLEAN_FIELDS` |
| `opensky_mapper.py` | `validate_raw_state_vector`, `map_raw_event_to_current_state`, `classify_freshness` |
| `bad_records.py` | `build_bad_record` |
| `monitoring.py` | `emit_metric`, `METRIC_NAMESPACE = "Wilvor/Pipeline"` |
| `__init__.py` | empty |

`classify_freshness` returns `UNAVAILABLE` when age is missing, `FRESH` / `ACCEPTABLE` / `STALE` from caller-supplied thresholds.

## Known limitations

- `time_position` is required at map time but is not in `OPENSKY_REQUIRED_CLEAN_FIELDS`.
- Copies under `functions/*/build/wilvor_aircraft/` can drift from this directory until the next aircraft zip build.

## Historical record

No superseded statement is stored on this page.

## Sources

- Source: `functions/shared/wilvor_aircraft/schemas.py`
- Source: `functions/shared/wilvor_aircraft/opensky_mapper.py` (`classify_freshness`)
- Tests: `tests/unit/aircraft/test_opensky_mapper.py`
