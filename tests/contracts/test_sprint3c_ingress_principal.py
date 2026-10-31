"""Sprint 3C ingress and trusted-principal contracts.

Forged events, direct Lambda invoke, replay, and missing AWS evidence
stay denials. A structural bind is not production authentication.
"""

from __future__ import annotations

import ast
import copy
import os
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_application_principal import (
    HOST_INGRESS_PROOF_REQUIREMENTS,
    PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE,
    IndependentlyHostAuthenticatedIdentityInput,
    PrincipalRecordKind,
    issue_production_application_principal,
    mint_nonproduction_contract_seal,
    reject_untrusted_authority,
)
from wilvor_ai.master_contracts import (
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_execution_grants import issue_production_execution_grant
from wilvor_ai.master_gateway_identity import (
    MasterGatewayIdentityConfiguration,
    issue_application_principal,
    reject_claimed_principal,
)
from wilvor_ai.master_gateway_runtime_boundary import (
    ISSUANCE_CONTROL_IDS,
    RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE,
    ConfigurationDenialReason,
    ConfigurationRestrictionDecision,
    EvidenceStatus,
    InvokePermissionStatement,
    RejectedInvocationProof,
    RuntimeEvidenceClass,
    RuntimeFactName,
    ServerOwnedGatewayManifest,
    current_disabled_route_manifest,
    expected_execution_arn,
    expected_invoke_arn,
    expected_source_arn,
    refuse_runtime_admission,
)
from wilvor_ai.master_planning_session import MasterPlanningSessionRegistry
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    PolicyAuthorityOrigin,
    TargetObligationKind,
    seal_server_policy_inputs,
)
from wilvor_ai.sprint3c_ingress_principal import (
    SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE,
    SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED,
    SPRINT3C_INGRESS_ATTESTATION_AVAILABLE,
    SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED,
    IngressMaterialClass,
    OperatorOwnedIssuanceControlManifest,
    ProductionPrincipalRecordKind,
    ReviewedIamInvocationRestriction,
    Sprint3CProductionPrincipalHandle,
    issue_sprint3c_production_ingress,
    mint_sprint3c_production_principal,
    open_sprint3c_ingress_principal_port,
    production_ai_query_route_is_disabled,
    refuse_policy_deny_as_execution_grant,
    unresolved_operator_issuance_controls,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE = (
    REPO_ROOT
    / "functions"
    / "shared"
    / "wilvor_ai"
    / "sprint3c_ingress_principal.py"
)

EVALUATION_EPOCH = 1_767_225_600
ISSUED_AT = EVALUATION_EPOCH - 30
NOT_BEFORE = EVALUATION_EPOCH - 10
EXPIRES_AT = EVALUATION_EPOCH + 300
MISLEADING_TIME_EPOCH_MS = 1_700_000_000_000
ISSUER = "https://idp.wilvor.test/realms/ops"
OTHER_ISSUER = "https://issuer.other.example/realms/ops"
AUDIENCE = "wilvor-master-ai"
OTHER_AUDIENCE = "wilvor-other-ai"
SUBJECT = "subject-sprint3c-1"
OTHER_SUBJECT = "subject-sprint3c-2"
SCOPE = "wilvor.ai.query"
OTHER_SCOPE = "wilvor.ai.other"
ROUTE_KEY = "POST /ai/query"
MASTER_REQUEST = "master-req-sprint3c-1"
OTHER_REQUEST = "master-req-other-1"
CORRELATION = "corr-sprint3c-1"
OTHER_CORRELATION = "corr-other-1"
GATEWAY_REQUEST = "gw-req-sprint3c-1"
PROOF_ID = "proof-sprint3c-1"
PROVENANCE = "wilvor-host-sprint3c-nonproduction"
APPLICATION_TIME = "2026-01-15T00:00:00Z"
ACCEPTED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
IDEMPOTENCY = "idem-sprint3c-1"
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
    return open_sprint3c_ingress_principal_port(
        _configuration() if configuration is None else configuration,
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )


def _errors(func: object) -> tuple[str, ...]:
    with pytest.raises(ContractValidationError) as caught:
        func()  # type: ignore[operator]
    return caught.value.errors


def _evaluate(port, registry, record, invocation, **overrides: object):
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
    return port.evaluate(**values)  # type: ignore[arg-type]


def test_cp1_forged_event_does_not_change_principal_ref_and_deny_is_not_a_grant():
    registry, record, _context, _plan_object, invocation = _plan()
    port = _port()
    result = _evaluate(port, registry, record, invocation)
    principal_ref = result.principal_ref
    evaluation = result.policy_evaluation
    handoff = result.agent_b_handoff()

    assert result.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert result.principal.production_principal is False
    assert result.production_principal_issued is False
    assert result.production_handle is None
    assert (
        result.intended_production_record_kind
        is ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION
    )
    assert (
        ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION.value
        != PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value
    )
    assert evaluation.decision is MasterRuntimePolicyDecision.DENY
    assert evaluation.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert evaluation.principal_ref is None
    assert Reason.PRINCIPAL_NOT_HOST_AUTHENTICATED in evaluation.denial_reasons
    assert result.grant_eligible is False
    assert handoff.grant_eligible is False
    assert handoff.principal_ref == principal_ref
    assert handoff.policy_evaluation is evaluation
    assert handoff.planning_session_id == record.session_id
    assert handoff.planning_session_id != evaluation.planning_session_id
    assert _errors(lambda: refuse_policy_deny_as_execution_grant(evaluation)) == (
        "policy_evaluation_is_not_a_permit",
    )
    assert _errors(lambda: issue_production_execution_grant(evaluation, handoff)) == (
        "production_grant_issuance_closed",
    )

    forged = _errors(
        lambda: _evaluate(
            port,
            registry,
            record,
            invocation,
            event=_event(claims={"iss": OTHER_ISSUER}),
            host_identity=_host(record, proof_id="proof-forged-issuer"),
        )
    )
    assert "invalid_issuer" in forged
    assert result.principal_ref == principal_ref
    assert result.principal.principal_ref == principal_ref


def test_original_planning_session_identity_is_preserved():
    registry, record, _context, _plan_object, invocation = _plan()
    result = _evaluate(_port(), registry, record, invocation)
    assert result.planning_session_id == record.session_id
    assert result.original_planning_session_id == record.session_id
    assert result.principal.planning_session_id == record.session_id
    assert result.handoff.planning_session_id == record.session_id
    assert result.binding.policy_session_id != record.session_id
    assert result.policy_evaluation.planning_session_id != record.session_id
    assert result.master_request_id == MASTER_REQUEST
    assert result.step_id == "live-1"
    assert result.invocation_id == invocation.invocation_id
    assert result.handoff.purpose is MasterPurpose.ANSWER
    assert result.handoff.policy_revision == POLICY_REVISION
    assert result.handoff.accepted_at_utc == ACCEPTED
    assert result.handoff.deadline_utc == DEADLINE


def test_forged_jwt_and_untrusted_materials_are_rejected_proofs():
    registry, record, _context, _plan_object, invocation = _plan()
    result = _evaluate(_port(), registry, record, invocation)
    report = result.boundary_report
    proofs = result.binding.rejected_invocation_proofs
    assert RejectedInvocationProof.FORGED_HTTP_API_EVENT in proofs
    assert RejectedInvocationProof.DIRECT_INVOKE_MIMIC in proofs
    assert RejectedInvocationProof.REQUEST_CONTEXT in proofs
    assert RejectedInvocationProof.JWT_LOOKING_MATERIAL in proofs
    assert RejectedInvocationProof.AUTHORIZATION_HEADER in proofs
    assert RejectedInvocationProof.LAMBDA_CONTEXT in proofs
    assert RejectedInvocationProof.TRUSTED_FLAG in proofs
    assert report.event_accepted_as_invocation_source is False
    assert report.invocation_attested is False
    assert result.caller_event_fields_accepted_as_attestation is False
    assert result.verified_upstream_authentication is False
    assert report.fact(RuntimeFactName.ACCOUNT).value == ACCOUNT
    assert report.fact(RuntimeFactName.ACCOUNT).value != FORGED_ACCOUNT
    assert result.iam_restriction.described_account_id == ACCOUNT
    assert result.iam_restriction.described_account_id != FORGED_ACCOUNT
    assert _DirectInvokeContext.aws_request_id != result.binding.gateway_request_id
    names = {item.name: item for item in result.classified_materials}
    assert names["http_api_event"].material_class is (
        IngressMaterialClass.CALLER_CREATED_EVENT_FIELD
    )
    assert names["http_api_event"].accepted_as_attestation is False
    assert names["server_owned_manifest"].material_class is (
        IngressMaterialClass.SERVER_OWNED_CONFIGURATION
    )
    assert names["lambda_context"].material_class is (
        IngressMaterialClass.LAMBDA_CONTEXT_OBSERVATION
    )
    assert names["trusted_flag"].material_class is (
        IngressMaterialClass.TRUSTED_FLAG_OBSERVATION
    )


def test_http_api_event_jwt_header_and_lambda_context_are_not_host_identity():
    registry, record, _context, _plan_object, invocation = _plan()
    event = _event()
    jwt_dict = {"iss": ISSUER, "aud": AUDIENCE, "sub": SUBJECT, "jwt": True}
    assert "forged_gateway_claims" in _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            host_identity=event,
        )
    )
    assert "forged_gateway_claims" in _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            host_identity=jwt_dict,
        )
    )
    assert _errors(lambda: reject_untrusted_authority("Bearer forged-header.not-a-subject")) == (
        "header_only_identity",
    )
    assert _errors(lambda: reject_claimed_principal(event)) == (
        "caller_principal_not_accepted",
    )
    assert _errors(lambda: reject_claimed_principal(record)) == (
        "planning_session_is_not_principal",
    )


