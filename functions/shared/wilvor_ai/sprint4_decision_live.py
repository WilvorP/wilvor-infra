"""Sprint 4 Decision Intelligence live binding.

Integrates the existing four Decision Tools with persisted risk,
recommendation, airport-assessment, and cited METAR/TAF evidence. Table
handles are injected by the host. This module does not construct AWS
clients, write DynamoDB, invoke a model, or open production.

Missing risk is not LOW. Missing recommendation is not MONITOR. Persisted
weather is not a current operational observation. Responses stay advisory.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    _validate_utc,
)
from wilvor_ai.decision_contracts import OPERATIONAL_ID_MAX_LENGTH
from wilvor_ai.decision_tools import (
    DECISION_TOOLS,
    DecisionToolInvocation,
    DecisionToolsAdapter,
    DecisionToolsRuntime,
)
from wilvor_ai.master_contracts import MasterSpecialist
from wilvor_ai.master_evidence_admission import MasterSourceAuthenticity
from wilvor_ai.persisted_airport_contracts import (
    PersistedAirportEvidence,
    validate_persisted_airport_tool_result,
)
from wilvor_ai.sprint3c_scoped_read import (
    CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT,
    ConsumedAttemptReadBinding,
    LEDGER_EXECUTION_STATE_STARTED,
)
from wilvor_ai.sprint4_decision_reconciliation import (
    Sprint4AdvisoryConstraintReport,
    Sprint4EntityLinkReport,
    Sprint4FreshnessReport,
    Sprint4MissingRecordReport,
    reconcile_sprint4_decision_evidence,
)
from wilvor_ai.sprint4_decision_weather_context import (
    Sprint4AirportWeatherContext,
    load_persisted_airport_weather_context,
)
from wilvor_operational.context import _epoch_to_utc_z, normalize_aircraft_id


SPRINT4_DECISION_LIVE_SCHEMA_VERSION = "wilvor.ai.sprint4.decision_live.v1"
ADAPTER_ORIGIN = "sprint4.decision.injected_tables"

PRODUCTION_DECISION_LIVE_AWS_ENABLED = False
LIVE_AWS_OPERATION_PERMITTED = False
APPROVED_SOURCE_ADAPTER = False
DIRECT_AWS_AUTHORITY_PERMITTED = False
ABSENT_RISK_IS_LOW_RISK = False
ABSENT_RECOMMENDATION_IS_MONITOR = False
OPERATIONAL_DIRECTIVES_ENABLED = False
PERSISTED_WEATHER_IS_CURRENT_OBSERVATION = False
CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT_HERE = False

_CURRENT_TOOLS = (
    "get_current_decision_context",
    "get_current_risk_evidence",
    "get_current_recommendation",
)
_PERSISTED_TOOL = "get_persisted_airport_candidate_evidence"
_CATALOG = {item.name for item in DECISION_TOOLS}
_DIRECT_AWS_METHODS = (
    "put_item",
    "delete_item",
    "update_item",
    "batch_write_item",
    "transact_write_items",
    "create_table",
    "assume_role",
    "invoke",
    "scan",
)


class Sprint4DecisionHostAuthority(str, Enum):
    """How table handles arrived. Not a production grant."""

    INJECTED_HOST_TABLES = "INJECTED_HOST_TABLES"
    CONSUMED_ATTEMPT_BINDING = "CONSUMED_ATTEMPT_BINDING"


def _closed() -> None:
    if (
        PRODUCTION_DECISION_LIVE_AWS_ENABLED is not False
        or LIVE_AWS_OPERATION_PERMITTED is not False
        or APPROVED_SOURCE_ADAPTER is not False
        or DIRECT_AWS_AUTHORITY_PERMITTED is not False
        or ABSENT_RISK_IS_LOW_RISK is not False
        or ABSENT_RECOMMENDATION_IS_MONITOR is not False
        or OPERATIONAL_DIRECTIVES_ENABLED is not False
        or PERSISTED_WEATHER_IS_CURRENT_OBSERVATION is not False
        or CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT_HERE is not False
        or CONSUMED_ATTEMPT_IS_PRODUCTION_GRANT is not False
    ):
        raise ContractValidationError("production_decision_live_aws_not_enabled")


def open_production_decision_live_aws(*_args: object, **_kwargs: object) -> None:
    """Production Decision AWS reads stay closed."""

    _closed()
    raise ContractValidationError("production_decision_live_aws_not_enabled")


def issue_production_decision_live_aws(*_args: object, **_kwargs: object) -> None:
    """Production Decision issuance is not integrated."""

    _closed()
    raise ContractValidationError("production_grant_issuer_not_integrated")


def _token(value: object, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
    ):
        raise ContractValidationError(f"invalid_{field_name}")
    return value


def _optional_identity(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    token = _token(value, field_name)
    if field_name == "aircraft_id" and len(token) > OPERATIONAL_ID_MAX_LENGTH:
        raise ContractValidationError("invalid_aircraft_id")
    return token


def _refuse_direct_aws(tables: Any) -> None:
    if tables is None:
        raise ContractValidationError("invalid_tables")
    type_name = type(tables).__name__.casefold()
    module_name = type(tables).__module__.casefold()
    if "boto3" in module_name or "botocore" in module_name:
        raise ContractValidationError("unapproved_direct_aws_authority")
    if "dynamodb" in type_name and "table" in type_name:
        raise ContractValidationError("unapproved_direct_aws_authority")
    for method in _DIRECT_AWS_METHODS:
        if callable(getattr(tables, method, None)):
            raise ContractValidationError("unapproved_direct_aws_authority")
    client = getattr(tables, "meta", None)
    if client is not None and callable(getattr(tables, "get_item", None)):
        raise ContractValidationError("unapproved_direct_aws_authority")


def _refuse_unsafe_requests(
    *,
    request_missing_risk_as_low: bool,
    request_missing_recommendation_as_monitor: bool,
    request_operational_directive: bool,
    live_aws: bool,
) -> None:
    if request_missing_risk_as_low:
        raise ContractValidationError("absent_risk_is_not_low")
    if request_missing_recommendation_as_monitor:
        raise ContractValidationError("absent_recommendation_is_not_monitor")
    if request_operational_directive:
        raise ContractValidationError("operational_directives_not_permitted")
    if live_aws:
        raise ContractValidationError("live_aws_operation_not_permitted")


@dataclass(frozen=True)
class Sprint4DecisionLiveCall:
    """Host-owned Decision live read. Models do not supply table handles."""

    tables: Any
    now_epoch: int
    query_timestamp_utc: str
    aircraft_id: str | None = None
    recommendation_id: str | None = None
    tool_call_id_prefix: str = "sprint4-decision"
    correlation_id: str | None = None
    consumed_attempt: ConsumedAttemptReadBinding | None = None
    request_missing_risk_as_low: bool = False
    request_missing_recommendation_as_monitor: bool = False
    request_operational_directive: bool = False
    live_aws: bool = False
    context_builder: Any = None

    def __post_init__(self) -> None:
        _closed()
        _refuse_direct_aws(self.tables)
        _refuse_unsafe_requests(
            request_missing_risk_as_low=self.request_missing_risk_as_low,
            request_missing_recommendation_as_monitor=(
                self.request_missing_recommendation_as_monitor
            ),
            request_operational_directive=self.request_operational_directive,
            live_aws=self.live_aws,
        )
        if type(self.now_epoch) is not int:
            raise ContractValidationError("invalid_now_epoch")
        errors = _validate_utc(self.query_timestamp_utc, "query_timestamp_utc")
        if errors:
            raise ContractValidationError(errors)
        aircraft_id = _optional_identity(self.aircraft_id, "aircraft_id")
        recommendation_id = _optional_identity(
            self.recommendation_id,
            "recommendation_id",
        )
        if aircraft_id is None and recommendation_id is None:
            raise ContractValidationError("decision_target_required")
        _token(self.tool_call_id_prefix, "tool_call_id_prefix")
        if self.correlation_id is not None:
            _token(self.correlation_id, "correlation_id")
        if self.consumed_attempt is not None:
            _validate_consumed_attempt(
                self.consumed_attempt,
                aircraft_id=aircraft_id,
                recommendation_id=recommendation_id,
            )


def _validate_consumed_attempt(
    binding: object,
    *,
    aircraft_id: str | None,
    recommendation_id: str | None,
) -> None:
    if type(binding) is not ConsumedAttemptReadBinding:
        raise ContractValidationError("invalid_consumed_attempt_binding")
    if binding.production_grant is not False:
        raise ContractValidationError("consumed_attempt_is_not_a_production_grant")
    if binding.specialist is not MasterSpecialist.DECISION:
        raise ContractValidationError("specialist_mismatch")
    if binding.ledger_state != LEDGER_EXECUTION_STATE_STARTED:
        raise ContractValidationError("ledger_execution_state_must_stay_started")
    bound_aircraft = binding.decision_aircraft_id
    bound_recommendation = binding.decision_recommendation_id
    if (
        bound_aircraft is not None
        and aircraft_id is not None
        and normalize_aircraft_id(bound_aircraft) != normalize_aircraft_id(aircraft_id)
    ):
        raise ContractValidationError("decision_target_mismatch")
    if (
        bound_recommendation is not None
        and recommendation_id is not None
        and bound_recommendation != recommendation_id
    ):
        raise ContractValidationError("decision_target_mismatch")


@dataclass(frozen=True)
class Sprint4DecisionLiveResult:
    """Existing tool results plus weather, links, and closed safety reports."""

    invocations: dict[str, DecisionToolInvocation]
    weather: tuple[Sprint4AirportWeatherContext, ...]
    missing: Sprint4MissingRecordReport
    freshness: Sprint4FreshnessReport
    entity_links: Sprint4EntityLinkReport
    advisory: Sprint4AdvisoryConstraintReport
    host_authority: Sprint4DecisionHostAuthority
    evaluation_clock_utc: str
    query_timestamp_utc: str
    source_authenticity: MasterSourceAuthenticity = (
        MasterSourceAuthenticity.NOT_ESTABLISHED
    )
    production_live_aws: bool = False
    approved_source_adapter: bool = False
    live_aws_initiated: bool = False
    schema_version: str = SPRINT4_DECISION_LIVE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _closed()
        if self.schema_version != SPRINT4_DECISION_LIVE_SCHEMA_VERSION:
            raise ContractValidationError("invalid_schema_version")
        if self.production_live_aws is not False:
            raise ContractValidationError("production_decision_live_aws_not_enabled")
        if self.approved_source_adapter is not False:
            raise ContractValidationError("approved_source_adapter_absent")
        if self.live_aws_initiated is not False:
            raise ContractValidationError("live_aws_operation_not_permitted")
        if self.source_authenticity is not MasterSourceAuthenticity.NOT_ESTABLISHED:
            raise ContractValidationError("source_authenticity_not_established")
        if self.advisory.advisory_only is not True:
            raise ContractValidationError("decision_must_remain_advisory")
        if not isinstance(self.invocations, dict) or any(
            name not in _CATALOG or not isinstance(item, DecisionToolInvocation)
            for name, item in self.invocations.items()
        ):
            raise ContractValidationError("invalid_decision_invocations")
        persisted = self.invocations.get(_PERSISTED_TOOL)
        if persisted is not None:
            if persisted.raw_tool_result.temporal_scope is not TemporalScope.PERSISTED:
                raise ContractValidationError("persisted_airport_temporal_scope")
        for name in _CURRENT_TOOLS:
            current = self.invocations.get(name)
            if current is None:
                continue
            if current.raw_tool_result.temporal_scope is not TemporalScope.CURRENT:
                raise ContractValidationError("decision_temporal_scope_must_be_current")


def execute_sprint4_decision_live(
    call: Sprint4DecisionLiveCall,
) -> Sprint4DecisionLiveResult:
    """Run the existing Decision Tools, then attach persisted weather context."""

    if not isinstance(call, Sprint4DecisionLiveCall):
        raise TypeError("call must be Sprint4DecisionLiveCall")
    _closed()
    runtime = DecisionToolsRuntime(
        tables=call.tables,
        now_epoch=call.now_epoch,
        query_timestamp_utc=call.query_timestamp_utc,
        correlation_id=call.correlation_id,
        context_builder=call.context_builder,
    )
    adapter = DecisionToolsAdapter(runtime)
    invocations: dict[str, DecisionToolInvocation] = {}
    sequence = 0
    if call.aircraft_id is not None:
        for tool_name in _CURRENT_TOOLS:
            sequence += 1
            invocations[tool_name] = adapter.invoke(
                tool_name,
                {"aircraft_id": call.aircraft_id},
                tool_call_id=f"{call.tool_call_id_prefix}-{sequence}",
            )
    if call.recommendation_id is not None:
        sequence += 1
        invocations[_PERSISTED_TOOL] = adapter.invoke(
            _PERSISTED_TOOL,
            {"recommendation_id": call.recommendation_id},
            tool_call_id=f"{call.tool_call_id_prefix}-{sequence}",
        )
    weather = _weather(call, invocations)
    evaluation_clock_utc = _epoch_to_utc_z(call.now_epoch)
    missing, freshness, links, advisory = reconcile_sprint4_decision_evidence(
        aircraft_id=call.aircraft_id,
        recommendation_id=call.recommendation_id,
        invocations=invocations,
        weather=weather,
        query_timestamp_utc=call.query_timestamp_utc,
        evaluation_clock_utc=evaluation_clock_utc,
    )
    authority = (
        Sprint4DecisionHostAuthority.CONSUMED_ATTEMPT_BINDING
        if call.consumed_attempt is not None
        else Sprint4DecisionHostAuthority.INJECTED_HOST_TABLES
    )
    return Sprint4DecisionLiveResult(
        invocations=invocations,
        weather=weather,
        missing=missing,
        freshness=freshness,
        entity_links=links,
        advisory=advisory,
        host_authority=authority,
        evaluation_clock_utc=evaluation_clock_utc,
        query_timestamp_utc=call.query_timestamp_utc,
    )


def _weather(
    call: Sprint4DecisionLiveCall,
    invocations: Mapping[str, DecisionToolInvocation],
) -> tuple[Sprint4AirportWeatherContext, ...]:
    invocation = invocations.get(_PERSISTED_TOOL)
    if invocation is None:
        return ()
    result = invocation.raw_tool_result
    try:
        persisted: PersistedAirportEvidence = validate_persisted_airport_tool_result(
            result,
        )
    except ContractValidationError:
        return ()
    if not persisted.candidates:
        return ()
    return load_persisted_airport_weather_context(
        call.tables,
        persisted.candidates,
        query_timestamp_utc=call.query_timestamp_utc,
        tool_call_id=result.tool_call_id,
    )
