variable "aws_region" {
  description = "AWS region used only for the disposable phase-0 acceptance harness."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "expected_test_account_id" {
  description = "Dedicated non-production AWS account allowed to receive test resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_test_account_id))
    error_message = "expected_test_account_id must contain exactly 12 digits."
  }
}

variable "production_account_id" {
  description = "Operator confirmation of a production AWS account that this harness must never target."
  type        = string

  validation {
    condition = (
      can(regex("^[0-9]{12}$", var.production_account_id)) &&
      var.production_account_id != var.expected_test_account_id
    )
    error_message = "production_account_id must be a different twelve-digit account ID."
  }
}

variable "operator_principal_arn" {
  description = "Exact IAM user or role principal allowed to assume the watcher test role."
  type        = string

  validation {
    condition = can(regex(
      "^arn:[a-z0-9-]+:iam::[0-9]{12}:(user|role)/[A-Za-z0-9+=,.@_/-]+$",
      var.operator_principal_arn,
    ))
    error_message = "operator_principal_arn must be an IAM user or role ARN."
  }
}

variable "resource_prefix" {
  description = "Short lowercase prefix for randomized disposable resources."
  type        = string
  default     = "minos-health-p0-accept"

  validation {
    condition = (
      length(var.resource_prefix) >= 3 &&
      length(var.resource_prefix) <= 24 &&
      can(regex("^[a-z0-9][a-z0-9-]*[a-z0-9]$", var.resource_prefix))
    )
    error_message = "resource_prefix must be 3-24 lowercase letters, digits, or hyphens."
  }
}
