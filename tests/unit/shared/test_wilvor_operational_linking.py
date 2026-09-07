"""Direct tests for shared operational relationship composition."""

from __future__ import annotations

import inspect
from pathlib import Path

from wilvor_operational import current_set
from wilvor_operational import linking


REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_DIR = REPO_ROOT / "functions" / "shared" / "wilvor_operational"
NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"


def test_risks_link_by_encounter_id_and_missing_risk_stays_missing():
    risks = current_set.index_latest_current_risks(
        [
            {
                "risk_id": "risk-b",
                "encounter_id": "enc-b",
                "generated_at_epoch": NOW,
            }
        ],
        current_encounter_ids={"enc-a", "enc-b"},
        now_epoch=NOW,
    )

    assert "enc-a" not in risks
    assert risks["enc-b"]["risk_id"] == "risk-b"
    assert risks.get("enc-a") is None
    assert risks.get("enc-a") is not False


def test_two_current_recommendations_remain_two():
    recs = linking.select_current_recommendations(
        [
            {
                "recommendation_id": "rec-1",
                "risk_id": "risk-1",
                "recommendation_status": "ACTIVE",
                "valid_until_utc": FUTURE,
            },
            {
                "recommendation_id": "rec-2",
                "risk_id": "risk-1",
                "recommendation_status": "ACTIVE",
                "valid_until_utc": FUTURE,
            },
        ],
        current_risk_ids={"risk-1"},
        now_epoch=NOW,
    )

    assert [item["recommendation_id"] for item in recs] == ["rec-1", "rec-2"]
    assert linking.recommendation_link_for(recs).state is linking.LinkState.PRESENT


def test_or_lineage_keeps_all_current_alerts():
    alerts = linking.select_current_alerts(
        [
            {
                "alert_id": "alert-risk",
                "risk_id": "risk-1",
                "alert_state": "NEW",
                "valid_until_utc": FUTURE,
            },
            {
                "alert_id": "alert-rec",
                "recommendation_id": "rec-1",
                "alert_state": "MONITORING",
                "valid_until_utc": FUTURE,
            },
        ],
        current_risk_ids={"risk-1"},
        current_recommendation_ids={"rec-1"},
        now_epoch=NOW,
    )

    assert [item["alert_id"] for item in alerts] == ["alert-risk", "alert-rec"]


def test_copied_risk_projection_metadata_is_not_revalidated():
    risk, link = linking.hydrate_risk(
        selected_risk_id="risk-1",
        selected_encounter_id="enc-1",
        hydrated={
            "risk_id": "risk-1",
            "encounter_id": "enc-1",
            "projection_id": "proj-copied-other",
            "hazard_source_version": "v-other",
            "generated_at_epoch": NOW,
        },
        current_encounter_ids={"enc-1"},
        now_epoch=NOW,
    )

    assert risk["projection_id"] == "proj-copied-other"
    assert link.state is linking.LinkState.PRESENT


def test_equal_risk_epoch_tie_preserves_first_input():
    first = {
        "risk_id": "risk-first",
        "encounter_id": "enc-1",
        "generated_at_epoch": NOW,
    }
    second = {
        "risk_id": "risk-second",
        "encounter_id": "enc-1",
        "generated_at_epoch": NOW,
    }
    indexed = current_set.index_latest_current_risks(
        [first, second],
        current_encounter_ids={"enc-1"},
        now_epoch=NOW,
    )

    assert indexed["enc-1"] is first


def test_source_values_are_preserved_on_composed_context():
    encounter = {"encounter_id": "enc-1", "aircraft_id": "abc123"}
    risk = {"risk_id": "risk-1", "encounter_id": "enc-1", "risk_score": 7}
    context = linking.compose_encounter_operational_context(
        encounter=encounter,
        encounter_is_current=True,
        encounter_link=linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.EXACT,
        ),
        hazard=None,
        hazard_link=linking.Link(
            state=linking.LinkState.HYDRATION_MISSING,
            kind=linking.LinkKind.VERSIONED,
        ),
        risk=risk,
        risk_link=linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.EXACT,
        ),
        recommendations=(),
        recommendation_link=linking.recommendation_link_for(()),
        alerts=(),
        alert_link=linking.alert_link_for(()),
    )

    assert context.encounter is encounter
    assert context.risk is risk
    assert context.risk["risk_score"] == 7
    assert context.recommendation_link.state is (
        linking.LinkState.ABSENT_FROM_CURRENT_CANDIDATES
    )


