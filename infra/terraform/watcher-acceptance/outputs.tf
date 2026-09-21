output "aws_account_id" {
  description = "Account independently reported by STS during apply."
  value       = data.aws_caller_identity.current.account_id
}

output "aws_region" {
  description = "Region containing the disposable resources."
  value       = var.aws_region
}

output "bucket_name" {
  description = "Randomized disposable versioned snapshot bucket."
  value       = local.bucket_name
}

output "operator_principal_arn" {
  description = "Principal bound into the watcher role trust policy."
  value       = local.expected_operator_arn
}

output "test_run_id" {
  description = "Random identifier tagging this disposable test run."
  value       = var.test_run_id
}

output "watcher_role_arn" {
  description = "Short-session role used by the watcher acceptance tests."
  value       = local.watcher_role_arn
}

output "watcher_role_name" {
  description = "Name used to verify role deletion after destroy."
  value       = aws_iam_role.watcher.name
}

output "watcher_role_permissions_boundary_arn" {
  description = "Fixed externally provisioned permissions boundary on the disposable role."
  value       = local.permissions_boundary_arn
}

output "watcher_role_unique_id" {
  description = "AWS immutable RoleId used to bind live teardown checks."
  value       = aws_iam_role.watcher.unique_id
}
