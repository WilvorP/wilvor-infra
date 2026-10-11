"""LO1 Live Ops adapter validation and dispatch tests."""

from __future__ import annotations

from wilvor_ai.contracts import TemporalScope, ToolResult, ToolResultStatus
from wilvor_ai.live_ops import LiveOpsCall
from wilvor_ai.live_ops_adapter import LiveOpsAdapter, canonical_tool_call_key
from wilvor_ai.live_ops_model_contracts import (
    LIVE_OPS_ARGUMENT_STRING_MAX,
    LIVE_OPS_HAZARD_IDS_MAX,
    LiveOpsValidationCode,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchStatus,
    LiveOpsRuntimeErrorCode,
    LiveOpsSpecialistTrustedContext,
)
from wilvor_ai.live_ops_tool_projection import LiveOpsProjectionError
from wilvor_ai.model_contracts import ProposedToolCall
from tests.fixtures import live_ops_domain_fixtures as fixtures


def _context(**overrides):
    return LiveOpsSpecialistTrustedContext(
        tables=overrides.get("tables", fixtures.tables()),
        now_epoch=overrides.get("now_epoch", fixtures.NOW),
        correlation_id=overrides.get("correlation_id", fixtures.CORRELATION_ID),
    )


def _factory(*ids):
    values = list(ids)
    index = {"i": 0}

    def produce() -> str:
        value = values[index["i"]]
        index["i"] += 1
        return value

    return produce


def _call(name, **arguments):
    return ProposedToolCall(name=name, arguments=arguments)


def test_valid_single_and_two_call_batches():
    adapter = LiveOpsAdapter(_context(tables=fixtures.california_tables()))
    one = adapter.dispatch(
        (_call("search_current_hazards", region="California", product_type="SIGMET"),),
        tool_call_id_factory=_factory("tc-1"),
    )
    assert one.status is LiveOpsDispatchStatus.COMPLETED
    assert len(one.invocations) == 1
    assert one.invocations[0].raw_tool_result.temporal_scope is TemporalScope.CURRENT
    assert one.invocations[0].raw_tool_result.tool_call_id == "tc-1"
    assert one.invocations[0].model_projection is not None

    two = adapter.dispatch(
        (
            _call("find_aircraft_by_callsign", callsign="ual1"),
            _call("get_airport_operational_context", airport_id="ksea"),
        ),
        tool_call_id_factory=_factory("tc-2", "tc-3"),
    )
    assert two.status is LiveOpsDispatchStatus.COMPLETED
    assert [item.tool_call_id for item in two.invocations] == ["tc-2", "tc-3"]
    assert two.invocations[0].executed_arguments["callsign"] == "UAL1"
    assert two.invocations[1].executed_arguments["airport_id"] == "KSEA"


def test_empty_and_oversized_batches_execute_nothing():
    executions = []

    def handler(call, **_kwargs):
        executions.append(call.tool_call_id)
        raise AssertionError("should not execute")

    adapter = LiveOpsAdapter(
        _context(),
        handlers={name: handler for name in (
            "search_current_hazards",
            "get_observed_network_state",
            "find_aircraft_by_callsign",
        )},
    )
    empty = adapter.dispatch((), tool_call_id_factory=_factory("x"))
    assert empty.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert empty.validation_failure.code is LiveOpsValidationCode.EMPTY_BATCH
    assert empty.execution_error is None
    assert empty.invocations == ()

    oversized = adapter.dispatch(
        (
            _call("get_observed_network_state"),
            _call("find_aircraft_by_callsign", callsign="A"),
            _call("search_current_hazards", region="California"),
        ),
        tool_call_id_factory=_factory("a", "b", "c"),
    )
    assert oversized.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert oversized.validation_failure.code is LiveOpsValidationCode.BATCH_TOO_LARGE
    assert executions == []


