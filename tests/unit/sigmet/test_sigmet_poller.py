from __future__ import annotations

import gzip
import json
import urllib.error
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


class FakeHttpResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


def test_fetch_noaa_sigmets_returns_decoded_json(
    sigmet_poller,
    monkeypatch,
):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeHttpResponse(
            json.dumps({"type": "FeatureCollection", "features": []})
            .encode("utf-8")
        )

    monkeypatch.setattr(
        sigmet_poller.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = sigmet_poller.fetch_noaa_sigmets()

    assert result == {"type": "FeatureCollection", "features": []}
    assert captured["timeout"] == 30
    assert captured["request"].method == "GET"
    assert captured["request"].full_url.endswith("/api/data/airsigmet")
    assert captured["request"].headers["Accept"] == "application/json"


def test_fetch_noaa_sigmets_rejects_non_200_status(
    sigmet_poller,
    monkeypatch,
):
    monkeypatch.setattr(
        sigmet_poller.urllib.request,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(b"{}", status=503),
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected status 503",
    ):
        sigmet_poller.fetch_noaa_sigmets()


def test_fetch_noaa_sigmets_wraps_url_error(
    sigmet_poller,
    monkeypatch,
):
    def fail(request, timeout):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(
        sigmet_poller.urllib.request,
        "urlopen",
        fail,
    )

    with pytest.raises(
        RuntimeError,
        match="connection refused",
    ):
        sigmet_poller.fetch_noaa_sigmets()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {
                "type": "FeatureCollection",
                "features": [{"type": "Feature"}],
            },
            [{"type": "Feature"}],
        ),
        (
            {"data": [{"id": 1}]},
            [{"id": 1}],
        ),
        (
            [{"id": 1}, {"id": 2}],
            [{"id": 1}, {"id": 2}],
        ),
        (
            {"type": "FeatureCollection", "features": "not-a-list"},
            [],
        ),
        (
            {"unexpected": []},
            [],
        ),
        (
            "not-a-container",
            [],
        ),
    ],
)
def test_extract_records_supports_noaa_response_shapes(
    sigmet_poller,
    body,
    expected,
):
    assert sigmet_poller.extract_records(body) == expected


def test_build_s3_key_normalizes_trailing_slash(sigmet_poller):
    received_at = datetime(
        2026,
        7,
        18,
        9,
        5,
        tzinfo=timezone.utc,
    )

    key = sigmet_poller.build_s3_key(
        poll_id="poll-123",
        raw_prefix="raw/source=sigmet/",
        received_at=received_at,
    )

    assert key == (
        "raw/source=sigmet/"
        "year=2026/month=07/day=18/hour=09/"
        "sigmet-poll-123.json.gz"
    )


def test_archive_raw_response_writes_gzipped_json(
    sigmet_poller,
    monkeypatch,
    sigmet_feature,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    received_at = datetime(
        2026,
        7,
        18,
        12,
        0,
        tzinfo=timezone.utc,
    )
    body = {
        "type": "FeatureCollection",
        "features": [sigmet_feature],
    }

    monkeypatch.setattr(sigmet_poller, "s3", FakeS3())

    key = sigmet_poller.archive_raw_response(
        poll_id="poll-1",
        response_body=body,
        received_at=received_at,
    )

    assert captured["Bucket"] == "test-sigmet-archive"
    assert captured["Key"] == key
    assert captured["ContentType"] == "application/json"
    assert captured["ContentEncoding"] == "gzip"
    assert json.loads(gzip.decompress(captured["Body"])) == body


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        ({"id": "id-1", "hazard": "TURB"}, "id-1"),
        ({"id": " ", "airSigmetId": "air-2"}, "air-2"),
        ({"hazard": "ICE"}, "ICE"),
        ({"rawSigmet": "RAW TEXT"}, "RAW TEXT"),
        ({"validTimeFrom": 12345}, "12345"),
        ({}, "sigmet-7"),
    ],
)
def test_derive_partition_key_uses_first_available_identity(
    sigmet_poller,
    properties,
    expected,
):
    feature = {
        "type": "Feature",
        "properties": properties,
    }

    assert sigmet_poller.derive_partition_key(feature, 7) == expected


def test_derive_partition_key_falls_back_for_non_object(sigmet_poller):
    assert sigmet_poller.derive_partition_key("bad", 3) == "sigmet-3"


def test_chunked_preserves_order(sigmet_poller):
    assert sigmet_poller.chunked([1, 2, 3, 4, 5], 2) == [
        [1, 2],
        [3, 4],
        [5],
    ]


