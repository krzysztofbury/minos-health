# Reviewed dictionary expansion and search recovery

Date: 2026-10-05. Issues: #125, #126 and the #42 latency boundary.
Status: implemented and checked locally; production rollout is pending.

## Production findings

The read-only investigation verified SSH authentication, the pinned host key,
expected host, Docker context and the running API before inspecting logs.
Production was running application revision
`169a1d806320e3e5f66beefbc826c894392b80b5`, with database SHA-256
`bbd4bd51597a12ca60a802ebad81d6d8c9ca88b6b0bf04ad98636a734dfba66c`.

- Two ordinary sequential ICD searches returned HTTP 429 `limit_exceeded`,
  taking 494.5 and 531.3 ms end-to-end. They were not a request burst.
- A bounded store profile on the VPS reproduced an ICD-10 `QueryLimitError`
  at 403.6 ms, with 380.6 ms in SQLite execution. This confirms the old 400 ms
  deadline as one cause. A separate ICD-9 sample completed with no results;
  not every failure has the same cause.
- The deployed database was schema 6, `lexical-2`, without the folded-token
  search profile or synonym mappings. The same verified database reproduced
  genuine missing results locally. Updating only the application cannot add
  the absent indexes or reviewed mappings.
- The sampled API log window contained 18 4xx ICD detail-page responses among
  684 detail requests. The old telemetry recorded only status classes, so
  these cannot all be classified as timeouts. Code inspection confirmed that
  capacity and deadline failures on those page routes returned JSON.

No production deployment, configuration mutation or production load test was
performed. Local measurements are not a claim about the VPS's new capacity.

## Before-implementation pair-programmer review

- **Safety, TigerStyle #6: Positive and negative space.** The complete external
  alias inventory moves `udar` toward heatstroke. The owner approved a reviewed
  subset rather than automatic activation of all 9,077 generated aliases.
- **Safety, TigerStyle #12: Full error handling.** Deadline, operation/output
  bounds and pool capacity shared HTTP 429. Failed group lookups could also
  produce the global "nothing found" message. Detail failures needed HTML.
- **Safety, TigerStyle #4: Paired assertions.** Search capability existed in
  code but not in the deployed bundle. Production readiness must check the
  selected profile, and artifact reads must validate reviewed mappings.
- **Performance, TigerStyle #14: Batching.** One home search fans out into
  three systems; overlapping suggestions can exhaust four immediate leases.
  Use cancellation and a bounded wait before refusing useful work.

## Implemented policy

The existing reviewed mappings remain active. Added mappings are:

- `zawał serca` to `zawał mięśnia sercowego`, preserving surrounding text;
- whole-query `wyrostek` to `wyrostek robaczkowy`;
- whole-query `endoproteza`, `endoproteza biodra`, and `endoproteza kolana`
  to the corresponding JGP procedure wording.

The first two are reviewed same-synset candidates from pinned `omw-pl:2.0`
through optional MIT-licensed `wn==1.1.1`. This is the Princeton-aligned
plWordNet 2.3 subset, not current full Słowosieć. The generator emits proposed
entries only. The full dictionary and `wn` are absent from query execution.
Archive checksum, synset identities, review evidence and licence notices are
preserved. Full upstream notices accompany bundle reviews and API metadata.

JGP device-to-procedure mappings are explicitly labelled **related procedure**,
not synonym equivalence or eligibility. They do not consume extra qualifiers,
infer implant history, or select another service date. Source names and exact
code precedence are retained. Proposed mappings do not enter runtime tables.

## Recovery contract

- Search has a 2-second aggregate elapsed budget. Other store stages allow
  1 second by default. The existing SQL-operation and output bounds remain.
  Nested budgets preserve outer deadlines and operation accounting instead
  of clearing the caller's SQLite progress handler.
- These are cooperative checks, not preemptive cancellation of arbitrary
  Python/native work. They do not promise a strict 2-second wall-clock ceiling.
