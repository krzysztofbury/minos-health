# API Load And Startup Memory (#42, #198)

Status: objectives adopted 2026-10-23 (the owner delegated their review),
local and public evidence. Not launch-capacity acceptance; #42 stays open.

## Objectives

They describe the free validation site with the MCP server (#216) on one
host CPU and 2 GiB, not a paid service. Realistic demand is a few people at
once, so the levels have distinct meanings: one user is the experience to
protect, four users is a busy moment that may slow down but stays correct,
and eight users is overload that must shed cleanly. Latency targets apply
only up to four users.

| Kind | Objective |
| --- | --- |
| Correctness, any concurrency | No edge-generated 5xx (an nginx 502, 503 or 504 without the API's JSON), no transport failure, no API 500 or `unavailable` 503 |
| Shedding | Overload answers are closed: `busy` (429), MCP `BUSY`, or the API's JSON deadline answers `query_timeout` and `evaluation_timeout` (504). At four users these stay at most 5% per route; at eight users they are not bounded |
| Readiness | `/readyz` ready after every run |
| One user, public edge, p95 | Search at most 500 ms; code and JGP pages at most 1 s; Gruper at most 1 s; one MCP tool call at most 1.5 s |
| Four users, public edge, p95 | Search at most 1.5 s; code and JGP pages at most 1.5 s; Gruper at most 1.5 s, half its 3 s evaluation deadline |
| Four users, local at 0.25 CPU, p95 | MCP `search_codes` at most 4 s (local only: one client cannot fan MCP out past the edge limit, and the CPU share does not calibrate MCP latency, see Method) |
| API memory | No OOM kill; process (anon) memory at most 400 MiB of the 512 MiB limit after load |
| Host headroom | `available_mib` at least the deploy floor (300 MiB plus one API limit) before an activation |
| Startup | Pool ready within the 120 s health start period; nine of the next ten deploys activate without a rollback in the deploy output, tracked in #198 comments |

A p95 at a deadline means most requests nearly time out, so no latency
target equals a deadline. Page cache filling the API's cgroup is not a
failure: it is reclaimed on demand, so `memory.peak` at the limit and
thousands of `memory.events max` appear on every healthy start.

## Method

`scripts/measure_api_load.py` sends a browser-shaped cycle (four searches
across ICD-10, ICD-9 and JGP plus ICD-11, a code detail, a JGP group, a
Gruper case) plus three MCP calls (`search_codes`, `get_code`, `group_case`).
Reports keep route kinds, statuses, closed error codes, timings and cgroup
memory; never queries, codes or bodies.

- `local` runs the API and edge images on a pinned bundle in a disposable
  Compose project at the production limits (API 1 CPU, 512 MiB, no swap; edge
  0.5 CPU, 64 MiB), at concurrency 1, 4 and 8. It sets `CF-Connecting-IP` per
  worker to emulate distinct agents, because the edge keys the MCP limit on it.
- `public` sends 40 requests at concurrency 1, 120 at concurrency 4, then 12
  sequential MCP calls to `https://minoshealth.pl`, and stops at the first
  transport failure or 5xx other than the API's closed deadline answers. One client cannot exceed the edge's 60 per minute
  MCP limit, so MCP fan-out under load is a local measurement only.

Bundle copies for local runs are written with `O_DIRECT`, so the container,
not the workstation, is charged for reading the database, as on the host.

The host CPU is several times slower than a workstation core. `local
--api-cpus 0.25` approximates it for the API: one-user Gruper cases take
204 ms there against a 281 ms host median that includes the network. MCP does
not scale the same way (host 882 ms against 186 ms locally for one
`search_codes` call), so MCP latency at this share is not a host estimate.

Each Gruper request logs its phases on the request line (#222): `upload_ms`,
`queue_ms`, `parse_ms`, `borrow_ms`, `evaluate_ms`, `render_ms` and
`respond_ms`, durations only. A failed or timed-out request charges its open
interval to the phase that was running, so a 3 s timeout appears in
`evaluate_ms`. Local reports add their percentiles per load phase as
`grouping_phases`, timed-out and served requests together.

## Local Evidence, 2026-10-23

API image with the #220 logging fix, production bundle `1c8f1fa1`, on an
8-core AMD Ryzen 7 255 workstation. Recent host activations loaded the
pool in 41 to 58 s against about 5 s here, so these latencies are a floor, not host
evidence. Report: `evidence/api-load/local-2026-10-23.json`.

| Phase | Search p95 | Gruper p95 | MCP `search_codes` p95 | Refusals |
| --- | ---: | ---: | ---: | --- |
| Concurrency 1, 60 requests | 22 ms | 67 ms | 64 ms | none |
| Concurrency 4, 240 requests | 521 ms | 685 ms | 1,067 ms | Gruper `busy` 1, MCP `group_case` `BUSY` 11 of 21 |
| Concurrency 8, 240 requests | 796 ms | 950 ms | 1,689 ms | search `busy` 10, Gruper 3, MCP `group_case` 14 of 21 |

- No 5xx, transport failure or OOM kill; `/readyz` ready afterwards.
- API process memory 278 MiB after startup, 328 MiB after the load.
- Gruper refusals come from its process-wide admission (burst 8, two per
  second, two workers), shared by the site and MCP. The cycle asks for about
  2.7 groupings per second, so the later MCP call usually finds the bucket
  empty. Agents get `BUSY` and retry; this is the designed bound.
- MCP `search_codes` is the slowest path: one call searches four
  dictionaries in turn.

## Gruper Under Contention (#222)

The public run's edge 504 came from Gruper cases sitting at their 3 s
evaluation deadline. At 0.25 CPU with the release image, the phase log put
nearly all of a four-user Gruper request in evaluation: `evaluate_ms` p50
1,902 ms and p95 2,690 ms, every other phase near zero. A profile of one
warm request showed 85% of its CPU in `reviewed_group_modifiers`, which
validated and reconciled the whole MODTAR dictionary again on every request
and for every valid group. Startup already validates it once per database
digest, and the bundle is immutable and checked on every lease, so the
grouper now keeps one validated reference per MODTAR and catalog release in
its release-static cache. A warm request fell from 37.5 ms to 8.8 ms with
identical answers.

Same bundle, 0.25 CPU, release image against the fix. Reports:
`evidence/api-load/local-cpu025-before-2026-10-23.json` (its phase names
predate `parse_ms` and `respond_ms`) and `local-cpu025-after-2026-10-23.json`.

| Phase | Gruper p95 | MCP `group_case` p95 | Search p95 | Search `query_timeout` |
| --- | ---: | ---: | ---: | ---: |
| Four users, before | 2,317 ms | 3,000 ms | 1,807 ms | 11 of 110 |
| Four users, after | 598 ms | 501 ms | 1,687 ms | 1 of 110 |
| Eight users, before | 3,106 ms, 1 `evaluation_timeout` | 3,195 ms | 2,194 ms | 8 of 110 |
| Eight users, after | 1,396 ms | 1,495 ms | 2,291 ms | 7 of 110 |

- No edge-generated 5xx, transport failure, API 500 or OOM kill in either
  run; `/readyz` ready afterwards. Anon memory 278 MiB after startup and
  302 MiB after the load.
- Search at this share still answered some requests with its closed
  `query_timeout`: 0.9% at four users, inside the objective, and more at
  eight. The next section removes that (#225).
- The local run never reproduced the host's edge 504 itself; #222 closes on
  the four-user public rerun.

## Release-Static Work Under Contention (#225)

Search itself was cheap: about 5 ms of CPU per request in process. At four
users the one CPU was full, and much of it was work that depends only on the
immutable bundle, repeated per request. CPU per browser-and-MCP cycle, measured
through the real app on one thread:

| Repeated work | Where | Fix |
| --- | --- | --- |
| `dataset_identity` serialised the 111 KB dataset manifest (about 1.1 ms) | every request log line, MCP's in-process calls included | remembered per frozen manifest, guarded by a weak reference |
| `MEMBERSHIP_SQL` rebuilt every group's selector reachability from condition JSON (about 9 ms) | code pages, dated JGP lists, MCP `get_code` | reachability cached per release and catalog; the per-code SQL is unchanged and an equivalence test compares it with the full query for every listed code |
| `map_rows` scanned half of `icd11_map_row` per code (3.8 ms) because bundles carry no planner statistics (#228) | code pages, MCP `get_code` | `INDEXED BY` the matching map index (0.004 ms) |
| `membership_counts` re-parsed the conditions manifests and the code count table | every word search | both cached; the dated selection still runs per request |

The caches belong to the API's verified pool and reach requests through its
leases, never a module-global release cache. Synthetic fixtures reach lists
directly, so `tests/compatibility/test_jgp_membership_cache.py` also proves the
cached JGP lists on a real bundle (`MINOS_GROUPER_BUNDLE`): every selector link
of every release and catalog, where selectors nest and about 220 lists are
shared between groups, plus every 50th listed code end to end. It passed on
the production bundle `1c8f1fa1`. One cycle fell from 129 ms to
75 ms of CPU: code page 6.8 to 1.0 ms, MCP `get_code` 28 to 6 ms, MCP
`search_codes` 31 to 19 ms.

Same bundle, 0.25 CPU, against the #222 report above. Report:
`evidence/api-load/local-cpu025-release-static-2026-10-23.json`.

| Phase | Search p95 | Search `query_timeout` | MCP `search_codes` p95 | Phase duration |
| --- | ---: | ---: | ---: | ---: |
| Four users, before | 1,687 ms | 1 of 110 | 3,498 ms | 40.9 s |
| Four users, after | 203 ms | 0 of 110 | 1,203 ms | 11.7 s |
| Eight users, before | 2,291 ms | 7 of 110 | 4,902 ms | 30.8 s |
| Eight users, after | 509 ms | 0 of 110 | 1,691 ms | 12.0 s |

- No edge 5xx, API 500 or OOM kill; `/readyz` ready afterwards; anon memory
  296 MiB after the load.
- The same requests now finish about 3.5 times faster, so the Gruper's
  process-wide admission (two per second) refuses more of them: `busy` 9 of 22
  at four users. That is its designed bound, unchanged.
- The host already met the four-user search objective before this change
  (977 ms, no deadline answers), so the 0.25 CPU share is harsher on search
  than the host. A public rerun needs the owner's approval.

## Planner Statistics Review (#228)

On 2026-10-26 the production bundle (`b8a7ffdb112b`, built 2026-10-22 without
statistics) was copied and given `ANALYZE`, as the builder now does, and the
real app was driven in process through search in every system, ICD-10, ICD-9
and ICD-11 code pages, JGP API, catalog and group pages, grouping
dictionaries, a grouping and the three MCP tools: 948 statements of 84 shapes.
`EXPLAIN QUERY PLAN` for each found no full scan of a large table on a
per-request lookup. Two passes over whole small sets remain and are measured:
the JGP date calendar unions `catalog_release` and the 3,627 rows of
`group_definition` (0.55 ms, with or without statistics), and word search
walks the `concept` covering index inside its ranked query (search medians 2
to 6 ms, unchanged).

Request medians on one lease, analyzed copy against the original bundle,
were within noise for every path (for example ICD-10 text search 6.2 against
6.1 ms, JGP catalog page 8.0 against 9.6 ms, code pages under 1 ms). The two
`INDEXED BY` pins on ICD-11 map lookups:

| Lookup | Pinned, statistics | Unpinned, statistics | Pinned, none | Unpinned, none |
| --- | --- | --- | --- | --- |
| ICD-10 to ICD-11 | 0.011 ms | 0.011 ms | 0.025 ms | 3.5 ms |
| ICD-11 to ICD-10 | 0.009 ms | 0.009 ms | 0.008 ms | 2.0 ms |

With statistics the planner picks the pinned indexes on its own, so the pins
cost nothing; without them, the bundle in production today, dropping the pins
makes each lookup about 140 to 220 times slower. Application and bundle
releases deploy independently, so the pins stay.

## Startup Memory (#198)

The same bundle, each API started alone; two probes per image at 512 MiB, one per tighter limit:

| Image | Memory limit | Outcome | Anon after startup |
| --- | --- | --- | ---: |
| `a6f1e6f` (before MCP) | 512 MiB | ready in 8 to 9 s | 206 and 254 MiB |
| `987f22f` (MCP) | 512 MiB | ready in 9 to 10 s | 257 and 276 MiB |
| #220 fix | 384, 320, 288 MiB | ready in about 10 s | 262 to 277 MiB |
| #220 fix | 256 MiB | OOM kill, exit 137, `OOMKilled` true | |

Memory pressure ends in an OOM kill, never in Uvicorn's exit code 3, so
the exit-3 failures were an exception raised during startup, not the memory
limit. The MCP image is 28 MB larger on disk; its process memory difference is
within the probe noise, roughly 20 to 50 MiB, and inside the proposed 400 MiB
objective.

The first 2026-10-23 activation then failed the same way and the #199
diagnostics named the class: `QueryTimeoutError`, no memory events. The
comparison preload in `ReferencePool` ran under a 5 s total deadline and,
nested inside it, the one-second request budget for each release; the host
took 3.5 s for five releases alone on 2026-10-11, and the previous colour
shares the one CPU during activation. The guarded retry activated at
08:43:10Z. Raising only the total to 30 s (#223) was not enough: the
activation of `14e489f` failed with the same class and rolled back. Each
release now shares the 30 s preload deadline, still failing closed well
inside the 120 s health start period; a fake-clock test reproduces the
failure without the fix.

## Public Run, 2026-10-23

Release `2e35218` on the host, run from the workstation with the owner's
approval. Report: `evidence/api-load/public-2026-10-23.json`. Host
`available_mib` was 1,158 before and 1,168 after.

| Phase | Search p95 | Code p95 | JGP p95 | Gruper p95 | Outcome |
| --- | ---: | ---: | ---: | ---: | --- |
| Concurrency 1, 40 requests | 326 ms | 145 ms | 256 ms | 710 ms | all 200 |
| Concurrency 4, stopped at 64 of 120 | 1,296 ms | 1,204 ms | 1,345 ms | 3,577 ms | search `busy` 6, code `busy` 1, Gruper edge 504 1 |
| 12 sequential MCP calls | 882 ms (`search_codes`) | 888 ms (`get_code`) | | 1,321 ms (`group_case`) | all answered |

Single-user and MCP results met the proposed objectives of the time. Four
concurrent users did not: one Gruper case outlasted the edge's 7 s timeout
and the run stopped there, as designed. Served Gruper cases sat at their 3 s
evaluation deadline (median 2,981 ms); the cause and its fix are under Gruper
Under Contention. Search refusals at four users are the closed `busy` answer.
The four-user rerun on the fixed release is recorded in #222.

## Public Reruns After The Fixes, 2026-10-23

Both owner-approved, same capped mix. Reports:
`evidence/api-load/public-f9828a5-2026-10-23.json` and
`public-0cde6b7-2026-10-23.json`.

- `f9828a5` (#222 fix): 120 of 120 at four users, no 5xx. One user p95:
  search 293 ms, code 197 ms, JGP 196 ms, Gruper 731 ms; four users: search
  977 ms, code 896 ms, JGP 903 ms, Gruper 917 ms. Five Gruper 429s came from
  the Cloudflare per-IP rule, because the four workers share one IP. Every
  public objective met.
- `0cde6b7` (#225 caches): every request answered (40, 120 and 12 MCP calls),
  no 5xx, no deadline answer; correctness only. Its latencies are not
  evidence: the workstation saw 20% loss and a 591 ms average round trip to the
  Cloudflare address serving the site (0% and 8 ms to 1.1.1.1), and
  `/cdn-cgi/trace`, answered by the edge alone, took up to 2.6 s. A latency
  rerun needs a clean client path (#225).

## Public Rerun On A Clean Path, 2026-10-25

Release `a8f8662` (#225 caches and #242), owner-approved, 0% loss and about
8 ms to the Cloudflare address before and after. Report:
`evidence/api-load/public-a8f8662-2026-10-25.json`. Every request answered, no
5xx. One user p95: search 282 ms, code 426 ms, JGP 194 ms, Gruper 879 ms (the
first case after the deploy builds its caches). Four users p95: search 214 ms,
code 139 ms, JGP 204 ms, Gruper 172 ms, against 977, 896, 903 and 917 ms on
`f9828a5`. Ten Gruper 429s came from the Cloudflare per-IP rule, since all
workers share one IP. Every public objective is met.

To repeat it:

```sh
uv run python -m scripts.deploy_validation inspect
uv run python -m scripts.measure_api_load public --output test-output/api-load-public.json
uv run python -m scripts.deploy_validation inspect
```
