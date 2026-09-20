locals {
  known_production_account_id = "218375352108"
  resource_name               = "${var.resource_prefix}-${random_id.suffix.hex}"
  bucket_name                 = "${var.resource_prefix}-${var.expected_test_account_id}-${random_id.suffix.hex}"

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
