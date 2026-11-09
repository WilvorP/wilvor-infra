resource "terraform_data" "development_account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == "396037508959"
      error_message = "envs/dev is bound to AWS account 396037508959 in us-west-1. Refusing to evaluate this configuration against a different account."
    }
  }
}
