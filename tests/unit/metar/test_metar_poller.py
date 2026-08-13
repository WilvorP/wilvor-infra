from __future__ import annotations

import gzip
import json
import urllib.error
from datetime import timedelta

import pytest


class FakeHttpResponse:
    def __init__(
        self,
        body: bytes,
        status: int = 200,
    ):
        self.status = status
        self._body = body

    def __enter__(self):
        return self

    def __exit__(
        self,
        exc_type,
        exc,
        traceback,
    ):
        return False

    def read(self):
        return self._body


def test_build_metar_url_adds_station_ids(
    metar_poller,
):
    url = metar_poller.build_metar_url(
        [
            "KJFK",
            "KBOS",
        ]
    )

    assert "ids=KJFK%2CKBOS" in url
    assert "format=geojson" in url


def test_fetch_noaa_metars_returns_json(
    metar_poller,
    monkeypatch,
):
    captured = {}

    def fake_urlopen(
        request,
        timeout,
    ):
        captured["request"] = request
        captured["timeout"] = timeout

        return FakeHttpResponse(
            json.dumps(
                {
                    "type":
                        "FeatureCollection",
                    "features": [],
                }
            ).encode("utf-8")
        )

    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    result = (
        metar_poller.fetch_noaa_metars(
            [
                "KJFK",
                "KBOS",
            ]
        )
    )

    assert result == {
        "type": "FeatureCollection",
        "features": [],
    }

    assert captured["timeout"] == 30

    assert (
        captured["request"].method
        == "GET"
    )

    assert (
        captured["request"]
        .headers["Accept"]
        == "application/json"
    )

    assert (
        "ids=KJFK%2CKBOS"
        in captured["request"].full_url
    )


def test_fetch_noaa_metars_rejects_non_200(
    metar_poller,
    monkeypatch,
):
    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        lambda request, timeout:
            FakeHttpResponse(
                b"{}",
                status=503,
            ),
    )

    with pytest.raises(
        RuntimeError,
        match="unexpected status 503",
    ):
        metar_poller.fetch_noaa_metars(
            ["KJFK"]
        )


def test_fetch_noaa_metars_wraps_url_error(
    metar_poller,
    monkeypatch,
):
    def fail(
        request,
        timeout,
    ):
        raise urllib.error.URLError(
            "connection refused"
        )

    monkeypatch.setattr(
        metar_poller.urllib.request,
        "urlopen",
        fail,
    )

    with pytest.raises(
        RuntimeError,
        match="connection refused",
    ):
        metar_poller.fetch_noaa_metars(
            ["KJFK"]
        )


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (
            {
                "type":
                    "FeatureCollection",
                "features": [
                    {"id": 1}
                ],
            },
            [
                {"id": 1}
            ],
        ),
        (
            {
                "data": [
                    {"id": 2}
                ]
            },
            [
                {"id": 2}
            ],
        ),
        (
            [
                {"id": 3}
            ],
            [
                {"id": 3}
            ],
        ),
        (
            {
                "type":
                    "FeatureCollection",
                "features": "bad",
            },
            [],
        ),
        (
            {
                "unexpected": []
            },
            [],
        ),
        (
            "bad",
            [],
        ),
    ],
)
def test_extract_records_supports_expected_shapes(
    metar_poller,
    body,
    expected,
):
    assert (
        metar_poller.extract_records(
            body
        )
        == expected
    )


def test_is_candidate_current_accepts_valid(
    metar_poller,
    fixed_metar_time,
):
    item = {
        "station_id": "KJFK",
        "valid_to_utc": (
            fixed_metar_time
            + timedelta(hours=2)
        ).isoformat(),
        "expires_at_epoch": int(
            (
                fixed_metar_time
                + timedelta(hours=3)
            ).timestamp()
        ),
    }

    assert (
        metar_poller.is_candidate_current(
            item,
            fixed_metar_time,
        )
        is True
    )


