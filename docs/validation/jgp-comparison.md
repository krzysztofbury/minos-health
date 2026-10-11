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
or SLO evidence. Deployment and actual-host verification remain pending.

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

The current production revision is still `5458ef2230425de390751d9c07b5a77857cfd948`.
#104 and #157 remain open; neither merged candidate is the active deployment.
The next comparison change needs reviewed resource/performance evidence before
another activation. Request budgets, source validation and privacy remain intact;
no production load, stress or profiling was performed. Exact reports are under
`test-output/validation-deploy/e5194a664a8e50588c0f299dd557865cf04328e6/`.
