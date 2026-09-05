class FakeTable:
    def __init__(self, items=None, query_items=None):
        self.items = items or {}
        self.query_items = query_items or []
        self.calls = []

    def get_item(self, **kwargs):
        self.calls.append(("get_item", kwargs))
        key = next(iter(kwargs["Key"].values()))
        item = self.items.get(key)
        return {"Item": item} if item else {}

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return {"Items": list(self.query_items)}


def test_recommendation_detail_assembles_links(
    operational_repository,
):
    operational_repository.RECOMMENDATIONS = FakeTable(
        {
            "rec#1": {
                "recommendation_id": "rec#1",
                "risk_id": "risk#1",
                "airport_evaluation_id": "eval#1",
            }
        }
    )
    operational_repository.RISKS = FakeTable(
        {"risk#1": {"risk_id": "risk#1"}}
    )
    operational_repository.AIRPORT_ASSESSMENTS = (
        FakeTable(
            query_items=[
                {
                    "evaluation_id": "eval#1",
                    "airport_id": "KSFO",
                }
            ]
        )
    )

    result = (
        operational_repository
        .get_recommendation_detail("rec#1")
    )

    assert result["risk"]["risk_id"] == "risk#1"
    assert result["airportAssessments"][0][
        "airport_id"
    ] == "KSFO"


def test_alert_detail_assembles_incident_chain(
    operational_repository,
):
    operational_repository.ALERTS = FakeTable(
        query_items=[
            {
                "alert_id": "alert#1",
                "recommendation_id": "rec#1",
                "risk_id": "risk#1",
            }
        ]
    )
    operational_repository.RECOMMENDATIONS = FakeTable(
        {
            "rec#1": {
                "recommendation_id": "rec#1"
            }
        }
    )
    operational_repository.RISKS = FakeTable(
        {
            "risk#1": {
                "risk_id": "risk#1",
                "encounter_id": "enc#1",
            }
        }
    )
    operational_repository.ENCOUNTERS = FakeTable(
        {
            "enc#1": {
                "encounter_id": "enc#1"
            }
        }
    )

    result = operational_repository.get_alert_detail(
        "alert#1"
    )

    assert result["alert"]["alert_id"] == "alert#1"
    assert result["encounter"]["encounter_id"] == (
        "enc#1"
    )


def test_detail_id_length_is_bounded(
    operational_repository,
):
    try:
        operational_repository.get_alert_detail(
            "a" * 257
        )
    except ValueError as exc:
        assert "too long" in str(exc)
    else:
        raise AssertionError("Expected ValueError")
