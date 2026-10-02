# Decisions

Status: accepted planning record, 2026-08-06; amended 2026-08-21, 2026-09-17,
2026-09-23, 2026-10-01, and 2026-10-02

## Product

### From Reference Tool To Settlement Platform

The project began as a Polish drug-substitution CLI, was reframed once into an
ICD-10 terminology reference (Apollo Codebook), and on 2026-08-06 was reframed
again after practitioner feedback: the problem worth solving is JGP settlement
intelligence. An ICD-10 browser alone is a commodity; the grouper, its rules,
its versions, and their explanation are the product.

The layered scope is:

1. terminology (`icd10-pl`, `icd9-pl`);
2. JGP knowledge base (`jgp-szp`): groups, conditions, points, versions,
   diffs;
3. grouper engine with full rule trace (`v0.0.2`);
4. what-if optimizer framed as completeness (`v0.0.3`).

The drug-substitution design remains outside scope and must not influence the
data model.

### Compliance Framing

Minos supports complete and correct coding of documented care and prevention
of avoidable claim rejections. It must never recommend coding beyond
documentation. This is both an ethical boundary and the commercially
defensible position: NFZ audits punish upcoding, and the durable customer
value is captured legitimate revenue plus fewer rejections.

Grouping output is points and product codes. Contractual PLN amounts vary by
payer contract and the PSZ ryczałt stream and are out of scope.

### Interfaces

An offline CLI and an anonymous webpage backed by a read-only HTTP API share
the same domain and store behaviour. Grouping (`v0.0.2`) adds one stateless
POST endpoint. Accounts, mutable user data, and personalization are deferred.

### Phased Offline And Commercial Model

The first web release remains anonymous and online; the CLI remains the initial
offline interface. The accepted client direction after that foundation is one
responsive installable PWA, not a separate desktop application. A later
offline phase publishes a complete, immutable client SQLite artifact; the PWA
downloads and verifies a new release when it regains connectivity and
atomically replaces its prior local snapshot. It does not replicate the central
operational database or migrate the active reference database in place.

Commercial calculations are introduced only with central authentication,
entitlements, and usage accounting. A small free allowance and individual paid
plans remain online so the server is the enforcement boundary. The PWA may
disable those actions while offline, but the server remains authoritative.
Premium offline calculation is licensed separately to a hospital or other
organization through an expiring signed entitlement and contract, not an
untrusted browser-side per-run counter. Exact allowances, prices, identity
provider, payment provider, check-in period, and organization licensing terms
remain future product decisions.

Offline calculation also requires the deterministic grouper to execute in the
browser. The implementation choice is intentionally open: a Python/WASM route
or a separately implemented client engine must satisfy the same golden cases
and traces before release. Offline PWA support is a measured requirement that
may reopen the application-language decision at its documented checkpoint; it
does not justify duplicating grouping rules prematurely.

Reference data, mutable application data, and local browser state remain
separate. A future application database may hold accounts, payment-provider
identifiers, subscriptions, entitlements, and an idempotent usage ledger; it
must never hold card details or encounter payloads. Offline distribution in a
paid product is limited to datasets cleared for commercial redistribution.

### Competitive Position (researched 2026-08-06)

- CGM/UHC gruper (jgp.uhc.com.pl): maintained, freemium capped at 5 groupings
  per day, 2009-era JSF stack, no API, licensing by phone.
- SGA WikiJGP: modern rules encyclopedia; optimization answers are sold as
  1-day human consulting reports.
- Asseco, KAMSOFT: HIS-embedded groupers; distribution moat, no standalone
  API.

Market gaps Minos targets: API access, explainability (rule trace, near-miss),
unified rules-plus-grouping-plus-diffs, interface quality. Content currency is
not a gap; incumbents track NFZ files within days. Minos must match that via
phase 0 watchers.

## Brand And Domains

### Provisional Brand: Minos

The public product name is **Minos** (Dante's judge who hears each case and
assigns it to its circle; the mythological line continues the operator's
Pythia and Virgil projects). The repository is `minos-health`; Python package
and CLI remain `minos`.

