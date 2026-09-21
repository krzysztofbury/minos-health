provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.expected_aws_account_id]

  default_tags {
    tags = merge(var.additional_tags, {
      DataClassification = "sensitive"
      Environment        = "production"
      ManagedBy          = "Terraform"
      Project            = "minos-health"
      Repository         = var.github_repository
    })
  }
}

data "aws_partition" "current" {}
