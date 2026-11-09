"""Isolated read-only probe for the AI host execution role.

This module is not the deployed AI host handler. ``modules/ai_host`` still
packages only ``src/handler.py``. Nothing here deploys, invokes, or opens
the Agent C live-network gate.

A future Lambda window would run ``lambda_handler`` under credentials the
Lambda service injected for ``wilvor-dev-ai-host-lambda-role``. This copy
keeps that path closed. Offline tests drive ``run_offline_probe`` with
doubles. The doubles are not a role session.

Agent C is imported only when a probe function needs it. The closed handler
returns before that import, so a constant-false package does not pay the
Agent C import during initialization.
"""

from __future__ import annotations

import os
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

_AGENT_LOADED = False
ContractValidationError = None
DYNAMODB_LEDGER_TABLE_NAME = None
ReadOperation = None
ABSENT_RECORD_IS_LOW_RISK = None
EMPTY_PAGE_IS_VERIFIED_ZERO = None
plan_server_owned_aws_read = None
LIVE_NETWORK_GATE_ENV = None
LIVE_NETWORK_GATE_VALUE = None
SPRINT3D_LIVE_NETWORK_TESTS_ENABLED = None
SPRINT3D_PRODUCTION_EXECUTION_ENABLED = None
AuthorizedReadDynamoClient = None
StsCallerIdentity = None
expected_iam_decisions = None
iam_role_arn = None
open_production_aws_execution = None
review_aws_execution_authority = None
reviewed_aws_deployment = None
reviewed_read_table = None
sts_identity_from_api = None
table_arn = None


def _load_agent_c() -> None:
    """Import Agent C on the probe path. The closed handler does not call this."""

    global _AGENT_LOADED
    global ContractValidationError, DYNAMODB_LEDGER_TABLE_NAME, ReadOperation
    global ABSENT_RECORD_IS_LOW_RISK, EMPTY_PAGE_IS_VERIFIED_ZERO, plan_server_owned_aws_read
    global LIVE_NETWORK_GATE_ENV, LIVE_NETWORK_GATE_VALUE
    global SPRINT3D_LIVE_NETWORK_TESTS_ENABLED, SPRINT3D_PRODUCTION_EXECUTION_ENABLED
    global AuthorizedReadDynamoClient, StsCallerIdentity, expected_iam_decisions
    global iam_role_arn, open_production_aws_execution, review_aws_execution_authority
    global reviewed_aws_deployment, reviewed_read_table, sts_identity_from_api, table_arn
    if _AGENT_LOADED:
        _diag("agent_c_imports_completed")
        return
    _diag("agent_c_imports_started")
    from wilvor_ai.contracts import ContractValidationError as _ContractValidationError
    from wilvor_ai.master_execution_ledger import DYNAMODB_LEDGER_TABLE_NAME as _LEDGER_TABLE
    from wilvor_ai.master_read_resource_guard import ReadOperation as _ReadOperation
    from wilvor_ai.sprint3c_scoped_read import (
        ABSENT_RECORD_IS_LOW_RISK as _ABSENT_RECORD_IS_LOW_RISK,
        EMPTY_PAGE_IS_VERIFIED_ZERO as _EMPTY_PAGE_IS_VERIFIED_ZERO,
        plan_server_owned_aws_read as _plan_server_owned_aws_read,
    )
    from wilvor_ai.sprint3d_aws_execution import (
        LIVE_NETWORK_GATE_ENV as _LIVE_NETWORK_GATE_ENV,
        LIVE_NETWORK_GATE_VALUE as _LIVE_NETWORK_GATE_VALUE,
        SPRINT3D_LIVE_NETWORK_TESTS_ENABLED as _SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
        SPRINT3D_PRODUCTION_EXECUTION_ENABLED as _SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
        AuthorizedReadDynamoClient as _AuthorizedReadDynamoClient,
        StsCallerIdentity as _StsCallerIdentity,
        expected_iam_decisions as _expected_iam_decisions,
        iam_role_arn as _iam_role_arn,
        open_production_aws_execution as _open_production_aws_execution,
        review_aws_execution_authority as _review_aws_execution_authority,
        reviewed_aws_deployment as _reviewed_aws_deployment,
        reviewed_read_table as _reviewed_read_table,
        sts_identity_from_api as _sts_identity_from_api,
        table_arn as _table_arn,
    )

    ContractValidationError = _ContractValidationError
    DYNAMODB_LEDGER_TABLE_NAME = _LEDGER_TABLE
    ReadOperation = _ReadOperation
    ABSENT_RECORD_IS_LOW_RISK = _ABSENT_RECORD_IS_LOW_RISK
    EMPTY_PAGE_IS_VERIFIED_ZERO = _EMPTY_PAGE_IS_VERIFIED_ZERO
    plan_server_owned_aws_read = _plan_server_owned_aws_read
    LIVE_NETWORK_GATE_ENV = _LIVE_NETWORK_GATE_ENV
    LIVE_NETWORK_GATE_VALUE = _LIVE_NETWORK_GATE_VALUE
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED = _SPRINT3D_LIVE_NETWORK_TESTS_ENABLED
    SPRINT3D_PRODUCTION_EXECUTION_ENABLED = _SPRINT3D_PRODUCTION_EXECUTION_ENABLED
    AuthorizedReadDynamoClient = _AuthorizedReadDynamoClient
    StsCallerIdentity = _StsCallerIdentity
    expected_iam_decisions = _expected_iam_decisions
    iam_role_arn = _iam_role_arn
    open_production_aws_execution = _open_production_aws_execution
    review_aws_execution_authority = _review_aws_execution_authority
    reviewed_aws_deployment = _reviewed_aws_deployment
    reviewed_read_table = _reviewed_read_table
    sts_identity_from_api = _sts_identity_from_api
    table_arn = _table_arn
    _AGENT_LOADED = True
    _diag("agent_c_imports_completed")


