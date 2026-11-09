"""Runtime read journal records a local fake without authenticating it."""

from __future__ import annotations

import json
import threading

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    QueryExecutionTrace,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_contracts import MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_provenance_trace import ProposedRetrievalLimit
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_runtime_read_journal import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    AWS_CONNECTION_IMPLEMENTED,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO,
    FAKE_RESULT_IS_AVIATION_EVIDENCE,
    LIVE_MASTER_EXECUTION_ENABLED,
    LOCAL_FAKE_EXECUTION_IS_SOURCE_AUTHENTICATION,
    PRODUCTION_DISPATCHER_IMPLEMENTED,
    TRUSTED_RUNTIME_OBSERVATION_AVAILABLE,
    DuplicateToolCallError,
    LocalFakeCancellation,
    MasterRuntimeReadJournal,
    PaginationObservation,
    ReadCancellation,
    ReadInvocation,
    ReadJournalCancelled,
    ReadJournalGap,
    ReadJournalOutcome,
    ReadJournalPhase,
    ReadJournalTimeout,
    RuntimeReadJournalEntry,
    mark_deterministic_local_fake,
)
from wilvor_ai.master_runtime_read_journal import LocalFakeReadResult


T0 = "2026-01-15T00:00:00Z"
T1 = "2026-01-15T00:00:01Z"
SECRET = "wilvor-test-secret-value"
PAGE_TOKEN = "page-token-secret"


class ScriptedClock:
    def __init__(self, instants: tuple[str, ...]):
        self._instants = list(instants)

    def now_utc(self) -> str:
        if not self._instants:
            raise AssertionError("clock exhausted")
        return self._instants.pop(0)


class MonotonicClock:
    def __init__(self) -> None:
        self._tick = 0
        self._lock = threading.Lock()

    def now_utc(self) -> str:
        with self._lock:
            self._tick += 1
            return f"2026-01-15T00:01:{self._tick:02d}Z"


def _invocation(**overrides: object) -> ReadInvocation:
    values: dict[str, object] = {
        "invocation_id": "inv-1",
        "tool_call_id": "tool-call-1",
        "tool_name": "describe_hazard",
        "adapter_identity": "local-fake-adapter",
        "requested_arguments": {"airport": "KSEA"},
        "master_request_id": "master-req-1",
        "expected_source_version": "v1",
    }
    values.update(overrides)
    return ReadInvocation(**values)  # type: ignore[arg-type]


def _record(
    record_id: str = "rec-1",
    source_version: str | None = "v1",
) -> SourceRecord:
    return SourceRecord(
        record_id=record_id,
        source_version=source_version,
        event_timestamp_utc=T0,
    )


