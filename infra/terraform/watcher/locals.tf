locals {
  snapshot_bucket_name       = "${var.snapshot_bucket_prefix}-${var.expected_aws_account_id}-${var.aws_region}"
  watcher_boundary_arn       = "arn:aws:iam::${var.expected_aws_account_id}:policy/minos-health-watcher-boundary"
  github_reader_boundary_arn = "arn:aws:iam::${var.expected_aws_account_id}:policy/minos-health-watcher-github-reader-boundary"

  watcher_object_prefixes = [
    "blobs/sha256",
    "control-recovery",
    "probes",
  ]

  github_read_object_prefixes = [
    "blobs/sha256",
    "control-recovery",
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