def test_invalid_second_call_executes_zero_tools():
    executions = []

    def handler(call, **_kwargs):
        executions.append(call.tool_call_id)
        raise AssertionError("should not execute")

    adapter = LiveOpsAdapter(
        _context(),
        handlers={"search_current_impacts": handler, "search_current_hazards": handler},
    )
    result = adapter.dispatch(
        (
            _call("search_current_impacts", region="California"),
            _call("search_current_hazards"),
        ),
        tool_call_id_factory=_factory("a", "b"),
    )
    assert result.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert result.validation_failure.code is LiveOpsValidationCode.UNBOUNDED_HAZARD_SEARCH
    assert result.execution_error is None
    assert executions == []


def test_unknown_tool_and_unexpected_and_trusted_arguments():
    adapter = LiveOpsAdapter(_context())
    unknown = adapter.dispatch(
        (_call("not_a_tool", region="CA"),),
        tool_call_id_factory=_factory("a"),
    )
    assert unknown.validation_failure.code is LiveOpsValidationCode.UNKNOWN_TOOL

    extra = adapter.dispatch(
        (_call("get_observed_network_state", tables="Aircraft"),),
        tool_call_id_factory=_factory("b"),
    )
    assert extra.validation_failure.code is LiveOpsValidationCode.TRUSTED_ARGUMENT_FORBIDDEN

    unexpected = adapter.dispatch(
        (_call("find_aircraft_by_callsign", callsign="A", extra="x"),),
        tool_call_id_factory=_factory("c"),
    )
    assert unexpected.validation_failure.code is LiveOpsValidationCode.UNKNOWN_ARGUMENT


def test_required_and_conditional_selectors():
    adapter = LiveOpsAdapter(_context())
    missing_aircraft = adapter.dispatch(
        (_call("get_aircraft_operational_context"),),
        tool_call_id_factory=_factory("a"),
    )
    assert missing_aircraft.validation_failure.code is LiveOpsValidationCode.MISSING_REQUIRED_ARGUMENT

    type_only_hazard = adapter.dispatch(
        (_call("search_current_hazards", product_type="SIGMET"),),
        tool_call_id_factory=_factory("type-only"),
    )
    assert type_only_hazard.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert type_only_hazard.validation_failure.code is LiveOpsValidationCode.UNBOUNDED_HAZARD_SEARCH
    assert type_only_hazard.execution_error is None

    hazard_type_only = adapter.dispatch(
        (_call("search_current_hazards", hazard_type="TS"),),
        tool_call_id_factory=_factory("type-only-2"),
    )
    assert hazard_type_only.validation_failure.code is LiveOpsValidationCode.UNBOUNDED_HAZARD_SEARCH

    missing_impact = adapter.dispatch(
        (_call("search_current_impacts", product_type="SIGMET"),),
        tool_call_id_factory=_factory("b"),
    )
    assert missing_impact.validation_failure.code is LiveOpsValidationCode.MISSING_SELECTOR

    missing_encounter = adapter.dispatch(
        (_call("search_current_encounters"),),
        tool_call_id_factory=_factory("c"),
    )
    assert missing_encounter.validation_failure.code is LiveOpsValidationCode.MISSING_SELECTOR

    empty_ids = adapter.dispatch(
        (_call("search_current_impacts", hazard_ids=[]),),
        tool_call_id_factory=_factory("d"),
    )
    assert empty_ids.validation_failure.code is LiveOpsValidationCode.EMPTY_HAZARD_IDS


