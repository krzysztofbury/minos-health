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

## Shadow ICD-10 Candidate (#141 Phase B1)

The operational CLI judges one archived RSK ICD-10 release against the live
bundle without activating anything:

```sh
minos data candidate-icd10 --live-bundle <live bundle> \
  --archive-root <archive> --product-sha256 <digest> \
  --basic-info-sha256 <digest> --fetched-at <observation time> \
  --policy manifests/policy/rsk-icd10-automatic.json --work <new directory>
```

The digests come from the watcher's `rsk_source_change` record and the archived
`00CD10` basic-info. The candidate is the live dataset plus the new release:

- the previous open RSK release is closed at the new `validDateFrom`, so earlier
  service dates keep answering from it, and each JGP catalog keeps the
  terminology release it was built with;
- the new entry takes its version, validity, file identity and creation time
  from RSK basic-info, and its licence and notices from the owner policy;
- ICD-10 synonyms pinned to the previous release become unscoped. The build
  accepts an unscoped terminology synonym only if its reviewed wording exists
  in every release of its system.

`manifests/policy/rsk-icd10-automatic.json` is the owner's class-level approval
(2026-10-20). It pins the SHA-256 of the RSK description, which carries the
licence statement, and the publisher; any change refuses the release. It also
bounds removed codes (1%) and the concept count change (5%).

The command prints one JSON report with no paths or source text and exits 0
for `would_activate`, 9 for a refusal that names its criterion (`bytes`,
`parse`, `build`, `regression`, `history`, `licence`) and code, and 3 for a
data or input error. Evaluation builds the candidate twice and requires equal
database SHA-256s, keeps only the first bundle, requires every live release's
rows to be byte-identical and every catalog, condition and dictionary table to
keep its live row count, then counts added, removed and redescribed codes.

Real-data evidence (2026-10-20, workstation): against the live bundle
(`1fbca86f`, schema 10) with a stand-in `014` derived from `013` by one changed
description, the report was `would_activate` with 12,371 concepts on both
sides, 0 added, 0 removed and 1 changed. Two builds took 276 seconds with a
207 MiB peak RSS. The API served both releases, each with its own wording, and
`/readyz` reported the candidate dataset. The database reached 494.7 MiB of
the then 512 MiB page cap, and public search would have stayed on the previous
release; both are resolved below.

### History Growth And Release Selection

Each RSK ICD-10 release adds about 70 MiB, and nothing is pruned, so the
database bound (`MAX_DATABASE_BYTES` in `src/minos/store/artifact.py`) is now
1 GiB. It sets the build's `max_page_count`, the manifest's size bound and the
host transfer bounds. Search attribution walked every release under one
10-second, 50-million-operation budget; that budget is now per release, with
unchanged output.

Evidence (2026-10-21, workstation, local images from this change): the live
inputs plus seven derived ICD-10 stand-ins (`014`-`020`, one changed
description each, starting yearly from 2027) built in 158.8 seconds with a
212 MiB peak RSS into a 915.9 MiB database with 10 sources. Each bundle was
then served from disk with a cold page cache under the production API limits
(1 CPU, 512 MiB with no swap, read-only, no network):

| Bundle | Ready | `pool_ms` | `trees_ms` | Anonymous memory | Slowest probe | OOM |
| --- | --- | --- | --- | --- | --- | --- |
| live `bundle-184`, 424.4 MiB | 8.7 s | 5,512 | 106 | 258 MiB | 54 ms | none |
| 10 sources, 915.9 MiB | 9.9 s | 6,284 | 544 | 264 MiB | 157 ms | none |

Process memory hardly depends on history. The page cache fills the rest of the
cgroup and is reclaimed (memory.events `max`: 1,136 and 4,336); every probe
returned 200. The host disk is slower than this workstation, so a cold
activation near 1 GiB is still to be timed against the 300-second transition
before Phase C. Retained bundles are never pruned, and a manifest holds at
most 16 sources; both need a plan before history accumulates unattended.

