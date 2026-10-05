# Search Improvements And Review Gates

## Opt-In Profile

Schema 8's `polish-folded-1` profile adds separately indexed Polish folded
tokens, explicit attachment 9 membership and a reviewed text-rewrite table.
Schema 2-7 bundles remain readable with their `lexical-2` metadata. Existing
migration checksums and morphology lemma keys are unchanged. New bundles report
`lexical-3`; original source labels, preferred descriptions and evidence are
not replaced by folded words or colloquial labels.

```sh
uv run python -m scripts.compose_conditions_manifest \
  --search-profile polish-folded-1 \
  --synonyms manifests/sources/search-synonyms-reviewed-v1.json \
  --output /path/to/new-search-manifest.json
```

The conditions selector remains the VPS-measured schema 6 selection and is not
activated (#32). Composition alone activates nothing.

### Production Activation On 2026-10-04

The owner approved activating `polish-folded-1` with the 13 reviewed rewrites
on the production catalog selector
(`manifests/sources/reference-catalog-public-validation.json`), without
conditions data. The owner signed off on the rewrite list at activation; the
entries keep their automated-audit provenance unchanged. The selector builds a
deterministic schema 8 bundle: dataset `f2eb5568...`, database `462715aa...`,
145 MiB versus 136 MiB for the schema 4 bundle it replaces. It has no conditions
reconstruction, so open-time validation stays small. Locally, application start
took 0.57 s (versus 0.44 s for schema 4) and a mixed six-query sequence had a
5.3 ms median and a 9.1 ms maximum (versus 2.8 ms and 6.4 ms). These figures are
not a production-load claim; the host deployment receipt and live probes are
the deployment evidence.

Without conditions data the membership tie-breaker is inactive (counts are
zero), so this bundle scores 35/44 top-1 and 40/44 top-5 in both engines, with
no baseline regressions. G10/G11 (`zawał serca`, `zawal serca`) rank I21 7th;
they reach the top five only with conditions membership, as in the 42/44 row
below.

## Matching And Privacy

- Exact code and exact description results retain priority.
- Reviewed, whole-phrase synonym rewrites are labelled with the source term.
  They preserve surrounding qualifiers and are neither chained nor used for
  spelling correction. At most three rewrites participate in a request.
- Literal token, morphology and substring results remain available. Rewrite
  results rank before them but may fill at most half of a page that literal
  matches could fill; further rewrite results follow the literal ones.
- Folded fallback adds results only when the original stages yield fewer than
  five, or fewer than the requested limit when that limit is smaller. It runs
  only for queries typed without Polish diacritics: a typed `ą` is deliberate,
  so `rąk` (hands) never retrieves `rak` (cancer) as a folded match.
- Folded results display `bez polskich znaków`; source strings remain intact.
- Explicit spelling suggestions still require user acceptance. The gold typo
  case is not silently corrected to improve a metric.
- Requests still use POST bodies. No query URLs, cookies, browser storage,
  query logs, model calls or external search calls are introduced.
- The complete search, including source selection and rewrites, retains its
  shared SQL/time budget. Membership selection deliberately avoids nested
  progress-handler contexts that could clear that budget.
- Membership is an optional signal. A missing Europe/Warsaw time zone or an
  ambiguous dated characteristics source drops the counts; the search still
  answers. Invalid stored characteristics manifests are an artifact error.

## Membership Is Not Clinical Frequency

The build projects only explicit selector/list connections and explicit list
contexts. It does not expand classification prefixes or manufacture descendants.
Each hit can show its literal code's group count and service date. Result links
retain that date. Earlier uncovered dates yield no membership signal.

A raw descending group-count boost caused a measured baseline regression:
highly specific diagnoses from broad lists displaced general pneumonia and
hypertension results. That version was rejected. Membership count is therefore
a tie-breaker after lexical ownership, prefix quality and code depth, not a
proxy for clinical frequency. The ambiguous appendix query remains unresolved.

Diagnosis pages can display up to 20 known procedures per catalog that share
explicit groups with the literal diagnosis code, with at most 20 group links
per procedure and visible truncation. All links retain the service date. Source
release compatibility is checked, and the page explicitly says this is neither
typical treatment nor hospitalization eligibility. No modifier or settlement
claim follows from that shared membership.

## Synonym Review Workflow

`manifests/sources/search-synonyms-proposed-v1.json` contains 13 proposals with
versioned identities, public citations and narrowly stated evidence. They are
not approved clinical synonyms. Proposed entries are preserved in dataset
provenance but never enter the runtime rewrite table.

To approve an entry, a real reviewer must confirm its terminology equivalence,
the exact pinned Polish dictionary wording and qualifier/technique boundaries,
then set `status=reviewed`, `reviewer` and `reviewed_on`. The source term rewrite
must not add codes or import WHO/US codes absent from the Polish release. Build
and read paths compare the complete approved table with the pinned manifest;
recalculated database hashes do not bypass the policy check.

Particular review risks are the shortened appendix phrase, device-versus-
procedure wording for joint implants, and translating the phacoemulsification
name to the actual Polish ICD-9 description. Synthetic fixtures use explicitly
synthetic reviewers and do not approve these real proposals.

The separately versioned `search-synonyms-reviewed-v1.json` now contains 13
source-audited entries. Its reviewer field explicitly identifies automated
terminology review, not practitioner validation. The pinned Wikipedia revisions
were fetched and their definitions checked against the exact Polish dictionary
wording. The practitioner shorthand in #125 supplies the shortened appendix and
hip-implant search phrases. Ambiguous appendix/device phrases are whole-query
only: mastoid, history, revision or device qualifiers cannot use those rewrites.
All entries are scoped to the evaluated dictionary releases. Build and open
validation also refuse wording absent from that pinned dictionary. Optional
fields are omitted at their defaults to preserve earlier schema 8 identities.

## Evaluation

`tests/evaluation/search-gold-v1.json` captures all 44 #126 cases, exact release
pins, baseline ranks and review status. Its service date is pinned to
2026-10-04. The evaluator checks every acceptable code in its selected release,
reports per-case IDs and category metrics without printing query text, and
distinguishes practitioner-reviewed from proposed regressions.

```sh
uv run python -m scripts.evaluate_search --bundle /path/to/bundle
uv run python -m scripts.evaluate_search --bundle /path/to/bundle --index portable
uv run python -m scripts.evaluate_search --bundle /path/to/bundle \
  --candidate-synonyms manifests/sources/search-synonyms-proposed-v1.json
```

The candidate mode is a hypothetical review aid, not approval or runtime
activation. It cannot be combined with `--gate-reviewed`. Ordinary reporting is
non-blocking; `--gate-reviewed` refuses practitioner baseline regressions only.
Unreviewed cases never become release gates merely because a target is reached.

Measured offline against the complete pinned source dataset:

| Profile | Top-1 | Top-5 | Baseline regressions |
| --- | --- | --- | --- |
| Previous search | 21/44 | 24/44 | none |
| Folded fallback only | 23/44 | 27/44 | none |
| Folded plus conservative membership tie-breaker | 23/44 | 29/44 | none |
| Hypothetical 13-proposal synonym evaluation | 35/44 | 42/44 | none |
| Active source-audited rewrites, auto and portable | 35/44 | 42/44 | none |
| Production selector without conditions data, auto and portable | 35/44 | 40/44 | none |

The active row is a real runtime measurement, with candidate mode disabled.
It meets the numerical top-5 target on the full conditions dataset; it is not a
claim of practitioner validation. The production row is the activated selector. The unresolved cases are G01
(ambiguous appendix ranking) and G09 (explicit spelling correction).

`Evaluate pinned offline search` is a separate manually dispatched CI job. It
composes the reviewed profile, fetches only allowlisted public endpoints and
checks all 14 exact source checksums/sizes before building the licensed bundle.
A changed, unavailable or redirected source fails closed, never updates a pin.
Both auto and portable reports use active rewrites with candidate mode disabled.
Only practitioner-reviewed baseline regressions are release gates; the 44-case
top-5 target and proposed cases remain reported. Uploaded artifacts contain only
query-free JSON reports, never source bytes or the generated dataset. The job
removes its inputs/bundle and cannot publish images or activate a selector.
Ordinary PR CI remains synthetic-only. The workflow must be present on the
default branch before GitHub permits manual dispatch; local command rehearsal
does not claim a completed hosted run.

## Live Timeout Investigation

This section records the 2026-10-04 investigation. The later
[2026-10-05 recovery policy](../validation/dictionary-search-recovery.md)
supersedes its 400 ms deadline and adds the reviewed dictionary subset.

On 2026-10-04, 44 sequential non-patient gold probes against the deployed
revision `3f62ca46dd2f5329ad595db212674b5c5878b5dc` reproduced a
`429 limit_exceeded` for G07 (267 ms end-to-end). The container was healthy,
limited to one CPU, and reported no cgroup CPU throttling. A separate read-only
profiler found 139,000 SQL operations for that path, far below the two-million
operation cap: the reproduced failure was the wall deadline, not operation
exhaustion. The warm profiler did not reproduce the timeout, so host scheduling
or cold-cache contribution remains unconfirmed.

The substring fallback now materializes matches from the narrow covering text
index before loading source documents and graph owners. On the deployed
dataset the empty-fallback query used 114,000 operations instead of 137,000,
about 17% less work. Host wall timings varied substantially; they are not a
general latency guarantee. Search alone now has the owner-accepted 400 ms shared
deadline; the operation cap and other reference-query budgets stay unchanged.

Candidate code was exercised in a separate read-only VPS process on the live
legacy dataset, not injected into the serving process. Two sequential 44-case
runs had no errors, maximum 112.6 ms, and G07 took 5.1 and 8.1 ms. This supports
the bounded regression path, not concurrent-load suitability. Live activation
and production verification remain pending owner review.
