"""Cross-module ingress identity, principal, and policy seam.

The sequence calls the existing modules. A NONPRODUCTION record is not a
permit, and the production evaluators still deny.
"""

from __future__ import annotations

import copy
import os
from dataclasses import replace
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_application_principal import (
    HOST_INGRESS_PROOF_REQUIREMENTS,
    PRODUCTION_PRINCIPAL_AVAILABLE,
    PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE,
    TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE,
    IndependentlyHostAuthenticatedIdentityInput,
    PrincipalRecordKind,
    issue_production_application_principal,
    mint_nonproduction_contract_seal,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import issue_production_execution_grant
from wilvor_ai.master_gateway_identity import (
    MasterGatewayIdentityConfiguration,
    issue_application_principal,
    promote_to_execution_permission,
)
from wilvor_ai.master_gateway_runtime_boundary import (
    DEPLOYMENT_REVIEW_CHECKLIST,
    ISSUANCE_CONTROL_IDS,
    RUNTIME_ADMISSION_ATTESTATION_AVAILABLE,
    RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE,
    ConfigurationDenialReason,
    ConfigurationRestrictionDecision,
    EvidenceStatus,
    InvokePermissionStatement,
    RejectedInvocationProof,
    RuntimeBoundaryLimitation,
    RuntimeEvidenceClass,
    RuntimeFactName,
    ServerOwnedGatewayManifest,
    assess_gateway_runtime_boundary,
    current_disabled_route_manifest,
    expected_execution_arn,
    expected_invoke_arn,
    expected_source_arn,
    refuse_runtime_admission,
)
from wilvor_ai.master_host_ingress_policy_integration import (
    NONPRODUCTION_BINDING_REDEEMABLE,
    PRODUCTION_INGRESS_INTEGRATION_AVAILABLE,
    IngressBindingDisposition,
    NonproductionIdentityPolicyBinding,
    issue_production_ingress_policy,
    open_nonproduction_ingress_policy_port,
)
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    HOST_GRANT_ISSUER_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    MasterRuntimePolicyEnvelope,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    _evaluate_hypothetical_runtime_policy,
    promote_policy_evaluation_to_permit,
    seal_server_policy_inputs,
)
from wilvor_ai.master_specialist_runtime_contract import PRODUCTION_HOST
from wilvor_ai.master_trusted_read_executor import (
    issue_production_read_invocation,
    open_production_executor,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
API_TF = REPO_ROOT / "modules" / "operational_api" / "api.tf"

EVALUATION_EPOCH = 1_767_225_600
ISSUED_AT = EVALUATION_EPOCH - 30
NOT_BEFORE = EVALUATION_EPOCH - 10
EXPIRES_AT = EVALUATION_EPOCH + 300
MISLEADING_TIME_EPOCH_MS = 1_700_000_000_000
ISSUER = "https://idp.wilvor.test/realms/ops"
OTHER_ISSUER = "https://issuer.other.example/realms/ops"
AUDIENCE = "wilvor-master-ai"
OTHER_AUDIENCE = "wilvor-other-ai"
SUBJECT = "subject-ingress-1"
OTHER_SUBJECT = "subject-ingress-2"
SCOPE = "wilvor.ai.query"
OTHER_SCOPE = "wilvor.ai.other"
EXTRA_SCOPE = "wilvor.ai.extra"
ROUTE_KEY = "POST /ai/query"
OTHER_ROUTE = "POST /ai/other"
MASTER_REQUEST = "master-req-ingress-1"
OTHER_REQUEST = "master-req-other-1"
CORRELATION = "corr-ingress-1"
OTHER_CORRELATION = "corr-other-1"
GATEWAY_REQUEST = "gw-req-ingress-1"
PROOF_ID = "proof-ingress-1"
PROVENANCE = "wilvor-host-ingress-nonproduction"
APPLICATION_TIME = "2026-01-15T00:00:00Z"
OTHER_APPLICATION_TIME = "2026-01-14T00:00:00Z"
ACCEPTED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
STALE_ACCEPTED = "2026-01-14T00:00:00Z"
IDEMPOTENCY = "idem-ingress-1"
TEXT = "Which aircraft are currently affected by hazards?"
ACCOUNT = "123456789012"
FORGED_ACCOUNT = "999999999999"
API_ID = "abc123def0"
REGION = "us-west-1"
PARTITION = "aws"
NAME_PREFIX = "wilvor-dev"
FUNCTION_ARN = f"arn:{PARTITION}:lambda:{REGION}:{ACCOUNT}:function:wilvor-ai-query"
LOG_GROUP = "/aws/lambda/wilvor-ai-query"
Reason = MasterRuntimePolicyDenialReason


class _DirectInvokeContext:
    """Stand-in for a Lambda context object. Not an invoke attestation."""

    function_name = "wilvor-ai-query"
    aws_request_id = "direct-invoke-not-gateway"
    invoked_function_arn = FUNCTION_ARN


def _configuration() -> MasterGatewayIdentityConfiguration:
    return MasterGatewayIdentityConfiguration(
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scopes=(SCOPE,),
        method="POST",
        raw_path="/ai/query",
        route_key=ROUTE_KEY,
    )


def _manifest() -> ServerOwnedGatewayManifest:
    source = expected_source_arn(
        expected_execution_arn(PARTITION, REGION, ACCOUNT, API_ID),
        "$default",
    )
    return ServerOwnedGatewayManifest(
        enable_ai_query_route=True,
        name_prefix=NAME_PREFIX,
        partition=PARTITION,
        region=REGION,
        account_id=ACCOUNT,
        api_id=API_ID,
        stage_name="$default",
        route_key=ROUTE_KEY,
        issuer=ISSUER,
        audiences=(AUDIENCE,),
        required_scopes=(SCOPE,),
        authorizer_issuer=ISSUER,
        authorizer_audiences=(AUDIENCE,),
        authorizer_type="JWT",
        authorization_type="JWT",
        identity_source="$request.header.Authorization",
        function_arn=FUNCTION_ARN,
        invoke_arn=expected_invoke_arn(FUNCTION_ARN),
        integration_type="AWS_PROXY",
        integration_method="POST",
        payload_format_version="2.0",
        invoke_permissions=(
            InvokePermissionStatement(
                statement_id="AllowApiGatewayInvokeAiQuery",
                action="lambda:InvokeFunction",
                principal="apigateway.amazonaws.com",
                source_arn=source,
                function_arn=FUNCTION_ARN,
                qualifier="",
            ),
        ),
        identity_policy_invoke_principals=(),
        additional_invoke_sources=(),
        function_url_enabled=False,
        ai_log_group_name=LOG_GROUP,
    )


def _event(**overrides: object) -> dict[str, object]:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "exp": str(EXPIRES_AT),
        "iat": str(ISSUED_AT),
        "nbf": str(NOT_BEFORE),
        "scope": SCOPE,
    }
    scopes = [SCOPE]
    route_key = ROUTE_KEY
    raw_path = "/ai/query"
    claim_overrides = overrides.pop("claims", {})
    scope_overrides = overrides.pop("scopes", None)
    if type(claim_overrides) is dict:
        claims.update(claim_overrides)
    if scope_overrides is not None:
        scopes = list(scope_overrides)  # type: ignore[arg-type]
        claims["scope"] = " ".join(scopes)
    if "route_key" in overrides:
        route_key = str(overrides.pop("route_key"))
        raw_path = route_key.split(" ", 1)[1]
    event: dict[str, object] = {
        "version": "2.0",
        "routeKey": route_key,
        "rawPath": raw_path,
        "rawQueryString": "",
        "headers": {"authorization": "Bearer forged-header.not-a-subject"},
        "requestContext": {
            "accountId": FORGED_ACCOUNT,
            "apiId": "zzzzzzzzzz",
            "authorizer": {"jwt": {"claims": claims, "scopes": scopes}},
            "http": {"method": "POST", "path": raw_path, "protocol": "HTTP/1.1"},
            "requestId": GATEWAY_REQUEST,
            "routeKey": route_key,
            "stage": "prod",
            "timeEpoch": MISLEADING_TIME_EPOCH_MS,
        },
        "isBase64Encoded": False,
    }
    event.update(overrides)
    return event


