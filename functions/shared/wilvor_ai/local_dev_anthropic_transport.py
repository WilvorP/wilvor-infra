"""Loopback developer transport for the existing Anthropic Messages adapters.

This module does not import the Anthropic SDK. The API key is read by the
caller and passed in. It is sent only on the outbound header and is removed
from raised errors. Responses are returned as mappings for the existing
``parse_messages_response`` and Live Ops parsers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib import error, request

from wilvor_ai.providers.anthropic_messages import MAX_TOKENS as MASTER_MAX_TOKENS
from wilvor_ai.providers.anthropic_live_ops_messages import (
    MAX_OUTPUT_TOKENS as LIVE_OPS_MAX_TOKENS,
)


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
# 2023-06-01 still accepts temperature. API version 2026-10-21 rejects
# temperature, top_p, and top_k on every model.
ANTHROPIC_VERSION = "2023-06-01"
# Master planning, aircraft tool turn, one related keyed query, terminal decision.
LOCAL_DEV_MAX_CALLS = 4
LOCAL_DEV_TIMEOUT_SECONDS = 90.0
LOCAL_DEV_MAX_RESPONSE_BYTES = 1_000_000
_ERROR_BODY_BYTES = 8_192
_TOKEN_CEILING = max(MASTER_MAX_TOKENS, LIVE_OPS_MAX_TOKENS)
_ERROR_TOKEN = re.compile(r"[a-z0-9_]{1,64}")
_REQUEST_ID = re.compile(r"req_[A-Za-z0-9]{6,128}")
_DIAGNOSTIC_ORDER = ("http_status", "error_type", "error_code", "request_id", "failure")


class LocalDevModelError(RuntimeError):
    """Closed local-preview model failure. The message is a stable code.

    ``diagnostics`` holds only status, Anthropic error type/code, request id,
    and a failure class. It never carries response prose, prompts, or headers.
    """

    def __init__(self, code: str, diagnostics: Mapping[str, str] | None = None) -> None:
        self.code = code
        self.diagnostics = {
            key: diagnostics[key]
            for key in _DIAGNOSTIC_ORDER
            if diagnostics and isinstance(diagnostics.get(key), str) and diagnostics[key]
        }
        super().__init__(code)

    def public_tokens(self) -> tuple[str, ...]:
        tokens = [self.code]
        for key in _DIAGNOSTIC_ORDER:
            value = self.diagnostics.get(key)
            if value:
                tokens.append("%s:%s" % (key, value))
        return tuple(tokens)


class BoundedAnthropicMessagesTransport:
    """One process-local Messages client with a hard call budget.

    ``send`` is injectable so tests decode responses without a paid call.
    The default sender performs one HTTPS POST and does not retry.
    """

    def __init__(
        self,
        *,
        api_key: str,
        send: Callable[[Mapping[str, Any], Mapping[str, str]], Mapping[str, Any]] | None = None,
        max_calls: int = LOCAL_DEV_MAX_CALLS,
        timeout_seconds: float = LOCAL_DEV_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise LocalDevModelError("anthropic_api_key_missing")
        if api_key != api_key.strip() or any(character.isspace() for character in api_key):
            raise LocalDevModelError("anthropic_api_key_missing")
        if type(max_calls) is not int or max_calls < 1 or max_calls > LOCAL_DEV_MAX_CALLS:
            raise LocalDevModelError("anthropic_call_budget_exceeded")
        if type(timeout_seconds) not in {int, float} or timeout_seconds <= 0:
            raise LocalDevModelError("anthropic_timeout_invalid")
        if timeout_seconds > LOCAL_DEV_TIMEOUT_SECONDS:
            raise LocalDevModelError("anthropic_timeout_invalid")
        self._api_key = api_key
        self._send = send or self._https_send
        self._max_calls = max_calls
        self._timeout_seconds = float(timeout_seconds)
        self.calls: list[dict[str, object]] = []

    def __repr__(self) -> str:
        return "BoundedAnthropicMessagesTransport(calls=%s)" % (len(self.calls),)

    def messages_create(self, **kwargs: Any) -> Mapping[str, object]:
        if len(self.calls) >= self._max_calls:
            raise LocalDevModelError("anthropic_call_budget_exceeded")
        max_tokens = kwargs.get("max_tokens")
        if type(max_tokens) is not int or max_tokens < 1 or max_tokens > _TOKEN_CEILING:
            raise LocalDevModelError("anthropic_max_tokens_rejected")
        model = kwargs.get("model")
        if not isinstance(model, str) or not model.strip():
            raise LocalDevModelError("anthropic_model_rejected")
        body = {key: value for key, value in kwargs.items()}
        try:
            encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise LocalDevModelError("anthropic_request_not_serializable") from exc
        if self._api_key.encode("utf-8") in encoded:
            raise LocalDevModelError("anthropic_api_key_in_request_body")
        headers = {
            "content-type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": self._api_key,
        }
        try:
            payload = self._send(body, headers)
        except LocalDevModelError:
            raise
        except Exception as exc:
            self._raise_transport(exc)
        if not isinstance(payload, Mapping):
            raise LocalDevModelError("anthropic_response_not_json")
        record = {
            "model": model,
            "max_tokens": max_tokens,
            "stop_reason": payload.get("stop_reason"),
            "usage": _usage(payload.get("usage")),
        }
        self.calls.append(record)
        return payload

    def _https_send(
        self,
        body: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> Mapping[str, Any]:
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        outgoing = request.Request(
            ANTHROPIC_MESSAGES_URL,
            data=encoded,
            headers=dict(headers),
            method="POST",
        )
        try:
            with request.urlopen(outgoing, timeout=self._timeout_seconds) as response:
                raw = response.read(LOCAL_DEV_MAX_RESPONSE_BYTES + 1)
        except error.HTTPError:
            raise
        except error.URLError:
            raise
        if len(raw) > LOCAL_DEV_MAX_RESPONSE_BYTES:
            raise LocalDevModelError("anthropic_response_too_large")
        try:
            loaded = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            raise LocalDevModelError(
                "anthropic_response_not_json",
                {"failure": "json"},
            ) from None
        if not isinstance(loaded, dict):
            raise LocalDevModelError(
                "anthropic_response_not_json",
                {"failure": "json"},
            )
        return loaded

    def _raise_transport(self, exc: Exception) -> None:
        """Classify one failed send. Raw exception text is never re-raised."""

        if self._api_key and self._api_key in str(exc):
            raise LocalDevModelError("anthropic_transport_failed") from None
        if isinstance(exc, error.HTTPError):
            status = exc.code if type(exc.code) is int else None
            error_type, error_code = _error_fields(exc)
            request_id = _request_id(exc.headers.get("request-id") if exc.headers else None)
            if request_id is None and exc.headers is not None:
                request_id = _request_id(exc.headers.get("x-request-id"))
            diagnostics = _http_diagnostics(status, error_type, error_code, request_id)
            raise LocalDevModelError(_safe_status(status), diagnostics) from None
        if _is_timeout(exc):
            raise LocalDevModelError("anthropic_timeout", {"failure": "timeout"}) from None
        if isinstance(exc, error.URLError) or isinstance(exc, ConnectionError):
            raise LocalDevModelError(
                "anthropic_connection_failed",
                {"failure": "connection"},
            ) from None
        if isinstance(exc, (UnicodeError, json.JSONDecodeError)):
            raise LocalDevModelError(
                "anthropic_response_not_json",
                {"failure": "json"},
            ) from None
        raise LocalDevModelError("anthropic_transport_failed") from None


def _usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    usage: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens"):
        item = value.get(name)
        if type(item) is int and item >= 0:
            usage[name] = item
    return usage or None


def _safe_status(code: object) -> str:
    if code == 401 or code == 403:
        return "anthropic_authentication_failed"
    if code == 408:
        return "anthropic_timeout"
    if code == 429:
        return "anthropic_rate_limited"
    if type(code) is int and 400 <= code < 500:
        return "anthropic_request_rejected"
    if type(code) is int and code >= 500:
        return "anthropic_provider_unavailable"
    return "anthropic_transport_failed"


def _http_diagnostics(
    status: int | None,
    error_type: str,
    error_code: str,
    request_id: str | None,
) -> dict[str, str]:
    diagnostics: dict[str, str] = {"failure": "http"}
    if status is not None:
        diagnostics["http_status"] = str(status)
    if error_type:
        diagnostics["error_type"] = error_type
    if error_code:
        diagnostics["error_code"] = error_code
    if request_id:
        diagnostics["request_id"] = request_id
    return diagnostics


def _error_fields(exc: error.HTTPError) -> tuple[str, str]:
    try:
        raw = exc.read(_ERROR_BODY_BYTES)
    except Exception:
        return "", ""
    if not isinstance(raw, (bytes, bytearray)):
        return "", ""
    try:
        loaded = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return "", ""
    if not isinstance(loaded, dict):
        return "", ""
    body = loaded.get("error")
    source = body if isinstance(body, dict) else loaded
    if not isinstance(source, dict):
        return "", ""
    return _error_token(source.get("type")), _error_token(source.get("code"))


def _error_token(value: object) -> str:
    if isinstance(value, str) and _ERROR_TOKEN.fullmatch(value):
        return value
    return ""


def _request_id(value: object) -> str | None:
    if isinstance(value, str) and _REQUEST_ID.fullmatch(value):
        return value
    return None


def _is_timeout(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    if isinstance(exc, error.URLError) and isinstance(exc.reason, TimeoutError):
        return True
    return type(exc).__name__ in {"TimeoutError", "timeout"}


__all__ = [
    "ANTHROPIC_MESSAGES_URL",
    "LOCAL_DEV_MAX_CALLS",
    "LOCAL_DEV_TIMEOUT_SECONDS",
    "BoundedAnthropicMessagesTransport",
    "LocalDevModelError",
]
