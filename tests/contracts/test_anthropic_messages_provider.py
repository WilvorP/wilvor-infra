"""Offline Phase 3A.4B Anthropic Messages adapter tests. No API or SDK."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from tests.fakes.fake_anthropic_messages import (
    FakeAnthropicMessages,
    end_turn_response,
    stop_reason_response,
    tool_use_response,
)
from tests.fakes.recording_historical_operations import RecordingHistoricalOperations
from wilvor_ai.contracts import (
    TemporalScope,
    ToolInputField,
    ToolInputValueType,
    ToolResultStatus,
)
from wilvor_ai.historical_analytics import historical_analytics_tool_schemas
from wilvor_ai.historical_analytics_mapping import map_historical_query_response
from wilvor_ai.historical_answer_renderer import finalize_historical_specialist_run
from wilvor_ai.historical_specialist import (
    HISTORICAL_SPECIALIST_INSTRUCTION_REF,
    HistoricalAnalyticsSpecialist,
    SequentialToolCallIdFactory,
)
from wilvor_ai.model_contracts import (
    MODEL_DECISION_SCHEMA_VERSION,
    ModelDecision,
    ModelDecisionKind,
    ModelTurnRequest,
    ProposedToolCall,
    ValidationFeedback,
    ValidationFeedbackCode,
)
from wilvor_ai.providers.anthropic_messages import (
    APPROVED_MODEL_IDS,
    DEFAULT_MODEL_ID,
    MAX_TOKENS,
    REFUSAL_PROVIDER,
    TEMPERATURE,
    AnthropicMessagesModelProvider,
    build_messages_kwargs,
    parse_messages_response,
    terminal_decision_json_schema,
    tool_defs_from_schemas,
)
from wilvor_ai.providers.errors import (
    ModelProviderContextLengthError,
    ModelProviderError,
    ModelProviderMalformedDecisionError,
)
from wilvor_ai.providers.instructions import (
    HISTORICAL_SPECIALIST_V1_INSTRUCTION,
    HISTORICAL_SPECIALIST_V2_INSTRUCTION,
    resolve_instruction,
)
from wilvor_ai.specialist_contracts import (
    ExactCountClaim,
    HistoricalSpecialistTrustedContext,
    HistoricalWindowClaim,
    LimitationClaim,
    LowerBoundCountClaim,
    RecordIdentityClaim,
    SpecialistRequest,
    SpecialistStatus,
    UnavailableClaim,
    UnsupportedReason,
    VerifiedZeroClaim,
    VerifierOutcome,
)
from wilvor_ai.specialist_runtime_contracts import SpecialistRunStatus
from wilvor_ai.tool_result_projection import project_tool_result
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES, ToolSchema
from wilvor_historical.coverage_contracts import Evaluability
from wilvor_historical.query_contracts import (
    INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
    CoverageEvidence,
    EncounterSummaryResult,
    HistoricalOperation,
    HistoricalQueryResponse,
    HistoricalQueryStatus,
    QueryEvidence,
    QueryExecutionEvidence,
    SummarizeHistoricalEncountersRequest,
)


AS_OF = "2026-09-13T12:00:00Z"
WINDOW = ("2026-09-11T10:00:00Z", "2026-09-11T11:00:00Z")
USER_TEXT = "How many historical encounters occurred in the window?"
SCHEMAS = historical_analytics_tool_schemas()
LIVE_OPS_NAMES = (
    "search_current_hazards",
    "search_current_impacts",
    "get_aircraft_operational_context",
    "find_aircraft_by_callsign",
)
AUDIT_LEAK_TOKENS = (
    "query_executions",
    "query_id",
    "execution_id",
    "bytes_scanned",
    "data_scanned_bytes",
    "workgroup",
    "SELECT ",
    "s3://",
    "athena-results",
)
_UNSUPPORTED_CONSTRAINTS = frozenset(
    {"minimum", "maximum", "multipleOf", "minLength", "maxLength"}
)
_MAX_STRICT_TOOLS = 20
_MAX_OPTIONAL_PARAMETERS = 24
_MAX_UNION_TYPED_PARAMETERS = 16


def _turn(
    *,
    tool_results=(),
    validation_feedback=None,
    instruction_ref: str | None = HISTORICAL_SPECIALIST_INSTRUCTION_REF,
) -> ModelTurnRequest:
    return ModelTurnRequest(
        user_text=USER_TEXT,
        instruction_ref=instruction_ref,
        tools=SCHEMAS,
        tool_results=tuple(tool_results),
        validation_feedback=validation_feedback,
    )


def _provider(script=None) -> tuple[AnthropicMessagesModelProvider, FakeAnthropicMessages]:
    client = FakeAnthropicMessages(script or ())
    return AnthropicMessagesModelProvider(client=client), client


def _decision_payload(kind: str, **fields: Any) -> dict[str, Any]:
    payload = {"schema_version": MODEL_DECISION_SCHEMA_VERSION, "kind": kind}
    payload.update(fields)
    return payload


def _final_claims_payload(tool_call_id: str = "historical-call-1") -> dict[str, Any]:
    return _decision_payload(
        "FINAL_CLAIMS",
        claims=[
            {
                "kind": "EXACT_COUNT",
                "tool_call_id": tool_call_id,
                "metric_id": "distinct_encounter_count",
                "value": 2,
            },
            {
                "kind": "HISTORICAL_WINDOW",
                "tool_call_id": tool_call_id,
                "start_utc": WINDOW[0],
                "end_utc": WINDOW[1],
            },
        ],
    )


def _all_claim_payloads() -> list[dict[str, Any]]:
    return [
        {
            "kind": "EXACT_COUNT",
            "tool_call_id": "historical-call-1",
            "metric_id": "distinct_encounter_count",
            "value": 2,
        },
        {
            "kind": "LOWER_BOUND_COUNT",
            "tool_call_id": "historical-call-1",
            "metric_id": "minimum_count",
            "minimum_value": 1,
        },
        {"kind": "VERIFIED_ZERO", "tool_call_id": "historical-call-1"},
        {
            "kind": "HISTORICAL_WINDOW",
            "tool_call_id": "historical-call-1",
            "start_utc": WINDOW[0],
            "end_utc": WINDOW[1],
        },
        {
            "kind": "RECORD_IDENTITY",
            "tool_call_id": "historical-call-1",
            "record_id": "rec-1",
        },
        {
            "kind": "LIMITATION",
            "tool_call_id": "historical-call-1",
            "limitation_code": "RESULT_TRUNCATED",
        },
        {
            "kind": "UNAVAILABLE",
            "tool_call_id": "historical-call-1",
            "error_or_coverage_code": "COVERAGE_BLOCKED",
        },
    ]


def _coverage():
    return CoverageEvidence(
        evaluability=Evaluability.EVALUABLE,
        reason="window_evaluable",
        required_horizon_seconds=173760,
        required_streams=("facts",),
        collection_epoch_ids=("epoch-1",),
    )


def _succeeded_encounters() -> HistoricalQueryResponse:
    return HistoricalQueryResponse(
        status=HistoricalQueryStatus.SUCCEEDED,
        operation=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
        requested_scope=SummarizeHistoricalEncountersRequest(
            start_utc=WINDOW[0],
            end_utc=WINDOW[1],
        ),
        coverage=_coverage(),
        evidence=QueryEvidence(
            query_name=HistoricalOperation.SUMMARIZE_HISTORICAL_ENCOUNTERS,
            datasets=("encounter",),
            requested_start_utc=WINDOW[0],
            requested_end_utc=WINDOW[1],
            coverage_state=Evaluability.EVALUABLE,
            collection_epoch_ids=("epoch-1",),
            semantic_match_count=2,
            semantic_match_count_is_exact=True,
            query_executions=(
                QueryExecutionEvidence(
                    query_id=INTERNAL_QUERY_ID_SUMMARIZE_ENCOUNTERS,
                    query_execution_id="exec-provider-1",
                    rows_returned=2,
                    data_scanned_bytes=64,
                    workgroup="historical-analytics",
                ),
            ),
            evaluated_as_of_utc=AS_OF,
        ),
        result=EncounterSummaryResult(
            physical_record_count=2,
            distinct_encounter_count=2,
            distinct_aircraft_count=1,
            distinct_hazard_count=1,
            distinct_dedup_count=2,
            min_event_time_utc="2026-09-11T10:00:00Z",
            max_event_time_utc="2026-09-11T10:30:00Z",
        ),
    )


def _projection():
    tool = map_historical_query_response(
        _succeeded_encounters(),
        tool_call_id="historical-call-1",
    )
    return tool, project_tool_result(tool)


def _walk_schema(node: Any) -> list[Any]:
    values = [node]
    if isinstance(node, Mapping):
        for item in node.values():
            values.extend(_walk_schema(item))
    elif isinstance(node, list):
        for item in node:
            values.extend(_walk_schema(item))
    return values


def _count_optional_parameters(schema: Mapping[str, Any]) -> int:
    count = 0
    if not isinstance(schema, Mapping):
        return 0
    if schema.get("type") == "object":
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        if isinstance(properties, Mapping):
            count += sum(1 for name in properties if name not in required)
            for child in properties.values():
                if isinstance(child, Mapping):
                    count += _count_optional_parameters(child)
    if "items" in schema and isinstance(schema["items"], Mapping):
        count += _count_optional_parameters(schema["items"])
    for branch in schema.get("anyOf") or ():
        if isinstance(branch, Mapping):
            count += _count_optional_parameters(branch)
    return count


def _count_union_typed_parameters(schema: Mapping[str, Any]) -> int:
    count = 0
    if not isinstance(schema, Mapping):
        return 0
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for child in properties.values():
            if not isinstance(child, Mapping):
                continue
            if "anyOf" in child or isinstance(child.get("type"), list):
                count += 1
            items = child.get("items")
            if isinstance(items, Mapping) and "anyOf" in items:
                count += 1
            count += _count_union_typed_parameters(child)
    if "items" in schema and isinstance(schema["items"], Mapping):
        count += _count_union_typed_parameters(schema["items"])
    for branch in schema.get("anyOf") or ():
        if isinstance(branch, Mapping):
            count += _count_union_typed_parameters(branch)
    return count


def test_approved_direct_model_is_accepted_and_unknown_ids_rejected():
    provider, _ = _provider()
    assert provider.model_id == DEFAULT_MODEL_ID
    assert APPROVED_MODEL_IDS == frozenset({DEFAULT_MODEL_ID})
    assert DEFAULT_MODEL_ID == "claude-sonnet-4-6"
    for rejected in (
        "claude-sonnet-5",
        "claude-sonnet-4-6-latest",
        "claude-sonnet-4-5-20250929",
        "us.anthropic.claude-sonnet-4-6",
        "anthropic.claude-sonnet-4-6",
        "global.anthropic.claude-sonnet-4-6",
    ):
        with pytest.raises(ValueError, match="unknown_model_id"):
            AnthropicMessagesModelProvider(
                client=FakeAnthropicMessages(),
                model_id=rejected,
            )


def test_unknown_instruction_ref_fails_closed():
    provider, _ = _provider([end_turn_response(_final_claims_payload())])
    with pytest.raises(ModelProviderError, match="unknown_instruction_ref"):
        provider.complete(_turn(instruction_ref="wilvor.unknown.v9"))
    assert resolve_instruction("wilvor.historical.specialist.v1") == (
        HISTORICAL_SPECIALIST_V1_INSTRUCTION
    )
    assert resolve_instruction(HISTORICAL_SPECIALIST_INSTRUCTION_REF) == (
        HISTORICAL_SPECIALIST_V2_INSTRUCTION
    )


def test_four_strict_tools_and_locked_messages_settings():
    client = FakeAnthropicMessages(
        [
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
                )
            )
        ]
    )
    provider = AnthropicMessagesModelProvider(client=client)
    provider.complete(_turn())
    kwargs = client.requests[0]
    assert kwargs["model"] == DEFAULT_MODEL_ID
    assert kwargs["max_tokens"] == MAX_TOKENS == 2048
    assert kwargs["temperature"] == TEMPERATURE == 0
    assert "top_p" not in kwargs
    assert "stop_sequences" not in kwargs
    assert "thinking" not in kwargs
    assert kwargs["tool_choice"] == {"type": "auto"}
    tools = kwargs["tools"]
    assert [item["name"] for item in tools] == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    ]
    assert len(tools) == 4
    dumped = json.dumps(kwargs)
    for name in LIVE_OPS_NAMES:
        assert name not in dumped
    for tool in tools:
        assert tool["strict"] is True
        schema = tool["input_schema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["required"], list)
        for field_name in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            assert field_name not in schema["properties"]
            assert field_name not in schema["required"]
        assert "description" in tool
    encounter = tools[0]["input_schema"]
    assert encounter["properties"]["start_utc"]["type"] == "string"
    assert encounter["properties"]["start_utc"]["description"]
    assert "start_utc" in encounter["required"]
    assert "end_utc" in encounter["required"]
    assert "aircraft_id" not in encounter["required"]
    list_schema = tools[3]["input_schema"]
    assert list_schema["properties"]["limit"]["type"] == "integer"
    output = kwargs["output_config"]["format"]
    assert output["type"] == "json_schema"
    assert isinstance(output["schema"], dict)
    assert output["schema"] == terminal_decision_json_schema()
    assert kwargs["system"] == HISTORICAL_SPECIALIST_V2_INSTRUCTION


def test_catalog_type_mapping_covers_all_value_types():
    schema = ToolSchema(
        name="type_probe",
        description="type coverage",
        input_fields=tuple(
            ToolInputField(
                name=item.name.lower(),
                required=True,
                value_type=item,
                description=item.value,
            )
            for item in ToolInputValueType
        ),
    )
    spec = tool_defs_from_schemas((schema,))[0]["input_schema"]
    expected = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
        "object": "object",
        "array": "array",
    }
    for name, json_type in expected.items():
        assert spec["properties"][name]["type"] == json_type


def test_terminal_schema_permits_only_three_kinds_and_supported_subset():
    schema = terminal_decision_json_schema()
    dumped = json.dumps(schema)
    assert "TOOL_CALLS" not in dumped
    assert "oneOf" not in dumped
    assert "answer" not in dumped
    assert "candidate_answer" not in dumped
    assert "factual_text" not in dumped
    kinds = {branch["properties"]["kind"]["const"] for branch in schema["anyOf"]}
    assert kinds == {"FINAL_CLAIMS", "UNSUPPORTED", "REFUSAL"}
    for node in _walk_schema(schema):
        if isinstance(node, dict):
            assert "oneOf" not in node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False


def test_generated_schemas_stay_within_anthropic_complexity_limits():
    kwargs = build_messages_kwargs(_turn(), DEFAULT_MODEL_ID)
    tools = kwargs["tools"]
    assert len(tools) == 4
    assert len(tools) <= _MAX_STRICT_TOOLS
    assert all(tool["strict"] is True for tool in tools)
    optional = 0
    unions = 0
    schemas = [tool["input_schema"] for tool in tools]
    schemas.append(kwargs["output_config"]["format"]["schema"])
    for schema in schemas:
        optional += _count_optional_parameters(schema)
        unions += _count_union_typed_parameters(schema)
        for node in _walk_schema(schema):
            if isinstance(node, dict):
                assert _UNSUPPORTED_CONSTRAINTS.isdisjoint(node)
                if node.get("type") == "object":
                    assert node.get("additionalProperties") is False
    assert optional <= _MAX_OPTIONAL_PARAMETERS
    assert unions <= _MAX_UNION_TYPED_PARAMETERS
    assert optional == 14
    assert unions == 1


def test_tool_use_maps_name_arguments_and_order():
    first = {"start_utc": WINDOW[0], "end_utc": WINDOW[1]}
    second = {
        "start_utc": WINDOW[0],
        "end_utc": WINDOW[1],
        "aircraft_id": "abc123",
        "limit": 20,
    }
    decision = parse_messages_response(
        tool_use_response(
            ("summarize_historical_encounters", first),
            ("list_historical_encounters", second),
            extra_text="Incidental prose that must never become an answer.",
        ),
        DEFAULT_MODEL_ID,
    )
    assert decision.kind is ModelDecisionKind.TOOL_CALLS
    assert [item.name for item in decision.tool_calls] == [
        "summarize_historical_encounters",
        "list_historical_encounters",
    ]
    assert decision.tool_calls[0].arguments == first
    assert decision.tool_calls[1].arguments == second
    dumped = json.dumps(decision.to_dict())
    assert "Incidental" not in dumped
    assert "toolu_vendor_1" not in dumped
    assert "answer" not in dumped


def test_unknown_tool_name_is_proposed_not_executed():
    decision = parse_messages_response(
        tool_use_response(("run_sql", {"q": "SELECT 1"})),
        DEFAULT_MODEL_ID,
    )
    assert decision.tool_calls == (
        ProposedToolCall(name="run_sql", arguments={"q": "SELECT 1"}),
    )


def test_end_turn_parses_typed_terminal_decisions():
    claims = parse_messages_response(
        end_turn_response(_final_claims_payload()),
        DEFAULT_MODEL_ID,
    )
    assert claims.kind is ModelDecisionKind.FINAL_CLAIMS
    assert isinstance(claims.claims[0], ExactCountClaim)
    assert isinstance(claims.claims[1], HistoricalWindowClaim)
    unsupported = parse_messages_response(
        end_turn_response(
            _decision_payload(
                "UNSUPPORTED",
                unsupported_reason=UnsupportedReason.GEOGRAPHY.value,
            )
        ),
        DEFAULT_MODEL_ID,
    )
    assert unsupported == ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
    )
    refusal = parse_messages_response(
        end_turn_response(_decision_payload("REFUSAL", refusal_code="MODEL_REFUSED")),
        DEFAULT_MODEL_ID,
    )
    assert refusal == ModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code="MODEL_REFUSED",
    )
    all_kinds = parse_messages_response(
        end_turn_response(_decision_payload("FINAL_CLAIMS", claims=_all_claim_payloads())),
        DEFAULT_MODEL_ID,
    )
    assert [type(item) for item in all_kinds.claims] == [
        ExactCountClaim,
        LowerBoundCountClaim,
        VerifiedZeroClaim,
        HistoricalWindowClaim,
        RecordIdentityClaim,
        LimitationClaim,
        UnavailableClaim,
    ]


def test_end_turn_text_json_is_accepted_and_fences_are_not_repaired():
    payload = _final_claims_payload()
    text_response = {
        "id": "msg_ok",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL_ID,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }
    assert (
        parse_messages_response(text_response, DEFAULT_MODEL_ID).kind
        is ModelDecisionKind.FINAL_CLAIMS
    )
    fenced = {
        "id": "msg_fenced",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL_ID,
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": f"```json\n{json.dumps(payload)}\n```"}],
    }
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(fenced, DEFAULT_MODEL_ID)


def test_end_turn_requires_exactly_one_text_content_block():
    payload = _final_claims_payload()
    decision = parse_messages_response(
        end_turn_response(payload),
        DEFAULT_MODEL_ID,
    )
    assert decision.kind is ModelDecisionKind.FINAL_CLAIMS
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            {
                "id": "msg_empty",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "end_turn",
                "content": [],
            },
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            {
                "id": "msg_two",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "end_turn",
                "content": [
                    {"type": "text", "text": json.dumps(payload)},
                    {"type": "text", "text": json.dumps(payload)},
                ],
            },
            DEFAULT_MODEL_ID,
        )


def test_response_envelope_is_required():
    payload = _final_claims_payload()
    valid_content = [{"type": "text", "text": json.dumps(payload)}]
    with pytest.raises(ModelProviderMalformedDecisionError, match="unexpected_response_type"):
        parse_messages_response(
            {
                "type": "error",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "end_turn",
                "content": valid_content,
            },
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError, match="unexpected_response_role"):
        parse_messages_response(
            {
                "type": "message",
                "role": "user",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "end_turn",
                "content": valid_content,
            },
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError, match="unexpected_response_model"):
        parse_messages_response(
            {
                "type": "message",
                "role": "assistant",
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
                "content": valid_content,
            },
            DEFAULT_MODEL_ID,
        )


def test_unexpected_content_block_types_fail_closed():
    for block_type in (
        "thinking",
        "redacted_thinking",
        "server_tool_use",
        "tool_result",
        "web_search_tool_result",
    ):
        with pytest.raises(
            ModelProviderMalformedDecisionError,
            match="unexpected_content_block",
        ):
            parse_messages_response(
                {
                    "id": "msg_block",
                    "type": "message",
                    "role": "assistant",
                    "model": DEFAULT_MODEL_ID,
                    "stop_reason": "end_turn",
                    "content": [{"type": block_type}],
                },
                DEFAULT_MODEL_ID,
            )


def test_tool_use_id_is_required_then_discarded():
    arguments = {"start_utc": WINDOW[0], "end_utc": WINDOW[1]}
    decision = parse_messages_response(
        {
            "id": "msg_tool",
            "type": "message",
            "role": "assistant",
            "model": DEFAULT_MODEL_ID,
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_keep_local",
                    "name": "summarize_historical_encounters",
                    "input": arguments,
                }
            ],
        },
        DEFAULT_MODEL_ID,
    )
    assert decision.kind is ModelDecisionKind.TOOL_CALLS
    assert decision.tool_calls == (
        ProposedToolCall(
            name="summarize_historical_encounters",
            arguments=arguments,
        ),
    )
    serialized = json.dumps(decision.to_dict())
    assert "toolu_keep_local" not in serialized
    assert "id" not in decision.tool_calls[0].to_dict()
    missing = {
        "id": "msg_missing",
        "type": "message",
        "role": "assistant",
        "model": DEFAULT_MODEL_ID,
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "name": "summarize_historical_encounters",
                "input": arguments,
            }
        ],
    }
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(missing, DEFAULT_MODEL_ID)
    for invalid_id in ("", "   ", 12, None):
        with pytest.raises(ModelProviderMalformedDecisionError):
            parse_messages_response(
                {
                    "id": "msg_bad_id",
                    "type": "message",
                    "role": "assistant",
                    "model": DEFAULT_MODEL_ID,
                    "stop_reason": "tool_use",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": invalid_id,
                            "name": "summarize_historical_encounters",
                            "input": arguments,
                        }
                    ],
                },
                DEFAULT_MODEL_ID,
            )


def test_stop_reasons_map_to_refusal_or_typed_errors():
    refusal = parse_messages_response(
        stop_reason_response("refusal"),
        DEFAULT_MODEL_ID,
    )
    assert refusal == ModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code=REFUSAL_PROVIDER,
    )
    cases = {
        "max_tokens": ModelProviderMalformedDecisionError,
        "stop_sequence": ModelProviderMalformedDecisionError,
        "pause_turn": ModelProviderMalformedDecisionError,
        "unexpected_reason": ModelProviderMalformedDecisionError,
        "model_context_window_exceeded": ModelProviderContextLengthError,
    }
    for reason, error in cases.items():
        with pytest.raises(error) as caught:
            parse_messages_response(stop_reason_response(reason), DEFAULT_MODEL_ID)
        assert "UNSUPPORTED" not in str(caught.value)
        assert caught.value.args[0] != UnsupportedReason.OUT_OF_CATALOG.value
    with pytest.raises(ModelProviderMalformedDecisionError, match="unexpected_stop_reason"):
        parse_messages_response(
            {
                "id": "msg_missing_stop",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "content": [],
            },
            DEFAULT_MODEL_ID,
        )


def test_malformed_and_contradictory_responses_fail_closed():
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="tool_use_without_tool_use_blocks",
    ):
        parse_messages_response(
            {
                "id": "msg_no_tools",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "tool_use",
                "content": [{"type": "text", "text": "please call a tool"}],
            },
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="end_turn_contains_tool_use",
    ):
        parse_messages_response(
            {
                "id": "msg_end_tool",
                "type": "message",
                "role": "assistant",
                "model": DEFAULT_MODEL_ID,
                "stop_reason": "end_turn",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "summarize_historical_encounters",
                        "input": {},
                    }
                ],
            },
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="contradictory_terminal_decision",
    ):
        parse_messages_response(
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
                ),
                terminal_json=_final_claims_payload(),
            ),
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            end_turn_response(_decision_payload("TOOL_CALLS", tool_calls=[])),
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            end_turn_response(_decision_payload("ANSWER", answer="nope")),
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            end_turn_response(
                _decision_payload(
                    "FINAL_CLAIMS",
                    claims=[
                        {
                            "kind": "STORY",
                            "tool_call_id": "historical-call-1",
                            "text": "two encounters",
                        }
                    ],
                )
            ),
            DEFAULT_MODEL_ID,
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_messages_response(
            end_turn_response(
                _decision_payload(
                    "FINAL_CLAIMS",
                    claims=[
                        {
                            "kind": "EXACT_COUNT",
                            "tool_call_id": "historical-call-1",
                            "metric_id": "distinct_encounter_count",
                            "value": 2,
                        }
                    ],
                    answer="There were exactly two encounters.",
                )
            ),
            DEFAULT_MODEL_ID,
        )


def test_snapshot_reconstruction_is_stateless_and_projection_only():
    tool_result, projection = _projection()
    assert tool_result.evidence[0].query_executions
    feedback = ValidationFeedback(
        code=ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
        tool_name="summarize_historical_encounters",
        argument_name="as_of_utc",
    )
    first = build_messages_kwargs(_turn(), DEFAULT_MODEL_ID)
    later_request = _turn(tool_results=(projection,), validation_feedback=feedback)
    later = build_messages_kwargs(later_request, DEFAULT_MODEL_ID)
    again = build_messages_kwargs(later_request, DEFAULT_MODEL_ID)
    assert later == again
    assert first["messages"][0] == {
        "role": "user",
        "content": [{"type": "text", "text": USER_TEXT}],
    }
    assert later["messages"][0] == first["messages"][0]
    projection_text = later["messages"][1]["content"][0]["text"]
    feedback_text = later["messages"][2]["content"][0]["text"]
    projection_payload = json.loads(projection_text)
    assert list(projection_payload) == ["tool_results"]
    visible = projection_payload["tool_results"][0]
    assert visible == projection.to_dict()
    assert visible["status"] == ToolResultStatus.SUCCESS.value
    assert visible["temporal_scope"] == TemporalScope.HISTORICAL.value
    assert visible["as_of_utc"] == AS_OF
    assert visible["result"]["distinct_encounter_count"] == 2
    assert visible["limitations"] == []
    model_visible = json.dumps(
        {
            "tool_results": later["messages"][1]["content"],
            "tools": later["tools"],
        }
    )
    for token in AUDIT_LEAK_TOKENS:
        assert token not in model_visible
    assert "query_executions" not in visible
    messages_dump = json.dumps(later["messages"])
    assert '"type": "tool_result"' not in messages_dump
    assert '"type": "tool_use"' not in messages_dump
    assert '"role": "assistant"' not in messages_dump
    assert "toolu_" not in messages_dump
    feedback_payload = json.loads(feedback_text)
    assert feedback_payload == {"validation_feedback": feedback.to_dict()}
    for message in later["messages"]:
        assert message["role"] == "user"
    assert "as_of_utc" not in json.dumps(later["tools"])


def test_provider_performs_one_messages_create_call_and_propagates_client_errors():
    client = FakeAnthropicMessages([RuntimeError("injected_transport_failure")])
    provider = AnthropicMessagesModelProvider(client=client)
    with pytest.raises(RuntimeError, match="injected_transport_failure"):
        provider.complete(_turn())
    assert len(client.requests) == 1


def test_offline_specialist_end_to_end_uses_renderer_answer():
    incidental = "THE SECRET ANSWER IS 999 encounters."
    client = FakeAnthropicMessages(
        [
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
                ),
                extra_text=incidental,
            ),
            end_turn_response(_final_claims_payload()),
        ]
    )
    operations = RecordingHistoricalOperations(
        {"summarize_historical_encounters": _succeeded_encounters()}
    )
    specialist = HistoricalAnalyticsSpecialist(
        provider=AnthropicMessagesModelProvider(client=client),
        operations=operations,
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    run = specialist.run(
        SpecialistRequest(text=USER_TEXT),
        HistoricalSpecialistTrustedContext(as_of_utc=AS_OF),
    )
    assert run.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert run.provider_turn_count == 2
    assert operations.calls[0]["as_of_utc"] == AS_OF
    assert run.tool_results[0].evidence[0].query_executions
    finalized = finalize_historical_specialist_run(run)
    assert finalized.status is SpecialistStatus.ANSWERED
    assert finalized.verifier_outcome is VerifierOutcome.PASSED
    assert "exact count of 2 for distinct encounters" in finalized.answer
    assert incidental not in finalized.answer
    assert "999" not in finalized.answer
    assert finalized.tool_results[0] is run.tool_results[0]
    assert finalized.evaluated_as_of_utc == AS_OF
    second = json.loads(client.requests[1]["messages"][1]["content"][0]["text"])
    assert second["tool_results"][0]["tool_call_id"] == "historical-call-1"
    assert "toolu_vendor_1" not in json.dumps(finalized.to_dict())
    assert "as_of_utc" not in json.dumps(client.requests[0]["tools"])
    assert "as_of_utc" not in client.requests[0]["messages"][0]["content"][0]["text"]


def test_invalid_trusted_argument_uses_existing_one_correction_policy():
    client = FakeAnthropicMessages(
        [
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {
                        "start_utc": WINDOW[0],
                        "end_utc": WINDOW[1],
                        "as_of_utc": "2099-01-01T00:00:00Z",
                    },
                )
            ),
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
                )
            ),
            end_turn_response(_final_claims_payload()),
        ]
    )
    operations = RecordingHistoricalOperations(
        {"summarize_historical_encounters": _succeeded_encounters()}
    )
    specialist = HistoricalAnalyticsSpecialist(
        provider=AnthropicMessagesModelProvider(client=client),
        operations=operations,
        tool_call_id_factory=SequentialToolCallIdFactory(),
    )
    run = specialist.run(
        SpecialistRequest(text=USER_TEXT),
        HistoricalSpecialistTrustedContext(as_of_utc=AS_OF),
    )
    assert run.status is SpecialistRunStatus.PROPOSED_CLAIMS
    assert len(operations.calls) == 1
    assert operations.calls[0]["as_of_utc"] == AS_OF
    assert run.validation_feedback[0].code is (
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN
    )
    feedback = json.loads(client.requests[1]["messages"][-1]["content"][0]["text"])
    assert feedback["validation_feedback"]["code"] == (
        ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN.value
    )
    assert feedback["validation_feedback"]["argument_name"] == "as_of_utc"
    finalized = finalize_historical_specialist_run(run)
    assert finalized.status is SpecialistStatus.ANSWERED
    assert finalized.answer.startswith("The historical query returned an exact count")
