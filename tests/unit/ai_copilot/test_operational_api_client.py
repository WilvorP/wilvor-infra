import io
import json
import socket
from urllib.error import HTTPError

import pytest

from operational_api_client import (
    OperationalApiClient,
    OperationalApiInvalidResponse,
    OperationalApiNotFound,
    OperationalApiUnavailable,
)


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _):
        return self.payload


def test_success_and_safe_path_encoding():
    urls = []

    def opener(request, timeout):
        urls.append((request.full_url, timeout))
        return FakeResponse(
            json.dumps({"aircraft": {}}).encode()
        )

    client = OperationalApiClient(
        "https://example.test",
        opener=opener,
    )
    result = client.aircraft("a/1#x")

    assert result == {"aircraft": {}}
    assert urls[0][0].endswith("/aircraft/a%2F1%23x")


def test_404_is_not_found():
    calls = 0

    def opener(*_, **__):
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://example.test/x",
            404,
            "not found",
            {},
            io.BytesIO(),
        )

    client = OperationalApiClient(
        "https://example.test",
        opener=opener,
    )
    with pytest.raises(OperationalApiNotFound):
        client.airport("KSFO")
    assert calls == 1


def test_timeout_is_unavailable():
    def opener(*_, **__):
        raise socket.timeout()

    client = OperationalApiClient(
        "https://example.test",
        opener=opener,
    )
    with pytest.raises(OperationalApiUnavailable):
        client.freshness()


def test_malformed_json_is_rejected():
    client = OperationalApiClient(
        "https://example.test",
        opener=lambda *_args, **_kwargs: FakeResponse(
            b"not-json"
        ),
    )
    with pytest.raises(
        OperationalApiInvalidResponse
    ):
        client.overview()


def test_downstream_5xx_is_unavailable():
    def opener(*_, **__):
        raise HTTPError(
            "https://example.test/x",
            503,
            "unavailable",
            {},
            io.BytesIO(),
        )

    client = OperationalApiClient(
        "https://example.test",
        opener=opener,
    )
    with pytest.raises(OperationalApiUnavailable):
        client.system_health()


@pytest.mark.parametrize(
    "status",
    [429, 500, 502, 503, 504],
)
def test_retryable_http_status_then_success(status):
    calls = 0
    delays = []

    def opener(*_, **__):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise HTTPError(
                "https://example.test/overview",
                status,
                "transient",
                {"Retry-After": "0"},
                io.BytesIO(),
            )
        return FakeResponse(b'{"ok":true}')

    client = OperationalApiClient(
        "https://example.test",
        max_attempts=2,
        opener=opener,
        sleeper=delays.append,
    )

    assert client.overview() == {"ok": True}
    assert calls == 2
    assert delays == [0.0]


def test_transient_timeout_then_success():
    calls = 0

    def opener(*_, **__):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise socket.timeout()
        return FakeResponse(b'{"ok":true}')

    client = OperationalApiClient(
        "https://example.test",
        max_attempts=2,
        opener=opener,
        sleeper=lambda _: None,
    )

    assert client.freshness() == {"ok": True}
    assert calls == 2


@pytest.mark.parametrize("status", [400, 401, 403])
def test_ordinary_4xx_is_not_retried(status):
    calls = 0

    def opener(*_, **__):
        nonlocal calls
        calls += 1
        raise HTTPError(
            "https://example.test/overview",
            status,
            "client error",
            {},
            io.BytesIO(),
        )

    client = OperationalApiClient(
        "https://example.test",
        max_attempts=2,
        opener=opener,
        sleeper=lambda _: None,
    )

    with pytest.raises(OperationalApiUnavailable):
        client.overview()
    assert calls == 1


def test_retry_budget_prevents_late_second_attempt():
    class Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = Clock()
    calls = 0

    def opener(*_, **__):
        nonlocal calls
        calls += 1
        clock.value = 0.95
        raise socket.timeout()

    client = OperationalApiClient(
        "https://example.test",
        timeout_seconds=1,
        max_attempts=2,
        retry_budget_seconds=1,
        retry_backoff_seconds=0.1,
        opener=opener,
        sleeper=lambda _: None,
        clock=clock,
    )

    with pytest.raises(OperationalApiUnavailable):
        client.overview()
    assert calls == 1


@pytest.mark.parametrize(
    "url",
    [
        "file:///tmp/data",
        "https://user:pass@example.test",
        "https://example.test?target=other",
    ],
)
def test_unsafe_base_urls_are_rejected(url):
    with pytest.raises(ValueError):
        OperationalApiClient(url)
