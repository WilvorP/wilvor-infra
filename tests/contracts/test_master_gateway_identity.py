"""Gateway identity boundary. A shaped event is not a principal."""

from __future__ import annotations

import base64
import copy
import json
import threading
from dataclasses import FrozenInstanceError, replace

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_contracts import MasterInvocationAuthorization, MasterProductionAuthority
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gateway_identity import (
    APPLICATION_OWNED_PRINCIPAL_AVAILABLE,
    GATEWAY_IDENTITY_LIMITATIONS,
    GATEWAY_INVOCATION_ATTESTATION_AVAILABLE,
    MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION,
    PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE,
    UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION,
    ApplicationPrincipalRequirement,
    ApplicationPrincipalStanding,
    AuthorizerClaimMetadata,
    GatewayIdentityLimitation,
    GatewayIdentityMaterial,
    GatewayInvocationProvenance,
    IngressAttestationStanding,
    InvocationProvenanceStanding,
    MasterGatewayIdentityAssessment,
    MasterGatewayIdentityConfiguration,
    RawAuthorizationHeader,
    UnresolvedIngressAttestation,
    assess_master_gateway_identity,
    issue_application_principal,
    promote_to_execution_permission,
    reject_claimed_principal,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_routing import MasterRequestCoverage, MasterRouteExecutionAuthority


EVALUATION_EPOCH = 1_767_225_600
EXPIRES_AT = EVALUATION_EPOCH + 300
ISSUED_AT = EVALUATION_EPOCH - 30
NOT_BEFORE = EVALUATION_EPOCH - 10
ISSUER = "https://issuer.wilvor.example/realms/master"
AUDIENCE = "wilvor-master-ai"
SUBJECT = "subject-user-1"
REQUEST_ID = "JKJaXmPLvHcESHA="
ROUTE_KEY = "POST /ai"
REQUIRED_SCOPE = "ai.master.invoke"
DELETE = object()
_DEFAULT_EVENT = object()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _forged_token() -> str:
    """Bearer token whose payload claims are not the authorizer claims."""

    header = _b64(b'{"alg":"none","typ":"JWT"}')
    payload = {
        "aud": AUDIENCE,
        "exp": EXPIRES_AT,
        "iss": ISSUER,
        "scope": "ai.master.invoke ai.admin",
        "sub": "forged-subject",
    }
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii"))
    return f"Bearer {header}.{body}.forged-signature"


FORGED_AUTHORIZATION = _forged_token()


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
        "headers": {
            "authorization": FORGED_AUTHORIZATION,
            "content-type": "application/json",
        },
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
            "http": {
                "method": "POST",
                "path": "/ai",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.10",
                "userAgent": "wilvor-test",
            },
            "requestId": REQUEST_ID,
            "routeKey": ROUTE_KEY,
            "stage": "$default",
            "timeEpoch": (EVALUATION_EPOCH + 10_000) * 1000,
        },
        "isBase64Encoded": False,
    }


def _with(event: dict[str, object], changes: dict[str, object]) -> dict[str, object]:
    cloned = copy.deepcopy(event)
    for path, value in changes.items():
        cursor = cloned
        keys = path.split(".")
        for key in keys[:-1]:
            cursor = cursor[key]
        if value is DELETE:
            del cursor[keys[-1]]
        else:
            cursor[keys[-1]] = value
    return cloned


def _assess(
    event: object = _DEFAULT_EVENT,
    config: object = None,
    epoch: object = EVALUATION_EPOCH,
    principal: object = None,
) -> MasterGatewayIdentityAssessment:
    return assess_master_gateway_identity(
        _event() if event is _DEFAULT_EVENT else event,
        _config() if config is None else config,
        evaluation_epoch=epoch,
        claimed_principal=principal,
    )


def _codes(func: object) -> tuple[str, ...]:
    with pytest.raises(ContractValidationError) as caught:
        func()  # type: ignore[operator]
    return caught.value.errors


