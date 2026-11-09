"""Scripted Bedrock Converse client. No network, boto3, or model behavior."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any


ScriptItem = (
    Mapping[str, object]
    | BaseException
    | Callable[[Mapping[str, object]], Mapping[str, object]]
)


class FakeBedrockRuntime:
    """Records converse kwargs and returns the next scripted mapping."""

    def __init__(self, script: Sequence[ScriptItem] | None = None) -> None:
        self._script = list(script or ())
        self.requests: list[dict[str, object]] = []

    def converse(self, **kwargs: Any) -> Mapping[str, object]:
        self.requests.append(copy.deepcopy(dict(kwargs)))
        if not self._script:
            raise RuntimeError("fake bedrock has no remaining responses")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item) and not isinstance(item, Mapping):
            return item(self.requests[-1])
        if not isinstance(item, Mapping):
            raise TypeError("scripted item must be a mapping or exception")
        return item


def tool_use_response(
    *calls: tuple[str, Mapping[str, object]],
    extra_text: str | None = None,
    terminal_json: Mapping[str, object] | None = None,
) -> dict[str, object]:
    content: list[dict[str, object]] = []
    if extra_text is not None:
        content.append({"text": extra_text})
    if terminal_json is not None:
        content.append(
            {
                "text": json.dumps(
                    dict(terminal_json),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                )
            }
        )
    for index, (name, arguments) in enumerate(calls, start=1):
        content.append(
            {
                "toolUse": {
                    "toolUseId": f"vendor-tool-{index}",
                    "name": name,
                    "input": dict(arguments),
                }
            }
        )
    return {
        "stopReason": "tool_use",
        "output": {"message": {"role": "assistant", "content": content}},
    }


def end_turn_response(decision: Mapping[str, object]) -> dict[str, object]:
    return {
        "stopReason": "end_turn",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "text": json.dumps(
                            dict(decision),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=True,
                        )
                    }
                ],
            }
        },
    }


def stop_reason_response(stop_reason: str) -> dict[str, object]:
    return {
        "stopReason": stop_reason,
        "output": {"message": {"role": "assistant", "content": []}},
    }
