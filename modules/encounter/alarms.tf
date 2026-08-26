# ============================================================
# AircraftHazardEncounter - CloudWatch Alarms
# ============================================================


# ------------------------------------------------------------
# Lambda errors
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "encounter_processor_errors" {
  alarm_name = (
    "${var.name_prefix}-encounter-processor-errors"
  )

  alarm_description = (
    "Encounter Processor returned one or more Lambda errors."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Errors"

  dimensions = {
    FunctionName = (
      aws_lambda_function.encounter_processor.function_name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# Lambda throttles
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "encounter_processor_throttles" {
  alarm_name = (
    "${var.name_prefix}-encounter-processor-throttles"
  )

  alarm_description = (
    "Encounter Processor Lambda was throttled."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Throttles"

  dimensions = {
    FunctionName = (
      aws_lambda_function.encounter_processor.function_name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# Lambda duration
#
# Encounter Lambda timeout is 60 seconds.
# Alarm at 45 seconds average for two periods.
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "encounter_processor_duration_high" {
  alarm_name = (
    "${var.name_prefix}-encounter-processor-duration-high"
  )

  alarm_description = (
    "Encounter Processor average duration exceeded 45 seconds."
  )

  namespace   = "AWS/Lambda"
  metric_name = "Duration"

  dimensions = {
    FunctionName = (
      aws_lambda_function.encounter_processor.function_name
    )
  }

  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 45000
  comparison_operator = "GreaterThanThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# AircraftHazardEncounter DynamoDB read throttles
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "aircraft_hazard_encounter_read_throttles" {
  alarm_name = (
    "${var.name_prefix}-aircraft-hazard-encounter-read-throttles"
  )

  alarm_description = (
    "AircraftHazardEncounter DynamoDB read throttling detected."
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "ReadThrottleEvents"

  dimensions = {
    TableName = (
      aws_dynamodb_table.aircraft_hazard_encounter.name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# AircraftHazardEncounter DynamoDB write throttles
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "aircraft_hazard_encounter_write_throttles" {
  alarm_name = (
    "${var.name_prefix}-aircraft-hazard-encounter-write-throttles"
  )

  alarm_description = (
    "AircraftHazardEncounter DynamoDB write throttling detected."
  )

  namespace   = "AWS/DynamoDB"
  metric_name = "WriteThrottleEvents"

  dimensions = {
    TableName = (
      aws_dynamodb_table.aircraft_hazard_encounter.name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# EventBridge:
# projection.ready -> Encounter Processor
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "encounter_projection_ready_eventbridge_failures" {
  alarm_name = (
    "${var.name_prefix}-encounter-projection-ready-eventbridge-failures"
  )

  alarm_description = (
    "projection.ready EventBridge delivery to Encounter Processor failed."
  )

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = (
      aws_cloudwatch_event_rule.projection_ready.name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}


# ------------------------------------------------------------
# EventBridge:
# hazard.materialized -> Encounter Processor
# ------------------------------------------------------------

resource "aws_cloudwatch_metric_alarm" "encounter_hazard_materialized_eventbridge_failures" {
  alarm_name = (
    "${var.name_prefix}-encounter-hazard-materialized-eventbridge-failures"
  )

  alarm_description = (
    "hazard.materialized EventBridge delivery to Encounter Processor failed."
  )

  namespace   = "AWS/Events"
  metric_name = "FailedInvocations"

  dimensions = {
    RuleName = (
      aws_cloudwatch_event_rule.hazard_materialized.name
    )
  }

  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"

  treat_missing_data = "notBreaching"

  tags = var.tags
}