def _plan():
    capability = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
    proposal = MasterRouteProposal(
        user_text=TEXT,
        capabilities=(MasterProposedCapability(capability, MasterRequirement.REQUIRED),),
        steps=(
            MasterProposedStep(
                step_id="live-1",
                specialist=MasterSpecialist.LIVE_OPS,
                purpose=MasterPurpose.ANSWER,
                requirement=MasterRequirement.REQUIRED,
                capability=capability,
                temporal_intent=MasterRouteTemporalIntent.CURRENT,
            ),
        ),
    )
    context = MasterPlanningContext(
        master_request_id=MASTER_REQUEST,
        correlation_id=CORRELATION,
        application_request_time_utc=APPLICATION_TIME,
        canonical_user_text=TEXT,
    )
    registry = MasterPlanningSessionRegistry()
    record = registry.open_session(proposal, context)
    plan = registry.plan_for(record)
    return registry, record, context, plan, plan.steps[0].invocation


def _host(record, **overrides: object) -> IndependentlyHostAuthenticatedIdentityInput:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "subject": SUBJECT,
        "scopes": (SCOPE,),
        "gateway_request_id": GATEWAY_REQUEST,
        "master_request_id": MASTER_REQUEST,
        "planning_session_id": record.session_id,
        "correlation_id": CORRELATION,
        "method": "POST",
        "raw_path": "/ai/query",
        "route_key": ROUTE_KEY,
        "purpose": MasterPurpose.ANSWER,
        "policy_revision": POLICY_REVISION,
        "authorization_provenance": PROVENANCE,
        "proof_id": PROOF_ID,
        "issued_at_epoch": ISSUED_AT,
        "not_before_epoch": NOT_BEFORE,
        "expires_at_epoch": EXPIRES_AT,
        "application_request_time_utc": APPLICATION_TIME,
    }
    values.update(overrides)
    return IndependentlyHostAuthenticatedIdentityInput(**values)  # type: ignore[arg-type]


