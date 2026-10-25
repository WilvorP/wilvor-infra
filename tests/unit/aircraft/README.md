# Aircraft unit tests

**Canonical page:** [`docs/ingestion/aircraft.md`](../../../docs/ingestion/aircraft.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** UNIT
**Production authorization:** these tests do not grant production use

This suite tests aircraft code without calling AWS or OpenSky.

Covered components:

- Shared OpenSky mapper and validation
- Bad-record construction
- CloudWatch EMF metric generation
- OpenSky poller helper and handler behavior
- Aircraft raw processor envelope, archive, publishing, and handler behavior
- Aircraft current-state writer validation, DynamoDB conversion, stale-write handling, and handler behavior

AWS clients are replaced with fakes. HTTP calls are replaced with local response objects.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Tests | UNIT | this directory |
| Application code under test | CODE | `functions/opensky_poller/app.py`, `functions/aircraft_raw_processor/app.py`, `functions/aircraft_current_state_writer/app.py`, `functions/shared/wilvor_aircraft/` |
| Deployment | UNKNOWN | these tests do not observe AWS |

## Install test dependencies

From the repository root:

```powershell
python -m pip install -r requirements-test.txt
```

## Run aircraft unit tests

```powershell
python -m pytest .\tests\unit\aircraft -v
```

Or use the test script:

```powershell
.\scripts\run-unit-tests.ps1 -Target aircraft
```

## Run with coverage

```powershell
.\scripts\run-unit-tests.ps1 `
    -Target aircraft `
    -Coverage
```

Generated reports are written to:

```text
test-results/unit/
├── aircraft-results.xml
└── aircraft-coverage.xml
```

## Unit-test rule

These tests must pass even when:

- Terraform infrastructure is destroyed
- AWS SSO is logged out
- OpenSky is unavailable
- NOAA is unavailable
- The machine has no internet connection

## Known limitations

- Shared fixtures in `tests/conftest.py` (`clean_aircraft_record`) still use `icao24`, `ttl_epoch`, and `aircraft_current_state.v1`. Current mapper schema is `aircraft_current_state.v2` with `aircraft_id` and `expires_at_epoch` (source: `functions/shared/wilvor_aircraft/schemas.py`).
- Some assertions in `test_opensky_mapper.py` still name `icao24` and `ttl_epoch`. `tests/manual_mapper_test.py` matches v2.

## Historical record

No superseded statement is stored on this page beyond the fixture drift named above.

## Sources

- Tests: `tests/unit/aircraft/test_opensky_poller.py`, `test_opensky_mapper.py`, `test_aircraft_raw_processor.py`, `test_aircraft_current_state_writer.py`
