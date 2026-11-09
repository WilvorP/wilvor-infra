"""Offline guardrails for future MA4.3 comparison.

These tests call existing contracts. They do not compare specialist facts,
and they do not treat the prerequisite catalog as a verdict engine.

Known gaps pinned by this module, because no current restriction closes them:

- A hazard-region intersection claim and an exact-intersection flag can be
  structurally valid without geometry, a dataset version, or airspace scope.
- A Live Ops contradiction subject for hazard source version does not store
  the two source versions.
- A Live Ops network metric name can be paired with subset count mode.
- A Live Ops recommendation set has no advisory-authority field.
- A Live Ops present risk level is a free token, not Decision ``StoredRiskLevel``.
- ``SpecialistResult.temporal_scope`` is not forced to HISTORICAL by the
  envelope. Routing and the historical verifier enforce that separately.
- A hand-built invocation record can store the same UTC string in more than
  one clock field. Classifiers do not copy reference time into source
  observation time, and they do not reject coincident strings.
"""

from __future__ import annotations

import inspect
from dataclasses import fields, replace

import pytest

from wilvor_ai.contracts import (
    PROHIBITED_V1_CAPABILITIES,
    TOOL_RESULT_SCHEMA_VERSION,
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    SourceCompleteness,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_claims import (
    AircraftIdentityClaim,
    CurrentPersistedLinkClaim,
    PersistedEvaluationClaim,
    RecommendationActionClaim,
    RiskAbsentClaim,
)
from wilvor_ai.decision_contracts import (
    DecisionAdvisoryAuthority,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionRecommendationEvidence,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    PersistedEvaluationScope,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
    validate_decision_tool_result,
)
from wilvor_ai.historical_evidence_verifier import (
    ALL_EXACT_METRICS,
    ClaimRejectionCode,
    verify_historical_specialist_run,
)
from wilvor_historical.query_contracts import HAZARD_VERSION_WINDOW_LIMITATION
from wilvor_ai.live_ops_mapping import (
    AIRCRAFT_FIELDS,
    SOURCE_REGIONS,
    _region_source_record,
    extract_aircraft,
    map_region_identity,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_COMPLETENESS_NOTICE,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicContradictionSubject,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicFlag,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicObservedField,
    LiveOpsPublicRecordedFlag,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunResult,
    LiveOpsSelectorHints,
    live_ops_reference_utc,
)
from wilvor_ai.master_contracts import (
    MasterBindingDefect,
    MasterCondition,
    MasterDisposition,
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
    MasterVerification,
    classify_historical_result,
    classify_historical_run,
    classify_live_ops_result,
    observe_callsign,
)
from wilvor_ai.master_orchestration import (
    SyntheticOutcome,
    SyntheticProductionAuthority,
    SyntheticWorkerResult,
    run_synthetic_master_workflow,
)
from wilvor_ai.master_routing import (
    MasterHintTrust,
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteGate,
    MasterRouteHints,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.persisted_airport_contracts import validate_persisted_airport_tool_result
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalWindowClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistResult,
    SpecialistStatus,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)
from wilvor_ai.tool_result_projection import ToolResultProjectionError, project_tool_result
from tests.contracts.test_historical_verification import (
    CALL_A,
    _exact,
    _hazard_versions,
    _run,
    _succeeded_encounters,
    _truncated_list,
    _verified_zero,
)
from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_EPOCH,
    SYNTHETIC_REQUEST_ID,
    SYNTHETIC_UTC,
    completed_all_verified,
    none_verified,
    sample_request,
    trusted_context,
)
from tests.fixtures.ma4_comparability_cases import (
    COMPARABILITY_CASES,
    FORBIDDEN_COMPARISON_OUTCOMES,
    GEOGRAPHIC_COMPARISON_IDS,
    REQUIRED_COMPARISON_CATEGORIES,
    ComparabilityEligibility,
    case_by_id,
)


TEXT = "Which aircraft are currently affected by hazards, and what recorded risks apply?"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
HISTORY = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
SAME_UTC = "2026-01-15T00:00:00Z"
OTHER_UTC = "2026-01-16T00:00:00Z"
REGION_IDENTITY_KEYS = frozenset(
    {
        "resolved",
        "query",
        "region_type",
        "code",
        "name",
        "dataset_product",
        "dataset_vintage",
        "dataset_sha256",
        "target_crs",
        "spatial_supported",
        "geometry_type",
    }
)


def _context(**overrides: object) -> MasterPlanningContext:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": "2026-01-15T00:00:01Z",
        "canonical_user_text": TEXT,
    }
    values.update(overrides)
    return MasterPlanningContext(**values)  # type: ignore[arg-type]


def _work(*capabilities: AgentCapability) -> tuple[MasterProposedCapability, ...]:
    return tuple(
        MasterProposedCapability(item, MasterRequirement.REQUIRED) for item in capabilities
    )


def _step(
    step_id: str,
    specialist: MasterSpecialist,
    purpose: MasterPurpose,
    capability: AgentCapability,
    **overrides: object,
) -> MasterProposedStep:
    values: dict[str, object] = {
        "step_id": step_id,
        "specialist": specialist,
        "purpose": purpose,
        "requirement": MasterRequirement.REQUIRED,
        "capability": capability,
    }
    values.update(overrides)
    return MasterProposedStep(**values)  # type: ignore[arg-type]


def _live(step_id: str = "live-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    return _step(
        step_id,
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.ANSWER,
        LIVE,
        **values,
    )