Registry pre-screen (2026-08-06, TMview API, registration numbers verbatim):

- EUTM `009772146` (MINOS, word, MINOS-EMI SA, GR): class 9 includes
  "interactive computer software" without field limitation; registered,
  renewed, expires 2031. Primary risk; attorney must assess narrowed-spec
  filing and non-use revocation exposure (registered 2011).
- EUTM `010138105` (MINOS, CEA, FR): class 42 limited to nuclear research; low
  overlap.
- PL national: Grupa INCO holds MINOS in class 3 (cleaning products; this is
  why `minos.pl` is occupied); a funeral-services mark exists in class 45.
  Dissimilar goods and services; not expected blockers.
- EUTM-opposition layer: CZ `593741` (investment-project software, 2024), FR
  MINOS 400 (Inetum ERP), IT MINOSSE (software house), GR `D43553`.

Verdict: usable only with counsel; moderate-high opposition exposure at EUIPO,
lower for a narrowed PL national filing. UPRP does not examine relative
grounds ex officio.

The registered `.com` and `.pl` domains are delegated to Cloudflare. The
accepted setup makes `minoshealth.pl` canonical behind Cloudflare Tunnel and
uses a proxied permanent redirect from `minoshealth.com` to `.pl` while
preserving safe paths. `minos.health` remains an optional alias only after
trademark review and renewal-price verification; `minos.pl` and `minos.com`
were already taken at the 2026-08-06 check.

### Fallback And Rejected Names

- **Telesphoros / Telesfor**: accepted fallback. God of convalescence and
  bringing-to-completion (coding completeness); registry-clean in EM/PL/WO on
  2026-08-06; `telesphoros.health`, `telesphoros.pl`, `telesforhealth.pl`
  free; `telesfor.pl` taken 09.2025.
- **Obol** (Charon's passage coin): rejected. EUTM `018756046` (THE OBOL
  ASSOCIATION, the Ethereum Obol Network entity) covers classes 9/36/42;
  class 36 financial services is directly adjacent to billing software.
- **Ariadne** (thread through the labyrinth): rejected. `ariadne.health`
  registered 2018, `ariadne.pl` taken, Ariadne Labs operates in healthcare.
- **Charon**: rejected for death connotations in a hospital product.
- **Apollo Codebook**: superseded by the pivot; the Apollo base name was
  crowded in healthcare (Apollo Hospitals and others).
- Earlier rejections retained from the Apollo Codebook process: Athelas
  (active healthcare AI company plus Tolkien estate risk), Apollo
  Ref/Index/Codes/Health/Med, Paeon, Iaso, Aceso, Epione, Meditrina, Machaon,
  and direct Tolkien names.

Domain registration remains a manual external action. The registrar account
must use 2FA, auto-renewal, registrar lock, recovery codes stored off-host,
and DNSSEC when the complete DNS chain supports it.

## Scope And Namespaces

Bundled systems: `icd10-pl`, `icd9-pl`; grouping scheme `jgp-szp`. Future
systems and schemes must remain distinct even when codes overlap:

- `icd10-who`, `icd10-pl`, `icd9-who`, `icd9-cm-us`, `icd9-pl`
- `jgp-szp`, `jgp-aos`, `jgp-reh`

No source may be presented under a generic `icd10`, `icd9`, or `jgp` identity
when its country, modification, or release affects meaning. The abstraction
layers (terminology systems, grouping schemes, tariff schedules) are the
mechanism by which other markets arrive without schema changes.

## Licensing

Repository software, infrastructure, tests, and documentation are reusable
under `Apache-2.0 OR MIT`. Generated datasets and images containing them
remain under source-specific terms.

The commercial core (JGP catalogs, charakterystyka, algorithm, ICD-9 PL, and
the billing-relevant ICD-10 codes with Polish titles embedded in załącznik 9;
verified by sampling the 1/2022 edition on 2026-08-06, 8,759 code-plus-title
strings in that file including JGP group names) is published inside
zarządzenia Prezesa NFZ and NFZ komunikaty. The strong reading is that these
are copyright-exempt official materials under Art. 4 pr. aut., with 15-plus
years of commercial grouper precedent (KAMSOFT, CGM, Asseco, SGA). A formal
legal opinion is a release gate for the paid product, not for development.

