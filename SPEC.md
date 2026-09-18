# Minos Specification

Status: accepted implementation baseline, 2026-08-06; Python architecture and
phased PWA/commercial direction accepted 2026-08-21; security, performance,
testing, and web-hosting direction accepted 2026-09-17; dedicated Mikr.us
hosting and SQLite watcher control plane accepted 2026-09-17

## Goal

Minos provides fast, reproducible, explainable access to the data that
determines Polish hospital settlement: medical classifications (`icd10-pl`,
`icd9-pl`) and the NFZ JGP grouping system (group definitions, grouping
conditions, point catalogs, and the grouper algorithm).

The product serves the people who code and settle hospitalizations: coders,
physicians, settlement departments, and consultants. Existing tools are either
HIS-embedded, rate-limited freemium groupers without APIs, or rules
encyclopedias whose optimization answers are sold as manual consulting. Minos
competes on freshness, explainability, API access, and interface quality.

The product reports source data faithfully and computes deterministic results
with visible reasoning. It does not provide medical advice, patient-specific
clinical interpretation, diagnosis, treatment, or clinical ranking. It
supports complete and correct coding of documented care; it must not suggest
coding beyond documentation.

Two interfaces share the same domain behaviour:

1. An offline-capable Python CLI packaged for supported desktop platforms.
2. An anonymous web application and read-only HTTP API.

## Scope

### Phase 0 (before product code)

- Daily watchers over all upstream sources (see
  [data sources](docs/v0.0.1/data-sources.md)).
- Immutable raw snapshot archive in private versioned S3. Publishers can change
  file sets or reuse URLs without useful content validators; an uncaptured
  predecessor may be unrecoverable.
- Automated data-update pull requests carrying manifest changes and
  human-readable diffs. Automation proposes; a human reviews and merges.

### Included In v0.0.1

- Terminology systems `icd10-pl` (RSK HCD `00CD10`) and `icd9-pl` (NFZ
  dictionary files).
- JGP knowledge base for hospital care (`jgp-szp`): groups, grouping
  conditions, ICD-10 and ICD-9 directional lists, age and length-of-stay
  conditions, point values, and product codes, ingested from zarządzenia
  Prezesa NFZ and komunikaty JGP.
- Bitemporal versioning: every fact carries the reporting period it governs
  (valid time) and the ingestion identity (knowledge time). Retroactive
  amendments are the observed norm, not an edge case.
- Version-to-version diffs as first-class data: what a new zarządzenie changed
  for a given group, list, or point value.
- Source release metadata and transformation provenance.
- Deterministic code and normalized-text search.
- Human-readable and JSON CLI output.
- A versioned read-only HTTP API and responsive anonymous webpage.
- One immutable API container and one immutable static web artifact, deployed
  together through Docker Compose locally and on the dedicated Mikr.us host.

### Planned For v0.0.2 (design baseline now, implementation later)

- `minos.grouper`: deterministic encounter evaluation producing the JGP group,
  point value, complete rule trace, and near-miss report (which conditions
  almost changed the outcome). Grouping requests use POST and are stateless:
  never stored, never logged.
- Correctness validation against existing groupers and reviewed real-world
  cases before any public claim of settlement accuracy.

### Explicitly Excluded

- Coding suggestions beyond documented care (upcoding).
- Contractual PLN amounts, payer contract terms, ryczałt PSZ computation.
- Drug data, prices, reimbursement, stock, dosage, or treatment guidance.
- Semantic or embedding-based search (deferred; requires its own safety and
  relevance gates before reconsideration).
- Accounts, personalization, favourites, or mutable user and product data. The
  watcher control database is operational state and is explicitly allowed.
- WHO ICD-10, WHO ICD-9, ICD-9-CM-US variants until separate source and
  redistribution reviews are complete.
- A public Python SDK, mobile applications, and write APIs.

These exclusions describe `v0.0.1`, not permanent product constraints.

### Accepted Future Direction

- One responsive installable PWA is the client direction; no separate desktop
  application is planned.
- Offline reference use receives complete versioned SQLite snapshots through
  one-way synchronization when connectivity returns. The active local snapshot
  is replaced only after compatibility and integrity checks.
- Individual calculations remain online when commercial controls arrive: a
  small free allowance and paid plans are authorized and metered centrally.
