"""Sprint 3C Agent C: bounded, read-only AWS source adapters.

This module translates published allowlisted tool/resource/operation pairs
into ``GET_ITEM`` or bounded ``QUERY`` against logical specialist tables.
The AWS client is injected. The only client this process will call is a
sealed offline double. A live SDK client is never invoked because
production authorization is unavailable.

Trust boundary:

- Consumed-attempt fields from Agent B are a descriptive binding. They are
  not a production grant and are not minted here.
- ``HostReadInvocation`` stays ``NONPRODUCTION_TEST_ADAPTER``. Copying
  ``grant_nonce`` binds identity; it does not become a read permit.
- ``ReadReceiptCandidate.approved_source_adapter`` stays false.
- ``source_authenticity`` stays ``NOT_ESTABLISHED``.
- Empty pages do not mint a verified zero. Absent records are not LOW risk.
- Region labels, including California, are not SIGMET polygon overlap.

Reuse, not a second framework: ``resolve_master_authorized_scope``,
``check_master_read_resource``, ``server_read_*``, ``RuntimeBudgetSession``,
``MasterTrustedReadExecutor.execute``, ``raw_read_observation``, and
``MasterRuntimeReadJournal``.
"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    AgentCapability,
    ContractValidationError,
    QueryExecutionTrace,
    SourceRecord,
    _validate_bounded_text,
    _validate_utc,
)
from wilvor_ai.decision_contracts import StoredRiskLevel
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeDenial,
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    LocalTestHostScope,
    MasterAuthorizedScopeDecision,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    MasterReadDenialReason,
    MasterReadResourceCheck,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    seal_ingress_principal,
    seal_population_scope,
    server_read_argument_names,
    server_read_budget,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW
from wilvor_ai.master_runtime_budget import (
    BudgetDecision,
    RequestBudgetEnvelope,
    RuntimeBudgetSession,
    StartedTaskDisposition,
    offline_harness_seal,
    open_private_offline_harness,
)
from wilvor_ai.master_runtime_read_journal import (
    LocalFakeReadResult,
    MasterRuntimeReadJournal,
    PaginationObservation,
    ReadInvocation,
    RuntimeReadJournalEntry,
    mark_deterministic_local_fake,
)
from wilvor_ai.master_trusted_read_executor import (
    HostCancellation,
    HostReadInvocation,
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionHostClock,
    NonproductionReadPage,
    ReadBoundaryDenial,
    ReadExecutionError,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
    raw_read_observation,
)


SPRINT3C_SCOPED_READ_SCHEMA_VERSION = "wilvor.ai.sprint3c.scoped_read.v1"
ADAPTER_ORIGIN = "sprint3c.offline_aws"

PRODUCTION_SCOPED_AWS_READ_ENABLED = False
LIVE_AWS_OPERATION_PERMITTED = False
APPROVED_SOURCE_ADAPTER = False
EMPTY_PAGE_IS_VERIFIED_ZERO = False
ABSENT_RECORD_IS_LOW_RISK = False
REGION_LABEL_IS_SIGMET_POLYGON = False
CALLER_ARGUMENTS_WIDEN_AUTHORIZATION = False
CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT = False
LEDGER_EXECUTION_STATE_STARTED = "STARTED"
GRANT_CONSUMPTION_LIFECYCLE = "CONSUMED_LOCAL_HARNESS"

_SQL_ARGUMENT_NAMES = frozenset({"sql", "statement", "query_string", "FilterExpression"})
_WRITE_METHODS = frozenset(
    {
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "transact_write_items",
        "execute_statement",
        "execute_transaction",
    }
)
_RESOURCE_KEY_ATTRIBUTE = {
    "aircraft": "aircraft_id",
    "hazards": "hazard_id",
    "hazard_coordinates": "hazard_id",
    "encounters": "encounter_id",
    "airports": "airport_id",
    "metar": "airport_id",
    "taf": "airport_id",
    "recommendations": "recommendation_id",
    "airport_assessments": "recommendation_id",
    "historical.encounters": "aircraft_id",
    "historical.risks": "aircraft_id",
    "historical.hazard_versions": "hazard_id",
}
_QUERY_PARTITION = {
    "aircraft": "callsign",
    "hazards": "region",
    "hazard_coordinates": "hazard_id",
    "encounters": "aircraft_id",
    "airport_assessments": "recommendation_id",
    "historical.encounters": "aircraft_id",
    "historical.risks": "aircraft_id",
    "historical.hazard_versions": "hazard_id",
}
_SCOPE_TO_READ = {
    EffectiveScopeDenial.SCAN_NOT_PERMITTED: ReadBoundaryDenial.SCAN_NOT_PERMITTED,
    EffectiveScopeDenial.ALL_AIRCRAFT_SCAN_REFUSED: ReadBoundaryDenial.SCAN_NOT_PERMITTED,
    EffectiveScopeDenial.WILDCARD_RESOURCE: ReadBoundaryDenial.WILDCARD_RESOURCE,
    EffectiveScopeDenial.UNBOUNDED_HISTORICAL_WINDOW: ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,
    EffectiveScopeDenial.HISTORICAL_WINDOW_NOT_HOST_APPROVED: ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,
    EffectiveScopeDenial.DEADLINE_EXPIRED: ReadBoundaryDenial.DEADLINE_EXPIRED,
    EffectiveScopeDenial.CALLER_DEADLINE_NOT_AUTHORITY: ReadBoundaryDenial.CALLER_DEADLINE_NOT_AUTHORITY,
    EffectiveScopeDenial.CALLER_BUDGET_NOT_AUTHORITY: ReadBoundaryDenial.CALLER_BUDGET_NOT_AUTHORITY,
    EffectiveScopeDenial.CALLER_AUTHORIZED_NOT_AUTHORITY: ReadBoundaryDenial.CALLER_SCOPE_NOT_AUTHORITY,
    EffectiveScopeDenial.TOOL_SELECTION_OUT_OF_BOUNDS: ReadBoundaryDenial.ARGUMENT_NOT_ALLOWLISTED,
    EffectiveScopeDenial.WRONG_ENTITY: ReadBoundaryDenial.DECISION_TARGET_MISMATCH,
    EffectiveScopeDenial.CROSS_REGION_ESCALATION: ReadBoundaryDenial.ARGUMENT_INVALID,
    EffectiveScopeDenial.FALSE_GEOGRAPHIC_COVERAGE_CLAIM: ReadBoundaryDenial.ARGUMENT_INVALID,
    EffectiveScopeDenial.MISSING_RISK_IS_NOT_LOW: ReadBoundaryDenial.ARGUMENT_INVALID,
    EffectiveScopeDenial.MISSING_RISK_RECORD: ReadBoundaryDenial.DECISION_TARGET_ABSENT,
    EffectiveScopeDenial.ABSENT_ROW: ReadBoundaryDenial.DECISION_TARGET_ABSENT,
    EffectiveScopeDenial.DECISION_AIRCRAFT_NOT_INDEPENDENTLY_VALIDATED: ReadBoundaryDenial.DECISION_TARGET_MISMATCH,
    EffectiveScopeDenial.PERSISTED_RECOMMENDATION_NOT_SEPARATELY_PERMITTED: ReadBoundaryDenial.DECISION_TARGET_MISMATCH,
    EffectiveScopeDenial.BROAD_US_ACCESS_REFUSED: ReadBoundaryDenial.ARGUMENT_INVALID,
}
_NS_PER_SECOND = 1_000_000_000
_BUDGET_GATE = frozenset(
    {
        MasterReadDenialReason.DEADLINE_EXPIRED,
        MasterReadDenialReason.BUDGET_EXHAUSTED,
        MasterReadDenialReason.CANCELLED,
    }
)


class _OfflineClientSeal:
    """Process-local mark. A live SDK client cannot become this object."""


_CLIENT_SEAL = _OfflineClientSeal()


class ScopedAwsReadRefusal(str, Enum):
    """Why the adapter did not perform a keyed AWS read."""

    LIVE_AWS_NOT_INITIATED = "LIVE_AWS_NOT_INITIATED"
    PRODUCTION_AUTHORIZATION_UNAVAILABLE = "PRODUCTION_AUTHORIZATION_UNAVAILABLE"
    CONSUMED_ATTEMPT_NOT_A_PRODUCTION_GRANT = "CONSUMED_ATTEMPT_NOT_A_PRODUCTION_GRANT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SCOPE_DENIED = "SCOPE_DENIED"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    ARBITRARY_SQL = "ARBITRARY_SQL"
    WRITE_NOT_PERMITTED = "WRITE_NOT_PERMITTED"
    RESOURCE_NOT_ALLOWLISTED = "RESOURCE_NOT_ALLOWLISTED"
    HISTORICAL_INTERVAL_ESCALATION = "HISTORICAL_INTERVAL_ESCALATION"
    GUARD_DENIED = "GUARD_DENIED"
    BUDGET_NOT_RESERVED = "BUDGET_NOT_RESERVED"
    NOT_ENTERED = "NOT_ENTERED"
    STOPPED_BEFORE_ADAPTER = "STOPPED_BEFORE_ADAPTER"
    READ_NOT_COMPLETED = "READ_NOT_COMPLETED"


def _closed() -> None:
    if (
        PRODUCTION_SCOPED_AWS_READ_ENABLED is not False
        or LIVE_AWS_OPERATION_PERMITTED is not False
        or APPROVED_SOURCE_ADAPTER is not False
        or EMPTY_PAGE_IS_VERIFIED_ZERO is not False
        or ABSENT_RECORD_IS_LOW_RISK is not False
        or REGION_LABEL_IS_SIGMET_POLYGON is not False
        or CALLER_ARGUMENTS_WIDEN_AUTHORIZATION is not False
        or CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT is not False
    ):
        raise ContractValidationError("production_read_executor_not_enabled")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_token(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _token(value, field_name)


def _whole_second(value: object, field_name: str) -> datetime:
    if not isinstance(value, str) or len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError(f"invalid_{field_name}")
    errors = _validate_utc(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _format_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(value: object, field_name: str, *, allow_zero: bool) -> int:
    if type(value) is not int or value < 0 or (value == 0 and not allow_zero):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


class ScopedAwsSourceError(Exception):
    """Scripted source failure. The adapter does not copy the message onto a receipt."""


class ScopedAwsTimeout(Exception):
    """Scripted source timeout. Not an AWS network timeout."""


@dataclass(frozen=True)
class ConsumedAttemptReadBinding:
    """Descriptive consumed-attempt fields from Agent B. Not a production grant.

    Construction copies fence and identity fields for read binding. It does
    not issue, verify, or consume a grant. ``production_grant`` cannot be true.
    """

    owner_id: str
    attempt_token: str
    fence_epoch: int
    grant_nonce: str
    idempotency_key: str
    payload_fingerprint: str
    ledger_state: str
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    principal_ref: str
    policy_revision: str
    tool_name: str
    resource_id: str
    operation: ReadOperation
    region_scope: tuple[str, ...]
    accepted_at_utc: str
    deadline_utc: str
    correlation_id: str
    grant_consumption_lifecycle: str
    historical_window: tuple[str, str] | None = None
    decision_aircraft_id: str | None = None
    decision_recommendation_id: str | None = None
    executions_used: int = 0
    model_turns_used: int = 0
    tool_calls_used: int = 0
    distributed_exactly_once: bool = False
    production_grant: bool = False
    grant_nonce_is_production_grant: bool = False

    def __post_init__(self) -> None:
        _closed()
        if self.production_grant is not False or self.grant_nonce_is_production_grant is not False:
            raise ContractValidationError("consumed_attempt_is_not_a_production_grant")
        if self.distributed_exactly_once is not False:
            raise ContractValidationError("distributed_exactly_once_not_established")
        _token(self.owner_id, "owner_id")
        _token(self.attempt_token, "attempt_token")
        _token(self.grant_nonce, "grant_nonce")
        _token(self.idempotency_key, "idempotency_key")
        _token(self.payload_fingerprint, "payload_fingerprint")
        _token(self.ledger_state, "ledger_state")
        _token(self.grant_consumption_lifecycle, "grant_consumption_lifecycle")
        if self.ledger_state != LEDGER_EXECUTION_STATE_STARTED:
            raise ContractValidationError("ledger_execution_state_must_stay_started")
        if self.grant_consumption_lifecycle != GRANT_CONSUMPTION_LIFECYCLE:
            raise ContractValidationError("grant_consumption_not_recorded")
        if self.ledger_state == self.grant_consumption_lifecycle:
            raise ContractValidationError("ledger_execution_state_must_stay_started")
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        _token(self.tool_name, "tool_name")
        _token(self.resource_id, "resource_id")
        _token(self.correlation_id, "correlation_id")
        _optional_token(self.decision_aircraft_id, "decision_aircraft_id")
        _optional_token(self.decision_recommendation_id, "decision_recommendation_id")
        if type(self.fence_epoch) is not int or self.fence_epoch < 0:
            raise ContractValidationError("invalid_fence_epoch")
        if not isinstance(self.specialist, MasterSpecialist):
            raise ContractValidationError("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            raise ContractValidationError("invalid_capability")
        if not isinstance(self.operation, ReadOperation):
            raise ContractValidationError("invalid_operation")
        if self.operation not in {ReadOperation.GET_ITEM, ReadOperation.QUERY}:
            raise ContractValidationError("scan_not_permitted")
        identities = (
            self.master_request_id,
            self.planning_session_id,
            self.step_id,
            self.invocation_id,
            self.correlation_id,
            self.idempotency_key,
        )
        if len(set(identities)) != len(identities):
            raise ContractValidationError("request_identity_not_distinct")
        if self.attempt_token == self.grant_nonce:
            raise ContractValidationError("grant_nonce_is_not_attempt_token")
        accepted = _whole_second(self.accepted_at_utc, "accepted_at_utc")
        deadline = _whole_second(self.deadline_utc, "deadline_utc")
        if deadline <= accepted:
            raise ContractValidationError("invalid_deadline_utc")
        if not isinstance(self.region_scope, tuple) or any(type(code) is not str for code in self.region_scope):
            raise ContractValidationError("invalid_region_scope")
        if self.historical_window is not None:
            if not isinstance(self.historical_window, tuple) or len(self.historical_window) != 2:
                raise ContractValidationError("invalid_historical_window")
            start = _whole_second(self.historical_window[0], "historical_start_utc")
            end = _whole_second(self.historical_window[1], "historical_end_utc")
            if start >= end or end - start > MAX_HISTORICAL_WINDOW:
                raise ContractValidationError("historical_interval_escalation")
        _count(self.executions_used, "executions_used", allow_zero=True)
        _count(self.model_turns_used, "model_turns_used", allow_zero=True)
        _count(self.tool_calls_used, "tool_calls_used", allow_zero=True)
        if _arn_or_wildcard(self.resource_id):
            raise ContractValidationError("arbitrary_table_arn" if _arn_text(self.resource_id) else "wildcard_resource")

    def __bool__(self) -> bool:
        raise ContractValidationError("consumed_attempt_is_not_a_production_grant")


def describe_consumed_attempt_read_binding(**fields: object) -> ConsumedAttemptReadBinding:
    """Accept Agent B's frozen fence fields as a descriptive binding.

    This does not issue a local harness grant and is not a production grant.
    """

    _closed()
    if fields.get("production_grant") is True or fields.get("grant_nonce_is_production_grant") is True:
        raise ContractValidationError("consumed_attempt_is_not_a_production_grant")
    return ConsumedAttemptReadBinding(**fields)  # type: ignore[arg-type]


def _arn_text(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith("arn:") or folded.startswith("dynamodb:") or ":table/" in folded


def _wildcard_text(value: str) -> bool:
    folded = value.casefold()
    return "*" in value or folded in {"all", "all_tables", "any", "unrestricted", "table", "tables"}


def _arn_or_wildcard(value: str) -> bool:
    return _arn_text(value) or _wildcard_text(value)


def _attribute(value: object) -> dict[str, object]:
    if value is None:
        return {"NULL": True}
    if type(value) is bool:
        return {"BOOL": value}
    if type(value) is int:
        return {"N": str(value)}
    if isinstance(value, str):
        return {"S": value}
    if isinstance(value, dict) and len(value) == 1 and next(iter(value)) in {"S", "N", "BOOL", "NULL"}:
        return dict(value)
    raise ContractValidationError("invalid_attribute_value")


def _decode_attribute(value: object) -> object:
    if not isinstance(value, dict) or len(value) != 1:
        return value
    kind, payload = next(iter(value.items()))
    if kind == "S" and isinstance(payload, str):
        return payload
    if kind == "N":
        return int(str(payload))
    if kind == "BOOL" and type(payload) is bool:
        return payload
    if kind == "NULL":
        return None
    return value


def _decode_item(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        raise ContractValidationError("invalid_read_page")
    decoded: dict[str, object] = {}
    for key, value in item.items():
        if not isinstance(key, str):
            raise ContractValidationError("invalid_read_page")
        decoded[key] = _decode_attribute(value)
    return decoded


def _encode_item(item: dict[str, object]) -> dict[str, dict[str, object]]:
    encoded: dict[str, dict[str, object]] = {}
    for key, value in item.items():
        encoded[key] = _attribute(value)
    return encoded


def _key_token(key: object) -> str:
    if not isinstance(key, dict) or not key:
        raise ContractValidationError("invalid_item_key")
    first = next(iter(key.values()))
    decoded = _decode_attribute(first)
    if not isinstance(decoded, str) or not decoded:
        raise ContractValidationError("invalid_item_key")
    return decoded


class OfflineAwsReadClient:
    """Deterministic DynamoDB-shaped double. Not a live SDK and not a live connection.

    Only ``get_item`` and ``query`` return data. Writes and Scan are refused
    without mutating stored items.
    """

    def __init__(
        self,
        *,
        get_items: dict[tuple[str, str], dict[str, object]] | None = None,
        query_pages: dict[str, list[dict[str, object]]] | None = None,
        errors: dict[str, BaseException] | None = None,
        stale_keys: frozenset[tuple[str, str]] | None = None,
        on_call: object = None,
        _seal: object = None,
    ) -> None:
        _closed()
        if _seal is not _CLIENT_SEAL:
            raise ContractValidationError("offline_aws_client_not_sealed")
        if on_call is not None and not callable(on_call):
            raise ContractValidationError("invalid_on_call")
        self._get_items = dict(get_items or {})
        self._query_pages = {table: list(pages) for table, pages in dict(query_pages or {}).items()}
        self._query_index = {table: 0 for table in self._query_pages}
        self._errors = dict(errors or {})
        self._stale = frozenset(stale_keys or ())
        self._on_call = on_call
        self._seal = _seal
        self._lock = threading.Lock()
        self.operations: list[str] = []
        self.requests: list[dict[str, object]] = []

    def __bool__(self) -> bool:
        raise ContractValidationError("offline_client_is_not_a_grant")

    @property
    def live_aws_connection(self) -> bool:
        return False

    def get_item(self, **kwargs: object) -> dict[str, object]:
        return self._call("get_item", kwargs)

    def query(self, **kwargs: object) -> dict[str, object]:
        return self._call("query", kwargs)

    def _call(self, method: str, kwargs: dict[str, object]) -> dict[str, object]:
        if self._seal is not _CLIENT_SEAL:
            raise ContractValidationError("offline_aws_client_not_sealed")
        with self._lock:
            self.operations.append(method)
            self.requests.append(dict(kwargs))
            table = kwargs.get("TableName")
            if not isinstance(table, str) or _arn_or_wildcard(table):
                raise ContractValidationError("resource_not_allowlisted")
            if method == "get_item" and kwargs.get("ConsistentRead") is not True:
                raise ContractValidationError("consistent_read_required")
            if "ExclusiveStartKey" in kwargs:
                raise ContractValidationError("pagination_follow_not_permitted")
            if method == "query" and "Limit" not in kwargs:
                raise ContractValidationError("query_limit_required")
            if self._on_call is not None:
                self._on_call()
            error = self._errors.get(method) or self._errors.get(table)
            if error is not None:
                raise error
            if method == "get_item":
                token = _key_token(kwargs.get("Key"))
                item = self._get_items.get((table, token))
                if item is None:
                    return {}
                payload = dict(item)
                if (table, token) in self._stale:
                    payload["stale"] = True
                return {"Item": _encode_item(payload)}
            pages = self._query_pages.get(table, [])
            index = self._query_index.get(table, 0)
            if index >= len(pages):
                return {"Items": []}
            page = dict(pages[index])
            self._query_index[table] = index + 1
            items = page.get("Items", [])
            encoded_items = []
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict):
                        encoded_items.append(_encode_item(item))
            response: dict[str, object] = {"Items": encoded_items}
            last_key = page.get("LastEvaluatedKey")
            if last_key:
                response["LastEvaluatedKey"] = last_key
            query_id = page.get("query_id")
            if isinstance(query_id, str):
                response["query_id"] = query_id
            return response


def bind_offline_aws_read_client(
    *,
    get_items: dict[tuple[str, str], dict[str, object]] | None = None,
    query_pages: dict[str, list[dict[str, object]]] | None = None,
    errors: dict[str, BaseException] | None = None,
    stale_keys: frozenset[tuple[str, str]] | None = None,
    on_call: object = None,
) -> OfflineAwsReadClient:
    """Seal an in-memory double. A raw mapping or live SDK client cannot pass."""

    return OfflineAwsReadClient(
        get_items=get_items,
        query_pages=query_pages,
        errors=errors,
        stale_keys=stale_keys,
        on_call=on_call,
        _seal=_CLIENT_SEAL,
    )


def open_production_scoped_aws_read(*_args: object, **_kwargs: object) -> None:
    """Production AWS reads stay closed. No client method is invoked."""

    _closed()
    raise ContractValidationError("production_read_executor_not_enabled")


def issue_production_scoped_aws_read(*_args: object, **_kwargs: object) -> None:
    """Production issuance is not integrated. No client method is invoked."""

    _closed()
    raise ContractValidationError("production_grant_issuer_not_integrated")


class _AlignedClock:
    def __init__(self, now_utc: str) -> None:
        _closed()
        self._executor_clock = bind_nonproduction_host_clock(now_utc)
        self._mono = 0

    @property
    def executor_clock(self) -> NonproductionHostClock:
        return self._executor_clock

    def now_utc(self) -> str:
        return self._executor_clock.now_utc()

    def monotonic_ns(self) -> int:
        return self._mono

    def advance_seconds(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ContractValidationError("invalid_clock_advance")
        current = _whole_second(self._executor_clock.now_utc(), "now_utc")
        stamp = _format_utc(current + timedelta(seconds=seconds))
        self._executor_clock.advance_to(stamp)
        self._mono += seconds * _NS_PER_SECOND


class _Remaining:
    def __init__(self, remaining_ms: int) -> None:
        if type(remaining_ms) is not int or remaining_ms < 1000:
            raise ContractValidationError("invalid_host_runtime")
        self._remaining_ms = remaining_ms

    def remaining_ms(self) -> int:
        return self._remaining_ms


@dataclass(frozen=True)
class AwsReadPlan:
    """Server-owned DynamoDB request. Table and key are not caller-selected."""

    method: str
    table_name: str
    operation: ReadOperation
    kwargs: dict[str, object]
    query_id: str | None

    def __post_init__(self) -> None:
        if self.method not in {"get_item", "query"}:
            raise ContractValidationError("scan_not_permitted")
        if self.operation is ReadOperation.GET_ITEM and self.method != "get_item":
            raise ContractValidationError("operation_mismatch")
        if self.operation is ReadOperation.QUERY and self.method != "query":
            raise ContractValidationError("operation_mismatch")
        if _arn_or_wildcard(self.table_name):
            raise ContractValidationError("arbitrary_table_arn")
        if "ExclusiveStartKey" in self.kwargs:
            raise ContractValidationError("pagination_follow_not_permitted")


@dataclass(frozen=True)
class ScopedAwsReadResult:
    """One bounded adapter outcome. Closed flags cannot become aviation evidence."""

    refusal: ScopedAwsReadRefusal | None
    binding: ConsumedAttemptReadBinding
    scope: MasterAuthorizedScopeDecision | None
    guard_check: MasterReadResourceCheck | None
    budget: BudgetDecision | None
    receipt: ReadReceiptCandidate | None
    observation: dict[str, object] | None
    journal_entry: RuntimeReadJournalEntry | None
    adapter_invoked: bool
    aws_operations: tuple[str, ...]
    plan: AwsReadPlan | None
    session: RuntimeBudgetSession | None
    schema_version: str = SPRINT3C_SCOPED_READ_SCHEMA_VERSION
    approved_source_adapter: bool = False
    production_grant_issued: bool = False
    live_aws_initiated: bool = False
    source_authenticity_established: bool = False
    verified_zero: bool = False
    absent_record_is_low_risk: bool = False
    sigmet_polygon_intersection_established: bool = False
    caller_arguments_widened_authorization: bool = False
    distributed_exactly_once: bool = False
    inferred_risk_level: StoredRiskLevel | None = None

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT3C_SCOPED_READ_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        closed_flags = (
            self.approved_source_adapter,
            self.production_grant_issued,
            self.live_aws_initiated,
            self.source_authenticity_established,
            self.verified_zero,
            self.absent_record_is_low_risk,
            self.sigmet_polygon_intersection_established,
            self.caller_arguments_widened_authorization,
            self.distributed_exactly_once,
        )
        if any(flag is not False for flag in closed_flags):
            raise ContractValidationError("scoped_aws_read_is_not_source_authentication")
        if self.inferred_risk_level is not None:
            raise ContractValidationError("absent_record_is_not_low_risk")
        if type(self.binding) is not ConsumedAttemptReadBinding:
            raise ContractValidationError("invalid_consumed_attempt_binding")
        if self.receipt is not None:
            if (
                self.receipt.approved_source_adapter is not False
                or self.receipt.verified_zero_minted is not False
                or self.receipt.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
                or self.receipt.production_authority is not MasterProductionAuthority.DENIED
            ):
                raise ContractValidationError("read_observation_is_not_source_authentication")
        if self.adapter_invoked is not (self.receipt is not None and self.receipt.adapter_invoked):
            raise ContractValidationError("invalid_scoped_aws_read_result")
        if self.live_aws_initiated is not False:
            raise ContractValidationError("live_aws_operation_not_initiated")

    def __bool__(self) -> bool:
        raise ContractValidationError("scoped_aws_read_is_not_source_authentication")


def _query_id(table_name: str, operation: ReadOperation, arguments: dict[str, object]) -> str:
    material = repr((table_name, operation.value, tuple(sorted(arguments.items(), key=lambda item: item[0]))))
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"q-{digest}"


def _plan_from_executed(
    resource_id: str,
    operation: ReadOperation,
    executed: dict[str, object],
) -> AwsReadPlan:
    if operation is ReadOperation.SCAN:
        raise ContractValidationError("scan_not_permitted")
    if _arn_or_wildcard(resource_id):
        raise ContractValidationError("arbitrary_table_arn")
    if operation is ReadOperation.GET_ITEM:
        attribute = _RESOURCE_KEY_ATTRIBUTE.get(resource_id)
        if attribute is None:
            raise ContractValidationError("resource_not_allowlisted")
        key_value = executed.get(attribute)
        if not isinstance(key_value, str) or not key_value:
            for fallback in ("aircraft_id", "hazard_id", "airport_id", "recommendation_id"):
                candidate = executed.get(fallback)
                if isinstance(candidate, str) and candidate:
                    attribute = fallback
                    key_value = candidate
                    break
        if not isinstance(key_value, str) or not key_value:
            raise ContractValidationError("argument_missing")
        kwargs = {
            "TableName": resource_id,
            "Key": {attribute: _attribute(key_value)},
            "ConsistentRead": True,
        }
        return AwsReadPlan(
            method="get_item",
            table_name=resource_id,
            operation=operation,
            kwargs=kwargs,
            query_id=None,
        )
    partition = _QUERY_PARTITION.get(resource_id, _RESOURCE_KEY_ATTRIBUTE.get(resource_id))
    partition_value: object = None
    if partition == "callsign":
        partition_value = executed.get("callsign")
    elif partition == "region":
        partition_value = executed.get("region")
        if partition_value is None:
            hazard_ids = executed.get("hazard_ids")
            if isinstance(hazard_ids, tuple) and hazard_ids:
                partition = "hazard_id"
                partition_value = hazard_ids[0]
            else:
                partition_value = executed.get("hazard_id")
                partition = "hazard_id"
    else:
        partition_value = executed.get(partition) if partition is not None else None
        if partition_value is None and partition != "aircraft_id":
            partition_value = executed.get("aircraft_id")
            if isinstance(partition_value, str):
                partition = "aircraft_id"
        if partition_value is None:
            partition_value = executed.get("hazard_id")
            if isinstance(partition_value, str):
                partition = "hazard_id"
    if partition is None or not isinstance(partition_value, str) or not partition_value:
        raise ContractValidationError("argument_missing")
    limit = executed.get("limit")
    if type(limit) is not int or isinstance(limit, bool) or limit < 1:
        limit = HISTORICAL_LIST_MAX
    if limit > HISTORICAL_LIST_MAX:
        raise ContractValidationError("query_limit_exceeded")
    values = {":pk": _attribute(partition_value)}
    condition = f"{partition} = :pk"
    start = executed.get("start_utc")
    end = executed.get("end_utc")
    if isinstance(start, str) and isinstance(end, str):
        values[":start"] = _attribute(start)
        values[":end"] = _attribute(end)
        condition = f"{partition} = :pk AND observed_at BETWEEN :start AND :end"
    kwargs = {
        "TableName": resource_id,
        "KeyConditionExpression": condition,
        "ExpressionAttributeValues": values,
        "Limit": limit,
    }
    return AwsReadPlan(
        method="query",
        table_name=resource_id,
        operation=operation,
        kwargs=kwargs,
        query_id=_query_id(resource_id, operation, executed),
    )


def _page_from_response(
    response: object,
    plan: AwsReadPlan,
    *,
    now_utc: str,
    row_limit: int,
) -> NonproductionReadPage:
    if not isinstance(response, dict):
        raise ContractValidationError("invalid_read_page")
    records: list[dict[str, object]] = []
    if plan.method == "get_item":
        item = response.get("Item")
        if item is not None:
            records.append(_decode_item(item))
    else:
        items = response.get("Items", [])
        if not isinstance(items, list):
            raise ContractValidationError("invalid_read_page")
        for item in items:
            records.append(_decode_item(item))
    if len(records) > row_limit:
        raise ContractValidationError("query_limit_exceeded")
    record_ids: list[str] = []
    versions: list[str] = []
    observed: str | None = None
    stale = False
    for record in records:
        record_id = _record_id(record, plan.table_name)
        record_ids.append(record_id)
        version = record.get("source_version")
        if version is None:
            version = record.get("version")
        if isinstance(version, str) and version:
            versions.append(version)
        stamp = record.get("source_observed_at_utc") or record.get("observed_at_utc") or record.get(
            "event_timestamp_utc"
        )
        if observed is None and isinstance(stamp, str):
            observed = stamp
        if record.get("stale") is True:
            stale = True
        expires = record.get("expires_at_utc")
        if isinstance(expires, str):
            try:
                if _whole_second(expires, "expires_at_utc") <= _whole_second(now_utc, "now_utc"):
                    stale = True
            except ContractValidationError:
                stale = True
    last_key = response.get("LastEvaluatedKey")
    has_more = last_key is not None and last_key != {}
    query_id = response.get("query_id") if isinstance(response.get("query_id"), str) else plan.query_id
    source_version = versions[0] if len(set(versions)) == 1 else (None if not versions else versions[0])
    if len(set(versions)) > 1:
        stale = True
    if observed is not None:
        _whole_second(observed, "source_reported_observed_at_utc")
    return NonproductionReadPage(
        record_ids=tuple(record_ids),
        source_name=plan.table_name,
        source_version=source_version,
        query_id=query_id,
        page_index=0,
        has_more=has_more,
        truncated=has_more or stale,
        source_observed_at_utc=observed,
        next_page_token="present" if has_more else None,
    )


def _record_id(record: dict[str, object], table_name: str) -> str:
    for key in (
        "record_id",
        _RESOURCE_KEY_ATTRIBUTE.get(table_name, ""),
        "aircraft_id",
        "hazard_id",
        "airport_id",
        "recommendation_id",
        "encounter_id",
    ):
        value = record.get(key) if key else None
        if isinstance(value, str) and value and not any(character.isspace() for character in value):
            return value
    raise ContractValidationError("invalid_read_page")


def _identity_mismatch(binding: ConsumedAttemptReadBinding, request: UntrustedReadRequest) -> bool:
    return (
        binding.master_request_id != request.master_request_id
        or binding.planning_session_id != request.planning_session_id
        or binding.step_id != request.step_id
        or binding.invocation_id != request.invocation_id
        or binding.specialist is not request.specialist
        or binding.purpose is not request.purpose
        or binding.capability is not request.capability
        or binding.tool_name != request.tool_name
        or binding.resource_id != request.resource_id
        or binding.operation is not request.operation
        or (
            request.correlation_id is not None
            and request.correlation_id != binding.correlation_id
        )
    )


def _sql_or_write_denials(request: UntrustedReadRequest) -> list[ReadBoundaryDenial]:
    reasons: list[ReadBoundaryDenial] = []
    if request.operation is ReadOperation.SCAN:
        reasons.append(ReadBoundaryDenial.SCAN_NOT_PERMITTED)
    if request.tool_name.casefold() in _WRITE_METHODS or request.resource_id.casefold() in _WRITE_METHODS:
        reasons.append(ReadBoundaryDenial.ARBITRARY_AWS_METHOD)
    if _arn_text(request.resource_id):
        reasons.append(ReadBoundaryDenial.ARBITRARY_TABLE_ARN)
    if _wildcard_text(request.resource_id):
        reasons.append(ReadBoundaryDenial.WILDCARD_RESOURCE)
    for name, value in request.requested_arguments:
        if name in _SQL_ARGUMENT_NAMES:
            reasons.append(ReadBoundaryDenial.ARBITRARY_SQL)
        if name.casefold() in _WRITE_METHODS:
            reasons.append(ReadBoundaryDenial.ARBITRARY_AWS_METHOD)
        if isinstance(value, str) and name in _SQL_ARGUMENT_NAMES:
            reasons.append(ReadBoundaryDenial.ARBITRARY_SQL)
    return reasons


def _scope_denials(decision: MasterAuthorizedScopeDecision) -> tuple[ReadBoundaryDenial, ...]:
    found: list[ReadBoundaryDenial] = [ReadBoundaryDenial.POLICY_EVALUATION_DENIED]
    for reason in decision.denial_reasons:
        mapped = _SCOPE_TO_READ.get(reason)
        if mapped is not None:
            found.append(mapped)
    unique: list[ReadBoundaryDenial] = []
    for item in found:
        if item not in unique:
            unique.append(item)
    return tuple(unique)


def _request_arg(request: UntrustedReadRequest, name: str) -> str | None:
    for key, value in request.requested_arguments:
        if key == name and isinstance(value, str):
            return value
    return None


def _window_for(decision: MasterAuthorizedScopeDecision, specialist: MasterSpecialist) -> tuple[str, str] | None:
    if specialist is not MasterSpecialist.HISTORICAL_ANALYTICS:
        return None
    start = decision.historical_window.eligible_start_utc
    end = decision.historical_window.eligible_end_utc
    if start is None or end is None:
        return None
    return start, end


class Sprint3CScopedReadPort:
    """Host port that binds a consumed attempt to one keyed offline AWS read."""

    def __init__(
        self,
        client: object,
        *,
        now_utc: str,
        remaining_ms: int = 30_000,
    ) -> None:
        _closed()
        self._client_ok = type(client) is OfflineAwsReadClient and client._seal is _CLIENT_SEAL
        self._client = client if self._client_ok else None
        self._rejected_live_client = client if not self._client_ok else None
        self._clock = _AlignedClock(now_utc)
        self._remaining = _Remaining(remaining_ms)
        self._lock = threading.Lock()
        self._cache: dict[tuple[str, str, str], ScopedAwsReadResult] = {}
        self._journal = MasterRuntimeReadJournal(self._clock)

    def __bool__(self) -> bool:
        raise ContractValidationError("scoped_aws_read_port_is_not_a_grant")

    @property
    def live_aws_initiated(self) -> bool:
        return False

    def now_utc(self) -> str:
        return self._clock.now_utc()

    def advance_seconds(self, seconds: int) -> None:
        self._clock.advance_seconds(seconds)

    def execute(
        self,
        binding: object,
        request: object,
        host: object,
        *,
        tool_call_id: str,
        action_id: str,
        cancellation: object = None,
        location_hint: str | None = None,
        census_region_name: str | None = None,
        sigmet_region_string: str | None = None,
        claims_geographic_coverage: bool = False,
        claims_exact_sigmet_polygon: bool = False,
        missing_risk_means_low: bool = False,
        unbound_plan_window: tuple[str, str] | None = None,
        copy_unbound_plan_window: bool = False,
    ) -> ScopedAwsReadResult:
        """Run one allowlisted GET_ITEM or QUERY, or fail closed without AWS."""

        _closed()
        if type(binding) is not ConsumedAttemptReadBinding:
            raise ContractValidationError("invalid_consumed_attempt_binding")
        if type(request) is not UntrustedReadRequest:
            raise ContractValidationError("invalid_read_request")
        if type(host) is not LocalTestHostScope:
            raise ContractValidationError("host_not_issued")
        _token(tool_call_id, "tool_call_id")
        _token(action_id, "action_id")
        cache_key = (binding.idempotency_key, binding.invocation_id, binding.grant_nonce)
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached
        if cancellation is not None and type(cancellation) is not HostCancellation:
            raise ContractValidationError("cancellation_not_host_issued")
        if cancellation is None:
            cancellation = bind_nonproduction_cancellation()
        assert isinstance(cancellation, HostCancellation)
        if not self._client_ok:
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    None,
                    None,
                    None,
                    (ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED, ReadBoundaryDenial.APPROVED_SOURCE_ADAPTER_ABSENT),
                    (),
                    ScopedAwsReadRefusal.LIVE_AWS_NOT_INITIATED,
                    aws_operations=(),
                ),
            )
        assert type(self._client) is OfflineAwsReadClient
        hints = self._hints(
            binding,
            request,
            location_hint=location_hint,
            census_region_name=census_region_name,
            sigmet_region_string=sigmet_region_string,
            claims_geographic_coverage=claims_geographic_coverage,
            claims_exact_sigmet_polygon=claims_exact_sigmet_polygon,
            missing_risk_means_low=missing_risk_means_low,
            unbound_plan_window=unbound_plan_window,
            copy_unbound_plan_window=copy_unbound_plan_window,
        )
        decision = resolve_master_authorized_scope(hints, host)
        early = self._early_denials(binding, request, decision)
        if early is not None:
            denials, refusal = early
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    decision,
                    None,
                    None,
                    denials,
                    (),
                    refusal,
                    aws_operations=(),
                ),
            )
        session = self._open_session(binding, decision)
        usage = session.usage(invocation_id=binding.invocation_id)
        guard = self._guard(hints, decision, session, usage, cancellation)
        budget_only = bool(guard.denial_reasons) and set(guard.denial_reasons) <= _BUDGET_GATE
        if guard.denial_reasons and not budget_only:
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    decision,
                    guard,
                    None,
                    (ReadBoundaryDenial.POLICY_EVALUATION_DENIED,),
                    guard.denial_reasons,
                    ScopedAwsReadRefusal.GUARD_DENIED,
                    session=session,
                ),
            )
        reservation = session.begin_read(
            invocation_id=binding.invocation_id,
            action_id=action_id,
            tool_name=binding.tool_name,
            operation=binding.operation,
            rows=0,
            pagination_pages=1,
            arguments=request.requested_arguments,
            resource_id=binding.resource_id,
            historical_window=_window_for(decision, binding.specialist),
        )
        if reservation.disposition is not StartedTaskDisposition.RESERVED or guard.denial_reasons:
            refusal = (
                ScopedAwsReadRefusal.GUARD_DENIED if guard.denial_reasons else ScopedAwsReadRefusal.BUDGET_NOT_RESERVED
            )
            denials = (
                (ReadBoundaryDenial.POLICY_EVALUATION_DENIED,)
                if guard.denial_reasons
                else (ReadBoundaryDenial.BUDGET_EXHAUSTED,)
            )
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    decision,
                    guard,
                    reservation,
                    denials,
                    guard.denial_reasons,
                    refusal,
                    session=session,
                ),
            )
        timeout = self._timeout_seconds(session)
        entered = session.mark_entered(action_id, downstream_timeout_s=timeout)
        if entered.disposition is not StartedTaskDisposition.STARTED:
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    decision,
                    guard,
                    entered,
                    (ReadBoundaryDenial.DEADLINE_EXPIRED,)
                    if entered.evaluation.value == "DEADLINE_EXPIRED"
                    else (ReadBoundaryDenial.CANCELLED,),
                    entered.denial_reasons if False else guard.denial_reasons,
                    ScopedAwsReadRefusal.NOT_ENTERED,
                    session=session,
                ),
            )
        if cancellation.is_cancelled() or self._deadline_reached(session):
            if cancellation.is_cancelled():
                session.cancel()
            finished = session.finish_started(action_id, failed=False)
            return self._finish(
                cache_key,
                self._denied(
                    binding,
                    request,
                    decision,
                    guard,
                    finished,
                    (ReadBoundaryDenial.CANCELLED,)
                    if cancellation.is_cancelled()
                    else (ReadBoundaryDenial.DEADLINE_EXPIRED,),
                    (),
                    ScopedAwsReadRefusal.STOPPED_BEFORE_ADAPTER,
                    session=session,
                ),
            )
        server_read_budget(binding.specialist)
        invocation = self._invocation(binding, decision, tool_call_id, timeout, usage)
        plan_holder: dict[str, AwsReadPlan | None] = {"plan": None}
        adapter = self._adapter(plan_holder, binding.resource_id, binding.operation)
        registry = bind_nonproduction_read_registry(
            (
                NonproductionAdapterSpec(
                    specialist=binding.specialist,
                    tool_name=binding.tool_name,
                    capability=binding.capability,
                    resource_id=binding.resource_id,
                    operation=binding.operation,
                    read=adapter,
                ),
            )
        )
        executor = MasterTrustedReadExecutor(registry, self._clock.executor_clock)
        receipt: ReadReceiptCandidate | None = None
        finished_budget: BudgetDecision | None = None
        try:
            try:
                receipt = executor.execute(
                    request,
                    host_invocation=invocation,
                    cancellation=cancellation,
                )
            except ReadExecutionError as exc:
                receipt = exc.receipt
            if cancellation.is_cancelled():
                session.cancel()
            self._record_source(session, binding.invocation_id, receipt)
            failed = receipt.outcome not in {
                ReadExecutionOutcome.COMPLETED,
                ReadExecutionOutcome.CANCELLED,
                ReadExecutionOutcome.TIMEOUT,
            }
            finished_budget = session.finish_started(action_id, failed=failed)
        except BaseException:
            if finished_budget is None:
                session.finish_started(action_id, failed=True)
            raise
        journal_entry = self._journal_receipt(binding, request, tool_call_id, receipt, plan_holder["plan"])
        completed = (
            receipt.outcome is ReadExecutionOutcome.COMPLETED
            and finished_budget.disposition is StartedTaskDisposition.COMPLETED_BOUNDED
        )
        aws_ops = tuple(self._client.operations)
        result = ScopedAwsReadResult(
            refusal=None if completed else ScopedAwsReadRefusal.READ_NOT_COMPLETED,
            binding=binding,
            scope=decision,
            guard_check=guard,
            budget=finished_budget,
            receipt=receipt,
            observation=None if receipt is None else raw_read_observation(receipt),
            journal_entry=journal_entry,
            adapter_invoked=receipt.adapter_invoked,
            aws_operations=aws_ops if receipt.adapter_invoked else (),
            plan=plan_holder["plan"] if receipt.adapter_invoked else None,
            session=session,
        )
        return self._finish(cache_key, result)

    def _hints(
        self,
        binding: ConsumedAttemptReadBinding,
        request: UntrustedReadRequest,
        *,
        location_hint: str | None,
        census_region_name: str | None,
        sigmet_region_string: str | None,
        claims_geographic_coverage: bool,
        claims_exact_sigmet_polygon: bool,
        missing_risk_means_low: bool,
        unbound_plan_window: tuple[str, str] | None,
        copy_unbound_plan_window: bool,
    ) -> EffectiveScopeRequestHints:
        window = binding.historical_window
        region = _request_arg(request, "region")
        claimed = (region,) if isinstance(region, str) else ()
        payload: dict[str, object] = {
            "master_request_id": binding.master_request_id,
            "planning_session_id": binding.planning_session_id,
            "step_id": binding.step_id,
            "invocation_id": binding.invocation_id,
            "correlation_id": binding.correlation_id,
            "idempotency_key": binding.idempotency_key,
            "specialist": binding.specialist,
            "purpose": binding.purpose,
            "capability": binding.capability,
            "tool_name": binding.tool_name,
            "resource_id": binding.resource_id,
            "operation": request.operation,
            "accepted_at_utc": binding.accepted_at_utc,
            "deadline_utc": binding.deadline_utc,
            "arguments": request.requested_arguments,
            "location_hint": location_hint,
            "claimed_state_codes": claimed,
            "sigmet_region_string": sigmet_region_string,
            "census_region_name": census_region_name,
            "claims_geographic_coverage": claims_geographic_coverage,
            "claims_exact_sigmet_polygon": claims_exact_sigmet_polygon,
            "aircraft_id": _request_arg(request, "aircraft_id") or binding.decision_aircraft_id,
            "entity_id": _request_arg(request, "aircraft_id")
            or _request_arg(request, "hazard_id")
            or _request_arg(request, "airport_id")
            or binding.decision_aircraft_id,
            "callsign": _request_arg(request, "callsign"),
            "recommendation_id": _request_arg(request, "recommendation_id") or binding.decision_recommendation_id,
            "missing_risk_means_low": missing_risk_means_low,
            "principal_label": request.principal_label,
            "caller_authorized": request.caller_authorized,
            "caller_deadline_utc": request.caller_deadline_utc,
            "caller_max_tool_calls": request.caller_max_tool_calls,
            "copy_unbound_plan_window": copy_unbound_plan_window,
        }
        if window is not None:
            payload["historical_start_utc"] = window[0]
            payload["historical_end_utc"] = window[1]
        if unbound_plan_window is not None:
            payload["unbound_plan_window_start_utc"] = unbound_plan_window[0]
            payload["unbound_plan_window_end_utc"] = unbound_plan_window[1]
        return EffectiveScopeRequestHints(**payload)  # type: ignore[arg-type]

    def _early_denials(
        self,
        binding: ConsumedAttemptReadBinding,
        request: UntrustedReadRequest,
        decision: MasterAuthorizedScopeDecision,
    ) -> tuple[tuple[ReadBoundaryDenial, ...], ScopedAwsReadRefusal] | None:
        sql_write = _sql_or_write_denials(request)
        if ReadBoundaryDenial.SCAN_NOT_PERMITTED in sql_write:
            return tuple(dict.fromkeys(sql_write)), ScopedAwsReadRefusal.SCAN_NOT_PERMITTED
        if ReadBoundaryDenial.ARBITRARY_SQL in sql_write:
            return tuple(dict.fromkeys(sql_write)), ScopedAwsReadRefusal.ARBITRARY_SQL
        if ReadBoundaryDenial.ARBITRARY_AWS_METHOD in sql_write:
            return tuple(dict.fromkeys(sql_write)), ScopedAwsReadRefusal.WRITE_NOT_PERMITTED
        if ReadBoundaryDenial.ARBITRARY_TABLE_ARN in sql_write or ReadBoundaryDenial.WILDCARD_RESOURCE in sql_write:
            return tuple(dict.fromkeys(sql_write)), ScopedAwsReadRefusal.RESOURCE_NOT_ALLOWLISTED
        if request.caller_scope is not None:
            return (ReadBoundaryDenial.CALLER_SCOPE_NOT_AUTHORITY,), ScopedAwsReadRefusal.SCOPE_DENIED
        if request.caller_callable is not None:
            return (ReadBoundaryDenial.CALLER_CALLABLE_REJECTED,), ScopedAwsReadRefusal.SCOPE_DENIED
        if request.caller_tool_result is not None:
            return (ReadBoundaryDenial.CALLER_TOOL_RESULT_NOT_A_READ,), ScopedAwsReadRefusal.SCOPE_DENIED
        if _identity_mismatch(binding, request):
            return (ReadBoundaryDenial.IDENTITY_MISMATCH,), ScopedAwsReadRefusal.IDENTITY_MISMATCH
        request_window = _requested_window(request)
        if request_window is not None:
            start, end = request_window
            if end - start > MAX_HISTORICAL_WINDOW:
                return (
                    ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,
                    ReadBoundaryDenial.POLICY_EVALUATION_DENIED,
                ), ScopedAwsReadRefusal.HISTORICAL_INTERVAL_ESCALATION
        if decision.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED:
            if EffectiveScopeDenial.UNBOUNDED_HISTORICAL_WINDOW in decision.denial_reasons:
                return _scope_denials(decision), ScopedAwsReadRefusal.HISTORICAL_INTERVAL_ESCALATION
            if EffectiveScopeDenial.SCAN_NOT_PERMITTED in decision.denial_reasons:
                return _scope_denials(decision), ScopedAwsReadRefusal.SCAN_NOT_PERMITTED
            return _scope_denials(decision), ScopedAwsReadRefusal.SCOPE_DENIED
        pairs = server_read_resources(binding.specialist, binding.tool_name)
        names = server_read_argument_names(binding.specialist, binding.tool_name)
        tools = server_read_tools(binding.specialist)
        if binding.tool_name not in tools or (binding.resource_id, binding.operation) not in pairs:
            return (ReadBoundaryDenial.RESOURCE_MISMATCH,), ScopedAwsReadRefusal.RESOURCE_NOT_ALLOWLISTED
        if any(name not in names for name, _value in request.requested_arguments):
            extra = [name for name, _value in request.requested_arguments if name not in names]
            if any(name in _SQL_ARGUMENT_NAMES for name in extra):
                return (ReadBoundaryDenial.ARBITRARY_SQL, ReadBoundaryDenial.ARGUMENT_NOT_ALLOWLISTED), ScopedAwsReadRefusal.ARBITRARY_SQL
            return (ReadBoundaryDenial.ARGUMENT_NOT_ALLOWLISTED,), ScopedAwsReadRefusal.RESOURCE_NOT_ALLOWLISTED
        return None

    def _open_session(
        self,
        binding: ConsumedAttemptReadBinding,
        decision: MasterAuthorizedScopeDecision,
    ) -> RuntimeBudgetSession:
        envelope = RequestBudgetEnvelope(
            master_request_id=binding.master_request_id,
            planning_session_id=binding.planning_session_id,
            step_id=binding.step_id,
            invocation_id=binding.invocation_id,
            specialist=decision.specialist,
            purpose=decision.purpose,
            capability=decision.capability,
            correlation_id=binding.correlation_id,
            idempotency_key=binding.idempotency_key,
            principal_ref=decision.principal_ref,
            policy_revision=decision.policy_revision,
            historical_window=binding.historical_window,
        )
        return open_private_offline_harness(
            offline_harness_seal(),
            clock=self._clock,
            remaining_runtime=self._remaining,
            envelope=envelope,
        )

    def _guard(
        self,
        hints: EffectiveScopeRequestHints,
        decision: MasterAuthorizedScopeDecision,
        session: RuntimeBudgetSession,
        usage: object,
        cancellation: HostCancellation,
    ) -> MasterReadResourceCheck:
        arguments = dict(hints.arguments)
        start = arguments.get("start_utc")
        end = arguments.get("end_utc")
        proposal = MasterReadResourceProposal(
            specialist=hints.specialist,
            purpose=hints.purpose,
            capability=hints.capability,
            tool_name=hints.tool_name,
            resource_id=hints.resource_id,
            operation=hints.operation,
            arguments=hints.arguments,
            master_request_id=hints.master_request_id,
            invocation_id=hints.invocation_id,
            historical_start_utc=start if isinstance(start, str) else None,
            historical_end_utc=end if isinstance(end, str) else None,
        )
        evaluation = ServerReadEvaluation(
            observed_utc=self._clock.now_utc(),
            deadline_utc=session.deadline_utc,
            executions_used=getattr(usage, "specialist_executions"),
            model_turns_used=getattr(usage, "model_turns"),
            tool_calls_used=getattr(usage, "tool_calls"),
            cancellation=ServerCancellation(cancellation.is_cancelled()),
        )
        codes = decision.region_scope.effective_state_codes
        population = seal_population_scope(frozenset(codes)) if codes else None
        principal = decision.principal_ref
        if principal is None:
            raise ContractValidationError("principal_not_trusted")
        return check_master_read_resource(
            proposal,
            evaluation=evaluation,
            principal=seal_ingress_principal(principal),
            population_scope=population,
        )

    def _timeout_seconds(self, session: RuntimeBudgetSession) -> int:
        remaining = int(
            (_whole_second(session.deadline_utc, "deadline_utc") - _whole_second(self._clock.now_utc(), "now_utc")).total_seconds()
        )
        return 1 if remaining < 1 else remaining

    def _deadline_reached(self, session: RuntimeBudgetSession) -> bool:
        return _whole_second(self._clock.now_utc(), "now_utc") >= _whole_second(session.deadline_utc, "deadline_utc")

    def _invocation(
        self,
        binding: ConsumedAttemptReadBinding,
        decision: MasterAuthorizedScopeDecision,
        tool_call_id: str,
        timeout: int,
        usage: object,
    ) -> HostReadInvocation:
        codes = decision.region_scope.effective_state_codes
        window = _window_for(decision, binding.specialist)
        principal = decision.principal_ref
        policy = decision.policy_revision
        if principal is None or policy is None:
            raise ContractValidationError("principal_not_trusted")
        return bind_nonproduction_test_invocation(
            master_request_id=binding.master_request_id,
            planning_session_id=binding.planning_session_id,
            step_id=binding.step_id,
            invocation_id=binding.invocation_id,
            specialist=decision.specialist,
            purpose=decision.purpose,
            capability=decision.capability,
            tool_name=binding.tool_name,
            tool_call_id=tool_call_id,
            resource_id=binding.resource_id,
            operation=binding.operation,
            principal_ref=principal,
            policy_revision=policy,
            accepted_at_utc=binding.accepted_at_utc,
            deadline_utc=binding.deadline_utc
            if _whole_second(binding.deadline_utc, "deadline_utc")
            <= _whole_second(self._clock.now_utc(), "now_utc") + timedelta(seconds=timeout + 1)
            else binding.deadline_utc,
            idempotency_key=binding.idempotency_key,
            grant_nonce=binding.grant_nonce,
            executions_used=getattr(usage, "specialist_executions"),
            model_turns_used=getattr(usage, "model_turns"),
            tool_calls_used=getattr(usage, "tool_calls"),
            timeout_budget_seconds=timeout,
            region_codes=frozenset(codes) if codes else None,
            historical_start_utc=None if window is None else window[0],
            historical_end_utc=None if window is None else window[1],
            decision_aircraft_id=decision.decision_target_scope.described_aircraft_id,
            decision_recommendation_id=decision.decision_target_scope.described_recommendation_id,
            correlation_id=binding.correlation_id,
        )

    def _adapter(
        self,
        plan_holder: dict[str, AwsReadPlan | None],
        resource_id: str,
        operation: ReadOperation,
    ):
        client = self._client
        clock = self._clock

        def read(executed_arguments: dict[str, object], cancellation: HostCancellation) -> NonproductionReadPage:
            if type(client) is not OfflineAwsReadClient:
                raise ContractValidationError("live_aws_operation_not_initiated")
            if cancellation.is_cancelled():
                raise ScopedAwsSourceError("cancelled")
            plan = _plan_from_executed(resource_id, operation, executed_arguments)
            plan_holder["plan"] = plan
            method = getattr(client, plan.method)
            response = method(**plan.kwargs)
            row_limit = 1 if operation is ReadOperation.GET_ITEM else int(plan.kwargs.get("Limit", HISTORICAL_LIST_MAX))
            return _page_from_response(response, plan, now_utc=clock.now_utc(), row_limit=row_limit)

        return read

    def _record_source(
        self,
        session: RuntimeBudgetSession,
        invocation_id: str,
        receipt: ReadReceiptCandidate,
    ) -> None:
        if receipt.outcome is not ReadExecutionOutcome.COMPLETED:
            return
        observed = receipt.source_reported_observed_at_utc
        if observed is None:
            return
        deadline = session.deadline_utc
        accepted = session.accepted_at_utc
        session.record_source_observation(
            invocation_id=invocation_id,
            observed_at_utc=observed,
            rows=receipt.rows_returned,
            pagination_pages=1 if receipt.partial_pagination else 0,
        )
        if session.deadline_utc != deadline or session.accepted_at_utc != accepted:
            raise ContractValidationError("source_clock_moved_deadline")

    def _journal_receipt(
        self,
        binding: ConsumedAttemptReadBinding,
        request: UntrustedReadRequest,
        tool_call_id: str,
        receipt: ReadReceiptCandidate,
        plan: AwsReadPlan | None,
    ) -> RuntimeReadJournalEntry | None:
        adapter_id = (
            f"{ADAPTER_ORIGIN}:{binding.specialist.value}:{binding.tool_name}:"
            f"{binding.resource_id}:{binding.operation.value}"
        )
        invocation = ReadInvocation(
            invocation_id=binding.invocation_id,
            tool_call_id=tool_call_id,
            tool_name=binding.tool_name,
            adapter_identity=adapter_id,
            requested_arguments={name: _jsonable(value) for name, value in request.requested_arguments},
            master_request_id=binding.master_request_id,
            expected_source_version=None,
            timeout_budget_seconds=30,
        )
        proposal = self._journal.propose(invocation)
        if not receipt.adapter_invoked:
            return proposal
        records = tuple(
            SourceRecord(
                record_id=record_id,
                source_version=receipt.source_version,
                event_timestamp_utc=receipt.source_reported_observed_at_utc,
            )
            for record_id in receipt.record_ids
        )
        query = None
        if receipt.query_id is not None:
            query = QueryExecutionTrace(
                query_id=receipt.query_id,
                rows_returned=receipt.rows_returned,
                engine_scope=binding.resource_id,
            )
        pagination = PaginationObservation(
            page_index=receipt.page_index,
            rows_in_page=receipt.rows_returned,
            row_limit=receipt.row_limit,
            has_more=receipt.has_more,
            next_page_token_present=receipt.next_page_token_present,
        )
        result = LocalFakeReadResult(
            source_records=records,
            query_execution=query,
            source_version=receipt.source_version,
            pagination=pagination,
            truncated=receipt.truncated,
            rows_returned=receipt.rows_returned,
            executed_arguments={name: _jsonable(value) for name, value in receipt.executed_arguments},
            source_response_ref=adapter_id,
        )

        def _observe() -> LocalFakeReadResult:
            del plan
            return result

        mark_deterministic_local_fake(_observe)
        try:
            return self._journal.execute(_observe, proposal)
        except Exception:
            return proposal

    def _denied(
        self,
        binding: ConsumedAttemptReadBinding,
        request: UntrustedReadRequest,
        scope: MasterAuthorizedScopeDecision | None,
        guard: MasterReadResourceCheck | None,
        budget: BudgetDecision | None,
        denials: tuple[ReadBoundaryDenial, ...],
        guard_reasons: tuple[MasterReadDenialReason, ...],
        refusal: ScopedAwsReadRefusal,
        *,
        session: RuntimeBudgetSession | None = None,
        aws_operations: tuple[str, ...] = (),
    ) -> ScopedAwsReadResult:
        registry = bind_nonproduction_read_registry(())
        executor = MasterTrustedReadExecutor(registry, self._clock.executor_clock)
        host_invocation = None
        if scope is not None and scope.status is EffectiveScopeStatus.BOUNDED_DESCRIBED:
            try:
                usage_exec = 0
                usage_turns = 0
                usage_calls = 0
                if session is not None:
                    used = session.usage(invocation_id=binding.invocation_id)
                    usage_exec = used.specialist_executions
                    usage_turns = used.model_turns
                    usage_calls = used.tool_calls

                class _Usage:
                    specialist_executions = usage_exec
                    model_turns = usage_turns
                    tool_calls = usage_calls

                host_invocation = self._invocation(binding, scope, request.tool_call_id, 30, _Usage())
            except ContractValidationError:
                host_invocation = None
        receipt = executor.reject_unexecuted(
            request,
            denials,
            host_invocation=host_invocation,
            guard_denial_reasons=guard_reasons,
        )
        return ScopedAwsReadResult(
            refusal=refusal,
            binding=binding,
            scope=scope,
            guard_check=guard,
            budget=budget,
            receipt=receipt,
            observation=raw_read_observation(receipt),
            journal_entry=None,
            adapter_invoked=False,
            aws_operations=aws_operations,
            plan=None,
            session=session,
        )

    def _finish(self, cache_key: tuple[str, str, str], result: ScopedAwsReadResult) -> ScopedAwsReadResult:
        with self._lock:
            self._cache.setdefault(cache_key, result)
            return self._cache[cache_key]


def _requested_window(request: UntrustedReadRequest) -> tuple[datetime, datetime] | None:
    arguments = dict(request.requested_arguments)
    start = arguments.get("start_utc")
    end = arguments.get("end_utc")
    if start is None and end is None:
        return None
    try:
        return _whole_second(start, "start_utc"), _whole_second(end, "end_utc")
    except ContractValidationError:
        return None


def _jsonable(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, tuple):
        return [item if isinstance(item, (str, int, float, bool)) or item is None else str(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return str(value)


def supported_read_catalog() -> dict[str, tuple[tuple[str, str], ...]]:
    """Published allowlisted tool/resource/operation pairs. Scan is absent."""

    catalog: dict[str, tuple[tuple[str, str], ...]] = {}
    for specialist in MasterSpecialist:
        pairs: list[tuple[str, str]] = []
        for tool_name in sorted(server_read_tools(specialist)):
            for resource_id, operation in sorted(
                server_read_resources(specialist, tool_name),
                key=lambda item: (item[0], item[1].value),
            ):
                if operation is ReadOperation.SCAN:
                    raise ContractValidationError("scan_not_permitted")
                pairs.append((tool_name, f"{resource_id}:{operation.value}"))
        catalog[specialist.value] = tuple(pairs)
    return catalog


__all__ = [
    "ABSENT_RECORD_IS_LOW_RISK",
    "ADAPTER_ORIGIN",
    "APPROVED_SOURCE_ADAPTER",
    "CALLER_ARGUMENTS_WIDEN_AUTHORIZATION",
    "CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT",
    "GRANT_CONSUMPTION_LIFECYCLE",
    "LEDGER_EXECUTION_STATE_STARTED",
    "EMPTY_PAGE_IS_VERIFIED_ZERO",
    "LIVE_AWS_OPERATION_PERMITTED",
    "PRODUCTION_SCOPED_AWS_READ_ENABLED",
    "REGION_LABEL_IS_SIGMET_POLYGON",
    "SPRINT3C_SCOPED_READ_SCHEMA_VERSION",
    "AwsReadPlan",
    "ConsumedAttemptReadBinding",
    "OfflineAwsReadClient",
    "ScopedAwsReadRefusal",
    "ScopedAwsReadResult",
    "ScopedAwsSourceError",
    "ScopedAwsTimeout",
    "Sprint3CScopedReadPort",
    "bind_offline_aws_read_client",
    "describe_consumed_attempt_read_binding",
    "issue_production_scoped_aws_read",
    "open_production_scoped_aws_read",
    "supported_read_catalog",
]
