# NFZ Publication Metadata (#92)

Status: implementation and local acceptance, 2026-10-12. Production activation
and endpoint acceptance are pending; this is not a deployment record.

## Public contract

`GET /api/v1/provenance/nfz` serves one immutable static JSON generation from
`public/nfz/current/api/v1/provenance/nfz.json`. It uses the same private watcher
control database and atomic filesystem publisher as RSK/BAW. The read-only edge
receives neither that database nor private archive access.

The schema identifies its exact reconciled revision and observation time. Its
coverage is `latest_reconciled_observation_only`, not a historical inventory.
Absence does not assert withdrawal. An empty observation produces an explicit
empty list. A failed, incomplete or unreconciled run cannot replace the last
valid projection; restoration reads the latest reconciled run without collecting
new data or changing source cursors.

Allowed channels and fields:

- Hospital orders: number, publication date, official NFZ link, BAW document ID.
- News/projects/JGP: title, publication ID/date and official link; JGP includes
  its same-run notice-bound DOCX/XLSX evidence. Projects stay labelled projects.
- CZS: dictionary code, technical generation/time, established display version,
  and MODTAR year/SZP scope. MODTAR display publication and publisher-declared
  validity dates remain separate. Same-run XML, SOAP and generation joins are
  checked. ICD9 versions require matching companion/crosscheck XML evidence;
  compatible legacy observations without it have a null version.
- NFZ ICD-9 companion: explicit version and file checksum, with the matched CZS
  XML checksum. It does not replace the canonical CZS catalog.
- AOTMiT: title, publication date and official PDF, explicitly a leading indicator.

