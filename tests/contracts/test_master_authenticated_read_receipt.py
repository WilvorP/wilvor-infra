"""Source-read receipt boundary. Local observation is not production authenticity."""

from __future__ import annotations

import pickle
from dataclasses import fields

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    QueryExecutionTrace,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_authenticated_read_receipt import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT,
    LOCAL_TRUSTED_ADAPTER_IS_PRODUCTION_AUTHENTICITY,
    MA4_CROSS_MODULE_INTERFACES,
    SOURCE_AUTHENTICATED_READ_AVAILABLE,
    TOOL_RESULT_IS_READ_RECEIPT,
    VERIFIED_ZERO_ALWAYS_MISSING,
    ApprovedExecutorAction,
    DescriptiveReadContext,
    DuplicateReadReceipt,
    GeographicLimitation,
    HostIssuedReadReceipt,
    LocalAdapterObservation,
    LocalReadTimeout,
    LocalTrustedReadHost,
    Ma4IntegrationRequirement,
    ObservedSourceRow,
    PopulationCondition,
    ProductionOrigin,
    ProvenanceRequirementStatus,
    ReadReceiptCancelled,
    ReadReceiptGap,
    ReadReceiptRejected,
    ReadReceiptTimeout,
    ReadReceiptVerificationStatus,
    ReadResponseDisposition,
    UntrustedPopulationIndication,
    assess_untrusted_read_subject,
    ma4_integration_requirements,
    verify_read_receipt,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import (
    MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_provenance_trace import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE as PROVENANCE_AUTHENTICATED,
    ProposedDispatcherExecutionEvent,
    ProposedPopulationScope,
)
from wilvor_ai.master_read_resource_guard import (
    DISPATCH_GRANT_AVAILABLE,
    PRODUCTION_PERMIT_ISSUER_INTEGRATED,
    ReadOperation,
)
from wilvor_ai.master_routing import MAX_DECISION_STEPS, MAX_HISTORICAL_WINDOW, MAX_ROUTE_STEPS
from wilvor_ai.master_source_read_trace_boundary import (
    COMPLETE_POPULATION_ABSENCE_AVAILABLE,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO,
    TRUSTED_DISPATCHER_IMPLEMENTED,
    TRUSTED_RUNTIME_OBSERVATION_AVAILABLE,
)
from wilvor_ai.contracts import AgentCapability
from wilvor_ai.specialist_contracts import SpecialistResult
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult
from datetime import timedelta


REQUEST = "2026-10-20T11:00:00Z"
DEADLINE = "2026-10-20T18:00:00Z"
QUERY_CLOCK = "2026-10-20T12:00:00Z"
EVAL_CLOCK = "2026-10-20T12:00:05Z"
HOST_A = "2026-10-20T12:01:00Z"
HOST_B = "2026-10-20T12:01:01Z"
START = "2026-10-01T00:00:00Z"
END = "2026-10-03T00:00:00Z"
ROW_TIME = "2026-10-02T00:00:00Z"


class Clock:
    def __init__(self, *instants: str) -> None:
        self.instants = list(instants)

    def now_utc(self) -> str:
        if not self.instants:
            raise AssertionError("host clock exhausted")
        return self.instants.pop(0)


def _query(end: str = END) -> tuple[tuple[str, str], ...]:
    return (("start_utc", START), ("end_utc", end))


def _context(**overrides: object) -> DescriptiveReadContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "specialist_invocation_id": "spec-inv-1",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        "principal_ref": "principal-ref-1",
        "policy_revision": "policy-rev-1",
        "resource_id": "historical.encounters",
        "operation": ReadOperation.QUERY,
        "region_scope": (),
        "historical_window_start_utc": START,
        "historical_window_end_utc": END,
        "decision_target_scope": None,
        "accepted_at_utc": REQUEST,
        "deadline_utc": DEADLINE,
        "idempotency_key": "idem-1",
        "max_model_turns": 99,
        "max_tool_calls": 99,
        "correlation_id": "corr-1",
        "adapter_identity": "local-historical-adapter",
        "tool_name": "summarize_historical_encounters",
        "tool_call_id": "tool-call-1",
        "requested_query": _query(),
        "expected_source_version": None,
        "asserted_grant_ref": "caller-grant-1",
        "asserted_ledger_ref": "caller-ledger-1",
    }
    values.update(overrides)
    return DescriptiveReadContext(**values)  # type: ignore[arg-type]


