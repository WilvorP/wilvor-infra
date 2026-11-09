"""Sprint 3D authentication and host-signed grants.

The RSA numbers next to this file are a generated test key. They are not
a production identity-provider key.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_application_principal import issue_production_application_principal
from wilvor_ai.master_contracts import MasterPurpose, MasterRequirement, MasterSpecialist
from wilvor_ai.master_execution_grants import (
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
    LogicalReadOperation,
    MasterExecutionGrantEnvelope,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import CONDITIONAL_PUT_ABSENT, DYNAMODB_LEDGER_TABLE_NAME
from wilvor_ai.master_gateway_identity import InvocationProvenanceStanding
from wilvor_ai.master_gateway_runtime_boundary import ISSUANCE_CONTROL_IDS
from wilvor_ai.master_read_resource_guard import ReadOperation, server_read_budget
from wilvor_ai.master_routing import (
    MasterPlanningContext,
    MasterProposedCapability,
    MasterProposedStep,
    MasterRouteExecutionAuthority,
    MasterRouteOutcome,
    MasterRouteProposal,
    MasterRouteTemporalIntent,
    authorize_route,
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
    evaluate_master_runtime_policy,
    promote_policy_evaluation_to_permit,
    retain_runtime_policy_session,
    seal_server_policy_inputs,
)
from wilvor_ai.sprint3c_grant_ledger import DECISION_TARGET_NOT_APPLICABLE, consumption_marker_key
from wilvor_ai.sprint3d_auth_grants import (
    AUTHORITY_DOMAIN,
    MAX_TOKEN_LIFETIME_SECONDS,
    AgentCPresentedBinding,
    AtomicConsumeCoordination,
    BearerAuthentication,
    HostOwnedGrantIssuer,
    HostSignedGrant,
    InjectedGrantSigningKeys,
    LambdaInvokePathReview,
    ReviewedRsaPublicKey,
    SameAccountInvokeStatement,
    Sprint3DRejection,
    Sprint3DTrustedPrincipal,
    assert_closed_production_entrypoints,
    authenticate_bearer,
    establish_trusted_principal,
    presented_binding_from_principal,
    review_lambda_invoke_paths,
    route_enablement_permitted,
    seal_reviewed_identity_provider,
    verify_serialized_grant,
)


_MODULUS, _EXPONENT, _PRIVATE = (
    int(line)
    for line in Path(__file__).with_name("_rsa_test_key.txt").read_text(encoding="utf-8").splitlines()
    if line.strip()
)
_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
NOW = datetime(2026, 1, 15, tzinfo=timezone.utc)
ACCEPTED = "2026-01-15T00:00:00Z"
DEADLINE = "2026-01-15T00:05:00Z"
ISSUER = "https://idp.wilvor.test/realms/ops"
AUDIENCE = "wilvor-ai"
SCOPE = "ai.query.read"
KID = "idp-1"
SUBJECT = "operator-7"
JTI = "jti-3d-1"
ORIGIN_SESSION = "origin-session-3d"
FUNCTION_ARN = "arn:aws:lambda:us-west-1:123456789012:function:wilvor-ai-query"
SOURCE_ARN = "arn:aws:execute-api:us-west-1:123456789012:abc123/$default/POST/ai/query"
LIVE = AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
ANSWER = MasterPurpose.ANSWER
REPO = Path(__file__).resolve().parents[2]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _sign(message: bytes) -> bytes:
    width = (_MODULUS.bit_length() + 7) // 8
    trailer = _DIGEST_INFO + hashlib.sha256(message).digest()
    padding = width - len(trailer) - 3
    encoded = b"\x00\x01" + (b"\xff" * padding) + b"\x00" + trailer
    return pow(int.from_bytes(encoded, "big"), _PRIVATE, _MODULUS).to_bytes(width, "big")


def _token(payload: dict[str, object], *, header: dict[str, object] | None = None, signature: bytes | None = None) -> str:
    header_bytes = json.dumps(header or {"alg": "RS256", "typ": "JWT", "kid": KID}, separators=(",", ":")).encode()
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
    signing_input = _b64(header_bytes) + "." + _b64(payload_bytes)
    signed = signature if signature is not None else _sign(signing_input.encode("ascii"))
    return signing_input + "." + _b64(signed)


def _claims(**overrides: object) -> dict[str, object]:
    issued = int(NOW.timestamp())
    values: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": SUBJECT,
        "scope": SCOPE,
        "iat": issued,
        "nbf": issued,
        "exp": issued + 600,
        "jti": JTI,
    }
    values.update(overrides)
    return values


def _provider():
    key = ReviewedRsaPublicKey(KID, _MODULUS, _EXPONENT)
    return seal_reviewed_identity_provider(
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scopes=(SCOPE,),
        keys=(key,),
    )


def _retain(master_request_id: str = "master-req-3d-1", correlation_id: str = "corr-3d-1"):
    step = MasterProposedStep(
        step_id="live-1",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=ANSWER,
        requirement=MasterRequirement.REQUIRED,
        capability=LIVE,
        temporal_intent=MasterRouteTemporalIntent.CURRENT,
    )
    context = MasterPlanningContext(
        master_request_id=master_request_id,
        correlation_id=correlation_id,
        application_request_time_utc=ACCEPTED,
        canonical_user_text="Which aircraft are currently affected?",
    )
    decision = authorize_route(
        MasterRouteProposal(
            user_text=context.canonical_user_text,
            capabilities=(MasterProposedCapability(LIVE, MasterRequirement.REQUIRED),),
            steps=(step,),
        ),
        context,
    )
    assert decision.outcome is MasterRouteOutcome.AUTHORIZED_PLAN
    assert decision.plan is not None
    assert decision.plan.execution_authority is MasterRouteExecutionAuthority.DENIED
    session = retain_runtime_policy_session(context, decision.plan)
    planned = decision.plan.steps[0]
    _executions, turns, calls = server_read_budget(planned.invocation.specialist)
    server = seal_server_policy_inputs(
        evaluation_utc=ACCEPTED,
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        region_codes=frozenset({"CA"}),
        tool_name="get_aircraft_operational_context",
        resource_id="aircraft",
        operation=ReadOperation.GET_ITEM,
        arguments=(("aircraft_id", "N123"),),
        target_kind=TargetObligationKind.NOT_APPLICABLE,
        idempotency_key="idem-3d-1",
    )
    envelope = MasterRuntimePolicyEnvelope(
        master_request_id=planned.invocation.master_request_id,
        planning_session_id=session.session_id,
        step_id=planned.step_id,
        invocation_id=planned.invocation.invocation_id,
        specialist=planned.invocation.specialist,
        purpose=planned.invocation.purpose,
        capability=planned.capability,
        principal_ref=None,
        policy_revision=POLICY_REVISION,
        resource_scope=server.resource_id,
        region_scope="CA",
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        idempotency_key=server.idempotency_key,
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=planned.invocation.correlation_id,
    )
    evaluation = evaluate_master_runtime_policy(session, decision.plan, planned, planned.invocation, server, envelope)
    return session, decision.plan, planned, planned.invocation, server, envelope, evaluation


def _keys() -> InjectedGrantSigningKeys:
    return InjectedGrantSigningKeys(active_key_id="grant-1", keys={"grant-1": b"k" * 32})


def _issued():
    _session, plan, step, invocation, server, policy_envelope, evaluation = _retain()
    authentication = authenticate_bearer(_token(_claims()), _provider(), NOW)
    established = establish_trusted_principal(
        authentication,
        evaluation,
        original_planning_session_id=ORIGIN_SESSION,
        now=NOW,
    )
    assert established.principal is not None
    _executions, turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    envelope = MasterExecutionGrantEnvelope(
        master_request_id=established.principal.master_request_id,
        planning_session_id=established.principal.planning_session_id,
        step_id=established.principal.step_id,
        invocation_id=established.principal.invocation_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=ANSWER,
        capability=LIVE,
        principal_ref=established.principal.principal_ref,
        policy_revision=POLICY_REVISION,
        tool_name="get_aircraft_operational_context",
        resource_scope=(LogicalReadOperation("aircraft", ReadOperation.GET_ITEM),),
        region_scope=("CA",),
        temporal_scopes=(TemporalScope.CURRENT,),
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        expires_at_utc=DEADLINE,
        idempotency_key="idem-3d-1",
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=established.principal.correlation_id,
    )
    issuer = HostOwnedGrantIssuer(_keys())
    grant, coordination = issuer.issue(established, envelope, now=NOW)
    return issuer, established, envelope, grant, coordination, plan, step, invocation, server, policy_envelope


def _resource_policy(
    *,
    source: str = SOURCE_ARN,
    principal: str = "apigateway.amazonaws.com",
    action: str = "lambda:InvokeFunction",
) -> dict[str, object]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": principal},
                "Action": action,
                "Resource": FUNCTION_ARN,
                "Condition": {"ArnLike": {"AWS:SourceArn": source}},
            }
        ],
    }


def test_verified_rs256_token_ignores_event_claims():
    event = {
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": "forged-user", "iss": ISSUER}}}},
        "headers": {"authorization": "Bearer not-the-token"},
    }
    result = authenticate_bearer(_token(_claims()), _provider(), NOW, untrusted_event=event)
    assert result.authenticated is True
    assert result.signature_verified is True
    assert result.subject == SUBJECT
    assert result.event_subject_ignored == "forged-user"
    assert result.issuer == ISSUER
    assert result.audience == AUDIENCE
    assert result.scopes == (SCOPE,)
    assert result.invocation_provenance is InvocationProvenanceStanding.NOT_ATTESTED
    assert result.route_enablement_permitted is False
    with pytest.raises(ContractValidationError, match="authentication_is_not_route_authority"):
        bool(result)


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("not-a-jwt", Sprint3DRejection.MALFORMED_TOKEN),
        (None, Sprint3DRejection.UNAUTHENTICATED),
        ({"sub": SUBJECT, "iss": ISSUER}, Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS),
    ],
)
def test_unauthenticated_and_caller_constructed_material_is_rejected(token: object, reason: Sprint3DRejection):
    result = authenticate_bearer(token, _provider(), NOW)
    assert result.authenticated is False
    assert result.rejection is reason
    assert result.subject is None
    assert result.signature_verified is False


def test_forged_wrong_algorithm_and_authority_claims_are_rejected():
    provider = _provider()
    valid = _token(_claims())
    forged = valid[:-4] + ("A" if valid[-4] != "A" else "B") + valid[-3:]
    assert authenticate_bearer(forged, provider, NOW).rejection is Sprint3DRejection.FORGED_SIGNATURE
    none_token = _token(_claims(), header={"alg": "none", "typ": "JWT", "kid": KID}, signature=b"")
    assert authenticate_bearer(none_token, provider, NOW).rejection is Sprint3DRejection.MALFORMED_TOKEN or (
        authenticate_bearer(none_token, provider, NOW).rejection is Sprint3DRejection.WRONG_ALGORITHM
    )
    hmac_header = _token(_claims(), header={"alg": "HS256", "typ": "JWT", "kid": KID})
    assert authenticate_bearer(hmac_header, provider, NOW).rejection is Sprint3DRejection.WRONG_ALGORITHM
    claimed = authenticate_bearer(_token(_claims(principal_ref="pr_caller")), provider, NOW)
    assert claimed.authenticated is False
    assert claimed.signature_verified is True
    assert claimed.rejection is Sprint3DRejection.CALLER_CONSTRUCTED_CLAIMS


def test_issuer_audience_scope_and_lifetime_are_enforced():
    provider = _provider()
    assert authenticate_bearer(_token(_claims(iss="https://other.example/realms/ops")), provider, NOW).rejection is (
        Sprint3DRejection.ISSUER_MISMATCH
    )
    assert authenticate_bearer(_token(_claims(aud="other-app")), provider, NOW).rejection is Sprint3DRejection.AUDIENCE_MISMATCH
    assert authenticate_bearer(_token(_claims(scope="other.read")), provider, NOW).rejection is Sprint3DRejection.SCOPE_MISMATCH
    issued = int(NOW.timestamp())
    expired = authenticate_bearer(
        _token(_claims(iat=issued - 120, nbf=issued - 120, exp=issued - 1)),
        provider,
        NOW,
    )
    assert expired.rejection is Sprint3DRejection.TOKEN_EXPIRED
    too_long = authenticate_bearer(
        _token(_claims(exp=issued + MAX_TOKEN_LIFETIME_SECONDS + 1)),
        provider,
        NOW,
    )
    assert too_long.rejection is Sprint3DRejection.TOKEN_LIFETIME_EXCEEDED
    with pytest.raises(ContractValidationError, match="weak_signing_key"):
        ReviewedRsaPublicKey(KID, 65537, 65537)


def test_policy_violations_and_hypothetical_allow_do_not_issue():
    session, plan, step, invocation, server, envelope, evaluation = _retain()
    authentication = authenticate_bearer(_token(_claims()), _provider(), NOW)
    poisoned = evaluate_master_runtime_policy(
        session,
        plan,
        step,
        invocation,
        server,
        replace(envelope, authorized=True, caller_policy_decision="ALLOW"),
    )
    denied = establish_trusted_principal(
        authentication,
        poisoned,
        original_planning_session_id=ORIGIN_SESSION,
        now=NOW,
    )
    assert denied.grant_eligible is False
    assert denied.rejection is Sprint3DRejection.POLICY_VIOLATION
    assert MasterRuntimePolicyDenialReason.CALLER_AUTHORIZED_FLAG_NOT_AUTHORITY in denied.policy_denial_reasons
    hypothetical = _evaluate_hypothetical_runtime_policy(
        session,
        plan,
        step,
        invocation,
        server,
        replace(envelope, principal_ref="hypothetical-principal"),
        principal_ref="hypothetical-principal",
    )
    assert hypothetical.decision is MasterRuntimePolicyDecision.ALLOW
    assert hypothetical.authority_origin is PolicyAuthorityOrigin.HYPOTHETICAL_TEST_HOST
    refused = establish_trusted_principal(
        authentication,
        hypothetical,
        original_planning_session_id=ORIGIN_SESSION,
        now=NOW,
    )
    assert refused.rejection is Sprint3DRejection.POLICY_ALLOW_NOT_AUTHORITY
    with pytest.raises(ContractValidationError, match="policy_evaluation_is_not_a_permit"):
        promote_policy_evaluation_to_permit(evaluation)
    issuer = HostOwnedGrantIssuer(_keys())
    grant, coordination = issuer.issue(denied, _issued_envelope_placeholder(), now=NOW)
    assert grant is None
    assert coordination.accepted is False


def _issued_envelope_placeholder() -> MasterExecutionGrantEnvelope:
    _executions, turns, calls = server_read_budget(MasterSpecialist.LIVE_OPS)
    return MasterExecutionGrantEnvelope(
        master_request_id="master-req-3d-1",
        planning_session_id=ORIGIN_SESSION,
        step_id="live-1",
        invocation_id="invocation-placeholder-3d",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=ANSWER,
        capability=LIVE,
        principal_ref="pr_placeholder_3d",
        policy_revision=POLICY_REVISION,
        tool_name="get_aircraft_operational_context",
        resource_scope=(LogicalReadOperation("aircraft", ReadOperation.GET_ITEM),),
        region_scope=("CA",),
        temporal_scopes=(TemporalScope.CURRENT,),
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc=ACCEPTED,
        deadline_utc=DEADLINE,
        expires_at_utc=DEADLINE,
        idempotency_key="idem-3d",
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id="corr-placeholder-3d",
    )


def test_host_signs_one_grant_and_publishes_the_agent_c_consume_request():
    issuer, established, envelope, grant, coordination, *_rest = _issued()
    assert established.grant_eligible is True
    assert established.principal is not None
    assert established.principal.principal_ref != SUBJECT
    assert established.principal.planning_session_id == ORIGIN_SESSION
    assert established.principal.policy_session_id != ORIGIN_SESSION
    assert established.principal.public_policy_decision is MasterRuntimePolicyDecision.DENY
    assert established.principal.idp_authenticated is True
    assert established.principal.invocation_provenance is InvocationProvenanceStanding.NOT_ATTESTED
    assert grant is not None and coordination.request is not None
    request = coordination.request
    document = json.loads(grant.canonical_document)
    assert document["authority_domain"] == AUTHORITY_DOMAIN
    assert document["nonce"] == request.grant_nonce
    assert document["route_enablement_permitted"] is False
    assert document["aws_controls_verified"] is False
    assert document["hmac_prevents_replay"] is False
    assert request.grant_nonce_is_ledger_nonce is True
    assert request.grant_nonce_is_attempt_token is False
    assert request.conditional_put_expression == CONDITIONAL_PUT_ABSENT
    assert request.ledger_table_name == DYNAMODB_LEDGER_TABLE_NAME
    assert request.consume_marker_has_ttl is False
    assert request.retention_after_deadline_seconds == 30 * 24 * 60 * 60
    assert request.repeat_non_idempotent_after_expiry is False
    assert request.distributed_exactly_once is False
    assert request.redeemable_on_production_route is False
    assert request.must_reverify_before_write is True
    assert request.do_not_call == "Sprint3CDurableGrantLedger.consume_signed_grant"
    assert request.decision_target_scope == DECISION_TARGET_NOT_APPLICABLE
    assert request.consume_marker_partition_key == f"consume-v1#{request.grant_nonce}"
    again = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=presented_binding_from_principal(established.principal, envelope),
        now=NOW,
    )
    assert again.accepted is True
    assert again.request is not None
    assert again.request.grant_nonce == request.grant_nonce
    seen: set[str] = set()

    def consume_once(nonce: str) -> str:
        if nonce in seen:
            return "REPLAYED"
        seen.add(nonce)
        return "CLAIMED"

    assert consume_once(request.grant_nonce) == "CLAIMED"
    assert consume_once(request.grant_nonce) == "REPLAYED"
    from wilvor_ai.master_execution_ledger import MasterExecutionLedgerEnvelope

    ledger_envelope = MasterExecutionLedgerEnvelope(
        master_request_id=request.master_request_id,
        planning_session_id=request.planning_session_id,
        step_id=request.step_id,
        invocation_id=request.invocation_id,
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=ANSWER,
        capability=LIVE,
        principal_ref=request.principal_ref,
        policy_revision=request.policy_revision,
        resource_scope=request.resource_scope_token,
        region_scope=request.region_scope_token,
        decision_target_scope=request.decision_target_scope,
        accepted_at_utc=request.accepted_at_utc,
        deadline_utc=request.deadline_utc,
        idempotency_key=request.idempotency_key,
        max_model_turns=request.max_model_turns,
        max_tool_calls=request.max_tool_calls,
        correlation_id=request.correlation_id,
    )
    assert consumption_marker_key(request.grant_nonce, ledger_envelope) == (
        request.consume_marker_partition_key,
        request.consume_marker_sort_key,
    )
    assert issuer.production_grants_issued == 0
    assert issuer.kms_integrated is False
    assert route_enablement_permitted() is False
    with pytest.raises(ContractValidationError, match="grant_is_not_route_authority"):
        bool(grant)


def test_replay_cross_principal_expiry_and_tampering_are_rejected():
    issuer, established, envelope, grant, coordination, *_rest = _issued()
    assert grant is not None and established.principal is not None
    second_grant, replay = issuer.issue(established, envelope, now=NOW)
    assert second_grant is None
    assert replay.rejection is Sprint3DRejection.REPLAY
    other = authenticate_bearer(_token(_claims(sub="operator-8", jti="jti-3d-2")), _provider(), NOW)
    _session, _plan, _step, _invocation, _server, _policy_envelope, evaluation = _retain()
    other_principal = establish_trusted_principal(
        other,
        evaluation,
        original_planning_session_id=ORIGIN_SESSION,
        now=NOW,
    )
    assert other_principal.principal is not None
    presented = presented_binding_from_principal(established.principal, envelope)
    crossed = replace(
        presented,
        principal_ref=other_principal.principal.principal_ref,
        verified_subject="operator-8",
    )
    mismatch = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=crossed,
        now=NOW,
    )
    assert mismatch.rejection is Sprint3DRejection.PRINCIPAL_MISMATCH
    flipped = grant.signature_hex[:-1] + ("0" if grant.signature_hex[-1] != "0" else "1")
    forged = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=flipped,
        key_id=grant.key_id,
        presented=presented,
        now=NOW,
    )
    assert forged.rejection is Sprint3DRejection.FORGED_GRANT
    mutated = json.loads(grant.canonical_document)
    mutated["region_scope"] = ["NY"]
    canonical = json.dumps(mutated, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    tampered = verify_serialized_grant(
        issuer._keys,
        canonical_document=canonical,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=presented,
        now=NOW,
    )
    assert tampered.accepted is False
    expired = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=presented,
        now=NOW + timedelta(minutes=5),
    )
    assert expired.rejection is Sprint3DRejection.EXPIRED_GRANT
    promoted = json.loads(grant.canonical_document)
    promoted["route_enablement_permitted"] = True
    promoted_document = json.dumps(promoted, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    signature = __import__("hmac").new(b"k" * 32, promoted_document.encode(), hashlib.sha256).hexdigest()
    blocked = verify_serialized_grant(
        issuer._keys,
        canonical_document=promoted_document,
        signature_hex=signature,
        key_id=grant.key_id,
        presented=presented,
        now=NOW,
    )
    assert blocked.rejection is Sprint3DRejection.ROUTE_ENABLEMENT_BLOCKED
    assert coordination.request is not None
    assert expired.request is None


def test_same_token_cannot_issue_a_second_invocation():
    issuer, established, _envelope, grant, _coordination, *_rest = _issued()
    assert grant is not None and established.principal is not None
    _session, _plan, _step, _invocation, _server, _policy_envelope, evaluation = _retain(
        master_request_id="master-req-3d-2",
        correlation_id="corr-3d-2",
    )
    authentication = authenticate_bearer(_token(_claims()), _provider(), NOW)
    second = establish_trusted_principal(
        authentication,
        evaluation,
        original_planning_session_id="origin-session-3d-b",
        now=NOW,
    )
    assert second.principal is not None
    envelope = replace(
        _envelope,
        master_request_id=second.principal.master_request_id,
        planning_session_id=second.principal.planning_session_id,
        step_id=second.principal.step_id,
        invocation_id=second.principal.invocation_id,
        principal_ref=second.principal.principal_ref,
        correlation_id=second.principal.correlation_id,
        idempotency_key=second.principal.idempotency_key,
    )
    outcome_grant, outcome = issuer.issue(second, envelope, now=NOW)
    assert outcome_grant is None
    assert outcome.rejection is Sprint3DRejection.REPLAY


def test_retired_signing_key_stops_verification():
    issuer, established, envelope, grant, _coordination, *_rest = _issued()
    assert grant is not None and established.principal is not None
    issuer._keys.rotate("grant-2", b"m" * 32)
    presented = presented_binding_from_principal(established.principal, envelope)
    still_valid = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=presented,
        now=NOW,
    )
    assert still_valid.accepted is True
    issuer._keys.retire(grant.key_id)
    retired = verify_serialized_grant(
        issuer._keys,
        canonical_document=grant.canonical_document,
        signature_hex=grant.signature_hex,
        key_id=grant.key_id,
        presented=presented,
        now=NOW,
    )
    assert retired.rejection is Sprint3DRejection.KEY_RETIRED


def test_same_account_invoke_is_reviewed_and_does_not_enable_the_route():
    closed = review_lambda_invoke_paths(
        resource_policy=_resource_policy(),
        identity_statements=(),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert closed.document_review_accepted is True
    assert closed.described_same_account_posture == "closed"
    assert closed.route_enablement_permitted is False
    assert closed.live_aws_controls_verified is False
    assert closed.live_blockers == ISSUANCE_CONTROL_IDS
    assert route_enablement_permitted(closed) is False
    allowed = SameAccountInvokeStatement(
        "arn:aws:iam::123456789012:role/dev",
        "Allow",
        "lambda:InvokeFunction",
        FUNCTION_ARN,
    )
    open_review = review_lambda_invoke_paths(
        resource_policy=_resource_policy(),
        identity_statements=(allowed,),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert open_review.document_review_accepted is False
    assert open_review.rejection is Sprint3DRejection.SAME_ACCOUNT_INVOKE_ALLOWED
    denied = SameAccountInvokeStatement("*", "Deny", "lambda:InvokeFunction", "*")
    covered = review_lambda_invoke_paths(
        resource_policy=_resource_policy(),
        identity_statements=(allowed, denied),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert covered.document_review_accepted is True
    assert covered.route_enablement_permitted is False
    wildcard = review_lambda_invoke_paths(
        resource_policy=_resource_policy(source=SOURCE_ARN.replace("abc123", "*")),
        identity_statements=(),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert Sprint3DRejection.WILDCARD_INVOKE in wildcard.document_defects
    other_principal = review_lambda_invoke_paths(
        resource_policy=_resource_policy(principal="lambda.amazonaws.com"),
        identity_statements=(),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert Sprint3DRejection.OTHER_INVOKE_PRINCIPAL in other_principal.document_defects
    function_url = review_lambda_invoke_paths(
        resource_policy=_resource_policy(),
        identity_statements=(),
        identity_policies_enumerated=True,
        function_url_present=True,
        async_invoke_sources=("sns",),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert Sprint3DRejection.FUNCTION_URL in function_url.document_defects
    assert Sprint3DRejection.ASYNC_INVOKE_SOURCE in function_url.document_defects
    unreviewed = review_lambda_invoke_paths(
        resource_policy=_resource_policy(),
        identity_statements=(),
        identity_policies_enumerated=False,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
    )
    assert unreviewed.rejection is Sprint3DRejection.SAME_ACCOUNT_INVOKE_UNREVIEWED
    forged_live = review_lambda_invoke_paths(
        resource_policy={"satisfied": True},
        identity_statements=(),
        identity_policies_enumerated=True,
        function_url_present=False,
        async_invoke_sources=(),
        expected_source_arn=SOURCE_ARN,
        expected_function_arn=FUNCTION_ARN,
        live_aws_api_called=True,
    )
    assert forged_live.rejection is Sprint3DRejection.FORGED_LIVE_AWS_CLAIM
    assert forged_live.live_aws_controls_verified is False


def test_closed_issuers_constants_and_route_gate_stay_shut():
    assert PRODUCTION_GRANT_ISSUANCE_AVAILABLE is False
    assert PRODUCTION_SIGNING_KEY_STORE_INTEGRATED is False
    assert HMAC_PREVENTS_REPLAY is False
    assert HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is False
    assert HOST_GRANT_ISSUER_AVAILABLE is False
    with pytest.raises(ContractValidationError):
        issue_production_execution_grant()
    with pytest.raises(ContractValidationError):
        issue_production_application_principal()
    assert_closed_production_entrypoints()
    with pytest.raises(ContractValidationError, match="caller_constructed_principal"):
        Sprint3DTrustedPrincipal(
            principal_ref="pr_caller",
            verified_subject=SUBJECT,
            issuer=ISSUER,
            audience=AUDIENCE,
            scopes=(SCOPE,),
            token_jti=JTI,
            token_expires_at_epoch=1,
            master_request_id="master-req-3d-1",
            planning_session_id=ORIGIN_SESSION,
            policy_session_id="policy-session",
            step_id="live-1",
            invocation_id="invocation-1",
            correlation_id="corr-3d-1",
            idempotency_key="idem-3d-1",
            tool_name="get_aircraft_operational_context",
            resource_id="aircraft",
            operation="GET_ITEM",
            region_scope=("CA",),
            purpose=ANSWER,
            policy_revision=POLICY_REVISION,
            policy_fingerprint="a" * 64,
            accepted_at_utc=ACCEPTED,
            deadline_utc=DEADLINE,
            route_key="POST /ai/query",
            grant_eligible=True,
            idp_authenticated=True,
        )
    with pytest.raises(ContractValidationError, match="caller_constructed_authentication"):
        BearerAuthentication(
            authenticated=True,
            rejection=None,
            subject=SUBJECT,
            issuer=ISSUER,
            audience=AUDIENCE,
            scopes=(SCOPE,),
            jti=JTI,
            issued_at_epoch=1,
            not_before_epoch=1,
            expires_at_epoch=2,
            signing_kid=KID,
            signature_verified=True,
            event_subject_ignored=None,
        )
    with pytest.raises(ContractValidationError, match="weak_signing_key"):
        InjectedGrantSigningKeys(active_key_id="grant-1", keys={"grant-1": b"short"})
    api = (REPO / "modules" / "operational_api" / "api.tf").read_text(encoding="utf-8")
    variables = (REPO / "modules" / "operational_api" / "variables.tf").read_text(encoding="utf-8")
    gate = variables.split('variable "ai_query_live_aws_controls_verified"', 1)[1].split("variable ", 1)[0]
    enable = variables.split('variable "enable_ai_query_route"', 1)[1].split('variable "ai_jwt_issuer"', 1)[0]
    assert "default     = false" in enable
    assert "default     = false" in gate
    assert "authorization_scopes" not in api
    assert "var.enable_ai_query_route && var.ai_query_live_aws_controls_verified" in api
    assert "POST /ai/query stays absent until the live AWS issuance controls are verified." in api
    assert api.count("lambda:InvokeFunction") == 3
    assert isinstance(AtomicConsumeCoordination(False, Sprint3DRejection.UNAUTHENTICATED, None), AtomicConsumeCoordination)
    assert isinstance(HostSignedGrant, type)
    assert isinstance(LambdaInvokePathReview, type)
    assert isinstance(AgentCPresentedBinding, type)
