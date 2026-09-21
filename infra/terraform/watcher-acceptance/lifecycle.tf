resource "aws_s3_bucket_lifecycle_configuration" "snapshots" {
  bucket = aws_s3_bucket.snapshots.id

  rule {
    id     = "expire-run-recovery"
    status = "Enabled"
    filter { prefix = "control-recovery/run/" }
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-run-recovery-delete-markers"
    status = "Enabled"
    filter { prefix = "control-recovery/run/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "expire-daily-recovery"
    status = "Enabled"
    filter { prefix = "control-recovery/daily/" }
    expiration { days = 31 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-daily-recovery-delete-markers"
    status = "Enabled"
    filter { prefix = "control-recovery/daily/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "expire-weekly-recovery"
    status = "Enabled"
    filter { prefix = "control-recovery/weekly/" }
    expiration { days = 91 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-weekly-recovery-delete-markers"
    status = "Enabled"
    filter { prefix = "control-recovery/weekly/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "expire-monthly-recovery"
    status = "Enabled"
    filter { prefix = "control-recovery/monthly/" }
    expiration { days = 400 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-monthly-recovery-delete-markers"
    status = "Enabled"
    filter { prefix = "control-recovery/monthly/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "expire-trusted-recovery"
    status = "Enabled"
    filter { prefix = "trusted-recovery/" }
    expiration { days = 400 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-trusted-recovery-delete-markers"
    status = "Enabled"
    filter { prefix = "trusted-recovery/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "expire-checksum-probes"
    status = "Enabled"
    filter { prefix = "probes/" }
    expiration { days = 7 }
    noncurrent_version_expiration { noncurrent_days = 1 }
  }

  rule {
    id     = "remove-checksum-probe-delete-markers"
    status = "Enabled"
    filter { prefix = "probes/" }
    expiration { expired_object_delete_marker = true }
  }

  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 7 }
  }

  depends_on = [
    aws_s3_bucket.snapshots,
    aws_s3_bucket_versioning.snapshots,
  ]
}
