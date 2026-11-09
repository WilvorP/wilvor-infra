"""Scripted Anthropic Messages client. No network, SDK, or model behavior."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any


DEFAULT_FAKE_MODEL_ID = "claude-sonnet-4-6"

ScriptItem = (
    Mapping[str, object]
    | BaseException
    | Callable[[Mapping[str, object]], Mapping[str, object]]
)


class FakeAnthropicMessages:
    """Records messages_create kwargs and returns the next scripted mapping."""

    def __init__(self, script: Sequence[ScriptItem] | None = None) -> None:
        self._script = list(script or ())
        self.requests: list[dict[str, object]] = []

    def messages_create(self, **kwargs: Any) -> Mapping[str, object]:
        self.requests.append(copy.deepcopy(dict(kwargs)))
        if not self._script:
            raise RuntimeError("fake anthropic has no remaining responses")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item) and not isinstance(item, Mapping):
            return item(self.requests[-1])
        if not isinstance(item, Mapping):
            raise TypeError("scripted item must be a mapping or exception")
        return item


def _envelope(
    *,
    stop_reason: str,
    content: list[dict[str, object]],
    model_id: str = DEFAULT_FAKE_MODEL_ID,
    message_id: str = "msg_test",
) -> dict[str, object]:
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model_id,
        "stop_reason": stop_reason,
        "content": content,
    }


def tool_use_response(
    *calls: tuple[str, Mapping[str, object]],
    extra_text: str | None = None,
    terminal_json: Mapping[str, object] | None = None,
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if extra_text is not None:
        content.append({"type": "text", "text": extra_text})
    if terminal_json is not None:
        content.append(
            {
                "type": "text",
                "text": json.dumps(
                    dict(terminal_json),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            }
        )
    for index, (name, arguments) in enumerate(calls, start=1):
        content.append(
            {
                "type": "tool_use",
                "id": f"toolu_vendor_{index}",
                "name": name,
                "input": dict(arguments),
            }
        )
    return _envelope(stop_reason="tool_use", content=content, model_id=model_id)


def end_turn_response(
    decision: Mapping[str, object],
    *,
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> dict[str, object]:
    return _envelope(
        stop_reason="end_turn",
        content=[
            {
                "type": "text",
                "text": json.dumps(
                    dict(decision),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ),
            }
        ],
        model_id=model_id,
    )


def stop_reason_response(
    stop_reason: str,
    *,
    model_id: str = DEFAULT_FAKE_MODEL_ID,
) -> dict[str, object]:
    return _envelope(stop_reason=stop_reason, content=[], model_id=model_id)
