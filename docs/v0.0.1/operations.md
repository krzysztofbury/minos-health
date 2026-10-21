# Operations

## Operating Model

One dedicated Mikr.us `3.0` host runs the public edge, API, watchers, scheduler,
self-hosted Uptime Kuma, and Cloudflare Tunnel through Docker Compose. The host
is replaceable, but it is not stateless: the watcher control SQLite database is
mutable and therefore receives consistent, tested R2 recovery images. Kuma also
has mutable `/app/data` and requires a separate stopped encrypted off-host
backup.

The target full-product system of record is Git, reviewed manifests, pinned
image digests, SHA-256-keyed R2 source snapshots, revision-numbered
control-database recovery images, and signed release and deployment records. A run is
not acknowledged as successful while the Mikr.us disk is the only copy of its
control metadata. As reconciled on 2026-10-11, the validation deployment records
exact API/edge/bundle identities and local/host activation receipts, but the
independently signed full-release manifest and off-host receipt chain remain
#41/#27 acceptance work. Recovery drills and representative host overlap evidence
remain #49/#42; the deployed validation stack is not proof of those gates.

### RSK Acquisition Modes

RSK `scheduled` and `catch_up` runs use daily acquisition semantics: they
archive the catalog and every returned HCD's `basic-info`, but download the
allowlisted `00CD10` XML only for a first observation, a selected identity or
file-set change. `weekly_verification` and `manual` runs always download and
re-hash the selected XML.

Before making a network request or a download decision, the collector validates
the persisted cursor's canonical full-state digest, exact catalog-to-resource
membership, normalized per-HCD metadata and digests, `00CD10` file-set link, and
semantic selected-file identity. Missing or inconsistent persisted state fails
closed as control-state corruption. It never forces a replacement download,
because doing so could hide local corruption and replace historical provenance
with current upstream state.

Each forced or changed download starts at byte zero, validates the declared and
received size, media type, XML completeness, and SHA-256, then archives an exact
post-download `00CD10` metadata refetch. Schema drift, duplicate UUIDs, ambiguous
selection, persistent rotation, malformed XML, and wrong media fail closed
without advancing observations or the source cursor. Bounded transport
failures, retryable HTTP statuses, and transient rotation enter the
source-neutral retry policy.

### RSK Registration And Scheduling

Registration is an explicit one-shot activation action, not watcher startup
side effect. The production Ansible activation invokes this command after
candidate preflight and promotion, and before starting the active watcher. It
uses `--no-deps`; local operators can run the equivalent command directly:

```text
docker compose --profile bootstrap run --rm watcher-bootstrap-rsk
```

Both local and rendered production Compose projects define this service under
the explicit `bootstrap` profile with the watcher's image, identity, secrets,
networks, and persistent paths. The active watcher and deployment services do
not depend on it, so ordinary `docker compose up` never performs registration.
Ansible calls the one-shot service as a separate fail-closed activation step.
It creates state only when source `rsk` is absent, treats an exact existing
registration as an idempotent no-op, and rejects disabled, customized, or
partial state without overwriting it.

The equivalent direct command is `minos watcher bootstrap-rsk` with the same
database, staging, archive, and canonical process-lock paths used by the watcher.
It atomically creates enabled source `rsk`, canonical watcher kind `rsk`, the
fully materialized default RSK configuration, daily `0 3 * * *` UTC polling,
and Sunday `0 4 * * 0` UTC forced verification. Both schedules use a 3600-second
misfire grace and the bounded 3-attempt, 0.5-to-30-second retry policy.

The command is idempotent only when configuration, enablement, schedule set,
cron expressions, misfire policy, and retry policy match exactly. It does not
reset due cursors. An operator-disabled source, custom configuration, custom
schedule, or partial registration causes a conflict and no write. Resolve such
state explicitly instead of rerunning bootstrap as a repair command.

Daily and weekly obligations have independent SQLite cursors. When both are
overdue, weekly verification has deterministic claim priority and daily remains
due. Startup drains at most the configured scheduler projection limit, then
stops; it does not poll in a busy loop. Overdue daily work reaches the collector
as `catch_up`, while overdue weekly work retains `weekly_verification` so the
forced re-download cannot be weakened by a restart. A claimed occurrence is
advanced before collection under the same fenced transaction. In-process retry
keeps the original mode; exhausted or stale claims are incidented and are not
immediately reclaimed, avoiding a failure loop.

