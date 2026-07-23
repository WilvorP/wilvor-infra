# METAR Unit Tests

These tests cover the current METAR poller and latest-state processor without
calling NOAA or AWS.

Run:

```powershell
.\scripts\run-unit-tests.ps1 -Target metar
```

Coverage:

```powershell
.\scripts\run-unit-tests.ps1 -Target metar -Coverage
```

Covered behavior includes HTTP fetching, response extraction, gzip archiving,
Kinesis contracts and batching, feature normalization, source versions,
NEW/UPDATED/CORRECTED/STALE/UNCHANGED classification, conditional DynamoDB
writes, pending EventBridge retries, bad-record archiving, metrics, and Lambda
partial-batch failures.
