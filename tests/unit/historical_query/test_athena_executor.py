"""Offline fake-client tests for the bounded Athena executor."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from wilvor_historical.query_contracts import (
    HistoricalOperation,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
)
from wilvor_historical_query import (
    AthenaExecutor,
    AthenaExecutorConfig,
    AthenaExecutorError,
    AthenaExecutorErrorCode,
    AthenaQueryResult,
    InternalQueryId,
    render_fixed_query,
    render_historical_operation,
)
from wilvor_historical_query.executor import (
    ATHENA_STATE_CANCELLED,
    ATHENA_STATE_FAILED,
    ATHENA_STATE_QUEUED,
    ATHENA_STATE_RUNNING,
    ATHENA_STATE_SUCCEEDED,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    AthenaClient,
)
from wilvor_historical_query.query_registry import RenderedQuery
from wilvor_historical.contracts import Dataset


WINDOW = ("2026-09-11T00:00:00Z", "2026-09-12T00:00:00Z")
WORKGROUP = "wilvor-dev-historical-analytics"
DATABASE = "wilvor_dev_historical"
RESULTS_BUCKET = "wilvor-dev-historical-athena-results-123456789012"
RESULTS_PREFIX = f"s3://{RESULTS_BUCKET}/athena-results/"
FACTS_BUCKET = "wilvor-dev-historical-facts-123456789012"
EXECUTION_ID = "11111111-2222-3333-4444-555555555555"


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAthenaClient:
    def __init__(
        self,
        *,
        states: list[str] | None = None,
        start_id: str | None = EXECUTION_ID,
        start_error: Exception | None = None,
        get_error: Exception | None = None,
        results: list[dict[str, Any]] | None = None,
        results_error: Exception | None = None,
        stop_error: Exception | None = None,
        workgroup: str = WORKGROUP,
        database: str = DATABASE,
        output_location: str | None = RESULTS_PREFIX + f"{EXECUTION_ID}.csv",
        data_scanned_bytes: int | None = 4096,
        include_data_scanned: bool = True,
        include_query: bool = True,
        query_text: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.states = list(states or [ATHENA_STATE_SUCCEEDED])
        self.start_id = start_id
        self.start_error = start_error
        self.get_error = get_error
        self.results = list(results or [])
        self.results_error = results_error
        self.stop_error = stop_error
        self.workgroup = workgroup
        self.database = database
        self.output_location = output_location
        self.data_scanned_bytes = data_scanned_bytes
        self.include_data_scanned = include_data_scanned
        self.include_query = include_query
        self.query_text = query_text
        self.reason = reason
        self.start_calls: list[dict[str, Any]] = []
        self.get_execution_calls: list[dict[str, Any]] = []
        self.get_results_calls: list[dict[str, Any]] = []
        self.stop_calls: list[dict[str, Any]] = []
        self._state_index = 0
        self._results_index = 0

    def start_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.start_calls.append(kwargs)
        if self.start_error is not None:
            raise self.start_error
        if self.start_id is None:
            return {}
        return {"QueryExecutionId": self.start_id}

    def get_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.get_execution_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        state = self.states[min(self._state_index, len(self.states) - 1)]
        self._state_index += 1
        execution: dict[str, Any] = {
            "QueryExecutionId": kwargs.get("QueryExecutionId"),
            "WorkGroup": self.workgroup,
            "QueryExecutionContext": {"Database": self.database},
            "Status": {"State": state},
        }
        if self.reason is not None:
            execution["Status"]["StateChangeReason"] = self.reason
        if self.output_location is not None:
            execution["ResultConfiguration"] = {
                "OutputLocation": self.output_location
            }
        if self.include_data_scanned:
            execution["Statistics"] = {
                "DataScannedInBytes": self.data_scanned_bytes
            }
        if self.include_query:
            if self.query_text is not None:
                execution["Query"] = self.query_text
            elif self.start_calls:
                execution["Query"] = self.start_calls[-1]["QueryString"]
        return {"QueryExecution": execution}

    def get_query_results(self, **kwargs: Any) -> dict[str, Any]:
        self.get_results_calls.append(kwargs)
        if self.results_error is not None:
            raise self.results_error
        if self._results_index >= len(self.results):
            raise AssertionError("unexpected GetQueryResults page")
        page = self.results[self._results_index]
        self._results_index += 1
        return page

    def stop_query_execution(self, **kwargs: Any) -> dict[str, Any]:
        self.stop_calls.append(kwargs)
        if self.stop_error is not None:
            raise self.stop_error
        return {}


def _config(**overrides: Any) -> AthenaExecutorConfig:
    values = {
        "workgroup": WORKGROUP,
        "database": DATABASE,
        "expected_results_prefix": RESULTS_PREFIX,
    }
    values.update(overrides)
    return AthenaExecutorConfig(**values)


def _executor(
    client: FakeAthenaClient,
    *,
    clock: FakeClock | None = None,
    config: AthenaExecutorConfig | None = None,
) -> AthenaExecutor:
    clock = clock or FakeClock()
    return AthenaExecutor(
        athena_client=client,
        config=config or _config(),
        monotonic_clock=clock.monotonic,
        sleeper=clock.sleep,
    )


def _summary_request() -> SummarizeHistoricalEncountersRequest:
    return SummarizeHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
    )


def _risk_request() -> SummarizeHistoricalRisksRequest:
    return SummarizeHistoricalRisksRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
    )


def _list_request(*, limit: int = 2) -> ListHistoricalEncountersRequest:
    return ListHistoricalEncountersRequest(
        start_utc=WINDOW[0],
        end_utc=WINDOW[1],
        aircraft_id="abc123",
        limit=limit,
    )


def _summary_query():
    return render_fixed_query(
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        _summary_request(),
    )


def _run_fixed(
    client: FakeAthenaClient,
    *,
    query_id: InternalQueryId = InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
    request: Any | None = None,
    clock: FakeClock | None = None,
    config: AthenaExecutorConfig | None = None,
):
    return _executor(client, clock=clock, config=config).execute_fixed(
        query_id=query_id,
        request=request or _summary_request(),
    )


def _run_rendered(
    client: FakeAthenaClient,
    query: RenderedQuery,
    *,
    clock: FakeClock | None = None,
    config: AthenaExecutorConfig | None = None,
):
    return _executor(client, clock=clock, config=config)._execute_rendered(query)


def _list_query(*, limit: int = 2) -> RenderedQuery:
    return render_fixed_query(
        InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
        _list_request(limit=limit),
    )


def _bounded_query(*, max_data_rows: int, columns: tuple[str, ...]) -> RenderedQuery:
    return RenderedQuery(
        query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        dataset=Dataset.ENCOUNTER,
        table_name="encounter",
        fact_schema_version="wilvor.historical.encounter_fact.v1",
        sql="SELECT physical_record_count FROM encounter",
        output_columns=columns,
        result_shape="test",
        max_data_rows=max_data_rows,
    )


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


def _page(
    columns: tuple[str, ...],
    rows: list[dict[str, Any]],
    *,
    next_token: str | None = None,
    include_metadata: bool = True,
) -> dict[str, Any]:
    result_set: dict[str, Any] = {"Rows": rows}
    if include_metadata:
        result_set["ResultSetMetadata"] = {"ColumnInfo": _column_info(columns)}
    page: dict[str, Any] = {"ResultSet": result_set}
    if next_token is not None:
        page["NextToken"] = next_token
    return page


def _summary_values() -> dict[str, str | None]:
    columns = _summary_query().output_columns
    return {name: "0" if "count" in name else "2026-09-11T00:00:00Z" for name in columns}


def test_athena_client_protocol_matches_injected_surface():
    names = {
        "start_query_execution",
        "get_query_execution",
        "get_query_results",
        "stop_query_execution",
    }
    assert names.issubset(set(dir(AthenaClient)))
    assert names.issubset(set(dir(FakeAthenaClient)))


def test_config_defaults_and_prefix_normalization():
    config = AthenaExecutorConfig(
        workgroup=WORKGROUP,
        database=DATABASE,
        expected_results_prefix=RESULTS_PREFIX.rstrip("/"),
    )
    assert config.timeout_seconds == DEFAULT_TIMEOUT_SECONDS == 180
    assert config.poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS == 2
    assert config.expected_results_prefix == RESULTS_PREFIX
    assert config.expected_results_prefix.endswith("/")


@pytest.mark.parametrize(
    "overrides",
    [
        {"workgroup": ""},
        {"workgroup": "bad workgroup"},
        {"database": ""},
        {"expected_results_prefix": ""},
        {"expected_results_prefix": "https://example.com/athena-results/"},
        {
            "expected_results_prefix": (
                f"s3://{RESULTS_BUCKET}/athena-results-evil/"
            )
        },
        {"expected_results_prefix": f"s3://{FACTS_BUCKET}/dataset=encounter/"},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"poll_interval_seconds": 0},
        {"poll_interval_seconds": 181},
    ],
)
def test_config_is_fail_closed(overrides: dict[str, Any]):
    with pytest.raises(AthenaExecutorError) as captured:
        _config(**overrides)
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST


def test_public_executor_accepts_only_fixed_query_id_and_typed_request():
    client = FakeAthenaClient()
    executor = _executor(client)
    public_methods = [
        name
        for name, value in inspect.getmembers(AthenaExecutor, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    assert public_methods == ["execute_fixed"]
    signature = inspect.signature(AthenaExecutor.execute_fixed)
    assert list(signature.parameters) == ["self", "query_id", "request"]
    assert not hasattr(AthenaExecutor, "execute")
    assert not hasattr(AthenaExecutor, "execute_sql")
    assert not hasattr(AthenaExecutor, "run_sql")
    assert not hasattr(AthenaExecutor, "execute_query_string")
    with pytest.raises(AthenaExecutorError) as captured:
        executor.execute_fixed(query_id="summarize_historical_encounters", request=_summary_request())  # type: ignore[arg-type]
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    with pytest.raises(AthenaExecutorError) as captured:
        executor.execute_fixed(
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            request="SELECT 1",
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    handmade = _bounded_query(max_data_rows=1, columns=("physical_record_count",))
    with pytest.raises(AthenaExecutorError) as captured:
        executor.execute_fixed(
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            request=handmade,  # type: ignore[arg-type]
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    assert client.start_calls == []


def test_start_payload_is_locked_to_config_and_rendered_sql():
    query = _summary_query()
    client = FakeAthenaClient(
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ]
    )
    result = _run_fixed(client)
    assert len(client.start_calls) == 1
    payload = client.start_calls[0]
    assert payload["QueryString"] == query.sql
    assert payload["QueryString"] == render_historical_operation(_summary_request()).queries[0].sql
    assert payload["WorkGroup"] == WORKGROUP
    assert payload["QueryExecutionContext"] == {"Database": DATABASE}
    assert payload["ResultReuseConfiguration"] == {
        "ResultReuseByAgeConfiguration": {"Enabled": False}
    }
    assert "ResultConfiguration" not in payload
    assert "OutputLocation" not in payload
    assert result.query_execution_id == EXECUTION_ID
    assert result.workgroup == WORKGROUP
    assert result.database == DATABASE
    assert result.output_location == RESULTS_PREFIX + f"{EXECUTION_ID}.csv"
    assert result.rows_returned == 1
    assert result.data_scanned_bytes == 4096
    assert result.query_id == query.query_id.value


def test_missing_query_execution_id_fails_closed():
    query = _summary_query()
    client = FakeAthenaClient(start_id=None)
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.MALFORMED_RESPONSE
    assert captured.value.query_id == query.query_id.value
    assert captured.value.query_execution_id is None
    assert client.get_execution_calls == []


def test_start_api_error_is_start_failed():
    query = _summary_query()
    client = FakeAthenaClient(start_error=RuntimeError("boom"))
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.START_FAILED
    assert query.sql not in str(captured.value)
    assert captured.value.query_id == query.query_id.value


def test_polls_queued_running_then_succeeded():
    query = _summary_query()
    client = FakeAthenaClient(
        states=[
            ATHENA_STATE_QUEUED,
            ATHENA_STATE_RUNNING,
            ATHENA_STATE_SUCCEEDED,
        ],
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ],
    )
    clock = FakeClock()
    result = _run_fixed(client, clock=clock)
    assert result.rows_returned == 1
    assert len(client.get_execution_calls) == 3
    assert clock.sleeps == [2, 2]
    assert all(
        call == {"QueryExecutionId": EXECUTION_ID}
        for call in client.get_execution_calls
    )
    assert client.stop_calls == []


def test_immediately_succeeded_does_not_sleep():
    query = _summary_query()
    client = FakeAthenaClient(
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ]
    )
    clock = FakeClock()
    _run_fixed(client, clock=clock)
    assert clock.sleeps == []
    assert len(client.get_execution_calls) == 1


def test_failed_state_is_query_failed():
    query = _summary_query()
    client = FakeAthenaClient(
        states=[ATHENA_STATE_FAILED],
        reason="SYNTAX_ERROR: boom",
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.QUERY_FAILED
    assert captured.value.athena_state == ATHENA_STATE_FAILED
    assert captured.value.athena_reason == "SYNTAX_ERROR: boom"
    assert captured.value.query_execution_id == EXECUTION_ID
    assert client.get_results_calls == []
    assert query.sql not in str(captured.value)


def test_cancelled_state_uses_get_query_execution_spelling():
    query = _summary_query()
    client = FakeAthenaClient(states=[ATHENA_STATE_CANCELLED], reason="stopped")
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.QUERY_CANCELED
    assert captured.value.athena_state == "CANCELLED"
    assert captured.value.athena_state != "CANCELED"
    assert client.get_results_calls == []


def test_eventbridge_canceled_spelling_is_unknown_state():
    query = _summary_query()
    client = FakeAthenaClient(states=["CANCELED"])
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.UNKNOWN_QUERY_STATE
    assert captured.value.athena_state == "CANCELED"
    assert client.get_results_calls == []


def test_unknown_state_fails_closed():
    query = _summary_query()
    client = FakeAthenaClient(states=["WEIRD"])
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.UNKNOWN_QUERY_STATE
    assert captured.value.athena_state == "WEIRD"
    assert client.get_results_calls == []


def test_timeout_stops_the_exact_query_execution():
    query = _summary_query()
    client = FakeAthenaClient(states=[ATHENA_STATE_QUEUED, ATHENA_STATE_RUNNING])
    clock = FakeClock()
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            client,
            clock=clock,
            config=_config(timeout_seconds=4, poll_interval_seconds=2),
        )
    assert captured.value.code is AthenaExecutorErrorCode.QUERY_TIMEOUT
    assert captured.value.query_execution_id == EXECUTION_ID
    assert captured.value.stop_failed is False
    assert client.stop_calls == [{"QueryExecutionId": EXECUTION_ID}]
    assert client.get_results_calls == []
    assert clock.sleeps == [2, 2]


def test_timeout_keeps_primary_outcome_when_stop_fails():
    query = _summary_query()
    client = FakeAthenaClient(
        states=[ATHENA_STATE_RUNNING],
        stop_error=RuntimeError("stop failed"),
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            client,
            config=_config(timeout_seconds=2, poll_interval_seconds=2),
        )
    assert captured.value.code is AthenaExecutorErrorCode.QUERY_TIMEOUT
    assert captured.value.stop_failed is True
    assert captured.value.query_execution_id == EXECUTION_ID
    assert client.stop_calls == [{"QueryExecutionId": EXECUTION_ID}]
    assert client.get_results_calls == []


def test_get_query_execution_api_error():
    query = _summary_query()
    client = FakeAthenaClient(get_error=RuntimeError("throttled"))
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.API_ERROR
    assert captured.value.query_execution_id == EXECUTION_ID
    assert client.get_results_calls == []


def test_workgroup_mismatch_does_not_fetch_results():
    query = _summary_query()
    client = FakeAthenaClient(workgroup="other-workgroup")
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.WORKGROUP_MISMATCH
    assert client.get_results_calls == []


def test_database_mismatch_does_not_fetch_results():
    query = _summary_query()
    client = FakeAthenaClient(database="other_database")
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.DATABASE_MISMATCH
    assert client.get_results_calls == []


@pytest.mark.parametrize(
    "output_location",
    [
        None,
        f"s3://{FACTS_BUCKET}/dataset=encounter/year=2026/month=09/day=11/",
        "s3://other-results-bucket/athena-results/qid.csv",
        f"s3://{RESULTS_BUCKET}/athena-results-evil/{EXECUTION_ID}.csv",
        "https://example.com/athena-results/qid.csv",
        "not-an-s3-uri",
        "",
    ],
)
def test_output_location_must_use_exact_derived_prefix(output_location: str | None):
    query = _summary_query()
    client = FakeAthenaClient(output_location=output_location)
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.OUTPUT_LOCATION_INVALID
    assert client.get_results_calls == []


def test_accepted_output_location_is_under_normalized_prefix():
    query = _summary_query()
    location = RESULTS_PREFIX + f"{EXECUTION_ID}.csv"
    client = FakeAthenaClient(
        output_location=location,
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ],
    )
    result = _run_fixed(client)
    assert result.output_location == location
    assert result.output_location.startswith(RESULTS_PREFIX)
    assert "/athena-results-evil/" not in result.output_location


def test_single_page_summary_header_is_not_counted():
    query = _summary_query()
    values = _summary_values()
    client = FakeAthenaClient(
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, values)],
            )
        ]
    )
    result = _run_fixed(client)
    assert result.rows_returned == 1
    assert len(result.rows) == 1
    assert result.rows[0]["physical_record_count"] == values["physical_record_count"]
    assert dict(result.rows[0]) == values


def test_multi_page_results_keep_later_page_first_data_row():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=3, columns=columns)
    first = {"record_id": "a", "hazard_id": "h1"}
    second = {"record_id": "b", "hazard_id": "h2"}
    third = {"record_id": "c", "hazard_id": None}
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [_header_row(columns), _data_row(columns, first)],
                next_token="page-2",
            ),
            _page(
                columns,
                [_data_row(columns, second), _data_row(columns, third)],
                include_metadata=False,
            ),
        ]
    )
    result = _run_rendered(client, query)
    assert [call.get("NextToken") for call in client.get_results_calls] == [
        None,
        "page-2",
    ]
    assert result.rows_returned == 3
    assert [dict(row) for row in result.rows] == [first, second, third]
    assert result.rows[2]["hazard_id"] is None


def test_null_cell_is_preserved_as_none():
    columns = ("physical_record_count", "min_event_time_utc")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [
                    _header_row(columns),
                    _data_row(
                        columns,
                        {"physical_record_count": "1", "min_event_time_utc": None},
                    ),
                ],
            )
        ]
    )
    result = _run_rendered(client, query)
    assert result.rows[0]["min_event_time_utc"] is None
    assert result.rows[0]["physical_record_count"] == "1"
    assert result.rows[0]["min_event_time_utc"] != ""
    assert result.rows[0]["min_event_time_utc"] != "NULL"


def test_exactly_max_data_rows_is_accepted():
    columns = ("record_id",)
    query = _bounded_query(max_data_rows=2, columns=columns)
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [
                    _header_row(columns),
                    _data_row(columns, {"record_id": "1"}),
                    _data_row(columns, {"record_id": "2"}),
                ],
            )
        ]
    )
    result = _run_rendered(client, query)
    assert result.rows_returned == 2


def test_max_data_rows_plus_one_is_rejected():
    columns = ("record_id",)
    query = _bounded_query(max_data_rows=2, columns=columns)
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [
                    _header_row(columns),
                    _data_row(columns, {"record_id": "1"}),
                    _data_row(columns, {"record_id": "2"}),
                    _data_row(columns, {"record_id": "3"}),
                ],
            )
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_LIMIT_EXCEEDED
    assert captured.value.query_execution_id == EXECUTION_ID


def test_next_token_after_exact_bound_is_rejected():
    columns = ("record_id",)
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [_header_row(columns), _data_row(columns, {"record_id": "1"})],
                next_token="more",
            )
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_LIMIT_EXCEEDED
    assert len(client.get_results_calls) == 1


def test_next_token_is_terminated_when_absent():
    columns = ("record_id",)
    query = _bounded_query(max_data_rows=2, columns=columns)
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [_header_row(columns), _data_row(columns, {"record_id": "1"})],
                next_token="page-2",
            ),
            _page(columns, [_data_row(columns, {"record_id": "2"})]),
        ]
    )
    result = _run_rendered(client, query)
    assert result.rows_returned == 2
    assert [call.get("NextToken") for call in client.get_results_calls] == [
        None,
        "page-2",
    ]
    assert len(client.get_results_calls) == 2


@pytest.mark.parametrize(
    "columns",
    [
        ("physical_record_count",),
        ("physical_record_count", "extra"),
        ("renamed_count", "min_event_time_utc"),
        ("min_event_time_utc", "physical_record_count"),
    ],
)
def test_column_metadata_mismatch_is_rejected(columns: tuple[str, ...]):
    expected = ("physical_record_count", "min_event_time_utc")
    query = _bounded_query(max_data_rows=1, columns=expected)
    values = {name: "1" for name in columns}
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [_header_row(columns), _data_row(columns, values)],
            )
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_duplicate_column_metadata_is_rejected():
    expected = ("physical_record_count", "min_event_time_utc")
    query = _bounded_query(max_data_rows=1, columns=expected)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {
                        "ColumnInfo": [
                            {"Name": "physical_record_count"},
                            {"Name": "physical_record_count"},
                        ]
                    },
                    "Rows": [_header_row(expected)],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_malformed_row_shape_fails_closed():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        _header_row(columns),
                        {"Data": [{"VarCharValue": "only-one-cell"}]},
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.MALFORMED_RESPONSE


def test_get_query_results_api_error():
    query = _summary_query()
    client = FakeAthenaClient(results_error=RuntimeError("results failed"))
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.API_ERROR
    assert captured.value.query_execution_id == EXECUTION_ID


def test_omitted_data_scanned_is_none_not_zero():
    query = _summary_query()
    client = FakeAthenaClient(
        include_data_scanned=False,
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ],
    )
    result = _run_fixed(client)
    assert result.data_scanned_bytes is None


def test_zero_data_scanned_is_preserved_when_athena_reports_it():
    query = _summary_query()
    client = FakeAthenaClient(
        data_scanned_bytes=0,
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ],
    )
    result = _run_fixed(client)
    assert result.data_scanned_bytes == 0


def test_list_query_uses_limit_plus_one_bound():
    query = _list_query(limit=2)
    assert query.max_data_rows == 3
    columns = query.output_columns
    empty = {name: "x" for name in columns}
    rows = [_header_row(columns)] + [_data_row(columns, empty) for _ in range(3)]
    client = FakeAthenaClient(results=[_page(columns, rows)])
    result = _run_fixed(
        client,
        query_id=InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
        request=_list_request(limit=2),
    )
    assert result.rows_returned == 3
    assert result.rows_returned == query.max_data_rows


def test_success_result_does_not_compute_domain_semantics():
    query = _summary_query()
    values = _summary_values()
    client = FakeAthenaClient(
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, values)],
            )
        ]
    )
    result = _run_fixed(client)
    assert isinstance(result, AthenaQueryResult)
    assert not hasattr(result, "semantic_match_count")
    assert not hasattr(result, "verified_zero")
    names = set(inspect.signature(AthenaQueryResult).parameters)
    assert "semantic_match_count" not in names


def test_executor_source_does_not_log_sql():
    source = inspect.getsource(AthenaExecutor.execute_fixed)
    assert "query.sql" not in source
    assert "QueryString" not in source


def test_wrong_request_type_fails_before_start():
    client = FakeAthenaClient()
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            client,
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            request=_risk_request(),
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    assert client.start_calls == []
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            client,
            query_id=InternalQueryId.LIST_HISTORICAL_ENCOUNTERS,
            request=_summary_request(),
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    assert client.start_calls == []


def test_internal_risk_level_query_requires_risk_request():
    columns = render_fixed_query(
        InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
        _risk_request(),
    ).output_columns
    values = {name: "HIGH" if name == "risk_level" else "1" for name in columns}
    client = FakeAthenaClient(
        results=[
            _page(
                columns,
                [_header_row(columns), _data_row(columns, values)],
            )
        ]
    )
    result = _run_fixed(
        client,
        query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
        request=_risk_request(),
    )
    assert result.query_id == InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value
    assert result.rows_returned == 1
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            FakeAthenaClient(),
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL,
            request=_summary_request(),
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    assert InternalQueryId.SUMMARIZE_HISTORICAL_RISKS_BY_LEVEL.value not in {
        item.value for item in HistoricalOperation
    }
    assert {item.value for item in HistoricalOperation} == {
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    }
    assert len(HistoricalOperation) == 4


def test_caller_cannot_override_closed_sql_or_config_through_request():
    request = _summary_request()
    rendered = render_fixed_query(
        InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        request,
    )
    client = FakeAthenaClient(
        results=[
            _page(
                rendered.output_columns,
                [
                    _header_row(rendered.output_columns),
                    _data_row(rendered.output_columns, _summary_values()),
                ],
            )
        ]
    )
    _run_fixed(client, request=request)
    payload = client.start_calls[0]
    assert payload["QueryString"] == rendered.sql
    assert "FROM encounter" in payload["QueryString"]
    assert payload["WorkGroup"] == WORKGROUP
    assert payload["QueryExecutionContext"]["Database"] == DATABASE
    assert payload["ResultReuseConfiguration"]["ResultReuseByAgeConfiguration"]["Enabled"] is False
    assert "ResultConfiguration" not in payload
    assert not hasattr(request, "sql")
    assert not hasattr(request, "table")
    assert not hasattr(request, "schema")
    assert not hasattr(request, "columns")
    assert not hasattr(request, "group_by")
    assert not hasattr(request, "order_by")
    assert not hasattr(request, "max_data_rows")
    assert not hasattr(request, "workgroup")
    assert not hasattr(request, "database")
    assert "LIMIT" not in inspect.signature(SummarizeHistoricalEncountersRequest).parameters


def test_athena_query_text_mismatch_fails_closed():
    query = _summary_query()
    client = FakeAthenaClient(
        query_text="SELECT 1",
        results=[
            _page(
                query.output_columns,
                [_header_row(query.output_columns), _data_row(query.output_columns, _summary_values())],
            )
        ],
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(client)
    assert captured.value.code is AthenaExecutorErrorCode.MALFORMED_RESPONSE
    assert query.sql not in str(captured.value)
    assert "SELECT 1" not in str(captured.value)
    assert client.get_results_calls == []


def test_valid_header_and_zero_data_rows():
    query = _summary_query()
    client = FakeAthenaClient(
        results=[_page(query.output_columns, [_header_row(query.output_columns)])]
    )
    result = _run_fixed(client)
    assert result.rows_returned == 0
    assert result.rows == ()
    assert not hasattr(result, "verified_zero")


def test_header_wrong_name_is_rejected():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    wrong = ("record_id", "other_id")
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        _header_row(wrong),
                        _data_row(columns, {"record_id": "1", "hazard_id": "h"}),
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_header_wrong_order_is_rejected():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        _header_row(("hazard_id", "record_id")),
                        _data_row(columns, {"record_id": "1", "hazard_id": "h"}),
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_header_missing_cell_is_rejected():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        {"Data": [{"VarCharValue": "record_id"}]},
                        _data_row(columns, {"record_id": "1", "hazard_id": "h"}),
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_header_extra_cell_is_rejected():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        {
                            "Data": [
                                {"VarCharValue": "record_id"},
                                {"VarCharValue": "hazard_id"},
                                {"VarCharValue": "extra"},
                            ]
                        }
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_header_null_cell_is_rejected():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=1, columns=columns)
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [
                        {"Data": [{"VarCharValue": "record_id"}, {}]},
                        _data_row(columns, {"record_id": "1", "hazard_id": "h"}),
                    ],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_first_row_of_data_is_not_silently_dropped():
    columns = ("record_id", "hazard_id")
    query = _bounded_query(max_data_rows=2, columns=columns)
    data = {"record_id": "kept-if-wrongly-dropped", "hazard_id": "h"}
    client = FakeAthenaClient(
        results=[
            {
                "ResultSet": {
                    "ResultSetMetadata": {"ColumnInfo": _column_info(columns)},
                    "Rows": [_data_row(columns, data)],
                }
            }
        ]
    )
    with pytest.raises(AthenaExecutorError) as captured:
        _run_rendered(client, query)
    assert captured.value.code is AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH


def test_hazard_request_is_incompatible_with_encounter_query():
    client = FakeAthenaClient()
    with pytest.raises(AthenaExecutorError) as captured:
        _run_fixed(
            client,
            query_id=InternalQueryId.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            request=SummarizeHistoricalHazardVersionsRequest(
                start_utc=WINDOW[0],
                end_utc=WINDOW[1],
            ),
        )
    assert captured.value.code is AthenaExecutorErrorCode.INVALID_REQUEST
    assert client.start_calls == []

