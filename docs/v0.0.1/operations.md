# Operations

## Operating Model

One dedicated Mikr.us `3.0` host runs the public edge, API, watchers, scheduler,
and Cloudflare Tunnel through Docker Compose. The host is replaceable, but it is
not stateless: the watcher control SQLite database is mutable and therefore
receives consistent, tested S3 recovery images.

The complete system of record is Git, reviewed manifests, pinned image digests,
versioned S3 source snapshots, revision-numbered control-database recovery
images, signed release and deployment records, and Terraform state. A run is
not acknowledged as successful while the Mikr.us disk is the only copy of its
control metadata.

## Asset Inventory

| Asset | Primary location | Recovery protection |
| --- | --- | --- |
| Source and manifests | Private GitHub repository | Git history and remote |
| Raw source snapshots | Private versioned S3 bucket | Content addressing, conditional writes, no watcher delete permission |
| Watcher control database | Mikr.us persistent volume | Immutable revisioned S3 recovery images and restore drills |
| Generated reference database | Private API image and data artifact | GHCR digest, checksum, reproducible build |
| Static web artifact | Private edge image and build artifact | GHCR digest, checksum, reproducible build |
| Watcher runtime | Private watcher image | GHCR digest and bounded release retention |
| Terraform state | Private S3 state bucket | Versioning, encryption, locking, recovery copy |
| Domain | Registrar | Lock, 2FA, auto-renewal, recovery codes |
| DNS and ingress | Cloudflare | Configuration record and recovery credentials |
| Host configuration | Git and Ansible | Reprovisioning test |
| Operator credentials | Password manager | Off-host recovery access |
| Release manifests | Private GitHub Releases and operator-only S3 prefix | Independent signature and immutable digest |
| Deployment receipts | Operator-only S3 prefix | Signed append-only chain with prior receipt |
| Trusted recovery inventories | Operator-only S3 prefix and off-host recovery kit | Non-expiring signed chain and verification key |
| Trusted recovery copies | Operator-only S3 prefix | Independently selected; 400-day expiration eligibility |

S3 source data and Terraform state use separate buckets and policies. The
Mikr.us watcher identity cannot read or modify Terraform state and cannot delete
snapshot objects or versions.

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
8. Migration compatibility, an S3 native-checksum probe, a recovery-image
   round trip, and a dedicated Healthchecks.io preflight check succeed while
   dispatch remains paused.
9. The previous watcher stops; the exact candidate starts paused against the live
   volume, applies the tested migration, and verifies the live revision,
   migration checksums, WAL, secrets, and post-migration recovery image. The two
   watcher processes never own the volume concurrently.
10. Container health, memory, disk, and error signals remain within recorded
   launch thresholds under representative traffic and one bounded watcher
   canary run.

The first production deployments are explicit operator actions. Automation may
later execute the same bounded procedure over restricted SSH, but it must not
change the release selection rule or deploy mutable tags.

## Rollback

Rollback restores the previous complete compatible API, edge, watcher,
`cloudflared`, and dataset digest set, not individual containers selected
independently. It does not rebuild, retag, mutate the reference database, or
follow `latest`.

After rollback, repeat readiness, static-integrity, browser, and known-search
tests through the public hostname. Preserve failed-version logs and release
metadata until the incident is understood. Do not let image cleanup remove the
current or prior compatible release.

The scheduler remains quiesced until the serving and watcher preflights pass.
Every `v0.0.1` control migration and candidate write must remain readable by the
retained previous watcher throughout the rollback window; test this on a copy
before touching the live database. An incompatible control migration blocks the
release and requires a separately designed maintenance cutover.

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
   key and compares byte count and native checksum after either create or `412`;
   a `409` retries the bounded conditional operation. Multipart upload is
   prohibited for watcher-created recovery images.
8. For a run image, records `heartbeat_pending`, sends the external success
   heartbeat, and marks the run successful only after the response succeeds.
9. Removes the temporary local copy.

