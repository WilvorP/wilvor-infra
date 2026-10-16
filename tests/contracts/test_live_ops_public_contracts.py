"""Structural tests for the Live Ops public v1 contract.

A passing test means the object matches the public schema. It does not
mean LO2 verified the claim or that a specialist invocation authorized it.
"""

from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ANSWER_INVALID_REQUEST,
    LIVE_OPS_ANSWER_NONE_VERIFIED,
    LIVE_OPS_COMPLETENESS_NOTICE,
    LIVE_OPS_ENTITY_AUTHORITY,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LiveOpsAnswerOrigin,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityHint,
    LiveOpsEntityRef,
    LiveOpsPublicAdvisoryMode,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicCurrentnessPredicate,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    LiveOpsSelectorHints,
    LiveOpsTrustedContext,
    LiveOpsUntrustedTurn,
    entity_hint_from_result,
    present_live_ops_result,
    validate_live_ops_presentation,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "tests" / "fixtures" / "live_ops_public_claim_snapshots.json"
EXAMPLES_PATH = "tests/fixtures/live_ops_public_examples.py"

RESULT_FIELDS = {
    "schema_version",
    "request_id",
    "conversation_id",
    "correlation_id",
    "execution_status",
    "verification_outcome",
    "rendering_posture",
    "answer_origin",
    "answer",
    "reference_epoch",
    "reference_utc",
    "completeness_notice",
    "verified_claims",
    "evidence_references",
    "diagnostics",
    "structural_codes",
    "contradiction_subjects",
    "mandatory_limitations",
    "discoveries",
    "verified_entity_refs",
    "failure",
}


@pytest.fixture
def examples(load_repo_module):
    return load_repo_module("live_ops_public_examples", EXAMPLES_PATH)


def test_schema_version_is_exact():
    assert LIVE_OPS_PUBLIC_SCHEMA_VERSION == "wilvor.ai.live_ops_public.v1"


def test_request_round_trip_preserves_untrusted_hints(examples):
    request = examples.sample_request()
    restored = LiveOpsRunRequest.from_dict(request.to_dict())

    assert restored == request
    assert restored.schema_version == LIVE_OPS_PUBLIC_SCHEMA_VERSION
    assert restored.selector_hints is not None
    assert restored.selector_hints.callsign == "UAL123"
    assert restored.entity_hints[0].entity_id == "AC-1"
    assert not hasattr(restored.entity_hints[0], "authority")
    assert not hasattr(restored.entity_hints[0], "evidence_tool_call_id")


def test_request_rejects_missing_text_unknown_keys_and_versions(examples):
    payload = examples.sample_request().to_dict()

    with pytest.raises(ContractValidationError, match="missing_user_text"):
        LiveOpsRunRequest.from_dict({key: value for key, value in payload.items() if key != "user_text"})
    with pytest.raises(ContractValidationError, match="unexpected_request_field"):
        LiveOpsRunRequest.from_dict({**payload, "verified_claims": []})
    with pytest.raises(ContractValidationError, match="unexpected_request_field"):
        LiveOpsRunRequest.from_dict({**payload, "raw_tool_result": {"rows": []}})
    with pytest.raises(ContractValidationError, match="unexpected_request_field"):
        LiveOpsRunRequest.from_dict({**payload, "now_epoch": examples.SYNTHETIC_EPOCH})
    with pytest.raises(ContractValidationError, match="invalid_schema_version"):
        LiveOpsRunRequest.from_dict({**payload, "schema_version": "wilvor.ai.live_ops_public.v2"})
    with pytest.raises(ContractValidationError, match="invalid_user_text"):
        LiveOpsRunRequest(user_text="   ")
    with pytest.raises(ContractValidationError, match="invalid_user_text"):
        LiveOpsRunRequest(user_text="a" * 8193)


