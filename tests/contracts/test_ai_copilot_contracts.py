from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal
from types import ModuleType
from typing import Callable

import pytest

from wilvor_ai import (
    ALLOWED_V1_CAPABILITIES,
    PROHIBITED_V1_CAPABILITIES,
    V1_AUTHORITY,
    AgentAuthority,
    AgentAuthorityMode,
    AgentCapability,
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FinalAIResponse,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)


@pytest.fixture
def examples(
    load_repo_module: Callable[[str, str], ModuleType],
) -> ModuleType:
    return load_repo_module(
        "phase_zero_ai_contract_examples",
        "tests/fixtures/ai_copilot_contract_examples.py",
    )


def assert_validation_error(
    expected_error: str,
    factory: Callable[[], object],
) -> None:
    with pytest.raises(ContractValidationError) as exc_info:
        factory()

    assert expected_error in exc_info.value.errors


def test_temporal_scope_contract_is_closed():
    assert {scope.value for scope in TemporalScope} == {
        "CURRENT",
        "HISTORICAL",
        "HYBRID",
    }


def test_confidence_freshness_and_result_status_contracts_are_closed():
    assert {value.value for value in ConfidenceLevel} == {
        "HIGH",
        "MEDIUM",
        "LOW",
        "UNKNOWN",
    }
    assert {value.value for value in FreshnessStatus} == {
        "FRESH",
        "ACCEPTABLE",
        "STALE",
        "UNAVAILABLE",
        "UNKNOWN",
    }
    assert {value.value for value in ToolResultStatus} == {
        "SUCCESS",
        "PARTIAL",
        "NOT_FOUND",
        "STALE",
        "UNAVAILABLE",
        "UNKNOWN",
    }


@pytest.mark.parametrize(
    "fixture_name",
    [
        "complete_current",
        "historical",
        "genuinely_hybrid",
        "unknown_preserved",
        "stale",
        "partial",
        "source_unavailable",
        "not_found",
        "multiple_source_records",
    ],
)
def test_approved_tool_result_examples_are_valid(
    examples,
    fixture_name,
):
    payload = examples.valid_tool_result(fixture_name)
    result = ToolResult.from_dict(payload)

    assert result.to_dict() == payload


@pytest.mark.parametrize(
    "fixture_name",
    [
        "hybrid_from_independent_results",
        "informational_without_required_review",
        "safety_sensitive_with_required_review",
        "unknown_with_limitations",
    ],
)
def test_approved_final_response_examples_are_valid(
    examples,
    fixture_name,
):
    payload = examples.valid_final_response(fixture_name)
    response = FinalAIResponse.from_dict(payload)

    assert response.to_dict() == payload


def test_current_and_historical_tool_results_remain_independently_scoped(
    examples,
):
    current = ToolResult.from_dict(
        examples.valid_tool_result("complete_current")
    )
    historical = ToolResult.from_dict(
        examples.valid_tool_result("historical")
    )

    assert current.temporal_scope is TemporalScope.CURRENT
    assert {
        item.temporal_scope for item in current.evidence
    } == {TemporalScope.CURRENT}

    assert historical.temporal_scope is TemporalScope.HISTORICAL
    assert {
        item.temporal_scope for item in historical.evidence
    } == {TemporalScope.HISTORICAL}


def test_hybrid_tool_result_requires_both_evidence_scopes(examples):
    payload = examples.valid_tool_result("genuinely_hybrid")
    payload["evidence"] = [payload["evidence"][0]]

    assert_validation_error(
        "hybrid_requires_current_and_historical_evidence",
        lambda: ToolResult.from_dict(payload),
    )


def test_hybrid_final_response_can_aggregate_independent_tool_evidence(
    examples,
):
    payload = examples.valid_final_response(
        "hybrid_from_independent_results"
    )
    response = FinalAIResponse.from_dict(payload)

    assert response.temporal_scope is TemporalScope.HYBRID
    assert {
        item.temporal_scope for item in response.evidence
    } == {
        TemporalScope.CURRENT,
        TemporalScope.HISTORICAL,
    }
    assert {
        item.tool_call_id for item in response.evidence
    } == {
        "tool-call-test-current-001",
        "tool-call-test-historical-001",
    }