- Sejm ELI (#282): per legal grouping dictionary, the pinned act and its
  base act (both Sejm ELI URLs with evidence), the in-force status and the
  findings that differ from the reviewed pin; at most two entries.

Every entry carries observation time and SHA-256 of the observed response.
Nested file/display evidence has separate checksums and observation timestamps.
All entries say `service_applicability: not_reviewed`; publication does not update
the reference bundle, grouper bindings or medical/legal approval. CZS timestamps
without a timezone retain that uncertainty rather than acquiring a UTC label.

Titles are source text, rendered with `textContent`. Response headers, summaries,
archive keys, run identifiers, cursors, schedules, incidents and credentials are
not copied. Redistribution artifacts stay empty. Strict output models and browser
validators reject extra fields, malformed dates/digests, duplicate identities,
wrong-channel links, userinfo, ports, fragments, traversal and unexpected query
parameters. Only CZS's pinned dictionary/generation and display queries are
allowed. Nginx rejects query/encoded/normalized alternatives to the exact route.

## Bounds and evidence

- Exact-run snapshot: at most 512 rows and 2 MiB aggregate input, checked before
  decoding metadata inside one SQLite read transaction. Blob receipts must exist.
- Public output: at most 256 entries/1 MiB. Per-channel caps are 32 orders,
  64 news, 64 projects, four JGP notices with eight files each, two CZS dictionaries,
  one companion and 12 AOTMiT notices. Excess is refused, never silently truncated.
- Current live-evidence preview (read-only): 10 entries, 6,296 bytes; the observed
  ICD9 version is 5.90 and MODTAR is generation 103/display 202665. These are
  publication observations, not the versions activated in the grouper.
- Synthetic maximum-channel fixture: 179 public entries from 213 current input
  rows, with 2,028,250 metadata bytes, after 100 historical runs/21,513 total rows
  in a 24,596,480-byte real control database. Thirty snapshot/build samples had
  13.27 ms median and 14.31 ms maximum; separately measured peak Python allocations
  were 4,352,897 bytes. Output was 141,352 bytes. This is local evidence, not host
  capacity or an incumbent-workflow comparison.

The owning tests exercise actual SQLite reconciliation/fencing, collection to
public generation, deterministic restoration, failed collection retention,
interrupted NFZ generation installation/retry, malformed joins and privacy/URL
refusals. The shared wire fixture is accepted by both Python and JavaScript.
Real Nginx/Chromium checks cover desktop, 390px and 320px layouts, keyboard tabs,
local search, text-only hostile titles, empty/unavailable/invalid/stale NFZ states,
and continued RSK/BAW availability. Screenshots are retained under `test-output/`.

The installed-wheel watcher image passed a real disposable Docker lifecycle test
in 23.41 seconds: copied-state paused preflight and projection restoration,
failed process activation followed by automatic recovery, successful activation,
and explicit rollback. Four healthy API/edge/tunnel/monitoring sentinels retained
their container IDs and restart counts. The fixture uses future-only scheduling
and no network or production credentials. CI runs the same smoke against its
built watcher image. Production S3 preflight and activation subsequently passed.

### Deployed acceptance (2026-10-12)

PRs #167/#168 deployed revision `0bf9b31d5b5b9f489d0a1249b950c70325626e45`.
Watcher preflight and activation passed with unchanged API/edge/tunnel/monitoring
identities; API/edge then activated at `2026-10-12T08:18:22Z`. Public NFZ JSON
exactly matched independently mapped committed evidence: revision 298, 10 entries,
6,296 bytes, SHA-256
`ac4ca62f52139bbf6f6fcffff09760a6c379f9a2f925e86ff6c26dc27ad058aa`.
Actual desktop/390px/320px Chromium checks and screenshot review passed; expanded
provenance, local search and route refusals passed. Repository/packages remain
private, the reference bundle is unchanged, and no preflight scratch remains.
See [issue acceptance](https://github.com/krzysztofbury/minos-health/issues/92#issuecomment-6120799364).

## Deployment boundary

The existing `deploy_validation` command changes only API/edge and requires the
watcher identity to remain unchanged. This feature also requires a reviewed
watcher image update so future scheduled runs publish NFZ metadata. Do not run
the maintenance-only full host playbook over the active validation deployment.
Record exact prior watcher/Compose identities, verify a scheduler-paused candidate
against isolated copied control state, and use a guarded watcher-only activation
and rollback preserving the API, tunnel and monitoring containers. Publish the
projection before exposing the new edge route. Verify the public JSON against
committed evidence and review actual desktop/mobile rendering after activation.

The owner approved this watcher-only path on 2026-10-12. The commands are:

```sh
uv run python -m scripts.deploy_watcher plan \
  --plan test-output/issue-92/watcher-plan.json \
  --revision "$REVIEWED_MAIN_SHA" --image "$PUBLISHED_WATCHER_DIGEST" \
  --publish-run "$WATCHER_PUBLISH_RUN"
uv run python -m scripts.deploy_watcher stage --plan test-output/issue-92/watcher-plan.json
uv run python -m scripts.deploy_watcher activate --plan test-output/issue-92/watcher-plan.json
# Explicit recovery if needed, before another deployment changes Compose:
uv run python -m scripts.deploy_watcher rollback --plan test-output/issue-92/watcher-plan.json
```

Plan/stage/activate check remote main, successful CI, successful watcher publication
and the exact digest in its `watcher-release` artifact. Staging pulls and checks
the image revision/platform, copies SQLite with the installed candidate CLI,
runs paused preflight, and restores NFZ in isolated private storage. One-shot
containers have bounded execution/output, and stop before scratch removal.
Production preflight uses the existing S3 checksum/recovery checks and read-only
secret mounts; it does not change live control state or source cursors.

Host transitions share `.validation-deploy.lock`. Root-only records under
`watcher-stage/<revision>/` retain original/candidate Compose, the original
four-service baseline, sanitized command results and receipts. Activation runs
only `up --no-deps --wait watcher`. Failed activation attempts restoration and
still returns failure. Explicit rollback accepts only the staged candidate or
original Compose bytes, retains the initial baseline on retries, and requires
all services healthy plus the expected watcher image before reporting recovery.
Rollback restores configuration/image, not the control database. This release
does not migrate its schema. A transport timeout requires inspection before
retry. Future API/edge rollback plans should be created after this transition,
so their preserved operational baseline includes the new watcher.

Host helpers target Ubuntu's Python 3.12 independently of the application's
Python 3.14 runtime; both wire modules have a 3.12 syntax gate. Validation-image
pruning retains images and bundles referenced by watcher stage originals and
candidates, including a previous watcher image no longer referenced by live
containers. Both guards have regression evidence from failures before the fix.
