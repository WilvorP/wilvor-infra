"""LO1 CURRENT Live Ops projection tests."""

from __future__ import annotations

import copy

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops_model_contracts import LIVE_OPS_PROJECTION_JSON_MAX_BYTES
from wilvor_ai.live_ops_tool_projection import (
    LiveOpsProjectionError,
    LiveOpsToolProjection,
    project_live_ops_tool_result,
)


AS_OF = "2026-09-06T03:30:00Z"


def _evidence(tool_call_id="tc-1", limitations=("FRESHNESS_NOT_ESTABLISHED",)):
    return Evidence(
        source="wilvor.active_hazards",
        source_records=(),
        query_timestamp_utc=AS_OF,
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.MEDIUM,
        limitations=limitations,
        tool_call_id=tool_call_id,
        temporal_scope=TemporalScope.CURRENT,
    )


def _result(tool_name, data, *, status=ToolResultStatus.SUCCESS, limitations=None, tool_call_id="tc-1"):
    limits = limitations or (
        "FRESHNESS_NOT_ESTABLISHED",
        "DynamoDB reads are independently observed; this composition is not a transactional cross-table snapshot.",
    )
    evidence = (_evidence(tool_call_id),) if status in {ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL} else ()
    if status is ToolResultStatus.PARTIAL and "LINEAGE_MISMATCH_PRESENT" not in limits:
        limits = limits + ("LINEAGE_MISMATCH_PRESENT",)
    return ToolResult(
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        status=status,
        temporal_scope=TemporalScope.CURRENT,
        data=data,
        evidence=evidence,
        as_of_utc=AS_OF,
        limitations=limits,
        correlation_id="corr-1",
    )


def test_current_accepted_historical_rejected():
    current = _result(
        "get_observed_network_state",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "observed": True,
            "complete_network_snapshot": False,
            "current_aircraft_count": 0,
            "current_hazard_count": 0,
            "current_encounter_count": 0,
            "current_risk_count": 0,
            "current_recommendation_count": 0,
            "current_alert_count": 0,
            "current_aircraft_ids": [],
            "current_hazard_ids": [],
            "current_encounter_ids": [],
            "current_risk_ids": [],
            "current_recommendation_ids": [],
            "current_alert_ids": [],
        },
    )
    projection = project_live_ops_tool_result(current)
    assert projection.temporal_scope is TemporalScope.CURRENT
    assert projection.data["complete_network_snapshot"] is False
    assert "FRESHNESS_NOT_ESTABLISHED" in projection.limitations

    historical = ToolResult(
        tool_name="summarize_historical_encounters",
        tool_call_id="hist-1",
        status=ToolResultStatus.UNKNOWN,
        temporal_scope=TemporalScope.HISTORICAL,
        data={"result": {}},
        evidence=(),
        as_of_utc=AS_OF,
        limitations=("coverage",),
    )
    with pytest.raises(LiveOpsProjectionError) as exc:
        project_live_ops_tool_result(historical)
    assert "projection_requires_current_scope" in exc.value.errors


def test_raw_result_not_mutated_by_projection():
    data = {
        "as_of_epoch": 1,
        "as_of_utc": AS_OF,
        "impact_count": 1,
        "unique_aircraft_count": 1,
        "unique_aircraft_ids": ["ac1"],
        "impacts": [
            {
                "hazard_id": "H1",
                "source_version": "v1",
                "encounter_id": "E1",
                "aircraft_id": "ac1",
                "aircraft": {"aircraft_id": "ac1"},
                "projection": {"projection_id": "P1"},
                "encounter_context": {
                    "encounter": {
                        "inside_now": False,
                        "exact_intersection_confirmed": False,
                    },
                    "recommendations": [
                        {
                            "recommendation_id": "R1",
                            "preferred_airport_id": "KLAX",
                            "alternative_actions": ["divert"],
                            "advisory_notice": "do not fly",
                            "primary_action_details": {"heading": 270},
                        }
                    ],
                },
            }
        ],
        "operationally_unevaluated": [],
        "geospatial_unevaluated": [],
    }
    original = copy.deepcopy(data)
    result = _result("search_current_impacts", data)
    projection = project_live_ops_tool_result(result)
    projection.data["impacts"] = []
    assert result.data == original
    assert result.data["impacts"][0]["encounter_context"]["recommendations"][0]["preferred_airport_id"] == "KLAX"


