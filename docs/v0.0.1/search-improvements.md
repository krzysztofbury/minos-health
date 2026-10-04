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
  --synonyms manifests/sources/search-synonyms-proposed-v1.json \
  --output /path/to/new-search-manifest.json
```

The existing conditions selector remains the VPS-measured schema 6 selection.
This optional enlarged search artifact is not activated by composition or by
opening the PR, and it needs separate runtime/resource evidence before rollout.

## Matching And Privacy

- Exact code and exact description results retain priority.
- Reviewed, whole-phrase synonym rewrites are labelled with the source term.
  They preserve surrounding qualifiers and are neither chained nor used for
  spelling correction. At most three rewrites participate in a request.
- Literal token, morphology and substring results remain available. Folded
  fallback adds results only when the original stages yield fewer than five,
  or fewer than the requested limit when that limit is smaller.
- Folded results display `bez polskich znaków`; source strings remain intact.
- Explicit spelling suggestions still require user acceptance. The gold typo
  case is not silently corrected to improve a metric.
- Requests still use POST bodies. No query URLs, cookies, browser storage,
  query logs, model calls or external search calls are introduced.
- The complete search, including source selection and rewrites, retains its
  shared SQL/time budget. Membership selection deliberately avoids nested
  progress-handler contexts that could clear that budget.

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

The last row is not the shipped score. The unresolved candidate cases are G01
(ambiguous appendix ranking) and G09 (explicit spelling correction). The full
gold evaluation requires the private licensed bundle: ordinary CI exercises the
synthetic evaluator and the new profile's real container/browser paths, but does
not fetch that licensed dataset or claim a full-gold CI score. A private-bundle
CI report remains an explicit integration gap.
