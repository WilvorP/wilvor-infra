import json


def event(method, path, path_parameters=None):
    return {
        "rawPath": path,
        "pathParameters": path_parameters or {},
        "requestContext": {
            "requestId": "request-1",
            "http": {"method": method},
        },
    }


def body(response):
    return json.loads(response["body"])


def test_recommendation_detail_route(
    operational_app,
):
    expected = {
        "recommendation": {
            "recommendation_id": "rec#1"
        },
        "risk": None,
        "airportAssessments": [],
    }
    operational_app.repository.get_recommendation_detail = (
        lambda value: expected
    )

    response = operational_app.lambda_handler(
        event(
            "GET",
            "/recommendations/rec%231",
            {"recommendationId": "rec#1"},
        ),
        None,
    )

    assert response["statusCode"] == 200
    assert body(response) == expected


def test_alert_detail_not_found(
    operational_app,
):
    response = operational_app.lambda_handler(
        event("GET", "/alerts/alert%231"),
        None,
    )

    assert response["statusCode"] == 404
    assert body(response)["message"] == "Alert not found"


def test_existing_active_route_wins_over_detail(
    operational_app,
):
    operational_app.repository.list_active_recommendations = (
        lambda **_: {"items": [], "count": 0, "nextToken": None}
    )

    response = operational_app.lambda_handler(
        event("GET", "/recommendations/active"),
        None,
    )

    assert response["statusCode"] == 200
    assert body(response)["count"] == 0


def test_non_get_is_rejected(
    operational_app,
):
    response = operational_app.lambda_handler(
        event("POST", "/health"),
        None,
    )
    assert response["statusCode"] == 405
