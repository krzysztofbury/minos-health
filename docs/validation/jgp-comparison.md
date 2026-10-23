# Pinned JGP Catalog Comparisons (#104)

## Selection And Source Contract

The catalog's comparison link selects its reviewed release and predecessor.
Both service dates and both release identities are explicit. A selected release
resolves at its own reviewed publication date and must match the requested pin;
an absent or inapplicable release is refused. This matters for 74/2026 and
102/2026, which both apply from 2026-07-01. Two service dates alone would select
102/2026 on both sides and hide the amendment.

Release labels quote the reviewed applicability-evidence prefix and publication
date. They are not inferred from internal release names. The base act remains
separately identified in provenance. Historical source links point to the
selected BAW document, not today's date-resolved group page.

The URL is:

```text
/jgp/porownaj/{dataset}/{before-date}/{before-release}/{after-date}/{after-release}/{scope}/{group}
```

`dataset` is the full verified dataset identity; `scope` is the SHA-256 of the
exact source scope label, or `wszystkie`; `group` is a source group code, or
`wszystkie`. A retired dataset returns 409, never a replacement dataset. Unknown
filters and uncovered dates fail explicitly. Reversing the selected sides
reverses additions/removals and old/new values.

## Comparison Boundary

The store reuses #20's `project_catalog` and `compare_facts`. Each service date
selects the applicable catalog row, including the deferred 2027 twins. Only
presence, definition, scope markers, notes and exact catalog values determine
changes; source-row movement and evidence-only window shifts do not.

The UI compares catalog base points without modifiers, not final settlement
points. Algorithm, qualification conditions and MODTAR changes are excluded
explicitly. A missing cell is shown as absent source data, never zero.
Scope membership on either side includes a group; scope renames are distinct
source labels rather than inferred synonyms.

The selected result is server-rendered and survives a shared URL or disabled
JavaScript. JavaScript changes public selection path segments only. Changed
values are visible first; unchanged values and repeated source details can be
expanded. Dates/releases, scope and group controls retain keyboard navigation.

Existing one-second/two-million-operation query budgets cover the comparison
and rendering under one bundle lease. Inventories, row populations and output
size remain bounded. Query strings are rejected; logs use the fixed `/jgp`
route label. No new request storage, analytics properties or dependencies exist.

## Acceptance Evidence

The real schema-10 bundle is database
`8ae874b13a7a8648980c133822b4955f38ef4996440f8759fc53d68c28c2e0ac`, dataset
`e141c2d6979b3adcb93335cafb4b2f8fbf28896e1c00b59e22b27cbb4f0ae9d2`.
At service date 2026-10-01, 74/2026 versus 102/2026 has 47 changed groups in
all scopes. The source's `kardiologia` filter yields D52A: same-day discharge,
one-day and two-day values of 3058, 4077 and 5096 points become empty catalog
cells. Hospitalization, financed stay and extra-day values remain unchanged.
This is catalog evidence, not an interpretation of the settlement consequences.

HTTP integration cases exercise exact values, additions/removals, same-day
retroactive releases with the same service start, deferred rows, scope membership
on either side, shared-response identity, source links and explicit refusals.
The installed API/edge browser driver checks desktop, laptop and mobile,
keyboard activation, no horizontal page overflow, shared links and no-JS
rendering. The real-source flow opens the comparison and selects kardiologia in
two actions from the catalog.

The combined local suite passed 1,943 tests with 39 environment/opt-in skips;
all pre-commit checks passed, including Ruff, mypy, BasedPyright and secret scans.
Pair-programmer review covered exact release refusal, dated twin selection,
source-link identity, unchanged request budgets, bounded rendering and privacy.
The earlier date-only selection gap was corrected before acceptance; no
remaining code findings were identified in those boundaries.

Private build, container and browser records are retained in
`test-output/issue-104/`. Local installed API/edge checks at 1 CPU/512 MiB
returned all six comparisons in 120-238 ms and four comparisons from two clients
in 400-573 ms. These are workstation-container observations, not host capacity
or SLO evidence. Deployment was still pending for this initial local evidence.

## First Activation And Rollback