def _row(version: str | None = "hv-9", record_id: str | None = "rec-1") -> ObservedSourceRow:
    return ObservedSourceRow(
        record_id=record_id,
        source_version=version,
        event_timestamp_utc=ROW_TIME,
    )


def _observation(**overrides: object) -> LocalAdapterObservation:
    values: dict[str, object] = {
        "executed_query": _query(),
        "response_status": ToolResultStatus.SUCCESS,
        "rows_returned": 1,
        "rows": (_row(),),
        "page_index": 0,
        "page_count": 1,
        "page_limit": 25,
        "row_limit": 25,
        "truncated": False,
        "has_more": False,
        "query_clock_utc": QUERY_CLOCK,
        "evaluation_clock_utc": EVAL_CLOCK,
        "freshness": FreshnessStatus.FRESH,
        "query_id": "query-1",
        "response_source_version": "hv-9",
    }
    values.update(overrides)
    return LocalAdapterObservation(**values)  # type: ignore[arg-type]


def _run(
    observation: LocalAdapterObservation | None = None,
    *,
    context: DescriptiveReadContext | None = None,
    clocks: tuple[str, ...] = (HOST_A, HOST_B),
    executor_result: object | None = None,
    raise_timeout: bool = False,
):
    host = LocalTrustedReadHost(Clock(*clocks))
    seen = {"called": False}

    def executor(action: ApprovedExecutorAction) -> object:
        seen["called"] = True
        assert action.tool_call_id == (context or _context()).tool_call_id
        if raise_timeout:
            raise LocalReadTimeout("hidden detail")
        if executor_result is not None:
            return executor_result
        return observation if observation is not None else _observation()

    registration = host.register_local_adapter("local-historical-adapter", executor)
    bound = context if context is not None else _context()
    action = host.approve_executor_action(registration, bound)
    return host, action, seen, host.execute_read(action)


def _evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "source": "historical.encounters",
        "source_records": (
            SourceRecord(
                record_id="rec-1",
                source_version="hv-9",
                event_timestamp_utc=ROW_TIME,
            ),
        ),
        "query_timestamp_utc": QUERY_CLOCK,
        "freshness_status": FreshnessStatus.FRESH,
        "confidence": ConfidenceLevel.HIGH,
        "limitations": (),
        "tool_call_id": "tool-call-1",
        "temporal_scope": TemporalScope.HISTORICAL,
        "query_executions": (
            QueryExecutionTrace(query_id="query-1", rows_returned=1),
        ),
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


