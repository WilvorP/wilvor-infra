"""Sprint 4 AWS ledger test plan. The plan is not execution.

These steps are the actual nonproduction checks an operator would run
after a reviewed IAM delta. This module does not call AWS, write DynamoDB,
invoke Lambda, or apply Terraform. ``execute_aws_test_plan`` always raises.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DYNAMODB_LEDGER_TABLE_NAME,
    MAX_LEASE_SECONDS,
)
from wilvor_ai.sprint3d_aws_execution import (
    LIVE_AWS_BLOCKERS,
    LIVE_NETWORK_GATE_ENV,
    LIVE_NETWORK_GATE_VALUE,
    SPRINT3D_LIVE_NETWORK_TESTS_ENABLED,
    live_network_gate_open,
    real_client_integration_contract,
)
from wilvor_ai.sprint4_security_authorization import (
    AIRCRAFT_TABLE_NAME,
    AI_HOST_ROLE_NAME,
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
)
from wilvor_ai.sprint4_security_ledger import (
    HOST_TIMEOUT_REASON,
    SPRINT4_LEDGER_WRITES_ENABLED,
    SPRINT4_LIVE_AWS_ENABLED,
    SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION,
    ledger_admission_contract,
)


SPRINT4_AWS_PLAN_SCHEMA = "wilvor.ai.sprint4.security_aws_plan.v1"
SPRINT4_AWS_PLAN_EXECUTION_ENABLED = False


def _assert_closed() -> None:
    if SPRINT4_AWS_PLAN_EXECUTION_ENABLED is not False:
        raise ContractValidationError("sprint4_aws_plan_is_not_execution")
    if SPRINT4_LEDGER_WRITES_ENABLED is not False:
        raise ContractValidationError("sprint4_ledger_writes_enabled")
    if SPRINT4_LIVE_AWS_ENABLED is not False:
        raise ContractValidationError("sprint4_live_aws_enabled")
    if SPRINT3D_LIVE_NETWORK_TESTS_ENABLED is not False:
        raise ContractValidationError("live_aws_network_not_reviewed")


def execute_aws_test_plan(*_args: object, **_kwargs: object) -> None:
    """The plan cannot be executed from this sprint. Offline tests use fakes."""

    _assert_closed()
    raise ContractValidationError("sprint4_aws_plan_is_not_execution")


@dataclass(frozen=True)
class AwsTestStep:
    """One operator step. ``live_execution_permitted`` stays false here."""

    step_id: str
    phase: str
    api: str
    purpose: str
    expected: str
    proves: str
    does_not_prove: str
    writes_dynamodb: bool
    live_execution_permitted: bool = False

    def __post_init__(self) -> None:
        if self.live_execution_permitted is not False:
            raise ContractValidationError("sprint4_aws_plan_is_not_execution")
        if self.phase not in {"control_plane", "data_plane_read", "data_plane_write", "negative", "restore"}:
            raise ContractValidationError("invalid_aws_plan_phase")
        if self.phase != "data_plane_write" and self.writes_dynamodb is not False:
            raise ContractValidationError("unexpected_write_step")
        if self.phase == "data_plane_write" and self.writes_dynamodb is not True:
            raise ContractValidationError("write_step_unmarked")

    def __bool__(self) -> bool:
        return False


@dataclass(frozen=True)
class Sprint4AwsTestPlan:
    """Ordered nonproduction checks. Holding this object does not call AWS."""

    schema_version: str
    account_id: str
    region: str
    role_name: str
    ledger_table: str
    aircraft_table: str
    live_gate_env: str
    live_gate_value: str
    live_gate_open_in_this_process: bool
    writes_enabled: bool
    execution_enabled: bool
    steps: tuple[AwsTestStep, ...]
    blockers: tuple[str, ...]
    admission_contract: dict[str, object]
    client_contract: dict[str, object]

    def __post_init__(self) -> None:
        _assert_closed()
        if self.schema_version != SPRINT4_AWS_PLAN_SCHEMA:
            raise ContractValidationError("invalid_schema_version")
        if self.ledger_table != DYNAMODB_LEDGER_TABLE_NAME:
            raise ContractValidationError("unexpected_ledger_table")
        if self.writes_enabled is not False or self.execution_enabled is not False:
            raise ContractValidationError("sprint4_aws_plan_is_not_execution")
        if not self.steps:
            raise ContractValidationError("aws_plan_empty")
        write_steps = tuple(step for step in self.steps if step.writes_dynamodb)
        if not write_steps:
            raise ContractValidationError("aws_plan_missing_write_steps")
        if any(step.live_execution_permitted for step in self.steps):
            raise ContractValidationError("sprint4_aws_plan_is_not_execution")

    def __bool__(self) -> bool:
        return False


def _steps() -> tuple[AwsTestStep, ...]:
    consume = (
        "TransactWriteItems of STARTED attempt item plus consume-v1# marker, "
        f"each ConditionExpression {CONDITIONAL_PUT_ABSENT}"
    )
    return (
        AwsTestStep(
            step_id="CP1",
            phase="control_plane",
            api="sts:GetCallerIdentity",
            purpose="Confirm the operator or assumed role is the reviewed development account.",
            expected=f"Account={REVIEWED_DEV_ACCOUNT_ID}; role name {AI_HOST_ROLE_NAME} or a documented operator principal.",
            proves="Caller account and role ARN at the time of the check.",
            does_not_prove="Source authenticity, grant issuance, or exactly-once specialist execution.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="CP2",
            phase="control_plane",
            api="dynamodb:DescribeTable",
            purpose="Confirm the ledger table keys, TTL attribute, and ACTIVE status.",
            expected="TableName=wilvor_master_execution_ledger; HASH pk; RANGE sk; TTL retention_expires_at_epoch.",
            proves="Table schema matches the documented ledger contract.",
            does_not_prove="That a consume transaction has ever succeeded, or that TTL deletion is first use.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="CP3",
            phase="control_plane",
            api="iam:SimulatePrincipalPolicy",
            purpose="Operator simulation of the AI host role against the least-privilege matrix.",
            expected="Allow GetItem/PutItem/TransactWriteItems on the ledger ARN; deny Scan/Query/Delete on ledger; allow GetItem on aircraft; deny Query on aircraft GSI.",
            proves="The simulated identity policy matches the reviewed matrix at simulation time.",
            does_not_prove="The deployed policy cannot change later, or that the runtime role may simulate itself.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="DP1",
            phase="data_plane_read",
            api="dynamodb:GetItem",
            purpose="Consistent read of a missing ledger item before consume.",
            expected="ConsistentRead=true; empty Item; no Scan.",
            proves="GetItem on the ledger ARN is reachable.",
            does_not_prove="Permission to write, or that absence is first use.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="DP2",
            phase="data_plane_read",
            api="dynamodb:GetItem",
            purpose="Bounded GetItem against the reviewed aircraft table using a probe aircraft id.",
            expected="TableName is the reviewed aircraft table; Key is aircraft_id only.",
            proves="The runtime role can read the reviewed GetItem mapping.",
            does_not_prove="Source authenticity, geographic intersection, or verified-zero from an empty item.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="W1",
            phase="data_plane_write",
            api="dynamodb:TransactWriteItems",
            purpose="Atomic STARTED admission of one signed nonce plus a no-TTL consume marker.",
            expected=consume,
            proves="One conditional transaction can fence a single attempt.",
            does_not_prove="Distributed exactly-once native specialist execution.",
            writes_dynamodb=True,
        ),
        AwsTestStep(
            step_id="W2",
            phase="data_plane_write",
            api="dynamodb:TransactWriteItems",
            purpose="Concurrent second writer of the same nonce and fingerprint.",
            expected="TransactionCanceledException; classified REPLAYED or REJECTED; no second STARTED item.",
            proves="Conditional conflict does not open another start for the same fingerprint.",
            does_not_prove="Exactly-once side effects outside the ledger item.",
            writes_dynamodb=True,
        ),
        AwsTestStep(
            step_id="W3",
            phase="data_plane_write",
            api="dynamodb:TransactWriteItems",
            purpose="Replay the same signed nonce after a successful consume.",
            expected="REPLAYED; marker still present; fence_epoch unchanged.",
            proves="Single-use consumption marker rejects a second start.",
            does_not_prove="HMAC by itself prevents replay across issuers.",
            writes_dynamodb=True,
        ),
        AwsTestStep(
            step_id="W4",
            phase="data_plane_write",
            api="dynamodb:PutItem",
            purpose="Fence-checked fail after host timeout under the current attempt token.",
            expected=f"state=FAILED; reason={HOST_TIMEOUT_REASON}; stale fence rejected.",
            proves="Timeout is recorded on the fenced attempt rather than starting again.",
            does_not_prove="The specialist ran, or that lease expiry rotates the grant nonce.",
            writes_dynamodb=True,
        ),
        AwsTestStep(
            step_id="N1",
            phase="negative",
            api="dynamodb:Scan",
            purpose="Scan the ledger and the aircraft table.",
            expected="AccessDeniedException or explicit deny.",
            proves="Scan is not in the runtime allow set.",
            does_not_prove="Every other table in the account is unreachable from a different role.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="N2",
            phase="negative",
            api="dynamodb:Query",
            purpose="Query the aircraft callsign GSI and the ledger.",
            expected="AccessDeniedException on both.",
            proves="Live Ops Query is not authorized on aircraft; ledger Query stays denied.",
            does_not_prove="A later Historical Query mapping, which still needs a reviewed IAM delta.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="N3",
            phase="negative",
            api="dynamodb:PutItem",
            purpose="Write a specialist table or delete a ledger item.",
            expected="AccessDeniedException. DeleteItem and specialist PutItem stay denied.",
            proves="Ledger consume APIs are not specialist write permissions.",
            does_not_prove="TTL deletion cannot still remove an attempt item asynchronously.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="N4",
            phase="negative",
            api="bedrock:InvokeModel",
            purpose="Any model invocation from the AI host role.",
            expected="AccessDeniedException. No real model API in this sprint.",
            proves="The runtime role cannot invoke Bedrock.",
            does_not_prove="A future reviewed model grant.",
            writes_dynamodb=False,
        ),
        AwsTestStep(
            step_id="R1",
            phase="restore",
            api="operator inspection",
            purpose="Confirm leftover STARTED items are not treated as native execution evidence.",
            expected="Audit records keep specialist_executed=false and distributed_exactly_once=false.",
            proves="Operators can distinguish ledger fencing from specialist success.",
            does_not_prove="Production readiness or route enablement.",
            writes_dynamodb=False,
        ),
    )


def sprint4_aws_test_plan() -> Sprint4AwsTestPlan:
    """Build the gated plan. Live gate state is observed, not opened."""

    _assert_closed()
    if live_network_gate_open():
        raise ContractValidationError("sprint4_aws_plan_refuses_open_live_gate")
    admission = ledger_admission_contract()
    if admission["max_lease_seconds"] != MAX_LEASE_SECONDS:
        raise ContractValidationError("lease_ceiling_changed")
    if admission["schema_version"] != SPRINT4_SECURITY_LEDGER_SCHEMA_VERSION:
        raise ContractValidationError("invalid_schema_version")
    return Sprint4AwsTestPlan(
        schema_version=SPRINT4_AWS_PLAN_SCHEMA,
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        role_name=AI_HOST_ROLE_NAME,
        ledger_table=DYNAMODB_LEDGER_TABLE_NAME,
        aircraft_table=AIRCRAFT_TABLE_NAME,
        live_gate_env=LIVE_NETWORK_GATE_ENV,
        live_gate_value=LIVE_NETWORK_GATE_VALUE,
        live_gate_open_in_this_process=False,
        writes_enabled=False,
        execution_enabled=False,
        steps=_steps(),
        blockers=LIVE_AWS_BLOCKERS,
        admission_contract=admission,
        client_contract=real_client_integration_contract(),
    )


def write_steps(plan: Sprint4AwsTestPlan) -> tuple[AwsTestStep, ...]:
    """The consume, concurrent writer, replay, and timeout writes. Not executed."""

    if type(plan) is not Sprint4AwsTestPlan:
        raise ContractValidationError("invalid_aws_plan")
    return tuple(step for step in plan.steps if step.writes_dynamodb)


_assert_closed()


__all__ = [
    "SPRINT4_AWS_PLAN_EXECUTION_ENABLED",
    "SPRINT4_AWS_PLAN_SCHEMA",
    "AwsTestStep",
    "Sprint4AwsTestPlan",
    "execute_aws_test_plan",
    "sprint4_aws_test_plan",
    "write_steps",
]
