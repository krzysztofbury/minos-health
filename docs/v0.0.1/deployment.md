# Deployment

Status: target architecture. The current repository has the CI verification
baseline and Terraform state bootstrap, but not the API image, watcher entry
point, archive adapter, Compose stack, scheduler, or release workflow.

## Goals

The delivery system must:

- run the complete Minos production stack on one dedicated Mikr.us `3.0` host;
- build one immutable API image containing the generated reference database;
- build one immutable edge image containing the static web artifact;
- build one immutable watcher image with the scheduler and source adapters;
- build checksummed standalone CLI and data-bundle artifacts;
- promote already tested digests rather than rebuild for release;
- keep CI separate from the production host and its secrets;
- make the host replaceable from Git, GHCR digests, S3 recovery images, and a
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
     -> /api/*, /healthz, /readyz -> API container

Watcher container
  -> APScheduler
  -> mutable control SQLite on a persistent volume
  -> private versioned S3 snapshot bucket

GitHub-hosted Actions
  -> test, build, scan, attest, publish to private GHCR
  -> no production runtime or Docker socket access
```

The host exposes no application port to the public internet. `cloudflared`
opens an outbound tunnel to the edge container. The edge serves static files
and proxies API routes under the same origin. The API and watcher networks are
private Compose networks; only the edge is reachable through the tunnel.

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
  database mounted from a dedicated persistent volume.

The generated reference database and watcher control database are deliberately
different systems:

- the reference database is immutable, release-scoped, and opened read-only by
  the API and CLI;
- the control database is mutable operational state for source configuration,
  schedules, runs, observations, artifact references, changes, and incidents.

The watcher is not included in the API image. The edge image contains no Python
runtime, source snapshots, credentials, or generated medical database.

## Artifact Lifecycle

Private GHCR is the canonical image registry. Tags aid discovery, but digests
are deployment identities:

```text
ghcr.io/krzysztofbury/minos-health-api@sha256:<digest>
ghcr.io/krzysztofbury/minos-health-web@sha256:<digest>
ghcr.io/krzysztofbury/minos-health-watcher@sha256:<digest>
cloudflare/cloudflared@sha256:<digest>
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
   manifest and signature to an operator-only S3 release prefix. The signed S3
   copy remains the recovery authority and binds the tag, source commit,
   artifact digests, compatibility, and manifest digest.
7. The operator quiesces watcher dispatch, reconciles any `committed` or
   `heartbeat_pending` run, creates and verifies a pre-deploy recovery image,
   and records the current and candidate release manifests.
8. A scheduler-paused candidate watcher validates a disposable database copy,
   migration compatibility, numeric identity, volume and secret access, an S3
   checksum round trip, recovery-image creation, and Healthchecks.io access.
9. The operator stops the previous watcher, starts the exact candidate watcher
   digest with dispatch still paused against the live volume, applies the tested
   migration, and verifies its live state revision, migration checksums, WAL,
   mounted secrets, and post-migration recovery image. The previous and candidate
   watcher never own the volume concurrently.
10. The operator starts the candidate edge and API, runs readiness, known-search,
    static-integrity, mobile, and desktop checks through the public hostname, and
    only then activates the live candidate watcher for one bounded canary run.
11. Success writes a signed append-only deployment receipt to an operator-only
    S3 prefix. Before the canary commits, failure restores the complete previous
    release and may restore the pre-deploy recovery image. After any candidate
    run commits, rollback must preserve that state and use the prior watcher
    against the backward-readable schema; an older database image must not be
    restored.

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
BasedPyright, mypy, pytest, Bandit, Gitleaks, dependency policy, Terraform, and
the built wheel. Planned checks add fixture ingestion, deterministic artifact
builds, container scans, Compose integration, and real browser smoke tests.

The `main` workflow may use short-lived AWS OIDC credentials when it needs
read-only access to reviewed source snapshots for a full artifact build. It
publishes only to private GHCR and GitHub release storage. It does not connect
to the production host during ordinary builds.

The issue #21 PR handoff is a separate operator-dispatched workflow from the
protected default branch. It receives one allowlisted recovery-image identity
and bounded change-record identity, uses the read-only OIDC role, verifies both,
and exports only the selected normalized proposal. Pull-request jobs receive no
archive role, and the production watcher receives no GitHub credential.

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

Only the watcher receives persistent writable storage. Ansible initializes its
persistent control directory as the pinned watcher UID and GID with mode `0700`;
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
writes. Exact raw bytes are uploaded to S3 and are not retained as the only copy
on the host. Logs use Docker's bounded `local` driver rather than an unbounded
JSON log file.

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
and control-database limits remain below the S3 single-part limit. It then uses
`HeadObject` with checksum mode enabled against that same requested key. Both a
successful create and `412 Precondition Failed` must return the expected byte
count and native checksum; mismatch is a hard incident. `409 Conflict` receives
a bounded retry of the conditional operation. The watcher then commits the
related observation, blob reference, and source cursor in one local SQLite
transaction. A post-upload crash can leave an unreferenced immutable object; a
retry reuses it only after the same verification.

Every restorable control-database transaction increments a monotonic
`state_revision`; a transaction that advances source state also leaves its run
in `committed`. No later claim for that source may start while a committed run
awaits reconciliation. The reconciler uses the SQLite backup API to create an
integrity-checked recovery image for that exact revision, uploads and verifies
it under an immutable revision-and-digest key, then records `heartbeat_pending`.
It sends the success heartbeat only from that state and marks the run
`successful` after a successful response. Every transition is idempotent. On
restart or restore, reconciliation resumes upload, verification, or heartbeat;
a duplicate heartbeat is acceptable, but a newer run never overtakes it.

The legal run transitions are `running -> committed -> heartbeat_pending ->
successful`, `running -> failed`, and `running -> expired`. Acquisition creates
`running`; only the matching live fenced claim can leave it. `committed` never
expires, terminal states never reopen, and no transition may be skipped.

There is no atomic transaction across local SQLite and S3. A complete host loss
after local commit but before recovery-image upload can lose that unacknowledged
observation relationship even when its raw blob survived. This narrow window is
an accepted residual risk; the run was never reported successful and must be
retried or investigated.

## AWS Scope

AWS is not an application runtime. It owns only:

- the existing private versioned Terraform state bucket;
- one private versioned S3 snapshot bucket for content-addressed raw bytes,
  immutable control-database recovery images, signed release manifests, and
  deployment receipts under separately authorized prefixes;
- narrowly scoped IAM identities for the Mikr.us watcher and GitHub OIDC reads;
- project budget notifications.

The snapshot bucket uses SSE-S3, public-access blocks, versioning, and enforced
conditional writes under immutable prefixes. The watcher can write only raw
blobs, recovery images, and checksum probes. Probe objects use a dedicated
`probes/<UTC-date>/<random>/<sha256>` prefix, become current-expiration eligible
after seven days, and have noncurrent versions removed after one additional day.
Release manifests, trusted recovery copies and inventories, and deployment
receipts require the operator identity and an independent signing key kept
off-host. Raw objects have no automatic expiry. Multipart upload is prohibited
for watcher writes; a seven-day incomplete-upload abort remains defense in depth
for operator tooling.

The Mikr.us watcher uses one narrowly scoped IAM access key because AWS workload
identity is unavailable on that host. Terraform creates the identity and policy
but never creates or stores the access-key secret. The operator creates and
rotates the key manually, stores the recovery copy in the password manager, and
installs it as a service-restricted host file mounted read-only into the watcher.
The identity cannot delete objects, alter bucket policy, read Terraform state,
or access unrelated AWS resources.

There is no ECR, Lambda, Function URL, CloudFront, frontend S3 bucket, ACM
certificate, or CloudWatch application-log stack in the accepted architecture.

## Terraform State

`infra/bootstrap` owns the existing dedicated S3 state bucket through its
separate one-time bootstrap state. The future `phase0` root initializes against
its own remote-state key and owns the snapshot bucket, IAM policy, GitHub OIDC
read role, budget notification, and Mikr.us watcher identity.

The state bucket and snapshot bucket remain separate. Production state is never
stored in a GitHub artifact or committed to Git. Any apply requires review of
the exact plan, including public exposure, IAM scope, replacement, and retention
changes.

## Cloudflare And Domains

After legal clearance and registration:

- `minoshealth.pl` is canonical and routes to Cloudflare Tunnel;
- `minoshealth.com` redirects permanently to `.pl` while preserving safe paths;
- `minos.health`, if purchased, redirects the same way;
- Cloudflare manages DNS and tunnel routing;
- DNSSEC is enabled after the registrar and delegation chain are verified;
- domain purchase, account recovery, and renewal remain manual.

The tunnel terminates public ingress without exposing Mikr.us application ports.
Cloudflare logging and analytics that could retain URLs, IP addresses, or
medical search terms remain disabled until a separate privacy review.

## Secrets

Production images pin numeric service UIDs and GIDs in the release contract.
Ansible creates each secret directory as `root:<service-gid>` mode `0750` and
each file as `root:<service-gid>` mode `0440`; Compose adds only that group and
mounts only the required file read-only. The watcher reads AWS credentials from
`AWS_SHARED_CREDENTIALS_FILE` and the Healthchecks.io target through a dedicated
`_FILE` setting. `cloudflared` receives its supported credentials file and
configuration, not an environment token. A preflight tests traversal and reads
using every exact image digest. Secret files include:

- the Cloudflare Tunnel credential;
- the narrowly scoped S3 access key;
- the Healthchecks.io ping target used by the watcher.

The private GHCR package-read token is separate host-side deployment material.
It belongs in the root-owned Docker credential store and is never mounted into
an application container.

A dedicated Ed25519 operator signing key is separate from SSH login and all
runtime credentials. Git uses its SSH-signature namespace for signed tags;
operator tooling uses OpenSSH signatures with domain-separated
`minos-release`, `minos-deployment`, and `minos-recovery` namespaces. The public
verification key is committed and included in the recovery kit. The private key
stays in the password manager or supported hardware and never enters Mikr.us,
GitHub Actions, AWS credentials, Terraform state, or container storage.

Secrets never enter Git, `.env`, Compose YAML, Terraform variables or state,
container images, logs, or GitHub Actions. Operator, SSH, registrar, and recovery
credentials stay in the password manager. Ordinary endpoints, regions, bucket
names, timeouts, and release digests are configuration rather than secrets.

## Promotion Evidence

After repository immutable releases are enabled and verified, a signed release
manifest is stored as a private GitHub Release asset and as the conditional
single-object envelope `release-manifests/<tag>.json` in operator-only S3. A
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