def _tool_result(evidence: Evidence | None = None, **overrides: object) -> ToolResult:
    item = evidence if evidence is not None else _evidence()
    values: dict[str, object] = {
        "tool_name": "summarize_historical_encounters",
        "tool_call_id": item.tool_call_id,
        "status": ToolResultStatus.SUCCESS,
        "temporal_scope": TemporalScope.HISTORICAL,
        "data": {"summary": "complete"},
        "evidence": (item,),
        "as_of_utc": EVAL_CLOCK,
        "limitations": (),
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def test_local_adapter_observation_is_not_production_authenticity():
    host, _action, seen, receipt = _run()
    assert seen["called"] is True
    verification = verify_read_receipt(
        host,
        receipt,
        expected_master_request_id="master-req-1",
        expected_step_id="step-1",
    )
    assert verification.status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert verification.local_test_host_observation is True
    assert verification.adapter_invoked is True
    assert verification.origin.value == "LOCAL_TRUSTED_ADAPTER_OBSERVATION"
    assert verification.production_origin is ProductionOrigin.UNRESOLVED
    assert verification.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert verification.production_authority.value == "DENIED"
    assert verification.evidence_admitted is False
    assert verification.aviation_evidence_admitted is False
    assert verification.verified_zero_established is False
    assert verification.production_grant_consumed is False
    assert verification.production_ledger_consumed is False
    assert verification.authenticated_source_read_available is False
    assert bool(receipt) is False
    assert bool(verification) is False
    assert receipt.record_ids == ("rec-1",)
    assert receipt.source_versions == ("hv-9",)
    assert receipt.query_ids == ("query-1",)
    assert receipt.requested_query == receipt.executed_query
    assert receipt.query_diverged is False
    assert receipt.resource_scope == ("historical.encounters",)
    assert receipt.operation is ReadOperation.QUERY
    assert receipt.request_clock_utc == REQUEST
    assert receipt.observation_clock_utc == HOST_B
    assert receipt.evaluation_clock_utc == EVAL_CLOCK
    assert receipt.query_clock_utc == QUERY_CLOCK
    assert len({REQUEST, HOST_B, EVAL_CLOCK, QUERY_CLOCK}) == 4
    assert receipt.asserted_grant_ref == "caller-grant-1"
    assert receipt.asserted_ledger_ref == "caller-ledger-1"
    assert receipt.local_consumption_id.startswith("local-consumption-")
    assert receipt.server_max_tool_calls == 2
    assert receipt.caller_max_tool_calls == 99
    assert ReadReceiptGap.CALLER_CEILING_IGNORED in receipt.gaps
    assert ReadReceiptGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF in receipt.gaps
    assert ReadReceiptGap.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in receipt.gaps
    finding = verification.provenance_findings[0]
    assert finding.requirement is MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER
    assert finding.status is ProvenanceRequirementStatus.UNRESOLVED
    assert all(
        item.status is ProvenanceRequirementStatus.UNRESOLVED
        for item in verification.provenance_findings
    )
    assert not hasattr(LocalTrustedReadHost, "from_event")
    assert not hasattr(HostIssuedReadReceipt, "from_dict")


def test_injected_tool_result_is_not_a_receipt():
    host = LocalTrustedReadHost(Clock(HOST_A))
    result = _tool_result()
    assessed = assess_untrusted_read_subject(result)
    verified = verify_read_receipt(host, result)
    for item in (assessed, verified):
        assert item.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
        assert item.receipt is None
        assert item.host_issued is False
        assert item.local_test_host_observation is False
        assert item.origin.value == "UNRESOLVED"
        assert item.production_origin is ProductionOrigin.UNRESOLVED
        assert item.evidence_admitted is False
        assert item.verified_zero_established is False
        assert ReadReceiptGap.TOOL_RESULT_NOT_RECEIPT in item.gaps
    event = ProposedDispatcherExecutionEvent(
        master_request_id="master-req-1",
        master_invocation_id="inv-1",
        tool_name="summarize_historical_encounters",
        tool_call_id="tool-call-1",
        population_scope=ProposedPopulationScope.CLAIMED_COMPLETE,
        source_version="hv-9",
        permit_consumption_ref="grant-1",
        adapter_identity="local-historical-adapter",
    )
    forged = assess_untrusted_read_subject(event)
    assert forged.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert forged.receipt is None
    body = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "pilot"}}}},
        "headers": {"Authorization": "Bearer forged"},
        "WILVOR_TEST_MODE": "1",
    }
    rejected = assess_untrusted_read_subject(body)
    assert rejected.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert rejected.production_origin is ProductionOrigin.UNRESOLVED


