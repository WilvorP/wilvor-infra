"""Operator-SSO development reads for the local Copilot preview.

These handles are not the Lambda execution role and they are not a
production source grant. Scan is refused. Query requires an explicit Limit
at or below the Live Ops page bound. The caller ARN is reduced to an
account and role label before it is shown.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.sprint4_live_ops_authorization import LIVE_OPS_QUERY_PAGE_LIMIT
from wilvor_operational import readers


OPERATOR_AUTHENTICATED_DEV = "OPERATOR_AUTHENTICATED_DEV"
LAMBDA_EXECUTION_ROLE_MARKER = "assumed-role/wilvor-dev-ai-host-lambda-role/"
LOCAL_DEV_REGION = "us-west-1"
LOCAL_DEV_TABLE_NAMES = {
    "aircraft": "wilvor-dev-aircraft-current-state",
    "projections": "wilvor-dev-aircraft-projection",
    "projection_points": "wilvor-dev-aircraft-projection-points",
    "hazards": "wilvor-dev-active-hazards",
    "hazard_coordinates": "wilvor-dev-hazard-coordinates",
    "encounters": "wilvor-dev-aircraft-hazard-encounter",
    "risks": "wilvor-dev-risk-results",
    "airports": "wilvor-dev-airport-status",
    "metar": "wilvor-dev-metar-latest",
    "taf": "wilvor-dev-taf-latest",
    "taf_periods": "wilvor-dev-taf-forecast-periods",
    "airport_assessments": "wilvor-dev-airport-assessment",
    "recommendations": "wilvor-dev-recommendations",
    "alerts": "wilvor-dev-active-alerts",
}


class LocalDevReadError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class OperatorDevTable:
    """DynamoDB resource wrapper. GetItem and bounded Query only."""

    def __init__(self, name: str, inner: object, journal: list[dict[str, object]]) -> None:
        self.table_name = name
        self._inner = inner
        self._journal = journal

    def get_item(self, **kwargs: object) -> object:
        key = kwargs.get("Key")
        if not isinstance(key, Mapping) or not key:
            raise ContractValidationError("unbounded_get_refused")
        if any(name in kwargs for name in ("FilterExpression", "ProjectionExpression")):
            raise ContractValidationError("unbounded_get_refused")
        self._journal.append(
            {
                "operation": "GetItem",
                "table": self.table_name,
                "keyAttributes": tuple(sorted(str(item) for item in key)),
                "consistentRead": kwargs.get("ConsistentRead") is True,
                "bounded": True,
                "provenance": OPERATOR_AUTHENTICATED_DEV,
            }
        )
        return self._inner.get_item(**kwargs)

    def query(self, **kwargs: object) -> object:
        limit = kwargs.get("Limit")
        if type(limit) is not int or isinstance(limit, bool) or limit < 1 or limit > LIVE_OPS_QUERY_PAGE_LIMIT:
            raise ContractValidationError("unbounded_query_refused")
        self._journal.append(
            {
                "operation": "Query",
                "table": self.table_name,
                "indexName": kwargs.get("IndexName"),
                "limit": limit,
                "bounded": True,
                "provenance": OPERATOR_AUTHENTICATED_DEV,
            }
        )
        return self._inner.query(**kwargs)

    def scan(self, **kwargs: object) -> object:
        raise ContractValidationError("scan_not_permitted")


def require_operator_principal(identity: Mapping[str, Any]) -> dict[str, str]:
    """Label an STS identity as operator DEV evidence, or refuse it.

    The Lambda execution role is refused so a developer preview cannot be
    described as production-authenticated source evidence.
    """

    arn = identity.get("Arn") if isinstance(identity, Mapping) else None
    account = identity.get("Account") if isinstance(identity, Mapping) else None
    if not isinstance(arn, str) or not arn.startswith("arn:aws:sts::"):
        raise LocalDevReadError("operator_principal_required")
    if LAMBDA_EXECUTION_ROLE_MARKER in arn:
        raise LocalDevReadError("lambda_execution_role_is_not_operator_dev_evidence")
    role = _role_label(arn)
    if role is None:
        raise LocalDevReadError("operator_principal_required")
    if not isinstance(account, str) or not account.isdigit():
        raise LocalDevReadError("operator_principal_required")
    return {
        "evidenceProvenance": OPERATOR_AUTHENTICATED_DEV,
        "accountId": account,
        "principalLabel": role,
        "sourceAuthenticity": "NOT_ESTABLISHED",
    }


def _role_label(arn: str) -> str | None:
    marker = ":assumed-role/"
    if marker in arn:
        role = arn.split(marker, 1)[1].split("/", 1)[0]
    elif ":user/" in arn:
        role = arn.split(":user/", 1)[1]
    else:
        return None
    if not role or any(character.isspace() for character in role):
        return None
    return role


def build_operator_tables(
    tables: Mapping[str, object],
    journal: list[dict[str, object]] | None = None,
) -> tuple[readers.OperationalTables, list[dict[str, object]]]:
    """Wrap injected handles. Missing attributes fail closed."""

    recorded = journal if journal is not None else []
    wrapped: dict[str, OperatorDevTable] = {}
    for name in LOCAL_DEV_TABLE_NAMES:
        if name not in tables:
            raise LocalDevReadError("operator_table_missing")
        wrapped[name] = OperatorDevTable(LOCAL_DEV_TABLE_NAMES[name], tables[name], recorded)
    bundle = readers.OperationalTables(**wrapped)
    return bundle, recorded


def open_operator_dev_tables(
    *,
    session_factory: Any = None,
    identity_lookup: Any = None,
) -> tuple[readers.OperationalTables, list[dict[str, object]], dict[str, str]]:
    """Open wilvor-dev tables with the process AWS credential chain.

    ``session_factory`` and ``identity_lookup`` exist so tests can refuse a
    Lambda role without contacting AWS. The server leaves both unset.
    """

    if session_factory is None or identity_lookup is None:
        import boto3

        session = boto3.Session(region_name=LOCAL_DEV_REGION)
        identity = session.client("sts").get_caller_identity()
        resource = session.resource("dynamodb")
        handles = {
            name: resource.Table(table_name)
            for name, table_name in LOCAL_DEV_TABLE_NAMES.items()
        }
    else:
        identity = identity_lookup()
        session = session_factory()
        resource = session.resource("dynamodb")
        handles = {
            name: resource.Table(table_name)
            for name, table_name in LOCAL_DEV_TABLE_NAMES.items()
        }
    principal = require_operator_principal(identity)
    bundle, journal = build_operator_tables(handles)
    return bundle, journal, principal


__all__ = [
    "LAMBDA_EXECUTION_ROLE_MARKER",
    "LOCAL_DEV_REGION",
    "LOCAL_DEV_TABLE_NAMES",
    "OPERATOR_AUTHENTICATED_DEV",
    "LocalDevReadError",
    "OperatorDevTable",
    "build_operator_tables",
    "open_operator_dev_tables",
    "require_operator_principal",
]
