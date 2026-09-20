provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.expected_test_account_id]
  forbidden_account_ids = distinct([
    local.known_production_account_id,
    var.production_account_id,
  ])

  default_tags {
    tags = {
      Disposable  = "true"
      Environment = "acceptance"
      ManagedBy   = "Terraform"
      Project     = "minos-health"
      TestHarness = "phase0-real-aws"
      TestRunId   = random_id.suffix.hex
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

resource "random_id" "suffix" {
  byte_length = 8
}

resource "terraform_data" "account_guard" {
  input = data.aws_caller_identity.current.account_id

  lifecycle {
    precondition {
      condition     = data.aws_caller_identity.current.account_id == var.expected_test_account_id
      error_message = "The active AWS identity is not in expected_test_account_id."
    }
    precondition {
      condition     = data.aws_caller_identity.current.account_id != var.production_account_id
      error_message = "The phase-0 acceptance harness cannot run in production."
    }
    precondition {
      condition     = data.aws_caller_identity.current.account_id != local.known_production_account_id
      error_message = "The phase-0 acceptance harness cannot run in the known production account."
    }
  }
}
