"""Phase 2C-preflight Phase 0 input-allowlist and evidence provenance tests."""

from __future__ import annotations

from types import ModuleType
from typing import Callable

import pytest

from wilvor_ai import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    MatchCardinality,
    QueryExecutionTrace,
    SourceCompleteness,
    TemporalScope,
    ToolInputField,
    ToolResult,
    ToolResultStatus,
)


QUERY_TIME = "2026-09-07T19:00:00Z"


@pytest.fixture
def examples(
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    return load_repo_module(
        "phase_zero_ai_contract_examples",
        "tests/fixtures/ai_copilot_contract_examples.py",
    )


def assert_validation_error(expected_error: str, factory) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()
    assert expected_error in exc_info.value.errors


def _evidence(**overrides) -> Evidence:
    payload = {
        "source": "wilvor.historical.encounter_fact",
        "source_records": (),
        "query_timestamp_utc": QUERY_TIME,
        "freshness_status": FreshnessStatus.UNKNOWN,
        "confidence": ConfidenceLevel.HIGH,
        "limitations": ("HISTORICAL_FRESHNESS_NOT_ESTABLISHED",),
        "tool_call_id": "tool-call-test-provenance-001",
        "temporal_scope": TemporalScope.HISTORICAL,
    }
    payload.update(overrides)
    return Evidence(**payload)


def test_tool_input_field_required_and_optional_round_trip():
    required = ToolInputField(name="start_utc", required=True)
    optional = ToolInputField(name="aircraft_id", required=False)

    assert required.to_dict() == {"name": "start_utc", "required": True}
    assert optional.to_dict() == {"name": "aircraft_id", "required": False}
    assert ToolInputField.from_dict(required.to_dict()) == required
    assert ToolInputField.from_dict(optional.to_dict()) == optional


@pytest.mark.parametrize(
    "name",
    ["", " ", "as-of", "1start", "start utc", "start.utc"],
)
def test_tool_input_field_rejects_invalid_names(name):
    assert_validation_error(
        "invalid_name",
        lambda: ToolInputField(name=name, required=True),
    )


def test_tool_input_field_rejects_non_boolean_required():
    assert_validation_error(
        "invalid_required",
        lambda: ToolInputField(name="start_utc", required=1),
    )


def test_legacy_v1_evidence_fixture_omits_provenance_keys(examples):
    payload = examples.valid_tool_result("complete_current")
    result = ToolResult.from_dict(payload)
    serialized = result.evidence[0].to_dict()

    assert serialized == payload["evidence"][0]
    assert "completeness" not in serialized
    assert "match_cardinality" not in serialized
    assert "query_executions" not in serialized
    assert "error_code" not in serialized
    assert result.to_dict() == payload


def test_missing_provenance_keys_default_safely(examples):
    payload = examples.valid_tool_result("historical")
    evidence = Evidence.from_dict(payload["evidence"][0])

    assert evidence.completeness is None
    assert evidence.match_cardinality is None
    assert evidence.query_executions == ()
    assert evidence.error_code is None
    assert evidence.to_dict() == payload["evidence"][0]


def test_populated_provenance_round_trips_exactly():
    evidence = _evidence(
        completeness=SourceCompleteness(
            status="EVALUABLE",
            reason="window_evaluable",
            epoch_ids=("epoch-1", "epoch-2"),
            evaluated_as_of_utc=QUERY_TIME,
        ),
        match_cardinality=MatchCardinality(
            exact_count=0,
            is_exact=True,
            minimum_count=None,
        ),
        query_executions=(
            QueryExecutionTrace(
                query_id="summarize_historical_encounters",
                execution_id="qid-1",
                rows_returned=1,
                bytes_scanned=128,
                engine_scope="historical-analytics",
            ),
        ),
        error_code=None,
    )
    payload = evidence.to_dict()
    restored = Evidence.from_dict(payload)

    assert restored == evidence
    assert restored.to_dict() == payload
    assert payload["schema_version"] == "wilvor.ai.evidence.v1"


def test_malformed_provenance_fails_predictably():
    evidence = _evidence()
    payload = evidence.to_dict()
    payload["completeness"] = "EVALUABLE"

    assert_validation_error(
        "invalid_completeness",
        lambda: Evidence.from_dict(payload),
    )


def test_match_cardinality_unknown_exact_and_lower_bound():
    unknown = MatchCardinality()
    exact_zero = MatchCardinality(exact_count=0, is_exact=True)
    exact_positive = MatchCardinality(exact_count=4, is_exact=True)
    lower_bound = MatchCardinality(is_exact=False, minimum_count=21)

    assert unknown.to_dict() == {
        "exact_count": None,
        "is_exact": None,
        "minimum_count": None,
    }
    assert MatchCardinality.from_dict(unknown.to_dict()) == unknown
    assert exact_zero.exact_count == 0
    assert exact_positive.exact_count == 4
    assert lower_bound.minimum_count == 21
    assert MatchCardinality.from_dict(lower_bound.to_dict()) == lower_bound


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"is_exact": True}, "exact_requires_exact_count"),
        (
            {"exact_count": 0, "is_exact": True, "minimum_count": 1},
            "exact_forbids_minimum_count",
        ),
        (
            {"exact_count": 1, "is_exact": False, "minimum_count": 1},
            "inexact_forbids_exact_count",
        ),
        ({"is_exact": False}, "inexact_requires_minimum_count"),
        ({"exact_count": 1}, "unknown_cardinality_forbids_counts"),
        ({"exact_count": -1, "is_exact": True}, "invalid_exact_count"),
        ({"is_exact": False, "minimum_count": -1}, "invalid_minimum_count"),
        ({"is_exact": "true", "exact_count": 0}, "invalid_is_exact"),
    ],
)
def test_match_cardinality_rejects_contradictory_shapes(kwargs, error):
    assert_validation_error(error, lambda: MatchCardinality(**kwargs))


