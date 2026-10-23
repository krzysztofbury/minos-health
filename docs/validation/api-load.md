# API Load And Startup Memory (#42, #198)

Status: proposed objectives for owner review, local and public evidence.
Not launch-capacity acceptance; #42 stays open.

## Proposed Objectives

The owner reviews these before the public run counts against them. They
describe the validation site with the MCP server (#216), not a paid service.

| Objective | Proposed threshold |
| --- | --- |
| Server errors | No HTTP 5xx and no transport failures at concurrency up to 8; overload answers only as the closed `busy` (HTTP 429) or MCP `BUSY` |
| Readiness | `/readyz` ready after every run |
| Single-user latency, public edge | Search p95 at most 500 ms; code and JGP pages p95 at most 1 s; Gruper p95 at most 2 s |
| Four concurrent users, public edge | Search p95 at most 1.5 s; Gruper p95 at most 3 s; MCP `search_codes` p95 at most 4 s |
| API memory | No OOM kill; process (anon) memory at most 400 MiB of the 512 MiB limit after load |
| Host headroom | `available_mib` at least the deploy floor (300 MiB plus one API limit) before an activation |
| Startup | Pool ready within the 120 s health start period; nine of ten activations ready on the first attempt (#198) |

Page cache filling the API's cgroup is not a failure: it is reclaimed on
demand, so `memory.peak` at the limit and thousands of `memory.events max`
appear on every healthy start.

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
  5xx or transport failure. One client cannot exceed the edge's 60 per minute
  MCP limit, so MCP fan-out under load is a local measurement only.

Bundle copies for local runs are written with `O_DIRECT`, so the container,
not the workstation, is charged for reading the database, as on the host.

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

Single-user and MCP results meet the proposed objectives. Four concurrent
users do not: one Gruper case outlasted the edge's 7 s timeout and the run
stopped there, as designed. Served Gruper cases sat at their 3 s evaluation
deadline (median 2,981 ms). #222 tracks finding where the time goes before any
bound changes. Search refusals at four users are the closed `busy` answer.

To repeat it:

```sh
uv run python -m scripts.deploy_validation inspect
uv run python -m scripts.measure_api_load public --output test-output/api-load-public.json
uv run python -m scripts.deploy_validation inspect
```
