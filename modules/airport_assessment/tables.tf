resource "aws_dynamodb_table" "airport_assessment" {
  name         = "${var.name_prefix}-airport-assessment"
  billing_mode = "PAY_PER_REQUEST"

  hash_key  = "evaluation_id"
  range_key = "airport_id"

  attribute {
    name = "evaluation_id"
    type = "S"
  }

  attribute {
    name = "airport_id"
    type = "S"
  }

  attribute {
    name = "airport_assessment_id"
    type = "S"
  }

  attribute {
    name = "created_at_epoch"
    type = "N"
  }

  global_secondary_index {
    name            = "airport_assessment_id-index"
    hash_key        = "airport_assessment_id"
    projection_type = "ALL"
  }

  global_secondary_index {
    name            = "airport_id-created_at_epoch-index"
    hash_key        = "airport_id"
    range_key       = "created_at_epoch"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  tags = merge(
    var.tags,
    {
      Name      = "${var.name_prefix}-airport-assessment"
      Component = "airport-assessment"
      DataType  = "derived-decision"
      TableRole = "airport-candidate-result"
    }
  )
}