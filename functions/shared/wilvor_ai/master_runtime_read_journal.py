"""Bounded journal for a read-only tool call that actually runs.

Sprint 3A could classify a caller-proposed event. This module is the next
step: a synchronous wrapper around an injected callable. Sprint 3B.1 accepts
only a deterministic local fake. The callable runs in this process. Nothing
here opens AWS, selects a production adapter, or dispatches a specialist.

Four states stay distinct:

1. Operation proposed. ``propose`` records the invocation, tool call, adapter
   label, and sanitized arguments. The callable has not run.
2. Operation started. ``execute`` reads the injected clock, then calls the fake.
3. Operation completed or failed. The journal reads the clock again and records
   completion, an exception, a timeout, or a cancellation.
4. Source-read authenticity independently established. A journal entry cannot
   enter this state. A finished trace, a matching source version, and a
   complete-looking fake result stay unauthenticated.

An empty row set does not mint a verified-zero claim. A fake result is not
aviation evidence. Failures are recorded without the exception text, and the
original exception is raised again.

PROPOSED and not available from this module:

- Authenticated source-read provenance.
- A production execution grant.
- A live Master dispatcher.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    JsonValue,
    QueryExecutionTrace,
    SourceCompleteness,
    SourceRecord,
    ToolResult,
    ToolResultStatus,
    _is_json_value,
    _validate_bounded_text,
    _validate_optional_count,
    _validate_utc,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_provenance_trace import ProposedRetrievalLimit
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.specialist_runtime_contracts import ExecutedToolInvocation


MASTER_RUNTIME_READ_JOURNAL_SCHEMA_VERSION = "wilvor.ai.master_runtime_read_journal.v1"

# Step 4 is not a product of recording steps 1-3.
AUTHENTICATED_SOURCE_READ_AVAILABLE = False
LOCAL_FAKE_EXECUTION_IS_SOURCE_AUTHENTICATION = False
FAKE_RESULT_IS_AVIATION_EVIDENCE = False
EMPTY_ROWS_CREATE_VERIFIED_ZERO = False
PRODUCTION_DISPATCHER_IMPLEMENTED = False
AWS_CONNECTION_IMPLEMENTED = False
LIVE_MASTER_EXECUTION_ENABLED = False
TRUSTED_RUNTIME_OBSERVATION_AVAILABLE = False

_MAX_ARGUMENTS = 32
_MAX_ARGUMENT_DEPTH = 8
_REDACTED = "[redacted]"
_PARTIAL_COMPLETENESS = frozenset({"PARTIAL", "INCOMPLETE", "LIMITED", "TRUNCATED"})
_SECRET_KEY_RE = re.compile(
    r"(password|passwd|secret|token|api[-_]?key|authorization|credential|"
    r"session|private[-_]?key|access[-_]?key)",
    re.IGNORECASE,
)
_ERROR_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_ALWAYS_GAPS = (
    "SOURCE_AUTHENTICITY_NOT_ESTABLISHED",
    "NO_TRUSTED_ADAPTER",
    "EXECUTION_NOT_AUTHORIZED",
    "POPULATION_SCOPE_NOT_ESTABLISHED",
)


class _JournalSeal:
    """Process-local mark for an entry this journal recorded."""


_RECORDED = _JournalSeal()


class ReadJournalPhase(str, Enum):
    """Lifecycle mark. A later phase does not prove source authenticity."""

    PROPOSED = "PROPOSED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReadJournalOutcome(str, Enum):
    """How the local call ended. Not an evidence admission status."""

    NOT_RUN = "NOT_RUN"
    COMPLETED = "COMPLETED"
    EXCEPTION = "EXCEPTION"
    TIMEOUT = "TIMEOUT"
    CANCELLATION = "CANCELLATION"
    DUPLICATE_TOOL_CALL = "DUPLICATE_TOOL_CALL"


class ReadJournalGap(str, Enum):
    """Provenance that remains unresolved after a local observation."""

    SOURCE_AUTHENTICITY_NOT_ESTABLISHED = "SOURCE_AUTHENTICITY_NOT_ESTABLISHED"
    LOCAL_FAKE_NOT_AVIATION_EVIDENCE = "LOCAL_FAKE_NOT_AVIATION_EVIDENCE"
    NO_TRUSTED_ADAPTER = "NO_TRUSTED_ADAPTER"
    EXECUTION_NOT_AUTHORIZED = "EXECUTION_NOT_AUTHORIZED"
    POPULATION_SCOPE_NOT_ESTABLISHED = "POPULATION_SCOPE_NOT_ESTABLISHED"
    SOURCE_VERSION_NOT_INTEGRITY_PROOF = "SOURCE_VERSION_NOT_INTEGRITY_PROOF"
    MISSING_SOURCE_IDENTITY = "MISSING_SOURCE_IDENTITY"
    MISSING_SOURCE_VERSION = "MISSING_SOURCE_VERSION"
    SOURCE_VERSION_MISMATCH = "SOURCE_VERSION_MISMATCH"
    ARGUMENT_MISMATCH = "ARGUMENT_MISMATCH"
    TOOL_IDENTITY_MISMATCH = "TOOL_IDENTITY_MISMATCH"
    PARTIAL_PAGINATION = "PARTIAL_PAGINATION"
    ROW_LIMIT_APPLIED = "ROW_LIMIT_APPLIED"
    TRUNCATED = "TRUNCATED"
    EMPTY_ROWS_NOT_VERIFIED_ZERO = "EMPTY_ROWS_NOT_VERIFIED_ZERO"
    RESULT_NOT_RETURNED = "RESULT_NOT_RETURNED"
    DUPLICATE_TOOL_CALL = "DUPLICATE_TOOL_CALL"
    FORGED_CALLER_EVENT = "FORGED_CALLER_EVENT"


class LocalFakeTimeout(Exception):
    """Timeout signal raised by a deterministic local fake.

    This is not an AWS or provider timeout. The journal records it and
    raises this same exception again.
    """


class LocalFakeCancellation(Exception):
    """Cancellation signal raised by a deterministic local fake.

    The journal records it and raises this same exception again.
    """


class ReadJournalError(Exception):
    """A journal outcome that is itself the failure the caller must see."""

    def __init__(self, entry: "RuntimeReadJournalEntry"):
        self.entry = entry
        super().__init__(entry.outcome.value)


class ReadJournalTimeout(ReadJournalError):
    """The injected clock passed the invocation's timeout budget."""


class ReadJournalCancelled(ReadJournalError):
    """The cancellation flag was set. The fake result is not admitted."""


class DuplicateToolCallError(ReadJournalError):
    """This tool-call id was already proposed or already started."""


