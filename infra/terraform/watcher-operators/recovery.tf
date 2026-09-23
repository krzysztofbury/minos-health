locals {
  state_recovery_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListOnlyReviewedStateKeys"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = local.state_bucket_arn
        Condition = { StringLike = {
          "s3:prefix" = flatten([
            for key in local.state_keys : [key, "${key}.tflock"]
          ])
        } }
      },
      {
        Sid      = "ReadOnlyReviewedStateVersions"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = [for key in local.state_keys : "${local.state_bucket_arn}/${key}"]
      },
      {
        Sid       = "RestoreOnlyWithReviewedCurrentEtag"
        Effect    = "Allow"
        Action    = "s3:PutObject"
        Resource  = [for key in local.state_keys : "${local.state_bucket_arn}/${key}"]
        Condition = { Null = { "s3:if-match" = "false" } }
      },
      {
        Sid      = "InspectOrRemoveOnlyReviewedLocks"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:DeleteObject"]
        Resource = [for key in local.state_keys : "${local.state_bucket_arn}/${key}.tflock"]
      },
      {
        Sid      = "VerifyCallerOnly"
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_policy" "state_recovery_boundary" {
  name        = "minos-health-watcher-state-recovery-boundary"
  description = "Maximum versioned watcher state recovery access."
  policy      = local.state_recovery_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}

resource "aws_iam_role" "state_recovery" {
  name                 = local.recovery_operator_name
  assume_role_policy   = local.sso_assume_policy
  permissions_boundary = local.state_recovery_boundary_arn
  max_session_duration = 3600

  depends_on = [aws_iam_policy.state_recovery_boundary]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy" "state_recovery" {
  name   = "minos-health-watcher-state-recovery-scoped"
  role   = aws_iam_role.state_recovery.name
  policy = local.state_recovery_policy
}
