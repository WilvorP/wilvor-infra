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
    def opener(*_, **__):
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
