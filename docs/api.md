# Validation Reference API

## Pinned concept detail

```sh
curl --fail https://minoshealth.pl/api/v1/concepts/icd9-pl/icd9-czs-5-90-gen-93-e3c744634e68/00.121
curl --fail https://minoshealth.pl/api/v1/concepts/icd9-pl/icd9-czs-5-90-gen-93-e3c744634e68/00.121/jgp/2026-10-02
curl --fail https://minoshealth.pl/api/v1/concepts/icd9-pl/icd9-czs-5-90-gen-93-e3c744634e68/00.121/jgp/2026-10-02/1a/pages/1
```

Select a release from `/api/v1/releases`. The contract is
`GET /api/v1/concepts/{system}/{release_id}/{code}`, optionally followed by
`/jgp/{YYYY-MM-DD}` or `/jgp/{YYYY-MM-DD}/{1a|1ae}/pages/{1..20}`.
Systems are `icd10-pl` and `icd9-pl`; codes are exact uppercase identifiers,
at most 16 ASCII letters/digits/dots/hyphens, starting with a letter or digit.
The 1-80 character release ID is lowercase letters/digits/hyphens.

The `api: 1` answer identifies `dataset_id`, `database_sha256`,
`transform_version` and `release` (version, checksum, URL, publisher, licence,
notices and separately named source dates). `concept` retains exact source code,
lookup code, kind, all descriptions and source attributes. `parents` and
`children` are direct source-local relations, including uncoded containers,
with source text, ordinal and explicit `contains`/`is_a` type. They are not
flattened or inferred from code prefixes. `supporting_contexts` independently
attributes exact-code workbook evidence without overriding CZS titles/status.
Supporting-only releases and codes cannot become canonical concepts.

An undated answer has `jgp: null`. A dated answer keeps the same dictionary
release and selects attachment 9 at the bundle's reviewed knowledge horizon.
It reports that horizon, whether the service date exceeds it, and membership
provenance separately for each catalog. Membership is an explicit source-list
reference, not encounter eligibility, treatment advice or settlement applicability.
Absent catalog coverage is labelled; absent coverage for every selected catalog
returns `404 no_ruleset_for_date`. A different terminology binding returns
`409 context_release_mismatch`; equal codes never authorize a cross-release join.
Each covered catalog supplies `page`, `page_size: 100`, `total`, and `groups`.
Use the explicit catalog/page route to continue; absent pages return 404.

`rank_evidence` remains release-scoped, with the existing provenance and
`ruleset_binding: not_bound`. `applicable_to_service_date` is null even for a
dated answer. Zero is an explicit source rank; a missing/unselected rank stays
null. Dictionary source status and dates do not establish legal applicability.

One connection lease and the existing 1 second / 2,000,000 SQLite-operation
budget cover the complete lookup and JSON encoding. Limits: 128 descriptions,
64 attributes and 400 direct neighbours per direction per source, at most 16
manifest sources, and 1 MiB UTF-8 output. Overflow fails closed with
`422 limit_exceeded`; no partial concept is returned. Deadline returns
`504 query_timeout`, pool exhaustion or more than 16 requests in flight
`429 busy`, unavailable/altered bundle
`503 unavailable`, and ambiguous JGP selection `409 ambiguous_ruleset`.
Unknown/mismatched release, system, code or malformed date returns 404.
Errors use `{"api":1,"status":"error","error":{"code":"..."}}` at the API.
All responses are `no-store`. The edge rejects query strings with 404; the
direct API returns `400 queries_must_not_be_in_urls`. Request logs contain the
fixed route category, never the supplied release, code or full URL.

## Search and existing reference routes

This is bounded public ICD-9 PL validation, not the complete product API.
Queries are POST JSON bodies so they do not enter request URLs. Do not submit
patient data. No account, API key or external search service is required.

