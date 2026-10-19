# R2 Migration Evidence (#200)

Status: code, docs and the archive copy are complete on the migration branch;
the production cutover and AWS retirement are recorded below as they happen.
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

The code therefore never sends the bucket-owner header, accepts a missing
`ChecksumType` but never a composite one, pins recovery downloads with
`If-Match`, and implements the trusted copy as a verified download plus a
create-once upload.

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

## Cutover Procedure

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

Pending cutover.
