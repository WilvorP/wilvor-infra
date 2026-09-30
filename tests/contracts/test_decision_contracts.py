"""Fail-closed tests for the DT1 decision evidence contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    ContractValidationError,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.decision_contracts import (
    V1_UNAVAILABLE_DECISION_CAPABILITIES,
    AlertCurrentLineage,
    DecisionAdvisoryAuthority,
    DecisionAlertLink,
    DecisionCapabilityGap,
    DecisionChainGap,
    DecisionEncounterLink,
    DecisionEvaluationState,
    DecisionEvidence,
    DecisionEvidenceKind,
    DecisionLimitationCode,
    DecisionRecommendationEvidence,
    DecisionRecommendationSet,
    DecisionReportedLinkState,
    DecisionRiskEvidence,
    DecisionRouteCapability,
    HazardSourceVersionLink,
    PersistedAirportEvaluationEvidence,
    PersistedConfidence,
    PersistedConfidenceSource,
    PersistedEvaluationScope,
    RecommendationActionType,
    RiskPresence,
    StoredRiskLevel,
    expected_decision_status,
    validate_decision_tool_result,
)
from wilvor_operational.linking import (
    NO_SNAPSHOT_LIMITATION as OPERATIONAL_NO_SNAPSHOT_LIMITATION,
)
from wilvor_operational.linking import (
    RECOMMENDATION_ABSENCE_LIMITATION as OPERATIONAL_RECOMMENDATION_ABSENCE,
)
from wilvor_operational.linking import LinkState


AS_OF = "2026-09-29T00:00:00Z"
UNTIL = "2026-09-29T00:15:00Z"
ADVISORY_NOTICE = (
    "Advisory decision support only. Human operational review is required. "
    "Wilvor does not issue autonomous flight-control, diversion, landing, "
    "dispatch, or ATC instructions."
)


def _errors(factory):
    with pytest.raises(ContractValidationError) as caught:
        factory()
    return caught.value.errors


def _capability() -> DecisionRouteCapability:
    return DecisionRouteCapability(validated_alternative_available=False)


def _stored_risk(
    *,
    level: StoredRiskLevel = StoredRiskLevel.LOW,
    score: int = 12,
    risk_id: str = "risk#low",
) -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.PRESENT,
        risk_id=risk_id,
        encounter_id="enc-1",
        scoring_ruleset_version="wilvor.risk.ruleset.v1",
        generated_at_utc=AS_OF,
        valid_until_utc=UNTIL,
        risk_level=level,
        risk_score=score,
        confidence=PersistedConfidence(
            ConfidenceLevel.LOW,
            PersistedConfidenceSource.RISK_RESULT,
        ),
    )


def _absent_risk() -> DecisionRiskEvidence:
    return DecisionRiskEvidence(
        presence=RiskPresence.ABSENT,
        encounter_id="enc-1",
    )


def _recommendation(
    recommendation_id: str = "rec#1",
    action: RecommendationActionType = RecommendationActionType.MONITOR,
    **overrides,
) -> DecisionRecommendationEvidence:
    values = {
        "recommendation_id": recommendation_id,
        "primary_action_type": action,
        "advisory_authority": DecisionAdvisoryAuthority.ADVISORY_ONLY,
        "recommendation_version": "ver-1",
        "ruleset_version": "wilvor.recommendation.ruleset.v1",
        "valid_from_utc": AS_OF,
        "valid_until_utc": UNTIL,
        "advisory_notice": ADVISORY_NOTICE,
        "confidence": PersistedConfidence(
            ConfidenceLevel.LOW,
            PersistedConfidenceSource.RECOMMENDATION,
        ),
    }
    values.update(overrides)
    return DecisionRecommendationEvidence(**values)


def _recommendations(*items, stale: tuple[str, ...] = ()) -> DecisionRecommendationSet:
    return DecisionRecommendationSet(
        current=tuple(items),
        absence_state=(
            DecisionReportedLinkState.PRESENT
            if items
            else DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
        ),
        excluded_stale_ids=stale,
    )


def _hazard(matched: bool = True) -> HazardSourceVersionLink:
    if matched:
        return HazardSourceVersionLink(
            hazard_id="haz-1",
            state=DecisionReportedLinkState.PRESENT,
            persisted_source_version="sv-1",
            current_source_version="sv-1",
        )
    return HazardSourceVersionLink(
        hazard_id="haz-1",
        state=DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH,
        persisted_source_version="sv-1",
        current_source_version="sv-2",
    )


def _encounter(risk, recommendations, hazard_link=None) -> DecisionEncounterLink:
    return DecisionEncounterLink(
        encounter_id="enc-1",
        projection_id="proj-1",
        aircraft_state_version="state-1",
        hazard=hazard_link or _hazard(),
        risk=risk,
        recommendations=recommendations,
    )


def _evidence(**overrides) -> DecisionEvidence:
    values = {
        "kind": DecisionEvidenceKind.DECISION_CONTEXT,
        "evaluation_state": DecisionEvaluationState.ESTABLISHED,
        "aircraft_in_current_set": True,
        "aircraft_id": "ac-1",
        "projection_state": DecisionReportedLinkState.PRESENT,
        "projection_id": "proj-1",
        "aircraft_state_version": "state-1",
        "encounters": (_encounter(_stored_risk(), _recommendations(_recommendation())),),
        "capability": _capability(),
        "chain_gaps": (),
        "limitation_codes": (),
    }
    values.update(overrides)
    return DecisionEvidence(**values)


def _tool_result(evidence: DecisionEvidence, limitations: tuple[str, ...] = ()) -> ToolResult:
    return ToolResult(
        tool_name="decision_evidence_contract",
        tool_call_id="tool-1",
        status=expected_decision_status(evidence),
        temporal_scope=TemporalScope.CURRENT,
        data=evidence.to_dict(),
        evidence=(
            Evidence(
                source="wilvor.decision.contract",
                source_records=(
                    SourceRecord(
                        record_id="ac-1",
                        source_version=None,
                        event_timestamp_utc=None,
                    ),
                ),
                query_timestamp_utc=AS_OF,
                freshness_status=FreshnessStatus.FRESH,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(),
                tool_call_id="tool-1",
                temporal_scope=TemporalScope.CURRENT,
            ),
        ),
        as_of_utc=AS_OF,
        limitations=limitations,
    )


def test_a_aircraft_not_found_is_not_found_without_a_fabricated_chain():
    evidence = _evidence(
        aircraft_in_current_set=False,
        aircraft_id="ac-missing",
        projection_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(DecisionChainGap.AIRCRAFT_NOT_IN_CURRENT_SET,),
    )

    validated = validate_decision_tool_result(_tool_result(evidence))

    assert validated.aircraft_in_current_set is False
    assert expected_decision_status(validated) is ToolResultStatus.NOT_FOUND
    assert validated.risk is None
    assert validated.recommendations is None
    assert validated.capability.validated_alternative_available is False
    assert "MONITOR" not in str(validated.to_dict())


def test_b_aircraft_without_current_projection_is_partial():
    evidence = _evidence(
        projection_state=DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES,
        projection_id=None,
        aircraft_state_version=None,
        encounters=(),
        chain_gaps=(DecisionChainGap.NO_CURRENT_PROJECTION,),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, ("No current projection.",))
    )

    assert expected_decision_status(validated) is ToolResultStatus.PARTIAL
    assert validated.encounters == ()


def test_c_current_encounter_with_missing_risk_stays_absent():
    evidence = _evidence(
        encounters=(
            _encounter(_absent_risk(), _recommendations()),
        ),
        chain_gaps=(
            DecisionChainGap.RISK_ABSENT,
            DecisionChainGap.RECOMMENDATION_ABSENT,
        ),
        limitation_codes=(
            DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
        ),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, ("Risk evidence is absent.",))
    )
    risk = validated.encounters[0].risk

    assert expected_decision_status(validated) is ToolResultStatus.PARTIAL
    assert risk.presence is RiskPresence.ABSENT
    assert risk.risk_level is None
    assert risk.risk_score is None
    assert risk.risk_id is None


def test_d_stored_low_risk_is_distinct_from_missing_risk():
    stored = _stored_risk()
    missing = _absent_risk()

    assert stored.presence is RiskPresence.PRESENT
    assert stored.risk_level is StoredRiskLevel.LOW
    assert stored.risk_score == 12
    assert missing.presence is RiskPresence.ABSENT
    assert missing.risk_level is None
    assert missing.risk_score is None
    assert stored != missing


def test_e_risk_without_recommendation_is_absence_not_monitor():
    evidence = _evidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        encounters=(),
        risk=_stored_risk(),
        recommendations=_recommendations(),
        chain_gaps=(DecisionChainGap.RECOMMENDATION_ABSENT,),
        limitation_codes=(
            DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION,
        ),
    )

    validated = validate_decision_tool_result(_tool_result(evidence))

    assert expected_decision_status(validated) is ToolResultStatus.SUCCESS
    assert validated.recommendations is not None
    assert validated.recommendations.current == ()
    assert (
        validated.recommendations.absence_state
        is DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )
    assert not hasattr(validated.recommendations, "primary_action_type")


def test_f_multiple_current_recommendations_have_no_winner():
    evidence = _evidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        encounters=(),
        risk=_stored_risk(),
        recommendations=_recommendations(
            _recommendation("rec#1"),
            _recommendation("rec#2", RecommendationActionType.MONITOR_AND_PREPARE_OPTIONS),
        ),
    )

    validated = validate_decision_tool_result(_tool_result(evidence))
    payload = validated.to_dict()

    assert validated.recommendations is not None
    assert [item.recommendation_id for item in validated.recommendations.current] == [
        "rec#1",
        "rec#2",
    ]
    assert "best_recommendation" not in payload
    assert "selected_recommendation" not in payload
    assert "primary_recommendation" not in payload
    payload["best_recommendation"] = "rec#1"
    assert "forbidden_decision_field" in _errors(
        lambda: DecisionEvidence.from_dict(payload)
    )


def test_g_stale_recommendation_cannot_be_current():
    evidence = _evidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        encounters=(),
        risk=_stored_risk(),
        recommendations=_recommendations(
            _recommendation("rec#current"),
            stale=("rec#stale",),
        ),
        chain_gaps=(DecisionChainGap.STALE_RECOMMENDATION_EXCLUDED,),
    )

    validated = validate_decision_tool_result(_tool_result(evidence))
    current_ids = {
        item.recommendation_id for item in validated.recommendations.current
    }

    assert "rec#stale" not in current_ids
    assert validated.recommendations.excluded_stale_ids == ("rec#stale",)
    assert "stale_recommendation_not_current" in _errors(
        lambda: _recommendations(_recommendation("rec#stale"), stale=("rec#stale",))
    )
    payload = _recommendation("rec#stale").to_dict()
    payload["stale"] = True
    assert "forbidden_decision_field" in _errors(
        lambda: DecisionRecommendationEvidence.from_dict(payload)
    )


def test_h_hazard_version_mismatch_cannot_be_repaired():
    evidence = _evidence(
        encounters=(
            _encounter(
                _stored_risk(),
                _recommendations(_recommendation()),
                _hazard(matched=False),
            ),
        ),
        chain_gaps=(DecisionChainGap.HAZARD_SOURCE_VERSION_MISMATCH,),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, ("Hazard source version mismatched.",))
    )
    link = validated.encounters[0].hazard

    assert link.persisted_source_version == "sv-1"
    assert link.current_source_version == "sv-2"
    assert link.state is DecisionReportedLinkState.HYDRATION_VERSION_MISMATCH
    assert expected_decision_status(validated) is ToolResultStatus.PARTIAL
    assert "hazard_version_mismatch_not_repairable" in _errors(
        lambda: HazardSourceVersionLink(
            hazard_id="haz-1",
            state=DecisionReportedLinkState.PRESENT,
            persisted_source_version="sv-1",
            current_source_version="sv-2",
        )
    )
    payload = link.to_dict()
    payload["repaired_hazard_source_version"] = "sv-2"
    assert "forbidden_decision_field" in _errors(
        lambda: HazardSourceVersionLink.from_dict(payload)
    )


def test_i_incomplete_chain_is_partial_with_limitations():
    evidence = _evidence(
        chain_gaps=(DecisionChainGap.INCOMPLETE_CURRENT_CHAIN,),
        limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, (OPERATIONAL_NO_SNAPSHOT_LIMITATION,))
    )

    assert expected_decision_status(validated) is ToolResultStatus.PARTIAL
    assert DecisionChainGap.INCOMPLETE_CURRENT_CHAIN in validated.chain_gaps


def test_j_no_snapshot_limitation_can_be_represented_on_success():
    evidence = _evidence(
        limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, (OPERATIONAL_NO_SNAPSHOT_LIMITATION,))
    )

    assert expected_decision_status(validated) is ToolResultStatus.SUCCESS
    assert (
        DecisionLimitationCode.NO_SNAPSHOT_LIMITATION
        in validated.limitation_codes
    )
    assert DecisionLimitationCode.NO_SNAPSHOT_LIMITATION.value == (
        "NO_SNAPSHOT_LIMITATION"
    )
    assert OPERATIONAL_NO_SNAPSHOT_LIMITATION.startswith("DynamoDB reads")


def test_k_no_validated_route_can_be_represented():
    evidence = _evidence()
    validated = validate_decision_tool_result(_tool_result(evidence))

    assert validated.capability.validated_alternative_available is False
    assert validated.capability.unavailable == V1_UNAVAILABLE_DECISION_CAPABILITIES
    assert (
        DecisionCapabilityGap.ROUTE_ALTERNATIVE_NOT_IMPLEMENTED
        in validated.capability.unavailable
    )
    assert expected_decision_status(validated) is not ToolResultStatus.NOT_FOUND
    assert "validated_alternative_forbidden" in _errors(
        lambda: DecisionRouteCapability(validated_alternative_available=True)
    )


def test_l_airport_evaluation_evidence_is_not_a_selected_diversion():
    evaluation = PersistedAirportEvaluationEvidence(
        airport_evaluation_id="eval#1",
        airport_assessment_ids=("aa#1",),
    )
    evidence = _evidence(
        encounters=(
            _encounter(
                _stored_risk(level=StoredRiskLevel.HIGH, score=80, risk_id="risk#high"),
                _recommendations(
                    _recommendation(
                        action=RecommendationActionType.EVALUATE_DIVERSION,
                        airport_evaluation=evaluation,
                    )
                ),
            ),
        ),
    )

    validated = validate_decision_tool_result(_tool_result(evidence))
    stored = validated.encounters[0].recommendations.current[0]

    assert stored.primary_action_type is RecommendationActionType.EVALUATE_DIVERSION
    assert stored.advisory_authority is DecisionAdvisoryAuthority.ADVISORY_ONLY
    assert stored.airport_evaluation is not None
    assert (
        stored.airport_evaluation.scope
        is PersistedEvaluationScope.PERSISTED_EVALUATION_EVIDENCE
    )
    assert validated.capability.validated_alternative_available is False
    payload = evaluation.to_dict()
    payload["selected_diversion"] = "aa#1"
    assert "forbidden_decision_field" in _errors(
        lambda: PersistedAirportEvaluationEvidence.from_dict(payload)
    )


def test_m_alert_can_be_current_through_risk_only():
    evidence = _evidence(
        alerts=(
            DecisionAlertLink(
                alert_id="alert-1",
                lineage=AlertCurrentLineage.RISK,
                risk_id="risk#low",
            ),
        )
    )

    validated = validate_decision_tool_result(_tool_result(evidence))

    assert validated.alerts[0].lineage is AlertCurrentLineage.RISK
    assert validated.alerts[0].recommendation_id is None
    assert expected_decision_status(validated) is ToolResultStatus.SUCCESS


def test_n_alert_can_be_current_through_recommendation_only():
    evidence = _evidence(
        alerts=(
            DecisionAlertLink(
                alert_id="alert-1",
                lineage=AlertCurrentLineage.RECOMMENDATION,
                recommendation_id="rec#1",
            ),
        )
    )

    validated = validate_decision_tool_result(_tool_result(evidence))

    assert validated.alerts[0].lineage is AlertCurrentLineage.RECOMMENDATION
    assert validated.alerts[0].risk_id is None


def test_o_alert_can_be_current_through_both_lineages():
    evidence = _evidence(
        alerts=(
            DecisionAlertLink(
                alert_id="alert-1",
                lineage=AlertCurrentLineage.BOTH,
                risk_id="risk#low",
                recommendation_id="rec#1",
            ),
        )
    )

    validated = validate_decision_tool_result(_tool_result(evidence))

    assert validated.alerts[0].lineage is AlertCurrentLineage.BOTH
    assert "invalid_alert_lineage" in _errors(
        lambda: DecisionAlertLink(
            alert_id="alert-1",
            lineage=AlertCurrentLineage.BOTH,
            risk_id="risk#low",
        )
    )


def test_p_route_and_waypoint_fields_are_rejected():
    payload = _evidence().to_dict()
    payload["waypoints"] = ["AAA", "BBB"]

    assert "forbidden_decision_field" in _errors(
        lambda: DecisionEvidence.from_dict(payload)
    )
    assert not hasattr(DecisionEvidence, "waypoints")
    assert not hasattr(DecisionRouteCapability, "route")


def test_q_recommendation_advisory_authority_is_preserved():
    evidence = _evidence()
    restored = DecisionEvidence.from_dict(evidence.to_dict())
    notice = restored.encounters[0].recommendations.current[0].advisory_notice

    assert notice == ADVISORY_NOTICE
    assert (
        restored.encounters[0].recommendations.current[0].advisory_authority
        is DecisionAdvisoryAuthority.ADVISORY_ONLY
    )
    payload = _recommendation().to_dict()
    payload["advisory_authority"] = "DIVERSION_CLEARANCE"
    assert "invalid_advisory_authority" in _errors(
        lambda: DecisionRecommendationEvidence.from_dict(payload)
    )


def test_r_no_model_generated_decision_confidence_field_exists():
    evidence = _evidence()
    payload = evidence.to_dict()

    assert "decision_confidence" not in payload
    assert not hasattr(evidence, "decision_confidence")
    confidence = evidence.encounters[0].risk.confidence
    assert confidence is not None
    assert confidence.source is PersistedConfidenceSource.RISK_RESULT
    payload["model_confidence"] = "HIGH"
    assert "forbidden_decision_field" in _errors(
        lambda: DecisionEvidence.from_dict(payload)
    )


def test_s_absence_cannot_synthesize_monitor():
    payload = _recommendations().to_dict()
    payload["primary_action_type"] = RecommendationActionType.MONITOR.value

    assert "unexpected_recommendation_set_field" in _errors(
        lambda: DecisionRecommendationSet.from_dict(payload)
    )


def test_t_missing_risk_cannot_synthesize_low():
    payload = _absent_risk().to_dict()
    payload["risk_level"] = StoredRiskLevel.LOW.value
    payload["risk_score"] = 0

    assert "absent_risk_forbids_stored_value" in _errors(
        lambda: DecisionRiskEvidence.from_dict(payload)
    )
    stored = _stored_risk().to_dict()
    stored["risk_level"] = "UNKNOWN"
    assert "invalid_risk_level" in _errors(
        lambda: DecisionRiskEvidence.from_dict(stored)
    )


def test_decision_evidence_round_trips_and_stays_current():
    evidence = _evidence(
        limitation_codes=(DecisionLimitationCode.NO_SNAPSHOT_LIMITATION,),
    )

    assert DecisionEvidence.from_dict(evidence.to_dict()) == evidence
    historical = _tool_result(evidence)
    historical = ToolResult(
        tool_name=historical.tool_name,
        tool_call_id=historical.tool_call_id,
        status=historical.status,
        temporal_scope=TemporalScope.HISTORICAL,
        data=historical.data,
        evidence=(
            Evidence(
                source="wilvor.decision.contract",
                source_records=(),
                query_timestamp_utc=AS_OF,
                freshness_status=FreshnessStatus.FRESH,
                confidence=ConfidenceLevel.UNKNOWN,
                limitations=(),
                tool_call_id="tool-1",
                temporal_scope=TemporalScope.HISTORICAL,
            ),
        ),
        as_of_utc=historical.as_of_utc,
        limitations=historical.limitations,
    )
    assert "decision_temporal_scope_must_be_current" in _errors(
        lambda: validate_decision_tool_result(historical)
    )


def test_unknown_evaluation_does_not_invent_a_chain():
    evidence = DecisionEvidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        evaluation_state=DecisionEvaluationState.NOT_ESTABLISHED,
        aircraft_in_current_set=False,
        projection_state=DecisionReportedLinkState.MISSING,
        capability=_capability(),
        chain_gaps=(),
        limitation_codes=(),
        risk=_absent_risk(),
    )

    validated = validate_decision_tool_result(
        _tool_result(evidence, ("Decision evidence was not established.",))
    )

    assert expected_decision_status(validated) is ToolResultStatus.UNKNOWN
    assert validated.risk is not None
    assert validated.risk.presence is RiskPresence.ABSENT
    assert validated.risk.risk_level is None


def test_context_may_omit_route_capability():
    payload = _evidence().to_dict()
    del payload["capability"]

    restored = DecisionEvidence.from_dict(payload)

    assert restored.kind is DecisionEvidenceKind.DECISION_CONTEXT
    assert restored.capability is None
    assert restored.to_dict()["capability"] is None
    payload["capability"] = None
    assert DecisionEvidence.from_dict(payload).capability is None


def test_risk_evidence_may_omit_route_capability():
    evidence = _evidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        encounters=(),
        risk=_stored_risk(),
        capability=None,
    )

    restored = DecisionEvidence.from_dict(evidence.to_dict())

    assert restored.capability is None
    assert restored.risk is not None
    assert restored.risk.presence is RiskPresence.PRESENT
    assert restored.risk.risk_level is StoredRiskLevel.LOW


def test_recommendation_evidence_may_omit_route_capability():
    evidence = _evidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        encounters=(),
        risk=_stored_risk(),
        recommendations=_recommendations(_recommendation()),
        capability=None,
    )

    restored = DecisionEvidence.from_dict(evidence.to_dict())

    assert restored.capability is None
    assert restored.recommendations is not None
    assert len(restored.recommendations.current) == 1


def _two_contract_encounters():
    first = _encounter(
        _stored_risk(risk_id="risk-1"),
        _recommendations(_recommendation("rec-1")),
    )
    second_risk = replace(
        _stored_risk(risk_id="risk-2", level=StoredRiskLevel.HIGH, score=80),
        encounter_id="enc-2",
    )
    second = replace(
        _encounter(
            second_risk,
            _recommendations(_recommendation("rec-2")),
        ),
        encounter_id="enc-2",
    )
    return first, second


def test_risk_evidence_with_two_encounters_has_no_top_level_winner():
    first, second = _two_contract_encounters()
    evidence = _evidence(
        kind=DecisionEvidenceKind.RISK_EVIDENCE,
        encounters=(first, second),
        risk=None,
        recommendations=None,
        capability=None,
    )

    restored = validate_decision_tool_result(_tool_result(evidence))

    assert restored.kind is DecisionEvidenceKind.RISK_EVIDENCE
    assert restored.risk is None
    assert restored.recommendations is None
    assert [item.risk.risk_id for item in restored.encounters] == ["risk-1", "risk-2"]
    assert expected_decision_status(restored) is ToolResultStatus.SUCCESS


def test_recommendation_evidence_with_two_encounters_preserves_both_sets():
    first, second = _two_contract_encounters()
    evidence = _evidence(
        kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
        encounters=(first, second),
        risk=None,
        recommendations=None,
        capability=None,
    )

    restored = validate_decision_tool_result(_tool_result(evidence))

    assert restored.kind is DecisionEvidenceKind.RECOMMENDATION_EVIDENCE
    assert restored.risk is None
    assert restored.recommendations is None
    assert [
        item.recommendations.current[0].recommendation_id for item in restored.encounters
    ] == ["rec-1", "rec-2"]
    assert expected_decision_status(restored) is ToolResultStatus.SUCCESS


def test_top_level_risk_plus_encounters_fails():
    first, second = _two_contract_encounters()
    assert "narrow_evidence_forbids_top_level_winner" in _errors(
        lambda: _evidence(
            kind=DecisionEvidenceKind.RISK_EVIDENCE,
            encounters=(first, second),
            risk=_stored_risk(),
            recommendations=None,
            capability=None,
        )
    )


def test_top_level_recommendations_plus_encounters_fails():
    first, second = _two_contract_encounters()
    assert "narrow_evidence_forbids_top_level_winner" in _errors(
        lambda: _evidence(
            kind=DecisionEvidenceKind.RECOMMENDATION_EVIDENCE,
            encounters=(first, second),
            risk=None,
            recommendations=_recommendations(_recommendation()),
            capability=None,
        )
    )


def test_present_route_capability_round_trips_and_stays_closed():
    evidence = _evidence()
    restored = DecisionEvidence.from_dict(evidence.to_dict())

    assert restored.capability == _capability()
    assert restored.capability is not None
    assert restored.capability.validated_alternative_available is False
    incomplete = restored.capability.to_dict()
    incomplete["unavailable"] = incomplete["unavailable"][:-1]
    assert "invalid_unavailable_capabilities" in _errors(
        lambda: DecisionRouteCapability.from_dict(incomplete)
    )
    malformed = evidence.to_dict()
    malformed["capability"] = {}
    assert "missing_unavailable" in _errors(
        lambda: DecisionEvidence.from_dict(malformed)
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "route",
        "routes",
        "waypoint",
        "waypoints",
        "trajectory",
        "trajectories",
        "flight_plan",
        "flight_plan_route",
    ],
)
def test_route_representations_stay_forbidden_without_capability(field_name):
    payload = _evidence(capability=None).to_dict()
    payload[field_name] = ["AAA"]

    assert "forbidden_decision_field" in _errors(
        lambda: DecisionEvidence.from_dict(payload)
    )


def test_reported_link_tokens_match_operational_link_state_names():
    reported = {item.value for item in DecisionReportedLinkState}
    operational = {item.value for item in LinkState}

    assert reported <= operational
    assert (
        DecisionReportedLinkState.ABSENT_FROM_CURRENT_CANDIDATES.value
        == LinkState.ABSENT_FROM_CURRENT_CANDIDATES.value
    )
    assert DecisionLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION.value == (
        "RECOMMENDATION_ABSENCE_LIMITATION"
    )
    assert "observed ACTIVE" in OPERATIONAL_RECOMMENDATION_ABSENCE
