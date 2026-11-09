"""Sprint 5 controlled nonproduction ledger test plan. Not execution.

Atomic STARTED admission is a consume fence. It is not distributed
exactly-once native specialist execution. Execute raises until the operator
approves an explicit write window.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_ledger import (
    CONDITIONAL_PUT_ABSENT,
    DYNAMODB_LEDGER_TABLE_NAME,
)
from wilvor_ai.sprint4_security_authorization import (
    AI_HOST_ROLE_NAME,
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
)
from wilvor_ai.sprint4_security_aws_plan import (
    SPRINT4_AWS_PLAN_EXECUTION_ENABLED,
    execute_aws_test_plan,
)
from wilvor_ai.sprint4_security_ledger import (
    SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION,
    SPRINT4_LEDGER_WRITES_ENABLED,
    SPRINT4_LIVE_AWS_ENABLED,
)


SPRINT5_LEDGER_PLAN_SCHEMA = "wilvor.ai.sprint5.ledger_plan.v1"
SPRINT5_LEDGER_WRITES_ENABLED = False
SPRINT5_LEDGER_PLAN_EXECUTION_ENABLED = False
ATOMIC_ADMISSION_IS_NATIVE_EXECUTION = False
DISTRIBUTED_EXACTLY_ONCE = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_LEDGER_WRITES_ENABLED is not False or SPRINT4_LEDGER_WRITES_ENABLED is not False:
        _fail("sprint5_ledger_writes_disabled")
    if SPRINT5_LEDGER_PLAN_EXECUTION_ENABLED is not False or SPRINT4_AWS_PLAN_EXECUTION_ENABLED is not False:
        _fail("sprint5_ledger_plan_is_not_execution")
    if SPRINT4_LIVE_AWS_ENABLED is not False:
        _fail("sprint5_live_aws_not_executed")
    if ATOMIC_ADMISSION_IS_NATIVE_EXECUTION is not False:
        _fail("atomic_admission_is_not_native_execution")
    if DISTRIBUTED_EXACTLY_ONCE is not False:
        _fail("distributed_exactly_once_not_claimed")
    if SPRINT4_ATOMIC_ADMISSION_IS_NATIVE_EXECUTION is not False:
        _fail("atomic_admission_is_not_native_execution")


@dataclass(frozen=True)
class Sprint5LedgerCheck:
    check_id: str
    api: str
    purpose: str
    expected: str
    writes_dynamodb: bool
    preserves_audit_records: bool
    live_execution_permitted: bool = False

    def __post_init__(self) -> None:
        if self.live_execution_permitted is not False:
            _fail("sprint5_ledger_plan_is_not_execution")
        if self.preserves_audit_records is not True:
            _fail("correctness_markers_must_be_preserved")


@dataclass(frozen=True)
class Sprint5LedgerTestPlan:
    schema_version: str
    account_id: str
    region: str
    role_name: str
    ledger_table: str
    checks: tuple[Sprint5LedgerCheck, ...]
    atomic_admission_is_native_execution: bool
    distributed_exactly_once: bool
    writes_enabled: bool
    execution_enabled: bool

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT5_LEDGER_PLAN_SCHEMA:
            _fail("invalid_schema_version")
        if self.ledger_table != DYNAMODB_LEDGER_TABLE_NAME:
            _fail("unexpected_ledger_table")
        if self.atomic_admission_is_native_execution is not False:
            _fail("atomic_admission_is_not_native_execution")
        if self.distributed_exactly_once is not False:
            _fail("distributed_exactly_once_not_claimed")
        if self.writes_enabled is not False or self.execution_enabled is not False:
            _fail("sprint5_ledger_plan_is_not_execution")


def execute_sprint5_ledger_plan(*_args: object, **_kwargs: object) -> None:
    """Write tests require a new explicit approval. Task 2C is not authority."""

    _closed()
    execute_aws_test_plan()
    raise ContractValidationError("sprint5_ledger_plan_is_not_execution")


def sprint5_ledger_test_plan() -> Sprint5LedgerTestPlan:
    _closed()
    consume = (
        "TransactWriteItems of STARTED attempt item plus consume-v1# marker, "
        f"each ConditionExpression {CONDITIONAL_PUT_ABSENT}"
    )
    checks = (
        Sprint5LedgerCheck(
            check_id="L1",
            api="dynamodb:TransactWriteItems",
            purpose="One atomic STARTED admission with a persistent single-use consume marker.",
            expected=consume,
            writes_dynamodb=True,
            preserves_audit_records=True,
        ),
        Sprint5LedgerCheck(
            check_id="L2",
            api="dynamodb:TransactWriteItems",
            purpose="Replay the same signed nonce after consume.",
            expected="REPLAYED; marker still present; no second STARTED item.",
            writes_dynamodb=True,
            preserves_audit_records=True,
        ),
        Sprint5LedgerCheck(
            check_id="L3",
            api="dynamodb:TransactWriteItems",
            purpose="Invalid or stale fence rejection.",
            expected="REJECTED; fence_epoch mismatch; no second execution.",
            writes_dynamodb=True,
            preserves_audit_records=True,
        ),
        Sprint5LedgerCheck(
            check_id="L4",
            api="repeated request identity",
            purpose="No second execution from a repeated client request.",
            expected="Host-owned identity and grant boundaries reject the repeat.",
            writes_dynamodb=True,
            preserves_audit_records=True,
        ),
        Sprint5LedgerCheck(
            check_id="L5",
            api="transaction failure classification",
            purpose="Classify TransactionCanceledException without retrying as a new start.",
            expected="REPLAYED or REJECTED; specialist_executed=false.",
            writes_dynamodb=True,
            preserves_audit_records=True,
        ),
    )
    return Sprint5LedgerTestPlan(
        schema_version=SPRINT5_LEDGER_PLAN_SCHEMA,
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        role_name=AI_HOST_ROLE_NAME,
        ledger_table=DYNAMODB_LEDGER_TABLE_NAME,
        checks=checks,
        atomic_admission_is_native_execution=False,
        distributed_exactly_once=False,
        writes_enabled=False,
        execution_enabled=False,
    )


__all__ = [
    "ATOMIC_ADMISSION_IS_NATIVE_EXECUTION",
    "DISTRIBUTED_EXACTLY_ONCE",
    "SPRINT5_LEDGER_PLAN_EXECUTION_ENABLED",
    "SPRINT5_LEDGER_PLAN_SCHEMA",
    "SPRINT5_LEDGER_WRITES_ENABLED",
    "Sprint5LedgerCheck",
    "Sprint5LedgerTestPlan",
    "execute_sprint5_ledger_plan",
    "sprint5_ledger_test_plan",
]
