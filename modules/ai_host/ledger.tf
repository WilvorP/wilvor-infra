resource "aws_dynamodb_table" "ledger" {
  name         = local.ledger_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # Schema attribute only. Enabling TTL does not make deletion a first use
  # or a permission to start again. Retention after deadline is 30 days in
  # the application contract; this table does not enforce that clock.
  ttl {
    attribute_name = "retention_expires_at_epoch"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }

  # This table lives in the disposable development root. dev-down removes it.
  deletion_protection_enabled = false

  tags = merge(
    var.tags,
    {
      Name        = local.ledger_table_name
      Component   = "ai-host"
      TableRole   = "execution-ledger"
      Lifecycle   = "disposable-dev-root"
      RouteStatus = "ai-query-disabled"
    }
  )

  lifecycle {
    precondition {
      condition     = local.ledger_table_name == "wilvor_master_execution_ledger"
      error_message = "The execution ledger table name is fixed to wilvor_master_execution_ledger."
    }
  }
}