- Premium offline calculation is an organization or hospital licence using a
  signed expiring entitlement, not browser-side per-run billing.
- Premium offline calculation also requires a browser-executable grouper that
  passes the canonical engine's golden cases and trace assertions; its language
  and packaging are deferred until measured browser requirements exist.
- Accounts, subscriptions, entitlements, usage, and payment-provider identifiers
  use a separate future application database. Card data and encounter payloads
  are not stored there.
- Commercial offline artifacts contain only data cleared for paid
  redistribution.

Exact pricing, quotas, payment and identity providers, and offline licence
terms remain out of scope until that phase is designed.

Future classification and scheme variants must remain explicitly namespaced,
including `icd10-who`, `icd10-pl`, `icd9-pl`, `icd9-cm-us`, `jgp-szp`,
`jgp-aos`, and `jgp-reh`. No source may be presented under a generic identity
when its country, modification, or release affects meaning.

## Architecture

### Domain

`minos.domain` is synchronous and framework-independent. It owns terminology
identifiers, concept and relation types, normalization rules, search result
types, deterministic ordering, grouping-scheme and ruleset identities, and
explicit errors. It does not own HTTP, database connections, command-line
parsing, or serialization policy.

### Store

`minos.store` owns the generated database schema, read-only queries, and
ingestion transactions. Lookup must work against a local database without
network access.

With vector search removed from scope, the default engine candidate is plain
SQLite with FTS5 through Python's pinned runtime and database driver. A short
implementation spike pins the Python and SQLite versions and verifies local
read-only operation, deterministic Polish search, FTS5 availability, packaged
CLI operation, deterministic container operation, and compatibility with the
pinned `linux/amd64` runtime before the physical schema is accepted. Portable
normalized B-tree search remains the required fallback.

### Grouper (v0.0.2)

`minos.grouper` evaluates encounter facts (principal and secondary diagnoses,
procedures with dates, age, admission and discharge modes and dates, length of
stay, department) against a pinned ruleset release. Rules are data; the engine
is versioned code, because the published algorithm changes structure between
versions. Given the same ruleset release and encounter, the result is
identical, and the trace lists every rule consulted with its outcome.

### CLI

`minos.cli` produces and inspects datasets and performs offline searches. It
owns argument parsing, output formatting, stable exit codes, and JSON schemas.
A representative command shape is:

```text
minos search --system icd10-pl --query "E11" --data artifacts/minos.db
minos data build --manifest dataset-manifest.toml --output artifacts/minos.db
```

The exact command grammar may change during implementation, but scripts must
be able to select a terminology system, a grouping scheme, and a pinned
dataset explicitly.

### HTTP And Web

`minos.http` is a thin FastAPI adapter over the synchronous domain and store.
Frontend source lives under `apps/web` and builds into a separate immutable
static artifact. In production an unprivileged edge container serves that
artifact and proxies `/api/*`, `/healthz`, and `/readyz` to the API container.
Cloudflare Tunnel is the only public ingress to the edge. Docker Compose serves
the exact same frontend artifact and API image locally.
Static HTML, CSS, and JavaScript avoid requiring a frontend build toolchain in
`v0.0.1`. This topology works on mobile because application code executes in
the browser and makes bounded API requests; no persistent frontend server is
required. The `v0.0.2` encounter builder, trace views, and later offline PWA may
outgrow this; introducing a frontend toolchain then is an accepted future
decision, not scope creep now.

The initial endpoint set is:

- `GET /healthz`: process liveness, with no data access.
- `GET /readyz`: confirms the configured dataset can be opened and queried.
- `GET /api/v1/search?system=icd10-pl&q=...`: bounded search.
- `GET /api/v1/concepts/{system}/{code}`: one concept and its hierarchy.
- `GET /api/v1/jgp/groups/{code}`: one group with conditions and point values
  for a given service date.
- `POST /api/v1/group` (`v0.0.2`): stateless encounter grouping. POST because
  encounter payloads must never appear in URLs or logs.

Read endpoints remain GET-based for standard read semantics and cacheability.
Input length, result count, and execution time must be bounded.
Queries answering "as of which service date" take an explicit date parameter
and default to the current date.

## Data Model

The physical schema may evolve, but it must represent these concepts:

