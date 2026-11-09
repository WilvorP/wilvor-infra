"""Loopback developer preview for the existing Wilvor Copilot page.

Bind address is 127.0.0.1 only. POST /ai/query is not implemented.
The Anthropic key is read from ANTHROPIC_API_KEY in this process and is
never written to the response or the access log.

Start from the repository root:

    $env:WILVOR_LOCAL_DEV_COPILOT = "1"
    $env:ANTHROPIC_API_KEY = "<key in this shell only>"
    python scripts/local_dev_copilot_server.py
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHARED_DIR = REPO_ROOT / "functions" / "shared"
for _path in (str(REPO_ROOT), str(SHARED_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from wilvor_ai.local_dev_anthropic_transport import BoundedAnthropicMessagesTransport
from wilvor_ai.local_dev_copilot import (
    LOCAL_DEV_PATH,
    _MAX_BODY_BYTES,
    admit_local_request,
    assert_loopback_bind,
    run_local_dev_copilot,
)
from wilvor_ai.local_dev_operator_reads import open_operator_dev_tables
from wilvor_ai.providers.anthropic_live_ops_messages import AnthropicLiveOpsMessagesProvider
from wilvor_ai.providers.anthropic_messages import AnthropicMessagesModelProvider


HOST = "127.0.0.1"
PORT = int(os.environ.get("WILVOR_LOCAL_DEV_COPILOT_PORT", "8765"))
_ALLOWED_ORIGINS = {"http://127.0.0.1:5173", "http://localhost:5173"}


class LocalDevCopilotHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _peer(self) -> str:
        host = self.client_address[0]
        return host

    def _origin(self) -> str | None:
        origin = self.headers.get("Origin")
        if origin in _ALLOWED_ORIGINS:
            return origin
        return None

    def _send(self, status: int, payload: dict[str, object] | None, origin: str | None) -> None:
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        origin = self._origin()
        if origin is None or self._peer() not in {"127.0.0.1", "::1"}:
            self._send(403, {"error": "nonlocal_request_rejected"}, None)
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self) -> None:
        origin = self._origin()
        try:
            admit_local_request(
                peer_host=self._peer(),
                host_header=self.headers.get("Host", ""),
                path=self.path,
                method="POST",
            )
        except Exception as exc:
            code = getattr(exc, "code", "request_rejected")
            status = 404 if code == "production_ai_query_not_served" else 403
            self._send(status, {"error": str(code), "productionAiActive": False}, origin)
            return
        length = self.headers.get("Content-Length")
        try:
            size = int(length or "0")
        except ValueError:
            size = _MAX_BODY_BYTES + 1
        if size < 1 or size > _MAX_BODY_BYTES:
            self._send(413, {"error": "request_too_large", "productionAiActive": False}, origin)
            return
        raw = self.rfile.read(size)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            self._send(400, {"error": "invalid_json", "productionAiActive": False}, origin)
            return
        if not isinstance(payload, dict):
            self._send(400, {"error": "invalid_json", "productionAiActive": False}, origin)
            return
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        try:
            transport = BoundedAnthropicMessagesTransport(api_key=key)
        except Exception as exc:
            code = getattr(exc, "code", "anthropic_api_key_missing")
            self._send(
                200,
                {
                    "error": str(code),
                    "productionAiActive": False,
                    "claimsAdmitted": False,
                    "sourceAuthenticity": "NOT_ESTABLISHED",
                },
                origin,
            )
            return
        from time import time

        result = run_local_dev_copilot(
            payload,
            planner=AnthropicMessagesModelProvider(client=transport),
            specialist_provider=AnthropicLiveOpsMessagesProvider(client=transport),
            open_tables=open_operator_dev_tables,
            now_epoch=int(time()),
            model_calls=transport.calls,
        )
        self._send(200, result, origin)


def main() -> None:
    if os.environ.get("WILVOR_LOCAL_DEV_COPILOT") != "1":
        raise SystemExit(
            "Refusing to start. Set WILVOR_LOCAL_DEV_COPILOT=1 in this shell. "
            "POST /ai/query stays disabled."
        )
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set in this process. "
            "Set it in the shell that starts the backend. Do not put it in a VITE variable."
        )
    assert_loopback_bind(HOST)
    server = ThreadingHTTPServer((HOST, PORT), LocalDevCopilotHandler)
    bound_host = server.server_address[0]
    assert_loopback_bind(bound_host)
    sys.stderr.write(
        "Wilvor local copilot preview at http://127.0.0.1:%s%s\n"
        "NOT PRODUCTION. POST /ai/query is not served.\n"
        "Evidence provenance: OPERATOR_AUTHENTICATED_DEV.\n"
        % (server.server_address[1], LOCAL_DEV_PATH)
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()


if __name__ == "__main__":
    main()