def test_type_whitespace_duplicate_and_length_validation():
    adapter = LiveOpsAdapter(_context())
    cases = [
        (_call("find_aircraft_by_callsign", callsign=1), LiveOpsValidationCode.INVALID_ARGUMENT_TYPE),
        (_call("find_aircraft_by_callsign", callsign=True), LiveOpsValidationCode.BOOLEAN_INTEGER_CONFUSION),
        (_call("find_aircraft_by_callsign", callsign="  A"), LiveOpsValidationCode.EMPTY_OR_WHITESPACE),
        (_call("find_aircraft_by_callsign", callsign="A" * (LIVE_OPS_ARGUMENT_STRING_MAX + 1)), LiveOpsValidationCode.STRING_TOO_LONG),
        (_call("search_current_hazards", hazard_ids="H1"), LiveOpsValidationCode.INVALID_ARGUMENT_TYPE),
        (_call("search_current_hazards", hazard_ids=["H1", "H1"]), LiveOpsValidationCode.DUPLICATE_HAZARD_ID),
        (
            _call("search_current_hazards", hazard_ids=[f"H{i}" for i in range(LIVE_OPS_HAZARD_IDS_MAX + 1)]),
            LiveOpsValidationCode.ARRAY_TOO_LARGE,
        ),
        (_call("search_current_hazards", hazard_ids=["H 1"]), LiveOpsValidationCode.EMPTY_OR_WHITESPACE),
        (_call("search_current_hazards", hazard_ids=[{"id": "H1"}]), LiveOpsValidationCode.NESTED_VALUE_FORBIDDEN),
        (_call("get_aircraft_operational_context", aircraft_id="ab cd"), LiveOpsValidationCode.INVALID_ARGUMENT_VALUE),
    ]
    for proposed, code in cases:
        result = adapter.dispatch((proposed,), tool_call_id_factory=_factory("x"))
        assert result.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
        assert result.validation_failure.code is code, (proposed, result.validation_failure)


def test_duplicate_canonical_calls_account_for_normalization():
    adapter = LiveOpsAdapter(_context())
    result = adapter.dispatch(
        (
            _call("search_current_hazards", region="California", hazard_ids=["H2", "H1"]),
            _call("search_current_hazards", region="california", hazard_ids=["H1", "H2"]),
        ),
        tool_call_id_factory=_factory("a", "b"),
    )
    assert result.validation_failure.code is LiveOpsValidationCode.DUPLICATE_TOOL_CALL
    left = canonical_tool_call_key(
        "search_current_hazards",
        {"region": "California", "hazard_ids": ["H2", "H1"]},
    )
    right = canonical_tool_call_key(
        "search_current_hazards",
        {"region": "california", "hazard_ids": ["H1", "H2"]},
    )
    assert left == right


def test_invalid_and_duplicate_generated_ids_reject_before_execution():
    executions = []

    def handler(call, **_kwargs):
        executions.append(call.tool_call_id)
        raise AssertionError("should not execute")

    adapter = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": handler},
    )
    blank = adapter.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("   "),
    )
    assert blank.status is LiveOpsDispatchStatus.EXECUTION_FAILED
    assert blank.execution_error is LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED
    assert executions == []

    reused_executions = []

    def keep(call, **_kwargs):
        reused_executions.append(call.tool_call_id)
        from wilvor_ai.live_ops import get_observed_network_state
        return get_observed_network_state(call)

    first = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": keep},
    )
    first.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("reuse-me"),
    )
    reused = first.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("reuse-me"),
    )
    assert reused.status is LiveOpsDispatchStatus.EXECUTION_FAILED
    assert reused.execution_error is LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED
    assert reused_executions == ["reuse-me"]

    before_explode = list(executions)

    def exploding():
        raise RuntimeError("factory exploded")

    exploded = adapter.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=exploding,
    )
    assert exploded.execution_error is LiveOpsRuntimeErrorCode.TOOL_CALL_ID_FACTORY_FAILED
    assert executions == before_explode


