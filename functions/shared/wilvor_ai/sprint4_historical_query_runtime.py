"""Sprint 4 Agent B: compose the reviewed historical analytics query runtime.

The four Historical Analytics tools already exist. This module wires them to
the closed Athena registry, bounded executor, S3 coverage store, and
coverage gate. Callers inject Athena and S3 doubles. A live SDK client is
never constructed here, and production query execution stays closed.

Trust boundary:

- Deployment workgroup, Glue database, Athena results prefix, and coverage
  bucket are constructor-bound. They are not tool arguments.
- ``AthenaExecutor.execute_fixed`` is the only execution entry. There is no
  raw-SQL path.
- Coverage remains the Phase 2A.1 evaluator's authority. Athena never
  establishes completeness or mint a verified zero from an empty page.
- Historical facts stay historical. ``as_of_utc`` is the coverage evaluation
  instant, not a current operational observation time.
- The unattached historical query IAM policy is not attached here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable, Mapping

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.historical_analytics import HISTORICAL_ANALYTICS_TOOLS
from wilvor_historical.query_windows import MAX_QUERY_WINDOW, MAX_QUERY_WINDOW_DAYS
from wilvor_historical_query.coverage_gate import CoverageGate
from wilvor_historical_query.coverage_store import CoverageStore, CoverageStoreConfig
from wilvor_historical_query.executor import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    AthenaExecutor,
    AthenaExecutorConfig,
)
from wilvor_historical_query.operations import HistoricalAnalyticsOperations
from wilvor_historical_query.query_registry import public_query_ids


SPRINT4_HISTORICAL_QUERY_RUNTIME_SCHEMA_VERSION = (
    "wilvor.ai.sprint4.historical_query_runtime.v1"
)
ADAPTER_ORIGIN = "sprint4.offline_historical_query"

PRODUCTION_HISTORICAL_ATHENA_ENABLED = False
LIVE_ATHENA_QUERY_PERMITTED = False
CALLER_SQL_PERMITTED = False
CALLER_WORKGROUP_PERMITTED = False
CALLER_OUTPUT_LOCATION_PERMITTED = False
HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS = False
EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO = False
COVERAGE_BLOCKED_IS_ZERO = False
UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT = False
HISTORICAL_QUERY_IAM_ATTACHED = False

_ATHENA_METHODS = (
    "start_query_execution",
    "get_query_execution",
    "get_query_results",
    "stop_query_execution",
)
_S3_METHODS = ("list_objects_v2", "get_object")
_LIVE_SDK_MARKERS = ("boto3", "botocore", "pyathena", "awswrangler")
_CATALOG_NAMES = frozenset(item.name for item in HISTORICAL_ANALYTICS_TOOLS)

SPRINT4_HISTORICAL_INTEGRATOR_SEAMS = (
    "Do not add start_utc or end_utc to SpecialistRequest. Bind tool windows "
    "to the host-eligible interval. That interval must sit inside the requested "
    "plan window and must not be copied from the unbound plan window.",
    "Do not add source_version or query_id to HistoricalSpecialistRunResult or "
    "SpecialistResult. source_version stays on SourceRecord. query_id stays on "
    "QueryExecutionTrace.",
    "Wire Sprint4HistoricalQueryRuntime.operations into the Historical "
    "specialist. DynamoDB QUERY is not historical fact retrieval.",
    "The 2B.5 historical analytics query IAM policy remains unattached. Attach "
    "it to the AI host role only in a reviewed change. Do not apply Terraform "
    "in this sprint.",
    "MasterSourceAuthenticity remains NOT_ESTABLISHED until the integration "
    "owner extends that enum. Offline Athena doubles are not source "
    "authentication.",
    "Historical facts must not be represented as current operational "
    "observations. Completeness is coverage evaluability, not freshness.",
)

SPRINT4_HISTORICAL_AWS_DEPENDENCIES = (
    "athena:StartQueryExecution, athena:GetQueryExecution, "
    "athena:GetQueryResults, athena:StopQueryExecution on the dedicated "
    "historical analytics workgroup only",
    "glue:GetDatabase on the historical facts catalog database; glue:GetTable "
    "on encounter, risk, and hazard_version only",
    "s3:GetObject and s3:ListBucket on the historical facts bucket under "
    "metadata/* for coverage control records",
    "s3:GetObject and s3:PutObject on s3://<results-bucket>/athena-results/ "
    "owned by the workgroup, not by the caller",
    "No Scan, no arbitrary SQL, no Glue partition mutation, no coverage "
    "control writes from this runtime",
)


class _OfflineAthenaSeal:
    """Process-local mark. A live SDK client cannot become this object."""


class _OfflineS3Seal:
    """Process-local mark. A live SDK client cannot become this object."""


_ATHENA_SEAL = _OfflineAthenaSeal()
_S3_SEAL = _OfflineS3Seal()


def _closed() -> None:
    if (
        PRODUCTION_HISTORICAL_ATHENA_ENABLED is not False
        or LIVE_ATHENA_QUERY_PERMITTED is not False
        or CALLER_SQL_PERMITTED is not False
        or CALLER_WORKGROUP_PERMITTED is not False
        or CALLER_OUTPUT_LOCATION_PERMITTED is not False
        or HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS is not False
        or EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO is not False
        or COVERAGE_BLOCKED_IS_ZERO is not False
        or UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT is not False
        or HISTORICAL_QUERY_IAM_ATTACHED is not False
    ):
        raise ContractValidationError("production_historical_query_not_enabled")
    if MAX_QUERY_WINDOW != timedelta(days=7) or MAX_QUERY_WINDOW_DAYS != 7:
        raise ContractValidationError("historical_window_ceiling_changed")
    if public_query_ids() != _CATALOG_NAMES:
        raise ContractValidationError("historical_catalog_registry_mismatch")


def _reject_live_sdk(client: object, *, kind: str) -> None:
    module = getattr(type(client), "__module__", "") or ""
    folded = f"{module}.{type(client).__name__}".casefold()
    if any(marker in folded for marker in _LIVE_SDK_MARKERS):
        raise ContractValidationError(f"live_{kind}_client_not_permitted")


def _require_methods(client: object, methods: tuple[str, ...], *, kind: str) -> None:
    if client is None:
        raise ContractValidationError(f"missing_{kind}_client")
    for name in methods:
        if not callable(getattr(client, name, None)):
            raise ContractValidationError(f"invalid_{kind}_client")


def _reuse_disabled(kwargs: Mapping[str, Any]) -> bool:
    reuse = kwargs.get("ResultReuseConfiguration")
    if not isinstance(reuse, Mapping):
        return False
    by_age = reuse.get("ResultReuseByAgeConfiguration")
    if not isinstance(by_age, Mapping):
        return False
    return by_age.get("Enabled") is False


@dataclass(frozen=True)
class Sprint4HistoricalDeploymentConfig:
    """Server-owned Athena/Glue/S3 bounds. Not historical request fields."""

    workgroup: str
    database: str
    expected_results_prefix: str
    coverage_bucket_name: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS

    def __post_init__(self) -> None:
        _closed()
        try:
            executor = AthenaExecutorConfig(
                workgroup=self.workgroup,
                database=self.database,
                expected_results_prefix=self.expected_results_prefix,
                timeout_seconds=self.timeout_seconds,
                poll_interval_seconds=self.poll_interval_seconds,
            )
            store = CoverageStoreConfig(bucket_name=self.coverage_bucket_name)
        except Exception as exc:
            raise ContractValidationError("invalid_historical_deployment_config") from exc
        object.__setattr__(self, "workgroup", executor.workgroup)
        object.__setattr__(self, "database", executor.database)
        object.__setattr__(self, "expected_results_prefix", executor.expected_results_prefix)
        object.__setattr__(self, "coverage_bucket_name", store.bucket_name)
        object.__setattr__(self, "timeout_seconds", executor.timeout_seconds)
        object.__setattr__(self, "poll_interval_seconds", executor.poll_interval_seconds)

    def athena_executor_config(self) -> AthenaExecutorConfig:
        return AthenaExecutorConfig(
            workgroup=self.workgroup,
            database=self.database,
            expected_results_prefix=self.expected_results_prefix,
            timeout_seconds=self.timeout_seconds,
            poll_interval_seconds=self.poll_interval_seconds,
        )

    def coverage_store_config(self) -> CoverageStoreConfig:
        return CoverageStoreConfig(bucket_name=self.coverage_bucket_name)


class Sprint4OfflineAthenaAdapter:
    """Sealed Athena double. Forwards only the four executor calls."""

    def __init__(
        self,
        inner: object,
        *,
        config: Sprint4HistoricalDeploymentConfig,
        _seal: object,
    ) -> None:
        _closed()
        if _seal is not _ATHENA_SEAL:
            raise ContractValidationError("offline_athena_client_required")
        _require_methods(inner, _ATHENA_METHODS, kind="athena")
        _reject_live_sdk(inner, kind="athena")
        self._inner = inner
        self._config = config
        self.start_calls: list[dict[str, Any]] = []
        self.get_execution_calls: list[dict[str, Any]] = []
        self.get_results_calls: list[dict[str, Any]] = []
        self.stop_calls: list[dict[str, Any]] = []
        self.live_athena_initiated = False

    def start_query_execution(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        if CALLER_SQL_PERMITTED is not False:
            raise ContractValidationError("caller_sql_not_permitted")
        if "ResultConfiguration" in kwargs or "OutputLocation" in kwargs:
            raise ContractValidationError("output_location_not_caller_selected")
        if kwargs.get("WorkGroup") != self._config.workgroup:
            raise ContractValidationError("workgroup_not_deployment_bound")
        context = kwargs.get("QueryExecutionContext")
        if not isinstance(context, Mapping) or context.get("Database") != self._config.database:
            raise ContractValidationError("database_not_deployment_bound")
        if not _reuse_disabled(kwargs):
            raise ContractValidationError("athena_result_reuse_not_disabled")
        query = kwargs.get("QueryString")
        if not isinstance(query, str) or not query.strip():
            raise ContractValidationError("missing_rendered_sql")
        self.start_calls.append(dict(kwargs))
        return self._inner.start_query_execution(**kwargs)

    def get_query_execution(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        self.get_execution_calls.append(dict(kwargs))
        return self._inner.get_query_execution(**kwargs)

    def get_query_results(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        self.get_results_calls.append(dict(kwargs))
        return self._inner.get_query_results(**kwargs)

    def stop_query_execution(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        self.stop_calls.append(dict(kwargs))
        return self._inner.stop_query_execution(**kwargs)


class Sprint4OfflineS3Adapter:
    """Sealed S3 double. Coverage metadata is read-only."""

    def __init__(
        self,
        inner: object,
        *,
        config: Sprint4HistoricalDeploymentConfig,
        _seal: object,
    ) -> None:
        _closed()
        if _seal is not _S3_SEAL:
            raise ContractValidationError("offline_s3_client_required")
        _require_methods(inner, _S3_METHODS, kind="s3")
        _reject_live_sdk(inner, kind="s3")
        self._inner = inner
        self._config = config
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []
        self.live_s3_initiated = False

    def list_objects_v2(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        if kwargs.get("Bucket") != self._config.coverage_bucket_name:
            raise ContractValidationError("coverage_bucket_not_deployment_bound")
        self.list_calls.append(dict(kwargs))
        return self._inner.list_objects_v2(**kwargs)

    def get_object(self, **kwargs: Any) -> Mapping[str, Any]:
        _closed()
        if kwargs.get("Bucket") != self._config.coverage_bucket_name:
            raise ContractValidationError("coverage_bucket_not_deployment_bound")
        self.get_calls.append(dict(kwargs))
        return self._inner.get_object(**kwargs)

    def put_object(self, **_kwargs: Any) -> Mapping[str, Any]:
        raise ContractValidationError("historical_coverage_writes_not_permitted")

    def delete_object(self, **_kwargs: Any) -> Mapping[str, Any]:
        raise ContractValidationError("historical_coverage_writes_not_permitted")


def bind_offline_athena_client(
    inner: object,
    *,
    config: Sprint4HistoricalDeploymentConfig,
) -> Sprint4OfflineAthenaAdapter:
    """Seal an in-memory Athena double. A live SDK client cannot pass."""

    return Sprint4OfflineAthenaAdapter(inner, config=config, _seal=_ATHENA_SEAL)


def bind_offline_s3_client(
    inner: object,
    *,
    config: Sprint4HistoricalDeploymentConfig,
) -> Sprint4OfflineS3Adapter:
    """Seal an in-memory S3 double. A live SDK client cannot pass."""

    return Sprint4OfflineS3Adapter(inner, config=config, _seal=_S3_SEAL)


def open_production_historical_athena(*_args: object, **_kwargs: object) -> None:
    """Production Athena stays closed. No client method is invoked."""

    _closed()
    raise ContractValidationError("production_historical_query_not_enabled")


def issue_production_historical_query(*_args: object, **_kwargs: object) -> None:
    """Production historical query issuance is not integrated."""

    _closed()
    raise ContractValidationError("production_historical_query_not_enabled")


class Sprint4HistoricalQueryRuntime:
    """Compose CoverageStore, CoverageGate, AthenaExecutor, and operations."""

    def __init__(
        self,
        *,
        athena: Sprint4OfflineAthenaAdapter,
        s3: Sprint4OfflineS3Adapter,
        config: Sprint4HistoricalDeploymentConfig,
        monotonic_clock: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        _closed()
        if type(athena) is not Sprint4OfflineAthenaAdapter:
            raise ContractValidationError("offline_athena_client_required")
        if type(s3) is not Sprint4OfflineS3Adapter:
            raise ContractValidationError("offline_s3_client_required")
        if not isinstance(config, Sprint4HistoricalDeploymentConfig):
            raise ContractValidationError("invalid_historical_deployment_config")
        if athena.live_athena_initiated is not False or s3.live_s3_initiated is not False:
            raise ContractValidationError("live_aws_not_initiated")
        self._athena = athena
        self._s3 = s3
        self._config = config
        store = CoverageStore(
            s3_client=s3,
            config=config.coverage_store_config(),
        )
        executor = AthenaExecutor(
            athena_client=athena,
            config=config.athena_executor_config(),
            monotonic_clock=monotonic_clock,
            sleeper=sleeper,
        )
        self._operations = HistoricalAnalyticsOperations(
            coverage_gate=CoverageGate(store=store),
            executor=executor,
        )

    @property
    def operations(self) -> HistoricalAnalyticsOperations:
        return self._operations

    @property
    def config(self) -> Sprint4HistoricalDeploymentConfig:
        return self._config

    @property
    def athena(self) -> Sprint4OfflineAthenaAdapter:
        return self._athena

    @property
    def s3(self) -> Sprint4OfflineS3Adapter:
        return self._s3

    @property
    def live_athena_initiated(self) -> bool:
        return self._athena.live_athena_initiated

    @property
    def live_s3_initiated(self) -> bool:
        return self._s3.live_s3_initiated

    @property
    def production_query_enabled(self) -> bool:
        return PRODUCTION_HISTORICAL_ATHENA_ENABLED


def compose_sprint4_historical_query_runtime(
    *,
    athena_client: object,
    s3_client: object,
    config: Sprint4HistoricalDeploymentConfig,
    monotonic_clock: Callable[[], float] | None = None,
    sleeper: Callable[[float], None] | None = None,
) -> Sprint4HistoricalQueryRuntime:
    """Build the offline historical query stack from sealed injected clients."""

    return Sprint4HistoricalQueryRuntime(
        athena=bind_offline_athena_client(athena_client, config=config),
        s3=bind_offline_s3_client(s3_client, config=config),
        config=config,
        monotonic_clock=monotonic_clock,
        sleeper=sleeper,
    )


__all__ = [
    "ADAPTER_ORIGIN",
    "CALLER_OUTPUT_LOCATION_PERMITTED",
    "CALLER_SQL_PERMITTED",
    "CALLER_WORKGROUP_PERMITTED",
    "COVERAGE_BLOCKED_IS_ZERO",
    "EMPTY_ATHENA_PAGE_IS_VERIFIED_ZERO",
    "HISTORICAL_FACTS_ARE_CURRENT_OBSERVATIONS",
    "HISTORICAL_QUERY_IAM_ATTACHED",
    "LIVE_ATHENA_QUERY_PERMITTED",
    "PRODUCTION_HISTORICAL_ATHENA_ENABLED",
    "SPRINT4_HISTORICAL_AWS_DEPENDENCIES",
    "SPRINT4_HISTORICAL_INTEGRATOR_SEAMS",
    "SPRINT4_HISTORICAL_QUERY_RUNTIME_SCHEMA_VERSION",
    "UNBOUND_PLAN_WINDOW_IS_TOOL_ARGUMENT",
    "Sprint4HistoricalDeploymentConfig",
    "Sprint4HistoricalQueryRuntime",
    "Sprint4OfflineAthenaAdapter",
    "Sprint4OfflineS3Adapter",
    "bind_offline_athena_client",
    "bind_offline_s3_client",
    "compose_sprint4_historical_query_runtime",
    "issue_production_historical_query",
    "open_production_historical_athena",
]