def _server(**overrides: object):
    values: dict[str, object] = {
        "evaluation_utc": ACCEPTED,
        "accepted_at_utc": ACCEPTED,
        "deadline_utc": DEADLINE,
        "executions_used": 0,
        "model_turns_used": 0,
        "tool_calls_used": 0,
        "cancelled": False,
        "region_codes": frozenset(),
        "tool_name": "get_aircraft_operational_context",
        "resource_id": "aircraft",
        "operation": ReadOperation.GET_ITEM,
        "arguments": (("aircraft_id", "N123"),),
        "target_kind": TargetObligationKind.NOT_APPLICABLE,
        "target_id": None,
        "idempotency_key": IDEMPOTENCY,
    }
    values.update(overrides)
    return seal_server_policy_inputs(**values)  # type: ignore[arg-type]


def _port(configuration: MasterGatewayIdentityConfiguration | None = None):
    return open_nonproduction_ingress_policy_port(
        _configuration() if configuration is None else configuration,
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )


def _errors(func: object) -> tuple[str, ...]:
    with pytest.raises(ContractValidationError) as caught:
        func()  # type: ignore[operator]
    return caught.value.errors


def _bind(port, registry, record, invocation, **overrides: object):
    values: dict[str, object] = {
        "manifest": _manifest(),
        "event": _event(),
        "host_identity": _host(record),
        "registry": registry,
        "planning_record": record,
        "invocation": invocation,
        "server": _server(),
        "evaluation_epoch": EVALUATION_EPOCH,
        "lambda_context": _DirectInvokeContext(),
        "trusted": True,
    }
    values.update(overrides)
    return port.bind(**values)  # type: ignore[arg-type]


