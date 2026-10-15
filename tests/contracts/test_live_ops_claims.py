"""Closed Live Ops claim parsing. A parsed claim is not an operational fact."""

from __future__ import annotations

import pytest

from wilvor_ai.contracts import ContractValidationError
from wilvor_ai.live_ops_claims import (
    LIVE_OPS_IDENTITY_SET_MAX,
    LIVE_OPS_MAX_CLAIMS_PER_RESPONSE,
    AircraftObservedStateClaim,
    CallsignMatchSetClaim,
    ConfirmedEncounterClaim,
    ConfirmedImpactClaim,
    CoverageLimitationClaim,
    CrossToolContradictionClaim,
    CurrentnessClaim,
    HazardRegionIntersectionClaim,
    LiveOpsAdvisoryMode,
    LiveOpsClaimKind,
    LiveOpsContradictionSubjectKind,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsCurrentnessPredicate,
    LiveOpsExecutedScope,
    LiveOpsLimitationCode,
    LiveOpsRiskMode,
    LiveOpsObservedField,
    QueryResultZeroClaim,
    RecommendationSetClaim,
    RiskStateClaim,
    ScopedCountClaim,
    live_ops_claim_from_dict,
)


def test_catalog_is_the_sixteen_approved_kinds():
    assert [item.value for item in LiveOpsClaimKind] == [
        "CONFIRMED_IMPACT",
        "CONFIRMED_ENCOUNTER",
        "SCOPED_COUNT",
        "QUERY_RESULT_ZERO",
        "CONTEXT_ROOT_ABSENT",
        "HAZARD_REGION_INTERSECTION",
        "AIRCRAFT_OBSERVED_STATE",
        "AIRCRAFT_PROJECTION_STATE",
        "CURRENTNESS",
        "RISK_STATE",
        "RECOMMENDATION_SET",
        "ALERT_RECORD",
        "CALLSIGN_MATCH_SET",
        "COVERAGE_LIMITATION",
        "MODEL_VISIBLE_TRUNCATION",
        "CROSS_TOOL_CONTRADICTION",
    ]
    assert LIVE_OPS_MAX_CLAIMS_PER_RESPONSE == 32


