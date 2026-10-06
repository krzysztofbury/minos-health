# Search relevance, safety and workflow baseline

Issues: #126, #25 and #42. One branch and PR, with no deployment.

## Scope and evidence rules

Measure the shipped search before changing it. Keep the original 44 ICD cases,
12 JGP cases, reviewed rewrite inventory and ranking unchanged. Dictionary and
embedding experiments remain separate. Do not convert a spelling suggestion
into an accepted result or change targets to improve scores.

The hosted baseline uses main revision
`902496d36a5548779f5808e144f217f27a1c0ef9`, run
[37433519986](https://github.com/krzysztofbury/minos-health/actions/runs/37433519986).
Both offline engines report 37/44 top-1 and 42/44 top-5. G01 remains rank 6;
G09 has no returned acceptable code. The successful workflow is evidence of
execution, not practitioner acceptance. Reports are retained under
`evidence/search-workflow/baseline-hosted/`.

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

## Browser method

`tests/evaluation/search-workflow-v1.json` contains five scripted public-reference
discovery tasks: exact diagnosis, folded wording, qualified procedure, exact
JGP group and group-name morphology. All three source filters stay enabled.
Three repetitions per task use desktop and mobile viewports in real Chromium.
Mobile uses 80 ms emulated network latency and bounded transfer rates. It is
not a physical mobile device or a measured production network.

- Insert characters via Chromium's input protocol at nominal 60 ms cadence.
- Measure the last input through visible suggestions, including the existing
  220 ms debounce. Absent suggestions are recorded, not silently successful.
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
open. No production load test, resource tuning or infrastructure change is
authorized by this PR.

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
