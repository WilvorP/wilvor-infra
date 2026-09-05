import threading

from context import ContextBuilders, material_context


class FakeClient:
    def overview(self):
        return {
            "generatedAt": "2026-01-01T00:00:00Z",
            "aircraft": {"activeCount": 2},
            "topRisks": [],
        }

    def freshness(self):
        return {
            "generatedAt": "2026-01-01T00:00:00Z",
            "sources": {
                "opensky": {
                    "status": "STALE",
                    "latestAt": "2026-01-01T00:00:00Z",
                    "ageSeconds": 300,
                },
                "sigmet": {"status": "AVAILABLE"},
            },
        }

    def system_health(self):
        return {
            "generatedAt": "2026-01-01T00:00:00Z",
            "status": "DEGRADED",
        }

    def aircraft(self, aircraft_id):
        return {
            "aircraft": {
                "aircraft_id": aircraft_id,
                "callsign": None,
                "baro_altitude_ft": 30000,
                "position_time_utc": (
                    "2026-01-01T00:00:00Z"
                ),
            },
            "projection": None,
            "projectionPoints": [],
            "recentEncounters": [],
            "recentRisks": [
                {
                    "risk_id": "risk#1",
                    "risk_level": "HIGH",
                    "limitations": [
                        "Aircraft-to-hazard altitude overlap is unknown."
                    ],
                }
            ],
            "recentRecommendations": [],
            "recentAlerts": [],
        }

    def airport(self, airport_id):
        return {
            "airport": {
                "airport_id": airport_id,
                "weather_risk_level": "MEDIUM",
                "known_limitations": [],
            },
            "metar": None,
            "taf": None,
            "tafForecastPeriods": [],
            "recentAssessments": [],
        }

    def recommendation(self, recommendation_id):
        return {
            "recommendation": {
                "recommendation_id": recommendation_id,
                "primary_action_type": (
                    "EVALUATE_DIVERSION"
                ),
                "limitations": ["Fuel state is unavailable."],
                "candidate_airport_summaries": [
                    {
                        "airport_id": "KSFO",
                        "rank": 1,
                        "total_airport_score": 75,
                    }
                ],
            },
            "risk": {
                "risk_id": "risk#1",
                "risk_level": "HIGH",
                "limitations": [],
            },
            "airportAssessments": [],
        }

    def alert(self, alert_id):
        return {
            "alert": {
                "alert_id": alert_id,
                "alert_state": "NEW",
                "updated_at_utc": (
                    "2026-01-01T00:00:00Z"
                ),
            },
            "recommendation": None,
            "risk": None,
            "encounter": None,
        }


def test_builds_all_context_types():
    builders = ContextBuilders(FakeClient())

    network = builders.build_network_context()
    aircraft = builders.build_aircraft_context("a67928")
    airport = builders.build_airport_context("KSFO")
    recommendation = (
        builders.build_recommendation_context("rec#1")
    )
    alert = builders.build_alert_context("alert#1")

    assert network["subject"]["type"] == "NETWORK"
    assert aircraft["projection"] is None
    assert (
        "Current projection unavailable."
        in aircraft["limitations"]
    )
    assert airport["metar"] is None
    assert recommendation["recommendation"][
        "primary_action_type"
    ] == "EVALUATE_DIVERSION"
    assert alert["alert"]["alert_state"] == "NEW"


def test_stale_freshness_is_explicit_and_evidenced():
    context = ContextBuilders(
        FakeClient()
    ).build_aircraft_context("a67928")

    assert (
        "OPENSKY data freshness is STALE."
        in context["dataFreshnessWarnings"]
    )
    assert (
        "Requested aircraft state freshness is UNKNOWN."
        in context["dataFreshnessWarnings"]
    )
    ids = {
        item["evidenceId"]
        for item in context["evidenceCatalog"]
    }
    assert "freshness.opensky.status" in ids
    assert any(
        item["value"] == "HIGH"
        for item in context["evidenceCatalog"]
    )


def test_fingerprint_material_ignores_volatile_metadata():
    value = {
        "generatedAt": "one",
        "freshness": {
            "ageSeconds": 1,
            "status": "FRESH",
        },
        "evidenceCatalog": [{"evidenceId": "x"}],
    }
    assert material_context(value) == {
        "freshness": {"status": "FRESH"}
    }


def test_network_context_fanout_is_bounded_to_two():
    class TrackingClient(FakeClient):
        def __init__(self):
            self.lock = threading.Lock()
            self.release = threading.Event()
            self.active = 0
            self.maximum_active = 0
            self.entered = 0

        def _tracked(self, callback):
            with self.lock:
                self.active += 1
                self.entered += 1
                self.maximum_active = max(
                    self.maximum_active,
                    self.active,
                )
                if self.entered == 3:
                    self.release.set()
            self.release.wait(timeout=0.05)
            try:
                return callback()
            finally:
                with self.lock:
                    self.active -= 1

        def overview(self):
            return self._tracked(super().overview)

        def freshness(self):
            return self._tracked(super().freshness)

        def system_health(self):
            return self._tracked(
                super().system_health
            )

    client = TrackingClient()
    context = ContextBuilders(
        client
    ).build_network_context()

    assert context["subject"]["type"] == "NETWORK"
    assert client.entered == 3
    assert client.maximum_active == 2