def test_duplicate_receipt_and_foreign_host_are_rejected():
    host, _action, _seen, receipt = _run()
    other = LocalTrustedReadHost(Clock(HOST_A))
    foreign = verify_read_receipt(other, receipt)
    assert foreign.status is ReadReceiptVerificationStatus.UNTRUSTED_SUBJECT
    assert foreign.evidence_admitted is False
    first = verify_read_receipt(host, receipt, expected_master_request_id="master-req-1", expected_step_id="step-1")
    assert first.status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    second = verify_read_receipt(host, receipt, expected_master_request_id="master-req-1", expected_step_id="step-1")
    assert second.status is ReadReceiptVerificationStatus.REPLAYED
    assert second.verified_zero_established is False
    host_b, _action_b, _seen_b, _receipt_b = None, None, None, None
    host_b = LocalTrustedReadHost(Clock())
    registration = host_b.register_local_adapter("local-historical-adapter", lambda _action: _observation())
    host_b.approve_executor_action(registration, _context())
    with pytest.raises(DuplicateReadReceipt) as caught:
        host_b.approve_executor_action(registration, _context(idempotency_key="idem-2"))
    assert caught.value.verification.status is ReadReceiptVerificationStatus.DUPLICATE


def test_request_step_mismatch_and_tamper_do_not_authenticate():
    host, _action, _seen, receipt = _run()
    mismatch = verify_read_receipt(
        host,
        receipt,
        expected_master_request_id="master-req-other",
        expected_step_id="step-1",
    )
    assert mismatch.status is ReadReceiptVerificationStatus.BINDING_MISMATCH
    assert mismatch.evidence_admitted is False
    step_mismatch = verify_read_receipt(
        host,
        receipt,
        expected_master_request_id="master-req-1",
        expected_step_id="step-9",
    )
    assert step_mismatch.status is ReadReceiptVerificationStatus.BINDING_MISMATCH
    object.__setattr__(receipt, "step_id", "step-forged")
    tampered = verify_read_receipt(
        host,
        receipt,
        expected_master_request_id="master-req-1",
        expected_step_id="step-1",
    )
    assert tampered.status is ReadReceiptVerificationStatus.TAMPERED
    assert tampered.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    values = {item.name: getattr(receipt, item.name) for item in fields(receipt)}
    values["_seal"] = object()
    with pytest.raises(ContractValidationError):
        HostIssuedReadReceipt(**values)
    with pytest.raises(ContractValidationError):
        pickle.dumps(receipt)


def test_missing_source_version_and_query_id_are_not_fabricated():
    host, _action, _seen, receipt = _run(
        _observation(
            rows=(_row(version=None),),
            response_source_version=None,
            query_id=None,
            rows_returned=1,
        )
    )
    assert receipt.response_source_version is None
    assert receipt.source_versions == (None,)
    assert receipt.query_ids == ()
    assert "hv-9" not in receipt.source_versions
    assert PopulationCondition.SOURCE_VERSION_ABSENT in receipt.population_conditions
    assert PopulationCondition.MISSING_QUERY_ID in receipt.population_conditions
    assert ReadReceiptGap.HISTORICAL_NATIVE_FIELD_ABSENT in receipt.gaps
    assert receipt.verified_zero_established is False
    verification = verify_read_receipt(host, receipt)
    assert "query_id_present" in verification.missing_verified_zero_conditions
    assert "source_version_present_and_matching" in verification.missing_verified_zero_conditions


def test_source_version_mismatch_is_not_integrity():
    _host, _action, _seen, receipt = _run(
        _observation(),
        context=_context(expected_source_version="hv-1"),
    )
    assert PopulationCondition.SOURCE_VERSION_MISMATCH in receipt.population_conditions
    assert receipt.response_source_version == "hv-9"
    assert receipt.verified_zero_established is False
    assert ReadReceiptGap.SOURCE_VERSION_NOT_INTEGRITY_PROOF in receipt.gaps