def test_nested_recommendation_details_do_not_leak():
    result = _result(
        "search_current_impacts",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "impact_count": 1,
            "impacts": [
                {
                    "hazard_id": "H1",
                    "source_version": "v1",
                    "encounter_id": "E1",
                    "aircraft_id": "ac1",
                    "aircraft": {"aircraft_id": "ac1", "latitude": 34.0},
                    "projection": {"projection_id": "P1", "projection_horizon_min": 20},
                    "encounter_context": {
                        "encounter": {
                            "encounter_id": "E1",
                            "inside_now": False,
                            "exact_intersection_confirmed": False,
                            "corridor_intersects": True,
                        },
                        "recommendations": [
                            {
                                "recommendation_id": "R1",
                                "recommendation_status": "ACTIVE",
                                "primary_action_type": "MONITOR",
                                "preferred_airport_id": "KLAX",
                                "preferred_airport_assessment_id": "A1",
                                "preferred_airport_score": 9,
                                "alternative_actions": ["DIVERT"],
                                "advisory_notice": "land now",
                                "primary_action_details": {"alt": 100},
                            }
                        ],
                        "alerts": [{"alert_id": "AL1", "preferred_airport_id": "KLAX"}],
                    },
                }
            ],
        },
    )
    projection = project_live_ops_tool_result(result)
    encoded = str(projection.to_dict())
    assert "preferred_airport" not in encoded
    assert "alternative_actions" not in encoded
    assert "advisory_notice" not in encoded
    assert "primary_action_details" not in encoded
    impact = projection.data["impacts"][0]
    assert impact["aircraft"]["aircraft_id"] == "ac1"
    assert impact["projection"]["projection_id"] == "P1"
    assert impact["encounter_context"]["encounter"]["inside_now"] is False
    assert impact["encounter_context"]["encounter"]["exact_intersection_confirmed"] is False
    assert projection.data["unique_hazard_count"] == 1


def test_list_truncation_reports_exact_metadata():
    impacts = [
        {
            "hazard_id": f"H{index}",
            "source_version": "v1",
            "encounter_id": f"E{index}",
            "aircraft_id": f"ac{index}",
        }
        for index in range(40)
    ]
    result = _result(
        "search_current_impacts",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "impact_count": 40,
            "unique_aircraft_count": 40,
            "unique_aircraft_ids": [f"ac{index}" for index in range(40)],
            "impacts": impacts,
        },
    )
    projection = project_live_ops_tool_result(result)
    assert projection.data["impact_count"] == 40
    assert projection.data["impacts_total_count"] == 40
    assert projection.data["impacts_visible_count"] == 25
    assert projection.data["impacts_omitted_count"] == 15
    assert projection.data["impacts_truncated"] is True
    assert len(projection.data["impacts"]) == 25
    assert projection.data["unique_hazard_count"] == 40


def test_unique_hazard_count_is_query_scoped():
    result = _result(
        "search_current_impacts",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "impact_count": 3,
            "impacts": [
                {"hazard_id": "H1", "source_version": "v1", "encounter_id": "E1", "aircraft_id": "a"},
                {"hazard_id": "H1", "source_version": "v2", "encounter_id": "E2", "aircraft_id": "b"},
                {"hazard_id": "H2", "source_version": "v1", "encounter_id": "E3", "aircraft_id": "c"},
            ],
        },
    )
    projection = project_live_ops_tool_result(result)
    assert projection.data["unique_hazard_count"] == 2
    assert projection.data["impact_count"] == 3


def test_size_bound_reduces_optional_records():
    huge_id = "H" + ("x" * 500)
    impacts = [
        {
            "hazard_id": f"{huge_id}{index}",
            "source_version": "v" + ("y" * 200),
            "encounter_id": f"E{index}" + ("z" * 200),
            "aircraft_id": f"ac{index}" + ("w" * 200),
            "aircraft": {"aircraft_id": f"ac{index}" + ("w" * 200)},
            "projection": {"projection_id": f"P{index}" + ("q" * 200)},
        }
        for index in range(25)
    ]
    result = _result(
        "search_current_impacts",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "impact_count": 25,
            "impacts": impacts,
        },
    )
    projection = project_live_ops_tool_result(result)
    encoded = projection.to_dict()
    import json

    raw = json.dumps(encoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    assert len(raw.encode("utf-8")) <= LIVE_OPS_PROJECTION_JSON_MAX_BYTES
    assert projection.data["impact_count"] == 25
    assert projection.data["impacts_truncated"] is True or projection.data["impacts_visible_count"] <= 25
    assert projection.data["impacts_visible_count"] == len(projection.data["impacts"])
    assert projection.data["impacts_visible_count"] < 25 or projection.data["impacts_omitted_count"] == 0


def test_mandatory_metadata_overflow_fails_closed():
    limitations = tuple(f"LIMITATION_{index}_" + ("A" * 200) for index in range(200))
    result = _result(
        "get_observed_network_state",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "observed": True,
            "complete_network_snapshot": False,
            "current_aircraft_count": 0,
            "current_hazard_count": 0,
            "current_encounter_count": 0,
            "current_risk_count": 0,
            "current_recommendation_count": 0,
            "current_alert_count": 0,
            "current_aircraft_ids": [],
            "current_hazard_ids": [],
            "current_encounter_ids": [],
            "current_risk_ids": [],
            "current_recommendation_ids": [],
            "current_alert_ids": [],
        },
        limitations=limitations,
    )
    with pytest.raises(LiveOpsProjectionError) as exc:
        project_live_ops_tool_result(result)
    assert "projection_mandatory_metadata_overflow" in exc.value.errors


