locals {
  sso_assume_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OnlyReviewedIdentityCenterRole"
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { AWS = "arn:aws:iam::${local.account_id}:root" }
      Condition = { ArnEquals = { "aws:PrincipalArn" = local.sso_role_arn } }
    }]
  })

  production_operator_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ListOnlyWatcherStateAndLock"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = local.state_bucket_arn
        Condition = { StringLike = {
          "s3:prefix" = ["watcher/terraform.tfstate", "watcher/terraform.tfstate.tflock"]
        } }
      },
      {
        Sid      = "ReadWriteOnlyWatcherState"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject"]
        Resource = "${local.state_bucket_arn}/watcher/terraform.tfstate"
      },
      {
        Sid      = "ManageOnlyWatcherLock"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
        Resource = "${local.state_bucket_arn}/watcher/terraform.tfstate.tflock"
      },
      {
        Sid       = "CreateOnlyReviewedSnapshotBucket"
        Effect    = "Allow"
        Action    = "s3:CreateBucket"
        Resource  = local.snapshot_arn
        Condition = { StringEquals = { "s3:LocationConstraint" = local.region } }
      },
      {
        Sid    = "ConfigureOnlyReviewedSnapshotBucket"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:PutBucketTagging",
          "s3:GetBucketCORS", "s3:GetBucketWebsite",
          "s3:GetAccelerateConfiguration", "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging", "s3:GetReplicationConfiguration",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketVersioning", "s3:PutBucketVersioning",
          "s3:GetBucketPolicy", "s3:PutBucketPolicy",
          "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketOwnershipControls", "s3:PutBucketOwnershipControls",
          "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration",
          "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration",
          "s3:GetBucketAcl", "s3:ListBucket", "s3:ListBucketVersions",
        ]
        Resource = local.snapshot_arn
      },
      {
        Sid      = "CreateOnlyBoundedWatcherUser"
        Effect   = "Allow"
        Action   = "iam:CreateUser"
        Resource = local.watcher_user_arn
        Condition = { StringEquals = {
          "iam:PermissionsBoundary" = local.watcher_boundary_arn
          "aws:RequestTag/Project"  = "minos-health"
        } }
      },
      {
        Sid       = "RetainOnlyReviewedWatcherBoundary"
        Effect    = "Allow"
        Action    = "iam:PutUserPermissionsBoundary"
        Resource  = local.watcher_user_arn
        Condition = { StringEquals = { "iam:PermissionsBoundary" = local.watcher_boundary_arn } }
      },
      {
        Sid      = "ReadAndTagOnlyWatcherUser"
        Effect   = "Allow"
        Action   = ["iam:GetUser", "iam:ListUserTags", "iam:TagUser", "iam:UntagUser", "iam:ListAttachedUserPolicies", "iam:ListUserPolicies"]
        Resource = local.watcher_user_arn
      },
      {
        Sid       = "AttachOnlyReviewedWatcherPolicy"
        Effect    = "Allow"
        Action    = ["iam:AttachUserPolicy", "iam:DetachUserPolicy"]
        Resource  = local.watcher_user_arn
        Condition = { ArnEquals = { "iam:PolicyARN" = local.watcher_archive_arn } }
      },
      {
        Sid    = "ManageOnlyWatcherArchivePolicy"
        Effect = "Allow"
        Action = [
          "iam:CreatePolicy", "iam:GetPolicy", "iam:GetPolicyVersion",
          "iam:ListPolicyVersions", "iam:CreatePolicyVersion", "iam:SetDefaultPolicyVersion",
          "iam:DeletePolicyVersion", "iam:TagPolicy", "iam:UntagPolicy",
        ]
        Resource = local.watcher_archive_arn
      },
      {
        Sid      = "CreateOnlyBoundedGithubReader"
        Effect   = "Allow"
        Action   = "iam:CreateRole"
        Resource = local.github_reader_arn
        Condition = { StringEquals = {
          "iam:PermissionsBoundary" = local.github_boundary_arn
          "aws:RequestTag/Project"  = "minos-health"
        } }
      },
      {
        Sid      = "ManageOnlyGithubReaderPolicy"
        Effect   = "Allow"
        Action   = ["iam:GetRole", "iam:ListRoleTags", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy"]
        Resource = local.github_reader_arn
      },
      {
        Sid      = "ManageOnlyGithubOidcProvider"
        Effect   = "Allow"
        Action   = ["iam:CreateOpenIDConnectProvider", "iam:GetOpenIDConnectProvider", "iam:TagOpenIDConnectProvider", "iam:ListOpenIDConnectProviderTags"]
        Resource = local.github_oidc_arn
      },
      {
        Sid      = "ListOidcProvidersForReadOnlyRefresh"
        Effect   = "Allow"
        Action   = "iam:ListOpenIDConnectProviders"
        Resource = "*"
      },
      {
        Sid      = "ManageOnlyMinosBudget"
        Effect   = "Allow"
        Action   = ["budgets:CreateBudget", "budgets:ModifyBudget", "budgets:ViewBudget", "budgets:DescribeBudget", "budgets:DescribeBudgets", "budgets:DescribeNotificationsForBudget", "budgets:UpdateBudget", "budgets:TagResource", "budgets:ListTagsForResource"]
        Resource = "arn:aws:budgets::${local.account_id}:budget/minos-health-watcher-monthly"
      },
      {
        Sid      = "ManageOnlyMinosBudgetTopic"
        Effect   = "Allow"
        Action   = ["sns:CreateTopic", "sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:TagResource", "sns:ListTagsForResource", "sns:Subscribe", "sns:ListSubscriptionsByTopic", "sns:Publish"]
        Resource = "arn:aws:sns:${local.region}:${local.account_id}:minos-health-watcher-budget-alerts"
      },
      {
        Sid      = "ReadSubscriptionAttributesInWatcherRegion"
        Effect   = "Allow"
        Action   = "sns:GetSubscriptionAttributes"
        Resource = "*"
        Condition = { StringEquals = {
          "aws:RequestedRegion" = local.region
        } }
      },
      {
        Sid      = "ReadOnlyMinosBudgetSubscription"
        Effect   = "Allow"
        Action   = ["sns:ListTagsForResource"]
        Resource = "arn:aws:sns:${local.region}:${local.account_id}:minos-health-watcher-budget-alerts:*"
      },
      {
        Sid      = "VerifyCallerOnly"
        Effect   = "Allow"
        Action   = "sts:GetCallerIdentity"
        Resource = "*"
      },
    ]
  })

  acceptance_operator_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "CreateOnlyDisposableBuckets"
        Effect    = "Allow"
        Action    = "s3:CreateBucket"
        Resource  = local.acceptance_arn
        Condition = { StringEquals = { "s3:LocationConstraint" = local.region } }
      },
      {
        Sid    = "ManageOnlyDisposableBuckets"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation", "s3:GetBucketTagging", "s3:PutBucketTagging",
          "s3:GetBucketCORS", "s3:GetBucketWebsite",
          "s3:GetBucketAccelerateConfiguration", "s3:GetBucketRequestPayment",
          "s3:GetBucketLogging", "s3:GetBucketReplication",
          "s3:GetBucketObjectLockConfiguration",
          "s3:GetBucketVersioning", "s3:PutBucketVersioning",
          "s3:GetBucketPolicy", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
          "s3:GetBucketPublicAccessBlock", "s3:PutBucketPublicAccessBlock",
          "s3:GetBucketOwnershipControls", "s3:PutBucketOwnershipControls",
          "s3:GetEncryptionConfiguration", "s3:PutEncryptionConfiguration",
          "s3:GetLifecycleConfiguration", "s3:PutLifecycleConfiguration",
          "s3:GetBucketAcl", "s3:ListBucket", "s3:ListBucketVersions", "s3:DeleteBucket",
        ]
        Resource = local.acceptance_arn
      },
      {
        Sid      = "ReadAndDeleteOnlyDisposableObjectVersions"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:GetObjectAttributes", "s3:GetObjectVersion", "s3:GetObjectVersionAttributes", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = "${local.acceptance_arn}/*"
      },
      {
        Sid       = "CreateOnlyConditionalDisposableObjects"
        Effect    = "Allow"
        Action    = "s3:PutObject"
        Resource  = "${local.acceptance_arn}/*"
        Condition = { StringEquals = { "s3:if-none-match" = "*" } }
      },
      {
        Sid      = "CreateOnlyBoundedDisposableWatcherRoles"
        Effect   = "Allow"
        Action   = "iam:CreateRole"
        Resource = local.acceptance_watcher_arn
        Condition = {
          StringEquals = {
            "iam:PermissionsBoundary"    = local.acceptance_role_boundary_arn
            "aws:RequestTag/Disposable"  = "true"
            "aws:RequestTag/Environment" = "acceptance"
            "aws:RequestTag/ManagedBy"   = "Terraform"
            "aws:RequestTag/Project"     = "minos-health"
            "aws:RequestTag/TestHarness" = "watcher-acceptance"
          }
          Null = { "aws:RequestTag/TestRunId" = "false" }
          "ForAllValues:StringEquals" = {
            "aws:TagKeys" = ["Disposable", "Environment", "ManagedBy", "Project", "TestHarness", "TestRunId"]
          }
        }
      },
      {
        Sid      = "CheckDisposableWatcherRoleExistence"
        Effect   = "Allow"
        Action   = "iam:GetRole"
        Resource = local.acceptance_watcher_arn
      },
      {
        Sid      = "ManageOnlyDisposableWatcherRoles"
        Effect   = "Allow"
        Action   = ["iam:ListRoleTags", "iam:ListAttachedRolePolicies", "iam:ListInstanceProfilesForRole", "iam:TagRole", "iam:UntagRole", "iam:ListRolePolicies", "iam:GetRolePolicy", "iam:PutRolePolicy", "iam:DeleteRolePolicy", "iam:DeleteRole"]
        Resource = local.acceptance_watcher_arn
        Condition = { StringEquals = {
          "aws:ResourceTag/Disposable"  = "true"
          "aws:ResourceTag/Environment" = "acceptance"
          "aws:ResourceTag/Project"     = "minos-health"
          "aws:ResourceTag/TestHarness" = "watcher-acceptance"
        } }
      },
      {
        Sid      = "AssumeOnlyDisposableWatcherRoles"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = local.acceptance_watcher_arn
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

resource "aws_iam_policy" "production_operator_boundary" {
  name        = "minos-health-watcher-terraform-operator-boundary"
  description = "Maximum resource scope for the dedicated production watcher Terraform operator."
  policy      = local.production_operator_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}

resource "aws_iam_role" "production_operator" {
  name                 = local.production_operator_name
  assume_role_policy   = local.sso_assume_policy
  permissions_boundary = local.production_operator_boundary_arn
  max_session_duration = 3600

  depends_on = [aws_iam_policy.production_operator_boundary]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy" "production_operator" {
  name   = "minos-health-watcher-terraform-scoped"
  role   = aws_iam_role.production_operator.name
  policy = local.production_operator_policy
}

resource "aws_iam_policy" "acceptance_operator_boundary" {
  name        = "minos-health-watcher-acceptance-operator-boundary"
  description = "Maximum disposable resource scope for the acceptance Terraform operator."
  policy      = local.acceptance_operator_policy

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [terraform_data.bootstrap_guard]
}

resource "aws_iam_role" "acceptance_operator" {
  name                 = local.acceptance_operator_name
  assume_role_policy   = local.sso_assume_policy
  permissions_boundary = local.acceptance_operator_boundary_arn
  max_session_duration = 3600

  depends_on = [aws_iam_policy.acceptance_operator_boundary]

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_iam_role_policy" "acceptance_operator" {
  name   = "minos-health-watcher-acceptance-scoped"
  role   = aws_iam_role.acceptance_operator.name
  policy = local.acceptance_operator_policy
}