def test_incomplete_page_is_not_complete_coverage():
    _host, _action, _seen, receipt = _run(
        _observation(page_index=0, page_count=3, has_more=True, page_limit=1)
    )
    assert PopulationCondition.PAGINATION_INCOMPLETE in receipt.population_conditions
    assert receipt.disposition is ReadResponseDisposition.PARTIAL
    assert receipt.reported_pagination_complete is False
    assert receipt.evidence_admitted is False
    assert receipt.page_count == 3
    assert PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION not in receipt.population_conditions


def test_zero_rows_are_distinct_from_omission_stale_and_scoped_empty():
    _host, _action, _seen, zero = _run(
        _observation(rows=(), rows_returned=0, response_source_version="hv-9")
    )
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS in zero.population_conditions
    assert PopulationCondition.SOURCE_OMITTED_RECORD not in zero.population_conditions
    assert PopulationCondition.SOURCE_STALE not in zero.population_conditions
    assert PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION not in zero.population_conditions
    assert zero.disposition is ReadResponseDisposition.ZERO_ROWS
    assert zero.verified_zero_established is False
    assert zero.query_ids == ("query-1",)

    _host, _action, _seen, omitted = _run(
        _observation(rows=(), rows_returned=None, source_omitted_record=True),
        context=_context(tool_call_id="tool-call-omit", idempotency_key="idem-omit"),
    )
    assert PopulationCondition.SOURCE_OMITTED_RECORD in omitted.population_conditions
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS not in omitted.population_conditions
    assert omitted.verified_zero_established is False

    _host, _action, _seen, stale = _run(
        _observation(
            response_status=ToolResultStatus.STALE,
            freshness=FreshnessStatus.STALE,
        ),
        context=_context(tool_call_id="tool-call-stale", idempotency_key="idem-stale"),
    )
    assert PopulationCondition.SOURCE_STALE in stale.population_conditions
    assert stale.disposition is ReadResponseDisposition.STALE
    assert stale.evidence_admitted is False
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS not in stale.population_conditions


def test_scoped_empty_population_still_is_not_a_verified_zero():
    host, _action, _seen, receipt = _run(
        _observation(
            rows=(),
            rows_returned=0,
            scoped_authoritative_empty=True,
            response_source_version="hv-9",
        )
    )
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS in receipt.population_conditions
    assert PopulationCondition.SCOPED_AUTHORITATIVE_EMPTY_POPULATION in receipt.population_conditions
    assert PopulationCondition.SOURCE_OMITTED_RECORD not in receipt.population_conditions
    assert PopulationCondition.PAGINATION_INCOMPLETE not in receipt.population_conditions
    verification = verify_read_receipt(host, receipt)
    assert verification.verified_zero_established is False
    assert set(VERIFIED_ZERO_ALWAYS_MISSING) <= set(verification.missing_verified_zero_conditions)
    assert "query_returned_zero_rows" not in verification.missing_verified_zero_conditions
    assert "scoped_authoritative_population" not in verification.missing_verified_zero_conditions
    assert "pagination_complete" not in verification.missing_verified_zero_conditions
    assert ReadReceiptGap.SCOPED_EMPTY_NOT_VERIFIED_ZERO in receipt.gaps


def test_wrong_clock_is_not_an_evaluation_time():
    _host, _action, _seen, receipt = _run(
        _observation(evaluation_clock_utc=REQUEST, observation_clock_utc=REQUEST)
    )
    assert receipt.request_clock_utc == REQUEST
    assert receipt.evaluation_clock_utc is None
    assert receipt.observation_clock_utc == HOST_B
    assert receipt.observation_clock_utc != receipt.request_clock_utc
    assert ReadReceiptGap.WRONG_CLOCK in receipt.gaps
    assert receipt.verified_zero_established is False


