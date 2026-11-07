"""Offline Athena/S3 doubles for Sprint 4 historical contract tests.

These clients are process-local. They are not boto3, and they do not
establish source authenticity or production query authority.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from wilvor_historical.coverage import ENCOUNTER_HORIZON_SECONDS
from wilvor_historical.coverage_contracts import (
    CONTROL_SCHEMA_VERSION,
    CollectionActivationRecord,
    CollectionEpochRecord,
    CollectionGapRecord,
    ControlRecordType,
    CoverageIntervalRecord,
    CoverageStream,
    GapDomain,
    RecoveryState,
    UncertaintyClass,
)
from wilvor_historical.time import canonicalize_utc_z
from wilvor_historical_query.executor import ATHENA_STATE_SUCCEEDED
from wilvor_historical_query.query_registry import (
    ENCOUNTER_SUMMARY_OUTPUT_COLUMNS,
    HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS,
    LIST_ENCOUNTER_OUTPUT_COLUMNS,
    RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
    RISK_SUMMARY_OUTPUT_COLUMNS,
)
from wilvor_ai.sprint4_historical_query_runtime import (
    Sprint4HistoricalDeploymentConfig,
    compose_sprint4_historical_query_runtime,
)
from wilvor_ai.sprint4_historical_tool_port import (
    HostAuthorizedHistoricalWindow,
    bind_sprint4_historical_tool_port,
)


WORKGROUP = "wilvor-dev-historical-analytics"
DATABASE = "wilvor_dev_historical"
RESULTS_BUCKET = "wilvor-dev-historical-athena-results-123456789012"
RESULTS_PREFIX = f"s3://{RESULTS_BUCKET}/athena-results/"
FACTS_BUCKET = "wilvor-dev-historical-facts-123456789012"
PLAN = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
ELIGIBLE = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
EPOCH_ID = "epoch-sprint4"
AS_OF = canonicalize_utc_z(
    datetime.fromisoformat(ELIGIBLE[1].replace("Z", "+00:00"))
    + timedelta(seconds=ENCOUNTER_HORIZON_SECONDS + 1)
)


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeHistoricalS3:
    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def put(self, key: str, payload: dict[str, Any]) -> None:
        self.objects[key] = json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def list_objects_v2(self, **kwargs: Any) -> dict[str, Any]:
        self.list_calls.append(kwargs)
        prefix = kwargs["Prefix"]
        keys = [key for key in self.objects if key.startswith(prefix)]
        keys.sort()
        return {
            "Contents": [{"Key": key} for key in keys],
            "IsTruncated": False,
        }

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self.get_calls.append(kwargs)
        return {"Body": FakeBody(self.objects[kwargs["Key"]])}


class ScriptedHistoricalAthena:
    """Deterministic Athena double. One result page per StartQueryExecution."""

    def __init__(
        self,
        pages: list[dict[str, Any]] | None = None,
        *,
        states: list[str] | None = None,
        workgroup: str = WORKGROUP,
        database: str = DATABASE,
        results_prefix: str = RESULTS_PREFIX,
    ) -> None:
        self.pages = list(pages or [])
        self.states = list(states or [ATHENA_STATE_SUCCEEDED])
        self.workgroup = workgroup
        self.database = database
        self.results_prefix = results_prefix
        self.start_calls: list[dict[str, Any]] = []
        self.get_execution_calls: list[dict[str, Any]] = []
        self.get_results_calls: list[dict[str, Any]] = []
        self.stop_calls: list[dict[str, Any]] = []
        self._starts = 0
        self._state_index = 0
        self._page_index = 0
        self._ids: list[str] = []

    def start_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(kwargs)
        self._starts += 1
        execution_id = f"aaaaaaaa-bbbb-cccc-dddd-{self._starts:012d}"
        self._ids.append(execution_id)
        return {"QueryExecutionId": execution_id}

    def get_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.get_execution_calls.append(kwargs)
        execution_id = kwargs.get("QueryExecutionId")
        state = self.states[min(self._state_index, len(self.states) - 1)]
        self._state_index += 1
        query = None
        if self.start_calls:
            query = self.start_calls[-1].get("QueryString")
        return {
            "QueryExecution": {
                "QueryExecutionId": execution_id,
                "WorkGroup": self.workgroup,
                "QueryExecutionContext": {"Database": self.database},
                "Status": {"State": state},
                "ResultConfiguration": {
                    "OutputLocation": f"{self.results_prefix}{execution_id}.csv"
                },
                "Statistics": {"DataScannedInBytes": 4096},
                "Query": query,
            }
        }

    def get_query_results(self, **kwargs: Any) -> dict[str, Any]:
        self.get_results_calls.append(kwargs)
        if self._page_index >= len(self.pages):
            raise AssertionError("unexpected GetQueryResults page")
        page = self.pages[self._page_index]
        self._page_index += 1
        return page

    def stop_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.stop_calls.append(kwargs)
        return {}


def deployment_config(**overrides: Any) -> Sprint4HistoricalDeploymentConfig:
    values = {
        "workgroup": WORKGROUP,
        "database": DATABASE,
        "expected_results_prefix": RESULTS_PREFIX,
        "coverage_bucket_name": FACTS_BUCKET,
    }
    values.update(overrides)
    return Sprint4HistoricalDeploymentConfig(**values)


def host_window(**overrides: Any) -> HostAuthorizedHistoricalWindow:
    values = {
        "eligible_start_utc": ELIGIBLE[0],
        "eligible_end_utc": ELIGIBLE[1],
        "unbound_plan_start_utc": PLAN[0],
        "unbound_plan_end_utc": PLAN[1],
        "as_of_utc": AS_OF,
    }
    values.update(overrides)
    return HostAuthorizedHistoricalWindow(**values)


def _column_info(columns: tuple[str, ...]) -> list[dict[str, str]]:
    return [{"Name": name, "Type": "varchar"} for name in columns]


def _header_row(columns: tuple[str, ...]) -> dict[str, Any]:
    return {"Data": [{"VarCharValue": name} for name in columns]}


def _data_row(columns: tuple[str, ...], values: dict[str, str | None]) -> dict[str, Any]:
    cells: list[dict[str, str]] = []
    for name in columns:
        value = values[name]
        if value is None:
            cells.append({})
        else:
            cells.append({"VarCharValue": value})
    return {"Data": cells}


def athena_page(
    columns: tuple[str, ...],
    rows: list[dict[str, str | None]],
) -> dict[str, Any]:
    page_rows = [_header_row(columns), *[_data_row(columns, row) for row in rows]]
    return {
        "ResultSet": {
            "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
            "Rows": page_rows,
        }
    }


def encounter_summary_row(physical: str = "2") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_encounter_count": "0" if zero else "2",
        "distinct_aircraft_count": "0" if zero else "1",
        "distinct_hazard_count": "0" if zero else "1",
        "distinct_dedup_count": "0" if zero else "2",
        "min_event_time_utc": None if zero else "2026-09-11T10:00:00Z",
        "max_event_time_utc": None if zero else "2026-09-11T10:30:00Z",
    }


def risk_summary_row(physical: str = "2") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_risk_count": "0" if zero else "2",
        "distinct_encounter_count": "0" if zero else "1",
        "distinct_aircraft_count": "0" if zero else "1",
        "min_risk_score": None if zero else "1",
        "max_risk_score": None if zero else "4",
    }


def risk_distribution_row(level: str = "HIGH", physical: str = "2") -> dict[str, str | None]:
    return {"risk_level": level, "physical_record_count": physical}


def hazard_summary_row(physical: str = "1") -> dict[str, str | None]:
    zero = physical == "0"
    return {
        "physical_record_count": physical,
        "distinct_hazard_count": "0" if zero else "1",
        "distinct_hazard_version_count": "0" if zero else "1",
        "min_materialized_at_utc": None if zero else "2026-09-11T10:00:00Z",
        "max_materialized_at_utc": None if zero else "2026-09-11T10:05:00Z",
    }


def list_encounter_row(suffix: str = "v1", event_time: str = "2026-09-11T10:00:00Z") -> dict[str, str | None]:
    identity = f"proj-1#hazard-1#{suffix}"
    return {
        "encounter_id": identity,
        "record_id": identity,
        "dedup_id": f"{identity}|ENCOUNTER_OBSERVED|1700000000|DETECTED",
        "aircraft_id": "abc123",
        "hazard_id": "hazard-1",
        "hazard_version_key": f"hazard-1#{suffix}",
        "fact_kind": "ENCOUNTER_OBSERVED",
        "encounter_state": "DETECTED",
        "event_time_utc": event_time,
        "hazard_type": "CONVECTION",
    }


def encounter_success_pages() -> list[dict[str, Any]]:
    return [
        athena_page(ENCOUNTER_SUMMARY_OUTPUT_COLUMNS, [encounter_summary_row()]),
    ]


def encounter_zero_pages() -> list[dict[str, Any]]:
    return [
        athena_page(ENCOUNTER_SUMMARY_OUTPUT_COLUMNS, [encounter_summary_row("0")]),
    ]


def risk_success_pages() -> list[dict[str, Any]]:
    return [
        athena_page(RISK_SUMMARY_OUTPUT_COLUMNS, [risk_summary_row()]),
        athena_page(
            RISK_LEVEL_DISTRIBUTION_OUTPUT_COLUMNS,
            [risk_distribution_row()],
        ),
    ]


def hazard_success_pages() -> list[dict[str, Any]]:
    return [
        athena_page(HAZARD_VERSION_SUMMARY_OUTPUT_COLUMNS, [hazard_summary_row()]),
    ]


def list_success_pages(*, rows: int = 1) -> list[dict[str, Any]]:
    records = [
        list_encounter_row(suffix=f"v{index + 1}", event_time=f"2026-09-11T10:0{index}:00Z")
        for index in range(rows)
    ]
    return [athena_page(LIST_ENCOUNTER_OUTPUT_COLUMNS, records)]


def seed_evaluable(client: FakeHistoricalS3) -> None:
    start, end = ELIGIBLE
    enabled_at = "2026-09-11T00:00:00Z"
    client.put(
        f"metadata/epoch/{EPOCH_ID}.json",
        CollectionEpochRecord(
            control_schema_version=CONTROL_SCHEMA_VERSION,
            record_type=ControlRecordType.COLLECTION_EPOCH,
            collection_epoch_id=EPOCH_ID,
            bucket_name=FACTS_BUCKET,
            created_at_utc="2026-09-01T00:00:00Z",
            dedup_id=f"epoch|{EPOCH_ID}",
        ).to_dict(),
    )
    client.put(
        f"metadata/activation/year=2026/month=09/day=11/{EPOCH_ID}.json",
        CollectionActivationRecord(
            control_schema_version=CONTROL_SCHEMA_VERSION,
            record_type=ControlRecordType.COLLECTION_ACTIVATION,
            collection_epoch_id=EPOCH_ID,
            enabled_at_utc=enabled_at,
            created_at_utc=enabled_at,
            dedup_id=f"activation|{EPOCH_ID}|{enabled_at}",
        ).to_dict(),
    )
    client.put(
        f"metadata/coverage/stream=facts/year=2026/month=09/day=11/{EPOCH_ID}.json",
        CoverageIntervalRecord(
            control_schema_version=CONTROL_SCHEMA_VERSION,
            record_type=ControlRecordType.COVERAGE_INTERVAL,
            collection_epoch_id=EPOCH_ID,
            stream=CoverageStream.FACTS,
            interval_start_utc=start,
            interval_end_utc=end,
            created_at_utc=end,
            dedup_id=f"coverage|{EPOCH_ID}|facts|{start}",
        ).to_dict(),
    )


def seed_gap(client: FakeHistoricalS3) -> None:
    seed_evaluable(client)
    start, end = ELIGIBLE
    client.put(
        "metadata/gaps/year=2026/month=09/day=11/gap.json",
        CollectionGapRecord(
            control_schema_version=CONTROL_SCHEMA_VERSION,
            record_type=ControlRecordType.COLLECTION_GAP,
            collection_epoch_id=EPOCH_ID,
            affected_datasets=("encounter",),
            gap_domain=GapDomain.DOMAIN_1,
            reason="PRODUCER_PUT_EVENTS_FAILURE",
            uncertainty_class=UncertaintyClass.KNOWN_MISSING,
            recovery_state=RecoveryState.OPEN,
            detected_at_utc=start,
            interval_start_utc=start,
            interval_end_utc=end,
            created_at_utc=start,
            dedup_id="gap-sprint4",
        ).to_dict(),
    )


def compose_stack(
    *,
    athena: ScriptedHistoricalAthena | None = None,
    s3: FakeHistoricalS3 | None = None,
    pages: list[dict[str, Any]] | None = None,
    seed: bool = True,
    clock: FakeClock | None = None,
    config: Sprint4HistoricalDeploymentConfig | None = None,
):
    config = config or deployment_config()
    athena = athena or ScriptedHistoricalAthena(pages or encounter_success_pages())
    s3 = s3 or FakeHistoricalS3()
    if seed:
        seed_evaluable(s3)
    clock = clock or FakeClock()
    runtime = compose_sprint4_historical_query_runtime(
        athena_client=athena,
        s3_client=s3,
        config=config,
        monotonic_clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    port = bind_sprint4_historical_tool_port(runtime)
    return runtime, port, athena, s3, clock
