"""Offline classification tests for Master invocation contracts.

These tests do not call specialists, providers, or AWS. Synthetic artifacts
remain production-ineligible.
"""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import ContractValidationError, TemporalScope, ToolResult, ToolResultStatus
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import CurrentPersistedLinkClaim
from wilvor_ai.decision_evidence_verifier import (
    CONTEXT_TOOL,
    verify_decision_evidence,
)
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionDispatchOrigin,
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
    DecisionToolInvocationAudit,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_COMPLETENESS_NOTICE,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsPublicVerificationOutcome,
)
from wilvor_ai.master_contracts import (
    MASTER_INVOCATION_SCHEMA_VERSION,
    MasterBindingDefect,
    MasterBindingKind,
    MasterCallsignObservation,
    MasterCondition,
    MasterDisposition,
    MasterInvocationAuthorization,
    MasterInvocationCollection,
    MasterInvocationRecord,
    MasterProductionAuthority,
    MasterPublicSection,
    MasterPurpose,
    MasterReportedShape,
    MasterRequirement,
    MasterSpecialist,
    MasterTypedIdObservation,
    MasterVerification,
    classify_decision_result,
    classify_deserialized,
    classify_historical_result,
    classify_historical_run,
    classify_live_ops_result,
    observe_callsign,
    observe_population,
    observe_typed_aircraft_id,
    project_public,
    reserved_production_adapter,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import (
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)
from tests.contracts.test_decision_evidence_verifier import (
    _bind,
    _context,
    _current_result,
    _persisted_payload,
    _persisted_result,
)
from tests.contracts.test_decision_specialist_runtime_contracts import (
    _factual_result,
    _failed_verification,
    _result,
)
from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_EPOCH,
    SYNTHETIC_REQUEST_ID,
    SYNTHETIC_UTC,
    completed_all_verified,
    none_verified,
    partially_verified,
    sample_request,
    tool_failed_not_authorized,
    trusted_context,
)

AS_OF = "2026-01-15T00:00:00Z"
OTHER_AS_OF = "2026-01-16T00:00:00Z"


def _authorization(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-1",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "specialist-req-1",
        "correlation_id": "corr-master-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "requested_evaluation_time_utc": AS_OF,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _historical_result(**overrides: object) -> SpecialistResult:
    values: dict[str, object] = {
        "status": SpecialistStatus.ANSWERED,
        "answer": "No matching historical records were found in the requested interval.",
        "temporal_scope": TemporalScope.HISTORICAL,
        "evaluated_as_of_utc": AS_OF,
        "tool_results": (),
        "verified_claims": (VerifiedZeroClaim("call-1"),),
        "limitations": (),
        "unsupported_reason": None,
        "verifier_outcome": VerifierOutcome.PASSED,
    }
    values.update(overrides)
    return SpecialistResult(**values)  # type: ignore[arg-type]


def _run_result() -> HistoricalSpecialistRunResult:
    return HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.PROPOSED_CLAIMS,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=(VerifiedZeroClaim("call-1"),),
        unsupported_reason=None,
        evaluated_as_of_utc=AS_OF,
        runtime_errors=(),
        provider_turn_count=1,
        executed_tool_call_count=0,
        validation_feedback=(),
    )


def _audit(raw: ToolResult, ref: str, arguments: dict[str, str]) -> DecisionToolInvocationAudit:
    return DecisionToolInvocationAudit(
        tool_name=raw.tool_name,
        tool_call_id=raw.tool_call_id,
        evidence_ref=ref,
        requested_arguments=arguments,
        executed_arguments=arguments,
        canonical_key=f"{raw.tool_name}:{raw.tool_call_id}",
        dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
    )