The RSK ICD-10 resource metadata identifies `CC BY-NC-SA 3.0 IGO`, with the
license sentence grammatically attached to the WHO 2019 English edition. The
full RSK dictionary is therefore treated as free-tier enrichment whose
noncommercial constraint must never sit under the paid path until the scope
question is legally resolved.

On 2026-10-01 the owner confirmed that the current project is noncommercial,
reference search will remain noncommercial, and the pinned RSK ICD-10 PL release
must be included in public search. The free search preserves attribution, source
terms, translation and non-endorsement notices. This does not clear RSK content
for paid features or commercial offline distribution.

The same decision selects NFZ CZS as the sole canonical ICD-9 PL catalog.
For the pinned 5.90 captures, CZS has 10,179 codes versus 10,059 in the workbook:
121 CZS-only codes and one workbook-only code mean neither is a strict superset.
Canonical codes, titles, statuses and dates come only from CZS. Same-version
workbook ancestry is supporting evidence linked by exact code, with its own
release identity and checksum. It never supplies canonical titles/statuses,
adds workbook-only search results, or invents parents from code prefixes.

WHO-specific ICD-10 and international ICD-9 redistribution rights are
unresolved and those datasets are excluded.

## Architecture

### Python Application

On 2026-08-21 Python 3.14 replaced the planned Rust workspace as the sole
application language through the 2027-08-21 checkpoint. This aligns the product
with the owner's applied-AI, backend, and data work while making Minos the place
to rebuild independent code review, testing, and debugging. Rust is frozen;
reconsideration requires the checkpoint or a measured requirement Python cannot
meet.

The accepted internal packages are:

- `minos.domain`
- `minos.store`
- `minos.ingest`
- `minos.cli`
- `minos.http`
- `minos.grouper` (planned, `v0.0.2`)

The domain remains synchronous and independent of CLI, HTTP, database, and
frontend frameworks. FastAPI is a thin adapter, not the owner of search or
grouping rules. The grouper is deterministic: rules are versioned data, the
engine is versioned code, and every result carries its trace. AI may assist
implementation, but code is accepted only when the owner can explain, review,
test, and debug it.

### Engineering Priorities And Dependencies

Security, correctness, and measured performance are co-equal release
requirements. A release must satisfy all three; tradeoffs require
representative evidence, documented limits, and explicit review. Developer
convenience follows these requirements. Minos is intended to outperform
incumbent workflows, but a performance claim requires representative browser,
API, container, and data benchmarks. Validation, privacy, provenance, and
determinism are not traded for an unmeasured speed improvement.

Use the Python standard library when it is the clearest adequate choice. Use a
maintained library when it materially improves correctness, security,
interoperability, performance, or maintainability. Approved foundation tools
are uv for environments and locking, Pydantic at configuration and untrusted
input boundaries, HTTPX for bounded HTTP communication, pytest for testing, and
Loguru for allowlisted structured process logs. Domain behavior remains
independent of transport, persistence, and serialization libraries.
Dependencies enter the lockfile only with working code, a defined boundary,
and a licence review.

Testing uses unit, targeted mutation, and integration layers where the changed
boundary warrants them. Mutation testing focuses on safety-critical decisions
and starts only after the selected tool is proven compatible with Python 3.14;
documented manual mutation or fault-injection cases cover changed critical
invariants until then. Each coherent implementation chunk has a user-visible
diff and relevant evidence. Substantial or safety-sensitive changes receive a
pair-programmer TigerStyle review; full repository checks run before submitting
code. Test-count and coverage-percentage targets do not replace boundary
evidence.

### Bitemporal Versioning

Every rule-bearing fact carries valid time (the reporting period it governs)
and knowledge time (when it was ingested). This is mandatory, not optional:
NFZ amendments apply retroactively (74/2026/DSOZ published 2026-07-23,
entered into force 2026-07-24, and applies to services from 2026-07-01 under
section 4 of the act in BAW). Queries select rules by service date; "what did
we believe on date Y about service date X" must be answerable for audit
support.