PROBE_SCHEMA = "wilvor.ai.execution_role_probe.v1"
REQUEST_SCHEMA = "wilvor.ai.execution_role_probe.request.v1"
ACCOUNT_ID = "396037508959"
REGION = "us-west-1"
ROLE_NAME = "wilvor-dev-ai-host-lambda-role"
FUNCTION_NAME = "wilvor-dev-ai-host"
AIRCRAFT_TABLE_NAME = "wilvor-dev-aircraft-current-state"
LOGICAL_AIRCRAFT = "aircraft"
PROBE_AIRCRAFT_ID = "WILVOR-SPRINT3D-READONLY-PROBE"
PROBE_OPERATION = "getitem_aircraft_probe"
CALLSIGN_INDEX_NAME = "callsign-position_time_epoch-index"
H3_INDEX_NAME = "current_h3_cell-position_time_epoch-index"
LAMBDA_TIMEOUT_SECONDS = 30
CONNECT_TIMEOUT_SECONDS = 2
READ_TIMEOUT_SECONDS = 3
TOTAL_MAX_ATTEMPTS = 1
_DIAG_STARTED = 0.0
_DIAG_FORBIDDEN = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "aws_secret",
    "credential",
    "password",
)


def _diag(checkpoint: str) -> None:
    """Flush a timing checkpoint. Checkpoint names are fixed; no credentials."""

    if any(token.casefold() in checkpoint.casefold() for token in _DIAG_FORBIDDEN):
        return
    elapsed_ms = 0.0 if _DIAG_STARTED == 0.0 else (time.perf_counter() - _DIAG_STARTED) * 1000.0
    sys.stdout.write(f"WILVOR_PROBE_DIAG checkpoint={checkpoint} elapsed_ms={elapsed_ms:.1f}\n")
    sys.stdout.flush()


OFFLINE_PROBE_PREPARED = "OFFLINE_PROBE_PREPARED"
REAL_EXECUTION_ROLE_NOT_YET_TESTED = "REAL_EXECUTION_ROLE_NOT_YET_TESTED"
LEDGER_WRITE_NOT_EXECUTED = "LEDGER_WRITE_NOT_EXECUTED"
PRODUCTION_NOT_READY = "PRODUCTION_NOT_READY"
CLASSIFICATIONS = (
    OFFLINE_PROBE_PREPARED,
    REAL_EXECUTION_ROLE_NOT_YET_TESTED,
    LEDGER_WRITE_NOT_EXECUTED,
    PRODUCTION_NOT_READY,
)
LIVE_READ_CLASSIFICATIONS = (
    "EXECUTION_ROLE_GETITEM_ATTEMPTED",
    LEDGER_WRITE_NOT_EXECUTED,
    PRODUCTION_NOT_READY,
)
LIVE_REFUSED_CLASSIFICATIONS = (
    "EXECUTION_ROLE_CALL_REFUSED",
    LEDGER_WRITE_NOT_EXECUTED,
    PRODUCTION_NOT_READY,
)
# One legal assumed-role ARN. The session suffix is an example. STS does not
# document that Lambda sets RoleSessionName to the function name, so the probe
# records the suffix and does not require it to equal FUNCTION_NAME.
EXPECTED_ASSUMED_ROLE_ARN = (
    f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/{FUNCTION_NAME}"
)
_ASSUMED_ROLE_ARN = re.compile(
    rf"^arn:aws:sts::{ACCOUNT_ID}:assumed-role/{re.escape(ROLE_NAME)}/([A-Za-z0-9+=,.@_-]+)$"
)
TRUST_SOURCE_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{FUNCTION_NAME}"
ASSUME_ROLE_COMPATIBILITY = "retained"
ASSUME_ROLE_COMPATIBILITY_REASON = (
    "A closed invoke of wilvor-dev-ai-host under the current trust policy "
    "returned Lambda HTTP 200, handler 503 ai_host_invoke_closed, and no "
    "FunctionError. Reserved concurrency was restored to 0 and the original "
    "CodeSha256 was unchanged. aws:SourceAccount and aws:SourceArn stay. "
    "This module does not change the trust policy."
)