- `source_release`: one upstream artifact: source identifier, source version,
  upstream timestamps, fetch date, source checksum, transformation version,
  license, and notices;
- `ruleset_release`: a composite binding for a reporting period: the
  zarządzenie and załącznik versions, algorithm version, ICD-9 version, and
  ICD-10 version that jointly govern grouping for that period (per § 10 of the
  base act these are announced and paired explicitly);
- `terminology_system`: stable namespace such as `icd10-pl`;
- `grouping_scheme`: stable namespace such as `jgp-szp`;
- `concept`: source code, status, source order, and release identity;
- `concept_description`: preferred and alternate source descriptions plus
  normalized search values;
- `concept_relation`: parent/child relations with explicit relation type;
- `group_definition`: JGP group identity, product code, and point values by
  settlement mode;
- `group_condition`: typed grouping conditions (directional diagnosis and
  procedure lists, procedure counts and ranks, age brackets, length-of-stay
  thresholds, discharge modes) preserving source rule identity;
- `tariff_value`: point values with explicit validity periods;
- bitemporal fields on all rule-bearing rows: `valid_from`/`valid_to`
  (reporting periods) and ingestion identity (knowledge time).

Source values must be preserved. Normalized values are additional search keys,
not replacements for authoritative text. Facts extracted from legal prose
rather than structured files (for example correction coefficients) are curated
overlay data with `manual` provenance and mandatory review. The database does
not contain its own file checksum; that checksum belongs to the external
artifact manifest.

## Ingestion

The build process must:

1. Resolve explicitly pinned upstream sources from the reviewed manifest
   (RSK file UUIDs; NFZ zarządzenie, komunikat, and dictionary files by URL
   and checksum).
2. Stream source files rather than loading them fully into memory where the
   format allows it.
3. Verify downloaded bytes against recorded SHA-256 checksums. Upstream
   provides no checksums; ours are computed at archive time and pinned in the
   manifest.
4. Harden every parser: reject XML DTDs and external entities; bound depth,
   element and text size, record count, and total bytes; treat XLSX and DOCX
   as zip archives with entry-count, expansion-ratio, and size limits.
5. Reject unsupported structures, missing required fields, duplicate
   identities, broken hierarchy or list references, and encoding errors.
6. Build the database transactionally and deterministically.
7. Record source and transformation provenance inside the database.
8. Produce a manifest and database checksum alongside the artifact.
9. Emit a human-readable diff against the previous release for review.

CI must never silently replace a pinned source with the latest upstream file.
The source collectors in issues #6-#8 detect changes, archive raw files, and
emit change records. The automation in issue #21 turns reviewed change records
into manifest pull requests; refreshing data is always a reviewed change.

## Search Behaviour

Lexical results are authoritative source matches and use this precedence:

1. Exact code match.
2. Exact normalized preferred or alternate description.
3. Indexed prefix and token match.
4. A bounded normalized substring fallback when the indexed stages are empty.

Ties use stable source order and then code order. The same database, query,
and configuration must always produce the same ordering.

## Grouping Behaviour (v0.0.2 baseline)

- Input: encounter facts plus an explicit service date; the ruleset release is
  selected by that date, never implicitly by "latest".
- Output: group, product code, point values by settlement mode, the full rule
  trace, and near-miss conditions. Output identifies the exact ruleset release
  and algorithm version used.
- The binding result for settlement is produced by NFZ's own systems during
  claim verification; the published algorithm description is descriptive.
  Minos states this, ships a golden-case regression suite cross-checked
  against existing groupers and reviewed real cases, and treats divergence as
  a defect to investigate, not to hide.

## Errors

Interfaces must distinguish at least:

- invalid or unsupported terminology system or grouping scheme;
- invalid query or code;
- missing or incompatible dataset;
- no ruleset release covering the requested service date;
- incomplete encounter (missing facts required by the scheme);
- unknown concept or group;
- malformed upstream source;
- internal storage failure;
- request limit or timeout exceeded.

Errors must not expose file paths, credentials, stack traces, raw user
queries, or encounter payloads in production logs.

## Quality Requirements

- Security and correctness take precedence over performance; measured
  performance takes precedence over developer convenience. Minos must not claim
  to be faster than competing workflows without representative browser and API
  benchmarks.
