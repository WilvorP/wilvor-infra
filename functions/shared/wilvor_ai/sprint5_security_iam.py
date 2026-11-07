"""Sprint 5 least-privilege IAM delta. Proposed, not applied.

Inspected from modules/ai_host, modules/historical_analytics, and the
reviewed operational table schemas. This module does not mutate IAM,
apply Terraform, or attach policies.
"""

from __future__ import annotations

from dataclasses import dataclass

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.master_execution_ledger import DYNAMODB_LEDGER_TABLE_NAME
from wilvor_ai.sprint4_security_authorization import (
    AI_HOST_ROLE_NAME,
    AIRCRAFT_TABLE_NAME,
    REVIEWED_DEV_ACCOUNT_ID,
    REVIEWED_DEV_REGION,
    SPRINT4_IAM_CHANGE_APPLIED,
    apply_iam_delta,
)


SPRINT5_IAM_SCHEMA = "wilvor.ai.sprint5.security_iam.v1"
SPRINT5_IAM_CHANGE_APPLIED = False
SPRINT5_TERRAFORM_APPLY_PERMITTED = False
SPRINT5_BEDROCK_INVOKE_INCLUDED = False

_ACCOUNT = REVIEWED_DEV_ACCOUNT_ID
_REGION = REVIEWED_DEV_REGION
_PREFIX = "wilvor-dev"


def _table_arn(name: str) -> str:
    return f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{name}"


def _index_arn(table: str, index: str) -> str:
    return f"{_table_arn(table)}/index/{index}"


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if SPRINT5_IAM_CHANGE_APPLIED is not False or SPRINT4_IAM_CHANGE_APPLIED is not False:
        _fail("sprint5_iam_not_applied")
    if SPRINT5_TERRAFORM_APPLY_PERMITTED is not False:
        _fail("terraform_apply_not_permitted")
    if SPRINT5_BEDROCK_INVOKE_INCLUDED is not False:
        _fail("bedrock_separately_gated")


@dataclass(frozen=True)
class Sprint5IamStatement:
    sid: str
    effect: str
    actions: tuple[str, ...]
    resources: tuple[str, ...]
    notes: str


@dataclass(frozen=True)
class Sprint5IamDeltaProposal:
    """Exact remaining host-role statements. ``applied`` stays false."""

    schema_version: str
    status: str
    account_id: str
    region: str
    role_name: str
    ledger_table: str
    current_getitem_tables: tuple[str, ...]
    statements: tuple[Sprint5IamStatement, ...]
    attach_existing_policies: tuple[str, ...]
    must_not_grant: tuple[str, ...]
    terraform_files: tuple[str, ...]
    terraform_apply_permitted: bool
    iam_change_applied: bool
    bedrock_included: bool
    historical_dynamodb_query_substituted: bool
    scan_granted: bool
    wildcard_dynamodb: bool

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT5_IAM_SCHEMA:
            _fail("invalid_schema_version")
        if self.status != "PROPOSED_NOT_APPLIED":
            _fail("sprint5_iam_not_applied")
        if self.terraform_apply_permitted is not False or self.iam_change_applied is not False:
            _fail("sprint5_iam_not_applied")
        if self.bedrock_included is not False:
            _fail("bedrock_separately_gated")
        if self.historical_dynamodb_query_substituted is not False:
            _fail("historical_dynamodb_query_not_substituted")
        if self.scan_granted is not False or self.wildcard_dynamodb is not False:
            _fail("wildcard_authorization_rejected")
        if any("*" in resource for statement in self.statements for resource in statement.resources):
            _fail("wildcard_authorization_rejected")
        if any(
            statement.effect == "Allow" and "Scan" in action
            for statement in self.statements
            for action in statement.actions
        ):
            _fail("scan_not_permitted")


def apply_sprint5_iam_delta(*_args: object, **_kwargs: object) -> None:
    """IAM stays unchanged until an operator approves the exact plan."""

    _closed()
    apply_iam_delta()
    raise ContractValidationError("sprint5_iam_not_applied")


