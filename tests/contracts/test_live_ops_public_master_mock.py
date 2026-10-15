"""A Master-shaped caller using only the public Live Ops contract.

The fake specialist returns a synthetic result. That result is not an LO2
verification, and the test does not call tools, providers, or LangGraph.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import replace

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_public_contracts import (
    LiveOpsRunRequest,
    LiveOpsRunResult,
    LiveOpsSpecialistPort,
    LiveOpsTrustedContext,
    present_live_ops_result,
    validate_live_ops_presentation,
)


EXAMPLES_PATH = "tests/fixtures/live_ops_public_examples.py"


class FakeLiveOpsSpecialist:
    """In-process stand-in for the future specialist port."""

    def __init__(self) -> None:
        self.calls = 0

    def run(self, request: LiveOpsRunRequest, context: LiveOpsTrustedContext) -> LiveOpsRunResult:
        if not isinstance(request, LiveOpsRunRequest) or not isinstance(context, LiveOpsTrustedContext):
            raise TypeError("Live Ops run requires a request and a trusted context")
        self.calls += 1
        examples_name = "live_ops_public_examples"
        examples = sys.modules[examples_name]
        return examples.completed_all_verified(
            request_id=request.request_id,
            conversation_id=request.conversation_id,
            correlation_id=context.correlation_id,
        )


def test_mock_master_keeps_context_out_of_graph_state(load_repo_module):
    assert "langgraph" not in sys.modules
    examples = load_repo_module("live_ops_public_examples", EXAMPLES_PATH)
    specialist = FakeLiveOpsSpecialist()
    assert isinstance(specialist, LiveOpsSpecialistPort)

    async def consume() -> tuple[dict[str, object], LiveOpsRunResult]:
        try:
            LiveOpsRunRequest.from_dict({"schema_version": examples.LIVE_OPS_PUBLIC_SCHEMA_VERSION})
        except ContractValidationError:
            parsed = None
        else:
            parsed = "malformed request entered the caller"
        assert parsed is None
        assert specialist.calls == 0

        request = LiveOpsRunRequest.from_dict(examples.sample_request().to_dict())
        context = examples.trusted_context()
        result = await asyncio.to_thread(specialist.run, request, context)
        state = {
            "live_ops_public_result": result.to_dict(),
            "factual_text": result.answer,
            "mandatory_limitations": list(result.mandatory_limitations),
        }
        return state, result

    state, trusted = asyncio.run(consume())

    assert specialist.calls == 1
    assert "tables" not in state
    assert "context" not in state
    assert state["factual_text"] == trusted.answer
    assert state["mandatory_limitations"] == list(trusted.mandatory_limitations)
    stored = state["live_ops_public_result"]
    assert isinstance(stored, dict)
    assert stored["correlation_id"] == trusted.correlation_id
    assert "raw_tool_result" not in stored

    section = present_live_ops_result(trusted)
    validate_live_ops_presentation(trusted, section)

    rewritten = LiveOpsRunResult.from_dict(
        {**stored, "mandatory_limitations": [], "answer": "Unauthorized rewrite."}
    )
    with pytest.raises(ContractValidationError, match="factual_text_mismatch"):
        validate_live_ops_presentation(trusted, present_live_ops_result(rewritten))
    with pytest.raises(ContractValidationError, match="mandatory_limitations_mismatch"):
        validate_live_ops_presentation(
            trusted,
            replace(present_live_ops_result(trusted), mandatory_limitations=()),
        )


def test_mock_rejects_rewritten_claims_and_wrong_run_types(load_repo_module):
    examples = load_repo_module("live_ops_public_examples", EXAMPLES_PATH)
    specialist = FakeLiveOpsSpecialist()
    request = examples.sample_request()
    context = examples.trusted_context()
    trusted = specialist.run(request, context)
    promoted = examples.snapshot_claims()["SCOPED_COUNT"]
    rewritten = replace(
        present_live_ops_result(trusted),
        verified_claims=trusted.verified_claims + (promoted,),
    )
    with pytest.raises(ContractValidationError, match="verified_claims_mismatch"):
        validate_live_ops_presentation(trusted, rewritten)
    with pytest.raises(TypeError):
        specialist.run({"user_text": "Question text."}, context)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        specialist.run(request, {"correlation_id": "corr-synthetic-1"})  # type: ignore[arg-type]
