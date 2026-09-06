# 2026-09-06T02:30:00Z
NOW = 1_788_661_800
FUTURE = "2026-09-06T04:00:00Z"
PAST = "2026-09-06T01:00:00Z"


def _install_overview_tables(repo, monkeypatch):
    monkeypatch.setattr(
        repo,
        "_current_encounter_snapshot",
        lambda: {
            "now_epoch": NOW,
            "items": [
                {
                    "encounter_id": "enc-current",
                    "aircraft_id": "abc123",
                },
                {
                    "encounter_id": "enc-current-high",
                    "aircraft_id": "def456",
                },
            ],
            "projection_ids": {
                "abc123": "proj-1",
                "def456": "proj-2",
            },
            "hazard_versions": {"hazard-1": "v1"},
        },
    )
    monkeypatch.setattr(repo, "_scan_count", lambda *args, **kwargs: 12)
    monkeypatch.setattr(repo, "_query_count", lambda *args, **kwargs: 3)
    monkeypatch.setattr(repo, "_now_iso", lambda: "2026-09-06T02:30:00Z")
    monkeypatch.setattr(repo.time, "time", lambda: NOW)

    scan_calls = []

    def scan_all(table, **kwargs):
        scan_calls.append(table)

        if table is repo.RISKS:
            return [
                {
                    "risk_id": "risk-older-high",
                    "encounter_id": "enc-current",
                    "aircraft_id": "abc123",
                    "hazard_id": "hazard-1",
                    "hazard_type": "SIGMET",
                    "risk_level": "HIGH",
                    "risk_score": 0.9,
                    "confidence": 0.8,
                    "generated_at_epoch": NOW - 100,
                    "generated_at_utc": "2026-09-06T02:28:20Z",
                    "valid_until_utc": FUTURE,
                },
                {
                    "risk_id": "risk-current",
                    "encounter_id": "enc-current",
                    "aircraft_id": "abc123",
                    "hazard_id": "hazard-1",
                    "hazard_type": "SIGMET",
                    "risk_level": "MEDIUM",
                    "risk_score": 0.4,
                    "confidence": 0.7,
                    "generated_at_epoch": NOW,
                    "generated_at_utc": "2026-09-06T02:30:00Z",
                    "valid_until_utc": FUTURE,
                },
                {
                    "risk_id": "risk-expired",
                    "encounter_id": "enc-current",
                    "aircraft_id": "abc123",
                    "hazard_id": "hazard-1",
                    "risk_level": "HIGH",
                    "risk_score": 0.95,
                    "generated_at_epoch": NOW + 50,
                    "valid_until_utc": PAST,
                },
                {
                    "risk_id": "risk-high",
                    "encounter_id": "enc-current-high",
                    "aircraft_id": "def456",
                    "hazard_id": "hazard-1",
                    "hazard_type": "SIGMET",
                    "risk_level": "HIGH",
                    "risk_score": 0.88,
                    "confidence": 0.9,
                    "generated_at_epoch": NOW,
                    "generated_at_utc": "2026-09-06T02:30:00Z",
                    "valid_until_utc": FUTURE,
                },
                {
                    "risk_id": "risk-old-encounter",
                    "encounter_id": "enc-old",
                    "aircraft_id": "old999",
                    "risk_level": "HIGH",
                    "risk_score": 0.99,
                    "generated_at_epoch": NOW,
                    "valid_until_utc": FUTURE,
                },
            ]

        if table is repo.RECOMMENDATIONS:
            return [
                {
                    "recommendation_id": "rec-current",
                    "risk_id": "risk-current",
                    "recommendation_status": "ACTIVE",
                    "aircraft_id": "abc123",
                    "hazard_id": "hazard-1",
                    "risk_level": "MEDIUM",
                    "risk_score": 0.4,
                    "confidence": 0.7,
                    "primary_action_type": "MONITOR",
                    "preferred_airport_id": "KSEA",
                    "preferred_airport_score": 0.6,
                    "valid_until_utc": FUTURE,
                    "created_at_utc": "2026-09-06T02:29:00Z",
                    "created_at_epoch": NOW - 60,
                },
                {
                    "recommendation_id": "rec-high",
                    "risk_id": "risk-high",
                    "recommendation_status": "ACTIVE",
                    "aircraft_id": "def456",
                    "hazard_id": "hazard-1",
                    "risk_level": "HIGH",
                    "risk_score": 0.88,
                    "confidence": 0.9,
                    "primary_action_type": "DIVERT",
                    "preferred_airport_id": "KPDX",
                    "preferred_airport_score": 0.8,
                    "valid_until_utc": FUTURE,
                    "created_at_utc": "2026-09-06T02:30:00Z",
                    "created_at_epoch": NOW,
                },
                {
                    "recommendation_id": "rec-retained",
                    "risk_id": "risk-old-encounter",
                    "recommendation_status": "ACTIVE",
                    "valid_until_utc": FUTURE,
                    "created_at_epoch": NOW + 10,
                },
            ]

        if table is repo.ALERTS:
            return [
                {
                    "alert_id": "alert-current",
                    "risk_id": "risk-current",
                    "recommendation_id": "rec-current",
                    "alert_state": "NEW",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": NOW,
                },
                {
                    "alert_id": "alert-retained",
                    "risk_id": "risk-old-encounter",
                    "recommendation_id": "rec-retained",
                    "alert_state": "MONITORING",
                    "valid_until_utc": FUTURE,
                    "updated_at_epoch": NOW + 10,
                },
            ]

        if table is repo.AIRPORTS:
            return [
                {
                    "airport_id": "KSEA",
                    "station_id": "KSEA",
                    "weather_risk_level": "HIGH",
                    "weather_impact_status": "WEATHER_IMPACTED",
                    "updated_at_epoch": NOW,
                },
                {
                    "airport_id": "KPDX",
                    "station_id": "KPDX",
                    "weather_risk_level": "LOW",
                    "weather_impact_status": "NONE",
                    "updated_at_epoch": NOW,
                },
            ]

        return []

    monkeypatch.setattr(repo, "_scan_all", scan_all)
    return scan_calls