def _denied(assessment: MasterGatewayIdentityAssessment) -> None:
    assert assessment.schema_version == MASTER_GATEWAY_IDENTITY_SCHEMA_VERSION
    assert assessment.production_authenticated is False
    assert assessment.execution_permitted is False
    assert bool(assessment) is False
    assert assessment.execution_authority is MasterRouteExecutionAuthority.DENIED
    assert assessment.production_authority is MasterProductionAuthority.DENIED
    assert assessment.source_authenticity is MasterSourceAuthenticity.NOT_ESTABLISHED
    assert assessment.request_coverage is MasterRequestCoverage.NOT_ESTABLISHED
    assert assessment.limitations == GATEWAY_IDENTITY_LIMITATIONS
    assert GatewayIdentityLimitation.INGRESS_ATTESTATION_UNRESOLVED in assessment.limitations
    assert GatewayIdentityLimitation.PRODUCTION_ISSUANCE_UNAVAILABLE in assessment.limitations
    assert assessment.invocation_provenance.standing is InvocationProvenanceStanding.NOT_ATTESTED
    assert assessment.invocation_provenance.attested is False
    assert assessment.invocation_provenance.event_dictionary_accepted_as_proof is False
    assert assessment.invocation_provenance.request_id_accepted_as_proof is False
    assert bool(assessment.invocation_provenance) is False
    assert assessment.ingress_attestation.standing is IngressAttestationStanding.UNRESOLVED
    assert assessment.ingress_attestation.protected_gateway_integration_reviewed is False
    assert assessment.ingress_attestation.lambda_invocation_boundary_reviewed is False
    assert assessment.ingress_attestation.production_issuance_available is False
    assert bool(assessment.ingress_attestation) is False
    assert assessment.application_principal.standing is ApplicationPrincipalStanding.NOT_ISSUED
    assert assessment.application_principal.issued is False
    assert assessment.application_principal.subject_accepted_as_principal is False
    assert bool(assessment.application_principal) is False
    assert assessment.authorizer_claims.structurally_valid is True
    assert assessment.authorizer_claims.authenticates_caller is False
    assert assessment.authorizer_claims.signature_verified is False
    assert assessment.authorizer_claims.jwks_consulted is False
    assert assessment.claimed_request.authenticates_caller is False
    assert assessment.raw_authorization.payload_decoded is False
    assert assessment.raw_authorization.accepted_as_authentication is False
    assert PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is False
    assert GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is False
    assert APPLICATION_OWNED_PRINCIPAL_AVAILABLE is False
    assert UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is False
    assert not isinstance(assessment, MasterInvocationAuthorization)
    assert set(InvocationProvenanceStanding) == {InvocationProvenanceStanding.NOT_ATTESTED}
    assert set(IngressAttestationStanding) == {IngressAttestationStanding.UNRESOLVED}
    assert set(ApplicationPrincipalStanding) == {ApplicationPrincipalStanding.NOT_ISSUED}


def test_structurally_valid_http_api_event_stays_unauthenticated():
    assessment = _assess()
    _denied(assessment)
    assert assessment.evaluated_at_epoch == EVALUATION_EPOCH
    assert assessment.evaluated_at_epoch != _event()["requestContext"]["timeEpoch"]  # type: ignore[index]
    assert assessment.raw_authorization.present is True
    assert assessment.raw_authorization.value == FORGED_AUTHORIZATION
    assert assessment.claimed_request.request_id == REQUEST_ID
    assert assessment.claimed_request.method == "POST"
    assert assessment.claimed_request.raw_path == "/ai"
    assert assessment.claimed_request.route_key == ROUTE_KEY
    assert assessment.authorizer_claims.issuer == ISSUER
    assert assessment.authorizer_claims.audience == AUDIENCE
    assert assessment.authorizer_claims.subject == SUBJECT
    assert assessment.authorizer_claims.subject != "forged-subject"
    assert assessment.authorizer_claims.scopes == (REQUIRED_SCOPE,)
    assert assessment.authorizer_claims.expires_at_epoch == EXPIRES_AT
    assert "forged-subject" not in repr(assessment)
    assert "forged-signature" not in repr(assessment)
    assert not hasattr(assessment.claimed_request, "source_ip")
    assert not hasattr(assessment, "configuration")
    materials = tuple(assessment.material(kind) for kind in GatewayIdentityMaterial)
    assert len(materials) == 5
    assert materials[0] is assessment.raw_authorization
    assert materials[4] is assessment.application_principal


