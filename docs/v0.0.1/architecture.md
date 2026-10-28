# Architecture

## Context

Minos is a versioned data platform with a read-only serving layer. Upstream
publishers (NFZ, RSK) change rules 10-20 times a year across sources, can change
file sets or reuse URLs without useful content validators, and apply amendments
retroactively. The architecture therefore has two halves:

1. an ingestion loop (watchers, archive, reviewed data PRs, deterministic
   builds) that turns messy upstream publications into immutable, bitemporal
   ruleset releases;
2. a serving layer (CLI, HTTP, web) that answers questions against those
   releases deterministically and explainably.

Datasets are small enough to build ahead of deployment and ship with the
runtime. This removes a production database dependency. One generated database
is reused by the CLI and HTTP service; the API image, static web bundle, and CLI
distribution remain separate release artifacts built from one source commit.

Security, correctness, and measured performance are co-equal architectural
release requirements. Tradeoffs among them require representative evidence,
documented limits, and explicit review; developer convenience follows. The
architecture also optimizes for provenance, reproducibility, explainability,
low operating cost, and simple recovery. It does not optimize for mutable user
data or high write throughput. Determinism, provenance, and faster completion
of real settlement workflows are product features: the rule trace, audit
history, and measured response time differentiate Minos from incumbent tools.

## Component Boundaries

### minos.domain

Responsibilities:

- namespaced terminology, grouping-scheme, and release identifiers;
- concepts, descriptions, hierarchy relations, and source provenance types;
- group definitions, typed grouping conditions, and tariff value types;
- Unicode-aware Polish normalization;
- search request and result types;
- lexical rank and stable tie-break rules;
- explicit domain errors.

The package is synchronous and has no dependency on FastAPI, the CLI adapter,
the SQL driver, or frontend representation.

### minos.store

Responsibilities:

- database schema and migrations used by the artifact builder;
- transactional import and integrity checks;
- read-only concept, hierarchy, group, condition, and search queries;
- service-date resolution to a ruleset release;
- conversion between physical rows and domain types;
- dataset compatibility checks at startup.

The store supports a local database path. Remote database connectivity is not
required for lookup and must not become an implicit runtime dependency.

With vector search out of scope, the default engine candidate is plain SQLite
with FTS5 through the pinned Python runtime and database driver. A short spike
pins the Python and SQLite versions and verifies read-only operation,
deterministic Polish search, FTS5 availability, standalone CLI packaging, and
deterministic `linux/amd64` container operation before the physical schema is
accepted.

### minos.grouper (v0.0.2)

Responsibilities:

- evaluation of encounter facts against a pinned ruleset release;
- the versioned algorithm engine (grouping steps, procedure ranks, exclusion
  rules, condition evaluation);
- the complete rule trace: every consulted condition with its outcome;
- the near-miss report: conditions that almost changed the outcome.

Rules are data; the engine is versioned code, because the published algorithm
changes structure between versions. The package is synchronous, deterministic,
and side-effect free: same release plus same encounter equals same result and
same trace. Encounter values never leave the evaluation; nothing is persisted.
`minos.grouper.pipeline.group_request` is the single application service used
by the CLI and HTTP adapters; it returns domain values, and the shared v1 wire
schema lives in `minos.cli.grouping_output` (decision 2026-10-08).

### minos.cli

Responsibilities:

- source-manifest and artifact-build commands;
- artifact inspection, integrity reports, and release diffs;
- offline search, concept lookup, and JGP group display;
- stable exit codes;
- human-readable and versioned JSON output.

Release artifacts include checksummed standalone CLI bundles for supported
platforms and a separately licensed data bundle. The CLI accepts an explicit
data path, verifies the external artifact manifest before opening the database,
and reports the application and dataset versions independently.

### minos.http

Responsibilities:

- bounded HTTP request parsing and response serialization;
- health and readiness endpoints;
- mapping domain errors to stable HTTP errors;
- privacy-safe request telemetry;
- API cache and security headers.

It runs as an ordinary FastAPI/ASGI process behind the private Compose edge
network. It has no cloud-provider adapter or remote database dependency.