def _history(step_id: str = "hist-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "temporal_intent": MasterRouteTemporalIntent.HISTORICAL,
        "historical_start_utc": "2026-01-01T00:00:00Z",
        "historical_end_utc": "2026-01-02T00:00:00Z",
    }
    values.update(overrides)
    return _step(
        step_id,
        MasterSpecialist.HISTORICAL_ANALYTICS,
        MasterPurpose.ANSWER,
        HISTORY,
        **values,
    )


def _decision(step_id: str = "dec-1", **overrides: object) -> MasterProposedStep:
    values: dict[str, object] = {
        "capability": RISK,
        "temporal_intent": MasterRouteTemporalIntent.CURRENT,
    }
    values.update(overrides)
    capability = values.pop("capability")
    return _step(
        step_id,
        MasterSpecialist.DECISION,
        MasterPurpose.ANSWER,
        capability,  # type: ignore[arg-type]
        **values,
    )


def _proposal(
    capabilities: tuple[MasterProposedCapability, ...],
    steps: tuple[MasterProposedStep, ...],
) -> MasterRouteProposal:
    return MasterRouteProposal(user_text=TEXT, capabilities=capabilities, steps=steps)


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
        "requested_evaluation_time_utc": SAME_UTC,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _historical_result(**overrides: object) -> SpecialistResult:
    values: dict[str, object] = {
        "status": SpecialistStatus.ANSWERED,
        "answer": "No matching historical records were found in the requested interval.",
        "temporal_scope": TemporalScope.HISTORICAL,
        "evaluated_as_of_utc": SAME_UTC,
        "tool_results": (),
        "verified_claims": (VerifiedZeroClaim("call-1"),),
        "limitations": (),
        "unsupported_reason": None,
        "verifier_outcome": VerifierOutcome.PASSED,
    }
    values.update(overrides)
    return SpecialistResult(**values)  # type: ignore[arg-type]


def _evidence(scope: TemporalScope, tool_call_id: str = "tool-1") -> Evidence:
    return Evidence(
        source="wilvor.comparability.guardrail",
        source_records=(SourceRecord("record-1", "sv-1", SAME_UTC),),
        query_timestamp_utc=SAME_UTC,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.UNKNOWN,
        limitations=("freshness_unknown",),
        tool_call_id=tool_call_id,
        temporal_scope=scope,
        completeness=SourceCompleteness(status="EVALUABLE", evaluated_as_of_utc=SAME_UTC),
    )


def _tool(
    scope: TemporalScope,
    status: ToolResultStatus = ToolResultStatus.UNKNOWN,
    **overrides: object,
) -> ToolResult:
    values: dict[str, object] = {
        "tool_name": "summarize_historical_encounters",
        "tool_call_id": "tool-1",
        "status": status,
        "temporal_scope": scope,
        "data": {"distinct_encounter_count": 2},
        "evidence": (_evidence(scope),),
        "as_of_utc": SAME_UTC,
        "limitations": ("guardrail",),
    }
    values.update(overrides)
    return ToolResult(**values)  # type: ignore[arg-type]


def _region(vintage: str | None, digest: str | None, geometry_type: str | None):
    class _Region:
        region_type = "us_state"
        code = "06"
        name = "California"
        dataset_product = "census-cartographic"
        dataset_vintage = vintage
        dataset_sha256 = digest
        target_crs = "EPSG:4269"
        spatial_supported = True

    region = _Region()
    region.geometry_type = geometry_type

    class _Resolution:
        resolved = True

    resolution = _Resolution()
    resolution.region = region
    return resolution


def _workers(calls: list) -> dict:
    def make(specialist: MasterSpecialist):
        def worker(assignment):
            calls.append(assignment)
            return SyntheticWorkerResult(
                master_request_id=assignment.master_request_id,
                correlation_id=assignment.correlation_id,
                invocation_id=assignment.invocation_id,
                specialist_request_id=assignment.specialist_request_id,
                specialist=assignment.specialist,
                outcome=SyntheticOutcome.SYNTHETIC_COMPLETED,
                note="synthetic",
            )

        return worker

    return {
        MasterSpecialist.LIVE_OPS: make(MasterSpecialist.LIVE_OPS),
        MasterSpecialist.HISTORICAL_ANALYTICS: make(MasterSpecialist.HISTORICAL_ANALYTICS),
        MasterSpecialist.DECISION: make(MasterSpecialist.DECISION),
    }


def test_catalog_names_real_specialists_and_blocks_production_verdicts():
    assert len(COMPARABILITY_CASES) >= 20
    assert [item.comparison_category for item in COMPARABILITY_CASES[:20]] == list(
        REQUIRED_COMPARISON_CATEGORIES
    )
    identifiers = {item.comparison_id for item in COMPARABILITY_CASES}
    assert set(GEOGRAPHIC_COMPARISON_IDS) <= identifiers
    known = {item.value for item in MasterSpecialist} | {
        "USER_REQUEST",
        "CENSUS_REGION_DATASET",
        "OPERATIONAL_AIRSPACE",
    }
    allowed = {item.value for item in ComparabilityEligibility}
    for case in COMPARABILITY_CASES:
        assert case.left_specialist in known
        assert case.right_specialist in known
        assert case.expected_eligibility.value in allowed
        assert case.expected_eligibility.value not in FORBIDDEN_COMPARISON_OUTCOMES
        assert case.missing_prerequisites
        assert case_by_id(case.comparison_id) is case
    assert ComparabilityEligibility.CONTRACT_CHECKABLE_ONLY.value not in {
        "VERIFIED",
        "COMPATIBLE",
        "CONFLICT",
    }


