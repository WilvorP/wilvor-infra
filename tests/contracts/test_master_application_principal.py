"""Application principal boundary. A shaped gateway event is not a principal."""

from __future__ import annotations

import copy
import os
import pickle
import threading

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_application_principal import (
    ABSENT_SOURCE_CLAIMS,
    APPLICATION_PRINCIPAL_LIMITATIONS,
    CLOCK_FIELD_MAPPING,
    HOST_INGRESS_PROOF_REQUIREMENTS,
    LAMBDA_INVOKE_BOUNDARY,
    MASTER_APPLICATION_PRINCIPAL_SCHEMA_VERSION,
    NON_PRINCIPAL_ENVELOPE_FIELDS,
    PRINCIPAL_ENVELOPE_FIELDS,
    PRODUCTION_PRINCIPAL_AVAILABLE,
    PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE,
    REJECTED_AUTHORITY_SOURCES,
    TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE,
    AbsentSourceClaim,
    ApplicationPrincipalLimitation,
    AuthorizationProvenanceStanding,
    HostIngressProofChecklist,
    HostIngressProofRequirement,
    HostProofStanding,
    IndependentlyHostAuthenticatedIdentityInput,
    NonproductionContractSeal,
    NonproductionPrincipalProofPort,
    PrincipalRecordKind,
    PrincipalScopeBinding,
    ServerOwnedPrincipalDescriptor,
    SourceClaimPresence,
    UntrustedGatewayClaimObservation,
    issue_production_application_principal,
    mint_nonproduction_contract_seal,
    observe_untrusted_gateway_claims,
    open_nonproduction_principal_proof_port,
    reject_untrusted_authority,
)
from wilvor_ai.master_contracts import (
    MasterProductionAuthority,
    MasterPurpose,
    MasterRequirement,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gateway_identity import (
    APPLICATION_OWNED_PRINCIPAL_AVAILABLE,
    MasterGatewayIdentityConfiguration,
    assess_master_gateway_identity,
    issue_application_principal,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
    MasterPlanningSessionState,
)
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
)


EVALUATION_EPOCH = 1_767_225_600
EXPIRES_AT = EVALUATION_EPOCH + 300
ISSUED_AT = EVALUATION_EPOCH - 30
NOT_BEFORE = EVALUATION_EPOCH - 10
ISSUER = "https://issuer.wilvor.example/realms/master"
OTHER_ISSUER = "https://issuer.other.example/realms/master"
AUDIENCE = "wilvor-master-ai"
OTHER_AUDIENCE = "wilvor-other-ai"
SUBJECT = "subject-user-1"
REQUEST_ID = "JKJaXmPLvHcESHA="
ROUTE_KEY = "POST /ai"
REQUIRED_SCOPE = "ai.master.invoke"
MASTER_REQUEST = "master-req-1"
SESSION = "session-1"
CORRELATION = "corr-1"
POLICY_REVISION = "policy-2026-10-27"
PROVENANCE = "wilvor-host-ingress"
PROOF_ID = "proof-1"
REQUEST_TIME = "2020-01-01T00:00:00Z"
CLOSED = (
    "production_principal_issuance_unavailable",
    "reviewed_host_authority_not_wired",
)


def _config(**overrides: object) -> MasterGatewayIdentityConfiguration:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "required_scopes": (REQUIRED_SCOPE,),
        "method": "POST",
        "raw_path": "/ai",
        "route_key": ROUTE_KEY,
    }
    values.update(overrides)
    return MasterGatewayIdentityConfiguration(**values)  # type: ignore[arg-type]


def _event() -> dict[str, object]:
    return {
        "version": "2.0",
        "routeKey": ROUTE_KEY,
        "rawPath": "/ai",
        "rawQueryString": "",
        "headers": {"authorization": "Bearer forged-subject.not-decoded"},
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc123def",
            "authorizer": {
                "jwt": {
                    "claims": {
                        "iss": ISSUER,
                        "aud": AUDIENCE,
                        "sub": SUBJECT,
                        "exp": str(EXPIRES_AT),
                        "iat": str(ISSUED_AT),
                        "nbf": str(NOT_BEFORE),
                        "scope": REQUIRED_SCOPE,
                    },
                    "scopes": [REQUIRED_SCOPE],
                }
            },
            "http": {"method": "POST", "path": "/ai", "protocol": "HTTP/1.1"},
            "requestId": REQUEST_ID,
            "routeKey": ROUTE_KEY,
            "stage": "$default",
            "timeEpoch": (EVALUATION_EPOCH + 10_000) * 1000,
        },
        "isBase64Encoded": False,
    }


