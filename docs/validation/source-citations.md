# Durable source citations

Status: #80 focused RSK/BAW discovery/citation slice deployed through #171/#172
at `088f190` on 2026-10-12T13:29:24Z. Copied-production recovery and public/browser
acceptance passed. The wider #80 audit remains open.

## Approved retention contract

- RSK and BAW retain the exact already-public projection bytes at
  `citations/{state_revision}-{projection_sha256}.json`, below their existing
  independent publication roots. The edge's route allowlist still makes these
  files inaccessible directly. No source bytes or control state enter a record.
- Citation installation, file fsync and directory fsync precede advancing the
  ordinary current-generation pointer. A retry after rename repeats directory
  fsync even when the record already exists.
- Existing generation and manifest formats remain byte-compatible. Their usual
  three-generation cleanup never selects a citation. First publication with
  retention enabled can retain an existing current generation without rewriting
  it.
- Owner-approved limits: 1,024 records and 256 MiB of payload bytes per provider,
  8 MiB per record. This is not a total filesystem-block quota. A single
  interrupted staging file can temporarily add up to 8 MiB. A full archive
  refuses a new publication and preserves existing records and current pointer.
- Existing citations cannot be rewritten, including by explicit RSK rebuild.
  Rollback to exactly the recorded bytes is supported. A change to historical
  projection semantics requires a separately reviewed version/recovery design.
- The watcher process lock remains the single-writer boundary. Names, owner,
  mode, regular-file identity, hardlinks, size and SHA-256 are checked. Only one
  recognized interrupted staging file is eligible for cleanup.

## Recovery

Restore verifies retained record bytes. Missing historical records are rebuilt
from recovery-reconciled control-database runs, in revision order, using each
run's exact evidence fence. The replay has a 1,024-run bound and does not fetch
upstream content. Committed-but-unreconciled runs are excluded. Ordinary watcher
reconciliation still owns those runs and can publish them after recovery
verification.

The existing deterministic public projection builders are therefore part of
the citation recovery contract. Preserve their legacy behavior. Citation
storage is derived from trusted control state, not a new mutable authority or
new database schema. Losing control history or changing an old projection
algorithm is not covered by a successful small fixture replay.

## Acceptance evidence

### HTTP and browser boundary

- `/zrodla` renders six initial items per provider, including independent
  failures. Existing JavaScript search enhances this HTML; a failed provider
  fetch preserves its server-rendered section until a validated retry succeeds.
  API-free maintenance retains its static catalog and official links; it does
  not advertise the API-backed source-detail routes.
- `/zrodla/rsk`, `/zrodla/baw` and `/zrodla/nfz` render provider catalogs.
  RSK datasets use `/zrodla/rsk/{hcd}`, BAW documents use
  `/zrodla/baw/{document_id}`. BAW lineage links keep original acts, amendments
  and technical variants distinct.
- `/zrodla/{rsk|baw}/obserwacje/{revision}-{sha256}` and its optional dataset
  suffix pin the exact retained public projection. The current page advertises
  a citation only after reading matching retained bytes. NFZ remains explicitly
  latest-observation metadata in this slice, as defined by #92.
- `/strona/{page}` paginates any provider/dataset/observation listing at 20
  entries, keeping the same observation fence in links. The first page has no
  numeric suffix. No free-text query is placed in a URL.
- Inputs retain their existing 8 MiB RSK, 256 KiB BAW and 1 MiB NFZ limits.
  Two concurrent source renderers and a 1 MiB HTML ceiling bound each worker.
  All source strings are escaped, URLs are validated, hashes and revision
  identities are verified, and unsafe files fail closed. Busy/error responses
  use no-store; successful mutable and exact-observation HTML revalidates.
- The API gains only `/var/lib/minos/public:ro`, copied from the edge's exact
  public bind. Host update guards refuse a private, conflicting, duplicated or
  writable mount. No raw archive, watcher state or credentials become readable
  to the API. Raw archive and direct filesystem paths remain denied by Nginx.
- Separate labels identify source dates, observed dates, legal applicability,
  missing RSK entries and stale observations. A recent observation does not
  claim that a later watcher attempt succeeded.