def test_optional_claims_and_ignored_profile_claims_do_not_issue_a_principal():
    without_times = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.iat": DELETE,
            "requestContext.authorizer.jwt.claims.nbf": DELETE,
        },
    )
    omitted = _assess(without_times)
    assert omitted.authorizer_claims.issued_at_epoch is None
    assert omitted.authorizer_claims.not_before_epoch is None
    _denied(omitted)

    with_profile = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.username": "admin",
            "requestContext.authorizer.jwt.claims.email": "user@example.com",
            "requestContext.authorizer.jwt.claims.client_id": "other-client",
        },
    )
    profile = _assess(with_profile)
    _denied(profile)
    assert profile.authorizer_claims.subject == SUBJECT
    assert not hasattr(profile.application_principal, "username")

    reordered = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.scope": f"ai.master.read {REQUIRED_SCOPE}",
            "requestContext.authorizer.jwt.scopes": ["ai.master.read", REQUIRED_SCOPE],
        },
    )
    extra = _assess(reordered)
    _denied(extra)
    assert extra.authorizer_claims.scopes == ("ai.master.read", REQUIRED_SCOPE)
    assert extra.execution_permitted is False


def test_event_mutation_and_equal_results_are_not_authentication():
    event = _event()
    left = _assess(event)
    event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"] = "mutated"  # type: ignore[index]
    event["requestContext"]["authorizer"]["jwt"]["scopes"].append("ai.admin")  # type: ignore[index]
    right = _assess()
    assert left.authorizer_claims.subject == SUBJECT
    assert left.authorizer_claims.scopes == (REQUIRED_SCOPE,)
    assert left == right
    _denied(left)
    _denied(right)


def test_forged_authorization_header_is_not_decoded():
    event = _event()
    del event["requestContext"]["authorizer"]  # type: ignore[index]
    assert _codes(lambda: _assess(event)) == ("missing_jwt_authorizer",)

    accepted = _assess()
    assert accepted.raw_authorization.value == FORGED_AUTHORIZATION
    assert "forged-subject" not in accepted.authorizer_claims.subject
    assert accepted.raw_authorization.payload_decoded is False
    with pytest.raises(ContractValidationError) as caught:
        RawAuthorizationHeader(FORGED_AUTHORIZATION, True, True)
    assert caught.value.errors == ("authorization_header_not_authentication",)


def test_forged_header_subject_does_not_override_authorizer_subject():
    assessment = _assess()
    encoded = FORGED_AUTHORIZATION.split(" ", 1)[1].split(".", 2)[1]
    padded = encoded + "=" * (-len(encoded) % 4)
    forged_payload = json.loads(base64.urlsafe_b64decode(padded))
    assert forged_payload["sub"] == "forged-subject"
    assert assessment.raw_authorization.value == FORGED_AUTHORIZATION
    assert assessment.authorizer_claims.subject == SUBJECT
    assert assessment.authorizer_claims.subject != forged_payload["sub"]
    assert assessment.raw_authorization.payload_decoded is False
    _denied(assessment)


def test_forged_subject_and_scope_do_not_authenticate():
    event = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.sub": "forged-subject",
            "requestContext.authorizer.jwt.claims.scope": f"{REQUIRED_SCOPE} ai.admin",
            "requestContext.authorizer.jwt.scopes": [REQUIRED_SCOPE, "ai.admin"],
        },
    )
    assessment = _assess(event)
    assert assessment.authorizer_claims.subject == "forged-subject"
    assert "ai.admin" in assessment.authorizer_claims.scopes
    _denied(assessment)
    assert _codes(lambda: promote_to_execution_permission(assessment)) == (
        "ingress_attestation_unresolved",
        "execution_permission_not_available",
    )


def test_missing_jwt_authorizer_context_is_rejected():
    missing = _event()
    del missing["requestContext"]["authorizer"]  # type: ignore[index]
    assert _codes(lambda: _assess(missing)) == ("missing_jwt_authorizer",)
    lambda_authorizer = _with(_event(), {"requestContext.authorizer": {"lambda": {"sub": SUBJECT}}})
    assert _codes(lambda: _assess(lambda_authorizer)) == ("missing_jwt_authorizer",)
    principal_id = _with(
        _event(),
        {"requestContext.authorizer.principalId": "admin"},
    )
    assert _codes(lambda: _assess(principal_id)) == ("caller_principal_not_accepted",)