class ReadCancellation:
    """Cooperative cancel flag. Setting it does not touch a remote system."""

    def __init__(self) -> None:
        self._flag = False
        self._lock = threading.Lock()

    def cancel(self) -> None:
        with self._lock:
            self._flag = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._flag


def mark_deterministic_local_fake(operation: Callable[[], object]) -> Callable[[], object]:
    """Label a callable as a Sprint 3B.1 local fake.

    The journal will not call an unmarked callable. The mark is not an
    execution grant and does not authenticate a source.
    """

    if not callable(operation):
        raise ContractValidationError("operation_not_callable")
    try:
        operation.wilvor_local_fake_read = True  # type: ignore[attr-defined]
        operation.wilvor_deterministic_local_fake = True  # type: ignore[attr-defined]
    except (AttributeError, TypeError) as exc:
        raise ContractValidationError("operation_not_local_fake") from exc
    return operation


def _token(value: object, field_name: str, *, optional: bool = False) -> str | None:
    errors = _validate_bounded_text(
        value,
        field_name,
        optional=optional,
        max_length=PROVENANCE_TOKEN_MAX_LENGTH,
    )
    if errors:
        raise ContractValidationError(errors)
    if value is None:
        return None
    if isinstance(value, str) and value != value.strip():
        raise ContractValidationError(f"invalid_{field_name}")
    return value  # type: ignore[return-value]


def _parse_utc(value: str) -> datetime:
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    return datetime.fromisoformat(text)


def _clock_instant(clock: object) -> str:
    reader = getattr(clock, "now_utc", None)
    if not callable(reader):
        raise ContractValidationError("invalid_journal_clock")
    instant = reader()
    errors = _validate_utc(instant, "journal_clock")
    if errors:
        raise ContractValidationError(errors)
    return instant


def _require_local_fake(operation: object) -> None:
    if not callable(operation):
        raise ContractValidationError("operation_not_local_fake")
    if getattr(operation, "wilvor_local_fake_read", False) is not True:
        raise ContractValidationError("operation_not_local_fake")
    if getattr(operation, "wilvor_deterministic_local_fake", False) is not True:
        raise ContractValidationError("operation_not_deterministic_local_fake")


def _sanitize_arguments(
    value: object,
    *,
    field_name: str,
    depth: int = 0,
) -> tuple[dict[str, JsonValue], bool]:
    if depth > _MAX_ARGUMENT_DEPTH or not isinstance(value, dict):
        raise ContractValidationError(f"invalid_{field_name}")
    if len(value) > _MAX_ARGUMENTS:
        raise ContractValidationError("excessive_arguments")
    cleaned: dict[str, JsonValue] = {}
    redacted = False
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > PROVENANCE_TOKEN_MAX_LENGTH:
            raise ContractValidationError(f"invalid_{field_name}")
        if not _is_json_value(item):
            raise ContractValidationError(f"invalid_{field_name}")
        if _SECRET_KEY_RE.search(key):
            cleaned[key] = _REDACTED
            redacted = True
            continue
        if isinstance(item, dict):
            nested, nested_redacted = _sanitize_arguments(
                item,
                field_name=field_name,
                depth=depth + 1,
            )
            cleaned[key] = nested
            redacted = redacted or nested_redacted
            continue
        if isinstance(item, list):
            cleaned_items: list[JsonValue] = []
            for entry in item:
                if isinstance(entry, dict):
                    nested, nested_redacted = _sanitize_arguments(
                        entry,
                        field_name=field_name,
                        depth=depth + 1,
                    )
                    cleaned_items.append(nested)
                    redacted = redacted or nested_redacted
                else:
                    cleaned_items.append(entry)
            cleaned[key] = cleaned_items
            continue
        cleaned[key] = item
    return cleaned, redacted


def _count(value: object, field_name: str) -> int | None:
    errors = _validate_optional_count(value, field_name)
    if errors:
        raise ContractValidationError(errors)
    return value  # type: ignore[return-value]


def _pagination_is_partial(pagination: "PaginationObservation | None") -> bool:
    if pagination is None:
        return False
    if pagination.has_more:
        return True
    if (
        pagination.page_index is not None
        and pagination.page_count is not None
        and pagination.page_index + 1 < pagination.page_count
    ):
        return True
    return False


def _gaps(*items: ReadJournalGap) -> tuple[ReadJournalGap, ...]:
    selected = set(items)
    selected.update(ReadJournalGap(name) for name in _ALWAYS_GAPS)
    return tuple(gap for gap in ReadJournalGap if gap in selected)


def _has_source_identity(entry: "RuntimeReadJournalEntry") -> bool:
    return bool(
        entry.source_records
        or entry.source_response_ref
        or entry.query_executions
        or entry.source_names
    )


@dataclass(frozen=True)
class PaginationObservation:
    """Page and row-limit facts supplied by the local operation.

    A continuation token is not stored. Only whether one was present is kept.
    """

    page_index: int | None = None
    page_count: int | None = None
    page_size: int | None = None
    rows_in_page: int | None = None
    row_limit: int | None = None
    has_more: bool = False
    next_page_token_present: bool = False

    def __post_init__(self) -> None:
        for name in ("page_index", "page_count", "page_size", "rows_in_page", "row_limit"):
            _count(getattr(self, name), name)
        if not isinstance(self.has_more, bool) or not isinstance(self.next_page_token_present, bool):
            raise ContractValidationError("invalid_pagination")


@dataclass(frozen=True)
class ReadInvocation:
    """Caller proposal for one local read. Not an authorization and not a result."""

    invocation_id: str
    tool_call_id: str
    tool_name: str
    adapter_identity: str
    requested_arguments: dict[str, JsonValue]
    master_request_id: str | None = None
    expected_source_version: str | None = None
    retry_of_tool_call_id: str | None = None
    timeout_budget_seconds: int | None = None
    arguments_redacted: bool = False

    def __post_init__(self) -> None:
        _token(self.invocation_id, "invocation_id")
        _token(self.tool_call_id, "tool_call_id")
        _token(self.tool_name, "tool_name")
        _token(self.adapter_identity, "adapter_identity")
        _token(self.master_request_id, "master_request_id", optional=True)
        _token(self.expected_source_version, "expected_source_version", optional=True)
        _token(self.retry_of_tool_call_id, "retry_of_tool_call_id", optional=True)
        if (
            self.retry_of_tool_call_id is not None
            and self.retry_of_tool_call_id == self.tool_call_id
        ):
            raise ContractValidationError("retry_targets_same_tool_call")
        if self.timeout_budget_seconds is not None:
            if (
                isinstance(self.timeout_budget_seconds, bool)
                or not isinstance(self.timeout_budget_seconds, int)
                or self.timeout_budget_seconds < 0
            ):
                raise ContractValidationError("invalid_timeout_budget_seconds")
        cleaned, redacted = _sanitize_arguments(
            self.requested_arguments,
            field_name="requested_arguments",
        )
        object.__setattr__(self, "requested_arguments", cleaned)
        object.__setattr__(self, "arguments_redacted", redacted)