def test_is_candidate_current_rejects_expired(
    metar_poller,
    fixed_metar_time,
):
    item = {
        "station_id": "KJFK",
        "valid_to_utc": (
            fixed_metar_time
            - timedelta(minutes=1)
        ).isoformat(),
        "expires_at_epoch": int(
            (
                fixed_metar_time
                + timedelta(hours=1)
            ).timestamp()
        ),
    }

    assert (
        metar_poller.is_candidate_current(
            item,
            fixed_metar_time,
        )
        is False
    )


def test_query_hazard_station_candidates(
    metar_poller,
    fixed_metar_time,
    monkeypatch,
):
    future_epoch = int(
        (
            fixed_metar_time
            + timedelta(hours=3)
        ).timestamp()
    )

    calls = []

    class FakeTable:
        def query(self, **kwargs):
            calls.append(kwargs)

            if len(calls) == 1:
                return {
                    "Items": [
                        {
                            "station_id":
                                "KJFK",
                            "airport_id":
                                "KJFK",
                            "hazard_id":
                                "hazard-1",
                            "hazard_version_key":
                                "hazard-1#v1",
                            "expires_at_epoch":
                                future_epoch,
                        }
                    ],
                    "LastEvaluatedKey": {
                        "hazard_version_key":
                            "hazard-1#v1",
                        "station_id":
                            "KJFK",
                    },
                }

            return {
                "Items": [
                    {
                        "station_id":
                            "KJFK",
                        "airport_id":
                            "KJFK",
                        "hazard_id":
                            "hazard-2",
                        "hazard_version_key":
                            "hazard-1#v1",
                        "expires_at_epoch":
                            future_epoch,
                    },
                    {
                        "station_id":
                            "KBOS",
                        "airport_id":
                            "KBOS",
                        "hazard_id":
                            "hazard-1",
                        "hazard_version_key":
                            "hazard-1#v1",
                        "expires_at_epoch":
                            future_epoch,
                    },
                ]
            }

    monkeypatch.setattr(
        metar_poller,
        "hazard_station_candidates",
        FakeTable(),
    )

    result = (
        metar_poller
        .query_hazard_station_candidates(
            "hazard-1#v1",
            fixed_metar_time,
        )
    )

    assert set(result) == {
        "KJFK",
        "KBOS",
    }

    assert (
        result["KJFK"]["airport_id"]
        == "KJFK"
    )

    assert (
        result["KJFK"]["hazard_ids"]
        == [
            "hazard-1",
            "hazard-2",
        ]
    )

    assert len(calls) == 2


def test_scan_active_hazard_station_candidates(
    metar_poller,
    fixed_metar_time,
    monkeypatch,
):
    future_epoch = int(
        (
            fixed_metar_time
            + timedelta(hours=2)
        ).timestamp()
    )

    expired_epoch = int(
        (
            fixed_metar_time
            - timedelta(minutes=1)
        ).timestamp()
    )

    class FakeTable:
        def scan(self, **kwargs):
            return {
                "Items": [
                    {
                        "station_id":
                            "KJFK",
                        "airport_id":
                            "KJFK",
                        "hazard_id":
                            "hazard-1",
                        "hazard_version_key":
                            "hazard-1#v1",
                        "expires_at_epoch":
                            future_epoch,
                    },
                    {
                        "station_id":
                            "KJFK",
                        "airport_id":
                            "KJFK",
                        "hazard_id":
                            "hazard-2",
                        "hazard_version_key":
                            "hazard-2#v1",
                        "expires_at_epoch":
                            future_epoch,
                    },
                    {
                        "station_id":
                            "KOLD",
                        "airport_id":
                            "KOLD",
                        "hazard_id":
                            "hazard-old",
                        "hazard_version_key":
                            "hazard-old#v1",
                        "expires_at_epoch":
                            expired_epoch,
                    },
                ]
            }

    monkeypatch.setattr(
        metar_poller,
        "hazard_station_candidates",
        FakeTable(),
    )

    result = (
        metar_poller
        .scan_active_hazard_station_candidates(
            fixed_metar_time
        )
    )

    assert set(result) == {
        "KJFK",
    }

    assert (
        result["KJFK"]["hazard_ids"]
        == [
            "hazard-1",
            "hazard-2",
        ]
    )


