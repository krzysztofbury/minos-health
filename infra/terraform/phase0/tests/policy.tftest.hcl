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
    arn  = "arn:aws:iam::123456789012:user/minos-health-phase0-watcher"
    name = "minos-health-phase0-watcher"
  }
}

run "project_budget_scope" {
  command = plan

  variables {
    budget_currency         = "USD"
    budget_subscriber_email = "operator@example.invalid"
    expected_aws_account_id = "123456789012"
    github_repository       = "example/minos-health"
    monthly_budget_amount   = 10
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
}