def test_request_bounds_prior_turns_selectors_and_entity_hints():
    with pytest.raises(ContractValidationError, match="too_many_prior_turns"):
        LiveOpsRunRequest(
            user_text="Question text.",
            prior_turns=tuple(LiveOpsUntrustedTurn(role="user", text="Turn text.") for _ in range(5)),
        )
    with pytest.raises(ContractValidationError, match="invalid_prior_turn_text"):
        LiveOpsUntrustedTurn(role="user", text="a" * 2049)
    with pytest.raises(ContractValidationError, match="invalid_prior_turn_role"):
        LiveOpsUntrustedTurn(role="system", text="Not a public role.")
    with pytest.raises(ContractValidationError, match="too_many_hazard_ids"):
        LiveOpsSelectorHints(hazard_ids=tuple(f"HZ-{index:02d}" for index in range(26)))
    with pytest.raises(ContractValidationError, match="invalid_hazard_ids"):
        LiveOpsSelectorHints(hazard_ids=("a" * 257,))
    with pytest.raises(ContractValidationError, match="too_many_entity_hints"):
        LiveOpsRunRequest(
            user_text="Question text.",
            entity_hints=tuple(
                LiveOpsEntityHint(entity_kind="AIRCRAFT", entity_id=f"AC-{index}")
                for index in range(5)
            ),
        )


def test_entity_hint_rejects_replayed_authority(examples):
    ref = examples.unique_match_result().verified_entity_refs[0]
    with pytest.raises(ContractValidationError, match="unexpected_entity_hint_field"):
        LiveOpsEntityHint.from_dict(ref.to_dict())
    with pytest.raises(ContractValidationError, match="unexpected_entity_hint_field"):
        LiveOpsEntityHint.from_dict(
            {
                "entity_kind": "AIRCRAFT",
                "entity_id": "AC-1",
                "authority": LIVE_OPS_ENTITY_AUTHORITY,
                "evidence_tool_call_id": "call-find-1",
            }
        )


def test_request_collections_are_frozen_copies():
    turns = [LiveOpsUntrustedTurn(role="user", text="Untrusted turn.")]
    request = LiveOpsRunRequest(user_text="Question text.", prior_turns=turns)
    turns.append(LiveOpsUntrustedTurn(role="assistant", text="Later turn."))

    assert len(request.prior_turns) == 1
    with pytest.raises(FrozenInstanceError):
        request.user_text = "Changed after validation."  # type: ignore[misc]


def test_trusted_context_validates_clock_and_is_not_serializable(examples):
    context = examples.trusted_context()
    assert context.now_epoch == examples.SYNTHETIC_EPOCH
    assert not hasattr(context, "to_dict")
    assert not hasattr(LiveOpsTrustedContext, "from_dict")

    calls: list[str] = []

    def clock() -> int:
        calls.append("clock")
        return examples.SYNTHETIC_EPOCH

    def cancelled() -> bool:
        calls.append("cancelled")
        return False

    LiveOpsTrustedContext(
        tables=object(),
        now_epoch=examples.SYNTHETIC_EPOCH,
        correlation_id="corr-synthetic-1",
        clock=clock,
        deadline_epoch=examples.SYNTHETIC_EPOCH,
        cancelled=cancelled,
    )
    assert calls == []

    with pytest.raises(ContractValidationError, match="invalid_tables"):
        examples.trusted_context(tables=None)
    with pytest.raises(ContractValidationError, match="invalid_now_epoch"):
        examples.trusted_context(now_epoch=True)
    with pytest.raises(ContractValidationError, match="invalid_correlation_id"):
        examples.trusted_context(correlation_id="  ")
    with pytest.raises(ContractValidationError, match="invalid_max_model_turns"):
        examples.trusted_context(max_model_turns=0)
    with pytest.raises(ContractValidationError, match="invalid_max_model_turns"):
        examples.trusted_context(max_model_turns=5)
    with pytest.raises(ContractValidationError, match="invalid_max_tool_calls"):
        examples.trusted_context(max_tool_calls=7)
    with pytest.raises(ContractValidationError, match="invalid_clock"):
        examples.trusted_context(clock="not-a-clock")
    with pytest.raises(ContractValidationError, match="deadline_requires_clock"):
        examples.trusted_context(clock=None, deadline_epoch=examples.SYNTHETIC_EPOCH)
    with pytest.raises(ContractValidationError, match="deadline_before_reference_clock"):
        examples.trusted_context(deadline_epoch=examples.SYNTHETIC_EPOCH - 1)
    with pytest.raises(ContractValidationError, match="invalid_cancelled"):
        examples.trusted_context(cancelled=False)


