"""Deterministic Live Ops claim verification against raw ToolResults."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from tests.fixtures import live_ops_domain_fixtures as fixtures
from wilvor_ai.contracts import (
    ContractValidationError,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops import (
    LiveOpsCall,
    find_aircraft_by_callsign,
    get_aircraft_operational_context,
    get_airport_operational_context,
    get_hazard_operational_context,
    get_observed_network_state,
    search_current_encounters,
    search_current_hazards,
    search_current_impacts,
)
from wilvor_ai.live_ops_claims import (
    LIVE_OPS_MAX_CLAIMS_PER_RESPONSE,
    AircraftObservedStateClaim,
    AircraftProjectionStateClaim,
    AlertRecordClaim,
    CallsignMatchSetClaim,
    ConfirmedEncounterClaim,
    ConfirmedImpactClaim,
    ContextRootAbsentClaim,
    CoverageLimitationClaim,
    CrossToolContradictionClaim,
    CurrentnessClaim,
    HazardRegionIntersectionClaim,
    LiveOpsAdvisoryMode,
    LiveOpsContradictionSubjectKind,
    LiveOpsCountMetric,
    LiveOpsCountMode,
    LiveOpsCurrentnessPredicate,
    LiveOpsExecutedScope,
    LiveOpsLimitationCode,
    LiveOpsObservedField,
    LiveOpsProjectionField,
    LiveOpsRiskMode,
    ModelVisibleTruncationClaim,
    QueryResultZeroClaim,
    RecommendationSetClaim,
    RiskStateClaim,
    ScopedCountClaim,
)
from wilvor_ai.live_ops_evidence_verifier import (
    LiveOpsClaimAssessment,
    LiveOpsClaimRejectionCode,
    LiveOpsClaimVerdict,
    LiveOpsRenderingPosture,
    LiveOpsTrustedEvidenceLedger,
    LiveOpsVerificationOutcome,
    LiveOpsVerificationResult,
    verify_live_ops_claims,
)
from wilvor_ai.live_ops_model_contracts import (
    LiveOpsValidationCode,
    LiveOpsValidationFailure,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsRuntimeErrorCode,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import project_live_ops_tool_result


def _run(function, tool_call_id, *args, tables=None, **kwargs):
    call = LiveOpsCall(
        tables=tables if tables is not None else fixtures.california_tables(),
        now_epoch=fixtures.NOW,
        tool_call_id=tool_call_id,
        correlation_id=fixtures.CORRELATION_ID,
    )
    return function(call, *args, **kwargs)


def _reidentify(result: ToolResult, tool_call_id: str) -> ToolResult:
    evidence = tuple(replace(item, tool_call_id=tool_call_id) for item in result.evidence)
    return replace(result, tool_call_id=tool_call_id, evidence=evidence)


def _mutate(result: ToolResult, mutator) -> ToolResult:
    data = copy.deepcopy(dict(result.data))
    mutator(data)
    return replace(result, data=data)


def _invocation(result: ToolResult, executed: dict, *, projection=True):
    model_projection = project_live_ops_tool_result(result) if projection else None
    return LiveOpsToolInvocation(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        requested_arguments=executed,
        executed_arguments=executed,
        canonical_key=result.tool_name,
        raw_tool_result=result,
        model_projection=model_projection,
    )


def _completed(*invocations: LiveOpsToolInvocation) -> LiveOpsDispatchResult:
    return LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.COMPLETED,
        invocations=invocations,
        planned_tool_call_ids=tuple(item.tool_call_id for item in invocations),
    )


def _ledger(*dispatches: LiveOpsDispatchResult, correlation_id=fixtures.CORRELATION_ID, now_epoch=fixtures.NOW):
    return LiveOpsTrustedEvidenceLedger(
        correlation_id=correlation_id,
        now_epoch=now_epoch,
        dispatches=dispatches,
    )


def _verify(claims, *invocations, **ledger_kwargs):
    return verify_live_ops_claims(tuple(claims), _ledger(_completed(*invocations), **ledger_kwargs))


def _codes(result, index=0):
    assessment = result.assessments[index]
    return assessment.verdict, assessment.codes


def _impact(tool_call_id="imp-h1", **overrides):
    payload = dict(
        tool_call_id=tool_call_id,
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    payload.update(overrides)
    return ConfirmedImpactClaim(**payload)


def test_confirmed_impact_and_encounter_bind_exact_identity():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    encounters = _invocation(
        _run(search_current_encounters, "enc-h1", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    hazards = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    verified = _verify((_impact(),), impacts)
    assert verified.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert verified.rendering_posture is LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET
    assert LiveOpsLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value in verified.mandatory_limitations
    assert LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED.value in verified.mandatory_limitations
    assert LiveOpsLimitationCode.NO_SNAPSHOT_LIMITATION.value in verified.mandatory_limitations

    wrong_aircraft = _verify((_impact(aircraft_id="c"),), impacts)
    assert _codes(wrong_aircraft) == (
        LiveOpsClaimVerdict.REJECTED,
        (LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,),
    )
    wrong_version = _verify((_impact(source_version="v2"),), impacts)
    assert _codes(wrong_version)[0] is LiveOpsClaimVerdict.REJECTED
    assert LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value in _codes(wrong_version)[1]

    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    assert _verify((encounter,), encounters).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    mismatched = replace(encounter, projection_id="proj-other")
    assert _codes(_verify((mismatched,), encounters))[1] == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    flagged = replace(
        encounter,
        recorded_flags=(("inside_now", True),),
    )
    assert _codes(_verify((flagged,), encounters))[1] == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )

    both = _verify(
        (
            ConfirmedEncounterClaim(
                tool_call_id="enc-h2",
                tool_name="search_current_encounters",
                encounter_id="H2-A",
                aircraft_id="a",
                hazard_id="H2",
                hazard_source_version="v1",
                projection_id="proj-a",
            ),
            encounter,
        ),
        encounters,
        _invocation(
            _run(search_current_encounters, "enc-h2", hazard_id="H2"),
            {"hazard_id": "H2"},
        ),
    )
    assert both.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert both.contradiction_subjects == ()

    intersection = HazardRegionIntersectionClaim(
        tool_call_id="hz-ca",
        hazard_id="H1",
        source_version="v1",
        region_code="CA",
        region_name="California",
        region_query="California",
    )
    unevaluated = replace(intersection, hazard_id="uneval", source_version="v1")
    region = verify_live_ops_claims((intersection, unevaluated), _ledger(_completed(hazards)))
    assert _codes(region, 0)[0] is LiveOpsClaimVerdict.VERIFIED
    assert _codes(region, 1) == (
        LiveOpsClaimVerdict.REJECTED,
        (LiveOpsClaimRejectionCode.SPATIAL_STATUS_MISMATCH.value,),
    )
    h3 = replace(intersection, hazard_id="H3")
    assert _codes(_verify((h3,), hazards))[0] is LiveOpsClaimVerdict.VERIFIED


def test_counts_units_aggregates_and_zero_matrix():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    region_impacts = _invocation(
        _run(search_current_impacts, "imp-ca", region="California"),
        {"region": "California"},
    )
    exact = ScopedCountClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    assert _verify((exact,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    swapped = replace(exact, metric=LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT, value=2)
    assert _verify((swapped,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    subset_on_exact = replace(exact, mode=LiveOpsCountMode.CONFIRMED_SUBSET)
    assert _codes(_verify((subset_on_exact,), impacts))[1] == (
        LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH.value,
    )

    partial_impacts = ScopedCountClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.CONFIRMED_SUBSET,
        value=3,
        scope=LiveOpsExecutedScope(region="California"),
    )
    partial_aircraft = replace(
        partial_impacts,
        metric=LiveOpsCountMetric.UNIQUE_AIRCRAFT_COUNT,
        value=2,
    )
    partial = _verify((partial_impacts, partial_aircraft), region_impacts)
    assert partial.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(
        _verify((replace(partial_impacts, mode=LiveOpsCountMode.EXACT_RETURNED_SET),), region_impacts)
    )[1] == (LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH.value,)
    assert _codes(_verify((replace(partial_aircraft, value=3),), region_impacts))[1] == (
        LiveOpsClaimRejectionCode.COUNT_MISMATCH.value,
    )

    tampered = _mutate(impacts.raw_tool_result, lambda data: data.__setitem__("impact_count", 99))
    assert _codes(_verify((exact,), _invocation(tampered, {"hazard_ids": ["H1"]})))[1] == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    duplicated = _mutate(
        impacts.raw_tool_result,
        lambda data: data.__setitem__("unique_aircraft_ids", ["a", "a"]),
    )
    assert _codes(
        _verify((swapped,), _invocation(duplicated, {"hazard_ids": ["H1"]}))
    )[1] == (LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,)

    empty_tables = fixtures.tables()
    empty_region = _invocation(
        _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="hz-empty",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )
    assert _verify((zero,), empty_region).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    california = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    assert _codes(_verify((replace(zero, tool_call_id="hz-ca"),), california))[1] == (
        LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED.value,
    )
    unresolved = _invocation(
        _run(search_current_hazards, "hz-bad", region="NotAPlace"),
        {"region": "NotAPlace"},
    )
    assert _codes(
        _verify(
            (
                QueryResultZeroClaim(
                    tool_call_id="hz-bad",
                    tool_name="search_current_hazards",
                    metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                    scope=LiveOpsExecutedScope(region="NotAPlace"),
                ),
            ),
            unresolved,
        )
    )[1] == (LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED.value,)
    coverage = _verify(
        (
            CoverageLimitationClaim(
                tool_call_id="hz-bad",
                tool_name="search_current_hazards",
                code=LiveOpsLimitationCode.REGION_UNRESOLVED,
            ),
        ),
        unresolved,
    )
    assert coverage.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    nope = _invocation(
        _run(search_current_hazards, "hz-nope", hazard_ids=["NOPE"]),
        {"hazard_ids": ["NOPE"]},
    )
    discovery_zero = QueryResultZeroClaim(
        tool_call_id="hz-nope",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(hazard_ids=("NOPE",)),
    )
    assert _verify((discovery_zero,), nope).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    outside = _invocation(
        _run(get_hazard_operational_context, "hz-out", "outside"),
        {"hazard_id": "outside"},
    )
    scoped_zero = ScopedCountClaim(
        tool_call_id="hz-out",
        tool_name="get_hazard_operational_context",
        metric=LiveOpsCountMetric.HAZARD_CONTEXT_IMPACT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=0,
        scope=LiveOpsExecutedScope(hazard_id="outside"),
    )
    assert _verify((scoped_zero,), outside).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    stale = _invocation(
        _run(get_hazard_operational_context, "hz-stale", "stale"),
        {"hazard_id": "stale"},
    )
    stale_zero = replace(scoped_zero, tool_call_id="hz-stale", scope=LiveOpsExecutedScope(hazard_id="stale"))
    assert _codes(_verify((stale_zero,), stale)) == (
        LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE,
        (LiveOpsClaimRejectionCode.IMPACTS_NOT_EVALUATED.value,),
    )

    unknown = _invocation(
        _run(search_current_impacts, "imp-unknown"),
        {},
    )
    assert _codes(
        _verify(
            (
                QueryResultZeroClaim(
                    tool_call_id="imp-unknown",
                    tool_name="search_current_impacts",
                    metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
                    scope=LiveOpsExecutedScope(),
                ),
            ),
            unknown,
        )
    )[1] == (LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED.value,)

    network = _invocation(_run(get_observed_network_state, "net"), {})
    network_count = ScopedCountClaim(
        tool_call_id="net",
        tool_name="get_observed_network_state",
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(),
    )
    network_result = _verify((network_count,), network)
    assert network_result.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert (
        LiveOpsLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value
        in network_result.mandatory_limitations
    )
    empty_network = _invocation(
        _run(get_observed_network_state, "net0", tables=fixtures.tables()),
        {},
    )
    assert _verify(
        (replace(network_count, tool_call_id="net0", value=0),),
        empty_network,
    ).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED


def test_aircraft_hazard_risk_recommendation_and_callsign_claims():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    aircraft_b = _invocation(
        _run(get_aircraft_operational_context, "ac-b", "b"),
        {"aircraft_id": "b"},
    )
    observed = AircraftObservedStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        aircraft_id="b",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=True,
    )
    assert _verify((observed,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(observed, retained_not_current=False),), impacts))[1] == (
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,
    )
    current_b = CurrentnessClaim(
        tool_call_id="ac-b",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsCurrentnessPredicate.AIRCRAFT_IS_CURRENT,
        value=False,
        aircraft_id="b",
    )
    assert _verify((current_b,), aircraft_b).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(current_b, value=True),), aircraft_b))[1] == (
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,
    )

    hazard = _invocation(
        _run(get_hazard_operational_context, "hz-h1", "H1"),
        {"hazard_id": "H1"},
    )
    stale = _invocation(
        _run(get_hazard_operational_context, "hz-stale", "stale"),
        {"hazard_id": "stale"},
    )
    lifecycle = CurrentnessClaim(
        tool_call_id="hz-h1",
        tool_name="get_hazard_operational_context",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_LIFECYCLE_ACTIVE,
        value=True,
        hazard_id="H1",
    )
    queryable = CurrentnessClaim(
        tool_call_id="hz-h1",
        tool_name="get_hazard_operational_context",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    assert _verify((lifecycle, queryable), hazard).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    stale_life = replace(
        lifecycle,
        tool_call_id="hz-stale",
        value=False,
        hazard_id="stale",
    )
    stale_current = replace(
        queryable,
        tool_call_id="hz-stale",
        value=False,
        hazard_id="stale",
    )
    assert _verify((stale_life, stale_current), stale).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(stale_current, value=True),), stale))[1] == (
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,
    )
    selection = _invocation(
        _run(search_current_hazards, "hz-stale-sel", hazard_ids=["stale"]),
        {"hazard_ids": ["stale"]},
    )
    not_current = CurrentnessClaim(
        tool_call_id="hz-stale-sel",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT,
        value=True,
        hazard_id="stale",
        source_version="v1",
    )
    assert _verify((not_current,), selection).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(not_current, value=False),), selection))[1] == (
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,
    )

    h3 = _invocation(
        _run(get_hazard_operational_context, "hz-h3", "H3"),
        {"hazard_id": "H3"},
    )
    lineage = _verify((_impact(tool_call_id="hz-h3", tool_name="get_hazard_operational_context"),), h3)
    assert _codes(lineage)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.LINEAGE_MISMATCH.value in _codes(lineage)[1]

    risk = RiskStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    assert _verify((risk,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(risk, risk_level="LOW"),), impacts))[1] == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )
    absent_risk = RiskStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    assert _codes(_verify((absent_risk,), impacts))[1] == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )

    recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    assert _verify((recommendations,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(
        _verify((replace(recommendations, recommendation_ids=("rec-1",)),), impacts)
    )[1] == (LiveOpsClaimRejectionCode.SET_MISMATCH.value,)
    absent_recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    assert _codes(_verify((absent_recommendations,), impacts))[1] == (
        LiveOpsClaimRejectionCode.SET_MISMATCH.value,
    )

    alert = AlertRecordClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    assert _verify((alert,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    projection = AircraftProjectionStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        aircraft_id="a",
        projection_id="proj-a",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    assert _verify((projection,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(projection, projection_id="proj-missing"),), impacts))[1] == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )

    callsign = _invocation(
        _run(find_aircraft_by_callsign, "cs", "UAL123"),
        {"callsign": "UAL123"},
    )
    matches = CallsignMatchSetClaim(
        tool_call_id="cs",
        callsign="UAL123",
        aircraft_ids=("a", "c"),
    )
    assert _verify((matches,), callsign).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(matches, aircraft_ids=("a",)),), callsign))[1] == (
        LiveOpsClaimRejectionCode.SET_MISMATCH.value,
    )
    duplicated = _mutate(
        callsign.raw_tool_result,
        lambda data: data["matches"].append(copy.deepcopy(data["matches"][0])),
    )
    ambiguous = _invocation(duplicated, {"callsign": "UAL123"})
    assert _codes(_verify((matches,), ambiguous))[1] == (
        LiveOpsClaimRejectionCode.CALLSIGN_AMBIGUOUS.value,
    )
    none = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "NONE"),
        {"callsign": "NONE"},
    )
    assert _verify(
        (
            QueryResultZeroClaim(
                tool_call_id="cs0",
                tool_name="find_aircraft_by_callsign",
                metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
                scope=LiveOpsExecutedScope(callsign="NONE"),
            ),
        ),
        none,
    ).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    missing = _invocation(
        _run(get_aircraft_operational_context, "ac-miss", "missing-ac"),
        {"aircraft_id": "missing-ac"},
    )
    absent = ContextRootAbsentClaim(
        tool_call_id="ac-miss",
        tool_name="get_aircraft_operational_context",
        entity_id="missing-ac",
    )
    assert _verify((absent,), missing).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(_verify((replace(absent, entity_id="a"),), missing))[1] == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    airport_missing = _invocation(
        _run(
            get_airport_operational_context,
            "ap-miss",
            "ZZZZ",
            tables=fixtures.airport_tables(),
        ),
        {"airport_id": "ZZZZ"},
    )
    assert _verify(
        (
            ContextRootAbsentClaim(
                tool_call_id="ap-miss",
                tool_name="get_airport_operational_context",
                entity_id="ZZZZ",
            ),
        ),
        airport_missing,
    ).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    airport = _invocation(
        _run(
            get_airport_operational_context,
            "ap",
            "KSEA",
            tables=fixtures.airport_tables(),
        ),
        {"airport_id": "KSEA"},
    )
    assert _verify(
        (
            CurrentnessClaim(
                tool_call_id="ap",
                tool_name="get_airport_operational_context",
                predicate=LiveOpsCurrentnessPredicate.AIRPORT_IS_CURRENT,
                value=True,
                airport_id="KSEA",
            ),
        ),
        airport,
    ).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED


def test_truncation_uses_visibility_for_sets_and_raw_lists_for_counts():
    items = [fixtures.aircraft(f"a{index:02d}", callsign="UAL999") for index in range(26)]
    tables = fixtures.tables(
        aircraft=fixtures.FlexibleTable(query_items={"UAL999": items})
    )
    callsign = _invocation(
        _run(find_aircraft_by_callsign, "cs26", "UAL999", tables=tables),
        {"callsign": "UAL999"},
    )
    with pytest.raises(ContractValidationError, match="too_many_aircraft_ids"):
        CallsignMatchSetClaim(
            tool_call_id="cs26",
            callsign="UAL999",
            aircraft_ids=tuple(f"a{index:02d}" for index in range(26)),
        )
    counted = ScopedCountClaim(
        tool_call_id="cs26",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=26,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    truncation = CoverageLimitationClaim(
        tool_call_id="cs26",
        tool_name="find_aircraft_by_callsign",
        code=LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION,
    )
    visible = ModelVisibleTruncationClaim(
        tool_call_id="cs26",
        tool_name="find_aircraft_by_callsign",
        list_name="matches",
        total_count=26,
    )
    counted_result = _verify((counted, visible, truncation), callsign)
    assert counted_result.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    base = _run(search_current_impacts, "imp-wide", hazard_ids=["H1"])
    seed = copy.deepcopy(base.data["impacts"][0])
    cloned_rows = []
    for index in range(26):
        row = copy.deepcopy(seed)
        encounter_id = f"H1-{index:02d}"
        row["encounter_id"] = encounter_id
        row["encounter_context"]["encounter"]["encounter_id"] = encounter_id
        cloned_rows.append(row)
    widened = _mutate(
        base,
        lambda data: data.update(impacts=cloned_rows, impact_count=26),
    )
    wide = _invocation(widened, {"hazard_ids": ["H1"]})
    visible_parent = RecommendationSetClaim(
        tool_call_id="imp-wide",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-00",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    omitted_parent = replace(visible_parent, encounter_id="H1-25")
    parent_result = _verify((visible_parent, omitted_parent), wide)
    assert _codes(parent_result, 0)[0] is LiveOpsClaimVerdict.VERIFIED
    assert _codes(parent_result, 1) == (
        LiveOpsClaimVerdict.REJECTED,
        (LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,),
    )
    assert parent_result.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert parent_result.rendering_posture is LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET


def test_ledger_authorization_and_failed_batches_preserve_audit():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    hazards = _invocation(
        _run(search_current_hazards, "hz-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    claim = _impact()
    two_batches = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), _completed(hazards)),
    )
    assert two_batches.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert set(two_batches.audit_tool_call_ids) == {"imp-h1", "hz-h1"}

    rejected = LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.VALIDATION_REJECTED,
        validation_failure=LiveOpsValidationFailure(
            code=LiveOpsValidationCode.EMPTY_BATCH
        ),
        planned_tool_call_ids=("planned-rejected",),
    )
    mixed_validation = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), rejected),
    )
    assert mixed_validation.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert "planned-rejected" in mixed_validation.audit_tool_call_ids

    failed = LiveOpsDispatchResult(
        status=LiveOpsDispatchStatus.EXECUTION_FAILED,
        invocations=(
            _invocation(
                _run(search_current_hazards, "hz-fail", hazard_ids=["H1"]),
                {"hazard_ids": ["H1"]},
                projection=False,
            ),
        ),
        execution_error=LiveOpsRuntimeErrorCode.TOOL_EXECUTION_FAILED,
        failed_tool_name="search_current_hazards",
        failed_tool_call_id="hz-fail",
        planned_tool_call_ids=("imp-h1", "hz-fail"),
    )
    poisoned = verify_live_ops_claims((claim,), _ledger(_completed(impacts), failed))
    assert poisoned.outcome is LiveOpsVerificationOutcome.NOT_AUTHORIZED
    assert poisoned.rendering_posture is LiveOpsRenderingPosture.NOT_RENDERABLE
    assert poisoned.verified_claims == ()
    assert poisoned.assessments == ()
    assert poisoned.structural_codes == (
        LiveOpsClaimRejectionCode.RUN_EXECUTION_FAILED.value,
    )
    assert "hz-fail" in poisoned.audit_tool_call_ids
    assert poisoned.mandatory_limitations

    duplicate = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), _completed(impacts)),
    )
    assert duplicate.structural_codes == (
        LiveOpsClaimRejectionCode.DUPLICATE_TOOL_CALL_ID.value,
    )
    assert duplicate.verified_claims == ()

    missing = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), correlation_id=""),
    )
    assert missing.structural_codes == (
        LiveOpsClaimRejectionCode.MISSING_RUN_CORRELATION.value,
    )
    mismatched = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), correlation_id="corr-other-run"),
    )
    assert mismatched.structural_codes == (
        LiveOpsClaimRejectionCode.RUN_SCOPE_MISMATCH.value,
    )
    clock = verify_live_ops_claims(
        (claim,),
        _ledger(_completed(impacts), now_epoch=fixtures.NOW + 1),
    )
    assert clock.structural_codes == (
        LiveOpsClaimRejectionCode.BATCH_CLOCK_MISMATCH.value,
    )
    empty = verify_live_ops_claims((claim,), _ledger())
    assert empty.structural_codes == (LiveOpsClaimRejectionCode.EMPTY_LEDGER.value,)

    renamed = replace(impacts, tool_name="search_current_hazards")
    identity = verify_live_ops_claims((claim,), _ledger(_completed(renamed)))
    assert identity.structural_codes == (
        LiveOpsClaimRejectionCode.TOOL_RESULT_IDENTITY_MISMATCH.value,
    )
    current_missing = _run(get_aircraft_operational_context, "hist", "missing-ac")
    historical_result = replace(
        current_missing,
        temporal_scope=TemporalScope.HISTORICAL,
    )
    historical = LiveOpsToolInvocation(
        tool_name=historical_result.tool_name,
        tool_call_id=historical_result.tool_call_id,
        requested_arguments={"aircraft_id": "missing-ac"},
        executed_arguments={"aircraft_id": "missing-ac"},
        canonical_key=historical_result.tool_name,
        raw_tool_result=historical_result,
        model_projection=project_live_ops_tool_result(current_missing),
    )
    non_current = verify_live_ops_claims((claim,), _ledger(_completed(historical)))
    assert non_current.structural_codes == (
        LiveOpsClaimRejectionCode.NON_CURRENT_EVIDENCE.value,
    )

    too_many = verify_live_ops_claims(
        tuple(replace(claim, tool_call_id=f"imp-{index}") for index in range(33)),
        _ledger(_completed(impacts)),
    )
    assert too_many.structural_codes == (LiveOpsClaimRejectionCode.TOO_MANY_CLAIMS.value,)
    assert too_many.mandatory_limitations
    assert too_many.verified_claims == ()
    with pytest.raises(TypeError):
        verify_live_ops_claims((claim,), object())
    malformed = verify_live_ops_claims(("not-a-claim",), _ledger(_completed(impacts)))
    assert malformed.structural_codes == (LiveOpsClaimRejectionCode.MALFORMED_CLAIM.value,)
    assert malformed.assessments == ()
    assert LIVE_OPS_MAX_CLAIMS_PER_RESPONSE == 32


def test_contradictions_are_precise_and_leave_unrelated_claims_verified():
    current = _invocation(
        _run(search_current_hazards, "hz-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    drifted = _mutate(
        _run(get_hazard_operational_context, "hz-drift", "H1"),
        lambda data: data.__setitem__("source_version", "v2"),
    )
    drift = _invocation(drifted, {"hazard_id": "H1"})
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    hazard_claim = CurrentnessClaim(
        tool_call_id="hz-h1",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    observed = AircraftObservedStateClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )
    conflict_claim = CrossToolContradictionClaim(
        tool_call_id_a="hz-h1",
        tool_call_id_b="hz-drift",
        subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
        hazard_id="H1",
    )
    drifted_result = verify_live_ops_claims(
        (hazard_claim, observed, conflict_claim),
        _ledger(_completed(current), _completed(drift), _completed(aircraft)),
    )
    assert _codes(drifted_result, 0)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert _codes(drifted_result, 1)[0] is LiveOpsClaimVerdict.VERIFIED
    assert _codes(drifted_result, 2)[0] is LiveOpsClaimVerdict.VERIFIED
    assert drifted_result.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert drifted_result.contradiction_subjects

    region = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    stale = _invocation(
        _run(get_hazard_operational_context, "hz-stale", "stale"),
        {"hazard_id": "stale"},
    )
    retained = verify_live_ops_claims(
        (
            HazardRegionIntersectionClaim(
                tool_call_id="hz-ca",
                hazard_id="H1",
                source_version="v1",
                region_code="CA",
                region_name="California",
                region_query="California",
            ),
        ),
        _ledger(_completed(region), _completed(stale)),
    )
    assert retained.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert retained.contradiction_subjects == ()

    nope = _invocation(
        _run(search_current_hazards, "hz-nope", hazard_ids=["NOPE"]),
        {"hazard_ids": ["NOPE"]},
    )
    filtered = verify_live_ops_claims(
        (
            QueryResultZeroClaim(
                tool_call_id="hz-nope",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(hazard_ids=("NOPE",)),
            ),
            HazardRegionIntersectionClaim(
                tool_call_id="hz-ca",
                hazard_id="H1",
                source_version="v1",
                region_code="CA",
                region_name="California",
                region_query="California",
            ),
            CrossToolContradictionClaim(
                tool_call_id_a="hz-nope",
                tool_call_id_b="hz-ca",
                subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
                hazard_id="H1",
            ),
        ),
        _ledger(_completed(nope), _completed(region)),
    )
    assert _codes(filtered, 0)[0] is LiveOpsClaimVerdict.VERIFIED
    assert _codes(filtered, 1)[0] is LiveOpsClaimVerdict.VERIFIED
    assert _codes(filtered, 2)[1] == (
        LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,
    )

    original = _invocation(
        _run(search_current_encounters, "enc-a", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    changed = _reidentify(
        _mutate(
            original.raw_tool_result,
            lambda data: _retarget_encounter(data, "H1-A", "c"),
        ),
        "enc-b",
    )
    sibling = _invocation(changed, {"hazard_id": "H1"})
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-a",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    network = _invocation(_run(get_observed_network_state, "net"), {})
    network_count = ScopedCountClaim(
        tool_call_id="net",
        tool_name="get_observed_network_state",
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(),
    )
    lineage = verify_live_ops_claims(
        (encounter, network_count),
        _ledger(_completed(original), _completed(sibling), _completed(network)),
    )
    assert _codes(lineage, 0)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert _codes(lineage, 1)[0] is LiveOpsClaimVerdict.VERIFIED

    confirmed_and_unevaluated = _mutate(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        lambda data: data.setdefault("operationally_unevaluated", []).append(
            {"encounter_id": "H1-A", "aircraft_id": "a", "hazard_id": "H1"}
        ),
    )
    overlap = _verify(
        (_impact(),),
        _invocation(confirmed_and_unevaluated, {"hazard_ids": ["H1"]}),
    )
    assert _codes(overlap)[0] is LiveOpsClaimVerdict.CONTRADICTED


def test_unsuccessful_verification_keeps_diagnostics_without_emitting_facts():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    rejected = _verify((_impact(aircraft_id="missing-aircraft"),), impacts)
    assert rejected.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert rejected.rendering_posture is LiveOpsRenderingPosture.NO_VERIFIED_FACTS
    assert rejected.verified_claims == ()
    assert rejected.assessments[0].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    assert LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED.value in rejected.mandatory_limitations
    assert all("aircraft" not in code.casefold() for code in rejected.mandatory_limitations)

    unknown = _verify((_impact(tool_call_id="missing-tool"),), impacts)
    assert unknown.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert unknown.assessments[0].codes == (
        LiveOpsClaimRejectionCode.UNKNOWN_TOOL_CALL_ID.value,
    )

    with pytest.raises(ContractValidationError):
        LiveOpsTrustedEvidenceLedger(
            correlation_id=fixtures.CORRELATION_ID,
            now_epoch=True,
            dispatches=(),
        )


def _impact_context(data, encounter_id="H1-A"):
    return next(row for row in data["impacts"] if row["encounter_id"] == encounter_id)[
        "encounter_context"
    ]


def _encounter_context(data, encounter_id="H1-A"):
    return next(row for row in data["encounters"] if row["encounter_id"] == encounter_id)


def _set_limit(data, limit) -> None:
    retrieval = data.get("retrieval")
    if not isinstance(retrieval, list) or not retrieval or not isinstance(retrieval[0], dict):
        data["retrieval"] = [{"source": "test", "limit": limit, "limitations": []}]
        return
    retrieval[0]["limit"] = limit


def _callsign_view(invocation, *, keep, truncated, visible_count=None, omitted=None, total=None, rewrite_id=None):
    projection = invocation.model_projection
    data = copy.deepcopy(dict(projection.data))
    raw = invocation.raw_tool_result.data["matches"]
    visible = copy.deepcopy(list(raw[:keep]))
    if rewrite_id is not None:
        visible[-1]["aircraft"]["aircraft_id"] = rewrite_id
    data["matches"] = visible
    data["matches_total_count"] = len(raw) if total is None else total
    data["matches_visible_count"] = len(visible) if visible_count is None else visible_count
    data["matches_omitted_count"] = len(raw) - len(visible) if omitted is None else omitted
    data["matches_truncated"] = truncated
    return replace(invocation, model_projection=replace(projection, data=data))


def _hazard_intersection(tool_call_id="hz-ca"):
    return HazardRegionIntersectionClaim(
        tool_call_id=tool_call_id,
        hazard_id="H1",
        source_version="v1",
        region_code="CA",
        region_name="California",
        region_query="California",
    )


def _california_zero(tool_call_id):
    return QueryResultZeroClaim(
        tool_call_id=tool_call_id,
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )


def test_callsign_complete_set_requires_visible_membership_not_the_flag():
    items = [fixtures.aircraft(f"a{index:02d}", callsign="UAL999") for index in range(4)]
    tables = fixtures.tables(aircraft=fixtures.FlexibleTable(query_items={"UAL999": items}))
    callsign = _invocation(
        _run(find_aircraft_by_callsign, "cs4", "UAL999", tables=tables),
        {"callsign": "UAL999"},
    )
    claimed = tuple(f"a{index:02d}" for index in range(4))
    complete = CallsignMatchSetClaim(tool_call_id="cs4", callsign="UAL999", aircraft_ids=claimed)
    counted = ScopedCountClaim(
        tool_call_id="cs4",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=4,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    hidden = _callsign_view(callsign, keep=3, truncated=True)
    lied = _callsign_view(callsign, keep=3, truncated=False)
    falsified_count = _callsign_view(
        callsign,
        keep=4,
        truncated=False,
        visible_count=3,
        omitted=0,
        total=4,
    )
    swapped = _callsign_view(callsign, keep=4, truncated=False, rewrite_id="z09")
    assert _codes(_verify((complete,), hidden))[1] == (
        LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,
    )
    assert _codes(_verify((complete,), lied))[1] == (
        LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,
    )
    assert _codes(_verify((complete,), falsified_count))[1] == (
        LiveOpsClaimRejectionCode.TRUNCATION_MISMATCH.value,
    )
    assert _codes(_verify((complete,), swapped))[1] == (
        LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,
    )
    assert _verify((complete,), callsign).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _verify((counted,), lied).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    renamed = replace(
        callsign,
        model_projection=replace(callsign.model_projection, tool_name="search_current_hazards"),
    )
    rejected = _verify((complete,), renamed)
    assert rejected.outcome is LiveOpsVerificationOutcome.NOT_AUTHORIZED
    assert rejected.structural_codes == (
        LiveOpsClaimRejectionCode.TOOL_RESULT_IDENTITY_MISMATCH.value,
    )
    clock = replace(
        callsign,
        model_projection=replace(callsign.model_projection, as_of_epoch=fixtures.NOW + 1),
    )
    assert _verify((complete,), clock).structural_codes == (
        LiveOpsClaimRejectionCode.BATCH_CLOCK_MISMATCH.value,
    )


def test_encounter_source_versions_must_agree():
    encounters = _invocation(
        _run(search_current_encounters, "enc-h1", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    claim = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    assert _verify((claim,), encounters).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    def _versions(data, outer, nested):
        row = _encounter_context(data)
        row["source_version"] = outer
        row["encounter_context"]["encounter"]["hazard_source_version"] = nested

    outer_wins = _invocation(
        _mutate(encounters.raw_tool_result, lambda data: _versions(data, "v1", "v9")),
        {"hazard_id": "H1"},
    )
    nested_wins = _invocation(
        _mutate(encounters.raw_tool_result, lambda data: _versions(data, "v9", "v1")),
        {"hazard_id": "H1"},
    )
    assert _codes(_verify((claim,), outer_wins))[0] is LiveOpsClaimVerdict.REJECTED
    assert LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value in _codes(_verify((claim,), outer_wins))[1]
    assert LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value in _codes(_verify((claim,), nested_wins))[1]
    wrong = replace(claim, hazard_source_version="v9", projection_id=None)
    assert _codes(_verify((wrong,), encounters))[1] == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    missing = _invocation(
        _mutate(
            encounters.raw_tool_result,
            lambda data: _encounter_context(data)["encounter_context"]["encounter"].pop(
                "hazard_source_version"
            ),
        ),
        {"hazard_id": "H1"},
    )
    assert _codes(_verify((claim,), missing)) == (
        LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE,
        (LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,),
    )
    risk = RiskStateClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    blocked = _verify((risk,), outer_wins)
    assert blocked.outcome is not LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _codes(blocked)[0] is not LiveOpsClaimVerdict.VERIFIED


def test_nested_risk_and_alert_identity_must_match_parent():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    risk = RiskStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    alert = AlertRecordClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    assert _verify((risk, alert, recommendations), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert "winning_recommendation_id" not in recommendations.to_dict()

    def _retag(field, value):
        def mutate(data):
            _impact_context(data)["risk"][field] = value
        return _invocation(_mutate(impacts.raw_tool_result, mutate), {"hazard_ids": ["H1"]})

    for field in ("aircraft_id", "hazard_id"):
        assert _codes(_verify((risk,), _retag(field, "OTHER")))[1] == (
            LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
        )
    assert _codes(_verify((risk,), _retag("hazard_source_version", "v9")))[1] == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )

    def _retag_alert(data):
        alerts = _impact_context(data)["alerts"]
        target = next(item for item in alerts if item.get("alert_id") == "alert-risk")
        target["aircraft_id"] = "OTHER"

    alert_conflict = _invocation(_mutate(impacts.raw_tool_result, _retag_alert), {"hazard_ids": ["H1"]})
    assert _codes(_verify((alert,), alert_conflict))[1] == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )
    assert _verify((alert,), impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    def _drop_risk(data):
        context = _impact_context(data)
        context["risk"] = None
        context["risk_link"]["state"] = "MISSING"

    missing_risk = _invocation(_mutate(impacts.raw_tool_result, _drop_risk), {"hazard_ids": ["H1"]})
    low = replace(risk, risk_level="LOW", risk_score=1)
    assert _codes(_verify((low,), missing_risk))[1] == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )

    def _hide_recommendation(invocation):
        projection = invocation.model_projection
        data = copy.deepcopy(dict(projection.data))
        parent = next(row for row in data["impacts"] if row["encounter_id"] == "H1-A")
        parent["encounter_context"]["recommendations"] = parent["encounter_context"]["recommendations"][:1]
        return replace(invocation, model_projection=replace(projection, data=data))

    hidden = _hide_recommendation(impacts)
    assert _codes(_verify((recommendations,), hidden))[1] == (
        LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,
    )


def test_current_encounter_authorization_is_explicit():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    network = _invocation(_run(get_observed_network_state, "net"), {})
    network_count = ScopedCountClaim(
        tool_call_id="net",
        tool_name="get_observed_network_state",
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(),
    )
    impact = _impact()
    risk = RiskStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    alert = AlertRecordClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    claims = (impact, risk, recommendations, alert)
    assert _verify(claims, impacts).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    def _current(value):
        def mutate(data):
            _impact_context(data)["encounter_is_current"] = value
        return _invocation(_mutate(impacts.raw_tool_result, mutate), {"hazard_ids": ["H1"]})

    stale = _current(False)
    current_false = CurrentnessClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=False,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    held = verify_live_ops_claims(
        claims + (current_false, network_count),
        _ledger(_completed(stale), _completed(network)),
    )
    assert all(item.verdict is LiveOpsClaimVerdict.REJECTED for item in held.assessments[:4])
    assert all(
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value in item.codes
        for item in held.assessments[:4]
    )
    assert held.assessments[4].verdict is LiveOpsClaimVerdict.VERIFIED
    assert held.assessments[5].verdict is LiveOpsClaimVerdict.VERIFIED
    assert held.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED

    absent = _current(None)
    missing = _verify(claims, absent)
    assert all(item.verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE for item in missing.assessments)
    assert all(
        item.codes == (LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,)
        for item in missing.assessments
    )

    def _drop_current(data):
        _impact_context(data).pop("encounter_is_current")

    dropped = _invocation(_mutate(impacts.raw_tool_result, _drop_current), {"hazard_ids": ["H1"]})
    assert _codes(_verify((impact,), dropped))[0] is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE

    def _drift(data):
        _impact_context(data)["encounter"]["hazard_source_version"] = "v9"

    drifted = _invocation(_mutate(impacts.raw_tool_result, _drift), {"hazard_ids": ["H1"]})
    drifted_result = _verify(claims, drifted)
    assert all(item.verdict is LiveOpsClaimVerdict.REJECTED for item in drifted_result.assessments)
    assert all(
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value in item.codes
        for item in drifted_result.assessments
    )


def test_encounter_contradictions_do_not_require_projection_id():
    original = _invocation(
        _run(search_current_encounters, "enc-a", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-a",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    network = _invocation(_run(get_observed_network_state, "net"), {})
    network_count = ScopedCountClaim(
        tool_call_id="net",
        tool_name="get_observed_network_state",
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(),
    )

    def _sibling(mutator, tool_call_id):
        changed = _reidentify(_mutate(original.raw_tool_result, mutator), tool_call_id)
        return _invocation(changed, {"hazard_id": "H1"})

    def _aircraft_without_projection(data):
        row = _encounter_context(data)
        row["aircraft_id"] = "c"
        encounter_row = row["encounter_context"]["encounter"]
        encounter_row["aircraft_id"] = "c"
        encounter_row.pop("projection_id", None)

    def _version_without_projection(data):
        row = _encounter_context(data)
        row["hazard_id"] = "H9"
        row["source_version"] = "v9"
        encounter_row = row["encounter_context"]["encounter"]
        encounter_row["hazard_id"] = "H9"
        encounter_row["hazard_source_version"] = "v9"
        encounter_row.pop("projection_id", None)

    def _other_projection(data):
        _encounter_context(data)["encounter_context"]["encounter"]["projection_id"] = "proj-b"

    def _drop_projection(data):
        _encounter_context(data)["encounter_context"]["encounter"].pop("projection_id", None)

    def _other_encounter(data):
        row = _encounter_context(data)
        row["encounter_id"] = "H1-Z"
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"

    aircraft_conflict = verify_live_ops_claims(
        (encounter, network_count),
        _ledger(
            _completed(original),
            _completed(_sibling(_aircraft_without_projection, "enc-b")),
            _completed(network),
        ),
    )
    assert _codes(aircraft_conflict, 0)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert _codes(aircraft_conflict, 1)[0] is LiveOpsClaimVerdict.VERIFIED
    assert any(
        subject.subject is LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE
        for subject in aircraft_conflict.contradiction_subjects
    )

    version_conflict = verify_live_ops_claims(
        (encounter,),
        _ledger(_completed(original), _completed(_sibling(_version_without_projection, "enc-c"))),
    )
    assert _codes(version_conflict)[0] is LiveOpsClaimVerdict.CONTRADICTED

    projection_conflict = verify_live_ops_claims(
        (encounter,),
        _ledger(_completed(original), _completed(_sibling(_other_projection, "enc-d"))),
    )
    assert _codes(projection_conflict)[0] is LiveOpsClaimVerdict.CONTRADICTED

    omitted_projection = verify_live_ops_claims(
        (encounter,),
        _ledger(_completed(original), _completed(_sibling(_drop_projection, "enc-e"))),
    )
    assert omitted_projection.contradiction_subjects == ()
    assert _codes(omitted_projection)[0] is LiveOpsClaimVerdict.VERIFIED

    other_encounter = verify_live_ops_claims(
        (encounter,),
        _ledger(_completed(original), _completed(_sibling(_other_encounter, "enc-f"))),
    )
    assert other_encounter.contradiction_subjects == ()
    assert other_encounter.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert other_encounter.verified_claims == ()
    assert other_encounter.assessments[0].codes == (
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value,
    )


def test_same_scope_zero_conflicts_with_positive_evidence():
    empty_tables = fixtures.tables()
    empty = _invocation(
        _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    populated = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    zero = _california_zero("hz-empty")
    intersection = _hazard_intersection("hz-ca")
    hazard_data = populated.raw_tool_result.data
    hazard_count = len(hazard_data["hazards"])
    unevaluated = bool(
        hazard_data.get("operationally_unevaluated") or hazard_data.get("geospatial_unevaluated")
    )
    exact = populated.raw_tool_result.status.value == "SUCCESS" and not unevaluated
    positive_count = ScopedCountClaim(
        tool_call_id="hz-ca",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        mode=(
            LiveOpsCountMode.EXACT_RETURNED_SET
            if exact
            else LiveOpsCountMode.CONFIRMED_SUBSET
        ),
        value=hazard_count,
        scope=LiveOpsExecutedScope(region="California"),
    )
    pair = verify_live_ops_claims(
        (zero, intersection),
        _ledger(_completed(empty), _completed(populated)),
    )
    assert pair.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert pair.verified_claims == ()
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in pair.assessments)
    assert LiveOpsLimitationCode.NO_SNAPSHOT_LIMITATION.value in pair.mandatory_limitations

    counts = verify_live_ops_claims(
        (zero, positive_count),
        _ledger(_completed(empty), _completed(populated)),
    )
    assert counts.outcome is not LiveOpsVerificationOutcome.ALL_VERIFIED
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in counts.assessments)

    empty_again = _invocation(
        _reidentify(
            _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
            "hz-empty-2",
        ),
        {"region": "California"},
    )
    zeros = verify_live_ops_claims(
        (zero, _california_zero("hz-empty-2")),
        _ledger(_completed(empty), _completed(empty_again)),
    )
    assert zeros.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    populated_again = _invocation(
        _reidentify(_run(search_current_hazards, "hz-ca", region="California"), "hz-ca-2"),
        {"region": "California"},
    )
    positives = verify_live_ops_claims(
        (intersection, replace(intersection, tool_call_id="hz-ca-2")),
        _ledger(_completed(populated), _completed(populated_again)),
    )
    assert positives.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    nope = _invocation(
        _run(search_current_hazards, "hz-nope", hazard_ids=["NOPE"]),
        {"hazard_ids": ["NOPE"]},
    )
    filtered = verify_live_ops_claims(
        (
            QueryResultZeroClaim(
                tool_call_id="hz-nope",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(hazard_ids=("NOPE",)),
            ),
            intersection,
        ),
        _ledger(_completed(nope), _completed(populated)),
    )
    assert filtered.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    nevada = _invocation(
        _run(search_current_hazards, "hz-nv", region="Nevada", tables=empty_tables),
        {"region": "Nevada"},
    )
    regions = verify_live_ops_claims(
        (
            QueryResultZeroClaim(
                tool_call_id="hz-nv",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="Nevada"),
            ),
            intersection,
        ),
        _ledger(_completed(nevada), _completed(populated)),
    )
    assert regions.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    observed = AircraftObservedStateClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )
    mixed = verify_live_ops_claims(
        (zero, intersection, observed),
        _ledger(_completed(empty), _completed(populated), _completed(aircraft)),
    )
    assert mixed.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert _codes(mixed, 0)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert _codes(mixed, 1)[0] is LiveOpsClaimVerdict.CONTRADICTED
    assert _codes(mixed, 2)[0] is LiveOpsClaimVerdict.VERIFIED


def test_scope_disagreement_contradicts_dependent_operational_claims():
    empty_tables = fixtures.tables()
    callsign_rows = [
        fixtures.aircraft("a", callsign="UAL999"),
        fixtures.aircraft("c", callsign="UAL999"),
    ]
    callsign_tables = fixtures.tables(
        aircraft=fixtures.FlexibleTable(query_items={"UAL999": callsign_rows})
    )
    callsign_zero = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    callsign_positive = _invocation(
        _run(find_aircraft_by_callsign, "cs", "UAL999", tables=callsign_tables),
        {"callsign": "UAL999"},
    )
    zero_claim = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    match_count = ScopedCountClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    match_set = CallsignMatchSetClaim(
        tool_call_id="cs",
        callsign="UAL999",
        aircraft_ids=("a", "c"),
    )
    callsign_limitation = CoverageLimitationClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        code=LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION,
    )
    callsigns = verify_live_ops_claims(
        (zero_claim, match_count, match_set, callsign_limitation),
        _ledger(_completed(callsign_zero), _completed(callsign_positive)),
    )
    assert [item.verdict for item in callsigns.assessments[:3]] == [
        LiveOpsClaimVerdict.CONTRADICTED,
        LiveOpsClaimVerdict.CONTRADICTED,
        LiveOpsClaimVerdict.CONTRADICTED,
    ]
    assert callsigns.assessments[3].verdict is LiveOpsClaimVerdict.VERIFIED
    assert callsigns.verified_claims == (callsign_limitation,)
    assert all(getattr(claim, "aircraft_ids", ()) == () for claim in callsigns.verified_claims)
    assert LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION.value in callsigns.mandatory_limitations
    assert LiveOpsLimitationCode.FRESHNESS_NOT_ESTABLISHED.value in callsigns.mandatory_limitations

    hazard_zero = _invocation(
        _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    hazard_positive = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    hazard_current = CurrentnessClaim(
        tool_call_id="hz-ca",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    independent_observed = AircraftObservedStateClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )
    hazards = verify_live_ops_claims(
        (_california_zero("hz-empty"), _hazard_intersection("hz-ca"), hazard_current, independent_observed),
        _ledger(_completed(hazard_zero), _completed(hazard_positive), _completed(aircraft)),
    )
    assert [item.verdict for item in hazards.assessments] == [
        LiveOpsClaimVerdict.CONTRADICTED,
        LiveOpsClaimVerdict.CONTRADICTED,
        LiveOpsClaimVerdict.CONTRADICTED,
        LiveOpsClaimVerdict.VERIFIED,
    ]
    assert hazards.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert hazards.rendering_posture is LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    assert hazards.verified_claims == (independent_observed,)
    assert LiveOpsLimitationCode.NO_SNAPSHOT_LIMITATION.value in hazards.mandatory_limitations

    impact_zero = _invocation(
        _run(search_current_impacts, "imp-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    impact_positive = _invocation(
        _run(search_current_impacts, "imp-ca", region="California"),
        {"region": "California"},
    )
    impact_identity = ConfirmedImpactClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    impact_risk = RiskStateClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    impact_recommendations = RecommendationSetClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    impact_alert = AlertRecordClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    impact_observed = AircraftObservedStateClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )
    impact_projection = AircraftProjectionStateClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        aircraft_id="a",
        projection_id="proj-a",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    impact_current = CurrentnessClaim(
        tool_call_id="imp-ca",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    impact_zero_claim = QueryResultZeroClaim(
        tool_call_id="imp-empty",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )
    impacts = verify_live_ops_claims(
        (
            impact_zero_claim,
            impact_identity,
            impact_risk,
            impact_recommendations,
            impact_alert,
            impact_observed,
            impact_projection,
            impact_current,
            independent_observed,
        ),
        _ledger(
            _completed(impact_zero),
            _completed(impact_positive),
            _completed(aircraft),
        ),
    )
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in impacts.assessments[:8])
    assert impacts.assessments[8].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impacts.verified_claims == (independent_observed,)
    assert impacts.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert impact_identity not in impacts.verified_claims
    assert impact_risk not in impacts.verified_claims

    stale = _invocation(
        _run(get_hazard_operational_context, "hz-stale", "stale"),
        {"hazard_id": "stale"},
    )
    retained = verify_live_ops_claims(
        (_hazard_intersection("hz-ca"),),
        _ledger(_completed(hazard_positive), _completed(stale)),
    )
    assert retained.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert retained.contradiction_subjects == ()


def test_non_authoritative_zero_does_not_contradict_positive_evidence():
    empty_tables = fixtures.tables()
    missing_hazard = _invocation(
        _run(search_current_encounters, "enc-empty", hazard_id="H1", tables=empty_tables),
        {"hazard_id": "H1"},
    )
    positive = _invocation(
        _run(search_current_encounters, "enc-h1", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    assert missing_hazard.raw_tool_result.data["rejected_hazard_selections"][0]["reason"] == "MISSING"
    refused_zero = QueryResultZeroClaim(
        tool_call_id="enc-empty",
        tool_name="search_current_encounters",
        metric=LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        scope=LiveOpsExecutedScope(hazard_id="H1"),
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    risk = RiskStateClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    combined = verify_live_ops_claims(
        (refused_zero, encounter, risk),
        _ledger(_completed(missing_hazard), _completed(positive)),
    )
    assert _codes(combined, 0) == (
        LiveOpsClaimVerdict.REJECTED,
        (LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED.value,),
    )
    assert combined.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert combined.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED
    assert encounter in combined.verified_claims
    assert combined.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    alone = _verify((encounter, risk), positive)
    assert alone.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED


def test_disagreeing_exact_positive_counts_conflict():
    original_result = _run(search_current_impacts, "imp-h1", hazard_ids=["H1"])
    original_count = len(original_result.data["impacts"])

    def drop_one(data):
        data["impacts"] = data["impacts"][:-1]
        data["impact_count"] = len(data["impacts"])

    reduced_result = _reidentify(_mutate(original_result, drop_one), "imp-h1-b")
    reduced_count = len(reduced_result.data["impacts"])
    assert original_result.status is ToolResultStatus.SUCCESS
    assert reduced_result.status is ToolResultStatus.SUCCESS
    assert reduced_count > 0
    assert reduced_count != original_count
    original = _invocation(original_result, {"hazard_ids": ["H1"]})
    reduced = _invocation(reduced_result, {"hazard_ids": ["H1"]})
    full = ScopedCountClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=original_count,
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    smaller = replace(full, tool_call_id="imp-h1-b", value=reduced_count)
    assert _verify((full,), original).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert _verify((smaller,), reduced).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    disagreed = verify_live_ops_claims(
        (full, smaller),
        _ledger(_completed(original), _completed(reduced)),
    )
    assert disagreed.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert disagreed.verified_claims == ()
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in disagreed.assessments)
    assert LiveOpsLimitationCode.NO_SNAPSHOT_LIMITATION.value in disagreed.mandatory_limitations


def test_network_inventory_counts_distinct_identities():
    network = _run(get_observed_network_state, "net")
    distinct = _invocation(network, {})
    aircraft_ids = network.data["current_aircraft_ids"]
    hazard_ids = network.data["current_hazard_ids"]
    aircraft_claim = ScopedCountClaim(
        tool_call_id="net",
        tool_name="get_observed_network_state",
        metric=LiveOpsCountMetric.NETWORK_AIRCRAFT_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=len(aircraft_ids),
        scope=LiveOpsExecutedScope(),
    )
    verified = _verify((aircraft_claim,), distinct)
    assert verified.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert (
        LiveOpsLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value
        in verified.mandatory_limitations
    )
    duplicated = _invocation(
        _mutate(
            network,
            lambda data: data.update(current_aircraft_ids=["a", "a"], current_aircraft_count=2),
        ),
        {},
    )
    inflated = replace(aircraft_claim, value=2)
    assert _codes(_verify((inflated,), duplicated))[1] == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    inconsistent = _invocation(
        _mutate(
            network,
            lambda data: data.update(current_aircraft_ids=["a", "a"], current_aircraft_count=1),
        ),
        {},
    )
    assert _codes(_verify((replace(aircraft_claim, value=1),), inconsistent))[1] == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    empty = _invocation(
        _mutate(
            network,
            lambda data: data.update(current_aircraft_ids=[], current_aircraft_count=0),
        ),
        {},
    )
    empty_count = _verify((replace(aircraft_claim, value=0),), empty)
    assert empty_count.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert (
        LiveOpsLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value
        in empty_count.mandatory_limitations
    )
    hazard_claim = replace(
        aircraft_claim,
        metric=LiveOpsCountMetric.NETWORK_HAZARD_COUNT,
        value=len(hazard_ids),
    )
    assert hazard_claim.metric is not aircraft_claim.metric
    assert _verify((hazard_claim,), distinct).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    if len(hazard_ids) != len(aircraft_ids):
        assert _codes(_verify((replace(aircraft_claim, value=len(hazard_ids)),), distinct))[1] == (
            LiveOpsClaimRejectionCode.COUNT_MISMATCH.value,
        )


def test_zero_authorization_rejects_unrecognized_retrieval_limits():
    empty = _run(
        search_current_hazards,
        "hz-empty",
        region="California",
        tables=fixtures.tables(),
    )
    zero = _california_zero("hz-empty")
    assert _verify((zero,), _invocation(empty, {"region": "California"})).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )
    absent = _mutate(empty, lambda data: data.pop("retrieval", None))
    assert _verify((zero,), _invocation(absent, {"region": "California"})).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )
    for limit in (10, 1.5, True, "10", 0):
        limited = _invocation(
            _mutate(empty, lambda data, value=limit: _set_limit(data, value)),
            {"region": "California"},
        )
        assert _codes(_verify((zero,), limited))[1] == (
            LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
        )
    malformed = _invocation(
        _mutate(empty, lambda data: data.__setitem__("retrieval", "unlimited")),
        {"region": "California"},
    )
    assert _codes(_verify((zero,), malformed))[1] == (
        LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
    )


def test_overlap_status_requires_exact_recorded_value():
    encounters = _run(search_current_encounters, "enc-h1", hazard_id="H1")
    recorded = _encounter_context(encounters.data)["encounter_context"]["encounter"].get(
        "geometry_overlap_status"
    )
    claim = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
        overlap_statuses=(("geometry_overlap_status", "RECORDED-OVERLAP"),),
    )
    labeled = _invocation(
        _mutate(
            encounters,
            lambda data: _encounter_context(data)["encounter_context"]["encounter"].__setitem__(
                "geometry_overlap_status",
                "RECORDED-OVERLAP",
            ),
        ),
        {"hazard_id": "H1"},
    )
    assert _verify((claim,), labeled).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    wrong = replace(claim, overlap_statuses=(("geometry_overlap_status", "NOT-RECORDED"),))
    assert _codes(_verify((wrong,), labeled))[1] == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )
    if recorded != "RECORDED-OVERLAP":
        assert _codes(_verify((claim,), _invocation(encounters, {"hazard_id": "H1"})))[1] == (
            LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
        )


def _retarget_encounter(data, encounter_id, aircraft_id) -> None:
    for row in data["encounters"]:
        if row.get("encounter_id") != encounter_id:
            continue
        row["aircraft_id"] = aircraft_id
        encounter = row["encounter_context"]["encounter"]
        encounter["aircraft_id"] = aircraft_id


def _observed_aircraft(tool_call_id="ac-a"):
    return AircraftObservedStateClaim(
        tool_call_id=tool_call_id,
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )


def _hazard_count(tool_call_id, value, hazard_ids):
    return ScopedCountClaim(
        tool_call_id=tool_call_id,
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=value,
        scope=LiveOpsExecutedScope(hazard_ids=hazard_ids),
    )


def _assert_scope_diagnostics(assessment, *opposing_ids):
    assert assessment.verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in assessment.codes
    assert assessment.conflicting_tool_call_ids == tuple(sorted(set(opposing_ids)))
    assert set(assessment.tool_call_ids).isdisjoint(assessment.conflicting_tool_call_ids)
    assert assessment.scope_disagreement_metrics
    assert assessment.normalized_scope
    rendered = " ".join(
        part for pair in assessment.normalized_scope for part in pair
    ).casefold()
    assert "database" not in rendered
    assert "corrupt" not in rendered


def test_equivalent_region_text_contradicts_dependent_claims():
    empty_tables = fixtures.tables()
    positive = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    lower = _invocation(
        _run(search_current_hazards, "hz-low", region="california", tables=empty_tables),
        {"region": "california"},
    )
    spaced = _invocation(
        _run(
            search_current_hazards,
            "hz-space",
            region="  CaLiFoRnIa  ",
            tables=empty_tables,
        ),
        {"region": "  CaLiFoRnIa  "},
    )
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    lower_zero = QueryResultZeroClaim(
        tool_call_id="hz-low",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(region="california"),
    )
    spaced_zero = QueryResultZeroClaim(
        tool_call_id="hz-space",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(region="california"),
    )
    intersection = _hazard_intersection("hz-ca")
    observed = _observed_aircraft()
    mixed = verify_live_ops_claims(
        (lower_zero, intersection, observed),
        _ledger(_completed(lower), _completed(positive), _completed(aircraft)),
    )
    assert mixed.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert mixed.rendering_posture is LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
    assert mixed.verified_claims == (observed,)
    assert mixed.contradiction_subjects == ()
    _assert_scope_diagnostics(mixed.assessments[0], "hz-ca")
    _assert_scope_diagnostics(mixed.assessments[1], "hz-low")
    assert mixed.assessments[0].normalized_scope == (("region", "california"),)
    assert mixed.assessments[2].conflicting_tool_call_ids == ()
    assert lower.executed_arguments["region"] == "california"

    whitespace = verify_live_ops_claims(
        (spaced_zero, intersection),
        _ledger(_completed(spaced), _completed(positive)),
    )
    assert whitespace.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert whitespace.verified_claims == ()
    _assert_scope_diagnostics(whitespace.assessments[0], "hz-ca")
    assert spaced.executed_arguments["region"] == "  CaLiFoRnIa  "

    empty_again = _invocation(
        _run(search_current_hazards, "hz-low-2", region="California", tables=empty_tables),
        {"region": "California"},
    )
    york = _invocation(
        _run(search_current_hazards, "hz-ny", region="New  York", tables=empty_tables),
        {"region": "New  York"},
    )
    york_again = _invocation(
        _run(search_current_hazards, "hz-ny-2", region="new york", tables=empty_tables),
        {"region": "new york"},
    )
    consistent = verify_live_ops_claims(
        (
            lower_zero,
            QueryResultZeroClaim(
                tool_call_id="hz-low-2",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="California"),
            ),
            QueryResultZeroClaim(
                tool_call_id="hz-ny",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="New  York"),
            ),
            QueryResultZeroClaim(
                tool_call_id="hz-ny-2",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="new york"),
            ),
        ),
        _ledger(
            _completed(lower),
            _completed(empty_again),
            _completed(york),
            _completed(york_again),
        ),
    )
    assert consistent.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert len(consistent.verified_claims) == 4
    assert all(item.conflicting_tool_call_ids == () for item in consistent.assessments)

    alias = _invocation(
        _run(search_current_hazards, "hz-alias", region="CA", tables=empty_tables),
        {"region": "CA"},
    )
    texas = _invocation(
        _run(search_current_hazards, "hz-tx", region="Texas", tables=empty_tables),
        {"region": "Texas"},
    )
    distinct = verify_live_ops_claims(
        (
            QueryResultZeroClaim(
                tool_call_id="hz-alias",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="CA"),
            ),
            intersection,
            QueryResultZeroClaim(
                tool_call_id="hz-tx",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(region="Texas"),
            ),
        ),
        _ledger(_completed(alias), _completed(positive), _completed(texas)),
    )
    assert distinct.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert len(distinct.verified_claims) == 3
    assert all(item.conflicting_tool_call_ids == () for item in distinct.assessments)


def test_hazard_id_order_uses_one_canonical_selector():
    empty_tables = fixtures.tables()
    positive_result = _run(search_current_hazards, "hz-ord", hazard_ids=["H1", "airmet"])
    zero_result = _run(
        search_current_hazards,
        "hz-ord0",
        hazard_ids=["airmet", "H1"],
        tables=empty_tables,
    )
    positive = _invocation(positive_result, {"hazard_ids": ["H1", "airmet"]})
    zero = _invocation(zero_result, {"hazard_ids": ["airmet", "H1"]})
    reversed_positive = _invocation(
        _reidentify(positive_result, "hz-rev"),
        {"hazard_ids": ["airmet", "H1"]},
    )
    reversed_zero = _invocation(
        _reidentify(zero_result, "hz-rev0"),
        {"hazard_ids": ["H1", "airmet"]},
    )
    current = CurrentnessClaim(
        tool_call_id="hz-ord",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    zero_claim = QueryResultZeroClaim(
        tool_call_id="hz-ord0",
        tool_name="search_current_hazards",
        metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsExecutedScope(hazard_ids=("H1", "airmet")),
    )
    forward = verify_live_ops_claims(
        (zero_claim, current),
        _ledger(_completed(zero), _completed(positive)),
    )
    assert forward.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert forward.verified_claims == ()
    _assert_scope_diagnostics(forward.assessments[0], "hz-ord")
    _assert_scope_diagnostics(forward.assessments[1], "hz-ord0")
    assert ("hazard_ids", "H1") in forward.assessments[0].normalized_scope
    assert ("hazard_ids", "airmet") in forward.assessments[0].normalized_scope
    assert zero.executed_arguments["hazard_ids"] == ["airmet", "H1"]

    backward = verify_live_ops_claims(
        (
            replace(zero_claim, tool_call_id="hz-rev0"),
            replace(current, tool_call_id="hz-rev"),
        ),
        _ledger(_completed(reversed_zero), _completed(reversed_positive)),
    )
    assert backward.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert backward.verified_claims == ()
    assert {item.tool_call_ids[0] for item in backward.assessments} == {"hz-rev0", "hz-rev"}

    ordered_count = _hazard_count(
        "hz-ord",
        len(positive_result.data["hazards"]),
        ("H1", "airmet"),
    )
    unsorted_positive = _invocation(positive_result, {"hazard_ids": ["airmet", "H1"]})
    echo = verify_live_ops_claims((ordered_count,), _ledger(_completed(unsorted_positive)))
    assert echo.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert echo.verified_claims == (ordered_count,)

    def reverse_rows(data):
        data["hazards"] = list(reversed(data["hazards"]))

    reordered = _invocation(
        _reidentify(_mutate(positive_result, reverse_rows), "hz-rows"),
        {"hazard_ids": ["H1", "airmet"]},
    )
    same_membership = verify_live_ops_claims(
        (ordered_count, replace(ordered_count, tool_call_id="hz-rows")),
        _ledger(_completed(positive), _completed(reordered)),
    )
    assert same_membership.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert len(same_membership.verified_claims) == 2

    h1_only = _invocation(
        _run(search_current_hazards, "hz-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    different_members = verify_live_ops_claims(
        (
            current,
            replace(current, tool_call_id="hz-h1"),
            _hazard_count("hz-h1", 1, ("H1",)),
        ),
        _ledger(_completed(positive), _completed(h1_only)),
    )
    assert different_members.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert all(item.conflicting_tool_call_ids == () for item in different_members.assessments)

    duplicated = _invocation(
        _reidentify(zero_result, "hz-dup"),
        {"hazard_ids": ["H1", "H1"]},
    )
    malformed = _invocation(
        _reidentify(zero_result, "hz-bad"),
        {"hazard_ids": ["H1", 1]},
    )
    refused = verify_live_ops_claims(
        (
            QueryResultZeroClaim(
                tool_call_id="hz-dup",
                tool_name="search_current_hazards",
                metric=LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
                scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
            ),
            replace(current, tool_call_id="hz-h1"),
        ),
        _ledger(_completed(duplicated), _completed(h1_only), _completed(malformed)),
    )
    assert refused.assessments[0].verdict is LiveOpsClaimVerdict.REJECTED
    assert refused.assessments[0].codes == (LiveOpsClaimRejectionCode.SCOPE_MISMATCH.value,)
    assert refused.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert refused.verified_claims == (replace(current, tool_call_id="hz-h1"),)
    assert all(item.conflicting_tool_call_ids == () for item in refused.assessments)


def test_reordered_selector_does_not_authorize_a_refused_zero():
    empty_tables = fixtures.tables()
    refused = _invocation(
        _run(
            search_current_encounters,
            "enc-empty",
            hazard_ids=["NOPE", "H1"],
            tables=empty_tables,
        ),
        {"hazard_ids": ["NOPE", "H1"]},
    )
    positive = _invocation(
        _run(search_current_encounters, "enc-h1", hazard_ids=["H1", "NOPE"]),
        {"hazard_ids": ["H1", "NOPE"]},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="enc-empty",
        tool_name="search_current_encounters",
        metric=LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        scope=LiveOpsExecutedScope(hazard_ids=("H1", "NOPE")),
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    result = verify_live_ops_claims(
        (zero, encounter),
        _ledger(_completed(refused), _completed(positive)),
    )
    assert result.assessments[0].verdict is LiveOpsClaimVerdict.REJECTED
    assert result.assessments[0].codes == (
        LiveOpsClaimRejectionCode.ZERO_NOT_AUTHORIZED.value,
    )
    assert result.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert result.verified_claims == (encounter,)
    assert result.assessments[1].conflicting_tool_call_ids == ()


def test_equal_counts_with_different_membership_contradict():
    rows = [
        fixtures.aircraft("a", callsign="UAL999"),
        fixtures.aircraft("c", callsign="UAL999"),
    ]
    tables = fixtures.tables(aircraft=fixtures.FlexibleTable(query_items={"UAL999": rows}))
    callsign_result = _run(find_aircraft_by_callsign, "cs", "UAL999", tables=tables)

    def rename_first(data):
        data["matches"][0]["aircraft"]["aircraft_id"] = "b"

    def reorder_matches(data):
        data["matches"] = list(reversed(data["matches"]))

    def as_multiset(data, aircraft_ids):
        template = data["matches"][0]
        data["matches"] = []
        for aircraft_id in aircraft_ids:
            row = copy.deepcopy(template)
            row["aircraft"]["aircraft_id"] = aircraft_id
            data["matches"].append(row)
        data["match_count"] = len(data["matches"])

    original = _invocation(callsign_result, {"callsign": "UAL999"})
    renamed = _invocation(
        _reidentify(_mutate(callsign_result, rename_first), "cs-b"),
        {"callsign": "UAL999"},
    )
    reordered = _invocation(
        _reidentify(_mutate(callsign_result, reorder_matches), "cs-order"),
        {"callsign": "UAL999"},
    )
    left_multi = _invocation(
        _reidentify(
            _mutate(callsign_result, lambda data: as_multiset(data, ["a", "a", "b"])),
            "cs-multi-a",
        ),
        {"callsign": "UAL999"},
    )
    right_multi = _invocation(
        _reidentify(
            _mutate(callsign_result, lambda data: as_multiset(data, ["a", "b", "b"])),
            "cs-multi-b",
        ),
        {"callsign": "UAL999"},
    )
    set_a = CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL999", aircraft_ids=("a", "c"))
    set_b = CallsignMatchSetClaim(tool_call_id="cs-b", callsign="UAL999", aircraft_ids=("b", "c"))
    set_order = CallsignMatchSetClaim(
        tool_call_id="cs-order",
        callsign="UAL999",
        aircraft_ids=("a", "c"),
    )
    diverged = verify_live_ops_claims(
        (set_a, set_b),
        _ledger(_completed(original), _completed(renamed)),
    )
    assert diverged.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert diverged.verified_claims == ()
    _assert_scope_diagnostics(diverged.assessments[0], "cs-b")
    _assert_scope_diagnostics(diverged.assessments[1], "cs")

    same_order = verify_live_ops_claims(
        (set_a, set_order),
        _ledger(_completed(original), _completed(reordered)),
    )
    assert same_order.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert same_order.verified_claims == (set_a, set_order)

    multi_count = ScopedCountClaim(
        tool_call_id="cs-multi-a",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=3,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    repeated = verify_live_ops_claims(
        (multi_count, replace(multi_count, tool_call_id="cs-multi-b")),
        _ledger(_completed(left_multi), _completed(right_multi)),
    )
    assert repeated.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert repeated.verified_claims == ()

    hazard_result = _run(search_current_hazards, "hz", hazard_ids=["H1"])

    def bump_version(data):
        data["hazards"][0]["hazard"]["source_version"] = "v2"

    def clear_version(data):
        data["hazards"][0]["hazard"]["source_version"] = ""

    hazard = _invocation(hazard_result, {"hazard_ids": ["H1"]})
    other_version = _invocation(
        _reidentify(_mutate(hazard_result, bump_version), "hz-v2"),
        {"hazard_ids": ["H1"]},
    )
    missing_version = _invocation(
        _reidentify(_mutate(hazard_result, clear_version), "hz-missing"),
        {"hazard_ids": ["H1"]},
    )
    hazard_v1 = CurrentnessClaim(
        tool_call_id="hz",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    hazard_v2 = replace(hazard_v1, tool_call_id="hz-v2", source_version="v2")
    versions = verify_live_ops_claims(
        (hazard_v1, hazard_v2, _hazard_count("hz", 1, ("H1",)), _hazard_count("hz-v2", 1, ("H1",))),
        _ledger(_completed(hazard), _completed(other_version)),
    )
    assert versions.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert versions.verified_claims == ()
    readable_count = _hazard_count("hz", 1, ("H1",))
    unreadable_count = _hazard_count("hz-missing", 1, ("H1",))
    unreadable = verify_live_ops_claims(
        (readable_count, unreadable_count),
        _ledger(_completed(hazard), _completed(missing_version)),
    )
    assert unreadable.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert unreadable.verified_claims == (readable_count,)
    assert unreadable.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert unreadable.assessments[0].codes == ()
    assert unreadable.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert unreadable.assessments[1].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )

    impact_result = _run(search_current_impacts, "imp", hazard_ids=["H1"])

    def change_impact_identity(data):
        data["impacts"][1]["encounter_id"] = "H1-Z"
        data["impacts"][1]["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"

    def reverse_impacts(data):
        data["impacts"] = list(reversed(data["impacts"]))

    def break_aggregate(data):
        data["impact_count"] = data["impact_count"] + 5

    impact = _invocation(impact_result, {"hazard_ids": ["H1"]})
    impact_lineage = _invocation(
        _reidentify(_mutate(impact_result, change_impact_identity), "imp-b"),
        {"hazard_ids": ["H1"]},
    )
    impact_order = _invocation(
        _reidentify(_mutate(impact_result, reverse_impacts), "imp-order"),
        {"hazard_ids": ["H1"]},
    )
    impact_bad_count = _invocation(
        _reidentify(_mutate(impact_result, break_aggregate), "imp-bad"),
        {"hazard_ids": ["H1"]},
    )
    impact_count = ScopedCountClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=len(impact_result.data["impacts"]),
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    impact_claim = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    impact_conflict = verify_live_ops_claims(
        (impact_count, replace(impact_count, tool_call_id="imp-b"), impact_claim, replace(impact_claim, tool_call_id="imp-b")),
        _ledger(_completed(impact), _completed(impact_lineage)),
    )
    assert impact_conflict.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert impact_conflict.verified_claims == ()
    assert impact_claim not in impact_conflict.verified_claims
    impact_same = verify_live_ops_claims(
        (impact_count, replace(impact_count, tool_call_id="imp-order"), impact_claim),
        _ledger(_completed(impact), _completed(impact_order)),
    )
    assert impact_same.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    aggregate = verify_live_ops_claims(
        (impact_count, replace(impact_count, tool_call_id="imp-bad")),
        _ledger(_completed(impact), _completed(impact_bad_count)),
    )
    assert aggregate.verified_claims == (impact_count,)
    assert aggregate.assessments[0].conflicting_tool_call_ids == ()
    assert aggregate.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )

    encounter_result = _run(search_current_encounters, "enc", hazard_id="H1")

    def change_encounter_lineage(data):
        data["encounters"][1]["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

    encounter = _invocation(encounter_result, {"hazard_id": "H1"})
    encounter_other = _invocation(
        _reidentify(_mutate(encounter_result, change_encounter_lineage), "enc-b"),
        {"hazard_id": "H1"},
    )
    encounter_count = ScopedCountClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        metric=LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=len(encounter_result.data["encounters"]),
        scope=LiveOpsExecutedScope(hazard_id="H1"),
    )
    encounter_claim = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    other_encounter_count = replace(encounter_count, tool_call_id="enc-b")
    other_encounter_claim = replace(encounter_claim, tool_call_id="enc-b")
    encounter_conflict = verify_live_ops_claims(
        (
            encounter_count,
            other_encounter_count,
            encounter_claim,
            other_encounter_claim,
        ),
        _ledger(_completed(encounter), _completed(encounter_other)),
    )
    # Nested v9 against parent v1 makes that row unreadable. The sibling H1-A
    # row stays readable, so its claims remain verified and the unreadable row
    # does not create a complete-set disagreement.
    assert encounter_conflict.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    assert encounter_conflict.verified_claims == (
        encounter_count,
        encounter_claim,
        other_encounter_claim,
    )
    assert encounter_conflict.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert encounter_conflict.assessments[1].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in encounter_conflict.assessments
    )

    other_scope = _invocation(
        _run(search_current_encounters, "enc-ids", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    other_claim = replace(encounter_claim, tool_call_id="enc-ids")
    separate_scopes = verify_live_ops_claims(
        (encounter_claim, other_claim),
        _ledger(_completed(encounter), _completed(other_scope)),
    )
    assert separate_scopes.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert separate_scopes.verified_claims == (encounter_claim, other_claim)

    def make_partial(data):
        data["operationally_unevaluated"] = [
            {"encounter_id": "pending", "limitations": []}
        ]
        extra = copy.deepcopy(data["hazards"][0])
        extra["hazard"] = dict(extra["hazard"])
        extra["hazard"]["hazard_id"] = "H-EXTRA"
        data["hazards"].append(extra)
        data["match_count"] = len(data["hazards"])

    partial = _invocation(
        replace(
            _reidentify(_mutate(hazard_result, make_partial), "hz-partial"),
            status=ToolResultStatus.PARTIAL,
        ),
        {"hazard_ids": ["H1"]},
    )
    partial_current = replace(hazard_v1, tool_call_id="hz-partial")
    partial_pair = verify_live_ops_claims(
        (hazard_v1, partial_current),
        _ledger(_completed(hazard), _completed(partial)),
    )
    assert partial_pair.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert partial_pair.verified_claims == (hazard_v1, partial_current)
    assert all(item.conflicting_tool_call_ids == () for item in partial_pair.assessments)


def test_truncation_cannot_certify_disputed_cardinality():
    rows = [
        fixtures.aircraft("a", callsign="UAL999"),
        fixtures.aircraft("c", callsign="UAL999"),
    ]
    tables = fixtures.tables(aircraft=fixtures.FlexibleTable(query_items={"UAL999": rows}))
    positive = _callsign_view(
        _invocation(
            _run(find_aircraft_by_callsign, "cs", "UAL999", tables=tables),
            {"callsign": "UAL999"},
        ),
        keep=1,
        truncated=True,
    )
    zero = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    truncation = ModelVisibleTruncationClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        list_name="matches",
        total_count=2,
    )
    coverage = CoverageLimitationClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        code=LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION,
    )
    zero_claim = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    match_count = ScopedCountClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=2,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    disputed = verify_live_ops_claims(
        (zero_claim, truncation, coverage, match_count),
        _ledger(_completed(zero), _completed(positive)),
    )
    assert disputed.assessments[1].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert truncation not in disputed.verified_claims
    assert match_count not in disputed.verified_claims
    assert disputed.verified_claims == (coverage,)
    assert LiveOpsLimitationCode.CALLSIGN_CASE_LIMITATION.value in disputed.mandatory_limitations
    _assert_scope_diagnostics(disputed.assessments[1], "cs0")

    alone = _verify((truncation,), positive)
    assert alone.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert alone.verified_claims == (truncation,)
    assert alone.assessments[0].conflicting_tool_call_ids == ()


def test_scope_disagreement_keeps_cited_and_opposing_tool_calls():
    rows = [
        fixtures.aircraft("a", callsign="UAL999"),
        fixtures.aircraft("c", callsign="UAL999"),
    ]
    tables = fixtures.tables(aircraft=fixtures.FlexibleTable(query_items={"UAL999": rows}))
    positive = _invocation(
        _run(find_aircraft_by_callsign, "cs", "UAL999", tables=tables),
        {"callsign": "UAL999"},
    )
    extra = _invocation(
        _reidentify(_run(find_aircraft_by_callsign, "cs-2", "UAL999", tables=tables), "cs-2"),
        {"callsign": "UAL999"},
    )

    def rename_first(data):
        data["matches"][0]["aircraft"]["aircraft_id"] = "b"

    renamed = _invocation(
        _reidentify(_mutate(positive.raw_tool_result, rename_first), "cs-b"),
        {"callsign": "UAL999"},
    )
    zero = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    zero_claim = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    match_set = CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL999", aircraft_ids=("a", "c"))
    observed = _observed_aircraft()
    partial = verify_live_ops_claims(
        (zero_claim, match_set, observed),
        _ledger(
            _completed(zero),
            _completed(positive),
            _completed(extra),
            _completed(renamed),
            _completed(aircraft),
        ),
    )
    assert partial.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED
    _assert_scope_diagnostics(partial.assessments[0], "cs", "cs-2", "cs-b")
    assert partial.assessments[0].tool_call_ids == ("cs0",)
    assert len(partial.assessments[0].conflicting_tool_call_ids) == len(
        set(partial.assessments[0].conflicting_tool_call_ids)
    )
    assert partial.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED
    assert partial.assessments[2].conflicting_tool_call_ids == ()
    assert partial.assessments[2].scope_disagreement_metrics == ()
    none = verify_live_ops_claims(
        (zero_claim, match_set),
        _ledger(_completed(zero), _completed(positive), _completed(extra), _completed(renamed)),
    )
    assert none.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert none.verified_claims == ()
    _assert_scope_diagnostics(none.assessments[0], "cs", "cs-2", "cs-b")

    drifted = _invocation(
        _mutate(
            _run(get_hazard_operational_context, "hz-drift", "H1"),
            lambda data: data.__setitem__("source_version", "v2"),
        ),
        {"hazard_id": "H1"},
    )
    current = _invocation(
        _run(search_current_hazards, "hz-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    lineage = verify_live_ops_claims(
        (
            CurrentnessClaim(
                tool_call_id="hz-h1",
                tool_name="search_current_hazards",
                predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
                value=True,
                hazard_id="H1",
                source_version="v1",
            ),
        ),
        _ledger(_completed(current), _completed(drifted)),
    )
    assert lineage.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert lineage.assessments[0].codes == (
        LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT.value,
    )
    assert lineage.assessments[0].conflicting_tool_call_ids == ()
    assert lineage.contradiction_subjects
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in lineage.assessments[0].codes


def _batch(claims, *invocations):
    return verify_live_ops_claims(
        tuple(claims),
        _ledger(*(_completed(item) for item in invocations)),
    )


def _posture_for(outcome):
    return {
        LiveOpsVerificationOutcome.ALL_VERIFIED: LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET,
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED: (
            LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET
        ),
        LiveOpsVerificationOutcome.NONE_VERIFIED: LiveOpsRenderingPosture.NO_VERIFIED_FACTS,
        LiveOpsVerificationOutcome.NOT_AUTHORIZED: LiveOpsRenderingPosture.NOT_RENDERABLE,
    }[outcome]


def _expect(result, verdicts, verified, outcome):
    assert [item.verdict for item in result.assessments] == list(verdicts)
    assert result.verified_claims == tuple(verified)
    assert result.outcome is outcome
    assert result.rendering_posture is _posture_for(outcome)
    rendered = str(result.to_dict()).casefold()
    assert "database is corrupt" not in rendered
    assert "database corrupted" not in rendered


def _count(tool_call_id, tool_name, metric, value, **scope):
    return ScopedCountClaim(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        metric=metric,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=value,
        scope=LiveOpsExecutedScope(**scope),
    )


def _nested_record(row):
    context = row.get("encounter_context")
    if isinstance(context, dict) and isinstance(context.get("encounter"), dict):
        return context["encounter"]
    encounter = row.get("encounter")
    if isinstance(encounter, dict):
        return encounter
    raise AssertionError("encounter identity is not mapped")


def _callsign_tables(*aircraft_ids):
    rows = [fixtures.aircraft(aircraft_id, callsign="UAL999") for aircraft_id in aircraft_ids]
    return fixtures.tables(aircraft=fixtures.FlexibleTable(query_items={"UAL999": rows}))


def _callsign_invocation(tool_call_id, *aircraft_ids, mutate=None):
    result = _run(
        find_aircraft_by_callsign,
        tool_call_id,
        "UAL999",
        tables=_callsign_tables(*aircraft_ids),
    )
    if mutate is not None:
        result = _mutate(result, mutate)
    return _invocation(result, {"callsign": "UAL999"})


def test_corrupt_hazard_aggregate_cannot_conceal_conflicting_rows():
    empty_tables = fixtures.tables()
    zero_inv = _invocation(
        _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    positive = _run(search_current_hazards, "hz-ca", region="California")
    bad = _invocation(
        _reidentify(
            _mutate(positive, lambda data: data.__setitem__("match_count", 99)),
            "hz-bad",
        ),
        {"region": "California"},
    )
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    zero = _california_zero("hz-empty")
    intersection = _hazard_intersection("hz-bad")
    observed = _observed_aircraft()
    hidden = _batch((zero, intersection, observed), zero_inv, bad, aircraft)
    _expect(
        hidden,
        (
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.VERIFIED,
        ),
        (observed,),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    _assert_scope_diagnostics(hidden.assessments[0], "hz-bad")
    _assert_scope_diagnostics(hidden.assessments[1], "hz-empty")
    assert hidden.assessments[2].conflicting_tool_call_ids == ()

    scoped = _run(search_current_hazards, "hz", hazard_ids=["H1"])
    original = _invocation(scoped, {"hazard_ids": ["H1"]})

    def retag(data):
        data["hazards"][0]["hazard"]["hazard_id"] = "H9"
        data["match_count"] = 99

    other = _invocation(
        _reidentify(_mutate(scoped, retag), "hz-other"),
        {"hazard_ids": ["H1"]},
    )
    good_count = _hazard_count("hz", len(scoped.data["hazards"]), ("H1",))
    bad_count = _hazard_count("hz-other", len(scoped.data["hazards"]), ("H1",))
    good_current = CurrentnessClaim(
        tool_call_id="hz",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    other_current = replace(good_current, tool_call_id="hz-other", hazard_id="H9")
    diverged = _batch(
        (good_count, bad_count, good_current, other_current),
        original,
        other,
    )
    _expect(
        diverged,
        (
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
        ),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    assert diverged.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    _assert_scope_diagnostics(diverged.assessments[0], "hz-other")

    same_bad = _invocation(
        _reidentify(
            _mutate(scoped, lambda data: data.__setitem__("match_count", 99)),
            "hz-same",
        ),
        {"hazard_ids": ["H1"]},
    )
    same_count = _hazard_count("hz-same", len(scoped.data["hazards"]), ("H1",))
    same_current = replace(good_current, tool_call_id="hz-same")
    unchanged = _batch((good_count, same_count, good_current, same_current), original, same_bad)
    _expect(
        unchanged,
        (
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.VERIFIED,
        ),
        (good_count, good_current, same_current),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert unchanged.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    assert all(item.conflicting_tool_call_ids == () for item in unchanged.assessments)

    def blank_identity(data):
        for row in data["hazards"]:
            row["hazard"]["hazard_id"] = " "
            row["hazard"]["source_version"] = " "

    malformed = _invocation(
        _reidentify(_mutate(positive, blank_identity), "hz-malformed"),
        {"region": "California"},
    )
    malformed_intersection = _hazard_intersection("hz-malformed")
    malformed_count = _count(
        "hz-malformed",
        "search_current_hazards",
        LiveOpsCountMetric.HAZARD_QUERY_MATCH_COUNT,
        len(positive.data["hazards"]),
        region="California",
    )
    quiet = _batch((zero, malformed_intersection, malformed_count), zero_inv, malformed)
    _expect(
        quiet,
        (
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.REJECTED,
        ),
        (zero,),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert quiet.assessments[0].conflicting_tool_call_ids == ()
    assert quiet.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert quiet.assessments[2].codes == (
        LiveOpsClaimRejectionCode.COUNT_MODE_MISMATCH.value,
    )

    def blank_scoped(data):
        for row in data["hazards"]:
            row["hazard"]["hazard_id"] = " "
            row["hazard"]["source_version"] = " "

    scoped_blank = _invocation(
        _reidentify(_mutate(scoped, blank_scoped), "hz-blank"),
        {"hazard_ids": ["H1"]},
    )
    blank_count = _hazard_count("hz-blank", len(scoped.data["hazards"]), ("H1",))
    blank_current = replace(good_current, tool_call_id="hz-blank")
    unreadable = _batch((blank_count, blank_current), scoped_blank)
    _expect(
        unreadable,
        (LiveOpsClaimVerdict.REJECTED, LiveOpsClaimVerdict.REJECTED),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    assert unreadable.assessments[0].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    assert unreadable.assessments[1].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )


def test_corrupt_impact_aggregate_cannot_conceal_conflicting_rows():
    empty_tables = fixtures.tables()
    zero_inv = _invocation(
        _run(search_current_impacts, "imp-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    positive = _run(search_current_impacts, "imp-ca", region="California")
    bad = _invocation(
        _reidentify(
            _mutate(positive, lambda data: data.__setitem__("impact_count", 99)),
            "imp-bad",
        ),
        {"region": "California"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="imp-empty",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )
    impact = ConfirmedImpactClaim(
        tool_call_id="imp-bad",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    hidden = _batch((zero, impact), zero_inv, bad)
    _expect(
        hidden,
        (LiveOpsClaimVerdict.CONTRADICTED, LiveOpsClaimVerdict.CONTRADICTED),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    _assert_scope_diagnostics(hidden.assessments[0], "imp-bad")
    _assert_scope_diagnostics(hidden.assessments[1], "imp-empty")

    scoped = _run(search_current_impacts, "imp", hazard_ids=["H1"])
    original = _invocation(scoped, {"hazard_ids": ["H1"]})
    value = len(scoped.data["impacts"])

    def retag(data):
        data["impacts"][0]["encounter_id"] = "H1-Z"
        data["impacts"][0]["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"
        data["impact_count"] = 99

    other = _invocation(
        _reidentify(_mutate(scoped, retag), "imp-other"),
        {"hazard_ids": ["H1"]},
    )
    good_count = _count(
        "imp",
        "search_current_impacts",
        LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        value,
        hazard_ids=("H1",),
    )
    bad_count = replace(good_count, tool_call_id="imp-other")
    good_impact = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    other_impact = replace(good_impact, tool_call_id="imp-other", encounter_id="H1-Z")
    diverged = _batch((good_count, bad_count, good_impact, other_impact), original, other)
    _expect(
        diverged,
        (
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
        ),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    assert diverged.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )

    same_bad = _invocation(
        _reidentify(
            _mutate(scoped, lambda data: data.__setitem__("impact_count", 99)),
            "imp-same",
        ),
        {"hazard_ids": ["H1"]},
    )
    same = _batch(
        (good_count, replace(good_count, tool_call_id="imp-same"), good_impact),
        original,
        same_bad,
    )
    _expect(
        same,
        (
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.VERIFIED,
        ),
        (good_count, good_impact),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert same.assessments[0].conflicting_tool_call_ids == ()
    assert same.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )

    def malformed_relationship(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-B"
        data["impacts"] = [row]
        data["impact_count"] = 1

    broken = _invocation(
        _reidentify(_mutate(positive, malformed_relationship), "imp-broken"),
        {"region": "California"},
    )
    broken_impact = replace(impact, tool_call_id="imp-broken")
    isolated = _batch((zero, broken_impact), zero_inv, broken)
    _expect(
        isolated,
        (LiveOpsClaimVerdict.VERIFIED, LiveOpsClaimVerdict.REJECTED),
        (zero,),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert isolated.assessments[0].conflicting_tool_call_ids == ()
    assert isolated.assessments[1].codes == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )


def test_corrupt_encounter_aggregate_cannot_conceal_conflicting_rows():
    scoped = _run(search_current_encounters, "enc", hazard_id="H1")
    original = _invocation(scoped, {"hazard_id": "H1"})
    value = len(scoped.data["encounters"])

    def retag(data):
        data["encounters"][1]["encounter_id"] = "H1-Z"
        data["encounters"][1]["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"
        data["encounter_count"] = 99

    other = _invocation(
        _reidentify(_mutate(scoped, retag), "enc-other"),
        {"hazard_id": "H1"},
    )
    good_count = _count(
        "enc",
        "search_current_encounters",
        LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        value,
        hazard_id="H1",
    )
    bad_count = replace(good_count, tool_call_id="enc-other")
    good_encounter = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    other_encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-other",
        tool_name="search_current_encounters",
        encounter_id="H1-Z",
        aircraft_id="b",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-b",
    )
    diverged = _batch(
        (good_count, bad_count, good_encounter, other_encounter),
        original,
        other,
    )
    _expect(
        diverged,
        (
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
        ),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    assert diverged.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    _assert_scope_diagnostics(diverged.assessments[0], "enc-other")

    same_bad = _invocation(
        _reidentify(
            _mutate(scoped, lambda data: data.__setitem__("encounter_count", 99)),
            "enc-same",
        ),
        {"hazard_id": "H1"},
    )
    same = _batch(
        (good_count, replace(good_count, tool_call_id="enc-same"), good_encounter),
        original,
        same_bad,
    )
    _expect(
        same,
        (
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.REJECTED,
            LiveOpsClaimVerdict.VERIFIED,
        ),
        (good_count, good_encounter),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert all(item.conflicting_tool_call_ids == () for item in same.assessments)

    def drop_lineage(data):
        for row in data["encounters"]:
            row["encounter_context"]["encounter"].pop("hazard_source_version")

    missing = _invocation(
        _reidentify(_mutate(scoped, drop_lineage), "enc-missing"),
        {"hazard_id": "H1"},
    )
    missing_claim = replace(good_encounter, tool_call_id="enc-missing")
    guessed = _verify((missing_claim,), missing)
    assert guessed.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert guessed.verified_claims == ()
    assert guessed.assessments[0].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert guessed.assessments[0].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    unrelated = _invocation(
        _run(search_current_encounters, "enc-h2", hazard_id="H2"),
        {"hazard_id": "H2"},
    )
    unrelated_claim = ConfirmedEncounterClaim(
        tool_call_id="enc-h2",
        tool_name="search_current_encounters",
        encounter_id="H2-A",
        aircraft_id="a",
        hazard_id="H2",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    preserved = _batch((missing_claim, unrelated_claim), missing, unrelated)
    assert preserved.assessments[0].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert preserved.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert preserved.verified_claims == (unrelated_claim,)


def test_corrupt_callsign_aggregate_cannot_conceal_conflicting_rows():
    left = _callsign_invocation("cs-ac", "a", "c")
    right = _callsign_invocation(
        "cs-bc",
        "a",
        "c",
        mutate=lambda data: (
            data["matches"][0]["aircraft"].__setitem__("aircraft_id", "b"),
            data.__setitem__("match_count", 99),
        ),
    )
    set_left = CallsignMatchSetClaim(
        tool_call_id="cs-ac",
        callsign="UAL999",
        aircraft_ids=("a", "c"),
    )
    set_right = CallsignMatchSetClaim(
        tool_call_id="cs-bc",
        callsign="UAL999",
        aircraft_ids=("b", "c"),
    )
    good_count = _count(
        "cs-ac",
        "find_aircraft_by_callsign",
        LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        2,
        callsign="UAL999",
    )
    bad_count = replace(good_count, tool_call_id="cs-bc")
    diverged = _batch((set_left, set_right, good_count, bad_count), left, right)
    _expect(
        diverged,
        (
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.CONTRADICTED,
            LiveOpsClaimVerdict.REJECTED,
        ),
        (),
        LiveOpsVerificationOutcome.NONE_VERIFIED,
    )
    assert diverged.assessments[3].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    _assert_scope_diagnostics(diverged.assessments[0], "cs-bc")

    inflated = _callsign_invocation(
        "cs-inflated",
        "a",
        "c",
        mutate=lambda data: data.__setitem__("match_count", 99),
    )
    inflated_set = replace(set_left, tool_call_id="cs-inflated")
    inflated_count = replace(good_count, tool_call_id="cs-inflated")
    equal = _batch((set_left, inflated_set, good_count, inflated_count), left, inflated)
    _expect(
        equal,
        (
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.VERIFIED,
            LiveOpsClaimVerdict.REJECTED,
        ),
        (set_left, inflated_set, good_count),
        LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
    )
    assert all(item.conflicting_tool_call_ids == () for item in equal.assessments)

    repeated = _callsign_invocation(
        "cs-repeat",
        "a",
        "c",
        mutate=lambda data: data["matches"][1]["aircraft"].__setitem__("aircraft_id", "a"),
    )
    repeated_set = CallsignMatchSetClaim(
        tool_call_id="cs-repeat",
        callsign="UAL999",
        aircraft_ids=("a",),
    )
    repeat_result = _batch((set_left, repeated_set), left, repeated)
    assert repeat_result.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert repeat_result.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert repeat_result.assessments[1].codes == (
        LiveOpsClaimRejectionCode.CALLSIGN_AMBIGUOUS.value,
    )
    assert repeat_result.verified_claims == ()

    zero_inv = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    malformed = _callsign_invocation(
        "cs-blank",
        "a",
        "c",
        mutate=lambda data: [
            row["aircraft"].__setitem__("aircraft_id", "") for row in data["matches"]
        ],
    )
    zero = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    blank_set = CallsignMatchSetClaim(
        tool_call_id="cs-blank",
        callsign="UAL999",
        aircraft_ids=("a", "c"),
    )
    quiet = _batch((zero, blank_set), zero_inv, malformed)
    assert quiet.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert quiet.assessments[0].conflicting_tool_call_ids == ()
    assert quiet.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert quiet.verified_claims == (zero,)
    assert quiet.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED


def test_retrieval_cap_cannot_authorize_a_complete_set():
    complete = _callsign_invocation("cs", "a", "c")
    claimed = CallsignMatchSetClaim(
        tool_call_id="cs",
        callsign="UAL999",
        aircraft_ids=("a", "c"),
    )
    counted = _count(
        "cs",
        "find_aircraft_by_callsign",
        LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        2,
        callsign="UAL999",
    )
    assert _verify((claimed, counted), complete).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    capped = _callsign_invocation(
        "cs-cap",
        "a",
        "c",
        mutate=lambda data: _set_limit(data, 2),
    )
    capped_set = replace(claimed, tool_call_id="cs-cap")
    capped_count = replace(counted, tool_call_id="cs-cap")
    limited = _verify((capped_set, capped_count), capped)
    assert limited.assessments[0].codes == (
        LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
    )
    assert limited.assessments[1].codes == (
        LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
    )
    assert limited.verified_claims == ()
    assert limited.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert all(item.conflicting_tool_call_ids == () for item in limited.assessments)

    left = _callsign_invocation(
        "cs-a",
        "a",
        "c",
        mutate=lambda data: (
            data.__setitem__("matches", [data["matches"][0]]),
            data.__setitem__("match_count", 1),
            _set_limit(data, 2),
        ),
    )
    right = _callsign_invocation(
        "cs-c",
        "a",
        "c",
        mutate=lambda data: (
            data.__setitem__("matches", [data["matches"][1]]),
            data.__setitem__("match_count", 1),
            _set_limit(data, 2),
        ),
    )
    subset_a = CallsignMatchSetClaim(tool_call_id="cs-a", callsign="UAL999", aircraft_ids=("a",))
    subset_c = CallsignMatchSetClaim(tool_call_id="cs-c", callsign="UAL999", aircraft_ids=("c",))
    subsets = _batch((subset_a, subset_c), left, right)
    assert [item.verdict for item in subsets.assessments] == [
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
    ]
    assert all(
        item.codes == (LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,)
        for item in subsets.assessments
    )
    assert all(item.conflicting_tool_call_ids == () for item in subsets.assessments)
    assert subsets.verified_claims == ()

    zero_inv = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    challenged = _batch((zero, capped_set), zero_inv, capped)
    assert challenged.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert challenged.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert challenged.verified_claims == ()
    assert challenged.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    _assert_scope_diagnostics(challenged.assessments[0], "cs-cap")

    for limit in (0, 2, 2.0, True, False, "2"):
        bounded = _callsign_invocation(
            "cs-limit",
            "a",
            "c",
            mutate=lambda data, limit=limit: _set_limit(data, limit),
        )
        rejected = _verify((replace(claimed, tool_call_id="cs-limit"),), bounded)
        assert rejected.assessments[0].codes == (
            LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
        )

    def clear_limit(data):
        data.pop("retrieval", None)

    def null_retrieval(data):
        data["retrieval"] = None

    def null_limit(data):
        _set_limit(data, None)

    for mutate in (clear_limit, null_retrieval, null_limit):
        opened = _callsign_invocation("cs-open", "a", "c", mutate=mutate)
        assert _verify((replace(claimed, tool_call_id="cs-open"),), opened).outcome is (
            LiveOpsVerificationOutcome.ALL_VERIFIED
        )

    truncated = _callsign_view(complete, keep=1, truncated=True)
    truncation = ModelVisibleTruncationClaim(
        tool_call_id="cs",
        tool_name="find_aircraft_by_callsign",
        list_name="matches",
        total_count=2,
    )
    visible = _batch((claimed, counted, truncation), truncated)
    assert visible.assessments[0].codes == (
        LiveOpsClaimRejectionCode.MODEL_VISIBLE_SET_TRUNCATED.value,
    )
    assert visible.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert visible.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED
    assert counted in visible.verified_claims
    assert claimed not in visible.verified_claims

    impact_source = _run(search_current_impacts, "imp-cap", hazard_ids=["H1"])

    def empty_impacts(data):
        data["impacts"] = []
        data["impact_count"] = 0
        if "unique_aircraft_ids" in data:
            data["unique_aircraft_ids"] = []

    impact_zero = _invocation(
        _reidentify(_mutate(impact_source, empty_impacts), "imp-empty"),
        {"hazard_ids": ["H1"]},
    )
    impact_capped = _invocation(
        _reidentify(_mutate(impact_source, lambda data: _set_limit(data, 1)), "imp-cap"),
        {"hazard_ids": ["H1"]},
    )
    impact_claim = ConfirmedImpactClaim(
        tool_call_id="imp-cap",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    assert _verify((impact_claim,), impact_capped).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    impact_zero_claim = QueryResultZeroClaim(
        tool_call_id="imp-empty",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    impact_count = _count(
        "imp-cap",
        "search_current_impacts",
        LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        len(impact_capped.raw_tool_result.data["impacts"]),
        hazard_ids=("H1",),
    )
    capped_conflict = _batch(
        (impact_zero_claim, impact_claim, impact_count),
        impact_zero,
        impact_capped,
    )
    assert capped_conflict.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert capped_conflict.assessments[1].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert capped_conflict.assessments[2].codes == (
        LiveOpsClaimRejectionCode.RETRIEVAL_LIMIT_PRESENT.value,
    )
    assert capped_conflict.verified_claims == ()


def test_nested_encounter_identity_must_agree_with_parent():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    impact = _impact()
    sibling = _impact(encounter_id="H1-B", aircraft_id="b")
    risk = RiskStateClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    recommendations = RecommendationSetClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    alert = AlertRecordClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    current = CurrentnessClaim(
        tool_call_id="imp-h1",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    agreed = _verify((impact, sibling, risk, recommendations, alert, current), impacts)
    assert agreed.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert agreed.rendering_posture is LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET

    def retarget(data, field, value):
        _nested_record(next(row for row in data["impacts"] if row["encounter_id"] == "H1-A"))[
            field
        ] = value

    disagreed = _invocation(
        _mutate(impacts.raw_tool_result, lambda data: retarget(data, "encounter_id", "H1-B")),
        {"hazard_ids": ["H1"]},
    )
    blocked = _verify((impact, sibling, risk, recommendations, alert, current), disagreed)
    assert [item.verdict for item in blocked.assessments[:1]] == [LiveOpsClaimVerdict.REJECTED]
    assert blocked.assessments[0].codes == (LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,)
    for index in range(2, 6):
        assert blocked.assessments[index].verdict is LiveOpsClaimVerdict.REJECTED
        assert blocked.assessments[index].codes == (
            LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
        )
    assert blocked.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert blocked.verified_claims == (sibling,)
    assert blocked.outcome is LiveOpsVerificationOutcome.PARTIALLY_VERIFIED

    omitted = _invocation(
        _mutate(
            impacts.raw_tool_result,
            lambda data: _nested_record(
                next(row for row in data["impacts"] if row["encounter_id"] == "H1-A")
            ).pop("encounter_id"),
        ),
        {"hazard_ids": ["H1"]},
    )
    unknown = _verify((impact, risk, current), omitted)
    assert unknown.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert all(item.conflicting_tool_call_ids == () for item in unknown.assessments)

    for field, code in (
        ("hazard_source_version", LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH),
        ("aircraft_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("hazard_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
    ):
        mutated = _invocation(
            _mutate(
                impacts.raw_tool_result,
                lambda data, field=field: retarget(data, field, "other-value"),
            ),
            {"hazard_ids": ["H1"]},
        )
        rejected = _verify((impact, sibling), mutated)
        assert rejected.assessments[0].codes == (code.value,)
        assert rejected.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
        assert sibling in rejected.verified_claims

    encounters = _invocation(
        _run(search_current_encounters, "enc-h1", hazard_id="H1"),
        {"hazard_id": "H1"},
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    encounter_sibling = ConfirmedEncounterClaim(
        tool_call_id="enc-h1",
        tool_name="search_current_encounters",
        encounter_id="H1-B",
        aircraft_id="b",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-b",
    )
    encounter_conflict = _invocation(
        _mutate(
            encounters.raw_tool_result,
            lambda data: _nested_record(
                next(row for row in data["encounters"] if row["encounter_id"] == "H1-A")
            ).__setitem__("encounter_id", "H1-Z"),
        ),
        {"hazard_id": "H1"},
    )
    encounter_result = _verify((encounter, encounter_sibling), encounter_conflict)
    assert encounter_result.assessments[0].codes == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )
    assert encounter_result.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert encounter_result.verified_claims == (encounter_sibling,)

    hazard_context = _invocation(
        _run(get_hazard_operational_context, "hz-ctx", "H1"),
        {"hazard_id": "H1"},
    )
    context_claim = replace(impact, tool_call_id="hz-ctx", tool_name="get_hazard_operational_context")
    assert _verify((context_claim,), hazard_context).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    context_conflict = _invocation(
        _mutate(
            hazard_context.raw_tool_result,
            lambda data: _nested_record(
                next(row for row in data["impacts"] if row["encounter_id"] == "H1-A")
            ).__setitem__("encounter_id", "H1-Z"),
        ),
        {"hazard_id": "H1"},
    )
    assert _verify((context_claim,), context_conflict).assessments[0].codes == (
        LiveOpsClaimRejectionCode.VALUE_MISMATCH.value,
    )


def test_verification_result_rejects_inconsistent_contracts():
    impacts = _invocation(
        _run(search_current_impacts, "imp-h1", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    ordinary = _verify((_impact(),), impacts)
    assert ordinary.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert ordinary.rendering_posture is LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET
    assert ordinary.verified_claims == (_impact(),)
    assert ordinary.to_dict()["assessments"][0]["verdict"] == "VERIFIED"
    assert ordinary.to_dict()["schema_version"]

    verified = LiveOpsClaimAssessment(
        claim_index=0,
        verdict=LiveOpsClaimVerdict.VERIFIED,
        codes=(),
        tool_call_ids=("imp-h1",),
        limitation_codes=(),
    )
    with pytest.raises(ContractValidationError) as conflicted:
        LiveOpsClaimAssessment(
            claim_index=0,
            verdict=LiveOpsClaimVerdict.VERIFIED,
            codes=(),
            tool_call_ids=("imp-h1",),
            limitation_codes=(),
            conflicting_tool_call_ids=("imp-b",),
            scope_disagreement_metrics=("IMPACT_RECORD_COUNT",),
            normalized_scope=(("hazard_ids", "H1"),),
        )
    assert "verified_forbids_conflict_metadata" in conflicted.value.errors

    with pytest.raises(ContractValidationError) as coded:
        LiveOpsClaimAssessment(
            claim_index=0,
            verdict=LiveOpsClaimVerdict.VERIFIED,
            codes=(LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value,),
            tool_call_ids=("imp-h1",),
            limitation_codes=(),
        )
    assert "verified_forbids_contradiction_codes" in coded.value.errors

    contradicted = LiveOpsClaimAssessment(
        claim_index=0,
        verdict=LiveOpsClaimVerdict.CONTRADICTED,
        codes=(LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value,),
        tool_call_ids=("imp-h1",),
        limitation_codes=(),
        conflicting_tool_call_ids=("imp-b",),
        scope_disagreement_metrics=(LiveOpsCountMetric.IMPACT_RECORD_COUNT.value,),
        normalized_scope=(("hazard_ids", "H1"),),
    )
    lineage = LiveOpsClaimAssessment(
        claim_index=0,
        verdict=LiveOpsClaimVerdict.CONTRADICTED,
        codes=(LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT.value,),
        tool_call_ids=("imp-h1",),
        limitation_codes=(),
    )
    with pytest.raises(ContractValidationError) as mixed:
        LiveOpsVerificationResult(
            outcome=LiveOpsVerificationOutcome.ALL_VERIFIED,
            rendering_posture=LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET,
            assessments=(verified, contradicted),
            verified_claims=(_impact(), _impact(tool_call_id="imp-b")),
            mandatory_limitations=(),
            structural_codes=(),
            contradiction_subjects=(),
        )
    assert "all_verified_requires_verified_assessments" in mixed.value.errors

    with pytest.raises(ContractValidationError) as counted:
        LiveOpsVerificationResult(
            outcome=LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
            rendering_posture=LiveOpsRenderingPosture.INCOMPLETE_VERIFIED_SUBSET,
            assessments=(verified, contradicted),
            verified_claims=(),
            mandatory_limitations=(),
            structural_codes=(),
            contradiction_subjects=(),
        )
    assert "verified_claims_must_match_assessments" in counted.value.errors

    coherent = LiveOpsVerificationResult(
        outcome=LiveOpsVerificationOutcome.NONE_VERIFIED,
        rendering_posture=LiveOpsRenderingPosture.NO_VERIFIED_FACTS,
        assessments=(lineage,),
        verified_claims=(),
        mandatory_limitations=(),
        structural_codes=(),
        contradiction_subjects=(),
    )
    assert coherent.verified_claims == ()
    assert coherent.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED

    with pytest.raises(ContractValidationError) as posture:
        LiveOpsVerificationResult(
            outcome=LiveOpsVerificationOutcome.ALL_VERIFIED,
            rendering_posture=LiveOpsRenderingPosture.NO_VERIFIED_FACTS,
            assessments=(verified,),
            verified_claims=(_impact(),),
            mandatory_limitations=(),
            structural_codes=(),
            contradiction_subjects=(),
        )
    assert "rendering_posture_mismatch" in posture.value.errors

    refused = LiveOpsVerificationResult(
        outcome=LiveOpsVerificationOutcome.NOT_AUTHORIZED,
        rendering_posture=LiveOpsRenderingPosture.NOT_RENDERABLE,
        assessments=(),
        verified_claims=(),
        mandatory_limitations=(),
        structural_codes=(LiveOpsClaimRejectionCode.EMPTY_CLAIM_SET.value,),
        contradiction_subjects=(),
    )
    assert refused.verified_claims == ()
    with pytest.raises(ContractValidationError) as weakened:
        LiveOpsVerificationResult(
            outcome=LiveOpsVerificationOutcome.NOT_AUTHORIZED,
            rendering_posture=LiveOpsRenderingPosture.NOT_RENDERABLE,
            assessments=(verified,),
            verified_claims=(_impact(),),
            mandatory_limitations=(),
            structural_codes=(LiveOpsClaimRejectionCode.EMPTY_CLAIM_SET.value,),
            contradiction_subjects=(),
        )
    assert "not_authorized_forbids_verified_claims" in weakened.value.errors


def test_aggregate_row_authorization_matrix():
    """Boundary cases for aggregate authority, row witnesses, and complete sets.

    The matrix is selective. Each case changes one of source integrity,
    coverage, or the relationship between two reads.
    """

    left = _callsign_invocation("cs-a", "a", "c")
    same = _callsign_invocation("cs-same", "a", "c")
    renamed = _callsign_invocation(
        "cs-b",
        "a",
        "c",
        mutate=lambda data: data["matches"][0]["aircraft"].__setitem__("aircraft_id", "b"),
    )
    inflated_same = _callsign_invocation(
        "cs-inflated",
        "a",
        "c",
        mutate=lambda data: data.__setitem__("match_count", 99),
    )
    inflated_other = _callsign_invocation(
        "cs-inflated-b",
        "a",
        "c",
        mutate=lambda data: (
            data["matches"][0]["aircraft"].__setitem__("aircraft_id", "b"),
            data.__setitem__("match_count", 99),
        ),
    )
    missing_count = _callsign_invocation(
        "cs-missing",
        "a",
        "c",
        mutate=lambda data: data.pop("match_count"),
    )
    blank = _callsign_invocation(
        "cs-blank",
        "a",
        "c",
        mutate=lambda data: [
            row["aircraft"].__setitem__("aircraft_id", "") for row in data["matches"]
        ],
    )
    capped_other = _callsign_invocation(
        "cs-capped-b",
        "a",
        "c",
        mutate=lambda data: (
            data["matches"][0]["aircraft"].__setitem__("aircraft_id", "b"),
            _set_limit(data, 2),
        ),
    )
    zero_inv = _invocation(
        _run(find_aircraft_by_callsign, "cs0", "UAL999", tables=fixtures.tables()),
        {"callsign": "UAL999"},
    )
    other_rows = [fixtures.aircraft("a", callsign="UAL123")]
    other_tables = fixtures.tables(
        aircraft=fixtures.FlexibleTable(query_items={"UAL123": other_rows})
    )
    other_scope = _invocation(
        _run(find_aircraft_by_callsign, "cs-other", "UAL123", tables=other_tables),
        {"callsign": "UAL123"},
    )

    def claim_set(tool_call_id, *aircraft_ids):
        return CallsignMatchSetClaim(
            tool_call_id=tool_call_id,
            callsign="UAL999",
            aircraft_ids=aircraft_ids,
        )

    def claim_count(tool_call_id, value):
        return _count(
            tool_call_id,
            "find_aircraft_by_callsign",
            LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
            value,
            callsign="UAL999",
        )

    zero = QueryResultZeroClaim(
        tool_call_id="cs0",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsExecutedScope(callsign="UAL999"),
    )
    cases = (
        (
            "consistent identical membership",
            (claim_set("cs-a", "a", "c"), claim_set("cs-same", "a", "c"), claim_count("cs-a", 2)),
            (left, same),
            LiveOpsVerificationOutcome.ALL_VERIFIED,
            (claim_set("cs-a", "a", "c"), claim_set("cs-same", "a", "c"), claim_count("cs-a", 2)),
        ),
        (
            "inconsistent aggregate identical membership",
            (claim_count("cs-a", 2), claim_count("cs-inflated", 2), claim_set("cs-a", "a", "c")),
            (left, inflated_same),
            LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
            (claim_count("cs-a", 2), claim_set("cs-a", "a", "c")),
        ),
        (
            "missing aggregate is not repaired",
            (claim_count("cs-missing", 2), claim_set("cs-missing", "a", "c")),
            (missing_count,),
            LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
            (claim_set("cs-missing", "a", "c"),),
        ),
        (
            "malformed identity does not challenge a zero",
            (zero, claim_set("cs-blank", "a", "c")),
            (zero_inv, blank),
            LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
            (zero,),
        ),
        (
            "consistent different membership",
            (claim_set("cs-a", "a", "c"), claim_set("cs-b", "b", "c")),
            (left, renamed),
            LiveOpsVerificationOutcome.NONE_VERIFIED,
            (),
        ),
        (
            "inconsistent aggregate different membership",
            (claim_set("cs-a", "a", "c"), claim_set("cs-inflated-b", "b", "c")),
            (left, inflated_other),
            LiveOpsVerificationOutcome.NONE_VERIFIED,
            (),
        ),
        (
            "authoritative zero versus positive witness",
            (zero, claim_set("cs-a", "a", "c"), claim_count("cs-a", 2)),
            (zero_inv, left),
            LiveOpsVerificationOutcome.NONE_VERIFIED,
            (),
        ),
        (
            "capped subsets are not complete sets",
            (claim_set("cs-a", "a", "c"), claim_set("cs-capped-b", "b", "c")),
            (
                _callsign_invocation("cs-a", "a", "c", mutate=lambda data: _set_limit(data, 2)),
                capped_other,
            ),
            LiveOpsVerificationOutcome.NONE_VERIFIED,
            (),
        ),
        (
            "different callsigns stay independent",
            (
                claim_set("cs-a", "a", "c"),
                CallsignMatchSetClaim(
                    tool_call_id="cs-other",
                    callsign="UAL123",
                    aircraft_ids=("a",),
                ),
            ),
            (left, other_scope),
            LiveOpsVerificationOutcome.ALL_VERIFIED,
            (
                claim_set("cs-a", "a", "c"),
                CallsignMatchSetClaim(
                    tool_call_id="cs-other",
                    callsign="UAL123",
                    aircraft_ids=("a",),
                ),
            ),
        ),
    )
    capped_positive = _callsign_invocation(
        "cs-capped",
        "a",
        "c",
        mutate=lambda data: _set_limit(data, 2),
    )
    partial_hazard = _run(search_current_hazards, "hz", hazard_ids=["H1"])

    def make_partial(data):
        data["operationally_unevaluated"] = [{"encounter_id": "pending", "limitations": []}]
        data["match_count"] = len(data["hazards"])

    partial = _invocation(
        replace(
            _reidentify(_mutate(partial_hazard, make_partial), "hz-partial"),
            status=ToolResultStatus.PARTIAL,
        ),
        {"hazard_ids": ["H1"]},
    )
    success = _invocation(partial_hazard, {"hazard_ids": ["H1"]})
    current = CurrentnessClaim(
        tool_call_id="hz",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    extra_cases = (
        (
            "capped positive witness challenges an authoritative zero",
            (zero, claim_set("cs-capped", "a", "c")),
            (zero_inv, capped_positive),
            LiveOpsVerificationOutcome.NONE_VERIFIED,
            (),
        ),
        (
            "partial coverage is not an exhaustive contradiction",
            (current, replace(current, tool_call_id="hz-partial")),
            (success, partial),
            LiveOpsVerificationOutcome.ALL_VERIFIED,
            (current, replace(current, tool_call_id="hz-partial")),
        ),
        (
            "model truncation does not change the raw count",
            (
                claim_set("cs-a", "a", "c"),
                claim_count("cs-a", 2),
            ),
            (_callsign_view(left, keep=1, truncated=True),),
            LiveOpsVerificationOutcome.PARTIALLY_VERIFIED,
            (claim_count("cs-a", 2),),
        ),
    )
    for name, claims, invocations, outcome, verified in cases + extra_cases:
        result = _batch(claims, *invocations)
        assert result.outcome is outcome, name
        assert result.rendering_posture is _posture_for(outcome), name
        assert result.verified_claims == tuple(verified), name


def _clear_to_zero(data, list_name, count_name):
    data[list_name] = []
    data[count_name] = 0
    for name in (
        "operationally_unevaluated",
        "geospatial_unevaluated",
        "rejected_hazard_selections",
        "rejected_hazard_candidates",
        "unique_aircraft_ids",
    ):
        if name in data:
            data[name] = []


def _parent_forms():
    return (
        ("absent", lambda row, field: row.pop(field, None)),
        ("none", lambda row, field: row.__setitem__(field, None)),
        ("empty", lambda row, field: row.__setitem__(field, "")),
        ("whitespace", lambda row, field: row.__setitem__(field, " ")),
    )


def test_parent_identity_does_not_fall_back_to_nested_encounter():
    """A blank parent identity is not copied from the nested encounter."""

    encounters = _run(search_current_encounters, "enc", hazard_id="H1")
    encounter_zero = _invocation(
        _reidentify(
            _mutate(encounters, lambda data: _clear_to_zero(data, "encounters", "encounter_count")),
            "enc0",
        ),
        {"hazard_id": "H1"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="enc0",
        tool_name="search_current_encounters",
        metric=LiveOpsCountMetric.ENCOUNTER_RECORD_COUNT,
        scope=LiveOpsExecutedScope(hazard_id="H1"),
    )
    assert _verify((zero,), encounter_zero).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    def encounter_claims(tool_call_id):
        encounter = ConfirmedEncounterClaim(
            tool_call_id=tool_call_id,
            tool_name="search_current_encounters",
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            hazard_source_version="v1",
            projection_id="proj-a",
        )
        current = CurrentnessClaim(
            tool_call_id=tool_call_id,
            tool_name="search_current_encounters",
            predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
            value=True,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
        )
        risk = RiskStateClaim(
            tool_call_id=tool_call_id,
            tool_name="search_current_encounters",
            mode=LiveOpsRiskMode.PRESENT,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            risk_id="risk-1",
            risk_level="HIGH",
            risk_score=90,
        )
        recommendations = RecommendationSetClaim(
            tool_call_id=tool_call_id,
            tool_name="search_current_encounters",
            mode=LiveOpsAdvisoryMode.PRESENT_SET,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            recommendation_ids=("rec-1", "rec-2"),
        )
        alert = AlertRecordClaim(
            tool_call_id=tool_call_id,
            tool_name="search_current_encounters",
            mode=LiveOpsAdvisoryMode.PRESENT_SET,
            encounter_id="H1-A",
            aircraft_id="a",
            hazard_id="H1",
            alert_id="alert-risk",
            alert_state="NEW",
        )
        return encounter, current, risk, recommendations, alert

    for field in ("aircraft_id", "hazard_id", "encounter_id", "source_version"):
        for form, apply in _parent_forms():
            def mutate(data, field=field, apply=apply):
                row = _encounter_context(data)
                data["encounters"] = [row]
                data["encounter_count"] = 1
                apply(row, field)

            malformed = _invocation(
                _reidentify(_mutate(encounters, mutate), "enc-bad"),
                {"hazard_id": "H1"},
            )
            claims = encounter_claims("enc-bad")
            result = _batch((zero, *claims), encounter_zero, malformed)
            assert result.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED, (field, form)
            assert result.assessments[0].conflicting_tool_call_ids == (), (field, form)
            assert all(
                item.verdict is not LiveOpsClaimVerdict.VERIFIED for item in result.assessments[1:]
            ), (field, form, [item.verdict.value for item in result.assessments])
            assert result.verified_claims == (zero,), (field, form)

    impacts = _run(search_current_impacts, "imp", hazard_ids=["H1"])
    impact_zero = _invocation(
        _reidentify(
            _mutate(impacts, lambda data: _clear_to_zero(data, "impacts", "impact_count")),
            "imp0",
        ),
        {"hazard_ids": ["H1"]},
    )
    impact_zero_claim = QueryResultZeroClaim(
        tool_call_id="imp0",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    assert _verify((impact_zero_claim,), impact_zero).outcome is LiveOpsVerificationOutcome.ALL_VERIFIED

    def blank_impact_parents(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["aircraft_id"] = None
        row["hazard_id"] = None
        data["impacts"] = [row]
        data["impact_count"] = 1

    blank_impact = _invocation(
        _reidentify(_mutate(impacts, blank_impact_parents), "imp-blank"),
        {"hazard_ids": ["H1"]},
    )
    impact = ConfirmedImpactClaim(
        tool_call_id="imp-blank",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    impact_current = CurrentnessClaim(
        tool_call_id="imp-blank",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    impact_risk = RiskStateClaim(
        tool_call_id="imp-blank",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    impact_recommendations = RecommendationSetClaim(
        tool_call_id="imp-blank",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    impact_alert = AlertRecordClaim(
        tool_call_id="imp-blank",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        alert_id="alert-risk",
        alert_state="NEW",
    )
    hidden = _batch(
        (
            impact_zero_claim,
            impact,
            impact_risk,
            impact_recommendations,
            impact_alert,
            impact_current,
        ),
        impact_zero,
        blank_impact,
    )
    assert hidden.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert hidden.assessments[0].conflicting_tool_call_ids == ()
    assert all(item.verdict is not LiveOpsClaimVerdict.VERIFIED for item in hidden.assessments[1:])
    assert hidden.verified_claims == (impact_zero_claim,)

    valid_impact = _invocation(impacts, {"hazard_ids": ["H1"]})
    valid_encounter = _invocation(encounters, {"hazard_id": "H1"})
    confirmed_impact = replace(impact, tool_call_id="imp")
    confirmed_encounter = encounter_claims("enc")[0]
    sibling_impact = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="b",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-B",
    )
    assert _verify((confirmed_impact, sibling_impact), valid_impact).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )
    assert _verify((confirmed_encounter,), valid_encounter).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )

    def blank_only_h1a(data):
        next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")["aircraft_id"] = ""

    mixed = _invocation(_mutate(impacts, blank_only_h1a), {"hazard_ids": ["H1"]})
    sibling_result = _verify((confirmed_impact, sibling_impact), mixed)
    assert sibling_result.assessments[0].verdict is not LiveOpsClaimVerdict.VERIFIED
    assert sibling_result.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert sibling_result.verified_claims == (sibling_impact,)

    aircraft = _run(get_aircraft_operational_context, "ac-a", "a")
    assert aircraft.data["encounters"] == []
    direct_context = copy.deepcopy(
        next(row for row in encounters.data["encounters"] if row["encounter_id"] == "H1-A")[
            "encounter_context"
        ]
    )
    assert "encounter_id" not in direct_context
    assert direct_context["encounter"]["encounter_id"] == "H1-A"
    direct = _invocation(
        _reidentify(
            _mutate(aircraft, lambda data: data.__setitem__("encounters", [direct_context])),
            "ac-a",
        ),
        {"aircraft_id": "a"},
    )
    direct_encounter = ConfirmedEncounterClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    direct_current = CurrentnessClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    assert _verify((direct_encounter, direct_current), direct).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )

    for field, code in (
        ("encounter_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("aircraft_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("hazard_id", LiveOpsClaimRejectionCode.VALUE_MISMATCH),
        ("hazard_source_version", LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH),
    ):
        def disagree(data, field=field):
            _nested_record(next(row for row in data["impacts"] if row["encounter_id"] == "H1-A"))[
                field
            ] = "other-value"

        rejected = _verify(
            (confirmed_impact, sibling_impact),
            _invocation(_mutate(impacts, disagree), {"hazard_ids": ["H1"]}),
        )
        assert rejected.assessments[0].codes == (code.value,), field
        assert rejected.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
        assert sibling_impact in rejected.verified_claims


def _blank_parent_source_version(row, form):
    if form == "absent":
        row.pop("source_version", None)
    elif form == "none":
        row["source_version"] = None
    elif form == "empty":
        row["source_version"] = ""
    else:
        row["source_version"] = " "


def _isolate_parent_row(data, list_name, count_name, encounter_id="H1-A"):
    row = next(item for item in data[list_name] if item.get("encounter_id") == encounter_id)
    data[list_name] = [row]
    if count_name is not None:
        data[count_name] = 1
    return row


def _lineage_subjects(result):
    return {
        subject.subject
        for subject in result.contradiction_subjects
        if subject.subject
        in {
            LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
            LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE,
        }
    }


def test_malformed_parent_source_version_does_not_create_contradictions():
    """A blank parent source version is not a nested hazard-version witness."""

    hazards = _invocation(
        _run(search_current_hazards, "hz", hazard_ids=["H1"]),
        {"hazard_ids": ["H1"]},
    )
    encounters = _run(search_current_encounters, "enc", hazard_id="H1")
    valid_enc = _invocation(encounters, {"hazard_id": "H1"})
    hazard_claim = CurrentnessClaim(
        tool_call_id="hz",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    risk = RiskStateClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    for form in ("absent", "none", "empty", "whitespace"):
        def malformed_encounter(data, form=form):
            row = _isolate_parent_row(data, "encounters", "encounter_count")
            _blank_parent_source_version(row, form)
            row["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

        bad = _invocation(
            _reidentify(_mutate(encounters, malformed_encounter), "enc-bad"),
            {"aircraft_id": "a"},
        )
        hazard_conflict = CrossToolContradictionClaim(
            tool_call_id_a="hz",
            tool_call_id_b="enc-bad",
            subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
            hazard_id="H1",
        )
        encounter_conflict = CrossToolContradictionClaim(
            tool_call_id_a="enc",
            tool_call_id_b="enc-bad",
            subject=LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE,
            encounter_id="H1-A",
        )
        result = _batch(
            (hazard_claim, encounter, risk, hazard_conflict, encounter_conflict),
            hazards,
            valid_enc,
            bad,
        )
        assert result.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED, form
        assert result.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED, form
        assert result.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED, form
        assert result.assessments[3].verdict is LiveOpsClaimVerdict.REJECTED, form
        assert result.assessments[3].codes == (
            LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,
        ), form
        assert result.assessments[4].codes == (
            LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,
        ), form
        assert _lineage_subjects(result) == set(), form
        assert hazard_claim in result.verified_claims
        assert encounter in result.verified_claims
        assert risk in result.verified_claims

    impacts = _run(search_current_impacts, "imp", hazard_ids=["H1"])

    def malformed_impact(data):
        row = _isolate_parent_row(data, "impacts", "impact_count")
        _blank_parent_source_version(row, "absent")
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

    bad_impact = _invocation(
        _reidentify(_mutate(impacts, malformed_impact), "imp-bad"),
        {"hazard_ids": ["H1"]},
    )
    impact_conflict = CrossToolContradictionClaim(
        tool_call_id_a="hz",
        tool_call_id_b="imp-bad",
        subject=LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION,
        hazard_id="H1",
    )
    impact_result = _batch((hazard_claim, impact_conflict), hazards, bad_impact)
    assert impact_result.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impact_result.assessments[1].codes == (
        LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,
    )
    assert _lineage_subjects(impact_result) == set()

    def explicit_disagreement(data):
        row = _isolate_parent_row(data, "encounters", "encounter_count")
        row["source_version"] = "v1"
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

    disagreed = _invocation(
        _reidentify(_mutate(encounters, explicit_disagreement), "enc-bad"),
        {"aircraft_id": "a"},
    )
    disagreed_claim = replace(encounter, tool_call_id="enc-bad")
    explicit = _batch(
        (hazard_claim, encounter, disagreed_claim, hazard_conflict),
        hazards,
        valid_enc,
        disagreed,
    )
    assert explicit.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert explicit.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert explicit.assessments[2].verdict is LiveOpsClaimVerdict.REJECTED
    assert LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value in explicit.assessments[2].codes
    assert explicit.assessments[3].codes == (
        LiveOpsClaimRejectionCode.CONTRADICTION_NOT_PRESENT.value,
    )
    assert _lineage_subjects(explicit) == set()

    def consistent_v2(data):
        row = _isolate_parent_row(data, "encounters", "encounter_count")
        row["source_version"] = "v2"
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v2"

    other = _invocation(
        _reidentify(_mutate(encounters, consistent_v2), "enc-v2"),
        {"hazard_id": "H2"},
    )
    genuine = _batch((encounter,), valid_enc, other)
    assert genuine.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert any(
        subject.subject is LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE
        and set(subject.tool_call_ids) >= {"enc", "enc-v2"}
        for subject in genuine.contradiction_subjects
    )
    hidden = _batch((encounter,), valid_enc, other, bad)
    assert hidden.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert any(
        subject.subject is LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE
        and set(subject.tool_call_ids) >= {"enc", "enc-v2"}
        for subject in hidden.contradiction_subjects
    )

    aircraft = _run(get_aircraft_operational_context, "ac", "a")
    direct_row = copy.deepcopy(
        next(row for row in encounters.data["encounters"] if row["encounter_id"] == "H1-A")[
            "encounter_context"
        ]
    )
    direct = _invocation(
        _reidentify(
            _mutate(aircraft, lambda data: data.__setitem__("encounters", [direct_row])),
            "ac",
        ),
        {"aircraft_id": "a"},
    )
    direct_encounter = ConfirmedEncounterClaim(
        tool_call_id="ac",
        tool_name="get_aircraft_operational_context",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    direct_result = _batch((direct_encounter, hazard_claim), direct, hazards, bad)
    assert direct_result.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert direct_result.assessments[1].verdict is LiveOpsClaimVerdict.VERIFIED
    assert _lineage_subjects(direct_result) == set()

    def blank_only_h1a(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        _blank_parent_source_version(row, "absent")
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

    mixed = _invocation(_mutate(impacts, blank_only_h1a), {"hazard_ids": ["H1"]})
    sibling = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="b",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-B",
    )
    observed = AircraftObservedStateClaim(
        tool_call_id="ac",
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        field=LiveOpsObservedField.ON_GROUND,
        value=False,
        retained_not_current=False,
    )
    preserved = _batch(
        (sibling, observed),
        mixed,
        _invocation(aircraft, {"aircraft_id": "a"}),
    )
    assert preserved.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert preserved.verified_claims == (sibling, observed)


_REMOVE = object()


def _retarget_projection_link(link, side, value):
    pairs = link.get(side)
    if not isinstance(pairs, list):
        link[side] = []
        pairs = link[side]
    kept = [pair for pair in pairs if not (isinstance(pair, list) and pair and pair[0] == "projection_id")]
    if value is not _REMOVE:
        kept.append(["projection_id", value])
    link[side] = kept


_OMIT = object()


def _retarget_projection(record, *, document=_OMIT, selected=_OMIT, observed=_OMIT):
    if document is not _OMIT and isinstance(record.get("projection"), dict):
        record["projection"]["projection_id"] = document
    link = record.get("projection_link")
    if not isinstance(link, dict):
        return
    if selected is not _OMIT:
        _retarget_projection_link(link, "selected_identity", selected)
    if observed is not _OMIT:
        _retarget_projection_link(link, "observed_identity", observed)


def test_projection_document_identity_must_match_selected_projection():
    impacts = _run(search_current_impacts, "imp", hazard_ids=["H1"])
    valid_projection = AircraftProjectionStateClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        projection_id="proj-a",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    sibling_projection = AircraftProjectionStateClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="b",
        projection_id="proj-b",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    assert _verify((valid_projection, sibling_projection), _invocation(impacts, {"hazard_ids": ["H1"]})).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )

    def mismatch_h1a(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        _retarget_projection(row, document="proj-doc", observed="proj-doc")

    mismatched = _invocation(_mutate(impacts, mismatch_h1a), {"hazard_ids": ["H1"]})
    changed = next(
        item
        for item in mismatched.raw_tool_result.data["impacts"]
        if item["encounter_id"] == "H1-A"
    )
    assert changed["projection"]["projection_id"] == "proj-doc"
    assert changed["encounter_context"]["encounter"]["projection_id"] == "proj-a"
    assert ["projection_id", "proj-a"] in changed["projection_link"]["selected_identity"]
    assert ["projection_id", "proj-doc"] in changed["projection_link"]["observed_identity"]
    document_claim = replace(valid_projection, projection_id="proj-doc")
    impact = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    current = CurrentnessClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    risk = RiskStateClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    recommendations = RecommendationSetClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    impact_result = _verify(
        (document_claim, valid_projection, sibling_projection, impact, current, risk, recommendations),
        mismatched,
    )
    assert impact_result.assessments[0].verdict is LiveOpsClaimVerdict.REJECTED
    assert impact_result.assessments[0].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    assert impact_result.assessments[1].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    assert impact_result.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impact_result.assessments[3].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impact_result.assessments[4].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impact_result.assessments[5].verdict is LiveOpsClaimVerdict.VERIFIED
    assert impact_result.assessments[6].verdict is LiveOpsClaimVerdict.VERIFIED
    assert sibling_projection in impact_result.verified_claims
    assert impact in impact_result.verified_claims
    assert risk in impact_result.verified_claims

    aircraft = _run(get_aircraft_operational_context, "ac-a", "a")
    other_aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-b", "b"),
        {"aircraft_id": "b"},
    )

    def mismatch_aircraft(data):
        _retarget_projection(data, document="proj-doc", observed="proj-doc")

    aircraft_bad = _invocation(
        _reidentify(_mutate(aircraft, mismatch_aircraft), "ac-a"),
        {"aircraft_id": "a"},
    )
    state_doc = AircraftProjectionStateClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        aircraft_id="a",
        projection_id="proj-doc",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    state_selected = replace(state_doc, projection_id="proj-a")
    current_doc = CurrentnessClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT,
        value=True,
        aircraft_id="a",
        projection_id="proj-doc",
    )
    current_selected = replace(current_doc, projection_id="proj-a")
    current_unnamed = CurrentnessClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT,
        value=True,
        aircraft_id="a",
    )
    current_false = replace(current_unnamed, value=False)
    other_state = AircraftProjectionStateClaim(
        tool_call_id="ac-b",
        tool_name="get_aircraft_operational_context",
        aircraft_id="b",
        projection_id="proj-b",
        field=LiveOpsProjectionField.PROJECTION_STATUS,
        value="READY",
    )
    aircraft_result = _batch(
        (
            state_doc,
            state_selected,
            current_doc,
            current_selected,
            current_unnamed,
            current_false,
            other_state,
        ),
        aircraft_bad,
        other_aircraft,
    )
    assert [item.verdict for item in aircraft_result.assessments[:6]] == [
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
        LiveOpsClaimVerdict.REJECTED,
    ]
    assert all(
        item.codes == (LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,)
        for item in aircraft_result.assessments[:5]
    )
    assert aircraft_result.assessments[5].verdict is not LiveOpsClaimVerdict.VERIFIED
    assert aircraft_result.assessments[6].verdict is LiveOpsClaimVerdict.VERIFIED
    assert other_state in aircraft_result.verified_claims

    agreed = _invocation(aircraft, {"aircraft_id": "a"})
    agreed_state = replace(state_selected, tool_call_id="ac-a")
    agreed_current = replace(current_selected, tool_call_id="ac-a")
    assert _verify((agreed_state, agreed_current), agreed).outcome is (
        LiveOpsVerificationOutcome.ALL_VERIFIED
    )

    def _identity_case(selected, observed):
        def mutate(data, selected=selected, observed=observed):
            _retarget_projection(data, selected=selected, observed=observed)

        return _invocation(
            _reidentify(_mutate(aircraft, mutate), "ac-a"),
            {"aircraft_id": "a"},
        )

    for selected, observed in (
        (_REMOVE, "proj-a"),
        (None, "proj-a"),
        ("", "proj-a"),
        (" ", "proj-a"),
        ("proj-a", _REMOVE),
        ("proj-a", None),
        ("proj-a", ""),
        ("proj-a", " "),
        ("proj-a", 12),
    ):
        incomplete = _verify((agreed_state, agreed_current), _identity_case(selected, observed))
        assert incomplete.assessments[0].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE, (
            selected,
            observed,
        )
        assert incomplete.assessments[1].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE, (
            selected,
            observed,
        )
        assert incomplete.verified_claims == ()

    def missing_projection(data):
        data["projection"] = None
        data["projection_is_current"] = False
        data["projection_link"] = {
            "state": "MISSING",
            "kind": "CURRENT_DEPENDENT",
            "reason": None,
            "selected_identity": [],
            "observed_identity": [],
        }

    absent = _invocation(
        _reidentify(_mutate(aircraft, missing_projection), "ac-a"),
        {"aircraft_id": "a"},
    )
    absent_false = CurrentnessClaim(
        tool_call_id="ac-a",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsCurrentnessPredicate.PROJECTION_IS_CURRENT,
        value=False,
        aircraft_id="a",
    )
    named_false = replace(absent_false, projection_id="proj-a")
    absent_true = replace(absent_false, value=True)
    absent_result = _verify((absent_false, named_false, absent_true), absent)
    assert absent_result.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert absent_result.assessments[1].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert absent_result.assessments[2].verdict is LiveOpsClaimVerdict.REJECTED
    assert absent_false in absent_result.verified_claims
    assert named_false not in absent_result.verified_claims

    def unknown_flag(data):
        data["projection_is_current"] = None

    unknown = _verify(
        (agreed_current, replace(agreed_current, value=False)),
        _invocation(_reidentify(_mutate(aircraft, unknown_flag), "ac-a"), {"aircraft_id": "a"}),
    )
    assert unknown.assessments[0].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert unknown.assessments[1].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert unknown.verified_claims == ()


def _blank_parent_versions(data, list_name):
    for row in data[list_name]:
        row.pop("source_version", None)
        nested = row.get("encounter_context", {}).get("encounter")
        if isinstance(nested, dict):
            nested["hazard_source_version"] = "v9"


def test_unreadable_membership_does_not_contradict_a_readable_complete_set():
    """Unknown membership is not proof that a readable complete set differs."""

    enc_raw = _run(search_current_encounters, "enc", hazard_id="H1")
    valid_enc = _invocation(enc_raw, {"hazard_id": "H1"})
    bad_enc = _invocation(
        _reidentify(_mutate(enc_raw, lambda data: _blank_parent_versions(data, "encounters")), "enc-bad"),
        {"hazard_id": "H1"},
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    sibling = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-B",
        aircraft_id="b",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-b",
    )
    current = CurrentnessClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    risk = RiskStateClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        mode=LiveOpsRiskMode.PRESENT,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        risk_id="risk-1",
        risk_level="HIGH",
        risk_score=90,
    )
    recommendations = RecommendationSetClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        mode=LiveOpsAdvisoryMode.PRESENT_SET,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        recommendation_ids=("rec-1", "rec-2"),
    )
    unsupported = replace(encounter, tool_call_id="enc-bad", hazard_source_version="v9")
    unsupported_risk = replace(risk, tool_call_id="enc-bad")
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    observed = _observed_aircraft()
    preserved = _batch(
        (encounter, sibling, current, risk, recommendations, unsupported, unsupported_risk, observed),
        valid_enc,
        bad_enc,
        aircraft,
    )
    assert [item.verdict for item in preserved.assessments[:5]] == [LiveOpsClaimVerdict.VERIFIED] * 5
    assert preserved.assessments[5].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert preserved.assessments[5].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    assert preserved.assessments[6].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert preserved.assessments[7].verdict is LiveOpsClaimVerdict.VERIFIED
    assert preserved.verified_claims == (
        encounter,
        sibling,
        current,
        risk,
        recommendations,
        observed,
    )
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in preserved.assessments
    )
    assert _lineage_subjects(preserved) == set()

    same_order = _invocation(
        _reidentify(
            _mutate(enc_raw, lambda data: data.__setitem__("encounters", list(reversed(data["encounters"])))),
            "enc-order",
        ),
        {"hazard_id": "H1"},
    )
    identical = _batch(
        (encounter, replace(encounter, tool_call_id="enc-order")),
        valid_enc,
        same_order,
    )
    assert identical.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert identical.verified_claims == (encounter, replace(encounter, tool_call_id="enc-order"))
    assert identical.contradiction_subjects == ()

    def retag_encounter(data):
        row = next(item for item in data["encounters"] if item["encounter_id"] == "H1-A")
        row["encounter_id"] = "H1-Z"
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"

    different = _invocation(
        _reidentify(_mutate(enc_raw, retag_encounter), "enc-other"),
        {"hazard_id": "H1"},
    )
    diverged = _batch((encounter, replace(encounter, tool_call_id="enc-other", encounter_id="H1-Z")), valid_enc, different)
    assert diverged.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert diverged.verified_claims == ()
    assert diverged.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in diverged.assessments[0].codes
    assert diverged.assessments[0].conflicting_tool_call_ids == ("enc-other",)

    def version_v2(data):
        row = next(item for item in data["encounters"] if item["encounter_id"] == "H1-A")
        row["source_version"] = "v2"
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v2"

    other_version = _invocation(
        _reidentify(_mutate(enc_raw, version_v2), "enc-v2"),
        {"hazard_id": "H1"},
    )
    versions = _batch((encounter,), valid_enc, other_version)
    assert versions.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert versions.verified_claims == ()
    assert versions.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.CONTRADICTED_SUBJECT.value in versions.assessments[0].codes
    assert any(
        subject.subject is LiveOpsContradictionSubjectKind.ENCOUNTER_LINEAGE
        and set(subject.tool_call_ids) >= {"enc", "enc-v2"}
        for subject in versions.contradiction_subjects
    )

    imp_raw = _run(search_current_impacts, "imp", hazard_ids=["H1"])
    valid_imp = _invocation(imp_raw, {"hazard_ids": ["H1"]})
    bad_imp = _invocation(
        _reidentify(_mutate(imp_raw, lambda data: _blank_parent_versions(data, "impacts")), "imp-bad"),
        {"hazard_ids": ["H1"]},
    )
    impact = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    impact_current = CurrentnessClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        predicate=LiveOpsCurrentnessPredicate.ENCOUNTER_IS_CURRENT,
        value=True,
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
    )
    impact_risk = replace(risk, tool_call_id="imp", tool_name="search_current_impacts")
    invented = replace(impact, tool_call_id="imp-bad", source_version="v9")
    impacts = _batch((impact, impact_current, impact_risk, invented), valid_imp, bad_imp)
    assert [item.verdict for item in impacts.assessments[:3]] == [LiveOpsClaimVerdict.VERIFIED] * 3
    assert impacts.assessments[3].verdict is LiveOpsClaimVerdict.REJECTED
    assert impacts.assessments[3].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    assert impacts.verified_claims == (impact, impact_current, impact_risk)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in impacts.assessments
    )

    def retag_impact(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["encounter_id"] = "H1-Z"
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"

    other_impact = _invocation(
        _reidentify(_mutate(imp_raw, retag_impact), "imp-other"),
        {"hazard_ids": ["H1"]},
    )
    impact_conflict = _batch((impact,), valid_imp, other_impact)
    assert impact_conflict.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert impact_conflict.verified_claims == ()
    assert impact_conflict.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert impact_conflict.assessments[0].codes == (
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value,
    )

    hazard_raw = _run(search_current_hazards, "hz", hazard_ids=["H1"])
    hazard = _invocation(hazard_raw, {"hazard_ids": ["H1"]})

    def clear_hazard_version(data):
        data["hazards"][0]["hazard"]["source_version"] = ""

    hazard_blank = _invocation(
        _reidentify(_mutate(hazard_raw, clear_hazard_version), "hz-blank"),
        {"hazard_ids": ["H1"]},
    )
    hazard_current = CurrentnessClaim(
        tool_call_id="hz",
        tool_name="search_current_hazards",
        predicate=LiveOpsCurrentnessPredicate.HAZARD_IS_CURRENT,
        value=True,
        hazard_id="H1",
        source_version="v1",
    )
    blank_current = replace(hazard_current, tool_call_id="hz-blank")
    hazards = _batch((hazard_current, blank_current), hazard, hazard_blank)
    assert hazards.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert hazards.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert hazards.assessments[1].codes == (
        LiveOpsClaimRejectionCode.IDENTITY_NOT_IN_EVIDENCE.value,
    )
    assert hazards.verified_claims == (hazard_current,)
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in hazards.assessments[0].codes

    def bump_hazard_version(data):
        data["hazards"][0]["hazard"]["source_version"] = "v2"

    hazard_v2 = _invocation(
        _reidentify(_mutate(hazard_raw, bump_hazard_version), "hz-v2"),
        {"hazard_ids": ["H1"]},
    )
    hazard_versions = _batch(
        (hazard_current, replace(hazard_current, tool_call_id="hz-v2", source_version="v2")),
        hazard,
        hazard_v2,
    )
    assert hazard_versions.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert hazard_versions.verified_claims == ()
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in hazard_versions.assessments)
    assert any(
        subject.subject is LiveOpsContradictionSubjectKind.HAZARD_SOURCE_VERSION
        for subject in hazard_versions.contradiction_subjects
    )

    callsign = _callsign_invocation("cs", "a", "c")
    callsign_blank = _callsign_invocation(
        "cs-bad",
        "a",
        "c",
        mutate=lambda data: data["matches"][0]["aircraft"].__setitem__("aircraft_id", " "),
    )
    match_set = CallsignMatchSetClaim(tool_call_id="cs", callsign="UAL999", aircraft_ids=("a", "c"))
    invented_set = CallsignMatchSetClaim(tool_call_id="cs-bad", callsign="UAL999", aircraft_ids=("a", "c"))
    callsigns = _batch((match_set, invented_set), callsign, callsign_blank)
    assert callsigns.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert callsigns.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert callsigns.assessments[1].codes == (
        LiveOpsClaimRejectionCode.CURRENTNESS_MISMATCH.value,
    )
    assert callsigns.verified_claims == (match_set,)
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in callsigns.assessments[0].codes

    empty_tables = fixtures.tables()
    hazard_zero = _invocation(
        _run(search_current_hazards, "hz-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    hazard_positive = _invocation(
        _run(search_current_hazards, "hz-ca", region="California"),
        {"region": "California"},
    )
    zero_pair = _batch((_california_zero("hz-empty"), _hazard_intersection("hz-ca")), hazard_zero, hazard_positive)
    assert zero_pair.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert zero_pair.verified_claims == ()
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in zero_pair.assessments)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in item.codes
        for item in zero_pair.assessments
    )

    def corrupt_count(data):
        data["impact_count"] = 99

    corrupt = _invocation(
        _reidentify(
            _mutate(_run(search_current_impacts, "imp-ca", region="California"), corrupt_count),
            "imp-corrupt",
        ),
        {"region": "California"},
    )
    impact_zero = _invocation(
        _run(search_current_impacts, "imp-empty", region="California", tables=empty_tables),
        {"region": "California"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="imp-empty",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )
    witnessed = replace(impact, tool_call_id="imp-corrupt")
    hidden = _batch((zero, witnessed, observed), impact_zero, corrupt, aircraft)
    assert hidden.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert hidden.assessments[1].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert hidden.assessments[2].verdict is LiveOpsClaimVerdict.VERIFIED
    assert hidden.verified_claims == (observed,)
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in hidden.assessments[0].codes

    same_corrupt = _invocation(
        _reidentify(_mutate(imp_raw, corrupt_count), "imp-corrupt"),
        {"hazard_ids": ["H1"]},
    )
    bad_count = ScopedCountClaim(
        tool_call_id="imp-corrupt",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=len(imp_raw.data["impacts"]),
        scope=LiveOpsExecutedScope(hazard_ids=("H1",)),
    )
    aggregate = _batch((impact, bad_count), valid_imp, same_corrupt)
    assert aggregate.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert aggregate.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert aggregate.assessments[1].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    assert aggregate.verified_claims == (impact,)
    assert aggregate.assessments[0].conflicting_tool_call_ids == ()

    def malformed_only(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-B"
        data["impacts"] = [row]
        data["impact_count"] = 1

    broken = _invocation(
        _reidentify(
            _mutate(_run(search_current_impacts, "imp-ca", region="California"), malformed_only),
            "imp-broken",
        ),
        {"region": "California"},
    )
    broken_impact = replace(impact, tool_call_id="imp-broken")
    quiet_zero = _batch((zero, broken_impact), impact_zero, broken)
    assert quiet_zero.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert quiet_zero.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert quiet_zero.verified_claims == (zero,)
    assert quiet_zero.assessments[0].conflicting_tool_call_ids == ()


def test_optional_nested_hazard_version_is_not_record_membership():
    """Parent identity is the impact membership signature.

    Optional nested hazard_source_version does not make two otherwise equal
    parent records different. A missing nested version is not repaired, and
    an explicit parent/nested mismatch stays unreadable.
    """

    raw = _run(search_current_impacts, "imp", hazard_ids=["H1"])
    with_nested = _invocation(raw, {"hazard_ids": ["H1"]})

    def drop_nested_version(data):
        for row in data["impacts"]:
            row["encounter_context"]["encounter"].pop("hazard_source_version", None)

    without_nested = _invocation(
        _reidentify(_mutate(raw, drop_nested_version), "imp-optional"),
        {"hazard_ids": ["H1"]},
    )
    present = ConfirmedImpactClaim(
        tool_call_id="imp",
        tool_name="search_current_impacts",
        aircraft_id="a",
        hazard_id="H1",
        source_version="v1",
        encounter_id="H1-A",
    )
    optional = replace(present, tool_call_id="imp-optional")
    paired = _batch((present, optional), with_nested, without_nested)
    assert paired.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    assert paired.rendering_posture is LiveOpsRenderingPosture.COMPLETE_VERIFIED_SET
    assert paired.verified_claims == (present, optional)
    assert all(item.verdict is LiveOpsClaimVerdict.VERIFIED for item in paired.assessments)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in paired.assessments
    )

    def bump_parent_version(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["source_version"] = "v2"
        row["encounter_context"]["encounter"]["hazard_source_version"] = "v2"

    other_version = _invocation(
        _reidentify(_mutate(raw, bump_parent_version), "imp-v2"),
        {"hazard_ids": ["H1"]},
    )
    version_claim = replace(present, tool_call_id="imp-v2", source_version="v2")
    versions = _batch((present, version_claim), with_nested, other_version)
    assert versions.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert versions.verified_claims == ()
    assert all(item.verdict is LiveOpsClaimVerdict.CONTRADICTED for item in versions.assessments)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in item.codes
        for item in versions.assessments
    )

    def nested_mismatch(data):
        for row in data["impacts"]:
            row["encounter_context"]["encounter"]["hazard_source_version"] = "v9"

    mismatched = _invocation(
        _reidentify(_mutate(raw, nested_mismatch), "imp-mismatch"),
        {"hazard_ids": ["H1"]},
    )
    rejected = replace(present, tool_call_id="imp-mismatch")
    conflict = _batch((present, rejected), with_nested, mismatched)
    assert conflict.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert conflict.assessments[1].verdict is LiveOpsClaimVerdict.REJECTED
    assert conflict.assessments[1].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    assert rejected not in conflict.verified_claims
    assert conflict.verified_claims == (present,)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in conflict.assessments
    )

    enc_raw = _run(search_current_encounters, "enc", hazard_id="H1")
    valid_enc = _invocation(enc_raw, {"hazard_id": "H1"})

    def drop_encounter_version(data):
        for row in data["encounters"]:
            row["encounter_context"]["encounter"].pop("hazard_source_version", None)

    missing_enc = _invocation(
        _reidentify(_mutate(enc_raw, drop_encounter_version), "enc-missing"),
        {"hazard_id": "H1"},
    )
    encounter = ConfirmedEncounterClaim(
        tool_call_id="enc",
        tool_name="search_current_encounters",
        encounter_id="H1-A",
        aircraft_id="a",
        hazard_id="H1",
        hazard_source_version="v1",
        projection_id="proj-a",
    )
    missing_claim = replace(encounter, tool_call_id="enc-missing")
    encounters = _batch((encounter, missing_claim), valid_enc, missing_enc)
    assert encounters.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert encounters.assessments[1].verdict is LiveOpsClaimVerdict.INSUFFICIENT_EVIDENCE
    assert encounters.assessments[1].codes == (
        LiveOpsClaimRejectionCode.SOURCE_VERSION_MISMATCH.value,
    )
    assert encounters.verified_claims == (encounter,)
    assert (
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value
        not in encounters.assessments[0].codes
    )

    def retag(data):
        row = next(item for item in data["impacts"] if item["encounter_id"] == "H1-A")
        row["encounter_id"] = "H1-Z"
        row["encounter_context"]["encounter"]["encounter_id"] = "H1-Z"

    retagged = _invocation(
        _reidentify(_mutate(raw, retag), "imp-other"),
        {"hazard_ids": ["H1"]},
    )
    other_impact = replace(present, tool_call_id="imp-other", encounter_id="H1-Z")
    ids = _batch((present, other_impact), with_nested, retagged)
    assert ids.outcome is LiveOpsVerificationOutcome.NONE_VERIFIED
    assert ids.verified_claims == ()
    assert ids.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in ids.assessments[0].codes

    def blank_parent(data):
        for row in data["impacts"]:
            row.pop("source_version", None)

    unreadable = _invocation(
        _reidentify(_mutate(raw, blank_parent), "imp-blank"),
        {"hazard_ids": ["H1"]},
    )
    blank_claim = replace(present, tool_call_id="imp-blank")
    excluded = _batch((present, blank_claim), with_nested, unreadable)
    assert excluded.assessments[0].verdict is LiveOpsClaimVerdict.VERIFIED
    assert excluded.assessments[1].verdict is not LiveOpsClaimVerdict.VERIFIED
    assert excluded.verified_claims == (present,)
    assert all(
        LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value not in item.codes
        for item in excluded.assessments
    )

    empty = fixtures.tables()
    zero_inv = _invocation(
        _run(search_current_impacts, "imp-empty", region="California", tables=empty),
        {"region": "California"},
    )
    positive = _run(search_current_impacts, "imp-ca", region="California")
    row = positive.data["impacts"][0]

    def corrupt_count(data):
        data["impact_count"] = 99

    corrupt = _invocation(
        _reidentify(_mutate(positive, corrupt_count), "imp-corrupt"),
        {"region": "California"},
    )
    zero = QueryResultZeroClaim(
        tool_call_id="imp-empty",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        scope=LiveOpsExecutedScope(region="California"),
    )
    witness = ConfirmedImpactClaim(
        tool_call_id="imp-corrupt",
        tool_name="search_current_impacts",
        aircraft_id=row["aircraft_id"],
        hazard_id=row["hazard_id"],
        source_version=row["source_version"],
        encounter_id=row["encounter_id"],
    )
    exact = ScopedCountClaim(
        tool_call_id="imp-corrupt",
        tool_name="search_current_impacts",
        metric=LiveOpsCountMetric.IMPACT_RECORD_COUNT,
        mode=LiveOpsCountMode.EXACT_RETURNED_SET,
        value=len(positive.data["impacts"]),
        scope=LiveOpsExecutedScope(region="California"),
    )
    observed = _observed_aircraft()
    aircraft = _invocation(
        _run(get_aircraft_operational_context, "ac-a", "a"),
        {"aircraft_id": "a"},
    )
    hidden = _batch((zero, witness, exact, observed), zero_inv, corrupt, aircraft)
    assert hidden.assessments[0].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert LiveOpsClaimRejectionCode.SCOPE_RESULT_DISAGREEMENT.value in hidden.assessments[0].codes
    assert hidden.assessments[1].verdict is LiveOpsClaimVerdict.CONTRADICTED
    assert hidden.assessments[2].verdict is LiveOpsClaimVerdict.REJECTED
    assert hidden.assessments[2].codes == (
        LiveOpsClaimRejectionCode.INCONSISTENT_AGGREGATE.value,
    )
    assert hidden.assessments[3].verdict is LiveOpsClaimVerdict.VERIFIED
    assert hidden.verified_claims == (observed,)
