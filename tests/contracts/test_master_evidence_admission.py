"""Offline admission tests. These fixtures are contracts, not authenticated evidence."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CONVERSATION_ID,
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_EPOCH,
    SYNTHETIC_REQUEST_ID,
    SYNTHETIC_UTC,
    completed_all_verified,
    none_verified,
    partially_verified,
    sample_request,
    trusted_context,
    unique_match_result,
)
from tests.contracts.test_master_contracts import _mixed_scope_decision_result
from wilvor_ai.contracts import ContractValidationError, TemporalScope
from wilvor_ai.live_ops_public_contracts import live_ops_reference_utc
from wilvor_ai.decision_model_contracts import DecisionUnsupportedReason
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    classify_historical_result,
)
from wilvor_ai.master_evidence_admission import (
    MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION,
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmission,
    MasterEvidenceAdmissionCollection,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
    MasterTemporalLimitation,
    admit_master_evidence,
)
from wilvor_ai.master_orchestration import SyntheticWorkerResult, SyntheticWorkflowResult
from wilvor_ai.master_routing import MasterRouteProposal, MasterRouteExecutionAuthority
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult, SpecialistRunStatus


AS_OF = "2026-01-15T00:00:00Z"
TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"


def _authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-hist-1",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "requested_evaluation_time_utc": AS_OF,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _historical(**overrides: object) -> SpecialistResult:
    values: dict[str, object] = {
        "status": SpecialistStatus.ANSWERED,
        "answer": "Historical specialist artifact for structural admission.",
        "temporal_scope": TemporalScope.HISTORICAL,
        "evaluated_as_of_utc": AS_OF,
        "tool_results": (),
        "verified_claims": (),
        "limitations": (),
        "unsupported_reason": None,
        "verifier_outcome": VerifierOutcome.NOT_RUN,
    }
    values.update(overrides)
    return SpecialistResult(**values)  # type: ignore[arg-type]


def _decision(**overrides: object) -> DecisionSpecialistRunResult:
    values: dict[str, object] = {
        "status": DecisionSpecialistRunStatus.UNSUPPORTED,
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": "ac-1",
        "recommendation_id": None,
        "tool_results": (),
        "evidence_snapshots": (),
        "evidence_bindings": (),
        "invocations": (),
        "proposed_claims": (),
        "verification": None,
        "render": None,
        "unsupported_reason": DecisionUnsupportedReason.INSUFFICIENT_EVIDENCE,
        "runtime_errors": (),
        "collection_partial_reason": None,
        "validation_feedback": (),
        "provider_turn_count": 0,
        "executed_tool_call_count": 0,
        "executed_persisted_call_count": 0,
        "terminal_kind": None,
    }
    values.update(overrides)
    return DecisionSpecialistRunResult(**values)  # type: ignore[arg-type]


def _live_sources(result=None, request=None, context=None) -> LiveOpsAdmissionSources:
    return LiveOpsAdmissionSources(
        request=sample_request() if request is None else request,
        context=trusted_context() if context is None else context,
        result=none_verified() if result is None else result,
    )


def _one(authorization: MasterInvocationAuthorization, artifact: object):
    collection = admit_master_evidence((MasterEvidenceAdmissionInput(authorization, artifact),))
    assert collection.evidence_verified is False
    assert collection.production_authority is MasterProductionAuthority.DENIED
    assert collection.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    return collection.admissions[0]


def _untrusted(admission) -> None:
    assert admission.evidence_verified is False
    assert admission.production_authority is MasterProductionAuthority.DENIED
    assert admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert admission.request_coverage.value == "NOT_ESTABLISHED"
    assert admission.end_to_end_fulfilled is False
    assert "answer" not in MasterEvidenceAdmission.__dataclass_fields__


def test_historical_artifact_is_structurally_admitted_without_authentication():
    admission = _one(_authorization(), _historical())
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterTemporalLimitation.HISTORICAL_SCOPE in admission.temporal_limitations
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in admission.reason_codes
    _untrusted(admission)


def test_live_ops_public_artifact_is_structurally_admitted():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources())
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_NONE_VERIFIED in admission.reason_codes
    assert MasterAdmissionReason.LIVE_OPS_EVIDENCE_REFS_ARE_NOT_TOOL_RESULTS in admission.reason_codes
    _untrusted(admission)


def test_decision_artifact_is_structurally_admitted_as_a_comparison_candidate():
    authorization = _authorization(
        invocation_id="inv-dec-1",
        specialist=MasterSpecialist.DECISION,
        expected_aircraft_id="ac-1",
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _decision())
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.EXPECTED_AIRCRAFT_IS_COMPARISON_CANDIDATE in admission.reason_codes
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in admission.reason_codes
    assert MasterTemporalLimitation.DECISION_SCOPE_NOT_CARRIED in admission.temporal_limitations
    _untrusted(admission)


def test_incorrect_specialist_type_is_rejected():
    admission = _one(_authorization(specialist=MasterSpecialist.LIVE_OPS, invocation_id="inv-live-1"), _historical())
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.SPECIALIST_ARTIFACT_MISMATCH in admission.reason_codes


def test_missing_native_artifact_is_rejected():
    admission = _one(_authorization(), None)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.MISSING_NATIVE_ARTIFACT in admission.reason_codes


def test_synthetic_worker_result_is_rejected():
    from wilvor_ai.master_orchestration import SyntheticOutcome, SyntheticProductionAuthority

    artifact = SyntheticWorkerResult(
        master_request_id="master-req-1",
        correlation_id="corr-1",
        invocation_id="inv-hist-1",
        specialist_request_id="master-req-1-specialist-1",
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
        note="synthetic",
    )
    assert artifact.production_authority is SyntheticProductionAuthority.DENIED
    admission = _one(_authorization(), artifact)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.reason_codes
    _untrusted(admission)


def test_synthetic_workflow_result_is_rejected():
    artifact = SyntheticWorkflowResult(
        terminal_status=__import__(
            "wilvor_ai.master_orchestration",
            fromlist=["SyntheticTerminalStatus"],
        ).SyntheticTerminalStatus.NOT_RUN,
        reasons=(),
        decision=None,
        step_statuses=(),
        results=(),
        limitations=(),
        dispatch_count=0,
        dispatch_batches=(),
        executed_nodes=(),
    )
    admission = _one(_authorization(), artifact)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert isinstance(artifact, SyntheticWorkflowResult)


def test_plain_dictionary_is_rejected():
    admission = _one(_authorization(), {"verification": "PASSED", "evidence_verified": True})
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in admission.reason_codes


def test_deserialized_passed_classification_is_rejected():
    classification = classify_historical_result(
        _authorization(specialist_request_id="specialist-req-1", correlation_id="corr-1"),
        _historical(verifier_outcome=VerifierOutcome.PASSED, verified_claims=(VerifiedZeroClaim("call-1"),)),
    )
    assert classification.record.verification.value == "PASSED"
    admission = _one(_authorization(), classification)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.CLASSIFICATION_NOT_NATIVE_ARTIFACT in admission.reason_codes
    _untrusted(admission)


def test_serialized_record_with_a_different_invocation_is_rejected():
    record = classify_historical_result(
        _authorization(invocation_id="inv-other", specialist_request_id="specialist-req-1"),
        _historical(),
    ).record
    restored = type(record).from_dict(record.to_dict())
    admission = _one(_authorization(), restored)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.SERIALIZED_RECORD in admission.reason_codes
    assert MasterAdmissionReason.INVOCATION_ID_MISMATCH in admission.reason_codes


def test_wrong_master_request_is_rejected_for_a_collection():
    other = _authorization(master_request_id="master-req-2", invocation_id="inv-hist-2")
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        admit_master_evidence(
            (
                MasterEvidenceAdmissionInput(_authorization(), _historical()),
                MasterEvidenceAdmissionInput(other, _historical()),
            )
        )


def test_wrong_live_ops_request_id_is_rejected():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id="other-request",
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources())
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterBindingDefect.REQUEST_ID_MISMATCH in admission.binding_defects


def test_wrong_live_ops_correlation_is_rejected():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id="corr-other",
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources())
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterBindingDefect.CORRELATION_MISMATCH in admission.binding_defects


def test_missing_native_correlation_stays_unresolved():
    authorization = _authorization(correlation_id="corr-master-1")
    admission = _one(authorization, _historical())
    assert admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_CORRELATION_ABSENT in admission.reason_codes
    assert not hasattr(admission, "artifact_correlation_id")


def test_missing_native_specialist_request_id_stays_unresolved():
    authorization = _authorization(specialist_request_id="specialist-req-1")
    admission = _one(authorization, _historical())
    assert admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_SPECIALIST_REQUEST_ID_ABSENT in admission.reason_codes


def test_decision_target_mismatch_is_rejected():
    authorization = _authorization(
        invocation_id="inv-dec-1",
        specialist=MasterSpecialist.DECISION,
        expected_aircraft_id="ac-1",
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _decision(aircraft_id="ac-2"))
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterBindingDefect.TARGET_MISMATCH in admission.binding_defects
    _untrusted(admission)


def test_callsign_discovery_does_not_authorize_a_target():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources(unique_match_result()))
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.CALLSIGN_DISCOVERY_NOT_TARGET in admission.reason_codes
    assert "trusted_aircraft_id" not in MasterEvidenceAdmission.__dataclass_fields__
    _untrusted(admission)


def test_distinct_invocation_ids_can_share_one_master_request():
    live = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    collection = admit_master_evidence(
        (
            MasterEvidenceAdmissionInput(_authorization(), _historical()),
            MasterEvidenceAdmissionInput(live, _live_sources()),
        )
    )
    assert [item.invocation_id for item in collection.admissions] == ["inv-hist-1", "inv-live-1"]
    assert {item.specialist for item in collection.admissions} == {
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterSpecialist.LIVE_OPS,
    }
    assert collection.evidence_verified is False


def test_duplicate_invocation_id_is_rejected():
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        admit_master_evidence(
            (
                MasterEvidenceAdmissionInput(_authorization(), _historical()),
                MasterEvidenceAdmissionInput(_authorization(invocation_id="inv-hist-1"), _historical()),
            )
        )


def test_more_than_four_invocations_are_rejected():
    items = tuple(
        MasterEvidenceAdmissionInput(_authorization(invocation_id=f"inv-{index}"), _historical())
        for index in range(5)
    )
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        admit_master_evidence(items)


def test_more_than_one_decision_invocation_is_rejected():
    first = _authorization(
        invocation_id="inv-dec-1",
        specialist=MasterSpecialist.DECISION,
        expected_aircraft_id="ac-1",
        requested_evaluation_time_utc=None,
    )
    second = _authorization(
        invocation_id="inv-dec-2",
        specialist=MasterSpecialist.DECISION,
        expected_aircraft_id="ac-1",
        requested_evaluation_time_utc=None,
    )
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        admit_master_evidence(
            (
                MasterEvidenceAdmissionInput(first, _decision()),
                MasterEvidenceAdmissionInput(second, _decision()),
            )
        )


def test_partially_verified_live_ops_stays_limited():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources(partially_verified()))
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_PARTIALLY_VERIFIED in admission.reason_codes
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION not in admission.reason_codes
    _untrusted(admission)


def test_unknown_freshness_is_not_upgraded():
    admission = _one(_authorization(), _historical())
    assert MasterTemporalLimitation.FRESHNESS_UNKNOWN in admission.temporal_limitations
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert not any(item.value == "FRESH" for item in admission.temporal_limitations)
    _untrusted(admission)


def test_historical_verifier_passed_is_not_source_authentication():
    artifact = _historical(
        verifier_outcome=VerifierOutcome.PASSED,
        verified_claims=(VerifiedZeroClaim("call-1"),),
    )
    assert artifact.verifier_outcome is VerifierOutcome.PASSED
    admission = _one(_authorization(), artifact)
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.HISTORICAL_VERIFIER_NOT_SOURCE_AUTHENTICATION in admission.reason_codes
    assert MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED in admission.reason_codes
    _untrusted(admission)


def test_decision_verifier_passed_is_not_source_authentication():
    artifact = _mixed_scope_decision_result()
    assert artifact.verification is not None
    assert artifact.verification.outcome is VerifierOutcome.PASSED
    admission = _one(
        _authorization(
            invocation_id="inv-dec-1",
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc=None,
        ),
        artifact,
    )
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in admission.reason_codes
    assert MasterTemporalLimitation.DECISION_SCOPE_CURRENT in admission.temporal_limitations
    assert MasterTemporalLimitation.DECISION_SCOPE_PERSISTED in admission.temporal_limitations
    _untrusted(admission)


def test_decision_verifier_absence_is_not_source_authentication():
    artifact = _decision()
    assert artifact.verification is None
    admission = _one(
        _authorization(
            invocation_id="inv-dec-1",
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc=None,
        ),
        artifact,
    )
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.DECISION_VERIFIER_NOT_SOURCE_AUTHENTICATION in admission.reason_codes
    _untrusted(admission)


def test_truncated_historical_evidence_stays_limited():
    admission = _one(
        _authorization(),
        _historical(status=SpecialistStatus.PARTIAL, limitations=("RESULT_TRUNCATED",)),
    )
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.TRUNCATED_EVIDENCE in admission.reason_codes
    assert MasterAdmissionReason.INCOMPLETE_SPECIALIST_STATUS in admission.reason_codes
    _untrusted(admission)


def test_empty_historical_result_does_not_infer_verified_zero():
    admission = _one(_authorization(), _historical(verified_claims=()))
    assert MasterAdmissionReason.VERIFIED_ZERO_NOT_INFERRED in admission.reason_codes
    assert MasterAdmissionReason.VERIFIED_ZERO_CLAIM_NOT_AUTHENTICATED not in admission.reason_codes
    _untrusted(admission)


def test_missing_geographic_provenance_is_recorded_and_not_invented():
    admission = _one(_authorization(), _historical())
    assert MasterAdmissionReason.GEOGRAPHIC_PROVENANCE_ABSENT in admission.reason_codes
    assert "region" not in MasterEvidenceAdmission.__dataclass_fields__
    _untrusted(admission)


def test_no_admission_can_acquire_production_authority():
    admission = _one(_authorization(), _historical())
    assert admission.production_authority is MasterProductionAuthority.DENIED
    assert admission.schema_version == MASTER_EVIDENCE_ADMISSION_SCHEMA_VERSION
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        MasterEvidenceAdmission(
            master_request_id="master-req-1",
            invocation_id="inv-hist-1",
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            admission_status=MasterAdmissionStatus.CONTRACT_ONLY,
            reason_codes=(MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,),
            binding_defects=(),
            temporal_limitations=(MasterTemporalLimitation.FRESHNESS_UNKNOWN,),
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        MasterEvidenceAdmission(
            master_request_id="master-req-1",
            invocation_id="inv-hist-1",
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            admission_status=MasterAdmissionStatus.CONTRACT_ONLY,
            reason_codes=(MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,),
            binding_defects=(),
            temporal_limitations=(MasterTemporalLimitation.FRESHNESS_UNKNOWN,),
            evidence_verified=True,
        )


def test_route_plan_and_historical_run_are_not_native_evidence():
    from wilvor_ai.contracts import AgentCapability
    from wilvor_ai.master_routing import (
        MasterPlanningContext,
        MasterProposedCapability,
        MasterProposedStep,
        MasterRouteTemporalIntent,
        authorize_route,
    )

    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(
            MasterProposedCapability(
                AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
                MasterRequirement.REQUIRED,
            ),
        ),
        steps=(
            MasterProposedStep(
                step_id="live-1",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
            ),
        ),
    )
    decision = authorize_route(
        proposal,
        MasterPlanningContext(
            master_request_id="master-req-1",
            correlation_id="corr-1",
            application_request_time_utc="2026-01-15T00:00:01Z",
            canonical_user_text=TEXT,
        ),
    )
    plan_admission = _one(_authorization(), decision.plan)
    assert plan_admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.ROUTE_PLAN_NOT_EVIDENCE in plan_admission.reason_codes
    run = HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.UNSUPPORTED,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=(),
        unsupported_reason=__import__(
            "wilvor_ai.specialist_contracts",
            fromlist=["UnsupportedReason"],
        ).UnsupportedReason.OUT_OF_CATALOG,
        evaluated_as_of_utc=AS_OF,
        runtime_errors=(),
        provider_turn_count=0,
        executed_tool_call_count=0,
        validation_feedback=(),
    )
    admission = _one(_authorization(), run)
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in admission.reason_codes
    assert MasterAdmissionReason.HISTORICAL_RUN_CANNOT_REPRODUCE_VERIFICATION in admission.reason_codes
    assert MasterRouteExecutionAuthority.DENIED.value == "DENIED"


def test_all_verified_live_ops_is_not_master_source_authentication():
    authorization = _authorization(
        invocation_id="inv-live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    admission = _one(authorization, _live_sources(completed_all_verified()))
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in admission.reason_codes
    _untrusted(admission)


def _live_authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "invocation_id": "inv-live-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "specialist_request_id": SYNTHETIC_REQUEST_ID,
        "correlation_id": SYNTHETIC_CORRELATION_ID,
        "requested_evaluation_time_utc": None,
    }
    values.update(overrides)
    return _authorization(**values)


def test_matching_live_ops_conversation_ids_are_admitted():
    request = sample_request()
    result = none_verified()
    assert request.conversation_id == result.conversation_id == SYNTHETIC_CONVERSATION_ID
    admission = _one(_live_authorization(), _live_sources(result, request))
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_MISMATCH not in admission.reason_codes
    assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_ABSENT not in admission.reason_codes
    _untrusted(admission)


def test_mismatched_live_ops_conversation_ids_are_rejected():
    request = replace(sample_request(), conversation_id="conv-other")
    admission = _one(_live_authorization(), _live_sources(none_verified(), request))
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_MISMATCH in admission.reason_codes
    _untrusted(admission)


def test_one_sided_live_ops_conversation_id_stays_unresolved():
    missing_on_result = _one(
        _live_authorization(),
        _live_sources(replace(none_verified(), conversation_id=None), sample_request()),
    )
    missing_on_request = _one(
        _live_authorization(),
        _live_sources(none_verified(), replace(sample_request(), conversation_id=None)),
    )
    both_absent = _one(
        _live_authorization(),
        _live_sources(
            replace(none_verified(), conversation_id=None),
            replace(sample_request(), conversation_id=None),
        ),
    )
    for admission in (missing_on_result, missing_on_request):
        assert admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
        assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_ABSENT in admission.reason_codes
        assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_MISMATCH not in admission.reason_codes
        _untrusted(admission)
    assert both_absent.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_CONVERSATION_ABSENT not in both_absent.reason_codes
    assert "conversation_id" not in MasterEvidenceAdmission.__dataclass_fields__


def test_matching_live_ops_batch_clock_is_not_freshness():
    context = trusted_context(clock=lambda: SYNTHETIC_EPOCH + 999)
    result = none_verified()
    assert context.now_epoch == result.reference_epoch
    admission = _one(_live_authorization(), _live_sources(result, context=context))
    assert admission.admission_status is MasterAdmissionStatus.CONTRACT_ONLY
    assert MasterAdmissionReason.LIVE_OPS_REFERENCE_EPOCH_MISMATCH not in admission.reason_codes
    assert MasterBindingDefect.TIME_MISMATCH not in admission.binding_defects
    assert MasterTemporalLimitation.FRESHNESS_UNKNOWN in admission.temporal_limitations
    assert MasterTemporalLimitation.LIVE_OPS_REFERENCE_TIME_NOT_FRESHNESS in admission.temporal_limitations
    assert not any(item.value == "FRESH" for item in admission.temporal_limitations)
    _untrusted(admission)


def test_contradictory_live_ops_reference_epoch_is_rejected():
    shifted = SYNTHETIC_EPOCH + 60
    result = replace(
        none_verified(),
        reference_epoch=shifted,
        reference_utc=live_ops_reference_utc(shifted),
    )
    assert trusted_context().now_epoch != result.reference_epoch
    admission = _one(_live_authorization(), _live_sources(result))
    assert admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.LIVE_OPS_REFERENCE_EPOCH_MISMATCH in admission.reason_codes
    assert MasterBindingDefect.TIME_MISMATCH not in admission.binding_defects
    assert MasterTemporalLimitation.FRESHNESS_UNKNOWN in admission.temporal_limitations
    _untrusted(admission)


def test_requested_evaluation_time_is_not_a_live_ops_reference_clock():
    authorization = _live_authorization(requested_evaluation_time_utc=SYNTHETIC_UTC)
    result = none_verified()
    assert result.reference_utc == SYNTHETIC_UTC
    admission = _one(authorization, _live_sources(result))
    assert admission.admission_status is MasterAdmissionStatus.BINDING_UNRESOLVED
    assert MasterAdmissionReason.NATIVE_EVALUATION_TIME_ABSENT in admission.reason_codes
    assert MasterTemporalLimitation.EVALUATION_TIME_ABSENT in admission.temporal_limitations
    assert MasterBindingDefect.TIME_MISMATCH not in admission.binding_defects
    assert MasterTemporalLimitation.FRESHNESS_UNKNOWN in admission.temporal_limitations
    assert MasterTemporalLimitation.LIVE_OPS_REFERENCE_TIME_NOT_FRESHNESS in admission.temporal_limitations
    _untrusted(admission)


def _direct_admission(**overrides: object) -> MasterEvidenceAdmission:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-hist-1",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "admission_status": MasterAdmissionStatus.CONTRACT_ONLY,
        "reason_codes": (MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED,),
        "binding_defects": (),
        "temporal_limitations": (MasterTemporalLimitation.FRESHNESS_UNKNOWN,),
    }
    values.update(overrides)
    return MasterEvidenceAdmission(**values)  # type: ignore[arg-type]


def test_collection_constructor_rejects_invalid_direct_invariants():
    valid = _direct_admission()
    with pytest.raises(ContractValidationError, match="invalid_master_request_id"):
        MasterEvidenceAdmissionCollection(master_request_id=" ", admissions=(valid,))
    with pytest.raises(ContractValidationError, match="empty_admission"):
        MasterEvidenceAdmissionCollection(master_request_id="master-req-1", admissions=())
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        MasterEvidenceAdmissionCollection(
            master_request_id="master-req-1",
            admissions=(valid, _direct_admission(invocation_id="inv-hist-1")),
        )
    with pytest.raises(ContractValidationError, match="master_request_mismatch"):
        MasterEvidenceAdmissionCollection(
            master_request_id="master-req-1",
            admissions=(valid, _direct_admission(master_request_id="master-req-2", invocation_id="inv-hist-2")),
        )
    with pytest.raises(ContractValidationError, match="excessive_plan_size"):
        MasterEvidenceAdmissionCollection(
            master_request_id="master-req-1",
            admissions=tuple(_direct_admission(invocation_id=f"inv-{index}") for index in range(5)),
        )
    with pytest.raises(ContractValidationError, match="decision_fanout_not_permitted"):
        MasterEvidenceAdmissionCollection(
            master_request_id="master-req-1",
            admissions=(
                _direct_admission(invocation_id="inv-dec-1", specialist=MasterSpecialist.DECISION),
                _direct_admission(invocation_id="inv-dec-2", specialist=MasterSpecialist.DECISION),
            ),
        )


def test_admission_constructor_rejects_defects_without_rejection():
    with pytest.raises(ContractValidationError, match="binding_defect_requires_rejection"):
        _direct_admission(binding_defects=(MasterBindingDefect.TIME_MISMATCH,))
    with pytest.raises(ContractValidationError, match="binding_defect_requires_rejection"):
        _direct_admission(
            admission_status=MasterAdmissionStatus.BINDING_UNRESOLVED,
            binding_defects=(MasterBindingDefect.CORRELATION_MISMATCH,),
        )
    with pytest.raises(ContractValidationError, match="request_identity_not_distinct"):
        _direct_admission(invocation_id="master-req-1")
    rejected = _direct_admission(
        admission_status=MasterAdmissionStatus.REJECTED,
        binding_defects=(MasterBindingDefect.TIME_MISMATCH,),
    )
    assert rejected.admission_status is MasterAdmissionStatus.REJECTED
    assert rejected.evidence_verified is False
    assert rejected.production_authority is MasterProductionAuthority.DENIED
