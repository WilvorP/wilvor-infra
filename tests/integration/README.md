# Wilvor Deployed Integration Tests

**Canonical page:** this page for the deployed pipeline suite. Layer map: [`../../docs/testing/overview.md`](../../docs/testing/overview.md)
**Verified against:** `8abd2d5` on `docs/ws1-platform-foundations`
**Maturity:** CODE, INT
**Production authorization:** no grant found. The suite refuses to run unless Terraform outputs report `environment = dev`.

These tests use controlled, dynamically generated records against the deployed
`dev` environment. They do not call OpenSky, NOAA, or any other external data
provider.

INT means these test modules exist and are written for a deployed `dev`
environment. This file does not show that the environment is applied now, and
it does not record a pass count.

## Implementation status

| Question | Result | Source |
| --- | --- | --- |
| Code | CODE | `tests/integration/{aircraft,sigmet,metar,taf}/test_*_pipeline.py` |
| Terraform | TF | tests read `envs/dev` outputs via `tests/integration/conftest.py` |
| Unit tests | none found in this directory | unit coverage lives under `tests/unit/` |
| Contract tests | none found in this directory | |
| Infrastructure tests | none found in this directory | |
| Integration tests | INT | the four pipeline modules named above |
| Live harness | none found in this directory | `scripts/poll-all-once.ps1` is a separate operator script |
| Deployment | UNKNOWN | `not verified` |
| Production authorization | no grant found | `tests/integration/conftest.py` (`environment == "dev"`) |

## Scope

Eight tests cover the behavior that unit and infrastructure tests cannot prove:

### Aircraft

- A controlled OpenSky raw envelope travels through the raw Kinesis stream,
  raw processor, clean stream, current-state writer, and DynamoDB.
- The deployed writer rejects an older aircraft state.
- An invalid raw aircraft vector is archived under the S3 bad-record prefix.

### SIGMET

- A controlled GeoJSON feature travels through the raw stream and produces an
  ActiveHazards item and expected HazardCells items.
- The SIGMET processor publishes `hazard.materialized` and
  `HazardCoordinates.materialized` on `wilvor.weather` (source:
  `functions/weather/sigmet/processor/app.py`, `publish_hazard_materialized`,
  `publish_hazard_coordinates_materialized`). It does not publish
  `Weather.changed`.
- Reprocessing the same feature updates `last_seen_at` without rewriting state
  or publishing another event.
- Unsupported geometry is archived as a permanent bad record.

### METAR

- A controlled GeoJSON feature produces the latest station state.
- The METAR processor publishes `metar.updated` on `wilvor.weather` (source:
  `functions/weather/metar/processor/app.py`, `publish_metar_updated_event`).
  It does not publish `Weather.changed`.
- Reprocessing the same report leaves the state and event count unchanged.
- A record without observation time is archived as a bad record.

### TAF

- A controlled TAF with two forecast periods produces the latest station state.
- The TAF processor publishes `taf.materialized` on `wilvor.weather` (source:
  `functions/weather/taf/processor/app.py`, `publish_taf_materialized`). It
  does not publish `Weather.changed`.
- Reprocessing the same TAF leaves the state and event count unchanged.
- A TAF without required content is archived as a bad record.

### Weather event logging

`modules/weather_events/main.tf` (`aws_cloudwatch_event_rule.weather_changed`)
matches `source = wilvor.weather` with detail-types `Weather.changed` and
`metar.updated` only. It does not match `hazard.materialized`,
`HazardCoordinates.materialized`, or `taf.materialized`.

No Python producer under `functions/` publishes `Weather.changed` at `8abd2d5`
(search: `Weather.changed` in `functions/**/*.py`).

The SIGMET, METAR, and TAF integration tests still call
`wait_for_weather_event` against `weather_changed_log_group_name`. That helper's
wait message is `Weather.changed event containing {token}` (source:
`tests/helpers/integration_support.py`, `wait_for_weather_event`). Those tests
were not edited in this documentation change. METAR `metar.updated` is in the
current weather_events pattern. SIGMET and TAF current detail-types are not.

## Why there is no combined test