@dataclass(frozen=True)
class LocalFakeReadResult:
    """Return value of a deterministic local fake. Not aviation evidence.

    ``next_page_token`` is accepted and discarded. The journal keeps only
    ``next_page_token_present``.
    """

    local_fake: bool = True
    tool_result: ToolResult | None = None
    source_records: tuple[SourceRecord, ...] = ()
    query_execution: QueryExecutionTrace | None = None
    completeness: SourceCompleteness | None = None
    executed_arguments: dict[str, JsonValue] | None = None
    source_response_ref: str | None = None
    source_version: str | None = None
    pagination: PaginationObservation | None = None
    truncated: bool = False
    rows_returned: int | None = None
    list_limit_default_applied: bool = False
    next_page_token: str | None = None

    def __post_init__(self) -> None:
        if self.local_fake is not True:
            raise ContractValidationError("result_not_local_fake")
        if self.tool_result is not None and type(self.tool_result) is not ToolResult:
            raise ContractValidationError("invalid_tool_result")
        if not isinstance(self.source_records, tuple) or any(
            type(item) is not SourceRecord for item in self.source_records
        ):
            raise ContractValidationError("invalid_source_records")
        if self.query_execution is not None and type(self.query_execution) is not QueryExecutionTrace:
            raise ContractValidationError("invalid_query_execution")
        if self.completeness is not None and type(self.completeness) is not SourceCompleteness:
            raise ContractValidationError("invalid_completeness")
        if self.pagination is not None and type(self.pagination) is not PaginationObservation:
            raise ContractValidationError("invalid_pagination")
        _token(self.source_response_ref, "source_response_ref", optional=True)
        _token(self.source_version, "source_version", optional=True)
        _count(self.rows_returned, "rows_returned")
        if not isinstance(self.truncated, bool) or not isinstance(self.list_limit_default_applied, bool):
            raise ContractValidationError("invalid_local_fake_result")
        if self.executed_arguments is not None:
            cleaned, _redacted = _sanitize_arguments(
                self.executed_arguments,
                field_name="executed_arguments",
            )
            object.__setattr__(self, "executed_arguments", cleaned)
        token_present = self.next_page_token is not None
        pagination = self.pagination
        if token_present:
            if pagination is None:
                pagination = PaginationObservation(next_page_token_present=True)
            elif not pagination.next_page_token_present:
                pagination = PaginationObservation(
                    page_index=pagination.page_index,
                    page_count=pagination.page_count,
                    page_size=pagination.page_size,
                    rows_in_page=pagination.rows_in_page,
                    row_limit=pagination.row_limit,
                    has_more=pagination.has_more,
                    next_page_token_present=True,
                )
            object.__setattr__(self, "pagination", pagination)
        object.__setattr__(self, "next_page_token", None)