### Watchers

The dedicated watcher container runs on the Mikr.us Compose host and polls
upstream sources through APScheduler (see [data-sources.md](data-sources.md)).
The source collectors in issues #6-#8 emit immutable observations and bounded
change or incident records; issue #21 translates reviewed changes into the
checked-in source manifest consumed by `minos data build`. Observation/change
schemas and the build manifest are separate contracts. BAW uses anonymous JSON
and binary endpoints by default; browser automation requires a separate
evidence-backed task and is not a baseline dependency.

The watcher control SQLite database owns source configuration, UTC schedules and
durable due cursors, fenced run claims, source cursors, state revisions,
observations, artifact references, changes, incidents, and audit events. Exact
source bytes live in a private R2 bucket under SHA-256 keys. On startup the watcher reconciles
`committed` and `heartbeat_pending` runs before reconstructing in-memory jobs;
application-level due cursors coalesce downtime into one bounded catch-up run.
`heartbeat_pending` is retained as a compatibility name for durable local
finalization after recovery-image verification, not for a monitoring request.
Only after a run reaches a durable terminal state does the watcher make a
best-effort up or down push to self-hosted Uptime Kuma. Push failure never
blocks or changes watcher completion. GitHub Actions builds and tests the
watcher image but does not schedule production collection.

Operators inspect this state without a writer connection by running
`minos watcher status [SOURCE_ID] [--database PATH] [--json]` in the watcher
container. Bounded, privacy-safe lifecycle logs remain local under Docker's
`local` log driver.

### Uptime Kuma

The same Mikr.us Compose host runs self-hosted Uptime Kuma 2.5.8 from the
official MIT `slim-rootless` image (Compose selects `linux/amd64`) pinned to
`sha256:970f01d661de548b88c7e6012d3574666d033b1de6a323fc1bebfc3cb3b4c437`.
It runs as `10003:10003`, has no Docker socket or host port, joins a private
monitoring network shared with the watcher and `cloudflared`, and separately
joins an outbound network for HTTP monitors and notifications. Its only
persistent mount is `/var/lib/minos/uptime-kuma` at `/app/data`.

The conditional Cloudflare route for `status.minoshealth.pl` defaults to false
so the first-run setup cannot be taken over publicly. The public page exposes
only coarse service state. Detailed schedules, runs, changes, incidents, and
verified products stay in the private status command; issue #54 remains the
future private detailed admin portal. This removes paid external monitoring
SaaS, but Kuma shares the monitored host and therefore cannot detect or notify
for a total Mikr.us host, network, or power outage. This same-host limitation is
accepted for now.

## Target Repository Layout

```text
minos-health/
|-- apps/
|   `-- web/                       # responsive web client; later installable PWA
|-- src/minos/
|   |-- domain/
|   |-- store/
|   |-- ingest/
|   |-- grouper/                   # v0.0.2
|   |-- cli/
|   `-- http/
|-- database/
|   |-- reference/
|   |   `-- sqlite/
|   |       |-- migrations/        # raw numbered build-time SQL
|   |       `-- queries/
|   |-- control/
|   |   `-- sqlite/
|   |       `-- migrations/        # mutable watcher control plane
|   `-- application/               # future accounts, billing, and usage
|       `-- postgresql/migrations/
|-- tests/
|   |-- unit/
|   |-- integration/
|   |-- compatibility/
|   |-- fixtures/
|   `-- golden/
|-- manifests/
|   |-- sources/
|   `-- releases/
|-- infra/
|   `-- ansible/                     # Mikr.us host and Compose deployment
|-- scripts/
|-- docs/v0.0.1/
|-- .github/workflows/
|-- pyproject.toml
|-- uv.lock
|-- .dockerignore
|-- Dockerfile
`-- compose.yaml
```

Raw source data remains under ignored `/data`; generated databases and
manifests remain under ignored `/artifacts`. Only small reviewed fixtures are
committed.

