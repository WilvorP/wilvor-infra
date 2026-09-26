"""Offline Phase 3A.4 Bedrock Converse adapter tests. No AWS or boto3."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from tests.fakes.fake_bedrock_runtime import (
    FakeBedrockRuntime,
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
from wilvor_ai.providers.bedrock_converse import (
    APPROVED_MODEL_IDS,
    DEFAULT_MODEL_ID,
    MAX_TOKENS,
    REFUSAL_CONTENT_FILTERED,
    REFUSAL_GUARDRAIL_INTERVENED,
    TEMPERATURE,
    TERMINAL_SCHEMA_NAME,
    BedrockConverseModelProvider,
    build_converse_kwargs,
    parse_converse_response,
    serialize_terminal_schema,
    terminal_decision_json_schema,
    tool_specs_from_schemas,
)
from wilvor_ai.providers.errors import (
    ModelProviderContextLengthError,
    ModelProviderError,
    ModelProviderMalformedDecisionError,
)
from wilvor_ai.providers.instructions import (
    HISTORICAL_SPECIALIST_V1_INSTRUCTION,
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


def _provider(script=None) -> tuple[BedrockConverseModelProvider, FakeBedrockRuntime]:
    client = FakeBedrockRuntime(script or ())
    return BedrockConverseModelProvider(client=client), client


def _decision_payload(
    kind: str,
    **fields: Any,
) -> dict[str, Any]:
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


def test_approved_geo_profile_is_accepted_and_unknown_ids_rejected():
    provider, _ = _provider()
    assert provider.model_id == DEFAULT_MODEL_ID
    assert APPROVED_MODEL_IDS == frozenset({DEFAULT_MODEL_ID})
    assert DEFAULT_MODEL_ID == "us.anthropic.claude-sonnet-4-6"
    for rejected in (
        "anthropic.claude-sonnet-4-5-20250929-v1:0",
        "us.anthropic.claude-sonnet-4-5",
        "us.anthropic.claude-sonnet-5",
        "global.anthropic.claude-sonnet-4-6",
        "anthropic.claude-sonnet-4-6",
    ):
        with pytest.raises(ValueError, match="unknown_model_id"):
            BedrockConverseModelProvider(
                client=FakeBedrockRuntime(),
                model_id=rejected,
            )


def test_unknown_instruction_ref_fails_closed():
    provider, _ = _provider([end_turn_response(_final_claims_payload())])
    with pytest.raises(ModelProviderError, match="unknown_instruction_ref"):
        provider.complete(_turn(instruction_ref="wilvor.unknown.v9"))
    assert resolve_instruction(HISTORICAL_SPECIALIST_INSTRUCTION_REF) == (
        HISTORICAL_SPECIALIST_V1_INSTRUCTION
    )


def test_four_strict_tool_specs_and_locked_converse_settings():
    client = FakeBedrockRuntime([tool_use_response(
        (
            "summarize_historical_encounters",
            {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
        )
    )])
    provider = BedrockConverseModelProvider(client=client)
    provider.complete(_turn())
    kwargs = client.requests[0]
    assert kwargs["modelId"] == DEFAULT_MODEL_ID
    assert kwargs["inferenceConfig"] == {
        "temperature": TEMPERATURE,
        "maxTokens": MAX_TOKENS,
    }
    assert "topP" not in kwargs["inferenceConfig"]
    assert kwargs["toolConfig"]["toolChoice"] == {"auto": {}}
    tools = kwargs["toolConfig"]["tools"]
    assert [item["toolSpec"]["name"] for item in tools] == [
        "summarize_historical_encounters",
        "summarize_historical_risks",
        "summarize_historical_hazard_versions",
        "list_historical_encounters",
    ]
    assert len(tools) == 4
    dumped = json.dumps(kwargs)
    for name in LIVE_OPS_NAMES:
        assert name not in dumped
    for spec in tools:
        tool_spec = spec["toolSpec"]
        assert tool_spec["strict"] is True
        schema = tool_spec["inputSchema"]["json"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["required"], list)
        for field_name in FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES:
            assert field_name not in schema["properties"]
            assert field_name not in schema["required"]
        assert "description" in tool_spec
    encounter = tools[0]["toolSpec"]["inputSchema"]["json"]
    assert encounter["properties"]["start_utc"]["type"] == "string"
    assert encounter["properties"]["start_utc"]["description"]
    assert "start_utc" in encounter["required"]
    assert "end_utc" in encounter["required"]
    assert "aircraft_id" not in encounter["required"]
    list_schema = tools[3]["toolSpec"]["inputSchema"]["json"]
    assert list_schema["properties"]["limit"]["type"] == "integer"
    output = kwargs["outputConfig"]["textFormat"]
    assert output["type"] == "json_schema"
    json_schema = output["structure"]["jsonSchema"]
    assert json_schema["name"] == TERMINAL_SCHEMA_NAME
    assert json_schema["schema"] == serialize_terminal_schema()
    assert "toolConfig" in kwargs
    assert "outputConfig" in kwargs
    assert kwargs["system"] == [
        {"text": HISTORICAL_SPECIALIST_V1_INSTRUCTION}
    ]


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
    spec = tool_specs_from_schemas((schema,))[0]["toolSpec"]["inputSchema"]["json"]
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
    serialized = serialize_terminal_schema()
    assert json.loads(serialized) == schema
    dumped = json.dumps(schema)
    assert "TOOL_CALLS" not in dumped
    assert "oneOf" not in dumped
    assert "answer" not in dumped
    assert "candidate_answer" not in dumped
    assert "factual_text" not in dumped
    kinds = {
        branch["properties"]["kind"]["const"]
        for branch in schema["anyOf"]
    }
    assert kinds == {"FINAL_CLAIMS", "UNSUPPORTED", "REFUSAL"}
    for node in _walk_schema(schema):
        if isinstance(node, dict):
            assert "oneOf" not in node
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False


def test_tool_use_maps_name_arguments_and_order():
    provider, _ = _provider()
    first = {"start_utc": WINDOW[0], "end_utc": WINDOW[1]}
    second = {
        "start_utc": WINDOW[0],
        "end_utc": WINDOW[1],
        "aircraft_id": "abc123",
        "limit": 20,
    }
    decision = parse_converse_response(
        tool_use_response(
            ("summarize_historical_encounters", first),
            ("list_historical_encounters", second),
            extra_text="Incidental prose that must never become an answer.",
        )
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
    assert "vendor-tool-1" not in dumped
    assert "answer" not in dumped


def test_unknown_tool_name_is_proposed_not_executed():
    decision = parse_converse_response(
        tool_use_response(("run_sql", {"q": "SELECT 1"}))
    )
    assert decision.tool_calls == (
        ProposedToolCall(name="run_sql", arguments={"q": "SELECT 1"}),
    )


def test_end_turn_parses_typed_terminal_decisions():
    claims = parse_converse_response(end_turn_response(_final_claims_payload()))
    assert claims.kind is ModelDecisionKind.FINAL_CLAIMS
    assert isinstance(claims.claims[0], ExactCountClaim)
    assert isinstance(claims.claims[1], HistoricalWindowClaim)
    unsupported = parse_converse_response(
        end_turn_response(
            _decision_payload(
                "UNSUPPORTED",
                unsupported_reason=UnsupportedReason.GEOGRAPHY.value,
            )
        )
    )
    assert unsupported == ModelDecision(
        kind=ModelDecisionKind.UNSUPPORTED,
        unsupported_reason=UnsupportedReason.GEOGRAPHY,
    )
    refusal = parse_converse_response(
        end_turn_response(_decision_payload("REFUSAL", refusal_code="MODEL_REFUSED"))
    )
    assert refusal == ModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code="MODEL_REFUSED",
    )
    all_kinds = parse_converse_response(
        end_turn_response(_decision_payload("FINAL_CLAIMS", claims=_all_claim_payloads()))
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
        "stopReason": "end_turn",
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": json.dumps(payload)}],
            }
        },
    }
    assert parse_converse_response(text_response).kind is ModelDecisionKind.FINAL_CLAIMS
    fenced = {
        "stopReason": "end_turn",
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": f"```json\n{json.dumps(payload)}\n```"}],
            }
        },
    }
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(fenced)


def test_end_turn_requires_exactly_one_text_content_block():
    payload = _final_claims_payload()
    decision = parse_converse_response(
        {
            "stopReason": "end_turn",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [{"text": json.dumps(payload)}],
                }
            },
        }
    )
    assert decision.kind is ModelDecisionKind.FINAL_CLAIMS
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
            {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [{"json": payload}],
                    }
                },
            }
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
            {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"text": json.dumps(payload)},
                            {"text": json.dumps(payload)},
                        ],
                    }
                },
            }
        )


def test_tool_use_id_is_required_then_discarded():
    arguments = {"start_utc": WINDOW[0], "end_utc": WINDOW[1]}
    decision = parse_converse_response(
        {
            "stopReason": "tool_use",
            "output": {
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "toolUse": {
                                "toolUseId": "vendor-tool-keep-local",
                                "name": "summarize_historical_encounters",
                                "input": arguments,
                            }
                        }
                    ],
                }
            },
        }
    )
    assert decision.kind is ModelDecisionKind.TOOL_CALLS
    assert decision.tool_calls == (
        ProposedToolCall(
            name="summarize_historical_encounters",
            arguments=arguments,
        ),
    )
    serialized = json.dumps(decision.to_dict())
    assert "toolUseId" not in serialized
    assert "vendor-tool-keep-local" not in serialized
    assert "toolUseId" not in decision.tool_calls[0].to_dict()
    missing = {
        "stopReason": "tool_use",
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "name": "summarize_historical_encounters",
                            "input": arguments,
                        }
                    }
                ],
            }
        },
    }
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(missing)
    for invalid_id in ("", "   ", 12, None):
        with pytest.raises(ModelProviderMalformedDecisionError):
            parse_converse_response(
                {
                    "stopReason": "tool_use",
                    "output": {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {
                                    "toolUse": {
                                        "toolUseId": invalid_id,
                                        "name": "summarize_historical_encounters",
                                        "input": arguments,
                                    }
                                }
                            ],
                        }
                    },
                }
            )


def test_stop_reasons_map_to_refusal_or_typed_errors():
    filtered = parse_converse_response(stop_reason_response("content_filtered"))
    assert filtered == ModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code=REFUSAL_CONTENT_FILTERED,
    )
    guardrail = parse_converse_response(stop_reason_response("guardrail_intervened"))
    assert guardrail == ModelDecision(
        kind=ModelDecisionKind.REFUSAL,
        refusal_code=REFUSAL_GUARDRAIL_INTERVENED,
    )
    cases = {
        "max_tokens": ModelProviderMalformedDecisionError,
        "stop_sequence": ModelProviderMalformedDecisionError,
        "malformed_model_output": ModelProviderMalformedDecisionError,
        "malformed_tool_use": ModelProviderMalformedDecisionError,
        "unexpected_reason": ModelProviderMalformedDecisionError,
        "model_context_window_exceeded": ModelProviderContextLengthError,
    }
    for reason, error in cases.items():
        with pytest.raises(error) as caught:
            parse_converse_response(stop_reason_response(reason))
        assert "UNSUPPORTED" not in str(caught.value)
        assert caught.value.args[0] != UnsupportedReason.OUT_OF_CATALOG.value


def test_malformed_and_contradictory_responses_fail_closed():
    with pytest.raises(ModelProviderMalformedDecisionError, match="missing_output"):
        parse_converse_response({"stopReason": "end_turn"})
    with pytest.raises(ModelProviderMalformedDecisionError, match="missing_message"):
        parse_converse_response({"stopReason": "end_turn", "output": {}})
    with pytest.raises(ModelProviderMalformedDecisionError, match="malformed_content"):
        parse_converse_response(
            {"stopReason": "end_turn", "output": {"message": {"content": "x"}}}
        )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="tool_use_without_tool_use_blocks",
    ):
        parse_converse_response(
            {
                "stopReason": "tool_use",
                "output": {
                    "message": {"content": [{"text": "please call a tool"}]}
                },
            }
        )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="end_turn_contains_tool_use",
    ):
        parse_converse_response(
            {
                "stopReason": "end_turn",
                "output": {
                    "message": {
                        "content": [
                            {
                                "toolUse": {
                                    "toolUseId": "vendor-1",
                                    "name": "summarize_historical_encounters",
                                    "input": {},
                                }
                            }
                        ]
                    }
                },
            }
        )
    with pytest.raises(
        ModelProviderMalformedDecisionError,
        match="contradictory_terminal_decision",
    ):
        parse_converse_response(
            tool_use_response(
                (
                    "summarize_historical_encounters",
                    {"start_utc": WINDOW[0], "end_utc": WINDOW[1]},
                ),
                terminal_json=_final_claims_payload(),
            )
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
            end_turn_response(_decision_payload("TOOL_CALLS", tool_calls=[]))
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
            end_turn_response(_decision_payload("ANSWER", answer="nope"))
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
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
            )
        )
    with pytest.raises(ModelProviderMalformedDecisionError):
        parse_converse_response(
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
            )
        )


def test_snapshot_reconstruction_is_stateless_and_projection_only():
    tool_result, projection = _projection()
    assert tool_result.evidence[0].query_executions
    feedback = ValidationFeedback(
        code=ValidationFeedbackCode.TRUSTED_ARGUMENT_FORBIDDEN,
        tool_name="summarize_historical_encounters",
        argument_name="as_of_utc",
    )
    first = build_converse_kwargs(_turn(), DEFAULT_MODEL_ID)
    later_request = _turn(tool_results=(projection,), validation_feedback=feedback)
    later = build_converse_kwargs(later_request, DEFAULT_MODEL_ID)
    again = build_converse_kwargs(later_request, DEFAULT_MODEL_ID)
    assert later == again
    assert first["messages"][0] == {"role": "user", "content": [{"text": USER_TEXT}]}
    assert later["messages"][0] == first["messages"][0]
    assert later["messages"][0]["content"][0]["text"] == USER_TEXT
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
            "toolConfig": later["toolConfig"],
        }
    )
    for token in AUDIT_LEAK_TOKENS:
        assert token not in model_visible
    assert "query_executions" not in visible
    messages_dump = json.dumps(later["messages"])
    assert "toolResult" not in messages_dump
    assert '"role": "assistant"' not in messages_dump
    assert "vendor-tool" not in messages_dump
    assert "toolUseId" not in messages_dump
    feedback_payload = json.loads(feedback_text)
    assert feedback_payload == {"validation_feedback": feedback.to_dict()}
    for message in later["messages"]:
        assert message["role"] == "user"
    assert "as_of_utc" not in json.dumps(later["toolConfig"])


def test_provider_performs_one_converse_call_and_propagates_client_errors():
    client = FakeBedrockRuntime(
        [RuntimeError("injected_transport_failure")]
    )
    provider = BedrockConverseModelProvider(client=client)
    with pytest.raises(RuntimeError, match="injected_transport_failure"):
        provider.complete(_turn())
    assert len(client.requests) == 1


def test_offline_specialist_end_to_end_uses_renderer_answer():
    incidental = "THE SECRET ANSWER IS 999 encounters."
    client = FakeBedrockRuntime(
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
        provider=BedrockConverseModelProvider(client=client),
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
    assert "as_of_utc" not in json.dumps(client.requests[0]["toolConfig"])
    assert "as_of_utc" not in client.requests[0]["messages"][0]["content"][0]["text"]


def test_invalid_trusted_argument_uses_existing_one_correction_policy():
    client = FakeBedrockRuntime(
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
        provider=BedrockConverseModelProvider(client=client),
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
