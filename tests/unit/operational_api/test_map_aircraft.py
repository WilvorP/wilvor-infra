from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]


class FakeTable:
    """
    Minimal DynamoDB table stub that records the kwargs it was called with.

    Scan pagination is emulated so tests can prove the repository drains
    every page rather than stopping at the first one.
    """

    def __init__(self, pages):
        self.pages = pages or [[]]
        self.scan_calls = []

    def scan(self, **kwargs):
        self.scan_calls.append(kwargs)

        index = len(self.scan_calls) - 1
        response = {"Items": list(self.pages[index])}

        if index + 1 < len(self.pages):
            response["LastEvaluatedKey"] = {
                "aircraft_id": f"cursor-{index}"
            }

        return response


def get_event(path, method="GET"):
    return {
        "rawPath": path,
        "requestContext": {
            "requestId": "test-request-id",
            "http": {"method": method},
        },
    }


def aircraft_item(
    aircraft_id="a1b2c3",
    callsign="UAL123",
    **overrides,
):
    item = {
        "aircraft_id": aircraft_id,
        "callsign": callsign,
        "latitude": Decimal("37.6188"),
        "longitude": Decimal("-122.375"),
        "track_deg": Decimal("270.5"),
        "baro_altitude_ft": Decimal("35000"),
        "ground_speed_kt": Decimal("450"),
        "position_time_epoch": 1786515880,
    }
    item.update(overrides)

    return item


def install_aircraft_table(repository, pages):
    table = FakeTable(pages)
    repository.AIRCRAFT = table

    return table


# ---------------------------------------------------------------------
# Payload contract
# ---------------------------------------------------------------------


def test_rows_are_positional_and_match_declared_columns(
    operational_repository,
):
    install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    result = operational_repository.get_map_aircraft()

    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["columns"] == [
        "aircraftId",
        "callsign",
        "longitude",
        "latitude",
        "trackDeg",
        "baroAltitudeFt",
        "groundSpeedKt",
        "positionTimeEpoch",
    ]

    row = result["aircraft"][0]

    # The compact encoding is only safe if position and column agree.
    assert len(row) == len(result["columns"])
    assert dict(zip(result["columns"], row)) == {
        "aircraftId": "a1b2c3",
        "callsign": "UAL123",
        "longitude": Decimal("-122.375"),
        "latitude": Decimal("37.6188"),
        "trackDeg": Decimal("270.5"),
        "baroAltitudeFt": Decimal("35000"),
        "groundSpeedKt": Decimal("450"),
        "positionTimeEpoch": 1786515880,
    }


def test_longitude_precedes_latitude(operational_repository):
    """GeoJSON order. Swapping these silently misplaces every aircraft."""

    install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    result = operational_repository.get_map_aircraft()
    row = result["aircraft"][0]

    assert row[2] == Decimal("-122.375")
    assert row[3] == Decimal("37.6188")


def test_missing_optional_attributes_are_null_not_zero(
    operational_repository,
):
    """
    The writers strip None before put_item, so absent attributes are
    normal. Coercing them to 0 would render an aircraft as being at sea
    level and stationary.
    """

    item = aircraft_item()
    del item["baro_altitude_ft"]
    del item["ground_speed_kt"]
    del item["track_deg"]

    install_aircraft_table(
        operational_repository,
        [[item]],
    )

    result = operational_repository.get_map_aircraft()
    row = result["aircraft"][0]

    assert row[4] is None
    assert row[5] is None
    assert row[6] is None


def test_records_without_aircraft_id_are_dropped(
    operational_repository,
):
    install_aircraft_table(
        operational_repository,
        [
            [
                aircraft_item(aircraft_id="keep"),
                aircraft_item(aircraft_id=""),
                {"callsign": "NOID"},
            ]
        ],
    )

    result = operational_repository.get_map_aircraft()

    assert result["count"] == 1
    assert result["aircraft"][0][0] == "keep"


def test_generated_at_is_reported(operational_repository):
    install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    result = operational_repository.get_map_aircraft()

    # The console shows data age on every operational view.
    assert result["generatedAt"].endswith("Z")


# ---------------------------------------------------------------------
# Read behaviour
# ---------------------------------------------------------------------


def test_projects_only_map_attributes(operational_repository):
    table = install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    operational_repository.get_map_aircraft()

    projection = table.scan_calls[0]["ProjectionExpression"]
    requested = set(projection.split(","))

    assert requested == {
        "aircraft_id",
        "callsign",
        "latitude",
        "longitude",
        "track_deg",
        "baro_altitude_ft",
        "ground_speed_kt",
        "position_time_epoch",
    }

    # The heavy attributes on the full state record are the reason this
    # endpoint exists; requesting them would defeat it.
    for attribute in (
        "raw_s3_uri",
        "idempotency_key",
        "correlation_id",
        "schema_version",
    ):
        assert attribute not in projection


