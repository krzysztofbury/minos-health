mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "218375352108"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json = "{}"
    }
  }
}

run "exact_same_account_confirmation_is_accepted" {
  command = plan

  variables {
    aws_region                         = "eu-central-1"
    expected_test_account_id           = "218375352108"
    production_account_id              = "218375352108"
    production_acceptance_enabled      = true
    production_acceptance_confirmation = "ACCEPT-DISPOSABLE-RESOURCES:218375352108"
    test_run_id                        = "0123456789abcdef"
  }

  assert {
    condition     = aws_s3_bucket.snapshots.bucket == "minos-health-watcher-acceptance-218375352108-0123456789abcdef"
    error_message = "The bucket identity must use the fixed prefix, account, and run ID."
  }

  assert {
    condition     = aws_iam_role.watcher.name == "minos-health-watcher-acceptance-0123456789abcdef-watcher"
    error_message = "The role identity must use the fixed prefix and run ID."
  }

  assert {
    condition     = output.operator_principal_arn == "arn:aws:iam::218375352108:role/minos-health-watcher-acceptance-operator"
    error_message = "The operator principal must be anchored to the tracked acceptance role."
  }

  assert {
    condition     = aws_iam_role.watcher.permissions_boundary == "arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary"
    error_message = "The disposable role must use the fixed external permissions boundary."
  }

  assert {
    condition     = output.watcher_role_permissions_boundary_arn == "arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary"
    error_message = "The boundary output must remain anchored to the tracked policy."
  }
}

run "wrong_confirmation_is_rejected" {
  command = plan

  variables {
    aws_region                         = "eu-central-1"
    expected_test_account_id           = "218375352108"
    production_account_id              = "218375352108"
    production_acceptance_enabled      = true
    production_acceptance_confirmation = "ACCEPT-DISPOSABLE-RESOURCES"
    test_run_id                        = "0123456789abcdef"
  }

  expect_failures = [terraform_data.account_guard]
}

run "wrong_account_is_rejected" {
  command = plan

  variables {
    aws_region                         = "eu-central-1"
    expected_test_account_id           = "111111111111"
    production_account_id              = "218375352108"
    production_acceptance_enabled      = true
    production_acceptance_confirmation = "ACCEPT-DISPOSABLE-RESOURCES:218375352108"
    test_run_id                        = "0123456789abcdef"
  }

  expect_failures = [terraform_data.account_guard]
}