def test_claimed_principal_and_direct_invoke_do_not_attest():
    registry, record, _context, _plan_object, invocation = _plan()
    assert "caller_principal_not_accepted" in _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            claimed_principal={"principal": SUBJECT, "authenticated": True},
        )
    )
    result = _evaluate(
        _port(),
        registry,
        record,
        invocation,
        lambda_context=_DirectInvokeContext(),
        trusted=True,
    )
    assert RejectedInvocationProof.LAMBDA_CONTEXT in result.binding.rejected_invocation_proofs
    assert RejectedInvocationProof.TRUSTED_FLAG in result.binding.rejected_invocation_proofs
    assert result.independent_host_authority_available is False


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("event_issuer", "invalid_issuer"),
        ("host_issuer", "issuer_mismatch"),
        ("event_audience", "invalid_audience"),
        ("host_audience", "audience_substitution"),
        ("event_scope", "missing_required_scope"),
        ("host_subject", "subject_inconsistent"),
        ("host_session", "cross_session_principal"),
        ("host_request", "confused_deputy"),
        ("host_correlation", "confused_deputy"),
        ("expired", "expired_claim"),
    ],
)
def test_issuer_audience_route_scope_and_principal_bindings_fail_closed(mutate: str, code: str):
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
    elif mutate == "event_scope":
        event = _event(scopes=[OTHER_SCOPE])
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
    else:
        raise AssertionError(mutate)
    errors = _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            event=event,
            host_identity=_host(record, **host_overrides),
        )
    )
    assert code in errors


