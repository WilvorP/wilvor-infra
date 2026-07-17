locals {
  weather_events_metric_namespace = "Wilvor/WeatherEvents"

  weather_events_alarm_dimensions = merge(
    {
      RuleName = aws_cloudwatch_event_rule.weather_changed.name
    },
    var.event_bus_name == "default" ? {} : {
      EventBusName = var.event_bus_name
    }
  )

  weather_events_dashboard_dimensions = (
    var.event_bus_name == "default"
    ? [
      "RuleName",
      aws_cloudwatch_event_rule.weather_changed.name
    ]
    : [
      "EventBusName",
      var.event_bus_name,
      "RuleName",
      aws_cloudwatch_event_rule.weather_changed.name
    ]
  )
}

# ============================================================
# EVENTBRIDGE FAILURE ALARM
# ============================================================

resource "aws_cloudwatch_metric_alarm" "weather_changed_failed_invocations" {
  alarm_name          = "${var.name_prefix}-weather-changed-failed-invocations"
  alarm_description   = "Weather.changed EventBridge rule had failed target invocations."
  namespace           = "AWS/Events"
  metric_name         = "FailedInvocations"
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  evaluation_periods  = 1
  period              = 60
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"

  dimensions = local.weather_events_alarm_dimensions
}

# ============================================================
# PRODUCT-TYPE METRIC
#
# Reads Weather.changed events from the shared CloudWatch Logs
# target and publishes one custom metric with ProductType as a
# dimension.
#
# Resulting metric series:
# - ProductType = METAR
# - ProductType = TAF
# - ProductType = SIGMET
# ============================================================

resource "aws_cloudwatch_log_metric_filter" "weather_changed_by_product" {
  name           = "${var.name_prefix}-weather-changed-by-product"
  log_group_name = aws_cloudwatch_log_group.weather_changed_events.name

  pattern = "{ $.detail.product_type = \"*\" }"

  metric_transformation {
    name      = "WeatherChangedEvents"
    namespace = local.weather_events_metric_namespace
    value     = "1"
    unit      = "Count"

    dimensions = {
      ProductType = "$.detail.product_type"
    }
  }
}

# ============================================================
# SHARED WEATHER EVENTS DASHBOARD
# ============================================================

resource "aws_cloudwatch_dashboard" "weather_events" {
  dashboard_name = "${var.name_prefix}-weather-events"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "text"
        x      = 0
        y      = 0
        width  = 24
        height = 2

        properties = {
          markdown = "# Wilvor Weather Events\nShared Weather.changed EventBridge routing and product-level event activity"
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 2
        width  = 24
        height = 6

        properties = {
          title   = "Weather.changed EventBridge Rule"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            concat(
              ["AWS/Events", "MatchedEvents"],
              local.weather_events_dashboard_dimensions
            ),
            concat(
              ["AWS/Events", "Invocations"],
              local.weather_events_dashboard_dimensions
            ),
            concat(
              ["AWS/Events", "FailedInvocations"],
              local.weather_events_dashboard_dimensions
            )
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 8
        width  = 24
        height = 6

        properties = {
          title   = "Weather.changed Events by Product Type"
          region  = var.aws_region
          stat    = "Sum"
          period  = 60
          view    = "timeSeries"
          stacked = false

          metrics = [
            [
              local.weather_events_metric_namespace,
              "WeatherChangedEvents",
              "ProductType",
              "METAR",
              {
                label = "METAR"
              }
            ],
            [
              local.weather_events_metric_namespace,
              "WeatherChangedEvents",
              "ProductType",
              "TAF",
              {
                label = "TAF"
              }
            ],
            [
              local.weather_events_metric_namespace,
              "WeatherChangedEvents",
              "ProductType",
              "SIGMET",
              {
                label = "SIGMET"
              }
            ]
          ]
        }
      }
    ]
  })
}