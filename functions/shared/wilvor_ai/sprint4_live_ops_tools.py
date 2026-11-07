"""Sprint 4 authorized execution of the eight Live Ops tools.

Each tool uses only the host-allowlisted ``GET_ITEM`` and ``QUERY``
resources for that tool. Scan-backed inventory and unallowlisted parent
hydration are refused or reported as limitations. Empty pages are not
verified zeros. Region labels are not SIGMET polygons.

Live AWS stays closed. Table handles are injected. A scan on a wrapped
handle fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS, LiveOpsCall
from wilvor_ai.live_ops_mapping import (
    map_aircraft_operational_context,
    map_airport_operational_context,
    map_find_aircraft_by_callsign,
    map_hazard_operational_context,
    map_search_current_encounters,
    map_search_current_hazards_discovery,
    map_search_current_hazards_region,
    map_search_current_impacts,
)
from wilvor_ai.master_read_resource_guard import ReadOperation
from wilvor_ai.sprint3c_scoped_read import ConsumedAttemptReadBinding
from wilvor_ai.sprint4_live_ops_authorization import (
    LIVE_OPS_QUERY_PAGE_LIMIT,
    LiveOpsReadAuthorization,
    authorize_live_ops_tool,
)
from wilvor_ai.sprint4_live_ops_source import (
    RELATED_RESOURCE_NOT_ALLOWLISTED,
    LiveOpsReadProvenance,
    LiveOpsSourceReport,
    build_live_ops_source_report,
    overlay_tool_result_source,
    provenance_for_page,
    refused_live_ops_source_report,
)
from wilvor_operational import current_set
from wilvor_operational import discovery
from wilvor_operational import linking
from wilvor_operational import observed
from wilvor_operational import readers
from wilvor_operational.linking import Coverage
from wilvor_operational.observed import epoch_to_utc_z
from wilvor_operational.query import (
    CurrentEncounterQueryResult,
    CurrentImpactQueryResult,
    OperationalUnevaluatedReason,
    OperationallyUnevaluatedEncounterCandidate,
    OperationallyUnevaluatedImpactCandidate,
)


SPRINT4_LIVE_OPS_TOOLS_SCHEMA_VERSION = "wilvor.ai.sprint4.live_ops_tools.v1"
_LIVE_OPS_TOOL_NAMES = frozenset(item.name for item in LIVE_OPS_TOOLS)
_TABLE_RESOURCES = {
    "aircraft": "aircraft",
    "hazards": "hazards",
    "hazard_coordinates": "hazard_coordinates",
    "encounters": "encounters",
    "airports": "airports",
    "metar": "metar",
    "taf": "taf",
    "projections": "projections",
    "projection_points": "projection_points",
    "risks": "risks",
    "taf_periods": "taf_periods",
    "airport_assessments": "airport_assessments",
    "recommendations": "recommendations",
    "alerts": "alerts",
}
_RETRIEVAL_RESOURCE = {
    "get_aircraft_record": ("aircraft", ReadOperation.GET_ITEM),
    "query_aircraft_by_callsign": ("aircraft", ReadOperation.QUERY),
    "get_hazard_record": ("hazards", ReadOperation.GET_ITEM),
    "query_active_hazard_candidates": ("hazards", ReadOperation.QUERY),
    "query_hazard_coordinate_rows": ("hazard_coordinates", ReadOperation.QUERY),
    "get_encounter_record": ("encounters", ReadOperation.GET_ITEM),
    "query_encounter_candidates_by_hazard": ("encounters", ReadOperation.QUERY),
    "query_encounter_candidates_by_aircraft": ("encounters", ReadOperation.QUERY),
    "get_airport_status_record": ("airports", ReadOperation.GET_ITEM),
    "get_metar_record": ("metar", ReadOperation.GET_ITEM),
    "get_taf_record": ("taf", ReadOperation.GET_ITEM),
}


class _ScanRejectedTable:
    """Injected DynamoDB handle that cannot Scan and cannot exceed allowlist."""

    def __init__(self, inner: object, *, allow_get: bool, allow_query: bool) -> None:
        self._inner = inner
        self._allow_get = allow_get
        self._allow_query = allow_query

    def get_item(self, **kwargs: object) -> object:
        if not self._allow_get:
            raise ContractValidationError("resource_not_allowlisted")
        return self._inner.get_item(**kwargs)

    def query(self, **kwargs: object) -> object:
        if not self._allow_query:
            raise ContractValidationError("resource_not_allowlisted")
        return self._inner.query(**kwargs)

    def scan(self, **kwargs: object) -> object:
        raise ContractValidationError("scan_not_permitted")


def wrap_authorized_tables(
    tables: readers.OperationalTables,
    authorization: LiveOpsReadAuthorization,
) -> readers.OperationalTables:
    """Reject Scan and unallowlisted get/query on injected table handles."""

    def wrap(attr: str) -> _ScanRejectedTable:
        resource = _TABLE_RESOURCES[attr]
        return _ScanRejectedTable(
            getattr(tables, attr),
            allow_get=authorization.allows(resource, ReadOperation.GET_ITEM),
            allow_query=authorization.allows(resource, ReadOperation.QUERY),
        )

    return readers.OperationalTables(
        aircraft=wrap("aircraft"),
        projections=wrap("projections"),
        projection_points=wrap("projection_points"),
        hazards=wrap("hazards"),
        hazard_coordinates=wrap("hazard_coordinates"),
        encounters=wrap("encounters"),
        risks=wrap("risks"),
        airports=wrap("airports"),
        metar=wrap("metar"),
        taf=wrap("taf"),
        taf_periods=wrap("taf_periods"),
        airport_assessments=wrap("airport_assessments"),
        recommendations=wrap("recommendations"),
        alerts=wrap("alerts"),
    )


@dataclass(frozen=True)
class Sprint4LiveOpsToolOutcome:
    tool_result: ToolResult
    source_report: LiveOpsSourceReport
    authorization: LiveOpsReadAuthorization | None
    refusal_code: str | None
    live_aws_initiated: bool = False
    verified_zero: bool = False

    def __post_init__(self) -> None:
        if self.live_aws_initiated is not False:
            raise ContractValidationError("live_aws_operation_not_initiated")
        if self.verified_zero is not False:
            raise ContractValidationError("empty_page_is_not_verified_zero")
        if self.source_report.verified_zero is not False:
            raise ContractValidationError("empty_page_is_not_verified_zero")


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unavailable(call: LiveOpsCall, tool_name: str, code: str) -> ToolResult:
    as_of = epoch_to_utc_z(call.now_epoch)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=call.tool_call_id,
        status=ToolResultStatus.UNAVAILABLE,
        temporal_scope=TemporalScope.CURRENT,
        data={"as_of_epoch": call.now_epoch, "as_of_utc": as_of},
        evidence=(),
        as_of_utc=as_of,
        limitations=(code,),
        correlation_id=call.correlation_id,
    )


def _provenance_from_retrieval(
    observations: tuple[Any, ...],
    *,
    tool_call_id: str,
) -> tuple[LiveOpsReadProvenance, ...]:
    items: list[LiveOpsReadProvenance] = []
    for index, observation in enumerate(observations):
        mapped = _RETRIEVAL_RESOURCE.get(getattr(observation, "source", ""))
        if mapped is None:
            continue
        resource_id, operation = mapped
        coverage = getattr(observation, "coverage", Coverage.EXACT_PK)
        truncated = linking.QUERY_PAGE_TRUNCATED in (
            getattr(observation, "limitations", ()) or ()
        )
        limit = getattr(observation, "limit", None)
        rows = int(limit) if type(limit) is int and not truncated else 0
        query_id = None
        if operation is ReadOperation.QUERY:
            query_id = f"live-ops-{resource_id}-{tool_call_id}-{index}"
        items.append(
            provenance_for_page(
                resource_id=resource_id,
                operation=operation,
                coverage=coverage if isinstance(coverage, Coverage) else Coverage.EXACT_PK,
                rows_returned=rows,
                truncated=truncated,
                query_id=query_id,
            )
        )
    return tuple(items)


def _records_from_mapping_root(root: object) -> tuple[Mapping[str, Any], ...]:
    found: list[Mapping[str, Any]] = []

    def add(value: object) -> None:
        if isinstance(value, Mapping):
            found.append(value)

    if root is None:
        return ()
    add(getattr(root, "aircraft", None))
    add(getattr(root, "hazard", None))
    add(getattr(root, "airport", None))
    add(getattr(root, "latest_metar", None))
    add(getattr(root, "latest_taf", None))
    for match in getattr(root, "matches", ()) or ():
        add(getattr(match, "source", None))
    for item in getattr(root, "intersecting", ()) or ():
        add(getattr(item, "hazard", None))
    for item in getattr(root, "impacts", ()) or ():
        add(getattr(getattr(item, "impact", None), "aircraft", None))
    for item in getattr(root, "encounters", ()) or ():
        context = getattr(item, "encounter_context", None)
        encounter = getattr(context, "encounter", None) if context is not None else None
        add(encounter)
        add(getattr(item, "aircraft", None))
    return tuple(found)


def _finish(
    authorization: LiveOpsReadAuthorization,
    call: LiveOpsCall,
    tool_result: ToolResult,
    *,
    root: object = None,
    observations: tuple[Any, ...] = (),
    records: tuple[Mapping[str, Any], ...] = (),
    spatial_statuses: tuple[str, ...] = (),
    intersecting_hazard_ids: tuple[str, ...] = (),
    region_query: str | None = None,
    coordinates_queried: bool = False,
    region_resolved: bool = False,
    spatial_supported: bool = False,
    extra_limitations: tuple[str, ...] = (),
) -> Sprint4LiveOpsToolOutcome:
    collected = records or _records_from_mapping_root(root)
    provenance = _provenance_from_retrieval(
        observations or tuple(getattr(root, "retrieval", ()) or ()),
        tool_call_id=call.tool_call_id,
    )
    if not provenance and collected:
        resource_id = authorization.binding.resource_id
        provenance = (
            provenance_for_page(
                resource_id=resource_id,
                operation=authorization.binding.operation,
                coverage=Coverage.EXACT_PK
                if authorization.binding.operation is ReadOperation.GET_ITEM
                else Coverage.BOUNDED_QUERY,
                rows_returned=len(collected),
                truncated=False,
            ),
        )
    report = build_live_ops_source_report(
        authorization,
        now_epoch=call.now_epoch,
        records=collected,
        provenance=provenance,
        spatial_statuses=spatial_statuses,
        intersecting_hazard_ids=intersecting_hazard_ids,
        region_query=region_query,
        coordinates_queried=coordinates_queried,
        region_resolved=region_resolved,
        spatial_supported=spatial_supported,
        extra_limitations=extra_limitations,
    )
    overlaid = overlay_tool_result_source(tool_result, report)
    return Sprint4LiveOpsToolOutcome(
        tool_result=overlaid,
        source_report=report,
        authorization=authorization,
        refusal_code=None,
    )


def _hazard_ids(arguments: Mapping[str, object]) -> list[str] | None:
    raw = arguments.get("hazard_ids")
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    return None


def _execute_search_hazards(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    region = _text(arguments.get("region")) or None
    product_type = arguments.get("product_type")
    hazard_type = arguments.get("hazard_type")
    hazard_ids = _hazard_ids(arguments)
    limit = authorization.query_limit
    extra: list[str] = []
    if region:
        from wilvor_operational import geospatial
        from wilvor_operational import regions

        resolution = regions.resolve_region(region)
        coordinates_ok = authorization.allows(
            "hazard_coordinates", ReadOperation.QUERY
        )
        if not coordinates_ok:
            extra.append(RELATED_RESOURCE_NOT_ALLOWLISTED)
            discovered = discovery.discover_current_hazards(
                tables,
                now_epoch=call.now_epoch,
                product_type=product_type,
                hazard_type=hazard_type,
                hazard_ids=hazard_ids,
                max_items=limit,
            )
            mapped = map_search_current_hazards_discovery(
                call,
                discovered,
                product_type=product_type,
                hazard_type=hazard_type,
                hazard_ids=hazard_ids,
            )
            return _finish(
                authorization,
                call,
                mapped,
                root=discovered,
                region_query=region,
                coordinates_queried=False,
                region_resolved=bool(resolution.resolved),
                spatial_supported=bool(
                    getattr(resolution.region, "spatial_supported", False)
                )
                if resolution.region is not None
                else False,
                extra_limitations=tuple(extra),
            )
        result = geospatial.discover_current_hazards_in_region(
            tables,
            region,
            now_epoch=call.now_epoch,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
            max_items=limit,
        )
        mapped = map_search_current_hazards_region(
            call,
            result,
            region=region,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
        )
        spatial = tuple(
            getattr(item.spatial_status, "value", str(item.spatial_status))
            for group in (
                result.intersecting,
                result.non_intersecting,
                result.unevaluated,
            )
            for item in group
        )
        return _finish(
            authorization,
            call,
            mapped,
            root=result,
            spatial_statuses=spatial,
            intersecting_hazard_ids=tuple(item.hazard_id for item in result.intersecting),
            region_query=region,
            coordinates_queried=True,
            region_resolved=bool(result.region_resolution.resolved),
            spatial_supported=bool(
                getattr(result.region_resolution.region, "spatial_supported", False)
            )
            if result.region_resolution.region is not None
            else False,
        )
    discovered = discovery.discover_current_hazards(
        tables,
        now_epoch=call.now_epoch,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
        max_items=limit,
    )
    mapped = map_search_current_hazards_discovery(
        call,
        discovered,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )
    return _finish(authorization, call, mapped, root=discovered)


def _execute_search_impacts(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    region = _text(arguments.get("region")) or None
    product_type = arguments.get("product_type")
    hazard_type = arguments.get("hazard_type")
    hazard_ids = _hazard_ids(arguments)
    extra: list[str] = []
    region_resolution = None
    geospatial_unevaluated: tuple[Any, ...] = ()
    if region:
        from wilvor_operational import regions

        region_resolution = regions.resolve_region(region)
        extra.append(RELATED_RESOURCE_NOT_ALLOWLISTED)
    selected: dict[str, str] = {}
    rejected = ()
    unevaluated: list[OperationallyUnevaluatedImpactCandidate] = []
    retrieval: tuple[Any, ...] = ()
    if hazard_ids is not None and authorization.allows("hazards", ReadOperation.GET_ITEM):
        discovered = discovery.discover_current_hazards(
            tables,
            now_epoch=call.now_epoch,
            product_type=product_type,
            hazard_type=hazard_type,
            hazard_ids=hazard_ids,
            max_items=authorization.query_limit,
        )
        retrieval = discovered.retrieval
        for match in discovered.matches:
            identity = getattr(match, "identity", ()) or ()
            hazard_key = ""
            version = ""
            for name, value in identity:
                if name == "hazard_id":
                    hazard_key = value
                if name == "source_version":
                    version = value
            if match.is_current and hazard_key and version:
                selected[hazard_key] = version
    elif hazard_ids is not None:
        extra.append(RELATED_RESOURCE_NOT_ALLOWLISTED)
    encounters: tuple[dict[str, Any], ...] = ()
    encounter_retrieval: tuple[Any, ...] = ()
    truncated = False
    if selected and authorization.allows("encounters", ReadOperation.QUERY):
        encounters, encounter_retrieval, truncated = (
            observed.load_encounter_candidates_for_hazards_bounded(
                tables,
                selected.keys(),
                limit=authorization.query_limit,
            )
        )
    else:
        extra.append(RELATED_RESOURCE_NOT_ALLOWLISTED)
    impact_unevaluated = list(unevaluated)
    for item in encounters:
        impact_unevaluated.append(
            OperationallyUnevaluatedImpactCandidate(
                encounter_id=_text(item.get("encounter_id")),
                aircraft_id=_text(item.get("aircraft_id")).lower(),
                hazard_id=_text(item.get("hazard_id")),
                selected_source_version=_text(item.get("hazard_source_version")),
                reason=OperationalUnevaluatedReason.HAZARD_AUTHORITY_UNESTABLISHED.value
                if not authorization.allows("hazards", ReadOperation.GET_ITEM)
                else OperationalUnevaluatedReason.ENCOUNTER_HYDRATION_MISSING.value,
                encounter_link=None,
                hazard_link=None,
                retrieval=encounter_retrieval,
                limitations=(linking.NO_SNAPSHOT_LIMITATION, RELATED_RESOURCE_NOT_ALLOWLISTED),
            )
        )
    result = CurrentImpactQueryResult(
        now_epoch=call.now_epoch,
        impacts=(),
        unique_aircraft_ids=(),
        operationally_unevaluated=tuple(impact_unevaluated),
        region_resolution=region_resolution,
        geospatial_unevaluated=geospatial_unevaluated,
        rejected_hazard_candidates=(),
        rejected_hazard_selections=rejected,
        retrieval=tuple(retrieval) + tuple(encounter_retrieval),
        limitations=(linking.NO_SNAPSHOT_LIMITATION, *extra),
    )
    mapped = map_search_current_impacts(
        call,
        result,
        region=region,
        product_type=product_type,
        hazard_type=hazard_type,
        hazard_ids=hazard_ids,
    )
    records = tuple(encounters)
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=records,
        region_query=region,
        coordinates_queried=False,
        region_resolved=bool(getattr(region_resolution, "resolved", False)),
        extra_limitations=tuple(extra)
        + ((linking.QUERY_PAGE_TRUNCATED,) if truncated else ()),
    )


def _execute_search_encounters(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    aircraft_id = _text(arguments.get("aircraft_id")).lower() or None
    callsign = _text(arguments.get("callsign")) or None
    hazard_id = _text(arguments.get("hazard_id")) or None
    hazard_ids = _hazard_ids(arguments)
    extra: list[str] = [RELATED_RESOURCE_NOT_ALLOWLISTED]
    aircraft_ids: list[str] = []
    callsign_matches: tuple[str, ...] = ()
    retrieval: list[Any] = []
    if aircraft_id and authorization.allows("aircraft", ReadOperation.GET_ITEM):
        row = readers.get_aircraft_record(tables.aircraft, aircraft_id)
        retrieval.append(linking.exact_pk_observation("get_aircraft_record"))
        if row is not None:
            aircraft_ids.append(aircraft_id)
    if callsign and authorization.allows("aircraft", ReadOperation.QUERY):
        found = discovery.discover_aircraft_by_callsign(
            tables,
            callsign,
            now_epoch=call.now_epoch,
        )
        retrieval.extend(found.retrieval)
        callsign_matches = tuple(
            _text(getattr(match, "source", {}) and match.source.get("aircraft_id")).lower()
            for match in found.matches
            if getattr(match, "is_current", False)
        )
        aircraft_ids.extend(item for item in callsign_matches if item)
    encounters: tuple[dict[str, Any], ...] = ()
    truncated = False
    if aircraft_ids and authorization.allows("encounters", ReadOperation.QUERY):
        encounters, encounter_retrieval, truncated = (
            observed.load_encounter_candidates_for_aircraft_bounded(
                tables,
                aircraft_ids,
                limit=authorization.query_limit,
            )
        )
        retrieval.extend(encounter_retrieval)
    elif (hazard_id or hazard_ids) and authorization.allows(
        "encounters", ReadOperation.QUERY
    ):
        keys = list(hazard_ids or [])
        if hazard_id:
            keys.append(hazard_id)
        encounters, encounter_retrieval, truncated = (
            observed.load_encounter_candidates_for_hazards_bounded(
                tables,
                keys,
                limit=authorization.query_limit,
            )
        )
        retrieval.extend(encounter_retrieval)
    unevaluated = tuple(
        OperationallyUnevaluatedEncounterCandidate(
            encounter_id=_text(item.get("encounter_id")),
            aircraft_id=_text(item.get("aircraft_id")).lower(),
            hazard_id=_text(item.get("hazard_id")),
            selected_source_version=_text(item.get("hazard_source_version")),
            reason=OperationalUnevaluatedReason.HAZARD_AUTHORITY_UNESTABLISHED.value,
            encounter_link=None,
            hazard_link=None,
            retrieval=tuple(retrieval),
            limitations=(linking.NO_SNAPSHOT_LIMITATION, RELATED_RESOURCE_NOT_ALLOWLISTED),
        )
        for item in encounters
    )
    result = CurrentEncounterQueryResult(
        now_epoch=call.now_epoch,
        encounters=(),
        operationally_unevaluated=unevaluated,
        callsign_matches=callsign_matches,
        rejected_hazard_selections=(),
        retrieval=tuple(retrieval),
        limitations=(linking.NO_SNAPSHOT_LIMITATION, *extra),
    )
    mapped = map_search_current_encounters(
        call,
        result,
        aircraft_id=aircraft_id,
        callsign=callsign,
        hazard_id=hazard_id,
        hazard_ids=hazard_ids,
    )
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=tuple(encounters),
        extra_limitations=tuple(extra)
        + ((linking.QUERY_PAGE_TRUNCATED,) if truncated else ()),
    )


def _execute_aircraft_context(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    from wilvor_operational.context import normalize_aircraft_id

    aircraft_id = normalize_aircraft_id(arguments.get("aircraft_id"))
    extra = (RELATED_RESOURCE_NOT_ALLOWLISTED,)
    if not aircraft_id or not authorization.allows("aircraft", ReadOperation.GET_ITEM):
        mapped = map_aircraft_operational_context(call, None, aircraft_id=aircraft_id)
        return _finish(authorization, call, mapped, extra_limitations=extra)
    row = readers.get_aircraft_record(tables.aircraft, aircraft_id)
    if row is None:
        mapped = map_aircraft_operational_context(call, None, aircraft_id=aircraft_id)
        return _finish(authorization, call, mapped, extra_limitations=extra)
    result = linking.AircraftOperationalContext(
        aircraft=row,
        aircraft_is_current=current_set.is_current_aircraft(row, call.now_epoch),
        projection=None,
        projection_is_current=False,
        encounters=(),
        projection_link=linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.CURRENT_DEPENDENT,
            reason=RELATED_RESOURCE_NOT_ALLOWLISTED,
        ),
        retrieval=(linking.exact_pk_observation("get_aircraft_record"),),
    )
    mapped = map_aircraft_operational_context(call, result, aircraft_id=aircraft_id)
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=(row,),
        extra_limitations=extra,
    )


def _execute_hazard_context(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    hazard_id = _text(arguments.get("hazard_id"))
    extra = (RELATED_RESOURCE_NOT_ALLOWLISTED,)
    if not hazard_id or not authorization.allows("hazards", ReadOperation.GET_ITEM):
        mapped = map_hazard_operational_context(call, None, hazard_id=hazard_id)
        return _finish(authorization, call, mapped, extra_limitations=extra)
    row = readers.get_hazard_record(tables.hazards, hazard_id)
    if row is None:
        mapped = map_hazard_operational_context(call, None, hazard_id=hazard_id)
        return _finish(authorization, call, mapped, extra_limitations=extra)
    source_version = _text(row.get("source_version"))
    result = linking.HazardOperationalContext(
        hazard=row,
        hazard_is_lifecycle_active=current_set.is_lifecycle_active_hazard(
            row, call.now_epoch
        ),
        hazard_is_current=current_set.is_current_hazard(row, call.now_epoch),
        source_version=source_version,
        impacts=(),
        hazard_version_link=linking.Link(
            state=linking.LinkState.PRESENT
            if source_version
            else linking.LinkState.MISSING,
            kind=linking.LinkKind.VERSIONED,
            selected_identity=(
                (("hazard_id", hazard_id), ("source_version", source_version))
                if source_version
                else (("hazard_id", hazard_id),)
            ),
            observed_identity=(
                (("hazard_id", _text(row.get("hazard_id"))), ("source_version", source_version))
                if source_version
                else (("hazard_id", _text(row.get("hazard_id"))),)
            ),
        ),
        retrieval=(linking.exact_pk_observation("get_hazard_record"),),
    )
    mapped = map_hazard_operational_context(call, result, hazard_id=hazard_id)
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=(row,),
        extra_limitations=extra,
    )


def _execute_airport_context(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    from wilvor_operational import context as operational_context

    airport_id = _text(arguments.get("airport_id")).upper()
    extra = (RELATED_RESOURCE_NOT_ALLOWLISTED,)
    if not airport_id or not authorization.allows("airports", ReadOperation.GET_ITEM):
        mapped = map_airport_operational_context(call, None, airport_id=airport_id)
        return _finish(authorization, call, mapped, extra_limitations=extra)
    result = operational_context.build_airport_operational_context(
        tables,
        airport_id,
        now_epoch=call.now_epoch,
        include_taf_periods=False,
    )
    mapped = map_airport_operational_context(call, result, airport_id=airport_id)
    records = ()
    if result is not None:
        records = tuple(
            item
            for item in (result.airport, result.latest_metar, result.latest_taf)
            if isinstance(item, dict)
        )
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=records,
        extra_limitations=extra,
    )


def _execute_find_callsign(
    authorization: LiveOpsReadAuthorization,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object],
) -> Sprint4LiveOpsToolOutcome:
    callsign = arguments.get("callsign")
    if not authorization.allows("aircraft", ReadOperation.QUERY):
        mapped = map_find_aircraft_by_callsign(call, None, callsign=callsign)
        return _finish(
            authorization,
            call,
            mapped,
            extra_limitations=(RELATED_RESOURCE_NOT_ALLOWLISTED,),
        )
    normalized = _text(callsign).upper()
    if not normalized:
        mapped = map_find_aircraft_by_callsign(call, None, callsign=callsign)
        return _finish(authorization, call, mapped)
    page = readers.query_aircraft_by_callsign_page(
        tables.aircraft,
        callsign=normalized,
        now_epoch=call.now_epoch,
        limit=authorization.query_limit,
    )
    truncated = page.get("last_evaluated_key") is not None
    matches = tuple(
        discovery.EntityMatch(
            kind=discovery.KIND_AIRCRAFT,
            identity=(("aircraft_id", _text(item.get("aircraft_id")).lower()),),
            source=item,
            is_current=current_set.is_current_aircraft(item, call.now_epoch),
        )
        for item in page.get("items", [])
        if current_set.is_current_aircraft(item, call.now_epoch)
    )
    result = discovery.AircraftDiscoveryResult(
        matches=matches,
        retrieval=(
            linking.bounded_query_observation(
                "query_aircraft_by_callsign",
                discovery.CALLSIGN_CASE_LIMITATION,
                limit=authorization.query_limit,
                truncated=truncated,
            ),
        ),
    )
    mapped = map_find_aircraft_by_callsign(call, result, callsign=callsign)
    records = tuple(
        match.source
        for match in result.matches
        if getattr(match, "source", None) is not None
    )
    extra = (linking.QUERY_PAGE_TRUNCATED,) if truncated else ()
    return _finish(
        authorization,
        call,
        mapped,
        root=result,
        records=records,
        extra_limitations=extra,
    )


def execute_authorized_live_ops_tool(
    authorization: LiveOpsReadAuthorization,
    *,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    arguments: Mapping[str, object] | None = None,
) -> Sprint4LiveOpsToolOutcome:
    if type(authorization) is not LiveOpsReadAuthorization:
        raise ContractValidationError("invalid_live_ops_authorization")
    if type(call) is not LiveOpsCall:
        raise ContractValidationError("invalid_live_ops_call")
    payload = dict(arguments or {})
    wrapped = wrap_authorized_tables(tables, authorization)
    handlers = {
        "search_current_hazards": _execute_search_hazards,
        "search_current_impacts": _execute_search_impacts,
        "search_current_encounters": _execute_search_encounters,
        "get_aircraft_operational_context": _execute_aircraft_context,
        "get_hazard_operational_context": _execute_hazard_context,
        "get_airport_operational_context": _execute_airport_context,
        "find_aircraft_by_callsign": _execute_find_callsign,
    }
    handler = handlers.get(authorization.tool_name)
    if handler is None:
        report = refused_live_ops_source_report(
            now_epoch=call.now_epoch,
            extra_limitations=("unbounded_population_refused",),
        )
        return Sprint4LiveOpsToolOutcome(
            tool_result=_unavailable(
                call, authorization.tool_name, "unbounded_population_refused"
            ),
            source_report=report,
            authorization=authorization,
            refusal_code="unbounded_population_refused",
        )
    return handler(authorization, wrapped, call, payload)


def run_authorized_live_ops_tool(
    binding: ConsumedAttemptReadBinding,
    *,
    tables: readers.OperationalTables,
    call: LiveOpsCall,
    tool_name: str,
    arguments: Mapping[str, object] | None = None,
    requested_resource_id: str | None = None,
    requested_operation: ReadOperation | None = None,
    query_limit: int = LIVE_OPS_QUERY_PAGE_LIMIT,
) -> Sprint4LiveOpsToolOutcome:
    payload = dict(arguments or {})
    try:
        authorization = authorize_live_ops_tool(
            binding,
            tool_name=tool_name,
            arguments=payload,
            requested_resource_id=requested_resource_id,
            requested_operation=requested_operation,
            query_limit=query_limit,
        )
    except ContractValidationError as exc:
        code = exc.errors[0] if exc.errors else "live_ops_authorization_refused"
        return Sprint4LiveOpsToolOutcome(
            tool_result=_unavailable(call, tool_name, code),
            source_report=refused_live_ops_source_report(
                now_epoch=call.now_epoch,
                extra_limitations=(code,),
            ),
            authorization=None,
            refusal_code=code,
        )
    return execute_authorized_live_ops_tool(
        authorization,
        tables=tables,
        call=call,
        arguments=payload,
    )


__all__ = [
    "SPRINT4_LIVE_OPS_TOOLS_SCHEMA_VERSION",
    "Sprint4LiveOpsToolOutcome",
    "execute_authorized_live_ops_tool",
    "run_authorized_live_ops_tool",
    "wrap_authorized_tables",
]