def test_observed_projection_and_encounter_remain_separate():
    result = _result(
        "get_aircraft_operational_context",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "found": True,
            "aircraft_id": "ac1",
            "aircraft_is_current": True,
            "aircraft": {"aircraft_id": "ac1", "latitude": 10.0, "callsign": "UAL1"},
            "projection_is_current": True,
            "projection": {"projection_id": "P1", "projection_horizon_min": 30},
            "encounters": [
                {
                    "encounter_is_current": True,
                    "encounter": {
                        "encounter_id": "E1",
                        "hazard_id": "H1",
                        "inside_now": False,
                        "exact_intersection_confirmed": False,
                    },
                }
            ],
        },
    )
    projection = project_live_ops_tool_result(result)
    assert "aircraft" in projection.data
    assert "projection" in projection.data
    assert projection.data["aircraft"]["latitude"] == 10.0
    assert projection.data["projection"]["projection_id"] == "P1"
    assert projection.data["encounters"][0]["encounter"]["inside_now"] is False
    assert "flight_plan" not in str(projection.to_dict())


def _hazard_context_data(**overrides):
    data = {
        "as_of_epoch": 1,
        "as_of_utc": AS_OF,
        "found": True,
        "hazard_id": "H1",
        "source_version": "v1",
        "hazard_is_lifecycle_active": True,
        "hazard_is_current": True,
        "current_impacts_evaluated": True,
        "hazard": {"hazard_id": "H1", "source_version": "v1", "status": "ACTIVE"},
        "impacts": [],
        "retrieval": [],
    }
    data.update(overrides)
    return data


def test_hazard_lifecycle_flag_uses_mapped_field_not_status():
    active_current = project_live_ops_tool_result(
        _result("get_hazard_operational_context", _hazard_context_data())
    )
    assert active_current.data["hazard_is_lifecycle_active"] is True
    assert active_current.data["hazard_is_current"] is True
    assert "lifecycle_active" not in active_current.data

    active_not_current = project_live_ops_tool_result(
        _result(
            "get_hazard_operational_context",
            _hazard_context_data(hazard_is_lifecycle_active=True, hazard_is_current=False),
        )
    )
    assert active_not_current.data["hazard_is_lifecycle_active"] is True
    assert active_not_current.data["hazard_is_current"] is False

    missing = _hazard_context_data()
    del missing["hazard_is_lifecycle_active"]
    with pytest.raises(LiveOpsProjectionError) as exc:
        project_live_ops_tool_result(
            _result("get_hazard_operational_context", missing)
        )
    assert "missing_hazard_is_lifecycle_active" in exc.value.errors

    malformed = _hazard_context_data(hazard_is_lifecycle_active="ACTIVE")
    with pytest.raises(LiveOpsProjectionError) as malformed_exc:
        project_live_ops_tool_result(
            _result("get_hazard_operational_context", malformed)
        )
    assert "invalid_hazard_is_lifecycle_active" in malformed_exc.value.errors


def test_airport_currentness_is_not_weather_freshness():
    result = _result(
        "get_airport_operational_context",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "found": True,
            "airport_id": "KSEA",
            "airport_is_current": False,
            "airport": {
                "airport_id": "KSEA",
                "metar_freshness_status": "FRESH",
                "taf_freshness_status": "FRESH",
                "flight_category": "VFR",
            },
            "latest_metar": {"station_id": "KSEA", "freshness_status": "FRESH"},
            "retrieval": [],
        },
    )
    projection = project_live_ops_tool_result(result)
    assert projection.data["found"] is True
    assert projection.data["airport_is_current"] is False
    assert projection.data["airport"]["metar_freshness_status"] == "FRESH"

    missing = {
        "as_of_epoch": 1,
        "as_of_utc": AS_OF,
        "found": True,
        "airport_id": "KSEA",
        "airport": {"airport_id": "KSEA"},
    }
    with pytest.raises(LiveOpsProjectionError) as exc:
        project_live_ops_tool_result(
            _result("get_airport_operational_context", missing)
        )
    assert "missing_airport_is_current" in exc.value.errors