def test_wrong_issuer_or_audience_is_rejected():
    wrong_issuer = _with(
        _event(),
        {"requestContext.authorizer.jwt.claims.iss": "https://attacker.example/realms/master"},
    )
    assert _codes(lambda: _assess(wrong_issuer)) == ("invalid_issuer",)
    other_config = _config(issuer="https://other.wilvor.example/realms/master")
    assert _codes(lambda: _assess(config=other_config)) == ("invalid_issuer",)
    wrong_audience = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.aud": "other-audience",
            "requestContext.authorizer.jwt.claims.client_id": AUDIENCE,
        },
    )
    assert _codes(lambda: _assess(wrong_audience)) == ("invalid_audience",)


def test_missing_subject_and_unexpected_route_are_rejected():
    assert _codes(
        lambda: _assess(_with(_event(), {"requestContext.authorizer.jwt.claims.sub": DELETE}))
    ) == ("missing_subject",)
    assert _codes(
        lambda: _assess(_with(_event(), {"requestContext.authorizer.jwt.claims.sub": " "}))
    ) == ("invalid_subject",)
    get_ai = _with(
        _event(),
        {
            "routeKey": "GET /ai",
            "requestContext.routeKey": "GET /ai",
            "requestContext.http.method": "GET",
        },
    )
    assert _codes(lambda: _assess(get_ai)) == ("unexpected_method",)
    post_health = _with(
        _event(),
        {
            "routeKey": "POST /health",
            "rawPath": "/health",
            "requestContext.routeKey": "POST /health",
            "requestContext.http.path": "/health",
        },
    )
    assert _codes(lambda: _assess(post_health)) == ("unexpected_route",)
    inconsistent = _with(_event(), {"routeKey": "GET /ai", "requestContext.routeKey": "GET /ai"})
    assert _codes(lambda: _assess(inconsistent)) == ("invalid_event",)
    broader = _config(required_scopes=(REQUIRED_SCOPE, "ai.master.read"))
    assert _codes(lambda: _assess(config=broader)) == ("missing_required_scope",)


def test_expired_and_malformed_claims_are_rejected():
    assert _codes(
        lambda: _assess(_with(_event(), {"requestContext.authorizer.jwt.claims.exp": str(EVALUATION_EPOCH)}))
    ) == ("expired_claim",)
    assert _codes(
        lambda: _assess(
            _with(_event(), {"requestContext.authorizer.jwt.claims.exp": str(EVALUATION_EPOCH - 1)})
        )
    ) == ("expired_claim",)
    excused_by_event_clock = _with(
        _event(),
        {
            "requestContext.authorizer.jwt.claims.exp": str(EVALUATION_EPOCH - 5),
            "requestContext.timeEpoch": (EVALUATION_EPOCH - 35) * 1000,
        },
    )
    assert _codes(lambda: _assess(excused_by_event_clock)) == ("expired_claim",)
    not_yet = _with(
        _event(),
        {"requestContext.authorizer.jwt.claims.nbf": str(EVALUATION_EPOCH + 10)},
    )
    assert _codes(lambda: _assess(not_yet)) == ("claim_not_yet_valid",)
    future_iat = _with(
        _event(),
        {"requestContext.authorizer.jwt.claims.iat": str(EVALUATION_EPOCH + 10)},
    )
    assert _codes(lambda: _assess(future_iat)) == ("claim_issued_in_future",)


@pytest.mark.parametrize(
    ("path", "value", "code"),
    [
        ("requestContext.authorizer.jwt.claims.exp", "nope", "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.exp", "01", "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.exp", "1.0", "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.exp", 123, "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.exp", True, "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.exp", "", "malformed_claim"),
        ("requestContext.authorizer.jwt.claims.iss", DELETE, "missing_claim"),
        ("requestContext.authorizer.jwt.claims.role", "admin", "caller_principal_not_accepted"),
        ("requestContext.authorizer.jwt.claims.cognito:groups", "admins", "caller_principal_not_accepted"),
        ("requestContext.authorizer.jwt.scopes", REQUIRED_SCOPE, "malformed_scopes"),
        ("requestContext.authorizer.jwt.scopes", [REQUIRED_SCOPE, REQUIRED_SCOPE], "malformed_scopes"),
        ("requestContext.authorizer.jwt.claims.scope", "ai.admin", "malformed_scopes"),
    ],
)
def test_malformed_claim_shapes_are_rejected(path: str, value: object, code: str):
    assert _codes(lambda: _assess(_with(_event(), {path: value}))) == (code,)


