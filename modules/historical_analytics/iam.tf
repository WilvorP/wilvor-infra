data "aws_partition" "current" {}

data "aws_iam_policy_document" "query" {
  count = local.enabled ? 1 : 0

  statement {
    sid    = "AthenaFixedQueries"
    effect = "Allow"

    actions = [
      "athena:StartQueryExecution",
      "athena:GetQueryExecution",
      "athena:GetQueryResults",
      "athena:StopQueryExecution",
    ]

    resources = [
      aws_athena_workgroup.historical_analytics[0].arn,
    ]
  }

  statement {
    sid    = "GlueCatalogRead"
    effect = "Allow"

    actions = [
      "glue:GetDatabase",
      "glue:GetTable",
    ]

    resources = [
      "arn:${data.aws_partition.current.partition}:glue:${var.aws_region}:${var.account_id}:catalog",
      aws_glue_catalog_database.historical_facts[0].arn,
      aws_glue_catalog_table.structured["encounter"].arn,
      aws_glue_catalog_table.structured["risk"].arn,
      aws_glue_catalog_table.structured["hazard_version"].arn,
    ]
  }

  statement {
    sid    = "CanonicalBucketList"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
    ]

    resources = [
      data.aws_s3_bucket.historical_facts[0].arn,
    ]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.query_canonical_list_prefixes
    }
  }

  statement {
    sid    = "CanonicalBucketLocation"
    effect = "Allow"

    actions = [
      "s3:GetBucketLocation",
    ]

    resources = [
      data.aws_s3_bucket.historical_facts[0].arn,
    ]
  }

  statement {
    sid    = "CanonicalObjectsRead"
    effect = "Allow"

    actions = [
      "s3:GetObject",
    ]

    resources = [
      for prefix in local.query_canonical_object_prefixes :
      "${data.aws_s3_bucket.historical_facts[0].arn}/${prefix}"
    ]
  }

  statement {
    sid    = "AthenaResultsBucketLocation"
    effect = "Allow"

    actions = [
      "s3:GetBucketLocation",
    ]

    resources = [
      aws_s3_bucket.athena_results[0].arn,
    ]
  }

  statement {
    sid    = "AthenaResultsBucketList"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
    ]

    resources = [
      aws_s3_bucket.athena_results[0].arn,
    ]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = local.query_results_list_prefixes
    }
  }

  statement {
    sid    = "AthenaResultsObjectsAccess"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]

    resources = [
      "${aws_s3_bucket.athena_results[0].arn}/${local.athena_results_prefix}*",
    ]
  }
}

resource "aws_iam_policy" "query" {
  count = local.enabled ? 1 : 0

  name        = local.query_policy_name
  description = "Unattached least-privilege permissions for deterministic historical analytics queries. Future Agent API attaches this to its own Lambda-trust role. This policy is not an execution principal."
  policy      = data.aws_iam_policy_document.query[0].json

  tags = merge(local.common_tags, {
    Name = local.query_policy_name
  })
}