def test_identity_mismatch_and_historical_scope_preserve_raw():
    from wilvor_ai.contracts import ConfidenceLevel, Evidence, FreshnessStatus
    from wilvor_ai.live_ops import get_observed_network_state as get_network

    def mismatched(call: LiveOpsCall) -> ToolResult:
        result = get_network(call)
        return ToolResult(
            tool_name="search_current_hazards",
            tool_call_id=call.tool_call_id,
            status=result.status,
            temporal_scope=result.temporal_scope,
            data=result.data,
            evidence=result.evidence,
            as_of_utc=result.as_of_utc,
            limitations=result.limitations,
            correlation_id=result.correlation_id,
        )

    def historical(call: LiveOpsCall) -> ToolResult:
        evidence = Evidence(
            source="wilvor.aircraft_current_state",
            source_records=(),
            query_timestamp_utc=fixtures.AS_OF_UTC,
            freshness_status=FreshnessStatus.UNKNOWN,
            confidence=ConfidenceLevel.UNKNOWN,
            limitations=("FRESHNESS_NOT_ESTABLISHED",),
            tool_call_id=call.tool_call_id,
            temporal_scope=TemporalScope.HISTORICAL,
        )
        return ToolResult(
            tool_name="get_observed_network_state",
            tool_call_id=call.tool_call_id,
            status=ToolResultStatus.SUCCESS,
            temporal_scope=TemporalScope.HISTORICAL,
            data={"observed": True},
            evidence=(evidence,),
            as_of_utc=fixtures.AS_OF_UTC,
            limitations=("FRESHNESS_NOT_ESTABLISHED",),
            correlation_id=call.correlation_id,
        )

    adapter = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": mismatched},
    )
    mismatch = adapter.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("want-id"),
    )
    assert mismatch.status is LiveOpsDispatchStatus.EXECUTION_FAILED
    assert mismatch.execution_error is LiveOpsRuntimeErrorCode.TOOL_RESULT_IDENTITY_MISMATCH
    assert mismatch.invocations[0].raw_tool_result.tool_name == "search_current_hazards"
    assert mismatch.invocations[0].model_projection is None

    scoped = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": historical},
    )
    historic = scoped.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("hist-id"),
    )
    assert historic.execution_error is LiveOpsRuntimeErrorCode.TOOL_RESULT_IDENTITY_MISMATCH
    assert historic.invocations[0].raw_tool_result.temporal_scope is TemporalScope.HISTORICAL


def test_tool_exception_after_success_preserves_first_result():
    from wilvor_ai.live_ops import get_observed_network_state

    def boom(_call, **_kwargs):
        raise RuntimeError("secret table failure")

    adapter = LiveOpsAdapter(
        _context(),
        handlers={
            "get_observed_network_state": get_observed_network_state,
            "find_aircraft_by_callsign": boom,
        },
    )
    result = adapter.dispatch(
        (
            _call("get_observed_network_state"),
            _call("find_aircraft_by_callsign", callsign="UAL1"),
        ),
        tool_call_id_factory=_factory("keep-me", "fail-me"),
    )
    assert result.status is LiveOpsDispatchStatus.EXECUTION_FAILED
    assert result.execution_error is LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED
    assert len(result.invocations) == 1
    assert result.invocations[0].tool_call_id == "keep-me"
    assert result.invocations[0].raw_tool_result.status is ToolResultStatus.SUCCESS
    assert result.failed_tool_call_id == "fail-me"


def test_projection_failure_preserves_raw_result(monkeypatch):
    from wilvor_ai import live_ops_adapter as adapter_module
    from wilvor_ai.live_ops import get_observed_network_state

    def fail_projection(_result):
        raise LiveOpsProjectionError(("projection_mandatory_metadata_overflow",))

    monkeypatch.setattr(adapter_module, "project_live_ops_tool_result", fail_projection)
    adapter = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": get_observed_network_state},
    )
    result = adapter.dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("raw-kept"),
    )
    assert result.execution_error is LiveOpsRuntimeErrorCode.PROJECTION_INTEGRITY_FAILED
    assert result.invocations[0].raw_tool_result.tool_call_id == "raw-kept"
    assert result.invocations[0].model_projection is None
    assert "overflow" not in str(result.invocations[0].raw_tool_result.data)


def test_all_eight_tools_can_dispatch():
    adapter = LiveOpsAdapter(_context(tables=fixtures.california_tables()))
    pairs = [
        ("search_current_hazards", {"region": "California"}),
        ("search_current_impacts", {"region": "California"}),
        ("search_current_encounters", {"callsign": "TEST1"}),
        ("get_observed_network_state", {}),
        ("get_aircraft_operational_context", {"aircraft_id": "missing"}),
        ("get_hazard_operational_context", {"hazard_id": "missing"}),
        ("get_airport_operational_context", {"airport_id": "ksea"}),
        ("find_aircraft_by_callsign", {"callsign": "none"}),
    ]
    for index, (name, arguments) in enumerate(pairs, start=1):
        result = adapter.dispatch(
            (_call(name, **arguments),),
            tool_call_id_factory=_factory(f"all-{index}"),
        )
        assert result.status is LiveOpsDispatchStatus.COMPLETED, (name, result)
        assert result.execution_error is None
        assert result.invocations[0].raw_tool_result.tool_name == name