Release selection follows the owner's rule (2026-10-21): the latest release
unless the question carries a date, and then the release in force that day.
A release governs from its validity start, as a Polish calendar date, until
the next release of its system starts; a day before every archived release
falls to the earliest, and two releases with the same start are ambiguous.
`/api/v1/releases` therefore lists the release in force today first for each
system and marks it `in_force`, so the website's first-release choice is
today's. A search with `service_date` answers from the release in force that
day, the dictionary page for a dated JGP view renders that release, and the
grouper suggests codes from the release in force on the case's service day.
In the 10-source bundle a search dated 2026-07-01 answered from `013` and one
dated 2031-06-01 from `018`; in Chromium the grouper suggested the `018`
wording for a June 2031 discharge, while search and the dictionary page stayed
on `013`.

Phase B1 reads a local archive root. The host processor (B3) fetches the
same digests from R2 with its own read-only key.

## Shadow ICD-9 Candidate (#141 Phase B2)

```sh
minos data candidate-icd9 --live-bundle <live bundle> \
  --archive-root <archive> --czs-sha256 <digest> \
  [--workbook-sha256 <digest> --workbook-url <NFZ XLSX URL>] \
  --fetched-at <observation time> \
  --policy manifests/policy/czs-icd9-automatic.json --work <new directory>
```

The CZS digest comes from the watcher's `czs_dictionary` record. Evaluation,
the report and the exit codes are those of B1. The candidate adds the new CZS
generation and a same-version NFZ workbook beside the previous pair:

- An unchanged version (`symbol`) reuses the live workbook bytes under a new
  release identity bound to the new generation. A changed version needs the
  archived workbook for that version (`workbook_companion_missing` otherwise).
- CZS has no dictionary-level validity, only a publication time and per-code
  procedure dates, and NFZ backdates some codes (generation 93, published
  2026-09-24, has codes starting 2026-09-01). The new generation therefore
  governs from its earliest changed date: an added code's `data_od`, either
  side of a changed `data_od` or `data_do`, and the publication day for a title,
  status or annotation change. The previous generation closes at that Polish
  midnight, so earlier service dates keep it.
- Refusals: backdating beyond `max_backdating_days` (92, owner policy; history
  `retroactive_amendment_exceeds_bound`), a generation number not newer than
  the live one, an effective day not after the live generation's start
  (`effective_day_not_after_live_release`), a republication with identical entries (`release_content_unchanged`,
  which B3 treats as nothing to do rather than a failure), an edition other
  than the policy's, and every B1 bytes, parse, build, regression and history
  check.
- ICD-9 synonyms pinned to the previous generation become unscoped. An unscoped
  terminology synonym is checked against every searchable release of its
  system; supporting workbooks are never searched, so they are excluded.

Real-data evidence (2026-10-21, workstation): against the live bundle with a
stand-in generation 94 derived from 93 (published 2026-10-15, one title
changed, one code added from 2026-10-01), the report was `would_activate` with
10,179 and 10,180 concepts, 1 added, 0 removed and 1 changed. Two builds took
269 seconds with a 207 MiB peak RSS; the database was 496.0 MiB. Through the
API the new generation was listed first and in force, a search dated 2026-09-15
answered from generation 93 with the old title, one dated 2026-10-01 from 94
with the new one, and the tree, the added code's page and the ICD-9 sitemap
came from generation 94 with the reused workbook hierarchy.

## Host Processor (#141 Phase B3)

`minos-processor.timer` starts `/opt/minos/bin/processor_host.py` as root every
six hours (01:30, 07:30, 13:30 and 19:30 with up to 10 minutes of jitter). The
runner takes the shared validation deployment lock, reads the live Compose
model for the reference API bundle, its image and the archive bucket, and runs
one container, `minos processor run`, from that API image. The Alpine watcher
image cannot build a bundle: it has neither the workbook reader nor the
lemmatizer. The container is outside the `minos` Compose project, so watcher
and validation transitions never see it. A deployment started during a run
fails fast with the existing lock message, and a run that finds the lock busy
is skipped.

