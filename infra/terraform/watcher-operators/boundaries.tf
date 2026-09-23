locals {
  watcher_boundary_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListOnlyWatcherPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = local.snapshot_arn
        Condition = {
          StringLike = {
            "s3:prefix" = flatten([
              for prefix in local.snapshot_prefixes : [prefix, "${prefix}/*"]
            ])
          }
        }
      },
      {
        Sid      = "ReadOnlyWatcherObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectAttributes", "s3:GetObjectVersion", "s3:GetObjectVersionAttributes"]
        Resource = [for prefix in local.snapshot_prefixes : "${local.snapshot_arn}/${prefix}/*"]
      },
      {
        Sid      = "CreateOnlyConditionalWatcherObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = [for prefix in local.snapshot_prefixes : "${local.snapshot_arn}/${prefix}/*"]
        Condition = {
          StringEquals = { "s3:if-none-match" = "*" }
        }
      },
    ]
  })

  github_reader_boundary_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListOnlyReviewedPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = local.snapshot_arn
        Condition = {
          StringLike = {
            "s3:prefix" = flatten([
              for prefix in local.github_prefixes : [prefix, "${prefix}/*"]
            ])
          }
        }
      },
      {
        Sid      = "ReadOnlyReviewedObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectAttributes", "s3:GetObjectVersion", "s3:GetObjectVersionAttributes"]
        Resource = [for prefix in local.github_prefixes : "${local.snapshot_arn}/${prefix}/*"]
      },
    ]
  })

  acceptance_watcher_boundary_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListOnlyDisposableWatcherPrefixes"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource = local.acceptance_arn
        Condition = {
          StringLike = {
            "s3:prefix" = flatten([
              for prefix in local.snapshot_prefixes : [prefix, "${prefix}/*"]
            ])
          }
        }
      },
      {
        Sid      = "ReadOnlyDisposableWatcherObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectAttributes", "s3:GetObjectVersion", "s3:GetObjectVersionAttributes"]
        Resource = [for prefix in local.snapshot_prefixes : "${local.acceptance_arn}/${prefix}/*"]
      },
      {
        Sid      = "CreateOnlyConditionalDisposableWatcherObjects"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = [for prefix in local.snapshot_prefixes : "${local.acceptance_arn}/${prefix}/*"]
        Condition = {
          StringEquals = { "s3:if-none-match" = "*" }
        }
      },
    ]
  })
}

resource "aws_iam_policy" "watcher_boundary" {
  name        = "minos-health-watcher-boundary"
  description = "Maximum S3 create/read permissions for the Mikr.us watcher user."
  policy      = local.watcher_boundary_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}

resource "aws_iam_policy" "github_reader_boundary" {
  name        = "minos-health-watcher-github-reader-boundary"
  description = "Maximum private S3 read permissions for the GitHub archive role."
  policy      = local.github_reader_boundary_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}

resource "aws_iam_policy" "acceptance_watcher_boundary" {
  name        = "minos-health-watcher-acceptance-boundary"
  description = "Maximum S3 permissions for the disposable acceptance watcher."
  policy      = local.acceptance_watcher_boundary_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}