def _assessment():
    return assess_master_gateway_identity(
        _event(),
        _config(),
        evaluation_epoch=EVALUATION_EPOCH,
    )


def _authority(**overrides: object) -> IndependentlyHostAuthenticatedIdentityInput:
    values: dict[str, object] = {
        "issuer": ISSUER,
        "audience": AUDIENCE,
        "subject": SUBJECT,
        "scopes": (REQUIRED_SCOPE,),
        "gateway_request_id": REQUEST_ID,
        "master_request_id": MASTER_REQUEST,
        "planning_session_id": SESSION,
        "correlation_id": CORRELATION,
        "method": "POST",
        "raw_path": "/ai",
        "route_key": ROUTE_KEY,
        "purpose": MasterPurpose.DISCOVERY,
        "policy_revision": POLICY_REVISION,
        "authorization_provenance": PROVENANCE,
        "proof_id": PROOF_ID,
        "issued_at_epoch": ISSUED_AT,
        "not_before_epoch": NOT_BEFORE,
        "expires_at_epoch": EXPIRES_AT,
        "application_request_time_utc": REQUEST_TIME,
    }
    values.update(overrides)
    return IndependentlyHostAuthenticatedIdentityInput(**values)  # type: ignore[arg-type]


def _port(config: MasterGatewayIdentityConfiguration | None = None) -> NonproductionPrincipalProofPort:
    return open_nonproduction_principal_proof_port(
        _config() if config is None else config,
        policy_revision=POLICY_REVISION,
        authorization_provenance=PROVENANCE,
        seal=mint_nonproduction_contract_seal(),
    )


def _codes(func: object) -> tuple[str, ...]:
    with pytest.raises(ContractValidationError) as caught:
        func()  # type: ignore[operator]
    return caught.value.errors


def _present(port: NonproductionPrincipalProofPort, descriptor: ServerOwnedPrincipalDescriptor, **overrides: object):
    values: dict[str, object] = {
        "evaluation_epoch": EVALUATION_EPOCH,
        "planning_session_id": descriptor.planning_session_id,
        "master_request_id": descriptor.master_request_id,
        "correlation_id": descriptor.correlation_id,
        "purpose": descriptor.purpose,
        "audience": descriptor.audience,
        "route_key": descriptor.route_key,
        "acting_subject": descriptor.subject,
        "gateway_request_id": descriptor.scope.gateway_request_id,
    }
    values.update(overrides)
    return port.present(descriptor, **values)  # type: ignore[arg-type]


def _live_member() -> tuple[MasterPlanningSessionRegistry, object]:
    capability = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
    proposal = MasterRouteProposal(
        user_text="Which aircraft are currently affected by hazards?",
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
        application_request_time_utc="2026-01-15T00:00:01Z",
        canonical_user_text=proposal.user_text,
    )
    registry = MasterPlanningSessionRegistry()
    return registry, registry.open_session(proposal, context)


def test_structural_gateway_observation_stays_descriptive():
    assessment = _assessment()
    observation = observe_untrusted_gateway_claims(assessment)
    assert observation.schema_version == MASTER_APPLICATION_PRINCIPAL_SCHEMA_VERSION
    assert observation.record_kind is PrincipalRecordKind.DESCRIPTIVE_OBSERVATION
    assert observation.subject == SUBJECT
    assert observation.subject != "forged-subject"
    assert "Bearer" not in repr(observation)
    assert observation.authorization_header_present is True
    assert observation.authorization_header_accepted is False
    assert observation.structural_assessment_accepted_as_proof is False
    assert observation.provenance_standing is AuthorizationProvenanceStanding.NOT_ESTABLISHED
    assert observation.tenant_presence is SourceClaimPresence.ABSENT
    assert observation.role_presence is SourceClaimPresence.ABSENT
    assert observation.gateway_claim_evaluated_at_epoch == EVALUATION_EPOCH
    event_clock = _event()["requestContext"]["timeEpoch"]  # type: ignore[index]
    assert observation.gateway_claim_evaluated_at_epoch != event_clock
    assert bool(observation) is False
    assert observation.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert observation.limitations == APPLICATION_PRINCIPAL_LIMITATIONS
    assert _codes(lambda: observe_untrusted_gateway_claims(assessment.authorizer_claims)) == (
        "forged_gateway_claims",
    )
    assert _codes(lambda: UntrustedGatewayClaimObservation(SUBJECT)) == (  # type: ignore[call-arg]
        "observation_requires_gateway_assessment",
    )