def test_one_master_request_keeps_distinct_specialist_invocation_ids():
    decision = authorize_route(
        _proposal(_work(LIVE, HISTORY), (_live(), _history())),
        _context(),
    )

    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    plan = decision.plan
    assert plan is not None
    invocations = [step.invocation for step in plan.steps]
    request_ids = {item.master_request_id for item in invocations}
    invocation_ids = [item.invocation_id for item in invocations]
    specialist_ids = [item.specialist_request_id for item in invocations]

    assert request_ids == {"master-req-1"}
    assert len(invocation_ids) == len(set(invocation_ids)) == 2
    assert len(specialist_ids) == len(set(specialist_ids)) == 2
    assert set(invocation_ids).isdisjoint(specialist_ids)
    assert {item.specialist for item in invocations} == {
        MasterSpecialist.LIVE_OPS,
        MasterSpecialist.HISTORICAL_ANALYTICS,
    }
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_duplicate_invocation_ids_are_rejected():
    decision = authorize_route(
        _proposal(_work(LIVE, HISTORY), (_live(), _history())),
        _context(),
    )
    assert decision.plan is not None
    payload = decision.plan.to_dict()
    payload["steps"][1]["invocation"]["invocation_id"] = payload["steps"][0]["invocation"][
        "invocation_id"
    ]

    with pytest.raises(ContractValidationError, match="duplicate_invocation_id"):
        type(decision.plan).from_dict(payload)


def test_callsign_discovery_does_not_authorize_a_decision_target():
    with pytest.raises(ContractValidationError, match="selection_not_authorized"):
        LiveOpsDiscovery(
            callsign="UAL123",
            status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
            aircraft_ids=("ac-1",),
            evidence_tool_call_id="call-cs",
            reference_epoch=SYNTHETIC_EPOCH,
            identity_resolved=True,
            selection_authorized=True,
        )
    discovery = LiveOpsDiscovery(
        callsign="UAL123",
        status=LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        aircraft_ids=("ac-1",),
        evidence_tool_call_id="call-cs",
        reference_epoch=SYNTHETIC_EPOCH,
        identity_resolved=True,
    )
    observation = observe_callsign(discovery)
    observation_fields = {item.name for item in fields(type(observation))}
    assert observation.aircraft_ids == ("ac-1",)
    assert observation.identity_resolved is True
    assert "expected_aircraft_id" not in observation_fields
    assert "selection_authorized" not in observation_fields

    find = _step(
        "find-1",
        MasterSpecialist.LIVE_OPS,
        MasterPurpose.DISCOVERY,
        LIVE,
        hints=MasterRouteHints(callsign="UAL123"),
    )
    decision = authorize_route(
        _proposal(_work(LIVE, RISK), (find, _decision(depends_on=("find-1",), hints=MasterRouteHints(callsign="UAL123")))),
        _context(),
    )

    assert decision.plan is not None
    discovery_step, decision_step = decision.plan.steps
    assert discovery_step.invocation.expected_aircraft_id is None
    assert discovery_step.hint_trust is MasterHintTrust.UNTRUSTED
    assert decision_step.invocation.expected_aircraft_id is None
    assert decision_step.target_authorization_granted is False
    assert MasterRouteGate.IDENTITY_DISCOVERY_NEEDED in decision_step.gates
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED


def test_california_region_text_stays_validation_pending():
    decision = authorize_route(
        _proposal(_work(LIVE), (_live(hints=MasterRouteHints(region_text="California")),)),
        _context(),
    )

    assert decision.plan is not None
    step = decision.plan.steps[0]
    assert step.region_text == "California"
    assert step.untrusted_hints.region_text == "California"
    assert step.hint_trust is MasterHintTrust.UNTRUSTED
    assert MasterRouteGate.REGION_VALIDATION_PENDING in step.gates
    assert step.ready_to_execute is False
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert decision.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    hints = LiveOpsSelectorHints(region="California")
    assert hints.region == "California"
    assert "geometry" not in {item.name for item in fields(LiveOpsSelectorHints)}
    assert "fir" not in {item.name for item in fields(LiveOpsSelectorHints)}


def test_census_region_identity_does_not_create_operational_airspace():
    mapped = map_region_identity(_region("2024", "abc123", "Polygon"), "California")
    unresolved = map_region_identity(None, "California")

    assert set(mapped) == REGION_IDENTITY_KEYS
    assert mapped["resolved"] is True
    assert mapped["region_type"] == "us_state"
    assert mapped["geometry_type"] == "Polygon"
    assert SOURCE_REGIONS == "wilvor.regions.us_states"
    for absent in ("fir", "artcc", "faa_scope", "operational_airspace", "coordinates"):
        assert absent not in mapped
        assert absent not in unresolved
    assert unresolved["resolved"] is False
    assert unresolved["geometry_type"] is None
    assert unresolved["dataset_vintage"] is None


def test_decision_plan_stays_identity_and_currentness_gated():
    decision = authorize_route(
        _proposal(_work(RISK), (_decision(hints=MasterRouteHints(typed_aircraft_id="ac-1")),)),
        _context(target_candidate_aircraft_id="ac-1"),
    )

    assert decision.plan is not None
    step = decision.plan.steps[0]
    assert step.invocation.expected_aircraft_id == "ac-1"
    assert step.target_authorization_granted is False
    assert step.ready_to_execute is False
    assert MasterRouteGate.TRUSTED_IDENTITY_VALIDATION_PENDING in step.gates
    assert MasterRouteGate.CURRENTNESS_VALIDATION_PENDING in step.gates
    assert decision.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert TemporalScope.HYBRID not in step.temporal_scopes