def test_genuine_sequence_binds_identity_to_a_nonredeemable_deny():
    os.environ["WILVOR_TEST_MODE"] = "1"
    os.environ["WILVOR_PRODUCTION_PRINCIPAL"] = "true"
    os.environ["WILVOR_ALLOW_SYNTHETIC_PRINCIPAL"] = "1"
    registry, record, _context, plan, invocation = _plan()
    binding = _bind(_port(), registry, record, invocation)
    report = binding.boundary_report
    assessment = binding.identity_assessment
    evaluation = binding.policy_evaluation
    handoff = binding.handoff_record()

    assert binding.disposition is IngressBindingDisposition.NONPRODUCTION
    assert binding.redeemable is False
    assert binding.production_activation_available is False
    assert handoff["disposition"] == "NONPRODUCTION"
    assert handoff["redeemable"] is False
    assert "authorized" not in handoff
    assert binding.consumed_record_kind is PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION
    assert binding.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert binding.principal.production_principal is False
    assert binding.observation.record_kind is PrincipalRecordKind.DESCRIPTIVE_OBSERVATION
    assert binding.observation.structural_assessment_accepted_as_proof is False
    assert binding.observation.subject == SUBJECT
    assert "Bearer" not in repr(binding.observation)
    assert "forged-header" not in repr(binding.observation)
    assert assessment.raw_authorization.payload_decoded is False
    assert assessment.raw_authorization.accepted_as_authentication is False
    assert assessment.evaluated_at_epoch == EVALUATION_EPOCH
    assert assessment.evaluated_at_epoch * 1000 != MISLEADING_TIME_EPOCH_MS
    assert assessment.invocation_provenance.attested is False
    assert assessment.ingress_attestation.lambda_invocation_boundary_reviewed is False
    assert assessment.production_authenticated is False
    assert report.configuration_decision is ConfigurationRestrictionDecision.RESTRICTED
    assert report.invocation_standing.value == "NOT_ATTESTED"
    assert report.event_accepted_as_invocation_source is False
    assert report.established_envelope_fields == ()
    assert report.fact(RuntimeFactName.ACCOUNT).value == ACCOUNT
    assert report.fact(RuntimeFactName.ACCOUNT).value != FORGED_ACCOUNT
    assert report.fact(RuntimeFactName.STAGE).value == "$default"
    assert report.fact(RuntimeFactName.EVENT_FIELDS).evidence_class is (
        RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD
    )
    assert report.fact(RuntimeFactName.LAMBDA_CONTEXT).status is EvidenceStatus.UNTRUSTED
    assert report.fact(RuntimeFactName.TRUSTED_FLAG).status is EvidenceStatus.UNTRUSTED
    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.DIRECT_INVOKE_MIMIC in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.REQUEST_CONTEXT in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.LAMBDA_CONTEXT in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.TRUSTED_FLAG in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.JWT_LOOKING_MATERIAL in binding.rejected_invocation_proofs
    assert RejectedInvocationProof.AUTHORIZATION_HEADER in binding.rejected_invocation_proofs
    assert _DirectInvokeContext.aws_request_id != binding.gateway_request_id
    assert binding.planning_session_id == record.session_id
    assert binding.policy_session_id == binding.policy_session.session_id
    assert binding.planning_session_id != binding.policy_session_id
    assert binding.session_identifier_equality_is_proof is False
    assert binding.membership_is_authentication is False
    assert binding.membership_established is True
    assert binding.policy_session.route_plan is plan
    assert binding.original_step_id == "live-1"
    assert binding.evaluated_step_id == "live-1"
    assert binding.master_request_id == MASTER_REQUEST
    assert binding.correlation_id == CORRELATION
    assert binding.invocation_id == invocation.invocation_id
    assert binding.specialist_request_id == invocation.specialist_request_id
    assert binding.policy_revision == POLICY_REVISION
    assert binding.policy_decision is MasterRuntimePolicyDecision.DENY
    assert binding.policy_denial_reasons == (Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED,)
    assert binding.policy_authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert evaluation.principal_ref is None
    assert evaluation.production_principal_established is False
    assert binding.policy_fingerprint == evaluation.policy_fingerprint
    assert len(binding.policy_fingerprint) == 64
    assert binding.allows_specialist_execution() is False
    assert binding.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert binding.production_authority is MasterProductionAuthority.DENIED
    assert binding.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert binding.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert binding.identity_evaluated_at_epoch != 0
    assert binding.policy_evaluation_utc == ACCEPTED
    assert binding.application_request_time_utc == APPLICATION_TIME
    with pytest.raises(ContractValidationError) as caught:
        bool(binding)
    assert caught.value.errors == ("nonproduction_binding_not_redeemable",)
    assert _errors(binding.redeem) == ("nonproduction_binding_not_redeemable",)
    with pytest.raises(ContractValidationError):
        copy.copy(binding)
    assert PRODUCTION_INGRESS_INTEGRATION_AVAILABLE is False
    assert NONPRODUCTION_BINDING_REDEEMABLE is False
    assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert HOST_GRANT_ISSUER_AVAILABLE is False


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("event_issuer", "invalid_issuer"),
        ("host_issuer", "issuer_mismatch"),
        ("event_audience", "invalid_audience"),
        ("host_audience", "audience_substitution"),
        ("event_route", "unexpected_route"),
        ("host_route", "route_mismatch"),
        ("event_scope", "missing_required_scope"),
        ("host_scope", "missing_required_scope"),
        ("host_extra_scope", "scope_mismatch"),
        ("host_subject", "subject_inconsistent"),
        ("host_session", "cross_session_principal"),
        ("host_request", "confused_deputy"),
        ("host_correlation", "confused_deputy"),
        ("expired", "expired_claim"),
        ("not_yet_valid", "claim_not_yet_valid"),
        ("application_clock", "invalid_application_request_time"),
        ("policy_revision", "policy_revision_mismatch"),
    ],
)
def test_wrong_identity_binding_fails_closed(mutate: str, code: str):
    registry, record, _context, _plan_object, invocation = _plan()
    event = _event()
    host_overrides: dict[str, object] = {}
    if mutate == "event_issuer":
        event = _event(claims={"iss": OTHER_ISSUER})
    elif mutate == "host_issuer":
        host_overrides["issuer"] = OTHER_ISSUER
    elif mutate == "event_audience":
        event = _event(claims={"aud": OTHER_AUDIENCE})
    elif mutate == "host_audience":
        host_overrides["audience"] = OTHER_AUDIENCE
    elif mutate == "event_route":
        event = _event(route_key=OTHER_ROUTE)
    elif mutate == "host_route":
        host_overrides["method"] = "POST"
        host_overrides["raw_path"] = "/ai/other"
        host_overrides["route_key"] = OTHER_ROUTE
    elif mutate == "event_scope":
        event = _event(scopes=[OTHER_SCOPE])
    elif mutate == "host_scope":
        host_overrides["scopes"] = (OTHER_SCOPE,)
    elif mutate == "host_extra_scope":
        host_overrides["scopes"] = (SCOPE, EXTRA_SCOPE)
    elif mutate == "host_subject":
        host_overrides["subject"] = OTHER_SUBJECT
    elif mutate == "host_session":
        host_overrides["planning_session_id"] = "session-foreign-1"
    elif mutate == "host_request":
        host_overrides["master_request_id"] = OTHER_REQUEST
    elif mutate == "host_correlation":
        host_overrides["correlation_id"] = OTHER_CORRELATION
    elif mutate == "expired":
        event = _event(claims={"exp": str(EVALUATION_EPOCH)})
    elif mutate == "not_yet_valid":
        event = _event(claims={"nbf": str(EVALUATION_EPOCH + 50)})
    elif mutate == "application_clock":
        host_overrides["application_request_time_utc"] = OTHER_APPLICATION_TIME
    elif mutate == "policy_revision":
        host_overrides["policy_revision"] = "policy-other-rev"
    else:
        raise AssertionError(mutate)
    errors = _errors(
        lambda: _bind(
            _port(),
            registry,
            record,
            invocation,
            event=event,
            host_identity=_host(record, **host_overrides),
        )
    )
    assert code in errors