`database/reference` is the language-neutral SQLite contract owned by
`minos.store`. It contains raw SQL, not an ORM model. The artifact builder
creates a fresh database, applies every numbered migration transactionally,
records migration checksums, imports data, and publishes the verified result.
The CLI, HTTP runtime, and future PWA open that artifact read-only and never run
reference migrations at startup.

`database/control` is the mutable watcher contract. It also uses raw numbered
SQLite migrations, but it is never bundled into the public API image or client
data. The watcher is its only writer. Consistent recovery images are created
with the SQLite backup API and uploaded to R2; the live database, WAL, and
shared-memory files are never copied as a backup set.

`database/control/sqlite/schema.sql` is the canonical readable definition of
the current complete schema. Runtime initialization still applies every
numbered `up` migration; tests require the migration chain and canonical DDL to
produce equivalent schemas. Every schema change updates both. Exact migration
bytes are checksum-locked after application. Down migrations are exercised only
in local and automated tests; production rollback restores the verified
pre-deploy recovery image. A retained older watcher may read an explicitly
compatible newer additive schema after validating every migration it knows.

The Python control adapter owns one dedicated raw-`sqlite3` writer connection.
It opens explicit `BEGIN IMMEDIATE` transactions in WAL mode with full
synchronous durability and SQLite defensive mode. Configuration and schedule
changes, fenced claims, expiry incidents, failures, observation commits, and
reconciliation transitions each allocate at most one monotonic state revision
and audit record. Configuration JSON is canonical and excludes secret-like
keys; cron expressions are parsed in UTC before persistence. Source advancement
accepts only structurally valid digest-derived receipts from the archive
boundary and commits observations, blob references, the source cursor, bounded
changes or incidents, and the `committed` run transition atomically. The archive
adapter owns native checksum and byte verification. A recovery receipt must
identify an immutable image of the current database revision before the control
adapter records `heartbeat_pending`.

The shared archive boundary stages at most one mode-`0600` file per configured
directory while incrementally computing its byte count and SHA-256. The local
adapter copies into an archive-owned mode-`0600` inode, atomically installs the
digest path without replacement, and re-verifies it for development.
The production adapter uses direct single-part R2 `PutObject` (S3 API) with conditional
creation and native SHA-256, then checksum-enabled `HeadObject`; it never uses a
transfer manager or multipart upload. Both return the same control-plane receipt
and reject existing bytes that do not match the requested digest and size.

The recovery boundary creates consistent SQLite copies only through
`sqlite3.Connection.backup()`. Recovery-only metadata is embedded in the copy,
not the live schema, preserving rollback compatibility with the retained watcher.
Images are integrity-checked, hashed, conditionally uploaded, ETag-pinned, and
selected through bounded pagination. Operator-trusted copies use a separately
signed canonical inventory chain and a restore path that acquires the same
database-derived process lock as the watcher.

One UTC in-memory APScheduler worker is only a wake-up mechanism. SQLite owns
durable due cursors, claims, retry policy, and startup catch-up. Startup first
reconciles committed work, then expires stale claims, reconstructs future jobs,
and executes at most one overdue cursor before normal scheduling begins.

`database/application` is a reserved future boundary, not part of `v0.0.1`.
If accounts and billing are introduced, its PostgreSQL migrations and backups
remain independent from replaceable reference artifacts and local browser
state. Empty future directories do not need to be committed before their
phase starts.

## Logical Data Model

### Source Release

One upstream artifact: source identifier, source version, upstream file
identity and timestamps, fetch date, checksum computed at archive time,
transformation version, license, and notices. The database file's SHA-256 is
recorded only in the external artifact manifest to avoid a circular
self-checksum.

### Ruleset Release

The composite that governs a reporting period: zarządzenie and załącznik
versions, algorithm version, ICD-9 version, and ICD-10 version, paired as
announced under § 10 of the base act. Grouping and JGP queries resolve a
service date to exactly one ruleset release; no release covering the date is
an explicit error, never a silent fallback to latest.

### Bitemporality