def test_live_ops_public_result_is_not_a_tool_result():
    result = completed_all_verified()

    assert isinstance(result, LiveOpsRunResult)
    assert not isinstance(result, ToolResult)
    assert result.schema_version == LIVE_OPS_PUBLIC_SCHEMA_VERSION
    assert result.schema_version != TOOL_RESULT_SCHEMA_VERSION
    assert "tool_name" not in result.to_dict()
    assert "evidence" not in result.to_dict()
    with pytest.raises(TypeError, match="ToolResult"):
        project_tool_result(result)  # type: ignore[arg-type]
    with pytest.raises(ContractValidationError):
        ToolResult.from_dict(result.to_dict())


def test_live_ops_reference_time_is_not_a_source_observation():
    result = completed_all_verified()
    authorization = _authorization(
        specialist=MasterSpecialist.LIVE_OPS,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        requested_evaluation_time_utc=result.reference_utc,
    )
    record = classify_live_ops_result(
        authorization,
        sample_request(),
        trusted_context(),
        result,
    ).record

    assert inspect.getdoc(live_ops_reference_utc)
    assert "Not a freshness statement" in inspect.getdoc(live_ops_reference_utc)
    assert result.reference_utc == live_ops_reference_utc(result.reference_epoch)
    assert result.evidence_references[0].as_of_utc == result.reference_utc
    assert record.specialist_reference_time_utc == result.reference_utc
    assert record.requested_evaluation_time_utc == result.reference_utc
    assert record.source_observation_time_utc is None
    assert record.evidence_evaluation_time_utc is None
    assert "position_time_utc" in AIRCRAFT_FIELDS
    assert "reference_utc" not in AIRCRAFT_FIELDS
    position = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-pos",
        tool_name="get_aircraft_operational_context",
        aircraft_id="ac-1",
        observed_field=LiveOpsPublicObservedField.POSITION_TIME_UTC,
        observed_value=result.reference_utc,
        retained_not_current=False,
    )
    assert position.observed_value == record.specialist_reference_time_utc
    assert record.source_observation_time_utc is None


def test_historical_route_and_verifier_keep_historical_scope():
    historical = authorize_route(_proposal(_work(HISTORY), (_history(),)), _context())
    assert historical.plan is not None
    assert historical.plan.steps[0].temporal_scopes == (TemporalScope.HISTORICAL,)

    rejected = authorize_route(
        _proposal(
            _work(LIVE),
            (_live(temporal_intent=MasterRouteTemporalIntent.HISTORICAL),),
        ),
        _context(),
    )
    assert rejected.outcome is MasterRouteOutcome.UNSUPPORTED
    assert rejected.reasons == ("live_ops_cannot_answer_historical",)
    assert rejected.plan is None

    current_tool = _tool(TemporalScope.CURRENT, ToolResultStatus.SUCCESS, limitations=())
    with pytest.raises(ToolResultProjectionError, match="projection_requires_historical_scope"):
        project_tool_result(current_tool)
    verified = verify_historical_specialist_run(
        _run(
            tool_results=(current_tool,),
            claims=(ExactCountClaim("tool-1", "distinct_encounter_count", 2),),
        )
    )
    assert verified.outcome is VerifierOutcome.FAILED
    assert ClaimRejectionCode.NON_HISTORICAL_TOOL in verified.rejected_claim_codes

    envelope = _historical_result(temporal_scope=TemporalScope.CURRENT, verified_claims=())
    assert envelope.temporal_scope is TemporalScope.CURRENT


def test_decision_current_and_persisted_claims_stay_separate():
    with pytest.raises(ContractValidationError, match="invalid_evidence_scope"):
        AircraftIdentityClaim("de-1", TemporalScope.PERSISTED, "ac-1")
    with pytest.raises(ContractValidationError, match="invalid_evidence_scope"):
        PersistedEvaluationClaim(
            "de-2",
            TemporalScope.CURRENT,
            "rec-1",
            None,
            PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE,
        )
    with pytest.raises(ContractValidationError, match="evidence_refs_must_differ"):
        CurrentPersistedLinkClaim("de-1", "de-1", "rec-1")
    link = CurrentPersistedLinkClaim("de-1", "de-2", "rec-1")
    assert "temporal_scope" not in {item.name for item in fields(type(link))}
    assert "evidence_scope" not in link.to_dict()

    with pytest.raises(ContractValidationError, match="persisted_airport_temporal_scope"):
        validate_persisted_airport_tool_result(_tool(TemporalScope.CURRENT))
    with pytest.raises(ContractValidationError, match="decision_temporal_scope_must_be_current"):
        validate_decision_tool_result(_tool(TemporalScope.PERSISTED))

    decision = authorize_route(
        _proposal(
            _work(RISK),
            (_decision(needs_persisted_evidence=True),),
        ),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    assert decision.plan is not None
    scopes = decision.plan.steps[0].temporal_scopes
    assert scopes == (TemporalScope.CURRENT, TemporalScope.PERSISTED)
    assert TemporalScope.HYBRID not in scopes


def test_classification_preserves_freshness_unknown():
    historical = classify_historical_result(
        _authorization(),
        _historical_result(),
    ).record
    live = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
            specialist_request_id=SYNTHETIC_REQUEST_ID,
        ),
        sample_request(),
        trusted_context(),
        completed_all_verified(),
    ).record

    assert historical.verification is MasterVerification.PASSED
    assert MasterCondition.FRESHNESS_UNKNOWN in historical.condition_flags
    assert live.verification is MasterVerification.PASSED
    assert MasterCondition.FRESHNESS_UNKNOWN in live.condition_flags
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in live.condition_flags
    assert historical.production_authority is MasterProductionAuthority.DENIED
    assert live.production_authority is MasterProductionAuthority.DENIED
    assert FreshnessStatus.UNKNOWN is not FreshnessStatus.FRESH
    assert "FRESHNESS_NOT_ESTABLISHED" in completed_all_verified().mandatory_limitations


