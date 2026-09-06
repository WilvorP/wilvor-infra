from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


PNG_BYTES = b"\x89PNG\r\n\x1a\nwidget-image"


SAMPLE_BODY = {
    "widgets": [
        {
            "type": "text",
            "x": 0,
            "y": 0,
            "width": 24,
            "height": 2,
            "properties": {
                "markdown": "# Wilvor Aircraft Pipeline\nOpenSky → Kinesis"
            },
        },
        {
            "type": "metric",
            "x": 0,
            "y": 2,
            "width": 12,
            "height": 6,
            "properties": {
                "title": "OpenSky Poller - Local Producer",
                "region": "us-west-1",
                "stat": "Sum",
                "period": 60,
                "metrics": [
                    [
                        "Wilvor/Aircraft",
                        "PollSuccess",
                        "Environment",
                        "dev",
                    ]
                ],
            },
        },
        {
            "type": "log",
            "x": 12,
            "y": 2,
            "width": 12,
            "height": 6,
            "properties": {"title": "Not used today"},
        },
    ]
}


class FakeCloudWatch:
    def __init__(self, body=None, image=PNG_BYTES, fail_image=False):
        self.body = body if body is not None else SAMPLE_BODY
        self.image = image
        self.fail_image = fail_image
        self.dashboard_calls = []
        self.image_calls = []

    def get_dashboard(self, DashboardName):
        self.dashboard_calls.append(DashboardName)
        return {
            "DashboardName": DashboardName,
            "DashboardBody": json.dumps(self.body),
        }

    def get_metric_widget_image(self, MetricWidget, OutputFormat="png"):
        self.image_calls.append(
            {
                "MetricWidget": json.loads(MetricWidget),
                "OutputFormat": OutputFormat,
            }
        )
        if self.fail_image:
            raise RuntimeError("render failed")
        return {"MetricWidgetImage": self.image}


@pytest.fixture
def dashboards(operational_api_env, load_repo_module):
    module = load_repo_module(
        "unit_operational_api_cloudwatch_dashboards",
        "functions/operational_api/cloudwatch_dashboards.py",
    )
    module._CACHE.clear()
    return module


def test_allowlist_contains_all_fourteen_stable_ids(dashboards):
    expected = {
        "aircraft-pipeline",
        "aircraft-hazard-encounter",
        "projection-pipeline",
        "sigmet-pipeline",
        "metar-pipeline",
        "taf-pipeline",
        "weather-events",
        "hazard-station-candidates",
        "airport-status",
        "airport-assessment",
        "risk-pipeline",
        "recommendations",
        "active-alerts",
        "runway-metadata",
    }

    assert set(dashboards.DASHBOARD_CATALOG) == expected
    assert (
        dashboards.dashboard_aws_name("aircraft-pipeline")
        == "wilvor-test-aircraft-pipeline"
    )


def test_unknown_dashboard_id_is_rejected(dashboards):
    with pytest.raises(dashboards.UnknownDashboard):
        dashboards.dashboard_aws_name("not-a-dashboard")


def test_get_dashboard_view_preserves_layout_and_text(dashboards):
    fake = FakeCloudWatch()
    dashboards.CLOUDWATCH = fake

    view = dashboards.get_dashboard_view("aircraft-pipeline")

    assert view["id"] == "aircraft-pipeline"
    assert view["awsDashboardName"] == "wilvor-test-aircraft-pipeline"
    assert view["gridColumns"] == 24
    assert [widget["id"] for widget in view["widgets"]] == [
        "widget-0",
        "widget-1",
        "widget-2",
    ]
    assert view["widgets"][0]["type"] == "text"
    assert view["widgets"][0]["markdown"].startswith("# Wilvor Aircraft Pipeline")
    assert view["widgets"][0]["width"] == 24
    assert view["widgets"][1]["type"] == "metric"
    assert view["widgets"][1]["title"] == "OpenSky Poller - Local Producer"
    assert view["widgets"][1]["x"] == 0
    assert view["widgets"][1]["y"] == 2
    assert view["widgets"][1]["supported"] is True
    assert view["widgets"][2]["type"] == "log"
    assert view["widgets"][2]["supported"] is False
    assert "metrics" not in view["widgets"][1]


def test_metric_payload_preserves_query_and_only_adds_range_and_size(dashboards):
    payload = dashboards.metric_widget_payload(
        SAMPLE_BODY["widgets"][1],
        "3h",
    )

    assert payload["metrics"] == SAMPLE_BODY["widgets"][1]["properties"]["metrics"]
    assert payload["stat"] == "Sum"
    assert payload["period"] == 60
    assert payload["region"] == "us-west-1"
    assert payload["title"] == "OpenSky Poller - Local Producer"
    assert payload["start"] == "-PT3H"
    assert payload["end"] == "P0D"
    assert payload["width"] == 12 * dashboards.PIXELS_PER_GRID_COLUMN
    assert payload["height"] == 6 * dashboards.PIXELS_PER_GRID_ROW


def test_metric_payload_uses_display_pixels_when_provided(dashboards):
    payload = dashboards.metric_widget_payload(
        SAMPLE_BODY["widgets"][1],
        "3h",
        overlay_width="960",
        overlay_height="420",
    )

    assert payload["width"] == 960
    assert payload["height"] == 420
    assert payload["metrics"] == SAMPLE_BODY["widgets"][1]["properties"]["metrics"]
    assert payload["period"] == 60


