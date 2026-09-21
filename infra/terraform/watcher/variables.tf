variable "aws_region" {
  description = "AWS region containing the watcher archive resources."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "expected_aws_account_id" {
  description = "Twelve-digit AWS account ID allowed to receive watcher resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_aws_account_id))
    error_message = "expected_aws_account_id must contain exactly 12 digits."
  }
}

variable "snapshot_bucket_prefix" {
  description = "Prefix combined with the account ID and region for global uniqueness."
  type        = string
  default     = "minos-health-snapshots"

  validation {
    condition = (
      length(var.snapshot_bucket_prefix) >= 3 &&
      length(var.snapshot_bucket_prefix) <= 30 &&
      can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.snapshot_bucket_prefix))
    )
    error_message = "snapshot_bucket_prefix must be 3-30 lowercase letters, digits, or hyphens and cannot start or end with a hyphen."
  }
}

variable "github_repository" {
  description = "GitHub owner and repository permitted to assume the archive read role."
  type        = string

  validation {
    condition = can(regex(
      "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$",
      var.github_repository,
    ))
    error_message = "github_repository must use the owner/repository form."
  }
}

variable "github_ref" {
  description = "Fully qualified protected Git ref permitted to assume the archive read role."
  type        = string
  default     = "refs/heads/main"

  validation {
    condition     = can(regex("^refs/(heads|tags)/[^:[:space:]]+$", var.github_ref))
    error_message = "github_ref must be a fully qualified branch or tag ref without whitespace or colons."
  }
}

variable "monthly_budget_amount" {
  description = "Approved monthly AWS cost budget in USD."
  type        = number

  validation {
    condition = (
      var.monthly_budget_amount > 0 &&
      var.monthly_budget_amount <= 20
    )
    error_message = "monthly_budget_amount must be greater than zero and no more than the approved USD 20 monthly ceiling."
  }
}

variable "budget_currency" {
  description = "Billing currency for the approved USD monthly cost budget."
  type        = string

  validation {
    condition     = var.budget_currency == "USD"
    error_message = "budget_currency must be USD."
  }
}

variable "budget_subscriber_email" {
  description = "Reviewed operator email that must confirm the AWS budget alert subscription."
  type        = string

  validation {
    condition = (
      length(var.budget_subscriber_email) <= 254 &&
      can(regex("^[^@[:space:]]+@[^@[:space:]]+\\.[^@[:space:]]+$", var.budget_subscriber_email))
    )
    error_message = "budget_subscriber_email must be a valid email address no longer than 254 characters."
  }
}

variable "additional_tags" {
  description = "Additional non-sensitive tags applied without overriding required ownership tags."
  type        = map(string)
  default     = {}
}
