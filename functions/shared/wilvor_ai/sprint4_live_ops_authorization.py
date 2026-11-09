"""Sprint 4 Live Ops host authorization for the eight existing tools.

A consumed-attempt binding plus the published server read allowlist decide
whether a Live Ops tool may perform ``GET_ITEM`` or ``QUERY``. Scan, SQL,
writes, ARNs, wildcards, and caller-widened arguments stay refused. Live AWS
and production grants stay closed.

This module does not call DynamoDB, a model API, or Terraform.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from wilvor_ai.contracts import (
    AgentCapability,
    ContractValidationError,
    PROVENANCE_TOKEN_MAX_LENGTH,
    _validate_bounded_text,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS
from wilvor_ai.live_ops_public_contracts import LIVE_OPS_HAZARD_IDS_MAX
from wilvor_ai.master_contracts import MasterPurpose, MasterSpecialist
from wilvor_ai.master_read_resource_guard import (
    ReadOperation,
    server_read_argument_names,
    server_read_resources,
    server_read_tools,
)
from wilvor_ai.sprint3c_scoped_read import (
    GRANT_CONSUMPTION_LIFECYCLE,
    LEDGER_EXECUTION_STATE_STARTED,
    ConsumedAttemptReadBinding,
)


SPRINT4_LIVE_OPS_AUTHORIZATION_SCHEMA_VERSION = (
    "wilvor.ai.sprint4.live_ops_authorization.v1"
)
ADAPTER_ORIGIN = "sprint4.live_ops.offline_aws"

PRODUCTION_LIVE_OPS_AWS_READ_ENABLED = False
LIVE_AWS_OPERATION_PERMITTED = False
APPROVED_SOURCE_ADAPTER = False
EMPTY_PAGE_IS_VERIFIED_ZERO = False
REGION_LABEL_IS_SIGMET_POLYGON = False
CALLER_ARGUMENTS_WIDEN_AUTHORIZATION = False
UNBOUNDED_NETWORK_INVENTORY_PERMITTED = False
RELATED_UNALLOWLISTED_READ_PERMITTED = False
CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT = False

LIVE_OPS_QUERY_PAGE_LIMIT = LIVE_OPS_HAZARD_IDS_MAX
_LIVE_OPS_TOOL_NAMES = frozenset(item.name for item in LIVE_OPS_TOOLS)
_SQL_ARGUMENT_NAMES = frozenset({"sql", "statement", "query_string", "FilterExpression"})
_WRITE_METHODS = frozenset(
    {
        "put_item",
        "delete_item",
        "update_item",
        "batch_write_item",
        "transact_write_items",
        "execute_statement",
        "scan",
    }
)
_SELECTOR_TOOLS = frozenset(
    {
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
    }
)


class LiveOpsAuthorizationRefusal(str, Enum):
    LIVE_AWS_NOT_INITIATED = "LIVE_AWS_NOT_INITIATED"
    PRODUCTION_AUTHORIZATION_UNAVAILABLE = "PRODUCTION_AUTHORIZATION_UNAVAILABLE"
    CONSUMED_ATTEMPT_NOT_A_PRODUCTION_GRANT = "CONSUMED_ATTEMPT_NOT_A_PRODUCTION_GRANT"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    TOOL_NOT_ALLOWLISTED = "TOOL_NOT_ALLOWLISTED"
    RESOURCE_NOT_ALLOWLISTED = "RESOURCE_NOT_ALLOWLISTED"
    OPERATION_NOT_ALLOWLISTED = "OPERATION_NOT_ALLOWLISTED"
    SCAN_NOT_PERMITTED = "SCAN_NOT_PERMITTED"
    ARBITRARY_SQL = "ARBITRARY_SQL"
    WRITE_NOT_PERMITTED = "WRITE_NOT_PERMITTED"
    ARGUMENT_NOT_ALLOWLISTED = "ARGUMENT_NOT_ALLOWLISTED"
    SELECTOR_REQUIRED = "SELECTOR_REQUIRED"
    UNBOUNDED_POPULATION_REFUSED = "UNBOUNDED_POPULATION_REFUSED"
    QUERY_LIMIT_EXCEEDED = "QUERY_LIMIT_EXCEEDED"


def _closed() -> None:
    if (
        PRODUCTION_LIVE_OPS_AWS_READ_ENABLED is not False
        or LIVE_AWS_OPERATION_PERMITTED is not False
        or APPROVED_SOURCE_ADAPTER is not False
        or EMPTY_PAGE_IS_VERIFIED_ZERO is not False
        or REGION_LABEL_IS_SIGMET_POLYGON is not False
        or CALLER_ARGUMENTS_WIDEN_AUTHORIZATION is not False
        or UNBOUNDED_NETWORK_INVENTORY_PERMITTED is not False
        or RELATED_UNALLOWLISTED_READ_PERMITTED is not False
        or CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT is not False
    ):
        raise ContractValidationError("production_live_ops_aws_read_not_enabled")


def _token(value: object, field_name: str) -> str:
    errors = _validate_bounded_text(
        value, field_name, max_length=PROVENANCE_TOKEN_MAX_LENGTH
    )
    if errors:
        raise ContractValidationError(errors)
    if not isinstance(value, str) or value != value.strip() or any(
        character.isspace() for character in value
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _arn_or_wildcard(value: str) -> bool:
    folded = value.casefold()
    return (
        folded.startswith("arn:")
        or folded.startswith("dynamodb:")
        or ":table/" in folded
        or "*" in value
        or folded in {"all", "all_tables", "any", "unrestricted"}
    )


@dataclass(frozen=True)
class LiveOpsReadAuthorization:
    """Host-owned Live Ops read permission check. Not a production grant."""

    binding: ConsumedAttemptReadBinding
    tool_name: str
    allowed_resources: frozenset[tuple[str, ReadOperation]]
    allowed_arguments: frozenset[str]
    query_limit: int
    live_aws_permitted: bool = False
    approved_source_adapter: bool = False
    production_grant: bool = False

    def __post_init__(self) -> None:
        _closed()
        if type(self.binding) is not ConsumedAttemptReadBinding:
            raise ContractValidationError("invalid_consumed_attempt_binding")
        if self.live_aws_permitted is not False:
            raise ContractValidationError("live_aws_operation_not_initiated")
        if self.approved_source_adapter is not False:
            raise ContractValidationError("approved_source_adapter_unavailable")
        if self.production_grant is not False or self.binding.production_grant is not False:
            raise ContractValidationError("consumed_attempt_is_not_a_production_grant")
        _token(self.tool_name, "tool_name")
        if self.tool_name not in _LIVE_OPS_TOOL_NAMES:
            raise ContractValidationError("tool_not_allowlisted")
        if self.binding.specialist is not MasterSpecialist.LIVE_OPS:
            raise ContractValidationError("invalid_specialist")
        if self.binding.capability is not (
            AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT
        ):
            raise ContractValidationError("invalid_capability")
        if self.binding.purpose not in {MasterPurpose.DISCOVERY, MasterPurpose.ANSWER}:
            raise ContractValidationError("invalid_purpose")
        if type(self.query_limit) is not int or self.query_limit < 1:
            raise ContractValidationError("invalid_query_limit")
        if self.query_limit > LIVE_OPS_QUERY_PAGE_LIMIT:
            raise ContractValidationError("query_limit_exceeded")

    def __bool__(self) -> None:
        raise ContractValidationError("live_ops_authorization_is_not_a_production_grant")

    def allows(self, resource_id: str, operation: ReadOperation) -> bool:
        return (resource_id, operation) in self.allowed_resources


def issue_production_live_ops_aws_read(*_args: object, **_kwargs: object) -> None:
    _closed()
    raise ContractValidationError("production_live_ops_aws_read_not_enabled")


def open_production_live_ops_aws_read(*_args: object, **_kwargs: object) -> None:
    _closed()
    raise ContractValidationError("production_live_ops_aws_read_not_enabled")


def _selector_present(tool_name: str, arguments: dict[str, object]) -> bool:
    if tool_name in {"search_current_hazards", "search_current_impacts"}:
        region = arguments.get("region")
        hazard_ids = arguments.get("hazard_ids")
        return bool(region) or hazard_ids is not None
    if tool_name == "search_current_encounters":
        return any(
            arguments.get(name)
            for name in ("aircraft_id", "callsign", "hazard_id")
        ) or arguments.get("hazard_ids") is not None
    return True


def authorize_live_ops_tool(
    binding: ConsumedAttemptReadBinding,
    *,
    tool_name: str,
    arguments: dict[str, object] | None = None,
    requested_resource_id: str | None = None,
    requested_operation: ReadOperation | None = None,
    query_limit: int = LIVE_OPS_QUERY_PAGE_LIMIT,
) -> LiveOpsReadAuthorization:
    """Admit one Live Ops tool against the existing host allowlist.

    Admission is not a DynamoDB call and is not source authentication.
    """

    _closed()
    if type(binding) is not ConsumedAttemptReadBinding:
        raise ContractValidationError("invalid_consumed_attempt_binding")
    if binding.ledger_state != LEDGER_EXECUTION_STATE_STARTED:
        raise ContractValidationError("ledger_execution_state_must_stay_started")
    if binding.grant_consumption_lifecycle != GRANT_CONSUMPTION_LIFECYCLE:
        raise ContractValidationError("grant_consumption_not_recorded")
    if binding.specialist is not MasterSpecialist.LIVE_OPS:
        raise ContractValidationError("invalid_specialist")
    if tool_name not in server_read_tools(MasterSpecialist.LIVE_OPS):
        raise ContractValidationError("tool_not_allowlisted")
    if tool_name != binding.tool_name:
        raise ContractValidationError("identity_mismatch")
    if requested_operation is ReadOperation.SCAN:
        raise ContractValidationError("scan_not_permitted")
    allowed_resources = server_read_resources(MasterSpecialist.LIVE_OPS, tool_name)
    allowed_arguments = server_read_argument_names(MasterSpecialist.LIVE_OPS, tool_name)
    payload = dict(arguments or {})
    for name in payload:
        if name in _SQL_ARGUMENT_NAMES or name.casefold() in _WRITE_METHODS:
            raise ContractValidationError("arbitrary_sql" if name in _SQL_ARGUMENT_NAMES else "write_not_permitted")
        if name not in allowed_arguments:
            raise ContractValidationError("argument_not_allowlisted")
        value = payload[name]
        if isinstance(value, str) and _arn_or_wildcard(value):
            raise ContractValidationError("arbitrary_table_arn")
    if tool_name in _SELECTOR_TOOLS and not _selector_present(tool_name, payload):
        raise ContractValidationError("selector_required")
    if requested_resource_id is not None:
        if _arn_or_wildcard(requested_resource_id):
            raise ContractValidationError(
                "arbitrary_table_arn"
                if requested_resource_id.casefold().startswith("arn:")
                else "wildcard_resource"
            )
        operation = requested_operation or binding.operation
        if (requested_resource_id, operation) not in allowed_resources:
            raise ContractValidationError("resource_not_allowlisted")
        if requested_resource_id != binding.resource_id or operation is not binding.operation:
            raise ContractValidationError("identity_mismatch")
    if type(query_limit) is not int or isinstance(query_limit, bool) or query_limit < 1:
        raise ContractValidationError("invalid_query_limit")
    if query_limit > LIVE_OPS_QUERY_PAGE_LIMIT:
        raise ContractValidationError("query_limit_exceeded")
    return LiveOpsReadAuthorization(
        binding=binding,
        tool_name=tool_name,
        allowed_resources=allowed_resources,
        allowed_arguments=allowed_arguments,
        query_limit=query_limit,
    )


__all__ = [
    "ADAPTER_ORIGIN",
    "APPROVED_SOURCE_ADAPTER",
    "CALLER_ARGUMENTS_WIDEN_AUTHORIZATION",
    "CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT",
    "EMPTY_PAGE_IS_VERIFIED_ZERO",
    "LIVE_AWS_OPERATION_PERMITTED",
    "LIVE_OPS_QUERY_PAGE_LIMIT",
    "LiveOpsAuthorizationRefusal",
    "LiveOpsReadAuthorization",
    "PRODUCTION_LIVE_OPS_AWS_READ_ENABLED",
    "REGION_LABEL_IS_SIGMET_POLYGON",
    "RELATED_UNALLOWLISTED_READ_PERMITTED",
    "SPRINT4_LIVE_OPS_AUTHORIZATION_SCHEMA_VERSION",
    "UNBOUNDED_NETWORK_INVENTORY_PERMITTED",
    "authorize_live_ops_tool",
    "issue_production_live_ops_aws_read",
    "open_production_live_ops_aws_read",
]