def test_verified_zero_is_not_an_empty_or_unknown_result():
    proved = verify_historical_specialist_run(
        _run(tool_results=(_verified_zero(),), claims=(VerifiedZeroClaim(CALL_A),))
    )
    ordinary_success = verify_historical_specialist_run(
        _run(tool_results=(_succeeded_encounters(),), claims=(VerifiedZeroClaim(CALL_A),))
    )
    unknown = verify_historical_specialist_run(
        _run(
            tool_results=(_tool(TemporalScope.HISTORICAL, ToolResultStatus.UNKNOWN),),
            claims=(VerifiedZeroClaim("tool-1"),),
        )
    )
    exact_zero = verify_historical_specialist_run(
        _run(tool_results=(_verified_zero(),), claims=(_exact(0),))
    )
    live = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
            specialist_request_id=SYNTHETIC_REQUEST_ID,
        ),
        sample_request(),
        trusted_context(),
        none_verified(),
    ).record

    assert proved.outcome is VerifierOutcome.PASSED
    assert proved.verified_claims[0].to_dict()["kind"] == "VERIFIED_ZERO"
    assert ordinary_success.rejected_claim_codes == (ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED,)
    assert unknown.rejected_claim_codes == (ClaimRejectionCode.VERIFIED_ZERO_PROOF_FAILED,)
    assert exact_zero.outcome is VerifierOutcome.FAILED
    assert ToolResultStatus.UNKNOWN.value != "VERIFIED_ZERO"
    assert live.verification is MasterVerification.NO_VERIFIED_FACTS
    assert MasterCondition.VERIFIED_ZERO not in live.condition_flags
    with pytest.raises(ContractValidationError, match="unknown_cardinality_forbids_counts"):
        MatchCardinality(exact_count=0, is_exact=None, minimum_count=None)
    query_zero = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
        tool_call_id="call-zero",
        tool_name="search_current_encounters",
        metric=LiveOpsPublicCountMetric.ENCOUNTER_RECORD_COUNT,
        scope=LiveOpsPublicScope(aircraft_id="ac-1"),
    )
    assert not isinstance(query_zero, VerifiedZeroClaim)
    assert none_verified().verification_outcome is LiveOpsPublicVerificationOutcome.NONE_VERIFIED


def test_lower_bound_count_is_not_an_exact_count():
    truncated = _truncated_list(minimum=6)
    bounded = verify_historical_specialist_run(
        _run(
            tool_results=(truncated,),
            claims=(LowerBoundCountClaim(CALL_A, "minimum_count", 6),),
        )
    )
    promoted = verify_historical_specialist_run(
        _run(tool_results=(truncated,), claims=(ExactCountClaim(CALL_A, "minimum_count", 6),))
    )
    exact_tool = _succeeded_encounters()
    demoted = verify_historical_specialist_run(
        _run(
            tool_results=(exact_tool,),
            claims=(LowerBoundCountClaim(CALL_A, "minimum_count", 2),),
        )
    )

    assert bounded.outcome is VerifierOutcome.PASSED
    assert bounded.verified_claims[0].to_dict()["kind"] == "LOWER_BOUND_COUNT"
    assert "value" not in bounded.verified_claims[0].to_dict()
    assert promoted.rejected_claim_codes == (ClaimRejectionCode.EXACT_COUNT_STATUS_UNSUPPORTED,)
    assert demoted.rejected_claim_codes == (ClaimRejectionCode.LOWER_BOUND_STATUS_UNSUPPORTED,)
    with pytest.raises(ContractValidationError, match="exact_forbids_minimum_count"):
        MatchCardinality(exact_count=6, is_exact=True, minimum_count=6)


def test_absent_risk_cannot_be_encoded_as_low():
    with pytest.raises(ContractValidationError, match="absent_risk_forbids_stored_value"):
        DecisionRiskEvidence(
            presence=RiskPresence.ABSENT,
            risk_level=StoredRiskLevel.LOW,
            risk_score=1,
            risk_id="risk-1",
        )
    absent = DecisionRiskEvidence(presence=RiskPresence.ABSENT)
    assert absent.risk_level is None
    assert absent.risk_score is None
    assert "risk_level" not in {item.name for item in fields(RiskAbsentClaim)}
    with pytest.raises(ContractValidationError, match="absent_risk_forbids_risk_fields"):
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.RISK_STATE,
            tool_call_id="call-risk",
            tool_name="search_current_encounters",
            risk_mode=LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
            encounter_id="en-1",
            aircraft_id="ac-1",
            hazard_id="hz-1",
            risk_level="LOW",
        )
    live_token = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-risk",
        tool_name="search_current_encounters",
        risk_mode=LiveOpsPublicRiskMode.PRESENT,
        encounter_id="en-1",
        aircraft_id="ac-1",
        hazard_id="hz-1",
        risk_id="risk-1",
        risk_level="LOW",
        risk_score=1,
    )
    assert live_token.risk_level == "LOW"
    assert not isinstance(live_token.risk_level, StoredRiskLevel)