def test_resolve_station_scope_for_hazard_event(
    metar_poller,
    fixed_metar_time,
    monkeypatch,
):
    expected_scope = {
        "KJFK": {
            "station_id": "KJFK",
        }
    }

    monkeypatch.setattr(
        metar_poller,
        "query_hazard_station_candidates",
        lambda hazard_version_key, at_time:
            expected_scope,
    )

    event = {
        "id": "event-1",
        "detail-type":
            "hazard.stations.ready",
        "detail": {
            "hazard_version_key":
                "hazard-1#v1",
            "correlation_id":
                "corr-1",
        },
    }

    (
        trigger_type,
        scope,
        correlation_id,
        hazard_version_key,
    ) = metar_poller.resolve_station_scope(
        event,
        fixed_metar_time,
    )

    assert (
        trigger_type
        == "HAZARD_STATIONS_READY"
    )

    assert scope == expected_scope
    assert correlation_id == "corr-1"

    assert (
        hazard_version_key
        == "hazard-1#v1"
    )


def test_resolve_station_scope_for_schedule(
    metar_poller,
    fixed_metar_time,
    monkeypatch,
):
    expected_scope = {
        "KJFK": {
            "station_id": "KJFK",
        }
    }

    monkeypatch.setattr(
        metar_poller,
        (
            "scan_active_"
            "hazard_station_candidates"
        ),
        lambda at_time:
            expected_scope,
    )

    (
        trigger_type,
        scope,
        correlation_id,
        hazard_version_key,
    ) = metar_poller.resolve_station_scope(
        {
            "id": "schedule-event-1"
        },
        fixed_metar_time,
    )

    assert (
        trigger_type
        == "SCHEDULED_HSC_REFRESH"
    )

    assert scope == expected_scope

    assert (
        correlation_id
        == "schedule-event-1"
    )

    assert hazard_version_key is None


