import json
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


class OperationalApiError(RuntimeError):
    pass


class OperationalApiNotFound(OperationalApiError):
    pass


class OperationalApiUnavailable(OperationalApiError):
    pass


class OperationalApiInvalidResponse(OperationalApiError):
    pass


class OperationalApiClient:
    RETRYABLE_HTTP_STATUSES = {
        408,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        max_response_bytes: int = 262144,
        max_attempts: int = 2,
        retry_budget_seconds: float = 6.0,
        retry_backoff_seconds: float = 0.1,
        opener=urlopen,
        sleeper=time.sleep,
        clock=time.monotonic,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Operational API base URL must be a fixed HTTP(S) origin"
            )
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds is out of bounds")
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes is too small")
        if max_attempts < 1 or max_attempts > 3:
            raise ValueError("max_attempts is out of bounds")
        if (
            retry_budget_seconds < 0.1
            or retry_budget_seconds > 15
        ):
            raise ValueError(
                "retry_budget_seconds is out of bounds"
            )
        if (
            retry_backoff_seconds < 0
            or retry_backoff_seconds > 1
        ):
            raise ValueError(
                "retry_backoff_seconds is out of bounds"
            )

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.max_attempts = max_attempts
        self.retry_budget_seconds = (
            retry_budget_seconds
        )
        self.retry_backoff_seconds = (
            retry_backoff_seconds
        )
        self._opener = opener
        self._sleeper = sleeper
        self._clock = clock

    @staticmethod
    def _identifier(value: Any, name: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{name} is required")
        if len(text) > 256 or any(ord(char) < 32 for char in text):
            raise ValueError(f"{name} is invalid")
        return quote(text, safe="")

    @staticmethod
    def _limit(value: Any, maximum: int) -> int:
        if isinstance(value, bool):
            raise ValueError("limit must be an integer")
        try:
            limit = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit must be an integer") from exc
        if limit < 1 or limit > maximum:
            raise ValueError(
                f"limit must be between 1 and {maximum}"
            )
        return limit

    def _request(self, path: str) -> dict[str, Any]:
        if not path.startswith("/") or "://" in path:
            raise ValueError("Operational API path is invalid")

        request = Request(
            f"{self.base_url}{path}",
            headers={"accept": "application/json"},
            method="GET",
        )

        deadline = (
            self._clock()
            + self.retry_budget_seconds
        )
        for attempt in range(self.max_attempts):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise OperationalApiUnavailable(
                    "Operational API retry budget was exhausted"
                )
            attempt_timeout = min(
                self.timeout_seconds,
                max(0.05, remaining),
            )
            try:
                with self._opener(
                    request,
                    timeout=attempt_timeout,
                ) as response:
                    raw = response.read(
                        self.max_response_bytes + 1
                    )
                if len(raw) > self.max_response_bytes:
                    raise OperationalApiInvalidResponse(
                        "Operational API response exceeded the size limit"
                    )
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (
                    UnicodeDecodeError,
                    json.JSONDecodeError,
                ) as exc:
                    raise OperationalApiInvalidResponse(
                        "Operational API returned malformed JSON"
                    ) from exc
                if not isinstance(payload, dict):
                    raise OperationalApiInvalidResponse(
                        "Operational API response must be a JSON object"
                    )
                return payload
            except HTTPError as exc:
                if exc.code == 404:
                    raise OperationalApiNotFound(
                        "Operational subject was not found"
                    ) from exc
                if (
                    exc.code
                    not in self.RETRYABLE_HTTP_STATUSES
                ):
                    raise OperationalApiUnavailable(
                        "Operational API request failed"
                    ) from exc
                delay = self._retry_delay(
                    attempt,
                    exc,
                )
            except OperationalApiInvalidResponse:
                raise
            except (URLError, TimeoutError, socket.timeout) as exc:
                delay = self._retry_delay(attempt)

            if (
                attempt + 1 >= self.max_attempts
                or self._clock() + delay >= deadline
            ):
                raise OperationalApiUnavailable(
                    "Operational API is unavailable"
                )
            self._sleeper(delay)

        raise OperationalApiUnavailable(
            "Operational API is unavailable"
        )

    def _retry_delay(
        self,
        attempt: int,
        error: HTTPError | None = None,
    ) -> float:
        delay = min(
            self.retry_backoff_seconds
            * (2 ** attempt),
            0.5,
        )
        if error is None or error.headers is None:
            return delay
        retry_after = error.headers.get("Retry-After")
        if retry_after is None:
            return delay
        try:
            return min(
                max(float(retry_after), 0.0),
                0.5,
            )
        except (TypeError, ValueError):
            return delay

    def health(self) -> dict[str, Any]:
        return self._request("/health")

    def overview(self) -> dict[str, Any]:
        return self._request("/overview")

    def freshness(self) -> dict[str, Any]:
        return self._request("/freshness")

    def system_health(self) -> dict[str, Any]:
        return self._request("/system-health")

    def aircraft(self, aircraft_id: str) -> dict[str, Any]:
        value = self._identifier(aircraft_id, "aircraftId")
        return self._request(f"/aircraft/{value}")

    def airport(self, airport_id: str) -> dict[str, Any]:
        value = self._identifier(airport_id, "airportId")
        return self._request(f"/airports/{value}")

    def recommendation(
        self,
        recommendation_id: str,
    ) -> dict[str, Any]:
        value = self._identifier(
            recommendation_id,
            "recommendationId",
        )
        return self._request(
            f"/recommendations/{value}"
        )

    def alert(self, alert_id: str) -> dict[str, Any]:
        value = self._identifier(alert_id, "alertId")
        return self._request(f"/alerts/{value}")

    def active_encounters(
        self,
        limit: int = 25,
    ) -> dict[str, Any]:
        value = self._limit(limit, 50)
        return self._request(
            "/encounters/active?"
            + urlencode({"limit": value})
        )

    def active_recommendations(
        self,
        limit: int = 25,
    ) -> dict[str, Any]:
        value = self._limit(limit, 50)
        return self._request(
            "/recommendations/active?"
            + urlencode({"limit": value})
        )

    def active_alerts(
        self,
        limit: int = 25,
    ) -> dict[str, Any]:
        value = self._limit(limit, 50)
        return self._request(
            "/alerts/active?"
            + urlencode({"limit": value})
        )
