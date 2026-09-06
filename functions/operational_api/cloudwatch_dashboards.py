"""
Render existing CloudWatch dashboards for System Health.

Terraform-managed dashboards remain the only metric source of truth.
This module reads DashboardBody via GetDashboard and renders metric
widgets via GetMetricWidgetImage. It does not invent metrics, share
dashboards, or accept widget JSON from the caller.

Transformations applied for GetMetricWidgetImage (documented, minimal):

1. start / end — selected viewer range only. Dashboard period/stat stay.
2. width / height — PNG pixel size. Defaults to the CloudWatch grid
   cell (width * 46, height * 42). The viewer may pass display pixels
   so the graph fills its cell; values are clamped to AWS limits
   [1, 2000]. This does not change metrics, period, or stat.
3. Every other property on the original metric widget is copied as-is
   (metrics, title, stat, period, region, view, stacked, annotations,
   legend, yAxis, labels, and any other keys present).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3


LOGGER = logging.getLogger()

CLOUDWATCH = boto3.client("cloudwatch")

NAME_PREFIX = os.environ["NAME_PREFIX"]

GRID_COLUMNS = 24
DASHBOARD_CACHE_TTL_SECONDS = 20
IMAGE_CACHE_TTL_SECONDS = 30

# CloudWatch grid → PNG. 24 * 46 = 1104px (under the 2000px API cap).
# Height 6 * 42 = 252px, matching a typical console metric widget.
PIXELS_PER_GRID_COLUMN = 46
PIXELS_PER_GRID_ROW = 42
MIN_IMAGE_WIDTH = 120
MIN_IMAGE_HEIGHT = 80
MAX_IMAGE_PIXELS = 2000

WIDGET_ID_RE = re.compile(r"^widget-(\d+)$")

TIME_RANGES = {
    "1h": "-PT1H",
    "3h": "-PT3H",
    "6h": "-PT6H",
    "12h": "-PT12H",
    "24h": "-PT24H",
}

DEFAULT_TIME_RANGE = "3h"

# Stable IDs match the frontend catalog. AWS names are
# f"{NAME_PREFIX}-{id}".
DASHBOARD_CATALOG = {
    "aircraft-pipeline": "Aircraft Pipeline",
    "aircraft-hazard-encounter": "Aircraft-Hazard Encounter",
    "projection-pipeline": "Projection Pipeline",
    "sigmet-pipeline": "SIGMET Pipeline",
    "metar-pipeline": "METAR Pipeline",
    "taf-pipeline": "TAF Pipeline",
    "weather-events": "Weather Events",
    "hazard-station-candidates": "Hazard Station Candidates",
    "airport-status": "Airport Status",
    "airport-assessment": "Airport Assessment",
    "risk-pipeline": "Risk Pipeline",
    "recommendations": "Recommendations",
    "active-alerts": "Active Alerts",
    "runway-metadata": "Runway Metadata",
}

SUPPORTED_WIDGET_TYPES = {
    "metric",
    "text",
}

_CACHE = {}


class UnknownDashboard(ValueError):
    pass


class UnknownWidget(ValueError):
    pass


class WidgetNotMetric(ValueError):
    pass


class BadTimeRange(ValueError):
    pass


class BadImageSize(ValueError):
    pass


def dashboard_aws_name(dashboard_id):
    if dashboard_id not in DASHBOARD_CATALOG:
        raise UnknownDashboard(
            f"Unknown dashboard '{dashboard_id}'"
        )

    return f"{NAME_PREFIX}-{dashboard_id}"


def resolve_time_range(raw):
    if raw is None or raw == "":
        return DEFAULT_TIME_RANGE

    if raw not in TIME_RANGES:
        raise BadTimeRange(
            "range must be one of: "
            + ", ".join(TIME_RANGES)
        )

    return raw


def parse_widget_index(widget_id):
    match = WIDGET_ID_RE.fullmatch(
        widget_id or ""
    )

    if match is None:
        raise UnknownWidget(
            f"Unknown widget '{widget_id}'"
        )

    return int(match.group(1))


def _cached(cache_key, ttl_seconds, loader):
    now = time.time()
    cached = _CACHE.get(cache_key)

    if cached and cached["expires_at"] > now:
        return cached["value"]

    value = loader()
    _CACHE[cache_key] = {
        "expires_at": now + ttl_seconds,
        "value": value,
    }
    return value


def _body_revision(dashboard_body):
    encoded = json.dumps(
        dashboard_body,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()[:16]


def get_dashboard_record(dashboard_id):
    aws_name = dashboard_aws_name(dashboard_id)

    def loader():
        response = CLOUDWATCH.get_dashboard(
            DashboardName=aws_name,
        )
        body = json.loads(
            response["DashboardBody"]
        )

        if not isinstance(body, dict):
            raise ValueError(
                "DashboardBody is not an object"
            )

        return {
            "aws_name": aws_name,
            "body": body,
            "revision": _body_revision(body),
        }

    return _cached(
        f"cw-dashboard:{aws_name}",
        DASHBOARD_CACHE_TTL_SECONDS,
        loader,
    )


def _int_or(value, default):
    if isinstance(value, bool):
        return default

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return default


def describe_widgets(dashboard_body):
    raw_widgets = dashboard_body.get("widgets")

    if not isinstance(raw_widgets, list):
        raw_widgets = []

    described = []

    for index, widget in enumerate(raw_widgets):
        if not isinstance(widget, dict):
            described.append(
                {
                    "id": f"widget-{index}",
                    "type": "unknown",
                    "x": 0,
                    "y": 0,
                    "width": GRID_COLUMNS,
                    "height": 2,
                    "title": None,
                    "markdown": None,
                    "supported": False,
                }
            )
            continue

        widget_type = widget.get("type")
        properties = widget.get("properties")

        if not isinstance(properties, dict):
            properties = {}

        entry = {
            "id": f"widget-{index}",
            "type": widget_type,
            "x": _int_or(widget.get("x"), 0),
            "y": _int_or(widget.get("y"), 0),
            "width": _int_or(
                widget.get("width"),
                GRID_COLUMNS,
            ),
            "height": _int_or(widget.get("height"), 2),
            "title": properties.get("title"),
            "markdown": None,
            "supported": widget_type in SUPPORTED_WIDGET_TYPES,
        }

        if widget_type == "text":
            markdown = properties.get("markdown")
            entry["markdown"] = (
                markdown if isinstance(markdown, str) else ""
            )

        described.append(entry)

    return described


def get_dashboard_view(dashboard_id):
    record = get_dashboard_record(dashboard_id)

    return {
        "id": dashboard_id,
        "name": DASHBOARD_CATALOG[dashboard_id],
        "awsDashboardName": record["aws_name"],
        "generatedAt": datetime.now(
            timezone.utc
        ).isoformat(),
        "revision": record["revision"],
        "gridColumns": GRID_COLUMNS,
        "widgets": describe_widgets(record["body"]),
    }


def _clamp_pixels(value, minimum):
    return max(
        minimum,
        min(MAX_IMAGE_PIXELS, value),
    )


def resolve_pixel_dim(raw, default, minimum):
    if raw is None or raw == "":
        return default

    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BadImageSize(
            "width and height must be integers"
        ) from exc

    if value < 1 or value > MAX_IMAGE_PIXELS:
        raise BadImageSize(
            "width and height must be between "
            f"1 and {MAX_IMAGE_PIXELS}"
        )

    return _clamp_pixels(value, minimum)


def image_pixel_size(
    width,
    height,
    overlay_width=None,
    overlay_height=None,
):
    default_width = _clamp_pixels(
        _int_or(width, 12) * PIXELS_PER_GRID_COLUMN,
        MIN_IMAGE_WIDTH,
    )
    default_height = _clamp_pixels(
        _int_or(height, 6) * PIXELS_PER_GRID_ROW,
        MIN_IMAGE_HEIGHT,
    )
    return (
        resolve_pixel_dim(
            overlay_width,
            default_width,
            MIN_IMAGE_WIDTH,
        ),
        resolve_pixel_dim(
            overlay_height,
            default_height,
            MIN_IMAGE_HEIGHT,
        ),
    )


def metric_widget_payload(
    widget,
    time_range,
    overlay_width=None,
    overlay_height=None,
):
    """
    Build GetMetricWidgetImage MetricWidget JSON from one DashboardBody widget.

    Copies the original properties object, then sets only start/end/size.
    """
    properties = widget.get("properties")

    if not isinstance(properties, dict):
        properties = {}

    payload = copy.deepcopy(properties)
    pixel_width, pixel_height = image_pixel_size(
        widget.get("width"),
        widget.get("height"),
        overlay_width,
        overlay_height,
    )
    payload["start"] = TIME_RANGES[time_range]
    payload["end"] = "P0D"
    payload["width"] = pixel_width
    payload["height"] = pixel_height
    return payload


def _raw_widget(dashboard_body, widget_id):
    widgets = dashboard_body.get("widgets")

    if not isinstance(widgets, list):
        widgets = []

    index = parse_widget_index(widget_id)

    if index < 0 or index >= len(widgets):
        raise UnknownWidget(
            f"Unknown widget '{widget_id}'"
        )

    widget = widgets[index]

    if not isinstance(widget, dict):
        raise UnknownWidget(
            f"Unknown widget '{widget_id}'"
        )

    return widget


def get_widget_image(
    dashboard_id,
    widget_id,
    time_range=None,
    pixel_width=None,
    pixel_height=None,
):
    resolved_range = resolve_time_range(time_range)
    record = get_dashboard_record(dashboard_id)
    widget = _raw_widget(record["body"], widget_id)

    if widget.get("type") != "metric":
        raise WidgetNotMetric(
            f"Widget '{widget_id}' is not a metric widget"
        )

    payload = metric_widget_payload(
        widget,
        resolved_range,
        pixel_width,
        pixel_height,
    )

    cache_key = (
        "cw-image:"
        f"{record['aws_name']}:{widget_id}:"
        f"{resolved_range}:{record['revision']}:"
        f"{payload['width']}x{payload['height']}"
    )

    def loader():
        try:
            response = CLOUDWATCH.get_metric_widget_image(
                MetricWidget=json.dumps(
                    payload,
                    separators=(",", ":"),
                ),
                OutputFormat="png",
            )
        except Exception:
            LOGGER.exception(
                json.dumps(
                    {
                        "event": (
                            "operational_api.cloudwatch_widget_image_failed"
                        ),
                        "dashboard_id": dashboard_id,
                        "widget_id": widget_id,
                        "aws_dashboard_name": record["aws_name"],
                    }
                )
            )
            raise

        image = response.get("MetricWidgetImage")

        if not image:
            raise RuntimeError(
                "GetMetricWidgetImage returned an empty image"
            )

        return image

    return _cached(
        cache_key,
        IMAGE_CACHE_TTL_SECONDS,
        loader,
    )
