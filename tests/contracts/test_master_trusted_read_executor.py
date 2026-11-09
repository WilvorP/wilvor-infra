"""Host read execution stays sealed, bounded, and unauthenticated."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_contracts import MasterProductionAuthority, MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    MasterReadCheckStatus,
    MasterReadResourceProposal,
    ReadOperation,
    ServerCancellation,
    ServerReadEvaluation,
    check_master_read_resource,
    seal_ingress_principal,
    seal_population_scope,
    server_read_budget,
)
from wilvor_ai.master_trusted_read_executor import (
    APPROVED_SOURCE_ADAPTER_INTEGRATED,
    EMPTY_PAGE_CREATES_VERIFIED_ZERO,
    ENVIRONMENT_SELECTS_TEST_AUTHORITY,
    FAKE_RESULT_IS_AVIATION_EVIDENCE,
    PRODUCTION_GRANT_ISSUER_INTEGRATED,
    PRODUCTION_READ_EXECUTOR_ENABLED,
    RAW_OBSERVATION_KIND,
    SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE,
    WITHIN_ALLOWLIST_IS_EXECUTION_GRANT,
    EffectPlane,
    HostAuthorityKind,
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
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
    issue_production_read_invocation,
    open_production_executor,
    raw_read_observation,
)


HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
LIVE = MasterSpecialist.LIVE_OPS
HISTORICAL = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RETRIEVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
OBSERVED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
START = "2026-01-08T00:00:00Z"
END = "2026-01-15T00:00:00Z"
SOURCE_OBSERVED = "2026-01-14T12:00:00Z"
SECRET_TOKEN = "secret-page-token"
SECRET_ERROR = "SECRET-sk-live-xyz"


class _ExplosiveCheck:
    def __getattribute__(self, name: str) -> object:
        raise AssertionError(name)


def _host(**overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL,
        "tool_name": "list_historical_encounters",
        "tool_call_id": "tool-call-1",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "accepted_at_utc": OBSERVED,
        "deadline_utc": DEADLINE,
        "idempotency_key": "idem-1",
        "grant_nonce": "nonce-1",
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "timeout_budget_seconds": 30,
        "historical_start_utc": START,
        "historical_end_utc": END,
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return bind_nonproduction_test_invocation(**values)  # type: ignore[arg-type]


def _request(host: object | None = None, **overrides: object) -> UntrustedReadRequest:
    bound = _host() if host is None else host
    values: dict[str, object] = {
        "master_request_id": bound.master_request_id,
        "planning_session_id": bound.planning_session_id,
        "step_id": bound.step_id,
        "invocation_id": bound.invocation_id,
        "specialist": bound.specialist,
        "purpose": bound.purpose,
        "capability": bound.capability,
        "tool_name": bound.tool_name,
        "tool_call_id": bound.tool_call_id,
        "resource_id": bound.resource_id,
        "operation": bound.operation,
        "requested_arguments": (
            ("start_utc", START),
            ("end_utc", END),
            ("aircraft_id", "AC1"),
        ),
        "correlation_id": bound.correlation_id,
    }
    values.update(overrides)
    return UntrustedReadRequest(**values)  # type: ignore[arg-type]


def _page(**overrides: object) -> NonproductionReadPage:
    values: dict[str, object] = {
        "record_ids": ("SIGMET-CA", "HZ2"),
        "source_name": "historical.encounters",
        "source_version": "version-1",
        "query_id": "query-1",
        "page_index": 0,
        "has_more": True,
        "source_observed_at_utc": SOURCE_OBSERVED,
        "next_page_token": SECRET_TOKEN,
    }
    values.update(overrides)
    return NonproductionReadPage(**values)  # type: ignore[arg-type]


def _executor(read: object, *, now_utc: str = OBSERVED):
    clock = bind_nonproduction_host_clock(now_utc)
    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=HISTORY,
                tool_name="list_historical_encounters",
                capability=HISTORICAL,
                resource_id="historical.encounters",
                operation=ReadOperation.QUERY,
                read=read,
            ),
        )
    )
    return MasterTrustedReadExecutor(registry, clock), clock


def _allowlist_check():
    proposal = MasterReadResourceProposal(
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORICAL,
        tool_name="list_historical_encounters",
        resource_id="historical.encounters",
        operation=ReadOperation.QUERY,
        arguments=(("start_utc", START), ("end_utc", END), ("aircraft_id", "AC1")),
        master_request_id="master-req-1",
        invocation_id="inv-1",
        historical_start_utc=START,
        historical_end_utc=END,
    )
    evaluation = ServerReadEvaluation(
        observed_utc=OBSERVED,
        deadline_utc=DEADLINE,
        executions_used=0,
        model_turns_used=0,
        tool_calls_used=0,
        cancellation=ServerCancellation(False),
    )
    check = check_master_read_resource(
        proposal,
        evaluation=evaluation,
        principal=seal_ingress_principal("principal-1"),
    )
    assert check.status is MasterReadCheckStatus.WITHIN_ALLOWLIST
    assert check.execution_permitted is False
    return check


def test_closed_constants_and_production_entrypoints_fail():
    assert PRODUCTION_READ_EXECUTOR_ENABLED is False
    assert PRODUCTION_GRANT_ISSUER_INTEGRATED is False
    assert APPROVED_SOURCE_ADAPTER_INTEGRATED is False
    assert SOURCE_AUTHENTICATED_EVIDENCE_AVAILABLE is False
    assert FAKE_RESULT_IS_AVIATION_EVIDENCE is False
    assert WITHIN_ALLOWLIST_IS_EXECUTION_GRANT is False
    assert EMPTY_PAGE_CREATES_VERIFIED_ZERO is False
    assert ENVIRONMENT_SELECTS_TEST_AUTHORITY is False
    with pytest.raises(ContractValidationError, match="production_grant_issuer_not_integrated"):
        issue_production_read_invocation()
    with pytest.raises(ContractValidationError, match="production_read_executor_not_enabled"):
        open_production_executor()


def test_bounded_local_read_keeps_requested_arguments_and_does_not_authenticate():
    seen: dict[str, object] = {}

    def read(arguments: dict[str, object], _cancellation: object) -> NonproductionReadPage:
        seen.update(arguments)
        return _page()

    host = _host()
    executor, _clock = _executor(read)
    receipt = executor.execute(
        _request(host),
        host_invocation=host,
        cancellation=bind_nonproduction_cancellation(),
        policy_check=_ExplosiveCheck(),
    )
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert receipt.authority_kind is HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER
    assert receipt.adapter_invoked is True
    assert receipt.local_test_adapter_resolved is True
    assert receipt.approved_source_adapter is False
    assert receipt.production_grant_issued is False
    assert receipt.policy_check_supplied is True
    assert receipt.policy_check_used is False
    assert receipt.guard_denial_reasons == ()
    assert receipt.requested_arguments == (
        ("start_utc", START),
        ("end_utc", END),
        ("aircraft_id", "AC1"),
    )
    assert receipt.executed_arguments[-1] == ("limit", HISTORICAL_LIST_MAX)
    assert receipt.scope_injected_argument_names == ("limit",)
    assert seen["limit"] == HISTORICAL_LIST_MAX
    assert "limit" not in dict(receipt.requested_arguments)
    assert receipt.record_ids == ("SIGMET-CA", "HZ2")
    assert receipt.partial_pagination is True
    assert receipt.next_page_token_present is True
    assert receipt.page_empty is False
    assert receipt.source_version == "version-1"
    assert receipt.source_version_is_integrity_proof is False
    assert receipt.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert receipt.production_authority is MasterProductionAuthority.DENIED
    assert receipt.aviation_evidence_admitted is False
    assert receipt.verified_zero_minted is False
    assert receipt.geographic_impact_verified is False
    assert receipt.geographic_coverage == "NOT_ESTABLISHED"
    assert receipt.tool_result_authentically_read is False
    assert receipt.local_fake_is_aviation_evidence is False
    assert receipt.source_reported_observed_at_utc == SOURCE_OBSERVED
    assert receipt.execution_started_at_utc == OBSERVED
    assert receipt.accepted_at_utc == OBSERVED
    assert receipt.deadline_utc == DEADLINE
    assert receipt.source_clock_authority == "NOT_ESTABLISHED"
    assert receipt.effects[0].plane is EffectPlane.OPERATIONAL_READ
    assert receipt.effects[0].operation == "QUERY"
    assert receipt.effects[0].resource_id == "historical.encounters"
    assert receipt.effects[0].operational_data_mutation is False
    assert receipt.effects[1].plane is EffectPlane.INTERNAL_LEDGER_WRITE
    assert receipt.effects[1].operation == "CONSUME"
    assert receipt.effects[1].resource_id == "ledger.invocation"
    assert receipt.effects[1].operational_data_mutation is False
    with pytest.raises(ContractValidationError, match="read_receipt_is_not_a_grant"):
        bool(receipt)
    observation = raw_read_observation(receipt)
    assert observation["candidate_kind"] == RAW_OBSERVATION_KIND
    assert observation["authentic_source_read"] is False
    assert "tool_result" not in observation
    assert "next_page_token" not in observation
    assert SECRET_TOKEN not in repr(observation)
    assert SECRET_TOKEN not in repr(receipt)


def test_empty_page_is_not_a_verified_zero():
    host = _host(idempotency_key="idem-empty")
    executor, _clock = _executor(lambda _arguments, _cancellation: _page(record_ids=(), has_more=False, next_page_token=None))
    receipt = executor.execute(
        _request(host),
        host_invocation=host,
        cancellation=bind_nonproduction_cancellation(),
    )
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert receipt.page_empty is True
    assert receipt.record_ids == ()
    assert receipt.rows_returned == 0
    assert receipt.verified_zero_minted is False
    assert receipt.empty_page_creates_verified_zero is False
    assert receipt.geographic_impact_verified is False


def test_wrong_specialist_does_not_call_adapter_even_when_the_request_is_allowlisted():
    calls: list[int] = []
    host = _host()
    executor, _clock = _executor(lambda *_args: calls.append(1))
    request = _request(
        host,
        specialist=LIVE,
        purpose=MasterPurpose.DISCOVERY,
        capability=RETRIEVE,
        tool_name="get_aircraft_operational_context",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        requested_arguments=(("aircraft_id", "AC1"),),
    )
    receipt = executor.execute(
        request,
        host_invocation=host,
        cancellation=bind_nonproduction_cancellation(),
        policy_check=_allowlist_check(),
    )
    assert calls == []
    assert receipt.adapter_invoked is False
    assert ReadBoundaryDenial.SPECIALIST_MISMATCH in receipt.denial_reasons
    assert receipt.policy_check_used is False
    assert receipt.guard_denial_reasons == ()


def test_unauthorized_resource_scan_unknown_argument_and_caller_scope_do_not_run():
    calls: list[str] = []

    def read(*_args: object) -> NonproductionReadPage:
        calls.append("ran")
        return _page()

    host = _host()
    executor, _clock = _executor(read)
    cancellation = bind_nonproduction_cancellation()
    unauthorized = executor.execute(
        _request(host, resource_id="aircraft"),
        host_invocation=host,
        cancellation=cancellation,
    )
    scanned = executor.execute(
        _request(host, operation=ReadOperation.SCAN),
        host_invocation=host,
        cancellation=cancellation,
    )
    unknown = executor.execute(
        _request(
            host,
            requested_arguments=(
                ("start_utc", START),
                ("end_utc", END),
                ("aircraft_id", "AC1"),
                ("extra", "SELECT * FROM encounters"),
            ),
        ),
        host_invocation=host,
        cancellation=cancellation,
    )
    scoped = executor.execute(
        _request(host, caller_scope=seal_population_scope(frozenset({"CA"}))),
        host_invocation=host,
        cancellation=cancellation,
    )
    assert calls == []
    assert ReadBoundaryDenial.RESOURCE_MISMATCH in unauthorized.denial_reasons
    assert ReadBoundaryDenial.SCAN_NOT_PERMITTED in scanned.denial_reasons
    assert ReadBoundaryDenial.ARGUMENT_NOT_ALLOWLISTED in unknown.denial_reasons
    assert ReadBoundaryDenial.ARBITRARY_SQL in unknown.denial_reasons
    assert ReadBoundaryDenial.CALLER_SCOPE_NOT_AUTHORITY in scoped.denial_reasons
    assert all(item.adapter_invoked is False for item in (unauthorized, scanned, unknown, scoped))


def test_within_allowlist_without_host_authority_does_not_execute():
    calls: list[int] = []
    executor, _clock = _executor(lambda *_args: calls.append(1))
    assert os.environ.get("ENVIRONMENT") == "test"
    receipt = executor.execute(
        _request(),
        host_invocation={"status": "WITHIN_ALLOWLIST", "execution_permitted": True},
        cancellation=bind_nonproduction_cancellation(),
        policy_check=_allowlist_check(),
    )
    assert calls == []
    assert receipt.authority_kind is HostAuthorityKind.ABSENT
    assert ReadBoundaryDenial.HOST_AUTHORITY_ABSENT in receipt.denial_reasons
    assert ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED in receipt.denial_reasons
    assert ReadBoundaryDenial.POLICY_CHECK_NOT_EXECUTION_GRANT in receipt.denial_reasons
    assert receipt.policy_check_used is False
    assert receipt.production_grant_issued is False
    assert receipt.adapter_invoked is False


def test_injected_callable_and_caller_tool_result_are_not_a_read():
    planted: list[str] = []
    adapter_calls: list[int] = []

    def planted_callable() -> None:
        planted.append("called")

    host = _host()
    executor, _clock = _executor(lambda *_args: adapter_calls.append(1))
    cancellation = bind_nonproduction_cancellation()
    callable_receipt = executor.execute(
        _request(host, caller_callable=planted_callable),
        host_invocation=host,
        cancellation=cancellation,
    )
    result_receipt = executor.execute(
        _request(host, caller_tool_result=object()),
        host_invocation=host,
        cancellation=cancellation,
    )
    assert planted == []
    assert adapter_calls == []
    assert ReadBoundaryDenial.CALLER_CALLABLE_REJECTED in callable_receipt.denial_reasons
    assert ReadBoundaryDenial.CALLER_TOOL_RESULT_NOT_A_READ in result_receipt.denial_reasons
    assert result_receipt.tool_result_authentically_read is False


def test_timeout_budget_exhaustion_and_cancellation_stop_the_read():
    calls: list[int] = []
    host = _host(timeout_budget_seconds=1, idempotency_key="idem-timeout")
    executor, clock = _executor(
        lambda arguments, cancellation: _advance_and_return(clock, calls, cancellation, arguments)
    )
    timeout_request = _request(host)
    with pytest.raises(ReadExecutionError) as timeout_caught:
        executor.execute(
            timeout_request,
            host_invocation=host,
            cancellation=bind_nonproduction_cancellation(),
        )
    assert timeout_caught.value.receipt.outcome is ReadExecutionOutcome.TIMEOUT
    assert timeout_caught.value.receipt.record_ids == ()
    assert SECRET_TOKEN not in str(timeout_caught.value)
    assert timeout_caught.value.__cause__ is None

    _max_executions, _max_turns, max_calls = server_read_budget(HISTORY)
    exhausted_host = _host(tool_calls_used=max_calls, idempotency_key="idem-budget")
    exhausted = executor.execute(
        _request(exhausted_host),
        host_invocation=exhausted_host,
        cancellation=bind_nonproduction_cancellation(),
    )
    assert ReadBoundaryDenial.BUDGET_EXHAUSTED in exhausted.denial_reasons
    assert exhausted.adapter_invoked is False

    cancelled = bind_nonproduction_cancellation()
    cancelled.cancel()
    before = executor.execute(
        _request(_host(idempotency_key="idem-cancel-before")),
        host_invocation=_host(idempotency_key="idem-cancel-before"),
        cancellation=cancelled,
    )
    assert ReadBoundaryDenial.CANCELLED in before.denial_reasons
    assert before.adapter_invoked is False

    during_calls = len(calls)
    during_cancellation = bind_nonproduction_cancellation()
    during_host = _host(idempotency_key="idem-cancel-during", timeout_budget_seconds=30)
    during_executor, _during_clock = _executor(
        lambda _arguments, cancellation: _cancel_and_return(cancellation, calls)
    )
    with pytest.raises(ReadExecutionError) as during_caught:
        during_executor.execute(
            _request(during_host),
            host_invocation=during_host,
            cancellation=during_cancellation,
        )
    assert during_caught.value.receipt.outcome is ReadExecutionOutcome.CANCELLED
    assert during_caught.value.receipt.record_ids == ()
    assert len(calls) == during_calls + 1


def _advance_and_return(clock: object, calls: list[int], _cancellation: object, _arguments: object) -> NonproductionReadPage:
    calls.append(1)
    clock.advance_to("2026-01-15T00:00:05Z")  # type: ignore[attr-defined]
    return _page()


def _cancel_and_return(cancellation: object, calls: list[int]) -> NonproductionReadPage:
    calls.append(1)
    cancellation.cancel()  # type: ignore[attr-defined]
    return _page()


def test_query_limit_and_exception_text_are_not_admitted():
    host = _host(idempotency_key="idem-limit")
    too_many = tuple(f"row-{index}" for index in range(HISTORICAL_LIST_MAX + 1))
    executor, _clock = _executor(lambda _arguments, _cancellation: _page(record_ids=too_many, has_more=False, next_page_token=None))
    with pytest.raises(ReadExecutionError) as limit_caught:
        executor.execute(
            _request(host),
            host_invocation=host,
            cancellation=bind_nonproduction_cancellation(),
        )
    assert limit_caught.value.receipt.outcome is ReadExecutionOutcome.QUERY_LIMIT_EXCEEDED
    assert limit_caught.value.receipt.record_ids == ()
    assert "row-0" not in repr(limit_caught.value.receipt)

    def explode(_arguments: object, _cancellation: object) -> NonproductionReadPage:
        raise RuntimeError(SECRET_ERROR)

    failed_host = _host(idempotency_key="idem-exception")
    failed_executor, _failed_clock = _executor(explode)
    with pytest.raises(ReadExecutionError) as failed:
        failed_executor.execute(
            _request(failed_host),
            host_invocation=failed_host,
            cancellation=bind_nonproduction_cancellation(),
        )
    assert failed.value.receipt.outcome is ReadExecutionOutcome.EXCEPTION
    assert failed.value.receipt.error_type == "RuntimeError"
    assert failed.value.receipt.record_ids == ()
    assert SECRET_ERROR not in str(failed.value)
    assert SECRET_ERROR not in repr(failed.value.receipt)
    assert failed.value.__cause__ is None


def test_duplicate_idempotency_does_not_read_again_and_replacement_is_not_sealed():
    calls: list[int] = []
    host = _host(idempotency_key="idem-once")
    executor, _clock = _executor(lambda _arguments, _cancellation: calls.append(1) or _page(has_more=False, next_page_token=None))
    cancellation = bind_nonproduction_cancellation()
    first = executor.execute(_request(host), host_invocation=host, cancellation=cancellation)
    second = executor.execute(_request(host), host_invocation=host, cancellation=cancellation)
    assert first.outcome is ReadExecutionOutcome.COMPLETED
    assert ReadBoundaryDenial.DUPLICATE_CONSUMPTION in second.denial_reasons
    assert second.adapter_invoked is False
    assert calls == [1]
    with pytest.raises(ContractValidationError, match="host_invocation_not_sealed"):
        replace(host, idempotency_key="idem-other")
    with pytest.raises(ContractValidationError, match="forged_read_receipt"):
        ReadReceiptCandidate(
            outcome=ReadExecutionOutcome.COMPLETED,
            authority_kind=HostAuthorityKind.NONPRODUCTION_TEST_ADAPTER,
            denial_reasons=(),
            guard_denial_reasons=(),
            adapter_invoked=True,
            local_test_adapter_resolved=True,
            policy_check_supplied=False,
            specialist=HISTORY,
            purpose=MasterPurpose.ANSWER,
            capability=HISTORICAL,
            operation=ReadOperation.QUERY,
            requested_arguments=(),
            executed_arguments=(),
            scope_injected_argument_names=(),
            effective_region_codes=(),
            effects=(),
            record_ids=("SIGMET-CA",),
            aviation_evidence_admitted=True,
            verified_zero_minted=True,
            geographic_impact_verified=True,
        )