A metadata rotation during transfer has a dedicated retryable outcome. The
orchestrator discards that attempt's staged control receipts and restarts from a
fresh catalog and all-HCD metadata snapshot. A stable retry may commit; repeated
rotation exhausts the configured bounded claim retry policy without partial
observations or cursor movement. Content-addressed bytes published before the
rotation check may remain as unreferenced immutable archive blobs.

Every RSK request receives connect, pool, write, and per-read HTTPX timeouts
bounded by the remaining claim deadline. The client checks the absolute
deadline before and after each streamed chunk, and the collector checks it
between parsing, archive publication, cursor and observation preparation, and
XML validation. Deadline exhaustion is retryable but cannot commit partial
state.

### Public Provenance Publication

Run reconciliation orders durable effects as: committed observation, verified
recovery image, `heartbeat_pending`, local public projection, then `successful`.
Publication failure leaves the run
`heartbeat_pending` and the previous generation active. A retry reuses or
validates the immutable generation and completes local finalization idempotently.
If interruption occurs after the symlink switch, the new generation may already
be active, but the run remains retryable until local finalization completes.
`heartbeat_pending` is retained as a compatibility name for this durable local
phase. It does not represent a monitoring request. After durable terminal
completion, the watcher makes a best-effort Uptime Kuma up or down push. A down
push carries only `failed: <category>`, one of the fixed run failure classes
(for example `source_drift`, `archive_failure`, `collection_failure`), so the
Telegram alert distinguishes upstream drift from storage or network failure
without source text, URLs or exception messages. Push failure never blocks or
changes the watcher result.

Startup first restores a missing or stale projection from the latest committed
RSK revision and then reconciles pending runs. A restored older control database
cannot silently replace a newer active public generation; automatic downgrade
is rejected for operator investigation. The public endpoint contains no
downloadable artifacts while `manifests/redistribution.json` has an empty
allowlist. Guarded application streaming is deferred until legal approval names
specific digests and an application endpoint exists.

An intentional reset uses the same control database and public directory as the
stopped watcher. Record the exact active revision and projection digest, build
and review the target digest from the selected recovery-reconciled revision,
then run:

```sh
minos watcher rebuild-public-rsk \
  --expected-current-revision <active-revision> \
  --expected-current-sha256 <active-projection-sha256> \
  --target-revision <target-revision> \
  --target-sha256 <reviewed-target-projection-sha256>
```

The command holds the watcher process lock and fails if either current fence is
stale, the target revision was not recovery reconciled, or rebuilt target bytes
do not match the reviewed digest. There is no unfenced or implicit downgrade.

## Asset Inventory

| Asset | Primary location | Recovery protection |
| --- | --- | --- |
| Source and manifests | Private GitHub repository | Git history and remote |
| Raw source snapshots | Private EU-jurisdiction R2 bucket `minos-archive` | SHA-256 keys, create-once writes, bucket lock refuses overwrite and delete for 365 days |
| Watcher control database | Mikr.us persistent volume | Locked revisioned R2 recovery images and restore drills |
| Sanitized public provenance | Dedicated host directory, watcher RW and edge RO | Current plus two prior immutable filesystem generations |
| Generated reference database | Private API image and data artifact | GHCR digest, checksum, reproducible build |
| Static web artifact | Private edge image and build artifact | GHCR digest, checksum, reproducible build |
| Watcher runtime | Private watcher image | GHCR digest and bounded release retention |
| Uptime Kuma state | `/var/lib/minos/uptime-kuma`, mounted as `/app/data` | Complete stopped encrypted off-host backup; restore same pinned version first |
| Domain | Registrar | Lock, 2FA, auto-renewal, recovery codes |
| DNS and ingress | Cloudflare | Configuration record and recovery credentials |
| Host configuration | Git and Ansible | Reprovisioning test |
| Operator credentials | Password manager | Off-host recovery access |
| Release manifests | Private GitHub Releases and operator-only R2 prefix | Independent signature and immutable digest |
| Deployment receipts | Operator-only R2 prefix | Signed append-only chain with prior receipt |
| Trusted recovery inventories | `recovery-inventories/` R2 prefix and off-host recovery kit | Non-expiring signed chain and verification key |
| Trusted recovery copies | `trusted-recovery/` R2 prefix | Independently selected; bucket lock refuses overwrite and delete |