Current recovery images become expiration-eligible after 30 days for `run`, 31
days for `daily`, 91 days for `weekly`, and 400 days for `monthly`. After current
expiration creates a delete marker, noncurrent versions become deletion-eligible
after one additional day; expired delete markers are removed. S3 lifecycle is
asynchronous and provides no guaranteed physical deletion deadline, so these are
deletion-eligibility thresholds rather than hard maximum retention bounds. A
seven-day incomplete-multipart abort remains defense in depth for operator
tooling. Raw source blobs never expire. Recovery images contain no credentials,
signed URLs, medical queries, encounter payloads, or patient data.

Recovery paginates the class prefixes within configured maximum object counts
derived from schedule frequency and retention; exceeding a bound stops automatic
selection for operator investigation rather than truncating candidates. It sorts
valid candidates by embedded `(state_revision, created_at, class_rank, sha256)`,
where `run > daily > weekly > monthly`, then
verifies key grammar, native S3 checksum, byte count, embedded metadata, complete
migration checksums, and SQLite integrity in descending order. A successfully
uploaded object is the completion record; partial PUTs never become visible.

These checks prove integrity, not that an authorized but compromised watcher
wrote honest state. At least weekly, and also at deployment, key rotation, and
recovery drills, an operator command selects one verified recovery image, copies
it conditionally with the operator identity to
`trusted-recovery/<20-digit-sequence>/<sha256>.sqlite3`, and verifies the copy.
It then signs an inventory containing that key, S3 version ID, checksum,
revision, S3 `LastModified`, sequence, and prior-inventory digest, and writes it
conditionally to `recovery-inventories/<20-digit-sequence>.json`. The command
derives the next sequence from the fully verified unique inventory head, creates
the sequence-only key with `If-None-Match: *`, accepts `412` only for an exact
idempotent byte match, then relists and rejects gaps, duplicate sequences, bad
links, or multiple heads. An unused concurrent trusted copy is an orphan and is
never selected. Trusted database copies become expiration-eligible after 400
days, with noncurrent versions eligible one day later. The small signed inventory
objects never expire, so their chain remains verifiable from sequence one. The
public verification key and latest trusted inventory also belong in the off-host
recovery kit; the signing key never enters Mikr.us. Missing the weekly cadence
triggers an external alert.

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
signed S3 manifest is the recovery authority.

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
3. Install fresh service-restricted Cloudflare, Healthchecks.io, and AWS secret
   files plus the root-owned GHCR Docker credential.
4. Verify the unique signed deployment-receipt chain head and its signed release
   manifest, then pull the exact current API, edge, watcher, and `cloudflared`
   digests.
5. Select the newest valid control-database recovery image under the ordinary or
   suspected-compromise rules above with the operator identity, not the watcher
   key.
6. Verify its signature coverage when required, S3 version, checksum, embedded
   revision and migration set, and integrity before placing it on the persistent
   volume with the pinned watcher ownership and modes.
7. Start Compose without public ingress and verify internal readiness, source
   schedules, and known searches.
8. Start `cloudflared`, then verify TLS, readiness, mobile and desktop rendering,
   known searches, and watcher heartbeat through external services.

### Control Database Corruption

Stop the watcher before recovery. Preserve the corrupt database and WAL files
for investigation, then restore the newest trusted revision under the selection
and compromise rules above. Runs whose recovery images were not durably verified
were never acknowledged as successful and must be retried or investigated. An
orphaned raw blob alone cannot reconstruct its lost source relationship or
observation time. Never create missing history by substituting a current
upstream response for an earlier observation.

### Raw Snapshot Loss

Recover a prior S3 object version by key and checksum. The accepted design uses
S3 as the only raw-snapshot provider, so complete AWS account compromise remains
a documented residual risk. The watcher has no delete permission, and ordinary
host compromise must not grant access to Terraform state or bucket policy.

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

## Recovery Drills

Before `v0.0.1`, perform and time at least:

