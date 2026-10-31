# Grouping Warm-up Recovery (#157)

## Failure And Limits Of The Reproduction

The 2026-10-09 production event recorded `failed:QueryTimeoutError` after
3,442 ms, with zero summary counters. The old implementation updated counters
only on complete success and stopped at the first failed reader. That event
cannot identify the failed release or establish how much was already cached.

Local fresh-start probes on 2026-10-11 used the deployed schema-10 database
`8ae874b13a7a8648980c133822b4955f38ef4996440f8759fc53d68c28c2e0ac`, read-only
containers, 512 MiB without swap, 64 PIDs and one CPU. Concurrent JGP pages,
current/older grouping and four search clients did not reproduce the timeout.
The four-search baseline warmed all five sheets and five product releases in
1,359 ms, with 31 successful requests and one bounded admission refusal.

A separate fault probe lowered the local CPU quota to 0.1 CPU, keeping four
search clients. This is deliberate CPU starvation, not the production CPU
configuration or a representative capacity result. The unmodified baseline
then timed out in `load_graph` while reading `directional_member` for the
102/2026 1a sheet. A matched one-request-per-client burst left baseline warm-up
failed after 1,400 ms. An experimental SQL aggregation also timed out and was
discarded. These observations demonstrate a contention failure path; they do
not retrospectively identify the production loader or prove its root cause.

## Recovery Contract

- Use the existing checksum-verified pool, immutable release caches, readers,
  SQL operation/elapsed limits, row caps and grouping request budgets.
- On a reader timeout, continue independent algorithm/sheet/product entries.
  After the pass, wait one second interruptibly and retry unfinished entries
  once. Inventories also get at most two attempts. The existing inventory caps
  allow at most 64 sheets and 64 product releases.
- Successful entries are never rebuilt by the retry. Validation, storage and
  operation-limit failures stop the warm-up without retry. An exhausted timeout
  remains failed; on-demand preparation retains its existing behavior.
- Set `PreparedSources.warmed` only after all entries succeed. Partial progress
  is reported accurately even if a later entry fails.
- Shutdown signals cancellation between bounded steps and joins the active
  reader before closing its SQLite pool. Readiness still does not wait for
  grouping warm-up.

`minos.http.warmup` emits `grouping_warmup_step` records with a fixed stage,
SHA-256 of the source release identifier, catalog, attempt, outcome and elapsed
milliseconds. Hash the manifest's release identifier to identify a sheet or
product release; the single algorithm is identified by its stage. No exception
message, SQL, input, query or local path enters these records. The summary keeps
`grouping_warmup`, adds `attempts`, and reports completed sheet/product counts.

## Verification

The matched CPU-starvation burst caused two real sheet-read timeouts. The fix
continued later sheets/products and recovered both on its second pass, completing
all five sheets and five product releases in 10,906 ms. All five pinned-source
golden cases then passed at 99-550 ms, without any additional static-input loads.
The overloaded startup searches still returned three 504s and one 429: recovery
does not turn insufficient CPU into an acceptable serving-capacity claim.

At the production resource limits with four search clients, the changed warm-up
completed in 1,412 ms on its first pass; 31 searches succeeded and one was refused
by admission. All five first golden requests passed at 15-61 ms without rebuilding
inputs, including the original May act. Local readiness was 8,322 ms. These are
workstation-container observations, not Mikr.us measurements or an SLO.

Integration fault cases verify transient recovery, bounded persistent failure,
no retry on invalid data, privacy-safe partial progress and a real HTTP answer
after recovery. A shutdown test blocks an active reader and proves the pool is
not closed underneath it. Existing query-budget tests retain the nested deadline
and operation-limit contract. The full local suite passed 1,925 tests, with 39
environment/opt-in skips; Ruff, BasedPyright and mypy passed.

Private diagnostic scripts and per-run records are retained under
`test-output/issue-157/`. Production was not load-tested.

## Failed Warm-up At Activation Of `4f2c7a1` (#310)

On 2026-10-31 the host ended warm-up with `failed:QueryTimeoutError` after
12.7 s. The largest sheet timed out on both attempts (1,009 and 1,422 ms)
against the one second budget of `load_graph`, while activation still shared the
CPU; every other entry completed. The first three Gruper cases then met the same
limit and answered 504, and the fourth filled the cache in 2,380 ms.

Warm-up now gives each sheet 5 s of SQL while requests keep their one second.
Operation limits are unchanged. The sheet cache no longer holds its lock while
reading: a request needing a sheet being warmed waits up to 2.5 s, inside its
3 s deadline, holding its lease, then answers its closed timeout, and other
entries stay available. Algorithm, product and modifier entries still hold the lock, but
they took 248 ms at most on the host. Decoding after the read has no budget, so
a warm-up step can run longer than 5 s; shutdown joins it.

## Actual-Host Recovery During The Rolled-Back #160 Activation

Revision `31ef247` reproduced a real sheet timeout at fresh startup. Its 1a
sheet with release hash `420933eb7e50d05ad87544e746324786d1239fadeed62b24bafe1ddbef39af15`
timed out after 1,013 ms on attempt one. Independent sheets and product releases
completed; attempt two recovered the failed sheet in 1,362 ms. The final summary
reported five sheets, five product releases, two attempts and 12,940 ms, with
no container restart. The stage time includes the existing sequence of bounded
readers and preparation; it is not a newly extended reader budget.

All five pinned-source golden requests reached their expected outcomes before
the comparison check. The release was nevertheless rolled back because #104's
comparison returned 504 during paced public acceptance. Successful compatible
activation was still pending at that point. These host records
establish real bounded recovery, not the exact loader of the old 2026-10-09 event
or a host-capacity claim. Evidence is retained under
`test-output/validation-deploy/31ef247143180648803baa64b6acc3b7b544cfd0/`.

The corrective #161 activation was also rolled back for repeated comparison
timeouts. Five golden cases passed on that revision, but the warm-up fix was not
left deployed. The prior `5458ef2` API and edge were restored and healthy;
the [comparison rollout record](jgp-comparison.md) records the failed acceptance
and the rollback requiring a second guarded invocation to start the edge.

## Successful #164 Activation

Revision `bc4e9ef4d272d915f27c1260bf50f1aca16c243e` activated successfully at
2026-10-11T18:06:56Z after green PR/main CI and private image publication. The
fresh API completed all five sheets and five product catalogs in 8,278 ms on
attempt one, without a restart. First pinned current/older-act golden requests
all passed, at 716, 115, 516, 256 and 212 ms. The comparison and public browser
checks also passed, so this compatible release remains active.

Together with the bounded timeout recovery demonstrated above, this completes
#157's acceptance. It does not identify the old event's exact loader or establish
production capacity. No production load or stress test was performed. See the
[exact release record](jgp-comparison.md#successful-164-activation) and retained
`test-output/validation-deploy/bc4e9ef4d272d915f27c1260bf50f1aca16c243e/` evidence.