def test_caller_controlled_principal_label_is_rejected():
    assessment = _assess()
    labels = [
        "admin",
        REQUEST_ID,
        {"sub": SUBJECT, "scope": REQUIRED_SCOPE},
        MasterPlanningSessionRecord("session-label-1", object()),
        MasterPlanningSessionRegistry(),
        assessment,
    ]
    for label in labels:
        expected = (
            "planning_session_is_not_principal"
            if isinstance(label, (MasterPlanningSessionRecord, MasterPlanningSessionRegistry))
            else "caller_principal_not_accepted"
        )
        assert _codes(lambda label=label: _assess(principal=label)) == (expected,)
        assert _codes(lambda label=label: reject_claimed_principal(label)) == (expected,)
    smuggled_header = _with(_event(), {"headers.x-principal": "admin"})
    assert _codes(lambda: _assess(smuggled_header)) == ("caller_principal_not_accepted",)
    smuggled_event = _event()
    smuggled_event["principal"] = "admin"
    assert _codes(lambda: _assess(smuggled_event)) == ("caller_principal_not_accepted",)
    query = _with(_event(), {"rawQueryString": "principal=admin"})
    assert _codes(lambda: _assess(query)) == ("caller_principal_not_accepted",)


def test_directly_constructed_event_is_not_attestation():
    constructed = _event()
    assessment = _assess(constructed)
    _denied(assessment)
    assert assessment.invocation_provenance == GatewayInvocationProvenance()
    authorizer_only = {"requestContext": constructed["requestContext"]}
    assert _codes(lambda: _assess(authorizer_only)) == ("invalid_event",)
    with pytest.raises(ContractValidationError) as caught:
        GatewayInvocationProvenance(attested=True)
    assert caught.value.errors == ("gateway_invocation_not_attested",)
    with pytest.raises(ContractValidationError) as attested:
        UnresolvedIngressAttestation(protected_gateway_integration_reviewed=True)
    assert attested.value.errors == ("ingress_attestation_unresolved",)


def test_matching_request_id_is_not_authentication():
    first = _assess()
    second = _assess()
    assert first.claimed_request.request_id == second.claimed_request.request_id == REQUEST_ID
    _denied(first)
    _denied(second)
    missing = _event()
    del missing["requestContext"]["authorizer"]  # type: ignore[index]
    assert missing["requestContext"]["requestId"] == REQUEST_ID  # type: ignore[index]
    assert _codes(lambda: _assess(missing)) == ("missing_jwt_authorizer",)
    assert _codes(lambda: _assess(principal=REQUEST_ID)) == ("caller_principal_not_accepted",)


def test_structurally_valid_claims_cannot_promote_to_execution():
    assessment = _assess()
    assert assessment.authorizer_claims.structurally_valid is True
    assert _codes(lambda: promote_to_execution_permission(assessment)) == (
        "ingress_attestation_unresolved",
        "execution_permission_not_available",
    )
    assert _codes(lambda: issue_application_principal(assessment)) == (
        "ingress_attestation_unresolved",
        "application_principal_not_issued",
    )
    assert _codes(lambda: promote_to_execution_permission("allow")) == ("invalid_assessment",)
    assert _codes(lambda: issue_application_principal(assessment.authorizer_claims)) == (
        "invalid_assessment",
    )
    with pytest.raises(FrozenInstanceError):
        assessment.production_authenticated = True  # type: ignore[misc]
    with pytest.raises(ContractValidationError) as caught:
        replace(assessment, production_authenticated=True)
    assert caught.value.errors == ("application_principal_not_issued",)
    with pytest.raises(ContractValidationError) as expired:
        replace(assessment, evaluated_at_epoch=EXPIRES_AT)
    assert expired.value.errors == ("expired_claim",)