def test_production_issuance_rejects_a_valid_structural_assessment():
    assessment = _assessment()
    observation = observe_untrusted_gateway_claims(assessment)
    authority = _authority()
    assert assessment.authorizer_claims.structurally_valid is True
    assert authority.production_principal is False
    assert bool(authority) is False
    assert _codes(lambda: issue_production_application_principal()) == CLOSED
    assert _codes(lambda: issue_production_application_principal(assessment)) == (
        *CLOSED,
        "structural_jwt_assessment_not_principal",
    )
    assert _codes(lambda: issue_production_application_principal(observation)) == (
        *CLOSED,
        "descriptive_observation_not_principal",
    )
    assert _codes(lambda: issue_production_application_principal(authority)) == (
        *CLOSED,
        "caller_built_identity_not_host_issued",
    )
    assert _codes(lambda: issue_application_principal(assessment)) == (
        "ingress_attestation_unresolved",
        "application_principal_not_issued",
    )
    assert APPLICATION_OWNED_PRINCIPAL_AVAILABLE is False
    assert PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_PRINCIPAL_AVAILABLE is False
    assert "directly invoked" in LAMBDA_INVOKE_BOUNDARY
    assert "resource policy" in LAMBDA_INVOKE_BOUNDARY


def test_forged_claims_and_header_only_identity_are_rejected():
    assessment = _assessment()
    observation = observe_untrusted_gateway_claims(assessment)
    port = _port()
    event = _event()
    forged_claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "forged-subject", "scope": REQUIRED_SCOPE}
    header = {"authorization": "Bearer forged-subject.not-decoded"}
    cases = [
        (assessment.authorizer_claims, "forged_gateway_claims"),
        (event, "forged_gateway_claims"),
        (forged_claims, "forged_gateway_claims"),
        (assessment.raw_authorization, "header_only_identity"),
        ("Bearer forged-subject.not-decoded", "header_only_identity"),
        (header, "header_only_identity"),
        (assessment.claimed_request, "claimed_request_not_a_principal"),
        ("allow", "policy_check_not_authority"),
        ("the aircraft should climb", "user_text_not_authority"),
        (True, "caller_flag_not_authority"),
        (REQUEST_ID, "principal_label_not_authority"),
    ]
    for candidate, code in cases:
        assert _codes(lambda candidate=candidate: observe_untrusted_gateway_claims(candidate)) == (code,)
        assert _codes(lambda candidate=candidate: reject_untrusted_authority(candidate)) == (code,)
        assert _codes(lambda candidate=candidate: port.admit(observation, candidate, evaluation_epoch=EVALUATION_EPOCH)) == (
            code,
        )
        assert _codes(lambda candidate=candidate: issue_production_application_principal(candidate))[:2] == CLOSED
    assert _codes(lambda: port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH, event=event)) == (
        "forged_gateway_claims",
    )
    assert _codes(
        lambda: port.admit(
            observation,
            _authority(),
            evaluation_epoch=EVALUATION_EPOCH,
            principal_label="forged-subject",
        )
    ) == ("principal_label_not_authority",)
    forged_subject = _authority(subject="forged-subject", proof_id="proof-forged")
    assert _codes(lambda: port.admit(observation, forged_subject, evaluation_epoch=EVALUATION_EPOCH)) == (
        "subject_inconsistent",
    )