def test_canonical_key_overflow_rejects_before_execution():
    executions = []

    def handler(call, **_kwargs):
        executions.append(call.tool_call_id)
        raise AssertionError("should not execute")

    adapter = LiveOpsAdapter(
        _context(),
        handlers={"search_current_hazards": handler},
    )
    oversized_ids = [f"H{index:02d}" + ("x" * 400) for index in range(25)]
    assert all(len(item) <= LIVE_OPS_ARGUMENT_STRING_MAX for item in oversized_ids)
    rejected = adapter.dispatch(
        (_call("search_current_hazards", hazard_ids=oversized_ids),),
        tool_call_id_factory=_factory("too-big"),
    )
    assert rejected.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert rejected.validation_failure.code is LiveOpsValidationCode.CANONICAL_KEY_TOO_LARGE
    assert rejected.execution_error is None
    assert rejected.invocations == ()
    assert executions == []

    from wilvor_ai.live_ops import search_current_hazards

    allowed = LiveOpsAdapter(
        _context(),
        handlers={"search_current_hazards": search_current_hazards},
    )
    accepted = allowed.dispatch(
        (_call("search_current_hazards", hazard_ids=["H1", "H2"]),),
        tool_call_id_factory=_factory("small-ok"),
    )
    assert accepted.status is LiveOpsDispatchStatus.COMPLETED
    assert accepted.invocations[0].executed_arguments["hazard_ids"] == ["H1", "H2"]


def test_retained_argument_lists_are_not_shared_after_dispatch():
    from wilvor_ai.live_ops import search_current_hazards

    hazard_ids = ["H1", "H2"]
    adapter = LiveOpsAdapter(
        _context(),
        handlers={"search_current_hazards": search_current_hazards},
    )
    result = adapter.dispatch(
        (_call("search_current_hazards", hazard_ids=hazard_ids),),
        tool_call_id_factory=_factory("copy-me"),
    )
    assert result.status is LiveOpsDispatchStatus.COMPLETED
    hazard_ids.append("H3")
    assert result.invocations[0].requested_arguments["hazard_ids"] == ["H1", "H2"]
    assert result.invocations[0].executed_arguments["hazard_ids"] == ["H1", "H2"]


def test_dispatch_states_keep_validation_and_execution_distinct():
    from wilvor_ai.live_ops import get_observed_network_state

    adapter = LiveOpsAdapter(_context())
    rejected = adapter.dispatch((), tool_call_id_factory=_factory("none"))
    assert rejected.status is LiveOpsDispatchStatus.VALIDATION_REJECTED
    assert rejected.execution_error is None
    assert rejected.invocations == ()

    completed = LiveOpsAdapter(
        _context(),
        handlers={"get_observed_network_state": get_observed_network_state},
    ).dispatch(
        (_call("get_observed_network_state"),),
        tool_call_id_factory=_factory("done"),
    )
    assert completed.status is LiveOpsDispatchStatus.COMPLETED
    assert completed.execution_error is None
    assert completed.validation_failure is None
    assert completed.invocations[0].model_projection is not None

    def boom(_call, **_kwargs):
        raise RuntimeError("second failed")

    failed = LiveOpsAdapter(
        _context(),
        handlers={
            "get_observed_network_state": get_observed_network_state,
            "find_aircraft_by_callsign": boom,
        },
    ).dispatch(
        (
            _call("get_observed_network_state"),
            _call("find_aircraft_by_callsign", callsign="UAL1"),
        ),
        tool_call_id_factory=_factory("keep", "fail"),
    )
    assert failed.status is LiveOpsDispatchStatus.EXECUTION_FAILED
    assert failed.execution_error is LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED
    assert failed.validation_failure is None
    assert len(failed.invocations) == 1
    assert failed.invocations[0].raw_tool_result.status is ToolResultStatus.SUCCESS
    assert failed.planned_tool_call_ids == ("keep", "fail")