- restoration of a prior Terraform state version in an isolated test;
- restoration and integrity verification of a control-database S3 recovery
  image, including the suspected-compromise signed-inventory path;
- reconstruction of a generated database from pinned raw snapshots;
- complete Compose deployment to an empty `linux/amd64` host;
- rollback to the prior compatible API, edge, watcher, `cloudflared`, and
  dataset digest set;
- recovery of registrar, Cloudflare, GHCR, AWS, and SSH access without relying
  on an active developer laptop.

Documentation alone is not a tested backup. Record observed recovery time and
every unresolved manual step.

## Logging

Loguru is configured once at each Python process entry and emits allowlisted
structured JSON to stdout in production. Local development may use a
human-readable console format with the same fields. Libraries and domain code
must not install independent sinks or serialize arbitrary request, response, or
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

## Secrets And Configuration

Local development uses ignored `.env` files based on `.env.example`. Local
cloud credentials stay in external profiles or keychains.

Production uses host-managed secret files readable only by root and the numeric
UID or GID of the required service. Ansible creates service-specific directories
and Compose mounts each file read-only into only that container:

- Cloudflare Tunnel credential for `cloudflared`;
- narrowly scoped AWS access key for the watcher.
- Healthchecks.io ping target for the watcher.

The GHCR package-read token belongs in the root-owned host Docker credential
store for image pulls and is not mounted into a container.

The recovery copies, SSH material, registrar credentials, and emergency access
stay in the password manager. Secret values never enter `.env`, GitHub Actions,
Terraform input or state, Compose YAML, images, or logs. Non-secret endpoints,
regions, bucket names, timeouts, and release digests remain normal configuration.

GitHub Actions uses OIDC for AWS reads and stores no long-lived AWS key. The
Mikr.us key is an explicit external-host exception: it is limited to the data
bucket, cannot delete, is installed outside the repository, and is rotated
immediately after any suspected exposure.

## Metrics And Alerts

Initial operational signals are:

- API request count by route template and status class;
- warm latency and bounded timeout rate;
- container restart count and health;
- host memory, disk space, and inode headroom;
- external readiness and known-search failures;
- deployed image digests and dataset release;
- per-source observation age;
- watcher due, running, failed, stale, and disabled status;
- backup age and last restore-test date;
- S3 spend and forecast.

Healthchecks.io receives only opaque schedule IDs and start, success, or failure
pings. It receives no URLs, source content, stack traces, or medical data. A
missing heartbeat detects complete host or scheduler failure from outside the
Mikr.us fault domain.

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
- Review Terraform plans for replacement, public exposure, IAM, and retention.
- Scan images and exercise the actual runtime after formatter or autofix passes.
- Report vulnerabilities through GitHub private vulnerability reporting.

## Data Refresh Operations

An upstream refresh is not an automatic production update:

1. The watcher detects a source change and archives exact raw bytes to S3.
2. It commits the observation, artifact references, source cursor, and bounded
   change or incident record to the control database.
3. An operator dispatches the issue #21 workflow with a selected recovery-image
   key, version ID, and checksum. The protected default-branch workflow uses the
   read-only GitHub OIDC archive role, verifies the image, extracts only the
   selected bounded change record, and opens a manifest pull request with
   provenance and a human-readable diff. The watcher receives no GitHub token.
4. The operator reviews source terms, dates, ruleset pairing, and unexplained
   changes.
5. CI builds and verifies a new immutable reference database and images.
6. A signed release promotes the already tested digest set to Mikr.us.

Large or unexplained source diffs block release. A new base act requires manual
review beyond automated comparison.

## Mutable Product Data Gate

The watcher control database is operational metadata, not product or user data.
If accounts, favourites, annotations, analytics, subscriptions, or other user
writes are added, work stops until their separate design includes data
classification, retention, access control, encrypted off-site backups, and a
tested recovery procedure. Encounter payloads remain prohibited from storage.