def test_unestablished_decision_evidence_cannot_hold_low_risk():
    missing = DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.NOT_ESTABLISHED,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        chain_gaps=(),
        limitation_codes=(),
        risk=DecisionRiskEvidence(presence=RiskPresence.ABSENT),
    )
    low = DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id="risk-1",
        risk_level=StoredRiskLevel.LOW,
        risk_score=1,
    )

    assert missing.risk is not None
    assert missing.risk.presence is RiskPresence.ABSENT
    assert missing.risk.risk_level is None
    assert expected_decision_status(missing) is ToolResultStatus.UNKNOWN
    with pytest.raises(ContractValidationError, match="unestablished_evidence_forbids_stored_risk"):
        DecisionEvidence(
            kind=DecisionEvidenceKind.RISK_EVIDENCE,
            evaluation_state=DecisionEvaluationState.NOT_ESTABLISHED,
            aircraft_in_current_set=False,
            projection_state=DecisionReportedLinkState.MISSING,
            chain_gaps=(),
            limitation_codes=(),
            risk=low,
        )
    accepted = validate_decision_tool_result(
        ToolResult(
            tool_name="get_current_risk_evidence",
            tool_call_id="tool-risk",
            status=ToolResultStatus.UNKNOWN,
            temporal_scope=TemporalScope.CURRENT,
            data=missing.to_dict(),
            evidence=(_evidence(TemporalScope.CURRENT, "tool-risk"),),
            as_of_utc=SAME_UTC,
            limitations=("not_established",),
        )
    )
    assert accepted.risk is not None
    assert accepted.risk.risk_level is None
    with pytest.raises(ContractValidationError, match="decision_status_mismatch"):
        validate_decision_tool_result(
            ToolResult(
                tool_name="get_current_risk_evidence",
                tool_call_id="tool-risk",
                status=ToolResultStatus.SUCCESS,
                temporal_scope=TemporalScope.CURRENT,
                data=missing.to_dict(),
                evidence=(_evidence(TemporalScope.CURRENT, "tool-risk"),),
                as_of_utc=SAME_UTC,
                limitations=(),
            )
        )


def test_result_envelopes_share_no_authenticated_aircraft_join_key():
    from wilvor_ai.decision_specialist_runtime_contracts import DecisionSpecialistRunResult

    live_fields = {item.name for item in fields(LiveOpsRunResult)}
    historical_fields = {item.name for item in fields(SpecialistResult)}
    decision_fields = {item.name for item in fields(DecisionSpecialistRunResult)}
    shared = live_fields & historical_fields & decision_fields

    assert shared == {"schema_version"}
    assert "aircraft_id" not in shared
    assert "aircraft_id" not in live_fields
    assert "aircraft_id" not in historical_fields
    assert "aircraft_id" in decision_fields
    assert "aircraft_id" in {item.name for item in fields(LiveOpsPublicClaim)}
    assert "aircraft_id" in {item.name for item in fields(AircraftIdentityClaim)}
    assert "aircraft_id" not in {item.name for item in fields(RecordIdentityClaim)}
    assert "record_id" in {item.name for item in fields(RecordIdentityClaim)}
    for module_name in (
        "wilvor_ai.live_ops_public_contracts",
        "wilvor_ai.specialist_contracts",
        "wilvor_ai.decision_claims",
        "wilvor_ai.master_contracts",
    ):
        module = __import__(module_name, fromlist=["*"])
        public_names = [name.lower() for name in dir(module) if not name.startswith("_")]
        assert not any(name.startswith("compare_") or "compatib" in name for name in public_names)


def test_synthetic_workflow_denies_evidence_and_execution():
    calls: list = []
    result = run_synthetic_master_workflow(
        _context(),
        _proposal(_work(LIVE, HISTORY), (_live(), _history())),
        _workers(calls),
    )

    assert result.evidence_verified is False
    assert result.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert result.results
    assert all(item.evidence_verified is False for item in result.results)
    assert all(
        item.production_authority is SyntheticProductionAuthority.DENIED for item in result.results
    )
    assert len({item.invocation_id for item in result.results}) == len(result.results)
    assert {item.master_request_id for item in result.results} == {"master-req-1"}
    payload = result.results[0].to_dict()
    payload["evidence_verified"] = True
    with pytest.raises(ContractValidationError, match="synthetic_evidence_not_verified"):
        SyntheticWorkerResult.from_dict(payload)
    payload["evidence_verified"] = False
    payload["production_authority"] = "GRANTED"
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        SyntheticWorkerResult.from_dict(payload)


def test_historical_count_metrics_do_not_match_live_ops_or_risk():
    live_metrics = {item.value for item in LiveOpsPublicCountMetric}
    assert ALL_EXACT_METRICS.isdisjoint(live_metrics)
    assert "distinct_encounter_count" in ALL_EXACT_METRICS
    assert "ENCOUNTER_RECORD_COUNT" in live_metrics
    assert "NETWORK_ENCOUNTER_COUNT" in live_metrics

    mismatched = verify_historical_specialist_run(
        _run(
            tool_results=(_succeeded_encounters(),),
            claims=(_exact(metric_id="ENCOUNTER_RECORD_COUNT"),),
        )
    )
    hazard = _hazard_versions()
    counted = verify_historical_specialist_run(
        _run(
            tool_results=(hazard,),
            claims=(_exact(2, "distinct_hazard_version_count"),),
        )
    )

    assert mismatched.rejected_claim_codes == (ClaimRejectionCode.UNKNOWN_METRIC,)
    assert counted.outcome is VerifierOutcome.PASSED
    assert counted.verified_claims[0].to_dict()["kind"] == "EXACT_COUNT"
    assert HAZARD_VERSION_WINDOW_LIMITATION in hazard.limitations
    with pytest.raises(ContractValidationError, match="inapplicable_claim_field"):
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
            tool_call_id="call-hz",
            tool_name="search_current_hazards",
            hazard_id="HZ-1",
            source_version="SV-1",
            region_code="06",
            region_name="California",
            region_query="California",
            risk_level="LOW",
        )