def test_every_public_claim_matches_its_golden_snapshot(examples):
    document = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    claims = examples.snapshot_claims()

    assert document["schema_version"] == LIVE_OPS_PUBLIC_SCHEMA_VERSION
    assert set(document["claims"]) == {kind.value for kind in LiveOpsPublicClaimKind}
    assert set(claims) == set(document["claims"])
    for kind, snapshot in document["claims"].items():
        claim = claims[kind]
        assert list(claim.to_dict()) == list(snapshot)
        assert claim.to_dict() == snapshot
        assert LiveOpsPublicClaim.from_dict(snapshot) == claim
        assert "verified" not in snapshot


def test_public_claims_reject_unknown_prose_instruction_and_identity_values(examples):
    impact = examples.snapshot_claims()["CONFIRMED_IMPACT"]
    payload = impact.to_dict()
    with pytest.raises(ContractValidationError, match="unexpected_claim_field"):
        LiveOpsPublicClaim.from_dict({**payload, "projection_id": "PROJ-1"})
    with pytest.raises(ContractValidationError, match="unexpected_claim_prose"):
        LiveOpsPublicClaim.from_dict({**payload, "explanation": "Model prose."})
    with pytest.raises(ContractValidationError, match="forbidden_claim_field"):
        LiveOpsPublicClaim.from_dict({**payload, "altitude_instruction": "Climb."})
    with pytest.raises(ContractValidationError, match="invalid_aircraft_id"):
        replace(impact, aircraft_id=" AC-1")
    with pytest.raises(ContractValidationError, match="invalid_aircraft_id"):
        replace(impact, aircraft_id="")
    with pytest.raises(ContractValidationError, match="invalid_aircraft_id"):
        replace(impact, aircraft_id="a" * 257)
    with pytest.raises(ContractValidationError, match="inapplicable_claim_field"):
        replace(impact, callsign="UAL123")

    observed = examples.snapshot_claims()["AIRCRAFT_OBSERVED_STATE"]
    with pytest.raises(ContractValidationError, match="invalid_value"):
        replace(observed, observed_value=math.nan)
    with pytest.raises(ContractValidationError, match="invalid_value"):
        replace(observed, observed_value=math.inf)
    with pytest.raises(ContractValidationError, match="invalid_retained_not_current"):
        replace(observed, retained_not_current=1)

    count = examples.snapshot_claims()["SCOPED_COUNT"]
    with pytest.raises(ContractValidationError, match="invalid_value"):
        replace(count, count_value=True)


def test_currentness_airport_identity_and_closed_limitation_code():
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CURRENTNESS,
        tool_call_id="call-airport-1",
        tool_name="get_airport_operational_context",
        predicate=LiveOpsPublicCurrentnessPredicate.AIRPORT_IS_CURRENT,
        currentness_value=True,
        airport_id="KSFO",
    )
    assert LiveOpsPublicClaim.from_dict(claim.to_dict()) == claim
    with pytest.raises(ContractValidationError, match="invalid_currentness_identity"):
        replace(claim, airport_id=None)
    with pytest.raises(ContractValidationError, match="not_a_source_limitation"):
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            limitation_code=LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY,
        )


def test_absent_risk_and_recommendation_shapes():
    risk = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        risk_mode=LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="EN-1",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
    )
    assert "risk_id" not in risk.to_dict()
    with pytest.raises(ContractValidationError, match="absent_risk_forbids_risk_fields"):
        replace(risk, risk_id="RISK-1")

    recommendations = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="EN-1",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        recommendation_ids=(),
    )
    assert recommendations.to_dict()["recommendation_ids"] == []


def test_completed_all_verified_shape_is_structural_only(examples):
    result = examples.completed_all_verified()
    restored = LiveOpsRunResult.from_dict(result.to_dict())

    assert restored == result
    assert set(result.to_dict()) == RESULT_FIELDS
    assert result.execution_status is LiveOpsRunStatus.COMPLETED
    assert result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert result.rendering_posture is LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert result.failure is None
    assert result.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE
    assert result.mandatory_limitations == ("FRESHNESS_NOT_ESTABLISHED", "SYNTHETIC_FUTURE_LIMITATION")
    assert "verified" not in result.to_dict()
    serialized = json.dumps(result.to_dict())
    for forbidden in ("raw_tool_result", "tool_results", "query_executions", "credentials"):
        assert forbidden not in serialized


