"""Offline ingress identity to principal to policy binding. Not a permit.

This module is the Sprint 3B.2X test seam for one cross-module sequence:

1. ``assess_gateway_runtime_boundary`` classifies the Lambda event. The
   event, Lambda context, and a trusted flag stay untrusted.
2. ``assess_master_gateway_identity`` structurally parses that same event
   against server-owned configuration. A parse is not attestation.
3. ``observe_untrusted_gateway_claims`` copies those claim fields. The
   copy is a descriptive observation.
4. ``NonproductionPrincipalProofPort`` compares the observation with a
   separately supplied ``IndependentlyHostAuthenticatedIdentityInput``,
   admits one synthetic principal, and presents it once.
5. ``retain_runtime_policy_session`` keeps the original planning objects.
   ``evaluate_master_runtime_policy`` runs the real deterministic checks.
   That production evaluator returns DENY.

The returned record is ``NONPRODUCTION`` and nonredeemable. Field equality
is not authentication. A hypothetical ALLOW from the private policy
harness is not produced here and cannot be stored on this record.

Production AWS IAM and edge trust remain unmet. The record copies
``DEPLOYMENT_REVIEW_CHECKLIST``, ``ISSUANCE_CONTROL_IDS``, and
``HOST_INGRESS_PROOF_REQUIREMENTS``. None of those controls is satisfied.
There is no environment variable, constructor flag, or argument that
turns this seam into production issuance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum

from wilvor_ai.contracts import AgentCapability, ContractValidationError
from wilvor_ai.master_application_principal import (
    HOST_INGRESS_PROOF_REQUIREMENTS,
    PRODUCTION_PRINCIPAL_ISSUANCE_AVAILABLE,
    AuthorizationProvenanceStanding,
    HostIngressProofChecklist,
    HostIngressProofRequirement,
    HostProofStanding,
    IndependentlyHostAuthenticatedIdentityInput,
    NonproductionContractSeal,
    PrincipalRecordKind,
    ServerOwnedPrincipalDescriptor,
    UntrustedGatewayClaimObservation,
    observe_untrusted_gateway_claims,
    open_nonproduction_principal_proof_port,
)
from wilvor_ai.master_contracts import (
    MasterInvocationAuthorization,
    MasterProductionAuthority,
    MasterPurpose,
    MasterSpecialist,
)
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_gateway_identity import (
    APPLICATION_OWNED_PRINCIPAL_AVAILABLE,
    GATEWAY_INVOCATION_ATTESTATION_AVAILABLE,
    PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE,
    UNVERIFIED_JWT_DECODING_IS_AUTHENTICATION,
    InvocationProvenanceStanding,
    MasterGatewayIdentityAssessment,
    MasterGatewayIdentityConfiguration,
    assess_master_gateway_identity,
)
from wilvor_ai.master_gateway_runtime_boundary import (
    DEPLOYMENT_REVIEW_CHECKLIST,
    ISSUANCE_CONTROL_IDS,
    RUNTIME_ADMISSION_ATTESTATION_AVAILABLE,
    RUNTIME_BOUNDARY_ISSUANCE_AVAILABLE,
    ConfigurationRestrictionDecision,
    EvidenceStatus,
    GatewayRuntimeBoundaryReport,
    RuntimeEvidenceClass,
    RejectedInvocationProof,
    RuntimeBoundaryLimitation,
    RuntimeFactName,
    ServerOwnedGatewayManifest,
    assess_gateway_runtime_boundary,
)
from wilvor_ai.master_planning_session import (
    MasterPlanningSessionRecord,
    MasterPlanningSessionRegistry,
)
from wilvor_ai.master_read_resource_guard import server_read_budget
from wilvor_ai.master_routing import (
    MasterRequestCoverage,
    MasterRouteExecutionAuthority,
    MasterRoutePlan,
    MasterRouteStepPlan,
)
from wilvor_ai.master_runtime_policy import (
    HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE,
    HOST_GRANT_ISSUER_AVAILABLE,
    POLICY_REVISION,
    MasterRuntimePolicyDecision,
    MasterRuntimePolicyDenialReason,
    MasterRuntimePolicyEnvelope,
    MasterRuntimePolicyEvaluation,
    PolicyAuthorityOrigin,
    RuntimePolicySession,
    ServerPolicyInputs,
    evaluate_master_runtime_policy,
    retain_runtime_policy_session,
)


MASTER_HOST_INGRESS_POLICY_SCHEMA_VERSION = (
    "wilvor.ai.master_host_ingress_policy_integration.v1"
)

# No constructor, environment variable, or argument assigns these.
PRODUCTION_INGRESS_INTEGRATION_AVAILABLE = False
NONPRODUCTION_BINDING_REDEEMABLE = False

_BIND = object()
_ENVELOPE_OVERRIDES = frozenset(
    {
        "accepted_at_utc",
        "authorized",
        "caller_policy_decision",
        "capability",
        "claimed_test_mode",
        "correlation_id",
        "deadline_utc",
        "decision_target_scope",
        "historical_window",
        "invocation_id",
        "master_request_id",
        "planning_session_id",
        "policy_revision",
        "principal_ref",
        "purpose",
        "region_scope",
        "resource_scope",
        "serialized_decision",
        "specialist",
        "step_id",
    }
)
_LIVE_INVOKE_FACTS = (
    RuntimeFactName.LIVE_RESOURCE_POLICY,
    RuntimeFactName.LIVE_IDENTITY_POLICY,
    RuntimeFactName.DEPLOYMENT,
    RuntimeFactName.LIVE_FUNCTION_URL,
)


class IngressBindingDisposition(str, Enum):
    """The only disposition this seam can record."""

    NONPRODUCTION = "NONPRODUCTION"


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
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


def _selected_invocation(
    registry: MasterPlanningSessionRegistry,
    record: MasterPlanningSessionRecord,
    invocation: MasterInvocationAuthorization,
) -> MasterInvocationAuthorization:
    try:
        selected = registry.selected_invocation(record)
    except ContractValidationError as error:
        if error.errors != ("invalid_session_state",):
            raise
        selected = registry.associate_invocation(record, invocation)
    if selected is not invocation:
        _fail("wrong_invocation")
    return selected


def _member_step(
    plan: MasterRoutePlan,
    invocation: MasterInvocationAuthorization,
) -> MasterRouteStepPlan:
    matched = [step for step in plan.steps if step.invocation is invocation]
    if len(matched) != 1:
        _fail("wrong_invocation")
    return matched[0]


def _align_manifest(
    manifest: ServerOwnedGatewayManifest,
    configuration: MasterGatewayIdentityConfiguration,
) -> None:
    if manifest.route_key != configuration.route_key:
        _fail("route_mismatch")
    if (
        manifest.issuer != configuration.issuer
        or manifest.authorizer_issuer != configuration.issuer
    ):
        _fail("issuer_mismatch")
    if (
        manifest.audiences != (configuration.audience,)
        or manifest.authorizer_audiences != (configuration.audience,)
    ):
        _fail("audience_substitution")
    if manifest.required_scopes != configuration.required_scopes:
        _fail("scope_mismatch")


def _align_application_clock(
    host_identity: IndependentlyHostAuthenticatedIdentityInput,
    application_request_time_utc: str,
) -> None:
    """The host clock must be the planning clock. Equality is not proof."""

    if host_identity.application_request_time_utc != application_request_time_utc:
        _fail("invalid_application_request_time")


def _policy_envelope(
    session: RuntimePolicySession,
    step: MasterRouteStepPlan,
    invocation: MasterInvocationAuthorization,
    server: ServerPolicyInputs,
    overrides: Mapping[str, object] | None,
) -> MasterRuntimePolicyEnvelope:
    if overrides is not None and (
        type(overrides) is not dict or not set(overrides).issubset(_ENVELOPE_OVERRIDES)
    ):
        _fail("invalid_policy_envelope")
    _executions, turns, calls = server_read_budget(invocation.specialist)
    historical_window = None
    if step.historical_start_utc is not None and step.historical_end_utc is not None:
        historical_window = (step.historical_start_utc, step.historical_end_utc)
    envelope = MasterRuntimePolicyEnvelope(
        master_request_id=session.planning_context.master_request_id,
        planning_session_id=session.session_id,
        step_id=step.step_id,
        invocation_id=invocation.invocation_id,
        specialist=invocation.specialist,
        purpose=invocation.purpose,
        capability=step.capability,
        principal_ref=None,
        policy_revision=POLICY_REVISION,
        resource_scope=server.resource_id,
        region_scope=None,
        historical_window=historical_window,
        decision_target_scope=None,
        accepted_at_utc=server.accepted_at_utc,
        deadline_utc=server.deadline_utc,
        idempotency_key=server.idempotency_key,
        max_model_turns=turns,
        max_tool_calls=calls,
        correlation_id=session.planning_context.correlation_id,
    )
    if not overrides:
        return envelope
    return replace(envelope, **overrides)


@dataclass(frozen=True)
class NonproductionIdentityPolicyBinding:
    """Identity to policy handoff. NONPRODUCTION. Not redeemable.

    ``policy_fingerprint`` is the policy evaluator's digest of a DENY.
    It is not a credential. ``planning_session_id`` is the planning
    registry label. ``policy_session_id`` is the label minted by
    ``retain_runtime_policy_session``. Equality of those labels is not
    proof, and neither label is a principal.
    """

    disposition: IngressBindingDisposition
    redeemable: bool
    production_activation_available: bool
    synthetic_principal_ref: str
    principal_record_kind: PrincipalRecordKind
    consumed_record_kind: PrincipalRecordKind
    principal_provenance: AuthorizationProvenanceStanding
    policy_decision: MasterRuntimePolicyDecision
    policy_denial_reasons: tuple[MasterRuntimePolicyDenialReason, ...]
    policy_authority_origin: PolicyAuthorityOrigin
    policy_fingerprint: str
    policy_revision: str
    membership_established: bool
    membership_is_authentication: bool
    session_identifier_equality_is_proof: bool
    session_membership_accepted_as_proof: bool
    matching_identifier_accepted_as_proof: bool
    structural_assessment_accepted_as_proof: bool
    event_accepted_as_invocation_source: bool
    lambda_context_accepted_as_proof: bool
    authorization_header_accepted: bool
    scope_edge_enforced: bool
    aws_invoke_boundary_resolved: bool
    issuance_controls_satisfied: bool
    host_ingress_proof_standing: HostProofStanding
    planning_session_id: str
    policy_session_id: str
    master_request_id: str
    original_step_id: str
    evaluated_step_id: str
    invocation_id: str
    specialist_request_id: str
    correlation_id: str
    specialist: MasterSpecialist
    purpose: MasterPurpose
    capability: AgentCapability
    issuer: str
    audience: str
    subject: str
    scopes: tuple[str, ...]
    route_key: str
    gateway_request_id: str
    proof_id: str
    authorization_provenance: str
    identity_evaluated_at_epoch: int
    principal_evaluated_at_epoch: int
    application_request_time_utc: str
    accepted_at_utc: str
    deadline_utc: str
    policy_evaluation_utc: str
    configuration_decision: ConfigurationRestrictionDecision
    invocation_standing: InvocationProvenanceStanding
    rejected_invocation_proofs: tuple[RejectedInvocationProof, ...]
    execution_authority: MasterRouteExecutionAuthority
    production_authority: MasterProductionAuthority
    source_authenticity: MasterSourceAuthenticity
    request_coverage: MasterRequestCoverage
    permit_issued: bool
    deployment_review_checklist: tuple[str, ...]
    unmet_host_ingress_proofs: tuple[HostIngressProofRequirement, ...]
    unsatisfied_issuance_control_ids: tuple[str, ...]
    boundary_report: GatewayRuntimeBoundaryReport
    identity_assessment: MasterGatewayIdentityAssessment
    observation: UntrustedGatewayClaimObservation
    principal: ServerOwnedPrincipalDescriptor
    policy_evaluation: MasterRuntimePolicyEvaluation
    policy_session: RuntimePolicySession
    server_inputs: ServerPolicyInputs
    schema_version: str = MASTER_HOST_INGRESS_POLICY_SCHEMA_VERSION
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _closed()
        if self._seal is not _BIND:
            _fail("nonproduction_binding_not_redeemable")
        if self.schema_version != MASTER_HOST_INGRESS_POLICY_SCHEMA_VERSION:
            _fail("nonproduction_binding_not_redeemable")
        if self.disposition is not IngressBindingDisposition.NONPRODUCTION:
            _fail("nonproduction_binding_not_redeemable")
        if self.redeemable is not False or NONPRODUCTION_BINDING_REDEEMABLE is not False:
            _fail("nonproduction_binding_not_redeemable")
        if self.production_activation_available is not False:
            _fail("production_principal_issuance_unavailable")
        if type(self.boundary_report) is not GatewayRuntimeBoundaryReport:
            _fail("invalid_report")
        if type(self.identity_assessment) is not MasterGatewayIdentityAssessment:
            _fail("invalid_assessment")
        if type(self.observation) is not UntrustedGatewayClaimObservation:
            _fail("observation_requires_gateway_assessment")
        if type(self.principal) is not ServerOwnedPrincipalDescriptor:
            _fail("synthetic_admission_not_production_principal")
        if type(self.policy_evaluation) is not MasterRuntimePolicyEvaluation:
            _fail("invalid_policy_evaluation")
        if type(self.policy_session) is not RuntimePolicySession:
            _fail("invalid_policy_session")
        if type(self.server_inputs) is not ServerPolicyInputs:
            _fail("invalid_server_policy_inputs")
        report = self.boundary_report
        assessment = self.identity_assessment
        observation = self.observation
        principal = self.principal
        evaluation = self.policy_evaluation
        if report.invocation_standing is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("runtime_admission_not_attested")
        if report.allows_production_issuance() is not False:
            _fail("production_issuance_unavailable")
        if (
            report.event_accepted_as_invocation_source is not False
            or report.invocation_attested is not False
            or report.production_issuance_available is not False
            or self.event_accepted_as_invocation_source is not False
            or self.lambda_context_accepted_as_proof is not False
        ):
            _fail("event_field_not_verified")
        if self.invocation_standing is not InvocationProvenanceStanding.NOT_ATTESTED:
            _fail("gateway_invocation_not_attested")
        if self.rejected_invocation_proofs != report.rejected_proofs:
            _fail("invalid_report")
        if self.configuration_decision is not report.configuration_decision:
            _fail("invalid_report")
        if (
            RuntimeBoundaryLimitation.SCOPES_ARE_NOT_EDGE_ENFORCED_BY_CURRENT_TERRAFORM
            not in report.limitations
            or self.scope_edge_enforced is not False
        ):
            _fail("reviewed_host_authority_not_wired")
        for name in _LIVE_INVOKE_FACTS:
            fact = report.fact(name)
            if (
                fact.evidence_class is not RuntimeEvidenceClass.NOT_YET_INSPECTED
                or fact.status is not EvidenceStatus.NOT_INSPECTED
            ):
                _fail("live_aws_fact_not_inspected")
        if self.aws_invoke_boundary_resolved is not False:
            _fail("reviewed_host_authority_not_wired")
        if self.issuance_controls_satisfied is not False or any(
            control.satisfied for control in report.issuance_controls
        ):
            _fail("issuance_control_not_satisfied")
        if self.unsatisfied_issuance_control_ids != ISSUANCE_CONTROL_IDS:
            _fail("issuance_control_not_satisfied")
        if self.deployment_review_checklist != DEPLOYMENT_REVIEW_CHECKLIST:
            _fail("invalid_report")
        if assessment.production_authenticated is not False or assessment.execution_permitted:
            _fail("application_principal_not_issued")
        if (
            assessment.ingress_attestation.lambda_invocation_boundary_reviewed is not False
            or assessment.ingress_attestation.protected_gateway_integration_reviewed is not False
            or assessment.application_principal.issued is not False
        ):
            _fail("ingress_attestation_unresolved")
        if assessment.evaluated_at_epoch != self.identity_evaluated_at_epoch:
            _fail("invalid_evaluation_epoch")
        if observation.structural_assessment_accepted_as_proof is not False:
            _fail("structural_jwt_assessment_not_principal")
        if observation.record_kind is not PrincipalRecordKind.DESCRIPTIVE_OBSERVATION:
            _fail("descriptive_observation_not_principal")
        if (
            self.authorization_header_accepted is not False
            or observation.authorization_header_accepted is not False
            or assessment.raw_authorization.payload_decoded is not False
            or assessment.raw_authorization.accepted_as_authentication is not False
        ):
            _fail("header_only_identity")
        if (
            principal.production_principal is not False
            or principal.authenticates_caller is not False
            or principal.execution_permitted is not False
            or self.principal_record_kind is not PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
            or principal.record_kind is not PrincipalRecordKind.SYNTHETIC_TEST_ADMISSION
        ):
            _fail("synthetic_admission_not_production_principal")
        if self.consumed_record_kind is not PrincipalRecordKind.CONSUMED_SYNTHETIC_ADMISSION:
            _fail("replayed_principal")
        if (
            self.principal_provenance
            is not AuthorizationProvenanceStanding.CONTRACT_TEST_HOST
            or principal.provenance_standing
            is not AuthorizationProvenanceStanding.CONTRACT_TEST_HOST
        ):
            _fail("caller_built_identity_not_host_issued")
        if (
            self.synthetic_principal_ref != principal.principal_ref
            or self.planning_session_id != principal.planning_session_id
            or self.master_request_id != principal.master_request_id
            or self.correlation_id != principal.correlation_id
            or self.subject != principal.subject
            or self.issuer != principal.issuer
            or self.audience != principal.audience
            or self.scopes != principal.scopes
            or self.route_key != principal.route_key
            or self.purpose is not principal.purpose
            or self.proof_id != principal.proof_id
            or self.authorization_provenance != principal.authorization_provenance
            or self.gateway_request_id != principal.scope.gateway_request_id
            or self.policy_revision != principal.policy_revision
            or self.policy_revision != POLICY_REVISION
        ):
            _fail("principal_descriptor_immutable")
        if principal.principal_evaluated_at_epoch != self.principal_evaluated_at_epoch:
            _fail("invalid_evaluation_epoch")
        if (
            self.session_membership_accepted_as_proof is not False
            or principal.session_membership_accepted_as_proof is not False
            or self.matching_identifier_accepted_as_proof is not False
            or principal.matching_identifier_accepted_as_proof is not False
            or self.structural_assessment_accepted_as_proof is not False
            or self.session_identifier_equality_is_proof is not False
            or self.membership_is_authentication is not False
        ):
            _fail("matching_identifier_not_proof")
        if self.policy_session_id != self.policy_session.session_id:
            _fail("invalid_policy_session")
        if self.master_request_id != self.policy_session.planning_context.master_request_id:
            _fail("master_request_mismatch")
        if self.correlation_id != self.policy_session.planning_context.correlation_id:
            _fail("correlation_mismatch")
        if self.application_request_time_utc != (
            self.policy_session.planning_context.application_request_time_utc
        ):
            _fail("invalid_application_request_time")
        if evaluation.decision is not MasterRuntimePolicyDecision.DENY:
            _fail("production_policy_result_must_deny")
        if evaluation.authority_origin is not PolicyAuthorityOrigin.NOT_ESTABLISHED:
            _fail("principal_not_host_authenticated")
        if (
            MasterRuntimePolicyDenialReason.PRINCIPAL_NOT_HOST_AUTHENTICATED
            not in evaluation.denial_reasons
        ):
            _fail("principal_not_host_authenticated")
        if (
            self.policy_decision is not evaluation.decision
            or self.policy_denial_reasons != evaluation.denial_reasons
            or self.policy_authority_origin is not evaluation.authority_origin
            or self.policy_fingerprint != evaluation.policy_fingerprint
            or self.membership_established is not evaluation.membership_established
            or self.evaluated_step_id != evaluation.step_id
            or self.invocation_id != evaluation.invocation_id
            or self.specialist is not evaluation.specialist
            or self.capability is not evaluation.capability
            or evaluation.principal_ref is not None
            or evaluation.production_principal_established is not False
            or evaluation.execution_permitted is not False
            or evaluation.permit_issued is not False
            or evaluation.grant_issued is not False
            or evaluation.allows_specialist_execution() is not False
        ):
            _fail("policy_evaluation_is_not_a_permit")
        if self.policy_evaluation_utc != evaluation.evaluation_utc:
            _fail("invalid_policy_evaluation")
        if self.accepted_at_utc != evaluation.accepted_at_utc:
            _fail("invalid_policy_evaluation")
        if self.deadline_utc != evaluation.deadline_utc:
            _fail("invalid_policy_evaluation")
        if (
            self.server_inputs.tool_name != evaluation.tool_name
            or self.server_inputs.resource_id != evaluation.resource_scope
            or self.server_inputs.idempotency_key != evaluation.idempotency_key
        ):
            _fail("invalid_server_policy_inputs")
        if (
            self.execution_authority is not MasterRouteExecutionAuthority.DENIED
            or self.production_authority is not MasterProductionAuthority.DENIED
            or self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
            or self.request_coverage is not MasterRequestCoverage.NOT_ESTABLISHED
            or evaluation.execution_authority is not MasterRouteExecutionAuthority.DENIED
            or evaluation.production_authority is not MasterProductionAuthority.DENIED
        ):
            _fail("execution_authority_not_granted")
        if self.permit_issued is not False:
            _fail("policy_evaluation_is_not_a_permit")
        if self.host_ingress_proof_standing is not HostProofStanding.UNRESOLVED:
            _fail("reviewed_host_authority_not_wired")
        if self.unmet_host_ingress_proofs != HOST_INGRESS_PROOF_REQUIREMENTS:
            _fail("reviewed_host_authority_not_wired")
        if not isinstance(self.specialist, MasterSpecialist):
            _fail("invalid_specialist")
        if not isinstance(self.purpose, MasterPurpose):
            _fail("invalid_purpose")
        if not isinstance(self.capability, AgentCapability):
            _fail("invalid_capability")
        if type(self.specialist_request_id) is not str or self.specialist_request_id == "":
            _fail("invalid_invocation_authorization")

    def __bool__(self) -> bool:
        _fail("nonproduction_binding_not_redeemable")
        raise AssertionError("unreachable")

    def __copy__(self) -> object:
        _fail("nonproduction_binding_not_redeemable")
        raise AssertionError("unreachable")

    def __deepcopy__(self, memo: object) -> object:
        _fail("nonproduction_binding_not_redeemable")
        raise AssertionError("unreachable")

    def __reduce__(self) -> object:
        _fail("nonproduction_binding_not_redeemable")
        raise AssertionError("unreachable")

    def allows_specialist_execution(self) -> bool:
        """Always false. A DENY record is not dispatch permission."""

        if self.policy_evaluation.allows_specialist_execution() is not False:
            _fail("policy_evaluation_is_not_a_permit")
        return False

    def redeem(self, *_args: object, **_kwargs: object) -> None:
        """Refuse redemption. This record is not a grant."""

        _fail("nonproduction_binding_not_redeemable")

    def handoff_record(self) -> dict[str, object]:
        """Plain record for the integration owner. Not an authorization token."""

        return {
            "disposition": IngressBindingDisposition.NONPRODUCTION.value,
            "redeemable": False,
            "production_activation_available": False,
            "synthetic_principal_ref": self.synthetic_principal_ref,
            "policy_revision": self.policy_revision,
            "policy_fingerprint": self.policy_fingerprint,
            "policy_decision": self.policy_decision.value,
            "authority_origin": self.policy_authority_origin.value,
            "membership_established": self.membership_established,
            "membership_is_authentication": False,
            "planning_session_id": self.planning_session_id,
            "policy_session_id": self.policy_session_id,
            "session_identifier_equality_is_proof": False,
            "master_request_id": self.master_request_id,
            "step_id": self.original_step_id,
            "evaluated_step_id": self.evaluated_step_id,
            "invocation_id": self.invocation_id,
            "specialist_request_id": self.specialist_request_id,
            "correlation_id": self.correlation_id,
            "specialist": self.specialist.value,
            "purpose": self.purpose.value,
            "capability": self.capability.value,
            "issuer": self.issuer,
            "audience": self.audience,
            "subject": self.subject,
            "scopes": self.scopes,
            "route_key": self.route_key,
            "gateway_request_id": self.gateway_request_id,
            "proof_id": self.proof_id,
            "authorization_provenance": self.authorization_provenance,
            "denial_reasons": tuple(reason.value for reason in self.policy_denial_reasons),
            "scope_edge_enforced": False,
            "aws_invoke_boundary_resolved": False,
            "issuance_controls_satisfied": False,
            "unsatisfied_issuance_control_ids": self.unsatisfied_issuance_control_ids,
            "unmet_host_ingress_proofs": tuple(
                item.value for item in self.unmet_host_ingress_proofs
            ),
            "deployment_review_checklist": self.deployment_review_checklist,
        }


class NonproductionIngressPolicyPort:
    """In-process seam. A seal string or environment variable cannot open it."""

    __slots__ = ("_configuration", "_proof")

    def __init__(
        self,
        configuration: object,
        *,
        authorization_provenance: object,
        seal: object,
    ) -> None:
        _closed()
        if type(seal) is not NonproductionContractSeal:
            if type(seal) is str or type(seal) is bool or seal is None:
                _fail("environment_not_principal_authority")
            _fail("nonproduction_seal_required")
        if type(configuration) is not MasterGatewayIdentityConfiguration:
            _fail("configuration_not_taken_from_request")
        self._configuration = configuration
        self._proof = open_nonproduction_principal_proof_port(
            configuration,
            policy_revision=POLICY_REVISION,
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

    def bind(
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
        lambda_context: object = None,
        trusted: object = None,
        policy_step: object = None,
        policy_invocation: object = None,
        envelope_overrides: Mapping[str, object] | None = None,
    ) -> NonproductionIdentityPolicyBinding:
        """Run the real modules once. The result stays NONPRODUCTION.

        ``event`` is not read as ``host_identity``. The host identity is a
        separate object. ``requestContext``, Lambda context, and ``trusted``
        are passed only to the runtime-boundary classifier.
        """

        _closed()
        if type(manifest) is not ServerOwnedGatewayManifest:
            _fail("manifest_not_server_owned")
        if type(host_identity) is not IndependentlyHostAuthenticatedIdentityInput:
            _fail("caller_built_identity_not_host_issued")
        if type(registry) is not MasterPlanningSessionRegistry:
            _fail("invalid_session")
        if type(planning_record) is not MasterPlanningSessionRecord:
            _fail("invalid_session")
        if type(invocation) is not MasterInvocationAuthorization:
            _fail("invalid_invocation_authorization")
        if type(server) is not ServerPolicyInputs:
            _fail("invalid_server_policy_inputs")
        if type(evaluation_epoch) is not int:
            _fail("invalid_evaluation_epoch")
        _align_manifest(manifest, self._configuration)
        report = assess_gateway_runtime_boundary(
            manifest,
            event,
            lambda_context=lambda_context,
            trusted=trusted,
        )
        assessment = assess_master_gateway_identity(
            event,
            self._configuration,
            evaluation_epoch=evaluation_epoch,
        )
        observation = observe_untrusted_gateway_claims(assessment)
        context = registry.context_for(planning_record)
        plan = registry.require_retained_plan(
            planning_record,
            registry.plan_for(planning_record),
        )
        _align_application_clock(
            host_identity,
            context.application_request_time_utc,
        )
        selected = _selected_invocation(registry, planning_record, invocation)
        member_step = _member_step(plan, selected)
        if type(member_step.invocation.specialist_request_id) is not str:
            _fail("invalid_invocation_authorization")
        if policy_step is None:
            evaluated_step = member_step
        else:
            if type(policy_step) is not MasterRouteStepPlan:
                _fail("invalid_route_step_plan")
            evaluated_step = policy_step
        if policy_invocation is None:
            evaluated_invocation = evaluated_step.invocation
        else:
            if type(policy_invocation) is not MasterInvocationAuthorization:
                _fail("invalid_invocation_authorization")
            evaluated_invocation = policy_invocation
        descriptor = self._proof.admit(
            observation,
            host_identity,
            evaluation_epoch=evaluation_epoch,
        )
        self._proof.present(
            descriptor,
            evaluation_epoch=evaluation_epoch,
            planning_session_id=planning_record.session_id,
            master_request_id=context.master_request_id,
            correlation_id=context.correlation_id,
            purpose=invocation.purpose,
            audience=self._configuration.audience,
            route_key=self._configuration.route_key,
            acting_subject=host_identity.subject,
            gateway_request_id=observation.gateway_request_id,
        )
        consumed = self._proof.lifecycle(descriptor)
        policy_session = retain_runtime_policy_session(context, plan)
        envelope = _policy_envelope(
            policy_session,
            evaluated_step,
            evaluated_invocation,
            server,
            envelope_overrides,
        )
        evaluation = evaluate_master_runtime_policy(
            policy_session,
            plan,
            evaluated_step,
            evaluated_invocation,
            server,
            envelope,
        )
        checklist = HostIngressProofChecklist()
        return NonproductionIdentityPolicyBinding(
            disposition=IngressBindingDisposition.NONPRODUCTION,
            redeemable=False,
            production_activation_available=False,
            synthetic_principal_ref=descriptor.principal_ref,
            principal_record_kind=descriptor.record_kind,
            consumed_record_kind=consumed,
            principal_provenance=descriptor.provenance_standing,
            policy_decision=evaluation.decision,
            policy_denial_reasons=evaluation.denial_reasons,
            policy_authority_origin=evaluation.authority_origin,
            policy_fingerprint=evaluation.policy_fingerprint,
            policy_revision=POLICY_REVISION,
            membership_established=evaluation.membership_established,
            membership_is_authentication=False,
            session_identifier_equality_is_proof=False,
            session_membership_accepted_as_proof=False,
            matching_identifier_accepted_as_proof=False,
            structural_assessment_accepted_as_proof=False,
            event_accepted_as_invocation_source=False,
            lambda_context_accepted_as_proof=False,
            authorization_header_accepted=False,
            scope_edge_enforced=False,
            aws_invoke_boundary_resolved=False,
            issuance_controls_satisfied=False,
            host_ingress_proof_standing=checklist.standing,
            planning_session_id=descriptor.planning_session_id,
            policy_session_id=policy_session.session_id,
            master_request_id=descriptor.master_request_id,
            original_step_id=member_step.step_id,
            evaluated_step_id=evaluation.step_id,
            invocation_id=evaluation.invocation_id,
            specialist_request_id=member_step.invocation.specialist_request_id,
            correlation_id=descriptor.correlation_id,
            specialist=evaluation.specialist,
            purpose=descriptor.purpose,
            capability=evaluation.capability,
            issuer=descriptor.issuer,
            audience=descriptor.audience,
            subject=descriptor.subject,
            scopes=descriptor.scopes,
            route_key=descriptor.route_key,
            gateway_request_id=descriptor.scope.gateway_request_id,
            proof_id=descriptor.proof_id,
            authorization_provenance=descriptor.authorization_provenance,
            identity_evaluated_at_epoch=assessment.evaluated_at_epoch,
            principal_evaluated_at_epoch=descriptor.principal_evaluated_at_epoch,
            application_request_time_utc=context.application_request_time_utc,
            accepted_at_utc=evaluation.accepted_at_utc,
            deadline_utc=evaluation.deadline_utc,
            policy_evaluation_utc=evaluation.evaluation_utc,
            configuration_decision=report.configuration_decision,
            invocation_standing=report.invocation_standing,
            rejected_invocation_proofs=report.rejected_proofs,
            execution_authority=MasterRouteExecutionAuthority.DENIED,
            production_authority=MasterProductionAuthority.DENIED,
            source_authenticity=MasterSourceAuthenticity.NOT_ESTABLISHED,
            request_coverage=MasterRequestCoverage.NOT_ESTABLISHED,
            permit_issued=False,
            deployment_review_checklist=report.deployment_review_checklist,
            unmet_host_ingress_proofs=checklist.requirements,
            unsatisfied_issuance_control_ids=ISSUANCE_CONTROL_IDS,
            boundary_report=report,
            identity_assessment=assessment,
            observation=observation,
            principal=descriptor,
            policy_evaluation=evaluation,
            policy_session=policy_session,
            server_inputs=server,
            _seal=_BIND,
        )

    def present_again(self, binding: object) -> None:
        """Present the same admission a second time. The proof port rejects it."""

        _closed()
        if type(binding) is not NonproductionIdentityPolicyBinding:
            _fail("nonproduction_binding_not_redeemable")
        descriptor = binding.principal
        self._proof.present(
            descriptor,
            evaluation_epoch=binding.principal_evaluated_at_epoch,
            planning_session_id=descriptor.planning_session_id,
            master_request_id=descriptor.master_request_id,
            correlation_id=descriptor.correlation_id,
            purpose=descriptor.purpose,
            audience=descriptor.audience,
            route_key=descriptor.route_key,
            acting_subject=descriptor.subject,
            gateway_request_id=descriptor.scope.gateway_request_id,
        )


def open_nonproduction_ingress_policy_port(
    configuration: object,
    *,
    authorization_provenance: object,
    seal: object,
) -> NonproductionIngressPolicyPort:
    """Open the offline seam. ``seal`` must be minted in-process."""

    return NonproductionIngressPolicyPort(
        configuration,
        authorization_provenance=authorization_provenance,
        seal=seal,
    )


def issue_production_ingress_policy(*_candidates: object, **_kwargs: object) -> None:
    """Reject production ingress issuance. Arguments cannot open it."""

    raise ContractValidationError(
        (
            "production_principal_issuance_unavailable",
            "reviewed_host_authority_not_wired",
            "nonproduction_binding_not_redeemable",
        )
    )