def test_query_execution_trace_valid_and_rejects_malformed():
    trace = QueryExecutionTrace(
        query_id="summarize_historical_risks",
        execution_id=None,
        rows_returned=0,
        bytes_scanned=0,
        engine_scope=None,
    )
    assert QueryExecutionTrace.from_dict(trace.to_dict()) == trace

    assert_validation_error(
        "invalid_query_id",
        lambda: QueryExecutionTrace(query_id=""),
    )
    assert_validation_error(
        "invalid_rows_returned",
        lambda: QueryExecutionTrace(
            query_id="summarize_historical_risks",
            rows_returned=-1,
        ),
    )
    assert_validation_error(
        "invalid_bytes_scanned",
        lambda: QueryExecutionTrace(
            query_id="summarize_historical_risks",
            bytes_scanned=-1,
        ),
    )
    payload = trace.to_dict()
    payload["rows_returned"] = -3
    assert_validation_error(
        "invalid_rows_returned",
        lambda: QueryExecutionTrace.from_dict(payload),
    )


def test_source_completeness_preserves_epoch_order():
    completeness = SourceCompleteness(
        status="EVALUABLE",
        reason="window_evaluable",
        epoch_ids=("epoch-b", "epoch-a"),
        evaluated_as_of_utc=QUERY_TIME,
    )
    assert completeness.epoch_ids == ("epoch-b", "epoch-a")
    assert SourceCompleteness.from_dict(completeness.to_dict()) == completeness


def test_source_completeness_rejects_malformed_values():
    assert_validation_error(
        "invalid_epoch_ids",
        lambda: SourceCompleteness(epoch_ids=("",)),
    )
    assert_validation_error(
        "invalid_evaluated_as_of_utc",
        lambda: SourceCompleteness(evaluated_as_of_utc="2026-09-07T19:00:00"),
    )
    assert_validation_error(
        "invalid_status",
        lambda: SourceCompleteness(status=" "),
    )


def test_verified_zero_is_representable_first_class():
    evidence = _evidence(
        completeness=SourceCompleteness(
            status="EVALUABLE",
            reason="window_evaluable",
            epoch_ids=("epoch-1",),
            evaluated_as_of_utc=QUERY_TIME,
        ),
        match_cardinality=MatchCardinality(exact_count=0, is_exact=True),
    )
    result = ToolResult(
        tool_name="summarize_historical_encounters",
        tool_call_id=evidence.tool_call_id,
        status=ToolResultStatus.NOT_FOUND,
        temporal_scope=TemporalScope.HISTORICAL,
        data={"status": "VERIFIED_ZERO", "physical_record_count": 0},
        evidence=(evidence,),
        as_of_utc=QUERY_TIME,
        limitations=(),
    )
    restored = ToolResult.from_dict(result.to_dict())

    assert restored.status is ToolResultStatus.NOT_FOUND
    assert restored.evidence[0].completeness is not None
    assert restored.evidence[0].completeness.status == "EVALUABLE"
    assert restored.evidence[0].match_cardinality is not None
    assert restored.evidence[0].match_cardinality.exact_count == 0
    assert restored.evidence[0].match_cardinality.is_exact is True
    assert "rows_returned" not in restored.data


def test_truncated_lower_bound_is_representable():
    evidence = _evidence(
        match_cardinality=MatchCardinality(
            exact_count=None,
            is_exact=False,
            minimum_count=21,
        )
    )
    restored = Evidence.from_dict(evidence.to_dict())

    assert restored.match_cardinality is not None
    assert restored.match_cardinality.is_exact is False
    assert restored.match_cardinality.minimum_count == 21
    assert restored.match_cardinality.exact_count is None


def test_two_risk_execution_traces_are_preserved():
    evidence = _evidence(
        query_executions=(
            QueryExecutionTrace(
                query_id="summarize_historical_risks",
                execution_id="qid-agg",
                rows_returned=1,
                bytes_scanned=64,
                engine_scope="historical-analytics",
            ),
            QueryExecutionTrace(
                query_id="summarize_historical_risks_by_level",
                execution_id="qid-dist",
                rows_returned=3,
                bytes_scanned=96,
                engine_scope="historical-analytics",
            ),
        )
    )
    restored = Evidence.from_dict(evidence.to_dict())
    traces = restored.query_executions

    assert [item.query_id for item in traces] == [
        "summarize_historical_risks",
        "summarize_historical_risks_by_level",
    ]
    assert traces[0].execution_id == "qid-agg"
    assert traces[1].execution_id == "qid-dist"
    assert traces[0].rows_returned == 1
    assert traces[1].rows_returned == 3
    assert traces[0].bytes_scanned == 64
    assert traces[1].bytes_scanned == 96
    assert "sql" not in traces[0].to_dict()
    assert "query_string" not in traces[0].to_dict()
    assert "output_location" not in traces[0].to_dict()
