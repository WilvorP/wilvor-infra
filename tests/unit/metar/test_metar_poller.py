from __future__ import annotations

import gzip
import json
import urllib.error
from types import SimpleNamespace

import pytest


class FakeHttpResponse:
    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


def test_fetch_noaa_metars_returns_json(metar_poller, monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(
            json.dumps({"type": "FeatureCollection", "features": []})
            .encode("utf-8")
        )

    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = metar_poller.fetch_noaa_metars()

    assert result == {"type": "FeatureCollection", "features": []}
    assert captured["timeout"] == 30
    assert captured["request"].method == "GET"
    assert captured["request"].headers["Accept"] == "application/json"


def test_fetch_noaa_metars_rejects_non_200(
    metar_poller,
    monkeypatch,
):
    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(b"{}", status=503),
    )

    with pytest.raises(RuntimeError, match="unexpected status 503"):
        metar_poller.fetch_noaa_metars()


def test_fetch_noaa_metars_wraps_url_error(
    metar_poller,
    monkeypatch,
):
    def fail(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        fail,
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        metar_poller.fetch_noaa_metars()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {"type": "FeatureCollection", "features": [{"id": 1}]},
            [{"id": 1}],
        ),
        ({"data": [{"id": 2}]}, [{"id": 2}]),
        ([{"id": 3}], [{"id": 3}]),
        ({"type": "FeatureCollection", "features": "bad"}, []),
        ({"unexpected": []}, []),
        ("bad", []),
    ],
)
def test_extract_records_supports_expected_shapes(
    metar_poller,
    body,
    expected,
):
    assert metar_poller.extract_records(body) == expected


def test_build_s3_key_normalizes_prefix(
    metar_poller,
    fixed_metar_time,
):
    key = metar_poller.build_s3_key(
        poll_id="poll-1",
        raw_prefix="raw/source=metar/",
        received_at=fixed_metar_time,
    )

    assert key == (
        "raw/source=metar/"
        "year=2026/month=07/day=18/hour=12/"
        "metar-poll-1.json.gz"
    )


def test_archive_raw_response_writes_gzip(
    metar_poller,
    metar_feature,
    fixed_metar_time,
    monkeypatch,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    body = {
        "type": "FeatureCollection",
        "features": [metar_feature],
    }
    monkeypatch.setattr(metar_poller, "s3", FakeS3())

    key = metar_poller.archive_raw_response(
        poll_id="poll-1",
        response_body=body,
        received_at=fixed_metar_time,
    )

    assert captured["Bucket"] == "test-weather-archive"
    assert captured["Key"] == key
    assert captured["ContentEncoding"] == "gzip"
    assert json.loads(gzip.decompress(captured["Body"])) == body


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"icaoId": "KJFK"}, "KJFK"),
        ({"station": "KSFO"}, "KSFO"),
        ({"station_id": "KLAX"}, "KLAX"),
        ({"id": 123}, "123"),
        ({}, "metar-7"),
    ],
)
def test_derive_partition_key(
    metar_poller,
    properties,
    expected,
):
    feature = {"type": "Feature", "properties": properties}
    assert metar_poller.derive_partition_key(feature, 7) == expected


def test_derive_partition_key_fallback_for_non_object(metar_poller):
    assert metar_poller.derive_partition_key("bad", 2) == "metar-2"


def test_chunked_preserves_order(metar_poller):
    assert metar_poller.chunked([1, 2, 3, 4, 5], 2) == [
        [1, 2],
        [3, 4],
        [5],
    ]


def test_publish_raw_records_builds_contract_and_counts(
    metar_poller,
    metar_feature,
    monkeypatch,
):
    calls = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            calls.append(kwargs)
            return {
                "FailedRecordCount": 1,
                "Records": [{}, {"ErrorCode": "Throttled"}],
            }

    monkeypatch.setattr(metar_poller, "kinesis", FakeKinesis())

    published, failed = metar_poller.publish_raw_records(
        poll_id="poll-1",
        received_at="2026-07-18T12:05:00+00:00",
        raw_s3_bucket="test-weather-archive",
        raw_s3_key="raw/metar.json.gz",
        records=[metar_feature, metar_feature],
    )

    assert (published, failed) == (1, 1)
    first = calls[0]["Records"][0]
    payload = json.loads(first["Data"].decode("utf-8"))

    assert calls[0]["StreamName"] == "test-metar-raw"
    assert first["PartitionKey"] == "kjfk"
    assert payload["schema_version"] == "raw.noaa.metar.v1"
    assert payload["product_type"] == "METAR"
    assert payload["feature"] == metar_feature


def test_publish_raw_records_batches_at_500(
    metar_poller,
    monkeypatch,
):
    batch_sizes = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            batch_sizes.append(len(kwargs["Records"]))
            return {"FailedRecordCount": 0, "Records": []}

    monkeypatch.setattr(metar_poller, "kinesis", FakeKinesis())

    features = [
        {
            "type": "Feature",
            "properties": {"icaoId": f"K{index:04d}"},
        }
        for index in range(501)
    ]

    assert metar_poller.publish_raw_records(
        poll_id="poll-batch",
        received_at="2026-07-18T12:05:00+00:00",
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        records=features,
    ) == (501, 0)
    assert batch_sizes == [500, 1]


def test_lambda_handler_success(
    metar_poller,
    metar_feature,
    fixed_metar_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        metar_poller.uuid,
        "uuid4",
        lambda: "poll-fixed",
    )
    monkeypatch.setattr(
        metar_poller,
        "now_utc",
        lambda: fixed_metar_time,
    )
    monkeypatch.setattr(
        metar_poller,
        "fetch_noaa_metars",
        lambda: {
            "type": "FeatureCollection",
            "features": [metar_feature],
        },
    )
    monkeypatch.setattr(
        metar_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/source=metar/test.json.gz",
    )
    monkeypatch.setattr(
        metar_poller,
        "publish_raw_records",
        lambda **kwargs: (1, 0),
    )
    monkeypatch.setattr(
        metar_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = metar_poller.lambda_handler(
        {},
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result["ok"] is True
    assert result["poll_id"] == "poll-fixed"
    assert result["feature_count"] == 1
    assert result["published_count"] == 1
    assert metrics[0]["metrics"]["PollSuccess"] == 1


def test_lambda_handler_publish_failure_emits_failure_metric(
    metar_poller,
    metar_feature,
    fixed_metar_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(metar_poller, "now_utc", lambda: fixed_metar_time)
    monkeypatch.setattr(
        metar_poller,
        "fetch_noaa_metars",
        lambda: [metar_feature],
    )
    monkeypatch.setattr(
        metar_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/metar.json.gz",
    )
    monkeypatch.setattr(
        metar_poller,
        "publish_raw_records",
        lambda **kwargs: (0, 1),
    )
    monkeypatch.setattr(
        metar_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="Failed to publish 1 of 1"):
        metar_poller.lambda_handler({}, None)

    assert metrics[0]["metrics"]["PollFailure"] == 1
    assert metrics[0]["metrics"]["RawArchiveSuccess"] == 1
