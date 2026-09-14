"""Bound read-only Historical Analytics adapters.

Trusted runtime constructs ``HistoricalAnalyticsCall`` and
``HistoricalAnalyticsAdapter``. Model-visible arguments are only the
catalog ``input_fields``. This module does not implement an agent, call
AWS, render SQL, or decide coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ContractValidationError,
    ToolInputField,
    ToolInputValueType,
    ToolResult,
)
from wilvor_ai.tool_schema import ToolSchema, build_tool_schemas
from wilvor_ai.historical_analytics_mapping import (
    map_historical_invalid_request,
    map_historical_query_response,
)
from wilvor_historical.query_contracts import (
    LIST_DEFAULT_LIMIT,
    HistoricalQueryError,
    ListHistoricalEncountersRequest,
    SummarizeHistoricalEncountersRequest,
    SummarizeHistoricalHazardVersionsRequest,
    SummarizeHistoricalRisksRequest,
)
from wilvor_historical_query.operations import HistoricalAnalyticsOperations


@dataclass(frozen=True)
class HistoricalAnalyticsCall:
    """Trusted per-invocation historical analytics context.

    ``operations``, ``as_of_utc``, and ``tool_call_id`` are constructor-bound
    by runtime composition. They are not model-visible tool arguments.
    """

    operations: HistoricalAnalyticsOperations
    as_of_utc: str
    tool_call_id: str

    def __post_init__(self) -> None:
        if self.operations is None:
            raise TypeError("operations is required")
        if not isinstance(self.as_of_utc, str) or not self.as_of_utc.strip():
            raise ContractValidationError("invalid_as_of_utc")
        if not isinstance(self.tool_call_id, str) or not self.tool_call_id.strip():
            raise ContractValidationError("invalid_tool_call_id")


@dataclass(frozen=True)
class HistoricalAnalyticsToolSpec:
    """Catalog entry for a Historical Analytics tool.

    ``input_fields`` is the authoritative AI-visible field allowlist. It is
    not provider JSON Schema. Future schema generation must use this
    catalog or a bound adapter method, not unbound callables.
    """

    name: str
    description: str
    authority_mode: AgentAuthorityMode
    capabilities: tuple[AgentCapability, ...]
    input_fields: tuple[ToolInputField, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.input_fields, tuple) or any(
            not isinstance(item, ToolInputField) for item in self.input_fields
        ):
            raise TypeError("input_fields must be a tuple of ToolInputField")


_READ_ONLY = AgentAuthorityMode.READ_ONLY_ADVISORY
_RETRIEVE = (AgentCapability.RETRIEVE_HISTORICAL_ANALYTICS,)


def _field(
    name: str,
    required: bool,
    *,
    description: str,
    value_type: ToolInputValueType = ToolInputValueType.STRING,
) -> ToolInputField:
    return ToolInputField(
        name=name,
        required=required,
        value_type=value_type,
        description=description,
    )


_START_UTC = _field(
    "start_utc",
    True,
    description="Canonical UTC inclusive historical window start",
)
_END_UTC = _field(
    "end_utc",
    True,
    description="Canonical UTC exclusive historical window end",
)
_AIRCRAFT_ID = _field(
    "aircraft_id",
    False,
    description="Optional stored aircraft identity filter",
)
_HAZARD_ID = _field(
    "hazard_id",
    False,
    description="Optional stored hazard identity filter",
)
_HAZARD_TYPE = _field(
    "hazard_type",
    False,
    description="Optional stored hazard-type filter; not a closed enum",
)


HISTORICAL_ANALYTICS_TOOLS = (
    HistoricalAnalyticsToolSpec(
        name="summarize_historical_encounters",
        description=(
            "Summarize persisted historical encounter observations in an "
            "explicit UTC historical window. Completeness is evaluated by "
            "Wilvor collection coverage. This is not current aircraft state, "
            "geography, or airport impact."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _START_UTC,
            _END_UTC,
            _AIRCRAFT_ID,
            _HAZARD_ID,
            _HAZARD_TYPE,
        ),
    ),
    HistoricalAnalyticsToolSpec(
        name="summarize_historical_risks",
        description=(
            "Summarize persisted historical deterministic risk results, "
            "including the code-owned risk-level distribution, in an explicit "
            "UTC historical window. This is not current operational risk, "
            "prediction, or recommendation."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _START_UTC,
            _END_UTC,
            _AIRCRAFT_ID,
            _HAZARD_ID,
            _field(
                "encounter_id",
                False,
                description="Optional stored encounter identity filter",
            ),
            _field(
                "risk_level",
                False,
                description="Optional stored risk-level filter; not a closed enum",
            ),
        ),
    ),
    HistoricalAnalyticsToolSpec(
        name="summarize_historical_hazard_versions",
        description=(
            "Summarize hazard versions materialized during an explicit UTC "
            "historical window. The window is materialization/event time, "
            "not hazard validity-interval overlap."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _START_UTC,
            _END_UTC,
            _HAZARD_ID,
            _HAZARD_TYPE,
            _field(
                "product_type",
                False,
                description="Optional stored product-type filter; not a closed enum",
            ),
        ),
    ),
    HistoricalAnalyticsToolSpec(
        name="list_historical_encounters",
        description=(
            "List bounded persisted historical encounter records for a known "
            "aircraft_id and/or hazard_id in an explicit UTC historical "
            "window. This is not callsign search, live aircraft state, "
            "current containment, or geography."
        ),
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(
            _START_UTC,
            _END_UTC,
            _AIRCRAFT_ID,
            _HAZARD_ID,
            _field(
                "limit",
                False,
                value_type=ToolInputValueType.INTEGER,
                description=(
                    "Requested maximum records; specialist runtime applies "
                    "its own lower model-facing limit policy later"
                ),
            ),
        ),
    ),
)

_TOOL_NAMES = frozenset(item.name for item in HISTORICAL_ANALYTICS_TOOLS)


def historical_analytics_tool_schemas() -> tuple[ToolSchema, ...]:
    """Build provider-neutral schemas from the historical catalog only."""

    return build_tool_schemas(HISTORICAL_ANALYTICS_TOOLS)


class HistoricalAnalyticsAdapter:
    """Bound historical analytics tools. Trusted context is constructor-only."""

    def __init__(self, call: HistoricalAnalyticsCall) -> None:
        if not isinstance(call, HistoricalAnalyticsCall):
            raise TypeError("call must be a HistoricalAnalyticsCall")
        self._call = call

    def get_handler(self, tool_name: str) -> Callable[..., ToolResult]:
        if tool_name not in _TOOL_NAMES:
            raise ContractValidationError("unknown_historical_tool")
        return getattr(self, tool_name)

    def summarize_historical_encounters(
        self,
        *,
        start_utc: str,
        end_utc: str,
        aircraft_id: str | None = None,
        hazard_id: str | None = None,
        hazard_type: str | None = None,
    ) -> ToolResult:
        attempted = {
            "start_utc": start_utc,
            "end_utc": end_utc,
            "aircraft_id": aircraft_id,
            "hazard_id": hazard_id,
            "hazard_type": hazard_type,
        }
        try:
            request = SummarizeHistoricalEncountersRequest(
                start_utc=start_utc,
                end_utc=end_utc,
                aircraft_id=aircraft_id,
                hazard_id=hazard_id,
                hazard_type=hazard_type,
            )
        except HistoricalQueryError:
            return self._invalid("summarize_historical_encounters", attempted)
        response = self._call.operations.summarize_historical_encounters(
            request,
            as_of_utc=self._call.as_of_utc,
        )
        return map_historical_query_response(
            response,
            tool_call_id=self._call.tool_call_id,
        )

    def summarize_historical_risks(
        self,
        *,
        start_utc: str,
        end_utc: str,
        aircraft_id: str | None = None,
        hazard_id: str | None = None,
        encounter_id: str | None = None,
        risk_level: str | None = None,
    ) -> ToolResult:
        attempted = {
            "start_utc": start_utc,
            "end_utc": end_utc,
            "aircraft_id": aircraft_id,
            "hazard_id": hazard_id,
            "encounter_id": encounter_id,
            "risk_level": risk_level,
        }
        try:
            request = SummarizeHistoricalRisksRequest(
                start_utc=start_utc,
                end_utc=end_utc,
                aircraft_id=aircraft_id,
                hazard_id=hazard_id,
                encounter_id=encounter_id,
                risk_level=risk_level,
            )
        except HistoricalQueryError:
            return self._invalid("summarize_historical_risks", attempted)
        response = self._call.operations.summarize_historical_risks(
            request,
            as_of_utc=self._call.as_of_utc,
        )
        return map_historical_query_response(
            response,
            tool_call_id=self._call.tool_call_id,
        )

    def summarize_historical_hazard_versions(
        self,
        *,
        start_utc: str,
        end_utc: str,
        hazard_id: str | None = None,
        hazard_type: str | None = None,
        product_type: str | None = None,
    ) -> ToolResult:
        attempted = {
            "start_utc": start_utc,
            "end_utc": end_utc,
            "hazard_id": hazard_id,
            "hazard_type": hazard_type,
            "product_type": product_type,
        }
        try:
            request = SummarizeHistoricalHazardVersionsRequest(
                start_utc=start_utc,
                end_utc=end_utc,
                hazard_id=hazard_id,
                hazard_type=hazard_type,
                product_type=product_type,
            )
        except HistoricalQueryError:
            return self._invalid(
                "summarize_historical_hazard_versions",
                attempted,
            )
        response = self._call.operations.summarize_historical_hazard_versions(
            request,
            as_of_utc=self._call.as_of_utc,
        )
        return map_historical_query_response(
            response,
            tool_call_id=self._call.tool_call_id,
        )

    def list_historical_encounters(
        self,
        *,
        start_utc: str,
        end_utc: str,
        aircraft_id: str | None = None,
        hazard_id: str | None = None,
        limit: int = LIST_DEFAULT_LIMIT,
    ) -> ToolResult:
        attempted = {
            "start_utc": start_utc,
            "end_utc": end_utc,
            "aircraft_id": aircraft_id,
            "hazard_id": hazard_id,
            "limit": limit,
        }
        try:
            request = ListHistoricalEncountersRequest(
                start_utc=start_utc,
                end_utc=end_utc,
                aircraft_id=aircraft_id,
                hazard_id=hazard_id,
                limit=limit,
            )
        except HistoricalQueryError:
            return self._invalid("list_historical_encounters", attempted)
        response = self._call.operations.list_historical_encounters(
            request,
            as_of_utc=self._call.as_of_utc,
        )
        return map_historical_query_response(
            response,
            tool_call_id=self._call.tool_call_id,
        )

    def _invalid(
        self,
        tool_name: str,
        attempted_scope: dict[str, Any],
    ) -> ToolResult:
        return map_historical_invalid_request(
            tool_name=tool_name,
            tool_call_id=self._call.tool_call_id,
            attempted_scope=attempted_scope,
        )


__all__ = [
    "HISTORICAL_ANALYTICS_TOOLS",
    "HistoricalAnalyticsAdapter",
    "HistoricalAnalyticsCall",
    "HistoricalAnalyticsToolSpec",
    "historical_analytics_tool_schemas",
]
