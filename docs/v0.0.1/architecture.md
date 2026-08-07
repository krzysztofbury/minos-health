# Architecture

## Context

Minos is a versioned data platform with a read-only serving layer. Upstream
publishers (NFZ, RSK) change rules 10-20 times a year across sources, replace
files in place, and apply amendments retroactively. The architecture therefore
has two halves:

1. an ingestion loop (watchers, archive, reviewed data PRs, deterministic
   builds) that turns messy upstream publications into immutable, bitemporal
   ruleset releases;
2. a serving layer (CLI, HTTP, web) that answers questions against those
   releases deterministically and explainably.

Datasets are small enough to build ahead of deployment and ship with the
runtime. This removes a production database dependency. One generated database
is reused by the CLI and HTTP service; the HTTP image and CLI distribution
remain separate release artifacts.

The architecture optimizes for correctness, provenance, reproducibility,
explainability, low operating cost, and simple recovery. It does not optimize
for mutable user data or high write throughput. Determinism and provenance are
product features here: the rule trace and the audit history are what
differentiate Minos from incumbent tools.

## Component Boundaries

### minos-domain

Responsibilities:

- namespaced terminology, grouping-scheme, and release identifiers;
- concepts, descriptions, hierarchy relations, and source provenance types;
- group definitions, typed grouping conditions, and tariff value types;
- Unicode-aware Polish normalization;
- search request and result types;
- lexical rank and stable tie-break rules;
- explicit domain errors.

The crate is synchronous and has no dependency on an executor, web framework,
CLI framework, SQL driver, or frontend representation.

### minos-store

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
through a pinned Rust crate with FTS5; libSQL remains an option. A short spike
pins the engine, crate, version, and features and verifies read-only
operation, deterministic Polish search, FTS, and Lambda compatibility before
the physical schema is accepted.

### minos-grouper (planned, v0.0.2)

Responsibilities:

- evaluation of encounter facts against a pinned ruleset release;
- the versioned algorithm engine (grouping steps, procedure ranks, exclusion
  rules, condition evaluation);
- the complete rule trace: every consulted condition with its outcome;
- the near-miss report: conditions that almost changed the outcome.

Rules are data; the engine is versioned code, because the published algorithm
changes structure between versions. The crate is synchronous, deterministic,
and side-effect free: same release plus same encounter equals same result and
same trace. Encounter values never leave the evaluation; nothing is persisted.

### minos-cli

Responsibilities:

- source-manifest and artifact-build commands;
- artifact inspection, integrity reports, and release diffs;
- offline search, concept lookup, and JGP group display;
- stable exit codes;
- human-readable and versioned JSON output.

Release artifacts include checksummed CLI binaries for supported platforms and
a separately licensed data bundle. The CLI accepts an explicit data path,
verifies the external artifact manifest before opening the database, and
reports the application and dataset versions independently.

### minos-http

Responsibilities:

- bounded HTTP request parsing and response serialization;
- health and readiness endpoints;
- mapping domain errors to stable HTTP errors;
- privacy-safe request telemetry;
- embedded static frontend assets;
- cache and security headers.

It runs as an ordinary HTTP process. Lambda Web Adapter translates Lambda
events to HTTP without adding Lambda-specific code to the application.

### Watchers (CI, not runtime)

Scheduled GitHub Actions jobs poll upstream sources, archive raw files, and
open data PRs (see [data-sources.md](data-sources.md)). They share the
manifest format with `minos data build` but are otherwise scripts plus a
headless-browser step for BAW; they hold no deployment permissions and are not
part of the runtime image.

## Target Repository Layout

```text
minos-health/
|-- crates/
|   |-- minos-domain/
|   |-- minos-store/
|   |-- minos-grouper/        # v0.0.2
|   |-- minos-cli/
|   `-- minos-http/
|-- fixtures/
|-- watchers/
|-- docs/v0.0.1/
|-- infra/
|   |-- bootstrap/
|   `-- aws/
|-- web/
|-- .github/workflows/
|-- Cargo.toml
|-- Dockerfile
`-- compose.yaml
```

Raw source data remains under ignored `/data`; generated databases and
manifests remain under ignored `/artifacts`. Only small reviewed fixtures are
committed.

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

1. Watchers detect and archive upstream changes, then open a manifest PR with
   a human-readable diff (added, removed, changed, reparented concepts; group
   and point changes).
2. A human reviews and merges. Large or unexplained diffs block.
3. CI builds the database transactionally and deterministically from the
   pinned manifest, runs integrity checks, and emits the artifact manifest,
   checksum, and release diff.

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
long cache lifetimes. CloudFront caching is disabled for `/api/*`, `/healthz`,
and `/readyz`; the API origin request policy forwards only documented query
parameters such as `system`, `q`, `date`, and `limit`, with no cookies or
viewer authorization header. CloudFront access logging remains disabled until
a privacy review addresses medical terms in URLs.

## Runtime

The runtime image contains:

- one `minos-http` binary;
- embedded or packaged static assets;
- the generated read-only database;
- dataset and build provenance;
- Lambda Web Adapter files needed by the Lambda profile.

The CLI binary and data bundle are promoted alongside the image but are not
installed in the HTTP runtime image unless a measured operational need
emerges.

The process listens on a configurable unprivileged port, runs as non-root,
uses a read-only filesystem, writes only to an explicitly allowed temporary
path, and handles termination cleanly. No runtime migration or upstream data
download is allowed.

## Concurrency And Limits

The HTTP process bounds request body size, query length, result count,
database work, and request duration. Lambda reserved concurrency is the
initial global cost and load guard. Per-process limiters may protect a warm
instance but are not treated as a distributed rate limit.

AWS WAF is not mandatory for the low-volume first release because it adds
fixed cost and complexity. It can be introduced if observed abuse justifies
it.

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
- user accounts or stored favourites;
- stored encounters or grouping history;
- mutable production terminology edits;
- query-history analytics;
- commercial datasets;
- clinical decision support;
- a distributed database;
- Kubernetes or Helm;
- public write APIs beyond stateless grouping.
