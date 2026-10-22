# Decisions

Status: accepted planning record, 2026-08-06; amended 2026-08-21, 2026-09-17,
2026-09-23, 2026-10-01, 2026-10-02, 2026-10-03, 2026-10-05, 2026-10-16 and
2026-10-22

## Product

### API Activation Without Downtime (2026-10-16)

For #192, the owner chose blue/green for the API only. Each release starts the
new API colour beside the live one, moves the edge after real API health, and
retires the old colour after real edge health. The API startup no longer
interrupts public traffic. The edge is still recreated per release (about one
second), which the public-edge Kuma monitor's retries absorb. The #181 deadline,
fail-closed checks and exact prior Compose rollback stay. Activation requires
`MemAvailable` for two APIs. Rollback without that room retires the current API
first, because recovery outranks availability. See
[colour activation](../validation/validation-deploy.md#colour-activation-without-downtime-192).

### Bounded Validation Startup And Rollback (2026-10-13)

For #181, the owner approved one shared five-minute real-health deadline for
each API/edge activation or rollback attempt. Start API first and edge only
after API readiness. Initial unhealthy status may recover within that bound;
exits, OOMs, new restarts, ambiguous state or lost API health fail closed.
Preserve the exact prior Compose bytes, validation and request/resource limits.
A failed activation retains one separately bounded rollback attempt. Production
activation still requires an exact-image plan and owner review. See the
[deployment procedure](../validation/validation-deploy.md).

### Source Freshness Presentation (2026-10-12)

The owner removed the blanket "Stan wiedzy Minos" horizon warning and repeated
cutoff copy from JGP catalogs, groups, comparisons and attachment-9 source
summaries, including ICD-9 context. Keep concrete source releases, governing
acts, publication/applicability dates, official links and checksums. A permanent
generic warning adds no actionable information to those source details.

This is a presentation decision. Bundle knowledge fences, explicit date/release
selection, API provenance and failures for unsupported dates remain enforced.
It neither advances a bundle's recorded cutoff nor enables deferred automatic
processing/activation under #141. Data freshness remains an operational duty.

### Durable Source Citations (2026-10-12)

For #80, the owner selected durable metadata-only observation citations rather
than links tied to rotating public generations. Routine cleanup must preserve
issued citations. Storage has an explicit bound; exhaustion refuses new
publication rather than evicting evidence. Exact observation recovery is part
of acceptance. Source, dataset, file, observation and legal applicability remain
distinct, and raw archive bytes remain private.

The owner approved 1,024 observations / 256 MiB per provider, explicit refusal
at exhaustion, and refusal to rewrite an already-cited revision. Rollback to
the original bytes remains supported. The serving layer reads only the same
read-only public metadata tree as the edge, with independent source failures
and bounded initial HTML. The RSK/BAW routes and local acceptance are recorded
in [source citations](../validation/source-citations.md). Deployment remains a
separate guarded acceptance boundary.

### Release-Pinned Concept JSON (2026-10-12)

The owner approved #23's explicit dictionary release and code path, with an
optional dated JGP-context path. The service date selects reviewed attachment 9
evidence, never a different ICD release or inferred dictionary validity. Reject
cross-release context rather than joining equal codes across releases. Rank
evidence keeps its existing release pairing and unbound applicability, with no
rank-to-service-date claim. Keep global query-string rejection and bounded
shared store/domain behavior. No implicit latest-release or today fallback.

### Watcher-Only Feature Activation (2026-10-12)

For #92, the owner approved a guarded watcher-only release path rather than
stopping the whole active validation stack through the maintenance playbook.
Require published immutable image evidence, successful main CI, isolated paused
preflight against copied control state, exact Compose fencing, and retained
rollback evidence. API, edge, tunnel and monitoring identities/restart counts
must remain unchanged. This authorizes no schema migration, upstream recollection
or automatic reference-bundle update. The ordinary API/edge deployment follows
after the watcher has restored the NFZ public projection.

### Implementation Order After Comparison Delivery (2026-10-11)

The owner deferred #42's capacity/SLO work until actual product traction and
paused practitioner-dependent acceptance until a practitioner is available.
Next: implement, verify and deploy #162's rollback diagnostics/recovery, then
#92's NFZ publication metadata and #23's JSON concept detail. These priorities
do not turn existing measurements into capacity or practitioner acceptance.

### Preloaded Catalog Comparisons (2026-10-11)

After #160 and #161 public comparison timeouts and guarded rollbacks, the owner
approved a per-verified-bundle cache of immutable decoded catalog schedules and
canonical comparison facts. Build it completely before API readiness; startup
fails closed on invalid data, deadline or memory/population limits. Do not fall
back to expensive request-time projection or share release-key entries across
bundles. Existing date/release selection, artifact identity checks and request
budgets continue to apply. The accepted cost is additional bounded startup work
and retained memory; verify both under the 512 MiB runtime limit and verify the
first public comparison after activation. This does not establish host capacity
or waive #42. See [comparison evidence](../validation/jgp-comparison.md).

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
POST endpoint. Accounts, mutable user data, and personalization are deferred,
except for the narrow browser-local search filter setting accepted below.

### Browser-Local Search Filters And Social Metadata

On 2026-10-05 the owner requested all available search sources selected by
default, automatic filter persistence in localStorage without an opt-in,
updated product metadata, a branded raster social preview and a privacy-policy
update. ICD-11 was unavailable then; since 2026-10-22 it is a source that
starts unselected (see below). Only explicit filter changes write the
versioned allowlisted selection and a 90-day expiry; searches, identifiers and
medical codes are excluded. Read/search does not extend expiry. Reset removes
only this key, restores the default sources, and does not write a new record.
Unavailable sources remain part of user intent; an intentional empty selection
is preserved and validated rather than silently replaced.

The policy describes the actual automatic behavior. After the initial release
on 2026-10-05, the owner requested removal of the inline storage notice from
the search form. The reset control and policy disclosure remain unchanged.
localStorage is subject to browser-storage law just like cookies. The owner's
implementation choice is not legal clearance: the adequacy of automatic
persistent storage without consent has not been established. No blanket
consent-exemption or no-banner-required claim is made. This setting must not
expand into analytics, identifiers, query history or broader personalization
without a new review. Expired data is rejected and deletion is attempted on
next access, not promised while the browser is closed or storage is blocked.

The 1200 x 630 static PNG uses original branding and existing OFL fonts.
Shared Open Graph/Twitter metadata keeps page-specific titles and provenance;
homepage copy covers reference ICD and JGP search, conditions and base points,
not an implemented encounter grouper or final settlement calculation.

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

### Reviewed Dictionary Expansion And Search Recovery

On 2026-10-05 the owner requested dictionary implementation with before/after
review and preferred useful results beyond 250/400 ms to blank failures.
After reviewing the full external dictionary's wrong-sense regression, the
owner selected a source-reviewed subset with explicit JGP related-procedure
labels. The external dictionary produces review candidates, not automatically
active mappings. Exact matches, source labels, qualifiers and dated scope stay
authoritative.

The implementation uses bounded waiting, cooperative search/stage deadlines,
and distinct capacity, timeout and result-bound errors. Latency targets do not
justify presenting failed work as no matches. Evidence and remaining VPS release
checks are in [dictionary search recovery](../validation/dictionary-search-recovery.md).

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

Refinement accepted by the owner in #18 (2026-10-02): for reviewed
releases ingested after publication, the knowledge-time filter uses the
reviewed publication date, while the Minos fetch time stays recorded as the
ingestion identity. Back-filled releases would otherwise share one ingestion
date and could not answer what was known before a retroactive amendment.

The #24 JGP routes carry the service date as a path segment instead of
SPEC's `?date=`, keeping the reject-every-query-string privacy rule global
and making group pages shareable. Their list is per catalog chapter at a stable
source position instead of a paginated `?section=` query.

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

### JGP Source-Local Code Correspondence (2026-10-03)

Owner-approved during #30: NFZ attachment 9 list members retain release-local
NFZ code/title identities even when the selected RSK ICD-10 release lacks an
exact counterpart. Source-local references must resolve; dictionary
correspondence is separately attributed and explicitly matched or absent.
Absence from RSK does not prove invalidity and never authorizes substitution,
deletion or extending canonical dictionary search. This refines #29's
reference-build gate, not the future service-date/executable ruleset gate.

See [the evidence and population contract](jgp-code-correspondence.md) for
the 19 observed source differences, independent government evidence and the
watcher/archive -> reviewed manifest PR -> deterministic rebuild -> immutable
application artifact -> #32 consumer path. Watchers never patch live conditions
or silently resolve a source disagreement.

### JGP Group Browser Pages (2026-10-03)

Owner-approved in #26: JGP groups get their own top-level section, "Grupy
JGP" at `/jgp`, next to the dictionaries instead of under them. A JGP group is
a settlement category, not a terminology code, so the never-built "Procedury
medyczne" dictionary placeholder (home chip and dictionaries card) is removed
rather than repurposed; no issue tracked it.

- URLs carry the service date as a path segment like the #24 API:
  `/jgp/{date}` (catalog), `/jgp/{date}/{CODE}` (group) and
  `/jgp/{date}/zakres/{n}` (catalog limited to one "Zakres świadczeń" column).
  No query string is accepted, and the date picker navigates by path because
  the CSP forbids form submission.
- `/jgp` and `/jgp/{CODE}` redirect (302, `no-store`) to today in
  Europe/Warsaw; missing time-zone data is an explicit 503, never a UTC guess.
   The original horizon notice was removed by the 2026-10-12 source-freshness
   presentation decision above. Lowercase codes redirect permanently to the
   uppercase URL.
- Pages are server-rendered from one bundle lease per request and complete
  without JavaScript. `jgp.js` adds the date picker and a local code/name
  filter (case and Polish diacritics folded) that sends no request, writes no
  URL and stores nothing. The zakres filter is a server-side page because it
  must use the scope markers of the row valid on the date: a section 4(5)
  twin and its deferred 2027-01-01 row can carry different markers.
- Pages are `noindex, follow` until the validation launch decides otherwise.
  Dated pages use the page cache policy; redirects and errors are `no-store`.
  The edge cache map passes the application's `Cache-Control` through for
  `/jgp` and `/api/v1/jgp/`, which also removes the duplicate header the JGP
  API carried after #24.
- Measure labels quote the reviewed source column headers, empty catalog cells
  read "brak wartości w katalogu" (never 0), and every page states "punkty
  bazowe wg katalogu, bez modyfikatorów" with the governing act, BAW document,
   service-start evidence and workbook checksum. The knowledge horizon remains
   part of the bundle and API provenance rather than repeated page copy.
- A group page links to the next row window when its row ends, and to the
  previous day only when the row starts inside its release; before a release
  start the group may not exist, so only the date picker reaches that day.

The layout follows WikiJGP and the UHC grouper reference tools (version first,
code-or-name lookup, base points, zakres filter) inside the #102 shared site
shell.

### JGP Coverage From January 2026 And MODTAR References (2026-10-03)

Owner decisions:

- The reviewed catalog lineage now starts at 2026-01-01 with 101/2025/DSOZ
  (original and after 14/2026, the only amendment that replaced attachment
  1a). 46/2026 supersedes it by repeal from 2026-05-01. Cross-act
  supersession is allowed only forward in service time; amendments within one
  act may still apply retroactively.
- MODTAR binding resolves each mapping against the catalog governing that
  mapping's own months. One trailing `.` or `_` is removed when the resulting
  group exists there (`C14.`, `P42.`, `P52_`). `Z01_S` has no public
  definition and is read as Z01 by explicit owner assumption for exactly that
  literal. Every 2026 MODTAR group reference now resolves; binding still
  requires a reviewed legal overlay.

See [JGP catalog ingestion](jgp-catalog-ingestion.md) and
[MODTAR ingestion](jgp-modifiers-ingest.md).

### Grouper Selection, Diagnosis Level And Age (2026-10-07)

Owner decisions while reviewing #144 against the pinned algorithm `1.1.2026`:

- Selection. Paragraph 113 leaves the choice among several valid JGPs for one
  hospitalization to the reporting provider. Minos returns every qualifying
  group with its base points, ordered by value, highlights the highest value
  and labels the choice as the provider's. It never reports one group as the
  correct result. This supersedes the single best-group step in #35 and suits
  the discharge audit: a reported group can be compared with valid alternatives.
- Diagnosis level. Paragraph 143 groups on the hospitalization-level SWIAD
  causes (`przyczyna-glowna`, `przyczyna-wsp`). A diagnosis reported at that
  level is never dropped by a paragraph 119 stay exclusion; a stay-attributed
  diagnosis follows its stay; a diagnosis with neither marker remains
  unresolved whenever an exclusion could apply.
- Age. Completed years follow Civil Code art. 112: a missing anniversary day
  means the month's last day, and age is reached at its start. Someone born on
  February 29 completes a year on February 28 of a non-leap year.

### Executable Attachment 9 Subset (2026-10-07)

This initial decision is retained chronologically. The tariff-assumptions
decision below extends supported predicates and root shapes; the 2026-10-09
settlement-product decision supersedes its temporary product-free restriction.

All attachment 9 conditions are machine-extracted and stored as `proposed`.
For #35 the owner approved a minimal executable subset: diagnosis list
presence by role, procedure list presence, `all`/`any`, age in completed years
and stay-length bounds (only with a complete, product-free report while #145
is open). Only groups with a single reference root are evaluated. Every other
kind, policy or root shape evaluates to `undetermined` with a reason and is
never guessed into a valid or invalid group.

### Tariff Assumptions Without NFZ Answers (2026-10-07)

The owner stated that NFZ will not answer the open MODTAR questions (#129).
These owner assumptions replace the answers and unblock #35:

- MODTAR as published wins. NFZ's settlement systems apply MODTAR, so it
  reflects what is paid. The six differences in #129 (DU58, Q16E, S21-S23,
  robotic groups under 17.18, PZA04/PZF13, PZD04A) follow the dictionary and are
  shown as known risks next to the affected answers.
- Generation 102 (published 2026-09-24) binds every 2026 catalog from that
  knowledge date; earlier knowledge dates stay unbound. On 2026-10-13 (#184)
  generation 103 (published 2026-10-09) replaced it under the same rule, from
  knowledge date 2026-10-09: it equals 102 plus four records adding PZA04 and
  PZF13 to 17.10 and 17.11 for months 7-12, which settles that #129 difference
  for July-December. January-June stays as published (without them).
- Section 17(6a) (simultaneous bilateral inguinal hernia, F72/F73, 1.4, from
  2026-10-01) is missing from MODTAR but undisputed. It is a separately
  attributed legal supplement that the caller must declare.
- Modifier eligibility facts that the encounter does not carry (contract
  scope, provider tiers, DiLO, KOC, bilateral operations) are declared by the
  caller. A mapped but undeclared modifier is reported as possible, with the
  value it would add; it is never applied silently.
- Applicable coefficients multiply: base x c1 x c2. Each clause reads
  "korygowana jest z zastosowaniem współczynnika"; the act states no
  combination rule.
- Final points are computed exactly and rounded half-up once to 0.01 point.
- The January-August 2026 food-standard ratio (14/2026 section 2) is out of
  scope; answers for those service dates carry a visible note.
- The executable attachment 9 subset widens to discharge mode, procedure
  counts (distinct codes on distinct dates; a missing date counts as the same
  day, paragraph 136) and multi-root groups read as alternatives (any root
  met). Negative lists stay undetermined.
- Label grammar: semicolons separate required parts; within a part "oraz",
  "i" and commas are AND, while "lub"/"albo" join only the adjacent
  alternatives (OR binds tighter than AND). D45 therefore reads: principal
  from D45 AND secondary from D3 AND procedure from D45 AND age over 17 AND
  (stay over 7 days OR discharge mode 9). Labels that still do not parse under
  this rule stay undetermined.

### Grouping Near Misses, Payload And Abuse Limits (2026-10-08)

Owner decisions while reviewing the #39/#36 grouping interface:

- Age and duration near misses are documentation checks for off-by-one errors
  only: a group is reported when a value one year or one day from the reported
  one makes it valid. A 51-year-old is not asked to check age against "< 18".
- A discharge-mode near miss is never emitted for mode 9 (death); other
  satisfying modes, such as J29's mode 3, remain checks.
- `not_evaluated` lists groups once per reason, keyed by catalog, instead of
  repeating an explanation for every catalog group on each recalculation.
- Per-client abuse limits belong to a Cloudflare per-IP rate-limit rule for
  `POST /api/v1/group`; the API keeps its process-wide bucket and no IP state.
  The rule is a separate edge change and a release gate for #39.

### Serving-Layer Service Boundaries (2026-10-08)

Reviewed after the #39 grouping interface, against repository, service-layer
and unit-of-work patterns:

- No repository classes. `minos.store` modules are read-only query functions
  over one immutable, checksum-verified SQLite artifact, returning domain
  types. A repository interface would add a persistence-swap seam with no
  second implementation; tests use real SQLite fixture bundles, which prove
  the SQL itself. Raw SQL belongs in `minos.store`, never in an adapter.
- No unit of work on the serving path. Reads mutate nothing; a `ReferencePool`
  lease over a `mode=ro&immutable=1` connection already delimits one
  consistent read. Atomic writes exist only in the artifact builder and the
  watcher control store, which owns its explicit `BEGIN IMMEDIATE` transaction.
- One application-service function per use case, in its domain package. For
  grouping, `minos.grouper.pipeline.group_request` owns the product-report
  gate, the whole-evaluation budgets and the store reads, and returns domain
  values (`GroupingOutcome` or `UnsupportedProductReport`).
- A versioned wire schema shared by the CLI and HTTP lives in a
  `minos.cli.*_output` module, as `search_envelope` already did. `minos.http`
  may import only those serializers from `minos.cli`, never commands or use
  cases. A separate schema package is deferred until a third adapter needs it.

### Grouping Builder Dictionaries (2026-10-08)

Owner decisions for #151: ward specialties and discharge modes are offered by
official name from pinned legal acts, through the reviewed manifest path.

- Discharge modes (annex 4, Dz. U. z 2025 r. poz. 1588) and specialty codes
  (annex 2, Dz. U. z 2019 r. poz. 173) are reviewed transcriptions of the
  authentic PDF, checked row by row against committed text extracts. No PDF
  parsing dependency is added; the ELI HTML is not used because it merges rows.
- Children's codes derived by paragraph 9 section 4 are offered and marked as
  derived; exact codes outside the annex remain accepted.
- Schema 9 stores the dictionaries; `GET /api/v1/grouping/dictionaries` serves
  them read-only.

### Settlement Product Exclusions (2026-10-09)

Owner decisions for #145, after reading the pinned algorithm (paragraphs on
products excluded from grouping, types W and V, and the duration formula with
an excluded product) and the BAW packages of every pinned act:

- The service date selects the governing act; that act's own product catalogs
  classify reported products, older acts included. All five pinned acts
  publish 1b, 1c, 1d, 1ts, 1ows and 1w and no 1v, so type V cannot occur; an
  act whose attachment set differs from the reviewed one fails the build.
- A product listed in the governing 1w catalog is type W. Every ICD-9
  procedure whose date falls within its start and end dates, inclusive, is
  excluded. Products in 1b, 1c, 1d, 1ts or 1ows but not 1w, and JGP product
  codes of the governing catalog (1a and 1ae), do not exclude anything. A code
  found in none of the act's parsed catalogs is unknown; codes of a pinned but
  unparsed legacy `.xls` catalog (1ts of act 101/2025) are therefore unknown.
- Owner interpretation: the formula th = (dw - dp) - (dzsw - dpsw), stated for
  a "katalog wyłączony z sumowania" (a phrase the NFZ order never uses), is
  the W duration rule; each W product subtracts end - start. Golden cases built
  on it are labelled as derived from this interpretation.
- A W product overlapping or adjacent to an excluded ward stay leaves the
  duration undetermined; disjoint intervals are subtracted separately.

Conservative rules adopted from the design review, each keeping an unknown
undetermined instead of guessing:

- Any unknown product, or a W product without both dates, leaves the duration
  undetermined and the procedures it could cover unresolved; a missing date
  extends that range to the episode boundary on its side.
- Products reported without a binding leave every procedure unresolved in the
  engine itself, not only behind the application refusal.
- W products that overlap or are adjacent (sharing a day or on consecutive days)
  exclude procedures over their union but leave the duration undetermined.
- A W product in a same-day hospitalization leaves the duration undetermined:
  the same-day formula and the subtraction disagree.
- An adjusted duration of 0 or less for a multi-day hospitalization is
  undetermined, also for ward exclusions alone.
- A ward exclusion outranks a W "unresolved" and a W exclusion outranks a ward
  "unresolved". An undated procedure is bounded by its attributed stay, else
  by the episode.
- Classification uses exactly the catalog release the answer cites; the
  release and catalog checksums are part of the answer's sources. A bundle
  without product catalogs answers `product_catalog_unavailable`.

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
a private R2 bucket under SHA-256 keys; source configuration, schedules, runs,
observations, artifact references, source cursors, state revisions, and audit
records live in SQLite. The object store is not a database and contains no
mutable watcher-state object or provenance sidecars. Immutable control-database recovery images are distinct
from source cursors.

Issue #21 turns verified change records into pinned manifest changes, logical
diffs and immutable artifacts. Automatic selection runs downstream of the
watcher with a read-only archive credential that the watcher does not hold; the
watcher receives no GitHub credential. The "CI never follows latest" rule is unchanged.
Details in [data-sources.md](data-sources.md).

### Automatic Routine Data Activation And Failure Alerts (2026-10-05)

The owner explicitly confirmed automatic activation for validated routine
data-only updates, superseding the earlier human-approved-per-update policy
and the conflicting #21 comment. A reviewed policy governs supported source,
layout, applicability, integrity, provenance, compatibility and runtime gates.
New base acts, unexplained logical changes, licence changes, unsupported layouts,
ambiguous applicability and legal/source disagreement stop for investigation.
Parser, schema, application, infrastructure and promotion-policy changes remain
reviewed code changes. No individual approval is needed for a passing routine
candidate; an auditable data PR or change record remains mandatory.

Failed parsing or any other processing/publication/activation gate retains the
last known-good compatible release. Failed post-activation checks restore the
complete previous set. Application-activation checkpoints advance only after
durable verified success, independently of successful watcher capture.

Each failed candidate produces an actionable Telegram notification using the
existing Kuma notification destination, with a separately identified data-update
event. Include bounded source/change/candidate identity, failing stage, stable
error code, allowlisted expected/observed checks, retained or rolled-back release,
and a private diagnostic receipt/workflow link. Do not send arbitrary exception
text, stack traces, source payloads, medical queries, encounter data, credentials,
signed URLs or local paths. Secrets stay in the trusted notification boundary;
ordinary build and pull-request jobs do not receive Telegram or production keys.

The incident and delivery intent must be durable before sending. Retry delivery
with bounded backoff, deduplicate by candidate and failure identity, and report
delivery failure separately; notification failure never changes activation
success/failure or loses the incident. Distinct failed candidates need alerts
even when a Kuma monitor is already down. Watcher pushes carry only `completed`
or a fixed `failed: <category>` message (2026-10-20), which names the failed
capture class but does not satisfy this detailed per-candidate alert contract. #21 owns implementation and actual Telegram delivery evidence.
No unattended processing or notification integration is enabled by this record.

### Validation-Stage Scope And Deferred Automation (2026-10-06)

The owner reprioritized practitioner value over unattended-release infrastructure.
Merge the useful completed subset of #140: pinned S3 materialization for the
existing builder, scoped acceptance repairs and Chromium test-startup reliability.
Preserve the rest on its branch and defer it in #141, rather than merge unused
release-authority, OCI overlay, processing-journal and signing prototypes.

The 2026-10-05 automatic-activation contract above remains the safety contract
if automation resumes, not a validation-stage delivery prerequisite. Validation
continues through the reviewed update process with exact artifacts, source and
privacy checks, compatible last-good rollback and explicit operator actions.
Next work is #126 and #25/#42 practitioner relevance and complete workflow-speed
validation; #34-#36 grouper work follows its encounter and legal-binding gates.
Keep dictionary and embedding experiments separate with unchanged gold sets.
#19 and #21 are not closed as delivered by this split; #41 remains independent.

### Unattended Data Activation Resumed (2026-10-20)

The owner prioritized unattended operation, meeting the #141 re-entry gate. A
new source file that parses and passes every activation criterion reaches users
without manual work; the owner intervenes only when a file fails under the
current setup, while the last good release keeps serving. The 2026-10-05
contract above is the safety contract. Owner choices:

- Processing and activation run on the Mikr.us host, in a processor container
  separate from the watcher, with its own read-only R2 key. The watcher never
  gains activation authority and no host deploy key enters GitHub.
- ICD-10 (RSK `00CD10`) and ICD-9 (CZS) activate first. JGP and grouper data
  from BAW zarządzenia follow after shadow evidence, under the same criteria.
  Retroactive amendments always go to the owner.
- Data-only activation does not wait for #41. Data bundles activate with R2
  `deployment-receipts/` and the existing checksums; code images stay
  owner-deployed and #41 still governs them.

Delivery is phased: failure categories in watcher pushes, then a shadow
candidate builder that evaluates and reports without activating, then
activation. A new terminology release is added beside the previous one, which
closes at the new validity start, so earlier service dates keep their release.
Licence approval is per source class: the reviewed policy file pins the
publisher's licence statement, and a reworded statement goes to the owner.
Users get the latest release unless a question carries a date; a dated
question uses the release in force on that day (owner, 2026-10-21). A CZS
ICD-9 generation governs from its earliest changed procedure date, never later
than its publication day, with backdating bounded by the policy (92
days, owner-confirmed 2026-10-21). For phase C (owner, 2026-10-21) the
processor's R2 key is read-write and also writes the receipts, activation
outcomes use a second `data-activation` Kuma monitor, the live bundle's
manifest is authoritative (a reviewed release may not drop a live release),
and activation is enabled per source class through a host allowlist that
starts empty. A failed start is retried once. Criteria and phases are recorded in
[#141](https://github.com/krzysztofbury/minos-health/issues/141#issuecomment-6298146114).

The owner already applied the custom GitHub OIDC subject template. Hosted
non-AWS [attempt 2](https://github.com/krzysztofbury/minos-health/actions/runs/37372958134/attempts/2)
passed, proving observed claim shape only. It neither repairs the old archive-reader
IAM trust nor establishes cryptographically verified provenance or activation.
Do not silently roll back this change or provision IAM/KMS/signer/host services.
Future live changes still require exact reviewed plans.

### Public MCP Server For Agents (#216, 2026-10-22)

The owner asked for an MCP server so agents and LLMs can do what the site
does: anonymous with a rate limit, everything in v1, MCP Python SDK 2.x, and a
documentation page. Owner choices: the Gruper is in v1; patient data needs no
detection beyond the structured input (the stay has no name, PESEL or address
field, nothing is stored, logs carry no arguments, and what a user sends
through their own LLM provider is between them); load is accepted until
traffic shows otherwise.

- `POST /mcp`: stateless Streamable HTTP with JSON answers, served by the
  reference API process. Twelve read-only tools call the site's own HTTP API in
  process, so validation, `busy`, bounds and data match the pages: search,
  code detail, classification browsing, WHO ICD-10/ICD-11 rows, JGP groups,
  lists, catalog sections and two-date comparison, case dictionaries, the
  Gruper (summary by default, `detail="trace"` for routing traces) and source
  status. Two resources and two prompts guide a coding workflow.
- Contract from librus-mcp: typed outputs, closed error codes without input
  echo, `LIMIT` above 256 KiB instead of a cut, titles stripped from schemas
  (catalog 17.5 KiB), same JSON in text and structured content.
- Free like the site: RSK ICD-10 is non-commercial and must not back a paid
  feature. Every result carries the notice and its release.
- The edge allows 60 requests per minute per client (`CF-Connecting-IP`,
  burst 30); deployment verification lists the tools and runs one search.
- `/dla-agentow` is generated from `tools/list`, in Polish with the English
  tool reference agents see; a test validates its examples against the schemas.
- Measured on the live bundle: API anonymous memory 271 MiB with MCP (245
  without), under the 512 MiB limit.

### ICD-11 PL Search And WHO Maps (#53, 2026-10-22)

The owner resumed #53, reversing its deferral: ICD-11 is still not an NFZ
settlement input, but the Polish translation and the WHO ICD-10/ICD-11 tables
help coders prepare for it. RSK `99ICD1` (ICD-11 PL, resource version 002)
publishes the MMS 2026-01 export and three map workbooks. Terms come from the
RSK description and the WHO translation agreement with the Ministry of Health
(TR/24/013) and the WHO ICD-11 licence: the translation is CC BY-ND 4.0,
pre-released for consultation, not made by WHO, with the English version
binding; software may incorporate ICD-11 if every item keeps its code, title
and WHO URI with attribution and no adaptation.

- Search covers titles, inclusions and index terms, reproduced word for word.
  Definitions and exclusions are shown, not searched. ICD-11 stays out of the
  project synonym mechanism; Minos adds no ICD-11 text.
- The ICD-11 search filter starts unselected (owner); every ICD-11 page and
  the dictionary carry the pre-released, not-in-force and not-for-NFZ notice,
  the WHO disclaimer, citation and URI.
- The maps are WHO's own ICD-10/ICD-11 tables, published by CeZ in the RSK
  `99ICD1` resource. Minos republishes them verbatim, row for row with their
  source row numbers, no match type and no filled gaps, attributed to WHO and
  RSK. Clause 1.2.4 of the WHO licence requires a separate agreement for
  producing mappings or crosswalks; Minos produces none, so the clause does
  not apply, and Minos never presents the tables as its own. Official
  adoption of ICD-11 in Poland would not change these terms. ICD-10 pages show the
  10-to-11 rows, ICD-11 pages the 11-to-10 rows; the tables' ICD-10 side is WHO
  2019, not the Polish 2008 edition.
- The RSK watcher keeps `99ICD1` metadata-only. The four files were downloaded
  once with the owner's approval and go to R2 with the operator key. The
  processor reports `manual icd11 <version>` on `data-update` while a current
  `99ICD1` XML or map file (RSK file UUID and size) is not pinned live. ICD-11
  is not activated automatically; WHO releases yearly.
- Measured on the live dataset (workstation, 2026-10-22): the database grows
  from 425 to 676 MiB of the 1 GiB bound, a build takes 148 s with 187 MiB
  peak memory, and API anonymous memory stays at 245 MiB (242 without
  ICD-11). Terminology history now has about 350 MiB of headroom.
- Search ranks ICD-11 extension codes (chapter X, every code starting with X,
  17,159 concepts) after all stem codes in each match stage, because WHO does
  not allow them alone; they stay findable and an exact code lookup is
  unchanged. ICD-10's own X00-X59 codes are not affected (owner, 2026-10-22).
- ICD-11 blocks and windows are tree levels: titled containers are listed and
  expanded like codes, while untitled containers (all of ICD-10's) stay
  flattened. No listing then exceeds 299 entries, where flattening put up to
  2,767 codes under chapter X; a code page lists a block's members under its
  title, since a block has no code and no page.

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
backup: mutable control SQLite is backed up consistently to R2, raw bytes are
stored directly in R2, and complete empty-host recovery is tested.

A managed application runtime can be reconsidered only if measured
availability, capacity, or operational burden exceeds recorded limits.

### Object Storage On Cloudflare R2 (#200, 2026-10-19)

The owner moved the archive from AWS S3 to one EU-jurisdiction Cloudflare R2
bucket and retired Minos's AWS account resources. Five Terraform stacks, several
IAM roles and permission boundaries, an unused GitHub OIDC role, SNS, a budget
and a state bucket carried a 1.31 GB archive; Cloudflare already ran DNS, the
tunnel and the edge. This supersedes the AWS parts of this document.

Integrity no longer rests on S3 versions, which R2 does not have:

- keys embed the SHA-256 of their bytes, as they already did on S3;
- writes are create-once (`If-None-Match: *`, atomic on R2) and carry
  `x-amz-checksum-sha256`; a wrong checksum is refused;
- bucket lock rules refuse overwrites and deletes: 365 days for `blobs/` and
  daily, weekly and monthly recovery images, 30 days for run images, which a
  31-day lifecycle rule then expires;
- every read verifies SHA-256 and size; a recovery download is pinned with
  `If-Match` on the ETag that HEAD verified.

R2 `CopyObject` ignores `If-None-Match` and drops the checksum, so the trusted
recovery copy is a pinned, verified download plus a create-once upload. R2
answers `501` to `x-amz-expected-bucket-owner`, so the account is pinned by the
endpoint instead: configuration accepts only an R2 account S3 endpoint. Tokens
are bucket-scoped read/write: the bucket lock, not the token, provides the
no-delete guarantee that the AWS put-only policy gave. CI holds no object-store
credential. The capability evidence is in #200.

## Infrastructure Ownership

- The Cloudflare dashboard and API own the zones, tunnel routing and the R2
  bucket with its lock and lifecycle rules; changes are owner-approved and
  recorded in issues. Minos keeps no Terraform state.
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

`status.minoshealth.pl` publishes coarse service state only. The Kuma login and
administration paths on that host sit behind Cloudflare Access for the owner
(2026-10-20), because the tunnel route covers the whole host; see
[deployment](deployment.md#cloudflare-and-domains). Detailed watcher state
remains private, and issue #54 remains the future private detailed admin
portal. Since Kuma shares the host, it cannot detect or notify for a total
Mikr.us host, network, or power outage. This limitation is accepted for now.
Docker logs are bounded locally. CloudWatch is not part of the runtime.
Selection of an external error-reporting service remains open and must pass a
privacy review before receiving events.

Local application secrets live only in ignored `.env` files; local cloud
credentials stay in external profiles or keychains. Production secrets are
host-managed files restricted to root and the pinned numeric UID or GID of only
the required service, then mounted read-only into that container. Recovery
copies stay in the password manager. GitHub stores no object-store credential.
The bucket-scoped R2 watcher key is an explicit external-host secret and never
enters Git, `.env`, GitHub, or an image.

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
