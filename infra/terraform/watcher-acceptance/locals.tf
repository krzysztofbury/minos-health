locals {
  known_production_account_id = "218375352108"
  production_confirmation     = "ACCEPT-DISPOSABLE-RESOURCES:${local.known_production_account_id}"
  expected_operator_arn       = "arn:aws:iam::${local.known_production_account_id}:role/minos-health-watcher-acceptance-operator"
  permissions_boundary_arn    = "arn:aws:iam::${local.known_production_account_id}:policy/minos-health-watcher-acceptance-boundary"
  resource_prefix             = "minos-health-watcher-acceptance"
  resource_name               = "${local.resource_prefix}-${var.test_run_id}"
  bucket_name                 = "${local.resource_prefix}-${local.known_production_account_id}-${var.test_run_id}"
  bucket_arn                  = "arn:aws:s3:::${local.bucket_name}"
  watcher_role_arn            = "arn:aws:iam::${local.known_production_account_id}:role/${local.resource_name}-watcher"

  acceptance_tags = {
    Disposable  = "true"
    Environment = "acceptance"
    ManagedBy   = "Terraform"
    Project     = "minos-health"
    TestHarness = "watcher-acceptance"
    TestRunId   = var.test_run_id
  }

  watcher_object_prefixes = [
    "blobs/sha256",
    "control-recovery",
    "probes",
  ]

  immutable_object_prefixes = [
    "blobs/sha256",
    "control-recovery",
    "deployment-receipts",
    "probes",
    "recovery-inventories",
    "release-manifests",
    "trusted-recovery",
  ]
}
