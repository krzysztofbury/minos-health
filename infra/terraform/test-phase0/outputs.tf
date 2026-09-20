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
  value       = aws_s3_bucket.snapshots.id
}

output "operator_principal_arn" {
  description = "Principal bound into the watcher role trust policy."
  value       = var.operator_principal_arn
}

output "test_run_id" {
  description = "Random identifier tagging this disposable test run."
  value       = random_id.suffix.hex
}

output "watcher_role_arn" {
  description = "Short-session role used by the watcher acceptance tests."
  value       = aws_iam_role.watcher.arn
}

output "watcher_role_name" {
  description = "Name used to verify role deletion after destroy."
  value       = aws_iam_role.watcher.name
}
