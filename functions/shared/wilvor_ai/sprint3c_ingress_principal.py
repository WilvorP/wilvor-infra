"""Sprint 3C host-side ingress authentication and trusted principal.

This module composes the sealed Sprint 3B.2 evaluators. It does not
replace them. The Lambda event, a JWT-looking dictionary, the
Authorization header, and Lambda context stay untrusted observations.
Independent host authority is unavailable, so public policy evaluation
stays ``DENY`` with ``PolicyAuthorityOrigin.NOT_ESTABLISHED``.

A production principal handle would use
``ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION``, which is
distinct from ``PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION``. That
handle is minted only after an operator-owned twelve-control manifest
and live AWS invoke-boundary evidence exist. This sprint's enable flag
stays false. Missing deployment and IAM proof remain unresolved. This
module does not fabricate a successful AWS authentication result.

``POST /ai/query`` stays disabled. Existing ``PRODUCTION_*``,
``*_AVAILABLE``, and ``*_ENABLED`` constants are not assigned here.

Agent B consumes ``Sprint3CPrincipalPolicyHandoff``: the sealed
synthetic principal, the DENY policy evaluation, and the original
planning-session identity. A DENY evaluation is not a grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_application_principal import (
    HOST_INGRESS_PROOF_REQUIREMENTS,
    PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE,
    HostIngressProofChecklist,
    HostIngressProofRequirement,
    HostProofStanding,
    IndependentlyHostAuthenticatedIdentityInput,
    NonproductionContractSeal,
    PrincipalRecordKind,
    ServerOwnedPrincipalDescriptor,
    UntrustedGatewayClaimObservation,
    issue_production_application_principal,
    reject_untrusted_authority,
)
from wilvor_ai.master_contracts import MasterPurpose
from wilvor_ai.master_gateway_identity import (
    APPLICATION_OWNED_PRINCIPAL_AVAILABLE,
    GATEWAY_INVOCATION_ATTESTATION_AVAILABLE,
    PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE,
    UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION,
    InvocationProvenanceStanding,
    MasterGatewayIdentityAssessment,
    MasterGatewayIdentityConfiguration,
    reject_claimed_principal,
)
from wilvor_ai.master_gateway_runtime_boundary import (
    ISSUANCE_CONTROL_IDS,
    REQUIRED_GATEWAY_PRINCIPAL,
    REQUIRED_INVOKE_ACTION,
    REQUIRED_ROUTE_KEY,
    REQUIRED_STAGE_NAME,
    RUNTIME_ADMISSION_ATTESTATION_AVAILABLE,
    RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE,
    ConfigurationDenialReason,
    ConfigurationRestrictionDecision,
    EvidenceStatus,
    GatewayRuntimeBoundaryReport,
    IssuanceControl,
    RuntimeEvidenceClass,
    RuntimeFactName,
    ServerOwnedGatewayManifest,
    assess_gateway_runtime_boundary,
    current_disabled_route_manifest,
    expected_execution_arn,
    expected_source_arn,
)
from wilvor_ai.master_host_ingress_policy_integration import (
    NONPRODUCTION_BINDING_REDEEMABLE,
    PRODUCTION_INGRESS_INTEGRATION_AVAILABLE,
    NonproductionIdentityPolicyBinding,
    open_nonproduction_ingress_policy_port,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    HOST_GRANT_ISSUER_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyEvaluation,
    PolicyAuthorityOrigin,
    ServerPolicyInputs,
)


SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION = "wilvor.ai.sprint3c.ingress_principal.v1"

# No constructor, environment variable, or event field assigns these.
SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED = False
SPRINT3C_INGRESS_ATTESTATION_AVAILABLE = False
SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE = False
SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED = False

_BIND = object()
_REQUIRED_SOURCE_ARN_SUFFIX = f"/{REQUIRED_STAGE_NAME}/POST/ai/query"
_LIVE_FACTS = (
    RuntimeFactName.LIVE_RESOURCE_POLICY,
    RuntimeFactName.LIVE_IDENTITY_POLICY,
    RuntimeFactName.DEPLOYMENT,
    RuntimeFactName.LIVE_LOGS,
    RuntimeFactName.LIVE_QUALIFIER,
    RuntimeFactName.LIVE_FUNCTION_URL,
)
_EVENT_SHAPED_KEYS = frozenset(
    {
        "requestcontext",
        "authorizer",
        "headers",
        "routekey",
        "rawpath",
        "authorization",
        "jwt",
        "claims",
        "iss",
        "aud",
        "sub",
        "trusted",
    }
)


class ProductionPrincipalRecordKind(str, Enum):
    """Would be stored on a host-issued production principal.

    Distinct from ``PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION``. No
    handle minted in this sprint carries this value.
    """

    REVIEWED_HOST_ADMISSION = "REVIEWED_HOST_ADMISSION"


class IngressMaterialClass(str, Enum):
    """Where one ingress input came from. These are not interchangeable."""

    SERVER_OWNED_CONFIGURATION = "SERVER_OWNED_CONFIGURATION"
    OPERATOR_OWNED_UNRESOLVED_CONTROL = "OPERATOR_OWNED_UNRESOLVED_CONTROL"
    CALLER_CREATED_EVENT_FIELD = "CALLER_CREATED_EVENT_FIELD"
    LAMBDA_CONTEXT_OBSERVATION = "LAMBDA_CONTEXT_OBSERVATION"
    TRUSTED_FLAG_OBSERVATION = "TRUSTED_FLAG_OBSERVATION"
    HOST_IDENTITY_INPUT_NOT_AUTHENTICATION = "HOST_IDENTITY_INPUT_NOT_AUTHENTICATION"


class Sprint3CIngressLimitation(str, Enum):
    """Standing limits. The full set is present on every result."""

    SYNTHETIC_ADMISSION_IS_NOT_A_PRODUCTION_PRINCIPAL = (
        "SYNTHETIC_ADMISSION_IS_NOT_A_PRODUCTION_PRINCIPAL"
    )
    PRODUCTION_RECORD_KIND_NOT_MINTED = "PRODUCTION_RECORD_KIND_NOT_MINTED"
    EVENT_IS_NOT_INVOCATION_ATTESTATION = "EVENT_IS_NOT_INVOCATION_ATTESTATION"
    JWT_CLAIMS_ARE_NOT_INVOCATION_ATTESTATION = (
        "JWT_CLAIMS_ARE_NOT_INVOCATION_ATTESTATION"
    )
    AUTHORIZATION_HEADER_IS_NOT_INVOCATION_ATTESTATION = (
        "AUTHORIZATION_HEADER_IS_NOT_INVOCATION_ATTESTATION"
    )
    LAMBDA_CONTEXT_IS_NOT_INVOCATION_ATTESTATION = (
        "LAMBDA_CONTEXT_IS_NOT_INVOCATION_ATTESTATION"
    )
    TRUSTED_FLAG_IS_NOT_INVOCATION_ATTESTATION = (
        "TRUSTED_FLAG_IS_NOT_INVOCATION_ATTESTATION"
    )
    DIRECT_INVOKE_CAN_FORGE_HTTP_API_EVENT = "DIRECT_INVOKE_CAN_FORGE_HTTP_API_EVENT"
    SAME_ACCOUNT_IAM_INVOKE_UNREVIEWED = "SAME_ACCOUNT_IAM_INVOKE_UNREVIEWED"
    LIVE_AWS_DEPLOYMENT_EVIDENCE_MISSING = "LIVE_AWS_DEPLOYMENT_EVIDENCE_MISSING"
    ISSUANCE_CONTROLS_UNSATISFIED = "ISSUANCE_CONTROLS_UNSATISFIED"
    HOST_INGRESS_PROOFS_UNMET = "HOST_INGRESS_PROOFS_UNMET"
    INDEPENDENT_HOST_AUTHORITY_UNAVAILABLE = "INDEPENDENT_HOST_AUTHORITY_UNAVAILABLE"
    PUBLIC_POLICY_REMAINS_DENY = "PUBLIC_POLICY_REMAINS_DENY"
    DENY_EVALUATION_IS_NOT_A_GRANT = "DENY_EVALUATION_IS_NOT_A_GRANT"
    POST_AI_QUERY_REMAINS_DISABLED = "POST_AI_QUERY_REMAINS_DISABLED"
    ORIGINAL_PLANNING_SESSION_IS_NOT_AUTHENTICATION = (
        "ORIGINAL_PLANNING_SESSION_IS_NOT_AUTHENTICATION"
    )
    PRODUCTION_ISSUANCE_UNAVAILABLE = "PRODUCTION_ISSUANCE_UNAVAILABLE"


SPRINT3C_INGRESS_LIMITATIONS = tuple(Sprint3CIngressLimitation)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed(value: object, expected: object, code: str) -> None:
    if value is not expected:
        _fail(code)


def _require_closed() -> None:
    if SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED is not False:
        _fail("production_principal_issuance_unavailable")
    if SPRINT3C_INGRESS_ATTESTATION_AVAILABLE is not False:
        _fail("ingress_attestation_unresolved")
    if SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE is not False:
        _fail("live_aws_fact_not_inspected")
    if SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED is not False:
        _fail("reviewed_host_authority_not_wired")
    if PRODUCTION_INGRESS_INTEGRATION_AVAILABLE is not False:
        _fail("production_principal_issuance_unavailable")
    if NONPRODUCTION_BINDING_REDEEMABLE is not False:
        _fail("nonproduction_binding_not_redeemable")
    if (
        HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is not False
        or HOST_GRANT_ISSUER_AVAILABLE is not False
    ):
        _fail("host_authenticated_principal_not_available")
    if (
        PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE is not False
        or PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE is not False
        or APPLICATION_OWNED_PRINCIPAL_AVAILABLE is not False
        or GATEWAY_INVOCATION_ATTESTATION_AVAILABLE is not False
        or UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION is not False
        or RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE is not False
        or RUNTIME_ADMISSION_ATTESTATION_AVAILABLE is not False
    ):
        _fail("production_issuance_unavailable")


def _unsatisfied_controls() -> tuple[IssuanceControl, ...]:
    return tuple(
        IssuanceControl(control_id, True, False) for control_id in ISSUANCE_CONTROL_IDS
    )


def _event_shaped(value: object) -> bool:
    if type(value) is not dict:
        return False
    names = {key.casefold() for key in value if type(key) is str}
    return bool(names & _EVENT_SHAPED_KEYS)


def _reject_event_as_configuration(value: object) -> None:
    if _event_shaped(value):
        _fail("event_field_not_verified")
    if type(value) is dict:
        _fail("configuration_not_taken_from_request")


class Sprint3CProductionPrincipalHandle:
    """Host-issued production principal. No instance can exist this sprint.

    ``record_kind`` is ``REVIEWED_HOST_ADMISSION``, which is not
    ``SYNTHETIC_TEST_ADMISSION``. Construction always raises.
    """

    record_kind = ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise ContractValidationError(
            (
                "production_principal_issuance_unavailable",
                "reviewed_host_authority_not_wired",
                "issuance_control_not_satisfied",
                "live_aws_fact_not_inspected",
            )
        )


@dataclass(frozen=True)
class ReviewedIamInvocationRestriction:
    """Intended Lambda invoke restrictions. Not a live ``GetPolicy`` result.

    Same-account identity-policy invoke remains an alternate path until an
    operator reviews it in AWS. This object cannot record that review.
    """

    gateway_service_principal: str = REQUIRED_GATEWAY_PRINCIPAL
    invoke_action: str = REQUIRED_INVOKE_ACTION
    required_route: str = REQUIRED_ROUTE_KEY
    required_stage: str = REQUIRED_STAGE_NAME
    required_source_arn_suffix: str = _REQUIRED_SOURCE_ARN_SUFFIX
    described_source_arn: str = ""
    described_account_id: str = ""
    wildcard_invoke_allowed: bool = False
    other_principals_allowed: bool = False
    function_url_allowed: bool = False
    operational_lambda_reuse_allowed: bool = False
    authorization_scopes_edge_enforced: bool = False
    same_account_identity_policy_reviewed: bool = False
    live_resource_policy_inspected: bool = False
    live_identity_policy_inspected: bool = False
    standing: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    production_invoke_attested: bool = False

    def __post_init__(self) -> None:
        if self.gateway_service_principal != REQUIRED_GATEWAY_PRINCIPAL:
            _fail("invalid_principal")
        if self.invoke_action != REQUIRED_INVOKE_ACTION:
            _fail("invalid_permission")
        if self.required_route != REQUIRED_ROUTE_KEY:
            _fail("unexpected_route")
        if self.required_stage != REQUIRED_STAGE_NAME:
            _fail("invalid_stage")
        if self.required_source_arn_suffix != _REQUIRED_SOURCE_ARN_SUFFIX:
            _fail("invalid_permission")
        if type(self.described_source_arn) is not str:
            _fail("invalid_permission")
        if type(self.described_account_id) is not str:
            _fail("invalid_account")
        _closed(self.wildcard_invoke_allowed, False, "wildcard_invoke_not_permitted")
        _closed(self.other_principals_allowed, False, "other_principal_invoke")
        _closed(self.function_url_allowed, False, "open_alternative_invocation_path")
        _closed(
            self.operational_lambda_reuse_allowed,
            False,
            "operational_lambda_reuse",
        )
        _closed(
            self.authorization_scopes_edge_enforced,
            False,
            "reviewed_host_authority_not_wired",
        )
        _closed(
            self.same_account_identity_policy_reviewed,
            False,
            "reviewed_host_authority_not_wired",
        )
        _closed(self.live_resource_policy_inspected, False, "live_aws_fact_not_inspected")
        _closed(self.live_identity_policy_inspected, False, "live_aws_fact_not_inspected")
        if self.standing is not EvidenceStatus.NOT_INSPECTED:
            _fail("live_aws_fact_not_inspected")
        _closed(self.production_invoke_attested, False, "runtime_admission_not_attested")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class MissingAwsDeploymentEvidence:
    """Live AWS facts this process has not inspected and cannot invent."""

    live_api_id: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_account: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_region: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_resource_policy: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_identity_policy: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_qualifier: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_function_url: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_log_group: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    live_deployment: EvidenceStatus = EvidenceStatus.NOT_INSPECTED
    authenticated: bool = False
    evidence_class: RuntimeEvidenceClass = RuntimeEvidenceClass.NOT_YET_INSPECTED

    def __post_init__(self) -> None:
        for status in (
            self.live_api_id,
            self.live_account,
            self.live_region,
            self.live_resource_policy,
            self.live_identity_policy,
            self.live_qualifier,
            self.live_function_url,
            self.live_log_group,
            self.live_deployment,
        ):
            if status is not EvidenceStatus.NOT_INSPECTED:
                _fail("live_aws_fact_not_inspected")
        _closed(self.authenticated, False, "live_aws_fact_not_inspected")
        if self.evidence_class is not RuntimeEvidenceClass.NOT_YET_INSPECTED:
            _fail("live_aws_fact_not_inspected")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class OperatorOwnedIssuanceControlManifest:
    """Operator-owned twelve-control evidence. Not built from the event.

    Every control stays unsatisfied. Live AWS inspection cannot be
    recorded here. A caller-supplied ``satisfied=True`` is rejected by
    ``IssuanceControl``.
    """

    name_prefix: str
    controls: tuple[IssuanceControl, ...]
    iam_restriction: ReviewedIamInvocationRestriction
    aws_deployment_evidence: MissingAwsDeploymentEvidence
    live_aws_inspected: bool = False
    schema_version: str = SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.name_prefix) is not str or self.name_prefix == "":
            _fail("invalid_name_prefix")
        if self.controls != _unsatisfied_controls():
            _fail("issuance_control_not_satisfied")
        if type(self.iam_restriction) is not ReviewedIamInvocationRestriction:
            _fail("reviewed_host_authority_not_wired")
        if type(self.aws_deployment_evidence) is not MissingAwsDeploymentEvidence:
            _fail("live_aws_fact_not_inspected")
        _closed(self.live_aws_inspected, False, "live_aws_fact_not_inspected")
        if self.schema_version != SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION:
            _fail("invalid_identity_input")
        if any(control.satisfied for control in self.controls):
            _fail("issuance_control_not_satisfied")

    def __bool__(self) -> bool:
        return False


def unresolved_operator_issuance_controls(
    *,
    name_prefix: str,
    described_account_id: str = "",
    described_source_arn: str = "",
) -> OperatorOwnedIssuanceControlManifest:
    """Build the only operator manifest this sprint can represent."""

    _require_closed()
    return OperatorOwnedIssuanceControlManifest(
        name_prefix=name_prefix,
        controls=_unsatisfied_controls(),
        iam_restriction=ReviewedIamInvocationRestriction(
            described_account_id=described_account_id,
            described_source_arn=described_source_arn,
        ),
        aws_deployment_evidence=MissingAwsDeploymentEvidence(),
    )


@dataclass(frozen=True)
class ClassifiedIngressMaterial:
    """One classified input. ``accepted_as_attestation`` cannot be true."""

    name: str
    material_class: IngressMaterialClass
    evidence_class: RuntimeEvidenceClass
    accepted_as_attestation: bool = False

    def __post_init__(self) -> None:
        if type(self.name) is not str or self.name == "":
            _fail("invalid_identity_material")
        if type(self.material_class) is not IngressMaterialClass:
            _fail("invalid_identity_material")
        if type(self.evidence_class) is not RuntimeEvidenceClass:
            _fail("invalid_identity_material")
        _closed(self.accepted_as_attestation, False, "event_field_not_verified")
        if self.material_class is IngressMaterialClass.CALLER_CREATED_EVENT_FIELD:
            if self.evidence_class is RuntimeEvidenceClass.VERIFIED_CONFIGURATION:
                _fail("event_field_not_verified")
        if self.material_class is IngressMaterialClass.SERVER_OWNED_CONFIGURATION:
            if self.evidence_class is RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD:
                _fail("configuration_not_taken_from_request")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint3CPrincipalPolicyHandoff:
    """Identity Agent B consumes. Not a grant and not production authority.

    ``planning_session_id`` is the original
    ``MasterPlanningSessionRecord.session_id``. The policy evaluation's
    ``planning_session_id`` is the policy-session label and is not that
    identity. ``policy_evaluation.principal_ref`` stays ``None`` because
    independent host authority is unavailable. ``principal_ref`` on this
    handoff is the synthetic descriptor handle.
    """

    principal: ServerOwnedPrincipalDescriptor
    policy_evaluation: MasterRuntimePolicyEvaluation
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    principal_ref: str
    policy_revision: str
    purpose: MasterPurpose
    accepted_at_utc: str
    deadline_utc: str
    production_principal: bool = False
    grant_eligible: bool = False
    policy_decision: MasterRuntimePolicyDecision = MasterRuntimePolicyDecision.DENY
    authority_origin: PolicyAuthorityOrigin = PolicyAuthorityOrigin.NOT_ESTABLISHED
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_closed()
        if self._seal is not _BIND:
            _fail("public_constructor_not_production_principal")
        if type(self.principal) is not ServerOwnedPrincipalDescriptor:
            _fail("synthetic_admission_not_production_principal")
        if type(self.policy_evaluation) is not MasterRuntimePolicyEvaluation:
            _fail("invalid_policy_evaluation")
        if self.principal.record_kind is not PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION:
            _fail("synthetic_admission_not_production_principal")
        _closed(self.principal.production_principal, False, "production_principal_not_issued")
        _closed(self.production_principal, False, "production_principal_not_issued")
        _closed(self.grant_eligible, False, "policy_evaluation_is_not_a_permit")
        if self.policy_decision is not MasterRuntimePolicyDecision.DENY:
            _fail("production_policy_result_must_deny")
        if self.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
            _fail("principal_not_host_authenticated")
        if self.policy_evaluation.decision is not MasterRuntimePolicyDecision.DENY:
            _fail("production_policy_result_must_deny")
        if self.policy_evaluation.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
            _fail("principal_not_host_authenticated")
        if self.policy_evaluation.principal_ref is not None:
            _fail("principal_not_host_authenticated")
        if self.principal_ref != self.principal.principal_ref:
            _fail("principal_descriptor_immutable")
        if self.planning_session_id != self.principal.planning_session_id:
            _fail("cross_session_principal")
        if self.master_request_id != self.principal.master_request_id:
            _fail("confused_deputy")
        if self.purpose is not self.principal.purpose:
            _fail("purpose_mismatch")
        if self.policy_revision != POLICY_REVISION:
            _fail("policy_revision_mismatch")
        if self.policy_revision != self.principal.policy_revision:
            _fail("policy_revision_mismatch")
        if self.policy_evaluation.master_request_id != self.master_request_id:
            _fail("master_request_mismatch")
        if self.policy_evaluation.step_id != self.step_id:
            _fail("wrong_invocation")
        if self.policy_evaluation.invocation_id != self.invocation_id:
            _fail("wrong_invocation")
        if self.policy_evaluation.accepted_at_utc != self.accepted_at_utc:
            _fail("invalid_policy_evaluation")
        if self.policy_evaluation.deadline_utc != self.deadline_utc:
            _fail("invalid_policy_evaluation")
        if self.planning_session_id == self.policy_evaluation.planning_session_id:
            _fail("matching_identifier_not_proof")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint3CIngressPrincipalResult:
    """Ingress assessment plus a non-production principal. Not a permit."""

    binding: NonproductionIdentityPolicyBinding
    handoff: Sprint3CPrincipalPolicyHandoff
    original_planning_session_id: str
    production_handle: None
    intended_production_record_kind: ProductionPrincipalRecordKind
    production_principal_issued: bool
    production_route_enabled: bool
    independent_host_authority_available: bool
    verified_upstream_authentication: bool
    caller_event_fields_accepted_as_attestation: bool
    grant_eligible: bool
    operator_controls: OperatorOwnedIssuanceControlManifest
    iam_restriction: ReviewedIamInvocationRestriction
    aws_deployment_evidence: MissingAwsDeploymentEvidence
    disabled_route_report: GatewayRuntimeBoundaryReport
    classified_materials: tuple[ClassifiedIngressMaterial, ...]
    unmet_host_ingress_proofs: tuple[HostIngressProofRequirement, ...]
    unsatisfied_issuance_control_ids: tuple[str, ...]
    host_ingress_proof_standing: HostProofStanding
    limitations: tuple[Sprint3CIngressLimitation, ...]
    schema_version: str = SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_closed()
        if self._seal is not _BIND:
            _fail("public_constructor_not_production_principal")
        if self.schema_version != SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION:
            _fail("invalid_identity_input")
        if type(self.binding) is not NonproductionIdentityPolicyBinding:
            _fail("nonproduction_binding_not_redeemable")
        if type(self.handoff) is not Sprint3CPrincipalPolicyHandoff:
            _fail("invalid_policy_evaluation")
        if type(self.disabled_route_report) is not GatewayRuntimeBoundaryReport:
            _fail("invalid_report")
        if type(self.operator_controls) is not OperatorOwnedIssuanceControlManifest:
            _fail("issuance_control_not_satisfied")
        if type(self.iam_restriction) is not ReviewedIamInvocationRestriction:
            _fail("reviewed_host_authority_not_wired")
        if type(self.aws_deployment_evidence) is not MissingAwsDeploymentEvidence:
            _fail("live_aws_fact_not_inspected")
        if self.production_handle is not None:
            _fail("production_principal_issuance_unavailable")
        if (
            self.intended_production_record_kind
            is not ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION
        ):
            _fail("production_principal_issuance_unavailable")
        if (
            self.intended_production_record_kind.value
            == PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION.value
        ):
            _fail("synthetic_admission_not_production_principal")
        _closed(self.production_principal_issued, False, "production_principal_not_issued")
        _closed(self.production_route_enabled, False, "production_issuance_unavailable")
        _closed(
            self.independent_host_authority_available,
            False,
            "host_authenticated_principal_not_available",
        )
        _closed(self.verified_upstream_authentication, False, "ingress_attestation_unresolved")
        _closed(
            self.caller_event_fields_accepted_as_attestation,
            False,
            "event_field_not_verified",
        )
        _closed(self.grant_eligible, False, "policy_evaluation_is_not_a_permit")
        if self.original_planning_session_id != self.binding.planning_session_id:
            _fail("cross_session_principal")
        if self.original_planning_session_id != self.handoff.planning_session_id:
            _fail("cross_session_principal")
        if self.original_planning_session_id == self.binding.policy_session_id:
            _fail("matching_identifier_not_proof")
        if (
            self.disabled_route_report.configuration_decision
            is not ConfigurationRestrictionDecision.DENIED
        ):
            _fail("production_issuance_unavailable")
        if (
            ConfigurationDenialReason.ROUTE_DISABLED_BY_DEFAULT
            not in self.disabled_route_report.denial_reasons
        ):
            _fail("production_issuance_unavailable")
        if self.disabled_route_report.allows_production_issuance() is not False:
            _fail("production_issuance_unavailable")
        if self.unsatisfied_issuance_control_ids != ISSUANCE_CONTROL_IDS:
            _fail("issuance_control_not_satisfied")
        if self.unmet_host_ingress_proofs != HOST_INGRESS_PROOF_REQUIREMENTS:
            _fail("reviewed_host_authority_not_wired")
        if self.host_ingress_proof_standing is not HostProofStanding.UNRESOLVED:
            _fail("reviewed_host_authority_not_wired")
        if self.limitations != SPRINT3C_INGRESS_LIMITATIONS:
            _fail("invalid_identity_input")
        if type(self.classified_materials) is not tuple or not self.classified_materials:
            _fail("invalid_identity_material")
        for material in self.classified_materials:
            if type(material) is not ClassifiedIngressMaterial:
                _fail("invalid_identity_material")
            if material.accepted_as_attestation is not False:
                _fail("event_field_not_verified")
        principal = self.binding.principal
        evaluation = self.binding.policy_evaluation
        if principal.record_kind is not PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION:
            _fail("synthetic_admission_not_production_principal")
        if principal.production_principal is not False:
            _fail("production_principal_not_issued")
        if evaluation.decision is not MasterRuntimePolicyDecision.DENY:
            _fail("production_policy_result_must_deny")
        if evaluation.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
            _fail("principal_not_host_authenticated")
        if evaluation.principal_ref is not None:
            _fail("principal_not_host_authenticated")
        report = self.binding.boundary_report
        for name in _LIVE_FACTS:
            fact = report.fact(name)
            if (
                fact.evidence_class is not RuntimeEvidenceClass.NOT_YET_INSPECTED
                or fact.status is not EvidenceStatus.NOT_INSPECTED
            ):
                _fail("live_aws_fact_not_inspected")
        if report.invocation_standing is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("runtime_admission_not_attested")
        if report.event_accepted_as_invocation_source is not False:
            _fail("event_field_not_verified")

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    def __deepcopy__(self, memo: object) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    def __reduce__(self) -> object:
        _fail("copied_principal_not_accepted")
        raise AssertionError("unreachable")

    @property
    def principal(self) -> ServerOwnedPrincipalDescriptor:
        return self.binding.principal

    @property
    def policy_evaluation(self) -> MasterRuntimePolicyEvaluation:
        return self.binding.policy_evaluation

    @property
    def identity_assessment(self) -> MasterGatewayIdentityAssessment:
        return self.binding.identity_assessment

    @property
    def boundary_report(self) -> GatewayRuntimeBoundaryReport:
        return self.binding.boundary_report

    @property
    def observation(self) -> UntrustedGatewayClaimObservation:
        return self.binding.observation

    @property
    def principal_ref(self) -> str:
        return self.handoff.principal_ref

    @property
    def planning_session_id(self) -> str:
        return self.original_planning_session_id

    @property
    def master_request_id(self) -> str:
        return self.handoff.master_request_id

    @property
    def step_id(self) -> str:
        return self.handoff.step_id

    @property
    def invocation_id(self) -> str:
        return self.handoff.invocation_id

    def agent_b_handoff(self) -> Sprint3CPrincipalPolicyHandoff:
        """Return the sealed identity Agent B consumes. Not a grant."""

        return self.handoff

    def redeem(self, *_args: object, **_kwargs: object) -> None:
        _fail("nonproduction_binding_not_redeemable")


def _described_source_arn(manifest: ServerOwnedGatewayManifest) -> str:
    if not (
        manifest.partition
        and manifest.region
        and manifest.account_id
        and manifest.api_id
    ):
        return ""
    return expected_source_arn(
        expected_execution_arn(
            manifest.partition,
            manifest.region,
            manifest.account_id,
            manifest.api_id,
        ),
        REQUIRED_STAGE_NAME,
    )


def _classify_materials(
    *,
    event_presented: bool,
    context_presented: bool,
    trusted_presented: bool,
) -> tuple[ClassifiedIngressMaterial, ...]:
    rows = [
        ClassifiedIngressMaterial(
            "server_owned_manifest",
            IngressMaterialClass.SERVER_OWNED_CONFIGURATION,
            RuntimeEvidenceClass.VERIFIED_CONFIGURATION,
        ),
        ClassifiedIngressMaterial(
            "server_owned_identity_configuration",
            IngressMaterialClass.SERVER_OWNED_CONFIGURATION,
            RuntimeEvidenceClass.VERIFIED_CONFIGURATION,
        ),
        ClassifiedIngressMaterial(
            "operator_owned_issuance_controls",
            IngressMaterialClass.OPERATOR_OWNED_UNRESOLVED_CONTROL,
            RuntimeEvidenceClass.NOT_YET_INSPECTED,
        ),
        ClassifiedIngressMaterial(
            "independently_host_authenticated_identity_input",
            IngressMaterialClass.HOST_IDENTITY_INPUT_NOT_AUTHENTICATION,
            RuntimeEvidenceClass.NOT_YET_INSPECTED,
        ),
    ]
    if event_presented:
        rows.append(
            ClassifiedIngressMaterial(
                "http_api_event",
                IngressMaterialClass.CALLER_CREATED_EVENT_FIELD,
                RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD,
            )
        )
    if context_presented:
        rows.append(
            ClassifiedIngressMaterial(
                "lambda_context",
                IngressMaterialClass.LAMBDA_CONTEXT_OBSERVATION,
                RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD,
            )
        )
    if trusted_presented:
        rows.append(
            ClassifiedIngressMaterial(
                "trusted_flag",
                IngressMaterialClass.TRUSTED_FLAG_OBSERVATION,
                RuntimeEvidenceClass.UNTRUSTED_EVENT_FIELD,
            )
        )
    return tuple(rows)


def _handoff_from_binding(
    binding: NonproductionIdentityPolicyBinding,
) -> Sprint3CPrincipalPolicyHandoff:
    return Sprint3CPrincipalPolicyHandoff(
        principal=binding.principal,
        policy_evaluation=binding.policy_evaluation,
        master_request_id=binding.master_request_id,
        planning_session_id=binding.planning_session_id,
        step_id=binding.original_step_id,
        invocation_id=binding.invocation_id,
        principal_ref=binding.synthetic_principal_ref,
        policy_revision=binding.policy_revision,
        purpose=binding.purpose,
        accepted_at_utc=binding.accepted_at_utc,
        deadline_utc=binding.deadline_utc,
        production_principal=False,
        grant_eligible=False,
        policy_decision=binding.policy_decision,
        authority_origin=binding.policy_authority_origin,
        _seal=_BIND,
    )


class Sprint3CIngressPrincipalPort:
    """In-process Sprint 3C seam. A seal string cannot open it."""

    __slots__ = ("_configuration", "_inner")

    def __init__(
        self,
        configuration: object,
        *,
        authorization_provenance: object,
        seal: object,
    ) -> None:
        _require_closed()
        if type(seal) is not NonproductionContractSeal:
            if type(seal) is str or type(seal) is bool or seal is None:
                _fail("environment_not_principal_authority")
            _fail("nonproduction_seal_required")
        if type(configuration) is not MasterGatewayIdentityConfiguration:
            _fail("configuration_not_taken_from_request")
        self._configuration = configuration
        self._inner = open_nonproduction_ingress_policy_port(
            configuration,
            authorization_provenance=authorization_provenance,
            seal=seal,
        )

    def __bool__(self) -> bool:
        return False

    def __copy__(self) -> object:
        _fail("proof_port_not_copyable")
        raise AssertionError("unreachable")

    def __deepcopy__(self, memo: object) -> object:
        _fail("proof_port_not_copyable")
        raise AssertionError("unreachable")

    def evaluate(
        self,
        *,
        manifest: object,
        event: object,
        host_identity: object,
        registry: object,
        planning_record: object,
        invocation: object,
        server: object,
        evaluation_epoch: object,
        operator_controls: object = None,
        lambda_context: object = None,
        trusted: object = None,
        claimed_principal: object = None,
        policy_step: object = None,
        policy_invocation: object = None,
        envelope_overrides: object = None,
    ) -> Sprint3CIngressPrincipalResult:
        """Bind untrusted ingress to a synthetic principal and a DENY policy.

        Production issuance stays closed. The event never selects issuer,
        audience, account, or invoke source. Independent host authority is
        unavailable, so access stays denied.
        """

        _require_closed()
        if claimed_principal is not None:
            reject_claimed_principal(claimed_principal)
        if type(manifest) is not ServerOwnedGatewayManifest:
            _reject_event_as_configuration(manifest)
            _fail("manifest_not_server_owned")
        if type(host_identity) is not IndependentlyHostAuthenticatedIdentityInput:
            reject_untrusted_authority(host_identity)
        if type(registry) is not MasterPlanningSessionRegistry:
            _fail("invalid_session")
        if type(planning_record) is not MasterPlanningSessionRecord:
            _fail("invalid_session")
        if type(server) is not ServerPolicyInputs:
            _fail("invalid_server_policy_inputs")
        if operator_controls is None:
            operator_controls = unresolved_operator_issuance_controls(
                name_prefix=manifest.name_prefix,
                described_account_id=manifest.account_id,
                described_source_arn=_described_source_arn(manifest),
            )
        else:
            _reject_event_as_configuration(operator_controls)
            if type(operator_controls) is not OperatorOwnedIssuanceControlManifest:
                _fail("issuance_control_not_satisfied")
        if operator_controls.live_aws_inspected is not False:
            _fail("live_aws_fact_not_inspected")
        if any(control.satisfied for control in operator_controls.controls):
            _fail("issuance_control_not_satisfied")
        if HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE is not False:
            _fail("host_authenticated_principal_not_available")
        disabled_report = assess_gateway_runtime_boundary(
            current_disabled_route_manifest(name_prefix=manifest.name_prefix),
            event,
            lambda_context=lambda_context,
            trusted=trusted,
        )
        binding = self._inner.bind(
            manifest=manifest,
            event=event,
            host_identity=host_identity,
            registry=registry,
            planning_record=planning_record,
            invocation=invocation,
            server=server,
            evaluation_epoch=evaluation_epoch,
            lambda_context=lambda_context,
            trusted=trusted,
            policy_step=policy_step,
            policy_invocation=policy_invocation,
            envelope_overrides=envelope_overrides,
        )
        original_session_id = planning_record.session_id
        if binding.planning_session_id != original_session_id:
            _fail("cross_session_principal")
        if binding.principal.planning_session_id != original_session_id:
            _fail("cross_session_principal")
        if binding.policy_decision is not MasterRuntimePolicyDecision.DENY:
            _fail("production_policy_result_must_deny")
        if binding.policy_authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
            _fail("principal_not_host_authenticated")
        iam_restriction = ReviewedIamInvocationRestriction(
            described_account_id=manifest.account_id,
            described_source_arn=_described_source_arn(manifest),
        )
        aws_evidence = MissingAwsDeploymentEvidence()
        checklist = HostIngressProofChecklist()
        return Sprint3CIngressPrincipalResult(
            binding=binding,
            handoff=_handoff_from_binding(binding),
            original_planning_session_id=original_session_id,
            production_handle=None,
            intended_production_record_kind=(
                ProductionPrincipalRecordKind.REVIEWED_HOST_ADMISSION
            ),
            production_principal_issued=False,
            production_route_enabled=False,
            independent_host_authority_available=False,
            verified_upstream_authentication=False,
            caller_event_fields_accepted_as_attestation=False,
            grant_eligible=False,
            operator_controls=operator_controls,
            iam_restriction=iam_restriction,
            aws_deployment_evidence=aws_evidence,
            disabled_route_report=disabled_report,
            classified_materials=_classify_materials(
                event_presented=event is not None,
                context_presented=lambda_context is not None,
                trusted_presented=trusted is not None,
            ),
            unmet_host_ingress_proofs=checklist.requirements,
            unsatisfied_issuance_control_ids=ISSUANCE_CONTROL_IDS,
            host_ingress_proof_standing=checklist.standing,
            limitations=SPRINT3C_INGRESS_LIMITATIONS,
            _seal=_BIND,
        )

    def present_again(self, result: object) -> None:
        """Present the same synthetic admission again. The proof port rejects it."""

        _require_closed()
        if type(result) is not Sprint3CIngressPrincipalResult:
            _fail("copied_principal_not_accepted")
        self._inner.present_again(result.binding)


def open_sprint3c_ingress_principal_port(
    configuration: object,
    *,
    authorization_provenance: object,
    seal: object,
) -> Sprint3CIngressPrincipalPort:
    """Open the Sprint 3C seam. ``seal`` must be minted in-process."""

    return Sprint3CIngressPrincipalPort(
        configuration,
        authorization_provenance=authorization_provenance,
        seal=seal,
    )


def refuse_policy_deny_as_execution_grant(evaluation: object) -> None:
    """A DENY evaluation is not forwarded as a grant input."""

    if type(evaluation) is not MasterRuntimePolicyEvaluation:
        _fail("invalid_policy_evaluation")
    if evaluation.decision is MasterRuntimePolicyDecision.DENY:
        _fail("policy_evaluation_is_not_a_permit")
    if evaluation.decision is MasterRuntimePolicyDecision.ALLOW:
        _fail("policy_evaluation_is_not_a_permit")
    _fail("policy_evaluation_is_not_a_permit")


def mint_sprint3c_production_principal(*candidates: object, **kwargs: object) -> None:
    """Reject production principal minting. Controls and AWS evidence are unmet."""

    _require_closed()
    if SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED is not False:
        _fail("production_principal_issuance_unavailable")
    issue_production_application_principal(*candidates, **kwargs)


def issue_sprint3c_production_ingress(*candidates: object, **kwargs: object) -> None:
    """Reject production ingress issuance. Arguments cannot open it."""

    _require_closed()
    raise ContractValidationError(
        (
            "production_principal_issuance_unavailable",
            "reviewed_host_authority_not_wired",
            "issuance_control_not_satisfied",
            "live_aws_fact_not_inspected",
            "ingress_attestation_unresolved",
        )
    )


def production_ai_query_route_is_disabled() -> bool:
    """Terraform default and this sprint keep ``POST /ai/query`` disabled."""

    _require_closed()
    return True


__all__ = [
    "SPRINT3C_AWS_DEPLOYMENT_EVIDENCE_AVAILABLE",
    "SPRINT3C_IAM_INVOKE_BOUNDARY_REVIEWED",
    "SPRINT3C_INGRESS_ATTESTATION_AVAILABLE",
    "SPRINT3C_INGRESS_LIMITATIONS",
    "SPRINT3C_INGRESS_PRINCIPAL_SCHEMA_VERSION",
    "SPRINT3C_PRODUCTION_PRINCIPAL_ISSUANCE_ENABLED",
    "ClassifiedIngressMaterial",
    "IngressMaterialClass",
    "MissingAwsDeploymentEvidence",
    "OperatorOwnedIssuanceControlManifest",
    "ProductionPrincipalRecordKind",
    "ReviewedIamInvocationRestriction",
    "Sprint3CIngressLimitation",
    "Sprint3CIngressPrincipalPort",
    "Sprint3CIngressPrincipalResult",
    "Sprint3CPrincipalPolicyHandoff",
    "Sprint3CProductionPrincipalHandle",
    "issue_sprint3c_production_ingress",
    "mint_sprint3c_production_principal",
    "open_sprint3c_ingress_principal_port",
    "production_ai_query_route_is_disabled",
    "refuse_policy_deny_as_execution_grant",
    "unresolved_operator_issuance_controls",
]