def test_context_found_is_required_and_not_found_cannot_be_found():
    missing = _hazard_context_data()
    del missing["found"]
    with pytest.raises(LiveOpsProjectionError) as missing_exc:
        project_live_ops_tool_result(
            _result("get_hazard_operational_context", missing)
        )
    assert "missing_found" in missing_exc.value.errors

    with pytest.raises(LiveOpsProjectionError) as malformed_exc:
        project_live_ops_tool_result(
            _result(
                "get_airport_operational_context",
                {
                    "as_of_epoch": 1,
                    "as_of_utc": AS_OF,
                    "found": "yes",
                    "airport_is_current": True,
                    "airport_id": "KSEA",
                },
            )
        )
    assert "invalid_found" in malformed_exc.value.errors

    not_found = project_live_ops_tool_result(
        _result(
            "get_hazard_operational_context",
            _hazard_context_data(
                found=False,
                hazard_is_lifecycle_active=False,
                hazard_is_current=False,
                hazard=None,
            ),
            status=ToolResultStatus.NOT_FOUND,
        )
    )
    assert not_found.data["found"] is False
    assert not_found.status is ToolResultStatus.NOT_FOUND

    with pytest.raises(LiveOpsProjectionError) as contradictory:
        project_live_ops_tool_result(
            _result(
                "get_hazard_operational_context",
                _hazard_context_data(found=True),
                status=ToolResultStatus.NOT_FOUND,
            )
        )
    assert "not_found_cannot_be_found" in contradictory.value.errors


def test_encounter_geometry_flags_stay_nested_on_mapping_shaped_search():
    result = _result(
        "search_current_encounters",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "callsign": "UAL1",
            "callsign_matches": ["ac1"],
            "encounter_count": 1,
            "encounters": [
                {
                    "hazard_id": "H1",
                    "source_version": "v1",
                    "encounter_id": "E1",
                    "aircraft_id": "ac1",
                    "aircraft_is_current": True,
                    "aircraft": {"aircraft_id": "ac1", "latitude": 34.0},
                    "encounter_context": {
                        "encounter_is_current": True,
                        "encounter": {
                            "encounter_id": "E1",
                            "aircraft_id": "ac1",
                            "projection_id": "P1",
                            "hazard_id": "H1",
                            "inside_now": True,
                            "exact_intersection_confirmed": False,
                            "corridor_intersects": True,
                        },
                    },
                }
            ],
            "operationally_unevaluated": [],
            "retrieval": [],
        },
    )
    projection = project_live_ops_tool_result(result)
    row = projection.data["encounters"][0]
    assert "inside_now" not in row
    assert "exact_intersection_confirmed" not in row
    encounter = row["encounter_context"]["encounter"]
    assert encounter["inside_now"] is True
    assert encounter["exact_intersection_confirmed"] is False
    assert row["aircraft"]["latitude"] == 34.0


def test_region_spatial_capability_and_retrieval_limit_are_preserved():
    result = _result(
        "search_current_hazards",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "region": {
                "resolved": True,
                "query": "California",
                "region_type": "ADMIN_1",
                "spatial_supported": False,
                "geometry_type": "MULTIPOLYGON",
            },
            "match_count": 0,
            "hazards": [],
            "geospatial_unevaluated": [
                {
                    "hazard_id": "H1",
                    "source_version": "v1",
                    "spatial_status": "UNEVALUATED",
                    "limitations": ["GEOSPATIAL_UNEVALUATED_PRESENT"],
                }
            ],
            "non_intersecting_count": 0,
            "retrieval": [
                {
                    "source": "query_active_hazard_candidates",
                    "coverage": "FULL_QUERY",
                    "consistency": "EVENTUAL",
                    "limit": 10,
                    "limitations": ["A recently written record may not yet be visible."],
                }
            ],
        },
        status=ToolResultStatus.PARTIAL,
        limitations=(
            "FRESHNESS_NOT_ESTABLISHED",
            "GEOSPATIAL_UNEVALUATED_PRESENT",
            "DynamoDB reads are independently observed; this composition is not a transactional cross-table snapshot.",
        ),
    )
    projection = project_live_ops_tool_result(result)
    assert projection.status is ToolResultStatus.PARTIAL
    assert projection.data["region"]["spatial_supported"] is False
    assert projection.data["region"]["geometry_type"] == "MULTIPOLYGON"
    assert projection.data["match_count"] == 0
    assert projection.data["geospatial_unevaluated"][0]["hazard_id"] == "H1"
    assert projection.data["retrieval"][0]["limit"] == 10
    assert any(
        "not a transactional cross-table snapshot" in item
        for item in projection.limitations
    )
    network = project_live_ops_tool_result(
        _result(
            "get_observed_network_state",
            {
                "as_of_epoch": 1,
                "as_of_utc": AS_OF,
                "observed": True,
                "complete_network_snapshot": True,
                "current_aircraft_count": 0,
                "current_hazard_count": 0,
                "current_encounter_count": 0,
                "current_risk_count": 0,
                "current_recommendation_count": 0,
                "current_alert_count": 0,
                "current_aircraft_ids": [],
                "current_hazard_ids": [],
                "current_encounter_ids": [],
                "current_risk_ids": [],
                "current_recommendation_ids": [],
                "current_alert_ids": [],
            },
        )
    )
    assert network.data["complete_network_snapshot"] is False