def test_swapped_step_session_correlation_and_clocks_deny():
    registry, record, _context, plan, invocation = _plan()
    copied_step = replace(plan.steps[0])
    step_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        policy_step=copied_step,
    )
    assert step_binding.policy_decision is MasterRuntimePolicyDecision.DENY
    assert step_binding.membership_established is False
    assert step_binding.original_step_id == step_binding.evaluated_step_id
    assert Reason.STEP_NOT_MEMBER in step_binding.policy_denial_reasons
    assert Reason.STRING_IDENTITY_NOT_MEMBERSHIP in step_binding.policy_denial_reasons
    assert Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED in step_binding.policy_denial_reasons
    assert step_binding.redeemable is False

    registry, record, _context, _plan_object, invocation = _plan()
    session_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        envelope_overrides={"planning_session_id": record.session_id},
    )
    assert Reason.CROSS_SESSION in session_binding.policy_denial_reasons
    assert session_binding.membership_established is False
    assert session_binding.policy_session_id != record.session_id
    assert session_binding.policy_decision is MasterRuntimePolicyDecision.DENY

    registry, record, _context, _plan_object, invocation = _plan()
    correlation_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        envelope_overrides={"correlation_id": OTHER_CORRELATION},
    )
    assert Reason.STRING_IDENTITY_NOT_MEMBERSHIP in correlation_binding.policy_denial_reasons
    assert correlation_binding.membership_established is False

    registry, record, _context, _plan_object, invocation = _plan()
    request_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        envelope_overrides={"master_request_id": OTHER_REQUEST},
    )
    assert Reason.STRING_IDENTITY_NOT_MEMBERSHIP in request_binding.policy_denial_reasons
    assert request_binding.master_request_id == MASTER_REQUEST

    registry, record, _context, _plan_object, invocation = _plan()
    expired_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        server=_server(deadline_utc=ACCEPTED),
    )
    assert Reason.DEADLINE_EXPIRED in expired_binding.policy_denial_reasons
    assert expired_binding.policy_decision is MasterRuntimePolicyDecision.DENY

    registry, record, _context, _plan_object, invocation = _plan()
    clock_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        envelope_overrides={"accepted_at_utc": STALE_ACCEPTED},
    )
    assert Reason.CALLER_CLOCK_NOT_AUTHORITY in clock_binding.policy_denial_reasons
    assert clock_binding.accepted_at_utc == ACCEPTED

    registry, record, _context, _plan_object, invocation = _plan()
    stale_binding = _bind(
        _port(),
        registry,
        record,
        invocation,
        server=_server(accepted_at_utc=STALE_ACCEPTED),
    )
    assert Reason.STALE_OR_WRONG_CLOCK in stale_binding.policy_denial_reasons
    assert stale_binding.policy_decision is MasterRuntimePolicyDecision.DENY