- Four workers remain. At most 12 additional callers wait up to 500 ms for a
  lease (four until #296). Further callers receive 429 `busy` with
  `Retry-After: 1`.
- Elapsed deadlines return 504 `query_timeout`; operation/output bounds return
  422 `limit_exceeded`; slow request bodies return 408 `request_timeout`.
- At 500 ms, result cards explain that work is continuing. Transient 429 and
  unstructured 503 receive at most one automatic retry, respecting a short
  `Retry-After`. Long server delays require manual retry. Browser waits are
  bounded at 12 seconds. Timeouts do not trigger automatic duplicate searches.
- New searches cancel superseded requests and retries. Suggestions do not
  retry. Failed or uncovered searches do not masquerade as zero matches.
- ICD and JGP page failures offer HTML recovery actions. Edge-generated
  502/503/504 also render a branded retry page, retaining security headers.
- Logs add bounded status/error identifiers, not queries or exception text.

## Unchanged gold results

| Corpus and variant | ICD top-1 / 44 | ICD top-5 / 44 | JGP / 12 |
| --- | ---: | ---: | ---: |
| Deployed full bundle | 21 | 24 | 11 |
| Same full sources, prior reviewed mappings | 35 | 42 | 11 |
| Same full sources, reviewed expansion | 37 | 42 | 12 |
| Catalog-only comparison, reviewed expansion | 37 | 43 | 12 |
| Prior catalog-only full-dictionary spike | 36 | 43 | 12 |

No previously passing full-bundle top-5 case regressed. G10/G11 improve from
rank 2 to 1; G12 remains rank 1. G01 improves from rank 11 to 6 with full
conditions, versus rank 5 on the catalog-only comparison. G09 remains absent.
Whole-query negative probes stay unexpanded; this is not evidence that the
engine understands every medical qualifier. These remain development-set
results, not held-out clinical validation.

The prior embedding alternatives scored 24/30 and 25/30 top-1/top-5 on the
44 ICD cases, with peak RSS above 800 MiB. They remain on their spike branch.
Neither spike branch was altered by this implementation.

## Runtime and browser evidence

The local test used the packaged wheel and nginx artifacts, the rebuilt full
conditions bundle, API limits of one CPU / 512 MiB and edge limits of
0.5 CPU / 64 MiB. It mixed six successful dictionary/JGP queries and three
detail routes through real HTTP. The phases were bounded at 20 seconds and
1,000 requests per worker.

| Concurrency | Requests | Duration | p95 | Maximum | HTTP errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1,000 | 17.89 s | 31.93 ms | 80.10 ms | 0 |
| 4 | 365 | 20.02 s | 411.03 ms | 618.84 ms | 0 |
| 8 | 399 | 20.30 s | 696.29 ms | 921.12 ms | 0 |

No successful search unexpectedly returned zero results. The test intentionally
accepts complete results slower than 400 ms. These short sustained phases do
not establish a launch SLO or measure contention with the production watcher.

A separate forced-overload run reduced the disposable API to 0.05 CPU and sent
24 concurrent requests: one 429, nineteen 503 and four 504 responses. Restoring
one CPU restored successful search. Its API cgroup peak, including the browser
phase, was 200,634,368 bytes (191.3 MiB); this is not a peak measurement from the
earlier sustained phases. Full-bundle pool/index startup was about 5.75 seconds
locally, including validation. The earlier catalog-only dictionary spike's
startup and RSS are not directly comparable to this larger full bundle.

Real Chromium desktop/mobile checks covered actual expanded searches and
click-throughs, 800 ms loading feedback, transient and sustained 429,
unstructured 503, 504, long Retry-After, manual recovery, superseded responses,
true empty results, related-procedure labels, and query URL/storage privacy.
The detail-timeout visual used the actual HTML renderer; integration tests
separately exercised HTTP exception-to-page handling. Screenshots were visually
inspected. The existing complete Compose/browser smoke also passed dictionary
trees, keyboard navigation, source rules, dated reverse links and rank pages.
Stopping and restarting the disposable API verified edge error recovery.

## After-implementation pair-programmer review

- **TigerStyle #6:** restricted related-procedure entries to JGP and prevented
  related results from suppressing existing folded-name matches.
- **TigerStyle #12:** repaired the global status after a successful manual
  retry, cancelled superseded dictionary requests, and handled non-JSON edge
  failures. Actual overload testing exposed the edge HTML path.
- **TigerStyle #2: Bounded loops:** retained three rewrites, 500 reviewed
  entries, four workers, four waiters, one retry and finite browser waits.
- **TigerStyle #11: Warning clean:** full pre-commit checks passed, including
  Ruff, mypy, BasedPyright, Bandit, pytest, lockfile, assets and licence policy.
  The suite has 1,575 passing tests and 13 environment-dependent skips.
- **TigerStyle #4:** startup refuses a production bundle without
  `polish-folded-1`; reviewed aliases are checked against actual source labels.

No remaining implementation blocker was found in this review. VPS verification
of the new artifacts remains a release boundary, not a completed check.

## Reproduction and release boundary

Use `scripts/compose_conditions_manifest.py` to compose the pinned catalog and
conditions sources. It now selects the reviewed synonym inventory by default.
Rebuild from checksum-verified source bytes; do not mutate or reuse the legacy
schema-6 production database with the new application entry point.

Generate additional review candidates with:

```sh
agent-scratch --root /tmp/opencode -- uv run --extra dictionary-tools python \
  -m scripts.propose_wordnet_synonyms \
  --synset omw-pl-14112855-n --synset omw-pl-05537576-n \
  --system icd10-pl --output /path/to/new-candidates.json
```

Evaluate a rebuilt bundle using `python -m scripts.evaluate_search --bundle
/path/to/bundle`. Build the API and edge through the existing Dockerfiles and
`scripts/build_validation_edge.py`. The standard runtime acceptance entry is
`python -m scripts.smoke_reference_web`; additional recovery browser checks are
in `tests/browser/search-recovery-browser.mjs`. The local full-corpus load
harness and screenshots are retained in the ignored task evidence directory.

Bounded, query-free results are versioned in
`docs/validation/evidence/dictionary-search-recovery/`. No source archive or
generated reference database is committed. Deployment requires publishing the
paired application/edge images and rebuilt bundle, then checking the same
searches, clicks, overload recovery and resource behavior on the actual VPS.
