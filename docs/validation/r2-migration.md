# R2 Migration Evidence (#200)

Status: complete. The watcher writes to R2 since 2026-10-19 and Minos's AWS
resources were retired on 2026-10-20 (#200, closed).
The decision is in [decisions.md](../v0.0.1/decisions.md#object-storage-on-cloudflare-r2-200-2026-10-19).

## Bucket

One EU-jurisdiction R2 bucket, `minos-archive`, created by the owner in the
Cloudflare dashboard on 2026-10-19. The S3 API endpoint is
`https://<account-id>.eu.r2.cloudflarestorage.com` and the region is `auto`.

| Prefix | Bucket lock | Lifecycle deletion |
| --- | --- | --- |
| `blobs/` | indefinite | never |
| `control-recovery/run/` | 30 days | 30 days |
| `control-recovery/daily/` | 31 days | 31 days |
| `control-recovery/weekly/` | 91 days | 91 days |
| `control-recovery/monthly/` | 400 days | 400 days |
| `trusted-recovery/` | 400 days | 400 days |
| `recovery-inventories/` | indefinite | never |
| `release-manifests/`, `deployment-receipts/` | indefinite | never |
| `probes/` | none | 7 days |

These are the S3 lifecycle ages, now enforced as lock ages too, so each object
is immutable for its whole retained life. Incomplete multipart uploads abort
after seven days.

Tokens, both Object Read & Write and limited to `minos-archive`, are stored in
the MinosHealth vault: `minos-r2-watcher` for the host, and `minos-r2-operator`
for copies and drills. The host credential document is
`minos-production-watcher-r2-credentials`.

## Capability Check (2026-10-19)

Run with the operator token against `probes/r2-spike/`; every probe object was
deleted afterwards.

| Contract | Result |
| --- | --- |
| `PutObject` with `If-None-Match: *` on an existing key | `412 PreconditionFailed` |
| Wrong `x-amz-checksum-sha256` | `400 BadDigest` |
| HEAD and GET with `ChecksumMode=ENABLED` | SHA-256 returned and matches; no `ChecksumType` |
| GET or copy source with a stale `If-Match` ETag | `412` |
| `x-amz-expected-bucket-owner` | `501 Not Implemented` |
| `CopyObject` with `If-None-Match` onto an existing key | overwrote it (ignored) and dropped the SHA-256 |
| Object versions | none returned by HEAD/GET |
| `PutObject` with `If-None-Match: *` onto an existing key under a lock rule | `409 ObjectLockedByBucketPolicy`; object unchanged |

The code therefore never sends the bucket-owner header, accepts a missing
`ChecksumType` but never a composite one, pins recovery downloads with
`If-Match`, and implements the trusted copy as a verified download plus a
create-once upload.

The lock row was measured after the lock rules existed (2026-10-19, after the
first capability check): R2 evaluates the lock before `If-None-Match`, so an
existing locked key never answers `412`. Every create-once writer therefore
treats that exact `409` like `412` and then verifies the stored SHA-256 and size;
any other `409` is still a bounded retry. Re-archiving an existing 173-byte blob
through `S3BlobArchive` against the real bucket returned a verified receipt and
left the object's ETag and `LastModified` unchanged.

## Archive Copy (2026-10-19)

Source: `minos-health-snapshots-...-eu-central-1`, 553 objects of 1.31 GB, each
with exactly one S3 version. The four `probes/` objects were not copied. For
every `blobs/` and `control-recovery/` object, the copy read the S3 bytes,
checked the SHA-256 in the key and the size, created the R2 object
create-once with that SHA-256, and verified the R2 HEAD checksum and size.

Result: 549 copied, 0 already present, 1,307,239,406 bytes, 0 failures.

The copy is idempotent: an existing R2 object must already match. It runs again
after the watcher cutover, so any object the old watcher wrote to S3 before the
swap also reaches R2 before AWS is retired.

## Cutover Record

The cutover ran once, on 2026-10-19, in these steps; it is a record, not a
procedure to repeat, and the AWS rollback target it names no longer exists.

1. Merge, then dispatch `ci.yml` and `publish-watcher.yml` on `main`.
2. Plan the watcher release with the one-time storage migration. The key file
   comes from the vault into a private temporary directory:

   ```sh
   uv run python -m scripts.deploy_watcher plan --plan <dir>/plan.json \
     --revision <main sha> --image <published watcher digest> \
     --publish-run <publish run id> \
     --storage-bucket minos-archive \
     --storage-endpoint https://<account-id>.eu.r2.cloudflarestorage.com \
     --storage-credentials <tmp>/watcher-r2-credentials
   ```

3. `stage` (with the same `--storage-credentials`, a mode-`0600` file) uploads
   the key by `scp` into root's home directory, refuses it unless it is a
   private root-owned regular file with the R2 schema and the planned digest,
   deletes the upload, and runs the isolated preflight with the new
   image, the R2 environment and the staged key: the R2 checksum probe and the
   recovery round trip must pass before anything live changes.
4. `activate` saves the live credential file, installs the R2 key and the R2
   environment together, and starts the new watcher. A failed activation or an
   explicit `rollback` restores the previous Compose and credential file, and so
   AWS, until AWS is retired.
5. Rerun the archive copy, confirm that every S3 `blobs/` and
   `control-recovery/` key exists in R2, and record the first watcher run whose
   recovery image lands in R2.

## Production Evidence

Cutover on 2026-10-19 at `36402de` (#201), watcher image
`sha256:fcd0ebd396eec89a6793df6eb038ea4f9c33ed5b92e6bf4a44ead462378f544d` from
publish run 39312453776, after main CI passed:

- `plan` recorded the live Compose digest and the R2 key digest.
- `stage` accepted the uploaded key and passed the isolated preflight with the
  new image against R2: the checksum probe and the recovery round trip.
- `activate` swapped the key and the R2 environment with the Compose and
  reported `healthy: true` and `watcher_image_matches: true`.
- The post-cutover copy found all 549 `blobs/` and `control-recovery/` objects
  from S3 already present in R2 with matching SHA-256 and size: 0 copied,
  0 failures. The first rerun stopped on the bucket lock's `409`, which led to
  the lock-answer fix above.

The lock-answer fix (#203) was released at `39d0cf6` the same evening with image
`sha256:7020a074104cc3d08a11ecc3508fd7b5ce7f19e33c7079099a7ebfe3952be174`
(publish run 39318981851): stage preflight passed, activate reported
`healthy: true` and `watcher_image_matches: true`.

First scheduled runs on R2, 2026-10-20 (UTC), from the bucket listing:

| Time | Run | Written to R2 |
| --- | --- | --- |
| 00:15 | daily recovery image | `control-recovery/daily/` |
| 03:00 | RSK | run recovery image; no new blobs, so unchanged objects were re-archived through the lock answer |
| 04:00 | NFZ | run recovery image and new blobs |
| 05:00 | BAW | run recovery image and new blobs |

NFZ and BAW added 15 blobs in total.

## AWS Retirement (2026-10-20)

Owner-approved, exact names, in the project's AWS account; the account's
unrelated resources were not touched.

- S3: the snapshots bucket (553 versions), the Terraform state bucket (38
  versions, 27 delete markers) and the empty acceptance bucket, emptied and
  deleted.
- IAM: five `minos-health-watcher-*` roles, the `minos-health-watcher` user with
  the old production access key, seven customer policies and the GitHub OIDC
  provider, which only the archive-reader role trusted.
- The `minos-health-watcher-monthly` budget and its SNS topic.
- IAM Identity Center: the `MinosAdministrator` assignment and permission set;
  the owner then removed the Minos SSO user and the instance.
- Secrets: the three AWS vault items archived, not deleted; the retired AWS key
  copy removed from the host, which keeps only its live R2 key.
