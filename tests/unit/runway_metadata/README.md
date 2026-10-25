# Runway metadata unit tests

**Canonical page:** [`docs/ingestion/runway-metadata.md`](../../../docs/ingestion/runway-metadata.md)
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** UNIT
**Production authorization:** these tests do not grant production use

These tests cover FAA ZIP parsing, cycle control, snapshot persistence, and
loader workflow without calling FAA or AWS.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Tests | UNIT | this directory |
| Application code under test | CODE | `functions/runway_metadata/loader/` |
| Deployment | UNKNOWN | these tests do not observe AWS |

## Coverage

- `test_airport_mapping.py` — FAA id to ICAO lookup
- `test_parsing_and_joining.py` — APT CSV join and unsupported-airport skip
- `test_validation.py` — runway ends, headings, coordinates
- `test_duplicate_cycle.py` — `CycleDecisionType`
- `test_source_loader.py` — local ZIP, archive, S3 replay
- `test_persistence.py` — snapshot plan, apply, deletion protection
- `test_bad_record_isolation.py` — invalid row does not block valid runways
- `test_loader_workflow.py` — first load and duplicate skip

## Run

From the repository root:

```powershell
python -m pytest .\tests\unit\runway_metadata -q
```

`scripts/run-unit-tests.ps1` joins `tests\unit\$Target`, so `-Target runway_metadata` selects this directory (source: `scripts/run-unit-tests.ps1`, `$testPath`).

## Known limitations

- There is no `tests/unit/station_reference/` counterpart.

## Historical record

No superseded statement is stored on this page.

## Sources

- Tests: `tests/unit/runway_metadata/test_loader_workflow.py`, `test_duplicate_cycle.py`
