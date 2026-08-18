data "aws_iam_policy_document" "projection_processor_assume_role" {
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


resource "aws_iam_role" "projection_processor" {
  name = "${var.name_prefix}-projection-processor-lambda-role"

  assume_role_policy = (
    data.aws_iam_policy_document
    .projection_processor_assume_role
    .json
  )

  tags = var.tags
}


data "aws_iam_policy_document" "projection_processor" {

  # -------------------------------------------------------------------------
  # AircraftCurrentState reads
  # -------------------------------------------------------------------------

  statement {
    sid = "ReadAircraftCurrentState"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.aircraft_current_state_table_arn
    ]
  }


  statement {
    sid = "QueryAircraftCurrentStateByH3"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      "${var.aircraft_current_state_table_arn}/index/current_h3_cell-position_time_epoch-index"
    ]
  }


  # -------------------------------------------------------------------------
  # ImpactCells reads
  # -------------------------------------------------------------------------

  statement {
    sid = "ReadImpactCells"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      var.impact_cells_table_arn
    ]
  }


  statement {
    sid = "QueryImpactCellsByHazard"

    actions = [
      "dynamodb:Query"
    ]

    resources = [
      "${var.impact_cells_table_arn}/index/hazard_version_key-h3_cell-index"
    ]
  }


  # -------------------------------------------------------------------------
  # ActiveHazards reads
  # -------------------------------------------------------------------------

  statement {
    sid = "ReadActiveHazards"

    actions = [
      "dynamodb:GetItem"
    ]

    resources = [
      var.active_hazards_table_arn
    ]
  }


  # -------------------------------------------------------------------------
  # Projection parent
  # -------------------------------------------------------------------------

  statement {
    sid = "WriteProjectionParent"

    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem"
    ]

    resources = [
      aws_dynamodb_table.aircraft_projection.arn
    ]
  }


  # -------------------------------------------------------------------------
  # Projection children
  # -------------------------------------------------------------------------

  statement {
    sid = "WriteProjectionChildren"

    actions = [
      "dynamodb:BatchWriteItem",
      "dynamodb:Query"
    ]

    resources = [
      aws_dynamodb_table.aircraft_projection_points.arn,
      aws_dynamodb_table.aircraft_projection_cells.arn
    ]
  }


  # -------------------------------------------------------------------------
  # EventBridge publication
  # -------------------------------------------------------------------------

  statement {
    sid = "PublishProjectionReady"

    actions = [
      "events:PutEvents"
    ]

    resources = [
      var.event_bus_arn
    ]
  }


  # -------------------------------------------------------------------------
  # CloudWatch
  # -------------------------------------------------------------------------

  statement {
    sid = "WriteLambdaLogs"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "${aws_cloudwatch_log_group.projection_processor.arn}:*"
    ]
  }


  statement {
    sid = "PublishProjectionMetrics"

    actions = [
      "cloudwatch:PutMetricData"
    ]

    resources = [
      "*"
    ]
  }
}


resource "aws_iam_role_policy" "projection_processor" {
  name = "${var.name_prefix}-projection-processor-lambda-policy"

  role = (
    aws_iam_role
    .projection_processor
    .id
  )

  policy = (
    data.aws_iam_policy_document
    .projection_processor
    .json
  )
}


# =============================================================================
# CloudWatch Log Group
# =============================================================================

resource "aws_cloudwatch_log_group" "projection_processor" {
  name = (
    "/aws/lambda/${var.name_prefix}-projection-processor"
  )

  retention_in_days = (
    var.log_retention_days
  )

  tags = var.tags
}


# =============================================================================
# Projection Processor Lambda
# =============================================================================