def test_principal_replay_is_rejected():
    registry, record, _context, _plan_object, invocation = _plan()
    port = _port()
    result = _evaluate(port, registry, record, invocation)
    assert "replayed_principal" in _errors(
        lambda: _evaluate(port, registry, record, invocation)
    )
    assert _errors(lambda: port.present_again(result)) == ("replayed_principal",)
    assert result.principal.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert result.binding.consumed_record_kind is PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION


def test_synthetic_principal_is_not_production_and_issuers_stay_closed():
    os.environ["WILVOR_TEST_MODE"] = "1"
    os.environ["WILVOR_PRODUCTION_PRINCIPAL"] = "true"
    registry, record, _context, _plan_object, invocation = _plan()
    result = _evaluate(_port(), registry, record, invocation)
    principal = result.principal
    assessment = result.identity_assessment
    assert principal.production_principal is False
    assert principal.execution_permitted is False
    assert bool(principal) is False
    assert bool(result) is False
    assert result.production_principal_issued is False
    assert "production_principal_issuance_unavailable" in _errors(
        lambda: mint_sprint3c_production_principal(result, principal, _event())
    )
    assert "reviewed_host_authority_not_wired" in _errors(
        lambda: issue_production_application_principal(principal)
    )
    assert _errors(lambda: issue_application_principal(assessment)) == (
        "ingress_attestation_unresolved",
        "application_principal_not_issued",
    )
    assert _errors(lambda: Sprint3CProductionPrincipalHandle()) == (
        "production_principal_issuance_unavailable",
        "reviewed_host_authority_not_wired",
        "issuance_control_not_satisfied",
        "live_aws_fact_not_inspected",
    )
    assert "production_principal_issuance_unavailable" in _errors(
        lambda: issue_sprint3c_production_ingress(result)
    )
    assert _errors(lambda: refuse_runtime_admission(result.disabled_route_report)) == (
        "runtime_admission_not_attested",
        "production_issuance_unavailable",
    )
    with pytest.raises(ContractValidationError):
        copy.copy(result)
    assert SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED is False
    assert PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False


