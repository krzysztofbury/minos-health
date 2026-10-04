# JGP Home Search (#130)

The home page offers a third chip, **JGP grupy**. Requests use the existing
`POST /api/v1/search` privacy and capacity boundary with `system: "jgp"`,
`query`, and an optional `limit` (1-20). Responses identify the scheme as
`jgp-szp`. Unlike ICD requests, no release pin is supplied by the browser.
Client date, release and language overrides are refused.

The server determines today in Europe/Warsaw for every request, resolves the
governing catalog with `resolve_ruleset`, and filters individual rows by their
service windows. Uncovered dates return `status: "not_covered"` and an explicit
empty state, not another release. Missing timezone data is an unavailable
response. The result heading shows `stan na YYYY-MM-DD`; a knowledge-horizon
notice remains visible when today exceeds reviewed source coverage.

Results carry code, unchanged source name, catalog (1a/1ae), governing act and
the dated group-page URL. No points are shown in search. Group pages retain the
base-points-without-modifiers label. **Więcej opcji** opens the dated catalog;
the home page has no date, catalog or scope controls.

## Matching And Bounds

- Exact code, then code prefix, then name tokens, then pinned Morfeusz2/SGJP
  alternatives, then the same conservative Polish folding fallback as ICD.
- Stable source order breaks ties. Numbers, short operators and negation stay
  literal. Deliberately typed diacritics are not broadened.
- A read-only shared index is built once from verified catalog rows before
  readiness. It needs no characteristics, schema migration or query cache.
- Bounds: 20,000 total rows, 2,000 per release, 4,096 characters per name and
  8 MiB aggregate source-name text. Runtime scans only the resolved release.
- The 400 ms wall deadline covers resolution, analysis, matching and hydration.
  Existing resolver SQL-operation caps and four-worker HTTP capacity remain.
- Query text travels only in POST bodies. No new URL, logging or browser-storage
  path is introduced. Suggestions keep the response's dated URL across midnight.

## Acceptance And Known Vocabulary Gap

`tests/fixtures/jgp-search-gold.json` contains twelve fixed source-backed cases:
exact/prefix codes, both catalogs, names, morphology, no-diacritic input,
colloquial terms and a retained qualifier. On the pinned full conditions bundle,
eleven pass; `endoproteza` is an explicit known gap. It is not a source-name or
morphological equivalent of `endoprotezoplastyka`; no unreviewed device-to-procedure
synonym is added. `endoprotez` and the actual source name retrieve the relevant
groups. This is reference lookup, not encounter eligibility or treatment ranking.

Run `uv run python -m scripts.evaluate_jgp_search --bundle PATH --output REPORT`
for query-free identity, rank and timing evidence. Known gaps stay visible in the
report. They are not relabelled as passing cases.

`tests/browser/jgp-search-browser.mjs` exercises the real edge/API path on desktop
and mobile, native keyboard chip selection, Escape without query loss, result
navigation, dated links, overflow, POST-only search and absent storage. Its
`conditions` mode also follows group -> directional list -> ICD -> reverse group
links on the same date. The deployment verifier runs this mode for #32 bundles.

## Combined Rollout With #32

Use the already reviewed `reference-conditions-public-validation.json` selector
(schema 6), exact main-revision API/edge digests and the guarded validation
deployment path. Schema 8 search and MODTAR legal binding are separate approvals.
The full conditions bundle retains the previously measured long cold start and
120-second healthcheck start period. Record fresh container/browser evidence and
the deployment receipt before claiming the combined release is live.

### Verification On 2026-10-04

- Full pinned bundle: database SHA-256
  `bbd4bd51597a12ca60a802ebad81d6d8c9ca88b6b0bf04ad98636a734dfba66c`, dataset
  `cb0cc740f0cf509fd248c4a1f2bde8a3f94691f49d210ce56a2e56891930268b`.
- Local full-source Compose: 12.206 seconds to readiness, 172.7 MiB container
  memory, 48 gold probes at concurrency four, p95 19.275 ms, maximum 20.333 ms,
  no request errors. These are local measurements, not production-load claims.
- Real Chromium at 1440px and 390px: home search, keyboard interaction, dated
  group, directional list and reverse ICD links passed. Screenshots inspected.
- The full-source browser run exposed a 500px mobile page caused by a nowrap
  knowledge-horizon badge. Condition badges and source-row references now wrap;
  the code-page grid can shrink. Checks compare against the configured viewport,
  after fonts load, rather than a mobile viewport already expanded by overflow.
- Full repository hooks and Python suite pass. Pre/post TigerStyle review covers
  bounded work (#2), explicit unsupported states (#6), startup analysis rather
  than repeated source-name analysis (#14), and warning-clean code (#11).
