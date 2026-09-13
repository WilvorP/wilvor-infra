"""Read-only Live Operations adapters for the Wilvor AI Operations Copilot.

These tools wrap committed Phase 1 public domain functions and return Phase 0
ToolResult envelopes. They do not implement an agent, call AWS, or decide
operational currentness, geography, or source-version authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ToolInputField,
    ToolResult,
)
from wilvor_ai import live_ops_mapping as mapping
from wilvor_operational import context
from wilvor_operational import discovery
from wilvor_operational import query


@dataclass(frozen=True)
class LiveOpsCall:
    """Per-invocation Live Operations call context.

    `tables`, `now_epoch`, and `correlation_id` may be reused across tools for
    one user request. Each invocation must receive a new unique `tool_call_id`.
    """

    tables: Any
    now_epoch: int
    tool_call_id: str
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        mapping.validate_call(self)


@dataclass(frozen=True)
class LiveOpsToolSpec:
    """Catalog entry for a Live Operations tool.

    `input_fields` is the authoritative AI-visible field allowlist. It is
    not provider JSON Schema. Future schema generation must use this
    catalog, not `inspect.signature` of the unbound functions, because
    those callables still accept trusted `LiveOpsCall` runtime context.
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
_RETRIEVE = (
    AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
)


def _optional_fields(*names: str) -> tuple[ToolInputField, ...]:
    return tuple(ToolInputField(name=name, required=False) for name in names)


def _required_field(name: str) -> tuple[ToolInputField, ...]:
    return (ToolInputField(name=name, required=True),)


def search_current_hazards(
    call: LiveOpsCall,
    *,
    region: str | None = None,
    product_type: str | None = None,
    hazard_type: str | None = None,
    hazard_ids: list[str] | tuple[str, ...] | None = None,
) -> ToolResult:
    mapping.validate_call(call)
    if region:
        from wilvor_operational import geospatial

        result = geospatial.discover_current_hazards_in_region(
            call.tables,
            region,
            now_epoch=call.now_epoch,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
        )
        return mapping.map_search_current_hazards_region(
            call,
            result,
            region=region,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
        )
    result = discovery.discover_current_hazards(
        call.tables,
        now_epoch=call.now_epoch,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )
    return mapping.map_search_current_hazards_discovery(
        call,
        result,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )


def search_current_impacts(
    call: LiveOpsCall,
    *,
    region: str | None = None,
    product_type: str | None = None,
    hazard_type: str | None = None,
    hazard_ids: list[str] | tuple[str, ...] | None = None,
) -> ToolResult:
    mapping.validate_call(call)
    result = query.search_current_impacts(
        call.tables,
        now_epoch=call.now_epoch,
        region=region,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )
    return mapping.map_search_current_impacts(
        call,
        result,
        region=region,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )


def search_current_encounters(
    call: LiveOpsCall,
    *,
    aircraft_id: str | None = None,
    callsign: str | None = None,
    hazard_id: str | None = None,
    hazard_ids: list[str] | tuple[str, ...] | None = None,
) -> ToolResult:
    mapping.validate_call(call)
    result = query.search_current_encounters(
        call.tables,
        now_epoch=call.now_epoch,
        aircraft_id=aircraft_id,
        callsign=callsign,
        hazard_id=hazard_id,
        hazard_ids=hazard_ids,
    )
    return mapping.map_search_current_encounters(
        call,
        result,
        aircraft_id=aircraft_id,
        callsign=callsign,
        hazard_id=hazard_id,
        hazard_ids=hazard_ids,
    )


def get_observed_network_state(call: LiveOpsCall) -> ToolResult:
    mapping.validate_call(call)
    result = query.get_observed_network_state(
        call.tables,
        now_epoch=call.now_epoch,
    )
    return mapping.map_observed_network_state(call, result)


def get_aircraft_operational_context(
    call: LiveOpsCall,
    aircraft_id: str,
) -> ToolResult:
    mapping.validate_call(call)
    result = context.build_aircraft_operational_context(
        call.tables,
        aircraft_id,
        now_epoch=call.now_epoch,
    )
    return mapping.map_aircraft_operational_context(
        call,
        result,
        aircraft_id=aircraft_id,
    )


def get_hazard_operational_context(
    call: LiveOpsCall,
    hazard_id: str,
) -> ToolResult:
    mapping.validate_call(call)
    result = context.build_hazard_operational_context(
        call.tables,
        hazard_id,
        now_epoch=call.now_epoch,
    )
    return mapping.map_hazard_operational_context(
        call,
        result,
        hazard_id=hazard_id,
    )


def get_airport_operational_context(
    call: LiveOpsCall,
    airport_id: str,
) -> ToolResult:
    mapping.validate_call(call)
    result = context.build_airport_operational_context(
        call.tables,
        airport_id,
        now_epoch=call.now_epoch,
    )
    return mapping.map_airport_operational_context(
        call,
        result,
        airport_id=airport_id,
    )


def find_aircraft_by_callsign(
    call: LiveOpsCall,
    callsign: str,
) -> ToolResult:
    mapping.validate_call(call)
    if not str(callsign or "").strip():
        return mapping.map_find_aircraft_by_callsign(
            call,
            None,
            callsign=callsign,
        )
    result = discovery.discover_aircraft_by_callsign(
        call.tables,
        callsign,
        now_epoch=call.now_epoch,
    )
    return mapping.map_find_aircraft_by_callsign(
        call,
        result,
        callsign=callsign,
    )


_HAZARD_FILTER_FIELDS = _optional_fields(
    "region",
    "product_type",
    "hazard_type",
    "hazard_ids",
)

LIVE_OPS_TOOLS = (
    LiveOpsToolSpec(
        name="search_current_hazards",
        description="Retrieve currently matching hazards, optionally by region or type.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_HAZARD_FILTER_FIELDS,
    ),
    LiveOpsToolSpec(
        name="search_current_impacts",
        description="Retrieve currently matching hazard-aircraft impacts.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_HAZARD_FILTER_FIELDS,
    ),
    LiveOpsToolSpec(
        name="search_current_encounters",
        description="Retrieve currently matching aircraft-hazard encounters.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_optional_fields(
            "aircraft_id",
            "callsign",
            "hazard_id",
            "hazard_ids",
        ),
    ),
    LiveOpsToolSpec(
        name="get_observed_network_state",
        description="Retrieve the observed current operational ID inventory.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=(),
    ),
    LiveOpsToolSpec(
        name="get_aircraft_operational_context",
        description="Retrieve operational context for a known aircraft ID.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_required_field("aircraft_id"),
    ),
    LiveOpsToolSpec(
        name="get_hazard_operational_context",
        description="Retrieve operational context for a known hazard ID.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_required_field("hazard_id"),
    ),
    LiveOpsToolSpec(
        name="get_airport_operational_context",
        description="Retrieve operational context for a known airport ID.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_required_field("airport_id"),
    ),
    LiveOpsToolSpec(
        name="find_aircraft_by_callsign",
        description="Retrieve current aircraft identities matching a callsign.",
        authority_mode=_READ_ONLY,
        capabilities=_RETRIEVE,
        input_fields=_required_field("callsign"),
    ),
)


__all__ = [
    "LIVE_OPS_TOOLS",
    "LiveOpsCall",
    "LiveOpsToolSpec",
    "find_aircraft_by_callsign",
    "get_aircraft_operational_context",
    "get_airport_operational_context",
    "get_hazard_operational_context",
    "get_observed_network_state",
    "search_current_encounters",
    "search_current_hazards",
    "search_current_impacts",
]