# Source constant. The file on disk stays false. The live-test zip is a
# separate artifact whose handler.py copy sets this assignment to True.
PROBE_LIVE_CALLS_ENABLED = False

ALLOWLISTED_EVENT = MappingProxyType(
    {
        "schema": REQUEST_SCHEMA,
        "operation": PROBE_OPERATION,
        "aircraft_id": PROBE_AIRCRAFT_ID,
    }
)
_CREDENTIAL_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
_CHAIN_FALLBACK_ENV = (
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_SHARED_CREDENTIALS_FILE",
)
_CLOSED_FLAGS = (
    "verified_zero",
    "verified_low_risk",
    "aircraft_absence_established",
    "source_authenticity_established",
    "record_modified",
    "ledger_write_executed",
    "grant_consumed",
    "model_invoked",
    "production_authorized",
    "table_described_by_execution_role",
    "iam_simulated_by_execution_role",
)


def _closed_constants() -> None:
    _load_agent_c()
    if SPRINT3D_PRODUCTION_EXECUTION_ENABLED is not False or SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is not False:
        raise ContractValidationError("production_execution_closed")
    if EMPTY_PAGE_IS_VERIFIED_ZERO is not False or ABSENT_RECORD_IS_LOW_RISK is not False:
        raise ContractValidationError("absent_record_is_not_low_risk")


def sealed_aircraft_mapping():
    """The reviewed dev aircraft mapping. The event cannot select another table."""

    _closed_constants()
    return reviewed_read_table(
        logical_resource_id=LOGICAL_AIRCRAFT,
        physical_table_name=AIRCRAFT_TABLE_NAME,
        source_name="aircraft-current-state",
        source_version_attribute="source_version",
        observation_time_attribute="source_observed_at_utc",
        hash_attribute="aircraft_id",
        query_partition_attribute="callsign",
        allowed_operations=frozenset({ReadOperation.GET_ITEM}),
    )


def sealed_deployment():
    aircraft = sealed_aircraft_mapping()
    return reviewed_aws_deployment(
        account_id=ACCOUNT_ID,
        region=REGION,
        role_arn=iam_role_arn(ACCOUNT_ID, ROLE_NAME),
        read_tables=(aircraft,),
    )


def _described_table(
    name: str,
    hash_key: str,
    range_key: str | None,
    indexes: tuple[tuple[str, str, str], ...] = (),
) -> dict[str, object]:
    schema: list[dict[str, str]] = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key is not None:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    secondary = []
    for index_name, index_hash, index_range in indexes:
        index_schema = [{"AttributeName": index_hash, "KeyType": "HASH"}]
        if index_range:
            index_schema.append({"AttributeName": index_range, "KeyType": "RANGE"})
        secondary.append({"IndexName": index_name, "KeySchema": index_schema})
    return {
        "Table": {
            "TableName": name,
            "TableStatus": "ACTIVE",
            "TableArn": table_arn(ACCOUNT_ID, REGION, name),
            "KeySchema": schema,
            "GlobalSecondaryIndexes": secondary,
        }
    }


