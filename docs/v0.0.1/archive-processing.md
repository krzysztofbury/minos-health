# Archive Processing And Application Freshness

Status: useful #19 build work retained on `feat/validation-archive-build`,
2026-10-06. The remaining #140 automation work is preserved on
`feat/archive-application-freshness`. The owner resumed #141 on 2026-10-20
(see [the decision](decisions.md#unattended-data-activation-resumed-2026-10-20)):
host-side, data-only activation in phases. Automatic selection, promotion,
activation and detailed Telegram delivery are not enabled yet; reviewed updates
continue until activation is enabled per source class.

## Archive Build Boundary (#19)

The installed operational CLI supports:

```sh
minos data build --manifest dataset.json \
  --archive-s3-selection archive-selection.json \
  --output new-reference-bundle --json
```

`--archive-root` remains the local alternative. Exactly one archive input is
required. The standalone reference distribution continues to accept local
archives only and does not import the R2 adapter or watcher runtime.

The selection is transport provenance, not another dataset/artifact manifest:

```json
{
  "schema_version": 2,
  "dataset_id": "<canonical DatasetManifest SHA-256>",
  "bucket": "minos-archive",
  "endpoint": "https://<account-id>.eu.r2.cloudflarestorage.com",
  "objects": [
    {
      "sha256": "<pinned source SHA-256>",
      "size_bytes": 1234
    }
  ]
}
```

The selection must exactly cover the deduplicated bytes required by all selected
terminology, Komunikat, catalog, conditions, MODTAR XML/display and reviewed
legal-overlay inputs. Missing, additional, duplicate or conflicting objects fail.
Dataset identity and coverage are checked before creating a cloud client. The
source manifest is read once and the verified model is passed to the builder;
rotating its path during transfer cannot change the build's meaning.

Object keys are derived as `blobs/sha256/<digest>`. The caller cannot submit a
key, URL, source payload, command or Git ref; the endpoint must be an R2
account S3 endpoint. Schema 1 pinned S3 version IDs; R2 has no versions, and
the bucket lock refuses overwrites under `blobs/`, so the SHA-256 and size are
the complete pin (#200). Every GET uses `ChecksumMode=ENABLED`. The native
SHA-256 (never a composite checksum) and byte count must match the pins, and streamed
bytes are independently hashed and counted before the local archive is offered
to any parser. There is no mutable-current fallback or upstream fetch.

Bounds: 128 distinct objects, 64 MiB per object, 512 MiB total, 64 KiB stream
chunks, 600-second transfer deadline, 10-second connection timeout, 30-second
read timeout and one SDK attempt. Cooperative deadline checks bracket network
operations and reads; an in-flight request can finish after the deadline, but
its result is rejected. These are refusal limits, not measured throughput or
capacity claims. Client creation uses the SDK credential chain, disables
configured endpoint overrides and does not accept credentials in the selection.
IAM, not the Python adapter, grants the protected workflow read-only access.

Materialization owns one private temporary archive and removes it after success
or failure. The existing builder copies/verifies each local source again, uses
one database transaction and publishes only a complete new bundle. Existing
bundles are never overwritten. The existing `manifest.json`, `minos.db` and
`review.txt` contract is unchanged. JSON CLI success includes
`archive_selection_sha256`, the digest of the canonical transport selection;
processing must retain that selection alongside its candidate audit record.
It is not embedded in the reference database or a new artifact manifest.

SDK assertions and synthetic multi-source build/open/double-build tests cover
local behavior. The disposable real-S3 tests that verified the schema 1 version
pins were retired with AWS (#200); R2 behavior evidence is in #200. Hosted full
real-selection repeated builds remain #19 gates. Do not
upload private raw bytes or source-bearing diffs merely to obtain CI evidence.

## Deferred Target (#21/#141)

The following is the acceptance scope resumed on 2026-10-20. It runs in a
host-side processor container, not GitHub Actions, so step 4's protected-branch
workflow is replaced by that container with its own read-only R2 key.

1. Verify bounded control recovery images and select exact `source_changes`
   rows with source/run/observation/revision identity assertions. Keep capture
   cursors separate from application-processing and activation checkpoints.
2. Persist candidate state, retry/coalescing/fencing and typed incidents with
   durable notification intent. Do not modify the live watcher control database
   from downstream processing or give the watcher GitHub credentials.
3. Generate pinned proposals and R2 transport selections, build/diff candidates,
   and check source-specific interpretation, correspondence, applicability,
   licensing, compatibility and representative resource evidence. Review explicit
   routine/significant-change criteria before enabling promotion. A selected-scope
   #20 diff is not a complete candidate audit.
4. Wire automatic protected-default-branch processing with a scoped read-only
   archive credential. PR jobs receive neither archive nor production credentials.
   A manual retry remains an investigation tool, not the normal update trigger.
5. Promote the exact tested compatible set through the signed/provenance release
   contract, verify actual application changes and historical dates, and restore
   the previous complete compatible set on failed post-activation checks.
6. Deliver distinct per-candidate redacted Telegram failures to the existing
   destination, including when a monitor is already down. Bound and deduplicate
   retries, preserve pending intent during outages and keep delivery failure
   independent of the activation outcome.

## Activation Risk Boundary

#41 is still open: the current validation deployment has exact digest identities
and compatible rollback, but lacks the independently signed complete release
manifest and off-host receipt chain. On 2026-10-20 the owner accepted data-only
activation without #41: data bundles activate with R2 `deployment-receipts/` and
the existing checksums, while code images stay owner-deployed under #41.
Existing source/policy/legal gates are not waived by a successful archive build.
No production configuration or notification secret changes are part of the
first chunk.
