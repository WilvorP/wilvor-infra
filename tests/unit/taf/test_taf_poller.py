from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
from types import SimpleNamespace

import pytest


class FakeHttpResponse:
    def __init__(self, body: bytes = b"", status: int = 200):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self._body


def test_get_station_ids_normalizes_sorts_and_deduplicates(taf_poller):
    assert taf_poller.get_station_ids() == ["KJFK", "KSFO"]


def test_get_station_ids_rejects_empty_configuration(
    taf_poller,
    monkeypatch,
):
    monkeypatch.setenv("TAF_STATION_IDS", " , ")
    with pytest.raises(RuntimeError, match="does not contain"):
        taf_poller.get_station_ids()


def test_chunked_preserves_order(taf_poller):
    assert taf_poller.chunked([1, 2, 3, 4, 5], 2) == [
        [1, 2],
        [3, 4],
        [5],
    ]


def test_fetch_taf_records_builds_chunked_requests(
    taf_poller,
    monkeypatch,
):
    monkeypatch.setenv("TAF_STATION_IDS", "KJFK,KSFO,KLAX")
    monkeypatch.setenv("TAF_STATION_CHUNK_SIZE", "2")
    requested_urls = []

    responses = iter(
        [
            FakeHttpResponse(
                json.dumps([{"icaoId": "KJFK"}]).encode("utf-8")
            ),
            FakeHttpResponse(
                json.dumps({"icaoId": "KLAX"}).encode("utf-8")
            ),
        ]
    )

    def fake_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        assert timeout == 60
        return next(responses)

    monkeypatch.setattr(
        taf_poller.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    records = taf_poller.fetch_taf_records()

    assert records == [{"icaoId": "KJFK"}, {"icaoId": "KLAX"}]
    first_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(requested_urls[0]).query
    )
    second_query = urllib.parse.parse_qs(
        urllib.parse.urlparse(requested_urls[1]).query
    )
    assert first_query == {"ids": ["KJFK,KLAX"], "format": ["json"]}
    assert second_query == {"ids": ["KSFO"], "format": ["json"]}


def test_fetch_taf_records_skips_204(taf_poller, monkeypatch):
    monkeypatch.setattr(
        taf_poller.urllib.request,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(status=204),
    )
    assert taf_poller.fetch_taf_records() == []


