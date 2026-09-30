"""Instruction versioning and v2 evidence-interpretation clarification tests."""

from __future__ import annotations

import pytest

from wilvor_ai.historical_analytics import historical_analytics_tool_schemas
from wilvor_ai.historical_specialist import HISTORICAL_SPECIALIST_INSTRUCTION_REF
from wilvor_ai.model_contracts import ModelTurnRequest
from wilvor_ai.providers.anthropic_messages import (
    DEFAULT_MODEL_ID as ANTHROPIC_MODEL_ID,
    build_messages_kwargs,
)
from wilvor_ai.providers.bedrock_converse import (
    DEFAULT_MODEL_ID as BEDROCK_MODEL_ID,
    build_converse_kwargs,
)
from wilvor_ai.providers.errors import ModelProviderError
from wilvor_ai.providers.instructions import (
    HISTORICAL_SPECIALIST_INSTRUCTION_REF as REGISTRY_V1_REF,
    HISTORICAL_SPECIALIST_V1_INSTRUCTION,
    HISTORICAL_SPECIALIST_V1_INSTRUCTION_REF,
    HISTORICAL_SPECIALIST_V2_INSTRUCTION,
    HISTORICAL_SPECIALIST_V2_INSTRUCTION_REF,
    resolve_instruction,
)


FROZEN_V1_INSTRUCTION = """\
You are the Wilvor Historical Analytics Specialist model.

Scope:
- Answer only from persisted historical analytics evidence.
- Use only the tools supplied in this turn.
- Do not infer current aircraft/hazard/airport state.
- Do not infer geography, airport impact, or spatial conclusions.
- Do not forecast or predict.
- Do not recommend, advise, or fabricate operational actions.

Tools:
- Call only supplied tool names and catalog arguments.
- Never pass as_of_utc, tool_call_id, operations, SQL, workgroup, database,
  bucket, or other trusted/runtime fields as tool arguments.
- Evaluated as_of_utc on tool-result projections is runtime context, not an
  argument you may set or override.

Evidence rules:
- Coverage blocked or unevaluable is not a verified zero.
- PARTIAL is not exact.
- Hazard-version materialization/event time is not validity-interval overlap.
- Do not invent tool results, counts, record identities, or limitations.
- When prior tool-result projections are supplied, treat them as the only
  evidence you may cite. Use each projection's Wilvor tool_call_id on claims.

Decision format:
- Return exactly one decision.
- To request tools, use native tool calls only. Do not emit a terminal JSON
  decision in the same turn as tool calls.
- When finished or when the request is out of scope, emit only the structured
  terminal JSON object: FINAL_CLAIMS, UNSUPPORTED, or REFUSAL.
- FINAL_CLAIMS must use typed claims only (EXACT_COUNT, LOWER_BOUND_COUNT,
  VERIFIED_ZERO, HISTORICAL_WINDOW, RECORD_IDENTITY, LIMITATION, UNAVAILABLE).
- Claims must reference Wilvor tool_call_id values from the supplied
  projections.
- Do not write a final factual answer, candidate answer, or operational prose.
- The deterministic Wilvor renderer will produce any user-visible answer.

UNSUPPORTED is a capability judgment (current state, geography, forecast,
action request, insufficient evidence, or out of catalog). Do not use it for
provider or transport failure.
"""


def test_v1_exists_and_is_byte_for_byte_unchanged():
    assert REGISTRY_V1_REF == "wilvor.historical.specialist.v1"
    assert HISTORICAL_SPECIALIST_V1_INSTRUCTION_REF == "wilvor.historical.specialist.v1"
    assert HISTORICAL_SPECIALIST_V1_INSTRUCTION == FROZEN_V1_INSTRUCTION
    assert resolve_instruction("wilvor.historical.specialist.v1") == FROZEN_V1_INSTRUCTION


