output "aws_account_id" {
  value = local.account_id
}

output "production_operator_role_arn" {
  value = aws_iam_role.production_operator.arn
}

output "acceptance_operator_role_arn" {
  value = aws_iam_role.acceptance_operator.arn
}

output "state_recovery_role_arn" {
  value = aws_iam_role.state_recovery.arn
}

output "watcher_boundary_arn" {
  value = aws_iam_policy.watcher_boundary.arn
}

output "github_reader_boundary_arn" {
  value = aws_iam_policy.github_reader_boundary.arn
}

output "acceptance_watcher_boundary_arn" {
  value = aws_iam_policy.acceptance_watcher_boundary.arn
}

output "state_bucket_name" {
  value = local.state_bucket
}
