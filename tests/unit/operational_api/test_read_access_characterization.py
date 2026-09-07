"""Golden characterization of pre-Phase-1B Operational API DynamoDB reads.

These tests freeze current retrieval request shapes and compatibility
behavior. They import only the Operational API repository. They do not
import wilvor_operational.access or wilvor_operational.readers.
"""

from __future__ import annotations

import inspect

from boto3.dynamodb.conditions import Attr, Key


NOW = 1_788_661_800  # 2026-09-06T02:30:00Z
NOW_ISO = "2026-09-06T02:30:00Z"
FUTURE = "2026-09-06T04:00:00Z"


class RecordingTable:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])
        self._index = 0

    def _next(self, default):
        if self._index < len(self.responses):
            response = self.responses[self._index]
            self._index += 1
            return response
        return default

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        return self._next({"Item": None})

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self._next({"Items": []})

    def scan(self, **kwargs):
        self.calls.append(("scan", kwargs))
        return self._next({"Items": []})


def condition_shape(expr):
    if expr is None:
        return None

    if hasattr(expr, "get_expression"):
        built = expr.get_expression()
        values = []
        for value in built["values"]:
            if hasattr(value, "get_expression"):
                values.append(condition_shape(value))
            elif hasattr(value, "name") and not isinstance(value, str):
                values.append(("name", value.name))
            else:
                values.append(value)
        return (built["operator"],) + tuple(values)

    return expr


def projection_fields(expression):
    return {part.strip() for part in expression.split(",")}


def install_time(repo, monkeypatch, now=NOW, now_iso=NOW_ISO):
    monkeypatch.setattr(repo.time, "time", lambda: now)
    monkeypatch.setattr(repo, "_now_iso", lambda: now_iso)


def stub_detail_dependents(repo, monkeypatch):
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))
    monkeypatch.setattr(
        repo.PROJECTION_POINTS,
        "query",
        lambda **kwargs: {"Items": []},
    )


def test_exact_aircraft_get_item_is_consistent_and_unfiltered(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable(
        [{"Item": {"aircraft_id": "abc123", "expires_at_epoch": NOW}}]
    )
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)
    stub_detail_dependents(repo, monkeypatch)

    detail = repo.get_aircraft_detail("ABC123")

    assert table.calls == [
        (
            "get_item",
            {
                "Key": {"aircraft_id": "abc123"},
                "ConsistentRead": True,
            },
        )
    ]
    assert detail["aircraft"]["expires_at_epoch"] == NOW


def test_exact_airport_metar_and_taf_get_items_are_consistent(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired_airport = {
        "airport_id": "KSEA",
        "station_id": "KSEA",
        "expires_at_epoch": NOW,
    }
    airports = RecordingTable([{"Item": expired_airport}])
    metar = RecordingTable([{"Item": {"station_id": "KSEA"}}])
    taf = RecordingTable([{"Item": {"station_id": "KSEA"}}])
    periods = RecordingTable([{"Items": [{"period_key": "p1"}]}])
    repo.AIRPORTS = airports
    repo.METAR = metar
    repo.TAF = taf
    repo.TAF_PERIODS = periods
    install_time(repo, monkeypatch)
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])

    detail = repo.get_airport_detail("ksea")

    assert airports.calls == [
        (
            "get_item",
            {
                "Key": {"airport_id": "KSEA"},
                "ConsistentRead": True,
            },
        )
    ]
    assert metar.calls == [
        (
            "get_item",
            {
                "Key": {"station_id": "KSEA"},
                "ConsistentRead": True,
            },
        )
    ]
    assert taf.calls == [
        (
            "get_item",
            {
                "Key": {"station_id": "KSEA"},
                "ConsistentRead": True,
            },
        )
    ]
    assert detail["airport"] is expired_airport
    assert detail["metar"]["station_id"] == "KSEA"
    assert detail["taf"]["station_id"] == "KSEA"