def test_public_policy_evaluation_remains_deny_without_host_authority():
    registry, record, _context, _plan_object, invocation = _plan()
    result = _evaluate(_port(), registry, record, invocation)
    evaluation = result.policy_evaluation
    assert evaluation.decision is MasterRuntimePolicyDecision.DENY
    assert evaluation.authority_origin is PolicyAuthorityOrigin.NOT_ESTABLISHED
    assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert result.independent_host_authority_available is False
    assert evaluation.production_principal_established is False
    assert evaluation.allows_specialist_execution() is False
    assert evaluation.permit_issued is False
    assert evaluation.grant_issued is False


def test_post_ai_query_stays_disabled_and_iam_aws_evidence_stay_unresolved():
    registry, record, _context, _plan_object, invocation = _plan()
    result = _evaluate(_port(), registry, record, invocation)
    disabled = result.disabled_route_report
    assert production_ai_query_route_is_disabled() is True
    assert result.production_route_enabled is False
    assert disabled.configuration_decision is ConfigurationRestrictionDecision.DENIED
    assert ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT in disabled.denial_reasons
    assert current_disabled_route_manifest(name_prefix=NAME_PREFIX).enable_ai_query_route is False
    iam = result.iam_restriction
    assert iam.gateway_service_principal == "apigateway.amazonaws.com"
    assert iam.invoke_action == "lambda:InvokeFunction"
    assert iam.required_route == ROUTE_KEY
    assert iam.required_source_arn_suffix == "/$default/POST/ai/query"
    assert iam.wildcard_invoke_allowed is False
    assert iam.other_principals_allowed is False
    assert iam.function_url_allowed is False
    assert iam.same_account_identity_policy_reviewed is False
    assert iam.live_resource_policy_inspected is False
    assert iam.live_identity_policy_inspected is False
    assert iam.authorization_scopes_edge_enforced is False
    assert iam.standing is EvidenceStatus.NOT_INSPECTED
    assert iam.production_invoke_attested is False
    assert bool(iam) is False
    evidence = result.aws_deployment_evidence
    assert evidence.authenticated is False
    assert evidence.live_resource_policy is EvidenceStatus.NOT_INSPECTED
    assert evidence.live_identity_policy is EvidenceStatus.NOT_INSPECTED
    assert evidence.live_deployment is EvidenceStatus.NOT_INSPECTED
    assert evidence.live_function_url is EvidenceStatus.NOT_INSPECTED
    assert evidence.evidence_class is RuntimeEvidenceClass.NOT_YET_INSPECTED
    for name in (
        RuntimeFactName.LIVE_RESOURCE_POLICY,
        RuntimeFactName.LIVE_IDENTITY_POLICY,
        RuntimeFactName.DEPLOYMENT,
        RuntimeFactName.LIVE_FUNCTION_URL,
    ):
        fact = result.boundary_report.fact(name)
        assert fact.status is EvidenceStatus.NOT_INSPECTED
        assert fact.accepted_as_invocation_source is False
    assert result.unsatisfied_issuance_control_ids == ISSUANCE_CONTROL_IDS
    assert result.unmet_host_ingress_proofs == HOST_INGRESS_PROOF_REQUIREMENTS
    assert result.host_ingress_proof_standing.value == "UNRESOLVED"
    assert SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE is False
    assert SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED is False
    assert SPRINT3C_INGRESS_ATTESTATION_AVAILABLE is False
    assert RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is False
    assert _errors(
        lambda: ReviewedIamInvocationRestriction(same_account_identity_policy_reviewed=True)
    ) == ("reviewed_host_authority_not_wired",)


