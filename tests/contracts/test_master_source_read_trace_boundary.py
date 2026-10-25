"""Source-read trace boundary. Proposed metadata is not a trusted read."""

from __future__ import annotations

import inspect
from dataclasses import replace
from datetime import datetime, timezone

import pytest

import wilvor_ai.master_source_read_trace_boundary as boundary
from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    QueryExecutionTrace,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.master_contracts import (
    MasterDisposition,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_local_capability import (
    LocalCapabilityRegistry,
    LocalCapabilityScope,
    LocalConsumptionOutcome,
    LocalConsumptionResult,
    LocalTestClock,
)
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_provenance_trace import (
    ProposedDispatcherExecutionEvent,
    ProposedPopulationScope,
    ProposedRetrievalLimit,
    ProvenanceAuditLimitation,
    ProvenanceRequirementStatus,
    classify_master_provenance,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_source_read_trace_boundary import (
    CALLER_SUPPLIED_TRACE_AUTHENTICATES_READ,
    CAPABILITY_CONSUMPTION_IS_DISPATCH_PERMISSION,
    EMPTY_ROWS_CREATE_VERIFIED_ZERO,
    MATCHING_REQUEST_ID_AUTHENTICATES_READ,
    MATCHING_SOURCE_VERSION_AUTHENTICATES_READ,
    MATCHING_TIMESTAMP_AUTHENTICATES_READ,
    TRUSTED_DISPATCHER_IMPLEMENTED,
    TRUSTED_RUNTIME_OBSERVATION_AVAILABLE,
    MasterSourceReadTraceAssessment,
    SourceReadTraceObligation,
    SourceReadTracePresence,
    assess_source_read_trace,
)
from wilvor_ai.specialist_contracts import VerifiedZeroClaim
from wilvor_ai.specialist_runtime_contracts import ExecutedToolInvocation


T = "2026-01-15T00:00:00Z"
LATER = "2026-01-15T03:00:00Z"
CLOSED = {
    ProvenanceRequirementStatus.UNRESOLVED,
    ProvenanceRequirementStatus.UNSATISFIED,
}


def _authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "spec-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": T,
        "requested_evaluation_time_utc": T,
        "expected_aircraft_id": "ABC123",
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _record(
    record_id: str = "rec-1",
    source_version: str | None = "v1",
    event_timestamp_utc: str | None = T,
) -> SourceRecord:
    return SourceRecord(
        record_id=record_id,
        source_version=source_version,
        event_timestamp_utc=event_timestamp_utc,
    )


def _evidence(**overrides: object) -> Evidence:
    values: dict[str, object] = {
        "source": "fixture",
        "source_records": (_record(),),
        "query_timestamp_utc": T,
        "freshness_status": FreshnessStatus.FRESH,
        "confidence": ConfidenceLevel.UNKNOWN,
        "limitations": (),
        "tool_call_id": "tool-call-1",
        "temporal_scope": TemporalScope.CURRENT,
    }
    values.update(overrides)
    return Evidence(**values)  # type: ignore[arg-type]


def _tool_result(evidence: Evidence | None = None, **overrides: object) -> ToolResult:
    item = evidence if evidence is not None else _evidence()
    values: dict[str, object] = {
        "tool_name": "describe_hazard",
        "tool_call_id": item.tool_call_id,
        "status": ToolResultStatus.SUCCESS,
        "temporal_scope": TemporalScope.CURRENT,
        "data": {"aircraft_id": "ZZZ999"},
        "evidence": (item,),
        "as_of_utc": T,
        "limitations": (),
        "correlation_id": "corr-1",
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _event(**overrides: object) -> ProposedDispatcherExecutionEvent:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "master_invocation_id": "inv-1",
        "specialist_invocation_id": "spec-req-1",
        "correlation_id": "corr-1",
        "permit_consumption_ref": "permit-1",
        "adapter_identity": "adapter-live-ops",
        "tool_name": "describe_hazard",
        "tool_call_id": "tool-call-1",
        "tool_arguments": (("aircraft_id", "ABC123"),),
        "source_response_ref": "response-1",
        "observation_time_utc": T,
        "evaluation_time_utc": T,
        "source_version": "v1",
        "entity_ids": ("ABC123",),
        "population_scope": ProposedPopulationScope.CLAIMED_COMPLETE,
        "completeness": SourceCompleteness(
            status="COMPLETE",
            reason="caller-supplied",
            epoch_ids=("epoch-1",),
            evaluated_as_of_utc=T,
        ),
        "retrieval_limits": (),
        "limitation_notes": ("geographic complete",),
        "freshness_status": FreshnessStatus.FRESH,
        "execution_trace_ref": None,
        "verified_zero_claim": None,
    }
    values.update(overrides)
    return ProposedDispatcherExecutionEvent(**values)  # type: ignore[arg-type]


def _executed(**overrides: object) -> ExecutedToolInvocation:
    arguments = {"aircraft_id": "ABC123"}
    values: dict[str, object] = {
        "tool_name": "describe_hazard",
        "tool_call_id": "tool-call-1",
        "requested_arguments": arguments,
        "executed_arguments": dict(arguments),
        "list_limit_default_applied": False,
        "canonical_key": "canonical-1",
    }
    values.update(overrides)
    return ExecutedToolInvocation(**values)  # type: ignore[arg-type]


def _assert_closed(assessment: MasterSourceReadTraceAssessment) -> None:
    assert assessment.authenticated_source_read_available is False
    assert assessment.observed_trusted_runtime_events == ()
    assert assessment.trusted_dispatcher_implemented is False
    assert assessment.trusted_runtime_observation_available is False
    assert assessment.caller_supplied_trace_authenticates_read is False
    assert assessment.matching_timestamp_authenticates_read is False
    assert assessment.matching_source_version_authenticates_read is False
    assert assessment.matching_request_id_authenticates_read is False
    assert assessment.capability_consumption_permits_dispatch is False
    assert assessment.empty_result_creates_verified_zero is False
    assert assessment.verified_zero_claim_minted is False
    assert assessment.verified_zero_established is False
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert assessment.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert assessment.dispatch_permitted is False
    assert assessment.end_to_end_fulfilled is False
    assert bool(assessment) is False
    assert assessment.missing_provenance == tuple(MasterProvenanceRequirement)
    assert len(assessment.classification.findings) == 7
    for finding in assessment.classification.findings:
        assert finding.status in CLOSED
        assert (
            ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE
            in finding.limitations
        )
    assert tuple(item.obligation for item in assessment.obligations) == tuple(
        SourceReadTraceObligation
    )
    for item in assessment.obligations:
        assert item.trusted_runtime_observed is False
        assert item.presence in {
            SourceReadTracePresence.NOT_PROPOSED,
            SourceReadTracePresence.PROPOSED_UNOBSERVED,
        }


def _presence(
    assessment: MasterSourceReadTraceAssessment,
    obligation: SourceReadTraceObligation,
) -> SourceReadTracePresence:
    return assessment.obligation(obligation).presence


def test_trace_boundary_constants_stay_closed():
    assert TRUSTED_DISPATCHER_IMPLEMENTED is False
    assert TRUSTED_RUNTIME_OBSERVATION_AVAILABLE is False
    assert CALLER_SUPPLIED_TRACE_AUTHENTICATES_READ is False
    assert MATCHING_TIMESTAMP_AUTHENTICATES_READ is False
    assert MATCHING_SOURCE_VERSION_AUTHENTICATES_READ is False
    assert MATCHING_REQUEST_ID_AUTHENTICATES_READ is False
    assert CAPABILITY_CONSUMPTION_IS_DISPATCH_PERMISSION is False
    assert EMPTY_ROWS_CREATE_VERIFIED_ZERO is False
    assert set(SourceReadTracePresence) == {
        SourceReadTracePresence.NOT_PROPOSED,
        SourceReadTracePresence.PROPOSED_UNOBSERVED,
    }
    assert "OBSERVED" not in {item.value for item in SourceReadTracePresence}
    assert "SATISFIED" not in {item.value for item in ProvenanceRequirementStatus}
    assessment = assess_source_read_trace(_event(), authorization=_authorization())
    assert {item.requirement for item in assessment.obligations} == set(MasterProvenanceRequirement)


def test_assessment_signature_cannot_accept_an_observed_event():
    signature = inspect.signature(assess_source_read_trace)
    forbidden = {
        "authenticated",
        "observed",
        "observed_events",
        "consumption",
        "consume",
        "dispatcher",
        "trusted_runtime_events",
    }
    assert forbidden.isdisjoint(signature.parameters)
    source = inspect.getsource(assess_source_read_trace)
    assert "VerifiedZeroClaim(" not in source
    assert "consume(" not in source


def test_complete_proposed_trace_stays_unestablished_and_unobserved():
    authorization = _authorization()
    event = _event()
    assessment = assess_source_read_trace(
        event,
        authorization=authorization,
        executed_invocation=_executed(),
    )
    _assert_closed(assessment)
    assert assessment.classification == classify_master_provenance(
        event,
        authorization=authorization,
    )
    assert assessment.proposed_identity_conflict is False
    proposed = {
        SourceReadTraceObligation.AUTHORIZED_INVOCATION,
        SourceReadTraceObligation.SPECIALIST_IDENTITY,
        SourceReadTraceObligation.CAPABILITY_CONSUMPTION,
        SourceReadTraceObligation.SELECTED_ADAPTER,
        SourceReadTraceObligation.EXECUTED_TOOL,
        SourceReadTraceObligation.TOOL_ARGUMENTS,
        SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY,
        SourceReadTraceObligation.OBSERVATION_CLOCK,
        SourceReadTraceObligation.EVALUATION_CLOCK,
        SourceReadTraceObligation.SOURCE_VERSION,
        SourceReadTraceObligation.ENTITY_BINDING,
        SourceReadTraceObligation.POPULATION_COMPLETENESS,
    }
    for obligation in proposed:
        assert _presence(assessment, obligation) is SourceReadTracePresence.PROPOSED_UNOBSERVED
    for obligation in (
        SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS,
        SourceReadTraceObligation.PAGINATION,
        SourceReadTraceObligation.ROW_LIMIT,
        SourceReadTraceObligation.TRUNCATION,
        SourceReadTraceObligation.ERROR_OUTCOME,
        SourceReadTraceObligation.TIMEOUT_OUTCOME,
        SourceReadTraceObligation.CANCELLATION_OUTCOME,
    ):
        assert _presence(assessment, obligation) is SourceReadTracePresence.NOT_PROPOSED
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN in source_read.limitations
    version = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY
    )
    assert ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF in version.limitations
    clocks = assessment.classification.finding(
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME
    )
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in clocks.limitations
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in clocks.limitations
    adapter = assessment.classification.finding(
        MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER
    )
    assert ProvenanceAuditLimitation.NO_TRUSTED_ADAPTER in adapter.limitations
    execution = assessment.classification.finding(
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT
    )
    assert ProvenanceAuditLimitation.PERMIT_REFERENCE_NOT_CONSUMPTION in execution.limitations


def test_geographic_wording_does_not_propose_geographic_completeness():
    assessment = assess_source_read_trace(_event(), authorization=_authorization())
    assert (
        _presence(assessment, SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.POPULATION_COMPLETENESS)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    population = assessment.classification.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert population.status is ProvenanceRequirementStatus.UNSATISFIED


def test_distinct_clock_roles_are_not_a_timestamp_conflict():
    event = _event(observation_time_utc=T, evaluation_time_utc=LATER)
    assessment = assess_source_read_trace(event, authorization=_authorization())
    clocks = assessment.classification.finding(
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME
    )
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in clocks.limitations
    assert assessment.proposed_identity_conflict is False
    _assert_closed(assessment)


def test_specialist_id_string_is_not_specialist_identity():
    event = _event(specialist_invocation_id="spec-req-1")
    assessment = assess_source_read_trace(event)
    assert (
        _presence(assessment, SourceReadTraceObligation.SPECIALIST_IDENTITY)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.AUTHORIZED_INVOCATION)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    _assert_closed(assessment)


def test_tool_result_payload_is_not_entity_binding_or_an_observed_read():
    assessment = assess_source_read_trace(_tool_result())
    _assert_closed(assessment)
    assert assessment.classification == classify_master_provenance(_tool_result())
    assert (
        _presence(assessment, SourceReadTraceObligation.ENTITY_BINDING)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.SELECTED_ADAPTER)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.EXECUTED_TOOL)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert assessment.obligation(SourceReadTraceObligation.EXECUTED_TOOL).trusted_runtime_observed is False
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert source_read.status is ProvenanceRequirementStatus.UNSATISFIED


def test_foreign_identities_conflict_and_stay_unestablished():
    event = _event(
        master_request_id="other-request",
        master_invocation_id="other-invocation",
        specialist_invocation_id="other-specialist",
        correlation_id="other-correlation",
        entity_ids=("OTHER",),
    )
    assessment = assess_source_read_trace(event, authorization=_authorization())
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is True
    execution = assessment.classification.finding(
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT
    )
    assert ProvenanceAuditLimitation.FOREIGN_REQUEST in execution.limitations
    assert ProvenanceAuditLimitation.FOREIGN_INVOCATION in execution.limitations
    assert ProvenanceAuditLimitation.CORRELATION_MISMATCH in execution.limitations


def test_executed_tool_disagreement_is_proposed_metadata():
    event = _event()
    executed = _executed(
        tool_name="other_tool",
        tool_call_id="other-call",
        requested_arguments={"limit": "10"},
        executed_arguments={"limit": 10},
    )
    assessment = assess_source_read_trace(event, executed_invocation=executed)
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is True
    assert (
        _presence(assessment, SourceReadTraceObligation.SELECTED_ADAPTER)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.TOOL_ARGUMENTS)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert assessment.obligation(SourceReadTraceObligation.TOOL_ARGUMENTS).trusted_runtime_observed is False


def test_matching_executed_arguments_are_not_execution_proof():
    assessment = assess_source_read_trace(
        _event(),
        authorization=_authorization(),
        executed_invocation=_executed(),
    )
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is False
    assert (
        _presence(assessment, SourceReadTraceObligation.EXECUTED_TOOL)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )


def test_requested_and_executed_argument_differences_conflict():
    executed = _executed(
        requested_arguments={"aircraft_id": "ABC123"},
        executed_arguments={"aircraft_id": "OTHER"},
    )
    assessment = assess_source_read_trace(_event(), executed_invocation=executed)
    assert assessment.proposed_identity_conflict is True
    _assert_closed(assessment)


def test_list_limit_proposes_a_row_limit_without_completeness():
    assessment = assess_source_read_trace(
        _tool_result(),
        executed_invocation=_executed(list_limit_default_applied=True),
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.ROW_LIMIT)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.PAGINATION)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    _assert_closed(assessment)