resource "aws_lambda_function" "projection_processor" {
  function_name = (
    "${var.name_prefix}-projection-processor"
  )

  filename = (
    var.projection_processor_zip_path
  )

  source_code_hash = filebase64sha256(
    var.projection_processor_zip_path
  )

  role = (
    aws_iam_role
    .projection_processor
    .arn
  )

  handler = "app.lambda_handler"
  runtime = "python3.12"

  memory_size = 256
  timeout     = 30

  environment {
    variables = {
      ENVIRONMENT = (
        lookup(
          var.tags,
          "Environment",
          "dev"
        )
      )


      # ---------------------------------------------------------------------
      # EventBridge
      # ---------------------------------------------------------------------

      EVENT_BUS_NAME = (
        var.event_bus_name
      )


      # ---------------------------------------------------------------------
      # Projection tables
      # ---------------------------------------------------------------------

      AIRCRAFT_PROJECTION_TABLE_NAME = (
        aws_dynamodb_table
        .aircraft_projection
        .name
      )

      AIRCRAFT_PROJECTION_POINTS_TABLE_NAME = (
        aws_dynamodb_table
        .aircraft_projection_points
        .name
      )

      AIRCRAFT_PROJECTION_CELLS_TABLE_NAME = (
        aws_dynamodb_table
        .aircraft_projection_cells
        .name
      )


      # ---------------------------------------------------------------------
      # Dependency tables
      # ---------------------------------------------------------------------

      AIRCRAFT_CURRENT_STATE_TABLE_NAME = (
        var.aircraft_current_state_table_name
      )

      IMPACT_CELLS_TABLE_NAME = (
        var.impact_cells_table_name
      )

      ACTIVE_HAZARDS_TABLE_NAME = (
        var.active_hazards_table_name
      )


      # ---------------------------------------------------------------------
      # Dependency indexes
      #
      # Used by hazard.materialized:
      #
      # hazard_version_key
      #      ↓
      # ImpactCells GSI
      #      ↓
      # H3 cells
      #      ↓
      # AircraftCurrentState H3 GSI
      # ---------------------------------------------------------------------

      AIRCRAFT_CURRENT_STATE_H3_INDEX_NAME = (
        "current_h3_cell-position_time_epoch-index"
      )

      IMPACT_CELLS_HAZARD_INDEX_NAME = (
        "hazard_version_key-h3_cell-index"
      )


      # ---------------------------------------------------------------------
      # Projection configuration
      # ---------------------------------------------------------------------

      PROJECTION_HORIZONS_MIN = (
        "5,10,15,30"
      )

      CORRIDOR_GRID_DISTANCES = (
        "0,0,1,1"
      )

      PROJECTION_ALGORITHM_VERSION = (
        "wilvor.projection.constant_velocity.v1"
      )

      PROJECTION_CONFIG_VERSION = (
        "wilvor.projection.config.v1"
      )

      PROJECTION_SCHEMA_VERSION = (
        "wilvor.aircraft_projection.v4.0"
      )

      PROJECTION_POINTS_SCHEMA_VERSION = (
        "wilvor.aircraft_projection_points.v4.0"
      )

      PROJECTION_CELLS_SCHEMA_VERSION = (
        "wilvor.aircraft_projection_cells.v4.0"
      )

      PROJECTION_RETENTION_SECONDS = (
        "3600"
      )

      MAX_CORRIDOR_CELLS = (
        "2000"
      )

      MAX_TRIGGER_HAZARDS = (
        "25"
      )


      # ---------------------------------------------------------------------
      # Eligibility configuration
      # ---------------------------------------------------------------------

      MAX_POSITION_AGE_SECONDS = (
        "180"
      )

      REQUIRE_AIRBORNE = (
        "true"
      )
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.projection_processor,
    aws_iam_role_policy.projection_processor
  ]

  tags = merge(
    var.tags,
    {
      Name = (
        "${var.name_prefix}-projection-processor"
      )

      Component = "projection"
    }
  )
}


# =============================================================================
# Trigger 1:
#
# Aircraft changes state.
#
# aircraft.state.updated
#       ↓
# Projection Processor
#       ↓
# Query current ImpactCells
#       ↓
# Generate projection when eligible
# =============================================================================

resource "aws_cloudwatch_event_rule" "aircraft_state_updated" {
  name = (
    "${var.name_prefix}-projection-aircraft-state-updated"
  )

  description = (
    "Starts projection eligibility evaluation after accepted aircraft state updates."
  )

  event_bus_name = (
    var.event_bus_name
  )

  state = (
    var.enable_projection_event_trigger
    ? "ENABLED"
    : "DISABLED"
  )

  event_pattern = jsonencode({
    source = [
      "wilvor.aircraft"
    ]

    detail-type = [
      "aircraft.state.updated"
    ]
  })

  tags = var.tags
}


resource "aws_cloudwatch_event_target" "projection_processor" {
  rule = (
    aws_cloudwatch_event_rule
    .aircraft_state_updated
    .name
  )

  event_bus_name = (
    var.event_bus_name
  )

  arn = (
    aws_lambda_function
    .projection_processor
    .arn
  )

  target_id = (
    "ProjectionProcessorFromAircraft"
  )
}


resource "aws_lambda_permission" "allow_eventbridge_projection_processor" {
  statement_id = (
    "AllowExecutionFromAircraftStateUpdated"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    aws_lambda_function
    .projection_processor
    .function_name
  )

  principal = (
    "events.amazonaws.com"
  )

  source_arn = (
    aws_cloudwatch_event_rule
    .aircraft_state_updated
    .arn
  )
}


# =============================================================================
# Trigger 2:
#
# Hazard materialization becomes READY.
#
# hazard.materialized
#       ↓
# Query ImpactCells by hazard_version_key
#       ↓
# Query aircraft by H3
#       ↓
# Refresh projections for affected aircraft
# =============================================================================

resource "aws_cloudwatch_event_rule" "hazard_materialized" {
  name = (
    "${var.name_prefix}-projection-hazard-materialized"
  )

  description = (
    "Refreshes aircraft projections when a hazard materialization becomes READY."
  )

  event_bus_name = (
    var.event_bus_name
  )

  state = (
    var.enable_projection_event_trigger
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


resource "aws_cloudwatch_event_target" "projection_processor_hazard" {
  rule = (
    aws_cloudwatch_event_rule
    .hazard_materialized
    .name
  )

  event_bus_name = (
    var.event_bus_name
  )

  arn = (
    aws_lambda_function
    .projection_processor
    .arn
  )

  target_id = (
    "ProjectionProcessorFromHazard"
  )
}


resource "aws_lambda_permission" "allow_eventbridge_projection_hazard" {
  statement_id = (
    "AllowExecutionFromHazardMaterialized"
  )

  action = (
    "lambda:InvokeFunction"
  )

  function_name = (
    aws_lambda_function
    .projection_processor
    .function_name
  )

  principal = (
    "events.amazonaws.com"
  )

  source_arn = (
    aws_cloudwatch_event_rule
    .hazard_materialized
    .arn
  )
}