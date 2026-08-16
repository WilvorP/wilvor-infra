from __future__ import annotations

from decimal import Decimal


class FakeTable:
    def __init__(self, item=None, scan_items=None):
        self.item = item
        self.scan_items = scan_items or []
        self.puts = []

    def get_item(self, Key):
        if self.item is None:
            return {}
        return {"Item": self.item}

    def put_item(self, Item):
        self.puts.append(Item)
        return {}

    def scan(self, **kwargs):
        return {"Items": self.scan_items}


def test_extract_station_id_from_direct_event(airport_status_materializer):
    assert airport_status_materializer.extract_station_ids_from_event(
        {"station_id": "ksfo"}
    ) == ["KSFO"]


def test_extract_station_id_from_eventbridge_detail(airport_status_materializer):
    assert airport_status_materializer.extract_station_ids_from_event(
        {
            "source": "wilvor.weather",
            "detail-type": "metar.updated",
            "detail": {"station_id": "kjfk"},
        }
    ) == ["KJFK"]


def test_high_weather_risk_from_ifr_metar(
    airport_status_materializer,
    fixed_airport_status_time,
    monkeypatch,
):
    monkeypatch.setattr(
        airport_status_materializer,
        "utc_now",
        lambda: fixed_airport_status_time,
    )

    now_epoch = int(fixed_airport_status_time.timestamp())

    monkeypatch.setattr(
        airport_status_materializer,
        "station_reference_table",
        FakeTable(
            {
                "station_id": "KJFK",
                "airport_id": "KJFK",
                "station_name": "John F Kennedy International Airport",
                "is_airport": True,
                "latitude": Decimal("40.6413"),
                "longitude": Decimal("-73.7781"),
                "h3_cell": "842a107ffffffff",
                "h3_resolution": 4,
                "source_version": "stations-v1",
            }
        ),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "metar_latest_table",
        FakeTable(
            {
                "station_id": "KJFK",
                "metar_version": "metar-v1",
                "observed_time_epoch": now_epoch - 300,
                "observed_time_utc": "2026-08-15T11:55:00+00:00",
                "flight_category": "IFR",
                "visibility_sm": Decimal("2"),
                "ceiling_ft": Decimal("800"),
                "wind_speed_kt": Decimal("18"),
                "weather_codes": ["RA", "BR"],
                "correlation_id": "corr-1",
            }
        ),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "taf_latest_table",
        FakeTable(
            {
                "station_id": "KJFK",
                "taf_version": "taf-v1",
                "source_version": "taf-source-v1",
                "taf_version_key": "KJFK#taf-v1",
                "issued_at_epoch": now_epoch - 3600,
                "issued_at_utc": "2026-08-15T11:00:00+00:00",
                "valid_from_utc": "2026-08-15T12:00:00+00:00",
                "valid_to_utc": "2026-08-16T12:00:00+00:00",
                "period_materialization_status": "READY",
                "forecast_period_count": 3,
            }
        ),
    )

    item = airport_status_materializer.build_airport_status_item("KJFK")

    assert item["airport_id"] == "KJFK"
    assert item["has_metar"] is True
    assert item["has_taf"] is True
    assert item["metar_freshness_status"] == "FRESH"
    assert item["taf_freshness_status"] == "FRESH"
    assert item["weather_risk_level"] == "HIGH"
    assert item["weather_impact_status"] == "WEATHER_IMPACTED"
    assert item["assessment_status"] == "EVALUATED"
    assert item["is_diversion_weather_ready"] is True
    assert item["schema_version"] == "airport_status.v1"


def test_partial_status_when_taf_missing(
    airport_status_materializer,
    fixed_airport_status_time,
    monkeypatch,
):
    monkeypatch.setattr(
        airport_status_materializer,
        "utc_now",
        lambda: fixed_airport_status_time,
    )

    now_epoch = int(fixed_airport_status_time.timestamp())

    monkeypatch.setattr(
        airport_status_materializer,
        "station_reference_table",
        FakeTable({"station_id": "KSFO", "airport_id": "KSFO"}),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "metar_latest_table",
        FakeTable(
            {
                "station_id": "KSFO",
                "metar_version": "metar-v1",
                "observed_time_epoch": now_epoch - 120,
                "flight_category": "VFR",
                "visibility_sm": Decimal("10"),
                "ceiling_ft": Decimal("5000"),
            }
        ),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "taf_latest_table",
        FakeTable(None),
    )

    item = airport_status_materializer.build_airport_status_item("KSFO")

    assert item["has_metar"] is True
    assert item["has_taf"] is False
    assert item["weather_risk_level"] == "LOW"
    assert item["assessment_status"] == "PARTIALLY_EVALUATED"
    assert item["is_diversion_weather_ready"] is False
    assert "Current TAF is missing." in item["known_limitations"]


def test_lambda_handler_writes_airport_status(
    airport_status_materializer,
    fixed_airport_status_time,
    monkeypatch,
):
    monkeypatch.setattr(
        airport_status_materializer,
        "utc_now",
        lambda: fixed_airport_status_time,
    )

    output_table = FakeTable()

    monkeypatch.setattr(
        airport_status_materializer,
        "airport_status_table",
        output_table,
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "station_reference_table",
        FakeTable({"station_id": "KOAK", "airport_id": "KOAK"}),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "metar_latest_table",
        FakeTable(
            {
                "station_id": "KOAK",
                "metar_version": "metar-v1",
                "observed_time_epoch": int(fixed_airport_status_time.timestamp()) - 60,
                "flight_category": "VFR",
                "visibility_sm": Decimal("10"),
            }
        ),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "taf_latest_table",
        FakeTable(None),
    )

    result = airport_status_materializer.lambda_handler(
        {"station_id": "KOAK"},
        None,
    )

    assert result["ok"] is True
    assert result["processed"] == 1
    assert result["written"] == ["KOAK"]
    assert output_table.puts[0]["airport_id"] == "KOAK"


def test_bootstrap_scans_weather_tables(
    airport_status_materializer,
    monkeypatch,
):
    monkeypatch.setattr(
        airport_status_materializer,
        "metar_latest_table",
        FakeTable(scan_items=[{"station_id": "KSFO"}, {"station_id": "KOAK"}]),
    )

    monkeypatch.setattr(
        airport_status_materializer,
        "taf_latest_table",
        FakeTable(scan_items=[{"station_id": "KOAK"}, {"station_id": "KSJC"}]),
    )

    assert airport_status_materializer.scan_station_ids_from_weather_tables() == [
        "KOAK",
        "KSFO",
        "KSJC",
    ]