def test_forged_regional_impact_does_not_prove_polygon_or_aircraft_count():
    _host, _action, _seen, receipt = _run(
        _observation(
            region_text="California",
            state_grouping=("CA",),
            claimed_impacted_aircraft_count=4,
            claimed_polygon_intersection=True,
        )
    )
    assert GeographicLimitation.REGIONAL_TEXT_NOT_POLYGON in receipt.geographic_limitations
    assert GeographicLimitation.STATE_GROUPING_NOT_POLYGON in receipt.geographic_limitations
    assert GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED in receipt.geographic_limitations
    assert receipt.polygon_intersection_established is False
    assert receipt.impacted_aircraft_count_established is False
    assert receipt.untrusted_claimed_impacted_aircraft_count == 4
    assert ReadReceiptGap.FORGED_REGIONAL_IMPACT in receipt.gaps
    forged = _tool_result(
        data={
            "region": "California",
            "state": "CA",
            "impacted_aircraft_count": 7,
            "polygon_intersection": True,
        }
    )
    assessed = assess_untrusted_read_subject(forged)
    assert assessed.status is ReadReceiptVerificationStatus.NOT_A_RECEIPT
    assert UntrustedPopulationIndication.FORGED_REGIONAL_IMPACT in assessed.untrusted_indications
    assert assessed.polygon_intersection_established is False
    assert assessed.impacted_aircraft_count_established is False
    assert assessed.untrusted_claimed_impacted_aircraft_count == 7
    zero_shaped = _tool_result(
        _evidence(
            query_executions=(QueryExecutionTrace(query_id="query-1", rows_returned=0),),
            match_cardinality=MatchCardinality(exact_count=0, is_exact=True),
            source_records=(),
        ),
        data={"rows": 0},
    )
    zero_assessment = assess_untrusted_read_subject(zero_shaped)
    assert UntrustedPopulationIndication.CALLER_REPORTED_ZERO_ROWS in zero_assessment.untrusted_indications
    assert UntrustedPopulationIndication.CALLER_REPORTED_EXACT_ZERO in zero_assessment.untrusted_indications
    assert UntrustedPopulationIndication.CALLER_REPORTED_OMITTED_RECORDS in zero_assessment.untrusted_indications
    assert zero_assessment.population_conditions == ()
    assert zero_assessment.verified_zero_established is False


def test_timeout_cancellation_and_partial_results_are_not_evidence():
    host = LocalTrustedReadHost(Clock(HOST_A, HOST_B))

    def time_out(_action: ApprovedExecutorAction) -> LocalAdapterObservation:
        raise LocalReadTimeout("hidden")

    registration = host.register_local_adapter("local-historical-adapter", time_out)
    action = host.approve_executor_action(registration, _context())
    with pytest.raises(ReadReceiptTimeout) as timed_out:
        host.execute_read(action)
    timeout_receipt = timed_out.value.verification.receipt
    assert timeout_receipt is not None
    assert timeout_receipt.disposition is ReadResponseDisposition.TIMEOUT
    assert timeout_receipt.adapter_invoked is True
    assert timeout_receipt.record_ids == ()
    assert timeout_receipt.query_ids == ()
    assert timeout_receipt.response_source_version is None
    assert timeout_receipt.evidence_admitted is False
    assert PopulationCondition.TIMEOUT in timeout_receipt.population_conditions
    assert "hidden" not in str(timed_out.value)

    cancel_host = LocalTrustedReadHost(Clock(HOST_A))
    called = {"value": False}

    def executor(_action: ApprovedExecutorAction) -> LocalAdapterObservation:
        called["value"] = True
        return _observation()

    cancel_registration = cancel_host.register_local_adapter("local-historical-adapter", executor)
    cancel_action = cancel_host.approve_executor_action(
        cancel_registration,
        _context(tool_call_id="tool-call-cancel", idempotency_key="idem-cancel"),
    )
    from wilvor_ai.master_authenticated_read_receipt import ReadCancellation

    cancellation = ReadCancellation()
    cancellation.cancel()
    with pytest.raises(ReadReceiptCancelled) as cancelled:
        cancel_host.execute_read(cancel_action, cancellation=cancellation)
    assert called["value"] is False
    cancel_receipt = cancelled.value.verification.receipt
    assert cancel_receipt is not None
    assert cancel_receipt.disposition is ReadResponseDisposition.CANCELLED
    assert cancel_receipt.adapter_invoked is False
    assert cancel_receipt.evidence_admitted is False
    assert PopulationCondition.CANCELLED in cancel_receipt.population_conditions

    _host, _action, _seen, partial = _run(
        _observation(response_status=ToolResultStatus.PARTIAL, truncated=True),
        context=_context(tool_call_id="tool-call-partial", idempotency_key="idem-partial"),
    )
    assert partial.disposition is ReadResponseDisposition.PARTIAL
    assert PopulationCondition.PARTIAL_RESULT in partial.population_conditions
    assert partial.evidence_admitted is False