def test_projection_data_is_required_on_constructor_and_from_dict():
    with pytest.raises(ContractValidationError) as constructor_exc:
        LiveOpsToolProjection(
            tool_name="get_observed_network_state",
            tool_call_id="lo-1",
            status=ToolResultStatus.SUCCESS,
            temporal_scope=TemporalScope.CURRENT,
            as_of_utc=AS_OF,
            data=None,
            limitations=("FRESHNESS_NOT_ESTABLISHED",),
            evidence=(),
        )
    assert "invalid_data" in constructor_exc.value.errors

    valid = LiveOpsToolProjection(
        tool_name="get_observed_network_state",
        tool_call_id="lo-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        as_of_utc=AS_OF,
        data={"observed": True, "complete_network_snapshot": False},
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        evidence=(),
    )
    payload = valid.to_dict()
    del payload["data"]
    with pytest.raises(ContractValidationError):
        LiveOpsToolProjection.from_dict(payload)


def test_clipped_free_text_is_marked_and_limitation_codes_are_not_clipped():
    long_reason = "hydrate mismatch " + ("A" * 300)
    result = _result(
        "search_current_impacts",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "impact_count": 0,
            "impacts": [],
            "operationally_unevaluated": [
                {
                    "encounter_id": "E1",
                    "aircraft_id": "ac1",
                    "hazard_id": "H1",
                    "reason": long_reason,
                    "limitations": ["LINEAGE_MISMATCH_PRESENT"],
                }
            ],
            "retrieval": [],
        },
        limitations=("FRESHNESS_NOT_ESTABLISHED", "LINEAGE_MISMATCH_PRESENT"),
    )
    projection = project_live_ops_tool_result(result)
    row = projection.data["operationally_unevaluated"][0]
    assert row["reason"] == "TEXT_CLIPPED"
    assert row["reason_clipped"] is True
    assert long_reason not in str(projection.to_dict())
    assert "LINEAGE_MISMATCH_PRESENT" in row["limitations"]
    assert "LINEAGE_MISMATCH_PRESENT" in projection.limitations


def test_link_identity_allowlist_omits_injected_diversion_and_geometry():
    result = _result(
        "get_aircraft_operational_context",
        {
            "as_of_epoch": 1,
            "as_of_utc": AS_OF,
            "found": True,
            "aircraft_id": "ac1",
            "aircraft_is_current": True,
            "aircraft": {"aircraft_id": "ac1"},
            "projection_is_current": True,
            "projection": {"projection_id": "P1"},
            "projection_link": {
                "state": "PRESENT",
                "kind": "VERSIONED",
                "selected_identity": [
                    ["projection_id", "P1"],
                    ["aircraft_id", "ac1"],
                    ["preferred_airport_id", "KLAX"],
                    ["geometry", {"type": "Polygon", "coordinates": [[[0, 0]]] }],
                ],
                "observed_identity": [["projection_id", "P1"]],
            },
            "encounters": [],
            "retrieval": [],
        },
    )
    projection = project_live_ops_tool_result(result)
    identity = projection.data["projection_link"]["selected_identity"]
    keys = {item[0] for item in identity}
    assert keys == {"projection_id", "aircraft_id"}
    encoded = str(projection.to_dict())
    assert "preferred_airport" not in encoded
    assert "Polygon" not in encoded