def test_forged_event_cannot_be_supplied_as_the_host_identity():
    registry, record, _context, _plan_object, invocation = _plan()
    event = _event()
    errors = _errors(
        lambda: _bind(
            _port(),
            registry,
            record,
            invocation,
            event=event,
            host_identity=event,
            lambda_context=_DirectInvokeContext(),
            trusted=True,
        )
    )
    assert "caller_built_identity_not_host_issued" in errors
    report = assess_gateway_runtime_boundary(
        _manifest(),
        event,
        lambda_context=_DirectInvokeContext(),
        trusted=True,
    )
    assert report.event_accepted_as_invocation_source is False
    assert report.invocation_attested is False
    assert report.allows_production_issuance() is False
    assert RejectedInvocationProof.DIRECT_INVOKE_MIMIC in report.rejected_proofs
    assert RejectedInvocationProof.LAMBDA_CONTEXT in report.rejected_proofs
    assert _DirectInvokeContext.aws_request_id not in report.established_envelope_fields


def test_duplicate_synthetic_admission_and_presentation_are_rejected():
    registry, record, _context, _plan_object, invocation = _plan()
    port = _port()
    binding = _bind(port, registry, record, invocation)
    errors = _errors(lambda: _bind(port, registry, record, invocation))
    assert "replayed_principal" in errors
    errors = _errors(lambda: port.present_again(binding))
    assert errors == ("replayed_principal",)
    assert binding.consumed_record_kind is PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION
    assert binding.allows_specialist_execution() is False


def test_hypothetical_allow_and_policy_strings_are_not_permits():
    registry, record, _context, _plan_object, invocation = _plan()
    binding = _bind(_port(), registry, record, invocation, trusted=None)
    session = binding.policy_session
    step = session.route_plan.steps[0]
    evaluated = step.invocation
    server = binding.server_inputs
    _executions, turns, calls = server_read_budget(evaluated.specialist)
    envelope = MasterRuntimePolicyEnvelope(
        master_request_id=binding.master_request_id,
        planning_session_id=session.session_id,
        step_id=step.step_id,
        invocation_id=evaluated.invocation_id,
        specialist=evaluated.specialist,
        purpose=evaluated.purpose,
        capability=step.capability,
        principal_ref=binding.synthetic_principal_ref,
        policy_revision=POLICY_REVISION,
        resource_scope=server.resource_id,
        region_scope=None,
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=server.accepted_at_utc,
        deadline_utc=server.deadline_utc,
        idempotency_key=server.idempotency_key,
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=binding.correlation_id,
    )
    allowed = _evaluate_hypothetical_runtime_policy(
        session,
        session.route_plan,
        step,
        evaluated,
        server,
        envelope,
        principal_ref=binding.synthetic_principal_ref,
    )
    assert allowed.decision is MasterRuntimePolicyDecision.ALLOW
    assert allowed.authority_origin is PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST
    assert allowed.production_principal_established is False
    assert allowed.allows_specialist_execution() is False
    assert allowed.permit_issued is False
    assert allowed.grant_issued is False
    with pytest.raises(ContractValidationError):
        bool(allowed)
    assert _errors(lambda: promote_policy_evaluation_to_permit(allowed)) == (
        "policy_evaluation_is_not_a_permit",
    )
    assert binding.policy_decision is MasterRuntimePolicyDecision.DENY
    assert binding.policy_evaluation is not allowed

    registry, record, _context, _plan_object, invocation = _plan()
    spoofed = _bind(
        _port(),
        registry,
        record,
        invocation,
        envelope_overrides={
            "authorized": True,
            "caller_policy_decision": "ALLOW",
            "claimed_test_mode": True,
        },
    )
    assert spoofed.policy_decision is MasterRuntimePolicyDecision.DENY
    assert Reason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY in spoofed.policy_denial_reasons
    assert Reason.CALLER_PROOF_NOT_AUTHORITY in spoofed.policy_denial_reasons
    assert Reason.CLAIMED_TEST_MODE_NOT_AUTHORITY in spoofed.policy_denial_reasons
    assert Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED in spoofed.policy_denial_reasons
    assert spoofed.redeemable is False
    assert _errors(lambda: promote_policy_evaluation_to_permit(spoofed.policy_evaluation)) == (
        "policy_evaluation_is_not_a_permit",
    )