def test_partial_none_and_not_authorized_outcomes_stay_distinct(examples):
    partial = examples.partially_verified()
    none = examples.none_verified()
    denied = examples.ledger_not_authorized()
    tool_denied = examples.tool_failed_not_authorized()

    assert partial.verification_outcome is LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED
    assert partial.verified_claims
    assert partial.diagnostics
    assert partial.rendering_posture is LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    assert none.verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED
    assert none.verified_claims == ()
    assert none.answer == LIVE_OPS_ANSWER_NONE_VERIFIED
    assert none.rendering_posture is LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS
    assert denied.verification_outcome is LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED
    assert denied.verification_outcome is not None
    assert denied.verified_claims == ()
    assert denied.diagnostics == ()
    assert denied.rendering_posture is LiveOpsPublicRenderingPosture.NOT_RENDERABLE
    assert "RUN_EXECUTION_FAILED" not in denied.structural_codes
    assert tool_denied.structural_codes == ("RUN_EXECUTION_FAILED",)
    assert tool_denied.execution_status is LiveOpsRunStatus.TOOL_FAILED
    assert LiveOpsRunResult.from_dict(partial.to_dict()) == partial
    assert LiveOpsRunResult.from_dict(none.to_dict()) == none
    assert LiveOpsRunResult.from_dict(denied.to_dict()) == denied


@pytest.mark.parametrize(
    "builder",
    [
        "tool_execution_failed",
        "unsupported_request",
        "model_refused",
        "provider_failed",
        "provider_timeout",
        "runtime_timeout",
        "budget_exhausted",
        "tool_budget_exhausted",
        "cancelled",
        "validation_exhausted",
        "rejected_request",
    ],
)
def test_non_completed_results_hide_operational_facts(examples, builder: str):
    result = getattr(examples, builder)()
    assert result.execution_status is not LiveOpsRunStatus.COMPLETED
    assert result.verified_claims == ()
    assert result.discoveries == ()
    assert result.verified_entity_refs == ()
    assert result.failure is not None
    assert result.answer_origin is LiveOpsAnswerOrigin.FIXED_SAFE_TEXT
    if result.verification_outcome is None:
        assert result.rendering_posture is None
        assert result.structural_codes == ()
    serialized = json.dumps(result.to_dict())
    assert "raw_tool_result" not in serialized
    assert "tables" not in result.to_dict()


def test_failure_statuses_use_distinct_reasons(examples):
    assert examples.provider_failed().failure.reason.value == "TRANSPORT_OR_API"
    assert examples.provider_timeout().execution_status is LiveOpsRunStatus.PROVIDER_TIMEOUT
    assert examples.runtime_timeout().execution_status is LiveOpsRunStatus.RUNTIME_TIMEOUT
    assert examples.budget_exhausted().failure.reason.value == "MODEL_TURNS"
    assert examples.tool_budget_exhausted().failure.reason.value == "TOOL_CALLS"
    assert examples.model_refused().execution_status is LiveOpsRunStatus.REFUSED
    assert examples.provider_failed().answer == examples.provider_timeout().answer
    assert examples.runtime_timeout().answer == examples.budget_exhausted().answer
    assert examples.model_refused().answer != examples.provider_failed().answer


def test_rejected_payload_uses_trusted_context_without_entering_run(examples):
    context = examples.trusted_context()
    with pytest.raises(ContractValidationError):
        LiveOpsRunRequest.from_dict({"schema_version": LIVE_OPS_PUBLIC_SCHEMA_VERSION})
    result = LiveOpsRunResult.rejected_payload(context)
    assert result.execution_status is LiveOpsRunStatus.INVALID_REQUEST
    assert result.answer == LIVE_OPS_ANSWER_INVALID_REQUEST
    assert result.correlation_id == context.correlation_id
    assert result.reference_epoch == context.now_epoch
    assert result.verification_outcome is None
    assert result.failure is not None
    assert result.failure.reason.value == "MALFORMED_PUBLIC_REQUEST"


