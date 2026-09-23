locals {
  account_id        = "218375352108"
  region            = "eu-central-1"
  state_bucket      = "minos-health-tfstate-${local.account_id}-${local.region}"
  state_bucket_arn  = "arn:aws:s3:::${local.state_bucket}"
  snapshot_bucket   = "minos-health-snapshots-${local.account_id}-${local.region}"
  snapshot_arn      = "arn:aws:s3:::${local.snapshot_bucket}"
  acceptance_bucket = "minos-health-watcher-acceptance-*"
  acceptance_arn    = "arn:aws:s3:::${local.acceptance_bucket}"

  sso_role_arn                     = "arn:aws:iam::${local.account_id}:role/aws-reserved/sso.amazonaws.com/${local.region}/AWSReservedSSO_MinosAdministrator_99fe77395d256089"
  production_operator_name         = "minos-health-watcher-terraform-operator"
  acceptance_operator_name         = "minos-health-watcher-acceptance-operator"
  recovery_operator_name           = "minos-health-watcher-state-recovery"
  production_operator_arn          = "arn:aws:iam::${local.account_id}:role/${local.production_operator_name}"
  acceptance_operator_arn          = "arn:aws:iam::${local.account_id}:role/${local.acceptance_operator_name}"
  acceptance_watcher_arn           = "arn:aws:iam::${local.account_id}:role/minos-health-watcher-acceptance-*-watcher"
  watcher_user_arn                 = "arn:aws:iam::${local.account_id}:user/minos-health-watcher"
  github_reader_arn                = "arn:aws:iam::${local.account_id}:role/minos-health-watcher-github-archive-reader"
  watcher_archive_arn              = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-archive"
  github_oidc_arn                  = "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com"
  acceptance_role_boundary_arn     = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-acceptance-boundary"
  watcher_boundary_arn             = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-boundary"
  github_boundary_arn              = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-github-reader-boundary"
  production_operator_boundary_arn = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-terraform-operator-boundary"
  acceptance_operator_boundary_arn = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-acceptance-operator-boundary"
  state_recovery_boundary_arn      = "arn:aws:iam::${local.account_id}:policy/minos-health-watcher-state-recovery-boundary"

  snapshot_prefixes = ["blobs/sha256", "control-recovery", "probes"]
  github_prefixes   = ["blobs/sha256", "control-recovery"]
  state_keys = [
    "watcher/terraform.tfstate",
    "watcher-operators/terraform.tfstate",
  ]
}