def test_retained_expired_detail_parents_are_returned_without_current_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    expired_aircraft = {"aircraft_id": "abc123", "expires_at_epoch": NOW}
    expired_airport = {
        "airport_id": "KSEA",
        "station_id": "KSEA",
        "expires_at_epoch": NOW,
    }
    repo.AIRCRAFT = RecordingTable([{"Item": expired_aircraft}])
    repo.AIRPORTS = RecordingTable([{"Item": expired_airport}])
    repo.METAR = RecordingTable([{}])
    repo.TAF = RecordingTable([{}])
    repo.TAF_PERIODS = RecordingTable([{"Items": []}])
    install_time(repo, monkeypatch)
    stub_detail_dependents(repo, monkeypatch)
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])

    aircraft_detail = repo.get_aircraft_detail("abc123")
    airport_detail = repo.get_airport_detail("KSEA")

    assert aircraft_detail["aircraft"] is expired_aircraft
    assert airport_detail["airport"] is expired_airport
    assert "FilterExpression" not in repo.AIRCRAFT.calls[0][1]
    assert "FilterExpression" not in repo.AIRPORTS.calls[0][1]


def test_aircraft_callsign_page_uses_newest_first_expiry_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable([{"Items": []}])
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)

    repo.list_aircraft(limit=20, callsign="ual123")

    operation, kwargs = table.calls[0]
    assert operation == "query"
    assert kwargs["IndexName"] == "callsign-position_time_epoch-index"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 20
    assert "ConsistentRead" not in kwargs
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "callsign"),
        "UAL123",
    )
    assert condition_shape(kwargs["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )


def test_aircraft_h3_page_uses_newest_first_expiry_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable([{"Items": []}])
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)

    repo.list_aircraft(limit=15, h3_cell="8428347ffffffff")

    operation, kwargs = table.calls[0]
    assert operation == "query"
    assert kwargs["IndexName"] == "current_h3_cell-position_time_epoch-index"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 15
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "current_h3_cell"),
        "8428347ffffffff",
    )
    assert condition_shape(kwargs["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )


def test_aircraft_scan_page_filters_strict_expiry_and_forwards_start_key(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    start_key = {"aircraft_id": "cursor-1"}
    table = RecordingTable(
        [
            {
                "Items": [],
                "LastEvaluatedKey": start_key,
            },
            {"Items": []},
        ]
    )
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)

    first = repo.list_aircraft(limit=10)
    assert first["nextToken"] is not None

    second = repo.list_aircraft(limit=10, next_token=first["nextToken"])

    first_kwargs = table.calls[0][1]
    second_kwargs = table.calls[1][1]
    assert table.calls[0][0] == "scan"
    assert "IndexName" not in first_kwargs
    assert first_kwargs["Limit"] == 10
    assert condition_shape(first_kwargs["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )
    assert "ExclusiveStartKey" not in first_kwargs
    assert second_kwargs["ExclusiveStartKey"] == start_key
    assert second["nextToken"] is None


def test_airport_impact_and_risk_pages_use_newest_first_expiry_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable([{"Items": []}, {"Items": []}])
    repo.AIRPORTS = table
    install_time(repo, monkeypatch)

    repo.list_airports(limit=25, weather_impact="weather_impacted")
    repo.list_airports(limit=25, weather_risk="high")

    impact_op, impact = table.calls[0]
    risk_op, risk = table.calls[1]
    assert impact_op == "query"
    assert risk_op == "query"
    assert impact["IndexName"] == "weather-impact-updated-index"
    assert risk["IndexName"] == "weather-risk-updated-index"
    assert impact["ScanIndexForward"] is False
    assert risk["ScanIndexForward"] is False
    assert impact["Limit"] == 25
    assert risk["Limit"] == 25
    assert condition_shape(impact["KeyConditionExpression"]) == (
        "=",
        ("name", "weather_impact_status"),
        "WEATHER_IMPACTED",
    )
    assert condition_shape(risk["KeyConditionExpression"]) == (
        "=",
        ("name", "weather_risk_level"),
        "HIGH",
    )
    assert condition_shape(impact["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )
    assert condition_shape(risk["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )


def test_airport_scan_page_filters_strict_expiry(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable([{"Items": []}])
    repo.AIRPORTS = table
    install_time(repo, monkeypatch)

    repo.list_airports(limit=40)

    operation, kwargs = table.calls[0]
    assert operation == "scan"
    assert "IndexName" not in kwargs
    assert kwargs["Limit"] == 40
    assert condition_shape(kwargs["FilterExpression"]) == (
        ">",
        ("name", "expires_at_epoch"),
        NOW,
    )


def test_active_hazard_gsi_query_uses_ready_filter_and_inclusive_valid_to(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    hazards = RecordingTable([{"Items": []}])
    repo.HAZARDS = hazards
    install_time(repo, monkeypatch)

    repo.list_active_hazards(limit=30)

    operation, kwargs = hazards.calls[0]
    assert operation == "query"
    assert kwargs["IndexName"] == "status-valid_to_epoch-index"
    assert kwargs["ScanIndexForward"] is True
    assert kwargs["Limit"] == 30
    assert "ConsistentRead" not in kwargs
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "AND",
        ("=", ("name", "status"), "ACTIVE"),
        (">=", ("name", "valid_to_epoch"), NOW),
    )
    assert condition_shape(kwargs["FilterExpression"]) == (
        "=",
        ("name", "materialization_status"),
        "READY",
    )


def test_projection_and_hazard_index_candidate_scans(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    projections = RecordingTable([{"Items": []}])
    hazards = RecordingTable([{"Items": []}])
    repo.PROJECTIONS = projections
    repo.HAZARDS = hazards
    install_time(repo, monkeypatch)

    repo._load_current_indexes(NOW)

    proj_op, proj = projections.calls[0]
    hazard_op, hazard = hazards.calls[0]
    assert proj_op == "scan"
    assert hazard_op == "scan"
    assert condition_shape(proj["FilterExpression"]) == (
        "AND",
        ("=", ("name", "projection_status"), "READY"),
        (">", ("name", "valid_until_epoch"), NOW),
    )
    assert projection_fields(proj["ProjectionExpression"]) == {
        "aircraft_id",
        "projection_id",
        "generated_at_epoch",
        "valid_until_epoch",
        "projection_status",
    }
    assert condition_shape(hazard["FilterExpression"]) == (
        "AND",
        (
            "AND",
            ("=", ("name", "status"), "ACTIVE"),
            ("=", ("name", "materialization_status"), "READY"),
        ),
        (">=", ("name", "valid_to_epoch"), NOW),
    )
    assert projection_fields(hazard["ProjectionExpression"]) == {
        "hazard_id",
        "source_version",
        "#hazard_status",
        "materialization_status",
        "valid_to_epoch",
    }
    assert hazard["ExpressionAttributeNames"] == {"#hazard_status": "status"}


def test_encounter_candidate_scan_filters_current_states(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    encounters = RecordingTable([{"Items": []}])
    repo.ENCOUNTERS = encounters
    install_time(repo, monkeypatch)
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))

    repo._current_encounter_snapshot()

    operation, kwargs = encounters.calls[0]
    assert operation == "scan"
    assert condition_shape(kwargs["FilterExpression"]) == (
        "IN",
        ("name", "encounter_state"),
        list(repo.current_set.CURRENT_ENCOUNTER_STATES),
    )
    assert projection_fields(kwargs["ProjectionExpression"]) == {
        "encounter_id",
        "aircraft_id",
        "projection_id",
        "hazard_id",
        "hazard_version_key",
        "hazard_source_version",
        "hazard_type",
        "severity",
        "encounter_state",
        "geometry_overlap_status",
        "time_overlap_status",
        "altitude_overlap_status",
        "resolution_reason",
        "resolved_at_utc",
        "freshness_status",
        "corridor_intersects",
        "centerline_intersects",
        "inside_now",
        "exact_intersection_confirmed",
        "trajectory_confidence",
        "matched_h3_cell_count",
        "detected_at_epoch",
        "detected_at_utc",
        "valid_from_utc",
        "valid_to_utc",
        "expires_at_epoch",
        "projection_generated_at_utc",
    }


def test_risk_candidate_scan_has_no_filter_and_projects_selection_fields(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    risks = RecordingTable([{"Items": []}])
    repo.RISKS = risks
    install_time(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )

    repo._latest_current_risks()

    operation, kwargs = risks.calls[0]
    assert operation == "scan"
    assert "FilterExpression" not in kwargs
    assert projection_fields(kwargs["ProjectionExpression"]) == {
        "risk_id",
        "encounter_id",
        "aircraft_id",
        "hazard_id",
        "hazard_type",
        "risk_level",
        "risk_score",
        "confidence",
        "generated_at_epoch",
        "generated_at_utc",
        "valid_until_utc",
    }


def test_recommendation_candidate_scans_use_active_and_iso_string_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    recommendations = RecordingTable([{"Items": []}, {"Items": []}])
    repo.RECOMMENDATIONS = recommendations
    install_time(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_latest_current_risks",
        lambda: {"current_risk_ids": set()},
    )

    repo._active_recommendations()
    repo._current_recommendation_snapshot()

    active_op, active = recommendations.calls[0]
    snapshot_op, snapshot = recommendations.calls[1]
    assert active_op == "scan"
    assert snapshot_op == "scan"
    expected_filter = (
        "AND",
        ("=", ("name", "recommendation_status"), "ACTIVE"),
        (">", ("name", "valid_until_utc"), NOW_ISO),
    )
    assert condition_shape(active["FilterExpression"]) == expected_filter
    assert condition_shape(snapshot["FilterExpression"]) == expected_filter
    assert projection_fields(active["ProjectionExpression"]) == {
        "recommendation_id",
        "risk_id",
        "recommendation_status",
        "valid_until_utc",
        "aircraft_id",
        "hazard_id",
        "risk_level",
        "risk_score",
        "confidence",
        "primary_action_type",
        "preferred_airport_id",
        "preferred_airport_score",
        "created_at_utc",
        "created_at_epoch",
    }
    assert "ProjectionExpression" not in snapshot


def test_alert_candidate_scans_use_active_states_and_iso_string_filter(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    alerts = RecordingTable([{"Items": []}, {"Items": []}])
    repo.ALERTS = alerts
    install_time(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_current_risk_and_recommendation_ids",
        lambda: (set(), set()),
    )

    repo._active_alerts()
    repo._current_alert_snapshot()

    active_op, active = alerts.calls[0]
    snapshot_op, snapshot = alerts.calls[1]
    assert active_op == "scan"
    assert snapshot_op == "scan"
    expected_filter = (
        "AND",
        (
            "IN",
            ("name", "alert_state"),
            list(repo.current_set.CURRENT_ALERT_STATES),
        ),
        (">", ("name", "valid_until_utc"), NOW_ISO),
    )
    assert condition_shape(active["FilterExpression"]) == expected_filter
    assert condition_shape(snapshot["FilterExpression"]) == expected_filter
    assert projection_fields(active["ProjectionExpression"]) == {
        "alert_state",
        "risk_id",
        "recommendation_id",
        "valid_until_utc",
    }
    assert "ProjectionExpression" not in snapshot


def test_query_latest_uses_newest_first_single_page_limits(
    operational_repository,
):
    repo = operational_repository
    table = RecordingTable([{"Items": [{"risk_id": "r1"}]}])

    items = repo._query_latest(
        table,
        repo.IDX_RISK_ENCOUNTER_TIME,
        "encounter_id",
        "encounter-1",
        limit=1,
    )

    operation, kwargs = table.calls[0]
    assert operation == "query"
    assert items == [{"risk_id": "r1"}]
    assert kwargs["IndexName"] == "encounter_id-generated_at_epoch-index"
    assert kwargs["ScanIndexForward"] is False
    assert kwargs["Limit"] == 1
    assert "ExclusiveStartKey" not in kwargs
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "encounter_id"),
        "encounter-1",
    )


def test_aircraft_detail_query_latest_limits_are_ten_and_fifty(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    calls = []

    def query_latest(table, index, partition, value, limit=10):
        calls.append((table, index, partition, value, limit))
        if table is repo.PROJECTIONS:
            return [
                {
                    "projection_id": "proj-1",
                    "aircraft_id": "abc123",
                    "projection_status": "READY",
                    "valid_until_epoch": NOW + 10,
                    "generated_at_epoch": NOW,
                }
            ]
        return []

    repo.AIRCRAFT = RecordingTable(
        [{"Item": {"aircraft_id": "abc123"}}]
    )
    points = RecordingTable([{"Items": [], "LastEvaluatedKey": {"point_key": "p"}}])
    repo.PROJECTION_POINTS = points
    install_time(repo, monkeypatch)
    monkeypatch.setattr(repo, "_query_latest", query_latest)
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))

    repo.get_aircraft_detail("abc123")

    limits = {
        (index, limit)
        for _table, index, _partition, _value, limit in calls
    }
    assert (
        repo.IDX_PROJECTION_AIRCRAFT_TIME,
        10,
    ) in limits
    assert (repo.IDX_ENCOUNTER_AIRCRAFT_TIME, 50) in limits
    assert (repo.IDX_RISK_AIRCRAFT_TIME, 50) in limits
    assert (repo.IDX_RECOMMENDATION_AIRCRAFT_TIME, 50) in limits
    assert (repo.IDX_ALERT_AIRCRAFT_TIME, 50) in limits


def test_projection_points_query_is_consistent_and_single_page(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    repo.AIRCRAFT = RecordingTable(
        [{"Item": {"aircraft_id": "abc123"}}]
    )
    points = RecordingTable(
        [
            {
                "Items": [{"point_key": "1"}],
                "LastEvaluatedKey": {"point_key": "1"},
            },
            {"Items": [{"point_key": "2"}]},
        ]
    )
    repo.PROJECTION_POINTS = points
    install_time(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_query_latest",
        lambda table, index, partition, value, limit=10: (
            [
                {
                    "projection_id": "proj-1",
                    "aircraft_id": "abc123",
                    "projection_status": "READY",
                    "valid_until_epoch": NOW + 10,
                    "generated_at_epoch": NOW,
                }
            ]
            if table is repo.PROJECTIONS
            else []
        ),
    )
    monkeypatch.setattr(repo, "_load_current_indexes", lambda now: ({}, {}))

    detail = repo.get_aircraft_detail("abc123")

    assert len(points.calls) == 1
    operation, kwargs = points.calls[0]
    assert operation == "query"
    assert kwargs["ScanIndexForward"] is True
    assert kwargs["ConsistentRead"] is True
    assert "ExclusiveStartKey" not in kwargs
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "=",
        ("name", "projection_id"),
        "proj-1",
    )
    assert detail["projectionPoints"] == [{"point_key": "1"}]


def test_hazard_coordinate_query_is_consistent_and_drains_pages(
    operational_repository,
):
    repo = operational_repository
    coordinates = RecordingTable(
        [
            {
                "Items": [
                    {
                        "polygon_index": 0,
                        "ring_index": 0,
                        "sequence_number": 0,
                        "longitude": 1,
                        "latitude": 2,
                    }
                ],
                "LastEvaluatedKey": {"coordinate_key": "c1"},
            },
            {
                "Items": [
                    {
                        "polygon_index": 0,
                        "ring_index": 0,
                        "sequence_number": 1,
                        "longitude": 3,
                        "latitude": 4,
                    }
                ]
            },
        ]
    )
    repo.HAZARD_COORDINATES = coordinates

    rows = repo._query_all(
        coordinates,
        KeyConditionExpression=Key("hazard_version_key").eq("hazard-1#v1"),
        ScanIndexForward=True,
        ConsistentRead=True,
    )

    assert len(coordinates.calls) == 2
    first = coordinates.calls[0][1]
    second = coordinates.calls[1][1]
    assert first["ScanIndexForward"] is True
    assert first["ConsistentRead"] is True
    assert "ExclusiveStartKey" not in first
    assert second["ExclusiveStartKey"] == {"coordinate_key": "c1"}
    assert condition_shape(first["KeyConditionExpression"]) == (
        "=",
        ("name", "hazard_version_key"),
        "hazard-1#v1",
    )
    assert len(rows) == 2


def test_taf_period_query_uses_six_to_thirty_six_hour_window_first_page_only(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    repo.AIRPORTS = RecordingTable(
        [
            {
                "Item": {
                    "airport_id": "KSEA",
                    "station_id": "KSEA",
                }
            }
        ]
    )
    repo.METAR = RecordingTable([{}])
    repo.TAF = RecordingTable([{}])
    periods = RecordingTable(
        [
            {
                "Items": [{"period_key": "p1"}],
                "LastEvaluatedKey": {"period_key": "p1"},
            },
            {"Items": [{"period_key": "p2"}]},
        ]
    )
    repo.TAF_PERIODS = periods
    install_time(repo, monkeypatch)
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])

    detail = repo.get_airport_detail("KSEA")

    assert len(periods.calls) == 1
    operation, kwargs = periods.calls[0]
    assert operation == "query"
    assert kwargs["IndexName"] == "station_id-period_from_epoch-index"
    assert kwargs["ScanIndexForward"] is True
    assert kwargs["Limit"] == 50
    assert "ExclusiveStartKey" not in kwargs
    assert "taf_version_key" not in str(kwargs)
    assert condition_shape(kwargs["KeyConditionExpression"]) == (
        "AND",
        ("=", ("name", "station_id"), "KSEA"),
        ("BETWEEN", ("name", "period_from_epoch"), NOW - 21600, NOW + 129600),
    )
    assert detail["tafForecastPeriods"] == [{"period_key": "p1"}]


def test_dynamodb_page_token_round_trip_preserves_last_evaluated_key(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    last_key = {"aircraft_id": "cursor-a"}
    table = RecordingTable(
        [
            {"Items": [{"aircraft_id": "a"}], "LastEvaluatedKey": last_key},
            {"Items": [{"aircraft_id": "b"}]},
        ]
    )
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)

    first = repo.list_aircraft(limit=1)
    decoded = repo._decode_token(first["nextToken"])
    second = repo.list_aircraft(limit=1, next_token=first["nextToken"])

    assert decoded == last_key
    assert table.calls[1][1]["ExclusiveStartKey"] == last_key
    assert second["nextToken"] is None


def test_in_memory_offset_token_paginates_current_encounters(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    install_time(repo, monkeypatch)
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [
                {"encounter_id": "newer", "detected_at_epoch": 20},
                {"encounter_id": "older", "detected_at_epoch": 10},
            ],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )
    monkeypatch.setattr(repo, "_query_latest", lambda *args, **kwargs: [])

    first = repo.list_active_encounters(limit=1)
    second = repo.list_active_encounters(
        limit=1,
        next_token=first["nextToken"],
    )

    assert first["items"][0]["encounter"]["encounter_id"] == "newer"
    assert repo._decode_token(first["nextToken"]) == {"offset": 1}
    assert second["items"][0]["encounter"]["encounter_id"] == "older"
    assert second["nextToken"] is None


def test_overview_cache_does_not_fill_list_or_map_snapshot_keys(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    install_time(repo, monkeypatch)
    monkeypatch.setattr(repo, "_scan_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(repo, "_query_count", lambda *args, **kwargs: 0)
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [],
            "projection_ids": {},
            "hazard_versions": {},
        },
    )
    monkeypatch.setattr(
        repo,
        "_latest_current_risks",
        lambda: {
            "by_encounter": {},
            "items": [],
            "current_risk_ids": set(),
        },
    )
    monkeypatch.setattr(repo, "_active_recommendations", lambda: {"items": []})
    monkeypatch.setattr(
        repo,
        "_active_alerts",
        lambda: {"items": [], "active_count": 0, "by_state": {}},
    )
    monkeypatch.setattr(repo, "_scan_all", lambda *args, **kwargs: [])

    repo.get_overview()

    assert "overview" in repo._CACHE
    assert "current_recommendations" not in repo._CACHE
    assert "current_alerts" not in repo._CACHE
    assert "map_aircraft" not in repo._CACHE


def test_map_aircraft_cache_is_not_used_by_aircraft_list(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    table = RecordingTable(
        [
            {"Items": []},
            {"Items": []},
        ]
    )
    repo.AIRCRAFT = table
    install_time(repo, monkeypatch)

    repo.get_map_aircraft()
    assert "map_aircraft" in repo._CACHE
    map_scans = [
        call for call in table.calls if call[0] == "scan"
    ]

    repo.list_aircraft(limit=10)

    list_scans = [
        call for call in table.calls if call[0] == "scan"
    ]
    assert len(list_scans) == len(map_scans) + 1
    assert list_scans[-1][1]["Limit"] == 10


def test_recommendation_status_index_is_defined_and_unused(
    operational_repository,
):
    repo = operational_repository
    source = inspect.getsource(repo)

    assert (
        repo.IDX_RECOMMENDATION_STATUS_TIME
        == "recommendation_status-updated_at_epoch-index"
    )
    assert source.count("IDX_RECOMMENDATION_STATUS_TIME") == 1
    assert "recommendation_status-updated_at_epoch-index" in source
    assert source.count("recommendation_status-updated_at_epoch-index") == 1


def test_condition_shape_helper_matches_boto3_conditions():
    combined = Attr("expires_at_epoch").gt(NOW) & Attr("latitude").exists()
    assert condition_shape(combined) == (
        "AND",
        (">", ("name", "expires_at_epoch"), NOW),
        ("attribute_exists", ("name", "latitude")),
    )
    assert condition_shape(Key("station_id").eq("KSEA")) == (
        "=",
        ("name", "station_id"),
        "KSEA",
    )
