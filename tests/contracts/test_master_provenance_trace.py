"""Fail-closed provenance audit. Supplied metadata is not an authenticated read."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError, replace

import pytest

import wilvor_ai.master_provenance_trace as trace
from wilvor_ai.contracts import (
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
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_parallel_contracts import MasterProvenanceRequirement
from wilvor_ai.master_provenance_trace import (
    AUTHENTICATED_SOURCE_READ_AVAILABLE,
    MasterProvenanceClassification,
    ProposedDispatcherExecutionEvent,
    ProposedPopulationScope,
    ProposedRetrievalLimit,
    ProvenanceAuditLimitation,
    ProvenanceRequirementFinding,
    ProvenanceRequirementStatus,
    ProvenanceSubjectKind,
    classify_master_provenance,
)
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.specialist_contracts import VerifiedZeroClaim


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
        "data": {"aircraft_id": "ABC123"},
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
        "limitation_notes": (),
        "freshness_status": FreshnessStatus.FRESH,
        "execution_trace_ref": None,
        "verified_zero_claim": None,
    }
    values.update(overrides)
    return ProposedDispatcherExecutionEvent(**values)  # type: ignore[arg-type]


def _assert_closed(classification: MasterProvenanceClassification) -> None:
    assert classification.authenticated_source_read_available is False
    assert classification.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert classification.production_authority is MasterProductionAuthority.DENIED
    assert classification.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert classification.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert classification.dispatch_permitted is False
    assert classification.end_to_end_fulfilled is False
    assert tuple(item.requirement for item in classification.findings) == tuple(
        MasterProvenanceRequirement
    )
    assert len(classification.findings) == 7
    for finding in classification.findings:
        assert finding.status in CLOSED
        assert (
            ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE
            in finding.limitations
        )


def test_provenance_status_cannot_mark_a_requirement_satisfied():
    assert set(ProvenanceRequirementStatus) == {
        ProvenanceRequirementStatus.UNRESOLVED,
        ProvenanceRequirementStatus.UNSATISFIED,
    }
    assert list(MasterSourceAuthenticity) == [MasterSourceAuthenticity.NOT_ESTABLISHED]
    assert list(MasterRouteExecutionAuthority) == [MasterRouteExecutionAuthority.DENIED]
    assert AUTHENTICATED_SOURCE_READ_AVAILABLE is False
    assert set(ProvenanceSubjectKind) == {
        ProvenanceSubjectKind.DESCRIPTIVE_TOOL_RESULT,
        ProvenanceSubjectKind.DESCRIPTIVE_EVIDENCE,
        ProvenanceSubjectKind.PROPOSED_DISPATCHER_EVENT,
    }
    with pytest.raises(ContractValidationError, match="provenance_requirement_not_satisfied"):
        ProvenanceRequirementFinding(
            requirement=MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
            status="SATISFIED",  # type: ignore[arg-type]
            limitations=(
                ProvenanceAuditLimitation.AUTHENTICATED_SOURCE_READ_NOT_AVAILABLE,
            ),
        )


def test_no_public_constructor_marks_provenance_authenticated():
    signature = inspect.signature(classify_master_provenance)
    assert "authenticated" not in signature.parameters
    assert "provenance_authenticated" not in signature.parameters
    assert "source_authenticity" not in signature.parameters
    functions = [
        name
        for name, value in vars(trace).items()
        if callable(value)
        and not isinstance(value, type)
        and not name.startswith("_")
        and getattr(value, "__module__", None) == trace.__name__
    ]
    assert functions == ["classify_master_provenance"]
    with pytest.raises(TypeError):
        ProposedDispatcherExecutionEvent(  # type: ignore[call-arg]
            master_request_id="master-req-1",
            master_invocation_id="inv-1",
            tool_name="describe_hazard",
            tool_call_id="tool-call-1",
            population_scope=ProposedPopulationScope.UNSPECIFIED,
            authenticated_source_read_available=True,
        )


def test_fabricated_metadata_with_every_field_populated_stays_unsatisfied():
    authorization = _authorization()
    event = classify_master_provenance(_event(), authorization=authorization)
    _assert_closed(event)
    assert event.subject_kind is ProvenanceSubjectKind.PROPOSED_DISPATCHER_EVENT
    assert all(
        finding.status is ProvenanceRequirementStatus.UNSATISFIED for finding in event.findings
    )
    binding = event.finding(MasterProvenanceRequirement.REQUEST_ENTITY_BINDING)
    version = event.finding(MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY)
    clocks = event.finding(MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME)
    population = event.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS)
    assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN in binding.limitations
    assert ProvenanceAuditLimitation.ENTITY_SCOPE_NOT_BINDING in binding.limitations
    assert ProvenanceAuditLimitation.FOREIGN_REQUEST not in binding.limitations
    assert ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF in version.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in clocks.limitations
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in clocks.limitations
    assert ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED in clocks.limitations
    assert ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED in population.limitations
    assert event.finding(MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER).status is (
        ProvenanceRequirementStatus.UNSATISFIED
    )
    assert (
        ProvenanceAuditLimitation.PERMIT_REFERENCE_NOT_CONSUMPTION
        in event.finding(MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT).limitations
    )

    evidence = _evidence(
        source_records=(
            _record("rec-1", "v1", T),
            _record("rec-2", "v1", T),
        ),
        completeness=SourceCompleteness(status="COMPLETE", evaluated_as_of_utc=T),
        match_cardinality=MatchCardinality(exact_count=2, is_exact=True),
    )
    claimed = {
        "source_authenticity": "ESTABLISHED",
        "execution_authority": "GRANTED",
        "production_authority": "PRODUCTION",
        "verified_zero": True,
        "source_version": "forged-from-payload",
    }
    descriptive = classify_master_provenance(
        _tool_result(evidence, data=claimed),
        authorization=authorization,
    )
    plain = classify_master_provenance(_tool_result(evidence), authorization=authorization)
    _assert_closed(descriptive)
    assert descriptive == plain
    assert descriptive.subject_kind is ProvenanceSubjectKind.DESCRIPTIVE_TOOL_RESULT
    assert (
        descriptive.finding(MasterProvenanceRequirement.TRUSTED_SOURCE_ADAPTER).status
        is ProvenanceRequirementStatus.UNRESOLVED
    )
    assert (
        ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN
        in descriptive.finding(MasterProvenanceRequirement.REQUEST_ENTITY_BINDING).limitations
    )
    assert (
        ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO
        not in descriptive.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE).limitations
    )
    evidence_only = classify_master_provenance(evidence, authorization=authorization)
    _assert_closed(evidence_only)
    assert evidence_only.subject_kind is ProvenanceSubjectKind.DESCRIPTIVE_EVIDENCE
    assert (
        evidence_only.finding(MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY).status
        is ProvenanceRequirementStatus.UNSATISFIED
    )


def test_foreign_request_and_invocation_stay_unsatisfied():
    authorization = _authorization(
        master_request_id="other-req",
        invocation_id="other-inv",
        specialist_request_id="other-spec",
        correlation_id="other-corr",
    )
    classification = classify_master_provenance(_event(), authorization=authorization)
    _assert_closed(classification)
    for requirement in (
        MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT,
        MasterProvenanceRequirement.REQUEST_ENTITY_BINDING,
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
    ):
        finding = classification.finding(requirement)
        assert finding.status is ProvenanceRequirementStatus.UNSATISFIED
        assert ProvenanceAuditLimitation.FOREIGN_REQUEST in finding.limitations
        assert ProvenanceAuditLimitation.FOREIGN_INVOCATION in finding.limitations
        assert ProvenanceAuditLimitation.CORRELATION_MISMATCH in finding.limitations
        assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN not in finding.limitations

    descriptive = classify_master_provenance(
        _tool_result(),
        authorization=authorization,
    )
    _assert_closed(descriptive)
    context = descriptive.finding(MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT)
    assert ProvenanceAuditLimitation.CORRELATION_MISMATCH in context.limitations
    assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN not in context.limitations
    assert descriptive.dispatch_permitted is False


def test_missing_source_version_does_not_establish_integrity():
    missing = classify_master_provenance(_event(source_version=None))
    version = missing.finding(MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY)
    assert version.status is ProvenanceRequirementStatus.UNRESOLVED
    assert ProvenanceAuditLimitation.MISSING_SOURCE_VERSION in version.limitations
    assert ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF not in version.limitations
    _assert_closed(missing)

    evidence = _evidence(source_records=(_record(source_version=None),))
    descriptive = classify_master_provenance(
        _tool_result(evidence, data={"source_version": "forged-from-payload"}),
    )
    descriptive_version = descriptive.finding(MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY)
    assert descriptive_version.status is ProvenanceRequirementStatus.UNRESOLVED
    assert ProvenanceAuditLimitation.MISSING_SOURCE_VERSION in descriptive_version.limitations
    assert (
        ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF
        not in descriptive_version.limitations
    )
    _assert_closed(descriptive)


def _version_finding(classification: MasterProvenanceClassification):
    return classification.finding(MasterProvenanceRequirement.SOURCE_VERSION_INTEGRITY)


def _clock_finding(classification: MasterProvenanceClassification):
    return classification.finding(MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME)


def test_distinct_clock_roles_are_not_timestamp_contradictions():
    """Event, query, observation, evaluation, and requested evaluation stay distinct."""

    event_and_query = classify_master_provenance(
        _tool_result(
            _evidence(
                query_timestamp_utc=T,
                source_records=(_record(event_timestamp_utc=LATER),),
                completeness=SourceCompleteness(evaluated_as_of_utc="2026-01-15T04:00:00Z"),
            ),
            as_of_utc="2026-01-15T05:00:00Z",
        ),
        authorization=_authorization(requested_evaluation_time_utc="2026-01-15T06:00:00Z"),
    )
    clocks = _clock_finding(event_and_query)
    assert clocks.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in clocks.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in clocks.limitations
    assert ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED in clocks.limitations
    _assert_closed(event_and_query)

    observation_and_evaluation = classify_master_provenance(
        _event(
            observation_time_utc=T,
            evaluation_time_utc=LATER,
            completeness=SourceCompleteness(evaluated_as_of_utc="2026-01-15T04:00:00Z"),
        ),
        authorization=_authorization(requested_evaluation_time_utc="2026-01-15T06:00:00Z"),
    )
    proposed_clocks = _clock_finding(observation_and_evaluation)
    assert proposed_clocks.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in proposed_clocks.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in proposed_clocks.limitations
    assert ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED in proposed_clocks.limitations
    _assert_closed(observation_and_evaluation)

    stale_roles = classify_master_provenance(
        _event(
            observation_time_utc=T,
            evaluation_time_utc=LATER,
            freshness_status=FreshnessStatus.STALE,
            population_scope=ProposedPopulationScope.UNSPECIFIED,
            completeness=None,
        )
    )
    stale_clocks = _clock_finding(stale_roles)
    assert ProvenanceAuditLimitation.STALE_DATA in stale_clocks.limitations
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in stale_clocks.limitations
    assert ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED in stale_clocks.limitations
    _assert_closed(stale_roles)

    missing_clocks = classify_master_provenance(
        _event(
            observation_time_utc=None,
            evaluation_time_utc=None,
            freshness_status=None,
            completeness=None,
            population_scope=ProposedPopulationScope.UNSPECIFIED,
        )
    )
    absent = _clock_finding(missing_clocks)
    assert absent.status is ProvenanceRequirementStatus.UNRESOLVED
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in absent.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF not in absent.limitations
    assert ProvenanceAuditLimitation.STALE_DATA not in absent.limitations
    _assert_closed(missing_clocks)


def test_contradictory_source_event_times_stay_unauthenticated():
    same_version = (
        _record("rec-1", "v1", T),
        _record("rec-1", "v1", LATER),
    )
    contradictory = classify_master_provenance(
        _evidence(query_timestamp_utc="2026-01-15T07:00:00Z", source_records=same_version)
    )
    clocks = _clock_finding(contradictory)
    assert clocks.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS in clocks.limitations
    assert ProvenanceAuditLimitation.TIMESTAMPS_NOT_OBSERVATION_PROOF in clocks.limitations
    _assert_closed(contradictory)

    equivalent_spellings = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", "v1", T),
                _record("rec-1", "v1", "2026-01-15T00:00:00+00:00"),
            )
        )
    )
    assert (
        ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS
        not in _clock_finding(equivalent_spellings).limitations
    )
    _assert_closed(equivalent_spellings)

    missing_version = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", None, T),
                _record("rec-1", None, LATER),
            )
        )
    )
    assert ProvenanceAuditLimitation.INCONSISTENT_TIMESTAMPS not in _clock_finding(missing_version).limitations
    assert ProvenanceAuditLimitation.MISSING_SOURCE_VERSION in _version_finding(missing_version).limitations
    _assert_closed(missing_version)


def test_source_versions_conflict_only_for_one_recorded_identity():
    different_records = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", "v1", T),
                _record("rec-2", "v2", LATER),
            )
        )
    )
    different_finding = _version_finding(different_records)
    assert different_finding.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF in different_finding.limitations
    assert ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE not in different_finding.limitations
    _assert_closed(different_records)

    same_record_different_events = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", "v1", T),
                _record("rec-1", "v2", LATER),
            )
        )
    )
    assert (
        ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE
        not in _version_finding(same_record_different_events).limitations
    )
    assert (
        ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF
        in _version_finding(same_record_different_events).limitations
    )
    _assert_closed(same_record_different_events)

    missing_event_time = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", "v1", None),
                _record("rec-1", "v2", None),
            )
        )
    )
    assert ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE not in _version_finding(missing_event_time).limitations
    assert (
        ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF
        in _version_finding(missing_event_time).limitations
    )
    _assert_closed(missing_event_time)

    alpha = _evidence(source="alpha", source_records=(_record("rec-1", "v1", T),))
    beta = _evidence(source="beta", source_records=(_record("rec-1", "v2", T),))
    separate_datasets = classify_master_provenance(
        ToolResult(
            tool_name="describe_hazard",
            tool_call_id="tool-call-1",
            status=ToolResultStatus.SUCCESS,
            temporal_scope=TemporalScope.CURRENT,
            data={"aircraft_id": "ABC123"},
            evidence=(alpha, beta),
            as_of_utc=T,
            limitations=(),
            correlation_id="corr-1",
        )
    )
    assert ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE not in _version_finding(separate_datasets).limitations
    _assert_closed(separate_datasets)

    conflict = classify_master_provenance(
        _evidence(
            source_records=(
                _record("rec-1", "v1", T),
                _record("rec-1", "v2", "2026-01-15T00:00:00+00:00"),
            )
        )
    )
    conflict_finding = _version_finding(conflict)
    assert conflict_finding.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.SOURCE_VERSIONS_DISAGREE in conflict_finding.limitations
    assert ProvenanceAuditLimitation.SOURCE_VERSION_NOT_INTEGRITY_PROOF in conflict_finding.limitations
    _assert_closed(conflict)


def test_partial_population_scope_stays_unsatisfied():
    proposed = classify_master_provenance(
        _event(population_scope=ProposedPopulationScope.PARTIAL)
    )
    population = proposed.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS)
    assert population.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE in population.limitations
    assert ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED in population.limitations
    _assert_closed(proposed)

    bounded = classify_master_provenance(
        _evidence(match_cardinality=MatchCardinality(is_exact=False, minimum_count=1))
    )
    assert (
        ProvenanceAuditLimitation.PARTIAL_POPULATION_SCOPE
        in bounded.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS).limitations
    )
    labeled = classify_master_provenance(
        _evidence(completeness=SourceCompleteness(status="PARTIAL", evaluated_as_of_utc=T))
    )
    assert (
        labeled.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS).status
        is ProvenanceRequirementStatus.UNSATISFIED
    )
    _assert_closed(bounded)
    _assert_closed(labeled)


def test_retrieval_limits_do_not_establish_completeness():
    limited = classify_master_provenance(
        _event(
            population_scope=ProposedPopulationScope.UNSPECIFIED,
            completeness=None,
            retrieval_limits=(ProposedRetrievalLimit.ROW_LIMIT,),
            limitation_notes=("row limit",),
        )
    )
    population = limited.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS)
    assert population.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.RETRIEVAL_LIMITED in population.limitations
    _assert_closed(limited)

    claimed_complete = classify_master_provenance(
        _event(retrieval_limits=(ProposedRetrievalLimit.RESULT_TRUNCATED,))
    )
    complete_population = claimed_complete.finding(
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS
    )
    assert complete_population.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.RETRIEVAL_LIMITED in complete_population.limitations
    assert (
        ProvenanceAuditLimitation.POPULATION_SCOPE_NOT_ESTABLISHED
        in complete_population.limitations
    )

    partial = classify_master_provenance(
        _tool_result(
            _evidence(limitations=("row limit",)),
            status=ToolResultStatus.PARTIAL,
            limitations=("row limit",),
        )
    )
    assert (
        ProvenanceAuditLimitation.RETRIEVAL_LIMITED
        in partial.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS).limitations
    )
    assert partial.dispatch_permitted is False


def test_stale_data_does_not_establish_observation():
    proposed = classify_master_provenance(
        _event(
            freshness_status=FreshnessStatus.STALE,
            population_scope=ProposedPopulationScope.UNSPECIFIED,
            completeness=None,
        )
    )
    clocks = proposed.finding(MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME)
    assert clocks.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.STALE_DATA in clocks.limitations
    assert ProvenanceAuditLimitation.FRESHNESS_NOT_AUTHENTICATED in clocks.limitations
    _assert_closed(proposed)

    descriptive = classify_master_provenance(
        _tool_result(
            _evidence(freshness_status=FreshnessStatus.STALE),
            status=ToolResultStatus.STALE,
            limitations=("stale source",),
        )
    )
    assert (
        ProvenanceAuditLimitation.STALE_DATA
        in descriptive.finding(MasterProvenanceRequirement.OBSERVATION_EVALUATION_TIME).limitations
    )
    assert descriptive.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert descriptive.production_authority is MasterProductionAuthority.DENIED


def test_fabricated_verified_zero_claim_is_not_authenticated():
    claim = VerifiedZeroClaim(tool_call_id="tool-call-1")
    proposed = classify_master_provenance(
        _event(
            verified_zero_claim=claim,
            population_scope=ProposedPopulationScope.CLAIMED_COMPLETE,
        ),
        authorization=_authorization(),
    )
    _assert_closed(proposed)
    for requirement in (
        MasterProvenanceRequirement.SOURCE_READ_PROVENANCE,
        MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS,
    ):
        finding = proposed.finding(requirement)
        assert finding.status is ProvenanceRequirementStatus.UNSATISFIED
        assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO in finding.limitations
        assert ProvenanceAuditLimitation.MATCHING_IDENTIFIERS_NOT_ORIGIN in finding.limitations

    exact_zero = classify_master_provenance(
        _evidence(match_cardinality=MatchCardinality(exact_count=0, is_exact=True))
    )
    assert (
        ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO
        in exact_zero.finding(MasterProvenanceRequirement.POPULATION_SCOPE_COMPLETENESS).limitations
    )
    _assert_closed(exact_zero)

    attached = classify_master_provenance(_evidence(), verified_zero_claim=claim)
    assert (
        ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO
        in attached.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE).limitations
    )
    assert attached.authenticated_source_read_available is False

    payload = classify_master_provenance(
        _tool_result(data={"verified_zero": True, "exact_count": 0})
    )
    untouched = classify_master_provenance(_tool_result())
    assert payload == untouched
    assert (
        ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO
        not in payload.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE).limitations
    )
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        classify_master_provenance(claim)  # type: ignore[arg-type]


def test_forged_execution_trace_is_not_source_read_provenance():
    forged_evidence = _evidence(
        query_executions=(
            QueryExecutionTrace(
                query_id="query-1",
                execution_id="execution-1",
                rows_returned=0,
                bytes_scanned=12,
                engine_scope="fixture",
            ),
        )
    )
    descriptive = classify_master_provenance(forged_evidence)
    source = descriptive.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    context = descriptive.finding(MasterProvenanceRequirement.AUTHORIZED_EXECUTION_CONTEXT)
    assert source.status is ProvenanceRequirementStatus.UNSATISFIED
    assert context.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in source.limitations
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in context.limitations
    assert ProvenanceAuditLimitation.FABRICATED_VERIFIED_ZERO not in source.limitations
    _assert_closed(descriptive)

    proposed = classify_master_provenance(_event(execution_trace_ref="trace-1"))
    proposed_source = proposed.finding(MasterProvenanceRequirement.SOURCE_READ_PROVENANCE)
    assert proposed_source.status is ProvenanceRequirementStatus.UNSATISFIED
    assert ProvenanceAuditLimitation.FORGED_EXECUTION_TRACE in proposed_source.limitations
    assert proposed.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert proposed.dispatch_permitted is False
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        classify_master_provenance(
            QueryExecutionTrace(query_id="query-1", execution_id="execution-1")
        )  # type: ignore[arg-type]


def test_denied_authenticity_fields_are_immutable():
    event = _event()
    classification = classify_master_provenance(event, authorization=_authorization())
    assert event.authenticated_source_read_available is False
    assert classification.authenticated_source_read_available is False
    for target in (event, classification):
        with pytest.raises(FrozenInstanceError):
            target.source_authenticity = "ESTABLISHED"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            target.production_authority = MasterProductionAuthority.PRODUCTION  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            target.execution_authority = "GRANTED"  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            target.dispatch_permitted = True  # type: ignore[misc]
        with pytest.raises(FrozenInstanceError):
            target.end_to_end_fulfilled = True  # type: ignore[misc]
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        replace(event, production_authority=MasterProductionAuthority.PRODUCTION)
    with pytest.raises(ContractValidationError, match="source_authenticity_not_established"):
        replace(event, source_authenticity="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_authority_not_granted"):
        replace(classification, execution_authority="GRANTED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="execution_not_granted"):
        replace(classification, dispatch_permitted=True)
    with pytest.raises(ContractValidationError, match="end_to_end_fulfillment_not_granted"):
        replace(event, end_to_end_fulfilled=True)
    with pytest.raises(ContractValidationError, match="request_coverage_not_established"):
        replace(classification, request_coverage="ESTABLISHED")  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        replace(event, schema_version="wilvor.ai.master_provenance_trace.forged")


def test_planning_authorization_does_not_grant_execution():
    authorization = _authorization()
    classification = classify_master_provenance(_event(), authorization=authorization)
    _assert_closed(classification)
    assert classification.dispatch_permitted is False
    assert classification.production_authority is MasterProductionAuthority.DENIED
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        classify_master_provenance(authorization)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="unsupported_provenance_subject"):
        classify_master_provenance({"master_request_id": "master-req-1", "source_version": "v1"})  # type: ignore[arg-type]


def test_malformed_audit_record_is_rejected():
    with pytest.raises(ContractValidationError, match="invalid_tool_arguments"):
        _event(tool_arguments=[("aircraft_id", "ABC123")])  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError, match="invalid_tool_name"):
        _event(tool_name="  ")
    with pytest.raises(ContractValidationError, match="invalid_verified_zero_claim"):
        classify_master_provenance(_event(), verified_zero_claim=VerifiedZeroClaim("tool-call-1"))