def test_configuration_is_not_read_from_the_request():
    staged = _with(
        _event(),
        {"stageVariables": {"issuer": ISSUER, "audience": AUDIENCE}},
    )
    assert _codes(lambda: _assess(staged)) == ("configuration_not_taken_from_request",)
    assert _codes(lambda: _assess(config=_event())) == ("configuration_not_taken_from_request",)  # type: ignore[arg-type]
    assert not hasattr(MasterGatewayIdentityConfiguration, "from_event")
    assert not hasattr(MasterGatewayIdentityConfiguration, "from_dict")
    assert _codes(lambda: _config(required_scopes=())) == ("missing_required_scope",)  # type: ignore[arg-type]
    assert _codes(lambda: _config(route_key="GET /ai")) == ("unexpected_route",)
    assert _codes(lambda: _assess(epoch=True)) == ("invalid_evaluation_epoch",)
    assert _codes(lambda: _assess(epoch=-1)) == ("invalid_evaluation_epoch",)
    assert _codes(lambda: _assess(epoch=1.5)) == ("invalid_evaluation_epoch",)


def test_sparse_operational_event_and_body_are_not_a_principal():
    operational = {
        "rawPath": "/health",
        "requestContext": {
            "requestId": "test-request-id",
            "http": {"method": "GET"},
        },
    }
    assert _codes(lambda: _assess(operational)) == ("invalid_event",)
    body = _with(
        _event(),
        {"body": json.dumps({"sub": "forged-subject", "iss": ISSUER, "scope": REQUIRED_SCOPE})},
    )
    parsed = _assess(body)
    assert parsed.authorizer_claims.subject == SUBJECT
    _denied(parsed)
    encoded = _with(
        _event(),
        {
            "isBase64Encoded": True,
            "body": _b64(b'{"sub":"forged-subject"}'),
        },
    )
    encoded_assessment = _assess(encoded)
    assert encoded_assessment.authorizer_claims.subject == SUBJECT
    assert encoded_assessment.raw_authorization.payload_decoded is False
    uppercase = _event()
    headers = uppercase["headers"]
    assert isinstance(headers, dict)
    headers["Authorization"] = headers.pop("authorization")
    assert _codes(lambda: _assess(uppercase)) == ("invalid_event",)


def test_dict_subclass_cannot_masquerade_as_the_event():
    class Trick(dict):
        def get(self, key, default=None):
            return "POST"

    disguised = Trick(_event())
    assert _codes(lambda: _assess(disguised)) == ("invalid_event",)
    assert _codes(lambda: _assess(None)) == ("invalid_event",)
    assert _codes(lambda: _assess(["POST", "/ai"])) == ("invalid_event",)


def test_closed_types_cannot_record_a_completed_review():
    with pytest.raises(ContractValidationError) as issued:
        ApplicationPrincipalRequirement(issued=True)
    assert issued.value.errors == ("application_principal_not_issued",)
    with pytest.raises(ContractValidationError) as reviewed:
        UnresolvedIngressAttestation(
            protected_gateway_integration_reviewed=True,
            lambda_invocation_boundary_reviewed=True,
            production_issuance_available=True,
        )
    assert reviewed.value.errors == ("ingress_attestation_unresolved",)
    assert AuthorizerClaimMetadata(
        issuer=ISSUER,
        audience=AUDIENCE,
        subject=SUBJECT,
        scopes=(REQUIRED_SCOPE,),
        expires_at_epoch=EXPIRES_AT,
        not_before_epoch=NOT_BEFORE,
        issued_at_epoch=ISSUED_AT,
    ).signature_verified is False


def test_concurrent_assessments_do_not_issue_a_principal():
    barrier = threading.Barrier(8)
    results: list[MasterGatewayIdentityAssessment] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _refusal(func: object) -> tuple[str, ...] | BaseException:
        try:
            func()  # type: ignore[operator]
        except ContractValidationError as exc:
            return exc.errors
        except BaseException as exc:  # noqa: BLE001 - worker records unexpected failures
            return exc
        return ()

    def worker() -> None:
        barrier.wait()
        try:
            assessment = _assess()
            issue_codes = _refusal(lambda: issue_application_principal(assessment))
            promote_codes = _refusal(lambda: promote_to_execution_permission(assessment))
        except BaseException as exc:  # noqa: BLE001 - the test records worker failures
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(assessment)
            if issue_codes != (
                "ingress_attestation_unresolved",
                "application_principal_not_issued",
            ) or promote_codes != (
                "ingress_attestation_unresolved",
                "execution_permission_not_available",
            ):
                errors.append(AssertionError((issue_codes, promote_codes)))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert len(results) == 8
    assert all(item.production_authenticated is False for item in results)
    assert PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is False
    assert GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is False