- Unit tests cover normalization, hierarchy, ranking, condition evaluation,
  and error mapping.
- Targeted mutation tests cover safety-critical validation, state transitions,
  ruleset selection, and grouper decisions. Tool selection is gated on Python
  3.14 compatibility; documented manual mutation or fault-injection cases are
  required until automation is selected.
- Fixture-driven ingestion tests include malformed and adversarial XML, XLSX,
  and DOCX inputs.
- Integration tests exercise CLI and HTTP behaviour against the same fixture.
- Full-dataset CI verifies counts, uniqueness, hierarchy integrity, list
  reference integrity, and checksums.
- Grouper releases require a reviewed golden-case suite with recorded expected
  groups and traces.
- The container runs as a non-root user with a read-only runtime filesystem.
- Container and deployed smoke tests exercise an actual search, not only
  health.
- Representative load tests measure warm latency, process startup, memory,
  image size, overload behavior, and errors on the production host before
  suitability is declared.

## Privacy And Safety

- The service is anonymous and stores no user profiles.
- Raw medical search queries and encounter payloads are prohibited in logs and
  analytics. Changing this boundary requires a separate privacy design and
  explicit accepted decision.
- Grouping is stateless: encounter inputs are evaluated in memory and
  discarded.
- Logs use request IDs, route templates, bounded durations, status classes,
  and dataset versions rather than request content.
- Fixtures and examples contain no patient data, prescriptions, or personal
  data.
- Every interface identifies the dataset and ruleset release and displays a
  concise reference-only disclaimer including the completeness-not-upcoding
  boundary.

## Delivery

The canonical runtime artifacts are private GHCR API, edge, and watcher images
addressed by digest. The API image contains the generated read-only reference
database; the edge image contains the checksummed static web bundle. A mandatory
signed `v*` tag promotes artifacts already built and tested from `main`; release
workflows do not rebuild them. Every digest and checksum must have verified
build provenance binding it to the tagged source commit.

Production runs the complete compatible digest set through Docker Compose on a
dedicated Mikr.us `3.0` host. Cloudflare Tunnel provides outbound-only public
ingress to the edge. Deployment records the prior digest set and verifies
readiness, a known search, static integrity, and mobile and desktop rendering;
failure restores the complete prior set. Mutable tags and Watchtower-style
automatic promotion are not deployment mechanisms.

Standalone CLI application bundles and their separate data bundle are also
built and checksummed on `main`, stored immutably by commit, and promoted
without rebuilding. The CLI verifies the data manifest before opening it.

A future PWA data release reuses the same immutable-artifact discipline: a
versioned client SQLite snapshot and release manifest are published separately
from application code, verified before local activation, and retained briefly
for rollback. A browser never receives or replicates the future central
application database.

GitHub-hosted Actions build, test, scan, attest, and publish artifacts but do not
run production workloads. The watcher and APScheduler run on the same Mikr.us
Compose host as the edge and API, with a separate mutable control SQLite
database protected by revisioned recovery images in S3. AWS is limited to
private versioned S3, narrow IAM, budgets, and Terraform state. See
[deployment.md](docs/v0.0.1/deployment.md).

## Remaining Gates

- Stand up phase 0 watchers and the raw archive (upstream history is being
  lost now).
- Provision the dedicated Mikr.us `3.0` host and validate complete Compose
  memory, disk, recovery, and load budgets.
- Inspect and fixture the actual RSK XML, NFZ XLSX, and komunikat DOCX
  structures.
- Formal legal opinion: Art. 4 status of zarządzenie appendices for a
  commercial product, and the scope of `CC BY-NC-SA 3.0 IGO` on the RSK
  ICD-10 translation.
- Trademark counsel review of the Minos name (registry pre-screen 2026-08-06:
  EUTM 009772146 covers class 9 software without field limitation; assess
  narrowed-specification filing and non-use exposure).
- Route canonical `minoshealth.pl` through Cloudflare Tunnel and redirect the
  registered `.com` domain to `.pl`; decide on premium `minos.health` after
  counsel review.
- Define launch service-level objectives and budget thresholds before load
  tests.
- Pin the embedded database engine after the reduced-scope spike.
- Validate grouper correctness strategy with real ward cases before `v0.0.2`
  release.
