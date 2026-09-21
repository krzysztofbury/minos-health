variable "aws_region" {
  description = "AWS region containing the Minos Terraform state bucket."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}-[a-z]+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "expected_aws_account_id" {
  description = "Twelve-digit AWS account ID allowed to receive the state bucket."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "expected_aws_account_id must contain exactly 12 digits."
  }
}

variable "state_bucket_prefix" {
  description = "Prefix combined with the account ID and region for global uniqueness."
  type        = string
  default     = "minos-health-tfstate"

  validation {
    condition = (
      length(var.state_bucket_prefix) >= 3 &&
      length(var.state_bucket_prefix) <= 30 &&
      can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.state_bucket_prefix))
    )
    error_message = "state_bucket_prefix must be 3-30 lowercase letters, digits, or hyphens and cannot start or end with a hyphen."
  }
}

variable "additional_tags" {
  description = "Additional non-sensitive tags applied without overriding required ownership tags."
  type        = map(string)
  default     = {}
}