def test_applies_a_filter_expression(operational_repository):
    table = install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    operational_repository.get_map_aircraft()

    # Expired records and records without a position must not reach the map.
    assert "FilterExpression" in table.scan_calls[0]


def test_drains_every_scan_page(operational_repository):
    table = install_aircraft_table(
        operational_repository,
        [
            [aircraft_item(aircraft_id="one")],
            [aircraft_item(aircraft_id="two")],
            [aircraft_item(aircraft_id="three")],
        ],
    )

    result = operational_repository.get_map_aircraft()

    assert len(table.scan_calls) == 3
    assert result["count"] == 3
    assert [row[0] for row in result["aircraft"]] == [
        "one",
        "two",
        "three",
    ]

    # Every page after the first must carry the previous cursor.
    assert "ExclusiveStartKey" in table.scan_calls[1]


def test_truncation_is_reported_rather_than_silent(
    operational_repository,
    monkeypatch,
):
    monkeypatch.setattr(
        operational_repository,
        "MAP_AIRCRAFT_MAX_ITEMS",
        2,
    )

    install_aircraft_table(
        operational_repository,
        [
            [
                aircraft_item(aircraft_id="one"),
                aircraft_item(aircraft_id="two"),
                aircraft_item(aircraft_id="three"),
            ]
        ],
    )

    result = operational_repository.get_map_aircraft()

    # A silently trimmed traffic picture is indistinguishable from a
    # complete one, which is the failure this flag prevents.
    assert result["truncated"] is True
    assert result["count"] == 2


def test_empty_table_returns_an_empty_layer(operational_repository):
    install_aircraft_table(operational_repository, [[]])

    result = operational_repository.get_map_aircraft()

    assert result["count"] == 0
    assert result["truncated"] is False
    assert result["aircraft"] == []


def test_response_is_cached_within_ttl(operational_repository):
    table = install_aircraft_table(
        operational_repository,
        [[aircraft_item()]],
    )

    first = operational_repository.get_map_aircraft()
    second = operational_repository.get_map_aircraft()

    # The Lambda is concurrency-limited, so repeated polling from multiple
    # operators must not re-scan the table.
    assert len(table.scan_calls) == 1
    assert first is second


def test_cache_ttl_matches_client_poll_budget(operational_repository):
    assert (
        operational_repository.MAP_AIRCRAFT_CACHE_TTL_SECONDS == 15
    )


# ---------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------


def test_route_returns_the_map_payload(operational_app, monkeypatch):
    payload = {
        "generatedAt": "2026-09-03T12:00:00Z",
        "columns": ["aircraftId"],
        "count": 1,
        "truncated": False,
        "aircraft": [["a1b2c3"]],
    }

    monkeypatch.setattr(
        operational_app.repository,
        "get_map_aircraft",
        lambda: payload,
    )

    response = operational_app.lambda_handler(
        get_event("/map/aircraft"),
        None,
    )

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == payload


def test_route_serialises_decimal_coordinates(
    operational_app,
    monkeypatch,
):
    monkeypatch.setattr(
        operational_app.repository,
        "get_map_aircraft",
        lambda: {
            "aircraft": [
                [Decimal("-122.375"), Decimal("35000")]
            ]
        },
    )

    response = operational_app.lambda_handler(
        get_event("/map/aircraft"),
        None,
    )

    body = json.loads(response["body"])

    # Decimal is what the DynamoDB resource returns; the encoder must keep
    # fractional precision and not stringify it.
    assert body["aircraft"][0] == [-122.375, 35000]


def test_route_does_not_shadow_aircraft_detail(
    operational_app,
    monkeypatch,
):
    """
    /map/aircraft must not be captured by the `/aircraft/` prefix branch,
    and must not itself capture aircraft detail requests.
    """

    called = []

    monkeypatch.setattr(
        operational_app.repository,
        "get_aircraft_detail",
        lambda aircraft_id: called.append(aircraft_id) or None,
    )
    monkeypatch.setattr(
        operational_app.repository,
        "get_map_aircraft",
        lambda: {"count": 0, "aircraft": []},
    )

    map_response = operational_app.lambda_handler(
        get_event("/map/aircraft"),
        None,
    )

    assert map_response["statusCode"] == 200
    assert called == []

    detail_response = operational_app.lambda_handler(
        get_event("/aircraft/a1b2c3"),
        None,
    )

    assert detail_response["statusCode"] == 404
    assert called == ["a1b2c3"]


@pytest.mark.parametrize(
    "method",
    ["POST", "PUT", "DELETE"],
)
def test_route_rejects_write_methods(
    operational_app,
    method,
):
    response = operational_app.lambda_handler(
        get_event("/map/aircraft", method=method),
        None,
    )

    assert response["statusCode"] == 405


def test_route_is_declared_in_terraform():
    """
    The Lambda branch is unreachable unless API Gateway also routes it.
    """

    api_tf = (
        REPO_ROOT
        / "modules"
        / "operational_api"
        / "api.tf"
    ).read_text(encoding="utf-8")

    assert '"GET /map/aircraft"' in api_tf