def test_publish_raw_records_builds_expected_event_and_counts(
    sigmet_poller,
    monkeypatch,
    sigmet_feature,
):
    captured = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            captured.append(kwargs)
            return {
                "FailedRecordCount": 1,
                "Records": [
                    {"SequenceNumber": "1", "ShardId": "s-1"},
                    {
                        "ErrorCode": "ProvisionedThroughputExceededException",
                        "ErrorMessage": "throttled",
                    },
                ],
            }

    monkeypatch.setattr(sigmet_poller, "kinesis", FakeKinesis())

    published, failed = sigmet_poller.publish_raw_records(
        poll_id="poll-1",
        received_at="2026-07-18T12:00:00+00:00",
        raw_s3_bucket="test-sigmet-archive",
        raw_s3_key="raw/test.json.gz",
        records=[sigmet_feature, sigmet_feature],
    )

    assert (published, failed) == (1, 1)
    assert len(captured) == 1
    assert captured[0]["StreamName"] == "test-sigmet-raw"

    first = captured[0]["Records"][0]
    payload = json.loads(first["Data"].decode("utf-8"))

    assert first["PartitionKey"] == "Turbulence"
    assert payload["schema_version"] == "raw.noaa.airsigmet.v1"
    assert payload["source"] == "NOAA_AVIATION_WEATHER"
    assert payload["product_type"] == "SIGMET"
    assert payload["record_index"] == 0
    assert payload["feature"] == sigmet_feature


def test_publish_raw_records_batches_at_500(
    sigmet_poller,
    monkeypatch,
):
    batch_sizes = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            batch_sizes.append(len(kwargs["Records"]))
            return {"FailedRecordCount": 0, "Records": []}

    monkeypatch.setattr(sigmet_poller, "kinesis", FakeKinesis())

    records = [
        {
            "type": "Feature",
            "properties": {"id": f"sigmet-{index}"},
        }
        for index in range(501)
    ]

    published, failed = sigmet_poller.publish_raw_records(
        poll_id="poll-batch",
        received_at="2026-07-18T12:00:00+00:00",
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        records=records,
    )

    assert batch_sizes == [500, 1]
    assert (published, failed) == (501, 0)


def test_lambda_handler_success(
    sigmet_poller,
    monkeypatch,
    sigmet_feature,
    fixed_sigmet_time,
):
    metrics = []
    response_body = {
        "type": "FeatureCollection",
        "features": [sigmet_feature],
    }

    monkeypatch.setattr(
        sigmet_poller.uuid,
        "uuid4",
        lambda: "poll-fixed",
    )
    monkeypatch.setattr(
        sigmet_poller,
        "now_utc",
        lambda: fixed_sigmet_time,
    )
    monkeypatch.setattr(
        sigmet_poller,
        "fetch_noaa_sigmets",
        lambda: response_body,
    )
    monkeypatch.setattr(
        sigmet_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/source=sigmet/test.json.gz",
    )
    monkeypatch.setattr(
        sigmet_poller,
        "publish_raw_records",
        lambda **kwargs: (1, 0),
    )
    monkeypatch.setattr(
        sigmet_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = sigmet_poller.lambda_handler(
        {},
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result == {
        "ok": True,
        "poll_id": "poll-fixed",
        "received_at": fixed_sigmet_time.isoformat(),
        "raw_s3_key": "raw/source=sigmet/test.json.gz",
        "feature_count": 1,
        "published_count": 1,
        "failed_kinesis_records": 0,
    }
    assert metrics[0]["metrics"]["PollSuccess"] == 1
    assert metrics[0]["metrics"]["FeaturesReceived"] == 1
    assert metrics[0]["metrics"]["RawArchiveSuccess"] == 1


def test_lambda_handler_failed_publish_emits_failure_metric(
    sigmet_poller,
    monkeypatch,
    sigmet_feature,
    fixed_sigmet_time,
):
    metrics = []

    monkeypatch.setattr(
        sigmet_poller.uuid,
        "uuid4",
        lambda: "poll-fixed",
    )
    monkeypatch.setattr(
        sigmet_poller,
        "now_utc",
        lambda: fixed_sigmet_time,
    )
    monkeypatch.setattr(
        sigmet_poller,
        "fetch_noaa_sigmets",
        lambda: {
            "type": "FeatureCollection",
            "features": [sigmet_feature],
        },
    )
    monkeypatch.setattr(
        sigmet_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/source=sigmet/test.json.gz",
    )
    monkeypatch.setattr(
        sigmet_poller,
        "publish_raw_records",
        lambda **kwargs: (0, 1),
    )
    monkeypatch.setattr(
        sigmet_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    with pytest.raises(
        RuntimeError,
        match="Failed to publish 1 of 1",
    ):
        sigmet_poller.lambda_handler({}, None)

    assert metrics[0]["metrics"]["PollSuccess"] == 0
    assert metrics[0]["metrics"]["PollFailure"] == 1
    assert metrics[0]["metrics"]["RawArchiveSuccess"] == 1
    assert metrics[0]["properties"]["ErrorType"] == "RuntimeError"


def test_lambda_handler_fetch_failure_records_archive_failure(
    sigmet_poller,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        sigmet_poller,
        "fetch_noaa_sigmets",
        lambda: (_ for _ in ()).throw(
            RuntimeError("NOAA unavailable")
        ),
    )
    monkeypatch.setattr(
        sigmet_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="NOAA unavailable"):
        sigmet_poller.lambda_handler({}, None)

    assert metrics[0]["metrics"]["PollFailure"] == 1
    assert metrics[0]["metrics"]["RawArchiveSuccess"] == 0