def _evidence(
    tool_call_id: str = "tool-call-1",
    records: tuple[SourceRecord, ...] = (),
    **overrides: object,
) -> Evidence:
    values: dict[str, object] = {
        "source": "fixture-source",
        "source_records": records or (_record(),),
        "query_timestamp_utc": T0,
        "freshness_status": FreshnessStatus.FRESH,
        "confidence": ConfidenceLevel.LOW,
        "limitations": (),
        "tool_call_id": tool_call_id,
        "temporal_scope": TemporalScope.CURRENT,
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


def _tool_result(
    tool_call_id: str = "tool-call-1",
    evidence: Evidence | None = None,
    **overrides: object,
) -> ToolResult:
    item = evidence if evidence is not None else _evidence(tool_call_id)
    values: dict[str, object] = {
        "tool_name": "describe_hazard",
        "tool_call_id": tool_call_id,
        "status": ToolResultStatus.SUCCESS,
        "temporal_scope": TemporalScope.CURRENT,
        "data": [{"hazard_id": "fixture-row"}],
        "evidence": (item,),
        "as_of_utc": T0,
        "limitations": (),
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _fake(result: object):
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        return result

    mark_deterministic_local_fake(operation)
    operation.calls = calls  # type: ignore[attr-defined]
    return operation


def _assert_closed(entry: RuntimeReadJournalEntry) -> None:
    assert entry.source_read_authenticity_established is False
    assert entry.authenticated_source_read_available is False
    assert entry.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert entry.production_authority is MasterProductionAuthority.DENIED
    assert entry.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert entry.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert entry.dispatch_permitted is False
    assert entry.aviation_evidence_admitted is False
    assert entry.result_admitted is False
    assert entry.verified_zero_minted is False
    assert entry.verified_zero_claim is None
    assert entry.population_scope_established is False
    assert entry.trusted_runtime_observed is False
    assert bool(entry) is False
    assert ReadJournalGap.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in entry.gaps
    assert ReadJournalGap.NO_TRUSTED_ADAPTER in entry.gaps
    assert ReadJournalGap.EXECUTION_NOT_AUTHORIZED in entry.gaps
    assert ReadJournalGap.POPULATION_SCOPE_NOT_ESTABLISHED in entry.gaps
    assert AUTHENTICATED_SOURCE_READ_AVAILABLE is False
    assert LOCAL_FAKE_EXECUTION_IS_SOURCE_AUTHENTICATION is False
    assert FAKE_RESULT_IS_AVIATION_EVIDENCE is False
    assert EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
    assert PRODUCTION_DISPATCHER_IMPLEMENTED is False
    assert AWS_CONNECTION_IMPLEMENTED is False
    assert LIVE_MASTER_EXECUTION_ENABLED is False
    assert TRUSTED_RUNTIME_OBSERVATION_AVAILABLE is False


def test_successful_fake_read_records_lifecycle_without_source_authentication():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    invocation = _invocation()
    proposal = journal.propose(invocation)
    operation = _fake(
        LocalFakeReadResult(
            tool_result=_tool_result(),
            executed_arguments={"airport": "KSEA"},
            source_response_ref="response-1",
            source_version="v1",
            rows_returned=1,
        )
    )

    assert proposal.operation_proposed is True
    assert proposal.operation_started is False
    assert proposal.operation_finished is False
    assert proposal.outcome is ReadJournalOutcome.NOT_RUN
    assert proposal.lifecycle == (ReadJournalPhase.PROPOSED,)
    assert operation.calls["count"] == 0
    _assert_closed(proposal)

    entry = journal.execute(operation, proposal)

    assert operation.calls["count"] == 1
    assert entry.recorded_by_journal is True
    assert entry.operation_proposed is True
    assert entry.operation_started is True
    assert entry.operation_finished is True
    assert entry.lifecycle == (
        ReadJournalPhase.PROPOSED,
        ReadJournalPhase.STARTED,
        ReadJournalPhase.COMPLETED,
    )
    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.started_at == T0
    assert entry.finished_at == T1
    assert entry.invocation_id == "inv-1"
    assert entry.tool_call_id == "tool-call-1"
    assert entry.tool_name == "describe_hazard"
    assert entry.adapter_identity == "local-fake-adapter"
    assert entry.invocation.requested_arguments == {"airport": "KSEA"}
    assert entry.executed_arguments_observed is True
    assert entry.arguments_match is True
    assert entry.executed_invocation is not None
    assert entry.executed_invocation.executed_arguments == {"airport": "KSEA"}
    assert entry.result_exists is True
    assert entry.result_admitted is False
    assert entry.result_status is ToolResultStatus.SUCCESS
    assert entry.source_response_ref == "response-1"
    assert entry.observed_source_versions == ("v1",)
    assert entry.source_records[0].record_id == "rec-1"
    assert entry.rows_returned == 1
    assert journal.proposal_for("tool-call-1") is proposal
    assert journal.execution_for("tool-call-1") is entry
    assert proposal.outcome is ReadJournalOutcome.NOT_RUN
    _assert_closed(entry)
    assert ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE in entry.gaps
    assert ReadJournalGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF in entry.gaps


def test_thrown_exception_is_recorded_and_reraised_without_the_message():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    supplied = {"airport": "KSEA", "api_key": SECRET}
    invocation = _invocation(requested_arguments=supplied)
    proposal = journal.propose(invocation)

    def operation():
        raise RuntimeError(SECRET)

    mark_deterministic_local_fake(operation)

    with pytest.raises(RuntimeError) as caught:
        journal.execute(operation, proposal)

    entry = journal.execution_for("tool-call-1")
    rendered = json.dumps(entry.to_dict())
    assert str(caught.value) == SECRET
    assert entry.outcome is ReadJournalOutcome.EXCEPTION
    assert entry.error_type == "RuntimeError"
    assert entry.lifecycle[-1] is ReadJournalPhase.FAILED
    assert entry.operation_started is True
    assert entry.result_exists is False
    assert entry.started_at == T0
    assert entry.finished_at == T1
    assert supplied["api_key"] == SECRET
    assert entry.invocation.requested_arguments["api_key"] == "[redacted]"
    assert entry.invocation.requested_arguments["airport"] == "KSEA"
    assert entry.arguments_redacted is True
    assert SECRET not in rendered
    assert SECRET not in repr(entry)
    _assert_closed(entry)


def test_timeout_uses_the_injected_clock_and_does_not_admit_the_result():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, "2026-01-15T00:00:05Z")))
    invocation = _invocation(timeout_budget_seconds=1)
    proposal = journal.propose(invocation)
    operation = _fake(
        LocalFakeReadResult(
            source_response_ref="response-1",
            source_version="v1",
            source_records=(_record(),),
            rows_returned=1,
            executed_arguments={"airport": "KSEA"},
        )
    )

    with pytest.raises(ReadJournalTimeout) as caught:
        journal.execute(operation, proposal)

    entry = caught.value.entry
    assert journal.execution_for("tool-call-1") is entry
    assert entry.outcome is ReadJournalOutcome.TIMEOUT
    assert entry.error_type == "timeout"
    assert entry.started_at == T0
    assert entry.finished_at == "2026-01-15T00:00:05Z"
    assert entry.result_exists is True
    assert entry.result_admitted is False
    assert entry.source_response_ref == "response-1"
    assert entry.lifecycle == (
        ReadJournalPhase.PROPOSED,
        ReadJournalPhase.STARTED,
        ReadJournalPhase.FAILED,
    )
    _assert_closed(entry)


