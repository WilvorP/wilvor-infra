# Ingestion and reference data

**Canonical page:** this index
**Verified against:** `8abd2d5` on `docs/ws2-ingestion`
**Maturity:** this index states no behavior

This index maps Wilvor ingestion and reference-data documentation. It does not state pipeline behavior. Canonical behavior pages are listed below. Maturity labels are defined only in [`docs/DOCUMENTATION_STANDARDS.md`](../DOCUMENTATION_STANDARDS.md).

Platform entry, lifecycle, and testing indexes belong to WS1 (`docs/README.md`, `docs/overview/`, `docs/operations/`, `docs/testing/`). Downstream processing, DynamoDB data-model narrative, Operational API, and dashboard pages belong to WS3 (`docs/processing/`, `docs/data/dynamodb.md`, `docs/api/`, `docs/dashboard/`). Historical facts and analytics belong to WS4 (`docs/historical/`). AI Operations Copilot belongs to WS5 (`docs/ai/`).

## Pages on this workstream

| Page | Topic |
| --- | --- |
| [Overview](overview.md) | Pipelines, event catalog, schedule flags, and Terraform roots |
| [Aircraft](aircraft.md) | OpenSky poll, raw-to-clean Kinesis, current-state writer |
| [SIGMET](sigmet.md) | NOAA airsigmet poll, v4 processor, hazard tables and events |
| [Hazard station candidates](hazard-station-candidates.md) | `HazardCoordinates.materialized` to `hazard.stations.ready` |
| [METAR](metar.md) | Hazard-scoped NOAA METAR poll and latest-state processor |
| [TAF](taf.md) | Hazard-scoped NOAA TAF poll and forecast-period materialization |
| [Station reference](station-reference.md) | Aviation Weather station cache loader |
| [Runway metadata](runway-metadata.md) | FAA NASR APT ZIP loader |
| [Weather events](weather-events.md) | EventBridge-to-logs capture of a subset of weather events |

## Colocated READMEs

| Path | Role |
| --- | --- |
| [`modules/aircraft_foundation/README.md`](../../modules/aircraft_foundation/README.md) | Terraform module pointer |
| [`modules/sigmet/README.md`](../../modules/sigmet/README.md) | Terraform module pointer |
| [`modules/metar/README.md`](../../modules/metar/README.md) | Terraform module pointer |
| [`modules/taf/README.md`](../../modules/taf/README.md) | Terraform module pointer |
| [`modules/station_reference/README.md`](../../modules/station_reference/README.md) | Terraform module pointer |
| [`modules/runway_metadata/README.md`](../../modules/runway_metadata/README.md) | Terraform module pointer |
| [`modules/weather_events/README.md`](../../modules/weather_events/README.md) | Terraform module pointer |
| [`functions/shared/wilvor_aircraft/README.md`](../../functions/shared/wilvor_aircraft/README.md) | Shared aircraft mapping package |
| [`functions/shared/wilvor_weather/README.md`](../../functions/shared/wilvor_weather/README.md) | Shared pipeline EMF helper |
| [`tests/unit/aircraft/README.md`](../../tests/unit/aircraft/README.md) | Aircraft unit-test runner notes |
| [`tests/unit/sigmet/README.md`](../../tests/unit/sigmet/README.md) | SIGMET unit-test runner notes |
| [`tests/unit/metar/README.md`](../../tests/unit/metar/README.md) | METAR unit-test runner notes |
| [`tests/unit/taf/README.md`](../../tests/unit/taf/README.md) | TAF unit-test runner notes |
| [`tests/unit/runway_metadata/README.md`](../../tests/unit/runway_metadata/README.md) | Runway unit-test runner notes |

## Historical runbooks

These files are preserved. Their bodies are not the current specification. Each file has a banner that points here.

- [`functions/metar_pipeline_runbook.txt`](../../functions/metar_pipeline_runbook.txt)
- [`functions/weather_pipeline_reproducibility.txt`](../../functions/weather_pipeline_reproducibility.txt)
- [`functions/local_runner_documentation.txt`](../../functions/local_runner_documentation.txt)
- [`functions/Runway_Metadata_Commands.txt`](../../functions/Runway_Metadata_Commands.txt)
- [`functions/Wilvor_Aircraft_SIGMET_Integration_Test_Runbook_Original_Structure.txt.txt`](../../functions/Wilvor_Aircraft_SIGMET_Integration_Test_Runbook_Original_Structure.txt.txt)