```sh
curl --fail https://minoshealth.pl/readyz
curl --fail https://minoshealth.pl/api/v1/releases
curl --fail https://minoshealth.pl/api/v1/search \
  --header 'Content-Type: application/json' \
  --data '{"system":"icd9-pl","release_id":"icd9-czs-5-90-gen-93-e3c744634e68","query":"A01","limit":10}'
curl --fail https://minoshealth.pl/api/v1/search \
  --header 'Content-Type: application/json' \
  --data '{"system":"icd9-pl","release_id":"icd9-nfz-5-90-main-6c607dd5e564","query":"badania moczu","limit":10}'
```

The two NFZ releases remain separate: CZS contains complete code/status/date
evidence without a hierarchy; the workbook contains explicit containment and
context without overriding CZS or implying billability. The API never silently
selects a latest release or joins these sources.

Each listed release carries `valid_from`, `valid_to` and `in_force`. A release
governs from its validity start (as a Polish calendar date) until the next
release of its system starts; a day before every archived release falls to the
earliest. For each system the release in force today is listed first, and the
others keep manifest order. Releases of one system starting on the same Polish
day resolve to the latest: the later validity instant, then source creation,
then fetch time, then release identity (owner decision 2026-10-25, #235). Search,
the dictionary pages, MCP and the grouper use that one rule.

Search returns `api: 1` plus the shared CLI v1 envelope: selected release and
dataset identities, database/source checksums, search/morphology versions,
labelled match evidence, exact source attributes, complete ancestor context,
explicit morphological alternatives, spelling suggestions and a disclaimer.
Suggestions are not automatically executed. Empty results return HTTP 200 with
`status: "no_results"`, not an invented code.

Inputs are strict JSON, at most 4 KiB, with a 256-character/eight-token query,
an explicit system/release and 1-20 results. A `service_date` (`YYYY-MM-DD`)
may replace the release: the search then answers from the release in force on
that Polish calendar day, and a pinned release that does not govern the given
day returns 400. Unknown fields/types are rejected.
The optional language is `pl` (default) or `en`. Invalid requests return 400;
unsupported content types 415; oversized bodies 413; capacity exhaustion 429
`busy` with `Retry-After: 1`; elapsed search deadlines 504 `query_timeout`;
operation/output bounds 422 `limit_exceeded`; body-read deadlines 408
`request_timeout`; unavailable/changed artifacts 503. Errors expose only fixed machine-readable
codes, never the submitted query or exception details. The edge rejects URL
query parameters and private artifact paths with 404. All responses are no-store.

HTML detail routes render retry pages for capacity/deadline failures. See
[dictionary search recovery](validation/dictionary-search-recovery.md) for the
bounded worker wait and browser retry contract. Release metadata includes the
upstream licence notice when reviewed WordNet-derived phrases are selected.

## Dictionary Tree

`GET /api/v1/tree/{release_id}/{node}` browses one selectable catalog's source
hierarchy. `node` is `0` for the top level or a positive source node number
returned by an earlier tree or search response; paths carry no free text and
any URL query is rejected. Each answer lists at most 400 direct structural
children in source order. Anonymous RSK containers are flattened, while source
text and modifier nodes appear only as the selected node's `notes`.

ICD-9 PL has no CZS hierarchy, so `supporting_hierarchy: true` marks the NFZ
workbook as the tree, with `hierarchy_release_id` naming it. Labels, status,
dates and inclusion/exclusion text still come from the canonical CZS concept by
exact code; workbook-only nodes carry `canonical: false`. CZS codes without a
workbook position are listed separately in `unplaced` and never re-parented.
Unknown releases, supporting releases and non-structural nodes return 404.

`/healthz` checks API process liveness; `/readyz` also checks the verified dataset.
The website search is `/` (the old `/szukaj` redirects there); dictionaries are
`/slowniki`, and the metadata-only catalog remains `/zrodla`.

See [deployment scope and acceptance](validation/reference-web-rollout.md).

## Procedure Rank Evidence

`GET /api/v1/procedure-ranks/{icd9_release_id}/{code}` returns pinned SZP
procedure-rank evidence for an exact canonical CZS ICD-9 code. `recorded`
includes its source rank; `not_listed` and `not_selected` return null rank,
never an inferred zero. Responses retain algorithm/rank source identities,
checksums and reporting-month evidence, and explicitly state that a legal
service-date ruleset is not bound. The endpoint is reference-only, not
clinical priority or billability. No query strings are accepted.

See [komunikat ingestion](v0.0.1/komunikat-ingestion.md) for the schema-2/3
compatibility, source pairing and UI contract.

## JGP Catalog Base Points

All three routes take an explicit service date in the path (`YYYY-MM-DD`) and
resolve it to the one reviewed catalog release governing that date, as known
on the bundle's reviewed knowledge horizon (`knowledge_as_of`), so a response
is fixed per bundle and cacheable. Query strings are rejected like every other
route; SPEC's `?date=` shape became a path segment to keep that privacy rule
global and to give group pages shareable URLs.

- `GET /api/v1/jgp/{service_date}/groups/{code}` returns everything a group
  page needs in one call: the ruleset (service date, knowledge date and
  horizon, release identity, act number, BAW document, publication, service
  evidence, workbook checksum, the explicit unbound modifier state), the group
  (product code, name, procedural flag, chapter, notes, source row and its own
  applicability window and evidence), base values and scope markers. A
  schema 12 bundle adds `statistics` (#283): NFZ monthly settlement figures of
  the group, not rules, and the same for every service date. A report covers
  its month, the previous month and the same month a year earlier, and lists
  a group only where it was settled, so every covered month comes from the
  newest report that covers it (`report_month`, `published_on`), and a group
  absent from that report counts zero for that month. `provisional` marks a
  report less than 12 months after the month. `hospitalizations` is a
  `min`/`max` range because NFZ suppresses counts below five (one to four
  each); `points_ten_thousandths` is the settled points NFZ reports, not
  catalog base points. `mature` totals the latest twelve settled calendar
  months (`months` says how many), overall and per hospital level. A group
  never settled in the pinned reports has `months: []`; older bundles have no
  `statistics`.
- `GET /api/v1/jgp/{service_date}/sections` lists every chapter of the
  governing release per catalog with the number of groups applying on the
  date (possibly 0).
- `GET /api/v1/jgp/{service_date}/sections/{catalog}/{position}` lists one
  chapter's groups with code, product code, name, procedural flag and
  hospitalization base points (`hospitalization_hundredths` and exact
  `hospitalization_display`, null when the group has none).

A section position is the chapter's place in the source worksheet, stored at
build time; chapter headings are unique per sheet. It names the same chapter on
every date of a release, so section URLs stay valid when rows change at
2027-01-01. A chapter with more than 500 groups fails with 503 `unavailable`
rather than returning a truncated list (the largest reviewed chapter has 97).
This replaces the issue's paginated `?section=` list.

`ruleset.service_date_after_knowledge` is true when the service date is after
`knowledge_as_of`: the answer is what the bundle knows, and a later act it has
not reviewed may apply.

Every value is a catalog base point without modifiers:
`points_kind: catalog_base_points_without_modifiers` and `points_label:
"punkty bazowe wg katalogu, bez modyfikatorów"`. Points are integer
hundredths (`amount`) with an exact decimal `display`; financed stay is a day
count. No response carries final points; `ruleset.modifiers.binding` is
`unbound` until #46 binds MODTAR, and #35 must refuse final calculation in that
state. Attachment 1a publishes no AOTMiT tariff flag, so none is returned.

| Status | `error.code` | Meaning |
| --- | --- | --- |
| 404 | `no_ruleset_for_date` | no reviewed release covers the date; no other release is substituted |
| 404 | `group_not_on_date` | the code has no catalog row on that date |
| 404 | `section_not_on_date` | the catalog has no chapter at that position |
| 404 | `no_jgp_catalog` | the deployed bundle contains no JGP catalog |
| 404 | `not_found` | malformed date, code, catalog or position |
| 409 | `ambiguous_ruleset` | reviewed releases overlap without supersession |
| 409 | `known_on_beyond_bundle` | defensive: the knowledge date exceeds the bundle horizon |
| 400 | `queries_must_not_be_in_urls` | a query string was sent |

See [JGP catalog ingestion](v0.0.1/jgp-catalog-ingestion.md) for the release
evidence, row windows and knowledge horizon.

The same data is served as HTML pages under `/jgp` (`GET` only, no query
strings): `/jgp/{service_date}`, `/jgp/{service_date}/{code}` and
`/jgp/{service_date}/zakres/{position}`, where `position` is the 1-based
"Zakres świadczeń" column of the reviewed layout. `/jgp` and `/jgp/{code}`
redirect to today in Europe/Warsaw. Page errors use the same cases as the API,
rendered as uncached HTML; see the
[JGP page decision](v0.0.1/decisions.md#jgp-group-browser-pages-2026-10-03).

### Dated condition reference (combined #32 / #46 branch)

Group answers add `conditions`. Its status is `fully_interpreted` only when
every stored node of the group is proposed. `nodes`, `roots` and `selectors`
are empty for `not_yet_interpreted`; `source_text` retains the original clauses
and cited cell evidence. Separate roots do not imply a conjunction. These are
reference fragments, not executable encounter qualification. Older base-only
bundles return `not_in_bundle`; uncovered contexts return `context_not_covered`.

- `GET /api/v1/jgp/{service_date}/lists/{catalog}/{identity}/pages/{page}`
  returns at most 100 physical member occurrences, original code/title text,
  source rows, marks and exact dictionary correspondence. Repeated cells remain
  separate evidence. Identities are local to the selected release and catalog.
- `/jgp/{service_date}/listy/{catalog}/{identity}/{page}` renders that page.
- Existing `/slowniki/icd-9/{code}` and `/slowniki/icd-10/{code}` remain intact.
  Additive `/slowniki/{dictionary}/{code}/jgp/{service_date}` pages show dated
  membership: each group with its catalog base points and source conditions,
  opened by default up to six groups. Further membership pages use
  `/jgp/{service_date}/{catalog}/{page}` after the code-page path. Changing the
  date resets membership pagination. Dated variants are `noindex, follow`.

Each membership group carries its catalog `name`, `procedural`, `base_values`
(amount and exact `display`, base points without modifiers) and the source
`conditions` clauses. A group with no catalog row on that day has `name: null`
and empty `base_values`, never a zero.

Reverse lookup includes explicit group-local lists and lists reached through
stored condition selectors, including unsupported source evidence. It never
expands classification families or code prefixes. Its coverage is not an
eligibility determination, and absence is not proof of exclusion. Codes with
`not_in_reference` correspondence remain visible but get no fabricated concept
link. List members, conditions and reverse links use the same selected day.

Each answer carries the exact characteristics release, worksheet, act, source
checksum, applicability evidence and reviewed knowledge horizon. An uncovered
dated ICD context returns uncached 404 `no_characteristics_for_date`, never
latest. Undated code pages with a today's membership section are `no-store`;
explicit dated pages are cacheable for the immutable bundle. A group present
only in characteristics has `product_code: null`, no base values and
`catalog_row_status: not_on_date`, not invented catalog data.

Schema 6 stores pinned MODTAR source rows and optional research candidates.
`ruleset.modifiers.review_candidate` records the explicitly selected source and
`pending_legal_review` evidence when present. **Binding remains `unbound`.**
The candidate is neither an approved compatible binding nor a set of eligible
modifiers. See [the modifier review gate](v0.0.1/jgp-modifiers-ingest.md).