PR #160's revision `31ef247143180648803baa64b6acc3b7b544cfd0` passed both main
CI and private publication. Its standard public readiness/rank/JGP/conditions
checks passed, but supplemental paced cardiology comparison requests returned
504 twice with an intervening 200. One API event recorded 1,224 ms. The complete
previous API/edge/bundle set at `5458ef2` was restored with the guarded rollback.
This is failed comparison acceptance, not a completed deployment.

A local profile identified unnecessary work in `project_catalog`: the web
consumer built every offline audit category and discarded all but schedules.
The follow-up adds a schedule-only projection mode for this consumer. It retains
the same bounded source reads, exact schedule construction, byte/row limits and
request deadline. The offline full diff retains all its categories by default.
No source validation, comparison semantics or request budget is weakened.

Matched disposable samples at 0.25 CPU completed all six requests in 258-590 ms,
versus 589-898 ms before the change. At the deliberate 0.125-CPU constraint the
baseline failed all six requests; the revised cardiology page passed all three
in 800-810 ms, while the all-scope page passed once and timed out twice. This is
reduced work under contention, not elimination of bounded overload failures.
Every successful response had the same complete HTML SHA-256 as its baseline
counterpart. The local profile reduced canonical JSON encodings from 24,015 to
5,984 for the two-release request. Full pre-commit checks passed; corrected
actual-host acceptance remains required before issue closure.

## Corrective Activation Also Rolled Back

PR #161's revision `e5194a664a8e50588c0f299dd557865cf04328e6` passed PR/main CI
and private publication and was activated at 2026-10-11T15:45:05Z. All five
pinned-source golden grouping cases passed at 1,110, 91, 358, 723 and 80 ms,
including current and original-act requests. The first cardiology comparison
returned 200, but shared-response verification failed and the subsequent paced
diagnostic pair returned 504 then 200. The comparison acceptance remains failed;
the local speed improvement is insufficient evidence of host reliability.

The complete previous `5458ef2` release was restored. The first rollback command
returned an uninformative remote-command failure after restoring Compose and
the API but leaving the edge in `created` state. SSH access remained available;
inspection found the old API healthy. A second guarded rollback started the
edge successfully. Final inspection confirmed both services healthy, unchanged
watcher/tunnel/Kuma identities and restart counts, and public 200 responses for
`/readyz`, `/`, `/jgp/2026-10-01` and `/gruper`.
Rollback diagnostic loss and partial edge restoration are tracked in #162.

After these rollbacks, production remained at `5458ef2230425de390751d9c07b5a77857cfd948`.
Acceptance for #104 and #157 was still pending. The next comparison change
required reviewed resource/performance evidence before another activation.
Request budgets, source validation and privacy remained intact;
no production load, stress or profiling was performed. Exact reports are under
`test-output/validation-deploy/e5194a664a8e50588c0f299dd557865cf04328e6/`.

## Approved Pre-readiness Cache

The owner approved complete preloading before readiness on 2026-10-11. Each
`ReferencePool` builds its own immutable catalog schedules and canonical facts
after checksum verification. Rows, definitions, scope markers and values are
nested tuples; the release map is read-only. Only the completed map is published.
The request still borrows the verified pool, checks artifact identity, resolves
both exact releases at their publication dates, selects date-applicable rows,
compares facts and renders under the existing one-second/two-million-operation
budget and two-MiB output bound. No per-URL cache grows with user selections.

Additional cache bounds are 64 releases, 2,000 groups and 4,000 dated rows per
release; each of the three source reads is capped at 20,000 rows and two MiB of
source values, and its schedule projection at two MiB. One release is built at
a time, limiting temporary populations independently of retained data. Retained
objects are conservatively charged by Python object size, including tuple and
Fact overhead and repeated references, against a 64 MiB ceiling. The total
preload deadline is 30 seconds, and each release's reader shares it (until
#198 five seconds in total and one second per release). Defaults for the
offline full diff remain unchanged. Limit failures close all readers and
prevent readiness, without lazy fallback or retries.