The container runs as `minos-processor` (UID/GID 10005) with the watcher's
hardening: read-only root, no capabilities, `no-new-privileges`, init, 0.5 CPU,
384 MiB without swap, 64 PIDs and a 16 MiB `/tmp`. Temporary files go to the
on-disk work directory (`TMPDIR`), because tmpfs pages count against the memory
limit. It mounts the public projections, the live bundle, the R2 key and the
Kuma target read-only; only `/var/lib/minos/processor/{work,state}` are
writable. It joins `minos_monitoring` for Kuma and a separate
`minos-processor-egress` bridge for R2. The runner refuses to start below
4 GiB free disk, kills a run after 75 minutes and empties the work directory
on every exit.

One run:

1. Reads the RSK and NFZ projections through their `current` generation and
   checks each file against the digest in the generation name. The processor
   never opens the watcher control database. The current archived `00CD10`
   product and its basic-info observation are the ICD-10 input (fetch time:
   the entry's first observation). The CZS `ICD9` dictionary and, when its
   digest is not live, the same-version companion workbook are the ICD-9
   input (fetch time: the CZS observation).
2. Skips an input whose digest is already a live source. A verdict is keyed by
   the input digests, the live database digest and the policy file digest, so
   each input is judged once per live bundle and policy.
3. For a new key, pins every live input and the new blobs (a HEAD supplies
   the size, bounded at 64 MiB and 1 MiB for basic-info), downloads them with
   checksum mode, and runs the B1 or B2 proposal and evaluation.
4. Stores the reports in `state/verdicts.json` (current keys only) and pushes
   the `data-update` monitor: up with `idle` or `would_activate <release_id>`,
   down with `rejected <system> <criterion>/<code>` or `failed: <step>`. The
   push accepts only those shapes, so no path, URL, digest or source text can
   reach Telegram. A failed step stores nothing and is retried next run.

It activates nothing. Every live input must be in R2: release bundles are built
on the workstation from `data/archive`, and no committed selection proves
that each curated workbook is in the bucket. A missing blob reports
`failed: fetch`, the cue to copy it with the operator key.

Container evidence (2026-10-21, workstation, API image from the tree, the
production limits, network off). A stand-in generation 94 (generation 93
with one `data_do` added) against the live bundle was `would_activate` twice,
with the same database digest: two builds took 678 and 613 seconds (the first
overlapped a full test run) and wrote a 496.0 MiB database. Cgroup memory
reached the 384 MiB limit, filled by page cache from the writes: anonymous
memory peaked at 159 MiB and file cache at 242 MiB, with about 33,000 reclaim
events and no OOM. The work directory peaked at 992 MiB. A bundle near the
1 GiB bound roughly doubles both. Host timing is not measured yet: the host
CPU is slower by an unknown factor, so the first production run is the
evidence for the 75-minute cap. A failed run keeps its container for
`docker logs minos-processor` until the next run. In a stand-in network with a fake Kuma, the processor read the
projection as UID 10005, loaded `/app/policy`, pushed `failed: fetch`
(no egress), then `idle` for the live generation, and left the work directory
empty. The watcher image refused the same candidate in 6 seconds for lack of
the workbook reader.

Known limits, before Phase C: Kuma notifies only on a status change, so a
second refusal while the monitor is already down does not reach Telegram
(the durable per-candidate delivery in
[failed data updates](operations.md#failed-data-updates-and-telegram) is still
open). The 92-day ICD-9 bound is usually not the operative one: a generation
whose effective day is not after the live generation's start is refused
(`effective_day_not_after_live_release`), so the effective bound is the
smaller of 92 days and the time since the live generation started. The
processor's reports are the evidence for revisiting it.

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