def test_cancellation_records_the_signal_and_reraises_it():
    journal = MasterRuntimeReadJournal(MonotonicClock())
    started = journal.propose(_invocation())

    def cancelled():
        raise LocalFakeCancellation()

    mark_deterministic_local_fake(cancelled)
    with pytest.raises(LocalFakeCancellation):
        journal.execute(cancelled, started)

    entry = journal.execution_for("tool-call-1")
    assert entry.outcome is ReadJournalOutcome.CANCELLATION
    assert entry.error_type == "LocalFakeCancellation"
    assert entry.operation_started is True
    assert entry.operation_finished is True
    assert entry.started_at is not None
    assert entry.finished_at is not None
    assert entry.result_admitted is False
    _assert_closed(entry)

    skipped = journal.propose(_invocation(tool_call_id="tool-call-2", invocation_id="inv-2"))
    flag = ReadCancellation()
    flag.cancel()
    calls = {"count": 0}

    def not_called():
        calls["count"] += 1
        return LocalFakeReadResult(rows_returned=1, source_response_ref="response-2", source_version="v1")

    mark_deterministic_local_fake(not_called)
    with pytest.raises(ReadJournalCancelled) as caught:
        journal.execute(not_called, skipped, cancellation=flag)

    assert calls["count"] == 0
    assert caught.value.entry.outcome is ReadJournalOutcome.CANCELLATION
    assert caught.value.entry.operation_started is False
    assert caught.value.entry.operation_proposed is True
    assert caught.value.entry.finished_at is not None
    _assert_closed(caught.value.entry)