def test_build_s3_key_normalizes_prefix(
    metar_poller,
    fixed_metar_time,
):
    key = metar_poller.build_s3_key(
        poll_id="poll-1",
        raw_prefix=(
            "raw/source=metar/"
        ),
        received_at=fixed_metar_time,
    )

    assert key == (
        "raw/source=metar/"
        "year=2026/"
        "month=07/"
        "day=18/"
        "hour=12/"
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
        def put_object(
            self,
            **kwargs,
        ):
            captured.update(
                kwargs
            )

    body = {
        "type": "FeatureCollection",
        "features": [
            metar_feature
        ],
    }

    monkeypatch.setattr(
        metar_poller,
        "s3",
        FakeS3(),
    )

    key = (
        metar_poller
        .archive_raw_response(
            poll_id="poll-1",
            response_body=body,
            received_at=
                fixed_metar_time,
        )
    )

    assert (
        captured["Bucket"]
        == "test-weather-archive"
    )

    assert captured["Key"] == key

    assert (
        captured["ContentEncoding"]
        == "gzip"
    )

    assert json.loads(
        gzip.decompress(
            captured["Body"]
        )
    ) == body


@pytest.mark.parametrize(
    ("properties", "expected"),
    [
        (
            {"icaoId": "kjfk"},
            "KJFK",
        ),
        (
            {"station": "KSFO"},
            "KSFO",
        ),
        (
            {"station_id": "klax"},
            "KLAX",
        ),
        (
            {"id": 123},
            "123",
        ),
        (
            {},
            "metar-7",
        ),
    ],
)
def test_derive_partition_key(
    metar_poller,
    properties,
    expected,
):
    feature = {
        "type": "Feature",
        "properties": properties,
    }

    assert (
        metar_poller
        .derive_partition_key(
            feature,
            7,
        )
        == expected
    )


def test_derive_partition_key_fallback(
    metar_poller,
):
    assert (
        metar_poller
        .derive_partition_key(
            "bad",
            2,
        )
        == "metar-2"
    )


def test_chunked_preserves_order(
    metar_poller,
):
    assert metar_poller.chunked(
        [
            1,
            2,
            3,
            4,
            5,
        ],
        2,
    ) == [
        [
            1,
            2,
        ],
        [
            3,
            4,
        ],
        [
            5,
        ],
    ]


def test_publish_raw_records_builds_v2_contract(
    metar_poller,
    metar_feature,
    monkeypatch,
):
    calls = []

    class FakeKinesis:
        def put_records(
            self,
            **kwargs,
        ):
            calls.append(
                kwargs
            )

            return {
                "FailedRecordCount": 0,
                "Records": [
                    {}
                ],
            }

    monkeypatch.setattr(
        metar_poller,
        "kinesis",
        FakeKinesis(),
    )

    station_scope = {
        "KJFK": {
            "station_id":
                "KJFK",
            "airport_id":
                "KJFK",
            "hazard_ids": [
                "hazard-1"
            ],
            "hazard_version_keys": [
                "hazard-1#v1"
            ],
        }
    }

    published, failed = (
        metar_poller
        .publish_raw_records(
            poll_id="poll-1",
            received_at=(
                "2026-07-18T"
                "12:05:00+00:00"
            ),
            raw_s3_bucket=(
                "test-weather-archive"
            ),
            raw_s3_key=(
                "raw/metar.json.gz"
            ),
            records=[
                metar_feature
            ],
            station_scope=
                station_scope,
            trigger_type=(
                "HAZARD_STATIONS_READY"
            ),
            correlation_id="corr-1",
            trigger_hazard_version_key=(
                "hazard-1#v1"
            ),
        )
    )

    assert (
        published,
        failed,
    ) == (
        1,
        0,
    )

    first = (
        calls[0]["Records"][0]
    )

    payload = json.loads(
        first["Data"]
        .decode("utf-8")
    )

    assert (
        calls[0]["StreamName"]
        == "test-metar-raw"
    )

    assert (
        first["PartitionKey"]
        == "KJFK"
    )

    assert (
        payload["schema_version"]
        == "raw.noaa.metar.v2"
    )

    assert (
        payload["correlation_id"]
        == "corr-1"
    )

    assert (
        payload[
            "candidate_context"
        ]["airport_id"]
        == "KJFK"
    )

    assert (
        payload[
            "trigger_hazard_version_key"
        ]
        == "hazard-1#v1"
    )

    assert (
        payload["feature"]
        == metar_feature
    )


def test_publish_raw_records_batches_at_500(
    metar_poller,
    monkeypatch,
):
    batch_sizes = []

    class FakeKinesis:
        def put_records(
            self,
            **kwargs,
        ):
            batch_sizes.append(
                len(
                    kwargs["Records"]
                )
            )

            return {
                "FailedRecordCount": 0,
                "Records": [],
            }

    monkeypatch.setattr(
        metar_poller,
        "kinesis",
        FakeKinesis(),
    )

    features = [
        {
            "type": "Feature",
            "properties": {
                "icaoId":
                    f"K{index:04d}"
            },
        }
        for index in range(501)
    ]

    result = (
        metar_poller
        .publish_raw_records(
            poll_id="poll-batch",
            received_at=(
                "2026-07-18T"
                "12:05:00+00:00"
            ),
            raw_s3_bucket="bucket",
            raw_s3_key="key",
            records=features,
            station_scope={},
            trigger_type=(
                "SCHEDULED_HSC_REFRESH"
            ),
            correlation_id="corr-1",
            trigger_hazard_version_key=None,
        )
    )

    assert result == (
        501,
        0,
    )

    assert batch_sizes == [
        500,
        1,
    ]


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
        "resolve_station_scope",
        lambda event, at_time: (
            "HAZARD_STATIONS_READY",
            {
                "KJFK": {
                    "station_id":
                        "KJFK",
                    "airport_id":
                        "KJFK",
                    "hazard_ids": [
                        "hazard-1"
                    ],
                    "hazard_version_keys": [
                        "hazard-1#v1"
                    ],
                }
            },
            "corr-1",
            "hazard-1#v1",
        ),
    )

    monkeypatch.setattr(
        metar_poller,
        "fetch_noaa_metars",
        lambda station_ids: {
            "type":
                "FeatureCollection",
            "features": [
                metar_feature
            ],
        },
    )

    monkeypatch.setattr(
        metar_poller,
        "archive_raw_response",
        lambda **kwargs:
            (
                "raw/source=metar/"
                "test.json.gz"
            ),
    )

    monkeypatch.setattr(
        metar_poller,
        "publish_raw_records",
        lambda **kwargs: (
            1,
            0,
        ),
    )

    monkeypatch.setattr(
        metar_poller,
        "emit_metric",
        lambda **kwargs:
            metrics.append(
                kwargs
            ),
    )

    result = (
        metar_poller.lambda_handler(
            {
                "detail-type":
                    "hazard.stations.ready"
            },
            None,
        )
    )

    assert result["ok"] is True

    assert (
        result["poll_id"]
        == "poll-fixed"
    )

    assert (
        result[
            "candidate_station_count"
        ]
        == 1
    )

    assert (
        result["feature_count"]
        == 1
    )

    assert (
        result["published_count"]
        == 1
    )

    assert (
        result["request_source"]
        == "HAZARD_STATIONS_READY"
    )

    assert (
        metrics[0]["metrics"][
            "PollSuccess"
        ]
        == 1
    )


