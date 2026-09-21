# Budget messages contain only AWS cost thresholds, so a KMS key is out of scope.
#trivy:ignore:AVD-AWS-0095:exp:2027-03-20
resource "aws_sns_topic" "budget_alerts" {
  name = "minos-health-watcher-budget-alerts"
}

data "aws_iam_policy_document" "budget_alerts" {
  statement {
    sid     = "AllowAWSBudgetsToPublish"
    effect  = "Allow"
    actions = ["SNS:Publish"]
    resources = [
      aws_sns_topic.budget_alerts.arn,
    ]

    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "AWS:SourceAccount"
      values   = [var.expected_aws_account_id]
    }

    condition {
      test     = "ArnLike"
      variable = "AWS:SourceArn"
      values = [
        "arn:${data.aws_partition.current.partition}:budgets::${var.expected_aws_account_id}:*",
      ]
    }
  }
}

resource "aws_sns_topic_policy" "budget_alerts" {
  arn    = aws_sns_topic.budget_alerts.arn
  policy = data.aws_iam_policy_document.budget_alerts.json
}

resource "aws_sns_topic_subscription" "budget_alert_email" {
  topic_arn = aws_sns_topic.budget_alerts.arn
  protocol  = "email"
  endpoint  = var.budget_subscriber_email

  confirmation_timeout_in_minutes = 10
  endpoint_auto_confirms          = false
}

resource "aws_budgets_budget" "project" {
  name         = "minos-health-watcher-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_amount)
  limit_unit   = var.budget_currency
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "TagKeyValue"
    values = ["user:Project$minos-health"]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 80
    threshold_type            = "PERCENTAGE"
    notification_type         = "ACTUAL"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  notification {
    comparison_operator       = "GREATER_THAN"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    notification_type         = "FORECASTED"
    subscriber_sns_topic_arns = [aws_sns_topic.budget_alerts.arn]
  }

  depends_on = [aws_sns_topic_policy.budget_alerts]
}
