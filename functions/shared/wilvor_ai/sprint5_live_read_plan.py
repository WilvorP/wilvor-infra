"""Sprint 5 specialist live-read validation plan. Not AWS execution.

Each read is bounded. Empty pages are not verified zeros. Incomplete
geographic collections are not statewide aircraft counts. Execute raises
until IAM and deployment are approved.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_security_authorization import (
    AI_HOST_ROLE_NAME,
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
)


SPRINT5_LIVE_READ_PLAN_SCHEMA = "wilvor.ai.sprint5.live_read_plan.v1"
SPRINT5_LIVE_READ_EXECUTION_ENABLED = False
EMPTY_PAGE_IS_VERIFIED_ZERO = False
CALIFORNIA_COVERAGE_FABRICATED = False
UNBOUNDED_READS_PERMITTED = False


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_LIVE_READ_EXECUTION_ENABLED is not False:
        _fail("sprint5_live_read_plan_is_not_execution")
    if EMPTY_PAGE_IS_VERIFIED_ZERO is not False:
        _fail("empty_page_is_not_verified_zero")
    if CALIFORNIA_COVERAGE_FABRICATED is not False:
        _fail("california_coverage_not_fabricated")
    if UNBOUNDED_READS_PERMITTED is not False:
        _fail("unbounded_reads_not_permitted")


@dataclass(frozen=True)
class Sprint5LiveReadCheck:
    check_id: str
    subject: str
    source_identity: str
    operation: str
    bounded: bool
    live_execution_permitted: bool = False
    invents_intersection: bool = False
    source_authenticity_established: bool = False

    def __post_init__(self) -> None:
        if self.live_execution_permitted is not False:
            _fail("sprint5_live_read_plan_is_not_execution")
        if self.bounded is not True:
            _fail("unbounded_reads_not_permitted")
        if self.invents_intersection is not False:
            _fail("geographic_intersection_not_invented")
        if self.source_authenticity_established is not False:
            _fail("source_authenticity_not_established")


@dataclass(frozen=True)
class Sprint5LiveReadPlan:
    schema_version: str
    account_id: str
    region: str
    role_name: str
    checks: tuple[Sprint5LiveReadCheck, ...]
    capture_fields: tuple[str, ...]
    execution_enabled: bool

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT5_LIVE_READ_PLAN_SCHEMA:
            _fail("invalid_schema_version")
        if self.execution_enabled is not False:
            _fail("sprint5_live_read_plan_is_not_execution")
        if len(self.checks) != 6:
            _fail("live_read_checks_incomplete")


CAPTURE_FIELDS = (
    "source_account_table_or_query_identity",
    "aws_request_or_query_ids",
    "observed_source_records",
    "source_observation_timestamps",
    "evaluation_timestamps",
    "coverage_and_pagination_status",
    "query_limitations",
    "execution_role_identity",
    "source_authenticity_established",
)


def execute_sprint5_live_reads(*_args: object, **_kwargs: object) -> None:
    _closed()
    raise ContractValidationError("sprint5_live_read_plan_is_not_execution")


def sprint5_live_read_plan() -> Sprint5LiveReadPlan:
    _closed()
    checks = (
        Sprint5LiveReadCheck(
            check_id="R1",
            subject="Aircraft operational context",
            source_identity="wilvor-dev-aircraft-current-state GetItem",
            operation="dynamodb:GetItem",
            bounded=True,
        ),
        Sprint5LiveReadCheck(
            check_id="R2",
            subject="SIGMET and hazard-coordinate evidence",
            source_identity="wilvor-dev-active-hazards Query + wilvor-dev-hazard-coordinates Query",
            operation="dynamodb:Query",
            bounded=True,
        ),
        Sprint5LiveReadCheck(
            check_id="R3",
            subject="Aircraft-hazard encounters",
            source_identity="wilvor-dev-aircraft-hazard-encounter Query aircraft_id-detected_at_epoch-index",
            operation="dynamodb:Query",
            bounded=True,
        ),
        Sprint5LiveReadCheck(
            check_id="R4",
            subject="METAR/TAF and airport evidence",
            source_identity="wilvor-dev-airport-status GetItem then metar-latest/taf-latest GetItem by station_id",
            operation="dynamodb:GetItem",
            bounded=True,
        ),
        Sprint5LiveReadCheck(
            check_id="R5",
            subject="Persisted risk and recommendation evidence",
            source_identity="wilvor-dev-risk-results and wilvor-dev-recommendations Query-by-aircraft",
            operation="dynamodb:Query",
            bounded=True,
        ),
        Sprint5LiveReadCheck(
            check_id="R6",
            subject="Historical Athena queries and coverage",
            source_identity="wilvor-dev-historical-analytics workgroup fixed registry queries",
            operation="athena:StartQueryExecution",
            bounded=True,
        ),
    )
    return Sprint5LiveReadPlan(
        schema_version=SPRINT5_LIVE_READ_PLAN_SCHEMA,
        account_id=REVIEWED_DEV_ACCOUNT_ID,
        region=REVIEWED_DEV_REGION,
        role_name=AI_HOST_ROLE_NAME,
        checks=checks,
        capture_fields=CAPTURE_FIELDS,
        execution_enabled=False,
    )


__all__ = [
    "CALIFORNIA_COVERAGE_FABRICATED",
    "CAPTURE_FIELDS",
    "EMPTY_PAGE_IS_VERIFIED_ZERO",
    "SPRINT5_LIVE_READ_EXECUTION_ENABLED",
    "SPRINT5_LIVE_READ_PLAN_SCHEMA",
    "UNBOUNDED_READS_PERMITTED",
    "Sprint5LiveReadCheck",
    "Sprint5LiveReadPlan",
    "execute_sprint5_live_reads",
    "sprint5_live_read_plan",
]