def _mixed_scope_decision_result() -> DecisionSpecialistRunResult:
    """Verified CURRENT and PERSISTED evidence in one offline Decision artifact."""

    current = _current_result(_context(), CONTEXT_TOOL, "call-c")
    persisted = _persisted_result(
        _persisted_payload(),
        "call-p",
        limitations=("Stored persisted limitation.",),
    )
    claim = CurrentPersistedLinkClaim("de-1", "de-2", "rec-1")
    bindings = (_bind("de-1", current), _bind("de-2", persisted))
    verification = verify_decision_evidence((claim,), bindings)
    rendered = render_decision_verification(verification)
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.COMPLETED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id="ac-1",
        recommendation_id=None,
        tool_results=(current, persisted),
        evidence_snapshots=(
            DecisionEvidenceSnapshot("de-1", project_decision_tool_result(current)),
            DecisionEvidenceSnapshot("de-2", project_decision_tool_result(persisted)),
        ),
        evidence_bindings=bindings,
        invocations=(
            _audit(current, "de-1", {"aircraft_id": "ac-1"}),
            _audit(persisted, "de-2", {"recommendation_id": "rec-1"}),
        ),
        proposed_claims=(claim,),
        verification=verification,
        render=rendered,
        unsupported_reason=None,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=2,
        executed_persisted_call_count=1,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def _denied(classification) -> None:
    assert classification.record.production_authority is MasterProductionAuthority.DENIED
    assert classification.binding.production_authority is MasterProductionAuthority.DENIED
    assert classification.binding.binding_kind is MasterBindingKind.CONTRACT_TEST


def test_schema_version_is_exact():
    assert MASTER_INVOCATION_SCHEMA_VERSION == "wilvor.ai.master_invocation.v1"
    authorization = _authorization()
    assert authorization.schema_version == MASTER_INVOCATION_SCHEMA_VERSION
    record = classify_historical_result(authorization, _historical_result()).record
    assert record.schema_version == MASTER_INVOCATION_SCHEMA_VERSION


def test_valid_invocation_record_round_trip_keeps_authority_denied():
    record = classify_historical_result(_authorization(), _historical_result()).record
    restored = MasterInvocationRecord.from_dict(record.to_dict())
    assert restored.invocation_id == "inv-1"
    assert restored.production_authority is MasterProductionAuthority.DENIED
    assert restored.reported_shape is MasterReportedShape.QUALIFIES
    assert classify_deserialized(restored).binding.binding_kind is MasterBindingKind.DESERIALIZED


def test_invalid_schema_and_fields_are_rejected():
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        _authorization(schema_version="wilvor.ai.master_invocation.v0")
    with pytest.raises(ContractValidationError):
        MasterInvocationAuthorization.from_dict({"schema_version": MASTER_INVOCATION_SCHEMA_VERSION})
    payload = classify_historical_result(_authorization(), _historical_result()).record.to_dict()
    payload["schema_version"] = "nope"
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        MasterInvocationRecord.from_dict(payload)
    payload = classify_historical_result(_authorization(), _historical_result()).record.to_dict()
    payload["raw_tool_result"] = {"secret": "nope"}
    with pytest.raises(ContractValidationError, match="unexpected_record_field"):
        MasterInvocationRecord.from_dict(payload)


def test_duplicate_invocation_ids_are_rejected():
    record = classify_historical_result(_authorization(), _historical_result()).record
    collection = MasterInvocationCollection().add(record)
    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        collection.add(record)


def test_historical_run_is_unfinalized_and_result_can_qualify_without_authority():
    run = classify_historical_run(_authorization(), _run_result())
    assert run.record.disposition is MasterDisposition.UNFINALIZED
    assert run.record.verification is MasterVerification.NOT_RUN
    assert run.record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    _denied(run)
    finalized = classify_historical_result(_authorization(), _historical_result())
    assert finalized.record.verification is MasterVerification.PASSED
    assert finalized.record.disposition is MasterDisposition.EXECUTED
    _denied(finalized)