def test_invalid_display_pixels_are_rejected(dashboards):
    with pytest.raises(dashboards.BadImageSize):
        dashboards.resolve_pixel_dim("wide", 552, dashboards.MIN_IMAGE_WIDTH)

    with pytest.raises(dashboards.BadImageSize):
        dashboards.resolve_pixel_dim("3000", 552, dashboards.MIN_IMAGE_WIDTH)


def test_image_rejects_text_widget_and_bad_ids(dashboards):
    dashboards.CLOUDWATCH = FakeCloudWatch()

    with pytest.raises(dashboards.WidgetNotMetric):
        dashboards.get_widget_image("aircraft-pipeline", "widget-0")

    with pytest.raises(dashboards.UnknownWidget):
        dashboards.get_widget_image("aircraft-pipeline", "not-a-widget")

    with pytest.raises(dashboards.UnknownWidget):
        dashboards.get_widget_image("aircraft-pipeline", "widget-99")


def test_image_rejects_arbitrary_range(dashboards):
    with pytest.raises(dashboards.BadTimeRange):
        dashboards.resolve_time_range("7d")


def test_allowed_ranges(dashboards):
    assert dashboards.resolve_time_range(None) == "3h"
    assert list(dashboards.TIME_RANGES) == [
        "1h",
        "3h",
        "6h",
        "12h",
        "24h",
    ]


def test_get_widget_image_uses_dashboard_body_not_caller_metrics(dashboards):
    fake = FakeCloudWatch()
    dashboards.CLOUDWATCH = fake

    image = dashboards.get_widget_image(
        "aircraft-pipeline",
        "widget-1",
        "1h",
    )

    assert image == PNG_BYTES
    widget = fake.image_calls[0]["MetricWidget"]
    assert widget["start"] == "-PT1H"
    assert widget["metrics"][0][1] == "PollSuccess"
    assert fake.dashboard_calls == ["wilvor-test-aircraft-pipeline"]


def test_image_cache_avoids_rerender_until_body_changes(dashboards):
    fake = FakeCloudWatch()
    dashboards.CLOUDWATCH = fake

    dashboards.get_widget_image("aircraft-pipeline", "widget-1", "3h")
    dashboards.get_widget_image("aircraft-pipeline", "widget-1", "3h")

    assert len(fake.image_calls) == 1
    assert len(fake.dashboard_calls) == 1

    dashboards.get_widget_image("aircraft-pipeline", "widget-1", "24h")
    assert len(fake.image_calls) == 2


def test_image_render_failure_is_logged(dashboards):
    dashboards.CLOUDWATCH = FakeCloudWatch(fail_image=True)

    with pytest.raises(RuntimeError, match="render failed"):
        dashboards.get_widget_image("aircraft-pipeline", "widget-1", "3h")


def test_app_routes_unknown_dashboard_and_text_image(
    operational_app,
    monkeypatch,
):
    fake = FakeCloudWatch()
    monkeypatch.setattr(
        operational_app.cloudwatch_dashboards,
        "CLOUDWATCH",
        fake,
    )
    operational_app.cloudwatch_dashboards._CACHE.clear()

    unknown = operational_app.lambda_handler(
        {
            "rawPath": "/system-health/dashboards/not-real",
            "requestContext": {
                "requestId": "req-1",
                "http": {"method": "GET"},
            },
        },
        SimpleNamespace(),
    )
    assert unknown["statusCode"] == 404

    text_image = operational_app.lambda_handler(
        {
            "rawPath": (
                "/system-health/dashboards/aircraft-pipeline"
                "/widgets/widget-0/image"
            ),
            "requestContext": {
                "requestId": "req-2",
                "http": {"method": "GET"},
            },
        },
        SimpleNamespace(),
    )
    assert text_image["statusCode"] == 400
    assert "not a metric widget" in text_image["body"]

    bad_range = operational_app.lambda_handler(
        {
            "rawPath": (
                "/system-health/dashboards/aircraft-pipeline"
                "/widgets/widget-1/image"
            ),
            "queryStringParameters": {"range": "week"},
            "requestContext": {
                "requestId": "req-3",
                "http": {"method": "GET"},
            },
        },
        SimpleNamespace(),
    )
    assert bad_range["statusCode"] == 400


def test_app_returns_png_for_metric_widget(operational_app, monkeypatch):
    fake = FakeCloudWatch()
    monkeypatch.setattr(
        operational_app.cloudwatch_dashboards,
        "CLOUDWATCH",
        fake,
    )
    operational_app.cloudwatch_dashboards._CACHE.clear()

    metadata = operational_app.lambda_handler(
        {
            "rawPath": "/system-health/dashboards/aircraft-pipeline",
            "requestContext": {
                "requestId": "req-4",
                "http": {"method": "GET"},
            },
        },
        SimpleNamespace(),
    )
    body = json.loads(metadata["body"])
    assert metadata["statusCode"] == 200
    assert body["widgets"][1]["id"] == "widget-1"

    image = operational_app.lambda_handler(
        {
            "rawPath": (
                "/system-health/dashboards/aircraft-pipeline"
                "/widgets/widget-1/image"
            ),
            "queryStringParameters": {"range": "6h"},
            "requestContext": {
                "requestId": "req-5",
                "http": {"method": "GET"},
            },
        },
        SimpleNamespace(),
    )
    assert image["statusCode"] == 200
    assert image["headers"]["content-type"] == "image/png"
    assert image["isBase64Encoded"] is True