def test_invalid_status_and_verification_combinations_fail(examples):
    completed = examples.completed_all_verified()
    denied = examples.ledger_not_authorized()
    with pytest.raises(ContractValidationError, match="unverified_run_forbids_claims"):
        replace(
            completed,
            verification_outcome=LiveOpsPublicVerificationOutcome.NONE_VERIFIED,
            rendering_posture=LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS,
            answer_origin=LiveOpsAnswerOrigin.FIXED_SAFE_TEXT,
            answer=LIVE_OPS_ANSWER_NONE_VERIFIED,
        )
    with pytest.raises(ContractValidationError, match="verified_outcome_requires_claims"):
        replace(completed, verified_claims=())
    with pytest.raises(ContractValidationError, match="rendering_posture_mismatch"):
        replace(completed, rendering_posture=LiveOpsPublicRenderingPosture.NOT_RENDERABLE)
    with pytest.raises(ContractValidationError, match="pre_verification_failure_forbids_outcome"):
        replace(
            examples.provider_failed(),
            verification_outcome=LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
            rendering_posture=LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
            verified_claims=completed.verified_claims,
            evidence_references=completed.evidence_references,
        )
    with pytest.raises(ContractValidationError, match="discovery_requires_completed_run"):
        replace(
            examples.tool_execution_failed(),
            discoveries=examples.unique_match_result().discoveries,
        )
    with pytest.raises(ContractValidationError, match="tool_failure_must_use_tool_failed_status"):
        replace(denied, structural_codes=("RUN_EXECUTION_FAILED", "LEDGER_NOT_AUTHORIZED"))
    with pytest.raises(ContractValidationError, match="invalid_completeness_notice"):
        replace(completed, completeness_notice="omitted")
    payload = completed.to_dict()
    del payload["completeness_notice"]
    with pytest.raises(ContractValidationError, match="missing_completeness_notice"):
        LiveOpsRunResult.from_dict(payload)
    with pytest.raises(ContractValidationError, match="unexpected_result_field"):
        LiveOpsRunResult.from_dict({**completed.to_dict(), "raw_tool_result": {"rows": [1]}})


def test_result_collections_cannot_change_after_validation(examples):
    claims = [examples.impact_claim()]
    result = examples.completed_all_verified()
    claims.append(examples.snapshot_claims()["SCOPED_COUNT"])
    assert len(result.verified_claims) == 1
    with pytest.raises(FrozenInstanceError):
        result.verified_claims = ()  # type: ignore[misc]


def test_discoveries_stay_separate_and_do_not_mint_authority(examples):
    unique = examples.unique_match_result()
    ambiguous = examples.ambiguous_match_result()
    incomplete = examples.incomplete_discovery_result()
    zero = examples.zero_match_result()
    separate = examples.separate_callsign_discoveries()

    assert unique.discoveries[0].status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH
    assert unique.verified_entity_refs[0].entity_id == "AC-1"
    assert unique.verified_entity_refs[0].authority == LIVE_OPS_ENTITY_AUTHORITY
    assert unique.discoveries[0].selection_authorized is False
    assert ambiguous.verified_entity_refs == ()
    assert ambiguous.discoveries[0].identity_resolved is False
    assert len(ambiguous.discoveries[0].aircraft_ids) == 2
    assert incomplete.discoveries[0].status is LiveOpsDiscoveryStatus.INCOMPLETE
    assert incomplete.verified_entity_refs == ()
    assert zero.discoveries[0].status is LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH
    assert [item.callsign for item in separate.discoveries] == ["UAL123", "DAL456"]
    assert separate.discoveries[0].aircraft_ids == ("AC-1",)
    assert separate.discoveries[1].aircraft_ids == ("AC-2", "AC-3")
    assert len(separate.verified_entity_refs) == 1
    hint = entity_hint_from_result(unique, 0)
    assert hint.to_dict() == {"entity_kind": "AIRCRAFT", "entity_id": "AC-1", "callsign": "UAL123"}

    with pytest.raises(ContractValidationError, match="selection_not_authorized"):
        LiveOpsDiscovery(
            callsign="UAL123",
            status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
            aircraft_ids=("AC-1",),
            evidence_tool_call_id="call-find-1",
            reference_epoch=examples.SYNTHETIC_EPOCH,
            identity_resolved=True,
            selection_authorized=True,
        )
    with pytest.raises(ContractValidationError, match="entity_ref_discovery_mismatch"):
        replace(ambiguous, verified_entity_refs=unique.verified_entity_refs)