def test_route_scope_is_not_edge_enforced_and_invoke_boundary_is_unresolved():
    registry, record, _context, _plan_object, invocation = _plan()
    binding = _bind(_port(), registry, record, invocation)
    report = binding.boundary_report
    scope = report.fact(RuntimeFactName.SCOPE)
    assert scope.value == SCOPE
    assert scope.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION
    assert scope.status is EvidenceStatus.VERIFIED
    assert (
        RuntimeBoundaryLimitation.SCOPES_ARE_NOT_EDGE_ENFORCED_BY_CURRENT_TERRAFORM
        in report.limitations
    )
    assert binding.scope_edge_enforced is False
    assert binding.aws_invoke_boundary_resolved is False
    for name in (
        RuntimeFactName.LIVE_RESOURCE_POLICY,
        RuntimeFactName.LIVE_IDENTITY_POLICY,
        RuntimeFactName.DEPLOYMENT,
        RuntimeFactName.LIVE_FUNCTION_URL,
        RuntimeFactName.LIVE_QUALIFIER,
        RuntimeFactName.LIVE_LOGS,
    ):
        fact = report.fact(name)
        assert fact.evidence_class is RuntimeEvidenceClass.NOT_YET_INSPECTED
        assert fact.status is EvidenceStatus.NOT_INSPECTED
        assert fact.accepted_as_invocation_source is False
    assert binding.unsatisfied_issuance_control_ids == ISSUANCE_CONTROL_IDS
    assert all(control.satisfied is False for control in report.issuance_controls)
    assert all(control.required_before_issuance is True for control in report.issuance_controls)
    assert binding.unmet_host_ingress_proofs == HOST_INGRESS_PROOF_REQUIREMENTS
    assert binding.deployment_review_checklist == DEPLOYMENT_REVIEW_CHECKLIST
    assert binding.host_ingress_proof_standing.value == "UNRESOLVED"
    api = API_TF.read_text(encoding="utf-8")
    assert "authorization_scopes" not in api
    assert "enable_ai_query_route" in api

    disabled = current_disabled_route_manifest(name_prefix=NAME_PREFIX)
    disabled_report = assess_gateway_runtime_boundary(
        disabled,
        _event(),
        lambda_context=_DirectInvokeContext(),
    )
    assert disabled_report.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT in disabled_report.denial_reasons
    assert disabled_report.allows_production_issuance() is False
    assert disabled_report.fact(RuntimeFactName.LIVE_IDENTITY_POLICY).status is (
        EvidenceStatus.NOT_INSPECTED
    )
    disabled_errors = _errors(
        lambda: _bind(
            _port(),
            registry,
            record,
            invocation,
            manifest=disabled,
        )
    )
    assert "route_mismatch" in disabled_errors