def test_historical_verified_partial_is_incomplete_and_truncated():
    result = _historical_result(
        status=SpecialistStatus.PARTIAL,
        verified_claims=(),
        limitations=("RESULT_TRUNCATED",),
        answer="The returned historical list is incomplete.",
    )
    record = classify_historical_result(_authorization(), result).record
    assert record.verification is MasterVerification.PASSED
    assert MasterCondition.INCOMPLETE_INVESTIGATION in record.condition_flags
    assert MasterCondition.TRUNCATED in record.condition_flags
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in record.condition_flags
    assert MasterCondition.VERIFIED_ZERO not in record.condition_flags
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_historical_verified_zero_keeps_evaluable_coverage_distinct():
    record = classify_historical_result(_authorization(), _historical_result()).record
    assert record.verification is MasterVerification.PASSED
    assert MasterCondition.VERIFIED_ZERO in record.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED not in record.condition_flags
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags
    assert record.evidence_evaluation_time_utc == AS_OF
    assert record.source_observation_time_utc is None
    assert record.artifact_correlation_id is None


def test_historical_verification_failure_does_not_qualify():
    result = _historical_result(
        status=SpecialistStatus.UNAVAILABLE,
        verifier_outcome=VerifierOutcome.FAILED,
        verified_claims=(),
        limitations=("verification_failed",),
        answer="Historical verification failed.",
    )
    record = classify_historical_result(_authorization(), result).record
    assert record.verification is MasterVerification.FAILED
    assert record.disposition is MasterDisposition.VERIFICATION_FAILED
    assert record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_decision_completed_with_failed_verification_is_not_verified():
    claims, verification, rendered = _failed_verification()
    result = _result(
        status=DecisionSpecialistRunStatus.COMPLETED,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        runtime_errors=(),
        executed_tool_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )
    assert result.verification.outcome is VerifierOutcome.FAILED
    record = classify_decision_result(
        _authorization(specialist=MasterSpecialist.DECISION, expected_aircraft_id="ac-1"),
        result,
    ).record
    assert record.disposition is MasterDisposition.VERIFICATION_FAILED
    assert record.verification is MasterVerification.FAILED
    assert record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert record.limitation_codes == result.verification.mandatory_limitations
    assert record.limitation_codes
    assert project_public(record).limitation_codes == record.limitation_codes
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_decision_verified_partial_keeps_incomplete_investigation():
    result = _factual_result(status=DecisionSpecialistRunStatus.PARTIAL)
    record = classify_decision_result(
        _authorization(specialist=MasterSpecialist.DECISION, expected_aircraft_id="ac-1"),
        result,
    ).record
    assert result.status is DecisionSpecialistRunStatus.COMPLETED or result.status is DecisionSpecialistRunStatus.PARTIAL
    assert record.verification is MasterVerification.PASSED
    assert record.disposition is MasterDisposition.EXECUTED
    assert MasterCondition.INCOMPLETE_INVESTIGATION in record.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in record.condition_flags
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_decision_non_factual_render_is_preserved_and_not_authorized():
    claims, verification, rendered = _failed_verification()
    result = _result(
        status=DecisionSpecialistRunStatus.COMPLETED,
        tool_results=(),
        evidence_snapshots=(),
        evidence_bindings=(),
        invocations=(),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        runtime_errors=(),
        executed_tool_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )
    record = classify_decision_result(
        _authorization(specialist=MasterSpecialist.DECISION, expected_aircraft_id="ac-1"),
        result,
    ).record
    assert result.render.outcome.value != "FACTUAL"
    assert record.answer == result.render.answer
    assert record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY


def test_decision_target_mismatch_is_unresolved_and_not_a_minted_target():
    result = _factual_result()
    record = classify_decision_result(
        _authorization(specialist=MasterSpecialist.DECISION, expected_aircraft_id="ac-other"),
        result,
    ).record
    assert MasterBindingDefect.TARGET_MISMATCH in record.binding_defects
    assert record.disposition is MasterDisposition.TARGET_UNRESOLVED
    assert record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY
    assert not hasattr(record, "trusted_target")


def test_live_ops_all_verified_is_not_fresh_or_complete_or_authorized():
    result = completed_all_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            purpose=MasterPurpose.DISCOVERY,
            correlation_id=SYNTHETIC_CORRELATION_ID,
            specialist_request_id=SYNTHETIC_REQUEST_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert record.verification is MasterVerification.PASSED
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in record.condition_flags
    assert record.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE
    assert record.specialist_reference_time_utc == SYNTHETIC_UTC
    assert record.source_observation_time_utc is None
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_live_ops_partially_verified_is_incomplete():
    result = partially_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert record.verification is MasterVerification.PASSED
    assert MasterCondition.INCOMPLETE_INVESTIGATION in record.condition_flags
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags


