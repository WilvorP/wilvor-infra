"""Host/specialist runtime contract stays descriptive and fail-closed."""

from __future__ import annotations

import ast
from dataclasses import asdict
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    TemporalScope,
)
from wilvor_ai.decision_specialist_runtime_contracts import (
    DecisionSpecialistRequest,
    DecisionSpecialistRunResult,
    DecisionTargetMode,
)
from wilvor_ai.live_ops_public_contracts import LiveOpsRunResult, LiveOpsRunStatus
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_decision_target_resolver import REQUIRED_HOST_PROOFS
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_historical_execution_readiness import (
    historical_native_result_correlation_gaps,
)
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget
from wilvor_ai.master_routing import (
    MAX_DECISION_STEPS,
    MAX_ROUTE_STEPS,
    MasterRouteExecutionAuthority,
)
from wilvor_ai.master_specialist_runtime_contract import (
    ACCEPTED_AT_CLOCK_ALIAS,
    ALLOWED_RECORD_STATUSES,
    DECISION_COMPARISON_FIELDS,
    DECISION_TARGET_GATE,
    DECISION_TOOLS_RUNTIME_FIELDS,
    HISTORICAL_CONTRACT_MIGRATION,
    NATIVE_ENTRYPOINTS,
    NATIVE_IDENTITY_MISMATCHES,
    NATIVE_RESULT_TYPES,
    PRODUCTION_AUTHORITY_AVAILABLE,
    PRODUCTION_HOST,
    SHARED_ENVELOPE_FIELDS,
    SPECIALIST_DISPATCH_AVAILABLE,
    ApprovedPolicyDecision,
    AuthorizationProofObservation,
    BudgetCancellation,
    CancellationObservation,
    ClockRole,
    EffectiveExecutionScope,
    FalseProofKind,
    HostInvocationIdentity,
    IssuedGrantReference,
    LedgerReservation,
    NativeResultStage,
    PolicyDecisionStatus,
    PrincipalRequirement,
    ProvenanceHandoff,
    RuntimeContractStatus,
    SpecialistRequestEnvelope,
    ToolExecutionObservation,
    ToolExecutionObservationStatus,
    TrustDistinction,
    admit_evidence,
    bind_effective_execution_scope,
    bind_host_invocation_identity_from_candidates,
    bind_native_specialist_execution_result,
    construct_decision_native_request,
    invoke_specialist,
    issue_grant,
    issue_principal,
    observe_authorization_proof,
    reference_unissued_grant,
    reject_false_proof,
)
from wilvor_ai.specialist_contracts import SpecialistRequest, SpecialistResult
from wilvor_ai.specialist_runtime_contracts import (
    HistoricalSpecialistRunResult,
    SpecialistRunStatus,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
AI_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_ai"
INTERFACE = AI_DIR / "SPRINT3B2_TRUSTED_HOST_INTERFACE.md"

ACCEPTED = "2026-10-27T12:00:00Z"
DEADLINE = "2026-10-27T12:05:00Z"
OBSERVED = "2026-10-27T12:01:00Z"


def _values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "master_request_id": "master-request-1",
        "planning_session_id": "planning-session-1",
        "step_id": "step-1",
        "invocation_id": "invocation-1",
        "specialist_request_id": "specialist-request-1",
        "correlation_id": "correlation-1",
        "specialist": MasterSpecialist.LIVE_OPS,
        "purpose": MasterPurpose.ANSWER,
        "capability": AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        "requirement": MasterRequirement.REQUIRED,
    }
    values.update(overrides)
    return values


def _identity(**overrides: object) -> HostInvocationIdentity:
    values = _values(**overrides)
    return HostInvocationIdentity(**values)  # type: ignore[arg-type]