def sealed_described_tables() -> tuple[dict[str, object], dict[str, object]]:
    """Reviewed schema seal. The execution role is not granted DescribeTable.

    The operator preflight re-reads the live table before any window. This
    tuple is the mapping Agent C already reviewed. It is not a live
    DescribeTable response and it is not taken from the Lambda event.
    """

    return (
        _described_table(DYNAMODB_LEDGER_TABLE_NAME, "pk", "sk"),
        _described_table(
            AIRCRAFT_TABLE_NAME,
            "aircraft_id",
            None,
            (
                (H3_INDEX_NAME, "current_h3_cell", "position_time_epoch"),
                (CALLSIGN_INDEX_NAME, "callsign", "position_time_epoch"),
            ),
        ),
    )


def sealed_iam_simulation(deployment: object) -> tuple[tuple[str, str, str], ...]:
    """Expected matrix. The execution role is not granted iam:SimulatePrincipalPolicy."""

    return expected_iam_decisions(deployment)  # type: ignore[arg-type]


def sealed_getitem_kwargs(deployment: object) -> dict[str, object]:
    """One consistent GetItem. The physical name comes from the sealed mapping."""

    plan = plan_server_owned_aws_read(
        LOGICAL_AIRCRAFT,
        ReadOperation.GET_ITEM,
        {"aircraft_id": PROBE_AIRCRAFT_ID},
    )
    if plan.method != "get_item" or plan.kwargs.get("TableName") != LOGICAL_AIRCRAFT:
        raise ContractValidationError("arbitrary_table")
    mapping = deployment.read_table(LOGICAL_AIRCRAFT)  # type: ignore[attr-defined]
    if mapping is None or mapping.physical_table_name != AIRCRAFT_TABLE_NAME:
        raise ContractValidationError("arbitrary_table")
    if ReadOperation.GET_ITEM not in mapping.allowed_operations or ReadOperation.QUERY in mapping.allowed_operations:
        raise ContractValidationError("unexpected_read_request")
    kwargs = dict(plan.kwargs)
    kwargs["TableName"] = mapping.physical_table_name
    key = kwargs.get("Key")
    if (
        set(kwargs) != {"TableName", "Key", "ConsistentRead"}
        or kwargs.get("ConsistentRead") is not True
        or not isinstance(key, dict)
        or set(key) != {"aircraft_id"}
        or key.get("aircraft_id") != {"S": PROBE_AIRCRAFT_ID}
    ):
        raise ContractValidationError("unexpected_read_request")
    return kwargs


def event_is_allowlisted(event: object) -> bool:
    return type(event) is dict and event == dict(ALLOWLISTED_EVENT) and set(event) == set(ALLOWLISTED_EVENT)


def assert_execution_role_session(identity: StsCallerIdentity) -> str:
    """Require an assumed-role session of this role, not an IAM user or another role.

    GetCallerIdentity returns the ARN of the session that already exists.
    The account and role name are required. The session suffix is whatever
    RoleSessionName the assumer supplied. Official Lambda execution-role
    documentation does not say that suffix is the function name, so a
    different suffix of this role is recorded and accepted. An IAM user ARN,
    a bare IAM role ARN, and any other role name are refused. The event cannot
    supply the ARN. This function does not call AssumeRole.
    """

    _load_agent_c()
    if type(identity) is not StsCallerIdentity:
        raise ContractValidationError("execution_role_session_required")
    match = _ASSUMED_ROLE_ARN.fullmatch(identity.arn)
    if identity.account_id != ACCOUNT_ID or match is None:
        raise ContractValidationError("execution_role_session_required")
    session = match.group(1)
    prefix, separator, user_session = identity.user_id.partition(":")
    if separator != ":" or not prefix.startswith("AROA") or len(prefix) <= 4 or user_session != session:
        raise ContractValidationError("execution_role_session_required")
    return session


def credential_source_refusal(environ: Mapping[str, str]) -> str | None:
    """Refuse every path other than the Lambda-injected environment variables.

    The default SDK chain is used only after this check. The three credential
    variables must be present so the chain cannot fall through, and a shared
    profile, web-identity file, role ARN, or container-credential URI must be
    absent.
    """

    _load_agent_c()
    if environ.get("AWS_PROFILE"):
        return "operator_profile_refused"
    if any(environ.get(name) for name in _CHAIN_FALLBACK_ENV):
        return "credential_chain_fallback_refused"
    if environ.get("AWS_LAMBDA_FUNCTION_NAME") != FUNCTION_NAME:
        return "execution_role_function_required"
    if environ.get("AWS_REGION") != REGION:
        return "execution_role_region_required"
    default_region = environ.get("AWS_DEFAULT_REGION")
    if default_region not in (None, "", REGION):
        return "execution_role_region_required"
    if any(not environ.get(name) for name in _CREDENTIAL_ENV):
        return "execution_role_credentials_absent"
    if environ.get(LIVE_NETWORK_GATE_ENV) == LIVE_NETWORK_GATE_VALUE:
        return "agent_c_live_gate_must_stay_closed"
    return None