def test_non_hybrid_result_rejects_mixed_scope_evidence(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["evidence"].append(examples.HISTORICAL_EVIDENCE)

    assert_validation_error(
        "evidence_tool_call_id_mismatch",
        lambda: ToolResult.from_dict(payload),
    )

    payload["evidence"][-1]["tool_call_id"] = payload["tool_call_id"]

    assert_validation_error(
        "evidence_temporal_scope_mismatch",
        lambda: ToolResult.from_dict(payload),
    )


def test_old_historical_event_is_not_automatically_stale(examples):
    result = ToolResult.from_dict(
        examples.valid_tool_result("historical")
    )
    record = result.evidence[0].source_records[0]

    assert record.event_timestamp_utc == examples.HISTORICAL_EVENT_TIME
    assert result.evidence[0].freshness_status is FreshnessStatus.FRESH
    assert result.status is ToolResultStatus.SUCCESS


def test_unknown_historical_freshness_with_limitation_is_valid(examples):
    result = ToolResult.from_dict(
        examples.valid_tool_result("multiple_source_records")
    )

    assert result.evidence[0].temporal_scope is TemporalScope.HISTORICAL
    assert (
        result.evidence[0].freshness_status
        is FreshnessStatus.UNKNOWN
    )
    assert result.evidence[0].limitations


def test_unknown_or_unavailable_freshness_requires_a_limitation(examples):
    payload = examples.valid_tool_result("source_unavailable")
    payload["evidence"][0]["limitations"] = []

    assert_validation_error(
        "uncertain_freshness_requires_limitations",
        lambda: ToolResult.from_dict(payload),
    )


def test_unknown_null_false_zero_and_empty_collection_survive_round_trip(
    examples,
):
    payload = examples.valid_tool_result("unknown_preserved")
    result = ToolResult.from_dict(payload)
    data = result.to_dict()["data"]

    assert isinstance(data, dict)
    assert data["altitude_overlap_status"] == "UNKNOWN"
    assert data["estimated_altitude_ft"] is None
    assert data["inside_now"] is False
    assert data["matched_record_count"] == 0
    assert data["items"] == []
    assert ToolResult.from_dict(result.to_dict()).to_dict() == result.to_dict()


def test_semantic_round_trip_does_not_depend_on_dictionary_key_order(
    examples,
):
    payload = examples.valid_tool_result("complete_current")
    reordered = dict(reversed(list(payload.items())))

    original = ToolResult.from_dict(payload)
    reparsed = ToolResult.from_dict(reordered)

    assert original == reparsed
    assert original.to_dict() == reparsed.to_dict()


def test_unavailable_source_does_not_fabricate_records_or_as_of_time(
    examples,
):
    result = ToolResult.from_dict(
        examples.valid_tool_result("source_unavailable")
    )

    assert result.status is ToolResultStatus.UNAVAILABLE
    assert result.data is None
    assert result.as_of_utc is None
    assert result.evidence[0].source_records == ()


def test_source_version_may_be_explicitly_unavailable(examples):
    result = ToolResult.from_dict(
        examples.valid_tool_result("multiple_source_records")
    )

    assert len(result.evidence[0].source_records) == 2
    assert result.evidence[0].source_records[1].source_version is None
    assert (
        result.to_dict()["evidence"][0]["source_records"][1][
            "source_version"
        ]
        is None
    )


def test_not_found_preserves_verified_empty_results_and_zero(examples):
    result = ToolResult.from_dict(
        examples.valid_tool_result("not_found")
    )

    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.data == {"items": [], "count": 0}


def test_partial_and_stale_results_preserve_limitations(examples):
    partial = ToolResult.from_dict(
        examples.valid_tool_result("partial")
    )
    stale = ToolResult.from_dict(
        examples.valid_tool_result("stale")
    )

    assert partial.status is ToolResultStatus.PARTIAL
    assert partial.limitations
    assert stale.status is ToolResultStatus.STALE
    assert stale.limitations
    assert any(
        item.freshness_status is FreshnessStatus.STALE
        for item in stale.evidence
    )


def test_human_review_required_is_explicit_and_both_booleans_are_valid(
    examples,
):
    informational = FinalAIResponse.from_dict(
        examples.valid_final_response(
            "informational_without_required_review"
        )
    )
    safety_sensitive = FinalAIResponse.from_dict(
        examples.valid_final_response(
            "safety_sensitive_with_required_review"
        )
    )

    assert informational.human_review_required is False
    assert informational.to_dict()["human_review_required"] is False
    assert safety_sensitive.human_review_required is True
    assert safety_sensitive.to_dict()["human_review_required"] is True


def test_human_review_required_must_be_present(examples):
    payload = examples.valid_final_response(
        "informational_without_required_review"
    )
    del payload["human_review_required"]

    assert_validation_error(
        "missing_human_review_required",
        lambda: FinalAIResponse.from_dict(payload),
    )


def test_human_review_required_must_be_boolean(examples):
    payload = examples.valid_final_response(
        "informational_without_required_review"
    )
    payload["human_review_required"] = "false"

    assert_validation_error(
        "human_review_required_must_be_boolean",
        lambda: FinalAIResponse.from_dict(payload),
    )


def test_empty_visualization_and_navigation_collections_are_supported(
    examples,
):
    response = FinalAIResponse.from_dict(
        examples.valid_final_response(
            "informational_without_required_review"
        )
    )

    assert response.visualizations == ()
    assert response.navigation == ()
    assert response.to_dict()["visualizations"] == []
    assert response.to_dict()["navigation"] == []


def test_contract_objects_are_frozen(examples):
    result = ToolResult.from_dict(
        examples.valid_tool_result("complete_current")
    )

    with pytest.raises(FrozenInstanceError):
        result.status = ToolResultStatus.UNKNOWN


def test_invalid_temporal_scope_uses_contract_validation_error(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["temporal_scope"] = "FUTURE"

    assert_validation_error(
        "invalid_temporal_scope",
        lambda: ToolResult.from_dict(payload),
    )


def test_invalid_result_status_uses_contract_validation_error(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["status"] = "COMPLETE"

    assert_validation_error(
        "invalid_status",
        lambda: ToolResult.from_dict(payload),
    )


def test_naive_or_non_utc_timestamps_are_rejected(examples):
    naive = examples.valid_tool_result("complete_current")
    naive["as_of_utc"] = "2026-09-07T19:00:00"

    assert_validation_error(
        "invalid_as_of_utc",
        lambda: ToolResult.from_dict(naive),
    )

    non_utc = examples.valid_tool_result("complete_current")
    non_utc["as_of_utc"] = "2026-09-07T15:00:00-04:00"

    assert_validation_error(
        "invalid_as_of_utc",
        lambda: ToolResult.from_dict(non_utc),
    )


def test_blank_identifiers_are_rejected(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["evidence"][0]["source_records"][0]["record_id"] = " "

    assert_validation_error(
        "invalid_record_id",
        lambda: ToolResult.from_dict(payload),
    )


def test_success_requires_data_and_evidence(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["data"] = None
    payload["evidence"] = []

    with pytest.raises(ContractValidationError) as exc_info:
        ToolResult.from_dict(payload)

    assert {
        "success_requires_data",
        "success_requires_evidence",
    }.issubset(exc_info.value.errors)


def test_partial_requires_limitations(examples):
    payload = examples.valid_tool_result("partial")
    payload["limitations"] = []

    assert_validation_error(
        "partial_requires_limitations",
        lambda: ToolResult.from_dict(payload),
    )


def test_stale_status_requires_stale_evidence(examples):
    payload = examples.valid_tool_result("stale")
    payload["evidence"][0]["freshness_status"] = "FRESH"

    assert_validation_error(
        "stale_requires_stale_evidence",
        lambda: ToolResult.from_dict(payload),
    )


def test_tool_result_rejects_mismatched_tool_call_ids(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["evidence"][0]["tool_call_id"] = "tool-call-test-other"

    assert_validation_error(
        "evidence_tool_call_id_mismatch",
        lambda: ToolResult.from_dict(payload),
    )


def test_tool_result_rejects_non_json_data(examples):
    payload = examples.valid_tool_result("complete_current")
    payload["data"] = {"risk_score": Decimal("1.5")}

    assert_validation_error(
        "data_must_be_json_compatible",
        lambda: ToolResult.from_dict(payload),
    )


def test_known_final_confidence_requires_evidence(examples):
    payload = examples.valid_final_response(
        "informational_without_required_review"
    )
    payload["evidence"] = []

    assert_validation_error(
        "known_confidence_requires_evidence",
        lambda: FinalAIResponse.from_dict(payload),
    )


def test_all_invalid_construction_uses_one_exception_type():
    assert_validation_error(
        "invalid_record_id",
        lambda: SourceRecord(
            record_id="",
            source_version=None,
            event_timestamp_utc=None,
        ),
    )
    assert_validation_error(
        "invalid_evidence",
        lambda: Evidence.from_dict("not-a-mapping"),
    )
    assert_validation_error(
        "invalid_tool_result",
        lambda: ToolResult.from_dict([]),
    )
    assert_validation_error(
        "invalid_final_response",
        lambda: FinalAIResponse.from_dict(None),
    )
    assert_validation_error(
        "invalid_allowed_capabilities",
        lambda: AgentAuthority(
            mode=AgentAuthorityMode.READ_ONLY_ADVISORY,
            allowed_capabilities=[],
            prohibited_capabilities=PROHIBITED_V1_CAPABILITIES,
        ),
    )


def test_v1_authority_is_read_only_advisory_and_machine_readable():
    assert V1_AUTHORITY.mode is AgentAuthorityMode.READ_ONLY_ADVISORY
    assert V1_AUTHORITY.allowed_capabilities == ALLOWED_V1_CAPABILITIES
    assert (
        V1_AUTHORITY.prohibited_capabilities
        == PROHIBITED_V1_CAPABILITIES
    )
    assert not (
        V1_AUTHORITY.allowed_capabilities
        & V1_AUTHORITY.prohibited_capabilities
    )


@pytest.mark.parametrize(
    "capability",
    [
        AgentCapability.MUTATE_OPERATIONAL_STATE,
        AgentCapability.MUTATE_ALERTS,
        (
            AgentCapability
            .CREATE_OR_OVERWRITE_DETERMINISTIC_RECOMMENDATIONS
        ),
        AgentCapability.ALTER_RISK_RESULTS,
        AgentCapability.ALTER_AIRCRAFT_PROJECTIONS,
        AgentCapability.ALTER_HAZARD_RECORDS,
        AgentCapability.EXECUTE_REROUTES,
        AgentCapability.ISSUE_AUTONOMOUS_FLIGHT_CONTROL_INSTRUCTIONS,
        AgentCapability.ISSUE_LANDING_INSTRUCTIONS,
        AgentCapability.ISSUE_ALTITUDE_INSTRUCTIONS,
        AgentCapability.ISSUE_ATC_INSTRUCTIONS,
        AgentCapability.MUTATE_AWS_INFRASTRUCTURE,
    ],
)
def test_v1_authority_explicitly_prohibits_unsafe_capabilities(
    capability,
):
    assert capability in PROHIBITED_V1_CAPABILITIES


def test_authority_round_trip_is_semantically_deterministic():
    payload = V1_AUTHORITY.to_dict()
    reparsed = AgentAuthority.from_dict(payload)

    assert reparsed == V1_AUTHORITY
    assert reparsed.to_dict() == payload


def test_authority_declaration_does_not_claim_runtime_enforcement():
    assert "enforce" not in V1_AUTHORITY.to_dict()
