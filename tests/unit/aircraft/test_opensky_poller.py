from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest


@pytest.fixture
def opensky_poller(load_repo_module, monkeypatch):
    module = load_repo_module(
        "unit_opensky_poller_app",
        "functions/opensky_poller/app.py",
    )

    monkeypatch.setenv("OPENSKY_SECRET_ARN", "test-opensky-secret")
    monkeypatch.setenv("OPENSKY_TOKEN_URL", "https://token.example.test")
    monkeypatch.setenv("OPENSKY_STATES_URL", "https://states.example.test")
    monkeypatch.setenv("OPENSKY_LAMIN", "37.0")
    monkeypatch.setenv("OPENSKY_LOMIN", "-123.0")
    monkeypatch.setenv("OPENSKY_LAMAX", "38.5")
    monkeypatch.setenv("OPENSKY_LOMAX", "-121.5")
    monkeypatch.setenv("AIRCRAFT_ARCHIVE_BUCKET", "test-aircraft-archive")
    monkeypatch.setenv("AIRCRAFT_RAW_STREAM_NAME", "test-aircraft-raw")

    module._cached_secret = None
    module._cached_token = None
    module._cached_token_expires_at = 0
    return module


@pytest.mark.parametrize(
    "secret_payload",
    [
        {
            "client_id": "client-1",
            "client_secret": "secret-1",
        },
        {
            "clientId": "client-1",
            "clientSecret": "secret-1",
        },
    ],
)
def test_get_secret_supports_both_key_formats(
    opensky_poller,
    secret_payload,
    monkeypatch,
):
    calls = []

    class FakeSecrets:
        def get_secret_value(self, **kwargs):
            calls.append(kwargs)
            return {"SecretString": json.dumps(secret_payload)}

    monkeypatch.setattr(opensky_poller, "secrets", FakeSecrets())

    assert opensky_poller.get_secret() == {
        "client_id": "client-1",
        "client_secret": "secret-1",
    }
    assert opensky_poller.get_secret() == {
        "client_id": "client-1",
        "client_secret": "secret-1",
    }
    assert calls == [{"SecretId": "test-opensky-secret"}]


def test_get_secret_rejects_missing_credentials(
    opensky_poller,
    monkeypatch,
):
    class FakeSecrets:
        def get_secret_value(self, **kwargs):
            return {"SecretString": json.dumps({"client_id": "only-id"})}

    monkeypatch.setattr(opensky_poller, "secrets", FakeSecrets())

    with pytest.raises(ValueError, match="must contain"):
        opensky_poller.get_secret()


def test_get_access_token_uses_cached_unexpired_token(
    opensky_poller,
    monkeypatch,
):
    opensky_poller._cached_token = "cached-token"
    opensky_poller._cached_token_expires_at = 10_000

    monkeypatch.setattr(opensky_poller.time, "time", lambda: 1_000)
    monkeypatch.setattr(
        opensky_poller,
        "get_secret",
        lambda: pytest.fail("secret should not be read"),
    )

    assert opensky_poller.get_access_token() == "cached-token"


def test_get_access_token_requests_and_caches_new_token(
    opensky_poller,
    monkeypatch,
):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "access_token": "new-token",
                    "expires_in": 900,
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(opensky_poller.time, "time", lambda: 1_000)
    monkeypatch.setattr(
        opensky_poller,
        "get_secret",
        lambda: {
            "client_id": "client-1",
            "client_secret": "secret-1",
        },
    )
    monkeypatch.setattr(
        opensky_poller.urllib.request,
        "urlopen",
        fake_urlopen,
    )

    assert opensky_poller.get_access_token() == "new-token"
    assert opensky_poller._cached_token == "new-token"
    assert opensky_poller._cached_token_expires_at == 1_900
    assert captured["timeout"] == 20
    assert captured["request"].full_url == "https://token.example.test"

    body = captured["request"].data.decode("utf-8")
    assert "grant_type=client_credentials" in body
    assert "client_id=client-1" in body
    assert "client_secret=secret-1" in body


