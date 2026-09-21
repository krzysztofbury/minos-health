provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.expected_test_account_id]
}

data "aws_caller_identity" "current" {}

resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.expected_test_account_id
      error_message = "The active AWS identity is not in expected_test_account_id."
    }
    precondition {
      condition     = var.expected_test_account_id == var.production_account_id
      error_message = "The expected and configured production accounts must match."
    }
    precondition {
      condition     = var.production_account_id == local.known_production_account_id
      error_message = "The configured production account is not the built-in repository account."
    }
    precondition {
      condition     = data.aws_caller_identity.current.account_id == local.known_production_account_id
      error_message = "The active AWS identity is not in the built-in repository account."
    }
    precondition {
      condition     = var.production_acceptance_enabled
      error_message = "production_acceptance_enabled must be true."
    }
    precondition {
      condition     = var.production_acceptance_confirmation == local.production_confirmation
      error_message = "production_acceptance_confirmation does not contain the exact required phrase."
    }
  }
}

resource "terraform_data" "run_guard" {
  input = var.test_run_id

  depends_on = [terraform_data.account_guard]
}
