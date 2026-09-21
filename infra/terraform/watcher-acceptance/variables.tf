variable "aws_region" {
  description = "AWS region used only for the disposable watcher acceptance harness."
  type        = string
  default     = "eu-central-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region identifier."
  }
}

variable "expected_test_account_id" {
  description = "Exact repository account allowed to receive disposable acceptance resources."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.expected_test_account_id))
    error_message = "expected_test_account_id must contain exactly 12 digits."
  }
}

variable "production_account_id" {
  description = "Operator confirmation of the repository production account used for acceptance."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.production_account_id))
    error_message = "production_account_id must contain exactly 12 digits."
  }
}

variable "production_acceptance_enabled" {
  description = "Explicit boolean opt-in for disposable acceptance in the production account."
  type        = bool
}

variable "production_acceptance_confirmation" {
  description = "Exact phrase confirming disposable acceptance in the built-in account."
  type        = string
}

variable "test_run_id" {
  description = "Pre-generated immutable identity for the reviewed disposable run."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{16}$", var.test_run_id))
    error_message = "test_run_id must contain exactly 16 lowercase hexadecimal characters."
  }
}
