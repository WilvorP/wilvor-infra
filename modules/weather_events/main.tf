resource "aws_cloudwatch_log_group" "weather_changed_events" {
  name              = "/aws/events/${var.name_prefix}-weather-changed"
  retention_in_days = 3

  tags = merge(var.tags, {
    Name      = "/aws/events/${var.name_prefix}-weather-changed"
    Component = "weather-processing"
  })
}

resource "aws_cloudwatch_event_rule" "weather_changed" {
  name           = "${var.name_prefix}-weather-changed"
  description    = "Captures Wilvor Weather.changed events"
  event_bus_name = var.event_bus_name

  event_pattern = jsonencode({
    source      = ["wilvor.weather"]
    detail-type = ["Weather.changed"]
  })

  tags = var.tags
}

data "aws_iam_policy_document" "weather_changed_logs_resource_policy" {
  statement {
    sid    = "AllowEventBridgeToWriteWeatherChangedLogs"
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["events.amazonaws.com"]
    }

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = [
      "${aws_cloudwatch_log_group.weather_changed_events.arn}:*",
    ]
  }
}

resource "aws_cloudwatch_log_resource_policy" "eventbridge_to_weather_changed_logs" {
  policy_name     = "${var.name_prefix}-eventbridge-weather-changed-logs"
  policy_document = data.aws_iam_policy_document.weather_changed_logs_resource_policy.json
}

resource "aws_cloudwatch_event_target" "weather_changed_logs" {
  rule           = aws_cloudwatch_event_rule.weather_changed.name
  event_bus_name = var.event_bus_name
  target_id      = "WeatherChangedCloudWatchLogs"
  arn            = aws_cloudwatch_log_group.weather_changed_events.arn
}