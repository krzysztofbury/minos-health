data "aws_iam_policy_document" "watcher_assume_role" {
  statement {
    sid     = "DedicatedAcceptancePrincipal"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${var.expected_test_account_id}:root"]
    }

    condition {
      test     = "ArnEquals"
      variable = "aws:PrincipalArn"
      values   = [var.operator_principal_arn]
    }
  }
}

resource "aws_iam_role" "watcher" {
  name                 = "${local.resource_name}-watcher"
  description          = "Disposable phase-0 watcher role for real-AWS acceptance only."
  assume_role_policy   = data.aws_iam_policy_document.watcher_assume_role.json
  max_session_duration = 3600

  depends_on = [terraform_data.account_guard]
}

data "aws_iam_policy_document" "watcher_archive" {
  statement {
    sid    = "ListWatcherArchivePrefixes"
    effect = "Allow"

    actions = [
      "s3:ListBucket",
      "s3:ListBucketVersions",
    ]
    resources = [aws_s3_bucket.snapshots.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = flatten([
        for prefix in local.watcher_object_prefixes : [prefix, "${prefix}/*"]
      ])
    }
  }

  statement {
    sid    = "ReadWatcherArchiveObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAttributes",
    ]
    resources = [
      for prefix in local.watcher_object_prefixes :
      "${aws_s3_bucket.snapshots.arn}/${prefix}/*"
    ]
  }

  statement {
    sid    = "CreateWatcherArchiveObjectsConditionally"
    effect = "Allow"

    actions = ["s3:PutObject"]
    resources = [
      for prefix in local.watcher_object_prefixes :
      "${aws_s3_bucket.snapshots.arn}/${prefix}/*"
    ]

    condition {
      test     = "StringEquals"
      variable = "s3:if-none-match"
      values   = ["*"]
    }
  }
}

resource "aws_iam_role_policy" "watcher_archive" {
  name   = "${local.resource_name}-watcher-archive"
  role   = aws_iam_role.watcher.id
  policy = data.aws_iam_policy_document.watcher_archive.json
}
