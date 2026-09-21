output "snapshot_bucket_name" {
  description = "Private versioned bucket used for watcher snapshots and records."
  value       = aws_s3_bucket.snapshots.id
}

output "snapshot_bucket_arn" {
  description = "ARN of the private watcher snapshot bucket."
  value       = aws_s3_bucket.snapshots.arn
}

output "aws_region" {
  description = "AWS region containing the watcher resources."
  value       = var.aws_region
}

output "expected_aws_account_id" {
  description = "AWS account ID guarded by the provider configuration."
  value       = var.expected_aws_account_id
}

output "watcher_iam_user_name" {
  description = "IAM user whose access key must be created and installed manually."
  value       = aws_iam_user.watcher.name
}

output "watcher_iam_user_arn" {
  description = "ARN of the narrowly scoped watcher IAM user."
  value       = aws_iam_user.watcher.arn
}

output "watcher_iam_policy_arn" {
  description = "ARN of the watcher archive policy."
  value       = aws_iam_policy.watcher_archive.arn
}

output "github_archive_reader_role_arn" {
  description = "Role assumed by the reviewed protected GitHub ref for archive reads."
  value       = aws_iam_role.github_archive_reader.arn
}

output "budget_name" {
  description = "Name of the monthly project cost budget."
  value       = aws_budgets_budget.project.name
}

output "budget_alert_topic_arn" {
  description = "SNS topic carrying project budget alerts."
  value       = aws_sns_topic.budget_alerts.arn
}

output "budget_alert_subscription_arn" {
  description = "Confirmed SNS email subscription ARN; pending confirmation is not accepted."
  value       = aws_sns_topic_subscription.budget_alert_email.arn
}

output "budget_alert_subscriber_email" {
  description = "Expected operator email endpoint for the budget alert subscription check."
  value       = var.budget_subscriber_email
  sensitive   = true
}
