"""Sprint 3D Agent C: AWS ledger consumption and bounded DynamoDB reads.

Real DynamoDB calls are reached only through a reviewed authorization.
``TransactWriteItems`` starts one fenced attempt and writes a no-TTL
consumption marker. That transaction is not exactly-once native execution.
Reads are ``GetItem`` and ``Query`` on reviewed table mappings. Scan,
writes, PartiQL, and caller-selected tables are refused before a data
client is invoked.

Live network construction is a separate gate. Unit tests inject a client
that speaks the low-level DynamoDB parameter contract. ``boto3`` is imported
only after that gate is open.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Mapping

from wilvor_ai.contracts import (
    PROVENANCE_TOKEN_MAX_LENGTH,
    ContractValidationError,
    _validate_bounded_text,
)
from wilvor_ai.master_authorized_scope import (
    EffectiveScopeRequestHints,
    EffectiveScopeStatus,
    LocalTestHostScope,
    resolve_master_authorized_scope,
)
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.master_execution_grants import (
    LocalHarnessGrantIssuer,
    LocalHarnessPrincipal,
    MasterExecutionGrantEnvelope,
)
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    CONDITIONAL_REPLACE_EXACT,
    CONDITIONAL_REPLACE_EXPIRED,
    CONDITIONAL_REPLACE_EXPIRED_REPEATABLE,
    CONDITIONAL_REPLACE_UNEXPIRED,
    DYNAMODB_LEDGER_TABLE_NAME,
    MAX_LEASE_SECONDS,
    LedgerStoreUnavailable,
    MasterExecutionLedgerEnvelope,
    MasterExecutionLedgerOutcome,
    MasterExecutionLedgerState,
)
from wilvor_ai.master_read_resource_guard import (
    HISTORICAL_LIST_MAX,
    ReadOperation,
    server_read_budget,
)
from wilvor_ai.master_routing import MAX_HISTORICAL_WINDOW
from wilvor_ai.master_trusted_read_executor import (
    HostCancellation,
    MasterTrustedReadExecutor,
    NonproductionAdapterSpec,
    NonproductionReadPage,
    ReadBoundaryDenial,
    ReadExecutionOutcome,
    ReadReceiptCandidate,
    UntrustedReadRequest,
    bind_nonproduction_cancellation,
    bind_nonproduction_host_clock,
    bind_nonproduction_read_registry,
    bind_nonproduction_test_invocation,
    raw_read_observation,
)
from wilvor_ai.sprint3c_grant_ledger import (
    SPRINT3C_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED,
    DurableConsumeResult,
    Sprint3CDurableGrantLedger,
    Sprint3CIdentityBinding,
    agent_a_identity_fixture,
    ledger_envelope_from_grant,
)
from wilvor_ai.sprint3c_scoped_read import (
    _QUERY_PARTITION,
    _RESOURCE_KEY_ATTRIBUTE,
    AwsReadPlan,
    ConsumedAttemptReadBinding,
    describe_consumed_attempt_read_binding,
    interpret_server_owned_aws_page,
    plan_server_owned_aws_read,
)


SPRINT3D_AWS_EXECUTION_SCHEMA_VERSION = "wilvor.ai.sprint3d.aws_execution.v1"
SPRINT3D_PRODUCTION_EXECUTION_ENABLED = False
SPRINT3D_LIVE_NETWORK_TESTS_ENABLED = False
SPRINT3D_DISTRIBUTED_EXACTLY_ONCE = False
SPRINT3D_LEDGER_TRANSACTION_IS_NATIVE_EXECUTION = False
SPRINT3D_SOURCE_AUTHENTICITY_ESTABLISHED = False
LIVE_NETWORK_GATE_ENV = "WILVOR_SPRINT3D_LIVE_AWS"
LIVE_NETWORK_GATE_VALUE = "reviewed-nonproduction"
REVIEWED_NONPRODUCTION = "REVIEWED_NONPRODUCTION"
UPSTREAM_ISSUER = "wilvor.operator_reviewed_manifest"
MAX_TRANSIENT_ATTEMPTS = 3

LIVE_AWS_BLOCKERS = (
    "live_network_gate_closed",
    "ledger_table_not_provisioned_by_this_module",
    "production_grant_issuance_closed",
    "upstream_application_identity_not_attested",
    "iam_policy_not_reviewed_in_a_live_account",
    "describe_table_not_run_against_a_live_account",
    "source_authenticity_not_established",
    "transact_write_is_not_exactly_once_native_execution",
    "enable_ai_query_route_remains_false",
    "physical_table_names_depend_on_unapplied_name_prefix",
)

_ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
_REGION_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+$")
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,255}$")
_IAM_ROLE_RE = re.compile(r"^arn:aws:iam::([0-9]{12}):role/([A-Za-z0-9+=,.@_-]+)$")
_STS_ASSUMED_RE = re.compile(
    r"^arn:aws:sts::([0-9]{12}):assumed-role/([A-Za-z0-9+=,.@_-]+)/([A-Za-z0-9+=,.@_-]+)$"
)
_TABLE_ARN_RE = re.compile(
    r"^arn:aws:dynamodb:([a-z0-9-]+):([0-9]{12}):table/([A-Za-z0-9_.-]{3,255})$"
)
_SQL_ARGUMENT_NAMES = frozenset({"sql", "statement", "query_string", "FilterExpression"})
_WRITE_METHODS = frozenset(
    {
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "transact_write_items",
        "execute_statement",
        "execute_transaction",
        "scan",
    }
)
_TTL_ATTRIBUTES = frozenset(
    {
        "retention_expires_at_epoch",
        "ttl",
        "expires_at_epoch",
        "TimeToLive",
        "time_to_live",
    }
)
_TRANSIENT_CODES = frozenset(
    {
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
        "InternalServerError",
        "ServiceUnavailable",
        "TransactionInProgressException",
    }
)
_PROHIBITED_ACTIONS = (
    "dynamodb:Scan",
    "dynamodb:DeleteItem",
    "dynamodb:UpdateItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:ExecuteStatement",
)
_ALLOWED_PUT_CONDITIONS = frozenset(
    {
        CONDITIONAL_PUT_ABSENT,
        CONDITIONAL_REPLACE_EXACT,
        CONDITIONAL_REPLACE_UNEXPIRED,
        CONDITIONAL_REPLACE_EXPIRED,
        CONDITIONAL_REPLACE_EXPIRED_REPEATABLE,
    }
)
_GET_ITEM_KEYS = frozenset({"TableName", "Key", "ConsistentRead"})
_QUERY_KEYS = frozenset(
    {
        "TableName",
        "KeyConditionExpression",
        "ExpressionAttributeValues",
        "ExpressionAttributeNames",
        "Limit",
        "IndexName",
    }
)
_READ_OPERATIONS = frozenset({ReadOperation.GET_ITEM, ReadOperation.QUERY})


class _Seal:
    pass


_DEPLOYMENT_SEAL = _Seal()
_IDENTITY_SEAL = _Seal()
_AUTH_SEAL = _Seal()


class AwsExecutionRefusal(str, Enum):
    """Why an AWS data call was not treated as authorized work."""

    UPSTREAM_IDENTITY_MISSING = "UPSTREAM_IDENTITY_MISSING"
    UPSTREAM_IDENTITY_NOT_VERIFIED = "UPSTREAM_IDENTITY_NOT_VERIFIED"
    UPSTREAM_IDENTITY_MISMATCH = "UPSTREAM_IDENTITY_MISMATCH"
    GRANT_MISSING = "GRANT_MISSING"
    GRANT_REJECTED = "GRANT_REJECTED"
    IAM_AUTHORITY_MISSING = "IAM_AUTHORITY_MISSING"
    LEAST_PRIVILEGE_VIOLATION = "LEAST_PRIVILEGE_VIOLATION"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    REGION_MISMATCH = "REGION_MISMATCH"
    TABLE_CONFIGURATION_MISSING = "TABLE_CONFIGURATION_MISSING"
    TABLE_CONFIGURATION_MISMATCH = "TABLE_CONFIGURATION_MISMATCH"
    SOURCE_MAPPING_MISSING = "SOURCE_MAPPING_MISSING"
    FENCE_NOT_CURRENT = "FENCE_NOT_CURRENT"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CALLER_SCOPE_NOT_AUTHORITY = "CALLER_SCOPE_NOT_AUTHORITY"
    CALLER_DEADLINE_NOT_AUTHORITY = "CALLER_DEADLINE_NOT_AUTHORITY"
    CALLER_BUDGET_NOT_AUTHORITY = "CALLER_BUDGET_NOT_AUTHORITY"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    ARBITRARY_SQL = "ARBITRARY_SQL"
    WRITE_NOT_PERMITTED = "WRITE_NOT_PERMITTED"
    ARBITRARY_TABLE = "ARBITRARY_TABLE"
    PAGINATION_LIMIT_EXCEEDED = "PAGINATION_LIMIT_EXCEEDED"
    SCOPE_DENIED = "SCOPE_DENIED"
    STORE_UNAVAILABLE = "STORE_UNAVAILABLE"
    LIVE_NETWORK_NOT_REVIEWED = "LIVE_NETWORK_NOT_REVIEWED"
    PRODUCTION_EXECUTION_CLOSED = "PRODUCTION_EXECUTION_CLOSED"
    READ_NOT_COMPLETED = "READ_NOT_COMPLETED"


def _assert_closed() -> None:
    if SPRINT3D_PRODUCTION_EXECUTION_ENABLED is not False:
        raise ContractValidationError("production_execution_closed")
    if SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is not False:
        raise ContractValidationError("live_aws_network_not_reviewed")
    if SPRINT3D_DISTRIBUTED_EXACTLY_ONCE is not False:
        raise ContractValidationError("distributed_exactly_once_not_achieved")
    if SPRINT3D_LEDGER_TRANSACTION_IS_NATIVE_EXECUTION is not False:
        raise ContractValidationError("ledger_transaction_is_not_native_execution")
    if SPRINT3D_SOURCE_AUTHENTICITY_ESTABLISHED is not False:
        raise ContractValidationError("source_authenticity_not_established")
    if SPRINT3C_PRODUCTION_GRANT_LEDGER_ENABLED is not False:
        raise ContractValidationError("production_grant_issuance_closed")
    if SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is not False:
        raise ContractValidationError("distributed_exactly_once_not_achieved")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH)
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(character.isspace() for character in value):
        raise ContractValidationError(f"invalid_{field_name}")
    if "*" in value or value.casefold().startswith("arn:"):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _whole_second(value: str) -> tuple[int, int, int, int, int, int]:
    if len(value) != 20 or value[10] != "T" or not value.endswith("Z"):
        raise ContractValidationError("invalid_utc")
    try:
        year = int(value[0:4])
        month = int(value[5:7])
        day = int(value[8:10])
        hour = int(value[11:13])
        minute = int(value[14:16])
        second = int(value[17:19])
    except ValueError as exc:
        raise ContractValidationError("invalid_utc") from exc
    if value[4] != "-" or value[7] != "-" or value[13] != ":" or value[16] != ":":
        raise ContractValidationError("invalid_utc")
    return year, month, day, hour, minute, second


def _utc_tuple(value: str) -> tuple[int, int, int, int, int, int]:
    _token(value, "utc")
    return _whole_second(value)


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, Mapping):
        error = response.get("Error")
        if isinstance(error, Mapping) and isinstance(error.get("Code"), str):
            return str(error["Code"])
    return type(exc).__name__


def _conditional_failure(exc: BaseException) -> bool:
    return _error_code(exc) == "ConditionalCheckFailedException"


def _transaction_canceled(exc: BaseException) -> bool:
    return _error_code(exc) == "TransactionCanceledException"


def _transient(exc: BaseException) -> bool:
    return _error_code(exc) in _TRANSIENT_CODES


def _arn_text(value: str) -> bool:
    folded = value.casefold()
    return folded.startswith("arn:") or ":table/" in folded or folded.startswith("dynamodb:")


def _wildcard_text(value: str) -> bool:
    return "*" in value or value.casefold() in {"*", "all", "all_tables", "any", "unrestricted"}


def _sql_text(value: str) -> bool:
    folded = f" {value.casefold()} "
    return any(word in folded for word in (" select ", " insert ", " drop ", " union ", " delete "))


def table_arn(account_id: str, region: str, table_name: str) -> str:
    """Exact DynamoDB table ARN. Wildcards are not a reviewed resource."""

    if _ACCOUNT_RE.fullmatch(account_id) is None or _REGION_RE.fullmatch(region) is None:
        raise ContractValidationError("invalid_table_arn")
    if _NAME_RE.fullmatch(table_name) is None or "*" in table_name:
        raise ContractValidationError("invalid_table_arn")
    return f"arn:aws:dynamodb:{region}:{account_id}:table/{table_name}"


def iam_role_arn(account_id: str, role_name: str) -> str:
    if _ACCOUNT_RE.fullmatch(account_id) is None or _NAME_RE.fullmatch(role_name) is None:
        raise ContractValidationError("invalid_role_arn")
    return f"arn:aws:iam::{account_id}:role/{role_name}"


def _role_name(arn: str) -> tuple[str, str] | None:
    iam = _IAM_ROLE_RE.fullmatch(arn)
    if iam is not None:
        return iam.group(1), iam.group(2)
    assumed = _STS_ASSUMED_RE.fullmatch(arn)
    if assumed is not None:
        return assumed.group(1), assumed.group(2)
    return None


@dataclass(frozen=True)
class ReviewedReadTable:
    """One logical allowlist id bound to one physical table. Not a caller choice."""

    logical_resource_id: str
    physical_table_name: str
    source_name: str
    source_version_attribute: str
    observation_time_attribute: str
    hash_attribute: str
    allowed_operations: frozenset[ReadOperation]
    query_partition_attribute: str
    query_index_name: str | None = None
    sort_attribute: str | None = None
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _DEPLOYMENT_SEAL:
            raise ContractValidationError("source_mapping_missing")
        logical = _token(self.logical_resource_id, "logical_resource_id")
        if logical not in _RESOURCE_KEY_ATTRIBUTE or logical not in _QUERY_PARTITION:
            raise ContractValidationError("source_mapping_missing")
        physical = _token(self.physical_table_name, "physical_table_name")
        if _NAME_RE.fullmatch(physical) is None or physical == DYNAMODB_LEDGER_TABLE_NAME or _arn_text(physical):
            raise ContractValidationError("arbitrary_table")
        _token(self.source_name, "source_name")
        _token(self.source_version_attribute, "source_version_attribute")
        _token(self.observation_time_attribute, "observation_time_attribute")
        if self.source_version_attribute in _TTL_ATTRIBUTES or self.observation_time_attribute in _TTL_ATTRIBUTES:
            raise ContractValidationError("source_mapping_missing")
        if type(self.allowed_operations) is not frozenset or not self.allowed_operations:
            raise ContractValidationError("source_mapping_missing")
        if any(operation not in _READ_OPERATIONS for operation in self.allowed_operations):
            raise ContractValidationError("scan_not_permitted")
        expected_hash = _RESOURCE_KEY_ATTRIBUTE[logical]
        expected_partition = _QUERY_PARTITION[logical]
        if self.hash_attribute != expected_hash and ReadOperation.GET_ITEM in self.allowed_operations:
            raise ContractValidationError("table_configuration_mismatch")
        if self.query_partition_attribute != expected_partition:
            raise ContractValidationError("table_configuration_mismatch")
        if self.query_partition_attribute == self.hash_attribute:
            if self.query_index_name is not None:
                raise ContractValidationError("table_configuration_mismatch")
        elif ReadOperation.QUERY in self.allowed_operations:
            if self.query_index_name is None or _NAME_RE.fullmatch(self.query_index_name) is None:
                raise ContractValidationError("table_configuration_mismatch")
        if self.sort_attribute is not None and self.sort_attribute != "observed_at":
            raise ContractValidationError("table_configuration_mismatch")

    def __bool__(self) -> bool:
        return False


def reviewed_read_table(**fields: object) -> ReviewedReadTable:
    """Seal one reviewed mapping. A dict of caller table names cannot pass."""

    _assert_closed()
    return ReviewedReadTable(_seal=_DEPLOYMENT_SEAL, **fields)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ReviewedAwsDeployment:
    """Server-owned account, region, role, and table map. Not AWS proof by itself."""

    account_id: str
    region: str
    role_arn: str
    read_tables: tuple[ReviewedReadTable, ...]
    ledger_table_name: str = DYNAMODB_LEDGER_TABLE_NAME
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _DEPLOYMENT_SEAL:
            raise ContractValidationError("aws_authority_missing")
        if _ACCOUNT_RE.fullmatch(self.account_id) is None:
            raise ContractValidationError("invalid_account_id")
        if _REGION_RE.fullmatch(self.region) is None:
            raise ContractValidationError("invalid_region")
        if self.ledger_table_name != DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("unexpected_ledger_table")
        parsed = _IAM_ROLE_RE.fullmatch(self.role_arn)
        if parsed is None or parsed.group(1) != self.account_id:
            raise ContractValidationError("invalid_role_arn")
        if not isinstance(self.read_tables, tuple) or not self.read_tables:
            raise ContractValidationError("source_mapping_missing")
        seen_logical: set[str] = set()
        seen_physical: set[str] = set()
        for table in self.read_tables:
            if type(table) is not ReviewedReadTable:
                raise ContractValidationError("source_mapping_missing")
            if table.logical_resource_id in seen_logical or table.physical_table_name in seen_physical:
                raise ContractValidationError("source_mapping_missing")
            seen_logical.add(table.logical_resource_id)
            seen_physical.add(table.physical_table_name)

    def __bool__(self) -> bool:
        return False

    def ledger_arn(self) -> str:
        return table_arn(self.account_id, self.region, self.ledger_table_name)

    def read_table(self, logical_resource_id: str) -> ReviewedReadTable | None:
        for table in self.read_tables:
            if table.logical_resource_id == logical_resource_id:
                return table
        return None

    def resource_arns(self) -> tuple[str, ...]:
        arns = [self.ledger_arn()]
        for table in self.read_tables:
            arns.append(table_arn(self.account_id, self.region, table.physical_table_name))
        return tuple(arns)


def reviewed_aws_deployment(**fields: object) -> ReviewedAwsDeployment:
    _assert_closed()
    return ReviewedAwsDeployment(_seal=_DEPLOYMENT_SEAL, **fields)  # type: ignore[arg-type]


def expected_iam_decisions(deployment: ReviewedAwsDeployment) -> tuple[tuple[str, str, str], ...]:
    """Least-privilege matrix. ``allowed`` is only the operation the table mapping names."""

    if type(deployment) is not ReviewedAwsDeployment:
        raise ContractValidationError("aws_authority_missing")
    rows: list[tuple[str, str, str]] = []
    ledger = deployment.ledger_arn()
    for action, decision in (
        ("dynamodb:GetItem", "allowed"),
        ("dynamodb:PutItem", "allowed"),
        ("dynamodb:TransactWriteItems", "allowed"),
        ("dynamodb:Query", "deny"),
    ):
        rows.append((action, ledger, decision))
    for action in _PROHIBITED_ACTIONS:
        rows.append((action, ledger, "deny"))
    for table in deployment.read_tables:
        arn = table_arn(deployment.account_id, deployment.region, table.physical_table_name)
        rows.append(("dynamodb:GetItem", arn, "allowed" if ReadOperation.GET_ITEM in table.allowed_operations else "deny"))
        rows.append(("dynamodb:Query", arn, "allowed" if ReadOperation.QUERY in table.allowed_operations else "deny"))
        rows.append(("dynamodb:PutItem", arn, "deny"))
        rows.append(("dynamodb:TransactWriteItems", arn, "deny"))
        for action in _PROHIBITED_ACTIONS:
            rows.append((action, arn, "deny"))
    return tuple(sorted(rows))


@dataclass(frozen=True)
class StsCallerIdentity:
    account_id: str
    arn: str
    user_id: str

    def __post_init__(self) -> None:
        if _ACCOUNT_RE.fullmatch(self.account_id) is None or not self.user_id or "*" in self.user_id:
            raise ContractValidationError("aws_identity_missing")
        if _role_name(self.arn) is None or "*" in self.arn:
            raise ContractValidationError("aws_identity_missing")


@dataclass(frozen=True)
class DescribedTable:
    table_name: str
    table_status: str
    table_arn: str
    hash_key: str
    range_key: str | None
    indexes: tuple[tuple[str, str, str | None], ...]

    def __post_init__(self) -> None:
        if self.table_status != "ACTIVE":
            raise ContractValidationError("table_configuration_mismatch")
        parsed = _TABLE_ARN_RE.fullmatch(self.table_arn)
        if parsed is None or parsed.group(3) != self.table_name:
            raise ContractValidationError("table_configuration_mismatch")


def sts_identity_from_api(response: object) -> StsCallerIdentity:
    """Parse ``GetCallerIdentity``. Missing Account, Arn, or UserId fails closed."""

    if not isinstance(response, Mapping):
        raise ContractValidationError("aws_identity_missing")
    account = response.get("Account")
    arn = response.get("Arn")
    user = response.get("UserId")
    if not isinstance(account, str) or not isinstance(arn, str) or not isinstance(user, str):
        raise ContractValidationError("aws_identity_missing")
    return StsCallerIdentity(account_id=account, arn=arn, user_id=user)


def _key_schema(value: object) -> tuple[str, str | None]:
    if not isinstance(value, list) or not value:
        raise ContractValidationError("table_configuration_missing")
    hash_key = None
    range_key = None
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ContractValidationError("table_configuration_missing")
        name = entry.get("AttributeName")
        kind = entry.get("KeyType")
        if not isinstance(name, str) or not name:
            raise ContractValidationError("table_configuration_missing")
        if kind == "HASH":
            hash_key = name
        elif kind == "RANGE":
            range_key = name
        else:
            raise ContractValidationError("table_configuration_mismatch")
    if hash_key is None:
        raise ContractValidationError("table_configuration_missing")
    return hash_key, range_key


def described_table_from_api(response: object) -> DescribedTable:
    """Parse ``DescribeTable``. A missing Table, ARN, or key schema fails closed."""

    if not isinstance(response, Mapping):
        raise ContractValidationError("table_configuration_missing")
    table = response.get("Table")
    if not isinstance(table, Mapping):
        raise ContractValidationError("table_configuration_missing")
    name = table.get("TableName")
    status = table.get("TableStatus")
    arn = table.get("TableArn")
    if not isinstance(name, str) or not isinstance(status, str) or not isinstance(arn, str):
        raise ContractValidationError("table_configuration_missing")
    hash_key, range_key = _key_schema(table.get("KeySchema"))
    indexes: list[tuple[str, str, str | None]] = []
    raw_indexes = table.get("GlobalSecondaryIndexes", [])
    if raw_indexes is None:
        raw_indexes = []
    if not isinstance(raw_indexes, list):
        raise ContractValidationError("table_configuration_missing")
    for index in raw_indexes:
        if not isinstance(index, Mapping) or not isinstance(index.get("IndexName"), str):
            raise ContractValidationError("table_configuration_missing")
        index_hash, index_range = _key_schema(index.get("KeySchema"))
        indexes.append((str(index["IndexName"]), index_hash, index_range))
    return DescribedTable(
        table_name=name,
        table_status=status,
        table_arn=arn,
        hash_key=hash_key,
        range_key=range_key,
        indexes=tuple(indexes),
    )


def iam_simulation_from_api(response: object) -> tuple[tuple[str, str, str], ...]:
    """Parse ``SimulatePrincipalPolicy``. A missing evaluation fails closed."""

    if not isinstance(response, Mapping):
        raise ContractValidationError("iam_authority_missing")
    results = response.get("EvaluationResults")
    if not isinstance(results, list) or not results:
        raise ContractValidationError("iam_authority_missing")
    rows: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entry in results:
        if not isinstance(entry, Mapping):
            raise ContractValidationError("iam_authority_missing")
        action = entry.get("EvalActionName")
        resource = entry.get("EvalResourceName")
        decision = entry.get("EvalDecision")
        if not isinstance(action, str) or not isinstance(resource, str) or not isinstance(decision, str):
            raise ContractValidationError("iam_authority_missing")
        if "*" in action or "*" in resource or action == "dynamodb:*":
            raise ContractValidationError("least_privilege_violation")
        pair = (action, resource)
        if pair in seen:
            raise ContractValidationError("iam_authority_missing")
        seen.add(pair)
        if decision == "allowed":
            normalized = "allowed"
        elif decision in {"explicitDeny", "implicitDeny"}:
            normalized = "deny"
        else:
            raise ContractValidationError("iam_authority_missing")
        rows.append((action, resource, normalized))
    return tuple(sorted(rows))


@dataclass(frozen=True)
class ReviewedUpstreamIdentity:
    """Operator review of one invocation. Not a production principal and not a grant."""

    account_id: str
    issuer: str
    subject: str
    principal_ref: str
    policy_revision: str
    master_request_id: str
    planning_session_id: str
    step_id: str
    invocation_id: str
    purpose: MasterPurpose
    accepted_at_utc: str
    deadline_utc: str
    review_status: str = REVIEWED_NONPRODUCTION
    production_principal: bool = False
    _seal: object = None

    def __post_init__(self) -> None:
        if self._seal is not _IDENTITY_SEAL:
            raise ContractValidationError("upstream_identity_missing")
        if _ACCOUNT_RE.fullmatch(self.account_id) is None:
            raise ContractValidationError("invalid_account_id")
        issuer = _token(self.issuer, "issuer")
        subject = _token(self.subject, "subject")
        if issuer != UPSTREAM_ISSUER or issuer.startswith("eyJ") or "Bearer" in issuer:
            raise ContractValidationError("upstream_identity_not_verified")
        if subject.startswith("eyJ") or "Bearer" in subject:
            raise ContractValidationError("upstream_identity_not_verified")
        _token(self.principal_ref, "principal_ref")
        _token(self.policy_revision, "policy_revision")
        _token(self.master_request_id, "master_request_id")
        _token(self.planning_session_id, "planning_session_id")
        _token(self.step_id, "step_id")
        _token(self.invocation_id, "invocation_id")
        if not isinstance(self.purpose, MasterPurpose):
            raise ContractValidationError("invalid_purpose")
        _utc_tuple(self.accepted_at_utc)
        _utc_tuple(self.deadline_utc)
        if self.review_status != REVIEWED_NONPRODUCTION or self.production_principal is not False:
            raise ContractValidationError("upstream_identity_not_verified")
        distinct = (
            self.master_request_id,
            self.planning_session_id,
            self.step_id,
            self.invocation_id,
            self.principal_ref,
        )
        if len(set(distinct)) != len(distinct):
            raise ContractValidationError("request_identity_not_distinct")

    def __bool__(self) -> bool:
        return False


def seal_reviewed_upstream_identity(**fields: object) -> ReviewedUpstreamIdentity:
    """Seal a nonproduction identity review. JWT text and production flags fail closed."""

    _assert_closed()
    return ReviewedUpstreamIdentity(_seal=_IDENTITY_SEAL, **fields)  # type: ignore[arg-type]


@dataclass(frozen=True)
class TrustedAwsExecutionAuthorization:
    """Control-plane review. Possession of this object is not a DynamoDB write permit."""

    deployment: ReviewedAwsDeployment
    caller_arn: str
    _seal: object = None

    def __post_init__(self) -> None:
        _assert_closed()
        if self._seal is not _AUTH_SEAL:
            raise ContractValidationError("aws_authority_missing")
        if type(self.deployment) is not ReviewedAwsDeployment:
            raise ContractValidationError("aws_authority_missing")
        if _role_name(self.caller_arn) is None:
            raise ContractValidationError("aws_identity_missing")

    @property
    def account_id(self) -> str:
        return self.deployment.account_id

    @property
    def region(self) -> str:
        return self.deployment.region

    @property
    def native_exactly_once(self) -> bool:
        return False

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class AwsAuthorityReview:
    authorization: TrustedAwsExecutionAuthorization | None
    refusal: AwsExecutionRefusal | None
    data_plane_invoked: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.data_plane_invoked is not False:
            raise ContractValidationError("data_plane_invoked_during_review")
        if (self.authorization is None) is (self.refusal is None):
            raise ContractValidationError("invalid_authority_review")
        if self.authorization is not None and self.authorization.native_exactly_once is not False:
            raise ContractValidationError("ledger_transaction_is_not_native_execution")

    def __bool__(self) -> bool:
        return False


def _tables_match(deployment: ReviewedAwsDeployment, described: tuple[DescribedTable, ...]) -> AwsExecutionRefusal | None:
    by_name = {table.table_name: table for table in described}
    expected_names = {deployment.ledger_table_name, *(table.physical_table_name for table in deployment.read_tables)}
    if set(by_name) != expected_names:
        return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
    ledger = by_name[deployment.ledger_table_name]
    parsed = _TABLE_ARN_RE.fullmatch(ledger.table_arn)
    if (
        parsed is None
        or parsed.group(1) != deployment.region
        or parsed.group(2) != deployment.account_id
        or ledger.hash_key != "pk"
        or ledger.range_key != "sk"
    ):
        return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
    for mapping in deployment.read_tables:
        table = by_name[mapping.physical_table_name]
        parsed = _TABLE_ARN_RE.fullmatch(table.table_arn)
        if parsed is None or parsed.group(1) != deployment.region or parsed.group(2) != deployment.account_id:
            return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
        if table.hash_key != mapping.hash_attribute:
            return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
        if mapping.sort_attribute is not None and table.range_key != mapping.sort_attribute:
            return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
        if mapping.query_index_name is None:
            continue
        matched = False
        for name, index_hash, _index_range in table.indexes:
            if name == mapping.query_index_name and index_hash == mapping.query_partition_attribute:
                matched = True
        if not matched:
            return AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH
    return None


def review_aws_execution_authority(
    deployment: object,
    *,
    caller_identity: object = None,
    described_tables: object = None,
    iam_simulation: object = None,
) -> AwsAuthorityReview:
    """Verify account, region, table keys, and least privilege. Does not call the data plane.

    API-shaped mappings are parsed with the GetCallerIdentity, DescribeTable,
    and SimulatePrincipalPolicy contracts. Missing sections fail closed.
    """

    _assert_closed()
    if type(deployment) is not ReviewedAwsDeployment:
        return AwsAuthorityReview(None, AwsExecutionRefusal.ACCOUNT_MISMATCH)
    if caller_identity is None or described_tables is None or iam_simulation is None:
        if caller_identity is None:
            return AwsAuthorityReview(None, AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING)
        if described_tables is None:
            return AwsAuthorityReview(None, AwsExecutionRefusal.TABLE_CONFIGURATION_MISSING)
        return AwsAuthorityReview(None, AwsExecutionRefusal.IAM_AUTHORITY_MISSING)
    try:
        identity = caller_identity if type(caller_identity) is StsCallerIdentity else sts_identity_from_api(caller_identity)
    except ContractValidationError:
        return AwsAuthorityReview(None, AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING)
    if identity.account_id != deployment.account_id:
        return AwsAuthorityReview(None, AwsExecutionRefusal.ACCOUNT_MISMATCH)
    caller_role = _role_name(identity.arn)
    configured_role = _role_name(deployment.role_arn)
    if caller_role is None or configured_role is None or caller_role != configured_role:
        return AwsAuthorityReview(None, AwsExecutionRefusal.ACCOUNT_MISMATCH)
    if not isinstance(described_tables, tuple) or not described_tables:
        return AwsAuthorityReview(None, AwsExecutionRefusal.TABLE_CONFIGURATION_MISSING)
    parsed_tables: list[DescribedTable] = []
    for entry in described_tables:
        try:
            parsed_tables.append(entry if type(entry) is DescribedTable else described_table_from_api(entry))
        except ContractValidationError as exc:
            text = str(exc)
            if "mismatch" in text:
                return AwsAuthorityReview(None, AwsExecutionRefusal.TABLE_CONFIGURATION_MISMATCH)
            return AwsAuthorityReview(None, AwsExecutionRefusal.TABLE_CONFIGURATION_MISSING)
    mismatch = _tables_match(deployment, tuple(parsed_tables))
    if mismatch is not None:
        return AwsAuthorityReview(None, mismatch)
    try:
        observed = (
            iam_simulation
            if isinstance(iam_simulation, tuple)
            else iam_simulation_from_api(iam_simulation)
        )
    except ContractValidationError as exc:
        if "least_privilege" in str(exc):
            return AwsAuthorityReview(None, AwsExecutionRefusal.LEAST_PRIVILEGE_VIOLATION)
        return AwsAuthorityReview(None, AwsExecutionRefusal.IAM_AUTHORITY_MISSING)
    expected = expected_iam_decisions(deployment)
    if not isinstance(observed, tuple) or tuple(sorted(observed)) != expected:
        if not isinstance(observed, tuple):
            return AwsAuthorityReview(None, AwsExecutionRefusal.IAM_AUTHORITY_MISSING)
        expected_pairs = {(action, resource) for action, resource, _decision in expected}
        observed_pairs = {(action, resource) for action, resource, _decision in observed}
        if expected_pairs != observed_pairs:
            return AwsAuthorityReview(None, AwsExecutionRefusal.IAM_AUTHORITY_MISSING)
        return AwsAuthorityReview(None, AwsExecutionRefusal.LEAST_PRIVILEGE_VIOLATION)
    authorization = TrustedAwsExecutionAuthorization(
        deployment=deployment,
        caller_arn=identity.arn,
        _seal=_AUTH_SEAL,
    )
    return AwsAuthorityReview(authorization, None)


def live_network_gate_open() -> bool:
    """Operator gate. The suite constant stays false; this env value is separate."""

    _assert_closed()
    return os.environ.get(LIVE_NETWORK_GATE_ENV) == LIVE_NETWORK_GATE_VALUE


def live_aws_blockers() -> tuple[str, ...]:
    return LIVE_AWS_BLOCKERS


def _require_low_level_dynamodb_client(client: object) -> None:
    meta = getattr(client, "meta", None)
    service = getattr(meta, "service_model", None)
    if getattr(service, "service_name", None) != "dynamodb":
        raise ContractValidationError("invalid_dynamodb_client")
    for method in ("get_item", "query", "put_item", "transact_write_items"):
        if not callable(getattr(client, method, None)):
            raise ContractValidationError("invalid_dynamodb_client")


def build_boto3_dynamodb_client(authorization: object, *, region: str) -> object:
    """Construct the low-level client. Refuses before import when the gate is closed."""

    _assert_closed()
    if type(authorization) is not TrustedAwsExecutionAuthorization:
        raise ContractValidationError("aws_authority_missing")
    if authorization.region != region:
        raise ContractValidationError("aws_region_mismatch")
    if not live_network_gate_open():
        raise ContractValidationError("live_aws_network_not_reviewed")
    import boto3

    client = boto3.client("dynamodb", region_name=region)
    _require_low_level_dynamodb_client(client)
    return client


def collect_live_control_plane(deployment: object) -> dict[str, object]:
    """Read STS, DescribeTable, and IAM simulation. Not a data-plane write.

    The live gate stays closed until infrastructure and credentials are reviewed.
    """

    _assert_closed()
    if type(deployment) is not ReviewedAwsDeployment:
        raise ContractValidationError("aws_authority_missing")
    if not live_network_gate_open():
        raise ContractValidationError("live_aws_network_not_reviewed")
    import boto3

    session = boto3.session.Session(region_name=deployment.region)
    identity = session.client("sts", region_name=deployment.region).get_caller_identity()
    dynamodb = session.client("dynamodb", region_name=deployment.region)
    described = [
        dynamodb.describe_table(TableName=deployment.ledger_table_name),
        *(
            dynamodb.describe_table(TableName=table.physical_table_name)
            for table in deployment.read_tables
        ),
    ]
    actions = sorted({action for action, _resource, _decision in expected_iam_decisions(deployment)})
    simulation = session.client("iam", region_name=deployment.region).simulate_principal_policy(
        PolicySourceArn=deployment.role_arn,
        ActionNames=actions,
        ResourceArns=list(deployment.resource_arns()),
    )
    return {
        "caller_identity": identity,
        "described_tables": described,
        "iam_simulation": simulation,
    }


def real_client_integration_contract() -> dict[str, object]:
    """Exact client contract. A passing local test does not remove the live blockers."""

    _assert_closed()
    return {
        "schema_version": SPRINT3D_AWS_EXECUTION_SCHEMA_VERSION,
        "ledger": {
            "service": "dynamodb",
            "client": "boto3.client('dynamodb') low-level client, not a Table resource",
            "consume_api": "TransactWriteItems",
            "transact_shape": (
                "exactly two Put requests, each with ConditionExpression "
                "attribute_not_exists(#pk) AND attribute_not_exists(#sk)"
            ),
            "attempt_state": "STARTED",
            "consumption_marker": "pk prefix consume-v1#, TTL attributes absent",
            "fence_read": "GetItem ConsistentRead=true",
            "fence_update": "PutItem with the published conditional replace expressions",
            "transient_attempts": MAX_TRANSIENT_ATTEMPTS,
            "conditional_conflict_retried_as_new_start": False,
            "prohibited_apis": (
                "Scan",
                "Query",
                "DeleteItem",
                "UpdateItem",
                "BatchWriteItem",
                "ExecuteStatement",
            ),
            "table_name": DYNAMODB_LEDGER_TABLE_NAME,
            "exactly_once_native_execution": False,
        },
        "read": {
            "apis": ("GetItem", "Query"),
            "consistent_read": True,
            "query_limit_required": True,
            "query_limit_ceiling": HISTORICAL_LIST_MAX,
            "exclusive_start_key_accepted": False,
            "caller_selected_table": False,
            "scan": False,
            "partiql": False,
            "writes": False,
            "source_authenticity": MasterSourceAuthenticity.NOT_ESTABLISHED.value,
        },
        "control_plane": {
            "sts": "GetCallerIdentity",
            "dynamodb": "DescribeTable",
            "iam": "SimulatePrincipalPolicy",
            "data_plane_during_review": False,
        },
        "live_gate_env": LIVE_NETWORK_GATE_ENV,
        "live_gate_value": LIVE_NETWORK_GATE_VALUE,
        "live_tests_enabled_constant": False,
        "blockers": LIVE_AWS_BLOCKERS,
    }


def open_production_aws_execution(*_args: object, **_kwargs: object) -> None:
    """Production execution stays closed. No client method is invoked."""

    _assert_closed()
    raise ContractValidationError("production_execution_closed")


def _invoke(inner: object, method: str, kwargs: dict[str, object]) -> object:
    function = getattr(inner, method)
    last: BaseException | None = None
    for attempt in range(MAX_TRANSIENT_ATTEMPTS):
        try:
            return function(**kwargs)
        except LedgerStoreUnavailable:
            raise
        except Exception as exc:
            if _conditional_failure(exc) or _transaction_canceled(exc):
                raise
            last = exc
            if not _transient(exc) or attempt == MAX_TRANSIENT_ATTEMPTS - 1:
                raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
    raise LedgerStoreUnavailable("durable_storage_unavailable") from last


def _attribute_text(item: Mapping[str, object], name: str) -> str | None:
    value = item.get(name)
    if isinstance(value, dict) and isinstance(value.get("S"), str):
        return str(value["S"])
    return None


class AuthorizedLedgerDynamoClient:
    """Ledger data plane. Scan and deletes are methods that do not reach the inner client."""

    def __init__(self, inner: object, authorization: object) -> None:
        _assert_closed()
        if type(authorization) is not TrustedAwsExecutionAuthorization:
            raise ContractValidationError("aws_authority_missing")
        for method in ("get_item", "put_item", "transact_write_items"):
            if not callable(getattr(inner, method, None)):
                raise ContractValidationError("invalid_ledger_client")
        self._inner = inner
        self._authorization = authorization
        self._table = authorization.deployment.ledger_table_name

    def get_item(self, **kwargs: object) -> object:
        self._require_ledger_get(kwargs)
        return _invoke(self._inner, "get_item", kwargs)

    def put_item(self, **kwargs: object) -> object:
        self._require_ledger_put(kwargs)
        return _invoke(self._inner, "put_item", kwargs)

    def transact_write_items(self, **kwargs: object) -> object:
        self._require_consume_transaction(kwargs)
        return _invoke(self._inner, "transact_write_items", kwargs)

    def scan(self, **_kwargs: object) -> None:
        raise ContractValidationError("scan_not_permitted")

    def query(self, **_kwargs: object) -> None:
        raise ContractValidationError("ledger_query_not_permitted")

    def delete_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("delete_not_permitted")

    def update_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("update_not_permitted")

    def batch_write_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("write_not_permitted")

    def execute_statement(self, **_kwargs: object) -> None:
        raise ContractValidationError("arbitrary_sql")

    def _require_ledger_get(self, kwargs: Mapping[str, object]) -> None:
        if set(kwargs) - {"TableName", "Key", "ConsistentRead"}:
            raise ContractValidationError("unexpected_ledger_request")
        if kwargs.get("TableName") != self._table or kwargs.get("ConsistentRead") is not True:
            raise ContractValidationError("unexpected_ledger_table")
        key = kwargs.get("Key")
        if not isinstance(key, dict) or set(key) != {"pk", "sk"}:
            raise ContractValidationError("unexpected_ledger_request")

    def _require_ledger_put(self, kwargs: Mapping[str, object]) -> None:
        if kwargs.get("TableName") != self._table:
            raise ContractValidationError("unexpected_ledger_table")
        if kwargs.get("ConditionExpression") not in _ALLOWED_PUT_CONDITIONS:
            raise ContractValidationError("unexpected_ledger_condition")
        item = kwargs.get("Item")
        if not isinstance(item, dict):
            raise ContractValidationError("invalid_ledger_record")
        pk = _attribute_text(item, "pk")
        if pk is None or not pk.startswith("ledger-v1#") or pk.startswith("consume-v1#"):
            raise ContractValidationError("unexpected_ledger_item")

    def _require_consume_transaction(self, kwargs: Mapping[str, object]) -> None:
        items = kwargs.get("TransactItems")
        if set(kwargs) - {"TransactItems"} or not isinstance(items, list) or len(items) != 2:
            raise ContractValidationError("unexpected_consume_transaction")
        prefixes: list[str] = []
        for entry in items:
            if not isinstance(entry, dict) or set(entry) != {"Put"}:
                raise ContractValidationError("unexpected_consume_transaction")
            put = entry["Put"]
            if not isinstance(put, dict):
                raise ContractValidationError("unexpected_consume_transaction")
            if put.get("TableName") != self._table or put.get("ConditionExpression") != CONDITIONAL_PUT_ABSENT:
                raise ContractValidationError("unexpected_consume_transaction")
            item = put.get("Item")
            if not isinstance(item, dict):
                raise ContractValidationError("unexpected_consume_transaction")
            pk = _attribute_text(item, "pk")
            if pk is None:
                raise ContractValidationError("unexpected_consume_transaction")
            prefixes.append(pk.split("#", 1)[0])
            if pk.startswith("consume-v1#") and any(name in item for name in _TTL_ATTRIBUTES):
                raise ContractValidationError("consumption_marker_must_not_have_ttl")
        if sorted(prefixes) != ["consume-v1", "ledger-v1"]:
            raise ContractValidationError("unexpected_consume_transaction")


class AuthorizedReadDynamoClient:
    """Specialist reads. The physical table must be the reviewed mapping."""

    def __init__(self, inner: object, authorization: object) -> None:
        _assert_closed()
        if type(authorization) is not TrustedAwsExecutionAuthorization:
            raise ContractValidationError("aws_authority_missing")
        for method in ("get_item", "query"):
            if not callable(getattr(inner, method, None)):
                raise ContractValidationError("invalid_read_client")
        self._inner = inner
        self._authorization = authorization
        self._tables = {
            table.physical_table_name: table for table in authorization.deployment.read_tables
        }

    def get_item(self, **kwargs: object) -> object:
        self._require_get(kwargs)
        return _invoke(self._inner, "get_item", kwargs)

    def query(self, **kwargs: object) -> object:
        self._require_query(kwargs)
        return _invoke(self._inner, "query", kwargs)

    def scan(self, **_kwargs: object) -> None:
        raise ContractValidationError("scan_not_permitted")

    def put_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("write_not_permitted")

    def delete_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("delete_not_permitted")

    def update_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("update_not_permitted")

    def transact_write_items(self, **_kwargs: object) -> None:
        raise ContractValidationError("write_not_permitted")

    def execute_statement(self, **_kwargs: object) -> None:
        raise ContractValidationError("arbitrary_sql")

    def _mapping(self, kwargs: Mapping[str, object]) -> ReviewedReadTable:
        name = kwargs.get("TableName")
        if not isinstance(name, str) or name not in self._tables or name == DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("arbitrary_table")
        return self._tables[name]

    def _require_get(self, kwargs: Mapping[str, object]) -> None:
        if set(kwargs) - _GET_ITEM_KEYS:
            raise ContractValidationError("unexpected_read_request")
        mapping = self._mapping(kwargs)
        if ReadOperation.GET_ITEM not in mapping.allowed_operations or kwargs.get("ConsistentRead") is not True:
            raise ContractValidationError("unexpected_read_request")
        key = kwargs.get("Key")
        if not isinstance(key, dict) or set(key) != {mapping.hash_attribute}:
            raise ContractValidationError("unexpected_read_request")

    def _require_query(self, kwargs: Mapping[str, object]) -> None:
        if set(kwargs) - _QUERY_KEYS or "Limit" not in kwargs or "KeyConditionExpression" not in kwargs:
            raise ContractValidationError("unexpected_read_request")
        if "ExclusiveStartKey" in kwargs or "FilterExpression" in kwargs:
            raise ContractValidationError("unexpected_read_request")
        mapping = self._mapping(kwargs)
        if ReadOperation.QUERY not in mapping.allowed_operations:
            raise ContractValidationError("unexpected_read_request")
        limit = kwargs.get("Limit")
        if type(limit) is not int or isinstance(limit, bool) or limit < 1 or limit > HISTORICAL_LIST_MAX:
            raise ContractValidationError("query_limit_exceeded")
        condition = kwargs.get("KeyConditionExpression")
        if not isinstance(condition, str) or mapping.query_partition_attribute not in condition or _sql_text(condition):
            raise ContractValidationError("arbitrary_sql")
        if "BETWEEN" in condition and mapping.sort_attribute != "observed_at":
            raise ContractValidationError("unexpected_read_request")
        index = kwargs.get("IndexName")
        if mapping.query_index_name is None:
            if index is not None:
                raise ContractValidationError("arbitrary_table")
        elif index != mapping.query_index_name:
            raise ContractValidationError("arbitrary_table")


@dataclass(frozen=True)
class _StoredAttempt:
    identity: ReviewedUpstreamIdentity
    envelope: MasterExecutionGrantEnvelope
    ledger_envelope: MasterExecutionLedgerEnvelope
    attempt_owner_id: str
    attempt_token: str
    fence_epoch: int
    grant_nonce: str
    resource_id: str
    operation: ReadOperation


@dataclass(frozen=True)
class AwsConsumeResult:
    """One consume outcome. A DynamoDB success still has ``native_exactly_once`` false."""

    refusal: AwsExecutionRefusal | None
    consume: DurableConsumeResult | None
    native_exactly_once: bool = False
    ledger_write_is_native_execution: bool = False
    control_plane_verified: bool = False

    def __post_init__(self) -> None:
        _assert_closed()
        if self.native_exactly_once is not False or self.ledger_write_is_native_execution is not False:
            raise ContractValidationError("ledger_transaction_is_not_native_execution")
        if self.consume is not None:
            if self.consume.distributed_exactly_once is not False or self.consume.specialist_executed is not False:
                raise ContractValidationError("ledger_transaction_is_not_native_execution")
            if self.consume.verified_aws_provenance is not False:
                raise ContractValidationError("durable_adapter_type_is_not_aws_provenance")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class AwsBoundedReadResult:
    """One bounded read. Empty pages are not a verified zero."""

    refusal: AwsExecutionRefusal | None
    receipt: ReadReceiptCandidate | None
    observation: dict[str, object] | None
    data_plane_invoked: bool
    live_aws_network_invoked: bool = False
    page_empty: bool = False
    page_complete: bool = False
    population_complete: bool = False
    verified_zero: bool = False
    approved_source_adapter: bool = False
    source_authenticity_established: bool = False
    sigmet_polygon_intersection_established: bool = False
    caller_arguments_widened_authorization: bool = False
    native_exactly_once: bool = False
    evaluation_time_utc: str | None = None
    accepted_at_utc: str | None = None
    deadline_utc: str | None = None
    source_observed_at_utc: str | None = None

    def __post_init__(self) -> None:
        _assert_closed()
        for flag in (
            self.live_aws_network_invoked,
            self.population_complete,
            self.verified_zero,
            self.approved_source_adapter,
            self.source_authenticity_established,
            self.sigmet_polygon_intersection_established,
            self.caller_arguments_widened_authorization,
            self.native_exactly_once,
        ):
            if flag is not False:
                raise ContractValidationError("scoped_aws_read_is_not_source_authentication")
        if self.receipt is not None:
            if (
                self.receipt.approved_source_adapter is not False
                or self.receipt.verified_zero_minted is not False
                or self.receipt.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED
            ):
                raise ContractValidationError("read_observation_is_not_source_authentication")
        if self.page_empty and self.page_complete:
            raise ContractValidationError("empty_page_is_not_complete_coverage")

    def __bool__(self) -> bool:
        return False


class Sprint3DAwsExecutionPort:
    """Reviewed account plus the existing conditional consume and bounded read executor."""

    def __init__(
        self,
        authorization: object,
        ledger_client: object,
        read_client: object,
        issuer: object,
        *,
        now_utc: str,
        token_source: object = None,
    ) -> None:
        _assert_closed()
        if type(authorization) is not TrustedAwsExecutionAuthorization:
            raise ContractValidationError("aws_authority_missing")
        if type(issuer) is not LocalHarnessGrantIssuer:
            raise ContractValidationError("invalid_grant_issuer")
        _utc_tuple(now_utc)
        self._authorization = authorization
        self._read_client = AuthorizedReadDynamoClient(read_client, authorization)
        self._read_inner = read_client
        self._issuer = issuer
        self._now = now_utc
        self._ledger = Sprint3CDurableGrantLedger(
            issuer=issuer,
            client=AuthorizedLedgerDynamoClient(ledger_client, authorization),
            token_source=token_source if callable(token_source) else None,
        )
        self._attempts: dict[str, _StoredAttempt] = {}
        self._reads: dict[tuple[str, str], AwsBoundedReadResult] = {}
        self._tool_calls: dict[str, int] = {}

    @property
    def native_exactly_once(self) -> bool:
        return False

    @property
    def verified_aws_account_and_table_provenance(self) -> bool:
        """Control-plane review is not the closed ledger provenance flag.

        The 3C ledger keeps ``verified_aws_provenance`` false because a
        DynamoDB adapter type is not production provenance and a transaction
        is not exactly-once specialist execution. This port's authorization
        is the separate account, region, table, and IAM review.
        """

        return False

    def now_utc(self) -> str:
        return self._now

    def advance_seconds(self, seconds: int) -> None:
        if type(seconds) is not int or seconds < 1:
            raise ContractValidationError("invalid_clock_advance")
        parsed = _utc_tuple(self._now)
        from datetime import datetime, timezone

        current = datetime(*parsed, tzinfo=timezone.utc)
        self._now = (current + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def consume_signed_grant(
        self,
        claim: object,
        *,
        envelope: object,
        principal: object,
        identity: object,
        owner_id: str,
        now_utc: str | None = None,
        lease_seconds: int = 60,
        production_requested: bool = False,
    ) -> AwsConsumeResult:
        """Consume one signed grant with TransactWriteItems when authority is present."""

        _assert_closed()
        if production_requested is True:
            return AwsConsumeResult(AwsExecutionRefusal.PRODUCTION_EXECUTION_CLOSED, None, control_plane_verified=True)
        identity_refusal = _identity_refusal(identity, envelope, self._authorization)
        if identity_refusal is not None:
            return AwsConsumeResult(identity_refusal, None, control_plane_verified=True)
        if claim is None or type(envelope) is not MasterExecutionGrantEnvelope:
            return AwsConsumeResult(AwsExecutionRefusal.GRANT_MISSING, None, control_plane_verified=True)
        if type(principal) is not LocalHarnessPrincipal:
            return AwsConsumeResult(AwsExecutionRefusal.GRANT_MISSING, None, control_plane_verified=True)
        if type(lease_seconds) is not int or lease_seconds < 1 or lease_seconds > MAX_LEASE_SECONDS:
            raise ContractValidationError("invalid_lease_seconds")
        assert isinstance(identity, ReviewedUpstreamIdentity)
        stamp = self._now if now_utc is None else now_utc
        _utc_tuple(stamp)
        synthetic = _synthetic_binding(identity)
        try:
            result = self._ledger.consume_signed_grant(
                claim,
                envelope=envelope,
                principal=principal,
                identity=synthetic,
                owner_id=owner_id,
                now_utc=stamp,
                lease_seconds=lease_seconds,
                production_requested=False,
            )
        except LedgerStoreUnavailable as exc:
            raise LedgerStoreUnavailable("durable_storage_unavailable") from exc
        if result.distributed_exactly_once is not False or result.specialist_executed is not False:
            raise ContractValidationError("ledger_transaction_is_not_native_execution")
        self._remember(identity, envelope, result)
        refusal = None
        if result.outcome is MasterExecutionLedgerOutcome.REJECTED and result.attempt is None:
            refusal = AwsExecutionRefusal.GRANT_REJECTED
        return AwsConsumeResult(refusal, result, control_plane_verified=True)

    def execute_bounded_read(
        self,
        request: object,
        host: object,
        *,
        owner_id: str,
        attempt_token: str,
        fence_epoch: int,
        grant_nonce: str,
        tool_call_id: str,
        action_id: str,
        cancellation: object = None,
        location_hint: str | None = None,
    ) -> AwsBoundedReadResult:
        """Read one reviewed table with the server fence, scope, deadline, and page ceiling."""

        _assert_closed()
        del action_id
        if type(request) is not UntrustedReadRequest:
            raise ContractValidationError("invalid_read_request")
        if type(host) is not LocalTestHostScope:
            raise ContractValidationError("host_not_issued")
        _token(owner_id, "owner_id")
        _token(attempt_token, "attempt_token")
        _token(grant_nonce, "grant_nonce")
        _token(tool_call_id, "tool_call_id")
        if type(fence_epoch) is not int or fence_epoch < 0:
            raise ContractValidationError("invalid_fence_epoch")
        if cancellation is not None and type(cancellation) is not HostCancellation:
            raise ContractValidationError("cancellation_not_host_issued")
        stored = self._attempts.get(request.invocation_id)
        if stored is None:
            return self._unread(request, AwsExecutionRefusal.GRANT_MISSING, ())
        if (
            owner_id != stored.attempt_owner_id
            or attempt_token != stored.attempt_token
            or fence_epoch != stored.fence_epoch
            or grant_nonce != stored.grant_nonce
        ):
            return self._unread(request, AwsExecutionRefusal.FENCE_NOT_CURRENT, ())
        cache_key = (stored.ledger_envelope.idempotency_key, tool_call_id)
        cached = self._reads.get(cache_key)
        if cached is not None:
            return cached
        presented = self._ledger.fenced_attempt_matches(
            stored.envelope,
            owner_id=owner_id,
            attempt_token=attempt_token,
            fence_epoch=fence_epoch,
            grant_nonce=grant_nonce,
        )
        if presented.matches_current_attempt is not True:
            return self._unread(request, AwsExecutionRefusal.FENCE_NOT_CURRENT, ())
        if _utc_tuple(self._now) >= _utc_tuple(stored.ledger_envelope.deadline_utc):
            return self._unread(request, AwsExecutionRefusal.DEADLINE_EXPIRED, ())
        early = _request_refusal(request, stored)
        if early is not None:
            return self._unread(request, early[0], early[1])
        _executions, _turns, max_calls = server_read_budget(stored.ledger_envelope.specialist)
        used = self._tool_calls.get(request.invocation_id, 0)
        if used >= _executions or used >= max_calls:
            return self._unread(request, AwsExecutionRefusal.BUDGET_EXHAUSTED, (ReadBoundaryDenial.BUDGET_EXHAUSTED,))
        binding = _binding_from_stored(stored)
        if (
            request.master_request_id != binding.master_request_id
            or request.planning_session_id != binding.planning_session_id
            or request.invocation_id != binding.invocation_id
            or request.tool_name != binding.tool_name
            or request.resource_id != binding.resource_id
            or request.operation is not binding.operation
        ):
            return self._unread(request, AwsExecutionRefusal.UPSTREAM_IDENTITY_MISMATCH, (ReadBoundaryDenial.IDENTITY_MISMATCH,))
        hints = _hints(binding, request, location_hint=location_hint)
        decision = resolve_master_authorized_scope(hints, host)
        if decision.status is not EffectiveScopeStatus.BOUNDED_DESCRIBED:
            return self._unread(request, AwsExecutionRefusal.SCOPE_DENIED, (ReadBoundaryDenial.POLICY_EVALUATION_DENIED,))
        if decision.principal_ref != binding.principal_ref or decision.policy_revision != binding.policy_revision:
            return self._unread(request, AwsExecutionRefusal.SCOPE_DENIED, (ReadBoundaryDenial.POLICY_EVALUATION_DENIED,))
        window = None
        if binding.specialist is MasterSpecialist.HISTORICAL_ANALYTICS:
            start = decision.historical_window.eligible_start_utc
            end = decision.historical_window.eligible_end_utc
            if start is None or end is None:
                return self._unread(
                    request,
                    AwsExecutionRefusal.SCOPE_DENIED,
                    (ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,),
                )
            window = (start, end)
        mapping = self._authorization.deployment.read_table(binding.resource_id)
        if mapping is None or binding.operation not in mapping.allowed_operations:
            return self._unread(request, AwsExecutionRefusal.SOURCE_MAPPING_MISSING, (ReadBoundaryDenial.RESOURCE_MISMATCH,))
        before = _operation_count(self._read_inner)
        receipt = self._execute(binding, request, decision, mapping, window, tool_call_id, used, cancellation)
        invoked = _operation_count(self._read_inner) > before
        if receipt.adapter_invoked and receipt.outcome is ReadExecutionOutcome.COMPLETED:
            self._tool_calls[request.invocation_id] = used + 1
        result = _read_result(
            None if receipt.outcome is ReadExecutionOutcome.COMPLETED else AwsExecutionRefusal.READ_NOT_COMPLETED,
            receipt,
            invoked,
            self._now,
            binding.accepted_at_utc,
            binding.deadline_utc,
        )
        if receipt.outcome is ReadExecutionOutcome.COMPLETED:
            self._reads[cache_key] = result
        return result

    def _remember(
        self,
        identity: ReviewedUpstreamIdentity,
        envelope: MasterExecutionGrantEnvelope,
        result: DurableConsumeResult,
    ) -> None:
        attempt = result.attempt
        if attempt is None or attempt.state is not MasterExecutionLedgerState.STARTED:
            return
        if result.outcome not in {MasterExecutionLedgerOutcome.CLAIMED, MasterExecutionLedgerOutcome.REPLAYED}:
            return
        ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
        resource_id, operation = _single_resource(ledger_envelope.resource_scope)
        self._attempts[envelope.invocation_id] = _StoredAttempt(
            identity=identity,
            envelope=envelope,
            ledger_envelope=ledger_envelope,
            attempt_owner_id=attempt.owner_id,
            attempt_token=attempt.attempt_token,
            fence_epoch=attempt.fence_epoch,
            grant_nonce=attempt.grant_nonce,
            resource_id=resource_id,
            operation=operation,
        )

    def _unread(
        self,
        request: UntrustedReadRequest,
        refusal: AwsExecutionRefusal,
        denials: tuple[ReadBoundaryDenial, ...],
    ) -> AwsBoundedReadResult:
        if not denials:
            denials = (ReadBoundaryDenial.PRODUCTION_GRANT_NOT_INTEGRATED,)
        clock = bind_nonproduction_host_clock(self._now)
        executor = MasterTrustedReadExecutor(bind_nonproduction_read_registry(()), clock)
        receipt = executor.reject_unexecuted(request, denials)
        return _read_result(refusal, receipt, False, self._now, None, None)

    def _execute(
        self,
        binding: ConsumedAttemptReadBinding,
        request: UntrustedReadRequest,
        decision: object,
        mapping: ReviewedReadTable,
        window: tuple[str, str] | None,
        tool_call_id: str,
        used: int,
        cancellation: object,
    ) -> ReadReceiptCandidate:
        if cancellation is None:
            cancellation = bind_nonproduction_cancellation()
        assert isinstance(cancellation, HostCancellation)
        remaining = _seconds_until(self._now, binding.deadline_utc)
        timeout = 1 if remaining < 1 else remaining
        codes = tuple(decision.region_scope.effective_state_codes)  # type: ignore[attr-defined]
        invocation = bind_nonproduction_test_invocation(
            master_request_id=binding.master_request_id,
            planning_session_id=binding.planning_session_id,
            step_id=binding.step_id,
            invocation_id=binding.invocation_id,
            specialist=binding.specialist,
            purpose=binding.purpose,
            capability=binding.capability,
            tool_name=binding.tool_name,
            tool_call_id=tool_call_id,
            resource_id=binding.resource_id,
            operation=binding.operation,
            principal_ref=binding.principal_ref,
            policy_revision=binding.policy_revision,
            accepted_at_utc=binding.accepted_at_utc,
            deadline_utc=binding.deadline_utc,
            idempotency_key=binding.idempotency_key,
            grant_nonce=binding.grant_nonce,
            executions_used=used,
            model_turns_used=0,
            tool_calls_used=used,
            timeout_budget_seconds=timeout,
            region_codes=frozenset(codes) if codes else None,
            historical_start_utc=None if window is None else window[0],
            historical_end_utc=None if window is None else window[1],
            decision_aircraft_id=decision.decision_target_scope.described_aircraft_id,  # type: ignore[attr-defined]
            decision_recommendation_id=decision.decision_target_scope.described_recommendation_id,  # type: ignore[attr-defined]
            correlation_id=binding.correlation_id,
        )
        client = self._read_client
        now_utc = self._now

        def read(executed_arguments: dict[str, object], host_cancellation: HostCancellation) -> NonproductionReadPage:
            if host_cancellation.is_cancelled():
                raise ContractValidationError("cancelled")
            executed = dict(executed_arguments)
            if window is not None:
                executed["start_utc"] = window[0]
                executed["end_utc"] = window[1]
            plan = plan_server_owned_aws_read(binding.resource_id, binding.operation, executed)
            kwargs = dict(plan.kwargs)
            kwargs["TableName"] = mapping.physical_table_name
            if plan.method == "query" and mapping.query_index_name is not None:
                kwargs["IndexName"] = mapping.query_index_name
            response = getattr(client, plan.method)(**kwargs)
            row_limit = 1 if binding.operation is ReadOperation.GET_ITEM else int(kwargs["Limit"])
            page = interpret_server_owned_aws_page(response, plan, now_utc=now_utc, row_limit=row_limit)
            return _overlay_source(page, response, plan, mapping)

        registry = bind_nonproduction_read_registry(
            (
                NonproductionAdapterSpec(
                    specialist=binding.specialist,
                    tool_name=binding.tool_name,
                    capability=binding.capability,
                    resource_id=binding.resource_id,
                    operation=binding.operation,
                    read=read,
                ),
            )
        )
        executor = MasterTrustedReadExecutor(registry, bind_nonproduction_host_clock(self._now))
        return executor.execute(request, host_invocation=invocation, cancellation=cancellation)


def open_reviewed_aws_execution(
    deployment: object,
    *,
    caller_identity: object = None,
    described_tables: object = None,
    iam_simulation: object = None,
    ledger_client: object = None,
    read_client: object = None,
    issuer: object = None,
    now_utc: str,
    token_source: object = None,
) -> tuple[AwsAuthorityReview, Sprint3DAwsExecutionPort | None]:
    """Open a port only after the control-plane review. A failed review does not touch clients."""

    review = review_aws_execution_authority(
        deployment,
        caller_identity=caller_identity,
        described_tables=described_tables,
        iam_simulation=iam_simulation,
    )
    if review.authorization is None or ledger_client is None or read_client is None or issuer is None:
        return review, None
    return review, Sprint3DAwsExecutionPort(
        review.authorization,
        ledger_client,
        read_client,
        issuer,
        now_utc=now_utc,
        token_source=token_source,
    )


def _identity_refusal(
    identity: object,
    envelope: object,
    authorization: TrustedAwsExecutionAuthorization,
) -> AwsExecutionRefusal | None:
    if identity is None:
        return AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING
    if type(identity) is Sprint3CIdentityBinding:
        return AwsExecutionRefusal.UPSTREAM_IDENTITY_NOT_VERIFIED
    if type(identity) is not ReviewedUpstreamIdentity:
        return AwsExecutionRefusal.UPSTREAM_IDENTITY_MISSING
    if type(envelope) is not MasterExecutionGrantEnvelope:
        return AwsExecutionRefusal.GRANT_MISSING
    if identity.account_id != authorization.account_id:
        return AwsExecutionRefusal.ACCOUNT_MISMATCH
    pairs = (
        (identity.principal_ref, envelope.principal_ref),
        (identity.policy_revision, envelope.policy_revision),
        (identity.master_request_id, envelope.master_request_id),
        (identity.planning_session_id, envelope.planning_session_id),
        (identity.step_id, envelope.step_id),
        (identity.invocation_id, envelope.invocation_id),
        (identity.accepted_at_utc, envelope.accepted_at_utc),
        (identity.deadline_utc, envelope.deadline_utc),
    )
    if any(left != right for left, right in pairs) or identity.purpose is not envelope.purpose:
        return AwsExecutionRefusal.UPSTREAM_IDENTITY_MISMATCH
    return None


def _synthetic_binding(identity: ReviewedUpstreamIdentity) -> Sprint3CIdentityBinding:
    """The durable ledger still requires the closed synthetic handoff shape.

    The reviewed identity is an additional gate. It does not become a
    production principal, and the synthetic record is not sufficient alone.
    """

    return agent_a_identity_fixture(
        master_request_id=identity.master_request_id,
        planning_session_id=identity.planning_session_id,
        step_id=identity.step_id,
        invocation_id=identity.invocation_id,
        principal_ref=identity.principal_ref,
        policy_revision=identity.policy_revision,
        purpose=identity.purpose,
        accepted_at_utc=identity.accepted_at_utc,
        deadline_utc=identity.deadline_utc,
        policy_decision="DENY",
        authority_origin="NOT_ESTABLISHED",
    )


def _single_resource(resource_scope: str) -> tuple[str, ReadOperation]:
    parts = resource_scope.split(",")
    if len(parts) != 1 or ":" not in parts[0]:
        raise ContractValidationError("invalid_resource_scope")
    resource_id, operation_name = parts[0].split(":", 1)
    if operation_name not in {"GET_ITEM", "QUERY"}:
        raise ContractValidationError("scan_not_permitted")
    return resource_id, ReadOperation(operation_name)


def _binding_from_stored(stored: _StoredAttempt) -> ConsumedAttemptReadBinding:
    envelope = stored.ledger_envelope
    window = None
    if envelope.historical_start_utc is not None and envelope.historical_end_utc is not None:
        window = (envelope.historical_start_utc, envelope.historical_end_utc)
    return describe_consumed_attempt_read_binding(
        owner_id=stored.attempt_owner_id,
        attempt_token=stored.attempt_token,
        fence_epoch=stored.fence_epoch,
        grant_nonce=stored.grant_nonce,
        idempotency_key=envelope.idempotency_key,
        payload_fingerprint=envelope.fingerprint,
        ledger_state="STARTED",
        grant_consumption_lifecycle="CONSUMED_LOCAL_HARNESS",
        master_request_id=envelope.master_request_id,
        planning_session_id=envelope.planning_session_id,
        step_id=envelope.step_id,
        invocation_id=envelope.invocation_id,
        specialist=envelope.specialist,
        purpose=envelope.purpose,
        capability=envelope.capability,
        principal_ref=envelope.principal_ref,
        policy_revision=envelope.policy_revision,
        tool_name=stored.envelope.tool_name,
        resource_id=stored.resource_id,
        operation=stored.operation,
        region_scope=tuple(envelope.region_scope.split(",")),
        accepted_at_utc=envelope.accepted_at_utc,
        deadline_utc=envelope.deadline_utc,
        correlation_id=envelope.correlation_id,
        historical_window=window,
    )


def _request_argument(request: UntrustedReadRequest, name: str) -> object:
    for key, value in request.requested_arguments:
        if key == name:
            return value
    return None


def _request_refusal(
    request: UntrustedReadRequest,
    stored: _StoredAttempt,
) -> tuple[AwsExecutionRefusal, tuple[ReadBoundaryDenial, ...]] | None:
    if request.operation is ReadOperation.SCAN or request.tool_name.casefold() == "scan":
        return AwsExecutionRefusal.SCAN_NOT_PERMITTED, (ReadBoundaryDenial.SCAN_NOT_PERMITTED,)
    if request.tool_name.casefold() in _WRITE_METHODS or request.resource_id.casefold() in _WRITE_METHODS:
        return AwsExecutionRefusal.WRITE_NOT_PERMITTED, (ReadBoundaryDenial.ARBITRARY_AWS_METHOD,)
    if _arn_text(request.resource_id) or _arn_text(request.tool_name):
        return AwsExecutionRefusal.ARBITRARY_TABLE, (ReadBoundaryDenial.ARBITRARY_TABLE_ARN,)
    if _wildcard_text(request.resource_id):
        return AwsExecutionRefusal.ARBITRARY_TABLE, (ReadBoundaryDenial.WILDCARD_RESOURCE,)
    for name, value in request.requested_arguments:
        if name in _SQL_ARGUMENT_NAMES or (isinstance(value, str) and _sql_text(value)):
            return AwsExecutionRefusal.ARBITRARY_SQL, (ReadBoundaryDenial.ARBITRARY_SQL,)
        if name.casefold() in _WRITE_METHODS:
            return AwsExecutionRefusal.WRITE_NOT_PERMITTED, (ReadBoundaryDenial.ARBITRARY_AWS_METHOD,)
        if name == "limit" and type(value) is int and not isinstance(value, bool) and value > HISTORICAL_LIST_MAX:
            return AwsExecutionRefusal.PAGINATION_LIMIT_EXCEEDED, (ReadBoundaryDenial.QUERY_LIMIT_EXCEEDED,)
    if request.caller_scope is not None or request.caller_authorized is not None or request.caller_policy_decision is not None:
        return AwsExecutionRefusal.CALLER_SCOPE_NOT_AUTHORITY, (ReadBoundaryDenial.CALLER_SCOPE_NOT_AUTHORITY,)
    if request.caller_deadline_utc is not None:
        return AwsExecutionRefusal.CALLER_DEADLINE_NOT_AUTHORITY, (ReadBoundaryDenial.CALLER_DEADLINE_NOT_AUTHORITY,)
    if request.caller_max_tool_calls is not None:
        return AwsExecutionRefusal.CALLER_BUDGET_NOT_AUTHORITY, (ReadBoundaryDenial.CALLER_BUDGET_NOT_AUTHORITY,)
    if request.caller_callable is not None or request.caller_tool_result is not None:
        return AwsExecutionRefusal.CALLER_SCOPE_NOT_AUTHORITY, (ReadBoundaryDenial.CALLER_CALLABLE_REJECTED,)
    start = _request_argument(request, "start_utc")
    end = _request_argument(request, "end_utc")
    if isinstance(start, str) and isinstance(end, str):
        try:
            from datetime import datetime, timezone

            start_parts = _utc_tuple(start)
            end_parts = _utc_tuple(end)
            start_dt = datetime(*start_parts, tzinfo=timezone.utc)
            end_dt = datetime(*end_parts, tzinfo=timezone.utc)
        except ContractValidationError:
            return AwsExecutionRefusal.SCOPE_DENIED, (ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,)
        if end_dt <= start_dt or end_dt - start_dt > MAX_HISTORICAL_WINDOW:
            return (
                AwsExecutionRefusal.SCOPE_DENIED,
                (ReadBoundaryDenial.HISTORICAL_WINDOW_OUTSIDE_HOST,),
            )
    if request.resource_id != stored.resource_id or request.operation is not stored.operation:
        return AwsExecutionRefusal.ARBITRARY_TABLE, (ReadBoundaryDenial.RESOURCE_MISMATCH,)
    return None


def _hints(
    binding: ConsumedAttemptReadBinding,
    request: UntrustedReadRequest,
    *,
    location_hint: str | None,
) -> EffectiveScopeRequestHints:
    aircraft = _request_argument(request, "aircraft_id")
    callsign = _request_argument(request, "callsign")
    payload: dict[str, object] = {
        "master_request_id": binding.master_request_id,
        "planning_session_id": binding.planning_session_id,
        "step_id": binding.step_id,
        "invocation_id": binding.invocation_id,
        "correlation_id": binding.correlation_id,
        "idempotency_key": binding.idempotency_key,
        "specialist": binding.specialist,
        "purpose": binding.purpose,
        "capability": binding.capability,
        "tool_name": binding.tool_name,
        "resource_id": binding.resource_id,
        "operation": binding.operation,
        "accepted_at_utc": binding.accepted_at_utc,
        "deadline_utc": binding.deadline_utc,
        "arguments": request.requested_arguments,
        "location_hint": location_hint,
        "aircraft_id": aircraft if isinstance(aircraft, str) else None,
        "entity_id": aircraft if isinstance(aircraft, str) else None,
        "callsign": callsign if isinstance(callsign, str) else None,
        "principal_label": request.principal_label,
        "caller_authorized": request.caller_authorized,
        "caller_deadline_utc": request.caller_deadline_utc,
        "caller_max_tool_calls": request.caller_max_tool_calls,
    }
    if binding.historical_window is not None:
        payload["historical_start_utc"] = binding.historical_window[0]
        payload["historical_end_utc"] = binding.historical_window[1]
    return EffectiveScopeRequestHints(**payload)  # type: ignore[arg-type]


def _overlay_source(
    page: NonproductionReadPage,
    response: object,
    plan: AwsReadPlan,
    mapping: ReviewedReadTable,
) -> NonproductionReadPage:
    items: list[Mapping[str, object]] = []
    if isinstance(response, dict):
        if plan.method == "get_item" and isinstance(response.get("Item"), dict):
            items = [response["Item"]]
        elif isinstance(response.get("Items"), list):
            items = [item for item in response["Items"] if isinstance(item, dict)]
    versions = [_attribute_text(item, mapping.source_version_attribute) for item in items]
    present = [version for version in versions if version is not None]
    conflict = len(set(present)) > 1 or (bool(items) and len(present) not in {0, len(items)})
    version = present[0] if present and not conflict and len(present) == len(items) else None
    observed = None
    for item in items:
        text = _attribute_text(item, mapping.observation_time_attribute)
        if text is not None:
            observed = text
            break
    if observed is not None:
        _utc_tuple(observed)
    return NonproductionReadPage(
        record_ids=page.record_ids,
        source_name=mapping.source_name,
        source_version=version,
        query_id=plan.query_id,
        page_index=page.page_index,
        has_more=page.has_more,
        truncated=page.has_more or page.truncated or conflict,
        source_observed_at_utc=observed,
        next_page_token=page.next_page_token,
    )


def _seconds_until(now_utc: str, deadline_utc: str) -> int:
    from datetime import datetime, timezone

    now = datetime(*_utc_tuple(now_utc), tzinfo=timezone.utc)
    deadline = datetime(*_utc_tuple(deadline_utc), tzinfo=timezone.utc)
    return int((deadline - now).total_seconds())


def _operation_count(client: object) -> int:
    operations = getattr(client, "operations", None)
    if isinstance(operations, list):
        return len(operations)
    return 0


def _read_result(
    refusal: AwsExecutionRefusal | None,
    receipt: ReadReceiptCandidate,
    invoked: bool,
    now_utc: str,
    accepted_at_utc: str | None,
    deadline_utc: str | None,
) -> AwsBoundedReadResult:
    completed = receipt.outcome is ReadExecutionOutcome.COMPLETED
    page_empty = bool(completed and receipt.page_empty)
    page_complete = bool(completed and not receipt.page_empty and not receipt.has_more and not receipt.truncated)
    observed = receipt.source_reported_observed_at_utc if completed else None
    return AwsBoundedReadResult(
        refusal=refusal,
        receipt=receipt,
        observation=raw_read_observation(receipt),
        data_plane_invoked=invoked,
        page_empty=page_empty,
        page_complete=page_complete,
        evaluation_time_utc=now_utc,
        accepted_at_utc=accepted_at_utc,
        deadline_utc=deadline_utc,
        source_observed_at_utc=observed,
    )


_assert_closed()


__all__ = [
    "LIVE_AWS_BLOCKERS",
    "LIVE_NETWORK_GATE_ENV",
    "LIVE_NETWORK_GATE_VALUE",
    "REVIEWED_NONPRODUCTION",
    "SPRINT3D_AWS_EXECUTION_SCHEMA_VERSION",
    "SPRINT3D_DISTRIBUTED_EXACTLY_ONCE",
    "SPRINT3D_LEDGER_TRANSACTION_IS_NATIVE_EXECUTION",
    "SPRINT3D_LIVE_NETWORK_TESTS_ENABLED",
    "SPRINT3D_PRODUCTION_EXECUTION_ENABLED",
    "SPRINT3D_SOURCE_AUTHENTICITY_ESTABLISHED",
    "UPSTREAM_ISSUER",
    "AuthorizedLedgerDynamoClient",
    "AuthorizedReadDynamoClient",
    "AwsAuthorityReview",
    "AwsBoundedReadResult",
    "AwsConsumeResult",
    "AwsExecutionRefusal",
    "ReviewedAwsDeployment",
    "ReviewedReadTable",
    "ReviewedUpstreamIdentity",
    "Sprint3DAwsExecutionPort",
    "TrustedAwsExecutionAuthorization",
    "build_boto3_dynamodb_client",
    "collect_live_control_plane",
    "described_table_from_api",
    "expected_iam_decisions",
    "iam_role_arn",
    "iam_simulation_from_api",
    "live_aws_blockers",
    "live_network_gate_open",
    "open_production_aws_execution",
    "open_reviewed_aws_execution",
    "real_client_integration_contract",
    "review_aws_execution_authority",
    "reviewed_aws_deployment",
    "reviewed_read_table",
    "seal_reviewed_upstream_identity",
    "sts_identity_from_api",
    "table_arn",
]