def sdk_retry_config() -> dict[str, int]:
    """One total network attempt, including the initial call.

    Botocore ``retries.max_attempts`` is extra retries after the first
    request, so ``max_attempts: 1`` would allow two GetItem calls.
    ``total_max_attempts: 1`` is the initial call only.
    """

    return {"total_max_attempts": TOTAL_MAX_ATTEMPTS}


def new_boto3_clients():
    """Build clients with the default SDK chain and an explicit region.

    Call only after ``credential_source_refusal`` returns None. Lambda tells
    function code to leave credential selection to the default chain, which
    selects the execution-role environment variables first. Keys are not
    copied into the client constructor.
    """

    import boto3
    from botocore.config import Config

    config = Config(
        connect_timeout=CONNECT_TIMEOUT_SECONDS,
        read_timeout=READ_TIMEOUT_SECONDS,
        retries=sdk_retry_config(),
    )
    return (
        boto3.client("sts", region_name=REGION, config=config),
        boto3.client("dynamodb", region_name=REGION, config=config),
    )


def build_execution_role_clients(environ: Mapping[str, str]):
    _load_agent_c()
    refusal = credential_source_refusal(environ)
    if refusal is not None:
        raise ContractValidationError(refusal)
    if PROBE_LIVE_CALLS_ENABLED is not True:
        raise ContractValidationError("execution_role_probe_closed")
    return new_boto3_clients()


@dataclass(frozen=True)
class ProbeResult:
    """One probe outcome. A missing item is an absent observation, not a verified zero."""

    refusal: str | None
    observation_present: bool
    observation_absent: bool
    get_item_calls: int
    caller_arn: str | None
    account_id: str | None
    role_session_name: str | None = None
    verified_zero: bool = False
    verified_low_risk: bool = False
    aircraft_absence_established: bool = False
    source_authenticity_established: bool = False
    record_modified: bool = False
    ledger_write_executed: bool = False
    grant_consumed: bool = False
    model_invoked: bool = False
    production_authorized: bool = False
    table_described_by_execution_role: bool = False
    iam_simulated_by_execution_role: bool = False
    live_aws_invoked: bool = False
    classifications: tuple[str, ...] = CLASSIFICATIONS

    def __post_init__(self) -> None:
        _closed_constants()
        for name in _CLOSED_FLAGS:
            if getattr(self, name) is not False:
                raise ContractValidationError("probe_safety_flag_closed")
        if self.live_aws_invoked is False:
            expected = CLASSIFICATIONS
        else:
            if PROBE_LIVE_CALLS_ENABLED is not True:
                raise ContractValidationError("real_execution_role_not_yet_tested")
            expected = LIVE_READ_CLASSIFICATIONS if self.refusal is None else LIVE_REFUSED_CLASSIFICATIONS
        if self.classifications != expected:
            raise ContractValidationError("probe_classification")
        if self.refusal is None:
            if self.observation_present is self.observation_absent:
                raise ContractValidationError("probe_observation_ambiguous")
            if self.get_item_calls != 1:
                raise ContractValidationError("read_budget_exceeded")
            if not isinstance(self.role_session_name, str) or self.role_session_name == "":
                raise ContractValidationError("execution_role_session_required")
            expected_arn = f"arn:aws:sts::{ACCOUNT_ID}:assumed-role/{ROLE_NAME}/{self.role_session_name}"
            if self.account_id != ACCOUNT_ID or self.caller_arn != expected_arn:
                raise ContractValidationError("execution_role_session_required")
        elif self.observation_present is not False or self.observation_absent is not False:
            raise ContractValidationError("refusal_is_not_an_observation")

    def __bool__(self) -> bool:
        return False