A `ruleset_release` is a composite: zarządzenie plus załącznik versions,
algorithm version, ICD-9 version, and ICD-10 version paired per reporting
period (per § 10 of the base act).

### Staged JGP Reference Delivery (2026-10-02)

The first JGP group browser (#17, minimal #18, #24 and #26) reports catalog
base points without modifiers, not final settlement points. API and UI label
that limitation and show the act/catalog release governing the selected service
date. Date resolution never falls back to the latest release.

Minimal #18 retains valid and knowledge time and row-level base-point
applicability, including 102/2026 section 4(5)'s catalog rows deferred to
2027-01-01. It represents modifier binding as explicitly unbound. #46 owns
MODTAR compatibility and binding plus the section 17 coefficient transition
under sections 4(3) and 4(4). #35 must fail on unbound modifiers before final
tariff calculation; an unbound source is not an empty set of applicable rules.

The first #26 acceptance is a shareable, date-specific group page with base
points by settlement mode, time-limited catalog parameters and provenance.
Version diffs are a separate follow-up depending on #20. Condition-based ICD
cross-links and reverse lookup belong to #32 after #30. This changes delivery
order, not the full product scope. Implementation PRs require owner review
before merge and preserve the #102 shared layout and search-first interface.

See [JGP delivery scope](jgp-delivery-scope.md) for issue ownership and review
gates.

### Search Storage

The #11-#13 pipeline replaces #10's disposable engine spike. Plain SQLite with
FTS5 through the pinned Python runtime and standard driver remains the default
candidate. Verify runtime identity, FTS5, read-only operation, determinism and
representative build/query measurements against the actual artifact. #14 owns
Polish search precedence and standalone CLI packaging; deterministic
`linux/amd64` container search remains a runtime acceptance gate.
Normalized B-tree search remains the portable fallback. Semantic
neighbours are deferred entirely and would require their own safety gates to
return.

The source contract review accepts one generic release-scoped node/relation
graph for classifications, including uncoded text and structural containers.
Concepts and multilingual descriptions link to source nodes, not duplicated
ICD-specific trees. Relations distinguish source containment from classification
semantics. Repeated dotted suffixes retain raw values and an explicit contextual
lookup conversion; single-digit modifiers remain non-concept nodes until their
semantics are separately reviewed. No conversion infers billability.

### Maintained Linguistic And Catalog Search (2026-09-30)

The owner approved replacing the narrow hand-maintained Polish form example with
Morfeusz2 1.99.15 and its pinned embedded SGJP dictionary, shared by language
rather than classification. Literal source vocabulary and exact-match precedence
remain separate from ambiguous linguistic alternatives. Numbers, code tokens,
short grammatical operators and negation are not rewritten or dropped. Explicit
query alternatives and source evidence accompany morphological matches. No model,
embedding or remote search provider is required; assisted search remains #95.

Morfeusz/SGJP use BSD-2-Clause; notices ship with the wheel and standalone CLI.
The glibc-only analyzer belongs to the `reference` extra and developer environment,
not the Alpine watcher. Standalone builds run in pinned Bookworm instead of
bundling a workstation's potentially incompatible C++ runtime. PyInstaller remains
an isolated build dependency under its approved application-bundling exception.

The owner also approved metadata-only `/zrodla` ranking, prefixes, explicit spelling
suggestions and filename/version evidence using locally vendored MiniSearch 7.2.0
(MIT). There is no file-content extraction or query transmission. Existing RSK
families and BAW parent/history grouping remain authoritative. The browser engine
is not a replacement for, or a claim of parity with, Polish morphological analysis.
Its pinned code/license concatenate with the UI into the existing exact-route
asset, without a CDN or frontend framework. See [catalog-search.md](catalog-search.md).

### Ingestion Model

Watchers-first. A dedicated watcher container on Mikr.us uses APScheduler and a
mutable control SQLite database to poll upstream sources, archive scoped raw
files immediately, and emit change records or incidents. Exact bytes live in
private versioned S3; source configuration, schedules, runs, observations,
artifact references, source cursors, state revisions, and audit records live in
SQLite. S3 is not a database and contains no mutable watcher-state object or
provenance sidecars. Immutable control-database recovery images are distinct
from source cursors.

Issue #21 turns reviewed changes into data PRs with manifest changes and diffs.
An operator-dispatched protected workflow identifies one verified recovery image
through the read-only GitHub OIDC archive role and exports only the selected
bounded change record; the watcher receives no GitHub credential. A human
approves and CI builds immutable artifacts. The "CI never follows latest" rule
is unchanged. Details in [data-sources.md](data-sources.md).

### Frontend

Frontend source lives under `apps/web`. The first webpage may remain static
HTML, CSS, and JavaScript with no frontend build framework. Its immutable output
is embedded in a separate unprivileged edge image. Docker Compose serves that
image locally and on Mikr.us, proxies API paths to the private API container,
and exposes the shared origin only through Cloudflare Tunnel. The later
installable PWA adds a service worker, local SQLite through WASM and browser
storage, and one-way dataset-release synchronization. The `v0.0.2` encounter
builder and trace visualization may justify introducing a frontend toolchain
before the offline phase.

## Artifact And Release

### Owner-Approved Public Validation (2026-10-01)

The owner explicitly approved a bounded public ICD-9 website/API validation
deployment after #16 rather than treating it as a commercial product release.
This exception retains exact image/dataset identities, rollback, query privacy,
source attribution and measured runtime limits. It neither claims legal/brand
clearance nor completes the independently signed product release gate. Medical
queries use POST bodies, not URLs. Raw private-archive routes remain closed;
official NFZ download links remain available. See
[the validation contract](../validation/reference-web-rollout.md) for the narrow
scope and the maintenance-playbook limitation.

One `linux/amd64` API image, one edge image containing the checksummed static
web bundle, and one watcher image are the runtime artifacts. Docker Compose runs
the complete set locally and on the dedicated Mikr.us `3.0` host.

Private GHCR is canonical. Images are built on `main`, tagged by commit, and
addressed by digest. A build attestation binds the digest to its source commit
and workflow identity; the web bundle has equivalent commit-bound checksum
provenance. A mandatory signed `v*` tag promotes the verified artifacts; it does
not rebuild. Deployment pulls an exact compatible digest set from private GHCR,
verifies it through the public Cloudflare hostname, and restores the complete
previous set on failure. Prebuilt standalone CLI and data artifacts follow the
same promotion rule.

Mutable `latest` deployment and Watchtower-style production promotion are
rejected. Helm is rejected because there is no Kubernetes deployment.

## Production Hosting

### Sole Production Runtime: Mikr.us 3.0

Mikr.us `3.0` is the sole production runtime for `v0.0.1`. The dedicated host
runs Cloudflare Tunnel, edge, API, and watcher containers through Compose.
Ansible owns reproducible host configuration, a restricted token pulls exact
private GHCR digests, and no application port is published publicly.

This accepts host patching and recovery responsibility in exchange for one
small fixed annual cost and a much simpler system than separate Lambda,
CloudFront, ECR, S3 frontend, and watcher-control services. The host is not a
backup: mutable control SQLite is backed up consistently to S3, raw bytes are
stored directly in versioned S3, and complete empty-host recovery is tested.

AWS remains only for the snapshot bucket, narrow IAM, budget notification, and
Terraform state. A managed application runtime can be reconsidered only if
measured availability, capacity, or operational burden exceeds recorded limits.

## Infrastructure Ownership

- Terraform/OpenTofu owns stable AWS and Cloudflare resources, including the
  snapshot bucket, watcher IAM policy, OIDC read role, budget, DNS, and tunnel
  routing where supported.
- Ansible owns the Mikr.us host configuration.
- Docker Compose owns local and Mikr.us process topology.
- GitHub-hosted Actions own tests, builds, scans, attestations, and GHCR
  publication. They do not run production schedules or use the host Docker
  socket.
- APScheduler in the watcher container owns runtime dispatch; SQLite owns its
  durable configuration and status.
- A registrar remains responsible only for domain registration and delegation.

## Privacy And Observability

Raw medical search queries and encounter payloads are prohibited in logs and
analytics. Grouping is stateless: inputs are evaluated in memory and
discarded. Structured events use route templates, request IDs, status classes,
timings, result counts, and dataset versions. Any future query analytics
require a separate privacy design and accepted decision.

The public site loads Plausible directly, not through a tag manager, for
sanitized pageviews and the tracker's related aggregate engagement duration and
scroll-depth measurements. The accepted configuration uses no cookies, local
storage, persistent identifiers, advertising, user IDs, medical values, or
custom event properties. Public URLs must not carry medical queries. Automatic
form, download, and outbound-link tracking remain disabled; any custom event
requires a documented property allowlist and privacy review.

Loguru emits allowlisted structured events to stdout for collection by the
active platform. It is not the incident model or an external error tracker.
Watcher and recovery-image failures surface through SQLite incidents inspected
with the detailed read-only
`minos watcher status [SOURCE_ID] [--database PATH] [--json]` command.
Operators correlate that durable status with bounded privacy-safe local
lifecycle output retained by Docker's `local` log driver. The production
commands are:

```sh
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  exec --no-TTY watcher /app/.venv/bin/minos watcher status rsk --json
docker compose --project-name minos --file /opt/minos/compose.yaml \
  logs --since 24h --tail 500 --timestamps watcher
```

`heartbeat_pending` remains the persisted compatibility name for the durable
local finalization phase after recovery-image verification. It is not a
monitoring request. The watcher sends only up or down to Uptime Kuma after a
durable terminal result; a failed monitor request is best-effort and never
blocks or changes watcher completion.

The accepted monitor is self-hosted Uptime Kuma 2.5.5 on the same Mikr.us host,
not a paid external monitoring SaaS. It uses the official MIT `slim-rootless`
`linux/amd64` image pinned to
`sha256:8bdad438d293e998fd7d45004cefb3baea0f96d3bc774502bba99c17d616e1f6`,
numeric identity `10003:10003`, no Docker socket, no host port, a private
Compose monitoring network, a separate outbound notification and HTTP monitor
network, and persistent `/app/data` at `/var/lib/minos/uptime-kuma`. The
Cloudflare route defaults off until private setup and access controls are
complete.

`status.minoshealth.pl` publishes coarse service state only. Detailed watcher
state remains private, and issue #54 remains the future private detailed admin
portal. Since Kuma shares the host, it cannot detect or notify for a total
Mikr.us host, network, or power outage. This limitation is accepted for now.
Docker logs are bounded locally. CloudWatch is not part of the runtime.
Selection of an external error-reporting service remains open and must pass a
privacy review before receiving events.

Local application secrets live only in ignored `.env` files; local cloud
credentials stay in external profiles or keychains. Production secrets are
host-managed files restricted to root and the pinned numeric UID or GID of only
the required service, then mounted read-only into that container. Recovery
copies stay in the password manager. GitHub authenticates to AWS through OIDC
and never stores long-lived AWS access keys. The narrowly scoped watcher access
key is an explicit external-host exception and never enters Git, `.env`, GitHub,
Terraform state, or an image.

## Open Gates

- Trademark counsel review of Minos; fallback decision if negative.
- DNSSEC and the optional `minos.health` decision.
- Formal Art. 4 legal opinion for commercial use of NFZ-published data.
- `CC BY-NC-SA 3.0 IGO` scope resolution for the RSK ICD-10 dictionary.
- Inspection and fixturing of complete RSK XML, NFZ XLSX, and komunikat DOCX
  structures.
- Launch SLOs, cost budget, and benchmark thresholds.
- Pinned embedded database engine from actual reference-artifact evidence,
  followed by standalone CLI and container search acceptance.
- Grouper correctness validation protocol with real ward cases (before
  `v0.0.2` ships).
