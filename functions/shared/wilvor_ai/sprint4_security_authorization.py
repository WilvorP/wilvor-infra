"""Sprint 4 production authorization design and reviewed IAM proposal.

This module does not change IAM, apply Terraform, or open production
issuance. It records the authorization chain that a later operator must
satisfy and the IAM delta that remains after the current ``modules/ai_host``
policy. Possession of these objects is not a DynamoDB write permit.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_grants import (
    DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
    HMAC_PREVENTS_REPLAY,
    PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
    PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
    issue_production_execution_grant,
)
from wilvor_ai.master_execution_ledger import (
    DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
    DYNAMODB_LEDGER_TABLE_NAME,
    GRANT_NONCE_ROTATES_ON_TAKEOVER,
)
from wilvor_ai.master_gateway_runtime_boundary import ISSUANCE_CONTROL_IDS
from wilvor_ai.sprint3d_auth_grants import (
    assert_closed_production_entrypoints,
    route_enablement_permitted,
)
from wilvor_ai.sprint3d_aws_execution import (
    SPRINT3D_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
    SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
    expected_iam_decisions,
    live_aws_blockers,
    open_production_aws_execution,
)
from wilvor_ai.sprint4_security_ledger import (
    SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION,
    SPRINT4_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT4_LEDGER_WRITES_ENABLED,
    SPRINT4_LIVE_AWS_ENABLED,
)


SPRINT4_SECURITY_AUTHORIZATION_SCHEMA = "wilvor.ai.sprint4.security_authorization.v1"
SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED = False
SPRINT4_IAM_CHANGE_APPLIED = False
REVIEWED_DEV_ACCOUNT_ID = "396037508959"
REVIEWED_DEV_REGION = "us-west-1"
AI_HOST_ROLE_NAME = "wilvor-dev-ai-host-lambda-role"
AIRCRAFT_TABLE_NAME = "wilvor-dev-aircraft-current-state"
ENCOUNTER_TABLE_NAME = "wilvor-dev-aircraft-hazard-encounter"
HISTORICAL_TABLE_NAME = "wilvor-dev-historical-encounters"

AUTHORIZATION_STAGES = (
    "gateway_ingress",
    "application_principal",
    "runtime_policy",
    "signed_execution_grant",
    "atomic_ledger_started_admission",
    "fenced_specialist_dispatch",
    "bounded_source_read",
    "evidence_admission",
)

CURRENT_LEDGER_ALLOW = (
    "dynamodb:GetItem",
    "dynamodb:PutItem",
    "dynamodb:TransactWriteItems",
)
CURRENT_LEDGER_DENY = (
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:UpdateItem",
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
)
CURRENT_SPECIALIST_GETITEM_DENY = (
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:TransactWriteItems",
    "dynamodb:BatchWriteItem",
    "dynamodb:BatchGetItem",
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
)
CLOSED_RUNTIME_DENY = (
    "bedrock:*",
    "secretsmanager:GetSecretValue",
    "secretsmanager:GetSecret",
    "lambda:InvokeFunction",
    "lambda:AddPermission",
    "lambda:CreateFunctionUrlConfig",
    "execute-api:Invoke",
)


def _assert_closed() -> None:
    if SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED is not False:
        raise ContractValidationError("production_authorization_closed")
    if SPRINT4_IAM_CHANGE_APPLIED is not False:
        raise ContractValidationError("iam_change_not_applied")
    if SPRINT4_LEDGER_WRITES_ENABLED is not False:
        raise ContractValidationError("sprint4_ledger_writes_enabled")
    if SPRINT4_LIVE_AWS_ENABLED is not False:
        raise ContractValidationError("sprint4_live_aws_enabled")
    if PRODUCTION_GRANT_ISSUANCE_AVAILABLE is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if SPRINT3D_PRODUCTION_EXECUTION_ENABLED is not False:
        raise ContractValidationError("production_execution_closed")
    if route_enablement_permitted() is not False:
        raise ContractValidationError("route_enablement_blocked")


def apply_iam_delta(*_args: object, **_kwargs: object) -> None:
    """IAM stays unchanged. This sprint records a proposal only."""

    _assert_closed()
    raise ContractValidationError("sprint4_iam_not_applied")


def enable_production_authorization(*_args: object, **_kwargs: object) -> None:
    _assert_closed()
    raise ContractValidationError("production_authorization_closed")


@dataclass(frozen=True)
class AuthorizationStageRequirement:
    """One host stage that must stay closed until the named evidence exists."""

    stage: str
    current_authority: str
    production_entry: str
    closed_constant: str
    satisfied: bool

    def __post_init__(self) -> None:
        if self.stage not in AUTHORIZATION_STAGES:
            raise ContractValidationError("invalid_authorization_stage")
        if self.satisfied is not False:
            raise ContractValidationError("authorization_stage_not_satisfied")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class ResourceIsolationBoundary:
    """Exact resources the AI host may touch. Wildcards are not isolation."""

    ledger_table: str
    reviewed_getitem_tables: tuple[str, ...]
    denied_tables: tuple[str, ...]
    operational_api_function: str
    ai_host_function: str
    model_invoke_denied: bool
    function_url_denied: bool
    ai_query_route_enabled: bool
    wildcard_table_arn: bool

    def __post_init__(self) -> None:
        if self.ledger_table != DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("unexpected_ledger_table")
        if self.ai_host_function == self.operational_api_function:
            raise ContractValidationError("ai_host_must_be_distinct")
        if self.model_invoke_denied is not True or self.function_url_denied is not True:
            raise ContractValidationError("runtime_deny_missing")
        if self.ai_query_route_enabled is not False or self.wildcard_table_arn is not False:
            raise ContractValidationError("resource_isolation_violated")
        if DYNAMODB_LEDGER_TABLE_NAME in self.reviewed_getitem_tables:
            raise ContractValidationError("ledger_is_not_a_specialist_table")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class IamChangeProposal:
    """Reviewed IAM delta. ``applied`` stays false. Terraform must not be applied."""

    schema_version: str
    status: str
    account_id: str
    region: str
    role_name: str
    current_ledger_allow: tuple[str, ...]
    current_ledger_deny: tuple[str, ...]
    current_specialist_getitem_allow: tuple[str, ...]
    remaining_deltas: tuple[str, ...]
    must_not_grant: tuple[str, ...]
    terraform_apply_permitted: bool
    iam_change_applied: bool
    production_authorization_enabled: bool

    def __post_init__(self) -> None:
        _assert_closed()
        if self.schema_version != SPRINT4_SECURITY_AUTHORIZATION_SCHEMA:
            raise ContractValidationError("invalid_schema_version")
        if self.status != "PROPOSED_NOT_APPLIED":
            raise ContractValidationError("iam_change_not_applied")
        if self.terraform_apply_permitted is not False or self.iam_change_applied is not False:
            raise ContractValidationError("iam_change_not_applied")
        if self.production_authorization_enabled is not False:
            raise ContractValidationError("production_authorization_closed")
        if self.current_ledger_allow != CURRENT_LEDGER_ALLOW:
            raise ContractValidationError("ledger_allow_drift")
        if "dynamodb:TransactWriteItems" not in self.current_ledger_allow:
            raise ContractValidationError("consume_api_missing")
        if "dynamodb:Scan" not in self.current_ledger_deny:
            raise ContractValidationError("scan_not_denied")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class ProductionAuthorizationDesign:
    """The chain a later operator must close. Designing it does not close it."""

    schema_version: str
    stages: tuple[AuthorizationStageRequirement, ...]
    isolation: ResourceIsolationBoundary
    iam_proposal: IamChangeProposal
    issuance_controls: tuple[str, ...]
    live_aws_blockers: tuple[str, ...]
    hmac_prevents_replay: bool
    grant_nonce_rotates_on_takeover: bool
    durable_adapter_is_provenance: bool
    atomic_admission_is_native_execution: bool
    distributed_exactly_once: bool
    production_enabled: bool
    route_enablement_permitted: bool
    ledger_writes_enabled: bool

    def __post_init__(self) -> None:
        _assert_closed()
        if self.schema_version != SPRINT4_SECURITY_AUTHORIZATION_SCHEMA:
            raise ContractValidationError("invalid_schema_version")
        if tuple(stage.stage for stage in self.stages) != AUTHORIZATION_STAGES:
            raise ContractValidationError("authorization_stages_incomplete")
        if any(stage.satisfied for stage in self.stages):
            raise ContractValidationError("authorization_stage_not_satisfied")
        if self.issuance_controls != ISSUANCE_CONTROL_IDS:
            raise ContractValidationError("issuance_control_not_satisfied")
        for flag in (
            self.hmac_prevents_replay,
            self.grant_nonce_rotates_on_takeover,
            self.durable_adapter_is_provenance,
            self.atomic_admission_is_native_execution,
            self.distributed_exactly_once,
            self.production_enabled,
            self.route_enablement_permitted,
            self.ledger_writes_enabled,
        ):
            if flag is not False:
                raise ContractValidationError("production_authorization_closed")

    def __bool__(self) -> bool:
        return False


def current_resource_isolation() -> ResourceIsolationBoundary:
    """Isolation already encoded in ``modules/ai_host``. Not a live AWS proof."""

    _assert_closed()
    return ResourceIsolationBoundary(
        ledger_table=DYNAMODB_LEDGER_TABLE_NAME,
        reviewed_getitem_tables=(AIRCRAFT_TABLE_NAME,),
        denied_tables=(ENCOUNTER_TABLE_NAME, HISTORICAL_TABLE_NAME),
        operational_api_function="wilvor-dev-operational-api",
        ai_host_function="wilvor-dev-ai-host",
        model_invoke_denied=True,
        function_url_denied=True,
        ai_query_route_enabled=False,
        wildcard_table_arn=False,
    )


def reviewed_iam_change_proposal() -> IamChangeProposal:
    """Remaining IAM work after the current host policy. Not an apply plan."""

    _assert_closed()
    return IamChangeProposal(
        schema_version=SPRINT4_SECURITY_AUTHORIZATION_SCHEMA,
        status="PROPOSED_NOT_APPLIED",
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        role_name=AI_HOST_ROLE_NAME,
        current_ledger_allow=CURRENT_LEDGER_ALLOW,
        current_ledger_deny=CURRENT_LEDGER_DENY,
        current_specialist_getitem_allow=("dynamodb:GetItem",),
        remaining_deltas=(
            "If Historical Analytics reads go live, allow dynamodb:Query on one reviewed historical table ARN only.",
            "If a reviewed mapping sets query_index_name, allow dynamodb:Query on that index ARN only.",
            "Keep Query denied on the aircraft callsign GSI; current Live Ops mapping is GetItem on aircraft_id.",
            "Keep TransactWriteItems denied on every specialist table.",
            "Do not grant dynamodb:DescribeTable or iam:SimulatePrincipalPolicy to the runtime role.",
            "Do not grant Bedrock, Secrets Manager reads, lambda:InvokeFunction, function URLs, or execute-api:Invoke.",
            "Do not raise reserved concurrency or set enable_invoke from this proposal.",
            "Do not enable POST /ai/query or LEDGER_WRITES_ENABLED.",
        ),
        must_not_grant=(
            "dynamodb:Scan",
            "dynamodb:DeleteItem",
            "dynamodb:UpdateItem",
            "dynamodb:BatchWriteItem",
            "dynamodb:*",
            "bedrock:*",
            "iam:SimulatePrincipalPolicy",
            "lambda:AddPermission",
            "lambda:CreateFunctionUrlConfig",
        ),
        terraform_apply_permitted=False,
        iam_change_applied=False,
        production_authorization_enabled=False,
    )


def production_authorization_design() -> ProductionAuthorizationDesign:
    """Assemble the closed design. Calling this does not inspect live AWS."""

    _assert_closed()
    assert_closed_production_entrypoints()
    stages = (
        AuthorizationStageRequirement(
            "gateway_ingress",
            "InvocationProvenanceStanding.NOT_ATTESTED",
            "issue_application_principal",
            "PRODUCTION_GATEWAY_ISSUANCE_AVAILABLE",
            False,
        ),
        AuthorizationStageRequirement(
            "application_principal",
            "SYNTHETIC_TEST_ADMISSION or host-minted nonproduction principal",
            "issue_production_application_principal",
            "HOST_AUTHENTICATED_PRINCIPAL_AVAILABLE",
            False,
        ),
        AuthorizationStageRequirement(
            "runtime_policy",
            "public evaluate_master_runtime_policy returns DENY",
            "promote_policy_evaluation_to_permit",
            "HOST_GRANT_ISSUER_AVAILABLE",
            False,
        ),
        AuthorizationStageRequirement(
            "signed_execution_grant",
            "LocalHarnessGrantIssuer or HostOwnedGrantIssuer, not production",
            "issue_production_execution_grant",
            "PRODUCTION_GRANT_ISSUANCE_AVAILABLE",
            False,
        ),
        AuthorizationStageRequirement(
            "atomic_ledger_started_admission",
            "injected TransactWriteItems of STARTED plus consume marker",
            "bind_production_grant_to_master_ledger",
            "DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE",
            False,
        ),
        AuthorizationStageRequirement(
            "fenced_specialist_dispatch",
            "consumed grant plus current fence required",
            "invoke_specialist",
            "PRODUCTION_DISPATCH_ENABLED",
            False,
        ),
        AuthorizationStageRequirement(
            "bounded_source_read",
            "GET_ITEM and QUERY on reviewed mappings; Scan refused",
            "open_production_executor",
            "PRODUCTION_READ_EXECUTOR_ENABLED",
            False,
        ),
        AuthorizationStageRequirement(
            "evidence_admission",
            "MasterSourceAuthenticity.NOT_ESTABLISHED",
            "admit_evidence",
            "SOURCE_AUTHENTICATED_READ_AVAILABLE",
            False,
        ),
    )
    return ProductionAuthorizationDesign(
        schema_version=SPRINT4_SECURITY_AUTHORIZATION_SCHEMA,
        stages=stages,
        isolation=current_resource_isolation(),
        iam_proposal=reviewed_iam_change_proposal(),
        issuance_controls=ISSUANCE_CONTROL_IDS,
        live_aws_blockers=live_aws_blockers(),
        hmac_prevents_replay=HMAC_PREVENTS_REPLAY,
        grant_nonce_rotates_on_takeover=GRANT_NONCE_ROTATES_ON_TAKEOVER,
        durable_adapter_is_provenance=DURABLE_ADAPTER_TYPE_IS_VERIFIED_AWS_PROVENANCE,
        atomic_admission_is_native_execution=SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION,
        distributed_exactly_once=SPRINT4_DISTRIBUTED_EXACTLY_ONCE or SPRINT3D_DISTRIBUTED_EXACTLY_ONCE,
        production_enabled=SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED,
        route_enablement_permitted=route_enablement_permitted(),
        ledger_writes_enabled=SPRINT4_LEDGER_WRITES_ENABLED,
    )


def closed_authorization_inventory() -> dict[str, object]:
    """Snapshot of closed flags this sprint must not flip."""

    _assert_closed()
    return {
        "schema_version": SPRINT4_SECURITY_AUTHORIZATION_SCHEMA,
        "PRODUCTION_GRANT_ISSUANCE_AVAILABLE": PRODUCTION_GRANT_ISSUANCE_AVAILABLE,
        "PRODUCTION_SIGNING_KEY_STORE_INTEGRATED": PRODUCTION_SIGNING_KEY_STORE_INTEGRATED,
        "DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE": DURABLE_MASTER_LEDGER_CONSUMPTION_AVAILABLE,
        "HMAC_PREVENTS_REPLAY": HMAC_PREVENTS_REPLAY,
        "SPRINT3D_PRODUCTION_EXECUTION_ENABLED": SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
        "SPRINT3D_LIVE_NETWORK_TESTS_ENABLED": SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
        "SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED": SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED,
        "SPRINT4_IAM_CHANGE_APPLIED": SPRINT4_IAM_CHANGE_APPLIED,
        "SPRINT4_LEDGER_WRITES_ENABLED": SPRINT4_LEDGER_WRITES_ENABLED,
        "SPRINT4_LIVE_AWS_ENABLED": SPRINT4_LIVE_AWS_ENABLED,
        "route_enablement_permitted": False,
        "issuance_controls_satisfied": False,
    }


def specialist_iam_matrix_from_deployment(deployment: object) -> tuple[tuple[str, str, str], ...]:
    """Reuse the 3D least-privilege matrix. The matrix is not a live simulation."""

    _assert_closed()
    return expected_iam_decisions(deployment)


def refuse_closed_production_entrypoints() -> None:
    """Keep the pre-existing issuers raising."""

    _assert_closed()
    assert_closed_production_entrypoints()
    try:
        issue_production_execution_grant()
    except ContractValidationError:
        grant_closed = True
    else:
        grant_closed = False
    try:
        open_production_aws_execution()
    except ContractValidationError:
        aws_closed = True
    else:
        aws_closed = False
    if not grant_closed or not aws_closed:
        raise ContractValidationError("production_issuance_unavailable")


SHARED_INTERFACE_REQUIREMENTS: tuple[str, ...] = (
    "required_atomic_single_use_interface().implemented stays false until the integration owner joins HostOwnedGrantIssuer with one TransactWriteItems consume that uses the signed nonce as the ledger nonce.",
    "LocalHarnessGrantIssuer.consume must keep rejecting MasterExecutionLedger. Do not retarget that signature in a worker branch.",
    "bind_production_grant_to_master_ledger and issue_production_execution_grant must keep raising.",
    "MasterSourceAuthenticity has only NOT_ESTABLISHED. Adding an admitted authenticity value is an integration-owner change.",
    "sprint3d_integration_host.py, contracts.py, and specialist_contracts.py stay frozen.",
    "enable_ai_query_route remains false in modules/operational_api. Worker agents must not add POST /ai/query.",
    "envs/dev Terraform and SPRINT3D IAM documents stay integrator-owned. This module records a proposal only.",
    "Historical Query IAM on a reviewed table is not present on the runtime role. Agent B cannot treat Query as authorized until the integrator applies a later delta.",
)


_assert_closed()


__all__ = [
    "AIRCRAFT_TABLE_NAME",
    "AI_HOST_ROLE_NAME",
    "AUTHORIZATION_STAGES",
    "CLOSED_RUNTIME_DENY",
    "CURRENT_LEDGER_ALLOW",
    "CURRENT_LEDGER_DENY",
    "CURRENT_SPECIALIST_GETITEM_DENY",
    "ENCOUNTER_TABLE_NAME",
    "HISTORICAL_TABLE_NAME",
    "REVIEWED_DEV_ACCOUNT_ID",
    "REVIEWED_DEV_REGION",
    "SHARED_INTERFACE_REQUIREMENTS",
    "SPRINT4_IAM_CHANGE_APPLIED",
    "SPRINT4_PRODUCTION_AUTHORIZATION_ENABLED",
    "SPRINT4_SECURITY_AUTHORIZATION_SCHEMA",
    "AuthorizationStageRequirement",
    "IamChangeProposal",
    "ProductionAuthorizationDesign",
    "ResourceIsolationBoundary",
    "apply_iam_delta",
    "closed_authorization_inventory",
    "current_resource_isolation",
    "enable_production_authorization",
    "production_authorization_design",
    "refuse_closed_production_entrypoints",
    "reviewed_iam_change_proposal",
    "specialist_iam_matrix_from_deployment",
]