class GetItemOnlyClient:
    """Forwards one sealed GetItem. Every other method stops on this object."""

    def __init__(self, inner: object) -> None:
        _load_agent_c()
        self._inner = inner
        self.forwarded: list[dict[str, object]] = []

    def get_item(self, **kwargs: object) -> object:
        if kwargs.get("TableName") != AIRCRAFT_TABLE_NAME:
            raise ContractValidationError("arbitrary_table")
        if len(self.forwarded) >= 1:
            raise ContractValidationError("read_budget_exceeded")
        self.forwarded.append(dict(kwargs))
        return self._inner.get_item(**kwargs)  # type: ignore[attr-defined]

    def query(self, **_kwargs: object) -> None:
        raise ContractValidationError("query_not_permitted")

    def scan(self, **_kwargs: object) -> None:
        raise ContractValidationError("scan_not_permitted")

    def put_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("write_not_permitted")

    def update_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("update_not_permitted")

    def delete_item(self, **_kwargs: object) -> None:
        raise ContractValidationError("delete_not_permitted")

    def transact_write_items(self, **_kwargs: object) -> None:
        raise ContractValidationError("write_not_permitted")

    def invoke_model(self, **_kwargs: object) -> None:
        raise ContractValidationError("model_invocation_not_permitted")


def _reader(dynamodb: object, authorization: object) -> tuple[AuthorizedReadDynamoClient, GetItemOnlyClient]:
    guard = GetItemOnlyClient(dynamodb)
    return AuthorizedReadDynamoClient(guard, authorization), guard


def rejected_operation(dynamodb: object, method: str, kwargs: dict[str, object] | None = None) -> str:
    """Drive a forbidden call through Agent C. The inner client must not be reached."""

    _closed_constants()
    if method == "invoke_model":
        raise ContractValidationError("model_invocation_not_permitted")
    if method == "production":
        open_production_aws_execution()
    deployment = sealed_deployment()
    identity = StsCallerIdentity(
        account_id=ACCOUNT_ID,
        arn=EXPECTED_ASSUMED_ROLE_ARN,
        user_id=f"AROAOFFLINEPROBE:{FUNCTION_NAME}",
    )
    review = review_aws_execution_authority(
        deployment,
        caller_identity=identity,
        described_tables=sealed_described_tables(),
        iam_simulation=sealed_iam_simulation(deployment),
    )
    if review.authorization is None:
        raise ContractValidationError("aws_authority_missing")
    reader, _guard = _reader(dynamodb, review.authorization)
    target = getattr(reader, method)
    target(**(kwargs or {}))
    raise ContractValidationError("forbidden_operation_was_accepted")


def _interpret_item(response: object) -> tuple[bool, bool]:
    if not isinstance(response, Mapping) or any(name in response for name in ("VerifiedZero", "LowRisk", "Absent")):
        raise ContractValidationError("probe_read_failed")
    if "Item" in response:
        return True, False
    return False, True


def run_offline_probe(
    event: object,
    *,
    sts: object,
    dynamodb: object,
    test_seam: bool = False,
    described_tables_override: object = None,
    iam_simulation_override: object = None,
) -> ProbeResult:
    """Run the read boundary against injected clients. This does not open a socket."""

    _closed_constants()
    if (described_tables_override is not None or iam_simulation_override is not None) and test_seam is not True:
        raise ContractValidationError("untrusted_control_plane_input")
    if not event_is_allowlisted(event):
        return ProbeResult(refusal="unsupported_request", observation_present=False, observation_absent=False, get_item_calls=0, caller_arn=None, account_id=None)
    _diag("before_sts")
    raw_identity = sts.get_caller_identity()  # type: ignore[attr-defined]
    _diag("after_sts")
    try:
        identity = sts_identity_from_api(raw_identity)
        session_name = assert_execution_role_session(identity)
    except ContractValidationError:
        return ProbeResult(refusal="execution_role_session_required", observation_present=False, observation_absent=False, get_item_calls=0, caller_arn=None, account_id=None)
    deployment = sealed_deployment()
    described = sealed_described_tables() if described_tables_override is None else described_tables_override
    simulation = sealed_iam_simulation(deployment) if iam_simulation_override is None else iam_simulation_override
    review = review_aws_execution_authority(
        deployment,
        caller_identity=identity,
        described_tables=described,
        iam_simulation=simulation,
    )
    if review.authorization is None or review.data_plane_invoked is not False:
        code = "aws_authority_missing" if review.refusal is None else review.refusal.value
        return ProbeResult(
            refusal=code,
            observation_present=False,
            observation_absent=False,
            get_item_calls=0,
            caller_arn=identity.arn,
            account_id=identity.account_id,
            role_session_name=session_name,
        )
    reader, guard = _reader(dynamodb, review.authorization)
    _diag("before_getitem")
    response = reader.get_item(**sealed_getitem_kwargs(deployment))
    _diag("after_getitem")
    present, absent = _interpret_item(response)
    return ProbeResult(
        refusal=None,
        observation_present=present,
        observation_absent=absent,
        get_item_calls=len(guard.forwarded),
        caller_arn=identity.arn,
        account_id=identity.account_id,
        role_session_name=session_name,
    )


