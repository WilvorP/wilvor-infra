from pathlib import Path

import pytest


pytestmark = pytest.mark.infrastructure

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / "modules" / "ai_copilot"


def text(name):
    return (MODULE / name).read_text(
        encoding="utf-8"
    )


def test_ai_routes_and_cost_controls_are_declared():
    api = text("api.tf")
    for route in (
        "POST /ai/chat",
        "POST /ai/summaries/network",
        "POST /ai/aircraft/{aircraftId}/explain",
        "POST /ai/airports/{airportId}/summarize",
        "POST /ai/recommendations/{recommendationId}/explain",
        "POST /ai/alerts/{alertId}/incident-summary",
        "GET /ai/insights/{subjectType}/{subjectId}",
    ):
        assert route in api
    assert "reserved_concurrent_executions" in api
    assert "throttling_burst_limit" in api
    assert "AI_MAX_TOOL_ROUNDS" in api


def test_ai_iam_has_no_operational_table_access():
    api = text("api.tf")
    assert "bedrock:InvokeModel" in api
    assert "ReadWriteAiInsightsOnly" in api
    assert "aws_dynamodb_table.insights.arn" in api
    for operational_reference in (
        "module.risk.",
        "module.recommendations.",
        "module.active_alerts.",
        "module.aircraft_foundation.",
        "aws_dynamodb_table.risk_results",
        "aws_dynamodb_table.active_alerts",
    ):
        assert operational_reference not in api
    assert "dynamodb:Scan" not in api
    assert "dynamodb:DeleteItem" not in api


def test_insights_table_is_encrypted_and_expiring():
    table = text("table.tf")
    assert 'hash_key  = "subject_key"' in table
    assert 'range_key = "sort_key"' in table
    assert 'attribute_name = "expires_at_epoch"' in table
    assert "server_side_encryption" in table
    assert "point_in_time_recovery" in table


def test_dev_proactive_ai_defaults_are_disabled():
    tfvars = (
        ROOT / "envs" / "dev" / "terraform.tfvars"
    ).read_text(encoding="utf-8")
    assert "enable_ai_event_triggers" in tfvars
    assert "enable_ai_network_summary_schedule" in tfvars
    disabled_lines = [
        line
        for line in tfvars.splitlines()
        if line.strip().startswith("enable_ai_")
    ]
    assert disabled_lines
    assert all(
        line.strip().endswith("false")
        for line in disabled_lines
    )


def test_event_delivery_is_bounded_and_recoverable():
    events = text("events.tf")
    assert "maximum_retry_attempts       = 2" in events
    assert "maximum_event_age_in_seconds = 3600" in events
    assert "dead_letter_config" in events
    assert "aws_sqs_queue.event_dlq.arn" in events
    assert (
        "aws_lambda_function_event_invoke_config"
        in events
    )