Real Chromium checks against locally built API/Nginx images passed with
JavaScript disabled at 1440, 390 and 320 px, touch navigation, keyboard focus,
200% text zoom, escaped hostile fixture text, a failed BAW fetch and successful
retry. Screenshots were visually inspected. The typed canary was absent from
network requests; encoded/private/query paths returned 404. Reproduction:

```sh
agent-scratch --root /tmp/opencode -- bash -c '
  umask 022
  trap '\''find "$AGENT_SCRATCH_DIR" -type d -user "$(id -un)" -exec chmod u+w {} + '\'' EXIT
  uv run python -m scripts.smoke_source_pages --output test-output/source-citations
'
```

Pass `--bundle data/bundles/conditions-products` for the pinned-bundle variant.
The smoke uses the deployment's 512 MiB / one-CPU API limits and exercises an
additional near-8-MiB RSK projection after the browser checks. It records
container memory peak, timings, restarts and exact locally built image IDs.

### Retention and recovery boundary

Focused filesystem, control-store, watcher and collection checks cover:

- retention across generation GC and a legacy-generation upgrade;
- quota refusal, retry at capacity and protection of the prior current pointer;
- interruption before/after citation rename and before current activation;
- digest corruption, symlinks, hardlinks and unexpected files;
- exclusion of unreconciled runs and source isolation;
- byte-for-byte RSK and BAW restoration of two issued observations into empty
  public storage using real SQLite control state and fixture collectors.

The full Python suite passed with 2,046 tests and 39 skipped external/private
checks. Two isolated runtime mutations were detected: removing the cited-revision
rewrite refusal and removing directory fsync on an idempotent retry. The latter
failed specifically after an interrupted rename, before current activation.

### Local retention measurements

Single-writer measurements on 2026-10-12 used Linux tmpfs, not the production
host's persistent disk. The downloaded public payloads were repeated under
synthetic revision keys to exercise storage population, not historical replay.

| Payload | Bytes per record | Records | Write p95 | Verify every retained digest |
| --- | ---: | ---: | ---: | ---: |
| RSK revision 302 | 24,009 | 1,024 | 2.65 ms | 21.34 ms |
| BAW revision 306 | 17,818 | 1,024 | 2.70 ms | 18.19 ms |
| Synthetic maximum record | 8,388,608 | 32 | 331.39 ms total writes | 151.50 ms |

Both count-bound populations refused another record. The maximum-record case
retained exactly 256 MiB and refused another record. Peak process RSS was
40,132 KiB. At the observed payload sizes, 1,024 RSK plus 1,024 BAW observations
use about 40.85 MiB of payload storage. The count bound corresponds to 1,024
captures, not a promised number of days; manual runs also consume records.

Private local measurement and mutation receipts are retained under
`test-output/issue-80-citations-2026-10-12/`. These results do not measure disk
fsync latency, full control-history replay, API rendering or production load.

### Rendering measurements

Downloaded public RSK/BAW/NFZ projections (revisions 302/306/298, respectively)
passed the same reader and renderer, with 40 direct render calls at concurrency
two: median 2.54 ms, p95 5.98 ms, maximum 8.51 ms. Largest HTML: 22,671 bytes.
A synthetic 5,952-entry RSK projection with 512-character names occupied
8,380,858 input bytes; 30 calls at concurrency two had p95 1,053.13 ms and
maximum 1,079.04 ms. Largest HTML: 26,076 bytes. This exercises the input bound,
not an assertion that typical catalogs approach it.

The actual API/edge container check used the currently pinned grouping bundle
(`8ae874b13a7a8648980c133822b4955f38ef4996440f8759fc53d68c28c2e0ac`),
512 MiB and one CPU. Eighty fixture requests at concurrency two had p95
11.49 ms and maximum 12.33 ms. A near-limit 6,112-entry synthetic RSK projection
then passed eight requests, maximum 1,110.07 ms. API cgroup peak memory was
404,635,648 bytes, with zero restarts and no OOM kill. Browser acceptance and
the private/query-path refusals passed on that same pinned-bundle stack.
The final receipts and screenshots are in the local `final-acceptance/`
subdirectory; earlier attempts are retained separately for diagnosis.

The measurements include validation, digest checking and source rendering;
container timings also include curl startup. They are local representative
boundary evidence, not sustained production capacity or field-user latency.

## Production activation