def test_fetch_taf_records_skips_http_204(taf_poller, monkeypatch):
    def no_content(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            204,
            "No Content",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(
        taf_poller.urllib.request,
        "urlopen",
        no_content,
    )

    assert taf_poller.fetch_taf_records() == []


def test_fetch_taf_records_rejects_invalid_json(
    taf_poller,
    monkeypatch,
):
    monkeypatch.setattr(
        taf_poller.urllib.request,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(b"not-json"),
    )

    with pytest.raises(RuntimeError, match="invalid JSON"):
        taf_poller.fetch_taf_records()


def test_fetch_taf_records_rejects_unexpected_shape(
    taf_poller,
    monkeypatch,
):
    monkeypatch.setattr(
        taf_poller.urllib.request,
        "urlopen",
        lambda request, timeout: FakeHttpResponse(
            json.dumps("bad").encode("utf-8")
        ),
    )

    with pytest.raises(RuntimeError, match="not a JSON list or object"):
        taf_poller.fetch_taf_records()


def test_build_s3_key(
    taf_poller,
    fixed_taf_time,
):
    assert taf_poller.build_s3_key(
        poll_id="poll-1",
        raw_prefix="raw/source=taf/",
        received_at=fixed_taf_time,
    ) == (
        "raw/source=taf/"
        "year=2026/month=07/day=18/hour=12/"
        "taf-poll-1.json.gz"
    )


def test_archive_raw_response_writes_gzip(
    taf_poller,
    taf_record,
    fixed_taf_time,
    monkeypatch,
):
    captured = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(taf_poller, "s3", FakeS3())

    key = taf_poller.archive_raw_response(
        poll_id="poll-1",
        records=[taf_record],
        received_at=fixed_taf_time,
    )

    assert captured["Bucket"] == "test-weather-archive"
    assert captured["Key"] == key
    assert captured["ContentEncoding"] == "gzip"
    assert json.loads(gzip.decompress(captured["Body"])) == [taf_record]


@pytest.mark.parametrize(
    ("record", "expected"),
    [
        ({"icaoId": "kjfk"}, "KJFK"),
        ({"stationId": "ksfo"}, "KSFO"),
        ({"station_id": "klax"}, "KLAX"),
        ({"id": 123}, "123"),
        ({}, "taf-4"),
    ],
)
def test_derive_partition_key(taf_poller, record, expected):
    assert taf_poller.derive_partition_key(record, 4) == expected


def test_publish_raw_records_builds_contract_and_counts(
    taf_poller,
    taf_record,
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

    monkeypatch.setattr(taf_poller, "kinesis", FakeKinesis())

    assert taf_poller.publish_raw_records(
        poll_id="poll-1",
        received_at="2026-07-18T12:05:00+00:00",
        raw_s3_bucket="test-weather-archive",
        raw_s3_key="raw/taf.json.gz",
        records=[taf_record, taf_record],
    ) == (1, 1)

    first = calls[0]["Records"][0]
    payload = json.loads(first["Data"].decode("utf-8"))

    assert calls[0]["StreamName"] == "test-taf-raw"
    assert first["PartitionKey"] == "KJFK"
    assert payload["schema_version"] == "raw.noaa.taf.v1"
    assert payload["product_type"] == "TAF"
    assert payload["taf"] == taf_record


def test_publish_raw_records_batches_at_500(
    taf_poller,
    monkeypatch,
):
    batch_sizes = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            batch_sizes.append(len(kwargs["Records"]))
            return {"FailedRecordCount": 0, "Records": []}

    monkeypatch.setattr(taf_poller, "kinesis", FakeKinesis())

    records = [{"icaoId": f"K{index:04d}"} for index in range(501)]

    assert taf_poller.publish_raw_records(
        poll_id="poll-batch",
        received_at="2026-07-18T12:05:00+00:00",
        raw_s3_bucket="bucket",
        raw_s3_key="key",
        records=records,
    ) == (501, 0)
    assert batch_sizes == [500, 1]


def test_lambda_handler_success(
    taf_poller,
    taf_record,
    fixed_taf_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(taf_poller.uuid, "uuid4", lambda: "poll-fixed")
    monkeypatch.setattr(taf_poller, "now_utc", lambda: fixed_taf_time)
    monkeypatch.setattr(
        taf_poller,
        "fetch_taf_records",
        lambda: [taf_record],
    )
    monkeypatch.setattr(
        taf_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/source=taf/test.json.gz",
    )
    monkeypatch.setattr(
        taf_poller,
        "publish_raw_records",
        lambda **kwargs: (1, 0),
    )
    monkeypatch.setattr(
        taf_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    result = taf_poller.lambda_handler(
        {},
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result["ok"] is True
    assert result["poll_id"] == "poll-fixed"
    assert result["record_count"] == 1
    assert result["published_count"] == 1
    assert metrics[0]["metrics"]["PollSuccess"] == 1


def test_lambda_handler_publish_failure_emits_failure_metric(
    taf_poller,
    taf_record,
    fixed_taf_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(taf_poller, "now_utc", lambda: fixed_taf_time)
    monkeypatch.setattr(
        taf_poller,
        "fetch_taf_records",
        lambda: [taf_record],
    )
    monkeypatch.setattr(
        taf_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/taf.json.gz",
    )
    monkeypatch.setattr(
        taf_poller,
        "publish_raw_records",
        lambda **kwargs: (0, 1),
    )
    monkeypatch.setattr(
        taf_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="Failed to publish 1 of 1"):
        taf_poller.lambda_handler({}, None)

    assert metrics[0]["metrics"]["PollFailure"] == 1
    assert metrics[0]["metrics"]["RawArchiveSuccess"] == 1
