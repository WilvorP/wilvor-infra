"""OFFLINE_TEST_FIXTURE builders for MA4.2 specialist corroboration.

Every object returned here is an in-memory contract example. Nothing in this
module retrieves rows from AWS, Athena, or a model provider, and nothing
grants production execution authority.

There is no specialist field that marks evidence as source-authenticated.
Do not treat a fixture that passes a deterministic verifier as a production
read. Live Ops public results are reused from the existing synthetic public
examples; constructing one does not mean LO2 ran.
"""

from __future__ import annotations

from dataclasses import replace

from wilvor_ai.contracts import (
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_claims import (
    AircraftIdentityClaim,
    ChainGapClaim,
    RiskPresentClaim,
    ToolStatusClaim,
)
from wilvor_ai.decision_contracts import DecisionChainGap, StoredRiskLevel
from wilvor_ai.decision_evidence_verifier import (
    CONTEXT_TOOL,
    RISK_TOOL,
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
from wilvor_ai.historical_evidence_verifier import LOWER_BOUND_METRIC_ID
from wilvor_ai.live_ops_public_contracts import LiveOpsEvidenceReference
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.specialist_contracts import (
    HistoricalWindowClaim,
    LowerBoundCountClaim,
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_historical.coverage_contracts import Evaluability
from tests.contracts.test_decision_evidence_verifier import (
    AS_OF as DECISION_AS_OF,
)
from tests.contracts.test_decision_evidence_verifier import (
    _absent_risk,
    _bind,
    _context,
    _current_result,
    _identity,
    _persisted_payload,
    _persisted_result,
    _risk_absent,
    _risk_evidence,
    _risk_present,
)
from tests.contracts.test_historical_verification import (
    AS_OF as HISTORICAL_AS_OF,
)
from tests.contracts.test_historical_verification import (
    CALL_A,
    WINDOW,
    _exact,
    _invalid_request,
    _run,
    _succeeded_encounters,
    _truncated_list,
    _verified_zero,
    _window,
)
from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_REQUEST_ID,
    SYNTHETIC_UTC,
    completed_all_verified,
    evidence,
    none_verified,
    partially_verified,
    sample_request,
    trusted_context,
    unique_match_result,
)

OFFLINE_TEST_FIXTURE = "OFFLINE_TEST_FIXTURE"
FRESHNESS_NOT_ESTABLISHED = "FRESHNESS_NOT_ESTABLISHED"
WRONG_WINDOW_END = "2026-09-11T12:00:00Z"
FORGED_EXACT_COUNT = 99
MISSING_TOOL_CALL_ID = "missing-call"
UNKNOWN_EVIDENCE_REF = "de-9"
OTHER_AIRCRAFT_ID = "ac-other"
OFFLINE_REQUEST_ID = "req-offline-other"
OFFLINE_CORRELATION_ID = "corr-offline-other"


def _mark_freshness_unknown(result: ToolResult) -> ToolResult:
    """OFFLINE_TEST_FIXTURE. Keep the verifier packet, but do not label it fresh."""

    marked = []
    for item in result.evidence:
        limitations = item.limitations
        if FRESHNESS_NOT_ESTABLISHED not in limitations:
            limitations = limitations + (FRESHNESS_NOT_ESTABLISHED,)
        marked.append(
            replace(
                item,
                freshness_status=FreshnessStatus.UNKNOWN,
                limitations=limitations,
            )
        )
    return replace(result, evidence=tuple(marked))


def _offline_current(evidence_payload, tool_name: str, tool_call_id: str) -> ToolResult:
    """OFFLINE_TEST_FIXTURE current Decision tool result."""

    return _mark_freshness_unknown(
        _current_result(evidence_payload, tool_name, tool_call_id)
    )


def historical_authorization(**overrides: object) -> MasterInvocationAuthorization:
    """OFFLINE_TEST_FIXTURE Master authorization for a historical artifact."""

    values: dict[str, object] = {
        "master_request_id": "master-req-offline-historical",
        "invocation_id": "inv-offline-historical",
        "specialist": MasterSpecialist.HISTORICAL_ANALYTICS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "specialist-req-offline-historical",
        "correlation_id": "corr-offline-historical",
        "application_request_time_utc": "2026-09-13T12:00:01Z",
        "requested_evaluation_time_utc": HISTORICAL_AS_OF,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def decision_authorization(**overrides: object) -> MasterInvocationAuthorization:
    """OFFLINE_TEST_FIXTURE Master authorization for a Decision artifact."""

    values: dict[str, object] = {
        "master_request_id": "master-req-offline-decision",
        "invocation_id": "inv-offline-decision",
        "specialist": MasterSpecialist.DECISION,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": "specialist-req-offline-decision",
        "correlation_id": "corr-offline-decision",
        "application_request_time_utc": "2026-09-29T00:00:01Z",
        "requested_evaluation_time_utc": DECISION_AS_OF,
        "expected_aircraft_id": "ac-1",
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def live_ops_authorization(**overrides: object) -> MasterInvocationAuthorization:
    """OFFLINE_TEST_FIXTURE Master authorization for a public Live Ops result."""

    values: dict[str, object] = {
        "master_request_id": "master-req-offline-live",
        "invocation_id": "inv-offline-live",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "specialist_request_id": SYNTHETIC_REQUEST_ID,
        "correlation_id": SYNTHETIC_CORRELATION_ID,
        "application_request_time_utc": "2025-10-15T00:00:01Z",
        "requested_evaluation_time_utc": SYNTHETIC_UTC,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def historical_exact_count_run():
    """OFFLINE_TEST_FIXTURE proposed exact count against a mapped summary."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(_exact(), _window()),
    )


def historical_forged_count_run():
    """OFFLINE_TEST_FIXTURE exact claim whose value is not the tool payload."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(_exact(value=FORGED_EXACT_COUNT), _window()),
    )


def historical_missing_tool_call_run():
    """OFFLINE_TEST_FIXTURE claim that cites no executed tool call."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(_exact(tool_call_id=MISSING_TOOL_CALL_ID),),
    )


def historical_duplicate_claims_run():
    """OFFLINE_TEST_FIXTURE two identical exact claims."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(_exact(), _exact()),
    )


def historical_contradictory_claims_run():
    """OFFLINE_TEST_FIXTURE verified zero beside a positive exact count."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(VerifiedZeroClaim(CALL_A), _exact(value=2)),
    )


def historical_wrong_window_run():
    """OFFLINE_TEST_FIXTURE window claim that does not match requested scope."""

    return _run(
        tool_results=(_succeeded_encounters(),),
        claims=(
            _exact(),
            HistoricalWindowClaim(
                tool_call_id=CALL_A,
                start_utc=WINDOW[0],
                end_utc=WRONG_WINDOW_END,
            ),
        ),
    )


def historical_non_historical_scope_run():
    """OFFLINE_TEST_FIXTURE summary tool relabeled CURRENT."""

    success = _succeeded_encounters()
    current = replace(
        success,
        temporal_scope=TemporalScope.CURRENT,
        evidence=(
            replace(success.evidence[0], temporal_scope=TemporalScope.CURRENT),
        ),
    )
    return _run(tool_results=(current,), claims=(_exact(),))


def historical_incomplete_coverage_run():
    """OFFLINE_TEST_FIXTURE SUCCESS summary whose coverage is not EVALUABLE."""

    success = _succeeded_encounters()
    completeness = success.evidence[0].completeness
    if completeness is None:
        raise RuntimeError("mapped historical evidence did not carry completeness")
    incomplete = replace(
        success,
        evidence=(
            replace(
                success.evidence[0],
                completeness=replace(
                    completeness,
                    status=Evaluability.NOT_YET_EVALUABLE.value,
                ),
            ),
        ),
    )
    return _run(tool_results=(incomplete,), claims=(_exact(),))


def historical_verified_zero_run():
    """OFFLINE_TEST_FIXTURE certified historical zero."""

    return _run(
        tool_results=(_verified_zero(),),
        claims=(VerifiedZeroClaim(CALL_A),),
    )


def historical_verified_zero_without_evaluable_coverage_run():
    """OFFLINE_TEST_FIXTURE zero-shaped tool whose coverage is not EVALUABLE."""

    tool = _verified_zero()
    completeness = tool.evidence[0].completeness
    if completeness is None:
        raise RuntimeError("mapped zero evidence did not carry completeness")
    degraded = replace(
        tool,
        evidence=(
            replace(
                tool.evidence[0],
                completeness=replace(
                    completeness,
                    status=Evaluability.GAP_OR_UNCERTAIN.value,
                ),
            ),
        ),
    )
    return _run(
        tool_results=(degraded,),
        claims=(VerifiedZeroClaim(CALL_A),),
        evaluated_as_of_utc=degraded.as_of_utc,
    )


def historical_unknown_run():
    """OFFLINE_TEST_FIXTURE invalid request. Unknown is not a verified zero."""

    return _run(
        tool_results=(_invalid_request(),),
        claims=(VerifiedZeroClaim(CALL_A),),
        evaluated_as_of_utc=None,
    )


def historical_truncated_lower_bound_run():
    """OFFLINE_TEST_FIXTURE truncated list claimed as a lower bound."""

    return _run(
        tool_results=(_truncated_list(),),
        claims=(
            LowerBoundCountClaim(
                tool_call_id=CALL_A,
                metric_id=LOWER_BOUND_METRIC_ID,
                minimum_value=6,
            ),
        ),
    )


def historical_truncated_called_exact_run():
    """OFFLINE_TEST_FIXTURE truncated list claimed as an exact count."""

    return _run(
        tool_results=(_truncated_list(),),
        claims=(_exact(metric_id="physical_record_count", value=1),),
    )


def historical_unverified_passed_result() -> SpecialistResult:
    """OFFLINE_TEST_FIXTURE SpecialistResult whose PASSED enum was not verified.

    The constructor accepts this shape. It does not re-run claim verification
    and it does not prove a source read.
    """

    return SpecialistResult(
        status=SpecialistStatus.ANSWERED,
        answer="OFFLINE_TEST_FIXTURE historical answer with no tool evidence.",
        temporal_scope=TemporalScope.HISTORICAL,
        evaluated_as_of_utc=HISTORICAL_AS_OF,
        tool_results=(),
        verified_claims=(VerifiedZeroClaim(CALL_A),),
        limitations=(),
        unsupported_reason=None,
        verifier_outcome=VerifierOutcome.PASSED,
    )


def historical_zero_claim_without_tools():
    """OFFLINE_TEST_FIXTURE same zero claim with no tool results to verify."""

    return _run(
        tool_results=(),
        claims=(VerifiedZeroClaim(CALL_A),),
    )


def decision_valid_identity_case():
    """OFFLINE_TEST_FIXTURE identity claim bound to an in-memory context tool."""

    raw = _offline_current(_context(), CONTEXT_TOOL, "call-offline-identity")
    return (_identity(),), (_bind("de-1", raw),)


def decision_absent_risk_case():
    """OFFLINE_TEST_FIXTURE recorded risk absence. The level is not LOW."""

    evidence_payload = _risk_evidence(
        risk=_absent_risk("enc-1"),
        chain_gaps=(DecisionChainGap.RISK_ABSENT,),
    )
    raw = _offline_current(evidence_payload, RISK_TOOL, "call-offline-absent")
    return (_risk_absent(),), (_bind("de-1", raw),), evidence_payload


def decision_absent_called_low_case():
    """OFFLINE_TEST_FIXTURE LOW claim against recorded absence."""

    _claims, bindings, evidence_payload = decision_absent_risk_case()
    claim = _risk_present(level=StoredRiskLevel.LOW, score=12)
    return (claim,), bindings, evidence_payload


def decision_present_risk_case():
    """OFFLINE_TEST_FIXTURE recorded present risk on an in-memory risk tool."""

    evidence_payload = _risk_evidence()
    raw = _offline_current(evidence_payload, RISK_TOOL, "call-offline-present")
    return (_risk_present(),), (_bind("de-1", raw),), evidence_payload


def decision_conflicting_risk_case():
    """OFFLINE_TEST_FIXTURE present and absent claims for one binding."""

    raw = _offline_current(_context(), CONTEXT_TOOL, "call-offline-conflict")
    return (_risk_present(), _risk_absent()), (_bind("de-1", raw),)


def decision_unknown_evidence_ref_case():
    """OFFLINE_TEST_FIXTURE claim whose evidence ref has no binding."""

    _claims, bindings = decision_valid_identity_case()
    return (_identity(UNKNOWN_EVIDENCE_REF),), bindings


def decision_wrong_aircraft_case():
    """OFFLINE_TEST_FIXTURE identity claim for an aircraft the tool did not carry."""

    _claims, bindings = decision_valid_identity_case()
    return (_identity(aircraft_id=OTHER_AIRCRAFT_ID),), bindings


def decision_incomplete_chain_case():
    """OFFLINE_TEST_FIXTURE blocking incomplete-chain gap on current context."""

    evidence_payload = _context(
        chain_gaps=(DecisionChainGap.INCOMPLETE_CURRENT_CHAIN,),
    )
    raw = _offline_current(evidence_payload, CONTEXT_TOOL, "call-offline-chain")
    claim = ChainGapClaim(
        "de-1",
        TemporalScope.CURRENT,
        DecisionChainGap.INCOMPLETE_CURRENT_CHAIN,
    )
    return (claim,), (_bind("de-1", raw),), raw


def decision_distinct_scope_case():
    """OFFLINE_TEST_FIXTURE current and persisted tool-status claims."""

    current = _offline_current(_context(), CONTEXT_TOOL, "call-offline-current")
    persisted = _mark_freshness_unknown(
        _persisted_result(_persisted_payload(), "call-offline-persisted")
    )
    claims = (
        ToolStatusClaim("de-1", TemporalScope.CURRENT, ToolResultStatus.SUCCESS),
        ToolStatusClaim("de-2", TemporalScope.PERSISTED, persisted.status),
    )
    bindings = (_bind("de-1", current), _bind("de-2", persisted))
    return claims, bindings


def decision_scope_rejected_risk_claim() -> RiskPresentClaim:
    """OFFLINE_TEST_FIXTURE helper. Current risk claims cannot take persisted scope.

    Construction raises. This function exists so the test can show the
    constructor boundary; it does not return a persisted risk claim.
    """

    return RiskPresentClaim(
        evidence_ref="de-1",
        evidence_scope=TemporalScope.PERSISTED,
        risk_id="risk-1",
        risk_level=StoredRiskLevel.HIGH,
        risk_score=80,
        encounter_id="enc-1",
    )


def _decision_audit(raw: ToolResult, ref: str, aircraft_id: str) -> DecisionToolInvocationAudit:
    arguments = {"aircraft_id": aircraft_id}
    return DecisionToolInvocationAudit(
        tool_name=raw.tool_name,
        tool_call_id=raw.tool_call_id,
        evidence_ref=ref,
        requested_arguments=arguments,
        executed_arguments=arguments,
        canonical_key=f"{raw.tool_name}:{raw.tool_call_id}",
        dispatch_origin=DecisionDispatchOrigin.MODEL_REQUEST,
    )


def decision_offline_consistent_run(
    *,
    aircraft_id: str = "ac-1",
    tool_call_id: str = "call-offline-run",
) -> DecisionSpecialistRunResult:
    """OFFLINE_TEST_FIXTURE Decision run that is internally consistent only.

    Verification is recomputed from the in-memory tool result. Passing that
    check does not prove the tool result was retrieved from AWS.
    """

    evidence_payload = _context(aircraft_id=aircraft_id)
    raw = _offline_current(evidence_payload, CONTEXT_TOOL, tool_call_id)
    claims = (AircraftIdentityClaim("de-1", TemporalScope.CURRENT, aircraft_id),)
    bindings = (_bind("de-1", raw),)
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    return DecisionSpecialistRunResult(
        status=DecisionSpecialistRunStatus.COMPLETED,
        mode=DecisionTargetMode.AIRCRAFT,
        aircraft_id=aircraft_id,
        recommendation_id=None,
        tool_results=(raw,),
        evidence_snapshots=(
            DecisionEvidenceSnapshot("de-1", project_decision_tool_result(raw)),
        ),
        evidence_bindings=bindings,
        invocations=(_decision_audit(raw, "de-1", aircraft_id),),
        proposed_claims=claims,
        verification=verification,
        render=rendered,
        unsupported_reason=None,
        runtime_errors=(),
        collection_partial_reason=None,
        validation_feedback=(),
        provider_turn_count=1,
        executed_tool_call_count=1,
        executed_persisted_call_count=0,
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )


def live_ops_request():
    """OFFLINE_TEST_FIXTURE public Live Ops request."""

    return sample_request()


def live_ops_context():
    """OFFLINE_TEST_FIXTURE trusted Live Ops context. The clock is fixed."""

    return trusted_context()


def live_ops_all_verified_result():
    """OFFLINE_TEST_FIXTURE structurally valid ALL_VERIFIED public result."""

    return completed_all_verified()


def live_ops_partially_verified_result():
    """OFFLINE_TEST_FIXTURE structurally valid PARTIALLY_VERIFIED public result."""

    return partially_verified()


def live_ops_none_verified_result():
    """OFFLINE_TEST_FIXTURE structurally valid NONE_VERIFIED public result."""

    return none_verified()


def live_ops_request_mismatch_result():
    """OFFLINE_TEST_FIXTURE public result whose request id differs."""

    return completed_all_verified(request_id=OFFLINE_REQUEST_ID)


def live_ops_correlation_mismatch_result():
    """OFFLINE_TEST_FIXTURE public result whose correlation id differs."""

    return completed_all_verified(correlation_id=OFFLINE_CORRELATION_ID)


def live_ops_public_evidence_reference() -> LiveOpsEvidenceReference:
    """OFFLINE_TEST_FIXTURE public evidence reference without a ToolResult."""

    return evidence(
        "call-impacts-1",
        "search_current_impacts",
        FRESHNESS_NOT_ESTABLISHED,
    )


def live_ops_unique_callsign_result():
    """OFFLINE_TEST_FIXTURE unique callsign discovery. Selection stays unauthorized."""

    return unique_match_result()
