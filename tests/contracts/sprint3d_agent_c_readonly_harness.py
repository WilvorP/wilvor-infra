"""Guarded operator inspection for the existing Agent C adapter.

Operator credentials may read the control plane and one bounded GetItem.
They are not the AI execution role, they do not open production, and they
do not send a ledger transaction. The Sprint 3D live-network and write
gates stay closed. A skip is not a live pass.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Callable

from wilvor_ai.contracts import AgentCapability, ContractValidationError, TemporalScope
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_execution_grants import LogicalReadOperation, MasterExecutionGrantEnvelope
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    CONDITIONAL_REPLACE_EXACT,
    DYNAMODB_LEDGER_TABLE_NAME,
    TTL_DELETION_IS_CORRECTNESS,
    MasterExecutionLedgerState,
    build_started_record_for_signed_nonce,
    encode_ledger_record_item,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_grant_ledger import (
    SPRINT3C_DISTRIBUTED_EXACTLY_ONCE,
    consumption_marker_key,
    encode_consumption_marker,
    ledger_envelope_from_grant,
)
from wilvor_ai.sprint3d_aws_execution import (
    LIVE_NETWORK_GATE_ENV,
    LIVE_NETWORK_GATE_VALUE,
    SPRINT3D_DISTRIBUTED_EXACTLY_ONCE,
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
    SPRINT3D_PRODUCTION_EXECUTION_ENABLED,
    SPRINT3D_SOURCE_AUTHENTICITY_ESTABLISHED,
    AuthorizedLedgerDynamoClient,
    AuthorizedReadDynamoClient,
    AwsExecutionRefusal,
    StsCallerIdentity,
    _TTL_ATTRIBUTES,
    described_table_from_api,
    expected_iam_decisions,
    iam_role_arn,
    iam_simulation_from_api,
    review_aws_execution_authority,
    reviewed_aws_deployment,
    reviewed_read_table,
    sts_identity_from_api,
    table_arn,
)
from tests.contracts.sprint3d_validation_contract import (
    REAL_AWS_ACCOUNT_ENV,
    REAL_AWS_LEDGER_TABLE_ENV,
    REAL_AWS_REGION_ENV,
    REAL_AWS_WRITES_ENV,
    RealAwsGateState,
    evaluate_real_aws_gate,
)


ACCOUNT_ID = "396037508959"
REGION = "us-west-1"
PROFILE = "wilvor-dev"
ROLE_NAME = "wilvor-dev-ai-host-lambda-role"
FUNCTION_NAME = "wilvor-dev-ai-host"
API_NAME = "wilvor-dev-operational-api"
AIRCRAFT_TABLE_NAME = "wilvor-dev-aircraft-current-state"
METAR_TABLE_NAME = "wilvor-dev-metar-latest"
ENCOUNTER_TABLE_NAME = "wilvor-dev-aircraft-hazard-encounter"
CALLSIGN_INDEX_NAME = "callsign-position_time_epoch-index"
H3_INDEX_NAME = "current_h3_cell-position_time_epoch-index"
PROBE_AIRCRAFT_ID = "WILVOR-SPRINT3D-READONLY-PROBE"
LEDGER_NAMESPACE = "sprint3d-np-ledger"
OPERATOR_READONLY_ENV = "WILVOR_SPRINT3D_OPERATOR_READONLY"
OPERATOR_READONLY_VALUE = "reviewed-operator-inspection"
REAL_AWS_READ_ONLY_PASS = "REAL_AWS_READ_ONLY_PASS"
REAL_AWS_WRITE_NOT_EXECUTED = "REAL_AWS_WRITE_NOT_EXECUTED"
PRODUCTION_NOT_READY = "PRODUCTION_NOT_READY"

_PAGE_LIMIT = 10
_ALLOWED_METHODS = {
    "sts": frozenset({"get_caller_identity"}),
    "dynamodb": frozenset({"describe_table", "describe_time_to_live", "get_item"}),
    "iam": frozenset({"get_role", "simulate_principal_policy"}),
    "lambda": frozenset({"get_function_concurrency", "get_function_configuration", "get_policy"}),
    "apigatewayv2": frozenset({"get_api", "get_apis", "get_routes"}),
}
_FORBIDDEN_METHODS = frozenset(
    {
        "assume_role",
        "attach_role_policy",
        "batch_write_item",
        "create_table",
        "delete_item",
        "execute_statement",
        "invoke",
        "put_function_concurrency",
        "put_item",
        "put_role_policy",
        "query",
        "scan",
        "transact_write_items",
        "update_assume_role_policy",
        "update_function_configuration",
        "update_item",
        "update_table",
    }
)


EXECUTION_ROLE_VALIDATION_DESIGN = {
    "activated": False,
    "assume_role_from_operator": False,
    "trust_policy_change": False,
    "lambda_invoke": False,
    "reserved_concurrency_change": False,
    "identity_policy_change": False,
    "post_ai_query_route": False,
    "production_signing": False,
    "role_name": ROLE_NAME,
    "function_name": FUNCTION_NAME,
    "trust_principal": "lambda.amazonaws.com",
    "trust_source_account": ACCOUNT_ID,
    "trust_source_arn": f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{FUNCTION_NAME}",
    "why_operator_credentials_are_not_enough": (
        "The role trust policy admits only lambda.amazonaws.com when "
        "aws:SourceAccount is the reviewed account and aws:SourceArn is "
        "wilvor-dev-ai-host. An operator STS identity fails "
        "review_aws_execution_authority with ACCOUNT_MISMATCH before a "
        "data client is opened. SimulatePrincipalPolicy inspects the role; "
        "it does not create a role session."
    ),
    "minimum_isolated_test": (
        "Keep the trust policy unchanged. Raise reserved concurrency from 0 "
        "to 1 only for a separately approved window, then return it to 0. "
        "Add a resource-based lambda:InvokeFunction permission for one "
        "operator principal and remove it afterward. Do not add an API route. "
        "Ship an isolated handler entry, default off, that uses the function's "
        "own credentials and one reviewed nonce. The current handler returns "
        "503 and does not call Agent C, so invoking it would not exercise the "
        "adapter. CloudTrail must show the operator invoke and the DynamoDB "
        "call in the Lambda role session. This task does not apply that window."
    ),
}


@dataclass(frozen=True)
class OperatorReadOnlyEvidence:
    """Facts from operator credentials. Not AI-role execution and not a release."""

    caller: StsCallerIdentity
    caller_is_ai_execution_role: bool
    authority_refusal: AwsExecutionRefusal | None
    ledger_name: str
    ledger_hash_key: str
    ledger_range_key: str | None
    ledger_arn: str
    ledger_ttl_attribute: str
    aircraft_name: str
    aircraft_hash_key: str
    aircraft_range_key: str | None
    aircraft_arn: str
    aircraft_ttl_attribute: str
    aircraft_indexes: tuple[tuple[str, str, str | None], ...]
    iam_rows: tuple[tuple[str, str, str], ...]
    unauthorized_denies: tuple[tuple[str, str, str], ...]
    reserved_concurrency: int
    lambda_environment: tuple[tuple[str, str], ...]
    resource_policy_absent: bool
    ai_query_routes: tuple[str, ...]
    trust_principals: tuple[str, ...]
    trust_source_arn: str
    get_item_has_item: bool
    calls: tuple[str, ...]
    ai_execution_role_proven: bool = False
    production_ready: bool = False
    writes_executed: bool = False
    verified_zero: bool = False
    source_authenticity_established: bool = False
    classification: str = REAL_AWS_READ_ONLY_PASS

    def __post_init__(self) -> None:
        if (
            self.ai_execution_role_proven is not False
            or self.production_ready is not False
            or self.verified_zero is not False
            or self.source_authenticity_established is not False
        ):
            raise ContractValidationError("operator_inspection_is_not_execution_or_release")
        if self.writes_executed is not False:
            raise ContractValidationError("ledger_write_not_approved")
        if self.caller_is_ai_execution_role is not False:
            raise ContractValidationError("operator_credentials_are_not_the_ai_role")
        if self.classification != REAL_AWS_READ_ONLY_PASS:
            raise ContractValidationError("operator_inspection_classification")


@dataclass(frozen=True)
class NamespacedLedgerProposal:
    """One unsent ledger transaction. Construction does not call AWS."""

    grant_nonce: str
    attempt_token: str
    transaction: dict[str, object]
    fence_expression: str
    fence_epoch: int
    sent_to_aws: bool = False
    distributed_exactly_once: bool = False
    native_exactly_once: bool = False
    specialist_executed: bool = False
    classification: str = REAL_AWS_WRITE_NOT_EXECUTED

    def __post_init__(self) -> None:
        if (
            self.sent_to_aws is not False
            or self.distributed_exactly_once is not False
            or self.native_exactly_once is not False
            or self.specialist_executed is not False
        ):
            raise ContractValidationError("ledger_transaction_is_not_native_execution")
        if self.classification != REAL_AWS_WRITE_NOT_EXECUTED:
            raise ContractValidationError("ledger_write_not_approved")


def live_probe_disposition(environ: Mapping[str, str]) -> tuple[str, str]:
    """Decide whether operator inspection may call AWS.

    The existing contract gate still fails closed when it is opened without
    verified hooks, and writes still fail closed. Neither path calls AWS.
    A closed gate is a skip, not a live pass.
    """

    status = evaluate_real_aws_gate(environ)
    if status.state is RealAwsGateState.FAIL_CLOSED:
        return "fail", status.reason
    if SPRINT3D_PRODUCTION_EXECUTION_ENABLED is not False or SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is not False:
        return "fail", "Agent C production and live-network constants must stay closed."
    if SPRINT3D_SOURCE_AUTHENTICITY_ESTABLISHED is not False or SPRINT3D_DISTRIBUTED_EXACTLY_ONCE is not False:
        return "fail", "Evidence and exactly-once flags must stay closed."
    if environ.get(LIVE_NETWORK_GATE_ENV) == LIVE_NETWORK_GATE_VALUE:
        return (
            "fail",
            "The Agent C live-network gate stays closed. Operator inspection "
            "must not activate execution-role network construction.",
        )
    mismatch = _target_mismatch(environ)
    if mismatch is not None and (
        environ.get(OPERATOR_READONLY_ENV) == OPERATOR_READONLY_VALUE
        or status.state is RealAwsGateState.READY_TO_PROBE
    ):
        return "fail", mismatch
    if environ.get(OPERATOR_READONLY_ENV) == OPERATOR_READONLY_VALUE:
        return "run", "Operator inspection is explicit. It does not prove the AI execution role."
    if status.state is RealAwsGateState.READY_TO_PROBE:
        return "run", status.reason
    return "skip", status.reason


def reviewed_dev_deployment():
    """Server-owned dev mapping. Building it does not contact AWS."""

    aircraft = reviewed_read_table(
        logical_resource_id="aircraft",
        physical_table_name=AIRCRAFT_TABLE_NAME,
        source_name="aircraft-current-state",
        source_version_attribute="source_version",
        observation_time_attribute="source_observed_at_utc",
        hash_attribute="aircraft_id",
        query_partition_attribute="callsign",
        allowed_operations=frozenset({ReadOperation.GET_ITEM}),
    )
    return reviewed_aws_deployment(
        account_id=ACCOUNT_ID,
        region=REGION,
        role_arn=iam_role_arn(ACCOUNT_ID, ROLE_NAME),
        read_tables=(aircraft,),
    )


def bounded_aircraft_getitem_kwargs() -> dict[str, object]:
    """One consistent GetItem for a key that is not an aircraft identifier."""

    return {
        "TableName": AIRCRAFT_TABLE_NAME,
        "Key": {"aircraft_id": {"S": PROBE_AIRCRAFT_ID}},
        "ConsistentRead": True,
    }


def offline_shape_authorization():
    """Seal a mapping check with a synthetic role identity.

    The returned authorization is for an in-memory client only. A boto3
    client is refused. This is not the live operator and not a role session.
    """

    deployment = reviewed_dev_deployment()
    identity = StsCallerIdentity(
        account_id=ACCOUNT_ID,
        arn=iam_role_arn(ACCOUNT_ID, ROLE_NAME),
        user_id="AROAOFFLINEHARNESS:not-a-live-session",
    )
    review = review_aws_execution_authority(
        deployment,
        caller_identity=identity,
        described_tables=_offline_described_tables(),
        iam_simulation=expected_iam_decisions(deployment),
    )
    if review.authorization is None or review.data_plane_invoked is not False:
        raise ContractValidationError("aws_authority_missing")
    return review.authorization


def assert_offline_client(client: object) -> None:
    meta = getattr(client, "meta", None)
    service = getattr(getattr(meta, "service_model", None), "service_name", None)
    if service == "dynamodb":
        raise ContractValidationError("live_client_refused_for_offline_authorization")


def collect_operator_readonly_evidence() -> OperatorReadOnlyEvidence:
    """Read STS, schema, IAM simulation, Lambda, API routes, and one GetItem."""

    disposition, reason = live_probe_disposition(os.environ)
    if disposition != "run":
        raise ContractValidationError(reason)
    saved = _borrow_operator_profile()
    calls: list[str] = []
    try:
        import boto3

        session = boto3.Session(profile_name=PROFILE, region_name=REGION)
        clients = {
            service: _Allowlisted(session.client(service, region_name=REGION), service, calls)
            for service in _ALLOWED_METHODS
        }
        identity_raw = clients["sts"].get_caller_identity()
        caller = sts_identity_from_api(identity_raw)
        deployment = reviewed_dev_deployment()
        ledger_raw = clients["dynamodb"].describe_table(TableName=DYNAMODB_LEDGER_TABLE_NAME)
        aircraft_raw = clients["dynamodb"].describe_table(TableName=AIRCRAFT_TABLE_NAME)
        ledger = described_table_from_api(ledger_raw)
        aircraft = described_table_from_api(aircraft_raw)
        ledger_ttl = _ttl_attribute(clients["dynamodb"], DYNAMODB_LEDGER_TABLE_NAME)
        aircraft_ttl = _ttl_attribute(clients["dynamodb"], AIRCRAFT_TABLE_NAME)
        iam_rows = _simulate_expected(clients["iam"], deployment)
        unauthorized = _simulate_unauthorized(clients["iam"])
        review = review_aws_execution_authority(
            deployment,
            caller_identity=caller,
            described_tables=(ledger_raw, aircraft_raw),
            iam_simulation=iam_rows,
        )
        configuration = clients["lambda"].get_function_configuration(FunctionName=FUNCTION_NAME)
        concurrency_response = clients["lambda"].get_function_concurrency(FunctionName=FUNCTION_NAME)
        policy_absent = _resource_policy_absent(clients["lambda"])
        routes = _ai_query_routes(clients["apigatewayv2"])
        principals, source_arn = _trust_facts(clients["iam"])
        item = clients["dynamodb"].get_item(**bounded_aircraft_getitem_kwargs())
    finally:
        _restore_environment(saved)
    if not isinstance(item, Mapping):
        raise ContractValidationError("unexpected_getitem_response")
    environment = configuration.get("Environment", {})
    variables = environment.get("Variables", {}) if isinstance(environment, Mapping) else {}
    if not isinstance(variables, Mapping):
        raise ContractValidationError("unexpected_lambda_environment")
    rendered = tuple(
        (str(key), str(variables[key]))
        for key in (
            "AI_QUERY_ROUTE_ENABLED",
            "PRODUCTION_ISSUANCE_AVAILABLE",
            "LEDGER_WRITES_ENABLED",
            "LEDGER_TABLE_NAME",
        )
    )
    if not isinstance(concurrency_response, Mapping):
        raise ContractValidationError("reserved_concurrency_missing")
    concurrency = concurrency_response.get("ReservedConcurrentExecutions")
    if type(concurrency) is not int:
        raise ContractValidationError("reserved_concurrency_missing")
    for call in calls:
        service, method = call.split(".", 1)
        if method in _FORBIDDEN_METHODS or method not in _ALLOWED_METHODS[service]:
            raise ContractValidationError(f"read_only_harness_refuses:{call}")
    caller_role = _role_label(caller.arn)
    return OperatorReadOnlyEvidence(
        caller=caller,
        caller_is_ai_execution_role=caller_role == (ACCOUNT_ID, ROLE_NAME),
        authority_refusal=review.refusal,
        ledger_name=ledger.table_name,
        ledger_hash_key=ledger.hash_key,
        ledger_range_key=ledger.range_key,
        ledger_arn=ledger.table_arn,
        ledger_ttl_attribute=ledger_ttl,
        aircraft_name=aircraft.table_name,
        aircraft_hash_key=aircraft.hash_key,
        aircraft_range_key=aircraft.range_key,
        aircraft_arn=aircraft.table_arn,
        aircraft_ttl_attribute=aircraft_ttl,
        aircraft_indexes=aircraft.indexes,
        iam_rows=iam_rows,
        unauthorized_denies=unauthorized,
        reserved_concurrency=concurrency,
        lambda_environment=rendered,
        resource_policy_absent=policy_absent,
        ai_query_routes=tuple(routes),
        trust_principals=principals,
        trust_source_arn=source_arn,
        get_item_has_item="Item" in item,
        calls=tuple(calls),
    )


def build_namespaced_ledger_proposal() -> NamespacedLedgerProposal:
    """Build the one transaction a later approval could send. This does not send it."""

    if SPRINT3C_DISTRIBUTED_EXACTLY_ONCE is not False or SPRINT3D_DISTRIBUTED_EXACTLY_ONCE is not False:
        raise ContractValidationError("distributed_exactly_once_not_achieved")
    if TTL_DELETION_IS_CORRECTNESS is not False:
        raise ContractValidationError("ttl_deletion_is_not_correctness")
    nonce = f"{LEDGER_NAMESPACE}-nonce"
    attempt = f"{LEDGER_NAMESPACE}-attempt"
    envelope = MasterExecutionGrantEnvelope(
        master_request_id=f"{LEDGER_NAMESPACE}-request",
        planning_session_id=f"{LEDGER_NAMESPACE}-session",
        step_id=f"{LEDGER_NAMESPACE}-step",
        invocation_id=f"{LEDGER_NAMESPACE}-invocation",
        specialist=MasterSpecialist.LIVE_OPS,
        purpose=MasterPurpose.ANSWER,
        capability=AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        principal_ref=f"{LEDGER_NAMESPACE}-principal",
        policy_revision=f"{LEDGER_NAMESPACE}-policy",
        tool_name="get_aircraft_operational_context",
        resource_scope=(LogicalReadOperation("aircraft", ReadOperation.GET_ITEM),),
        region_scope=("CA",),
        temporal_scopes=(TemporalScope.CURRENT,),
        historical_window=None,
        decision_target_scope=None,
        accepted_at_utc="2026-11-01T16:00:00Z",
        deadline_utc="2026-11-01T17:00:00Z",
        expires_at_utc="2026-11-01T17:00:00Z",
        idempotency_key=f"{LEDGER_NAMESPACE}-idem",
        max_model_turns=1,
        max_tool_calls=1,
        correlation_id=f"{LEDGER_NAMESPACE}-correlation",
    )
    ledger_envelope = ledger_envelope_from_grant(envelope, principal_ref=envelope.principal_ref)
    started = build_started_record_for_signed_nonce(
        ledger_envelope,
        owner_id=f"{LEDGER_NAMESPACE}-owner",
        grant_nonce=nonce,
        attempt_token=attempt,
        now_utc="2026-11-01T16:10:00Z",
        lease_seconds=60,
        fence_epoch=1,
    )
    started_item = encode_ledger_record_item(started)
    marker = encode_consumption_marker(
        grant_nonce=nonce,
        envelope=ledger_envelope,
        consumed_at_utc="2026-11-01T16:10:00Z",
    )
    marker_key = consumption_marker_key(nonce, ledger_envelope)
    if started.state is not MasterExecutionLedgerState.STARTED:
        raise ContractValidationError("ledger_attempt_not_started")
    if marker_key[0] != marker["pk"]["S"]:
        raise ContractValidationError("consumption_marker_key_mismatch")
    if any(name in marker for name in _TTL_ATTRIBUTES):
        raise ContractValidationError("consumption_marker_must_not_have_ttl")
    transaction = {
        "TransactItems": [
            _absent_put(started_item),
            _absent_put(marker),
        ]
    }
    return NamespacedLedgerProposal(
        grant_nonce=nonce,
        attempt_token=attempt,
        transaction=transaction,
        fence_expression=CONDITIONAL_REPLACE_EXACT,
        fence_epoch=1,
    )


def execute_proposed_ledger_transaction(*_args: object, **_kwargs: object) -> None:
    """The live write path is not approved. No client is constructed."""

    raise ContractValidationError("ledger_write_not_approved")


def accept_bounded_getitem_offline(client: object) -> None:
    """Prove the probe key is the one shape the read wrapper forwards."""

    assert_offline_client(client)
    reader = AuthorizedReadDynamoClient(client, offline_shape_authorization())
    reader.get_item(**bounded_aircraft_getitem_kwargs())


def reject_unauthorized_mappings_offline(client: object) -> tuple[str, ...]:
    """Refuse other tables and operations before the inner client is called."""

    assert_offline_client(client)
    reader = AuthorizedReadDynamoClient(client, offline_shape_authorization())
    aircraft_key = {"aircraft_id": {"S": PROBE_AIRCRAFT_ID}}
    attempts = (
        ("scan", {}),
        ("put_item", {"TableName": AIRCRAFT_TABLE_NAME, "Item": {}}),
        ("query", {"TableName": AIRCRAFT_TABLE_NAME, "Limit": 1, "KeyConditionExpression": "callsign = :c"}),
        (
            "get_item",
            {
                "TableName": METAR_TABLE_NAME,
                "Key": {"station_id": {"S": PROBE_AIRCRAFT_ID}},
                "ConsistentRead": True,
            },
        ),
        (
            "get_item",
            {
                "TableName": ENCOUNTER_TABLE_NAME,
                "Key": {"encounter_id": {"S": PROBE_AIRCRAFT_ID}},
                "ConsistentRead": True,
            },
        ),
        (
            "get_item",
            {
                "TableName": DYNAMODB_LEDGER_TABLE_NAME,
                "Key": aircraft_key,
                "ConsistentRead": True,
            },
        ),
        (
            "get_item",
            {
                "TableName": AIRCRAFT_TABLE_NAME,
                "Key": aircraft_key,
                "ConsistentRead": False,
            },
        ),
    )
    reasons: list[str] = []
    for method, kwargs in attempts:
        try:
            getattr(reader, method)(**kwargs)
        except ContractValidationError as exc:
            reasons.append(str(exc))
        else:
            raise ContractValidationError("unauthorized_mapping_was_accepted")
    if len(reasons) != len(attempts):
        raise ContractValidationError("unauthorized_mapping_was_accepted")
    return tuple(reasons)


def accept_proposed_transaction_offline(client: object, proposal: NamespacedLedgerProposal) -> None:
    """Run the unsent transaction through the ledger wrapper and an in-memory client."""

    assert_offline_client(client)
    if proposal.sent_to_aws is not False:
        raise ContractValidationError("ledger_write_not_approved")
    ledger = AuthorizedLedgerDynamoClient(client, offline_shape_authorization())
    ledger.transact_write_items(**proposal.transaction)


def _absent_put(item: Mapping[str, object]) -> dict[str, object]:
    return {
        "Put": {
            "TableName": DYNAMODB_LEDGER_TABLE_NAME,
            "Item": dict(item),
            "ConditionExpression": CONDITIONAL_PUT_ABSENT,
            "ExpressionAttributeNames": {"#pk": "pk", "#sk": "sk"},
        }
    }


def _target_mismatch(environ: Mapping[str, str]) -> str | None:
    expected = {
        REAL_AWS_ACCOUNT_ENV: ACCOUNT_ID,
        REAL_AWS_REGION_ENV: REGION,
        REAL_AWS_LEDGER_TABLE_ENV: DYNAMODB_LEDGER_TABLE_NAME,
    }
    for name, value in expected.items():
        supplied = environ.get(name)
        if supplied not in (None, "") and supplied != value:
            return f"{name} does not match the reviewed nonproduction target."
    profile = environ.get("AWS_PROFILE")
    if profile not in (None, "", PROFILE):
        return "AWS_PROFILE is not wilvor-dev. Refusing to inspect a different profile."
    return None


def _borrow_operator_profile() -> dict[str, str | None]:
    """Drop the pytest dummy keys so the named profile is the credential source."""

    saved: dict[str, str | None] = {}
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_PROFILE",
        "AWS_DEFAULT_REGION",
        "AWS_REGION",
        "AWS_EC2_METADATA_DISABLED",
    ):
        saved[key] = os.environ.get(key)
        os.environ.pop(key, None)
    os.environ["AWS_PROFILE"] = PROFILE
    os.environ["AWS_DEFAULT_REGION"] = REGION
    os.environ["AWS_REGION"] = REGION
    os.environ["AWS_EC2_METADATA_DISABLED"] = "true"
    return saved


def _restore_environment(saved: Mapping[str, str | None]) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class _Allowlisted:
    """Forwards one read method and refuses every other client operation."""

    def __init__(self, inner: object, service: str, sink: list[str]) -> None:
        self._inner = inner
        self._service = service
        self._sink = sink

    def __getattr__(self, name: str) -> Callable[..., object]:
        if name.startswith("_") or name in _FORBIDDEN_METHODS or name not in _ALLOWED_METHODS[self._service]:
            raise ContractValidationError(f"read_only_harness_refuses:{self._service}.{name}")

        target = getattr(self._inner, name)

        def call(*args: object, **kwargs: object) -> object:
            if args:
                raise ContractValidationError("read_only_harness_refuses_positional")
            self._sink.append(f"{self._service}.{name}")
            return target(**kwargs)

        return call


def _pages(fetch: Callable[..., object], *, item_key: str = "Items") -> list[object]:
    items: list[object] = []
    token: str | None = None
    for _ in range(_PAGE_LIMIT):
        kwargs = {} if token is None else {"NextToken": token}
        page = fetch(**kwargs)
        if not isinstance(page, Mapping):
            raise ContractValidationError("unexpected_page")
        chunk = page.get(item_key, [])
        if not isinstance(chunk, list):
            raise ContractValidationError("unexpected_page")
        items.extend(chunk)
        next_token = page.get("NextToken")
        if not isinstance(next_token, str) or not next_token:
            return items
        token = next_token
    raise ContractValidationError("page_limit_exceeded")


def _ttl_attribute(client: object, table_name: str) -> str:
    described = client.describe_time_to_live(TableName=table_name)
    if not isinstance(described, Mapping):
        raise ContractValidationError("table_configuration_missing")
    body = described.get("TimeToLiveDescription")
    if not isinstance(body, Mapping):
        raise ContractValidationError("table_configuration_missing")
    if body.get("TimeToLiveStatus") != "ENABLED":
        raise ContractValidationError("table_configuration_mismatch")
    name = body.get("AttributeName")
    if not isinstance(name, str) or not name:
        raise ContractValidationError("table_configuration_missing")
    return name


_TABLE_SIMULATION_ACTIONS = frozenset(
    {
        "dynamodb:BatchWriteItem",
        "dynamodb:DeleteItem",
        "dynamodb:GetItem",
        "dynamodb:PutItem",
        "dynamodb:Query",
        "dynamodb:Scan",
        "dynamodb:UpdateItem",
    }
)
_TRANSACTION_SIMULATION_ACTIONS = frozenset(
    {
        "dynamodb:ExecuteStatement",
        "dynamodb:TransactWriteItems",
    }
)


def _action_groups(actions: list[str]) -> tuple[list[str], ...]:
    """IAM refuses one simulation that mixes table actions with transaction actions."""

    remaining = set(actions)
    groups: list[list[str]] = []
    for known in (_TABLE_SIMULATION_ACTIONS, _TRANSACTION_SIMULATION_ACTIONS):
        matched = sorted(remaining & known)
        if matched:
            groups.append(matched)
            remaining -= set(matched)
    groups.extend([action] for action in sorted(remaining))
    return tuple(groups)


def _simulate(client: object, *, actions: list[str], resources: list[str]) -> tuple[tuple[str, str, str], ...]:
    rows: list[tuple[str, str, str]] = []
    for group in _action_groups(actions):
        rows.extend(_simulate_group(client, actions=group, resources=resources))
    return tuple(sorted(rows))


def _simulate_group(client: object, *, actions: list[str], resources: list[str]) -> tuple[tuple[str, str, str], ...]:
    results: list[object] = []
    marker: str | None = None
    for _ in range(_PAGE_LIMIT):
        kwargs: dict[str, object] = {
            "PolicySourceArn": iam_role_arn(ACCOUNT_ID, ROLE_NAME),
            "ActionNames": actions,
            "ResourceArns": resources,
        }
        if marker is not None:
            kwargs["Marker"] = marker
        page = client.simulate_principal_policy(**kwargs)
        if not isinstance(page, Mapping):
            raise ContractValidationError("iam_authority_missing")
        chunk = page.get("EvaluationResults", [])
        if not isinstance(chunk, list):
            raise ContractValidationError("iam_authority_missing")
        results.extend(chunk)
        if page.get("IsTruncated") is not True:
            return iam_simulation_from_api({"EvaluationResults": results})
        next_marker = page.get("Marker")
        if not isinstance(next_marker, str) or not next_marker:
            raise ContractValidationError("iam_authority_missing")
        marker = next_marker
    raise ContractValidationError("page_limit_exceeded")


def _simulate_expected(client: object, deployment: object) -> tuple[tuple[str, str, str], ...]:
    expected = expected_iam_decisions(deployment)
    rows: list[tuple[str, str, str]] = []
    resources = sorted({resource for _action, resource, _decision in expected})
    for resource in resources:
        actions = sorted({action for action, candidate, _decision in expected if candidate == resource})
        rows.extend(_simulate(client, actions=actions, resources=[resource]))
    return tuple(sorted(rows))


def _simulate_unauthorized(client: object) -> tuple[tuple[str, str, str], ...]:
    aircraft = table_arn(ACCOUNT_ID, REGION, AIRCRAFT_TABLE_NAME)
    pairs = (
        ("dynamodb:GetItem", table_arn(ACCOUNT_ID, REGION, METAR_TABLE_NAME)),
        ("dynamodb:PutItem", table_arn(ACCOUNT_ID, REGION, METAR_TABLE_NAME)),
        ("dynamodb:Query", table_arn(ACCOUNT_ID, REGION, METAR_TABLE_NAME)),
        ("dynamodb:GetItem", table_arn(ACCOUNT_ID, REGION, ENCOUNTER_TABLE_NAME)),
        ("dynamodb:TransactWriteItems", table_arn(ACCOUNT_ID, REGION, ENCOUNTER_TABLE_NAME)),
        ("dynamodb:Query", f"{aircraft}/index/{CALLSIGN_INDEX_NAME}"),
        ("dynamodb:GetItem", f"{aircraft}/index/{CALLSIGN_INDEX_NAME}"),
        ("dynamodb:Scan", table_arn(ACCOUNT_ID, REGION, DYNAMODB_LEDGER_TABLE_NAME)),
    )
    rows: list[tuple[str, str, str]] = []
    for action, resource in pairs:
        rows.extend(_simulate(client, actions=[action], resources=[resource]))
    return tuple(sorted(rows))


def _resource_policy_absent(client: object) -> bool:
    try:
        client.get_policy(FunctionName=FUNCTION_NAME)
    except Exception as exc:
        response = getattr(exc, "response", None)
        code = ""
        if isinstance(response, Mapping):
            error = response.get("Error")
            if isinstance(error, Mapping) and isinstance(error.get("Code"), str):
                code = str(error["Code"])
        if code == "ResourceNotFoundException":
            return True
        raise
    return False


def _ai_query_routes(client: object) -> list[str]:
    found: list[str] = []
    named = 0
    for api in _pages(client.get_apis):
        if not isinstance(api, Mapping) or not isinstance(api.get("ApiId"), str):
            raise ContractValidationError("unexpected_api_page")
        if api.get("Name") == API_NAME:
            named += 1
        for route in _pages(lambda **kwargs: client.get_routes(ApiId=str(api["ApiId"]), **kwargs)):
            if not isinstance(route, Mapping) or not isinstance(route.get("RouteKey"), str):
                raise ContractValidationError("unexpected_route_page")
            key = str(route["RouteKey"])
            if key == "POST /ai/query" or "ai/query" in key:
                found.append(f"{api.get('Name', '')} {key}")
    if named != 1:
        raise ContractValidationError("operational_api_missing")
    return found


def _trust_facts(client: object) -> tuple[tuple[str, ...], str]:
    role = client.get_role(RoleName=ROLE_NAME)
    if not isinstance(role, Mapping):
        raise ContractValidationError("aws_identity_missing")
    body = role.get("Role")
    if not isinstance(body, Mapping):
        raise ContractValidationError("aws_identity_missing")
    document = body.get("AssumeRolePolicyDocument")
    if isinstance(document, str):
        import json

        document = json.loads(document)
    if not isinstance(document, Mapping):
        raise ContractValidationError("aws_identity_missing")
    statements = document.get("Statement")
    if isinstance(statements, Mapping):
        statements = [statements]
    if not isinstance(statements, list) or not statements:
        raise ContractValidationError("aws_identity_missing")
    principals: list[str] = []
    source_arn = ""
    for statement in statements:
        if not isinstance(statement, Mapping):
            raise ContractValidationError("aws_identity_missing")
        principal = statement.get("Principal")
        service = principal.get("Service") if isinstance(principal, Mapping) else None
        if isinstance(service, str):
            principals.append(service)
        elif isinstance(service, list):
            principals.extend(str(item) for item in service)
        else:
            raise ContractValidationError("aws_identity_missing")
        condition = statement.get("Condition")
        if not isinstance(condition, Mapping):
            raise ContractValidationError("aws_identity_missing")
        arn_like = condition.get("ArnLike")
        if not isinstance(arn_like, Mapping):
            raise ContractValidationError("aws_identity_missing")
        source = arn_like.get("aws:SourceArn")
        if isinstance(source, list):
            source = source[0] if source else ""
        if not isinstance(source, str):
            raise ContractValidationError("aws_identity_missing")
        source_arn = source
    return tuple(principals), source_arn


def _role_label(arn: str) -> tuple[str, str] | None:
    from wilvor_ai.sprint3d_aws_execution import _role_name

    return _role_name(arn)


def _offline_described_tables() -> tuple[dict[str, object], dict[str, object]]:
    return (
        _described(DYNAMODB_LEDGER_TABLE_NAME, "pk", "sk"),
        _described(AIRCRAFT_TABLE_NAME, "aircraft_id", None),
    )


def _described(name: str, hash_key: str, range_key: str | None) -> dict[str, object]:
    schema = [{"AttributeName": hash_key, "KeyType": "HASH"}]
    if range_key is not None:
        schema.append({"AttributeName": range_key, "KeyType": "RANGE"})
    return {
        "Table": {
            "TableName": name,
            "TableStatus": "ACTIVE",
            "TableArn": table_arn(ACCOUNT_ID, REGION, name),
            "KeySchema": schema,
            "GlobalSecondaryIndexes": [],
        }
    }


__all__ = (
    "EXECUTION_ROLE_VALIDATION_DESIGN",
    "LEDGER_NAMESPACE",
    "PROBE_AIRCRAFT_ID",
    "PRODUCTION_NOT_READY",
    "REAL_AWS_READ_ONLY_PASS",
    "REAL_AWS_WRITE_NOT_EXECUTED",
    "NamespacedLedgerProposal",
    "OperatorReadOnlyEvidence",
    "accept_bounded_getitem_offline",
    "assert_offline_client",
    "bounded_aircraft_getitem_kwargs",
    "build_namespaced_ledger_proposal",
    "collect_operator_readonly_evidence",
    "execute_proposed_ledger_transaction",
    "expected_iam_decisions",
    "accept_proposed_transaction_offline",
    "live_probe_disposition",
    "offline_shape_authorization",
    "reject_unauthorized_mappings_offline",
    "reviewed_dev_deployment",
)