def test_in_process_synthetic_admission_is_not_a_production_principal():
    assessment = _assessment()
    observation = observe_untrusted_gateway_claims(assessment)
    authority = _authority()
    port = _port()
    descriptor = port.admit(observation, authority, evaluation_epoch=EVALUATION_EPOCH)
    assert type(descriptor) is ServerOwnedPrincipalDescriptor
    assert descriptor.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert descriptor.provenance_standing is AuthorizationProvenanceStanding.CONTRACT_TEST_HOST
    assert descriptor.production_principal is False
    assert descriptor.authenticates_caller is False
    assert descriptor.environment_activated is False
    assert descriptor.session_membership_accepted_as_proof is False
    assert descriptor.matching_identifier_accepted_as_proof is False
    assert descriptor.structural_assessment_accepted_as_proof is False
    assert descriptor.execution_permitted is False
    assert bool(descriptor) is False
    assert bool(port) is False
    assert descriptor.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert descriptor.production_authority is MasterProductionAuthority.DENIED
    assert descriptor.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert descriptor.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert descriptor.subject == authority.subject == observation.subject
    assert descriptor.scopes == authority.scopes == (REQUIRED_SCOPE,)
    assert descriptor.scope.production_scope_grant is False
    assert descriptor.issuer == ISSUER
    assert descriptor.audience == AUDIENCE
    assert descriptor.route_key == ROUTE_KEY
    assert descriptor.purpose is MasterPurpose.DISCOVERY
    assert descriptor.policy_revision == POLICY_REVISION
    assert descriptor.principal_ref.startswith("synthetic-")
    assert descriptor.principal_ref != descriptor.subject
    assert descriptor.accepted_at_epoch == ISSUED_AT
    assert descriptor.expires_at_epoch == EXPIRES_AT
    assert descriptor.not_before_epoch == NOT_BEFORE
    assert descriptor.principal_evaluated_at_epoch == EVALUATION_EPOCH
    assert descriptor.gateway_claim_evaluated_at_epoch == observation.gateway_claim_evaluated_at_epoch
    assert descriptor.application_request_time_utc == REQUEST_TIME
    assert descriptor.tenant_presence is SourceClaimPresence.ABSENT
    assert descriptor.role_presence is SourceClaimPresence.ABSENT
    assert descriptor.limitations == tuple(ApplicationPrincipalLimitation)
    assert port.lifecycle(descriptor) is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    presented = _present(port, descriptor)
    assert presented is descriptor
    assert descriptor.record_kind is PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
    assert port.lifecycle(descriptor) is PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION
    assert _codes(lambda: issue_production_application_principal(descriptor, port)) == (
        *CLOSED,
        "synthetic_admission_not_production_principal",
        "test_double_not_production_issuer",
    )
    assert set(PrincipalRecordKind) == {
        PrincipalRecordKind.DESCRIPTIVE_OBSERVATION,
        PrincipalRecordKind.HOST_IDENTITY_INPUT,
        PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION,
        PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION,
    }
    assert [item.value for item in AuthorizationProvenanceStanding] == [
        "NOT_ESTABLISHED",
        "CONTRACT_TEST_HOST",
    ]
    assert [item.value for item in SourceClaimPresence] == ["ABSENT"]
    assert "deadline_utc" in NON_PRINCIPAL_ENVELOPE_FIELDS
    assert "principal_ref" in PRINCIPAL_ENVELOPE_FIELDS
    assert CLOCK_FIELD_MAPPING[1][0] == "deadline_utc"
    assert "environment variable" in REJECTED_AUTHORITY_SOURCES