def test_v2_exists_and_extends_v1():
    assert HISTORICAL_SPECIALIST_V2_INSTRUCTION_REF == "wilvor.historical.specialist.v2"
    assert HISTORICAL_SPECIALIST_V2_INSTRUCTION.startswith(FROZEN_V1_INSTRUCTION)
    assert HISTORICAL_SPECIALIST_V2_INSTRUCTION != FROZEN_V1_INSTRUCTION
    assert resolve_instruction("wilvor.historical.specialist.v2") == (
        HISTORICAL_SPECIALIST_V2_INSTRUCTION
    )


def test_specialist_requests_v2_not_v1():
    assert HISTORICAL_SPECIALIST_INSTRUCTION_REF == "wilvor.historical.specialist.v2"
    assert HISTORICAL_SPECIALIST_INSTRUCTION_REF != REGISTRY_V1_REF


def test_unknown_instruction_ref_still_fails_closed():
    with pytest.raises(ModelProviderError, match="unknown_instruction_ref"):
        resolve_instruction("wilvor.historical.specialist.v9")
    with pytest.raises(ModelProviderError, match="unknown_instruction_ref"):
        resolve_instruction(None)


def test_both_provider_adapters_resolve_v2_from_shared_registry():
    request = ModelTurnRequest(
        user_text="How many historical encounters occurred?",
        instruction_ref=HISTORICAL_SPECIALIST_V2_INSTRUCTION_REF,
        tools=historical_analytics_tool_schemas(),
    )
    anthropic_kwargs = build_messages_kwargs(request, ANTHROPIC_MODEL_ID)
    bedrock_kwargs = build_converse_kwargs(request, BEDROCK_MODEL_ID)
    expected = resolve_instruction(HISTORICAL_SPECIALIST_V2_INSTRUCTION_REF)
    assert anthropic_kwargs["system"] == expected
    assert bedrock_kwargs["system"] == [{"text": expected}]
    assert expected == HISTORICAL_SPECIALIST_V2_INSTRUCTION


def test_v2_certified_zero_guidance():
    text = HISTORICAL_SPECIALIST_V2_INSTRUCTION
    assert "NOT_FOUND" in text
    assert "EVALUABLE" in text
    assert "exact cardinality 0" in text
    assert "is_exact true" in text
    assert "VERIFIED_ZERO" in text
    assert "Do not use EXACT_COUNT for that certified-zero evidence." in text
    assert "never VERIFIED_ZERO" in text


def test_v2_partial_truncation_guidance():
    text = HISTORICAL_SPECIALIST_V2_INSTRUCTION
    assert "PARTIAL and/or RESULT_TRUNCATED is usable partial evidence" in text
    assert "do not automatically call the same list" in text
    assert "Do not increase the requested limit in response to truncation." in text
    assert "model-facing list maximum is 25" in text
    assert 'metric_id "minimum_count"' in text
    assert "LOWER_BOUND_COUNT" in text
    assert "Never convert a lower bound" in text
    assert "into EXACT_COUNT" in text
    assert "do not repeat an identical call" in text


def test_v2_hazard_materialization_guidance():
    text = HISTORICAL_SPECIALIST_V2_INSTRUCTION
    assert "do not establish" in text
    assert "validity-interval overlap" in text
    assert "do not repeat the same hazard tool" in text
    assert "propose only factual claims supported by the materialization evidence" in text
    assert "HAZARD_VERSION_WINDOW_LIMITATION" in text
    assert "Do not emit a LIMITATION claim" in text
    assert "Wilvor adds deterministically" in text


def test_v2_unknown_is_not_unavailable():
    text = HISTORICAL_SPECIALIST_V2_INSTRUCTION
    assert "status is UNKNOWN because of INVALID_REQUEST" in text
    assert "not" in text
    assert "UNAVAILABLE" in text
    assert "Do not propose an UNAVAILABLE claim" in text


def test_v2_out_of_catalog_is_not_model_generated_refusal():
    text = HISTORICAL_SPECIALIST_V2_INSTRUCTION
    assert "arbitrary SQL" in text
    assert "Athena access" in text
    assert "AWS access" in text
    assert "fifth/non-catalog tools" in text
    assert "UNSUPPORTED with unsupported_reason" in text
    assert "OUT_OF_CATALOG" in text
    assert "Do not emit model-generated REFUSAL" in text
    assert "genuine model/provider" in text
