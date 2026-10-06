# Search relevance, safety and workflow baseline

Issues: #126, #25 and #42. One branch and PR. The owner later authorized merge
and validation deployment only if CI, capacity and preflight gates pass; no
production load test is authorized.

## Scope and evidence rules

Measure the shipped search before changing it. Keep the original 44 ICD cases,
12 JGP cases, reviewed rewrite inventory and ranking unchanged. Dictionary and
embedding experiments remain separate. Do not convert a spelling suggestion
into an accepted result or change targets to improve scores.

The only application change is the owner-requested suggestion quiet period:
`SUGGEST_DELAY_MS = 150` in `apps/web/search.js`, previously 220 ms. The owner
selected 150 ms after reviewing the separate 120 ms experiment below. A named
constant keeps the static asset explicit without adding deployment environment
plumbing. Enter and search submission remain immediate; ranking, rewrites,
filter preferences/defaults/reset and privacy disclosure are unchanged.

The hosted baseline uses main revision
`902496d36a5548779f5808e144f217f27a1c0ef9`, run
[37433519986](https://github.com/krzysztofbury/minos-health/actions/runs/37433519986).
Both offline engines report 37/44 top-1 and 42/44 top-5. G01 remains rank 6;
G09 has no returned acceptable code. The successful workflow is evidence of
execution, not practitioner acceptance. Reports are retained under
`evidence/search-workflow/baseline-hosted/`.

Extended hosted run
[37435838355](https://github.com/krzysztofbury/minos-health/actions/runs/37435838355)
at `3977c30759e0deeb202b86f19b45ba692ab02f56` passed with the same ICD scores,
12/12 unchanged JGP targets, and the separate safety observations below. Exact
fixture hashes and query-free reports are retained under
`evidence/search-workflow/extended-hosted/`. Local safety reports match the
hosted reports exactly in both engines; ICD/JGP timing samples are not identical.

The full reference selection has 32,609 concept rows, 3,627 versioned group
definitions and 119,078 characteristics-code evidence rows. It includes all
five pinned catalog releases and all three selected conditions releases.
Database SHA-256:
`84e1731e3ac6dc0512c4f461145c7f433cbd014aa439c16dfca37d50185e5ee8`.
Dataset identity:
`8b67585350698d8808890a32063e4689c47de2ebd369de3850c5d070ac07a1dc`.

## Separate safety observations

`tests/evaluation/search-safety-v1.json` is a newly authored source-based probe
corpus, frozen before its first evaluation. It is **not an independently
reviewed held-out set**: the same engineering session authored and evaluated
it, using known search-development context. Independent review and practitioner
dispositions remain required before promoting ranking or alias experiments.
No tuning against these probes occurs in this PR.

Source facts were checked directly in the checksum-pinned official XML,
separately from search output:

- RSK parent E10 versus E11, with relative modifiers .0 (coma), .2 (renal
  complications) and .9 (without complications).
- CZS generation 93 distinguishes total hip 81.51, partial hip 81.52, hip
  revision 81.53, primary knee 81.54 and knee revision 81.55. Exclusions on
  81.53/81.54 point to component-specific alternatives; source-name retrieval
  does not establish the appropriate code for an encounter.
- RSK H70.0 is acute mastoiditis, not K35 acute appendicitis. The shared
  colloquial word does not authorize rewriting longer qualified phrases.

The corpus cites the exact source URLs and release IDs. RSK source-derived
content retains CC BY-NC-SA 3.0 IGO constraints and the source translation
disclaimer in `manifests/sources/reference-catalog-public-validation.json`.
NFZ supplies no stated publisher licence. This is owner-authorized noncommercial
evaluation, not redistribution clearance, medical advice or an NFZ decision.

The evaluator records acceptable rank, all observed contradictory codes/ranks
within the returned top 20, suggestions separately, empty controls and failures.
It validates both acceptable and contradictory targets against exact release
pins before querying. Failure is never counted as a successful empty response.
A lexical negation match is an observed risk, not a clinical recommendation.
No clean top-5 score can erase contradictory lower-ranked results.

| Probes | Observed output in both engines | Limit |
| --- | --- | --- |
| S01-S04 diabetes type/complications | No hits; selected targets missed | Qualified retrieval remains a gap |
| S05-S08 joint and primary/revision wording | Selected targets at rank 1 | Source-name discovery, not encounter qualification |
| S09 mastoid versus appendix | H70.0 at rank 1 | One ambiguity observation |
| S10-S11 negation | S10 returns I23.3 and I24.0; S11 empty | No selected contradictory target, not clinical negation understanding |
| S12-S13 unrelated controls | No hits; S12 has one spelling suggestion | Suggestions are not accepted hits |
| S14 typo | No hits; one spelling suggestion | Explicit correction remains necessary |

There are no evaluator failures or selected contradictory codes in the returned
top 20. This checks only the enumerated contradiction inventory, not every
potentially inappropriate code. The probes' S01/S02 identifiers are local to
this fixture, not the practitioner engine scenarios S01/S02 in #126/#37.

Do not fix the diabetes misses by dropping type/complication qualifiers or
silently propagating parent labels into child matches. Any broader hierarchical
matching needs accepted-contract review and separate safety evidence first.

## Browser method

`tests/evaluation/search-workflow-v1.json` contains five scripted public-reference
discovery tasks: exact diagnosis, folded wording, qualified procedure, exact
JGP group and group-name morphology. All three source filters stay enabled.
At 60 ms typing cadence, three repetitions per task use desktop and mobile
viewports in real Chromium; at 180 ms, one repetition per task/viewport gives
40 observations per debounce run.
Mobile uses 80 ms emulated network latency and bounded transfer rates. It is
not a physical mobile device or a measured production network.

- Insert characters via Chromium's input protocol at nominal 60 or 180 ms cadence.
- Measure the last input through visible suggestions, including the served
  debounce. Absent suggestions are recorded, not silently successful. Intermediate
  renders do not retain a previous input's timestamp.
- Measure submit through all-source settled results with in-page timestamps
  and two animation frames. Failed/uncovered sources remain separate states.
- Record expected-target rank/completion and click to the actual detail page.
  Click timing includes up to 50 ms observation polling, plus animation frames.
- Separate first-pass and warm-browser observations. A first pass is not a
  cold server. Record the first text HTTP request after fresh API startup
  separately, before browser searches.
- Inject only local transport failures: one source busy once, a partial timeout
  and manual recovery. The production UI and abort/retry/rendering logic stay
  unchanged. Existing filter-preference browser acceptance runs separately.
- Verify POST-only search, no query URLs, query-free container logs and no
  query storage/cookies. No telemetry or application instrumentation is added.

The disposable runtime uses the actual packaged wheel, API and generated edge
artifacts. Limits match the accepted local validation boundary: API one CPU,
512 MiB, one Uvicorn worker/four leases; edge 0.5 CPU/64 MiB. Image identities,
bundle/fixture fingerprints, source revision, working-tree diff fingerprint,
browser version and local platform are recorded. Small samples are descriptive,
not launch SLOs, production capacity or watcher-contention evidence.

## Controlled 220/120 ms comparison

The 2026-10-06 runs use Chromium 152.0.7977.82 on the same Linux workstation,
identical harness/fixture/bundle hashes and clean tracked working trees. The
220 ms baseline is revision `da85bc6520c02c87084f3d7ff04ecffdf4d99063`; 120 ms
is `a024a64a6f7be5d4c63166d5f19cca98a9f8ee0b`. Reports are
`evidence/search-workflow/local/workflow-220.json` and `workflow-120.json`.
The earlier 30-observation `workflow.json` is supporting history, not the
controlled comparison. Image identities are recorded in each report.

Values below are min/median/max milliseconds, not percentiles or an SLO.

| Viewport/cadence | Suggestions, 220 ms | Suggestions, 120 ms | Submitted results, 120 ms | Detail click, 120 ms |
| --- | --- | --- | --- | --- |
| Desktop/60 ms, n=15 | 240.9/270.0/304.2 | 136.9/163.6/180.6 | 29.8/61.8/94.0 | 24.7/41.6/75.5 |
| Desktop/180 ms, n=5 | 240.8/275.1/281.7 | 145.0/171.0/176.0 | 30.6/56.0/93.9 | 34.3/42.9/54.7 |
| Mobile/60 ms, n=15 | 320.8/331.2/349.0 | 219.8/233.9/250.1 | 111.1/115.1/126.8 | 100.1/106.9/111.3 |
| Mobile/180 ms, n=5 | 321.8/324.0/335.7 | 221.9/227.1/228.1 | 119.4/125.9/127.1 | 101.3/104.4/108.2 |

Both runs complete all 40 scripted discoveries at target rank 1, with all three
submitted source states ready and service date 2026-10-06. Suggestions are
present for every final query. The 120 ms fast-cadence medians improve by
106.4 ms desktop and 97.3 ms mobile. The 150 ms starting UI budget is still
not met uniformly; immediate submission timing is a different measurement.

### Request-volume tradeoff and review boundary

| Per viewport | Suggestion requests, 220 ms | Suggestion requests, 120 ms | Submission requests in each run |
| --- | ---: | ---: | ---: |
| 60 ms cadence, 15 observations | 45 | 45 | 45 |
| 180 ms cadence, 5 observations | 15 | 276 | 15 |

At slower typing, 120 ms sends a three-source suggestion batch for nearly every
character after the minimum query length. That is **18.4x more suggestion
requests**, or 9.7x total search requests in the five-task phase. Desktop observes
all 276 suggestion responses as HTTP 200. Mobile cancels 261 intermediate browser
requests before response delivery; only the final 15 suggestion responses are
observed as HTTP 200. Cancellation prevents stale publication, not proof that
server work stops: API telemetry still records completion. Across workflow,
fault recovery and the separate filter suite, server search counts are 295 versus
817, all HTTP 200. Injected busy/timeout responses are local transport faults,
not server error telemetry.

Fresh-process first text HTTP requests are 21.0 versus 19.5 ms; API cgroup peaks
are 193.1 versus 194.7 MiB. These sequential samples do not establish sustained
concurrency, production capacity, cold filesystem behavior or watcher overlap.
Earlier four-session all-filter evidence already includes capacity errors;
do not infer that 120 ms is production-safe from these error-free sequential runs.

The 120 ms experiment is not the selected release value. The final 150 ms
choice and its capacity gate are recorded below. Do not tune deadlines/resources,
add caching or redesign the UI without a demonstrated boundary and review.

Transient busy retry, settled partial-timeout feedback, manual recovery, POST-only
queries, query-free container logs and no query/cookie storage passed in both
runs. The separate filter suite passed defaults, persistence/restart, fixed expiry,
empty intent, unavailable sources, reset, blocked/malformed storage, keyboard,
zoom and cancellation checks. Desktop/mobile results and partial-error screenshots
were visually inspected in real Chromium output; layouts and failure feedback
remain readable without horizontal overflow.

## Selected 150 ms and deployment gates

The owner selected 150 ms, then authorized commit/push/CI, production preflight
and merge/deployment only if all gates pass. The single-user run at
`cff5c4867c27f8fa210f81be299d85d3f3edc7ab` uses the same frozen harness and
evaluation bundle as the 220/120 comparison; see `local/workflow-150.json`.
It completes all 40 tasks at rank 1 with all submitted sources ready. Recovery,
privacy and the complete filter-preference suite pass, with desktop/mobile and
partial-error screenshots visually inspected.

| Viewport/cadence | Suggestions, min/median/max ms | Submitted results, min/median/max ms |
| --- | --- | --- |
| Desktop/60 ms, n=15 | 168.0/200.7/216.9 | 28.7/65.9/103.4 |
| Desktop/180 ms, n=5 | 163.1/203.9/211.6 | 29.0/60.7/70.9 |
| Mobile/60 ms, n=15 | 248.0/259.4/279.5 | 111.9/121.1/126.6 |
| Mobile/180 ms, n=5 | 250.3/256.0/263.9 | 113.0/115.6/122.6 |

The fast-cadence suggestion medians improve by 69.3 ms desktop and 71.8 ms
mobile against 220 ms. Slower typing still sends 276 suggestions per viewport,
not the baseline's 15. Server telemetry is 817 HTTP 200 responses; first text
HTTP is 20.7 ms and API peak memory 197.6 MiB. This sequential result is not
multi-user acceptance or an error-free production claim.

The production preflight observes revision `1d1e8b4` with database SHA-256
`0c4936ccc405388e66b17791eea5d86e0148054930ec3c187dc2b6a997ce261d` and
dataset identity
`60c3c2d3a12de1d84ef17644765869eb0309ccc5e1e8e7957eaf876622cc72e0`.
The evaluation selection differs in transformation and recorded lockfile identity,
not selected source facts. Do not substitute that evaluation artifact during this
code-only update. A local rebuild using the deployed selection identities must
match the production database exactly before capacity/deployment preparation.

Initial bounded four-browser comparison uses the exact deployed database and
the same one-CPU/512 MiB API. Four independent Chromium processes execute the
40-observation script against one shared API/edge per phase. The 220 ms phase
completes 160/160 tasks with no missing final suggestions and three server busy
429s alongside 1,033 HTTP 200s; query privacy and recovery pass. The initial
150 ms phase stops in two sessions on a missing JGP service date and establishes
no complete acceptance report. The harness formerly required a date even for
failed source states. Capture these failures explicitly and repeat both phases;
do not waive the final all-sources-ready/rank/completion gate or report the
incomplete run as success. The initial 220 ms report is `local/capacity-220.json`.

Initial host access is fresh dedicated-key SSH with the pinned host key. The
read-only state check reports healthy API/edge, 1,253 MiB available memory,
18.5 GiB free disk and the expected operational service identities. Package
visibility preflight is blocked because local GitHub authentication lacks
`read:packages`; restore that access before continuing production work.
CI and container/browser checks pass on `cff5c48`, but new harness/evidence
commits need their own final-head checks. No merge or activation is established
by these observations. Deployment must preserve the exact bundle, operational
services and prior rollback identities, and use reviewed exact main artifacts.

### Repeated capacity result: 150 ms fails the release gate

The repeated comparison at `54495b7` uses the repaired observation harness,
the exact production database and four concurrent Chromium processes. Both
phases record all 160 observations with no browser execution errors; failed
sources are retained rather than hidden or counted as successful empty results.
Reports: `local/capacity-220-repeat.json` and `local/capacity-150-repeat.json`.

| Outcome | 220 ms | 150 ms |
| --- | ---: | ---: |
| Correct target discoveries at rank 1 | 160/160 | 156/160 |
| Observations with all three final sources ready | 160/160 | 151/160 |
| Failed final source responses | 0/480 | 16/480 |
| Missing final suggestion displays | 1/160 | 16/160 |
| Server search HTTP 200 events | 1,031 | 1,838 |
| Server search busy HTTP 429 events | 5 | 1,297 |
| Browser-observed HTTP 503 responses | 0 | 4 |
| API peak memory, MiB | 210.6 | 219.8 |
| Readiness after browser load | ready | ready |

All nine incomplete searches occur at 180 ms typing cadence: eight desktop
and one mobile. Four lose the target discovery; the other five retain the target
but still have failed source responses. All 16 failed source outcomes are HTTP
429 after the normal submitted-search retry. Some additional busy responses
recover successfully; these do not erase failures. API log status counts and
browser wire counts differ because cancelled requests may still complete on
the server and Uvicorn can reject before application telemetry.

Desktop slower-typing submit-to-settled median worsens from 65.5 to 679.9 ms,
including failed outcomes. Suggestions remain faster when available, but that
does not satisfy co-equal correctness and performance requirements. No resource
limits, deadlines, source filters, ranking or retry behavior were changed.
Injected recovery, filter preferences, query privacy and final readiness still
pass; they are not substitutes for successful ordinary submissions under load.
Real screenshots show partial/all-source busy feedback and manual retry controls,
not a fictitious empty-results success.

**Hold merge and deployment of 150 ms.** The owner's authorization was
conditional on passing gates, which this run fails. Recommend restoring 220 ms
while retaining the evidence, or separately review a bounded request-pacing/
cancellation fix and remeasure before activation. Do not silently restore the
owner-selected value, waive the failed gate or compensate with unreviewed
resource/deadline changes. Actual-host capacity, sustained load and launch SLOs
remain unproved even for the passing local 220 ms phase.

## Remaining practitioner protocol

Run the same documented tasks with a coder in their actual incumbent workflow
and Minos. Counterbalance tool order; use equivalent independently reviewed
task variants rather than teaching the answer in the first tool. Record task
completion, elapsed time, incorrect selections, recovery and observed confusion.
Separate code discovery from final documented encounter qualification.

Collect aggregate dispositions keyed by case/task ID, not patient encounters,
raw medical queries, screen recordings or HIS screenshots. Review proposed
gold targets and safety contradictions before accepting clinical relevance.
There is no practitioner session or incumbent access in this engineering run.
Those acceptance items, faster-than-incumbent claims and actual-host SLOs stay
open. Conditional validation deployment does not authorize a production load
test, resource tuning or infrastructure change.

## Reproduction

Use `agent-scratch --root /tmp/opencode` for source downloads, builds and browser
profiles. Compose the full manifest with `--search-profile polish-folded-1`,
fetch its checksum-pinned inputs, and build through `minos data build`. Evaluate
with `scripts.evaluate_search` and `scripts.evaluate_search_safety` in both
`auto` and `portable` modes, plus `scripts.evaluate_jgp_search` for the unchanged
JGP fixture. The hosted workflow now retains all three query-free evaluations
and the source/fixture identity record; it never deploys or activates the bundle.

Build unique task-owned API/edge image tags through `packaging/api/Dockerfile`
and `scripts.build_validation_edge`. Run `scripts.measure_search_workflow` with
`--bundle`, `--output`, `--api-image` and `--edge-image`. Copy only query-free
reports and necessary screenshots outside scratch, then remove the task-owned
image tags. The measurement script tears down its unique Compose project even
after failure. Never publish the raw archives or generated database.