def lambda_response(result: ProbeResult) -> dict[str, object]:
    status = 200 if result.refusal is None else 403 if result.refusal == "execution_role_session_required" else 400
    body = {
        "schema": PROBE_SCHEMA,
        "refusal": result.refusal,
        "observation_present": result.observation_present,
        "observation_absent": result.observation_absent,
        "verified_zero": False,
        "verified_low_risk": False,
        "aircraft_absence_established": False,
        "source_authenticity_established": False,
        "record_modified": False,
        "ledger_write_executed": False,
        "grant_consumed": False,
        "model_invoked": False,
        "production_authorized": False,
        "table_described_by_execution_role": False,
        "iam_simulated_by_execution_role": False,
        "live_aws_invoked": result.live_aws_invoked,
        "classifications": list(result.classifications),
        "caller_arn": result.caller_arn,
        "account_id": result.account_id,
        "role_session_name": result.role_session_name,
        "get_item_calls": result.get_item_calls,
        "handler_armed": PROBE_LIVE_CALLS_ENABLED is True,
        "trust_policy_retained": True,
        "trust_execution_blocker": False,
    }
    return {"statusCode": status, "headers": {"content-type": "application/json"}, "body": body}


def _closed_response(reason: str) -> dict[str, object]:
    return {
        "statusCode": 503,
        "headers": {"content-type": "application/json"},
        "body": {
            "schema": "wilvor.ai.execution_role_probe.closed.v1",
            "admitted": False,
            "live_calls_enabled": False,
            "handler_armed": PROBE_LIVE_CALLS_ENABLED is True,
            "reason": reason,
            "classifications": list(CLASSIFICATIONS),
            "verified_zero": False,
            "ledger_write_executed": False,
            "production_authorized": False,
            "trust_policy_retained": True,
            "trust_execution_blocker": False,
        },
    }


def _with_live_classification(result: ProbeResult) -> ProbeResult:
    classifications = LIVE_READ_CLASSIFICATIONS if result.refusal is None else LIVE_REFUSED_CLASSIFICATIONS
    return ProbeResult(
        refusal=result.refusal,
        observation_present=result.observation_present,
        observation_absent=result.observation_absent,
        get_item_calls=result.get_item_calls,
        caller_arn=result.caller_arn,
        account_id=result.account_id,
        role_session_name=result.role_session_name,
        live_aws_invoked=True,
        classifications=classifications,
    )


def lambda_handler(event, context):
    """Closed unless the source constant is changed in a later approval.

    The constant in this source file is false, so this function returns before
    importing Agent C or constructing a client. The live-test zip carries a
    copy of this file with the assignment set true. That copy checks the
    function name, region, and credential environment, uses the default SDK
    chain, and issues one GetItem through the existing Agent C read wrapper.
    """

    global _DIAG_STARTED
    del context
    _DIAG_STARTED = time.perf_counter()
    _diag("handler_entered")
    if PROBE_LIVE_CALLS_ENABLED is not True:
        _diag("handler_closed")
        return _closed_response("execution_role_probe_closed")
    _closed_constants()
    refusal = credential_source_refusal(os.environ)
    if refusal is not None:
        _diag("execution_context_refused")
        return _closed_response(refusal)
    _diag("execution_context_validated")
    if not event_is_allowlisted(event):
        _diag("unsupported_request")
        return lambda_response(
            ProbeResult(
                refusal="unsupported_request",
                observation_present=False,
                observation_absent=False,
                get_item_calls=0,
                caller_arn=None,
                account_id=None,
            )
        )
    sts, dynamodb = build_execution_role_clients(os.environ)
    result = run_offline_probe(event, sts=sts, dynamodb=dynamodb)
    _diag("handler_completed")
    return lambda_response(_with_live_classification(result))