def test_hazard_version_mismatch_does_not_substitute_observed_row():
    live = {
        "hazard_id": "hazard-1",
        "source_version": "v2",
        "status": "ACTIVE",
    }
    hazard, link = linking.hydrate_hazard(
        selected_hazard_id="hazard-1",
        selected_source_version="v1",
        hydrated=live,
    )

    assert hazard is None
    assert link.state is linking.LinkState.HYDRATION_VERSION_MISMATCH
    assert ("source_version", "v1") in link.selected_identity
    assert ("source_version", "v2") in link.observed_identity


def test_scan_observation_is_eventual_and_not_a_completeness_boolean():
    observation = linking.scan_observation("scan_risk_candidates")

    assert observation.coverage is linking.Coverage.FULL_SCAN
    assert observation.consistency is linking.Consistency.EVENTUAL
    assert observation.limit is None
    assert linking.EVENTUAL_SCAN_LIMITATION in observation.limitations
    assert not hasattr(observation, "complete_for_current_membership")


def test_query_observation_is_full_query_eventual_and_not_complete():
    observation = linking.query_observation("query_encounter_candidates_by_hazard")

    assert observation.coverage is linking.Coverage.FULL_QUERY
    assert observation.consistency is linking.Consistency.EVENTUAL
    assert observation.limit is None
    assert linking.EVENTUAL_SCAN_LIMITATION in observation.limitations
    assert not hasattr(observation, "complete_for_current_membership")
    assert not hasattr(observation, "complete")
    assert not hasattr(observation, "is_complete")


def test_hydrate_aircraft_present_retained_missing_and_mismatch():
    current = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW + 100,
    }
    retained = {
        "aircraft_id": "abc123",
        "expires_at_epoch": NOW,
    }
    other = {
        "aircraft_id": "other",
        "expires_at_epoch": NOW + 100,
    }

    present, present_link = linking.hydrate_aircraft(
        selected_aircraft_id="ABC123",
        hydrated=current,
        now_epoch=NOW,
    )
    stale, stale_link = linking.hydrate_aircraft(
        selected_aircraft_id="abc123",
        hydrated=retained,
        now_epoch=NOW,
    )
    missing, missing_link = linking.hydrate_aircraft(
        selected_aircraft_id="abc123",
        hydrated=None,
        now_epoch=NOW,
    )
    mismatch, mismatch_link = linking.hydrate_aircraft(
        selected_aircraft_id="abc123",
        hydrated=other,
        now_epoch=NOW,
    )

    assert present is current
    assert present_link.state is linking.LinkState.PRESENT
    assert stale is retained
    assert stale_link.state is linking.LinkState.HYDRATION_NO_LONGER_CURRENT
    assert missing is None
    assert missing_link.state is linking.LinkState.HYDRATION_MISSING
    assert mismatch is None
    assert mismatch_link.state is linking.LinkState.HYDRATION_IDENTITY_MISMATCH


def test_hazard_context_types_wrap_encounter_without_duplicating_chain():
    encounter = linking.compose_encounter_operational_context(
        encounter={"encounter_id": "enc-1"},
        encounter_is_current=True,
        encounter_link=linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.EXACT,
        ),
        hazard=None,
        hazard_link=linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.VERSIONED,
        ),
        risk=None,
        risk_link=linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.EXACT,
        ),
        recommendations=(),
        recommendation_link=linking.recommendation_link_for(()),
        alerts=(),
        alert_link=linking.alert_link_for(()),
    )
    impact = linking.HazardImpactContext(
        aircraft={"aircraft_id": "abc123"},
        aircraft_is_current=True,
        aircraft_link=linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.EXACT,
        ),
        projection=None,
        projection_is_current=False,
        projection_link=linking.Link(
            state=linking.LinkState.MISSING,
            kind=linking.LinkKind.CURRENT_DEPENDENT,
        ),
        encounter=encounter,
    )
    context = linking.HazardOperationalContext(
        hazard={"hazard_id": "hazard-1", "source_version": "v1"},
        hazard_is_lifecycle_active=True,
        hazard_is_current=True,
        source_version="v1",
        impacts=(impact,),
        hazard_version_link=linking.Link(
            state=linking.LinkState.PRESENT,
            kind=linking.LinkKind.VERSIONED,
        ),
        retrieval=(),
    )

    assert context.impacts[0].encounter is encounter
    assert not hasattr(impact, "risk")
    assert not hasattr(impact, "recommendations")
    assert not hasattr(impact, "alerts")
    assert encounter.risk is None