def test_recommendation_action_stays_advisory_and_atc_is_refused():
    action = RecommendationActionClaim(
        "de-1",
        TemporalScope.CURRENT,
        "rec-1",
        RecommendationActionType.MONITOR,
        DecisionAdvisoryAuthority.ADVISORY_ONLY,
    )
    assert action.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    with pytest.raises(ContractValidationError, match="invalid_advisory_authority"):
        RecommendationActionClaim.from_dict(
            {
                "kind": "RECOMMENDATION_ACTION",
                "evidence_ref": "de-1",
                "evidence_scope": "CURRENT",
                "recommendation_id": "rec-1",
                "primary_action_type": "MONITOR",
                "advisory_authority": "ATC_CLEARANCE",
            }
        )
    with pytest.raises(ContractValidationError, match="forbidden_decision_field"):
        DecisionRecommendationEvidence.from_dict({"diversion_clearance": "CLEARED"})
    with pytest.raises(ContractValidationError, match="validated_alternative_forbidden"):
        DecisionRouteCapability(validated_alternative_available=True)
    assert AgentCapability.ISSUE_ATC_INSTRUCTIONS in PROHIBITED_V1_CAPABILITIES
    refused = authorize_route(
        _proposal(
            _work(RISK, AgentCapability.ISSUE_ATC_INSTRUCTIONS),
            (_decision(),),
        ),
        _context(target_candidate_aircraft_id="ac-1"),
    )
    assert refused.outcome is MasterRouteOutcome.REFUSED
    assert refused.plan is None
    assert refused.execution_authority is MasterRouteExecutionAuthority.DENIED
    recommendation_fields = {item.name for item in fields(LiveOpsPublicClaim)}
    assert "advisory_mode" in recommendation_fields
    assert "advisory_authority" not in recommendation_fields
    assert "clearance" not in recommendation_fields


def test_same_hazard_id_with_different_source_versions_is_not_merged():
    first = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        tool_call_id="call-a",
        tool_name="search_current_impacts",
        aircraft_id="ac-1",
        hazard_id="HZ-1",
        source_version="SV-1",
        encounter_id="en-1",
    )
    second = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        tool_call_id="call-b",
        tool_name="search_current_impacts",
        aircraft_id="ac-1",
        hazard_id="HZ-1",
        source_version="SV-2",
        encounter_id="en-2",
    )
    contradiction = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
        tool_call_id_a="call-a",
        tool_call_id_b="call-b",
        subject=LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION,
        hazard_id="HZ-1",
    )

    assert first.hazard_id == second.hazard_id
    assert first.source_version != second.source_version
    assert contradiction.source_version is None
    assert contradiction.to_dict()["hazard_id"] == "HZ-1"
    assert "source_version" not in contradiction.to_dict()
    with pytest.raises(ContractValidationError, match="inapplicable_claim_field"):
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
            tool_call_id_a="call-a",
            tool_call_id_b="call-b",
            subject=contradiction.subject,
            hazard_id="HZ-1",
            source_version="SV-1",
        )


def test_partial_returned_set_is_not_a_network_conclusion():
    subset = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-count",
        tool_name="search_current_encounters",
        metric=LiveOpsPublicCountMetric.NETWORK_ENCOUNTER_COUNT,
        count_mode=LiveOpsPublicCountMode.CONFIRMED_SUBSET,
        count_value=3,
        scope=LiveOpsPublicScope(region="California"),
    )
    result = completed_all_verified()
    record = classify_live_ops_result(
        _authorization(
            specialist=MasterSpecialist.LIVE_OPS,
            correlation_id=SYNTHETIC_CORRELATION_ID,
            specialist_request_id=SYNTHETIC_REQUEST_ID,
        ),
        sample_request(),
        trusted_context(),
        result,
    ).record

    assert subset.count_mode is LiveOpsPublicCountMode.CONFIRMED_SUBSET
    assert subset.count_mode is not LiveOpsPublicCountMode.EXACT_RETURNED_SET
    assert subset.scope is not None
    assert subset.scope.region == "California"
    assert result.completeness_notice == LIVE_OPS_COMPLETENESS_NOTICE
    assert "does not mean" in result.completeness_notice
    assert MasterCondition.SOURCE_COMPLETENESS_NOT_ESTABLISHED in record.condition_flags
    with pytest.raises(ContractValidationError, match="invalid_completeness_notice"):
        replace(result, completeness_notice="The network investigation is complete.")