def test_public_constructor_cannot_mint_a_production_principal():
    assert _codes(lambda: ServerOwnedPrincipalDescriptor(SUBJECT)) == (  # type: ignore[call-arg]
        "public_constructor_not_production_principal",
    )
    assert _codes(lambda: PrincipalScopeBinding(SUBJECT)) == (  # type: ignore[call-arg]
        "public_constructor_not_production_principal",
    )
    assert _codes(lambda: _authority(production_principal=True)) == ("caller_flag_not_authority",)
    assert _codes(lambda: _authority(reviewed_host_mechanism=True)) == (
        "reviewed_host_authority_not_wired",
    )
    assert _codes(lambda: _authority(record_kind=PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION)) == (
        "identity_input_not_an_admission",
    )
    assert _codes(lambda: _authority(proof_id=SUBJECT)) == ("matching_identifier_not_proof",)
    checklist = HostIngressProofChecklist()
    assert checklist.satisfied is False
    assert checklist.requirements == HOST_INGRESS_PROOF_REQUIREMENTS
    assert bool(checklist) is False
    assert set(HostProofStanding) == {HostProofStanding.UNRESOLVED}
    assert _codes(lambda: HostIngressProofChecklist(satisfied=True)) == (
        "reviewed_host_authority_not_wired",
    )
    assert _codes(lambda: issue_production_application_principal(checklist)) == (
        *CLOSED,
        "host_checklist_not_issuance",
    )
    assert HostIngressProofRequirement.INDEPENDENT_TOKEN_VERIFICATION in HOST_INGRESS_PROOF_REQUIREMENTS
    assert HostIngressProofRequirement.LAMBDA_INVOCATION_BOUNDARY_ATTESTATION in HOST_INGRESS_PROOF_REQUIREMENTS
    assert AbsentSourceClaim.TENANT in ABSENT_SOURCE_CLAIMS
    assert AbsentSourceClaim.ROLE in ABSENT_SOURCE_CLAIMS


def test_environment_variables_do_not_enable_the_proof_port(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WILVOR_TEST_MODE", "1")
    monkeypatch.setenv("WILVOR_PRODUCTION_PRINCIPAL", "true")
    monkeypatch.setenv("WILVOR_ALLOW_SYNTHETIC_PRINCIPAL", "1")
    monkeypatch.setenv("ENVIRONMENT", "test")
    assert os.environ["WILVOR_TEST_MODE"] == "1"
    assert PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is False
    assert TEST_DOUBLE_ENVIRONMENT_ACTIVATION_AVAILABLE is False
    assert _codes(issue_production_application_principal) == CLOSED
    assert _codes(lambda: issue_production_application_principal(test_mode="1")) == (
        *CLOSED,
        "environment_not_principal_authority",
    )
    assert _codes(
        lambda: open_nonproduction_principal_proof_port(
            _config(),
            policy_revision=POLICY_REVISION,
            authorization_provenance=PROVENANCE,
            seal=os.environ["WILVOR_TEST_MODE"],
        )
    ) == ("environment_not_principal_authority",)
    assert _codes(lambda: NonproductionContractSeal(os.environ["WILVOR_PRODUCTION_PRINCIPAL"])) == (
        "nonproduction_seal_not_environment_activated",
    )
    assert not hasattr(NonproductionPrincipalProofPort, "from_environ")
    assert not hasattr(open_nonproduction_principal_proof_port, "install")


def test_audience_issuer_route_scope_and_purpose_consistency():
    observation = observe_untrusted_gateway_claims(_assessment())
    port = _port()
    assert _codes(
        lambda: port.admit(
            observation,
            _authority(audience=OTHER_AUDIENCE, proof_id="proof-aud"),
            evaluation_epoch=EVALUATION_EPOCH,
        )
    ) == ("audience_substitution",)
    assert _codes(
        lambda: port.admit(
            observation,
            _authority(issuer=OTHER_ISSUER, proof_id="proof-iss"),
            evaluation_epoch=EVALUATION_EPOCH,
        )
    ) == ("issuer_mismatch",)
    assert _codes(
        lambda: port.admit(
            observation,
            _authority(method="GET", raw_path="/other", route_key="GET /other", proof_id="proof-route"),
            evaluation_epoch=EVALUATION_EPOCH,
        )
    ) == ("route_mismatch",)
    assert _codes(
        lambda: port.admit(
            observation,
            _authority(scopes=(REQUIRED_SCOPE, "ai.admin"), proof_id="proof-scope"),
            evaluation_epoch=EVALUATION_EPOCH,
        )
    ) == ("scope_mismatch",)
    other_port = _port(_config(audience=OTHER_AUDIENCE))
    assert _codes(lambda: other_port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH)) == (
        "audience_substitution",
    )
    descriptor = port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH)
    assert descriptor.scopes == (REQUIRED_SCOPE,)
    assert _codes(lambda: _present(port, descriptor, audience=OTHER_AUDIENCE)) == ("audience_substitution",)
    assert _codes(lambda: _present(port, descriptor, route_key="GET /ai")) == ("route_mismatch",)
    assert _codes(lambda: _present(port, descriptor, purpose=MasterPurpose.ANSWER)) == ("purpose_mismatch",)
    assert _codes(lambda: port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH, deadline_utc="2099-01-01T00:00:00Z")) == (
        "request_deadline_not_principal_lifetime",
    )


