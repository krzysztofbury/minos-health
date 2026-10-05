# Archive Processing And Application Freshness

Status: #19/#21 implementation started on `feat/archive-application-freshness`.
The first chunk adds explicitly pinned S3 materialization to the existing
reference builder. Automatic selection, promotion, activation and detailed
Telegram delivery are not implemented or enabled by this chunk.

## Archive Build Boundary (#19)

The installed operational CLI supports:

```sh
minos data build --manifest dataset.json \
  --archive-s3-selection archive-selection.json \
  --output new-reference-bundle --json
```

`--archive-root` remains the local alternative. Exactly one archive input is
required. The standalone reference distribution continues to accept local
archives only and does not import the S3 adapter or watcher runtime.

The selection is transport provenance, not another dataset/artifact manifest:

```json
{
  "schema_version": 1,
  "dataset_id": "<canonical DatasetManifest SHA-256>",
  "bucket": "<reviewed snapshot bucket>",
  "region": "eu-central-1",
  "expected_bucket_owner": "<12-digit AWS account ID>",
  "objects": [
    {
      "sha256": "<pinned source SHA-256>",
      "size_bytes": 1234,
      "version_id": "<exact non-null S3 version ID>"
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
key, URL, endpoint, source payload, command or Git ref. Every GET requests the
exact version with `ExpectedBucketOwner` and `ChecksumMode=ENABLED`. Response
version, full-object native SHA-256 and byte count must match the pins, and streamed
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
local behavior. Eight disposable real-S3 tests additionally verify pinned CLI
builds, owner/native checksum/version refusals and a session-scoped read-only
boundary; see [evidence](../validation/archive-freshness.md). Protected-ref OIDC
and hosted full real-selection repeated builds remain #19 gates. Do not
upload private raw bytes or source-bearing diffs merely to obtain CI evidence.

## Read-Only Change Identity Boundary (#21)

`minos.ingest.processing.recovery_changes` reads a transport-verified recovery
image inside its download lease. It has no network adapter, production trigger,
proposal builder or persistence. No processing/activation checkpoint advances.

The reader rehashes the opened regular file and reuses recovery validation for
SQLite integrity, foreign keys, exact schema/migrations and embedded metadata.
It then opens an immutable, query-only SQLite connection through that descriptor,
with trusted schema disabled. File identity is checked again before returning.
Recovery key, non-null S3 version, digest, size and embedded revision/migration
identities are retained and checked again during exact change reselection.
S3 owner, native checksum and configured bucket trust remain the existing
transport adapter's responsibility, not evidence supplied by a local path.

A source window selects at most 128 changes after an explicit captured revision,
ordered by revision and change ID. Source/run/observation joins must agree;
the captured run and observation revisions must equal the change revision.
Captured configuration bytes must match their stored checksum. Missing sources,
checkpoints ahead of the image and oversized windows fail rather than returning
a partial batch or skipping a revision. Serialized batches also reject duplicate,
unsorted, wrong-source and out-of-window identities.

Returned records contain bounded identifier tokens and digests, not configuration,
summaries, headers, source URLs, cursor JSON, unrelated observations or local paths.
Private text is hashed one row at a time, after a materialized bounded selection
of narrow change IDs. It is not sorted or written to a temporary SQLite file.
Queries use fixed SQL and bound parameters. A 300-second cooperative deadline,
2 MiB SQLite row limit and the existing 2 GiB recovery-image bound are refusal
limits, not representative throughput/capacity claims.

The selected observation can be a catalog/metadata response. Its digest is not
necessarily a changed product's digest; a summary hash is not a before/after
blob pin. Source-specific interpretation and complete product/version selection
are still required before constructing any data proposal. This reader does not
implement #21's automatic selector, coalescing, before/after assertions, policy
gates, deployment or Telegram delivery, and none is enabled by this chunk.

## Subsequent Chunks (#21)

1. Wire the bounded recovery/change reader into exact-version automatic selection
   and source-specific before/after/product identity assertions. Keep capture
   cursors separate from application-processing and activation checkpoints.
2. Persist candidate state, retry/coalescing/fencing and typed incidents with
   durable notification intent. Do not modify the live watcher control database
   from downstream processing or give the watcher GitHub credentials.
3. Generate pinned proposals and S3 transport selections, build/diff candidates,
   and check source-specific interpretation, correspondence, applicability,
   licensing, compatibility and representative resource evidence. Review explicit
   routine/significant-change criteria before enabling promotion. A selected-scope
   #20 diff is not a complete candidate audit.
4. Wire automatic protected-default-branch processing with the scoped OIDC
   archive reader. PR jobs receive neither archive nor production credentials.
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
manifest and off-host receipt chain. #21 explicitly requires that trusted
contract and must not silently substitute the manual validation exception.
Implement/review the needed release boundary before enabling unattended
activation. Existing source/policy/legal gates are not waived by a successful
archive build. No production configuration or notification secret changes are
part of the first chunk.

The owner accepted the [delegated authority model](routine-data-authority-proposal.md)
for offline verification/refusal tests and retained the canonical embedded dataset.
The first verifier checks mathematical signatures and pinned scope only, explicitly
not activation authorization. Custody, finite authority limits, ledger/IAM changes,
independent artifact/authority proofs and production enablement remain gated.
#41's live release contract is unchanged; no operational key is provisioned.
