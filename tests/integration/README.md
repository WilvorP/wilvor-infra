# Wilvor Deployed Integration Tests

These tests use controlled, dynamically generated records against the deployed
`dev` environment. They do not call OpenSky, NOAA, or any other external data
provider.

## Scope

Eight tests cover the behavior that unit and infrastructure tests cannot prove:

### Aircraft

- A controlled OpenSky raw envelope travels through the raw Kinesis stream,
  raw processor, clean stream, current-state writer, and DynamoDB.
- The deployed writer rejects an older aircraft state.
- An invalid raw aircraft vector is archived under the S3 bad-record prefix.

### SIGMET

- A controlled GeoJSON feature travels through the raw stream and produces an
  ActiveHazards item, all expected HazardCells items, and a Weather.changed
  event in the shared EventBridge log group.
- Reprocessing the same feature updates `last_seen_at` without rewriting state
  or publishing another event.
- Unsupported geometry is archived as a permanent bad record.

### METAR

- A controlled GeoJSON feature produces the latest station state and a
  Weather.changed event.
- Reprocessing the same report leaves the state and event count unchanged.
- A record without observation time is archived as a bad record.

### TAF

- A controlled TAF with two forecast periods produces the latest station state
  and a Weather.changed event.
- Reprocessing the same TAF leaves the state and event count unchanged.
- A TAF without required content is archived as a bad record.

## Why there is no combined test

`integration/combined/` remains empty because the current repository does not
yet contain a deployed service that combines aircraft, SIGMET, METAR, and TAF
state. Adding a synthetic combined test would duplicate the individual
pipelines without testing real application behavior.

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