def test_latest_weather_source_link_match_mismatch_absent_and_missing():
    latest = {"station_id": "KSEA", "metar_version": "M1"}

    matched, present = linking.latest_weather_source_link(
        selected_station_id="KSEA",
        selected_source_version="M1",
        version_name="metar_version",
        hydrated=latest,
        observed_version="M1",
    )
    mismatched_row = {"station_id": "KSEA", "metar_version": "M2"}
    kept, mismatch = linking.latest_weather_source_link(
        selected_station_id="KSEA",
        selected_source_version="M1",
        version_name="metar_version",
        hydrated=mismatched_row,
        observed_version="M2",
    )
    unproven_row = {"station_id": "KSEA", "metar_version": "M2"}
    retained, unproven = linking.latest_weather_source_link(
        selected_station_id="KSEA",
        selected_source_version="",
        version_name="metar_version",
        hydrated=unproven_row,
        observed_version="M2",
    )
    missing, missing_link = linking.latest_weather_source_link(
        selected_station_id="KSEA",
        selected_source_version="M1",
        version_name="metar_version",
        hydrated=None,
        observed_version="",
    )
    identity_row = {"station_id": "KPDX", "metar_version": "M1"}
    rejected, identity = linking.latest_weather_source_link(
        selected_station_id="KSEA",
        selected_source_version="M1",
        version_name="metar_version",
        hydrated=identity_row,
        observed_version="M1",
    )

    assert matched is latest
    assert present.state is linking.LinkState.PRESENT
    assert present.kind is linking.LinkKind.VERSIONED
    assert kept is mismatched_row
    assert mismatch.state is linking.LinkState.HYDRATION_VERSION_MISMATCH
    assert retained is unproven_row
    assert unproven.state is linking.LinkState.MISSING
    assert unproven.state is not linking.LinkState.PRESENT
    assert missing is None
    assert missing_link.state is linking.LinkState.HYDRATION_MISSING
    assert rejected is None
    assert identity.state is linking.LinkState.HYDRATION_IDENTITY_MISMATCH


def test_taf_periods_link_names_parent_version_key():
    present = linking.taf_periods_link_for(
        ({"period_key": "p1"},),
        taf_version_key="KSEA#T1",
    )
    empty = linking.taf_periods_link_for((), taf_version_key="KSEA#T1")
    missing = linking.taf_periods_link_for((), taf_version_key="")

    assert present.state is linking.LinkState.PRESENT
    assert ("taf_version_key", "KSEA#T1") in present.selected_identity
    assert ("taf_version_key", "KSEA#T1") in present.observed_identity
    assert empty.state is linking.LinkState.ABSENT_FROM_CURRENT_CANDIDATES
    assert ("taf_version_key", "KSEA#T1") in empty.selected_identity
    assert missing.state is linking.LinkState.MISSING
    assert missing.kind is linking.LinkKind.VERSIONED


def test_consistent_query_observation_is_full_query_without_eventual_limitation():
    observation = linking.query_observation(
        "query_taf_period_rows_for_version",
        linking.TAF_PERIOD_VERSION_LIMITATION,
        consistency=linking.Consistency.CONSISTENT,
    )

    assert observation.coverage is linking.Coverage.FULL_QUERY
    assert observation.consistency is linking.Consistency.CONSISTENT
    assert observation.limit is None
    assert linking.EVENTUAL_SCAN_LIMITATION not in observation.limitations
    assert linking.TAF_PERIOD_VERSION_LIMITATION in observation.limitations
    assert not hasattr(observation, "complete")


def test_linking_source_has_no_io_or_forbidden_imports():
    source = inspect.getsource(linking)
    text = (PACKAGE_DIR / "linking.py").read_text(encoding="utf-8")

    assert "time.time" not in source
    assert "datetime.now" not in source
    assert "boto3" not in text
    assert "wilvor_ai" not in text
    assert "operational_api" not in text
    assert "os.environ" not in text
    assert "readers" not in linking.__dict__
    assert "access" not in linking.__dict__
