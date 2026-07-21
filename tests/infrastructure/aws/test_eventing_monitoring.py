from __future__ import annotations

import json

import pytest


pytestmark = pytest.mark.infrastructure


SCHEDULE_OUTPUTS = [
    (
        "opensky_poller_schedule_name",
        "opensky_poller_schedule_state",
    ),
    (
        "sigmet_poller_schedule_name",
        "sigmet_poller_schedule_state",
    ),
    (
        "metar_poller_schedule_name",
        "metar_poller_schedule_state",
    ),
    (
        "taf_poller_schedule_name",
        "taf_poller_schedule_state",
    ),
]

DASHBOARD_OUTPUTS = [
    "sigmet_dashboard_name",
    "metar_dashboard_name",
    "taf_dashboard_name",
    "weather_events_dashboard_name",
]

LAMBDA_OUTPUTS = [
    "opensky_poller_lambda_name",
    "aircraft_raw_processor_lambda_name",
    "aircraft_current_state_writer_lambda_name",
    "sigmet_poller_function_name",
    "sigmet_processor_lambda_name",
    "metar_poller_function_name",
    "metar_processor_lambda_name",
    "taf_poller_function_name",
    "taf_processor_lambda_name",
]


@pytest.mark.parametrize(
    ("name_output", "state_output"),
    SCHEDULE_OUTPUTS,
)
def test_development_poller_rule_is_disabled(
    name_output,
    state_output,
    terraform_outputs,
    aws_client,
):
    rule_name = terraform_outputs[name_output]

    assert terraform_outputs[state_output] == "DISABLED"

    rule = aws_client("events").describe_rule(Name=rule_name)
    assert rule["Name"] == rule_name
    assert rule["State"] == "DISABLED"


@pytest.mark.parametrize("output_name", DASHBOARD_OUTPUTS)
def test_cloudwatch_dashboard_exists_and_has_widgets(
    output_name,
    terraform_outputs,
    aws_client,
):
    dashboard_name = terraform_outputs[output_name]
    response = aws_client("cloudwatch").get_dashboard(
        DashboardName=dashboard_name
    )
    body = json.loads(response["DashboardBody"])

    assert body.get("widgets"), (
        f"Dashboard {dashboard_name} contains no widgets."
    )


def test_weather_changed_log_group_exists(
    terraform_outputs,
    aws_client,
):
    expected_name = terraform_outputs[
        "weather_changed_log_group_name"
    ]

    groups = aws_client("logs").describe_log_groups(
        logGroupNamePrefix=expected_name,
        limit=50,
    )["logGroups"]

    assert any(
        group["logGroupName"] == expected_name
        for group in groups
    )


@pytest.mark.parametrize("output_name", LAMBDA_OUTPUTS)
def test_lambda_log_group_exists(
    output_name,
    terraform_outputs,
    aws_client,
):
    function_name = terraform_outputs[output_name]
    expected_name = f"/aws/lambda/{function_name}"

    groups = aws_client("logs").describe_log_groups(
        logGroupNamePrefix=expected_name,
        limit=50,
    )["logGroups"]

    assert any(
        group["logGroupName"] == expected_name
        for group in groups
    )


def test_cloudwatch_alarms_exist_for_dev_stack(
    name_prefix,
    aws_client,
):
    alarms = aws_client("cloudwatch").describe_alarms(
        AlarmNamePrefix=name_prefix,
        MaxRecords=100,
    )["MetricAlarms"]

    assert alarms, (
        f"No CloudWatch alarms were found with prefix {name_prefix!r}."
    )
