"""Sprint 5 bounded Decision reads.

Decision tools previously inherited Scan-backed aircraft context. This
module replaces those scans with reviewed key and GSI Query access for one
aircraft_id. Absence is not LOW or MONITOR. Truncation is not a verified
zero. This module does not construct AWS clients or grant Scan.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from boto3.dynamodb.conditions import Attr

from wilvor_ai.contracts import ContractValidationError
from wilvor_operational import current_set
from wilvor_operational import linking
from wilvor_operational import readers
from wilvor_operational.context import (
    _build_encounter_context,
    _epoch_to_utc_z,
    _text,
    normalize_aircraft_id,
)


SPRINT5_DECISION_READS_SCHEMA = "wilvor.ai.sprint5.decision_reads.v1"
DECISION_BOUNDED_QUERY_LIMIT = 25
DECISION_SCAN_REMOVED = True
EMPTY_BOUNDED_PAGE_IS_VERIFIED_ZERO = False

_SCAN_SOURCES = frozenset(
    {
        "scan_projection_index_candidates",
        "scan_hazard_index_candidates",
        "scan_encounter_candidates",
        "scan_risk_candidates",
        "scan_recommendation_candidates",
        "scan_alert_candidates",
    }
)


def _fail(code: str) -> None:
    raise ContractValidationError(code)


def _closed() -> None:
    if DECISION_SCAN_REMOVED is not True:
        _fail("decision_scan_not_removed")
    if EMPTY_BOUNDED_PAGE_IS_VERIFIED_ZERO is not False:
        _fail("empty_page_is_not_verified_zero")
    if type(DECISION_BOUNDED_QUERY_LIMIT) is not int or DECISION_BOUNDED_QUERY_LIMIT < 1:
        _fail("invalid_query_limit")


class _ScanRejectedHandle:
    """Injected handle that cannot Scan."""

    def __init__(self, inner: object, *, allow_get: bool, allow_query: bool) -> None:
        self._inner = inner
        self._allow_get = allow_get
        self._allow_query = allow_query

    def get_item(self, **kwargs: object) -> object:
        if not self._allow_get:
            _fail("resource_not_allowlisted")
        return self._inner.get_item(**kwargs)

    def query(self, **kwargs: object) -> object:
        if not self._allow_query:
            _fail("resource_not_allowlisted")
        return self._inner.query(**kwargs)

    def scan(self, **kwargs: object) -> object:
        _fail("scan_not_permitted")


def wrap_decision_tables_reject_scan(tables: readers.OperationalTables) -> readers.OperationalTables:
    """Reject Scan on Decision table handles. GetItem/Query stay available."""

    _closed()
    if not isinstance(tables, readers.OperationalTables):
        _fail("invalid_tables")

    def wrap(attr: str, *, allow_get: bool, allow_query: bool) -> _ScanRejectedHandle:
        return _ScanRejectedHandle(
            getattr(tables, attr),
            allow_get=allow_get,
            allow_query=allow_query,
        )

    return readers.OperationalTables(
        aircraft=wrap("aircraft", allow_get=True, allow_query=False),
        projections=wrap("projections", allow_get=True, allow_query=True),
        projection_points=wrap("projection_points", allow_get=True, allow_query=False),
        hazards=wrap("hazards", allow_get=True, allow_query=False),
        hazard_coordinates=wrap("hazard_coordinates", allow_get=False, allow_query=False),
        encounters=wrap("encounters", allow_get=True, allow_query=True),
        risks=wrap("risks", allow_get=True, allow_query=True),
        airports=wrap("airports", allow_get=True, allow_query=False),
        metar=wrap("metar", allow_get=True, allow_query=False),
        taf=wrap("taf", allow_get=True, allow_query=False),
        taf_periods=wrap("taf_periods", allow_get=False, allow_query=False),
        airport_assessments=wrap("airport_assessments", allow_get=True, allow_query=True),
        recommendations=wrap("recommendations", allow_get=True, allow_query=True),
        alerts=wrap("alerts", allow_get=True, allow_query=True),
    )


def _page_items(page: dict[str, Any]) -> tuple[list[Any], bool]:
    items = list(page.get("items") or [])
    truncated = page.get("last_evaluated_key") is not None
    return items, truncated


def build_aircraft_operational_context_bounded(
    tables,
    aircraft_id,
    *,
    now_epoch,
    query_limit: int = DECISION_BOUNDED_QUERY_LIMIT,
):
    """Aircraft context using GetItem plus aircraft-partitioned Query only."""

    _closed()
    if type(query_limit) is not int or query_limit < 1 or query_limit > DECISION_BOUNDED_QUERY_LIMIT:
        _fail("invalid_query_limit")
    aircraft_id = normalize_aircraft_id(aircraft_id)
    if not aircraft_id:
        return None
    aircraft = readers.get_aircraft_record(tables.aircraft, aircraft_id)
    if aircraft is None:
        return None

    now_iso = _epoch_to_utc_z(now_epoch)
    projection_page = readers.query_projection_candidates_by_aircraft_page(
        tables.projections,
        aircraft_id,
        now_epoch=now_epoch,
        limit=query_limit,
        attr=Attr,
    )
    encounter_page = readers.query_encounter_candidates_by_aircraft_page(
        tables.encounters,
        aircraft_id,
        limit=query_limit,
        attr=Attr,
    )
    risk_page = readers.query_risk_candidates_by_aircraft_page(
        tables.risks,
        aircraft_id,
        limit=query_limit,
    )
    recommendation_page = readers.query_recommendation_candidates_by_aircraft_page(
        tables.recommendations,
        aircraft_id,
        now_iso=now_iso,
        limit=query_limit,
        attr=Attr,
    )
    alert_page = readers.query_alert_candidates_by_aircraft_page(
        tables.alerts,
        aircraft_id,
        now_iso=now_iso,
        limit=query_limit,
        attr=Attr,
    )
    projection_candidates, projection_truncated = _page_items(projection_page)
    encounter_candidates, encounter_truncated = _page_items(encounter_page)
    risk_candidates, risk_truncated = _page_items(risk_page)
    recommendation_candidates, recommendation_truncated = _page_items(recommendation_page)
    alert_candidates, alert_truncated = _page_items(alert_page)

    retrieval = [
        linking.exact_pk_observation("get_aircraft_record"),
        linking.bounded_query_observation(
            "query_projection_candidates_by_aircraft",
            limit=query_limit,
            truncated=projection_truncated,
        ),
        linking.bounded_query_observation(
            "query_encounter_candidates_by_aircraft",
            limit=query_limit,
            truncated=encounter_truncated,
        ),
        linking.bounded_query_observation(
            "query_risk_candidates_by_aircraft",
            limit=query_limit,
            truncated=risk_truncated,
        ),
        linking.bounded_query_observation(
            "query_recommendation_candidates_by_aircraft",
            linking.ISO_CANDIDATE_LIMITATION,
            linking.RECOMMENDATION_ABSENCE_LIMITATION,
            limit=query_limit,
            truncated=recommendation_truncated,
        ),
        linking.bounded_query_observation(
            "query_alert_candidates_by_aircraft",
            linking.ISO_CANDIDATE_LIMITATION,
            linking.ALERT_ABSENCE_LIMITATION,
            limit=query_limit,
            truncated=alert_truncated,
        ),
        linking.exact_pk_observation("get_projection_record"),
        linking.exact_pk_observation("get_hazard_record"),
        linking.exact_pk_observation("get_risk_record"),
        linking.exact_pk_observation("get_encounter_record"),
    ]
    if any(item.source in _SCAN_SOURCES for item in retrieval):
        _fail("decision_scan_not_removed")

    current_projection_ids = current_set.index_current_projections(
        projection_candidates,
        now_epoch,
    )
    hazard_ids = {
        _text(item.get("hazard_id"))
        for item in encounter_candidates
        if _text(item.get("hazard_id"))
    }
    current_hazard_versions: dict[str, str] = {}
    for hazard_id in sorted(hazard_ids):
        hydrated = readers.get_hazard_record(tables.hazards, hazard_id)
        if hydrated is None:
            continue
        if not current_set.is_current_hazard(hydrated, now_epoch):
            continue
        source_version = _text(hydrated.get("source_version"))
        if source_version:
            current_hazard_versions[hazard_id] = source_version

    selected_projection_id = current_projection_ids.get(aircraft_id)
    projection = None
    projection_is_current = False
    if not selected_projection_id:
        projection_link = linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.CURRENT_DEPENDENT,
            reason="no current projection identity in the observed candidate set",
        )
    else:
        hydrated_projection = readers.get_projection_record(
            tables.projections,
            selected_projection_id,
        )
        projection, projection_link = linking.hydrate_projection(
            selected_projection_id=selected_projection_id,
            context_aircraft_id=aircraft_id,
            hydrated=hydrated_projection,
            now_epoch=now_epoch,
        )
        projection_is_current = projection_link.state == linking.LinkState.PRESENT

    selected_encounters = [
        item
        for item in encounter_candidates
        if _text(item.get("aircraft_id")).lower() == aircraft_id
        and current_set.is_current_encounter(
            item,
            current_projection_ids=current_projection_ids,
            current_hazard_versions=current_hazard_versions,
        )
    ]
    selected_encounter_ids = {
        item.get("encounter_id")
        for item in selected_encounters
        if item.get("encounter_id")
    }
    selected_risks = current_set.index_latest_current_risks(
        risk_candidates,
        current_encounter_ids=selected_encounter_ids,
        now_epoch=now_epoch,
    )
    encounters = tuple(
        _build_encounter_context(
            tables,
            aircraft_id=aircraft_id,
            selected_projection_id=selected_projection_id or "",
            encounter_candidate=item,
            current_projection_ids=current_projection_ids,
            current_hazard_versions=current_hazard_versions,
            selected_risks=selected_risks,
            selected_encounter_ids=selected_encounter_ids,
            recommendation_candidates=recommendation_candidates,
            alert_candidates=alert_candidates,
            now_epoch=now_epoch,
        )
        for item in selected_encounters
    )
    return linking.AircraftOperationalContext(
        aircraft=aircraft,
        aircraft_is_current=current_set.is_current_aircraft(aircraft, now_epoch),
        projection=projection,
        projection_is_current=projection_is_current,
        encounters=encounters,
        projection_link=projection_link,
        retrieval=tuple(retrieval),
    )


def bounded_decision_context_builder(
    query_limit: int = DECISION_BOUNDED_QUERY_LIMIT,
) -> Callable[..., Any]:
    """Return a Decision context builder that never Scans."""

    _closed()

    def builder(tables, aircraft_id, *, now_epoch):
        wrapped = wrap_decision_tables_reject_scan(tables)
        return build_aircraft_operational_context_bounded(
            wrapped,
            aircraft_id,
            now_epoch=now_epoch,
            query_limit=query_limit,
        )

    return builder


__all__ = [
    "DECISION_BOUNDED_QUERY_LIMIT",
    "DECISION_SCAN_REMOVED",
    "EMPTY_BOUNDED_PAGE_IS_VERIFIED_ZERO",
    "SPRINT5_DECISION_READS_SCHEMA",
    "bounded_decision_context_builder",
    "build_aircraft_operational_context_bounded",
    "wrap_decision_tables_reject_scan",
]