def reviewed_sprint5_iam_delta() -> Sprint5IamDeltaProposal:
    """Least-privilege remainder after the current ai_host policy."""

    _closed()
    aircraft = AIRCRAFT_TABLE_NAME
    hazards = f"{_PREFIX}-active-hazards"
    coordinates = f"{_PREFIX}-hazard-coordinates"
    encounters = f"{_PREFIX}-aircraft-hazard-encounter"
    airports = f"{_PREFIX}-airport-status"
    metar = f"{_PREFIX}-metar-latest"
    taf = f"{_PREFIX}-taf-latest"
    projections = f"{_PREFIX}-aircraft-projection"
    risks = f"{_PREFIX}-risk-results"
    recommendations = f"{_PREFIX}-recommendations"
    assessments = f"{_PREFIX}-airport-assessment"
    alerts = f"{_PREFIX}-active-alerts"
    statements = (
        Sprint5IamStatement(
            sid="ReadReviewedGetItemTablesSprint5",
            effect="Allow",
            actions=("dynamodb:GetItem",),
            resources=(
                _table_arn(aircraft),
                _table_arn(hazards),
                _table_arn(encounters),
                _table_arn(airports),
                _table_arn(metar),
                _table_arn(taf),
                _table_arn(projections),
                _table_arn(risks),
                _table_arn(recommendations),
                _table_arn(assessments),
                _table_arn(alerts),
            ),
            notes="Exact PK GetItem only. Ledger stays on its existing statement.",
        ),
        Sprint5IamStatement(
            sid="QueryReviewedLiveOpsIndexes",
            effect="Allow",
            actions=("dynamodb:Query",),
            resources=(
                _index_arn(hazards, "status-valid_to_epoch-index"),
                _table_arn(coordinates),
                _index_arn(encounters, "aircraft_id-detected_at_epoch-index"),
                _index_arn(encounters, "hazard_id-detected_at_epoch-index"),
            ),
            notes=(
                "Live Ops bounded Query. hazard-coordinates is queried by "
                "hash/range on the base table. Callsign GSI stays omitted "
                "until find_aircraft_by_callsign is separately authorized."
            ),
        ),
        Sprint5IamStatement(
            sid="QueryReviewedDecisionIndexes",
            effect="Allow",
            actions=("dynamodb:Query",),
            resources=(
                _index_arn(projections, "aircraft_id-generated_at_epoch-index"),
                _index_arn(encounters, "aircraft_id-detected_at_epoch-index"),
                _index_arn(risks, "aircraft_id-generated_at_epoch-index"),
                _index_arn(recommendations, "aircraft_id-created_at_epoch-index"),
                _index_arn(alerts, "aircraft_id-updated_at_epoch-index"),
                _table_arn(assessments),
            ),
            notes=(
                "Decision bounded Query. Airport assessment is the base table "
                "hash evaluation_id, not airport_id-created_at_epoch-index. "
                "Limit 25. These ARNs must also leave DenyUnreviewedDynamoDb."
            ),
        ),
        Sprint5IamStatement(
            sid="DenyScanAndUnreviewedDynamoDbSprint5",
            effect="Deny",
            actions=(
                "dynamodb:Scan",
                "dynamodb:DeleteItem",
                "dynamodb:UpdateItem",
                "dynamodb:BatchWriteItem",
                "dynamodb:PartiQLSelect",
                "dynamodb:PartiQLInsert",
                "dynamodb:PartiQLUpdate",
                "dynamodb:PartiQLDelete",
            ),
            resources=(
                _table_arn(aircraft),
                _table_arn(hazards),
                _table_arn(coordinates),
                _table_arn(encounters),
                _table_arn(airports),
                _table_arn(metar),
                _table_arn(taf),
                _table_arn(projections),
                _table_arn(risks),
                _table_arn(recommendations),
                _table_arn(assessments),
                _table_arn(alerts),
                _table_arn(DYNAMODB_LEDGER_TABLE_NAME),
            ),
            notes=(
                "Explicit deny of Scan and writes. DenyUnreviewedDynamoDb still "
                "uses dynamodb:* and overrides any Allow whose ARN is absent "
                "from its not_resources. Do not replace that deny with a wildcard allow."
            ),
        ),
    )
    return Sprint5IamDeltaProposal(
        schema_version=SPRINT5_IAM_SCHEMA,
        status="PROPOSED_NOT_APPLIED",
        account_id=_ACCOUNT,
        region=_REGION,
        role_name=AI_HOST_ROLE_NAME,
        ledger_table=DYNAMODB_LEDGER_TABLE_NAME,
        current_getitem_tables=(aircraft,),
        statements=statements,
        attach_existing_policies=(
            f"{_PREFIX}-historical-analytics-query",
        ),
        must_not_grant=(
            "dynamodb:Scan",
            "dynamodb:*",
            "bedrock:*",
            "bedrock:InvokeModel",
            "bedrock:Converse",
            "iam:SimulatePrincipalPolicy",
            "lambda:InvokeFunction",
            "lambda:AddPermission",
            "lambda:CreateFunctionUrlConfig",
            "execute-api:Invoke",
            "historical DynamoDB Query as Athena substitute",
        ),
        terraform_files=(
            "modules/ai_host/iam.tf",
            "modules/ai_host/locals.tf",
            "modules/ai_host/variables.tf",
            "envs/dev/main.tf",
            "modules/historical_analytics/iam.tf",
        ),
        terraform_apply_permitted=False,
        iam_change_applied=False,
        bedrock_included=False,
        historical_dynamodb_query_substituted=False,
        scan_granted=False,
        wildcard_dynamodb=False,
    )


__all__ = [
    "SPRINT5_BEDROCK_INVOKE_INCLUDED",
    "SPRINT5_IAM_CHANGE_APPLIED",
    "SPRINT5_IAM_SCHEMA",
    "SPRINT5_TERRAFORM_APPLY_PERMITTED",
    "Sprint5IamDeltaProposal",
    "Sprint5IamStatement",
    "apply_sprint5_iam_delta",
    "reviewed_sprint5_iam_delta",
]