def test_duplicate_tool_call_id_keeps_the_first_record():
    journal = MasterRuntimeReadJournal(MonotonicClock())
    first = journal.propose(_invocation())
    with pytest.raises(DuplicateToolCallError) as proposed_again:
        journal.propose(_invocation())
    assert proposed_again.value.entry.outcome is ReadJournalOutcome.DUPLICATE_TOOL_CALL
    assert proposed_again.value.entry.operation_started is False
    assert journal.proposal_for("tool-call-1") is first

    operation = _fake(
        LocalFakeReadResult(
            source_response_ref="response-1",
            source_version="v1",
            rows_returned=1,
            executed_arguments={"airport": "KSEA"},
        )
    )
    entry = journal.execute(operation, first)
    with pytest.raises(DuplicateToolCallError) as executed_again:
        journal.execute(operation, first)

    assert operation.calls["count"] == 1
    assert executed_again.value.entry.outcome is ReadJournalOutcome.DUPLICATE_TOOL_CALL
    assert executed_again.value.entry.recorded_by_journal is True
    assert journal.execution_for("tool-call-1") is entry
    assert entry.outcome is ReadJournalOutcome.COMPLETED
    _assert_closed(executed_again.value.entry)

    raced = MasterRuntimeReadJournal(MonotonicClock())
    proposal = raced.propose(_invocation(tool_call_id="tool-call-race"))
    wins: list[object] = []
    losses: list[DuplicateToolCallError] = []
    race_calls = {"count": 0}
    race_lock = threading.Lock()

    def race_operation():
        with race_lock:
            race_calls["count"] += 1
        return LocalFakeReadResult(
            source_response_ref="response-race",
            source_version="v1",
            rows_returned=1,
        )

    mark_deterministic_local_fake(race_operation)

    def run():
        try:
            wins.append(raced.execute(race_operation, proposal))
        except DuplicateToolCallError as exc:
            losses.append(exc)

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert len(wins) == 1
    assert len(losses) == 1
    assert race_calls["count"] == 1
    assert losses[0].entry.operation_started is False
    winner = wins[0]
    assert winner.operation_started is True  # type: ignore[attr-defined]
    assert raced.execution_for("tool-call-race") is winner