def test_pagination_row_limit_and_truncation_do_not_complete_a_population():
    event = _event(
        population_scope=ProposedPopulationScope.CLAIMED_COMPLETE,
        retrieval_limits=(
            ProposedRetrievalLimit.PAGE_LIMIT,
            ProposedRetrievalLimit.ROW_LIMIT,
            ProposedRetrievalLimit.RESULT_TRUNCATED,
            ProposedRetrievalLimit.SOURCE_WINDOW_LIMIT,
        ),
        completeness=SourceCompleteness(status="TRUNCATED", evaluated_as_of_utc=T),
    )
    assessment = assess_source_read_trace(event, authorization=_authorization())
    _assert_closed(assessment)
    for obligation in (
        SourceReadTraceObligation.PAGINATION,
        SourceReadTraceObligation.ROW_LIMIT,
        SourceReadTraceObligation.TRUNCATION,
        SourceReadTraceObligation.POPULATION_COMPLETENESS,
    ):
        assert _presence(assessment, obligation) is SourceReadTracePresence.PROPOSED_UNOBSERVED
    assert (
        _presence(assessment, SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    population = assessment.classification.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert ProvenanceAuditLimitation.RETRIEVAL_LIMITED in population.limitations
    assert ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE in population.limitations
    assert population.status is ProvenanceRequirementStatus.UNSATISFIED


def test_partial_tool_result_is_truncated_and_not_geographically_complete():
    evidence = _evidence(
        completeness=SourceCompleteness(status="PARTIAL", evaluated_as_of_utc=T),
        limitations=("returned page only",),
    )
    result = _tool_result(
        evidence,
        status=ToolResultStatus.PARTIAL,
        limitations=("returned page only",),
    )
    assessment = assess_source_read_trace(result)
    _assert_closed(assessment)
    assert (
        _presence(assessment, SourceReadTraceObligation.TRUNCATION)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.GEOGRAPHIC_COMPLETENESS)
        is SourceReadTracePresence.NOT_PROPOSED
    )


def test_stale_matching_clocks_and_version_do_not_authenticate():
    evidence = _evidence(freshness_status=FreshnessStatus.STALE)
    result = _tool_result(
        evidence,
        status=ToolResultStatus.STALE,
        limitations=("stale fixture",),
        as_of_utc=T,
    )
    assessment = assess_source_read_trace(result, authorization=_authorization())
    _assert_closed(assessment)
    clocks = assessment.classification.finding(
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME
    )
    assert ProvenanceAuditLimitation.STALE_DATA in clocks.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in clocks.limitations
    assert clocks.status is ProvenanceRequirementStatus.UNSATISFIED
    assert (
        _presence(assessment, SourceReadTraceObligation.SOURCE_VERSION)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )


def test_empty_rows_do_not_mint_a_verified_zero_claim():
    evidence = _evidence(source_records=())
    result = _tool_result(evidence, data={"rows": []})
    assessment = assess_source_read_trace(result)
    _assert_closed(assessment)
    assert assessment.rows_absent is True
    population = assessment.classification.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO not in population.limitations
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO not in source_read.limitations


def test_not_found_does_not_become_verified_zero():
    result = ToolResult(
        tool_name="describe_hazard",
        tool_call_id="tool-call-1",
        status=ToolResultStatus.NOT_FOUND,
        temporal_scope=TemporalScope.CURRENT,
        data=None,
        evidence=(),
        as_of_utc=T,
        limitations=(),
    )
    assessment = assess_source_read_trace(result)
    _assert_closed(assessment)
    assert assessment.rows_absent is True
    assert (
        _presence(assessment, SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    population = assessment.classification.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO not in population.limitations


def test_exact_zero_cardinality_is_not_an_established_zero():
    evidence = _evidence(
        match_cardinality=MatchCardinality(exact_count=0, is_exact=True),
    )
    assessment = assess_source_read_trace(_tool_result(evidence, data={"rows": []}))
    _assert_closed(assessment)
    assert assessment.rows_absent is True
    assert assessment.proposed_identity_conflict is True
    population = assessment.classification.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO in population.limitations
    assert population.status is ProvenanceRequirementStatus.UNSATISFIED


def test_caller_verified_zero_on_a_cancelled_read_stays_unestablished():
    evidence = _evidence(source_records=())
    claim = VerifiedZeroClaim(tool_call_id="tool-call-1")
    assessment = assess_source_read_trace(
        _tool_result(evidence, data={"rows": []}),
        verified_zero_claim=claim,
        disposition=MasterDisposition.CANCELLED,
    )
    _assert_closed(assessment)
    assert (
        _presence(assessment, SourceReadTraceObligation.CANCELLATION_OUTCOME)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.TIMEOUT_OUTCOME)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO in source_read.limitations


def test_invented_query_execution_is_not_an_observed_source_event():
    evidence = _evidence(
        query_executions=(
            QueryExecutionTrace(query_id="query-1", execution_id="exec-1", rows_returned=0),
        ),
    )
    assessment = assess_source_read_trace(_tool_result(evidence))
    _assert_closed(assessment)
    assert assessment.rows_absent is True
    assert assessment.proposed_identity_conflict is True
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in source_read.limitations
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO not in source_read.limitations
    assert (
        _presence(assessment, SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )


def test_execution_trace_ref_does_not_authenticate_a_successful_read():
    event = _event(execution_trace_ref="trace-1", verified_zero_claim=None)
    assessment = assess_source_read_trace(event, disposition=MasterDisposition.EXECUTED)
    _assert_closed(assessment)
    source_read = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE
    )
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in source_read.limitations
    assert source_read.status is ProvenanceRequirementStatus.UNSATISFIED


def test_success_status_disagrees_with_timeout_and_cancellation():
    result = _tool_result()
    timeout = assess_source_read_trace(result, disposition=MasterDisposition.PROVIDER_TIMEOUT)
    cancelled = assess_source_read_trace(result, disposition=MasterDisposition.CANCELLED)
    for assessment, obligation in (
        (timeout, SourceReadTraceObligation.TIMEOUT_OUTCOME),
        (cancelled, SourceReadTraceObligation.CANCELLATION_OUTCOME),
    ):
        _assert_closed(assessment)
        assert assessment.proposed_identity_conflict is True
        assert _presence(assessment, obligation) is SourceReadTracePresence.PROPOSED_UNOBSERVED
    runtime = assess_source_read_trace(result, disposition=MasterDisposition.RUNTIME_TIMEOUT)
    assert (
        _presence(runtime, SourceReadTraceObligation.TIMEOUT_OUTCOME)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(runtime, SourceReadTraceObligation.ERROR_OUTCOME)
        is SourceReadTracePresence.NOT_PROPOSED
    )


def test_error_code_text_timeout_is_not_a_timeout_outcome():
    evidence = _evidence(error_code="timeout")
    assessment = assess_source_read_trace(_tool_result(evidence))
    _assert_closed(assessment)
    assert (
        _presence(assessment, SourceReadTraceObligation.ERROR_OUTCOME)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.TIMEOUT_OUTCOME)
        is SourceReadTracePresence.NOT_PROPOSED
    )
    assert (
        _presence(assessment, SourceReadTraceObligation.CANCELLATION_OUTCOME)
        is SourceReadTracePresence.NOT_PROPOSED
    )


def test_failure_dispositions_propose_an_error_without_authorizing_a_read():
    result = ToolResult(
        tool_name="describe_hazard",
        tool_call_id="tool-call-1",
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.CURRENT,
        data=None,
        evidence=(),
        as_of_utc=None,
        limitations=("unavailable",),
    )
    for disposition in (
        MasterDisposition.TOOL_FAILED,
        MasterDisposition.PROVIDER_FAILED,
        MasterDisposition.VERIFICATION_FAILED,
        MasterDisposition.REFUSED,
        MasterDisposition.BUDGET_EXHAUSTED,
    ):
        assessment = assess_source_read_trace(result, disposition=disposition)
        _assert_closed(assessment)
        assert (
            _presence(assessment, SourceReadTraceObligation.ERROR_OUTCOME)
            is SourceReadTracePresence.PROPOSED_UNOBSERVED
        )
        assert assessment.rows_absent is False


def test_executed_disposition_on_not_found_conflicts_and_does_not_verify_zero():
    result = ToolResult(
        tool_name="describe_hazard",
        tool_call_id="tool-call-1",
        status=ToolResultStatus.NOT_FOUND,
        temporal_scope=TemporalScope.CURRENT,
        data=None,
        evidence=(),
        as_of_utc=T,
        limitations=(),
    )
    assessment = assess_source_read_trace(result, disposition=MasterDisposition.EXECUTED)
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is True
    assert assessment.rows_absent is True
    assert assessment.verified_zero_established is False


def test_conflicting_source_versions_and_event_times_stay_unestablished():
    evidence = _evidence(
        source_records=(
            _record(source_version="v1", event_timestamp_utc=T),
            _record(source_version="v2", event_timestamp_utc=T),
            _record(source_version="v1", event_timestamp_utc=LATER),
        ),
    )
    assessment = assess_source_read_trace(evidence)
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is True
    version = assessment.classification.finding(
        MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY
    )
    clocks = assessment.classification.finding(
        MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME
    )
    assert ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE in version.limitations
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS in clocks.limitations
    assert version.status is ProvenanceRequirementStatus.UNSATISFIED
    assert clocks.status is ProvenanceRequirementStatus.UNSATISFIED


def test_permit_reference_is_not_capability_consumption_or_dispatch():
    assessment = assess_source_read_trace(_event(), authorization=_authorization())
    _assert_closed(assessment)
    assert (
        _presence(assessment, SourceReadTraceObligation.CAPABILITY_CONSUMPTION)
        is SourceReadTracePresence.PROPOSED_UNOBSERVED
    )
    execution = assessment.classification.finding(
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT
    )
    assert ProvenanceAuditLimitation.PERMIT_REFERENCE_NOT_CONSUMPTION in execution.limitations


def test_local_consumption_result_is_not_a_trace_subject():
    fabricated = LocalConsumptionResult(
        outcome=LocalConsumptionOutcome.TRANSITION_RECORDED,
        reason=None,
        local_state_transition_only=True,
    )
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        assess_source_read_trace(fabricated)  # type: ignore[arg-type]
    clock = LocalTestClock(datetime(2026, 1, 15, tzinfo=timezone.utc))
    registry = LocalCapabilityRegistry(clock)
    scope = LocalCapabilityScope(
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        agent_capability=AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
    )
    handle = registry.issue(
        scope=scope,
        invocation_id="inv-1",
        consumer_id="consumer-1",
        session_id="session-1",
        expires_at=datetime(2026, 1, 16, tzinfo=timezone.utc),
    )
    consumed = registry.consume(
        handle,
        scope=scope,
        invocation_id="inv-1",
        consumer_id="consumer-1",
    )
    assert consumed.outcome is LocalConsumptionOutcome.TRANSITION_RECORDED
    assert consumed.execution_permitted is False
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        assess_source_read_trace(consumed)  # type: ignore[arg-type]
    assessment = assess_source_read_trace(_event(permit_consumption_ref="permit-1"))
    _assert_closed(assessment)
    assert assessment.capability_consumption_permits_dispatch is False
    assert assessment.observed_trusted_runtime_events == ()


def test_mismatched_verified_zero_tool_call_conflicts():
    evidence = _evidence(source_records=())
    assessment = assess_source_read_trace(
        _tool_result(evidence, data={"rows": []}),
        verified_zero_claim=VerifiedZeroClaim(tool_call_id="other-call"),
    )
    _assert_closed(assessment)
    assert assessment.proposed_identity_conflict is True
    assert assessment.rows_absent is True


def test_authority_promotion_is_rejected():
    event = _event()
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        assess_source_read_trace(
            event,
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        assess_source_read_trace(event, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        assess_source_read_trace(event, source_authenticity="ESTABLISHED")
    assessment = assess_source_read_trace(event)
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(assessment, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="trusted_runtime_observation_not_available"):
        replace(assessment, observed_trusted_runtime_events=("runtime-event",))
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(assessment, verified_zero_established=True)


def test_unsupported_trace_attachments_are_rejected():
    event = _event()
    with pytest.raises(ContractValidationError, match="invalid_executed_tool_invocation"):
        assess_source_read_trace(event, executed_invocation={"tool_name": "describe_hazard"})  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_disposition"):
        assess_source_read_trace(event, disposition="CANCELLED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        assess_source_read_trace(object())  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_verified_zero_claim"):
        assess_source_read_trace(
            event,
            verified_zero_claim=VerifiedZeroClaim(tool_call_id="tool-call-1"),
        )


def test_direct_observation_flag_cannot_be_set():
    with pytest.raises(ContractValidationError, match="trusted_runtime_observation_not_available"):
        boundary.SourceReadTraceObligationFinding(
            obligation=SourceReadTraceObligation.SOURCE_RESPONSE_IDENTITY,
            requirement=MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
            presence=SourceReadTracePresence.PROPOSED_UNOBSERVED,
            trusted_runtime_observed=True,
        )