def test_expiry_not_before_and_replay():
    observation = observe_untrusted_gateway_claims(_assessment())
    port = _port()
    authority = _authority()
    assert _codes(lambda: port.admit(observation, authority, evaluation_epoch=EXPIRES_AT)) == ("principal_expired",)
    assert _codes(lambda: port.admit(observation, authority, evaluation_epoch=NOT_BEFORE - 1)) == (
        "principal_not_yet_valid",
    )
    assert _codes(lambda: port.admit(observation, authority, evaluation_epoch=ISSUED_AT - 1)) == (
        "principal_issued_in_future",
    )
    assert _codes(lambda: port.admit(observation, authority, evaluation_epoch=True)) == (
        "invalid_evaluation_epoch",
    )
    first = port.admit(observation, authority, evaluation_epoch=EVALUATION_EPOCH)
    assert _codes(lambda: port.admit(observation, authority, evaluation_epoch=EVALUATION_EPOCH)) == (
        "replayed_principal",
    )
    second = port.admit(
        observation,
        _authority(proof_id="proof-2"),
        evaluation_epoch=EVALUATION_EPOCH,
    )
    assert second.principal_ref != first.principal_ref
    assert second.proof_id == "proof-2"
    assert _codes(lambda: _present(port, second, evaluation_epoch=EXPIRES_AT)) == ("principal_expired",)
    assert _codes(lambda: _present(port, second, evaluation_epoch=NOT_BEFORE - 1)) == (
        "principal_not_yet_valid",
    )
    assert _present(port, second) is second
    assert _codes(lambda: _present(port, second)) == ("replayed_principal",)
    assert first.production_principal is False


def test_copied_principal_is_rejected():
    observation = observe_untrusted_gateway_claims(_assessment())
    port = _port()
    descriptor = port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH)
    assert _codes(lambda: copy.copy(descriptor)) == ("copied_principal_not_accepted",)
    assert _codes(lambda: copy.deepcopy(descriptor)) == ("copied_principal_not_accepted",)
    assert _codes(lambda: pickle.dumps(descriptor)) == ("copied_principal_not_accepted",)
    assert _codes(lambda: copy.copy(port)) == ("proof_port_not_copyable",)
    other = _port()
    assert _codes(lambda: _present(other, descriptor)) == ("copied_principal_not_accepted",)
    object.__setattr__(descriptor.scope, "subject", "other-subject")
    assert _codes(lambda: _present(port, descriptor)) == ("principal_descriptor_immutable",)
    with pytest.raises(ContractValidationError):
        descriptor.production_principal = True  # type: ignore[misc]


def test_cross_session_confused_deputy_and_membership_are_not_proof():
    registry, record = _live_member()
    assert registry.state(record) is MasterPlanningSessionState.OPEN
    observation = observe_untrusted_gateway_claims(_assessment())
    port = _port()
    bound = _authority(planning_session_id=record.session_id, proof_id="proof-member")
    assert _codes(lambda: port.admit(observation, bound, evaluation_epoch=EVALUATION_EPOCH, session=record)) == (
        "session_membership_not_proof",
    )
    assert _codes(lambda: port.admit(observation, bound, evaluation_epoch=EVALUATION_EPOCH, session=registry)) == (
        "session_membership_not_proof",
    )
    assert _codes(lambda: issue_production_application_principal(record)) == (
        *CLOSED,
        "session_membership_not_proof",
    )
    assert _codes(lambda: issue_production_application_principal(registry)) == (
        *CLOSED,
        "session_membership_not_proof",
    )
    loose = MasterPlanningSessionRecord(SESSION, object())
    assert _codes(lambda: reject_untrusted_authority(loose)) == ("session_membership_not_proof",)
    descriptor = port.admit(observation, bound, evaluation_epoch=EVALUATION_EPOCH)
    assert descriptor.planning_session_id == record.session_id
    assert descriptor.session_membership_accepted_as_proof is False
    assert descriptor is not record
    assert _codes(lambda: _present(port, descriptor, planning_session_id=SESSION)) == (
        "cross_session_principal",
    )
    assert _codes(lambda: _present(port, descriptor, acting_subject="other-subject")) == ("confused_deputy",)
    assert _codes(lambda: _present(port, descriptor, master_request_id="master-req-2")) == ("confused_deputy",)
    assert _codes(lambda: _present(port, descriptor, correlation_id="corr-2")) == ("confused_deputy",)
    assert _codes(lambda: _present(port, descriptor, gateway_request_id="other-request")) == (
        "request_identity_mismatch",
    )
    assert _present(port, descriptor) is descriptor
    assert descriptor.production_principal is False