def test_concurrent_independent_reads_both_complete():
    journal = MasterRuntimeReadJournal(MonotonicClock())
    barrier = threading.Barrier(2)
    entries: list[RuntimeReadJournalEntry] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def run(tool_call_id: str, invocation_id: str):
        proposal = journal.propose(
            _invocation(
                tool_call_id=tool_call_id,
                invocation_id=invocation_id,
                expected_source_version="v1",
            )
        )

        def operation():
            barrier.wait(timeout=5)
            return LocalFakeReadResult(
                source_response_ref=f"response-{tool_call_id}",
                source_version="v1",
                source_records=(_record(record_id=tool_call_id),),
                rows_returned=1,
                executed_arguments={"airport": "KSEA"},
            )

        mark_deterministic_local_fake(operation)
        try:
            entry = journal.execute(operation, proposal)
        except Exception as exc:
            with lock:
                errors.append(exc)
            return
        with lock:
            entries.append(entry)

    threads = [
        threading.Thread(target=run, args=("tool-call-a", "inv-a")),
        threading.Thread(target=run, args=("tool-call-b", "inv-b")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert [thread.is_alive() for thread in threads] == [False, False]
    assert errors == []
    assert len(entries) == 2
    by_tool = {entry.tool_call_id: entry for entry in entries}
    assert set(by_tool) == {"tool-call-a", "tool-call-b"}
    for entry in entries:
        assert entry.outcome is ReadJournalOutcome.COMPLETED
        assert entry.operation_started is True
        assert entry.started_at is not None
        assert entry.finished_at is not None
        assert entry.started_at < entry.finished_at
        assert entry.source_response_ref == f"response-{entry.tool_call_id}"
        _assert_closed(entry)
    assert journal.execution_for("tool-call-a").attempt_id != journal.execution_for("tool-call-b").attempt_id


def test_argument_mismatch_is_recorded_on_the_completed_fake():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation(requested_arguments={"airport": "KSEA"}))
    operation = _fake(
        LocalFakeReadResult(
            executed_arguments={"airport": "KJFK"},
            source_response_ref="response-1",
            source_version="v1",
            rows_returned=1,
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.arguments_match is False
    assert entry.executed_invocation is not None
    assert entry.executed_invocation.requested_arguments == {"airport": "KSEA"}
    assert entry.executed_invocation.executed_arguments == {"airport": "KJFK"}
    assert ReadJournalGap.ARGUMENT_MISMATCH in entry.gaps
    _assert_closed(entry)


def test_source_version_mismatch_stays_unauthenticated():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation(expected_source_version="v1"))
    operation = _fake(
        LocalFakeReadResult(
            source_records=(_record(source_version="v2"),),
            source_version="v2",
            source_response_ref="response-1",
            executed_arguments={"airport": "KSEA"},
            rows_returned=1,
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.observed_source_versions == ("v2",)
    assert entry.invocation.expected_source_version == "v1"
    assert entry.source_version_mismatch is True
    assert entry.source_records[0].source_version == "v2"
    assert ReadJournalGap.SOURCE_VERSION_MISMATCH in entry.gaps
    assert ReadJournalGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF in entry.gaps
    _assert_closed(entry)


def test_partial_pagination_is_observed_and_does_not_complete_the_population():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation())
    operation = _fake(
        LocalFakeReadResult(
            source_response_ref="response-1",
            source_version="v1",
            rows_returned=2,
            executed_arguments={"airport": "KSEA"},
            next_page_token=PAGE_TOKEN,
            pagination=PaginationObservation(
                page_index=0,
                page_count=3,
                page_size=2,
                rows_in_page=2,
                has_more=True,
            ),
        )
    )

    entry = journal.execute(operation, proposal)
    rendered = json.dumps(entry.to_dict())

    assert entry.pagination is not None
    assert entry.pagination.has_more is True
    assert entry.pagination.next_page_token_present is True
    assert ProposedRetrievalLimit.PAGE_LIMIT in entry.retrieval_limits
    assert ProposedRetrievalLimit.RESULT_TRUNCATED not in entry.retrieval_limits
    assert ReadJournalGap.PARTIAL_PAGINATION in entry.gaps
    assert ReadJournalGap.TRUNCATED not in entry.gaps
    assert entry.population_scope_established is False
    assert PAGE_TOKEN not in rendered
    assert PAGE_TOKEN not in repr(entry)
    _assert_closed(entry)


def test_truncation_and_row_limit_are_separate_observations():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation())
    evidence = _evidence(
        completeness=SourceCompleteness(status="TRUNCATED", reason="row-limit"),
    )
    operation = _fake(
        LocalFakeReadResult(
            tool_result=_tool_result(
                evidence=evidence,
                status=ToolResultStatus.PARTIAL,
                data=[{"hazard_id": "fixture-row"}],
                limitations=("truncated",),
            ),
            executed_arguments={"airport": "KSEA"},
            truncated=True,
            rows_returned=1,
            list_limit_default_applied=True,
            pagination=PaginationObservation(row_limit=1, has_more=False),
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.result_status is ToolResultStatus.PARTIAL
    assert ProposedRetrievalLimit.RESULT_TRUNCATED in entry.retrieval_limits
    assert ProposedRetrievalLimit.ROW_LIMIT in entry.retrieval_limits
    assert ReadJournalGap.TRUNCATED in entry.gaps
    assert ReadJournalGap.ROW_LIMIT_APPLIED in entry.gaps
    assert ReadJournalGap.PARTIAL_PAGINATION not in entry.gaps
    assert entry.executed_invocation is not None
    assert entry.executed_invocation.list_limit_default_applied is True
    assert entry.population_scope_established is False
    _assert_closed(entry)


def test_missing_source_identity_is_left_missing():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation(expected_source_version=None))
    operation = _fake(
        LocalFakeReadResult(
            rows_returned=1,
            executed_arguments={"airport": "KSEA"},
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.result_exists is True
    assert entry.source_records == ()
    assert entry.source_response_ref is None
    assert entry.query_executions == ()
    assert entry.source_names == ()
    assert entry.observed_source_versions == ()
    assert ReadJournalGap.MISSING_SOURCE_IDENTITY in entry.gaps
    assert ReadJournalGap.MISSING_SOURCE_VERSION in entry.gaps
    _assert_closed(entry)


def test_empty_returned_rows_do_not_mint_a_verified_zero():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation())
    evidence = _evidence(
        query_executions=(
            QueryExecutionTrace(query_id="query-1", rows_returned=0),
        ),
    )
    operation = _fake(
        LocalFakeReadResult(
            tool_result=_tool_result(evidence=evidence, data=[]),
            executed_arguments={"airport": "KSEA"},
            rows_returned=0,
            source_version="v1",
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.result_exists is True
    assert entry.rows_returned == 0
    assert entry.query_executions[0].rows_returned == 0
    assert ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO in entry.gaps
    assert entry.verified_zero_minted is False
    assert entry.verified_zero_claim is None
    assert "verified_zero_claim" not in entry.to_dict()
    _assert_closed(entry)


def test_error_followed_by_retry_preserves_the_failure():
    journal = MasterRuntimeReadJournal(MonotonicClock())
    failed_proposal = journal.propose(_invocation(tool_call_id="tool-call-failed"))

    def fail():
        raise RuntimeError("local-read-failed")

    mark_deterministic_local_fake(fail)
    with pytest.raises(RuntimeError, match="local-read-failed"):
        journal.execute(fail, failed_proposal)

    failed = journal.execution_for("tool-call-failed")
    retry_proposal = journal.propose(
        _invocation(
            invocation_id="inv-1",
            tool_call_id="tool-call-retry",
            retry_of_tool_call_id="tool-call-failed",
        )
    )
    retry = journal.execute(
        _fake(
            LocalFakeReadResult(
                source_response_ref="response-retry",
                source_version="v1",
                rows_returned=1,
                executed_arguments={"airport": "KSEA"},
            )
        ),
        retry_proposal,
    )

    assert failed.outcome is ReadJournalOutcome.EXCEPTION
    assert failed.error_type == "RuntimeError"
    assert retry.outcome is ReadJournalOutcome.COMPLETED
    assert retry.invocation.retry_of_tool_call_id == "tool-call-failed"
    assert journal.execution_for("tool-call-failed") is failed
    assert "local-read-failed" not in json.dumps(failed.to_dict())

    with pytest.raises(DuplicateToolCallError):
        journal.execute(fail, failed_proposal)
    assert journal.execution_for("tool-call-failed") is failed
    assert failed.outcome is ReadJournalOutcome.EXCEPTION


def test_forged_caller_created_journal_event_is_not_an_observation():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    payload = {
        "outcome": "COMPLETED",
        "source_authenticity": "ESTABLISHED",
        "verified_zero_minted": True,
        "aviation_evidence_admitted": True,
        "trusted_runtime_observed": True,
        "tool_call_id": "forged-tool",
        "source_version": "v9",
        "api_key": SECRET,
    }
    forged = RuntimeReadJournalEntry.from_caller(payload)

    assert forged.recorded_by_journal is False
    assert forged.outcome is ReadJournalOutcome.NOT_RUN
    assert forged.operation_started is False
    assert forged.operation_finished is False
    assert forged.result_exists is False
    assert forged.observed_source_versions == ()
    assert forged.tool_call_id == "caller-event"
    assert ReadJournalGap.FORGED_CALLER_EVENT in forged.gaps
    assert SECRET not in json.dumps(forged.to_dict())
    _assert_closed(forged)

    with pytest.raises(ContractValidationError) as rejected:
        journal.reject_caller_event(payload)
    assert "forged_caller_journal_event" in rejected.value.errors
    assert journal.entries() == ()

    operation = _fake(LocalFakeReadResult(rows_returned=1, source_version="v1"))
    with pytest.raises(ContractValidationError) as executed:
        journal.execute(operation, forged)
    assert "forged_caller_journal_event" in executed.value.errors
    assert operation.calls["count"] == 0
    assert journal.entries() == ()

    with pytest.raises(ContractValidationError) as minted:
        RuntimeReadJournalEntry(
            attempt_id="caller-forged",
            invocation=_invocation(),
            lifecycle=(ReadJournalPhase.COMPLETED, ReadJournalPhase.STARTED, ReadJournalPhase.COMPLETED),
            outcome=ReadJournalOutcome.COMPLETED,
            gaps=(),
            verified_zero_minted=True,
        )
    assert "verified_zero_not_minted" in minted.value.errors


def test_complete_looking_trace_does_not_authenticate_the_source():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation())
    evidence = _evidence(
        records=(_record(),),
        completeness=SourceCompleteness(
            status="COMPLETE",
            reason="caller-shaped",
            epoch_ids=("epoch-1",),
            evaluated_as_of_utc=T0,
        ),
        query_executions=(
            QueryExecutionTrace(
                query_id="query-1",
                execution_id="execution-1",
                rows_returned=1,
            ),
        ),
    )
    operation = _fake(
        LocalFakeReadResult(
            tool_result=_tool_result(evidence=evidence, data=[{"hazard_id": "fixture-row"}]),
            executed_arguments={"airport": "KSEA"},
            source_response_ref="response-1",
            source_version="v1",
            rows_returned=1,
            completeness=SourceCompleteness(status="COMPLETE", evaluated_as_of_utc=T0),
        )
    )

    entry = journal.execute(operation, proposal)

    assert entry.lifecycle == (
        ReadJournalPhase.PROPOSED,
        ReadJournalPhase.STARTED,
        ReadJournalPhase.COMPLETED,
    )
    assert entry.outcome is ReadJournalOutcome.COMPLETED
    assert entry.arguments_match is True
    assert entry.source_version_mismatch is False
    assert entry.observed_source_versions == ("v1",)
    assert entry.source_response_ref == "response-1"
    assert entry.source_names == ("fixture-source",)
    assert entry.query_executions[0].query_id == "query-1"
    assert entry.completeness is not None
    assert entry.completeness.status == "COMPLETE"
    assert entry.rows_returned == 1
    assert entry.retrieval_limits == ()
    assert ReadJournalGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF in entry.gaps
    assert ReadJournalGap.LOCAL_FAKE_NOT_AVIATION_EVIDENCE in entry.gaps
    assert ReadJournalGap.SOURCE_VERSION_MISMATCH not in entry.gaps
    assert ReadJournalGap.EMPTY_ROWS_NOT_VERIFIED_ZERO not in entry.gaps
    assert ReadJournalGap.MISSING_SOURCE_IDENTITY not in entry.gaps
    _assert_closed(entry)
    assert bool(journal) is False


def test_unmarked_callable_is_refused_before_it_runs():
    journal = MasterRuntimeReadJournal(ScriptedClock((T0, T1)))
    proposal = journal.propose(_invocation())
    calls = {"count": 0}

    def operation():
        calls["count"] += 1
        return LocalFakeReadResult(source_version="v1", rows_returned=1)

    with pytest.raises(ContractValidationError) as caught:
        journal.execute(operation, proposal)

    assert "operation_not_local_fake" in caught.value.errors
    assert calls["count"] == 0
    assert journal.proposal_for("tool-call-1") is proposal
    marked = mark_deterministic_local_fake(operation)
    entry = journal.execute(
        marked,
        proposal,
    )
    assert calls["count"] == 1
    assert entry.outcome is ReadJournalOutcome.COMPLETED
    _assert_closed(entry)
