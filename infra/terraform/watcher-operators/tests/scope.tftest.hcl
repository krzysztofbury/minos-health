mock_provider "aws" {}

override_data {
  target = data.aws_caller_identity.bootstrap
  values = {
    account_id = "218375352108"
    arn        = "arn:aws:sts::218375352108:assumed-role/AWSReservedSSO_MinosAdministrator_99fe77395d256089/review"
  }
}

override_resource {
  target          = aws_iam_policy.production_operator_boundary
  override_during = plan
  values = {
    arn = "arn:aws:iam::218375352108:policy/minos-health-watcher-terraform-operator-boundary"
  }
}

override_resource {
  target          = aws_iam_policy.acceptance_operator_boundary
  override_during = plan
  values = {
    arn = "arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-operator-boundary"
  }
}

run "fixed_principals_and_bounded_policy_documents" {
  command = plan

  assert {
    condition = (
      aws_iam_role.acceptance_operator.name == "minos-health-watcher-acceptance-operator" &&
      aws_iam_policy.acceptance_watcher_boundary.name == "minos-health-watcher-acceptance-boundary"
    )
    error_message = "Acceptance principal and boundary must match the tracked harness identities."
  }

  assert {
    condition = (
      aws_iam_role.production_operator.permissions_boundary == local.production_operator_boundary_arn &&
      aws_iam_role.acceptance_operator.permissions_boundary == local.acceptance_operator_boundary_arn
    )
    error_message = "Human operator roles must have their independent permission ceilings."
  }

  assert {
    condition = (
      length(local.production_operator_policy) <= 6144 &&
      length(local.acceptance_operator_policy) <= 6144 &&
      length(local.watcher_boundary_policy) <= 6144 &&
      length(local.github_reader_boundary_policy) <= 6144 &&
      length(local.acceptance_watcher_boundary_policy) <= 6144 &&
      length(local.state_recovery_policy) <= 6144
    )
    error_message = "A managed IAM policy exceeds the 6144-character AWS limit."
  }

  assert {
    condition = (
      sha256(local.acceptance_operator_policy) == "124dbccc1b978439d325130e7ae37cde0caa0be44363e939524e0f5247f73d86" &&
      sha256(local.acceptance_watcher_boundary_policy) == "91ab68d1b81b319c49134c39730361e3e4598aa7f6a5ac41014f0e6b38185d0b" &&
      sha256(local.github_reader_boundary_policy) == "73bcefd93a5886a60fc799194ac4c5568823fe7f86799257d7eb961da9930f80" &&
      sha256(local.production_operator_policy) == "f13c1342409b8bd4d9e68c53e6485447871d1fa769bed14a0c2588f0f40b431b" &&
      sha256(local.state_recovery_policy) == "4aee3685f6cb01246537c1202fd6a1d0ba4839d57aaf2f0ccbb8a6302672ed88" &&
      sha256(local.watcher_boundary_policy) == "f869bbd94ba056a2ea4641cba5f6f34d3173d798f760da0ab0d2f581409757ce"
    )
    error_message = "Every operator policy requires its own reviewed digest update."
  }

  assert {
    condition = (
      aws_iam_policy.watcher_boundary.name == "minos-health-watcher-boundary" &&
      aws_iam_policy.github_reader_boundary.name == "minos-health-watcher-github-reader-boundary"
    )
    error_message = "The two production principals need distinct fixed S3 boundaries."
  }

  assert {
    condition = (
      !strcontains(local.watcher_boundary_policy, "s3:DeleteObject") &&
      !strcontains(local.github_reader_boundary_policy, "s3:PutObject") &&
      !strcontains(local.acceptance_watcher_boundary_policy, "s3:DeleteObject")
    )
    error_message = "The runtime boundaries must not permit raw deletion or GitHub writes."
  }

  assert {
    condition = (
      !strcontains(local.state_recovery_policy, "s3:DeleteObjectVersion") &&
      !strcontains(local.state_recovery_policy, "s3:DeleteBucket") &&
      strcontains(local.state_recovery_policy, "s3:if-match") &&
      aws_iam_role.state_recovery.name == "minos-health-watcher-state-recovery"
    )
    error_message = "The off-host state recovery identity must never delete state versions."
  }

  assert {
    condition = length([
      for statement in jsondecode(local.acceptance_operator_policy).Statement : statement
      if statement.Sid == "CreateOnlyBoundedDisposableWatcherRoles" &&
      statement.Condition.StringEquals["iam:PermissionsBoundary"] == local.acceptance_role_boundary_arn &&
      statement.Condition.StringEquals["aws:RequestTag/Project"] == "minos-health" &&
      statement.Condition.Null["aws:RequestTag/TestRunId"] == "false" &&
      length(statement.Condition["ForAllValues:StringEquals"]["aws:TagKeys"]) == 6
    ]) == 1
    error_message = "Disposable role creation must require the fixed boundary and complete tag set."
  }

  assert {
    condition = (
      !strcontains(local.production_operator_policy, "iam:PassRole") &&
      !strcontains(local.production_operator_policy, "iam:CreateAccessKey") &&
      !strcontains(local.acceptance_operator_policy, "iam:PassRole") &&
      !strcontains(local.acceptance_operator_policy, "iam:CreateAccessKey") &&
      !strcontains(local.acceptance_operator_policy, "watcher/terraform.tfstate")
    )
    error_message = "Neither operator may pass roles, create host keys, or expose Terraform state to acceptance."
  }
}