Rule-bearing rows carry `valid_from`/`valid_to` (reporting periods, valid
time) and ingestion identity (knowledge time). Publication and effective dates
are stored separately because amendments apply retroactively. Two queries must
always be answerable: "which rules govern service date X" and "what did we
believe about X on date Y".

### Concept

A concept is identified by `(source_release, code)`, not by code alone. It
retains source status and source ordering when available.

### Description

Descriptions retain exact source text and type. Search normalization is stored
separately. Alternate source labels must not be invented from normalization.

### Relation

Hierarchy is represented explicitly as typed relations. Parent codes must
exist in the same release unless the source defines and documents a
cross-release reference.

### Group Definition And Conditions

A JGP group carries its code, name, product code, and point values per
settlement mode with validity periods. Grouping conditions are typed rows:
directional diagnosis lists, directional procedure lists, procedure-count
requirements, procedure ranks, age brackets, length-of-stay thresholds, and
discharge modes. Conditions preserve source rule identity so the trace can
cite them. Facts extracted from legal prose (correction coefficients) are
curated overlay rows with `manual` provenance and mandatory review.

## Ingestion Pipeline

1. Source collectors detect and archive upstream changes, then emit an
   immutable change record. Issue #21 selects verified changes and proposes a
   pinned manifest with a
   human-readable diff (added, removed, changed, reparented concepts; group and
   point changes).
2. Reviewed policy gates classify routine data-only candidates. Under the
   2026-10-05 owner decision, passing routine candidates activate automatically;
   failed or significant candidates retain the last good release and send an
   actionable Telegram alert. Parser, schema and policy changes require review.
3. CI builds the database transactionally and deterministically from the
   pinned manifest, runs integrity checks, and emits the artifact manifest,
   checksum, and release diff. Promotion uses the exact tested compatible set,
   with post-activation verification and complete-set rollback on failure.

Parser hardening applies to every format: XML (no DTDs or external entities;
bounded depth, sizes, and counts), XLSX and DOCX (zip entry-count,
expansion-ratio, and size limits; merged-cell-aware header handling), and
explicit rejection of unknown structures that affect meaning.

## Search Pipeline

The request specifies a terminology system and bounded query. The store
executes lexical stages in order:

1. Exact source code.
2. Exact normalized description.
3. Indexed prefix and token candidates.
4. Bounded substring fallback only when prior stages are empty.

The domain assigns stable rank categories. Ties use source order and then
code. SQL query planner order is never treated as user-visible ordering.

## Grouping Pipeline (v0.0.2)

1. Validate the encounter payload (bounded sizes, known codes, explicit
   service date).
2. Resolve the ruleset release for the service date.
3. Run the versioned engine: qualification, exclusions, procedure ranking,
   condition evaluation, group selection, tariff determination.
4. Return group, product code, point values, the full trace, and near-misses,
   labelled with the exact ruleset release and algorithm version.

The published NFZ algorithm description is descriptive; the binding result is
produced by NFZ claim verification. A golden-case regression suite
cross-checked against existing groupers and reviewed real cases guards
correctness, and divergences are investigated as defects.

## HTTP Contract

All API responses include an API version and ruleset/dataset release identity.
Search requests cap query length and result count. Unsupported systems fail
explicitly. Read endpoints take an explicit service-date parameter defaulting
to the current date.

`/healthz` confirms only that the process loop is responsive. `/readyz` opens
the configured database, validates its schema and release metadata, and
performs a bounded sentinel query. The deployment smoke test performs a known
public search in addition to readiness.

`POST /api/v1/group` (`v0.0.2`) accepts a bounded JSON encounter, is
stateless, and is excluded from any body logging. POST is mandatory because
encounter facts must never appear in URLs, caches, or access logs.

The static interface and API share an origin. Hashed static assets receive
long cache lifetimes from the edge container. The edge routes `/api/*`,
`/healthz`, and `/readyz` to the API container and disables caching for those
paths. It does not emit access logs containing complete URLs, client addresses,
or query values. Cloudflare Tunnel is the only public ingress. Mobile and
desktop browsers receive the same responsive static artifact.

## Runtime

The runtime image contains:

