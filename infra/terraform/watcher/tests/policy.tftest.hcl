mock_provider "aws" {}

override_data {
  target = data.aws_partition.current
  values = {
    partition = "aws"
  }
}

override_resource {
  target          = aws_s3_bucket.snapshots
  override_during = plan
  values = {
    arn    = "arn:aws:s3:::minos-health-snapshots-test"
    bucket = "minos-health-snapshots-test"
    id     = "minos-health-snapshots-test"
  }
}

override_resource {
  target          = aws_iam_user.watcher
  override_during = plan
  values = {
    arn = "arn:aws:iam::123456789012:user/minos-health-watcher"
  }
}

run "project_budget_scope" {
  command = plan

  variables {
    budget_currency         = "USD"
    budget_subscriber_email = "operator@example.com"
    expected_aws_account_id = "123456789012"
    github_repository       = "example/minos-health"
    monthly_budget_amount   = 20
    snapshot_bucket_prefix  = "minos-health-snapshots"
  }

  assert {
    condition = one([
      for filter in aws_budgets_budget.project.cost_filter :
      filter.name == "TagKeyValue" &&
      length(filter.values) == 1 &&
      filter.values[0] == "user:Project$minos-health"
    ])
    error_message = "The project budget must be scoped to the Minos project tag."
  }

  assert {
    condition     = aws_iam_user.watcher.name == "minos-health-watcher"
    error_message = "The production watcher IAM user name must not duplicate the watcher suffix."
  }

  assert {
    condition     = aws_iam_policy.watcher_archive.name == "minos-health-watcher-archive"
    error_message = "The production watcher policy name must not duplicate the watcher suffix."
  }
}

run "reject_budget_above_approved_ceiling" {
  command = plan

  variables {
    budget_currency         = "USD"
    budget_subscriber_email = "operator@example.com"
    expected_aws_account_id = "123456789012"
    github_repository       = "example/minos-health"
    monthly_budget_amount   = 21
    snapshot_bucket_prefix  = "minos-health-snapshots"
  }

  expect_failures = [var.monthly_budget_amount]
}

run "reject_non_usd_budget" {
  command = plan

  variables {
    budget_currency         = "EUR"
    budget_subscriber_email = "operator@example.com"
    expected_aws_account_id = "123456789012"
    github_repository       = "example/minos-health"
    monthly_budget_amount   = 20
    snapshot_bucket_prefix  = "minos-health-snapshots"
  }

  expect_failures = [var.budget_currency]
}