`integration/combined/` contains only `.gitkeep` at `8abd2d5`. No
`tests/integration` module exercises projection, encounter, risk, airport
status, assessment, recommendations, alerts, or the Operational API together.

Those processors exist as Terraform modules in `envs/dev/main.tf`. They are
not covered by this integration suite.

Dedicated unit-test directories at `8abd2d5`: `tests/unit/projection/`,
`tests/unit/encounter/`, `tests/unit/risk/`, `tests/unit/airport_status/`,
`tests/unit/recommendations/`, and `tests/unit/active_alerts/`. Search for
`tests/unit/airport_assessment/` returned no directory. Airport-assessment
coverage, if any, is not a dedicated unit suite under that path.

## Safety

The suite refuses to run unless Terraform outputs report:

```text
environment = dev
```

Each test uses unique aircraft, station, hazard, and correlation identifiers.

By default, the suite deletes:

- DynamoDB records it creates
- SIGMET H3 index records it creates
- S3 bad-record objects it creates

Kinesis records expire according to the stream retention policy. EventBridge
test events remain in the shared CloudWatch log group until its configured
three-day retention removes them.

## Run

All pipelines:

```powershell
.\scripts\run-integration-tests.ps1 -Target all
```

One pipeline:

```powershell
.\scripts\run-integration-tests.ps1 -Target aircraft
.\scripts\run-integration-tests.ps1 -Target sigmet
.\scripts\run-integration-tests.ps1 -Target metar
.\scripts\run-integration-tests.ps1 -Target taf
```

Keep created DynamoDB and S3 artifacts for debugging:

```powershell
.\scripts\run-integration-tests.ps1 `
    -Target sigmet `
    -KeepArtifacts
```

Results are written to:

```text
test-results/integration/
```

## Known limitations

- Whether `envs/dev` is applied is UNKNOWN. These tests fail in
  `tests/integration/conftest.py` when Terraform outputs cannot be read or
  `environment` is not `dev`.
- Combined, processing-pipeline, and Operational API integration tests are
  absent from this directory.
- SIGMET and TAF integration tests still wait on the weather-changed log
  group even though current producers do not emit `Weather.changed` and
  `weather_events` does not match `hazard.materialized` or `taf.materialized`.
- `tests/unit/airport_assessment/` is absent.
- A passing local run is not recorded here.

## Historical record

The previous combined-test paragraph said `integration/combined/` was empty
because the repository did not yet contain a deployed service that combines
aircraft, SIGMET, METAR, and TAF state. That sentence described an earlier
phase. At `8abd2d5`, `envs/dev/main.tf` instantiates projection, encounter,
risk, airport status, airport assessment, recommendations, active alerts, and
the Operational API. Combined integration coverage is still absent; the reason
is missing tests, not missing processor modules.

The previous SIGMET, METAR, and TAF bullets said each pipeline produced a
`Weather.changed` event in the shared EventBridge log group. Current producers
emit `hazard.materialized` / `HazardCoordinates.materialized`, `metar.updated`,
and `taf.materialized` respectively. The existing integration tests still use
`wait_for_weather_event` and `weather_changed_log_group_name`; this page does
not change those tests.

The previous combined-test paragraph also said downstream processors had unit
tests under `tests/unit/`. That overstated coverage: `tests/unit/airport_assessment/`
is absent.

## Sources

- Source: `tests/integration/conftest.py`
- Source: `tests/integration/aircraft/test_aircraft_pipeline.py`
- Source: `tests/integration/sigmet/test_sigmet_pipeline.py`
- Source: `tests/integration/metar/test_metar_pipeline.py`
- Source: `tests/integration/taf/test_taf_pipeline.py`
- Source: `scripts/run-integration-tests.ps1`
- Source: `envs/dev/main.tf`
- Source: `functions/weather/sigmet/processor/app.py` (`publish_hazard_materialized`, `publish_hazard_coordinates_materialized`)
- Source: `functions/weather/metar/processor/app.py` (`publish_metar_updated_event`)
- Source: `functions/weather/taf/processor/app.py` (`publish_taf_materialized`)
- Source: `modules/weather_events/main.tf` (`aws_cloudwatch_event_rule.weather_changed`)
- Source: `tests/helpers/integration_support.py` (`wait_for_weather_event`)