def test_live_ops_none_verified_is_not_a_verified_zero():
    result = none_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert record.verification is MasterVerification.NO_VERIFIED_FACTS
    assert record.disposition is MasterDisposition.EXECUTED
    assert MasterCondition.VERIFIED_ZERO not in record.condition_flags
    assert record.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY


def test_live_ops_not_authorized_stays_denied():
    result = tool_failed_not_authorized()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert record.verification is MasterVerification.FAILED
    assert record.disposition is MasterDisposition.TOOL_FAILED
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_synthetic_and_deserialized_results_cannot_restore_production_authority():
    record = classify_historical_result(_authorization(), _historical_result()).record
    payload = record.to_dict()
    payload["production_authority"] = "PRODUCTION"
    payload["eligibility"] = "ELIGIBLE"
    payload["reported_eligibility"] = "ELIGIBLE"
    restored = MasterInvocationRecord.from_dict(payload)
    assert restored.production_authority is MasterProductionAuthority.DENIED
    assert classify_deserialized(restored).binding.binding_kind is MasterBindingKind.DESERIALIZED
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        MasterInvocationRecord(
            invocation_id="inv-9",
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            purpose=MasterPurpose.ANSWER,
            requirement=MasterRequirement.REQUIRED,
            disposition=MasterDisposition.EXECUTED,
            verification=MasterVerification.PASSED,
            condition_flags=(),
            reported_shape=MasterReportedShape.QUALIFIES,
            temporal_scopes=(TemporalScope.HISTORICAL,),
            answer="Synthetic answer.",
            limitation_codes=(),
            production_authority=MasterProductionAuthority.PRODUCTION,
        )


def test_fake_specialist_cannot_mint_production_authority():
    class FakeSpecialist:
        def __init__(self) -> None:
            self.calls = 0

        def run(self, *_args: object, **_kwargs: object) -> object:
            self.calls += 1
            return {"factually_eligible": True}

    fake = FakeSpecialist()
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        reserved_production_adapter(fake)
    assert fake.calls == 0
    assert not hasattr(reserved_production_adapter, "grant")


def test_incomplete_and_unknown_freshness_coexist():
    result = partially_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert MasterCondition.INCOMPLETE_INVESTIGATION in record.condition_flags
    assert MasterCondition.FRESHNESS_UNKNOWN in record.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in record.condition_flags


def test_callsign_observations_do_not_mint_targets():
    unique = observe_callsign(
        LiveOpsDiscovery(
            callsign="UAL123",
            status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
            aircraft_ids=("AC-1",),
            evidence_tool_call_id="call-1",
            reference_epoch=SYNTHETIC_EPOCH,
            identity_resolved=True,
        ),
        ("CALLSIGN_CASE_LIMITATION",),
    )
    multiple = observe_callsign(
        LiveOpsDiscovery(
            callsign="UAL123",
            status=LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES,
            aircraft_ids=("AC-1", "AC-2"),
            evidence_tool_call_id="call-2",
            reference_epoch=SYNTHETIC_EPOCH,
            identity_resolved=False,
        ),
    )
    incomplete = observe_callsign(
        LiveOpsDiscovery(
            callsign="UAL123",
            status=LiveOpsDiscoveryStatus.INCOMPLETE,
            aircraft_ids=(),
            evidence_tool_call_id="call-3",
            reference_epoch=SYNTHETIC_EPOCH,
            identity_resolved=False,
        ),
    )
    assert isinstance(unique, MasterCallsignObservation)
    assert "CALLSIGN_CASE_LIMITATION" in unique.limitation_codes
    assert multiple.status is LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES
    assert incomplete.status is LiveOpsDiscoveryStatus.INCOMPLETE
    assert not hasattr(unique, "trusted_target")
    import wilvor_ai.master_contracts as master

    assert not hasattr(master, "authorize_acknowledged_callsign")
    assert not hasattr(master, "authorize_attested_aircraft")
    assert not hasattr(master, "authorize_persisted_recommendation")


