"""Offline tests for the deterministic Live Ops answer renderer.

Hand-built bundles below are structurally valid public exports. They are not
trusted verification artifacts. The integration test is the one path that
runs LO2 and LO3A.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from wilvor_ai.contracts import (
    ConfidenceLevel,
    Evidence,
    FreshnessStatus,
    SourceRecord,
    TemporalScope,
    ToolResult,
    ToolResultStatus,
)
from wilvor_ai.live_ops_answer_renderer import (
    LiveOpsRenderError,
    LiveOpsRenderErrorCode,
    render_live_ops_answer,
)
from wilvor_ai.live_ops_claims import ConfirmedImpactClaim
from wilvor_ai.live_ops_evidence_verifier import (
    LiveOpsTrustedEvidenceLedger,
    LiveOpsVerificationOutcome,
    verify_live_ops_claims,
)
from wilvor_ai.live_ops_public_contracts import (
    LIVE_OPS_ENTITY_AUTHORITY,
    LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH,
    LIVE_OPS_PUBLIC_SCHEMA_VERSION,
    LIVE_OPS_PUBLIC_SPECIALIST,
    LiveOpsAnswerOrigin,
    LiveOpsClaimDiagnostic,
    LiveOpsContradictionRecord,
    LiveOpsDiscovery,
    LiveOpsDiscoveryStatus,
    LiveOpsEntityRef,
    LiveOpsEvidenceReference,
    LiveOpsPublicAdvisoryMode,
    LiveOpsPublicClaim,
    LiveOpsPublicClaimKind,
    LiveOpsPublicClaimVerdict,
    LiveOpsPublicContradictionSubject,
    LiveOpsPublicCountMetric,
    LiveOpsPublicCountMode,
    LiveOpsPublicCurrentnessPredicate,
    LiveOpsPublicFlag,
    LiveOpsPublicLimitationCode,
    LiveOpsPublicObservedField,
    LiveOpsPublicOverlap,
    LiveOpsPublicOverlapField,
    LiveOpsPublicProjectionField,
    LiveOpsPublicRecordedFlag,
    LiveOpsPublicRenderingPosture,
    LiveOpsPublicRiskMode,
    LiveOpsPublicScope,
    LiveOpsPublicVerificationOutcome,
    LiveOpsRunResult,
    LiveOpsRunStatus,
    live_ops_reference_utc,
)
from wilvor_ai.live_ops_public_export import (
    LIVE_OPS_EXPORT_SCHEMA_VERSION,
    LiveOpsEvidenceExport,
    export_live_ops_evidence,
)
from wilvor_ai.live_ops_specialist_runtime_contracts import (
    LiveOpsDispatchResult,
    LiveOpsDispatchStatus,
    LiveOpsToolInvocation,
)
from wilvor_ai.live_ops_tool_projection import project_live_ops_tool_result
import wilvor_ai.live_ops_answer_renderer as renderer


EPOCH = 1_788_661_800
UTC = live_ops_reference_utc(EPOCH)
CORRELATION = "corr-render-1"
OPENING = (
    "These statements repeat only the verified live operations claims. "
    "Verification of this set does not mean every relevant operational fact was investigated."
)
PARTIAL = (
    "These statements repeat only the verified subset of the submitted live operations claims. "
    "Unverified submissions are omitted and are not established facts. "
    "Verification of this subset does not mean every relevant operational fact was investigated."
)
RELATIONSHIP = (
    "Each stored impact or encounter stated above is a stored record. "
    "None of them establishes physical entry into hazardous weather, severe turbulence, "
    "a current position inside the hazard, a change in operational risk, or a diversion."
)
RISK_NOTE = (
    "Where a stored risk record is stated above, that record is a persisted value, "
    "not a newly calculated risk assessment, and it does not establish that an aircraft "
    "is unsafe or that operational risk increased."
)
RECOMMENDATION_NOTE = (
    "Where stored recommendation ids are stated above, none of those records is selected. "
    "They are not a diversion, route, altitude, or ATC instruction."
)
ALERT_NOTE = (
    "Where a stored alert is stated above, it is a record, not an operational or ATC instruction."
)
RENDERER_PATH = Path(__file__).resolve().parents[2] / "functions" / "shared" / "wilvor_ai" / "live_ops_answer_renderer.py"
_POSTURE = {
    LiveOpsPublicVerificationOutcome.ALL_VERIFIED: LiveOpsPublicRenderingPosture.COMPLETE_VERIFIED_SET,
    LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED: LiveOpsPublicRenderingPosture.INCOMPLETE_VERIFIED_SUBSET,
    LiveOpsPublicVerificationOutcome.NONE_VERIFIED: LiveOpsPublicRenderingPosture.NO_VERIFIED_FACTS,
    LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED: LiveOpsPublicRenderingPosture.NOT_RENDERABLE,
}


def _ref(tool_call_id: str, tool_name: str) -> LiveOpsEvidenceReference:
    return LiveOpsEvidenceReference(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        as_of_epoch=EPOCH,
        as_of_utc=UTC,
    )


def _export(
    claims: tuple[LiveOpsPublicClaim, ...] = (),
    *,
    outcome: LiveOpsPublicVerificationOutcome = LiveOpsPublicVerificationOutcome.ALL_VERIFIED,
    limitations: tuple[str, ...] = (),
    diagnostics: tuple[LiveOpsClaimDiagnostic, ...] = (),
    discoveries: tuple[LiveOpsDiscovery, ...] = (),
    entities: tuple[LiveOpsEntityRef, ...] = (),
    contradictions: tuple[LiveOpsContradictionRecord, ...] = (),
    structural: tuple[str, ...] = (),
    names: dict[str, str] | None = None,
) -> LiveOpsEvidenceExport:
    """Build a structural export. This helper does not verify the claims."""

    resolved = dict(names or {})
    ordered: list[str] = []
    for claim in claims:
        if claim.tool_name and claim.tool_call_id:
            resolved.setdefault(claim.tool_call_id, claim.tool_name)
        for tool_call_id in claim.cited_tool_call_ids():
            if tool_call_id not in ordered:
                ordered.append(tool_call_id)
    for discovery in discoveries:
        if discovery.evidence_tool_call_id not in ordered:
            ordered.append(discovery.evidence_tool_call_id)
        resolved.setdefault(discovery.evidence_tool_call_id, "find_aircraft_by_callsign")
    return LiveOpsEvidenceExport(
        schema_version=LIVE_OPS_EXPORT_SCHEMA_VERSION,
        correlation_id=CORRELATION,
        reference_epoch=EPOCH,
        reference_utc=UTC,
        verification_outcome=outcome,
        rendering_posture=_POSTURE[outcome],
        verified_claims=claims,
        evidence_references=tuple(_ref(tool_call_id, resolved[tool_call_id]) for tool_call_id in ordered),
        diagnostics=diagnostics,
        structural_codes=structural,
        contradiction_subjects=contradictions,
        mandatory_limitations=limitations,
        discoveries=discoveries,
        verified_entity_refs=entities,
    )


def _assert_exact(export: LiveOpsEvidenceExport, sentences: list[str]) -> None:
    first = render_live_ops_answer(export)
    second = render_live_ops_answer(export)
    assert first == second
    assert [item.text for item in first.sentences] == sentences
    assert first.answer == " ".join(sentences)
    assert first.answer == first.answer.strip()
    assert len(first.answer) <= LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH
    represented = {index for sentence in first.sentences for index in sentence.claim_indexes}
    assert represented == set(range(len(export.verified_claims)))
    assert first.sentences[0].claim_indexes == ()
    assert UTC not in first.answer


def _impact(
    aircraft: str = "AC-1",
    hazard: str = "HZ-1",
    version: str = "SV-1",
    encounter: str = "EN-1",
    tool_call_id: str = "call-impacts-1",
) -> LiveOpsPublicClaim:
    return LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_IMPACT,
        tool_call_id=tool_call_id,
        tool_name="search_current_impacts",
        aircraft_id=aircraft,
        hazard_id=hazard,
        source_version=version,
        encounter_id=encounter,
    )


def _discovery(
    tool_call_id: str,
    callsign: str,
    status: LiveOpsDiscoveryStatus,
    aircraft_ids: tuple[str, ...] = (),
) -> LiveOpsDiscovery:
    return LiveOpsDiscovery(
        callsign=callsign,
        status=status,
        aircraft_ids=aircraft_ids,
        evidence_tool_call_id=tool_call_id,
        reference_epoch=EPOCH,
        identity_resolved=status is LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH,
        selection_authorized=False,
    )


def _raises(export: object, code: LiveOpsRenderErrorCode) -> LiveOpsRenderError:
    with pytest.raises(LiveOpsRenderError) as caught:
        render_live_ops_answer(export)  # type: ignore[arg-type]
    assert caught.value.code is code
    assert str(caught.value) == code.value
    assert "Traceback" not in str(caught.value)
    return caught.value


def test_renderer_accepts_only_the_export_argument():
    import inspect

    assert list(inspect.signature(render_live_ops_answer).parameters) == ["export"]
    assert "plain text" in renderer.__doc__


def test_confirmed_impact_does_not_claim_physical_entry():
    claim = _impact()
    _assert_exact(
        _export((claim,), limitations=("RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY",)),
        [
            OPENING,
            'Aircraft "AC-1" has a stored impact record with hazard "HZ-1", source version "SV-1", and encounter "EN-1".',
            RELATIONSHIP,
            "Limitations: A recorded impact, encounter, overlap status, or geometry flag is not physical entry into hazardous weather.",
        ],
    )


def test_encounter_flags_and_overlap_statuses_stay_stored_labels():
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        tool_call_id="call-encounters-1",
        tool_name="search_current_encounters",
        encounter_id="EN-ENC",
        aircraft_id="AC-ENC",
        hazard_id="HZ-ENC",
        hazard_source_version="SV-ENC",
        projection_id="PROJ-ENC",
        recorded_flags=(
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.CENTERLINE_INTERSECTS, False),
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.CORRIDOR_INTERSECTS, True),
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.EXACT_INTERSECTION_CONFIRMED, True),
            LiveOpsPublicFlag(LiveOpsPublicRecordedFlag.INSIDE_NOW, True),
        ),
        overlap_statuses=(
            LiveOpsPublicOverlap(LiveOpsPublicOverlapField.ALTITUDE_OVERLAP_STATUS, "RECORDED"),
            LiveOpsPublicOverlap(LiveOpsPublicOverlapField.GEOMETRY_OVERLAP_STATUS, "RECORDED"),
            LiveOpsPublicOverlap(LiveOpsPublicOverlapField.TIME_OVERLAP_STATUS, "NONE"),
        ),
    )
    rendered = render_live_ops_answer(_export((claim,)))
    text = rendered.answer
    assert "The stored flag inside_now is true." in text
    assert "The stored flag exact_intersection_confirmed is true." in text
    assert "The stored flag centerline_intersects is false." in text
    assert 'The stored geometry overlap status is "RECORDED".' in text
    assert "physical penetration" not in text
    assert "severe turbulence" in RELATIONSHIP and RELATIONSHIP in text
    assert "currently inside" not in text
    assert rendered.sentences[1].claim_indexes == (0,)


def test_exact_count_confirmed_subset_and_scoped_zero_stay_distinct():
    exact = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        metric=LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT,
        count_mode=LiveOpsPublicCountMode.EXACT_RETURNED_SET,
        count_value=1,
        scope=LiveOpsPublicScope(region="California", product_type="SIGMET", hazard_ids=("HZ-1", "HZ-2")),
    )
    subset = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        metric=LiveOpsPublicCountMetric.UNIQUE_AIRCRAFT_COUNT,
        count_mode=LiveOpsPublicCountMode.CONFIRMED_SUBSET,
        count_value=2,
        scope=LiveOpsPublicScope(region="California"),
    )
    network_zero = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-network-1",
        tool_name="get_observed_network_state",
        metric=LiveOpsPublicCountMetric.NETWORK_AIRCRAFT_COUNT,
        count_mode=LiveOpsPublicCountMode.EXACT_RETURNED_SET,
        count_value=0,
        scope=LiveOpsPublicScope(),
    )
    _assert_exact(
        _export((exact,)),
        [
            OPENING,
            'The executed scope with region "California", product type "SIGMET", hazard ids "HZ-1", "HZ-2" '
            "has an exact returned-set count of 1 for impact records. "
            "This count is only that returned set, not a count outside the executed scope.",
        ],
    )
    subset_answer = render_live_ops_answer(_export((subset,))).answer
    assert "confirmed-subset count of 2 for unique aircraft" in subset_answer
    assert "not the exact returned set" in subset_answer
    assert "not a complete population count" in subset_answer
    zero_answer = render_live_ops_answer(_export((network_zero,))).answer
    assert "exact returned-set count of 0 for observed-network aircraft" in zero_answer
    assert "not an authoritative query zero" in zero_answer
    assert "not a count of every aircraft or hazard" in zero_answer
    assert "every aircraft in California" not in zero_answer


def test_authoritative_query_zero_is_not_an_incomplete_search():
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
        tool_call_id="call-hazards-1",
        tool_name="search_current_hazards",
        metric=LiveOpsPublicCountMetric.HAZARD_QUERY_MATCH_COUNT,
        scope=LiveOpsPublicScope(region="California", product_type="SIGMET"),
    )
    _assert_exact(
        _export((claim,)),
        [
            OPENING,
            'The executed scope with region "California", product type "SIGMET" has an authoritative query result '
            "of zero for hazard-query matches. This zero is limited to that executed query. "
            "It is not an incomplete result, and it does not prove that matching records are absent outside that scope.",
        ],
    )


def test_context_root_absence_uses_the_tool_noun_only():
    aircraft = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        entity_id="AC-9",
    )
    hazard = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
        tool_call_id="call-hazard-1",
        tool_name="get_hazard_operational_context",
        entity_id="HZ-9",
    )
    airport = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONTEXT_ROOT_ABSENT,
        tool_call_id="call-airport-1",
        tool_name="get_airport_operational_context",
        entity_id="KSFO",
    )
    aircraft_text = render_live_ops_answer(_export((aircraft,))).answer
    assert 'No operational context root was found for aircraft "AC-9".' in aircraft_text
    assert "does not prove that the aircraft is absent from every other source" in aircraft_text
    assert "hazard" not in aircraft_text.split("Limitations:")[0]
    hazard_text = render_live_ops_answer(_export((hazard,))).answer
    assert 'for hazard "HZ-9"' in hazard_text
    airport_text = render_live_ops_answer(_export((airport,))).answer
    assert 'for airport "KSFO"' in airport_text


def test_region_intersection_does_not_place_an_aircraft_or_invent_sigmet():
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        tool_call_id="call-hazards-1",
        tool_name="search_current_hazards",
        hazard_id="HZ-SIG-1",
        source_version="SV-9",
        region_code="US-CA",
        region_name="California",
        region_query="California",
    )
    answer = render_live_ops_answer(_export((claim,))).answer
    assert 'recorded region intersection with "US-CA" ("California")' in answer
    assert "does not place an aircraft inside the region or inside the hazard" in answer
    assert "does not classify the hazard product" in answer
    assert "SIGMET" not in answer


def test_retained_not_current_false_is_not_affirmative_currentness():
    retained = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=-122.3,
        retained_not_current=True,
    )
    not_retained = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=37.6,
        retained_not_current=False,
    )
    retained_answer = render_live_ops_answer(_export((retained,))).answer
    assert "This observed value is marked as retained and not current." in retained_answer
    assert "marked current" not in retained_answer
    false_answer = render_live_ops_answer(_export((not_retained,))).answer
    assert "This observed value is not marked as retained/not-current by this flag." in false_answer
    assert "marked current" not in false_answer
    assert "is current" not in false_answer
    assert "AIRCRAFT_IS_CURRENT" not in false_answer
    current = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CURRENTNESS,
        tool_call_id="call-current-1",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsPublicCurrentnessPredicate.AIRCRAFT_IS_CURRENT,
        currentness_value=True,
        aircraft_id="AC-1",
    )
    combined = render_live_ops_answer(_export((not_retained, current)))
    observed_sentence = combined.sentences[2].text
    current_sentence = combined.sentences[3].text
    assert "not marked as retained/not-current" in observed_sentence
    assert "is true" not in observed_sentence
    assert current_sentence == 'The recorded aircraft currentness predicate for aircraft "AC-1" is true.'
    assert combined.sentences[2].claim_indexes == (0,)
    assert combined.sentences[3].claim_indexes == (1,)


def test_float_repr_preserves_values_that_shorter_formats_change():
    sum_value = 0.1 + 0.2
    altered = 1.2300000000000002
    assert format(sum_value, ".16g") != repr(sum_value)
    assert format(altered, ".16g") != repr(altered)
    assert format(-0.0, ".16g") != repr(-0.0)
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=sum_value,
        retained_not_current=False,
    )
    longitude = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-2",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-2",
        observed_field=LiveOpsPublicObservedField.LONGITUDE,
        observed_value=-0.0,
        retained_not_current=False,
    )
    speed = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-3",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-3",
        observed_field=LiveOpsPublicObservedField.GROUND_SPEED_KT,
        observed_value=altered,
        retained_not_current=False,
    )
    answer = render_live_ops_answer(_export((claim, longitude, speed))).answer
    assert "stored observed latitude 0.30000000000000004 degrees" in answer
    assert "latitude 0.3 degrees" not in answer
    assert "stored observed longitude -0.0 degrees" in answer
    assert "longitude -0 degrees" not in answer
    assert "stored observed ground_speed_kt 1.2300000000000002 knots" in answer
    assert "1.23 knots" not in answer


def test_observed_units_and_projection_are_not_the_same_state():
    observed = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.POSITION_TIME_UTC,
        observed_value="2024-01-02T03:04:05Z",
        retained_not_current=False,
    )
    altitude = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.BARO_ALTITUDE_FT,
        observed_value=35000,
        retained_not_current=False,
    )
    track = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.TRACK_DEG,
        observed_value=270,
        retained_not_current=False,
    )
    on_ground = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.ON_GROUND,
        observed_value=False,
        retained_not_current=False,
    )
    projection = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        projection_id="PROJ-1",
        projection_field=LiveOpsPublicProjectionField.GENERATED_AT_UTC,
        projection_value="2025-10-15T00:00:00Z",
    )
    horizon = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        projection_id="PROJ-9",
        projection_field=LiveOpsPublicProjectionField.PROJECTION_HORIZON_MIN,
        projection_value=30,
    )
    answer = render_live_ops_answer(_export((observed, altitude, track, on_ground, projection, horizon))).answer
    assert 'stored observed position_time_utc "2024-01-02T03:04:05Z"' in answer
    assert "not the batch reference clock" in answer
    assert "stored observed baro_altitude_ft 35000 feet" in answer
    assert "stored observed track_deg 270 degrees" in answer
    assert "stored observed on_ground false" in answer
    assert "on_ground False" not in answer
    assert "stored projection \"PROJ-1\" generated_at_utc" in answer
    assert "not an observed aircraft state" in answer
    assert "projection_horizon_min 30 minutes" in answer
    assert UTC not in answer
    assert answer.index("position_time_utc") < answer.index("generated_at_utc")


def test_all_currentness_predicates_keep_their_own_meaning():
    claims = (
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c1", tool_name="get_aircraft_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.AIRCRAFT_IS_CURRENT, currentness_value=True, aircraft_id="AC-1"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c2", tool_name="get_aircraft_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.AIRCRAFT_IS_CURRENT, currentness_value=False, aircraft_id="AC-2"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c3", tool_name="search_current_hazards", predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT, currentness_value=True, hazard_id="HZ-1", source_version="SV-1"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c4", tool_name="get_hazard_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT, currentness_value=False, hazard_id="HZ-2"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c5", tool_name="get_hazard_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_LIFECYCLE_ACTIVE, currentness_value=True, hazard_id="HZ-3"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c6", tool_name="get_aircraft_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.PROJECTION_IS_CURRENT, currentness_value=True, aircraft_id="AC-1", projection_id="PROJ-1"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c7", tool_name="get_aircraft_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.PROJECTION_IS_CURRENT, currentness_value=False, aircraft_id="AC-3"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c8", tool_name="search_current_encounters", predicate=LiveOpsPublicCurrentnessPredicate.ENCOUNTER_IS_CURRENT, currentness_value=True, encounter_id="EN-1", aircraft_id="AC-1", hazard_id="HZ-1"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c9", tool_name="get_airport_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.AIRPORT_IS_CURRENT, currentness_value=False, airport_id="KSFO"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c10", tool_name="get_hazard_operational_context", predicate=LiveOpsPublicCurrentnessPredicate.CURRENT_IMPACTS_EVALUATED, currentness_value=True, hazard_id="HZ-1"),
        LiveOpsPublicClaim(kind=LiveOpsPublicClaimKind.CURRENTNESS, tool_call_id="c11", tool_name="search_current_hazards", predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_SELECTION_NOT_CURRENT, currentness_value=False, hazard_id="HZ-1", source_version="SV-1"),
    )
    answer = render_live_ops_answer(_export(claims)).answer
    expected = [
        'The recorded aircraft currentness predicate for aircraft "AC-1" is true.',
        'The recorded aircraft currentness predicate for aircraft "AC-2" is false.',
        'The recorded hazard currentness predicate for hazard "HZ-1" at source version "SV-1" is true. This is not a source-freshness determination.',
        'The recorded hazard currentness predicate for hazard "HZ-2" is false. This is not a source-freshness determination.',
        'The recorded hazard lifecycle-active predicate for hazard "HZ-3" is true. This is not hazard currentness.',
        'The recorded projection currentness predicate for aircraft "AC-1" and projection "PROJ-1" is true. This is not aircraft currentness.',
        'The recorded projection currentness predicate for aircraft "AC-3" is false. This is not aircraft currentness.',
        'The recorded encounter currentness predicate for encounter "EN-1", aircraft "AC-1", and hazard "HZ-1" is true.',
        'The recorded airport currentness predicate for airport "KSFO" is false.',
        'The recorded current-impacts-evaluated predicate for hazard "HZ-1" is true. This is not a count and it is not a source-freshness determination.',
        'The recorded predicate HAZARD_SELECTION_NOT_CURRENT for hazard "HZ-1" at source version "SV-1" is false. A false value is not rewritten as hazard currentness.',
    ]
    for sentence in expected:
        assert sentence in answer
    assert answer.index(expected[0]) < answer.index(expected[-1])
    freshness = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-9",
        observed_field=LiveOpsPublicObservedField.FRESHNESS_STATUS,
        observed_value="FRESH",
        retained_not_current=False,
    )
    fresh_answer = render_live_ops_answer(
        _export((freshness,), limitations=("FRESHNESS_NOT_ESTABLISHED",))
    ).answer
    assert 'stored observed freshness_status "FRESH"' in fresh_answer
    assert "not a new freshness determination" in fresh_answer
    assert "Source freshness is not established." in fresh_answer
    assert "source data is fresh" not in fresh_answer


def test_risk_recommendation_and_alert_do_not_become_decisions():
    present = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        risk_mode=LiveOpsPublicRiskMode.PRESENT,
        encounter_id="EN-1",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        risk_id="RISK-1",
        risk_level="HIGH",
        risk_score=2,
    )
    absent = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-impacts-2",
        tool_name="search_current_impacts",
        risk_mode=LiveOpsPublicRiskMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="EN-2",
        aircraft_id="AC-2",
        hazard_id="HZ-2",
    )
    recommendations = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.PRESENT_SET,
        encounter_id="EN-1",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        recommendation_ids=("REC-1", "REC-2"),
    )
    absent_recommendations = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RECOMMENDATION_SET,
        tool_call_id="call-impacts-2",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="EN-2",
        aircraft_id="AC-2",
        hazard_id="HZ-2",
    )
    alert = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.ALERT_RECORD,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.PRESENT_SET,
        encounter_id="EN-1",
        aircraft_id="AC-1",
        hazard_id="HZ-1",
        alert_id="AL-1",
        alert_state="ACTIVE",
        alert_type="IMPACT",
    )
    alert_without_type = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.ALERT_RECORD,
        tool_call_id="call-impacts-3",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.PRESENT_SET,
        encounter_id="EN-3",
        aircraft_id="AC-3",
        hazard_id="HZ-3",
        alert_id="AL-3",
        alert_state="ACTIVE",
    )
    absent_alert = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.ALERT_RECORD,
        tool_call_id="call-impacts-2",
        tool_name="search_current_impacts",
        advisory_mode=LiveOpsPublicAdvisoryMode.ABSENT_FROM_OBSERVED_CANDIDATES,
        encounter_id="EN-2",
        aircraft_id="AC-2",
        hazard_id="HZ-2",
    )
    present_answer = render_live_ops_answer(_export((present,))).answer
    assert 'stored risk record "RISK-1" with stored level "HIGH" and stored score 2.' in present_answer
    assert RISK_NOTE in present_answer
    assert "newly calculated" in present_answer
    absent_answer = render_live_ops_answer(_export((absent,))).answer
    assert "This is not a LOW risk level." in absent_answer
    assert "stored score" not in absent_answer
    assert "LOW" not in absent_answer.replace("This is not a LOW risk level.", "")
    recommendation_answer = render_live_ops_answer(_export((recommendations, absent_recommendations))).answer
    assert 'stored recommendation ids "REC-1", "REC-2".' in recommendation_answer
    assert RECOMMENDATION_NOTE in recommendation_answer
    assert "No stored recommendation was present in the observed candidates" in recommendation_answer
    assert "winning" not in recommendation_answer
    assert "divert" not in recommendation_answer.casefold()
    alert_answer = render_live_ops_answer(_export((alert, alert_without_type, absent_alert))).answer
    assert 'stored alert "AL-1" in state "ACTIVE" with stored type "IMPACT".' in alert_answer
    assert 'stored alert "AL-3" in state "ACTIVE".' in alert_answer
    assert "with stored type" not in alert_answer.split("AL-3", 1)[1].split(".", 1)[0]
    assert ALERT_NOTE in alert_answer
    assert "ATC instruction" in alert_answer
    assert "No stored alert was present in the observed candidates" in alert_answer


def test_truncation_and_contradiction_do_not_select_hidden_rows_or_winners():
    truncation = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.MODEL_VISIBLE_TRUNCATION,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        list_name="impacts",
        total_count=30,
    )
    hazard = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
        tool_call_id_a="call-hazards-a",
        tool_call_id_b="call-hazards-b",
        subject=LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION,
        hazard_id="HZ-1",
    )
    aircraft = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
        tool_call_id_a="call-aircraft-a",
        tool_call_id_b="call-aircraft-b",
        subject=LiveOpsPublicContradictionSubject.AIRCRAFT_CURRENTNESS,
        aircraft_id="AC-1",
    )
    encounter = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CROSS_TOOL_CONTRADICTION,
        tool_call_id_a="call-encounter-a",
        tool_call_id_b="call-encounter-b",
        subject=LiveOpsPublicContradictionSubject.ENCOUNTER_LINEAGE,
        encounter_id="EN-1",
    )
    truncation_answer = render_live_ops_answer(_export((truncation,))).answer
    assert 'The model-visible list "impacts" is truncated. The stored total count is 30.' in truncation_answer
    assert "omitted from the model-visible list are not described" in truncation_answer
    names = {
        "call-hazards-a": "search_current_hazards",
        "call-hazards-b": "search_current_hazards",
        "call-aircraft-a": "get_aircraft_operational_context",
        "call-aircraft-b": "get_aircraft_operational_context",
        "call-encounter-a": "search_current_encounters",
        "call-encounter-b": "search_current_encounters",
    }
    hazard_answer = render_live_ops_answer(_export((hazard,), names=names)).answer
    assert 'disagree on the source version recorded for hazard "HZ-1". Neither version is selected.' in hazard_answer
    aircraft_answer = render_live_ops_answer(_export((aircraft,), names=names)).answer
    assert "Neither value is selected." in aircraft_answer
    encounter_answer = render_live_ops_answer(_export((encounter,), names=names)).answer
    assert "Neither record is selected." in encounter_answer
    subject_only = _export(
        (_impact(),),
        contradictions=(
            LiveOpsContradictionRecord(
                subject=LiveOpsPublicContradictionSubject.HAZARD_SOURCE_VERSION,
                identity=(("hazard_id", "HZ-ONLY-IN-SUBJECT"),),
                tool_call_ids=("call-impacts-1",),
            ),
        ),
    )
    assert "HZ-ONLY-IN-SUBJECT" not in render_live_ops_answer(subject_only).answer


def test_matching_encounter_groups_without_reordering_or_inheriting_version():
    impact = _impact(aircraft="AC-441", hazard="HZ-SIG-1", version="SV-9", encounter="EN-77")
    risk = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        risk_mode=LiveOpsPublicRiskMode.PRESENT,
        encounter_id="EN-77",
        aircraft_id="AC-441",
        hazard_id="HZ-SIG-1",
        risk_id="RISK-77",
        risk_level="HIGH",
        risk_score=2,
    )
    other = _impact(aircraft="AC-882", hazard="HZ-SIG-1", version="SV-9", encounter="EN-88", tool_call_id="call-impacts-2")
    rendered = render_live_ops_answer(_export((impact, risk, other)))
    texts = [sentence.text for sentence in rendered.sentences]
    assert texts[1] == 'For encounter "EN-77", aircraft "AC-441", and hazard "HZ-SIG-1":'
    assert rendered.sentences[1].claim_indexes == (0, 1)
    assert "SV-9" in texts[2]
    assert "SV-9" not in texts[3]
    assert texts[4].startswith('Aircraft "AC-882"')
    assert texts.index(texts[2]) < texts.index(texts[3]) < texts.index(texts[4])


def test_version_mismatch_and_noncontiguous_claims_are_not_grouped():
    first = _impact(version="SV-A", encounter="EN-1")
    second = _impact(version="SV-B", encounter="EN-1", tool_call_id="call-impacts-2")
    mismatched = render_live_ops_answer(_export((first, second))).answer
    assert "For encounter" not in mismatched
    assert mismatched.index("SV-A") < mismatched.index("SV-B")
    count = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.SCOPED_COUNT,
        tool_call_id="call-count-1",
        tool_name="search_current_impacts",
        metric=LiveOpsPublicCountMetric.IMPACT_RECORD_COUNT,
        count_mode=LiveOpsPublicCountMode.EXACT_RETURNED_SET,
        count_value=2,
        scope=LiveOpsPublicScope(aircraft_id="AC-1"),
    )
    left = _impact(encounter="EN-1", tool_call_id="call-left")
    right = _impact(encounter="EN-1", tool_call_id="call-right")
    ordered = render_live_ops_answer(_export((left, count, right))).answer
    count_text = "exact returned-set count of 2"
    impact_text = 'encounter "EN-1".'
    before, after = ordered.split(count_text)
    assert impact_text in before and impact_text in after
    assert "For encounter" not in ordered
    other_encounter = _impact(encounter="EN-2", tool_call_id="call-other")
    separate = render_live_ops_answer(_export((_impact(encounter="EN-1"), other_encounter))).answer
    assert separate.index("EN-1") < separate.index("EN-2")
    assert "For encounter" not in separate


def test_missing_version_does_not_join_a_hazard_and_aircraft_id_does_not_join_events():
    intersection = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        tool_call_id="call-hazards-1",
        tool_name="search_current_hazards",
        hazard_id="HZ-1",
        source_version="SV-1",
        region_code="US-CA",
        region_name="California",
        region_query="California",
    )
    unversioned = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CURRENTNESS,
        tool_call_id="call-hazard-current",
        tool_name="get_hazard_operational_context",
        predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT,
        currentness_value=True,
        hazard_id="HZ-1",
    )
    separated = render_live_ops_answer(_export((intersection, unversioned)))
    assert "For hazard" not in separated.answer
    assert "SV-1" not in separated.sentences[2].text
    versioned = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CURRENTNESS,
        tool_call_id="call-hazard-version",
        tool_name="search_current_hazards",
        predicate=LiveOpsPublicCurrentnessPredicate.HAZARD_IS_CURRENT,
        currentness_value=True,
        hazard_id="HZ-1",
        source_version="SV-1",
    )
    joined = render_live_ops_answer(_export((intersection, versioned)))
    assert joined.sentences[1].text == 'For hazard "HZ-1" at source version "SV-1":'
    observed = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        observed_field=LiveOpsPublicObservedField.HAS_POSITION,
        observed_value=True,
        retained_not_current=False,
    )
    mixed = render_live_ops_answer(_export((observed, _impact())))
    assert "For aircraft" not in mixed.answer
    assert "For encounter" not in mixed.answer
    assert mixed.sentences[1].text.startswith("Aircraft")
    assert "has_position true" in mixed.sentences[1].text
    projection = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_PROJECTION_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-1",
        projection_id="PROJ-1",
        projection_field=LiveOpsPublicProjectionField.PROJECTION_STATUS,
        projection_value="READY",
    )
    unscoped_projection = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CURRENTNESS,
        tool_call_id="call-projection-current",
        tool_name="get_aircraft_operational_context",
        predicate=LiveOpsPublicCurrentnessPredicate.PROJECTION_IS_CURRENT,
        currentness_value=True,
        aircraft_id="AC-1",
    )
    projection_answer = render_live_ops_answer(_export((projection, unscoped_projection))).answer
    assert "For aircraft" not in projection_answer
    assert "PROJ-1" not in projection_answer.split("is true")[-1]


def test_california_findings_do_not_invent_sigmet_membership_or_missing_risk():
    region = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.HAZARD_REGION_INTERSECTION,
        tool_call_id="call-hazards-1",
        tool_name="search_current_hazards",
        hazard_id="HZ-SIG-1",
        source_version="SV-9",
        region_code="US-CA",
        region_name="California",
        region_query="California",
    )
    first = _impact(aircraft="AC-441", hazard="HZ-SIG-1", version="SV-9", encounter="EN-77")
    risk = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.RISK_STATE,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        risk_mode=LiveOpsPublicRiskMode.PRESENT,
        encounter_id="EN-77",
        aircraft_id="AC-441",
        hazard_id="HZ-SIG-1",
        risk_id="RISK-77",
        risk_level="HIGH",
        risk_score=2,
    )
    second = _impact(aircraft="AC-882", hazard="HZ-SIG-1", version="SV-9", encounter="EN-88", tool_call_id="call-impacts-2")
    rendered = render_live_ops_answer(
        _export(
            (region, first, risk, second),
            limitations=(
                "FRESHNESS_NOT_ESTABLISHED",
                "NO_SNAPSHOT_LIMITATION",
                "RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY",
            ),
        )
    )
    answer = rendered.answer
    assert "SIGMET" not in answer
    assert "AC-882" in answer
    assert answer.index("AC-441") < answer.index("RISK-77") < answer.index("AC-882")
    assert answer.count('stored risk record "RISK-77"') == 1
    assert "physical entry into hazardous weather" in answer
    assert "Source freshness is not established." in answer
    assert len(answer) < 8192
    assert rendered.sentences[-3].claim_indexes == ()
    assert rendered.sentences[-3].text.startswith("Limitations:")


def test_partial_verification_omits_rejected_diagnostics():
    encounter = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CONFIRMED_ENCOUNTER,
        tool_call_id="call-encounters-1",
        tool_name="search_current_encounters",
        encounter_id="EN-ENC",
        aircraft_id="AC-ENC",
        hazard_id="HZ-ENC",
        hazard_source_version="SV-ENC",
    )
    diagnostic = LiveOpsClaimDiagnostic(
        claim_kind=LiveOpsPublicClaimKind.RISK_STATE,
        verdict=LiveOpsPublicClaimVerdict.REJECTED,
        codes=("SECRET_REJECTED_CODE",),
        tool_call_ids=("call-secret-hidden",),
    )
    rendered = render_live_ops_answer(
        _export(
            (encounter,),
            outcome=LiveOpsPublicVerificationOutcome.PARTIALLY_VERIFIED,
            diagnostics=(diagnostic,),
            limitations=("RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY",),
        )
    )
    assert rendered.sentences[0].text == PARTIAL
    assert "call-secret-hidden" not in rendered.answer
    assert "SECRET_REJECTED_CODE" not in rendered.answer
    assert "AC-ENC" in rendered.answer
    assert "only the verified subset" in rendered.answer


def test_none_verified_and_not_authorized_are_not_renderable():
    empty = _export(outcome=LiveOpsPublicVerificationOutcome.NONE_VERIFIED)
    denied = _export(
        outcome=LiveOpsPublicVerificationOutcome.NOT_AUTHORIZED,
        structural=("EMPTY_LEDGER",),
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
    )
    _raises(empty, LiveOpsRenderErrorCode.NOT_RENDERABLE)
    _raises(denied, LiveOpsRenderErrorCode.NOT_RENDERABLE)
    _raises(object(), LiveOpsRenderErrorCode.INVALID_EXPORT)


def test_callsign_discoveries_do_not_select_or_merge_searches():
    unique_claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-1",
        tool_name="find_aircraft_by_callsign",
        callsign="UAL123",
        aircraft_ids=("AC-1",),
    )
    entity = LiveOpsEntityRef(
        entity_kind="AIRCRAFT",
        entity_id="AC-1",
        callsign="UAL123",
        authority=LIVE_OPS_ENTITY_AUTHORITY,
        source_specialist=LIVE_OPS_PUBLIC_SPECIALIST,
        public_schema_version=LIVE_OPS_PUBLIC_SCHEMA_VERSION,
        correlation_id=CORRELATION,
        reference_epoch=EPOCH,
        reference_utc=UTC,
        evidence_tool_call_id="call-find-1",
    )
    unique = render_live_ops_answer(
        _export(
            (unique_claim,),
            discoveries=(_discovery("call-find-1", "UAL123", LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH, ("AC-1",)),),
            entities=(entity,),
        )
    )
    assert unique.sentences[1].text == (
        'Callsign search "UAL123" (tool call "call-find-1") returned one current supported aircraft id, "AC-1". '
        "This is the supported match for this search. It is not a permanent identity guarantee, and no other aircraft is selected."
    )
    assert unique.sentences[1].claim_indexes == (0,)
    assert unique.answer.count("returned one current supported") == 1
    assert LIVE_OPS_ENTITY_AUTHORITY not in unique.answer
    multiple_claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-2",
        tool_name="find_aircraft_by_callsign",
        callsign="UAL123",
        aircraft_ids=("AC-1", "AC-2"),
    )
    multiple = render_live_ops_answer(
        _export(
            (multiple_claim,),
            discoveries=(_discovery("call-find-2", "UAL123", LiveOpsDiscoveryStatus.MULTIPLE_SUPPORTED_MATCHES, ("AC-1", "AC-2")),),
        )
    ).answer
    assert 'multiple current supported aircraft ids: "AC-1", "AC-2". No aircraft is selected.' in multiple
    zero_claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.QUERY_RESULT_ZERO,
        tool_call_id="call-find-zero",
        tool_name="find_aircraft_by_callsign",
        metric=LiveOpsPublicCountMetric.CALLSIGN_MATCH_COUNT,
        scope=LiveOpsPublicScope(callsign="NONE0"),
    )
    zero = render_live_ops_answer(
        _export(
            (zero_claim,),
            discoveries=(_discovery("call-find-zero", "NONE0", LiveOpsDiscoveryStatus.NO_SUPPORTED_MATCH),),
        )
    ).answer
    assert (
        'Callsign search "NONE0" (tool call "call-find-zero") returned an authoritative query result of zero '
        "current supported matches. This does not prove that an aircraft with this callsign does not exist."
    ) in zero
    assert zero.count("authoritative query result of zero") == 1
    repeated_a = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-a",
        tool_name="find_aircraft_by_callsign",
        callsign="UAL123",
        aircraft_ids=("AC-1",),
    )
    repeated_b = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.CALLSIGN_MATCH_SET,
        tool_call_id="call-find-b",
        tool_name="find_aircraft_by_callsign",
        callsign="UAL123",
        aircraft_ids=("AC-9",),
    )
    repeated = render_live_ops_answer(
        _export(
            (repeated_a, repeated_b),
            discoveries=(
                _discovery("call-find-a", "UAL123", LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH, ("AC-1",)),
                _discovery("call-find-b", "UAL123", LiveOpsDiscoveryStatus.UNIQUE_SUPPORTED_MATCH, ("AC-9",)),
            ),
        )
    )
    assert repeated.sentences[1].claim_indexes == (0,)
    assert repeated.sentences[2].claim_indexes == (1,)
    assert "call-find-a" in repeated.sentences[1].text
    assert "call-find-b" in repeated.sentences[2].text
    assert "multiple current supported" not in repeated.answer
    incomplete = render_live_ops_answer(
        _export(
            (_impact(),),
            discoveries=(_discovery("call-find-open", "N123", LiveOpsDiscoveryStatus.INCOMPLETE),),
        )
    )
    notice = [sentence for sentence in incomplete.sentences if "did not produce a verified match set" in sentence.text]
    assert len(notice) == 1
    assert notice[0].claim_indexes == ()
    assert "AC-NOT-EXPORTED" not in incomplete.answer
    assert "N123" in incomplete.answer


def test_known_and_unknown_limitations_remain_visible():
    coverage = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.COVERAGE_LIMITATION,
        tool_call_id="call-impacts-1",
        tool_name="search_current_impacts",
        limitation_code=LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED,
    )
    rendered = render_live_ops_answer(_export((coverage,), limitations=("FRESHNESS_NOT_ESTABLISHED",)))
    assert rendered.sentences[1].claim_indexes == (0,)
    assert rendered.sentences[1].text.startswith("Limitations: Source freshness is not established.")
    codes = tuple(code.value for code in LiveOpsPublicLimitationCode)
    sentences = {
        LiveOpsPublicLimitationCode.FRESHNESS_NOT_ESTABLISHED.value: "Source freshness is not established.",
        LiveOpsPublicLimitationCode.NO_SNAPSHOT_LIMITATION.value: "not a single transactional snapshot.",
        LiveOpsPublicLimitationCode.GEOSPATIAL_UNEVALUATED_PRESENT.value: "not spatially evaluated",
        LiveOpsPublicLimitationCode.OPERATIONALLY_UNEVALUATED_PRESENT.value: "not operationally evaluated",
        LiveOpsPublicLimitationCode.REGION_UNRESOLVED.value: "No region membership is established",
        LiveOpsPublicLimitationCode.LINEAGE_MISMATCH_PRESENT.value: "not reconciled into one record",
        LiveOpsPublicLimitationCode.ALERT_EVIDENCE_IDENTITY_UNAVAILABLE.value: "No alert identity is established",
        LiveOpsPublicLimitationCode.QUERY_SELECTION_REQUIRED.value: "worldwide impact scan was not performed",
        LiveOpsPublicLimitationCode.ENCOUNTER_SELECTION_REQUIRED.value: "unscoped encounter scan was not performed",
        LiveOpsPublicLimitationCode.CALLSIGN_REQUIRED.value: "requires a non-empty callsign",
        LiveOpsPublicLimitationCode.CALLSIGN_CASE_LIMITATION.value: "not case-insensitive global callsign coverage",
        LiveOpsPublicLimitationCode.EVENTUAL_SCAN_LIMITATION.value: "may not yet be visible",
        LiveOpsPublicLimitationCode.RECOMMENDATION_ABSENCE_LIMITATION.value: "Absence of a recommendation is limited",
        LiveOpsPublicLimitationCode.ALERT_ABSENCE_LIMITATION.value: "Absence of an alert is limited",
        LiveOpsPublicLimitationCode.OBSERVED_INVENTORY_NOT_A_COMPLETE_SNAPSHOT.value: "not a complete snapshot",
        LiveOpsPublicLimitationCode.SPATIAL_EVALUATION_UNSUPPORTED.value: "not an intersection",
        LiveOpsPublicLimitationCode.RECORDED_OVERLAP_NOT_PHYSICAL_ENTRY.value: "not physical entry into hazardous weather",
    }
    all_codes = render_live_ops_answer(_export((_impact(),), limitations=codes)).answer
    position = 0
    for code in codes:
        fragment = sentences[code]
        found = all_codes.index(fragment)
        assert found >= position
        position = found
    unknown = render_live_ops_answer(
        _export((_impact(),), limitations=("FUTURE_LIMITATION_CODE", "NEW_<TAG>"))
    ).answer
    assert 'Limitation "FUTURE\\_LIMITATION\\_CODE" applies. This renderer does not expand that code and does not treat it as harmless.' in unknown
    assert 'Limitation "NEW\\_\\<TAG\\>" applies.' in unknown
    assert "harmless" in unknown
    assert unknown.index("FUTURE\\_LIMITATION\\_CODE") < unknown.index("NEW\\_\\<TAG\\>")


def test_adversarial_identifiers_remain_data():
    bad = "AC\n\"Divert now.\" \\ **go** <script>\u202eEND"
    claim = _impact(aircraft=bad)
    rendered = render_live_ops_answer(_export((claim,)))
    answer = rendered.answer
    expected_token = '"AC\\u000a\\"Divert now.\\" \\\\ \\*\\*go\\*\\* \\<script\\>\\u202eEND"'
    assert expected_token in answer
    assert "\n" not in answer
    assert "**" not in answer
    assert "<script>" not in answer
    assert "\u202e" not in answer
    assert "\\u202e" in answer
    assert answer.count("Divert now.") == 1
    assert "Divert now." in expected_token.replace('\\"', '"')


def test_ordinary_answers_fit_and_overlong_answers_fail_closed():
    short = render_live_ops_answer(_export((_impact(), _impact(encounter="EN-2", tool_call_id="call-2"))))
    assert len(short.answer) <= 8192
    assert short.answer.count("stored impact record") == 2
    assert RELATIONSHIP in short.answer
    assert short.answer.count(RELATIONSHIP) == 1
    long_id = "A" * 180
    claims = tuple(
        _impact(
            aircraft=long_id + str(index),
            hazard=long_id,
            version=long_id,
            encounter=long_id + str(index),
            tool_call_id=f"call-{index}",
        )
        for index in range(12)
    )
    error = _raises(_export(claims), LiveOpsRenderErrorCode.ANSWER_LENGTH_EXCEEDED)
    assert not hasattr(error, "answer")
    assert long_id not in str(error)


def test_closed_errors_do_not_leak_values(monkeypatch):
    claim = LiveOpsPublicClaim(
        kind=LiveOpsPublicClaimKind.AIRCRAFT_OBSERVED_STATE,
        tool_call_id="call-aircraft-1",
        tool_name="get_aircraft_operational_context",
        aircraft_id="AC-SECRET",
        observed_field=LiveOpsPublicObservedField.LATITUDE,
        observed_value=1.0,
        retained_not_current=False,
    )
    export = _export((claim,))
    object.__setattr__(claim, "observed_value", float("nan"))
    error = _raises(export, LiveOpsRenderErrorCode.UNSAFE_VALUE)
    assert "AC-SECRET" not in str(error)
    assert "nan" not in str(error).casefold()
    broken = _export((_impact(),))
    object.__setattr__(broken, "rendering_posture", LiveOpsPublicRenderingPosture.NOT_RENDERABLE)
    _raises(broken, LiveOpsRenderErrorCode.INVALID_EXPORT)
    monkeypatch.delitem(renderer._RENDERERS, LiveOpsPublicClaimKind.CONFIRMED_IMPACT)
    _raises(_export((_impact(),)), LiveOpsRenderErrorCode.UNSUPPORTED_CLAIM)
    monkeypatch.undo()
    monkeypatch.setattr(renderer, "_groups", lambda claims: ())
    _raises(_export((_impact(),)), LiveOpsRenderErrorCode.TEMPLATE_FAILURE)


def test_rendered_answer_satisfies_public_result_construction():
    export = _export(
        (_impact(),),
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
    )
    rendered = render_live_ops_answer(export)
    result = LiveOpsRunResult(
        correlation_id=export.correlation_id,
        execution_status=LiveOpsRunStatus.COMPLETED,
        verification_outcome=export.verification_outcome,
        rendering_posture=export.rendering_posture,
        answer_origin=LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER,
        answer=rendered.answer,
        reference_epoch=export.reference_epoch,
        reference_utc=export.reference_utc,
        verified_claims=export.verified_claims,
        evidence_references=export.evidence_references,
        mandatory_limitations=export.mandatory_limitations,
    )
    assert result.answer_origin is LiveOpsAnswerOrigin.DETERMINISTIC_RENDERER
    assert result.answer == rendered.answer
    assert len(result.answer) <= LIVE_OPS_PUBLIC_ANSWER_MAX_LENGTH
    assert "LiveOpsRunResult(" not in RENDERER_PATH.read_text(encoding="utf-8")


def test_real_verifier_export_and_renderer_chain_uses_only_verified_claims():
    now = EPOCH
    correlation = "corr-export-1"
    evidence = Evidence(
        source="synthetic-export",
        source_records=(
            SourceRecord(
                record_id="rec-export-1",
                source_version="SV-1",
                event_timestamp_utc="2020-01-01T00:00:00Z",
            ),
        ),
        query_timestamp_utc="2021-06-01T00:00:00Z",
        freshness_status=FreshnessStatus.UNKNOWN,
        confidence=ConfidenceLevel.LOW,
        limitations=("FRESHNESS_NOT_ESTABLISHED",),
        tool_call_id="call-impacts-1",
        temporal_scope=TemporalScope.CURRENT,
    )
    data = {
        "as_of_epoch": now,
        "impact_count": 1,
        "impacts": [
            {
                "encounter_id": "EN-1",
                "aircraft_id": "AC-1",
                "hazard_id": "HZ-1",
                "source_version": "SV-1",
                "aircraft_is_current": True,
                "encounter_context": {
                    "encounter_is_current": True,
                    "encounter": {
                        "encounter_id": "EN-1",
                        "aircraft_id": "AC-1",
                        "hazard_id": "HZ-1",
                        "hazard_source_version": "SV-1",
                    },
                },
            }
        ],
    }
    result = ToolResult(
        tool_name="search_current_impacts",
        tool_call_id="call-impacts-1",
        status=ToolResultStatus.SUCCESS,
        temporal_scope=TemporalScope.CURRENT,
        data=data,
        evidence=(evidence,),
        as_of_utc="2019-05-01T00:00:00Z",
        limitations=(
            "DynamoDB reads are independently observed; this composition is not "
            "a transactional cross-table snapshot.",
        ),
        correlation_id=correlation,
    )
    invocation = LiveOpsToolInvocation(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        requested_arguments={"hazard_ids": ["HZ-1"]},
        executed_arguments={"hazard_ids": ["HZ-1"]},
        canonical_key="search_current_impacts:call-impacts-1",
        raw_tool_result=result,
        model_projection=project_live_ops_tool_result(result),
    )
    ledger = LiveOpsTrustedEvidenceLedger(
        correlation_id=correlation,
        now_epoch=now,
        dispatches=(
            LiveOpsDispatchResult(
                status=LiveOpsDispatchStatus.COMPLETED,
                invocations=(invocation,),
                planned_tool_call_ids=("call-impacts-1",),
            ),
        ),
    )
    submitted = (
        ConfirmedImpactClaim(
            tool_call_id="call-impacts-1",
            tool_name="search_current_impacts",
            aircraft_id="AC-1",
            hazard_id="HZ-1",
            source_version="SV-1",
            encounter_id="EN-1",
        ),
    )
    verified = verify_live_ops_claims(submitted, ledger)
    assert verified.outcome is LiveOpsVerificationOutcome.ALL_VERIFIED
    exported = export_live_ops_evidence(verified, ledger, submitted)
    rendered = render_live_ops_answer(exported)
    assert rendered.answer == (
        OPENING
        + ' Aircraft "AC-1" has a stored impact record with hazard "HZ-1", source version "SV-1", and encounter "EN-1". '
        + RELATIONSHIP
        + " Limitations: Source freshness is not established. A stored currentness value, a stored freshness status, "
        "and the batch reference clock do not substitute for source freshness. "
        "These records were read independently and are not a single transactional snapshot. "
        "A recorded impact, encounter, overlap status, or geometry flag is not physical entry into hazardous weather."
    )
    assert rendered.sentences[1].claim_indexes == (0,)
    assert live_ops_reference_utc(now) not in rendered.answer
    assert "2019-05-01T00:00:00Z" not in rendered.answer
    assert "2020-01-01T00:00:00Z" not in rendered.answer


def test_renderer_imports_stay_closed():
    tree = ast.parse(RENDERER_PATH.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported == {
        "__future__",
        "dataclasses",
        "enum",
        "math",
        "unicodedata",
        "wilvor_ai.live_ops_public_contracts",
        "wilvor_ai.live_ops_public_export",
    }
    source = RENDERER_PATH.read_text(encoding="utf-8")
    forbidden = (
        "anthropic",
        "boto3",
        "botocore",
        "langgraph",
        "openai",
        "wilvor_operational",
        "live_ops_adapter",
        "live_ops_evidence_verifier",
        "live_ops_mapping",
        "time.time",
        "datetime.now",
    )
    for name in forbidden:
        assert name not in source
