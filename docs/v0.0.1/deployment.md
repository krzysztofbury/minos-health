# Deployment

Status reconciled 2026-10-11: the public-validation API, web edge, RSK/BAW/NFZ
watchers, Cloudflare Tunnel and Uptime Kuma are deployed on Mikr.us. Private
GHCR publication and guarded exact-digest API/edge/bundle updates are implemented.
The latest verified activation is #156 (`5458ef2`, 2026-10-09, schema 10);
[its receipt](https://github.com/krzysztofbury/minos-health/pull/156#issuecomment-6077733204)
records isolation, public checks and rollback.

This document describes the target full-product release contract. The current
[validation update path](../validation/validation-deploy.md) mounts a separate
verified read-only data bundle. Independent signatures and complete compatible
release receipts (#41), hosted archive-reader acceptance (#19), recovery/R2
drills (#49), representative capacity (#42) and full deployment acceptance (#27)
remain open. Startup warm-up timeout is tracked in #157. Unattended activation
is deferred in #141, not a prerequisite for reviewed validation updates.

## Goals

The delivery system must:

- run the complete Minos production stack on one dedicated Mikr.us `3.0` host;
- build one immutable API image containing the generated reference database;
- build one immutable edge image containing the static web artifact;
- build one immutable watcher image with the scheduler and source adapters;
- build checksummed standalone CLI and data-bundle artifacts;
- promote already tested digests rather than rebuild for release;
- keep CI separate from the production host and its secrets;
- make the host replaceable from Git, GHCR digests, R2 recovery images, and a
  runbook;
- support bounded rollback without following mutable image tags;
- remain inexpensive and understandable for one operator.

## Production Topology

```text
Browser
  -> Cloudflare
  -> outbound Cloudflare Tunnel
  -> edge container
     -> static web artifact
     -> /api/v1/provenance/rsk -> read-only public watcher projection
     -> /api/*, /healthz, /readyz -> API container

Status-page browser
  -> status.minoshealth.pl
  -> conditional outbound Cloudflare Tunnel route
  -> Uptime Kuma container

Watcher container
  -> APScheduler
  -> mutable control SQLite on a persistent volume
  -> private R2 snapshot bucket (SHA-256 keys, bucket lock)
  -> dedicated local public-projection volume
  -> best-effort terminal up/down push to Uptime Kuma (down names the failure category)

Uptime Kuma container
  -> private monitoring network
  -> outbound HTTP monitors and notifications
  -> persistent /app/data bind mount

GitHub-hosted Actions
  -> test, build, scan, attest, publish to private GHCR
  -> no production runtime or Docker socket access
```

The host exposes no application port to the public internet. `cloudflared`
opens outbound routes to the edge and, only after explicit enablement, Uptime
Kuma. The edge serves static files and proxies API routes under the same origin.
The application and monitoring networks are private Compose networks. Neither
Kuma nor the watcher receives a Docker socket or host port.

Mikr.us `3.0` is the selected production plan: 2 GB RAM, 25 GB disk, Docker
support, and a fixed 130 PLN annual price observed during planning. It provides
no SLA, so Minos depends on tested recovery rather than host durability.

## Runtime Components

The production Compose project contains:

- `cloudflared`: outbound ingress only, with no Docker socket;
- `edge`: unprivileged static server and reverse proxy;
- `api`: FastAPI/ASGI service with the generated reference SQLite database
  embedded in the immutable image;
- `watcher`: APScheduler, source adapters, and the mutable control SQLite
  database mounted from a dedicated persistent volume, plus write access to a
  separate public-projection root;
- `uptime-kuma`: self-hosted Uptime Kuma 2.5.5 from the official MIT
  `slim-rootless` `linux/amd64` image, rootless as `10003:10003`, with a private
  monitoring network, a separate outbound monitor and notification network,
  and `/var/lib/minos/uptime-kuma` mounted at `/app/data`;
- `watcher-bootstrap-rsk`: an explicit one-shot `bootstrap` profile service
  using the same pinned production watcher image, identity, secrets, networks,
  and persistent volumes. No active service depends on it, so ordinary stack
  activation cannot run registration implicitly.

The generated reference database and watcher control database are deliberately
different systems:

- the reference database is immutable, release-scoped, and opened read-only by
  the API and CLI;
- the control database is mutable operational state for source configuration,
  schedules, runs, observations, artifact references, changes, and incidents.

The watcher is not included in the API image. The edge image contains no Python
runtime, private source snapshots, credentials, or generated medical database.
It mounts only the dedicated public-projection root read-only. It cannot access
watcher control SQLite, private filesystem or R2 archive data, staging, R2
credentials, object-store URLs, or presigned URLs.

## Artifact Lifecycle

Private GHCR is the canonical image registry. Tags aid discovery, but digests
are deployment identities:

```text
ghcr.io/krzysztofbury/minos-health-api@sha256:<digest>
ghcr.io/krzysztofbury/minos-health-web@sha256:<digest>
ghcr.io/krzysztofbury/minos-health-watcher@sha256:<digest>
cloudflare/cloudflared@sha256:<digest>
docker.io/louislam/uptime-kuma@sha256:8bdad438d293e998fd7d45004cefb3baea0f96d3bc774502bba99c17d616e1f6
```

`latest`, `main`, and other mutable tags are not deployable references.

The target lifecycle is:

1. A pull request builds fixture images and runs the Compose stack through a
   real browser-to-API search without production secrets.
2. `main` builds the pinned complete dataset, API image, edge image, watcher
   image, standalone CLI bundles, and data bundle.
3. CI scans and exercises each runnable image, then publishes commit-addressed
   images and provenance to private GHCR.
4. CI verifies each deployable digest is the intended runnable `linux/amd64`
   manifest and records compatible API, web, watcher, and dataset versions in a
   release manifest, including the pinned `cloudflared` digest.
5. A mandatory signed `v*` tag identifies a commit whose artifacts already
   passed CI. Release verification does not rebuild them.
6. CI publishes the candidate release manifest and GitHub OIDC provenance as a
   private GitHub Release asset after repository immutable releases are enabled
   and verified. The operator verifies them and the signed tag, signs the
   manifest with an independent off-host key, and conditionally copies the
   manifest and signature to an operator-only R2 release prefix. The signed R2
   copy remains the recovery authority and binds the tag, source commit,
   artifact digests, compatibility, and manifest digest.
7. The operator quiesces watcher dispatch, reconciles any `committed` or
   `heartbeat_pending` run, creates and verifies a pre-deploy recovery image,
   and records the current and candidate release manifests.
8. A scheduler-paused candidate watcher validates a disposable database copy.
   Supported v1 and v2 copies are migrated to the exact candidate schema before
   integrity, migration-checksum, identity, volume, secret, R2 checksum,
   and recovery-image checks. The original live database is not changed. The
   copy is limited to 2 GiB and a 240-second deadline, with a
   300-second outer timeout. A missing database on an initial empty host remains
   valid. A newer or malformed schema fails before the active stack is stopped.
9. The operator stops the previous watcher and captures a second consistent,
   bounded preactivation image. After atomic runtime promotion, the candidate
   migration command upgrades the quiesced live database from supported v1 or
   v2 to v3 and reports the exact from/to result. If a later step fails after a
   schema change, rescue keeps the candidate runtime and migrated database
   stopped; it never starts an older writer against the newer schema.
10. Ansible explicitly runs the profile-gated `watcher-bootstrap-rsk` one-shot
    service with `--no-deps`. An absent RSK registration is created
    transactionally, an exact existing registration is a no-op, and disabled,
    customized, or partial state fails without being overwritten. A read-only
    command validates any existing immutable public generation. An absent
    projection is valid only before the canary.
11. Before active services start, Ansible runs one manual RSK collection with a
    900-second outer timeout. It archives the catalog and `basic-info` response
    for every returned HCD, so ICD-9 and ICD-11 metadata are part of the same
    committed canary. The public projection contains file-level entries for
    every observed HCD that exposes files. Manual mode additionally forces a
    fresh download of the separately allowlisted `00CD10` XML product. The
    canary must commit and publish a canonical, schema-valid projection.
12. The operator starts the exact candidate digest with scheduling unpaused only
    for the active watcher. Its health contract becomes ready after startup
    reconciliation, stale-claim expiry, job reconstruction, and bounded catch-up;
    every probe verifies the marked scheduler process and exact control schema.
    Compose waits for health, and Ansible then requires 30 seconds of healthy,
    restart-free stability. It requests `/zrodla`, its exact CSS and JavaScript
    assets, the strict CSP, and the provenance endpoint through the running
    production edge. Provenance must return HTTP 200 canonical JSON whose
    SHA-256 matches the revalidated current generation. Candidate and ordinary
    preflight services remain scheduler-paused and cannot report active
    readiness.
13. Success writes a signed append-only deployment receipt to an operator-only
    R2 prefix. Before the canary commits, failure restores the complete previous
    release and may restore the pre-deploy recovery image only if the live schema
    was not migrated. After migration, explicit recovery retains the promoted
    candidate and current live state; an older database image or writer must not
    be restored automatically.

Initial production deployment is an explicit operator action. A later SSH
workflow may automate the same exact-digest procedure, but it must not broaden
CI access to production data or the Docker socket.

Watchtower is not a deployment mechanism. It requires root-equivalent Docker
socket access and follows mutable tags; images pinned by digest cannot be
updated by Watchtower. It also cannot prove that API, web, watcher, and dataset
artifacts form one reviewed compatible release.

## GitHub Actions

GitHub-hosted Actions own verification and artifact production. They are not
the production scheduler and no self-hosted Actions runner runs on Mikr.us.

Pull requests use committed fixtures and receive neither production secrets nor
raw-source access. The current baseline checks repository hygiene, Ruff,
BasedPyright, mypy, pytest, Bandit, Gitleaks, dependency policy, and the built
wheel. Planned checks add fixture ingestion, deterministic artifact
builds, container scans, Compose integration, and real browser smoke tests.

GitHub Actions holds no object-store credential; artifact builds that need
archived snapshots run on the operator machine with the read-only selection
(`minos data build --archive-s3-selection`). CI publishes only to private GHCR
and GitHub release storage. It does not connect
to the production host during ordinary builds.

The issue #21 processing handoff is a separate trusted workflow from the
protected default branch. Automatic selection supplies one verified recovery-image identity
and bounded change-record identity, uses a read-only archive credential, verifies
both, and exports only the selected normalized proposal. Pull-request jobs
receive no archive credential, and the production watcher receives no GitHub
credential.

The 2026-10-05 owner policy requires automatic activation of passing routine
data-only updates, with no per-update manual dispatch, merge or deployment gate.
Manual dispatch remains a recovery path. Failed or significant candidates retain
the last good compatible release and produce a detailed privacy-safe Telegram
notification to the existing Kuma destination. Trusted activation/notification
credentials stay separate from ordinary builds and untrusted PR jobs. #21 owns
implementation and delivery/rollback evidence; this policy does not enable it.

GitHub service limits change and must be rechecked before enabling full-data
builds. Image retention remains bounded while preserving the current and prior
compatible release sets.

## Container Requirements

Every production image must:

- target `linux/amd64` and run on the same architecture in CI and production;
- run as a non-root user with a read-only root filesystem;
- drop unnecessary Linux capabilities and use an explicit process limit;
- have bounded memory and temporary storage;
- contain no build tools, test fixtures, shell, or package manager unless a
  demonstrated runtime need requires one;
- expose liveness and readiness where applicable;
- terminate cleanly and fail rather than serve incomplete data;
- carry source commit, build, dataset, and licence labels.

Only the watcher and Uptime Kuma receive persistent writable storage. Ansible
initializes the watcher's persistent control directory as the pinned watcher
UID and GID with mode `0700`;
SQLite, its WAL, recovery-image staging files, and their parent directory remain
writable by that identity. Restore reapplies the same ownership and modes before
startup. Downloads use a separate disk-backed `noexec,nosuid,nodev` staging
directory with one temporary file, a `600 MiB` application-enforced quota, and a
`512 MiB` per-file ceiling matching the RSK transport default. The sole worker
removes interrupted staging files at startup and after each attempt. Before a
run or recovery image, the watcher requires at least the larger of `1 GiB` or
three control-database sizes plus the maximum download size to remain free.
Crossing the configured `2 GiB` control database ceiling blocks new runs and
raises an incident rather than filling the host. API and edge services receive
only bounded tmpfs mounts when a demonstrated runtime path needs temporary
writes. Exact raw bytes are uploaded to R2 and are not retained as the only copy
on the host. Logs use Docker's bounded `local` driver rather than an unbounded
JSON log file.

Ansible initializes `/var/lib/minos/uptime-kuma` as `10003:10003` with mode
`0700`. Image identity is pinned beside the data. An image change fails closed
until a stopped full-data backup and migration procedure exists. Never copy
Kuma's SQLite files while the service is live.

The watcher publishes sanitized provenance under
`generations/<state-revision>-<projection-sha256>/`, fsyncs every file and
directory, then atomically replaces the `current` symlink using descriptor-
relative no-follow operations. Generation files are mode `0444` and directories
are mode `0555`; the current generation and two predecessors are retained.
Automatic publication refuses to replace a newer current revision with an
older recovered control database. Nginx serves only the exact extensionless
`/api/v1/provenance/rsk` route with JSON, `nosniff`, same-origin, and `no-store`
headers. The responsive Polish `/zrodla` page reads only that same-origin route,
validates the complete bounded schema before rendering, and derives every
official RSK link from a validated UUID. It renders at most 75 rows at once,
debounces local filtering, and labels current, stale, severe-stale, and material
future-clock states separately from schema validity. Nginx serves the page and
the shared `/site.css` and its `/sources.js` asset only on exact query-free
paths, compresses JSON, and applies a CSP with external styles and same-origin
fonts only. The artifact
namespace is deny-all and returns 404. The page offers no Minos artifact
download.

The watcher image remains shell-free. Compose invokes the packaged
`minos watcher health` command directly. Its readiness marker is bound to the
live process identity, refreshed by the sole scheduler worker, and removed on
shutdown; the command also validates the exact bundled control schema read-only.
Production allows a 6,400-second health start period, a 6,600-second Compose
wait, and a 7,800-second systemd start timeout. These bounds cover the supported
one-hour collector deadline plus bounded recovery and preflight work. After
startup, a marker older than 75 minutes fails health as an unresponsive worker.

Operators inspect durable watcher state and bounded local logs on the host with:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  logs --since 24h --tail 500 --timestamps watcher
```

The optional source filter is `minos watcher status [SOURCE_ID] --json`.

The watcher sends Uptime Kuma only `up` after durable success or `down` after a
durable failed terminal result. It sends nothing at run start. Monitor requests
are best-effort and never block or alter the watcher result. The persisted
`heartbeat_pending` name remains a compatibility name for durable local
finalization before `successful`, not a pending monitoring request.

## Watcher Scheduling And State

Stable APScheduler `3.11.x` runs as a `BlockingScheduler` in UTC with
`coalesce=True`, `max_instances=1`, one executor worker, and an explicit bounded
`misfire_grace_time`. Its job store is in memory. The control SQLite database is
the durable source of truth for enabled sources, UTC cron expressions,
`next_due_at`, run modes, configuration revisions, run claims, source cursors,
observations, incidents, and audit events. APScheduler does not serialize jobs
to another database and does not require SQLAlchemy.

Startup reconciles every `committed` and `heartbeat_pending` run before dispatch,
then expires stale `running` claims, atomically claims at most one overdue
schedule, records its missed scheduled time, and advances `next_due_at` to the
first cron fire strictly after the same transaction-captured `now`. This
application-level cursor, not the in-memory job store, coalesces downtime into
one bounded catch-up run. A manual invocation uses the same claim path. A source
with a `running`, `committed`, or `heartbeat_pending` run cannot be claimed.

Each claim carries an unpredictable token and a monotonically increasing source
fencing epoch. Collector mutations compare the source ID, token, epoch,
`running` status, and `deadline > now`. Expiry instead compares the same identity
with `running` and `deadline <= now`, then atomically records the incident and
`expired` state before a later claim can increment the epoch. Reconciliation
compares the run ID, exact expected `committed` or `heartbeat_pending` state, and
state revision; it does not require a live claim. A stale worker may finish
network I/O, but its collector transaction is rejected and cannot advance a
source cursor after ownership changes.

For each raw object, the watcher derives the requested key from its local
SHA-256 and uses single-part `PutObject` with exact `ContentLength`, the
Base64-encoded whole-object SHA-256 in `ChecksumSHA256`, and
`If-None-Match: *`. Multipart upload is prohibited for watcher objects; source
and control-database limits remain below the R2 single-part limit. It then uses
`HeadObject` with checksum mode enabled against that same requested key. Both a
successful create and an existing key must return the expected byte count and
native checksum; mismatch is a hard incident. R2 checks bucket lock rules before
`If-None-Match`, so an existing key answers `412 Precondition Failed`, or
`409 ObjectLockedByBucketPolicy` under a locked prefix. Any other
`409 Conflict` receives a bounded retry of the conditional operation. The watcher then commits the
related observation, blob reference, and source cursor in one local SQLite
transaction. A post-upload crash can leave an unreferenced immutable object; a
retry reuses it only after the same verification.

Every restorable control-database transaction increments a monotonic
`state_revision`; a transaction that advances source state also leaves its run
in `committed`. No later claim for that source may start while a committed run
awaits reconciliation. The reconciler uses the SQLite backup API to create an
integrity-checked recovery image for that exact revision, uploads and verifies
it under an immutable revision-and-digest key, then records `heartbeat_pending`.
It publishes the local projection from that state and marks the run `successful`
after durable local finalization. Every transition is idempotent. On restart or
restore, reconciliation resumes upload, verification, or finalization, and a
newer run never overtakes it. `heartbeat_pending` is retained as a compatibility
name for this local phase and does not represent an external ping.

The legal run transitions are `running -> committed -> heartbeat_pending ->
successful`, `running -> failed`, and `running -> expired`. Acquisition creates
`running`; only the matching live fenced claim can leave it. `committed` never
expires, terminal states never reopen, and no transition may be skipped.

Claiming a durable schedule advances its cron cursor but retains the original
scheduled time and run mode as a pending obligation until the run reaches
`successful`. Failure or expiry atomically records a bounded exponential
retry-not-before time. A single scheduler interval job checks eligible durable
work, while SQLite serialization and the source blocking-run guard permit only
one claimant. Restart reconstruction uses the same persisted state, so an
exhausted daily, catch-up, or weekly collection is delayed rather than lost or
immediately busy-looped.

There is no atomic transaction across local SQLite and R2. A complete host loss
after local commit but before recovery-image upload can lose that unacknowledged
observation relationship even when its raw blob survived. This narrow window is
an accepted residual risk; the run was never reported successful and must be
retried or investigated.

## Object Storage

Cloudflare R2 is the only object store and is not an application runtime
(#200). One private EU-jurisdiction bucket, `minos-archive`, holds
content-addressed raw bytes, immutable control-database recovery images,
checksum probes, trusted recovery copies and inventories, signed release
manifests, and deployment receipts under separate prefixes.

R2 has no object versions, so immutability comes from three layers: keys embed
the SHA-256 of their bytes; every write is create-once (`If-None-Match: *`) with
`x-amz-checksum-sha256`; and bucket lock rules refuse overwrites and deletes.

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

Incomplete multipart uploads are aborted after seven days as defense in depth;
watcher writes never use multipart upload. Every read verifies SHA-256 and byte
count, and a recovery download is pinned with `If-Match` on the ETag that HEAD
verified. R2 answers `501` to `x-amz-expected-bucket-owner`, so the account is
pinned by configuration: only an R2 account S3 endpoint is accepted.

The Mikr.us watcher uses the bucket-scoped token `minos-r2-watcher`
(Object Read & Write). The bucket lock, not the token, provides the no-delete
guarantee, and the token cannot change lock or lifecycle rules. The operator
token `minos-r2-operator` is separate. Both secrets live in the MinosHealth
vault; the watcher's is installed as a service-restricted host file. Release
manifests, trusted recovery copies and inventories, and deployment receipts
require an independent signing key kept off-host.

There is no ECR, Lambda, CloudFront, frontend bucket, or managed log stack, and
Minos keeps no Terraform state.

## Cloudflare And Domains

After legal clearance and registration:

- `minoshealth.pl` is canonical and routes to Cloudflare Tunnel;
- `minoshealth.com` redirects permanently to `.pl` while preserving safe paths;
- `status.minoshealth.pl` routes to Uptime Kuma only when
  `uptime_kuma_public_status_enabled=true` after private bootstrap;
  the tunnel sends every path of that host to Kuma, so a Cloudflare Access
  application (`minos-status-admin`, owner email only, 24-hour session) guards
  the Kuma login and admin paths: `/socket.io*`, `/dashboard*`, `/setup*`,
  `/settings*`, `/manage-status-page`, `/add*`, `/edit/*`, `/clone/*`,
  `/maintenance*`, `/api/push/*`, `/api/badge/*` and `/metrics`. The public page
  needs only `/`, `/assets/*`, `/icon.svg`, `/api/entry-page` and the three
  `minos-health` status-page reads, and never opens `/socket.io`. Watcher pushes
  stay on the internal network and never pass through Cloudflare (2026-10-20);
- `minos.health`, if purchased, redirects the same way;
- Cloudflare manages DNS and tunnel routing;
- DNSSEC is enabled after the registrar and delegation chain are verified;
- domain purchase, account recovery, and renewal remain manual.

The tunnel terminates public ingress without exposing Mikr.us application ports.
Cloudflare access logging and analytics that could retain URLs, IP addresses, or
medical search terms remain disabled. The site uses the reviewed direct
Plausible script for sanitized pageviews and aggregate engagement duration and
scroll depth, without cookies or custom properties; public URLs must never
contain medical queries.

## Secrets

Production images pin numeric service UIDs and GIDs in the release contract.
Ansible creates each secret directory as `root:<service-gid>` mode `0750` and
each file as `root:<service-gid>` mode `0440`; Compose adds only that group and
mounts only the required file read-only. The watcher reads its R2 key from the
S3-compatible credential file at `AWS_SHARED_CREDENTIALS_FILE` and the endpoint
from `MINOS_WATCHER_S3_ENDPOINT`. `cloudflared` receives its supported credentials
file and configuration, not an environment token. A preflight tests traversal
and reads using every exact image digest. Secret files include:

- the Cloudflare Tunnel credential;
- the bucket-scoped R2 access key;
- the Uptime Kuma watcher target document, installed for the watcher only.

The target document has the exact schema
`{"version":1,"sources":{"rsk":"<32-character-alphanumeric-token>"}}`.
During private bootstrap, `{"version":1,"sources":{}}` is valid only with
`watcher_uptime_kuma_required=false`. The source file supplied through
`watcher_uptime_kuma_targets_source` must be a regular mode-`0400` or
mode-`0600` file outside the repository. Ansible installs it as a root-owned,
watcher-secret-group file and Compose mounts it read-only only into the watcher.

NFZ discovery activation is opt-in with `watcher_nfz_activation_enabled=true`.
Before enabling it, create a separate NFZ push monitor in the private Uptime
Kuma instance and add its token alongside the existing BAW target in the
host-managed target document. The deployment preflight requires both `nfz` and
`baw` targets and `watcher_uptime_kuma_required=true`; it rejects activation
before changing watcher registration if either is absent. The existing BAW
source must already be enabled and scheduled. Without the opt-in, deployment
does not bootstrap NFZ or change the active source set.

The private GHCR package-read token is separate host-side deployment material.
It belongs in the root-owned Docker credential store and is never mounted into
an application container.

A dedicated Ed25519 operator signing key is separate from SSH login and all
runtime credentials. Git uses its SSH-signature namespace for signed tags;
operator tooling uses OpenSSH signatures with domain-separated
`minos-release`, `minos-deployment`, and `minos-recovery` namespaces. The public
verification key is committed and included in the recovery kit. The private key
stays in the password manager or supported hardware and never enters Mikr.us,
GitHub Actions, object-store credentials, or container storage.

Secrets never enter Git, `.env`, Compose YAML, container images, logs, or GitHub
Actions. Operator, SSH, registrar, and recovery credentials stay in the password
manager. Ordinary endpoints, bucket names, timeouts, and release digests are
configuration rather than secrets.

## Promotion Evidence

After repository immutable releases are enabled and verified, a signed release
manifest is stored as a private GitHub Release asset and as the conditional
single-object envelope `release-manifests/<tag>.json` in the operator R2 prefix. A
signed append-only receipt under
`deployment-receipts/<20-digit-sequence>.json` identifies its prior receipt
digest, release-manifest digest, operator, host, timestamp, pre-deploy recovery
image, rollback compatibility, and verification evidence. Each writer derives
the next sequence from the verified unique head, creates the sequence-only key
with `If-None-Match: *`, and accepts `412` only when the bytes match its
idempotent retry. It then relists and verifies exactly one fully linked head;
gaps or forks fail closed immediately. The watcher credential cannot access
either prefix. A successful record contains:

- source commit and signed tag;
- verified commit-to-digest attestations;
- API, edge, and watcher image digests;
- the `cloudflared` image digest;
- dataset and static-asset checksums;
- compatible release-manifest versions;
- prior and new complete digest sets;
- public readiness, known-search, static-integrity, mobile, and desktop results;
- representative post-deploy memory, disk, latency, and error evidence.

A quiet period alone is not deployment verification.
