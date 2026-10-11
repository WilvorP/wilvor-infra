"""LO1 Live Ops model-turn and catalog contract tests."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    TemporalScope,
    ToolInputValueType,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops import LIVE_OPS_TOOLS, live_ops_tool_schemas
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_INSTRUCTION_REF,
    LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION,
    MAX_TOOL_CALLS_PER_BATCH,
    LiveOpsModelDecision,
    LiveOpsModelDecisionKind,
    LiveOpsModelTurnRequest,
    LiveOpsProviderFailureKind,
    LiveOpsUnsupportedReason,
    LiveOpsValidationCode,
    LiveOpsValidationFailure,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsToolProjection
from wilvor_ai.model_contracts import ProposedToolCall
from wilvor_ai.tool_result_projection import ToolResultProjection
from wilvor_ai.tool_schema import FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES


def test_catalog_has_eight_tools_and_array_hazard_ids():
    names = [item.name for item in LIVE_OPS_TOOLS]
    assert names == [
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
        "get_observed_network_state",
        "get_aircraft_operational_context",
        "get_hazard_operational_context",
        "get_airport_operational_context",
        "find_aircraft_by_callsign",
    ]
    schemas = live_ops_tool_schemas()
    assert [item.name for item in schemas] == names
    for spec, schema in zip(LIVE_OPS_TOOLS, schemas, strict=True):
        assert schema.input_fields == spec.input_fields
        names_fold = {field.name.casefold() for field in spec.input_fields}
        assert names_fold.isdisjoint(FORBIDDEN_TOOL_SCHEMA_FIELD_NAMES)
    for tool_name in (
        "search_current_hazards",
        "search_current_impacts",
        "search_current_encounters",
    ):
        fields = {item.name: item for item in dict(zip(names, LIVE_OPS_TOOLS))[tool_name].input_fields}
        assert fields["hazard_ids"].required is False
        assert fields["hazard_ids"].value_type is ToolInputValueType.ARRAY
    network = {item.name: item for item in LIVE_OPS_TOOLS}["get_observed_network_state"]
    assert network.input_fields == ()


def test_batch_limit_is_two():
    assert MAX_TOOL_CALLS_PER_BATCH == 2


def test_refusal_is_not_a_provider_failure():
    assert "REFUSAL" not in LiveOpsProviderFailureKind.__members__
    assert LiveOpsModelDecisionKind.REFUSAL.value == "REFUSAL"
    assert set(LiveOpsProviderFailureKind) == {
        LiveOpsProviderFailureKind.TRANSPORT_OR_API,
        LiveOpsProviderFailureKind.MALFORMED_OUTPUT,
        LiveOpsProviderFailureKind.CONTEXT_LENGTH,
    }
    refusal = LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.REFUSAL,
        refusal_code="model_refused",
    )
    unsupported = LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.UNSUPPORTED,
        unsupported_reason=LiveOpsUnsupportedReason.ACTION_REQUEST,
    )
    assert refusal.kind is not unsupported.kind
    assert refusal.unsupported_reason is None


def test_model_decision_kind_invariants():
    with pytest.raises(ContractValidationError):
        LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.TOOL_CALLS)
    with pytest.raises(ContractValidationError):
        LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.REFUSAL)
    with pytest.raises(ContractValidationError):
        LiveOpsModelDecision(kind=LiveOpsModelDecisionKind.UNSUPPORTED)
    decision = LiveOpsModelDecision(
        kind=LiveOpsModelDecisionKind.TOOL_CALLS,
        tool_calls=(ProposedToolCall(name="find_aircraft_by_callsign", arguments={"callsign": "UAL1"}),),
    )
    assert decision.to_dict()["schema_version"] == LIVE_OPS_MODEL_DECISION_SCHEMA_VERSION
    restored = LiveOpsModelDecision.from_dict(decision.to_dict())
    assert restored.kind is LiveOpsModelDecisionKind.TOOL_CALLS


def test_turn_request_rejects_historical_projection():
    historical = ToolResultProjection(
        tool_name="summarize_historical_encounters",
        tool_call_id="hist-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.HISTORICAL,
        as_of_utc="2026-09-06T03:30:00Z",
        operation="summarize",
        requested_scope={"start_utc": "2026-01-01T00:00:00Z"},
        result={"count": 0},
        limitations=(),
        error_code=None,
        evidence=(),
    )
    with pytest.raises(ContractValidationError) as exc:
        LiveOpsModelTurnRequest(user_text="how many", tool_results=(historical,))
    assert "invalid_tool_results" in exc.value.errors


def test_turn_request_accepts_current_projection():
    projection = LiveOpsToolProjection(
        tool_name="get_observed_network_state",
        tool_call_id="lo-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        as_of_utc="2026-09-06T03:30:00Z",
        data={"observed": True, "complete_network_snapshot": False},
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        evidence=(),
        as_of_epoch=1,
    )
    request = LiveOpsModelTurnRequest(
        user_text="network",
        instruction_ref=LIVE_OPS_INSTRUCTION_REF,
        tool_results=(projection,),
        validation_feedback=LiveOpsValidationFailure(
            code=LiveOpsValidationCode.MISSING_SELECTOR,
            tool_name="search_current_impacts",
        ),
    )
    restored = LiveOpsModelTurnRequest.from_dict(request.to_dict())
    assert restored.tool_results[0].tool_name == "get_observed_network_state"
    assert restored.validation_feedback.code is LiveOpsValidationCode.MISSING_SELECTOR


def test_hazard_search_catalog_states_adapter_selector_policy():
    spec = {item.name: item for item in LIVE_OPS_TOOLS}["search_current_hazards"]
    assert "adapter" in spec.description.lower()
    assert "hazard_ids" in spec.description
    assert "unbound tool does not enforce" in spec.description.lower()


def test_dead_tool_call_id_validation_codes_are_removed():
    assert "INVALID_TOOL_CALL_ID" not in LiveOpsValidationCode.__members__
    assert "DUPLICATE_TOOL_CALL_ID" not in LiveOpsValidationCode.__members__
    assert LiveOpsValidationCode.CANONICAL_KEY_TOO_LARGE.value == "CANONICAL_KEY_TOO_LARGE"


def test_dispatch_result_states_are_mutually_exclusive():
    from wilvor_ai.live_ops_specialist_runtime_contracts import (
        LiveOpsDispatchResult,
        LiveOpsDispatchStatus,
        LiveOpsRuntimeErrorCode,
        LiveOpsToolInvocation,
    )

    rejected = LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.VALIDATION_REJECTED,
        validation_failure=LiveOpsValidationFailure(
            code=LiveOpsValidationCode.EMPTY_BATCH
        ),
    )
    assert rejected.execution_error is None
    assert rejected.invocations == ()

    with pytest.raises(ContractValidationError) as exc:
        LiveOpsDispatchResult(
            status=LiveOpsDispatchStatus.VALIDATION_REJECTED,
            validation_failure=LiveOpsValidationFailure(
                code=LiveOpsValidationCode.EMPTY_BATCH
            ),
            execution_error=LiveOpsRuntimeErrorCode.INVALID_TOOL_CALL,
        )
    assert "validation_rejected_forbids_execution_error" in exc.value.errors

    raw = ToolResult(
        tool_name="get_observed_network_state",
        tool_call_id="id-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data={"observed": True, "complete_network_snapshot": False},
        evidence=(
            Evidence(
                source="wilvor.aircraft_current_state",
                source_records=(),
                query_timestamp_utc="2026-09-06T03:30:00Z",
                freshness_status=FreshnessStatus.UNKNOWN,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=("FRESHNESS_NOT_ESTABLISHED",),
                tool_call_id="id-1",
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc="2026-09-06T03:30:00Z",
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
    )
    projection = LiveOpsToolProjection(
        tool_name="get_observed_network_state",
        tool_call_id="id-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        as_of_utc="2026-09-06T03:30:00Z",
        data={"observed": True, "complete_network_snapshot": False},
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        evidence=(),
    )
    invocation = LiveOpsToolInvocation(
        tool_name="get_observed_network_state",
        tool_call_id="id-1",
        requested_arguments={},
        executed_arguments={},
        canonical_key='{"arguments":{},"name":"get_observed_network_state"}',
        raw_tool_result=raw,
        model_projection=projection,
    )
    completed = LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.COMPLETED,
        invocations=(invocation,),
        planned_tool_call_ids=("id-1",),
    )
    assert completed.execution_error is None
    assert completed.validation_failure is None

    failed = LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.EXECUTION_FAILED,
        invocations=(
            LiveOpsToolInvocation(
                tool_name="get_observed_network_state",
                tool_call_id="id-1",
                requested_arguments={},
                executed_arguments={},
                canonical_key='{"arguments":{},"name":"get_observed_network_state"}',
                raw_tool_result=raw,
                model_projection=None,
            ),
        ),
        execution_error=LiveOpsRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED,
        failed_tool_call_id="id-1",
        planned_tool_call_ids=("id-1",),
    )
    assert failed.validation_failure is None
    assert failed.invocations[0].raw_tool_result is raw