R2 tokens are bucket-scoped read/write, so deletion protection comes from the
bucket lock rules, not the token (#200). Minos keeps no Terraform state.

## Deployment

A deployment selects one signed compatible release manifest containing exact
API, edge, watcher, `cloudflared`, and dataset identities. It first quiesces
watcher dispatch, reconciles `committed` and `heartbeat_pending` runs, verifies a
pre-deploy recovery image, and runs the candidate watcher with scheduling
disabled against a disposable database copy. It then verifies:

1. Every image digest and architecture matches the release manifest.
2. `/readyz` succeeds through the public Cloudflare hostname.
3. A known exact-code search returns the expected dataset release.
4. A known normalized-text search returns the expected stable result.
5. Static assets match the recorded checksums.
6. Mobile and desktop browser flows render and perform a real search.
7. The pinned watcher UID/GID can access its volume and mounted secret files.
8. A disposable control-database copy upgrades supported older schemas to the
   candidate schema, then migration compatibility, an R2 native-checksum probe,
   and a recovery-image round trip succeed while dispatch remains paused.
9. The previous watcher stops. The candidate captures preactivation evidence,
   promotes the reviewed runtime, and applies the same tested migration to the
   quiesced live volume. The two watcher processes never own the volume
   concurrently. If the live schema changed and a later activation step fails,
   rescue preserves the candidate runtime and migrated database stopped rather
   than starting an older writer.
10. Activation bootstraps or validates canonical RSK registration, validates any
    existing public projection, runs one 900-second-bounded manual RSK canary,
    and requires its canonical public projection before active services start.
11. Container health, memory, disk, and error signals remain within recorded
    launch thresholds under representative traffic. The production edge must
    return HTTP 200 canonical JSON whose SHA-256 equals the validated current
    projection.
12. Uptime Kuma has no host port or Docker socket, the public status route was
    enabled only after private admin and 2FA setup, HTTP and RSK push monitors
    use reviewed notifications, and the public page contains coarse state only.
    From outside, every Kuma admin path on `status.minoshealth.pl` (`/socket.io`,
    `/dashboard`, `/setup`, `/api/push`, `/api/badge`, `/metrics`) redirects to
    Cloudflare Access, while `/` and the status-page reads return 200.

The first production deployments are explicit operator actions. Automation may
later execute the same bounded procedure over restricted SSH, but it must not
change the release selection rule or deploy mutable tags.

## Rollback

Rollback restores the previous complete compatible API, edge, watcher,
`cloudflared`, Uptime Kuma, and dataset digest set, not individual containers
selected independently. It does not rebuild, retag, mutate the reference
database, or follow `latest`.

After rollback, repeat readiness, static-integrity, browser, and known-search
tests through the public hostname. Preserve failed-version logs and release
metadata until the incident is understood. Do not let image cleanup remove the
current or prior compatible release.

The scheduler remains quiesced until the serving and watcher preflights pass.
Every supported control migration is tested first on a disposable copy. Before
the live migration, normal rescue may restore the prior compatible runtime.
After the live schema changes, automatic rollback to an older writer is
forbidden; preserve the promoted candidate and migrated database stopped for
explicit recovery.

Before the candidate canary commits, rollback may restore the verified
pre-deploy recovery image with the pinned ownership and modes. Once a candidate
run reaches `committed`, `heartbeat_pending`, or `successful`, never restore an
older database image. Roll back the containers against the current
backward-readable database, reconcile the run, and preserve its acknowledged
state. If writing the success receipt fails after the canary, keep dispatch
quiesced and retry that idempotent receipt; do not erase the canary.

## Control Database Recovery Images

The watcher creates recovery images with `sqlite3.Connection.backup()` rather
than copying the live database, WAL, or shared-memory files. Every restorable
transaction increments an embedded monotonic `state_revision`; a transaction
that advances source state creates a `run` recovery image before reporting
success. Scheduled `daily`, `weekly`, and `monthly` images provide longer
retention and do not require a currently committed run. Each image:

1. Runs `PRAGMA quick_check` on the live database.
2. Creates a consistent temporary SQLite copy through the backup API.
3. Runs `PRAGMA integrity_check` on the copy.
4. Verifies its embedded state revision, schema version, complete migration
   checksum set, and, for a `run` image, the expected committed run.
5. Computes its byte count and SHA-256.
6. Uploads it through single-part `PutObject` with exact `ContentLength`,
   Base64-encoded whole-object SHA-256 in `ChecksumSHA256`, and
   `If-None-Match: *` under one of these immutable grammars:
   `control-recovery/run/<20-digit-revision>/<sha256>.sqlite3`,
   `control-recovery/daily/<YYYY-MM-DD>/<revision>/<sha256>.sqlite3`,
   `control-recovery/weekly/<YYYY-Www>/<revision>/<sha256>.sqlite3`, or
   `control-recovery/monthly/<YYYY-MM>/<revision>/<sha256>.sqlite3`.
7. Performs `HeadObject` with checksum mode enabled against the derived requested
   key and compares byte count and native checksum after create, `412` or
   `409 ObjectLockedByBucketPolicy` (an existing locked key); any other `409`
   retries the bounded conditional operation. Multipart upload is
   prohibited for watcher-created recovery images.
8. For a run image, records `heartbeat_pending`, publishes the fenced sanitized
   public projection, and marks the run successful only after durable local
   finalization succeeds.
9. Removes the temporary local copy.

R2 lifecycle rules delete recovery images after 30 days for `run`, 31 days for
`daily`, 91 days for `weekly`, and 400 days for `monthly`; R2 bucket lock rules
with the same ages refuse any earlier overwrite or delete, so every image is
immutable for its whole retained life. Lifecycle deletion is asynchronous and
has no guaranteed physical deadline, so these are deletion-eligibility
thresholds rather than hard maximum retention bounds. A seven-day
incomplete-multipart abort remains defense in depth for operator tooling. Raw
source blobs never expire and are locked indefinitely. Recovery images contain no credentials,
signed URLs, medical queries, encounter payloads, or patient data.

Recovery paginates the class prefixes within configured maximum object counts
derived from schedule frequency and retention; exceeding a bound stops automatic
selection for operator investigation rather than truncating candidates. It sorts
valid candidates by embedded `(state_revision, created_at, class_rank, sha256)`,
where `run > daily > weekly > monthly`, then
verifies key grammar, native R2 SHA-256, byte count, embedded metadata, complete
migration checksums, and SQLite integrity in descending order. A successfully
uploaded object is the completion record; partial PUTs never become visible.

These checks prove integrity, not that an authorized but compromised watcher
wrote honest state. At least weekly, and also at deployment, key rotation, and
recovery drills, an operator command selects one verified recovery image, copies
it conditionally with the operator identity to
`trusted-recovery/<20-digit-sequence>/<sha256>.sqlite3`, and verifies the copy.
It then signs an inventory containing that key, checksum, revision, R2
`LastModified`, sequence, and prior-inventory digest, and writes it
conditionally to `recovery-inventories/<20-digit-sequence>.json`. The command
derives the next sequence from the fully verified unique inventory head, creates
the sequence-only key with `If-None-Match: *`, accepts `412` (or the bucket
lock's `409 ObjectLockedByBucketPolicy`) only for an exact idempotent byte
match, then relists and rejects gaps, duplicate sequences, bad links, or
multiple heads. An unused concurrent trusted copy is an orphan and is
never selected. R2 `CopyObject` ignores `If-None-Match`, so the copy is a
download pinned to the source ETag and verified against its SHA-256, then a
create-once upload with that checksum. Trusted database copies are locked and
then deleted after 400 days. The small signed inventory objects are locked
indefinitely and never expire, so their chain remains verifiable from sequence
one. The
public verification key and latest trusted inventory also belong in the off-host
recovery kit; the signing key never enters Mikr.us. Operators detect a missed
weekly cadence from the read-only watcher status during routine review.

Ordinary host-loss recovery may use the newest valid watcher-created recovery
image. If host or watcher-key compromise is suspected, automatic newest-image
selection is forbidden: rotate the key, establish a cutoff from external
evidence, verify the signed inventory chain, and restore only its operator-
copied pre-cutoff image.
This deliberately accepts the older compromise-recovery point rather than
treating attacker-created high revisions as trusted.

## Release And Deployment Records

Signed release envelopes use the sequence-free conditional key
`release-manifests/<tag>.json`. Successful deployment receipts use
`deployment-receipts/<20-digit-sequence>.json` and contain their
sequence, prior receipt digest, release-manifest digest, pre-deploy recovery
image, operator, host, time, rollback compatibility, and verification evidence.
The operator creates both conditionally; the watcher cannot access these
prefixes. Repository immutable releases must be enabled and verified, but the
signed R2 manifest is the recovery authority.

Each receipt writer derives the next sequence from the verified unique head and
uses `If-None-Match: *` on that sequence-only key. A `412` is an idempotent retry
only when the stored bytes match exactly. After every write, relist within the
asserted bound, verify every signature and digest link from sequence one, and
require exactly one highest chain head. Missing links, bad prior digests, or any
conflicting sequence fail closed immediately. Receipt objects do not expire.

## Recovery

### Mikr.us Host Loss

1. Obtain a replacement dedicated Mikr.us `3.0` host.
2. Verify SSH host identity and apply the reviewed Ansible configuration.
3. Install fresh service-restricted Cloudflare and R2 secret files plus the
   root-owned GHCR Docker credential.
4. Verify the unique signed deployment-receipt chain head and its signed release
   manifest, then pull the exact current API, edge, watcher, and `cloudflared`
   digests.
5. Select the newest valid control-database recovery image under the ordinary or
   suspected-compromise rules above with the operator identity, not the watcher
   key.
6. Verify its signature coverage when required, ETag-pinned bytes, checksum, embedded
   revision and migration set, and integrity before placing it on the persistent
   volume with the pinned watcher ownership and modes.
7. Start Compose without public ingress and verify internal readiness, source
   schedules, known searches, the restored Uptime Kuma data against the same
   pinned version, and the installed watcher target document.
8. Start `cloudflared`, then verify TLS, readiness, mobile and desktop rendering,
   known searches, the coarse public status page, notification delivery,
   read-only watcher status, and bounded local watcher logs.

### Control Database Corruption

Stop the watcher before recovery. Preserve the corrupt database and WAL files
for investigation, then restore the newest trusted revision under the selection
and compromise rules above. Runs whose recovery images were not durably verified
were never acknowledged as successful and must be retried or investigated. An
orphaned raw blob alone cannot reconstruct its lost source relationship or
observation time. Never create missing history by substituting a current
upstream response for an earlier observation.

### Raw Snapshot Loss

R2 has no object versions; bucket lock rules refuse overwriting or deleting a
raw blob, and every read verifies the SHA-256 in its key. The watcher token is
bucket-scoped read/write but cannot change lock or lifecycle rules, which need a
Cloudflare account administrator. R2 is the only raw-snapshot provider, so a
complete Cloudflare account compromise remains a documented residual risk.

### GHCR Loss

Rebuild only from the exact source snapshot, source commit, toolchain, and
manifest recorded for the release. If the output does not match the expected
logical data and checksums, treat it as a new candidate requiring full review
rather than silently substituting it.

### Domain Or Tunnel Loss

Recover the registrar and Cloudflare accounts through stored recovery material.
Restore the tunnel route and DNS from the recorded configuration, verify DNSSEC,
and then repeat public TLS, browser, and known-search checks. Do not change
nameservers during an unrelated application incident unless DNS is proven to be
the failing boundary.

### Uptime Kuma Backup And Restore

Uptime Kuma data can contain the admin credential state, notification
credentials, and watcher push tokens. Back up the complete `/app/data` tree,
which is `/var/lib/minos/uptime-kuma` on the host, only while Kuma is stopped:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  stop uptime-kuma
```

Keep the service stopped until the complete directory has been archived,
encrypted, transferred off-host, and checked. Never copy the live SQLite
database, WAL, or shared-memory files. Restore the same pinned version first:
Uptime Kuma 2.5.5, official MIT `slim-rootless`, `linux/amd64`, digest
`sha256:8bdad438d293e998fd7d45004cefb3baea0f96d3bc774502bba99c17d616e1f6`.
Ansible intentionally refuses an image change while preserved data exists until
a reviewed stopped backup and migration procedure is available.

On 2026-09-23 an isolated restore of the refreshed 1Password Document used a
disposable local Docker volume, the same pinned image and UID `10003`, no
network, and no published port. The restored service became healthy and served
the status page; the temporary container and volume were removed. This does not
replace the pending complete empty-host Mikr.us recovery drill.

## Recovery Drills

Before `v0.0.1`, perform and time at least:

- restoration and integrity verification of a control-database R2 recovery
  image, including the suspected-compromise signed-inventory path;
- reconstruction of a generated database from pinned raw snapshots;
- complete Compose deployment to an empty `linux/amd64` host;
- rollback to the prior compatible API, edge, watcher, `cloudflared`, and
  Uptime Kuma and dataset digest set;
- restore of a complete encrypted Uptime Kuma `/app/data` backup against the
  same pinned image before testing any migration;
- recovery of registrar, Cloudflare, GHCR, and SSH access without relying
  on an active developer laptop.

Documentation alone is not a tested backup. Record observed recovery time and
every unresolved manual step.

## Logging

Watcher lifecycle and RSK transport events use allowlisted Loguru fields. Docker
retains stdout and stderr with its bounded `local` driver; a dedicated production
JSON sink and application-wide logging configuration remain future work.
Libraries and domain code must not serialize arbitrary request, response, or
exception objects.

Application logs may contain:

- timestamp and severity;
- request, run, or trace ID;
- route template, watcher source ID, and run mode;
- status class or bounded error category;
- bounded duration and result count;
- dataset, application, and watcher release identities.

Application logs must not contain:

- raw URL query values or medical terms;
- encounter payloads or grouping content;
- request headers, cookies, authorization values, or signed URLs;
- patient data, prescriptions, source bytes, credentials, or local paths;
- arbitrary upstream response objects or public stack traces.

Docker uses the bounded `local` log driver. The initial limit is three 10 MB
files per service and must be verified after representative watcher and API
traffic. Cloudflare access logging and analytics remain disabled until a privacy
review addresses client identifiers and medical terms in URLs. Direct Plausible
pageviews and their aggregate engagement duration and scroll depth are the only
active public analytics. They use no cookies or custom properties; custom events
require an explicit event and property allowlist.

CloudWatch is not used for the Mikr.us runtime. A future external error service
requires explicit event allowlisting, bounded retention, and a privacy review.

Inspect all watcher sources or one source through the read-only status command:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status rsk --json
```

Read a bounded recent log window from the host-local Docker log store:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  logs --since 24h --tail 500 --timestamps watcher
```

For direct local development, use
`uv run minos watcher status [SOURCE_ID] --database
.minos/control/control.sqlite3 --json`.
The status path opens SQLite read-only and reports bounded schedules, recent
runs and changes, open incidents, and verified downloaded products without raw
source payloads or credentials. Confirm the container is running before treating
empty log output as evidence. Self-hosted Uptime Kuma adds coarse public state
and notifications, but runs on this same host. It cannot detect or notify for a
total Mikr.us host, network, or power outage. This limitation is accepted for
now.

## Secrets And Configuration

Local development uses ignored `.env` files based on `.env.example`. Local
cloud credentials stay in external profiles or keychains.

Production uses host-managed secret files readable only by root and the numeric
UID or GID of the required service. Ansible creates service-specific directories
and Compose mounts each file read-only into only that container:

- Cloudflare Tunnel credential for `cloudflared`;
- bucket-scoped R2 access key for the watcher (S3-compatible credential file);
- Uptime Kuma target JSON for the watcher, containing only version, source IDs,
  and 32-character alphanumeric push tokens;
- a separate read-only, bucket-scoped R2 key and a `data-update`-only Kuma
  target for the data-update processor, in `/etc/minos/processor`, mode `0440`
  root:`minos-processor` (see [the processor setup](#data-update-processor-setup)).

The production target schema is exactly
`{"version":1,"sources":{"rsk":"<32-character-alphanumeric-token>"}}`.
An empty `sources` mapping is allowed only during bootstrap while
`watcher_uptime_kuma_required=false`. Supply the file to Ansible through
`watcher_uptime_kuma_targets_source` from a mode-`0400` or mode-`0600` regular
file outside the repository. Set `watcher_uptime_kuma_required=true` only after
the corresponding push monitor exists.

The GHCR package-read token belongs in the root-owned host Docker credential
store for image pulls and is not mounted into a container.

The recovery copies, SSH material, registrar credentials, and emergency access
stay in the password manager. Secret values never enter `.env`, GitHub Actions,
Compose YAML, images, or logs. Non-secret endpoints, bucket names, timeouts, and
release digests remain normal configuration.

GitHub Actions stores no object-store credential. The Mikr.us R2 key
(`minos-r2-watcher` in the MinosHealth vault) is limited to the `minos-archive`
bucket, relies on bucket lock rules for no-delete, is installed outside the
repository, and is rotated immediately after any suspected exposure. Operator
work uses the separate `minos-r2-operator` key.

## Metrics And Alerts

Initial operational signals are:

- API request count by route template and status class;
- warm latency and bounded timeout rate;
- container restart count and health;
- host memory, disk space, and inode headroom;
- operator-run public readiness and known-search failures;
- deployed image digests and dataset release;
- per-source observation age;
- watcher due, running, failed, stale, and disabled status;
- backup age and last restore-test date;
- R2 storage and operation counts against the free tier.

SQLite incidents and `minos watcher status [SOURCE_ID] --json` are the watcher
status authority. Docker's bounded local logs provide process detail. The
self-hosted Uptime Kuma public page provides coarse service state only; issue
#54 remains the future private detailed admin portal. Kuma HTTP monitors,
watcher terminal up/down pushes, and notifications do not replace SQLite as the
detailed authority. Monitor requests are best-effort and never block or alter
watcher completion.

The public-edge HTTP monitor checks every 60 seconds and retries 3 times at
20-second intervals before it reports down and notifies (owner setting,
2026-10-16). One failed check, such as the transient Kuma DNS failure of
2026-10-15, no longer pages, and a real outage still alerts after about a
minute. Validation releases avoid the API startup outage (#192); see
[colour activation](../validation/validation-deploy.md#colour-activation-without-downtime-192).

Because Kuma is on the same Mikr.us host, it cannot detect or notify for a total
host, network, or power outage. This limitation is accepted for now. There is no
paid external monitoring SaaS in the accepted design.

Empty log output is not evidence of health until the service, container, and log
filter are confirmed against actually running resources.

## Representative Verification

Before launch, measure on the actual Mikr.us `3.0` host:

- image pull, container startup, and readiness duration;
- total and per-container RSS under concurrent API and watcher work;
- disk use for images, volumes, temporary downloads, and bounded logs;
- reference database open and sentinel-query time;
- exact code, common prefix, no-result, and worst-case bounded search latency;
- JGP group lookup with service-date resolution;
- warm p50, p95, and p99 API latency through Cloudflare Tunnel;
- error rate at expected launch concurrency and a documented burst;
- watcher memory and disk use while streaming a representative large file;
- backup and full empty-host restore duration;
- mobile and desktop startup and end-to-end search latency.

Record launch SLOs and resource budgets before judging results. A low-traffic
quiet window is not representative verification.

## Security Operations

- Patch the Mikr.us host and Docker on a documented cadence and after relevant
  security advisories.
- Rotate credentials exposed in source, logs, terminal output, or another
  project's setup before reuse.
- Restrict the GHCR token to package read access and the required package.
- Do not mount the Docker socket into Watchtower or application containers.
- Keep the registry, source bucket, recovery images, and control database
  private.
- Review R2 lock, lifecycle and token-scope changes before applying them.
- Scan images and exercise the actual runtime after formatter or autofix passes.
- Report vulnerabilities through GitHub private vulnerability reporting.

## Data Refresh Operations

Current validation scope, 2026-10-06: unattended processing/activation is parked
in #141. Continue the reviewed operator update process and existing validation
deployment playbook; do not enable automation or change live trust/credentials.
The numbered pipeline below describes the deferred target, not current operations.

Owner-approved policy, 2026-10-05: supported routine data-only changes activate
automatically after all reviewed gates pass. Capture alone is not promotion.
The #21 pipeline is still unimplemented; current validation deployments remain
explicit operator actions until that separately tested boundary is delivered.

1. The watcher detects a source change and archives exact raw bytes to R2.
2. It commits the observation, artifact references, source cursor, and bounded
   change or incident record to the control database.
3. #21 automatically selects an exact recovery image and change record. Its
   protected processing workflow uses a read-only archive credential to
   verify key, checksum, revision and observation identity, then emits
   a pinned manifest proposal with provenance and a logical diff. The watcher
   receives no GitHub token. Manual dispatch is a recovery path, not a routine gate.
4. Apply the reviewed significance policy to source terms, structure, dates,
   ruleset pairing, unresolved references, logical changes and runtime behavior.
   Routine candidates proceed; failed or significant candidates require review.
5. Build and verify a complete immutable reference database and compatible images.
6. Automatically promote the exact tested candidate through the signed release
   contract, verify actual public behavior and record durable activation. Roll
   back the complete set on failed post-activation checks, never individual rows.

Large or unexplained source diffs block release. A new base act requires manual
review beyond automated comparison.

### New Base Act And Conditions Review Checklist

A new base act is not a routine amendment even when its workbook parses:

1. Pin the act, consolidation/amendment lineage and exact attachment bytes.
   Independently record publication, entry into force, service applicability and
   knowledge evidence; never derive them from a filename or download date.
2. Verify workbook layout, sheets, merged-cell structure, column meanings,
   source consumption and parser/transformation identity. A new layout needs a
   reviewed parser change, not wider automatic acceptance bounds.
3. Review old/new group/product codes and catalog context (1a versus 1ae),
   renumbered legal clauses, directional list labels and source-local vocabulary.
   Do not equate internal row/list/selector IDs across releases or infer renamed
   groups from similar titles. Record explicit correspondence or unpaired changes.
4. Compare condition trees, Boolean operators, selectors, thresholds, ranks,
   age/LOS boundaries, discharge modes, exclusions and guarded source fragments.
   Retain unsupported conditions and their reasons; they must not disappear or
   become executable defaults during a renumbering or structural rewrite.
5. Recheck source-local list membership including duplicates and markers, then
   recompute exact dictionary correspondence against explicit ICD release pins.
   Missing dictionary matches stay visible, not deleted or replaced by parents.
6. Review catalog/conditions/algorithm/dictionary pairing, MODTAR binding state,
   supersession, gaps, retroactivity and row-level deferred applicability. Exercise
   both sides of every changed boundary plus knowledge dates before/after publication.
7. Inspect the complete logical diff and separate substantive changes from source
   row/order/provenance movement. Run build/open integrity and actual CLI checks;
   record exact old/new identities, reviewer and dispositions for unexplained changes.
8. Approve a new supported policy/profile only after representative runtime and
   rollback evidence. Stop #21 promotion and notify the owner until these checks
   are reviewed; a readable diff is evidence, not automatic legal clearance.

### Failed Data Updates And Telegram

Record a durable incident and notification intent before attempting delivery.
Send each failed candidate to the existing Kuma Telegram destination with its
source/change/candidate identity, failing stage, stable failure code, bounded
allowlisted expected/observed checks, active retained/rollback release and a
private receipt or workflow link. Details must come from typed safe diagnostics,
not raw exception strings or source rows. No queries, encounter data, credentials,
signed URLs, headers, cookies, local paths or stack traces enter Telegram.

Use a distinct data-update event/target, not a watcher heartbeat that could
misreport a successful capture. Generic watcher `failed` pushes are insufficient.
Each distinct failed candidate must notify even if the monitor is already down;
bounded retries deduplicate one failure, not all subsequent failures. Keep
delivery intent pending on send failure and expose its separate incident for
operator retry. A send failure cannot mark processing successful, undo a valid
activation, advance the activation cursor or erase the original failure.
Verify actual private delivery and redaction before enabling #21 automation.

### Data-Update Processor Setup

The [host processor](archive-processing.md#host-processor-141-phase-b3) runs in
the reference API image, so it starts working after the API release that ships
`minos processor run`. Owner steps:

1. Create a Cloudflare R2 API token with Object Read only on `minos-archive`.
   It lives in the MinosHealth vault as the login item
   `minos-production-processor-r2-credentials` (ID
   `3lapt4pwp43x2rojdjzccsumhi`; its title starts with a space, so use the ID)
   with the fields `access key id` (32 hex) and `secret access key` (64 hex).
2. In Kuma, create a push monitor named `data-update` with a heartbeat interval
   of 28800 seconds (six-hour timer, jitter and the 75-minute run cap) and the
   Telegram notification. Its push token (the 32 characters after
   `/api/push/`) is the `data_update` field of the `MinosHealth Uptime Kuma`
   item (ID `clb4foeld7unl27zh7o6ainxja`), beside the watcher tokens.
3. After the API release, install the processor. The subshell builds the
   credential file and the Kuma target in a private temporary directory and
   removes it on exit:

   ```sh
   (
     set -euo pipefail
     umask 077
     secrets="$(mktemp -d)"
     trap 'rm -rf -- "$secrets"' EXIT INT TERM HUP
     printf '[default]\naws_access_key_id = %s\naws_secret_access_key = %s\n' \
       "$(op read 'op://MinosHealth/3lapt4pwp43x2rojdjzccsumhi/access key id')" \
       "$(op read 'op://MinosHealth/3lapt4pwp43x2rojdjzccsumhi/secret access key')" \
       > "$secrets/r2"
     printf '{"version":1,"sources":{"data-update":"%s"}}\n' \
       "$(op read 'op://MinosHealth/clb4foeld7unl27zh7o6ainxja/data_update')" \
       > "$secrets/kuma.json"
     ANSIBLE_CONFIG=infra/ansible/ansible.cfg \
       uvx --from ansible-core==2.21.4 ansible-playbook \
       infra/ansible/processor.yml \
       -e processor_r2_credentials_source="$secrets/r2" \
       -e processor_uptime_kuma_targets_source="$secrets/kuma.json"
   )
   ```

4. Start one run and read its two journal lines:
   `systemctl start minos-processor.service` and
   `journalctl -u minos-processor.service -n 5 --no-pager`. Expect
   `{"processor_host": "completed"}` and a verdict line, or `failed: fetch` if
   a live input is missing from R2. This first run also confirms the
   `minos_monitoring` network name, the runner-created
   `minos-processor-egress` bridge under the host firewall and the host build
   time. After a failure, `docker logs minos-processor` shows the run.

Later applies may omit both sources; the installed files are kept and
re-checked. `processor.yml` never touches the `minos` Compose project.

## Mutable Product Data Gate

The watcher control database is operational metadata, not product or user data.
If accounts, favourites, annotations, analytics, subscriptions, or other user
writes are added, work stops until their separate design includes data
classification, retention, access control, encrypted off-site backups, and a
tested recovery procedure. Encounter payloads remain prohibited from storage.