def test_typed_id_and_population_cannot_mint_targets():
    typed = observe_typed_aircraft_id("AC-1")
    population = observe_population(("AC-1", "AC-2"), ("OPERATIONALLY_UNEVALUATED_PRESENT",))
    assert isinstance(typed, MasterTypedIdObservation)
    assert population.aircraft_ids == ("AC-1", "AC-2")
    assert not hasattr(typed, "authorize")
    assert not hasattr(population, "trusted_target")


def test_current_and_persisted_scopes_stay_distinct():
    historical = classify_historical_result(_authorization(), _historical_result()).record
    current_only = _factual_result()
    decision = classify_decision_result(
        _authorization(
            invocation_id="inv-2",
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc="2026-09-29T00:00:00Z",
        ),
        current_only,
    ).record
    mixed_result = _mixed_scope_decision_result()
    mixed = classify_decision_result(
        _authorization(
            invocation_id="inv-3",
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc=mixed_result.verification.current_as_of_utc,
        ),
        mixed_result,
    ).record
    assert historical.temporal_scopes == (TemporalScope.HISTORICAL,)
    assert current_only.verification.verified_temporal_scopes == (TemporalScope.CURRENT,)
    assert decision.temporal_scopes == (TemporalScope.CURRENT,)
    assert mixed_result.verification.verified_temporal_scopes == (
        TemporalScope.CURRENT,
        TemporalScope.PERSISTED,
    )
    assert mixed.temporal_scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED)
    assert TemporalScope.HYBRID not in mixed.temporal_scopes
    assert mixed.temporal_scopes != (TemporalScope.CURRENT,)
    assert mixed.answer == mixed_result.render.answer
    assert mixed.limitation_codes == mixed_result.verification.mandatory_limitations
    assert mixed.production_authority is MasterProductionAuthority.DENIED
    assert project_public(mixed).temporal_scopes == mixed.temporal_scopes
    assert project_public(mixed).answer == mixed_result.render.answer


def test_decision_mandatory_limitations_survive_classification_and_projection():
    result = _mixed_scope_decision_result()
    assert result.verification.outcome is VerifierOutcome.PASSED
    assert result.verification.mandatory_limitations == (
        "Stored persisted limitation.",
        "Persisted airport evaluation evidence is not asserted to be current.",
    )
    classification = classify_decision_result(
        _authorization(
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc=result.verification.current_as_of_utc,
        ),
        result,
    )
    record = classification.record
    public = project_public(record)
    _denied(classification)
    assert record.limitation_codes == result.verification.mandatory_limitations
    assert public.limitation_codes == result.verification.mandatory_limitations
    assert public.answer == result.render.answer
    assert "tool_results" not in public.to_dict()
    assert "aa-1" not in str(public.to_dict())
    assert "get_persisted_airport_candidate_evidence" not in str(public.to_dict())
    restored = MasterInvocationRecord.from_dict(record.to_dict())
    assert restored.limitation_codes == result.verification.mandatory_limitations
    assert restored.production_authority is MasterProductionAuthority.DENIED


def test_absent_decision_verification_does_not_invent_limitations():
    result = _result()
    assert result.verification is None
    record = classify_decision_result(
        _authorization(specialist=MasterSpecialist.DECISION, expected_aircraft_id="ac-1"),
        result,
    ).record
    assert record.limitation_codes == ()
    assert project_public(record).limitation_codes == ()
    assert record.production_authority is MasterProductionAuthority.DENIED


