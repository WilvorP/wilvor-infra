locals {
  # Fixed by the Sprint 3C ledger contract. DynamoDbMasterExecutionLedgerStore
  # rejects any other table name. TTL deletion is not a correctness boundary.
  ledger_table_name = "wilvor_master_execution_ledger"

  function_name                 = "${var.name_prefix}-ai-host"
  operational_api_function_name = "${var.name_prefix}-operational-api"
  role_name                     = "${var.name_prefix}-ai-host-lambda-role"
  log_group_name                = "/aws/lambda/${var.name_prefix}-ai-host"

  closed_reserved_concurrency    = 0
  reviewed_invoke_concurrency    = 2
  reserved_concurrent_executions = var.enable_invoke ? local.reviewed_invoke_concurrency : local.closed_reserved_concurrency

  # TransactWriteItems is the consume API on the ledger table ARN only.
  # It stays out of this list so the ledger deny does not override that allow.
  prohibited_ledger_actions = [
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:UpdateItem",
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
  ]

  # GetItem-only reviewed tables. Query stays denied, including callsign lookup.
  prohibited_getitem_table_actions = [
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:TransactWriteItems",
    "dynamodb:BatchWriteItem",
    "dynamodb:BatchGetItem",
    "dynamodb:Scan",
    "dynamodb:Query",
    "dynamodb:CreateTable",
    "dynamodb:PartiQLSelect",
    "dynamodb:PartiQLInsert",
    "dynamodb:PartiQLUpdate",
    "dynamodb:PartiQLDelete",
  ]
}