def test_scan_wide_window_and_divergent_query_fail_at_execution():
    host = LocalTrustedReadHost(Clock(HOST_A))
    called = {"value": False}

    def executor(_action: ApprovedExecutorAction) -> LocalAdapterObservation:
        called["value"] = True
        return _observation()

    registration = host.register_local_adapter("local-historical-adapter", executor)
    action = host.approve_executor_action(
        registration,
        _context(operation=ReadOperation.SCAN, tool_call_id="tool-call-scan", idempotency_key="idem-scan"),
    )
    with pytest.raises(ReadReceiptRejected) as scanned:
        host.execute_read(action)
    assert called["value"] is False
    assert scanned.value.verification.evidence_admitted is False
    assert scanned.value.verification.receipt is not None
    assert scanned.value.verification.receipt.disposition is ReadResponseDisposition.REJECTED

    wide_host = LocalTrustedReadHost(Clock(HOST_A))
    wide_called = {"value": False}

    def wide_executor(_action: ApprovedExecutorAction) -> LocalAdapterObservation:
        wide_called["value"] = True
        return _observation()

    wide_registration = wide_host.register_local_adapter("local-historical-adapter", wide_executor)
    wide_action = wide_host.approve_executor_action(
        wide_registration,
        _context(
            historical_window_end_utc="2026-10-09T00:00:00Z",
            requested_query=_query("2026-10-09T00:00:00Z"),
            tool_call_id="tool-call-wide",
            idempotency_key="idem-wide",
        ),
    )
    with pytest.raises(ReadReceiptRejected):
        wide_host.execute_read(wide_action)
    assert wide_called["value"] is False

    diverged_host = LocalTrustedReadHost(Clock(HOST_A, HOST_B))
    diverged_registration = diverged_host.register_local_adapter(
        "local-historical-adapter",
        lambda _action: _observation(executed_query=_query("2026-10-20T00:00:00Z")),
    )
    diverged_action = diverged_host.approve_executor_action(
        diverged_registration,
        _context(tool_call_id="tool-call-diverged", idempotency_key="idem-diverged"),
    )
    with pytest.raises(ReadReceiptRejected) as diverged:
        diverged_host.execute_read(diverged_action)
    diverged_receipt = diverged.value.verification.receipt
    assert diverged_receipt is not None
    assert diverged_receipt.adapter_invoked is True
    assert diverged_receipt.record_ids == ()
    assert diverged_receipt.query_ids == ()
    assert diverged_receipt.response_source_version is None
    assert ReadReceiptGap.REQUESTED_QUERY_NOT_EXECUTED in diverged_receipt.gaps


