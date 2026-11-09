"""Sprint 3C Agent E: evidence identity, provenance, admission, and MA5 safety.

Positive fixtures are structurally valid source-backed observations. They do
not establish production authenticity. ``MasterSourceAuthenticity`` stays
``NOT_ESTABLISHED`` and ``claims_admitted`` stays false.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.fixtures.live_ops_public_examples import (
    SYNTHETIC_CORRELATION_ID,
    SYNTHETIC_EPOCH,
    SYNTHETIC_REQUEST_ID,
    completed_all_verified,
    none_verified,
    sample_request,
    trusted_context,
)
from tests.fixtures.ma4_specialist_evidence_examples import (
    _decision_audit,
    decision_absent_called_low_case,
    decision_absent_risk_case,
)
from wilvor_ai.contracts import (
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_answer_renderer import render_decision_verification
from wilvor_ai.decision_evidence_verifier import verify_decision_evidence
from wilvor_ai.decision_model_contracts import DecisionEvidenceSnapshot
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRunResult,
    DecisionSpecialistRunStatus,
    DecisionTargetMode,
)
from wilvor_ai.decision_tool_projection import project_decision_tool_result
from wilvor_ai.live_ops_public_contracts import LiveOpsPublicVerificationOutcome
from wilvor_ai.model_contracts import ModelDecisionKind
from wilvor_ai.master_authenticated_read_receipt import (
    SOURCE_AUTHENTICATED_READ_AVAILABLE,
    ApprovedExecutorAction,
    DescriptiveReadContext,
    GeographicLimitation,
    LocalAdapterObservation,
    LocalTrustedReadHost,
    ObservedSourceRow,
    PopulationCondition,
    ReadReceiptVerificationStatus,
    VERIFIED_ZERO_ALWAYS_MISSING,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_decision_target_resolver import RiskRecordObservation
from wilvor_ai.master_evidence_admission import (
    LiveOpsAdmissionSources,
    MasterAdmissionReason,
    MasterAdmissionStatus,
    MasterEvidenceAdmissionInput,
    MasterSourceAuthenticity,
)
from wilvor_ai.master_evidence_corroboration import (
    MasterCorroborationReason,
    MasterEvidenceCorroborationInput,
)
from wilvor_ai.master_host_evidence_integration import (
    PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED,
    ExpectedObservationClocks,
    HostEvidenceLimitation,
    open_production_source_authenticator,
)
from wilvor_ai.master_parallel_contracts import (
    MasterParallelContractBundle,
    MasterResponseLimitation,
    MasterResponsePolicyState,
)
from wilvor_ai.master_read_resource_guard import ReadOperation as GuardReadOperation
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
)
from wilvor_ai.master_specialist_runtime_contract import admit_evidence
from wilvor_ai.master_trusted_read_executor import (
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadExecutionOutcome,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
)
from wilvor_ai.specialist_contracts import UnsupportedReason
from wilvor_ai.specialist_runtime_contracts import HistoricalSpecialistRunResult, SpecialistRunStatus
from wilvor_ai.sprint3c_evidence_response import (
    ABSENT_RISK_IS_LOW_RISK,
    AVIATION_EVIDENCE_ADMISSION_ENABLED,
    EMPTY_PAGE_CREATES_VERIFIED_ZERO,
    NUMERICAL_IMPACT_COUNTS_IN_MA5,
    PRODUCTION_MA4_ADMISSION_AVAILABLE,
    REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION,
    SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED,
    SPRINT3C_SCOPED_READ_ADAPTER_PREFIX,
    Sprint3cConsumedAttemptBinding,
    Sprint3cEvidenceResponseInput,
    Sprint3cJoinDefect,
    Sprint3cJoinStatus,
    Sprint3cRejectedClaim,
    join_sprint3c_evidence_response,
    open_sprint3c_production_evidence_admission,
    production_admission_gaps,
    refuse_sprint3c_runtime_evidence_admission,
)


HISTORY = MasterSpecialist.HISTORICAL_ANALYTICS
LIVE = MasterSpecialist.LIVE_OPS
DECISION = MasterSpecialist.DECISION
HISTORICAL_CAP = AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS
OPERATIONAL = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
RISK = AgentCapability.EXPLAIN_EXISTING_RISK
OBSERVED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
START = "2026-01-08T00:00:00Z"
END = "2026-01-15T00:00:00Z"
SOURCE_OBSERVED = "2026-01-14T12:00:00Z"
TEXT = "Which recorded hazards apply inside the authorized window?"
LIVE_TEXT = "Which aircraft are currently affected by California SIGMETs?"


class Clock:
    def __init__(self, *instants: str) -> None:
        self.instants = list(instants)

    def now_utc(self) -> str:
        if not self.instants:
            raise AssertionError("host clock exhausted")
        return self.instants.pop(0)


def _auth(**overrides: object) -> MasterInvocationAuthorization:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "invocation_id": "inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "requirement": MasterRequirement.REQUIRED,
        "correlation_id": "corr-1",
        "application_request_time_utc": OBSERVED,
    }
    values.update(overrides)
    return MasterInvocationAuthorization(**values)  # type: ignore[arg-type]


def _adapter_id(
    specialist: MasterSpecialist,
    tool_name: str,
    resource_id: str,
    operation: GuardReadOperation,
) -> str:
    return f"nonproduction:{specialist.value}:{tool_name}:{resource_id}:{operation.value}"


def _attempt(**overrides: object) -> Sprint3cConsumedAttemptBinding:
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "principal_ref": "principal-1",
        "policy_revision": "policy-rev-1",
        "owner_id": "owner-1",
        "attempt_token": "attempt-1",
        "fence_epoch": 1,
        "grant_nonce": "nonce-1",
        "idempotency_key": "idem-1",
        "payload_fingerprint": "fingerprint-1",
        "ledger_state": "STARTED",
        "resource_id": "historical.encounters",
        "operation": GuardReadOperation.QUERY,
        "adapter_identity": _adapter_id(
            HISTORY,
            "list_historical_encounters",
            "historical.encounters",
            GuardReadOperation.QUERY,
        ),
        "tool_name": "list_historical_encounters",
        "tool_call_id": "tool-call-1",
        "accepted_at_utc": OBSERVED,
        "deadline_utc": DEADLINE,
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "specialist_request_id": "spec-req-1",
        "correlation_id": "corr-1",
        "ledger_grant_nonce": "ledger-nonce-1",
        "region_scope": (),
        "distributed_exactly_once": False,
        "production_grant": False,
    }
    values.update(overrides)
    return Sprint3cConsumedAttemptBinding(**values)  # type: ignore[arg-type]


def _host(**overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "planning_session_id": "plan-1",
        "step_id": "step-1",
        "invocation_id": "inv-1",
        "specialist": HISTORY,
        "purpose": MasterPurpose.ANSWER,
        "capability": HISTORICAL_CAP,
        "tool_name": "list_historical_encounters",
        "tool_call_id": "tool-call-1",
        "resource_id": "historical.encounters",
        "operation": GuardReadOperation.QUERY,
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


def _request(host: object, **overrides: object) -> UntrustedReadRequest:
    values: dict[str, object] = {
        "master_request_id": host.master_request_id,
        "planning_session_id": host.planning_session_id,
        "step_id": host.step_id,
        "invocation_id": host.invocation_id,
        "specialist": host.specialist,
        "purpose": host.purpose,
        "capability": host.capability,
        "tool_name": host.tool_name,
        "tool_call_id": host.tool_call_id,
        "resource_id": host.resource_id,
        "operation": host.operation,
        "requested_arguments": (
            ("start_utc", START),
            ("end_utc", END),
            ("aircraft_id", "AC1"),
            ("hazard_id", "HZ-1"),
        ),
        "correlation_id": host.correlation_id,
    }
    values.update(overrides)
    return UntrustedReadRequest(**values)  # type: ignore[arg-type]


def _page(**overrides: object) -> NonproductionReadPage:
    values: dict[str, object] = {
        "record_ids": ("HZ-1",),
        "source_name": "historical.encounters",
        "source_version": "version-1",
        "query_id": "query-1",
        "page_index": 0,
        "has_more": False,
        "source_observed_at_utc": SOURCE_OBSERVED,
    }
    values.update(overrides)
    return NonproductionReadPage(**values)  # type: ignore[arg-type]


def _execute(host: object, page: object, **request_overrides: object):
    def read(_arguments: object, _cancellation: object) -> object:
        return page

    registry = bind_nonproduction_read_registry(
        (
            NonproductionAdapterSpec(
                specialist=host.specialist,
                tool_name=host.tool_name,
                capability=host.capability,
                resource_id=host.resource_id,
                operation=host.operation,
                read=read,
            ),
        )
    )
    executor = MasterTrustedReadExecutor(registry, bind_nonproduction_host_clock(OBSERVED))
    return executor.execute(
        _request(host, **request_overrides),
        host_invocation=host,
        cancellation=bind_nonproduction_cancellation(),
    )


def _closed(join) -> None:
    assert join.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert join.production_authority is MasterProductionAuthority.DENIED
    assert join.claims_admitted is False
    assert join.aviation_evidence_admitted is False
    assert join.verified_zero_established is False
    assert join.polygon_intersection_established is False
    assert join.impacted_aircraft_count_established is False
    assert join.low_risk_admitted is False
    assert join.production_ma4_admission is False
    assert join.authenticated_source_read_available is False
    assert bool(join) is False
    assert Sprint3cRejectedClaim.PRODUCTION_AUTHENTICATED_SOURCE in join.rejected_claims
    assert Sprint3cJoinDefect.PRODUCTION_AUTHENTICITY_UNAVAILABLE in join.defects
    if join.admission is not None:
        assert join.admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
        assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in join.admission.reason_codes
    if join.response_render is not None:
        assert join.response_render.claims_admitted is False
        assert not any(character.isdigit() for character in join.response_render.status_text)
        assert "LOW" not in join.response_render.status_text
        assert "recommend" not in join.response_render.status_text.casefold()
        assert "divert" not in join.response_render.status_text.casefold()


def _plan(*steps: MasterProposedStep, **context_overrides: object):
    values: dict[str, object] = {
        "master_request_id": "master-req-1",
        "correlation_id": "corr-1",
        "application_request_time_utc": OBSERVED,
        "canonical_user_text": TEXT,
    }
    values.update(context_overrides)
    context = MasterPlanningContext(**values)  # type: ignore[arg-type]
    capabilities = tuple(
        dict.fromkeys(MasterProposedCapability(step.capability, step.requirement) for step in steps)
    )
    decision = authorize_route(
        MasterRouteProposal(user_text=values["canonical_user_text"], capabilities=capabilities, steps=steps),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    return context, decision.plan


def _historical_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="hist-1",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=HISTORICAL_CAP,
        temporal_intent=MasterRouteTemporalIntent.HISTORICAL,
        historical_start_utc=START,
        historical_end_utc=END,
    )


def _live_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="live-1",
        specialist=LIVE,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=OPERATIONAL,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _decision_step() -> MasterProposedStep:
    return MasterProposedStep(
        step_id="dec-1",
        specialist=DECISION,
        purpose=MasterPurpose.ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=RISK,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )


def _aligned_live_sources(authorization: MasterInvocationAuthorization, result=None):
    request = replace(sample_request(), request_id=authorization.specialist_request_id, conversation_id=None)
    context = replace(
        trusted_context(),
        correlation_id=authorization.correlation_id,
        now_epoch=SYNTHETIC_EPOCH,
    )
    base = none_verified() if result is None else result
    aligned = replace(
        base,
        request_id=authorization.specialist_request_id,
        correlation_id=authorization.correlation_id,
        conversation_id=None,
        reference_epoch=SYNTHETIC_EPOCH,
    )
    return LiveOpsAdmissionSources(request, context, aligned)


def _native_live_ops(authorization: MasterInvocationAuthorization, sources: LiveOpsAdmissionSources):
    return SimpleNamespace(
        invocation_id=authorization.invocation_id,
        step_id="live-1",
        specialist_request_id=authorization.specialist_request_id,
        correlation_id=authorization.correlation_id,
        application_request_time_utc=authorization.application_request_time_utc,
        native_result=sources.result,
        bridge=SimpleNamespace(request=sources.request, invocation_id=authorization.invocation_id),
        regional_string_matches=(),
        geospatial_coverage_established=False,
        regional_string_match_is_sigmet_coverage=False,
        census_region_resolution_is_sigmet_coverage=False,
        aircraft_impact_count=None,
        verified_zero=False,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        authenticated_source_evidence=False,
        verification_outcome=sources.result.verification_outcome,
        native_query_result_zero_claims=(),
    )


def _pack(claims, bindings, **overrides: object) -> DecisionSpecialistRunResult:
    verification = verify_decision_evidence(claims, bindings)
    rendered = render_decision_verification(verification)
    tools = tuple(item.raw_tool_result for item in bindings)
    persisted = sum(
        1
        for item in tools
        if item.tool_name == "get_persisted_airport_candidate_evidence"
    )
    values: dict[str, object] = {
        "status": DecisionSpecialistRunStatus.COMPLETED,
        "mode": DecisionTargetMode.AIRCRAFT,
        "aircraft_id": "ac-1",
        "recommendation_id": None,
        "tool_results": tools,
        "evidence_snapshots": tuple(
            DecisionEvidenceSnapshot(item.evidence_ref, project_decision_tool_result(item.raw_tool_result))
            for item in bindings
        ),
        "evidence_bindings": bindings,
        "invocations": tuple(
            _decision_audit(item.raw_tool_result, item.evidence_ref, "ac-1") for item in bindings
        ),
        "proposed_claims": claims,
        "verification": verification,
        "render": rendered,
        "unsupported_reason": None,
        "runtime_errors": (),
        "collection_partial_reason": None,
        "validation_feedback": (),
        "provider_turn_count": 1,
        "executed_tool_call_count": len(tools),
        "executed_persisted_call_count": persisted,
        "terminal_kind": ModelDecisionKind.FINAL_CLAIMS,
    }
    values.update(overrides)
    return DecisionSpecialistRunResult(**values)  # type: ignore[arg-type]


def _join(**overrides: object):
    values: dict[str, object] = {
        "authorization": _auth(),
        "consumed_attempt": _attempt(),
    }
    values.update(overrides)
    return join_sprint3c_evidence_response(Sprint3cEvidenceResponseInput(**values))  # type: ignore[arg-type]


def _local_receipt(**observation: object):
    request = "2026-10-20T11:00:00Z"
    context = DescriptiveReadContext(
        master_request_id="master-req-1",
        planning_session_id="plan-1",
        step_id="step-1",
        invocation_id="inv-1",
        specialist_invocation_id="spec-inv-1",
        specialist=HISTORY,
        purpose=MasterPurpose.ANSWER,
        capability=HISTORICAL_CAP,
        principal_ref="principal-ref-1",
        policy_revision="policy-rev-1",
        resource_id="historical.encounters",
        operation=GuardReadOperation.QUERY,
        accepted_at_utc=request,
        deadline_utc="2026-10-20T18:00:00Z",
        adapter_identity="local-historical-adapter",
        tool_name="summarize_historical_encounters",
        tool_call_id=observation.pop("tool_call_id", "tool-call-local"),
        requested_query=(("start_utc", "2026-10-01T00:00:00Z"), ("end_utc", "2026-10-03T00:00:00Z")),
        historical_window_start_utc="2026-10-01T00:00:00Z",
        historical_window_end_utc="2026-10-03T00:00:00Z",
        idempotency_key=observation.pop("idempotency_key", "idem-local-1"),
        correlation_id="corr-1",
    )
    row = ObservedSourceRow(record_id="rec-1", source_version="hv-9", event_timestamp_utc="2026-10-02T00:00:00Z")
    observed = {
        "executed_query": context.requested_query,
        "response_status": ToolResultStatus.SUCCESS,
        "rows_returned": 1,
        "rows": (row,),
        "page_index": 0,
        "page_count": 1,
        "page_limit": 25,
        "row_limit": 25,
        "query_clock_utc": "2026-10-20T12:00:00Z",
        "evaluation_clock_utc": "2026-10-20T12:00:05Z",
        "freshness": FreshnessStatus.FRESH,
        "query_id": "query-1",
        "response_source_version": "hv-9",
    }
    observed.update(observation)
    host = LocalTrustedReadHost(Clock("2026-10-20T12:01:00Z", "2026-10-20T12:01:01Z"))
    registration = host.register_local_adapter(
        "local-historical-adapter",
        lambda _action: LocalAdapterObservation(**observed),  # type: ignore[arg-type]
    )
    action = host.approve_executor_action(registration, context)
    assert isinstance(action, ApprovedExecutorAction)
    return host, host.execute_read(action)


def test_closed_production_paths_remain_fail_closed():
    assert list(MasterSourceAuthenticity) == [MasterSourceAuthenticity.NOT_ESTABLISHED]
    assert PRODUCTION_MA4_ADMISSION_AVAILABLE is False
    assert AVIATION_EVIDENCE_ADMISSION_ENABLED is False
    assert PRODUCTION_SOURCE_AUTHENTICATOR_INTEGRATED is False
    assert SOURCE_AUTHENTICATED_READ_AVAILABLE is False
    assert SAFETY_OR_DIVERSION_INSTRUCTIONS_ENABLED is False
    assert REGIONAL_TEXT_ESTABLISHES_POLYGON_INTERSECTION is False
    assert ABSENT_RISK_IS_LOW_RISK is False
    assert EMPTY_PAGE_CREATES_VERIFIED_ZERO is False
    assert NUMERICAL_IMPACT_COUNTS_IN_MA5 is False
    with pytest.raises(ContractValidationError, match="production_source_authenticator_not_integrated"):
        open_production_source_authenticator()
    with pytest.raises(ContractValidationError, match="production_ma4_admission_not_available"):
        open_sprint3c_production_evidence_admission()
    with pytest.raises(ContractValidationError, match="evidence_not_admitted"):
        admit_evidence()
    with pytest.raises(ContractValidationError, match="evidence_not_admitted"):
        refuse_sprint3c_runtime_evidence_admission()
    gaps = production_admission_gaps()
    assert "MasterSourceAuthenticity has only NOT_ESTABLISHED" in gaps
    assert "no ADMITTED MasterAdmissionStatus" in gaps


def test_matching_scoped_read_records_provenance_without_production_admission():
    host = _host()
    receipt = _execute(host, _page())
    assert receipt.outcome is ReadExecutionOutcome.COMPLETED
    assert receipt.adapter_id is not None
    assert receipt.adapter_id.startswith(SPRINT3C_SCOPED_READ_ADAPTER_PREFIX)
    attempt = _attempt(adapter_identity=receipt.adapter_id, grant_nonce=receipt.grant_nonce)
    join = _join(
        read_receipt=receipt,
        consumed_attempt=attempt,
        expected_source_version="version-1",
        expected_aircraft_id="AC1",
        expected_hazard_id="HZ-1",
        expected_clocks=ExpectedObservationClocks(
            accepted_at_utc=OBSERVED,
            source_reported_observed_at_utc=SOURCE_OBSERVED,
        ),
    )
    assert join.status is Sprint3cJoinStatus.STRUCTURAL_BIND_RECORDED
    assert join.authorization_bound is True
    assert join.consumed_attempt_bound is True
    assert join.scoped_read_adapter_accepted is True
    assert join.provenance.source_name == "historical.encounters"
    assert join.provenance.source_version == "version-1"
    assert join.provenance.query_id == "query-1"
    assert join.provenance.source_observation_time_utc == SOURCE_OBSERVED
    assert join.provenance.requested_time_utc is None
    assert join.provenance.application_request_time_utc == OBSERVED
    assert join.provenance.source_version_is_integrity_proof is False
    assert join.provenance.query_id_authenticated is False
    assert join.provenance.freshness_authenticated is False
    assert join.provenance.evaluation_copied_from_request is False
    assert join.host_evidence is not None
    assert join.host_evidence.observation_status is ReadReceiptVerificationStatus.LOCAL_OBSERVATION_RECORDED
    assert join.host_evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in join.host_evidence.admission.reason_codes
    assert join.production_ma4_admission is False
    _closed(join)


def test_cp5_live_ops_join_keeps_authenticity_unestablished_and_rejects_region_text():
    context, plan = _plan(
        _live_step(),
        master_request_id="master-req-live-1",
        correlation_id=SYNTHETIC_CORRELATION_ID,
        canonical_user_text=LIVE_TEXT,
        application_request_time_utc=OBSERVED,
    )
    authorization = plan.steps[0].invocation
    sources = _aligned_live_sources(authorization, completed_all_verified())
    native = _native_live_ops(authorization, sources)
    live_host = _host(
        master_request_id=authorization.master_request_id,
        planning_session_id="plan-live-1",
        step_id=plan.steps[0].step_id,
        invocation_id=authorization.invocation_id,
        specialist=LIVE,
        purpose=MasterPurpose.ANSWER,
        capability=OPERATIONAL,
        tool_name="get_aircraft_operational_context",
        tool_call_id="tool-call-live",
        resource_id="aircraft",
        operation=GuardReadOperation.GET_ITEM,
        historical_start_utc=None,
        historical_end_utc=None,
        correlation_id=authorization.correlation_id,
        idempotency_key="idem-live-cp5",
        grant_nonce="nonce-live-1",
    )
    receipt = _execute(
        live_host,
        _page(
            record_ids=("AC-1",),
            source_name="aircraft",
            source_version="sv-live-1",
            query_id="query-live-1",
            source_observed_at_utc=None,
        ),
        requested_arguments=(("aircraft_id", "AC-1"),),
    )
    attempt = _attempt(
        master_request_id=authorization.master_request_id,
        planning_session_id="plan-live-1",
        step_id=plan.steps[0].step_id,
        invocation_id=authorization.invocation_id,
        specialist=LIVE,
        purpose=MasterPurpose.ANSWER,
        specialist_request_id=authorization.specialist_request_id,
        correlation_id=authorization.correlation_id,
        resource_id="aircraft",
        operation=GuardReadOperation.GET_ITEM,
        adapter_identity=receipt.adapter_id,
        tool_name="get_aircraft_operational_context",
        tool_call_id="tool-call-live",
        grant_nonce=receipt.grant_nonce,
        idempotency_key="idem-live-cp5",
        accepted_at_utc=OBSERVED,
        principal_ref="principal-1",
        policy_revision="policy-rev-1",
    )
    california = ToolResult(
        tool_name="search_current_hazards",
        tool_call_id="tool-call-region",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data={
            "region": "California",
            "polygon_intersection": True,
            "impacted_aircraft_count": 12,
        },
        evidence=(
            Evidence(
                source="hazards",
                source_records=(
                    SourceRecord(
                        record_id="SIGMET-CA",
                        source_version="sv-live-1",
                        event_timestamp_utc=SOURCE_OBSERVED,
                    ),
                ),
                query_timestamp_utc=SOURCE_OBSERVED,
                freshness_status=FreshnessStatus.FRESH,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(),
                tool_call_id="tool-call-region",
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc=OBSERVED,
        limitations=(),
        correlation_id=authorization.correlation_id,
    )
    evidence_input = MasterEvidenceCorroborationInput(
        MasterEvidenceAdmissionInput(authorization, sources)
    )
    workflow = MasterParallelContractBundle(
        context=context,
        plan=plan,
        evidence_inputs=(evidence_input,),
    )
    join = _join(
        authorization=authorization,
        consumed_attempt=attempt,
        read_receipt=receipt,
        native_live_ops=native,
        live_ops_context=sources.context,
        native_admission_artifact=sources,
        workflow=workflow,
        untrusted_claim=california,
        expected_aircraft_id="AC-1",
    )
    assert join.admission is not None
    assert join.admission.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert MasterAdmissionReason.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in join.admission.reason_codes
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in join.admission.reason_codes
    assert join.admission.admission_status in {
        MasterAdmissionStatus.CONTRACT_ONLY,
        MasterAdmissionStatus.BINDING_UNRESOLVED,
    }
    assert join.admission.admission_status is not getattr(MasterAdmissionStatus, "ADMITTED", None)
    assert join.response_render is not None
    assert join.response_render.claims_admitted is False
    assert join.response_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert MasterResponseLimitation.SOURCE_AUTHENTICITY_NOT_ESTABLISHED in join.response_handoff.limitations
    assert Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON in join.defects
    assert Sprint3cJoinDefect.LIVE_OPS_VERIFIED_NOT_AUTHENTICATED in join.defects
    assert Sprint3cRejectedClaim.SIGMET_POLYGON_INTERSECTION in join.rejected_claims
    assert Sprint3cRejectedClaim.IMPACTED_AIRCRAFT_COUNT in join.rejected_claims
    assert join.polygon_intersection_established is False
    assert join.impacted_aircraft_count_established is False
    assert join.host_evidence is not None
    assert HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in join.host_evidence.limitations
    assert GeographicLimitation.POLYGON_INTERSECTION_NOT_ESTABLISHED in join.host_evidence.geographic_limitations
    _closed(join)


def test_local_receipt_is_not_production_evidence():
    host, receipt = _local_receipt()
    join = _join(
        read_receipt=receipt,
        local_host=host,
        authorization=_auth(application_request_time_utc="2026-10-20T11:00:00Z"),
        consumed_attempt=_attempt(
            adapter_identity="local-historical-adapter",
            accepted_at_utc="2026-10-20T11:00:00Z",
            tool_call_id="tool-call-local",
        ),
    )
    assert join.status is Sprint3cJoinStatus.LOCAL_RECEIPT_NOT_PRODUCTION
    assert join.scoped_read_adapter_accepted is False
    assert Sprint3cJoinDefect.LOCAL_ADAPTER_NOT_SCOPED_READ in join.defects
    assert join.host_evidence is not None
    assert join.host_evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    assert MasterAdmissionReason.UNSUPPORTED_ARTIFACT in join.host_evidence.admission.reason_codes
    _closed(join)


def test_empty_page_is_not_a_verified_zero():
    receipt = _execute(_host(idempotency_key="idem-empty"), _page(record_ids=()))
    join = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-empty",
        ),
    )
    assert receipt.page_empty is True
    assert receipt.verified_zero_minted is False
    assert Sprint3cJoinDefect.EMPTY_PAGE_NOT_VERIFIED_ZERO in join.defects
    assert Sprint3cJoinDefect.POPULATION_INCOMPLETE in join.defects
    assert join.verified_zero_established is False
    assert join.host_evidence is not None
    assert PopulationCondition.QUERY_RETURNED_ZERO_ROWS in join.host_evidence.population_conditions
    assert any(name in join.host_evidence.missing_verified_zero_conditions for name in VERIFIED_ZERO_ALWAYS_MISSING)
    _closed(join)


def test_partial_truncated_stale_and_conflicting_records_fail_closed():
    partial = _execute(
        _host(idempotency_key="idem-partial"),
        _page(has_more=True, truncated=True, next_page_token="secret-page-token"),
    )
    partial_join = _join(
        read_receipt=partial,
        consumed_attempt=_attempt(
            adapter_identity=partial.adapter_id,
            grant_nonce=partial.grant_nonce,
            idempotency_key="idem-partial",
        ),
    )
    assert Sprint3cJoinDefect.PARTIAL_OR_TRUNCATED in partial_join.defects
    assert Sprint3cJoinDefect.POPULATION_INCOMPLETE in partial_join.defects
    assert "secret-page-token" not in repr(partial_join)

    stale_host, stale_receipt = _local_receipt(
        response_status=ToolResultStatus.STALE,
        freshness=FreshnessStatus.STALE,
        tool_call_id="tool-call-stale",
        idempotency_key="idem-stale",
    )
    stale_join = _join(
        read_receipt=stale_receipt,
        local_host=stale_host,
        authorization=_auth(application_request_time_utc="2026-10-20T11:00:00Z"),
        consumed_attempt=_attempt(
            adapter_identity="local-historical-adapter",
            accepted_at_utc="2026-10-20T11:00:00Z",
            tool_call_id="tool-call-stale",
            idempotency_key="idem-stale",
        ),
    )
    assert Sprint3cJoinDefect.STALE_SOURCE in stale_join.defects
    assert stale_join.provenance.freshness is FreshnessStatus.STALE
    assert stale_join.provenance.freshness_authenticated is False

    mismatched = _execute(_host(idempotency_key="idem-conflict"), _page(source_version="stale-version"))
    conflict = _join(
        read_receipt=mismatched,
        consumed_attempt=_attempt(
            adapter_identity=mismatched.adapter_id,
            grant_nonce=mismatched.grant_nonce,
            idempotency_key="idem-conflict",
        ),
        expected_source_version="current-version",
    )
    assert Sprint3cJoinDefect.SOURCE_VERSION_MISMATCH in conflict.defects
    assert Sprint3cJoinDefect.CONFLICTING_RECORDS in conflict.defects
    _closed(partial_join)
    _closed(stale_join)
    _closed(conflict)


def test_missing_source_version_and_query_id_stay_absent():
    receipt = _execute(
        _host(idempotency_key="idem-absent-ids"),
        _page(source_version=None, query_id=None),
    )
    join = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-absent-ids",
        ),
    )
    assert join.provenance.source_version is None
    assert join.provenance.query_id is None
    assert Sprint3cJoinDefect.SOURCE_VERSION_ABSENT in join.defects
    assert Sprint3cJoinDefect.QUERY_ID_ABSENT in join.defects
    _closed(join)


def test_identity_and_scope_mismatches_fail_closed():
    receipt = _execute(_host(idempotency_key="idem-mismatch"), _page())
    foreign = _join(
        read_receipt=receipt,
        authorization=_auth(invocation_id="inv-other"),
        consumed_attempt=_attempt(
            invocation_id="inv-other",
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-mismatch",
        ),
    )
    assert foreign.status is Sprint3cJoinStatus.IDENTITY_MISMATCH
    assert Sprint3cJoinDefect.CONSUMED_ATTEMPT_MISMATCH in foreign.defects

    wrong_adapter = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity="nonproduction:LIVE_OPS:other:aircraft:GET_ITEM",
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-mismatch",
        ),
    )
    assert wrong_adapter.scoped_read_adapter_accepted is False
    assert Sprint3cJoinDefect.ADAPTER_IDENTITY_MISMATCH in wrong_adapter.defects

    wrong_aircraft = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-mismatch",
        ),
        expected_aircraft_id="AC-OTHER",
        expected_hazard_id="HZ-1",
    )
    assert Sprint3cJoinDefect.AIRCRAFT_IDENTITY_MISMATCH in wrong_aircraft.defects
    _closed(foreign)
    _closed(wrong_adapter)
    _closed(wrong_aircraft)


def test_hazard_identity_mismatch_and_omitted_record_are_not_coverage():
    receipt = _execute(
        _host(idempotency_key="idem-hazard"),
        _page(record_ids=("HZ-OTHER",)),
    )
    join = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-hazard",
        ),
        expected_hazard_id="HZ-1",
        expected_aircraft_id="AC1",
    )
    assert Sprint3cJoinDefect.HAZARD_IDENTITY_MISMATCH in join.defects
    assert Sprint3cJoinDefect.MISSING_RECORD in join.defects
    assert join.host_evidence is not None
    assert PopulationCondition.SOURCE_OMITTED_RECORD in join.host_evidence.population_conditions
    _closed(join)


def test_california_text_is_not_sigmet_polygon_or_an_impact_count():
    receipt = _execute(
        _host(idempotency_key="idem-ca", region_codes=frozenset({"CA"})),
        _page(record_ids=("SIGMET-CA",)),
    )
    claim = ToolResult(
        tool_name="list_historical_encounters",
        tool_call_id="tool-call-ca",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.HISTORICAL,
        data={
            "region": "California",
            "polygon_intersection": True,
            "impacted_aircraft_count": 7,
            "risk_level": "LOW",
        },
        evidence=(
            Evidence(
                source="historical.encounters",
                source_records=(
                    SourceRecord(
                        record_id="SIGMET-CA",
                        source_version="forged-version",
                        event_timestamp_utc=SOURCE_OBSERVED,
                    ),
                ),
                query_timestamp_utc=SOURCE_OBSERVED,
                freshness_status=FreshnessStatus.FRESH,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(),
                tool_call_id="tool-call-ca",
                temporal_scope=TemporalScope.HISTORICAL,
            ),
        ),
        as_of_utc=OBSERVED,
        limitations=(),
        correlation_id="corr-1",
    )
    join = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-ca",
            region_scope=("CA",),
        ),
        untrusted_claim=claim,
    )
    assert Sprint3cJoinDefect.REGIONAL_TEXT_NOT_POLYGON in join.defects
    assert Sprint3cJoinDefect.MISSING_RISK_NOT_LOW in join.defects
    assert join.polygon_intersection_established is False
    assert join.impacted_aircraft_count_established is False
    assert join.host_evidence is not None
    assert HostEvidenceLimitation.REGIONAL_TEXT_NOT_POLYGON_INTERSECTION in join.host_evidence.limitations
    _closed(join)


def test_missing_risk_is_not_low_and_stays_out_of_ma5():
    context, plan = _plan(_decision_step(), target_candidate_aircraft_id="ac-1")
    authorization = plan.steps[0].invocation
    claims, bindings, _payload = decision_absent_risk_case()
    artifact = _pack(claims, bindings)
    evidence = MasterEvidenceCorroborationInput(MasterEvidenceAdmissionInput(authorization, artifact))
    workflow = MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=(evidence,))
    resolution = SimpleNamespace(
        decision_request=None,
        native_request_built=False,
        trusted_target_issued=False,
        absent_risk_is_low_risk=False,
        authenticated_evidence=False,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        risk_observation=RiskRecordObservation.MISSING,
        observed_risk_level=None,
        observed_aircraft_id="ac-1",
    )
    join = _join(
        authorization=authorization,
        consumed_attempt=_attempt(
            master_request_id=authorization.master_request_id,
            invocation_id=authorization.invocation_id,
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            specialist_request_id=authorization.specialist_request_id,
            correlation_id=authorization.correlation_id,
            accepted_at_utc=OBSERVED,
        ),
        native_admission_artifact=artifact,
        decision_resolution=resolution,
        workflow=workflow,
    )
    assert Sprint3cJoinDefect.MISSING_RISK_NOT_LOW in join.defects
    assert Sprint3cJoinDefect.DECISION_COMPARISON_NOT_TARGET in join.defects
    assert Sprint3cJoinDefect.DECISION_REQUEST_NOT_ESTABLISHED in join.defects
    assert join.low_risk_admitted is False
    assert join.corroboration is not None
    assert MasterCorroborationReason.RECORDED_RISK_ABSENCE_NOT_LOW in join.corroboration.results[0].reason_codes
    assert join.response_render is not None
    assert "LOW" not in join.response_render.status_text
    _closed(join)

    low_claims, low_bindings, _low = decision_absent_called_low_case()
    low_join = _join(
        authorization=authorization,
        consumed_attempt=_attempt(
            master_request_id=authorization.master_request_id,
            invocation_id=authorization.invocation_id,
            specialist=DECISION,
            purpose=MasterPurpose.ANSWER,
            specialist_request_id=authorization.specialist_request_id,
            correlation_id=authorization.correlation_id,
            accepted_at_utc=OBSERVED,
            idempotency_key="idem-low-claim",
            payload_fingerprint="fingerprint-low",
        ),
        native_admission_artifact=_pack(low_claims, low_bindings),
        decision_resolution=resolution,
    )
    assert low_join.low_risk_admitted is False
    assert "CLAIM_VALUE_MISMATCH" in low_join.corroboration.results[0].rejected_claim_codes
    _closed(low_join)


def test_historical_run_is_not_a_final_artifact_and_preserves_limitations():
    run = HistoricalSpecialistRunResult(
        status=SpecialistRunStatus.UNSUPPORTED,
        tool_results=(),
        tool_result_projections=(),
        executed_invocations=(),
        proposed_claims=(),
        unsupported_reason=UnsupportedReason.OUT_OF_CATALOG,
        evaluated_as_of_utc=OBSERVED,
        runtime_errors=(),
        provider_turn_count=0,
        executed_tool_call_count=0,
        validation_feedback=(),
    )
    envelope = SimpleNamespace(
        finalization_performed=False,
        evidence_issued=False,
        run_is_final_artifact=False,
        verified_zero_admitted=False,
        source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
        invocation_id="inv-1",
        native_run=run,
        native_final_result=None,
        source_version_mismatch=False,
        authenticated_source_version=None,
        authenticated_query_id=None,
    )
    join = _join(
        historical_envelope=envelope,
        historical_run=run,
        authorization=_auth(requested_evaluation_time_utc=OBSERVED),
    )
    assert join.status is Sprint3cJoinStatus.NATIVE_REJECTED
    assert join.admission is not None
    assert MasterAdmissionReason.HISTORICAL_RUN_NOT_FINAL_ARTIFACT in join.admission.reason_codes
    assert Sprint3cJoinDefect.HISTORICAL_RUN_NOT_FINAL in join.defects
    _closed(join)


def test_live_ops_all_verified_is_not_source_authentication():
    authorization = _auth(
        invocation_id="inv-live-1",
        specialist=LIVE,
        specialist_request_id=SYNTHETIC_REQUEST_ID,
        correlation_id=SYNTHETIC_CORRELATION_ID,
        requested_evaluation_time_utc=None,
    )
    sources = _aligned_live_sources(authorization, completed_all_verified())
    join = _join(
        authorization=authorization,
        consumed_attempt=_attempt(
            invocation_id="inv-live-1",
            specialist=LIVE,
            specialist_request_id=SYNTHETIC_REQUEST_ID,
            correlation_id=SYNTHETIC_CORRELATION_ID,
        ),
        native_admission_artifact=sources,
        native_live_ops=_native_live_ops(authorization, sources),
        live_ops_context=sources.context,
    )
    assert sources.result.verification_outcome is LiveOpsPublicVerificationOutcome.ALL_VERIFIED
    assert join.admission is not None
    assert MasterAdmissionReason.LIVE_OPS_ALL_VERIFIED_NOT_SOURCE_AUTHENTICATION in join.admission.reason_codes
    assert Sprint3cJoinDefect.LIVE_OPS_VERIFIED_NOT_AUTHENTICATED in join.defects
    assert "FRESHNESS_NOT_ESTABLISHED" in join.native_limitations
    _closed(join)


def test_ma5_keeps_counts_recommendations_and_safety_text_out():
    context, plan = _plan(_historical_step())
    empty = MasterParallelContractBundle(context=context, plan=plan, evidence_inputs=())
    join = _join(
        authorization=plan.steps[0].invocation,
        consumed_attempt=_attempt(
            master_request_id=plan.master_request_id,
            invocation_id=plan.steps[0].invocation.invocation_id,
            specialist_request_id=plan.steps[0].invocation.specialist_request_id,
            correlation_id=plan.steps[0].invocation.correlation_id,
            accepted_at_utc=OBSERVED,
        ),
        workflow=empty,
    )
    assert join.response_handoff is not None
    assert join.response_handoff.state is MasterResponsePolicyState.INSUFFICIENT_EVIDENCE
    assert join.response_render is not None
    assert join.response_render.claims_admitted is False
    assert MasterResponseLimitation.NO_OPERATIONAL_CLAIM in join.response_handoff.limitations
    assert Sprint3cRejectedClaim.OPERATIONAL_RECOMMENDATION in join.rejected_claims
    assert Sprint3cRejectedClaim.SAFETY_OR_DIVERSION_INSTRUCTION in join.rejected_claims
    assert Sprint3cRejectedClaim.IMPACTED_AIRCRAFT_COUNT in join.rejected_claims
    _closed(join)


def test_clock_roles_stay_distinct_and_swapped_clocks_are_defects():
    receipt = _execute(_host(idempotency_key="idem-clocks"), _page())
    swapped = _join(
        read_receipt=receipt,
        consumed_attempt=_attempt(
            adapter_identity=receipt.adapter_id,
            grant_nonce=receipt.grant_nonce,
            idempotency_key="idem-clocks",
        ),
        expected_clocks=ExpectedObservationClocks(
            accepted_at_utc=SOURCE_OBSERVED,
            execution_started_at_utc=OBSERVED,
            execution_finished_at_utc=OBSERVED,
            source_reported_observed_at_utc=OBSERVED,
        ),
    )
    assert Sprint3cJoinDefect.CLOCK_ROLE_COLLISION in swapped.defects
    assert swapped.host_evidence is not None
    assert HostEvidenceLimitation.CLOCKS_SWAPPED in swapped.host_evidence.limitations
    assert swapped.provenance.evaluation_copied_from_request is False
    _closed(swapped)


def test_forged_or_untrusted_subjects_are_not_receipts():
    join = _join(read_receipt={"authentic_source_read": True, "candidate_kind": "raw_read_observation"})
    assert join.scoped_read_adapter_accepted is False
    assert Sprint3cJoinDefect.FORGED_OR_UNTRUSTED_RECEIPT in join.defects
    assert join.host_evidence is not None
    assert join.host_evidence.admission.admission_status is MasterAdmissionStatus.REJECTED
    _closed(join)


def test_consumed_attempt_rejects_scan_and_production_flags():
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        _attempt(operation=GuardReadOperation.SCAN)
    with pytest.raises(ContractValidationError, match="distributed_exactly_once_not_established"):
        _attempt(distributed_exactly_once=True)
    with pytest.raises(ContractValidationError, match="production_grant_issuance_closed"):
        _attempt(production_grant=True)
    with pytest.raises(ContractValidationError, match="request_identity_not_distinct"):
        _attempt(invocation_id="master-req-1")


def test_decision_request_cannot_be_smuggled_through_the_join():
    with pytest.raises(ContractValidationError, match="decision_target_not_established"):
        _join(
            authorization=_auth(invocation_id="inv-dec-1", specialist=DECISION, expected_aircraft_id="ac-1"),
            consumed_attempt=_attempt(invocation_id="inv-dec-1", specialist=DECISION),
            decision_resolution=SimpleNamespace(
                decision_request=object(),
                native_request_built=False,
                absent_risk_is_low_risk=False,
                authenticated_evidence=False,
                source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
                risk_observation=RiskRecordObservation.MISSING,
                observed_risk_level=None,
                observed_aircraft_id=None,
            ),
        )
