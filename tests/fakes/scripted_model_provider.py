"""Scripted ModelProvider for offline specialist tests. No SDK or network."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from wilvor_ai.model_contracts import ModelDecision, ModelTurnRequest

ScriptItem = ModelDecision | BaseException | Callable[[ModelTurnRequest], ModelDecision]


class ScriptedModelProvider:
    """Returns the next scripted ModelDecision or raises a scripted exception."""

    def __init__(self, script: Sequence[ScriptItem]) -> None:
        self._script = list(script)
        self.requests: list[ModelTurnRequest] = []

    def complete(self, request: ModelTurnRequest) -> ModelDecision:
        self.requests.append(request)
        if not self._script:
            raise RuntimeError("scripted provider has no remaining decisions")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        if callable(item) and not isinstance(item, ModelDecision):
            return item(request)
        if not isinstance(item, ModelDecision):
            raise TypeError("scripted item must be a ModelDecision or exception")
        return item