def test_archive_raw_response_writes_expected_s3_object(
    opensky_poller,
    monkeypatch,
):
    captured = {}
    fixed_time = datetime(2026, 7, 18, 8, 15, tzinfo=timezone.utc)

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(opensky_poller, "s3", FakeS3())
    monkeypatch.setattr(opensky_poller, "now_utc", lambda: fixed_time)

    body = {"time": 123, "states": [["abc123"]]}

    key = opensky_poller.archive_raw_response("poll-1", body)

    assert key == (
        "raw/source=opensky/"
        "year=2026/month=07/day=18/hour=08/poll-1.json"
    )
    assert captured["Bucket"] == "test-aircraft-archive"
    assert captured["Key"] == key
    assert json.loads(captured["Body"].decode("utf-8")) == body


def test_publish_raw_records_builds_one_event_per_state(
    opensky_poller,
    monkeypatch,
):
    calls = []

    class FakeKinesis:
        def put_records(self, **kwargs):
            calls.append(kwargs)
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

    monkeypatch.setattr(opensky_poller, "kinesis", FakeKinesis())

    response = {
        "time": 12345,
        "states": [
            ["abc123"],
            [None],
        ],
    }

    published, failed = opensky_poller.publish_raw_records(
        poll_id="poll-1",
        opensky_response=response,
        fetched_at_utc="2026-07-18T12:00:00+00:00",
    )

    assert published == 1
    assert failed == 1
    assert len(calls) == 1
    assert calls[0]["StreamName"] == "test-aircraft-raw"

    first = calls[0]["Records"][0]
    second = calls[0]["Records"][1]

    assert first["PartitionKey"] == "abc123"
    assert second["PartitionKey"] == "unknown-1"

    first_event = json.loads(first["Data"].decode("utf-8"))
    assert first_event["schema_version"] == "opensky_aircraft_raw.v1"
    assert first_event["source"] == "opensky"
    assert first_event["poll_id"] == "poll-1"
    assert first_event["raw_index"] == 0
    assert first_event["raw_state_vector"] == ["abc123"]


def test_handler_success_returns_poll_summary(
    opensky_poller,
    monkeypatch,
):
    metrics = []
    response = {
        "time": 12345,
        "states": [["abc123"], ["def456"]],
    }

    monkeypatch.setattr(
        opensky_poller,
        "fetch_opensky_states",
        lambda: response,
    )
    monkeypatch.setattr(
        opensky_poller,
        "archive_raw_response",
        lambda **kwargs: "raw/source=opensky/test.json",
    )
    monkeypatch.setattr(
        opensky_poller,
        "publish_raw_records",
        lambda **kwargs: (2, 0),
    )
    monkeypatch.setattr(
        opensky_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )
    monkeypatch.setattr(
        opensky_poller.uuid,
        "uuid4",
        lambda: "poll-fixed",
    )
    monkeypatch.setattr(
        opensky_poller,
        "now_utc_iso",
        lambda: "2026-07-18T12:00:00+00:00",
    )

    result = opensky_poller.handler(
        {},
        SimpleNamespace(aws_request_id="request-1"),
    )

    assert result == {
        "ok": True,
        "mode": "real-opensky-poller",
        "poll_id": "poll-fixed",
        "states_count": 2,
        "published_to_kinesis": 2,
        "failed_kinesis_records": 0,
        "raw_s3_key": "raw/source=opensky/test.json",
    }
    assert metrics[0]["metrics"]["PollSuccess"] == 1
    assert metrics[0]["metrics"]["PollFailure"] == 0


def test_handler_failure_emits_failure_metric_and_reraises(
    opensky_poller,
    monkeypatch,
):
    metrics = []

    monkeypatch.setattr(
        opensky_poller,
        "fetch_opensky_states",
        lambda: (_ for _ in ()).throw(TimeoutError("OpenSky timeout")),
    )
    monkeypatch.setattr(
        opensky_poller,
        "emit_metric",
        lambda **kwargs: metrics.append(kwargs),
    )
    monkeypatch.setattr(
        opensky_poller.uuid,
        "uuid4",
        lambda: "poll-fixed",
    )

    with pytest.raises(TimeoutError, match="OpenSky timeout"):
        opensky_poller.handler(
            {},
            SimpleNamespace(aws_request_id="request-2"),
        )

    assert metrics[0]["metrics"]["PollSuccess"] == 0
    assert metrics[0]["metrics"]["PollFailure"] == 1
    assert metrics[0]["properties"]["error_type"] == "TimeoutError"
