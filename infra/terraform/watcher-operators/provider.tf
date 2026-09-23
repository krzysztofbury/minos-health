provider "aws" {
  region              = "eu-central-1"
  allowed_account_ids = [local.account_id]

  default_tags {
    tags = {
      DataClassification = "sensitive"
      Environment        = "production"
      ManagedBy          = "Terraform"
      Project            = "minos-health"
      Repository         = "krzysztofbury/minos-health"
    }
  }
}

data "aws_caller_identity" "bootstrap" {}

resource "terraform_data" "bootstrap_guard" {
  input = local.account_id

  lifecycle {
    precondition {
      condition = can(regex(
        "^arn:aws:sts::${local.account_id}:assumed-role/AWSReservedSSO_MinosAdministrator_99fe77395d256089/[^/]+$",
        data.aws_caller_identity.bootstrap.arn,
      ))
      error_message = "Operator bootstrap requires the reviewed MinosAdministrator SSO role in the fixed account."
    }
  }
}