def test_missing_geometry_does_not_become_an_intersection_proof():
    intersection = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        tool_call_id="call-hz",
        tool_name="search_current_hazards",
        hazard_id="HZ-1",
        source_version="SV-1",
        region_code="06",
        region_name="California",
        region_query="California",
    )
    flagged = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        tool_call_id="call-en",
        tool_name="search_current_encounters",
        encounter_id="en-1",
        aircraft_id="ac-1",
        hazard_id="HZ-1",
        hazard_source_version="SV-1",
        recorded_flags=(
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.EXACT_INTERSECTION_CONFIRMED, True),
        ),
    )
    unflagged = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        tool_call_id="call-en-2",
        tool_name="search_current_encounters",
        encounter_id="en-2",
        aircraft_id="ac-1",
        hazard_id="HZ-1",
        hazard_source_version="SV-1",
    )
    claim_fields = {item.name for item in fields(LiveOpsPublicClaim)}

    assert "geometry" not in claim_fields
    assert "polygon" not in claim_fields
    assert intersection.region_query == "California"
    assert flagged.recorded_flags[0].value is True
    assert unflagged.recorded_flags == ()
    assert map_region_identity(None, "California")["geometry_type"] is None
    with pytest.raises(ContractValidationError, match="not_a_source_limitation"):
        LiveOpsPublicClaim(
            kind=LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
            tool_call_id="call-lim",
            tool_name="search_current_hazards",
            limitation_code=LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY,
        )


def test_clocks_remain_distinct_when_the_utc_string_matches():
    evidence = _evidence(TemporalScope.HISTORICAL)
    tool = _tool(
        TemporalScope.HISTORICAL,
        ToolResultStatus.NOT_FOUND,
        data={"distinct_encounter_count": 0},
        limitations=(),
    )
    record = classify_historical_result(
        _authorization(requested_evaluation_time_utc=SAME_UTC),
        _historical_result(tool_results=(tool,), evaluated_as_of_utc=SAME_UTC),
    ).record
    window = HistoricalWindowClaim(CALL_A, "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z")
    mismatched = classify_historical_result(
        _authorization(requested_evaluation_time_utc=OTHER_UTC),
        _historical_result(),
    ).record

    assert evidence.query_timestamp_utc == evidence.source_records[0].event_timestamp_utc == SAME_UTC
    assert evidence.query_timestamp_utc != window.start_utc
    assert record.evidence_evaluation_time_utc == SAME_UTC
    assert record.requested_evaluation_time_utc == SAME_UTC
    assert record.application_request_time_utc == "2026-01-15T00:00:01Z"
    assert record.source_observation_time_utc is None
    assert record.specialist_reference_time_utc is None
    assert MasterBindingDefect.TIME_MISMATCH in mismatched.binding_defects
    assert mismatched.requested_evaluation_time_utc == OTHER_UTC
    assert mismatched.evidence_evaluation_time_utc == SAME_UTC
    assert mismatched.source_observation_time_utc is None


def test_unfinalized_model_claims_are_not_verified_evidence():
    run = HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.PROPOSED_CLAIMS,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=(VerifiedZeroClaim("call-1"),),
        unsupported_reason=None,
        evaluated_as_of_utc=SAME_UTC,
        runtime_errors=(),
        provider_turn_count=1,
        executed_tool_call_count=0,
        validation_feedback=(),
        terminal_kind=ModelDecisionKind.FINAL_CLAIMS,
    )
    classified = classify_historical_run(_authorization(), run)

    assert run.terminal_kind is ModelDecisionKind.FINAL_CLAIMS
    assert "verified_claims" not in {item.name for item in fields(type(run))}
    assert classified.record.disposition is MasterDisposition.UNFINALIZED
    assert classified.record.verification is MasterVerification.NOT_RUN
    assert MasterCondition.FRESHNESS_UNKNOWN in classified.record.condition_flags
    assert classified.record.production_authority is MasterProductionAuthority.DENIED
    assert classified.record.source_observation_time_utc is None


def test_missing_region_dataset_version_is_not_invented():
    missing = _region_source_record(_region(None, None, "Polygon"))
    partial = _region_source_record(_region(None, "abc123", "Polygon"))
    identity = map_region_identity(_region(None, None, "Polygon"), "California")

    assert missing is not None
    assert missing.source_version is None
    assert missing.event_timestamp_utc is None
    assert partial is not None
    assert partial.source_version == ":abc123"
    assert partial.event_timestamp_utc is None
    assert identity["dataset_vintage"] is None
    assert identity["dataset_sha256"] is None
    assert identity["resolved"] is True
    assert "coordinates" not in identity


def test_static_aircraft_observation_is_not_a_boundary_crossing():
    south = extract_aircraft(
        {
            "aircraft_id": "ac-1",
            "latitude": 41.9,
            "longitude": -124.2,
            "track_deg": 0,
            "position_time_utc": SAME_UTC,
        }
    )
    north = extract_aircraft(
        {
            "aircraft_id": "ac-1",
            "latitude": 42.1,
            "longitude": -124.2,
            "track_deg": 0,
            "position_time_utc": OTHER_UTC,
        }
    )
    assert south is not None and north is not None

    assert south["aircraft_id"] == north["aircraft_id"] == "ac-1"
    assert south["latitude"] != north["latitude"]
    assert south["position_time_utc"] != north["position_time_utc"]
    assert "intersects" not in south
    assert "boundary_crossing" not in south
    assert "flight_path" not in north
    early = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-early",
        tool_name="get_aircraft_operational_context",
        aircraft_id="ac-1",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=41.9,
        retained_not_current=False,
    )
    later = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-later",
        tool_name="get_aircraft_operational_context",
        aircraft_id="ac-1",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=42.1,
        retained_not_current=False,
    )
    assert early.aircraft_id == later.aircraft_id
    assert early.observed_value != later.observed_value
    assert early.tool_call_id != later.tool_call_id