@dataclass(frozen=True)
class TrustPolicyAssessment:
    """Static reading of the deployed trust policy. This does not call IAM."""

    principal: str
    source_account: str
    source_arn: str
    source_arn_has_wildcard: bool
    operator_assume_role_blocked: bool
    qualified_arn_matches: bool
    assume_role_compatibility: str
    activation_blocker: bool
    weakening_proposed: bool
    accepted_to_weaken: bool
    reason: str

    def __post_init__(self) -> None:
        _load_agent_c()
        if self.principal != "lambda.amazonaws.com":
            raise ContractValidationError("trust_principal_refused")
        if self.source_account != ACCOUNT_ID or self.source_arn != TRUST_SOURCE_ARN:
            raise ContractValidationError("trust_source_mismatch")
        if self.source_arn_has_wildcard is not False or self.qualified_arn_matches is not False:
            raise ContractValidationError("trust_wildcard_refused")
        if self.operator_assume_role_blocked is not True:
            raise ContractValidationError("trust_policy_must_block_operator_assume_role")
        if self.assume_role_compatibility != ASSUME_ROLE_COMPATIBILITY:
            raise ContractValidationError("trust_policy_must_stay")
        if self.activation_blocker is not False:
            raise ContractValidationError("trust_policy_must_stay")
        if self.weakening_proposed is not False or self.accepted_to_weaken is not False:
            raise ContractValidationError("trust_policy_weakening_refused")


def assess_ai_host_trust_tf(text: str) -> TrustPolicyAssessment:
    """Read the current trust policy in ``modules/ai_host/iam.tf``.

    The source-account and source-ARN conditions must still be present. A
    closed invoke already ran under this policy. This function does not edit
    Terraform and does not propose removing the conditions.
    """

    _load_agent_c()
    required = (
        'identifiers = ["lambda.amazonaws.com"]',
        'variable = "aws:SourceAccount"',
        'variable = "aws:SourceArn"',
        '"arn:aws:lambda:${var.aws_region}:${var.account_id}:function:${local.function_name}"',
    )
    if any(snippet not in text for snippet in required):
        raise ContractValidationError("trust_source_arn_missing")
    for line in text.splitlines():
        if "function:${local.function_name}" in line and ("*" in line or "?" in line):
            raise ContractValidationError("trust_wildcard_refused")
    qualified = f"{TRUST_SOURCE_ARN}:1"
    return TrustPolicyAssessment(
        principal="lambda.amazonaws.com",
        source_account=ACCOUNT_ID,
        source_arn=TRUST_SOURCE_ARN,
        source_arn_has_wildcard=False,
        operator_assume_role_blocked=True,
        qualified_arn_matches=_arn_like_matches(TRUST_SOURCE_ARN, qualified),
        assume_role_compatibility=ASSUME_ROLE_COMPATIBILITY,
        activation_blocker=False,
        weakening_proposed=False,
        accepted_to_weaken=False,
        reason=ASSUME_ROLE_COMPATIBILITY_REASON,
    )


def _arn_like_matches(pattern: str, arn: str) -> bool:
    """ArnLike without ``*`` or ``?`` is an exact match. A version suffix does not match."""

    _load_agent_c()
    if "*" in pattern or "?" in pattern:
        raise ContractValidationError("trust_wildcard_refused")
    return pattern == arn


def classify_trust_change(document: Mapping[str, object]) -> str:
    """Classify a trust document. Nothing here is approved or applied."""

    statements = document.get("Statement")
    if isinstance(statements, Mapping):
        statements = (statements,)
    elif isinstance(statements, (str, bytes)) or not isinstance(statements, Sequence):
        return "weakening_refused"
    if len(statements) != 1 or not isinstance(statements[0], Mapping):
        return "weakening_refused"
    statement = statements[0]
    principal = statement.get("Principal")
    if not isinstance(principal, Mapping) or set(principal) != {"Service"} or principal.get("Service") != "lambda.amazonaws.com":
        return "principal_refused"
    condition = statement.get("Condition")
    if "Condition" not in statement:
        return "weakening_refused"
    if not isinstance(condition, Mapping):
        return "weakening_refused"
    source_account = _condition_value(condition, "StringEquals", "aws:SourceAccount")
    source_arn = _condition_value(condition, "ArnLike", "aws:SourceArn")
    if source_account != ACCOUNT_ID or source_arn != TRUST_SOURCE_ARN:
        if isinstance(source_arn, str) and ("*" in source_arn or "?" in source_arn):
            return "wildcard_refused"
        return "weakening_refused"
    return "current_pattern_unchanged"


def apply_trust_change(*_args: object, **_kwargs: object) -> None:
    _load_agent_c()
    raise ContractValidationError("trust_policy_change_not_approved")


def _condition_value(condition: Mapping[str, object], operator: str, key: str) -> object:
    block = condition.get(operator)
    if not isinstance(block, Mapping):
        return None
    value = block.get(key)
    if isinstance(value, list):
        return value[0] if len(value) == 1 else None
    return value