@dataclass(frozen=True)
class RuntimeReadJournalEntry:
    """One journal record.

    ``recorded_by_journal`` is true only for an object this process's journal
    stored. A caller-built object is collapsed to an unrun proposal and cannot
    establish source authenticity. ``__bool__`` is false so the entry cannot
    be used as an execution gate.
    """

    attempt_id: str
    invocation: ReadInvocation
    lifecycle: tuple[ReadJournalPhase, ...]
    outcome: ReadJournalOutcome
    gaps: tuple[ReadJournalGap, ...]
    proposal_attempt_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_type: str | None = None
    result_exists: bool = False
    result_status: ToolResultStatus | None = None
    source_records: tuple[SourceRecord, ...] = ()
    source_names: tuple[str, ...] = ()
    query_executions: tuple[QueryExecutionTrace, ...] = ()
    completeness: SourceCompleteness | None = None
    source_response_ref: str | None = None
    observed_source_versions: tuple[str, ...] = ()
    source_version_mismatch: bool = False
    rows_returned: int | None = None
    pagination: PaginationObservation | None = None
    retrieval_limits: tuple[ProposedRetrievalLimit, ...] = ()
    executed_invocation: ExecutedToolInvocation | None = None
    executed_arguments_observed: bool = False
    arguments_match: bool | None = None
    arguments_redacted: bool = False
    tool_identity_matches: bool | None = None
    source_authenticity: MasterSourceAuthenticity = MasterSourceAuthenticity.NOT_ESTABLISHED
    production_authority: MasterProductionAuthority = MasterProductionAuthority.DENIED
    execution_authority: MasterRouteExecutionAuthority = MasterRouteExecutionAuthority.DENIED
    request_coverage: MasterRequestCoverage = MasterRequestCoverage.NOT_ESTABLISHED
    dispatch_permitted: bool = False
    aviation_evidence_admitted: bool = False
    result_admitted: bool = False
    verified_zero_minted: bool = False
    population_scope_established: bool = False
    trusted_runtime_observed: bool = False
    schema_version: str = MASTER_RUNTIME_READ_JOURNAL_SCHEMA_VERSION
    _journal_seal: object = None

    def __post_init__(self) -> None:
        if self.schema_version != MASTER_RUNTIME_READ_JOURNAL_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        self._deny_trust()
        if self._journal_seal is not _RECORDED:
            self._collapse_caller_event()
            return
        self._validate_recorded()

    def __bool__(self) -> bool:
        return False

    @property
    def recorded_by_journal(self) -> bool:
        return self._journal_seal is _RECORDED

    @property
    def operation_proposed(self) -> bool:
        return ReadJournalPhase.PROPOSED in self.lifecycle

    @property
    def operation_started(self) -> bool:
        return ReadJournalPhase.STARTED in self.lifecycle

    @property
    def operation_finished(self) -> bool:
        return (
            ReadJournalPhase.COMPLETED in self.lifecycle
            or ReadJournalPhase.FAILED in self.lifecycle
        )

    @property
    def source_read_authenticity_established(self) -> bool:
        """Step 4. A journal entry has no path that returns true."""

        return False

    @property
    def authenticated_source_read_available(self) -> bool:
        return AUTHENTICATED_SOURCE_READ_AVAILABLE

    @property
    def verified_zero_claim(self) -> None:
        """Empty rows do not become a verified-zero claim."""

        return None

    @property
    def invocation_id(self) -> str:
        return self.invocation.invocation_id

    @property
    def tool_call_id(self) -> str:
        return self.invocation.tool_call_id

    @property
    def tool_name(self) -> str:
        return self.invocation.tool_name

    @property
    def adapter_identity(self) -> str:
        return self.invocation.adapter_identity

    def to_dict(self) -> dict[str, JsonValue]:
        """Safe projection. Exception text and page tokens are omitted."""

        return {
            "schema_version": self.schema_version,
            "attempt_id": self.attempt_id,
            "proposal_attempt_id": self.proposal_attempt_id,
            "invocation_id": self.invocation_id,
            "master_request_id": self.invocation.master_request_id,
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "adapter_identity": self.adapter_identity,
            "retry_of_tool_call_id": self.invocation.retry_of_tool_call_id,
            "requested_arguments": dict(self.invocation.requested_arguments),
            "executed_arguments": (
                None
                if self.executed_invocation is None or not self.executed_arguments_observed
                else dict(self.executed_invocation.executed_arguments)
            ),
            "arguments_redacted": self.arguments_redacted,
            "arguments_match": self.arguments_match,
            "lifecycle": [phase.value for phase in self.lifecycle],
            "outcome": self.outcome.value,
            "error_type": self.error_type,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "result_exists": self.result_exists,
            "result_admitted": False,
            "result_status": None if self.result_status is None else self.result_status.value,
            "source_response_ref": self.source_response_ref,
            "source_names": list(self.source_names),
            "source_records": [record.to_dict() for record in self.source_records],
            "observed_source_versions": list(self.observed_source_versions),
            "expected_source_version": self.invocation.expected_source_version,
            "source_version_mismatch": self.source_version_mismatch,
            "query_executions": [item.to_dict() for item in self.query_executions],
            "completeness": None if self.completeness is None else self.completeness.to_dict(),
            "rows_returned": self.rows_returned,
            "pagination": None if self.pagination is None else {
                "page_index": self.pagination.page_index,
                "page_count": self.pagination.page_count,
                "page_size": self.pagination.page_size,
                "rows_in_page": self.pagination.rows_in_page,
                "row_limit": self.pagination.row_limit,
                "has_more": self.pagination.has_more,
                "next_page_token_present": self.pagination.next_page_token_present,
            },
            "retrieval_limits": [item.value for item in self.retrieval_limits],
            "gaps": [gap.value for gap in self.gaps],
            "operation_proposed": self.operation_proposed,
            "operation_started": self.operation_started,
            "operation_finished": self.operation_finished,
            "source_read_authenticity_established": False,
            "authenticated_source_read_available": False,
            "verified_zero_minted": False,
            "aviation_evidence_admitted": False,
            "population_scope_established": False,
            "trusted_runtime_observed": False,
            "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED.value,
            "production_authority": MasterProductionAuthority.DENIED.value,
            "execution_authority": MasterRouteExecutionAuthority.DENIED.value,
            "dispatch_permitted": False,
            "recorded_by_journal": self.recorded_by_journal,
        }

    @classmethod
    def from_caller(cls, payload: Mapping[str, object]) -> "RuntimeReadJournalEntry":
        """Build the non-observed residue of a caller-created event.

        Trust, authenticity, and verified-zero flags in ``payload`` are not read.
        The returned object is not inserted into a journal.
        """

        if not isinstance(payload, Mapping):
            raise ContractValidationError("forged_caller_journal_event")
        invocation = ReadInvocation(
            invocation_id="caller-event",
            tool_call_id="caller-event",
            tool_name="caller-event",
            adapter_identity="caller-event",
            requested_arguments={},
        )
        return cls(
            attempt_id="caller-forged",
            invocation=invocation,
            lifecycle=(ReadJournalPhase.COMPLETED,),
            outcome=ReadJournalOutcome.COMPLETED,
            gaps=_gaps(),
            result_exists=True,
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            verified_zero_minted=False,
            aviation_evidence_admitted=False,
        )

    def _deny_trust(self) -> None:
        if type(self.invocation) is not ReadInvocation:
            raise ContractValidationError("invalid_read_invocation")
        _token(self.attempt_id, "attempt_id")
        _token(self.proposal_attempt_id, "proposal_attempt_id", optional=True)
        if self.schema_version != MASTER_RUNTIME_READ_JOURNAL_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.production_authority is not MasterProductionAuthority.DENIED:
            raise ContractValidationError("production_authority_not_issued")
        if self.execution_authority is not MasterRouteExecutionAuthority.DENIED:
            raise ContractValidationError("execution_authority_not_granted")
        if self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED:
            raise ContractValidationError("request_coverage_not_established")
        if self.dispatch_permitted is not False:
            raise ContractValidationError("execution_not_granted")
        if self.aviation_evidence_admitted is not False:
            raise ContractValidationError("aviation_evidence_not_admitted")
        if self.result_admitted is not False:
            raise ContractValidationError("fake_result_not_admitted")
        if self.verified_zero_minted is not False:
            raise ContractValidationError("verified_zero_not_minted")
        if self.population_scope_established is not False:
            raise ContractValidationError("population_scope_not_established")
        if self.trusted_runtime_observed is not False:
            raise ContractValidationError("trusted_runtime_observation_not_available")

    def _collapse_caller_event(self) -> None:
        object.__setattr__(self, "_journal_seal", None)
        object.__setattr__(self, "lifecycle", (ReadJournalPhase.PROPOSED,))
        object.__setattr__(self, "outcome", ReadJournalOutcome.NOT_RUN)
        object.__setattr__(self, "started_at", None)
        object.__setattr__(self, "finished_at", None)
        object.__setattr__(self, "error_type", None)
        object.__setattr__(self, "result_exists", False)
        object.__setattr__(self, "result_status", None)
        object.__setattr__(self, "source_records", ())
        object.__setattr__(self, "source_names", ())
        object.__setattr__(self, "query_executions", ())
        object.__setattr__(self, "completeness", None)
        object.__setattr__(self, "source_response_ref", None)
        object.__setattr__(self, "observed_source_versions", ())
        object.__setattr__(self, "source_version_mismatch", False)
        object.__setattr__(self, "rows_returned", None)
        object.__setattr__(self, "pagination", None)
        object.__setattr__(self, "retrieval_limits", ())
        object.__setattr__(self, "executed_invocation", None)
        object.__setattr__(self, "executed_arguments_observed", False)
        object.__setattr__(self, "arguments_match", None)
        object.__setattr__(self, "tool_identity_matches", None)
        object.__setattr__(
            self,
            "gaps",
            _gaps(ReadJournalGap.FORGED_CALLER_EVENT),
        )

    def _validate_recorded(self) -> None:
        _clock_field(self.started_at, "started_at")
        _clock_field(self.finished_at, "finished_at")
        if self.error_type is not None and (
            not isinstance(self.error_type, str) or _ERROR_TYPE_RE.fullmatch(self.error_type) is None
        ):
            raise ContractValidationError("invalid_error_type")
        if not isinstance(self.lifecycle, tuple) or any(
            not isinstance(phase, ReadJournalPhase) for phase in self.lifecycle
        ):
            raise ContractValidationError("invalid_lifecycle")
        if not isinstance(self.outcome, ReadJournalOutcome):
            raise ContractValidationError("invalid_outcome")
        if not isinstance(self.gaps, tuple) or any(
            not isinstance(gap, ReadJournalGap) for gap in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if len(self.gaps) != len(set(self.gaps)):
            raise ContractValidationError("invalid_provenance_gaps")
        if ReadJournalGap.FORGED_CALLER_EVENT in self.gaps:
            raise ContractValidationError("forged_caller_journal_event")
        for name in _ALWAYS_GAPS:
            if ReadJournalGap(name) not in self.gaps:
                raise ContractValidationError("invalid_provenance_gaps")
        if not isinstance(self.result_exists, bool):
            raise ContractValidationError("invalid_result_exists")
        if self.result_status is not None and not isinstance(self.result_status, ToolResultStatus):
            raise ContractValidationError("invalid_result_status")
        if not isinstance(self.source_records, tuple) or any(
            type(item) is not SourceRecord for item in self.source_records
        ):
            raise ContractValidationError("invalid_source_records")
        if not isinstance(self.source_names, tuple) or any(
            not isinstance(item, str) for item in self.source_names
        ):
            raise ContractValidationError("invalid_source_names")
        if not isinstance(self.query_executions, tuple) or any(
            type(item) is not QueryExecutionTrace for item in self.query_executions
        ):
            raise ContractValidationError("invalid_query_executions")
        if self.completeness is not None and type(self.completeness) is not SourceCompleteness:
            raise ContractValidationError("invalid_completeness")
        if self.pagination is not None and type(self.pagination) is not PaginationObservation:
            raise ContractValidationError("invalid_pagination")
        if not isinstance(self.retrieval_limits, tuple) or any(
            not isinstance(item, ProposedRetrievalLimit) for item in self.retrieval_limits
        ):
            raise ContractValidationError("invalid_retrieval_limits")
        if len(self.retrieval_limits) != len(set(self.retrieval_limits)):
            raise ContractValidationError("invalid_retrieval_limits")
        if self.executed_invocation is not None and (
            type(self.executed_invocation) is not ExecutedToolInvocation
        ):
            raise ContractValidationError("invalid_executed_tool_invocation")
        _count(self.rows_returned, "rows_returned")
        self._validate_phase_consistency()
        self._validate_gap_consistency()

    def _validate_phase_consistency(self) -> None:
        started = self.operation_started
        if self.outcome is ReadJournalOutcome.NOT_RUN:
            expected = (ReadJournalPhase.PROPOSED,)
            if self.lifecycle != expected or self.started_at is not None or self.finished_at is not None:
                raise ContractValidationError("invalid_lifecycle")
            if self.result_exists or self.executed_invocation is not None:
                raise ContractValidationError("invalid_lifecycle")
            return
        if self.outcome is ReadJournalOutcome.DUPLICATE_TOOL_CALL:
            if self.lifecycle != (ReadJournalPhase.PROPOSED, ReadJournalPhase.FAILED):
                raise ContractValidationError("invalid_lifecycle")
            if self.started_at is not None or self.finished_at is None or started:
                raise ContractValidationError("invalid_lifecycle")
            return
        if self.outcome is ReadJournalOutcome.COMPLETED:
            if self.lifecycle != (
                ReadJournalPhase.PROPOSED,
                ReadJournalPhase.STARTED,
                ReadJournalPhase.COMPLETED,
            ):
                raise ContractValidationError("invalid_lifecycle")
            if self.started_at is None or self.finished_at is None:
                raise ContractValidationError("invalid_lifecycle")
        elif self.outcome is ReadJournalOutcome.CANCELLATION and self.started_at is None:
            if self.lifecycle != (ReadJournalPhase.PROPOSED, ReadJournalPhase.FAILED):
                raise ContractValidationError("invalid_lifecycle")
            if self.finished_at is None:
                raise ContractValidationError("invalid_lifecycle")
        elif self.outcome in (
            ReadJournalOutcome.EXCEPTION,
            ReadJournalOutcome.TIMEOUT,
            ReadJournalOutcome.CANCELLATION,
        ):
            if self.lifecycle != (
                ReadJournalPhase.PROPOSED,
                ReadJournalPhase.STARTED,
                ReadJournalPhase.FAILED,
            ):
                raise ContractValidationError("invalid_lifecycle")
            if self.started_at is None or self.finished_at is None:
                raise ContractValidationError("invalid_lifecycle")
        else:
            raise ContractValidationError("invalid_outcome")
        if started and self.executed_invocation is None:
            raise ContractValidationError("invalid_executed_tool_invocation")
        if not started and self.executed_invocation is not None:
            raise ContractValidationError("invalid_executed_tool_invocation")
        if self.outcome is ReadJournalOutcome.EXCEPTION and self.error_type is None:
            raise ContractValidationError("invalid_error_type")
        if self.started_at is not None and self.finished_at is not None:
            if _parse_utc(self.finished_at) < _parse_utc(self.started_at):
                raise ContractValidationError("journal_clock_moved_backward")

    def _validate_gap_consistency(self) -> None:
        started = self.operation_started
        if started and ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE not in self.gaps:
            raise ContractValidationError("invalid_provenance_gaps")
        if not started and ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE in self.gaps:
            raise ContractValidationError("invalid_provenance_gaps")
        if self.observed_source_versions:
            if ReadJournalGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF not in self.gaps:
                raise ContractValidationError("invalid_provenance_gaps")
        elif started and ReadJournalGap.MISSING_SOURCE_VERSION not in self.gaps:
            raise ContractValidationError("invalid_provenance_gaps")
        if self.source_version_mismatch is not (
            ReadJournalGap.SOURCE_VERSION_MISMATCH in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if _has_source_identity(self) is (
            ReadJournalGap.MISSING_SOURCE_IDENTITY in self.gaps
        ) and started:
            raise ContractValidationError("invalid_provenance_gaps")
        if not started and ReadJournalGap.MISSING_SOURCE_IDENTITY in self.gaps:
            raise ContractValidationError("invalid_provenance_gaps")
        if (self.rows_returned == 0) is not (
            ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if _pagination_is_partial(self.pagination) is not (
            ReadJournalGap.PARTIAL_PAGINATION in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if (ProposedRetrievalLimit.RESULT_TRUNCATED in self.retrieval_limits) is not (
            ReadJournalGap.TRUNCATED in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if (ProposedRetrievalLimit.ROW_LIMIT in self.retrieval_limits) is not (
            ReadJournalGap.ROW_LIMIT_APPLIED in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if (self.arguments_match is False) is not (
            ReadJournalGap.ARGUMENT_MISMATCH in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if (self.tool_identity_matches is False) is not (
            ReadJournalGap.TOOL_IDENTITY_MISMATCH in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if (self.outcome is ReadJournalOutcome.DUPLICATE_TOOL_CALL) is not (
            ReadJournalGap.DUPLICATE_TOOL_CALL in self.gaps
        ):
            raise ContractValidationError("invalid_provenance_gaps")
        if self.outcome is ReadJournalOutcome.COMPLETED and not self.result_exists:
            if ReadJournalGap.RESULT_NOT_RETURNED not in self.gaps:
                raise ContractValidationError("invalid_provenance_gaps")


def _clock_field(value: object, field_name: str) -> None:
    errors = _validate_utc(value, field_name, optional=True)
    if errors:
        raise ContractValidationError(errors)


class MasterRuntimeReadJournal:
    """Records propose, start, and finish for local read-only fakes.

    The journal does not establish source-read authenticity. ``__bool__`` is
    false. There is no AWS client and no production dispatcher.
    """

    def __init__(self, clock: object):
        if not callable(getattr(clock, "now_utc", None)):
            raise ContractValidationError("invalid_journal_clock")
        self._clock = clock
        self._lock = threading.Lock()
        self._order: list[str] = []
        self._by_attempt: dict[str, RuntimeReadJournalEntry] = {}
        self._by_tool: dict[str, str] = {}
        self._consumed: set[str] = set()
        self._seq = 0

    def __bool__(self) -> bool:
        return False

    def propose(self, invocation: ReadInvocation) -> RuntimeReadJournalEntry:
        """Record step 1. The callable is not invoked."""

        if type(invocation) is not ReadInvocation:
            raise ContractValidationError("invalid_read_invocation")
        with self._lock:
            if invocation.tool_call_id in self._by_tool:
                entry = self._duplicate_entry(invocation, proposal_attempt_id=self._by_tool[invocation.tool_call_id])
                self._store(entry)
                raise DuplicateToolCallError(entry)
            entry = self._proposal_entry(invocation)
            self._store(entry)
            self._by_tool[invocation.tool_call_id] = entry.attempt_id
            return entry

    def execute(
        self,
        operation: Callable[[], object],
        proposal: RuntimeReadJournalEntry,
        *,
        cancellation: ReadCancellation | None = None,
    ) -> RuntimeReadJournalEntry:
        """Run a local fake and record steps 2 and 3.

        The proposal must be an entry this journal stored. A caller-built
        stand-in is rejected and the callable is not run. Source authenticity
        stays unestablished whether the fake returns, raises, times out, or
        is cancelled.
        """

        _require_local_fake(operation)
        if cancellation is not None and type(cancellation) is not ReadCancellation:
            raise ContractValidationError("invalid_cancellation")
        with self._lock:
            self._claim(proposal)
            if cancellation is not None and cancellation.is_cancelled():
                entry = self._terminal_entry(
                    proposal,
                    outcome=ReadJournalOutcome.CANCELLATION,
                    started_at=None,
                    finished_at=_clock_instant(self._clock),
                    error_type="cancellation",
                    result=None,
                    error=None,
                )
                self._store(entry)
                raise ReadJournalCancelled(entry)
            started_at = _clock_instant(self._clock)

        outcome = ReadJournalOutcome.COMPLETED
        error: BaseException | None = None
        result: LocalFakeReadResult | None = None
        try:
            returned = operation()
            result = _coerce_result(returned)
        except LocalFakeTimeout as exc:
            outcome = ReadJournalOutcome.TIMEOUT
            error = exc
        except LocalFakeCancellation as exc:
            outcome = ReadJournalOutcome.CANCELLATION
            error = exc
        except Exception as exc:
            outcome = ReadJournalOutcome.EXCEPTION
            error = exc
            result = None

        with self._lock:
            finished_at = _clock_instant(self._clock)
            if _parse_utc(finished_at) < _parse_utc(started_at):
                entry = self._terminal_entry(
                    proposal,
                    outcome=ReadJournalOutcome.EXCEPTION,
                    started_at=started_at,
                    finished_at=started_at,
                    error_type="ContractValidationError",
                    result=None,
                    error=None,
                )
                self._store(entry)
                raise ContractValidationError("journal_clock_moved_backward")
            if (
                outcome is ReadJournalOutcome.COMPLETED
                and cancellation is not None
                and cancellation.is_cancelled()
            ):
                outcome = ReadJournalOutcome.CANCELLATION
                error = None
            elif outcome is ReadJournalOutcome.COMPLETED and _budget_exceeded(
                started_at,
                finished_at,
                proposal.invocation.timeout_budget_seconds,
            ):
                outcome = ReadJournalOutcome.TIMEOUT
                error = None
            error_type = _error_type(outcome, error)
            entry = self._terminal_entry(
                proposal,
                outcome=outcome,
                started_at=started_at,
                finished_at=finished_at,
                error_type=error_type,
                result=result if outcome is not ReadJournalOutcome.EXCEPTION else result,
                error=error,
            )
            self._store(entry)

        if error is not None:
            raise error
        if outcome is ReadJournalOutcome.TIMEOUT:
            raise ReadJournalTimeout(entry)
        if outcome is ReadJournalOutcome.CANCELLATION:
            raise ReadJournalCancelled(entry)
        return entry

    def reject_caller_event(self, payload: object) -> None:
        """Refuse a caller-created journal event. The journal is unchanged."""

        if not isinstance(payload, Mapping):
            raise ContractValidationError("forged_caller_journal_event")
        raise ContractValidationError("forged_caller_journal_event")

    def entries(self) -> tuple[RuntimeReadJournalEntry, ...]:
        with self._lock:
            return tuple(self._by_attempt[attempt_id] for attempt_id in self._order)

    def proposal_for(self, tool_call_id: str) -> RuntimeReadJournalEntry:
        with self._lock:
            for attempt_id in self._order:
                entry = self._by_attempt[attempt_id]
                if (
                    entry.tool_call_id == tool_call_id
                    and entry.outcome is ReadJournalOutcome.NOT_RUN
                    and entry.recorded_by_journal
                ):
                    return entry
        raise ContractValidationError("proposal_not_recorded")

    def execution_for(self, tool_call_id: str) -> RuntimeReadJournalEntry:
        with self._lock:
            for attempt_id in self._order:
                entry = self._by_attempt[attempt_id]
                if entry.tool_call_id == tool_call_id and entry.operation_started:
                    return entry
        raise ContractValidationError("execution_not_recorded")

    def _claim(self, proposal: object) -> None:
        if type(proposal) is not RuntimeReadJournalEntry:
            raise ContractValidationError("forged_caller_journal_event")
        stored = self._by_attempt.get(proposal.attempt_id)
        if stored is not proposal or proposal._journal_seal is not _RECORDED:
            raise ContractValidationError("forged_caller_journal_event")
        if (
            proposal.outcome is not ReadJournalOutcome.NOT_RUN
            or proposal.lifecycle != (ReadJournalPhase.PROPOSED,)
        ):
            raise ContractValidationError("forged_caller_journal_event")
        if proposal.attempt_id in self._consumed:
            entry = self._duplicate_entry(
                proposal.invocation,
                proposal_attempt_id=proposal.attempt_id,
            )
            self._store(entry)
            raise DuplicateToolCallError(entry)
        self._consumed.add(proposal.attempt_id)

    def _next_attempt_id(self) -> str:
        self._seq += 1
        return f"read-attempt-{self._seq}"

    def _store(self, entry: RuntimeReadJournalEntry) -> None:
        self._by_attempt[entry.attempt_id] = entry
        self._order.append(entry.attempt_id)

    def _proposal_entry(self, invocation: ReadInvocation) -> RuntimeReadJournalEntry:
        return RuntimeReadJournalEntry(
            attempt_id=self._next_attempt_id(),
            invocation=invocation,
            lifecycle=(ReadJournalPhase.PROPOSED,),
            outcome=ReadJournalOutcome.NOT_RUN,
            gaps=_gaps(),
            _journal_seal=_RECORDED,
        )

    def _duplicate_entry(
        self,
        invocation: ReadInvocation,
        *,
        proposal_attempt_id: str,
    ) -> RuntimeReadJournalEntry:
        return RuntimeReadJournalEntry(
            attempt_id=self._next_attempt_id(),
            proposal_attempt_id=proposal_attempt_id,
            invocation=invocation,
            lifecycle=(ReadJournalPhase.PROPOSED, ReadJournalPhase.FAILED),
            outcome=ReadJournalOutcome.DUPLICATE_TOOL_CALL,
            gaps=_gaps(ReadJournalGap.DUPLICATE_TOOL_CALL),
            finished_at=_clock_instant(self._clock),
            _journal_seal=_RECORDED,
        )

    def _terminal_entry(
        self,
        proposal: RuntimeReadJournalEntry,
        *,
        outcome: ReadJournalOutcome,
        started_at: str | None,
        finished_at: str,
        error_type: str | None,
        result: LocalFakeReadResult | None,
        error: BaseException | None,
    ) -> RuntimeReadJournalEntry:
        observed = _observe(proposal.invocation, result if error is None or result is not None else None)
        if outcome is ReadJournalOutcome.EXCEPTION:
            observed = _observe(proposal.invocation, None)
        started = started_at is not None
        lifecycle: tuple[ReadJournalPhase, ...]
        if not started:
            lifecycle = (ReadJournalPhase.PROPOSED, ReadJournalPhase.FAILED)
        elif outcome is ReadJournalOutcome.COMPLETED:
            lifecycle = (
                ReadJournalPhase.PROPOSED,
                ReadJournalPhase.STARTED,
                ReadJournalPhase.COMPLETED,
            )
        else:
            lifecycle = (
                ReadJournalPhase.PROPOSED,
                ReadJournalPhase.STARTED,
                ReadJournalPhase.FAILED,
            )
        if not started:
            observed = _EMPTY_OBSERVATION
        gaps = _derive_gaps(outcome, started, observed)
        executed = None
        if started:
            executed_arguments = (
                observed.executed_arguments if observed.executed_arguments_observed else {}
            )
            executed = ExecutedToolInvocation(
                tool_name=proposal.tool_name,
                tool_call_id=proposal.tool_call_id,
                requested_arguments=dict(proposal.invocation.requested_arguments),
                executed_arguments=dict(executed_arguments),
                list_limit_default_applied=observed.list_limit_default_applied,
                canonical_key=proposal.tool_call_id,
            )
        return RuntimeReadJournalEntry(
            attempt_id=self._next_attempt_id(),
            proposal_attempt_id=proposal.attempt_id,
            invocation=proposal.invocation,
            lifecycle=lifecycle,
            outcome=outcome,
            gaps=gaps,
            started_at=started_at,
            finished_at=finished_at,
            error_type=error_type,
            result_exists=observed.result_exists if started else False,
            result_status=observed.result_status if started else None,
            source_records=observed.source_records if started else (),
            source_names=observed.source_names if started else (),
            query_executions=observed.query_executions if started else (),
            completeness=observed.completeness if started else None,
            source_response_ref=observed.source_response_ref if started else None,
            observed_source_versions=observed.observed_source_versions if started else (),
            source_version_mismatch=observed.source_version_mismatch if started else False,
            rows_returned=observed.rows_returned if started else None,
            pagination=observed.pagination if started else None,
            retrieval_limits=observed.retrieval_limits if started else (),
            executed_invocation=executed,
            executed_arguments_observed=observed.executed_arguments_observed if started else False,
            arguments_match=observed.arguments_match if started else None,
            arguments_redacted=observed.arguments_redacted or proposal.invocation.arguments_redacted,
            tool_identity_matches=observed.tool_identity_matches if started else None,
            _journal_seal=_RECORDED,
        )


@dataclass(frozen=True)
class _Observation:
    result_exists: bool = False
    result_status: ToolResultStatus | None = None
    source_records: tuple[SourceRecord, ...] = ()
    source_names: tuple[str, ...] = ()
    query_executions: tuple[QueryExecutionTrace, ...] = ()
    completeness: SourceCompleteness | None = None
    source_response_ref: str | None = None
    observed_source_versions: tuple[str, ...] = ()
    source_version_mismatch: bool = False
    rows_returned: int | None = None
    pagination: PaginationObservation | None = None
    retrieval_limits: tuple[ProposedRetrievalLimit, ...] = ()
    executed_arguments: dict[str, JsonValue] | None = None
    executed_arguments_observed: bool = False
    arguments_match: bool | None = None
    arguments_redacted: bool = False
    tool_identity_matches: bool | None = None
    list_limit_default_applied: bool = False


_EMPTY_OBSERVATION = _Observation()


def _coerce_result(value: object) -> LocalFakeReadResult:
    if value is None:
        return LocalFakeReadResult()
    if type(value) is LocalFakeReadResult:
        return value
    if type(value) is ToolResult:
        return LocalFakeReadResult(tool_result=value)
    raise ContractValidationError("local_fake_result_required")


def _error_type(outcome: ReadJournalOutcome, error: BaseException | None) -> str | None:
    if error is not None:
        return type(error).__name__
    if outcome is ReadJournalOutcome.TIMEOUT:
        return "timeout"
    if outcome is ReadJournalOutcome.CANCELLATION:
        return "cancellation"
    return None


def _budget_exceeded(started_at: str, finished_at: str, budget_seconds: int | None) -> bool:
    if budget_seconds is None:
        return False
    elapsed = (_parse_utc(finished_at) - _parse_utc(started_at)).total_seconds()
    return elapsed > budget_seconds


def _partial_status(completeness: SourceCompleteness | None) -> bool:
    return (
        completeness is not None
        and completeness.status is not None
        and completeness.status in _PARTIAL_COMPLETENESS
    )


def _observe(invocation: ReadInvocation, result: LocalFakeReadResult | None) -> _Observation:
    if result is None:
        return _Observation(
            arguments_redacted=invocation.arguments_redacted,
            executed_arguments_observed=False,
            arguments_match=None,
        )
    records: list[SourceRecord] = list(result.source_records)
    names: list[str] = []
    queries: list[QueryExecutionTrace] = []
    completeness = result.completeness
    if result.query_execution is not None:
        queries.append(result.query_execution)
    tool_result = result.tool_result
    if tool_result is not None:
        for evidence in tool_result.evidence:
            names.append(evidence.source)
            for record in evidence.source_records:
                if record not in records:
                    records.append(record)
            for query in evidence.query_executions:
                if query not in queries:
                    queries.append(query)
            if completeness is None and evidence.completeness is not None:
                completeness = evidence.completeness
    versions: list[str] = []
    if result.source_version is not None:
        versions.append(result.source_version)
    for record in records:
        if record.source_version is not None and record.source_version not in versions:
            versions.append(record.source_version)
    mismatch = len(set(versions)) > 1 or (
        invocation.expected_source_version is not None
        and any(version != invocation.expected_source_version for version in versions)
    )
    rows = result.rows_returned
    if rows is None and tool_result is not None and isinstance(tool_result.data, list):
        rows = len(tool_result.data)
    if rows is None and len(queries) == 1 and queries[0].rows_returned is not None:
        rows = queries[0].rows_returned
    pagination = result.pagination
    truncated = result.truncated or _partial_status(completeness)
    if tool_result is not None and tool_result.status is ToolResultStatus.PARTIAL:
        truncated = True
    row_limited = result.list_limit_default_applied or (
        pagination is not None and pagination.row_limit is not None
    )
    limits: list[ProposedRetrievalLimit] = []
    if truncated:
        limits.append(ProposedRetrievalLimit.RESULT_TRUNCATED)
    if row_limited:
        limits.append(ProposedRetrievalLimit.ROW_LIMIT)
    if pagination is not None:
        limits.append(ProposedRetrievalLimit.PAGE_LIMIT)
    executed_observed = result.executed_arguments is not None
    executed_arguments = result.executed_arguments if executed_observed else None
    arguments_match = None
    if executed_observed:
        arguments_match = executed_arguments == invocation.requested_arguments
    tool_identity_matches = None
    if tool_result is not None:
        tool_identity_matches = (
            tool_result.tool_name == invocation.tool_name
            and tool_result.tool_call_id == invocation.tool_call_id
        )
    redacted = invocation.arguments_redacted
    if executed_arguments is not None and any(
        value == _REDACTED for value in _string_values(executed_arguments)
    ):
        redacted = True
    result_exists = not (
        tool_result is None
        and not records
        and result.source_response_ref is None
        and result.source_version is None
        and result.query_execution is None
        and result.executed_arguments is None
        and pagination is None
        and result.rows_returned is None
        and completeness is None
    )
    return _Observation(
        result_exists=result_exists,
        result_status=None if tool_result is None else tool_result.status,
        source_records=tuple(records),
        source_names=tuple(dict.fromkeys(names)),
        query_executions=tuple(queries),
        completeness=completeness,
        source_response_ref=result.source_response_ref,
        observed_source_versions=tuple(versions),
        source_version_mismatch=mismatch,
        rows_returned=rows,
        pagination=pagination,
        retrieval_limits=tuple(limits),
        executed_arguments=executed_arguments,
        executed_arguments_observed=executed_observed,
        arguments_match=arguments_match,
        arguments_redacted=redacted,
        tool_identity_matches=tool_identity_matches,
        list_limit_default_applied=result.list_limit_default_applied,
    )


def _string_values(value: object) -> list[str]:
    found: list[str] = []
    if isinstance(value, str):
        found.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(_string_values(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_string_values(item))
    return found


def _derive_gaps(
    outcome: ReadJournalOutcome,
    started: bool,
    observed: _Observation,
) -> tuple[ReadJournalGap, ...]:
    extra: list[ReadJournalGap] = []
    if started:
        extra.append(ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE)
    if observed.observed_source_versions:
        extra.append(ReadJournalGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF)
    elif started:
        extra.append(ReadJournalGap.MISSING_SOURCE_VERSION)
    if observed.source_version_mismatch:
        extra.append(ReadJournalGap.SOURCE_VERSION_MISMATCH)
    has_identity = bool(
        observed.source_records
        or observed.source_response_ref
        or observed.query_executions
        or observed.source_names
    )
    if started and not has_identity:
        extra.append(ReadJournalGap.MISSING_SOURCE_IDENTITY)
    if observed.arguments_match is False:
        extra.append(ReadJournalGap.ARGUMENT_MISMATCH)
    if observed.tool_identity_matches is False:
        extra.append(ReadJournalGap.TOOL_IDENTITY_MISMATCH)
    if _pagination_is_partial(observed.pagination):
        extra.append(ReadJournalGap.PARTIAL_PAGINATION)
    if ProposedRetrievalLimit.ROW_LIMIT in observed.retrieval_limits:
        extra.append(ReadJournalGap.ROW_LIMIT_APPLIED)
    if ProposedRetrievalLimit.RESULT_TRUNCATED in observed.retrieval_limits:
        extra.append(ReadJournalGap.TRUNCATED)
    if observed.rows_returned == 0:
        extra.append(ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO)
    if outcome is ReadJournalOutcome.COMPLETED and not observed.result_exists:
        extra.append(ReadJournalGap.RESULT_NOT_RETURNED)
    if outcome is ReadJournalOutcome.DUPLICATE_TOOL_CALL:
        extra.append(ReadJournalGap.DUPLICATE_TOOL_CALL)
    return _gaps(*extra)