- the Minos Python environment and ASGI entry point;
- the generated read-only database;
- dataset and build provenance.

The separate edge image contains only an unprivileged static server, reviewed
HTML, CSS, JavaScript, icons, and build metadata. Docker Compose serves the same
API and edge digests locally and in production.

The standalone CLI bundle and data bundle are promoted alongside the image but
are not installed in the HTTP runtime image unless a measured operational need
emerges.

The process listens on a configurable unprivileged port, runs as non-root,
uses a read-only filesystem, writes only to an explicitly allowed temporary
path, and handles termination cleanly. No runtime migration or upstream data
download is allowed.

The watcher is a separate immutable image. Its read-only root filesystem has one
persistent volume owned by its pinned numeric identity for control SQLite and
recovery-image staging. Downloads use a separate disk-backed staging directory
with one temporary file, a `600 MiB` application quota, and a `512 MiB` per-file
ceiling. Raw bytes are uploaded and checksum-verified in R2 before the related
observation and source cursor are committed. The edge, API, and watcher images
are promoted as one compatible digest set.

## Future PWA And Commercial Boundary

The web client can evolve in phases without a desktop application:

1. `v0.0.1` serves an anonymous responsive web interface from the edge container
   and sends bounded API requests to the API container under the same public
   Cloudflare Tunnel hostname.
2. A later installable PWA stores a versioned reference SQLite snapshot through
   SQLite WASM and supported browser storage. On startup or restored
   connectivity it checks a release manifest, downloads a complete compatible
   snapshot, verifies size, checksum, schema metadata, integrity, and sentinel
   queries, then switches the active release. Failure leaves the prior release
   active.
3. Online commercial calculations add central identity, entitlement, payment,
   and idempotent usage services backed by a separate application database.
4. Premium hospital offline calculation uses an expiring signed organization
   entitlement. Browser controls are not treated as strong per-run enforcement;
   the commercial control is organization licensing plus periodic renewal or
   manual signed-license import for an air-gapped installation. This phase also
   requires a browser-executable grouper, through Python/WASM or a separately
   validated client implementation, that passes the same golden cases and
   produces the same trace as the canonical engine.

Application code and reference data have independent versions and an explicit
schema compatibility range. Full client snapshots are the default because a
returning browser can skip any number of releases. Delta updates require
measured evidence that full compressed snapshots are too large.

The PWA only disables online paid actions as user feedback. Every online
calculation is authorized and metered by the server. Offline reference use
continues against the last verified local dataset, with its release and last
successful update check visible to the user.

## Concurrency And Limits

The HTTP process bounds request body size, query length, result count,
database work, request duration, worker count, and concurrent requests. Compose
resource limits and the edge request policy provide the initial host and
overload guards. These are single-host limits, not a distributed rate limit.
Cloudflare controls may be introduced only when observed abuse justifies their
privacy and operational cost.

## Failure Behaviour

Startup fails if the dataset is missing, incompatible, corrupt, or not the
configured release. It never starts in a partially functional empty-data mode.

Malformed upstream records fail the artifact build visibly. The importer does
not skip semantically relevant fields to make a build pass. Intentional source
exceptions require fixture coverage and a recorded transformation rule.

## Security And Privacy

The public API is read-only apart from the stateless grouping endpoint. It
accepts no file uploads or rich markup. Responses escape source text in HTML
contexts. Production errors hide internal paths and stack traces.

Logs exclude raw query values, encounter payloads, headers, cookies, and
client IP. Changing this boundary requires a separate privacy design and
accepted decision. Dependencies and the final container are scanned in CI, but
scanning does not replace runtime smoke tests.

## Deferred Architecture

The following require a new design decision rather than an incremental
shortcut:

- semantic or embedding-based search;
- implementation details for user accounts, payments, plans, quotas, and
  stored favourites;
- stored encounters or grouping history;
- mutable production terminology edits;
- query-history analytics;
- commercial datasets;
- clinical decision support;
- a distributed database;
- Kubernetes or Helm;
- public write APIs beyond stateless grouping.