def test_impact_claim_round_trips_without_prose():
    claim = ConfirmedImpactClaim(
        tool_call_id="tool-1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    restored = live_ops_claim_from_dict(claim.to_dict())
    assert restored == claim
    assert "statement" not in claim.to_dict()


def test_unknown_prose_and_instruction_fields_are_rejected():
    payload = ConfirmedImpactClaim(
        tool_call_id="tool-1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    ).to_dict()
    payload["explanation"] = "Aircraft penetrated the cell."
    with pytest.raises(ContractValidationError, match="unexpected_claim_prose"):
        live_ops_claim_from_dict(payload)

    payload = ConfirmedImpactClaim(
        tool_call_id="tool-1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    ).to_dict()
    payload["diversion_airport"] = "KSMF"
    with pytest.raises(ContractValidationError, match="forbidden_claim_field"):
        live_ops_claim_from_dict(payload)

    payload = ConfirmedImpactClaim(
        tool_call_id="tool-1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    ).to_dict()
    payload["winning_recommendation_id"] = "rec-1"
    with pytest.raises(ContractValidationError, match="forbidden_claim_field"):
        live_ops_claim_from_dict(payload)

    with pytest.raises(ContractValidationError, match="invalid_kind"):
        live_ops_claim_from_dict({"kind": "NOT_A_CLAIM"})


def test_numeric_and_set_shapes_are_closed():
    with pytest.raises(ContractValidationError, match="invalid_value"):
        ScopedCountClaim(
            tool_call_id="tool-1",
            tool_name="search_current_impacts",
            metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
            mode=LiveOpsCountMode.EXACT_RETURNED_SET,
            value=True,
            scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
        )
    with pytest.raises(ContractValidationError, match="invalid_value"):
        ScopedCountClaim(
            tool_call_id="tool-1",
            tool_name="search_current_impacts",
            metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
            mode=LiveOpsCountMode.EXACT_RETURNED_SET,
            value=1.5,
            scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
        )
    with pytest.raises(ContractValidationError, match="duplicate_recommendation_ids"):
        RecommendationSetClaim(
            tool_call_id="tool-1",
            tool_name="search_current_impacts",
            mode=LiveOpsAdvisoryMode.PRESENT_SET,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            recommendation_ids=("rec-1", "rec-1"),
        )
    with pytest.raises(ContractValidationError, match="unsorted_aircraft_ids"):
        CallsignMatchSetClaim(
            tool_call_id="tool-1",
            callsign="UAL123",
            aircraft_ids=("c", "a"),
        )
    with pytest.raises(ContractValidationError, match="invalid_zero_metric"):
        QueryResultZeroClaim(
            tool_call_id="tool-1",
            tool_name="get_observed_network_state",
            metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
            scope=LiveOpsExecutedScope(),
        )
    with pytest.raises(ContractValidationError, match="invalid_risk_score"):
        RiskStateClaim(
            tool_call_id="tool-1",
            tool_name="search_current_impacts",
            mode=LiveOpsRiskMode.PRESENT,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            risk_id="risk-1",
            risk_level="LOW",
            risk_score=1.5,
        )


def test_currentness_and_contradiction_identities_are_predicate_specific():
    with pytest.raises(ContractValidationError, match="inapplicable_identity_field"):
        CurrentnessClaim(
            tool_call_id="tool-1",
            tool_name="get_aircraft_operational_context",
            predicate=LiveOpsCurrentnessPredicate.AIRCRAFT_IS_CURRENT,
            value=True,
            aircraft_id="a",
            source_version="v1",
        )
    claim = CurrentnessClaim(
        tool_call_id="tool-1",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    assert live_ops_claim_from_dict(claim.to_dict()) == claim
    with pytest.raises(ContractValidationError, match="contradiction_requires_two_calls"):
        CrossToolContradictionClaim(
            tool_call_id_a="tool-1",
            tool_call_id_b="tool-1",
            subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
            hazard_id="H1",
        )


def test_recorded_overlap_is_not_a_model_source_limitation():
    with pytest.raises(ContractValidationError, match="not_a_source_limitation"):
        CoverageLimitationClaim(
            tool_call_id="tool-1",
            tool_name="search_current_impacts",
            code=LiveOpsLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY,
        )


def test_observed_value_rejects_non_scalars_and_blank_tokens():
    with pytest.raises(ContractValidationError, match="invalid_value"):
        AircraftObservedStateClaim(
            tool_call_id="tool-1",
            tool_name="get_aircraft_operational_context",
            aircraft_id="a",
            field=LiveOpsObservedField.ON_GROUND,
            value=None,
            retained_not_current=False,
        )
    with pytest.raises(ContractValidationError, match="invalid_tool_call_id"):
        ConfirmedEncounterClaim(
            tool_call_id="  ",
            tool_name="search_current_encounters",
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            hazard_source_version="v1",
        )


def test_intersection_tool_name_is_fixed():
    payload = HazardRegionIntersectionClaim(
        tool_call_id="tool-1",
        hazard_id="H1",
        source_version="v1",
        region_code="CA",
        region_name="California",
        region_query="California",
    ).to_dict()
    payload["tool_name"] = "search_current_impacts"
    with pytest.raises(ContractValidationError, match="invalid_tool_name"):
        live_ops_claim_from_dict(payload)
    payload["tool_name"] = "search_current_hazards"
    payload["route"] = ["KSFO", "KLAX"]
    with pytest.raises(ContractValidationError, match="forbidden_claim_field"):
        live_ops_claim_from_dict(payload)


def test_identity_sets_and_recorded_flags_are_bounded():
    accepted = tuple(f"a{index:02d}" for index in range(LIVE_OPS_IDENTITY_SET_MAX))
    claim = CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL123", aircraft_ids=accepted)
    assert claim.aircraft_ids == accepted
    payload = claim.to_dict()
    assert live_ops_claim_from_dict(payload) == claim

    oversized = [f"a{index:02d}" for index in range(LIVE_OPS_IDENTITY_SET_MAX + 1)]
    with pytest.raises(ContractValidationError, match="too_many_aircraft_ids"):
        CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL123", aircraft_ids=tuple(oversized))
    payload["aircraft_ids"] = oversized
    with pytest.raises(ContractValidationError, match="too_many_aircraft_ids"):
        CallsignMatchSetClaim.from_dict(payload)
    with pytest.raises(ContractValidationError, match="too_many_aircraft_ids"):
        live_ops_claim_from_dict(payload)

    with pytest.raises(ContractValidationError, match="duplicate_aircraft_ids"):
        CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL123", aircraft_ids=("a", "a"))
    with pytest.raises(ContractValidationError, match="unsorted_aircraft_ids"):
        CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL123", aircraft_ids=("c", "a"))

    recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=accepted,
    )
    assert len(recommendations.recommendation_ids) == LIVE_OPS_IDENTITY_SET_MAX
    recommendation_payload = recommendations.to_dict()
    recommendation_payload["recommendation_ids"] = oversized
    with pytest.raises(ContractValidationError, match="too_many_recommendation_ids"):
        RecommendationSetClaim.from_dict(recommendation_payload)
    recommendation_payload["winning_recommendation_id"] = "rec-1"
    with pytest.raises(ContractValidationError, match="forbidden_claim_field"):
        live_ops_claim_from_dict(recommendation_payload)

    flags = (
        ("centerline_intersects", False),
        ("corridor_intersects", True),
        ("exact_intersection_confirmed", False),
        ("inside_now", True),
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        recorded_flags=flags,
    )
    assert len(encounter.recorded_flags) == 4
    with pytest.raises(ContractValidationError, match="too_many_recorded_flags"):
        ConfirmedEncounterClaim(
            tool_call_id="enc-h1",
            tool_name="search_current_encounters",
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            hazard_source_version="v1",
            recorded_flags=flags + (("corridor_intersects", False),),
        )
    encounter_payload = encounter.to_dict()
    encounter_payload["recorded_flags"] = [
        {"name": "corridor_intersects", "value": True} for _index in range(5)
    ]
    with pytest.raises(ContractValidationError, match="too_many_recorded_flags"):
        ConfirmedEncounterClaim.from_dict(encounter_payload)
    encounter_payload["recorded_flags"] = [
        {"name": "corridor_intersects", "value": True},
        {"name": "inside_now", "value": True},
        {"name": "centerline_intersects", "value": False},
        {"name": "not_a_flag", "value": True},
    ]
    with pytest.raises(ContractValidationError, match="invalid_name|invalid_recorded_flags"):
        ConfirmedEncounterClaim.from_dict(encounter_payload)
