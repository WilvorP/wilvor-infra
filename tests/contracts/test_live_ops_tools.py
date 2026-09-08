"""Phase 1E Live Operations adapter contract tests."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import (
    AgentAuthorityMode,
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops import (
    LIVE_OPS_TOOLS,
    LiveOpsCall,
    find_aircraft_by_callsign,
    get_aircraft_operational_context,
    get_airport_operational_context,
    get_hazard_operational_context,
    get_observed_network_state,
    search_current_encounters,
    search_current_hazards,
    search_current_impacts,
)
from wilvor_ai.live_ops_mapping import (
    ALERT_EVIDENCE_IDENTITY_UNAVAILABLE,
    FRESHNESS_NOT_ESTABLISHED,
    NOT_RETURNED_BY_FILTERED_DISCOVERY,
    SOURCE_ALERTS,
    SOURCE_COORDINATES,
    SOURCE_HAZARDS,
    SOURCE_PROJECTION,
    SOURCE_REGIONS,
    SOURCE_TAF_PERIODS,
    LiveOpsMappingError,
    collect_retrieval,
    extract_alert,
    extract_hazard,
)
from wilvor_operational import linking
from wilvor_operational.query import QUERY_SELECTION_REQUIRED


def _call(tables, tool_call_id=fixtures.TOOL_CALL_ID):
    return LiveOpsCall(
        tables=tables,
        now_epoch=fixtures.NOW,
        tool_call_id=tool_call_id,
        correlation_id=fixtures.CORRELATION_ID,
    )


def _by_source(result):
    return {item.source: item for item in result.evidence}


def _round_trip(result):
    restored = ToolResult.from_dict(result.to_dict())
    assert restored == result
    return restored


def _assert_phase1e_freshness(result):
    assert result.status is not ToolResultStatus.STALE
    for item in result.evidence:
        assert item.freshness_status is FreshnessStatus.UNKNOWN
        assert FRESHNESS_NOT_ESTABLISHED in item.limitations
        assert item.temporal_scope is TemporalScope.CURRENT
        assert item.confidence is not ConfidenceLevel.LOW


def test_unknown_retrieval_source_fails_loudly():
    observation = linking.RetrievalObservation(
        source="not_a_real_reader",
        coverage=linking.Coverage.EXACT_PK,
        consistency=linking.Consistency.CONSISTENT,
        limit=None,
    )
    with pytest.raises(LiveOpsMappingError) as exc_info:
        collect_retrieval(SimpleNamespace(retrieval=(observation,)))
    assert exc_info.value.code == "unknown_retrieval_source:not_a_real_reader"


def test_nested_retrieval_is_unioned_and_unknown_nested_source_fails():
    parent = linking.exact_pk_observation("get_hazard_record")
    nested = linking.query_observation(
        "query_hazard_coordinate_rows",
        consistency=linking.Consistency.CONSISTENT,
    )
    root = SimpleNamespace(
        retrieval=(parent,),
        geospatial_unevaluated=(SimpleNamespace(retrieval=(nested,)),),
    )
    collected = collect_retrieval(root)
    assert [item.source for item in collected] == [
        "get_hazard_record",
        "query_hazard_coordinate_rows",
    ]

    bad = linking.RetrievalObservation(
        source="mystery_scan",
        coverage=linking.Coverage.FULL_SCAN,
        consistency=linking.Consistency.EVENTUAL,
        limit=None,
    )
    with pytest.raises(LiveOpsMappingError) as exc_info:
        collect_retrieval(
            SimpleNamespace(
                retrieval=(parent,),
                intersecting=(SimpleNamespace(retrieval=(bad,)),),
            )
        )
    assert "unknown_retrieval_source:mystery_scan" in exc_info.value.code


def test_evidence_follows_collected_retrieval_not_kwargs():
    tables = fixtures.tables(
        hazards=fixtures.FlexibleTable(
            records={"H1": fixtures.hazard("H1")},
            query_items={"ACTIVE": [fixtures.hazard("H1")]},
        )
    )
    result = search_current_hazards(
        _call(tables),
        product_type="SIGMET",
        hazard_type="CONVECTION",
    )
    sources = set(_by_source(result))
    assert SOURCE_HAZARDS in sources
    assert SOURCE_COORDINATES not in sources
    assert SOURCE_REGIONS not in sources
    assert result.data["region"] is None


def test_region_hazard_search_emits_hazards_coordinates_and_census():
    result = search_current_hazards(
        _call(fixtures.california_tables()),
        region="California",
        product_type="SIGMET",
    )
    sources = _by_source(result)
    assert SOURCE_HAZARDS in sources
    assert SOURCE_COORDINATES in sources
    assert SOURCE_REGIONS in sources
    coord_ids = [item.record_id for item in sources[SOURCE_COORDINATES].source_records]
    assert "H1#v1" in coord_ids
    assert all("#" in item and item.count("#") == 1 for item in coord_ids)
    assert len(coord_ids) == len(set(coord_ids))
    assert len(coord_ids) < 20
    census = sources[SOURCE_REGIONS].source_records[0]
    assert census.record_id == "CA"
    assert ":" in (census.source_version or "")
    assert result.data["region"]["resolved"] is True
    assert result.data["region"]["code"] == "CA"
    assert "coordinates" not in result.data["region"]
    assert all(item["hazard_is_current"] is True for item in result.data["hazards"])
    assert all(item["spatial_status"] == "INTERSECTS" for item in result.data["hazards"])
    hazard_ids = {item["hazard"]["hazard_id"] for item in result.data["hazards"]}
    assert "H1" in hazard_ids
    assert "outside" not in hazard_ids
    assert result.data["non_intersecting_count"] >= 1
    assert any(item["hazard_id"] == "uneval" for item in result.data["geospatial_unevaluated"])
    assert any(item["hazard_id"] == "stale" for item in result.data["rejected_hazard_candidates"])
    assert result.status is ToolResultStatus.PARTIAL
    _assert_phase1e_freshness(result)
    _round_trip(result)


def test_region_impact_search_emits_hazards_coordinates_census_and_full_payload():
    result = search_current_impacts(
        _call(fixtures.california_tables()),
        region="California",
        product_type="SIGMET",
    )
    sources = _by_source(result)
    assert SOURCE_HAZARDS in sources
    assert SOURCE_COORDINATES in sources
    assert SOURCE_REGIONS in sources
    assert {item["encounter_id"] for item in result.data["impacts"]} >= {"H1-A", "H1-B", "H2-A"}
    impact = next(item for item in result.data["impacts"] if item["encounter_id"] == "H1-A")
    assert impact["aircraft"]["aircraft_id"] == "a"
    assert impact["aircraft"]["on_ground"] is False
    assert impact["encounter_context"]["hazard"]["hazard_id"] == "H1"
    assert impact["encounter_context"]["risk"]["risk_id"] == "risk-1"
    assert impact["encounter_context"]["risk"]["reasons"] == ["overlap"]
    assert [item["recommendation_id"] for item in impact["encounter_context"]["recommendations"]] == [
        "rec-1",
        "rec-2",
    ]
    assert [item["alert_id"] for item in impact["encounter_context"]["alerts"]] == [
        "alert-risk",
        "alert-rec",
    ]
    assert all("fingerprint" not in item for item in impact["encounter_context"]["alerts"])
    alert_ids = [item.record_id for item in sources[SOURCE_ALERTS].source_records]
    assert "fp-alert-risk" in alert_ids
    assert "alert-risk" not in alert_ids
    assert impact["aircraft_is_current"] is True
    _assert_phase1e_freshness(result)
    _round_trip(result)


def test_non_region_paths_omit_coordinate_and_census_evidence():
    tables = fixtures.california_tables()
    hazards = search_current_hazards(_call(tables), hazard_ids=["H1"])
    impacts = search_current_impacts(_call(tables), hazard_ids=["H1"])
    for result in (hazards, impacts):
        sources = set(_by_source(result))
        assert SOURCE_COORDINATES not in sources
        assert SOURCE_REGIONS not in sources
        assert result.data["region"] is None


def test_network_state_emits_projection_evidence_with_empty_records():
    result = get_observed_network_state(_call(fixtures.california_tables()))
    sources = _by_source(result)
    assert SOURCE_PROJECTION in sources
    assert sources[SOURCE_PROJECTION].source_records == ()
    assert result.data["observed"] is True
    assert result.data["complete_network_snapshot"] is False
    assert result.data["current_aircraft_count"] == len(result.data["current_aircraft_ids"])
    assert result.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in result.evidence)
    _assert_phase1e_freshness(result)
    _round_trip(result)


def test_alert_fingerprint_identity_and_no_alert_id_fallback():
    tables = fixtures.california_tables()
    tables.alerts.scan_items = [
        {
            "alert_id": "alert-risk",
            "risk_id": "risk-1",
            "alert_state": "NEW",
            "valid_until_utc": fixtures.FUTURE,
        }
    ]
    result = search_current_impacts(
        _call(tables),
        region="California",
        product_type="SIGMET",
    )
    impact = next(item for item in result.data["impacts"] if item["encounter_id"] == "H1-A")
    assert impact["encounter_context"]["alerts"][0]["alert_id"] == "alert-risk"
    assert "fingerprint" not in impact["encounter_context"]["alerts"][0]
    alerts = _by_source(result)[SOURCE_ALERTS]
    assert alerts.source_records == ()
    assert ALERT_EVIDENCE_IDENTITY_UNAVAILABLE in alerts.limitations
    assert ALERT_EVIDENCE_IDENTITY_UNAVAILABLE in result.limitations
    assert extract_alert({"alert_id": "alert-risk", "fingerprint": "secret"})["alert_id"] == "alert-risk"
    assert "fingerprint" not in extract_alert({"alert_id": "alert-risk", "fingerprint": "secret"})


def test_taf_period_composite_evidence_id_and_full_period_payload():
    result = get_airport_operational_context(_call(fixtures.airport_tables()), "KSEA")
    sources = _by_source(result)
    assert SOURCE_TAF_PERIODS in sources
    assert sources[SOURCE_TAF_PERIODS].source_records[0].record_id == "KSEA#T1|P1"
    period = result.data["latest_taf_periods"][0]
    assert period["taf_version_key"] == "KSEA#T1"
    assert period["period_key"] == "P1"
    assert period["weather_codes"] == []
    assert period["clouds"] == []
    assert period["not_decoded"] is False
    assert period["wind_speed_kt"] == 0
    assert result.data["latest_metar"]["temperature_c"] == 12
    assert "metar" not in result.data
    assert "taf" not in result.data
    assert result.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in result.evidence)
    _round_trip(result)


def test_filtered_explicit_absent_id_is_not_missing():
    tables = fixtures.tables(
        hazards=fixtures.FlexibleTable(records={"H1": fixtures.hazard("H1")})
    )
    filtered = search_current_hazards(
        _call(tables),
        hazard_ids=["NOPE"],
        product_type="SIGMET",
    )
    assert filtered.data["hazards"] == []
    assert filtered.data["rejected_hazard_selections"][0]["reason"] == (
        NOT_RETURNED_BY_FILTERED_DISCOVERY
    )
    assert filtered.status is ToolResultStatus.SUCCESS

    unfiltered = search_current_hazards(_call(tables), hazard_ids=["NOPE"])
    assert unfiltered.data["rejected_hazard_selections"][0]["reason"] == "MISSING"

    filtered_impacts = search_current_impacts(
        _call(tables),
        hazard_ids=["NOPE"],
        hazard_type="CONVECTION",
    )
    assert filtered_impacts.data["rejected_hazard_selections"][0]["reason"] == (
        NOT_RETURNED_BY_FILTERED_DISCOVERY
    )
    unfiltered_impacts = search_current_impacts(_call(tables), hazard_ids=["NOPE"])
    assert unfiltered_impacts.data["rejected_hazard_selections"][0]["reason"] == "MISSING"


def test_unresolved_region_is_not_found_and_resolved_unevaluated_is_partial():
    unresolved = search_current_hazards(
        _call(fixtures.tables()),
        region="NotAState",
    )
    assert unresolved.status is ToolResultStatus.NOT_FOUND
    assert SOURCE_REGIONS in _by_source(unresolved)
    assert unresolved.data["region"]["resolved"] is False
    assert all(item.confidence is ConfidenceLevel.UNKNOWN for item in unresolved.evidence)

    unsupported = search_current_impacts(
        _call(fixtures.tables()),
        region="NotAState",
    )
    assert unsupported.status is ToolResultStatus.NOT_FOUND

    uneval = fixtures.hazard("uneval")
    partial = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(
                    records={"uneval": uneval},
                    query_items={"ACTIVE": [uneval]},
                ),
                coordinates=fixtures.FlexibleTable(query_items={"uneval#v1": []}),
            )
        ),
        region="California",
        product_type="SIGMET",
    )
    assert partial.data["hazards"] == []
    assert partial.data["geospatial_unevaluated"]
    assert partial.status is ToolResultStatus.PARTIAL
    assert all(item.confidence is ConfidenceLevel.UNKNOWN for item in partial.evidence)


def test_confirmed_zero_success_vs_partial_zero():
    empty = search_current_hazards(
        _call(fixtures.tables(hazards=fixtures.FlexibleTable(query_items={"ACTIVE": []}))),
        product_type="SIGMET",
    )
    assert empty.data["hazards"] == []
    assert empty.data["geospatial_unevaluated"] == []
    assert empty.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in empty.evidence)

    region_empty = search_current_hazards(
        _call(fixtures.tables(hazards=fixtures.FlexibleTable(query_items={"ACTIVE": []}))),
        region="California",
        product_type="SIGMET",
    )
    assert region_empty.data["hazards"] == []
    assert region_empty.data["geospatial_unevaluated"] == []
    assert region_empty.status is ToolResultStatus.SUCCESS

    missing_selector = search_current_impacts(_call(fixtures.tables()))
    assert missing_selector.status is ToolResultStatus.UNKNOWN
    assert QUERY_SELECTION_REQUIRED in missing_selector.limitations


def test_success_medium_unknown_freshness_and_no_high_for_eventual_or_no_snapshot():
    gsi = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(
                    records={"H1": fixtures.hazard("H1")},
                    query_items={"ACTIVE": [fixtures.hazard("H1")]},
                )
            )
        )
    )
    assert gsi.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in gsi.evidence)
    _assert_phase1e_freshness(gsi)

    context = get_aircraft_operational_context(
        _call(fixtures.california_tables()),
        "a",
    )
    assert context.status is ToolResultStatus.SUCCESS
    assert context.data["aircraft_is_current"] is True
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in context.evidence)
    assert linking.NO_SNAPSHOT_LIMITATION in context.limitations

    hazard_context = get_hazard_operational_context(
        _call(fixtures.california_tables()),
        "H1",
    )
    assert hazard_context.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in hazard_context.evidence)


def test_high_only_for_single_consistent_non_snapshot_hit():
    result = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(records={"H1": fixtures.hazard("H1")})
            )
        ),
        hazard_ids=["H1"],
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["match_count"] == 1
    assert all(item.confidence is ConfidenceLevel.HIGH for item in result.evidence)
    _assert_phase1e_freshness(result)

    two = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(
                    records={
                        "H1": fixtures.hazard("H1"),
                        "H2": fixtures.hazard("H2"),
                    }
                )
            )
        ),
        hazard_ids=["H1", "H2"],
    )
    assert two.status is ToolResultStatus.SUCCESS
    assert all(item.confidence is ConfidenceLevel.MEDIUM for item in two.evidence)


def test_search_current_hazards_primary_list_is_current_only():
    current = fixtures.hazard("H1")
    expired = fixtures.hazard("H2", valid_to_epoch=fixtures.NOW - 60)
    result = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(
                    records={"H1": current, "H2": expired}
                )
            )
        ),
        hazard_ids=["H1", "H2"],
    )
    assert [item["hazard"]["hazard_id"] for item in result.data["hazards"]] == ["H1"]
    assert result.data["hazards"][0]["hazard_is_current"] is True
    assert result.data["rejected_hazard_selections"][0]["hazard_id"] == "H2"
    assert result.data["rejected_hazard_selections"][0]["reason"] == "NOT_CURRENT"
    assert extract_hazard({"hazard_id": "H1"})["altitude_bands"] == []
    assert extract_hazard({"hazard_id": "H1"})["severity"] is None


def test_callsign_zero_one_many_and_no_winner():
    empty = find_aircraft_by_callsign(_call(fixtures.tables()), "UAL999")
    assert empty.status is ToolResultStatus.SUCCESS
    assert empty.data["match_count"] == 0
    assert empty.data["matches"] == []

    one_tables = fixtures.tables(
        aircraft=fixtures.FlexibleTable(
            query_pages=[{"Items": [fixtures.aircraft("abc123")]}]
        )
    )
    one = find_aircraft_by_callsign(_call(one_tables), "UAL123")
    assert one.status is ToolResultStatus.SUCCESS
    assert one.data["match_count"] == 1
    assert one.data["matches"][0]["aircraft_is_current"] is True

    many_tables = fixtures.tables(
        aircraft=fixtures.FlexibleTable(
            query_pages=[
                {
                    "Items": [
                        fixtures.aircraft("aaa111"),
                        fixtures.aircraft("ccc333", current=False),
                    ],
                    "LastEvaluatedKey": {"aircraft_id": "aaa111"},
                },
                {"Items": [fixtures.aircraft("bbb222")]},
            ]
        )
    )
    many = find_aircraft_by_callsign(_call(many_tables), "UAL123")
    assert many.status is ToolResultStatus.SUCCESS
    assert many.data["match_count"] == 2
    assert [item["aircraft"]["aircraft_id"] for item in many.data["matches"]] == [
        "aaa111",
        "bbb222",
    ]
    assert "winner" not in many.data
    unknown = find_aircraft_by_callsign(_call(fixtures.tables()), "   ")
    assert unknown.status is ToolResultStatus.UNKNOWN
    _round_trip(many)


def test_known_id_retained_expired_missing_and_mismatch():
    expired = get_aircraft_operational_context(
        _call(fixtures.california_tables()),
        "b",
    )
    assert expired.status is ToolResultStatus.SUCCESS
    assert expired.data["found"] is True
    assert expired.data["aircraft_is_current"] is False

    missing = get_aircraft_operational_context(_call(fixtures.tables()), "missing")
    assert missing.status is ToolResultStatus.NOT_FOUND
    assert missing.data["found"] is False
    assert missing.evidence == ()

    missing_hazard = get_hazard_operational_context(_call(fixtures.tables()), "NOPE")
    assert missing_hazard.status is ToolResultStatus.NOT_FOUND

    mismatch = get_airport_operational_context(
        _call(fixtures.airport_tables(mismatch=True)),
        "KSEA",
    )
    assert mismatch.status is ToolResultStatus.PARTIAL
    assert mismatch.data["found"] is True


def test_search_current_encounters_preserves_callsign_matches_and_no_winner():
    result = search_current_encounters(
        _call(fixtures.california_tables()),
        callsign="UAL123",
    )
    assert result.status in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL}
    assert "winner" not in result.data
    assert isinstance(result.data["callsign_matches"], list)
    missing = search_current_encounters(_call(fixtures.tables()))
    assert missing.status is ToolResultStatus.UNKNOWN


def test_json_round_trip_preserves_empty_none_false_zero_unknown():
    result = search_current_hazards(
        _call(
            fixtures.tables(
                hazards=fixtures.FlexibleTable(records={"H1": fixtures.hazard("H1")})
            )
        ),
        hazard_ids=["H1"],
    )
    payload = result.to_dict()
    restored = ToolResult.from_dict(payload)
    hazard_row = restored.data["hazards"][0]["hazard"]
    assert hazard_row["altitude_bands"] == []
    aircraft = get_aircraft_operational_context(
        _call(fixtures.california_tables()),
        "a",
    )
    assert aircraft.data["aircraft"]["on_ground"] is False
    assert aircraft.data["aircraft"]["freshness_status"] == "UNKNOWN"
    empty = find_aircraft_by_callsign(_call(fixtures.tables()), "NONE")
    assert empty.data["match_count"] == 0
    assert empty.data["matches"] == []
    _round_trip(result)
    _round_trip(aircraft)
    _round_trip(empty)


def test_catalog_has_exactly_eight_read_only_retrieve_tools():
    names = [item.name for item in LIVE_OPS_TOOLS]
    assert names == [
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
        "get_observed_network_state",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
        "find_aircraft_by_callsign",
    ]
    assert len(set(names)) == 8
    for spec in LIVE_OPS_TOOLS:
        assert spec.authority_mode is AgentAuthorityMode.READ_ONLY_ADVISORY
        assert spec.capabilities == (
            AgentCapability.RETRIEVE_DETERMINISTIC_OPERATIONAL_CONTEXT,
        )
        assert spec.description
        assert "route" not in spec.description.lower()
        assert "instead" not in spec.description.lower()


def test_live_ops_call_rejects_blank_id_and_non_int_epoch():
    tables = fixtures.tables()
    with pytest.raises(ContractValidationError):
        LiveOpsCall(tables=tables, now_epoch=fixtures.NOW, tool_call_id=" ")
    with pytest.raises(TypeError):
        LiveOpsCall(tables=tables, now_epoch=True, tool_call_id="tool-1")
    with pytest.raises(TypeError):
        LiveOpsCall(tables=tables, now_epoch=fixtures.NOW + 0.5, tool_call_id="tool-1")


def test_decimal_and_zero_false_null_payload_mapping():
    row = extract_hazard(
        {
            "hazard_id": "H1",
            "geometry_point_count": Decimal("0"),
            "altitude_bands": None,
        }
    )
    assert row["geometry_point_count"] == 0
    assert row["altitude_bands"] == []
    assert row["severity"] is None