def test_production_issuance_entrypoints_reject_every_candidate():
    registry, record, _context, _plan_object, invocation = _plan()
    event = _event()
    host = _host(record)
    binding = _bind(_port(), registry, record, invocation, event=event, host_identity=host)
    assessment = binding.identity_assessment
    report = binding.boundary_report
    evaluation = binding.policy_evaluation

    assert _errors(lambda: issue_production_ingress_policy(binding, event, host, True)) == (
        "production_principal_issuance_unavailable",
        "reviewed_host_authority_not_wired",
        "nonproduction_binding_not_redeemable",
    )
    assert "production_principal_issuance_unavailable" in _errors(
        lambda: issue_production_application_principal(binding, event, host, assessment)
    )
    assert "reviewed_host_authority_not_wired" in _errors(
        lambda: issue_production_application_principal(binding)
    )
    assert _errors(lambda: issue_application_principal(assessment)) == (
        "ingress_attestation_unresolved",
        "application_principal_not_issued",
    )
    assert _errors(lambda: promote_to_execution_permission(assessment)) == (
        "ingress_attestation_unresolved",
        "execution_permission_not_available",
    )
    assert _errors(lambda: refuse_runtime_admission(report)) == (
        "runtime_admission_not_attested",
        "production_issuance_unavailable",
    )
    assert _errors(lambda: promote_policy_evaluation_to_permit(evaluation)) == (
        "policy_evaluation_is_not_a_permit",
    )
    assert _errors(lambda: issue_production_execution_grant(binding, evaluation)) == (
        "production_grant_issuance_closed",
    )
    assert _errors(lambda: issue_production_read_invocation(binding)) == (
        "production_grant_issuer_not_integrated",
    )
    assert _errors(lambda: open_production_executor(binding, event)) == (
        "production_read_executor_not_enabled",
    )
    assert _errors(
        lambda: open_nonproduction_ingress_policy_port(
            _configuration(),
            authorization_provenance=PROVENANCE,
            seal="true",
        )
    ) == ("environment_not_principal_authority",)
    with pytest.raises(ContractValidationError):
        NonproductionIdentityPolicyBinding(
            disposition=IngressBindingDisposition.NONPRODUCTION,
            redeemable=False,
            production_activation_available=False,
            synthetic_principal_ref="synthetic-forged",
            principal_record_kind=PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION,
            consumed_record_kind=PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION,
            principal_provenance=binding.principal_provenance,
            policy_decision=MasterRuntimePolicyDecision.ALLOW,
            policy_denial_reasons=(),
            policy_authority_origin=PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST,
            policy_fingerprint="a" * 64,
            policy_revision=POLICY_REVISION,
            membership_established=True,
            membership_is_authentication=True,
            session_identifier_equality_is_proof=True,
            session_membership_accepted_as_proof=True,
            matching_identifier_accepted_as_proof=True,
            structural_assessment_accepted_as_proof=True,
            event_accepted_as_invocation_source=True,
            lambda_context_accepted_as_proof=True,
            authorization_header_accepted=True,
            scope_edge_enforced=True,
            aws_invoke_boundary_resolved=True,
            issuance_controls_satisfied=True,
            host_ingress_proof_standing=binding.host_ingress_proof_standing,
            planning_session_id=binding.planning_session_id,
            policy_session_id=binding.policy_session_id,
            master_request_id=MASTER_REQUEST,
            original_step_id="live-1",
            evaluated_step_id="live-1",
            invocation_id=binding.invocation_id,
            specialist_request_id=binding.specialist_request_id,
            correlation_id=CORRELATION,
            specialist=binding.specialist,
            purpose=binding.purpose,
            capability=binding.capability,
            issuer=ISSUER,
            audience=AUDIENCE,
            subject=SUBJECT,
            scopes=(SCOPE,),
            route_key=ROUTE_KEY,
            gateway_request_id=GATEWAY_REQUEST,
            proof_id=PROOF_ID,
            authorization_provenance=PROVENANCE,
            identity_evaluated_at_epoch=EVALUATION_EPOCH,
            principal_evaluated_at_epoch=EVALUATION_EPOCH,
            application_request_time_utc=APPLICATION_TIME,
            accepted_at_utc=ACCEPTED,
            deadline_utc=DEADLINE,
            policy_evaluation_utc=ACCEPTED,
            configuration_decision=ConfigurationRestrictionDecision.RESTRICTED,
            invocation_standing=binding.invocation_standing,
            rejected_invocation_proofs=(),
            execution_authority=MasterRouteExecutionAuthority.DENIED,
            production_authority=MasterProductionAuthority.DENIED,
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            request_coverage=MasterRequestCoverage.NOT_ESTABLISHED,
            permit_issued=True,
            deployment_review_checklist=DEPLOYMENT_REVIEW_CHECKLIST,
            unmet_host_ingress_proofs=HOST_INGRESS_PROOF_REQUIREMENTS,
            unsatisfied_issuance_control_ids=ISSUANCE_CONTROL_IDS,
            boundary_report=report,
            identity_assessment=assessment,
            observation=binding.observation,
            principal=binding.principal,
            policy_evaluation=evaluation,
            policy_session=binding.policy_session,
            server_inputs=binding.server_inputs,
        )
    assert PRODUCTION_HOST is None
    assert PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_PRINCIPAL_AVAILABLE is False
    assert TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE is False
    assert RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is False
    assert RUNTIME_ADMISSION_ATTESTATION_AVAILABLE is False
    assert not hasattr(IngressBindingDisposition, "PRODUCTION")