def test_route_and_decision_ceilings_match_the_existing_constants():
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert MAX_HISTORICAL_WINDOW == timedelta(days=7)
    host = LocalTrustedReadHost(Clock())
    registration = host.register_local_adapter("local-historical-adapter", lambda _action: _observation())
    for index in range(MAX_ROUTE_STEPS):
        host.approve_executor_action(
            registration,
            _context(tool_call_id=f"tool-call-{index}", idempotency_key=f"idem-{index}", step_id=f"step-{index}"),
        )
    with pytest.raises(ReadReceiptRejected) as overflow:
        host.approve_executor_action(
            registration,
            _context(tool_call_id="tool-call-extra", idempotency_key="idem-extra", step_id="step-extra"),
        )
    assert overflow.value.verification.status is ReadReceiptVerificationStatus.REJECTED
    decision_host = LocalTrustedReadHost(Clock())
    decision_registration = decision_host.register_local_adapter(
        "local-decision-adapter",
        lambda _action: _observation(),
    )
    decision_context = _context(
        specialist=MasterSpecialist.DECISION,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.EXPLAIN_EXISTING_RISK,
        adapter_identity="local-decision-adapter",
        tool_name="get_current_risk_evidence",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        requested_query=(("aircraft_id", "ABC123"),),
        historical_window_start_utc=None,
        historical_window_end_utc=None,
        tool_call_id="decision-call-1",
        idempotency_key="decision-idem-1",
        master_request_id="master-req-decision",
    )
    decision_host.approve_executor_action(decision_registration, decision_context)
    with pytest.raises(ReadReceiptRejected):
        decision_host.approve_executor_action(
            decision_registration,
            _context(
                specialist=MasterSpecialist.DECISION,
                purpose=MasterPurpose.ANSWER,
                capability=AgentCapability.EXPLAIN_EXISTING_RISK,
                adapter_identity="local-decision-adapter",
                tool_name="get_current_risk_evidence",
                resource_id="aircraft",
                operation=ReadOperation.GET_ITEM,
                requested_query=(("aircraft_id", "ABC123"),),
                historical_window_start_utc=None,
                historical_window_end_utc=None,
                tool_call_id="decision-call-2",
                idempotency_key="decision-idem-2",
                master_request_id="master-req-decision",
                step_id="step-2",
            ),
        )


def test_historical_native_migration_and_ma4_requirements_stay_unresolved():
    run_fields = {item.name for item in fields(HistoricalSpecialistRunResult)}
    final_fields = {item.name for item in fields(SpecialistResult)}
    for name in HISTORICAL_NATIVE_FIELDS_NOT_ON_RUN_RESULT:
        assert name not in run_fields
        assert name not in final_fields
    findings = ma4_integration_requirements()
    assert tuple(item.requirement for item in findings) == tuple(Ma4IntegrationRequirement)
    assert all(item.status is ProvenanceRequirementStatus.UNRESOLVED for item in findings)
    assert all(item.satisfied is False for item in findings)
    producers = " ".join(item.producer for item in findings)
    assert "grant" in producers.casefold()
    assert "ledger" in producers.casefold()
    assert "Not imported" in producers
    assert any("Lambda" in item or "grant" in item for item in MA4_CROSS_MODULE_INTERFACES)


def test_existing_admission_and_trace_constants_stay_closed():
    assert list(MasterSourceAuthenticity) == [MasterSourceAuthenticity.NOT_ESTABLISHED]
    assert MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION == "wilvor.ai.master_evidence_admission.v1"
    assert TRUSTED_DISPATCHER_IMPLEMENTED is False
    assert TRUSTED_RUNTIME_OBSERVATION_AVAILABLE is False
    assert EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
    assert COMPLETE_POPULATION_ABSENCE_AVAILABLE is False
    assert PROVENANCE_AUTHENTICATED is False
    assert AUTHENTICATED_SOURCE_READ_AVAILABLE is False
    assert PRODUCTION_PERMIT_ISSUER_INTEGRATED is False
    assert DISPATCH_GRANT_AVAILABLE is False
    assert SOURCE_AUTHENTICATED_READ_AVAILABLE is False
    assert LOCAL_TRUSTED_ADAPTER_IS_PRODUCTION_AUTHENTICITY is False
    assert TOOL_RESULT_IS_READ_RECEIPT is False