def test_overview_keeps_current_set_counts_and_latest_rows(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    _install_overview_tables(repo, monkeypatch)

    overview = repo.get_overview()

    assert overview["aircraft"]["activeCount"] == 12
    assert overview["hazards"]["activeCount"] == 3
    assert overview["encounters"]["activeCount"] == 2
    assert overview["encounters"]["riskEvaluatedCount"] == 2
    assert overview["encounters"]["highRiskCount"] == 1
    assert overview["encounters"]["mediumRiskCount"] == 1
    assert overview["encounters"]["lowRiskCount"] == 0
    assert overview["encounters"]["riskCounts"] == {
        "HIGH": 1,
        "MEDIUM": 1,
    }

    assert overview["recommendations"]["activeCount"] == 3
    assert overview["recommendations"]["currentCount"] == 2
    assert overview["recommendations"]["latest"] == [
        {
            "recommendationId": "rec-high",
            "aircraftId": "def456",
            "hazardId": "hazard-1",
            "riskLevel": "HIGH",
            "riskScore": 0.88,
            "confidence": 0.9,
            "action": "DIVERT",
            "preferredAirportId": "KPDX",
            "preferredAirportScore": 0.8,
            "validUntilUtc": FUTURE,
            "createdAtUtc": "2026-09-06T02:30:00Z",
        },
        {
            "recommendationId": "rec-current",
            "aircraftId": "abc123",
            "hazardId": "hazard-1",
            "riskLevel": "MEDIUM",
            "riskScore": 0.4,
            "confidence": 0.7,
            "action": "MONITOR",
            "preferredAirportId": "KSEA",
            "preferredAirportScore": 0.6,
            "validUntilUtc": FUTURE,
            "createdAtUtc": "2026-09-06T02:29:00Z",
        },
    ]

    assert overview["alerts"]["activeCount"] == 2
    assert overview["alerts"]["currentCount"] == 1
    assert overview["alerts"]["byState"] == {
        "NEW": 1,
        "MONITORING": 1,
    }

    assert overview["airports"]["currentCount"] == 2
    assert overview["airports"]["weatherImpactedCount"] == 1

    top_risk_ids = [item["risk_id"] for item in overview["topRisks"]]
    assert top_risk_ids == ["risk-high", "risk-current"]


def test_overview_shares_one_scan_per_table_with_listings(
    operational_repository,
    monkeypatch,
):
    repo = operational_repository
    scan_calls = _install_overview_tables(repo, monkeypatch)

    overview = repo.get_overview()

    assert scan_calls.count(repo.RISKS) == 1
    assert scan_calls.count(repo.RECOMMENDATIONS) == 1
    assert scan_calls.count(repo.ALERTS) == 1
    assert scan_calls.count(repo.AIRPORTS) == 1

    listing = repo.list_active_recommendations(limit=5)
    alerts = repo.list_active_alerts(limit=5)

    assert scan_calls.count(repo.RISKS) == 1
    assert listing["count"] == 2
    assert listing["items"][0]["recommendation_id"] == "rec-high"
    assert alerts["count"] == 1
    assert alerts["items"][0]["alert_id"] == "alert-current"
    assert overview["recommendations"]["currentCount"] == listing["count"]
    assert overview["alerts"]["currentCount"] == alerts["count"]