def test_rewritten_decision_limitation_projection_is_not_the_public_section():
    result = _mixed_scope_decision_result()
    record = classify_decision_result(
        _authorization(
            specialist=MasterSpecialist.DECISION,
            expected_aircraft_id="ac-1",
            requested_evaluation_time_utc=result.verification.current_as_of_utc,
        ),
        result,
    ).record
    public = project_public(record)
    canonical = public.to_dict()
    dropped = dict(canonical)
    dropped["limitation_codes"] = list(canonical["limitation_codes"][:-1])
    rewritten = dict(canonical)
    rewritten["limitation_codes"] = ["NOT_CURRENT"]
    assert dropped != canonical
    assert rewritten != canonical
    assert public.limitation_codes == result.verification.mandatory_limitations
    altered = MasterPublicSection(
        invocation_id=public.invocation_id,
        specialist=public.specialist,
        disposition=public.disposition,
        verification=public.verification,
        condition_flags=public.condition_flags,
        reported_shape=public.reported_shape,
        temporal_scopes=public.temporal_scopes,
        answer=public.answer,
        limitation_codes=public.limitation_codes[:-1],
        completeness_notice=public.completeness_notice,
        binding_defects=public.binding_defects,
        evidence_evaluation_time_utc=public.evidence_evaluation_time_utc,
        specialist_reference_time_utc=public.specialist_reference_time_utc,
        source_observation_time_utc=public.source_observation_time_utc,
        failure_code=public.failure_code,
    )
    assert altered != public
    assert altered.limitation_codes != result.verification.mandatory_limitations


def test_timestamps_stay_independent_and_reference_time_is_not_observation_time():
    historical = classify_historical_result(
        _authorization(requested_evaluation_time_utc=OTHER_AS_OF),
        _historical_result(),
    ).record
    assert MasterBindingDefect.TIME_MISMATCH in historical.binding_defects
    assert historical.requested_evaluation_time_utc == OTHER_AS_OF
    assert historical.evidence_evaluation_time_utc == AS_OF
    assert historical.application_request_time_utc == "2026-01-15T00:00:01Z"
    assert historical.source_observation_time_utc is None
    live = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        completed_all_verified(),
    ).record
    assert live.specialist_reference_time_utc == SYNTHETIC_UTC
    assert live.source_observation_time_utc is None
    assert live.evidence_evaluation_time_utc is None


def test_correlation_mismatch_is_recorded_without_inventing_historical_correlation():
    historical = classify_historical_result(_authorization(), _historical_result()).record
    assert historical.correlation_id == "corr-master-1"
    assert historical.artifact_correlation_id is None
    assert MasterBindingDefect.CORRELATION_MISMATCH not in historical.binding_defects
    mismatched = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id="corr-other",
        ),
        sample_request(),
        trusted_context(),
        completed_all_verified(),
    ).record
    assert MasterBindingDefect.CORRELATION_MISMATCH in mismatched.binding_defects
    assert mismatched.artifact_correlation_id == SYNTHETIC_CORRELATION_ID
    assert mismatched.reported_shape is MasterReportedShape.DOES_NOT_QUALIFY


def test_required_limitations_and_notice_are_copied_exactly():
    result = completed_all_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record
    assert record.limitation_codes == result.mandatory_limitations
    assert record.completeness_notice == result.completeness_notice
    assert record.answer == result.answer


def test_public_projection_omits_internal_payloads():
    tool = ToolResult(
        tool_name="summarize_historical_encounters",
        tool_call_id="call-1",
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.HISTORICAL,
        data={"secret": "raw-tool-payload"},
        evidence=(),
        as_of_utc=AS_OF,
        limitations=("unavailable",),
        correlation_id=None,
    )
    result = _historical_result(tool_results=(tool,))
    record = classify_historical_result(_authorization(), result).record
    public = project_public(record).to_dict()
    assert "tool_results" not in public
    assert "raw-tool-payload" not in str(public)
    assert "tables" not in public
    assert "secret" not in str(public)
    assert public["answer"] == result.answer
    assert public["limitation_codes"] == list(result.limitations)
    assert public["production_authority"] == "DENIED"


def test_decision_result_type_is_required():
    with pytest.raises(TypeError):
        classify_decision_result(
            _authorization(specialist=MasterSpecialist.DECISION),
            {"status": "COMPLETED"},  # type: ignore[arg-type]
        )
    assert DecisionSpecialistRunResult is not None
