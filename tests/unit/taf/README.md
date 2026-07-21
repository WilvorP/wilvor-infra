# TAF Unit Tests

These tests cover the current TAF poller and latest-state processor without
calling NOAA or AWS.

Run:

```powershell
.\scripts\run-unit-tests.ps1 -Target taf
```

Coverage:

```powershell
.\scripts\run-unit-tests.ps1 -Target taf -Coverage
```

Covered behavior includes station normalization and API chunking, 204 handling,
gzip archiving, Kinesis contracts and batching, TAF and forecast-period
normalization, visibility, wind and ceiling handling, stable source versions,
NEW/UPDATED/CORRECTED/STALE/UNCHANGED classification, conditional DynamoDB
writes, EventBridge retry behavior, bad-record archiving, metrics, and Lambda
partial-batch failures.
