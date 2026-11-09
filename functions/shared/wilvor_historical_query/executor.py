"""Bounded Athena executor for closed historical queries.

The caller injects an Athena client. This module does not create
network or AWS SDK clients. Coverage and VERIFIED_ZERO belong to later
subphases.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from .errors import AthenaExecutorError, AthenaExecutorErrorCode
from .query_registry import InternalQueryId, RenderedQuery, render_fixed_query
from .query_sql import HistoricalQueryRenderError


ATHENA_STATE_QUEUED = "QUEUED"
ATHENA_STATE_RUNNING = "RUNNING"
ATHENA_STATE_SUCCEEDED = "SUCCEEDED"
ATHENA_STATE_FAILED = "FAILED"
# GetQueryExecution spelling. EventBridge query-state events use CANCELED.
ATHENA_STATE_CANCELLED = "CANCELLED"

_ACTIVE_STATES = frozenset({ATHENA_STATE_QUEUED, ATHENA_STATE_RUNNING})
_QUERY_EXECUTION_ID_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-"
)
_CONFIG_TOKEN_CHARACTERS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)

DEFAULT_TIMEOUT_SECONDS = 180
DEFAULT_POLL_INTERVAL_SECONDS = 2


class AthenaClient(Protocol):
    """Minimal Athena surface. Compatible with a later injected SDK client."""

    def start_query_execution(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_query_execution(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def get_query_results(self, **kwargs: Any) -> Mapping[str, Any]: ...

    def stop_query_execution(self, **kwargs: Any) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class AthenaExecutorConfig:
    workgroup: str
    database: str
    expected_results_prefix: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        workgroup = _require_config_token(self.workgroup, "workgroup")
        database = _require_config_token(self.database, "database")
        prefix = _normalize_results_prefix(self.expected_results_prefix)
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, int)
            or self.timeout_seconds < 1
        ):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "invalid timeout_seconds",
            )
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not isinstance(self.poll_interval_seconds, int)
            or self.poll_interval_seconds < 1
            or self.poll_interval_seconds > self.timeout_seconds
        ):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "invalid poll_interval_seconds",
            )
        object.__setattr__(self, "workgroup", workgroup)
        object.__setattr__(self, "database", database)
        object.__setattr__(self, "expected_results_prefix", prefix)


@dataclass(frozen=True)
class AthenaQueryResult:
    query_id: str
    query_execution_id: str
    workgroup: str
    database: str
    output_location: str
    rows: tuple[Mapping[str, str | None], ...]
    rows_returned: int
    data_scanned_bytes: int | None

    def __post_init__(self) -> None:
        frozen_rows = tuple(MappingProxyType(dict(row)) for row in self.rows)
        object.__setattr__(self, "rows", frozen_rows)
        if self.rows_returned != len(self.rows):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "rows_returned does not match data rows",
                query_id=self.query_id,
                query_execution_id=self.query_execution_id,
            )


def _require_config_token(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            f"missing {field_name}",
        )
    text = value.strip()
    if any(character not in _CONFIG_TOKEN_CHARACTERS for character in text):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            f"invalid {field_name}",
        )
    return text


def _normalize_results_prefix(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            "missing expected_results_prefix",
        )
    prefix = value.strip().rstrip("/") + "/"
    if not prefix.startswith("s3://"):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            "expected_results_prefix must be an s3 URI ending in /",
        )
    rest = prefix[len("s3://") :]
    if "//" in rest:
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            "expected_results_prefix must be an s3 URI ending in /",
        )
    bucket, separator, key = rest.partition("/")
    if not separator or not bucket or not key.endswith("athena-results/"):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            "expected_results_prefix must be the derived athena-results/ location",
        )
    if not prefix.endswith("/athena-results/"):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.INVALID_REQUEST,
            "expected_results_prefix must be the derived athena-results/ location",
        )
    return prefix


def _require_query_execution_id(value: Any, *, query_id: str | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "missing QueryExecutionId",
            query_id=query_id,
        )
    text = value.strip()
    if any(character not in _QUERY_EXECUTION_ID_CHARACTERS for character in text):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid QueryExecutionId",
            query_id=query_id,
        )
    return text


class AthenaExecutor:
    """Execute one closed historical query through an injected Athena client."""

    def __init__(
        self,
        *,
        athena_client: AthenaClient,
        config: AthenaExecutorConfig,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        if athena_client is None:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "missing athena_client",
            )
        if not isinstance(config, AthenaExecutorConfig):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "invalid executor config",
            )
        self._client = athena_client
        self._config = config
        self._monotonic = monotonic_clock or time.monotonic
        self._sleep = sleeper or time.sleep

    def execute_fixed(
        self,
        *,
        query_id: InternalQueryId,
        request: Any,
    ) -> AthenaQueryResult:
        if not isinstance(query_id, InternalQueryId):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "executor accepts only a closed internal query identity",
            )
        if isinstance(request, RenderedQuery) or isinstance(request, str):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "executor does not accept SQL or a caller-built rendered query",
            )
        try:
            rendered = render_fixed_query(query_id, request)
        except HistoricalQueryRenderError as exc:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.INVALID_REQUEST,
                "fixed query does not belong to the supplied request",
                query_id=query_id.value if isinstance(query_id, InternalQueryId) else None,
            ) from exc
        return self._execute_rendered(rendered)

    def _execute_rendered(self, query: RenderedQuery) -> AthenaQueryResult:
        query_id = query.query_id.value
        execution_id = self._start(query)
        snapshot = self._poll_until_terminal(query_id, execution_id)
        state = snapshot["state"]
        if state == ATHENA_STATE_FAILED:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.QUERY_FAILED,
                "Athena query failed",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=state,
                athena_reason=snapshot["reason"],
            )
        if state == ATHENA_STATE_CANCELLED:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.QUERY_CANCELED,
                "Athena query cancelled",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=state,
                athena_reason=snapshot["reason"],
            )
        if state != ATHENA_STATE_SUCCEEDED:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.UNKNOWN_QUERY_STATE,
                "unknown Athena query state",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=state,
                athena_reason=snapshot["reason"],
            )
        self._verify_boundary(query_id, execution_id, snapshot, expected_sql=query.sql)
        rows = self._fetch_rows(query, execution_id)
        return AthenaQueryResult(
            query_id=query_id,
            query_execution_id=execution_id,
            workgroup=snapshot["workgroup"],
            database=snapshot["database"],
            output_location=snapshot["output_location"],
            rows=rows,
            rows_returned=len(rows),
            data_scanned_bytes=snapshot["data_scanned_bytes"],
        )

    def _start(self, query: RenderedQuery) -> str:
        query_id = query.query_id.value
        try:
            response = self._client.start_query_execution(
                QueryString=query.sql,
                QueryExecutionContext={"Database": self._config.database},
                WorkGroup=self._config.workgroup,
                ResultReuseConfiguration={
                    "ResultReuseByAgeConfiguration": {"Enabled": False}
                },
            )
        except AthenaExecutorError:
            raise
        except Exception as exc:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.START_FAILED,
                "StartQueryExecution failed",
                query_id=query_id,
            ) from exc
        if not isinstance(response, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "malformed StartQueryExecution response",
                query_id=query_id,
            )
        return _require_query_execution_id(
            response.get("QueryExecutionId"),
            query_id=query_id,
        )

    def _poll_until_terminal(
        self,
        query_id: str,
        execution_id: str,
    ) -> dict[str, Any]:
        deadline = self._monotonic() + self._config.timeout_seconds
        while True:
            snapshot = self._get_execution(query_id, execution_id)
            if snapshot["state"] not in _ACTIVE_STATES:
                return snapshot
            remaining = deadline - self._monotonic()
            if remaining <= 0:
                self._stop_for_timeout(query_id, execution_id)
                raise AthenaExecutorError(
                    AthenaExecutorErrorCode.QUERY_TIMEOUT,
                    "Athena query timed out",
                    query_id=query_id,
                    query_execution_id=execution_id,
                    athena_state=snapshot["state"],
                    athena_reason=snapshot["reason"],
                )
            self._sleep(min(self._config.poll_interval_seconds, remaining))

    def _stop_for_timeout(self, query_id: str, execution_id: str) -> None:
        try:
            self._client.stop_query_execution(QueryExecutionId=execution_id)
        except AthenaExecutorError:
            raise
        except Exception as exc:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.QUERY_TIMEOUT,
                "Athena query timed out",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_RUNNING,
                stop_failed=True,
            ) from exc

    def _get_execution(self, query_id: str, execution_id: str) -> dict[str, Any]:
        try:
            response = self._client.get_query_execution(
                QueryExecutionId=execution_id
            )
        except AthenaExecutorError:
            raise
        except Exception as exc:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.API_ERROR,
                "GetQueryExecution failed",
                query_id=query_id,
                query_execution_id=execution_id,
            ) from exc
        if not isinstance(response, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "malformed GetQueryExecution response",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        execution = response.get("QueryExecution")
        if not isinstance(execution, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "missing QueryExecution",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        status = execution.get("Status")
        if not isinstance(status, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "missing QueryExecution.Status",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        state = status.get("State")
        if not isinstance(state, str) or not state.strip():
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "missing Athena state",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        reason = status.get("StateChangeReason")
        if reason is not None and not isinstance(reason, str):
            reason = None
        context = execution.get("QueryExecutionContext")
        database = None
        if isinstance(context, Mapping):
            database = context.get("Database")
        result_configuration = execution.get("ResultConfiguration")
        output_location = None
        if isinstance(result_configuration, Mapping):
            output_location = result_configuration.get("OutputLocation")
        statistics = execution.get("Statistics")
        data_scanned = None
        if isinstance(statistics, Mapping) and "DataScannedInBytes" in statistics:
            scanned = statistics["DataScannedInBytes"]
            if isinstance(scanned, bool) or not isinstance(scanned, int) or scanned < 0:
                raise AthenaExecutorError(
                    AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                    "invalid DataScannedInBytes",
                    query_id=query_id,
                    query_execution_id=execution_id,
                    athena_state=state.strip(),
                )
            data_scanned = scanned
        reported_id = execution.get("QueryExecutionId")
        if reported_id is not None and reported_id != execution_id:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "QueryExecutionId mismatch",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=state.strip(),
            )
        submitted_query = execution.get("Query")
        if submitted_query is not None and not isinstance(submitted_query, str):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "invalid Athena Query text",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=state.strip(),
            )
        return {
            "state": state.strip(),
            "reason": reason.strip() if isinstance(reason, str) and reason.strip() else None,
            "workgroup": execution.get("WorkGroup"),
            "database": database,
            "output_location": output_location,
            "data_scanned_bytes": data_scanned,
            "submitted_query": submitted_query,
        }

    def _verify_boundary(
        self,
        query_id: str,
        execution_id: str,
        snapshot: Mapping[str, Any],
        *,
        expected_sql: str,
    ) -> None:
        if snapshot["workgroup"] != self._config.workgroup:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.WORKGROUP_MISMATCH,
                "Athena workgroup mismatch",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_SUCCEEDED,
            )
        if snapshot["database"] != self._config.database:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.DATABASE_MISMATCH,
                "Athena database mismatch",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_SUCCEEDED,
            )
        output_location = snapshot["output_location"]
        if not isinstance(output_location, str) or not output_location.strip():
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.OUTPUT_LOCATION_INVALID,
                "missing OutputLocation",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_SUCCEEDED,
            )
        location = output_location.strip()
        if not location.startswith(self._config.expected_results_prefix):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.OUTPUT_LOCATION_INVALID,
                "OutputLocation is outside the controlled results prefix",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_SUCCEEDED,
            )
        submitted_query = snapshot.get("submitted_query")
        if submitted_query is not None and submitted_query != expected_sql:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "Athena Query does not match the closed renderer",
                query_id=query_id,
                query_execution_id=execution_id,
                athena_state=ATHENA_STATE_SUCCEEDED,
            )

    def _fetch_rows(
        self,
        query: RenderedQuery,
        execution_id: str,
    ) -> tuple[Mapping[str, str | None], ...]:
        query_id = query.query_id.value
        expected = query.output_columns
        rows: list[Mapping[str, str | None]] = []
        next_token: str | None = None
        first_page = True
        while True:
            page = self._get_results_page(query_id, execution_id, next_token)
            metadata_names = _column_names(page, query_id, execution_id)
            if first_page:
                if metadata_names != expected:
                    raise AthenaExecutorError(
                        AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                        "Athena result columns do not match the fixed query",
                        query_id=query_id,
                        query_execution_id=execution_id,
                    )
            elif metadata_names is not None and metadata_names != expected:
                raise AthenaExecutorError(
                    AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                    "Athena result columns do not match the fixed query",
                    query_id=query_id,
                    query_execution_id=execution_id,
                )
            page_rows = _result_rows(page, query_id, execution_id)
            if first_page:
                if not page_rows:
                    raise AthenaExecutorError(
                        AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                        "Athena result page is missing the header row",
                        query_id=query_id,
                        query_execution_id=execution_id,
                    )
                header_values = _parse_header_values(
                    page_rows[0],
                    expected,
                    query_id,
                    execution_id,
                )
                if header_values != expected:
                    raise AthenaExecutorError(
                        AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                        "Athena header row does not match the fixed query",
                        query_id=query_id,
                        query_execution_id=execution_id,
                    )
                page_rows = page_rows[1:]
            for raw in page_rows:
                parsed = _parse_data_row(raw, expected, query_id, execution_id)
                if len(rows) >= query.max_data_rows:
                    raise AthenaExecutorError(
                        AthenaExecutorErrorCode.RESULT_LIMIT_EXCEEDED,
                        "Athena result exceeded the code-owned row bound",
                        query_id=query_id,
                        query_execution_id=execution_id,
                    )
                rows.append(parsed)
            next_token = page.get("NextToken")
            if next_token is None or next_token == "":
                return tuple(rows)
            if not isinstance(next_token, str) or not next_token.strip():
                raise AthenaExecutorError(
                    AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                    "invalid NextToken",
                    query_id=query_id,
                    query_execution_id=execution_id,
                )
            if len(rows) >= query.max_data_rows:
                raise AthenaExecutorError(
                    AthenaExecutorErrorCode.RESULT_LIMIT_EXCEEDED,
                    "Athena result exceeded the code-owned row bound",
                    query_id=query_id,
                    query_execution_id=execution_id,
                )
            first_page = False

    def _get_results_page(
        self,
        query_id: str,
        execution_id: str,
        next_token: str | None,
    ) -> Mapping[str, Any]:
        kwargs: dict[str, Any] = {"QueryExecutionId": execution_id}
        if next_token is not None:
            kwargs["NextToken"] = next_token
        try:
            response = self._client.get_query_results(**kwargs)
        except AthenaExecutorError:
            raise
        except Exception as exc:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.API_ERROR,
                "GetQueryResults failed",
                query_id=query_id,
                query_execution_id=execution_id,
            ) from exc
        if not isinstance(response, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "malformed GetQueryResults response",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        return response


def _column_names(
    page: Mapping[str, Any],
    query_id: str,
    execution_id: str,
) -> tuple[str, ...] | None:
    result_set = page.get("ResultSet")
    if not isinstance(result_set, Mapping):
        return None
    metadata = result_set.get("ResultSetMetadata")
    if not isinstance(metadata, Mapping):
        return None
    columns = metadata.get("ColumnInfo")
    if not isinstance(columns, list):
        return None
    names: list[str] = []
    seen: set[str] = set()
    for column in columns:
        if not isinstance(column, Mapping) or not isinstance(column.get("Name"), str):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                "invalid Athena column metadata",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        name = column["Name"]
        if name in seen:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                "duplicate Athena result column",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        seen.add(name)
        names.append(name)
    return tuple(names)


def _result_rows(
    page: Mapping[str, Any],
    query_id: str,
    execution_id: str,
) -> list[Any]:
    result_set = page.get("ResultSet")
    if not isinstance(result_set, Mapping):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "missing ResultSet",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    rows = result_set.get("Rows")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid ResultSet.Rows",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    return rows


def _parse_header_values(
    raw: Any,
    expected: tuple[str, ...],
    query_id: str,
    execution_id: str,
) -> tuple[str, ...]:
    if not isinstance(raw, Mapping):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid Athena header row",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    cells = raw.get("Data")
    if not isinstance(cells, list):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid Athena header row",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    if len(cells) != len(expected):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
            "Athena header row does not match the fixed query",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    values: list[str] = []
    for cell in cells:
        if not isinstance(cell, Mapping):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "invalid Athena header cell",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        if "VarCharValue" not in cell or cell["VarCharValue"] is None:
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.RESULT_SCHEMA_MISMATCH,
                "Athena header cell is NULL",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        value = cell["VarCharValue"]
        if not isinstance(value, str):
            raise AthenaExecutorError(
                AthenaExecutorErrorCode.MALFORMED_RESPONSE,
                "invalid Athena header cell",
                query_id=query_id,
                query_execution_id=execution_id,
            )
        values.append(value)
    return tuple(values)


def _parse_data_row(
    raw: Any,
    expected: tuple[str, ...],
    query_id: str,
    execution_id: str,
) -> Mapping[str, str | None]:
    if not isinstance(raw, Mapping):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid Athena result row",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    cells = raw.get("Data")
    if not isinstance(cells, list) or len(cells) != len(expected):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "Athena result row does not match the column contract",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    parsed: dict[str, str | None] = {}
    for name, cell in zip(expected, cells, strict=True):
        parsed[name] = _parse_cell(cell, query_id, execution_id)
    return MappingProxyType(parsed)


def _parse_cell(cell: Any, query_id: str, execution_id: str) -> str | None:
    if not isinstance(cell, Mapping):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid Athena result cell",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    if "VarCharValue" not in cell:
        return None
    value = cell["VarCharValue"]
    if value is None:
        return None
    if not isinstance(value, str):
        raise AthenaExecutorError(
            AthenaExecutorErrorCode.MALFORMED_RESPONSE,
            "invalid Athena VarCharValue",
            query_id=query_id,
            query_execution_id=execution_id,
        )
    return value