def test_lambda_handler_no_candidates(
    metar_poller,
    fixed_metar_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        metar_poller,
        "now_utc",
        lambda: fixed_metar_time,
    )

    monkeypatch.setattr(
        metar_poller,
        "resolve_station_scope",
        lambda event, at_time: (
            "SCHEDULED_HSC_REFRESH",
            {},
            "corr-empty",
            None,
        ),
    )

    monkeypatch.setattr(
        metar_poller,
        "emit_metric",
        lambda **kwargs:
            metrics.append(
                kwargs
            ),
    )

    result = (
        metar_poller.lambda_handler(
            {},
            None,
        )
    )

    assert result["ok"] is True

    assert (
        result[
            "candidate_station_count"
        ]
        == 0
    )

    assert (
        result["reason"]
        == (
            "NO_ACTIVE_"
            "HAZARD_STATIONS"
        )
    )

    assert (
        metrics[0]["metrics"][
            "ApiRequests"
        ]
        == 0
    )


def test_lambda_handler_publish_failure(
    metar_poller,
    metar_feature,
    fixed_metar_time,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        metar_poller,
        "now_utc",
        lambda: fixed_metar_time,
    )

    monkeypatch.setattr(
        metar_poller,
        "resolve_station_scope",
        lambda event, at_time: (
            "HAZARD_STATIONS_READY",
            {
                "KJFK": {
                    "station_id":
                        "KJFK",
                }
            },
            "corr-1",
            "hazard-1#v1",
        ),
    )

    monkeypatch.setattr(
        metar_poller,
        "fetch_noaa_metars",
        lambda station_ids: [
            metar_feature
        ],
    )

    monkeypatch.setattr(
        metar_poller,
        "archive_raw_response",
        lambda **kwargs:
            "raw/metar.json.gz",
    )

    monkeypatch.setattr(
        metar_poller,
        "publish_raw_records",
        lambda **kwargs: (
            0,
            1,
        ),
    )

    monkeypatch.setattr(
        metar_poller,
        "emit_metric",
        lambda **kwargs:
            metrics.append(
                kwargs
            ),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "Failed to publish "
            "1 of 1"
        ),
    ):
        metar_poller.lambda_handler(
            {},
            None,
        )

    assert (
        metrics[-1]["metrics"][
            "PollFailure"
        ]
        == 1
    )

    assert (
        metrics[-1]["metrics"][
            "RawArchiveSuccess"
        ]
        == 1
    )