data "aws_iam_policy_document" "encounter_processor_assume_role" {
  statement {
    actions = [
      "sts:AssumeRole"
    ]

    principals {
      type = "Service"

      identifiers = [
        "lambda.amazonaws.com"
      ]
    }
  }
}

resource "aws_iam_role" "encounter_processor" {
  name = "${var.name_prefix}-encounter-processor-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document
    .encounter_processor_assume_role
    .json
  )

  tags = var.tags
}

data "aws_iam_policy_document" "encounter_processor" {
  statement {
    sid = "ReadAircraftProjection"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.aircraft_projection_table_arn
    ]
  }

  statement {
    sid = "ReadAircraftProjectionCells"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      var.aircraft_projection_cells_table_arn,
      "${var.aircraft_projection_cells_table_arn}/index/${var.aircraft_projection_cells_h3_index_name}"
    ]
  }

  statement {
    sid = "ReadHazardCells"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      var.hazard_cells_table_arn,
      "${var.hazard_cells_table_arn}/index/${var.hazard_cells_hazard_version_index_name}"
    ]
  }

  statement {
    sid = "ReadActiveHazards"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.active_hazards_table_arn
    ]
  }

  statement {
    sid = "ReadHazardCoordinates"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      var.hazard_coordinates_table_arn
    ]
  }

  statement {
    sid = "WriteAircraftHazardEncounter"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:Query"
    ]

    resources = [
      aws_dynamodb_table.aircraft_hazard_encounter.arn,
      "${aws_dynamodb_table.aircraft_hazard_encounter.arn}/index/projection_id-hazard_version_key-index"
    ]
  }

  statement {
    sid = "PublishEncounterEvents"

    actions = [
      "events:PutEvents"
    ]

    resources = [
      var.event_bus_arn
    ]
  }

  statement {
    sid = "WriteLambdaLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.encounter_processor.arn}:*"
    ]
  }

  statement {
    sid = "PublishEncounterMetrics"

    actions = [
      "cloudwatch:PutMetricData"
    ]

    resources = [
      "*"
    ]
  }
}

resource "aws_iam_role_policy" "encounter_processor" {
  name = "${var.name_prefix}-encounter-processor-lambda-policy"
  role = (
    aws_iam_role
    .encounter_processor
    .id
  )

  policy = (
    data.aws_iam_policy_document
    .encounter_processor
    .json
  )
}

resource "aws_cloudwatch_log_group" "encounter_processor" {
  name = "/aws/lambda/${var.name_prefix}-encounter-processor"

  retention_in_days = var.log_retention_days

  tags = var.tags
}

resource "aws_lambda_function" "encounter_processor" {
  function_name = "${var.name_prefix}-encounter-processor"

  filename = var.encounter_processor_zip_path

  source_code_hash = filebase64sha256(
    var.encounter_processor_zip_path
  )

  role = (
    aws_iam_role
    .encounter_processor
    .arn
  )

  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = 512
  timeout     = 60

  environment {
    variables = {
      ENVIRONMENT = (
        lookup(
          var.tags,
          "Environment",
          "dev"
        )
      )

      EVENT_BUS_NAME = var.event_bus_name

      AIRCRAFT_PROJECTION_TABLE_NAME = (
        var.aircraft_projection_table_name
      )

      AIRCRAFT_PROJECTION_CELLS_TABLE_NAME = (
        var.aircraft_projection_cells_table_name
      )

      AIRCRAFT_PROJECTION_CELLS_H3_INDEX_NAME = (
        var.aircraft_projection_cells_h3_index_name
      )

      HAZARD_CELLS_TABLE_NAME = (
        var.hazard_cells_table_name
      )

      HAZARD_CELLS_HAZARD_VERSION_INDEX_NAME = (
        var.hazard_cells_hazard_version_index_name
      )

      ACTIVE_HAZARDS_TABLE_NAME = (
        var.active_hazards_table_name
      )

      HAZARD_COORDINATES_TABLE_NAME = (
        var.hazard_coordinates_table_name
      )

      AIRCRAFT_HAZARD_ENCOUNTER_TABLE_NAME = (
        aws_dynamodb_table
        .aircraft_hazard_encounter
        .name
      )

      AHE_SCHEMA_VERSION = (
        "wilvor.aircraft_hazard_encounter.v4.0"
      )

      AHE_RETENTION_SECONDS = (
        tostring(var.encounter_retention_seconds)
      )

      MAX_MATCHED_H3_CELLS = (
        tostring(var.max_matched_h3_cells)
      )
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.encounter_processor,
    aws_iam_role_policy.encounter_processor
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-encounter-processor"
    Component = "encounter"
  })
}

resource "aws_cloudwatch_event_rule" "projection_ready" {
  name = "${var.name_prefix}-encounter-projection-ready"

  description = (
    "Runs AircraftHazardEncounter evaluation after AircraftProjection becomes READY."
  )

  event_bus_name = var.event_bus_name

  state = (
    var.enable_encounter_event_trigger
    ? "ENABLED"
    : "DISABLED"
  )

  event_pattern = jsonencode({
    source = [
      "wilvor.projection"
    ]

    detail-type = [
      "projection.ready"
    ]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "encounter_processor_projection_ready" {
  rule = (
    aws_cloudwatch_event_rule
    .projection_ready
    .name
  )

  event_bus_name = var.event_bus_name

  arn = (
    aws_lambda_function
    .encounter_processor
    .arn
  )

  target_id = "EncounterProcessorFromProjectionReady"
}

resource "aws_lambda_permission" "allow_eventbridge_projection_ready" {
  statement_id = "AllowExecutionFromProjectionReady"

  action = "lambda:InvokeFunction"

  function_name = (
    aws_lambda_function
    .encounter_processor
    .function_name
  )

  principal = "events.amazonaws.com"

  source_arn = (
    aws_cloudwatch_event_rule
    .projection_ready
    .arn
  )
}

resource "aws_cloudwatch_event_rule" "hazard_materialized" {
  name = "${var.name_prefix}-encounter-hazard-materialized"

  description = (
    "Runs targeted AircraftHazardEncounter reevaluation after hazard materialization."
  )

  event_bus_name = var.event_bus_name

  state = (
    var.enable_encounter_event_trigger
    ? "ENABLED"
    : "DISABLED"
  )

  event_pattern = jsonencode({
    source = [
      "wilvor.weather"
    ]

    detail-type = [
      "hazard.materialized"
    ]
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "encounter_processor_hazard_materialized" {
  rule = (
    aws_cloudwatch_event_rule
    .hazard_materialized
    .name
  )

  event_bus_name = var.event_bus_name

  arn = (
    aws_lambda_function
    .encounter_processor
    .arn
  )

  target_id = "EncounterProcessorFromHazardMaterialized"
}

resource "aws_lambda_permission" "allow_eventbridge_hazard_materialized" {
  statement_id = "AllowExecutionFromHazardMaterializedForEncounter"

  action = "lambda:InvokeFunction"

  function_name = (
    aws_lambda_function
    .encounter_processor
    .function_name
  )

  principal = "events.amazonaws.com"

  source_arn = (
    aws_cloudwatch_event_rule
    .hazard_materialized
    .arn
  )
}