def _envelope(
    identity: HostInvocationIdentity | None = None,
    **overrides: object,
) -> SpecialistRequestEnvelope:
    owner = _identity() if identity is None else identity
    executions, turns, calls = server_read_budget(owner.specialist)
    values: dict[str, object] = {
        "identity": owner,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "idempotency_key": "idempotency-1",
        "max_model_turns": turns,
        "max_tool_calls": calls,
        "max_executions": executions,
        "principal_ref": "hint-principal",
        "policy_revision": "policy-rev-1",
        "resource_scope": ("aircraft",),
        "region_scope": ("KSEA",),
    }
    if owner.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
        values["historical_window_start_utc"] = "2026-10-20T00:00:00Z"
        values["historical_window_end_utc"] = "2026-10-26T00:00:00Z"
    if owner.specialist is MasterSpecialist.DECISION:
        values["decision_target_scope"] = "candidate-hint"
    values.update(overrides)
    return SpecialistRequestEnvelope(**values)  # type: ignore[arg-type]


def _run_signature(path: Path, class_name: str) -> tuple[tuple[str, ...], tuple[str, ...], str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "run":
                    args = tuple(arg.arg for arg in item.args.args if arg.arg != "self")
                    annotations = tuple(
                        ast.unparse(arg.annotation) if arg.annotation is not None else ""
                        for arg in item.args.args
                        if arg.arg != "self"
                    )
                    returns = ast.unparse(item.returns) if item.returns is not None else ""
                    return args, annotations, returns
    raise AssertionError(class_name)


def _dataclass_fields(path: Path, class_name: str) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return tuple(
                item.target.id
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            )
    raise AssertionError(class_name)


def test_shared_envelope_names_and_clock_alias():
    assert "master_request_id" in SHARED_ENVELOPE_FIELDS
    assert "planning_session_id" in SHARED_ENVELOPE_FIELDS
    assert "specialist_request_id" in SHARED_ENVELOPE_FIELDS
    assert "accepted_at_utc" in SHARED_ENVELOPE_FIELDS
    assert ACCEPTED_AT_CLOCK_ALIAS == "application_request_time_utc"
    assert ClockRole.APPLICATION_REQUEST.value == "APPLICATION_REQUEST"
    assert ClockRole.EVALUATION is not ClockRole.APPLICATION_REQUEST
    assert MAX_ROUTE_STEPS == 4
    assert MAX_DECISION_STEPS == 1
    assert PRODUCTION_AUTHORITY_AVAILABLE is False
    assert SPECIALIST_DISPATCH_AVAILABLE is False
    assert PRODUCTION_HOST is None
    assert ALLOWED_RECORD_STATUSES == frozenset(
        {
            RuntimeContractStatus.DESCRIBED,
            RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
            RuntimeContractStatus.EVALUATED_DENY,
            RuntimeContractStatus.NOT_ESTABLISHED,
        }
    )


def test_realistic_description_does_not_authorize():
    identity = _identity()
    envelope = _envelope(identity)
    principal = PrincipalRequirement(identity=identity, principal_ref_hint="hint-principal")
    scope = bind_effective_execution_scope(envelope, (TemporalScope.CURRENT,))
    policy = ApprovedPolicyDecision(
        identity=identity,
        policy_revision="policy-rev-1",
        policy_status=PolicyDecisionStatus.ALLOW_DESCRIPTIVE,
        status=RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
    )
    grant = reference_unissued_grant(
        envelope,
        scope,
        grant_ref="grant-ref-1",
        policy_revision="policy-rev-1",
    )
    reservation = LedgerReservation(
        identity=identity,
        reservation_ref="reservation-1",
        idempotency_key=envelope.idempotency_key,
    )
    budget = BudgetCancellation(
        identity=identity,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
        deadline_utc=envelope.deadline_utc,
        accepted_at_utc=envelope.accepted_at_utc,
        independent_observation_time_utc=OBSERVED,
        cancellation=CancellationObservation.NOT_CANCELLED,
        model_turns_observed=0,
        tool_calls_observed=0,
        budget_exhausted=False,
    )
    observation = ToolExecutionObservation(
        identity=identity,
        tool_name="get_aircraft_operational_context",
        operation=ReadOperation.GET_ITEM,
        observation_status=ToolExecutionObservationStatus.COMPLETED,
        argument_names=("aircraft_id",),
        source_identity="aircraft-table",
        query_or_record_id="record-1",
        source_version="version-1",
        source_observation_time_utc=OBSERVED,
        evaluation_time_utc="2026-10-27T12:02:00Z",
        completeness="page",
        pagination_complete=False,
        truncated=True,
        missing_record=True,
        empty_page=True,
        regional_name_match="Seattle",
    )
    native = bind_native_specialist_execution_result(
        identity,
        NativeResultStage.LIVE_OPS_RUN,
        LiveOpsRunStatus.COMPLETED,
    )
    handoff = ProvenanceHandoff(
        identity=identity,
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        tool_observations=(observation,),
        native_result=native,
        requested_evaluation_time_utc="2026-10-27T11:00:00Z",
        source_observation_time_utc=OBSERVED,
        evaluation_time_utc="2026-10-27T12:02:00Z",
        independent_observation_time_utc=OBSERVED,
    )

    assert identity.status is RuntimeContractStatus.DESCRIBED
    assert identity.posture is TrustDistinction.DESCRIPTIVE
    assert envelope.authorization_attached is False
    assert envelope.hints_are_authority is False
    assert principal.satisfied is False
    assert principal.host_issued is False
    assert principal.edge_jwt_is_attestation is False
    assert scope.redeemable is False
    assert scope.max_route_steps == 4
    assert scope.decision_target_established is False
    assert policy.usable_as_production_grant is False
    assert policy.policy_status is PolicyDecisionStatus.ALLOW_DESCRIPTIVE
    assert grant.issued is False
    assert grant.copied_from_envelope is True
    assert grant.self_authorizing is False
    assert reservation.consumed is False
    assert budget.enforced_at_tool_execution is False
    assert budget.caller_budget_is_authority is False
    assert observation.aviation_evidence is False
    assert observation.verified_zero is False
    assert observation.sigmet_geographic_coverage is False
    assert observation.approved_runtime_adapter is False
    assert native.observed is False
    assert native.result_retained is False
    assert native.host_identity_complete is False
    assert native.matching_identifier_is_authorization is False
    assert "correlation_id" in native.present_host_fields
    assert "master_request_id" in native.missing_host_fields
    assert handoff.admitted is False
    assert handoff.verified_zero is False
    for record in (
        identity,
        envelope,
        principal,
        scope,
        policy,
        grant,
        reservation,
        budget,
        observation,
        native,
        handoff,
    ):
        assert record.production_authority is MasterProductionAuthority.DENIED
        assert record.execution_authority is MasterRouteExecutionAuthority.DENIED
        assert record.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED


def test_closed_operations_reject_a_valid_description():
    identity = _identity()
    envelope = _envelope(identity)
    scope = bind_effective_execution_scope(envelope, (TemporalScope.CURRENT,))
    grant = reference_unissued_grant(
        envelope,
        scope,
        grant_ref="grant-ref-1",
        policy_revision="policy-rev-1",
    )
    with pytest.raises(ContractValidationError, match="principal_not_issued"):
        issue_principal(PrincipalRequirement(identity=identity))
    with pytest.raises(ContractValidationError, match="grant_not_issued"):
        issue_grant(grant)
    with pytest.raises(ContractValidationError, match="specialist_invocation_not_granted"):
        invoke_specialist(envelope, grant)
    with pytest.raises(ContractValidationError, match="evidence_not_admitted"):
        admit_evidence(envelope)
    with pytest.raises(ContractValidationError, match="decision_target_not_established"):
        construct_decision_native_request(
            DecisionSpecialistRequest(
                user_text="explain the current risk",
                mode=DecisionTargetMode.AIRCRAFT,
                aircraft_id="ACFT1",
            )
        )


def test_allowed_policy_statuses_do_not_grant():
    identity = _identity()
    for policy_status, record_status in (
        (PolicyDecisionStatus.ALLOW_DESCRIPTIVE, RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE),
        (PolicyDecisionStatus.DENY, RuntimeContractStatus.EVALUATED_DENY),
        (PolicyDecisionStatus.NOT_ESTABLISHED, RuntimeContractStatus.NOT_ESTABLISHED),
    ):
        decision = ApprovedPolicyDecision(
            identity=identity,
            policy_revision="policy-rev-1",
            policy_status=policy_status,
            status=record_status,
        )
        assert decision.usable_as_production_grant is False
        assert decision.posture is TrustDistinction.EVALUATED
        observed = observe_authorization_proof(decision, identity)
        assert observed.status is RuntimeContractStatus.REJECTED_FALSE_PROOF
        assert observed.proof_kind is FalseProofKind.POLICY_CHECK
        assert observed.grant_issued is False


def test_disallowed_status_and_authority_fail():
    identity = _identity()
    with pytest.raises(ContractValidationError, match="trust_distinction_not_available"):
        ApprovedPolicyDecision(
            identity=identity,
            policy_revision="policy-rev-1",
            policy_status=PolicyDecisionStatus.ALLOW_DESCRIPTIVE,
            posture=TrustDistinction.HOST_ISSUED,
            status=RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
        )
    with pytest.raises(ContractValidationError, match="descriptive_allow_not_a_grant"):
        ApprovedPolicyDecision(
            identity=identity,
            policy_revision="policy-rev-1",
            policy_status=PolicyDecisionStatus.ALLOW_DESCRIPTIVE,
            usable_as_production_grant=True,
            status=RuntimeContractStatus.EVALUATED_ALLOW_DESCRIPTIVE,
        )
    with pytest.raises(ContractValidationError, match="production_authority_not_issued"):
        HostInvocationIdentity(
            **_values(),  # type: ignore[arg-type]
            production_authority=MasterProductionAuthority.PRODUCTION,
        )
    with pytest.raises(ContractValidationError, match="invalid_status"):
        HostInvocationIdentity(
            **_values(),  # type: ignore[arg-type]
            status=RuntimeContractStatus.REJECTED_FALSE_PROOF,
        )


def test_missing_and_ambiguous_identity():
    with pytest.raises(ContractValidationError, match="identity_missing"):
        bind_host_invocation_identity_from_candidates()
    with pytest.raises(ContractValidationError, match="identity_missing"):
        bind_host_invocation_identity_from_candidates({"master_request_id": "master-request-1"})
    first = _values()
    second = _values(correlation_id="correlation-2")
    with pytest.raises(ContractValidationError, match="identity_ambiguous"):
        bind_host_invocation_identity_from_candidates(first, second)
    with pytest.raises(ContractValidationError, match="identity_ambiguous"):
        bind_host_invocation_identity_from_candidates("master-request-1")  # type: ignore[arg-type]
    bound = bind_host_invocation_identity_from_candidates(first, dict(first))
    assert bound.correlation_id == "correlation-1"
    assert bound.production_authority is MasterProductionAuthority.DENIED


def test_collapsed_identity_and_bad_fields():
    with pytest.raises(ContractValidationError, match="request_identity_not_distinct"):
        _identity(invocation_id="master-request-1")
    with pytest.raises(ContractValidationError, match="invalid_master_request_id"):
        _identity(master_request_id="master request")
    with pytest.raises(ContractValidationError, match="capability_step_mismatch"):
        _identity(capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS)
    with pytest.raises(ContractValidationError, match="unsupported_specialist_purpose"):
        _identity(
            specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
            purpose=MasterPurpose.DISCOVERY,
            capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
        )
    with pytest.raises(ContractValidationError, match="prohibited_capability"):
        _identity(capability=AgentCapability.ISSUE_LANDING_INSTRUCTIONS)
    with pytest.raises(ContractValidationError, match="budget_exceeds_server_ceiling"):
        _envelope(max_model_turns=5)
    with pytest.raises(ContractValidationError, match="invalid_deadline"):
        _envelope(deadline_utc=ACCEPTED)
    with pytest.raises(ContractValidationError, match="arbitrary_resource_not_permitted"):
        _envelope(resource_scope=("arn:aws:dynamodb:us-west-1:1:table/aircraft",))
    with pytest.raises(ContractValidationError, match="contradictory_temporal_bounds"):
        _envelope(historical_window_start_utc="2026-10-20T00:00:00Z")


def test_historical_window_ceiling_and_decision_target_hint():
    historical = _identity(
        specialist=MasterSpecialist.HISTORICAL_ANALYTICS,
        capability=AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,
    )
    envelope = _envelope(historical)
    scope = bind_effective_execution_scope(envelope, (TemporalScope.HISTORICAL,))
    assert scope.historical_window_bound_to_tool_arguments is False
    assert envelope.historical_window_is_tool_argument is False
    with pytest.raises(ContractValidationError, match="historical_window_exceeds_seven_days"):
        _envelope(
            historical,
            historical_window_end_utc="2026-10-27T00:00:01Z",
        )
    exact = _envelope(
        historical,
        historical_window_start_utc="2026-10-20T00:00:00Z",
        historical_window_end_utc="2026-10-27T00:00:00Z",
    )
    assert exact.historical_window_end_utc == "2026-10-27T00:00:00Z"
    decision = _identity(
        specialist=MasterSpecialist.DECISION,
        capability=AgentCapability.EXPLAIN_EXISTING_RISK,
    )
    decision_envelope = _envelope(decision)
    decision_scope = bind_effective_execution_scope(decision_envelope, (TemporalScope.CURRENT, TemporalScope.PERSISTED))
    assert decision_scope.decision_target_scope == "candidate-hint"
    assert decision_scope.decision_target_established is False
    with pytest.raises(ContractValidationError, match="decision_target_not_established"):
        EffectiveExecutionScope(
            identity=decision,
            temporal_scopes=(TemporalScope.CURRENT,),
            resource_scope=(),
            region_scope=(),
            historical_window_start_utc=None,
            historical_window_end_utc=None,
            decision_target_scope="candidate-hint",
            decision_target_established=True,
        )


def test_false_proof_kinds_issue_nothing():
    identity = _identity()
    envelope = _envelope(identity)
    cases = (
        (True, FalseProofKind.BOOLEAN),
        ("PRODUCTION", FalseProofKind.STRING_CLAIM),
        ('{"schema_version":"copied"}', FalseProofKind.SERIALIZED_DATACLASS),
        (asdict(envelope), FalseProofKind.SERIALIZED_DATACLASS),
        (envelope, FalseProofKind.SAME_ID_OBJECT),
        (identity, FalseProofKind.SAME_ID_OBJECT),
        ({"ENVIRONMENT": "test", "WILVOR_TEST_MODE": "1"}, FalseProofKind.ENVIRONMENT_CLAIM),
        ({"requestContext": {"authorizer": {"jwt": {"sub": "x"}}}}, FalseProofKind.JWT_DICTIONARY),
        ({"Authorization": "Bearer forged"}, FalseProofKind.AUTHORIZATION_HEADER),
        ({"body": "{}"}, FalseProofKind.EVENT_BODY),
        (SpecialistRequest(text="status", request_id="specialist-request-1"), FalseProofKind.NATIVE_RESULT),
        (object(), FalseProofKind.CALLER_OBJECT),
    )
    for proof, kind in cases:
        observed = observe_authorization_proof(proof, identity)
        assert observed.status is RuntimeContractStatus.REJECTED_FALSE_PROOF
        assert observed.proof_kind is kind
        assert observed.grant_issued is False
        assert observed.principal_issued is False
        assert observed.production_authority is MasterProductionAuthority.DENIED
        with pytest.raises(ContractValidationError, match="caller_proof_not_host_issued"):
            reject_false_proof(proof, identity)
    absent = observe_authorization_proof(None, identity)
    assert absent.status is RuntimeContractStatus.NOT_ESTABLISHED
    assert absent.proof_kind is None
    assert absent.grant_issued is False
    with pytest.raises(ContractValidationError, match="authorization_not_established"):
        reject_false_proof(None, identity)


def test_absence_and_regional_string_are_not_verified_zero():
    identity = _identity()
    observation = ToolExecutionObservation(
        identity=identity,
        tool_name="search_current_hazards",
        operation=ReadOperation.QUERY,
        observation_status=ToolExecutionObservationStatus.COMPLETED,
        argument_names=("region",),
        missing_record=True,
        empty_page=True,
        impact_count=None,
        regional_name_match="Seattle",
    )
    assert observation.verified_zero is False
    assert observation.aviation_evidence is False
    assert observation.sigmet_geographic_coverage is False
    with pytest.raises(ContractValidationError, match="absence_is_not_verified_zero"):
        ToolExecutionObservation(
            identity=identity,
            tool_name="search_current_hazards",
            operation=ReadOperation.QUERY,
            observation_status=ToolExecutionObservationStatus.COMPLETED,
            verified_zero=True,
        )
    with pytest.raises(ContractValidationError, match="geospatial_coverage_not_established"):
        ToolExecutionObservation(
            identity=identity,
            tool_name="search_current_hazards",
            operation=ReadOperation.QUERY,
            observation_status=ToolExecutionObservationStatus.NOT_EXECUTED,
            sigmet_geographic_coverage=True,
        )
    with pytest.raises(ContractValidationError, match="scan_not_permitted"):
        ToolExecutionObservation(
            identity=identity,
            tool_name="search_current_hazards",
            operation=ReadOperation.SCAN,
            observation_status=ToolExecutionObservationStatus.REJECTED,
        )
    with pytest.raises(ContractValidationError, match="unsupported_tool"):
        ToolExecutionObservation(
            identity=identity,
            tool_name="put_item",
            operation=ReadOperation.GET_ITEM,
            observation_status=ToolExecutionObservationStatus.REJECTED,
        )
    with pytest.raises(ContractValidationError, match="argument_not_allowlisted"):
        ToolExecutionObservation(
            identity=identity,
            tool_name="search_current_hazards",
            operation=ReadOperation.QUERY,
            observation_status=ToolExecutionObservationStatus.REJECTED,
            argument_names=("sql",),
        )


def test_cancellation_is_recorded_without_tool_enforcement():
    identity = _identity()
    envelope = _envelope(identity)
    expired = BudgetCancellation(
        identity=identity,
        max_model_turns=envelope.max_model_turns,
        max_tool_calls=envelope.max_tool_calls,
        deadline_utc=DEADLINE,
        accepted_at_utc=ACCEPTED,
        independent_observation_time_utc="2026-10-27T12:05:00Z",
        cancellation=CancellationObservation.DEADLINE_EXPIRED,
        model_turns_observed=envelope.max_model_turns,
        tool_calls_observed=0,
        budget_exhausted=True,
    )
    assert expired.enforced_at_tool_execution is False
    assert expired.clocks_equated is False
    with pytest.raises(ContractValidationError, match="execution_not_enforced_here"):
        BudgetCancellation(
            identity=identity,
            max_model_turns=envelope.max_model_turns,
            max_tool_calls=envelope.max_tool_calls,
            deadline_utc=DEADLINE,
            accepted_at_utc=ACCEPTED,
            independent_observation_time_utc=OBSERVED,
            cancellation=CancellationObservation.NOT_CANCELLED,
            model_turns_observed=0,
            tool_calls_observed=0,
            budget_exhausted=False,
            enforced_at_tool_execution=True,
        )


def test_native_entrypoints_match_source_signatures():
    expected = {
        MasterSpecialist.LIVE_OPS: (
            AI_DIR / "live_ops_specialist.py",
            "LiveOpsSpecialist",
            ("request", "context"),
            ("LiveOpsRunRequest", "LiveOpsTrustedContext"),
            "LiveOpsRunResult",
        ),
        MasterSpecialist.HISTORICAL_ANALYTICS: (
            AI_DIR / "historical_specialist.py",
            "HistoricalAnalyticsSpecialist",
            ("request", "context"),
            ("SpecialistRequest", "HistoricalSpecialistTrustedContext"),
            "HistoricalSpecialistRunResult",
        ),
        MasterSpecialist.DECISION: (
            AI_DIR / "decision_specialist.py",
            "DecisionSpecialist",
            ("request", "runtime"),
            ("DecisionSpecialistRequest", "DecisionToolsRuntime"),
            "DecisionSpecialistRunResult",
        ),
    }
    assert len(NATIVE_ENTRYPOINTS) == 3
    for entry in NATIVE_ENTRYPOINTS:
        path, class_name, args, annotations, returns = expected[entry.specialist]
        assert _run_signature(path, class_name) == (args, annotations, returns)
        assert entry.method == "run"
        assert entry.invoked_here is False
        assert entry.owner_type == class_name
        assert entry.context_parameter == args[1]
        assert entry.request_type == annotations[0]
        assert entry.context_type == annotations[1]
        assert entry.direct_result_type == returns
    historical = next(item for item in NATIVE_ENTRYPOINTS if item.specialist is MasterSpecialist.HISTORICAL_ANALYTICS)
    decision = next(item for item in NATIVE_ENTRYPOINTS if item.specialist is MasterSpecialist.DECISION)
    assert historical.intermediate_result_type == "HistoricalSpecialistRunResult"
    assert historical.final_result_type == "SpecialistResult"
    assert historical.finalization_entrypoint == "finalize_historical_specialist_run"
    assert decision.target_required_before_request is True
    assert decision.direct_result_type == "DecisionSpecialistRunResult"
    assert NATIVE_RESULT_TYPES[NativeResultStage.LIVE_OPS_RUN] is LiveOpsRunResult
    assert NATIVE_RESULT_TYPES[NativeResultStage.HISTORICAL_INTERMEDIATE] is HistoricalSpecialistRunResult
    assert NATIVE_RESULT_TYPES[NativeResultStage.HISTORICAL_FINAL] is SpecialistResult
    assert NATIVE_RESULT_TYPES[NativeResultStage.DECISION_RUN] is DecisionSpecialistRunResult
    assert _dataclass_fields(AI_DIR / "decision_tools.py", "DecisionToolsRuntime") == DECISION_TOOLS_RUNTIME_FIELDS


def test_historical_migration_and_decision_gate_stay_unresolved():
    by_name = {item.contract_name: item for item in HISTORICAL_CONTRACT_MIGRATION}
    baseline = {item.contract_name: item.missing_fields for item in historical_native_result_correlation_gaps()}
    assert set(by_name) == set(baseline)
    for name, missing in baseline.items():
        assert by_name[name].missing_fields == missing
        assert by_name[name].source_version_owner == "SourceRecord"
        assert by_name[name].query_id_owner == "QueryExecutionTrace"
        assert by_name[name].native_type_changed is False
    for contract_name in ("HistoricalSpecialistRunResult", "SpecialistResult"):
        missing = by_name[contract_name].missing_fields
        for field_name in (
            "master_request_id",
            "specialist_request_id",
            "correlation_id",
            "source_version",
            "query_id",
        ):
            assert field_name in missing
    assert "correlation_id" not in by_name["ToolResult"].missing_fields
    assert "source_version" in by_name["ToolResult"].missing_fields
    assert "query_id" in by_name["ToolResult"].missing_fields
    assert "QueryExecutionTrace" not in by_name
    assert "SourceRecord" not in by_name
    mismatches = {item.contract_name: item for item in NATIVE_IDENTITY_MISMATCHES}
    assert "correlation_id" in mismatches["LiveOpsRunResult"].present_host_fields
    assert mismatches["LiveOpsRunResult"].missing_host_fields
    assert mismatches["HistoricalSpecialistRunResult"].present_host_fields == ()
    assert mismatches["DecisionSpecialistRunResult"].present_host_fields == ()
    assert mismatches["DecisionToolsRuntime"].inspected is False
    assert "correlation_id" in mismatches["DecisionToolsRuntime"].present_host_fields
    assert DECISION_TARGET_GATE.native_request_constructed is False
    assert DECISION_TARGET_GATE.proofs_satisfied == ()
    assert DECISION_TARGET_GATE.required_proofs == tuple(item.value for item in REQUIRED_HOST_PROOFS)
    assert DECISION_TARGET_GATE.proof_issuer == "unavailable"
    assert DECISION_TARGET_GATE.comparison_fields_are_targets is False
    assert "MasterInvocationAuthorization.expected_aircraft_id" in DECISION_COMPARISON_FIELDS
    with pytest.raises(ContractValidationError, match="specialist_artifact_mismatch"):
        bind_native_specialist_execution_result(
            _identity(),
            NativeResultStage.LIVE_OPS_RUN,
            LiveOpsRunStatus.COMPLETED,
            observed_result=object(),
        )
    with pytest.raises(ContractValidationError, match="serialized_record_not_native_result"):
        bind_native_specialist_execution_result(
            _identity(),
            NativeResultStage.HISTORICAL_INTERMEDIATE,
            SpecialistRunStatus.UNSUPPORTED,
            observed_result={"status": "UNSUPPORTED"},
        )


def test_interface_note_matches_the_frozen_contract():
    text = INTERFACE.read_text(encoding="utf-8")
    for required in (
        "wilvor.ai.master_specialist_runtime_contract.v1",
        "Do not import `wilvor_ai.master_specialist_runtime_contract`",
        "LiveOpsSpecialist.run",
        "HistoricalAnalyticsSpecialist.run",
        "DecisionSpecialist.run",
        "LiveOpsRunResult",
        "HistoricalSpecialistRunResult",
        "SpecialistResult",
        "DecisionSpecialistRunResult",
        "DecisionToolsRuntime",
        "SOURCE_CURRENTNESS",
        "ALLOWED_TARGET_POPULATION",
        "SEPARATE_PERSISTED_RECOMMENDATION_AUTHORIZATION",
        "SourceRecord",
        "QueryExecutionTrace",
        "before constructing `DecisionSpecialistRequest`",
        "GET_ITEM",
        "QUERY",
        "MAX_ROUTE_STEPS=4",
        "MAX_DECISION_STEPS=1",
    ):
        assert required in text


def test_environment_test_mode_does_not_open_authority(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("WILVOR_TEST_MODE", "1")
    identity = _identity()
    assert identity.production_authority is MasterProductionAuthority.DENIED
    observed = observe_authorization_proof({"ENVIRONMENT": "test"}, identity)
    assert isinstance(observed, AuthorizationProofObservation)
    assert observed.proof_kind is FalseProofKind.ENVIRONMENT_CLAIM
    assert PRODUCTION_AUTHORITY_AVAILABLE is False
    with pytest.raises(ContractValidationError, match="grant_not_issued"):
        IssuedGrantReference(
            identity=identity,
            grant_ref="grant-ref-1",
            policy_revision="policy-rev-1",
            effective_scope=bind_effective_execution_scope(_envelope(identity), (TemporalScope.CURRENT,)),
            issued=True,
        )
