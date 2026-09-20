resource "aws_iam_user" "watcher" {
  name          = "minos-health-phase0-watcher"
  force_destroy = false
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

resource "aws_iam_policy" "watcher_archive" {
  name        = "minos-health-phase0-watcher-archive"
  description = "Conditional create and read access to watcher-owned archive prefixes."
  policy      = data.aws_iam_policy_document.watcher_archive.json
}

resource "aws_iam_user_policy_attachment" "watcher_archive" {
  user       = aws_iam_user.watcher.name
  policy_arn = aws_iam_policy.watcher_archive.arn
}

resource "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"

  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "github_archive_assume_role" {
  statement {
    sid     = "GitHubProtectedRef"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:ref:${var.github_ref}"]
    }
  }
}

resource "aws_iam_role" "github_archive_reader" {
  name                 = "minos-health-phase0-github-archive-reader"
  description          = "Read-only archive access from the reviewed protected GitHub ref."
  assume_role_policy   = data.aws_iam_policy_document.github_archive_assume_role.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_archive_reader" {
  statement {
    sid    = "ListReviewedArchivePrefixes"
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
        for prefix in local.github_read_object_prefixes : [prefix, "${prefix}/*"]
      ])
    }
  }

  statement {
    sid    = "ReadReviewedArchiveObjects"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectAttributes",
      "s3:GetObjectVersion",
      "s3:GetObjectVersionAttributes",
    ]
    resources = [
      for prefix in local.github_read_object_prefixes :
      "${aws_s3_bucket.snapshots.arn}/${prefix}/*"
    ]
  }
}

resource "aws_iam_role_policy" "github_archive_reader" {
  name   = "minos-health-phase0-reviewed-archive-read"
  role   = aws_iam_role.github_archive_reader.id
  policy = data.aws_iam_policy_document.github_archive_reader.json
}
