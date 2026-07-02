resource "aws_iam_openid_connect_provider" "github_actions" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = [
    "sts.amazonaws.com"
  ]

  thumbprint_list = [
    "6938fd4d98bab03faadb97b34396831e3780aea1"
  ]

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-github-actions-oidc-provider"
    Component = "ci-cd"
    Purpose   = "github-actions-ecr-push"
  })
}

data "aws_iam_policy_document" "github_actions_ecr_push_assume_role" {
  statement {
    effect = "Allow"

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    actions = [
      "sts:AssumeRoleWithWebIdentity"
    ]

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"

      values = [
        "sts.amazonaws.com"
      ]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"

      values = [
        "repo:${var.github_repository}:ref:refs/heads/${var.github_actions_branch}"
      ]
    }
  }
}

resource "aws_iam_role" "github_actions_ecr_push" {
  name               = "${var.name_prefix}-github-actions-ecr-push-role"
  assume_role_policy = data.aws_iam_policy_document.github_actions_ecr_push_assume_role.json

  tags = merge(var.tags, {
    Name      = "${var.name_prefix}-github-actions-ecr-push-role"
    Component = "ci-cd"
    Purpose   = "github-actions-ecr-push"
  })
}

data "aws_iam_policy_document" "github_actions_ecr_push" {
  statement {
    sid    = "GetEcrAuthorizationToken"
    effect = "Allow"

    actions = [
      "ecr:GetAuthorizationToken"
    ]

    resources = ["*"]
  }

  statement {
    sid    = "PushProbeImageToEcr"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeRepositories",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart"
    ]

    resources = [
      aws_ecr_repository.opensky_fargate_probe.arn
    ]
  }
}

resource "aws_iam_role_policy" "github_actions_ecr_push" {
  name   = "${var.name_prefix}-github-actions-ecr-push-policy"
  role   = aws_iam_role.github_actions_ecr_push.id
  policy = data.aws_iam_policy_document.github_actions_ecr_push.json
}