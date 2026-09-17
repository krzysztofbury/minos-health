output "state_bucket_name" {
  description = "Bucket used by later Terraform roots for remote state."
  value       = aws_s3_bucket.terraform_state.id
}

output "state_bucket_arn" {
  description = "ARN of the Terraform state bucket."
  value       = aws_s3_bucket.terraform_state.arn
}

output "aws_region" {
  description = "Region containing the Terraform state bucket."
  value       = var.aws_region
}