def test_absent_tenant_and_role_claims_are_rejected():
    observation = observe_untrusted_gateway_claims(_assessment())
    port = _port()
    assert _codes(lambda: port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH, tenant="acme")) == (
        "absent_source_claim",
    )
    assert _codes(lambda: port.admit(observation, _authority(), evaluation_epoch=EVALUATION_EPOCH, role="dispatcher")) == (
        "absent_source_claim",
    )
    mapping = {"tenant": "acme", "sub": SUBJECT, "role": "dispatcher"}
    assert _codes(lambda: reject_untrusted_authority(mapping)) == ("absent_source_claim",)
    assert _codes(lambda: issue_production_application_principal(tenant="acme", role="dispatcher")) == (
        *CLOSED,
        "absent_source_claim",
    )
    descriptor = port.admit(observation, _authority(proof_id="proof-absent"), evaluation_epoch=EVALUATION_EPOCH)
    assert descriptor.tenant_presence is SourceClaimPresence.ABSENT
    assert descriptor.role_presence is SourceClaimPresence.ABSENT
    assert not hasattr(descriptor, "tenant")
    assert not hasattr(IndependentlyHostAuthenticatedIdentityInput, "from_dict")
    assert not hasattr(IndependentlyHostAuthenticatedIdentityInput, "from_event")


def test_direct_invoke_event_and_matching_ids_do_not_issue():
    direct = _event()
    direct["principal"] = SUBJECT
    assert _codes(
        lambda: assess_master_gateway_identity(direct, _config(), evaluation_epoch=EVALUATION_EPOCH)
    ) == ("caller_principal_not_accepted",)
    assessment = _assessment()
    observation = observe_untrusted_gateway_claims(assessment)
    assert _codes(lambda: issue_production_application_principal(direct, assessment, _authority()))[:2] == CLOSED
    assert "structural_jwt_assessment_not_principal" in _codes(
        lambda: issue_production_application_principal(direct, assessment, _authority())
    )
    assert _codes(lambda: reject_untrusted_authority(EVALUATION_EPOCH)) == ("matching_identifier_not_proof",)
    assert observation.gateway_request_id == REQUEST_ID
    assert observation.subject != observation.gateway_request_id
    port = _port()
    descriptor = port.admit(observation, _authority(proof_id="proof-direct"), evaluation_epoch=EVALUATION_EPOCH)
    assert descriptor.subject == SUBJECT
    assert descriptor.production_principal is False
    assert observation.structural_assessment_accepted_as_proof is False
    assert MasterProductionAuthority.DENIED.value == "DENIED"
    assert MasterSourceAuthenticity.NOT_ESTABLISHED.value == "NOT_ESTABLISHED"
    assert MasterRequestCoverage.NOT_ESTABLISHED.value == "NOT_ESTABLISHED"


def test_concurrent_admits_of_one_proof_mint_one_synthetic_admission():
    observation = observe_untrusted_gateway_claims(_assessment())
    authority = _authority(proof_id="proof-race")
    port = _port()
    barrier = threading.Barrier(8)
    outcomes: list[object] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            descriptor = port.admit(observation, authority, evaluation_epoch=EVALUATION_EPOCH)
            outcome: object = descriptor
        except ContractValidationError as exc:
            outcome = exc.errors
        with lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    admitted = [item for item in outcomes if type(item) is ServerOwnedPrincipalDescriptor]
    replayed = [item for item in outcomes if item == ("replayed_principal",)]
    assert len(admitted) == 1
    assert len(replayed) == 7
    assert admitted[0].production_principal is False
    assert _codes(lambda: issue_production_application_principal(admitted[0])) == (
        *CLOSED,
        "synthetic_admission_not_production_principal",
    )
