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