Both PRs passed their full CI and container/browser checks before merge. Main CI
run `38250442162`, private watcher publication `38250457222`, and private API/edge
publication `38250456248` passed for `088f1907341539ce2715e50bef36c7e013103cfa`.
The repository and all three GHCR packages remain private.

The guarded watcher preflight used a copied production control database, paused
scheduling and isolated public storage on the host's ext4 filesystem. A read-only
baseline mount supplied only existing public metadata for byte comparison. Two
empty-tree replays produced identical complete citation inventories; every record
passed the serving reader, and all three existing public generations per provider
matched recovered bytes exactly. NFZ restoration also passed.

| Provider | Records | Payload bytes | Allocated bytes | Empty restore 1 / 2 | Idempotent restore |
| --- | ---: | ---: | ---: | ---: | ---: |
| RSK | 38 | 909,664 | 933,888 | 16,910.86 / 14,209.20 ms | 692.04 ms |
| BAW | 22 | 332,552 | 393,216 | 3,640.28 / 5,733.24 ms | 189.38 ms |

Probe peak RSS was 63,380 KiB. These are observed production-state recovery costs,
not sustained-capacity claims. The initial local command deadline expired while
the remote stage continued. Completed host records, released deployment lock,
unchanged live Compose/services and removed isolated scratch were verified before
recovering the receipt; the stage was not blindly repeated.

The watcher activated first and preserved the API, edge, tunnel and monitoring
identities/restart counts. Ordinary API/edge activation failed twice at the startup
health gate. The first rollback restored both services automatically; the second
needed the documented explicit rollback, after which public readiness/homepage
and both service healthchecks passed.

An isolated candidate under the same one-CPU / 512 MiB limits stayed alive without
OOM or restarts and completed validation in 173.15 seconds. Sampled stacks showed
progress through imports and bundle validation, with high measured host CPU/I/O
pressure. Both candidate and prior-image starts exceeded the ordinary health
grace during this incident. The owner approved a one-off, bounded five-minute
startup wait. It retained the exact staged Compose/images, real healthchecks,
request budgets, validation and guarded rollback, but waited through transient
startup-unhealthy status before starting the edge. No new image build was needed.
The permanent startup/rollback policy remains follow-up work under #27/#42.

Final API/edge activation: `2026-10-12T13:29:24Z`.

- Watcher: `sha256:3277b5088dec4df4af713652b8b1d15545ac4aa4bc8e43cd0eca10e9ca65b8e1`.
- API: `sha256:53a6acfe32b62fccd834d611de66d663053e1ec3fdb424c81a27e0c735b89be3`.
- Edge: `sha256:4a17eb2abd90030284ca580afef43bbc95ea2320c81c7c32a840774992f80d5d`.
- Bundle/database: `8ae874b13a7a8648980c133822b4955f38ef4996440f8759fc53d68c28c2e0ac`.

All five services were healthy with zero restarts/OOM kills. The running API had
exactly the expected two read-only binds: bundle and public metadata. RSK/BAW/NFZ
public projections were unchanged byte-for-byte. Source/provider/dataset pages
and four current observation citations returned 200 with correct canonical URLs;
private, query-bearing and noncanonical routes returned 404. Repeated citation
main content was byte-identical. Cloudflare randomizes footer email-obfuscation
tokens, so full edge HTML is not itself a stable artifact digest.

Live Chromium passed no-JS desktop/390/320 px, touch, keyboard, 200% text zoom,
independent provider failure, labelled initial fallback, retry and query-privacy
checks. Public JGP/ICD-9 browser checks confirmed removal of the blanket cutoff
copy while retaining concrete sources. Screenshots were visually inspected.
The first post-startup JGP verification returned 504; the subsequent complete
guarded verification passed readiness, rank, dated JGP/conditions and desktop/
mobile search, then stored the host and local activation receipt. This records
acceptance after startup, not absence of transient host-contention failures or
field/load acceptance.

Private receipts, bounded diagnostic events, operator probe programs and browser
captures are retained under the local `deployment-1213/` evidence directory and
`test-output/validation-deploy/088f1907341539ce2715e50bef36c7e013103cfa/`.
All owned remote diagnostic containers and scratch directories were removed.
Actions usage was investigated after deployment and recorded in #174.