def test_operator_controls_cannot_come_from_the_event_or_mark_aws_success():
    registry, record, _context, _plan_object, invocation = _plan()
    event = _event()
    assert "event_field_not_verified" in _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            operator_controls=event,
        )
    )
    controls = unresolved_operator_issuance_controls(name_prefix=NAME_PREFIX)
    assert controls.live_aws_inspected is False
    assert all(control.satisfied is False for control in controls.controls)
    assert bool(controls) is False
    assert not hasattr(OperatorOwnedIssuanceControlManifest, "from_event")
    assert not hasattr(OperatorOwnedIssuanceControlManifest, "from_dict")
    assert _errors(
        lambda: OperatorOwnedIssuanceControlManifest(
            name_prefix=NAME_PREFIX,
            controls=controls.controls,
            iam_restriction=controls.iam_restriction,
            aws_deployment_evidence=controls.aws_deployment_evidence,
            live_aws_inspected=True,
        )
    ) == ("live_aws_fact_not_inspected",)
    with pytest.raises(ContractValidationError):
        type(controls.controls[0])(
            controls.controls[0].control_id,
            True,
            True,
        )


def test_disabled_route_manifest_cannot_satisfy_post_ai_query_identity():
    registry, record, _context, _plan_object, invocation = _plan()
    disabled = current_disabled_route_manifest(name_prefix=NAME_PREFIX)
    errors = _errors(
        lambda: _evaluate(
            _port(),
            registry,
            record,
            invocation,
            manifest=disabled,
        )
    )
    assert "route_mismatch" in errors


def test_environment_and_seal_strings_cannot_open_the_port():
    os.environ["WILVOR_TEST_MODE"] = "1"
    os.environ["WILVOR_PRODUCTION_PRINCIPAL"] = "true"
    os.environ["WILVOR_ALLOW_SYNTHETIC_PRINCIPAL"] = "1"
    assert _errors(
        lambda: open_sprint3c_ingress_principal_port(
            _configuration(),
            authorization_provenance=PROVENANCE,
            seal="true",
        )
    ) == ("environment_not_principal_authority",)
    assert SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED is False


def test_module_does_not_import_aws_jwt_or_grant_issuers():
    source = MODULE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {
        "boto3",
        "botocore",
        "jose",
        "jwt",
        "os",
        "wilvor_ai.master_execution_grants",
        "wilvor_ai.master_execution_ledger",
        "wilvor_ai.live_ops_specialist",
        "wilvor_ai.historical_specialist",
        "wilvor_ai.decision_specialist",
    }
    assert forbidden.isdisjoint(imported)
    assert "os.environ" not in source
    assert "jwt.decode" not in source
    assert "boto3" not in source
    assert "_evaluate_hypothetical_runtime_policy" not in source
    assert "enable_ai_query_route = True" not in source
    defined = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    assert "from_event" not in defined
    assert "decode_jwt" not in defined
    mint = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "mint_sprint3c_production_principal":
            mint = node
            break
    assert mint is not None
    assert any(isinstance(item, ast.Raise) or isinstance(item, ast.Expr) for item in mint.body)
    assert "issue_production_application_principal" in ast.dump(mint)
