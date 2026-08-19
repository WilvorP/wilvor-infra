resource "aws_cloudwatch_dashboard" "aircraft_hazard_encounter" {
  dashboard_name = "${var.name_prefix}-aircraft-hazard-encounter"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6

        properties = {
          title   = "AircraftHazardEncounter Lambda"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/Lambda",
              "Invocations",
              "FunctionName",
              aws_lambda_function.encounter_processor.function_name
            ],
            [
              ".",
              "Errors",
              ".",
              "."
            ],
            [
              ".",
              "Throttles",
              ".",
              "."
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6

        properties = {
          title   = "AHE Records and Candidate Counts"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "Wilvor/Pipeline",
              "EncountersWritten",
              "Environment",
              lookup(var.tags, "Environment", "dev"),
              "Pipeline",
              "encounter",
              "Component",
              "encounter_processor",
              "Stage",
              "evaluation"
            ],
            [
              ".",
              "ProjectionCandidates",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "HazardCandidates",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "ExactConfirmed",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ],
            [
              ".",
              "NoCandidates",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              ".",
              "."
            ]
          ]
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 24
        height = 6

        properties = {
          title   = "AHE DynamoDB Consumed Capacity"
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          period  = 60
          stat    = "Sum"

          metrics = [
            [
              "AWS/DynamoDB",
              "ConsumedReadCapacityUnits",
              "TableName",
              aws_dynamodb_table.aircraft_hazard_encounter.name
            ],
            [
              ".",
              "ConsumedWriteCapacityUnits",
              ".",
              "."
            ],
            [
              ".",
              "ThrottledRequests",
              ".",
              "."
            ]
          ]
        }
      }
    ]
  })
}