def test_contradiction_requires_both_evidence_references(examples):
    result = examples.contradiction_result()
    claim = result.verified_claims[0]
    assert claim.kind is LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION
    assert claim.cited_tool_call_ids() == ("call-hazards-a", "call-hazards-b")
    assert {item.tool_call_id for item in result.evidence_references} == set(claim.cited_tool_call_ids())
    assert LiveOpsRunResult.from_dict(result.to_dict()) == result

    with pytest.raises(ContractValidationError, match="contradiction_requires_two_calls"):
        replace(claim, tool_call_id_b="call-hazards-a")
    with pytest.raises(ContractValidationError, match="missing_evidence_reference"):
        replace(result, evidence_references=(result.evidence_references[0],))
    with pytest.raises(ContractValidationError, match="evidence_tool_name_mismatch"):
        impact = examples.completed_all_verified()
        replace(
            impact,
            evidence_references=(
                replace(impact.evidence_references[0], tool_name="search_current_hazards"),
            ),
        )


def test_presentation_rejects_rewrites_of_the_trusted_result(examples):
    trusted = examples.completed_all_verified()
    section = present_live_ops_result(trusted)
    validate_live_ops_presentation(trusted, section)
    assert section.factual_text == trusted.answer
    assert section.mandatory_limitations == trusted.mandatory_limitations
    assert section.verified_claims == trusted.verified_claims
    assert section.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE

    with pytest.raises(ContractValidationError, match="factual_text_mismatch"):
        validate_live_ops_presentation(trusted, replace(section, factual_text="Rewritten factual text."))
    with pytest.raises(ContractValidationError, match="mandatory_limitations_mismatch"):
        validate_live_ops_presentation(trusted, replace(section, mandatory_limitations=()))

    partial = examples.partially_verified()
    promoted = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-count-rejected",
        tool_name="search_current_impacts",
        metric=LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT,
        count_mode=LiveOpsPublicCountMode.EXACT_RETURNED_SET,
        count_value=1,
        scope=LiveOpsPublicScope(region="California"),
    )
    partial_section = present_live_ops_result(partial)
    assert partial.diagnostics[0].claim_kind is LiveOpsPublicClaimKind.SCOPED_COUNT
    assert all(item.kind is not LiveOpsPublicClaimKind.SCOPED_COUNT for item in partial.verified_claims)
    with pytest.raises(ContractValidationError, match="verified_claims_mismatch"):
        validate_live_ops_presentation(
            partial,
            replace(partial_section, verified_claims=partial.verified_claims + (promoted,)),
        )

    rewritten_payload = trusted.to_dict()
    rewritten_payload["answer"] = "A reconstructed model answer."
    rewritten = LiveOpsRunResult.from_dict(rewritten_payload)
    with pytest.raises(ContractValidationError, match="factual_text_mismatch"):
        validate_live_ops_presentation(trusted, present_live_ops_result(rewritten))


def test_scope_round_trip_uses_json_primitives(examples):
    scope = LiveOpsPublicScope(region="California", hazard_ids=("HZ-1", "HZ-2"))
    assert scope.to_dict()["hazard_ids"] == ["HZ-1", "HZ-2"]
    assert LiveOpsPublicScope.from_dict(scope.to_dict()) == scope
    with pytest.raises(ContractValidationError, match="unsorted_hazard_ids"):
        LiveOpsPublicScope(hazard_ids=("HZ-2", "HZ-1"))
    entity = examples.unique_match_result().verified_entity_refs[0]
    assert isinstance(entity, LiveOpsEntityRef)
    assert entity.reference_utc == examples.SYNTHETIC_UTC
    assert "expires_at" not in entity.to_dict()