Fixed startup diagnostics report completion, release count, charged bytes and
duration. They contain no source labels, paths or request data. Existing grouper
warm-up starts after this cache is complete, avoiding two concurrent builders.

Regression checks cover startup-limit failures and reader cleanup, concurrent
different bundles with the same release IDs but different values, artifact
identity refusal after readiness, and correct HTTP comparisons with SQLite
authorization explicitly denying subsequent source-population reads. Existing
date-twin, retroactive release, unavailable-pin and offline-diff tests retain
their independent contracts. Actual-host results follow below.

Local installed API/edge acceptance with the real schema-10 bundle at 1 CPU and
512 MiB returned six comparisons in 14-43 ms. Two-client comparisons completed
in 16-24 ms; four-client mixed search/grouping/comparison work completed all eight
requests, with comparisons at 21-76 ms, search at 271-332 ms and grouping at
336-398 ms. The container cgroup memory peak was 279,293,952 bytes (266.4 MiB).
The first local preload completed in 263 ms. At a deliberate 0.125-CPU constraint,
all six comparison requests passed in 11-16 ms with byte-identical baseline HTML.
These bounded local samples do not establish public-host capacity.

Full pre-commit checks passed, including 1,950 tests and 39 environment/opt-in
skips; final focused cache checks and warning-clean type checks also passed.
Installed browser checks covered desktop/laptop/mobile, keyboard, shared URLs,
no-JS rendering and privacy; desktop/mobile screenshots were visually reviewed.
Pair-programmer review covered immutable ownership, retained/temporary bounds,
startup cleanup, unchanged offline defaults and all request-side guards. No
remaining findings were identified at those boundaries.

## Successful #164 Activation

Revision `bc4e9ef4d272d915f27c1260bf50f1aca16c243e` passed PR/main CI and private
image publication (run `38160761078`). It activated at 2026-10-11T18:06:56Z:

- API: `sha256:14a8ac1418a41ff44e9b96466d58fb5dbdb278b6fb04765e2d580d52e4a5b89f`.
- Edge: `sha256:5dae92fcca715537bfde09ccb8e0baf81f555f3b2b9ff639859d04a8b63a742d`.
- Database and dataset: the same exact schema-10 identities recorded above.
- Both GHCR packages and the repository remain private.

The first activation attempt failed and automatically restored healthy prior
API/edge services. The wrapper again lost its underlying error, tracked in #162;
the cause is unconfirmed. A single isolated, network-disabled startup probe on
the host completed in 134.8 seconds, including a 2.36-second comparison preload.
The guarded retry preserved the host error envelope and succeeded without
changing runtime limits, request budgets or activation guards.

On the successful fresh API, all five comparison releases preloaded in 3,463 ms,
charging 22,864,211 bytes against the 64 MiB ceiling. Grouping warm-up completed
five sheets and five product releases on attempt one in 8,278 ms. Restart count
was zero. All five first golden grouping requests passed (716, 115, 516, 256,
212 ms), including current and original acts.

The first public cardiology comparison and its repeat returned 200 in 178 and
110 ms; a follow-up pair returned 200 in 346 and 217 ms. Shared HTML matched
after decoding only Cloudflare's randomized footer email fields, verifying the
decoded address. Dataset mismatch returned 409 and query-string input 404.
Public Chromium comparison checks passed desktop/laptop/mobile, the two-action
kardiologia flow, keyboard, shared URL and no-JS rendering. Desktop/mobile
screenshots were visually reviewed. Standard readiness, rank, JGP, conditions
and search browser acceptance also passed.

Host diagnostics contained no server errors at the captured acceptance point
and no privacy sentinel; startup records contained only the allowed fixed fields.
Final remote inspection confirmed this exact revision, healthy API/edge, and
unchanged watcher/tunnel/Kuma identities and restart counts. These paced public
checks and bounded local concurrency results complete #104/#157 acceptance, not
host capacity or practitioner/ward validation. #42, #158 and #38 remain separate.
No production load or stress test was performed.

Exact receipts, public response evidence, startup diagnostics and screenshots
are retained under
`test-output/validation-deploy/bc4e9ef4d272d915f27c1260bf50f1aca16c243e/`.
