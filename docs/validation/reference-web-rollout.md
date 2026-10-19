# Public Reference Validation

## Explicit Validation Boundary

On 2026-10-01 the owner requested a website/API deployment after #16 and explicitly
authorized proceeding as a public-data validation project rather than waiting
for the commercial/product launch gates. This is an explicit, narrow development
exception, not a written legal opinion, trademark clearance, signed product
release or completion of #2, #3, #27 or #41.

The owner subsequently confirmed on 2026-10-01 that reference search will always
remain free and noncommercial and authorized adding pinned RSK ICD-10 PL. The
current selection is `manifests/sources/reference-public-validation.json`:
one canonical NFZ CZS ICD-9 PL catalog and one RSK ICD-10 PL catalog. It does not
expose the private S3 archive, credentials, control database, raw source routes,
patient data, grouping calculations, payments or a claim of clinical correctness.
Official upstream download links remain available. RSK publisher, CC BY-NC-SA
3.0 IGO terms, translation disclaimer, transformation and non-endorsement notices
appear in API provenance and on the search page. No paid feature may depend on
the RSK content; source-derived data remain subject to the source terms.

`context_for` binds the immutable same-version NFZ workbook to its canonical CZS
release. The verified DB repeats this binding in build metadata; manifest/DB
disagreement fails closed. Supporting sources are not selectable search releases,
even by direct API or CLI request. CZS result codes/titles/statuses/dates stay
unchanged. `supporting_contexts` carries exact-code-linked workbook ancestors,
all supplied parents, original workbook labels, its release identity/checksum
and source terms, separately from the canonical `context`. Workbook-only codes,
conflicting titles and annotations are not additional canonical matches. Earlier
bundles without `context_for` retain their explicit separate-release behavior.

## API And Privacy

The validation adapter uses `POST /api/v1/search` with a bounded JSON body, not
the future product issue #23's query-string shape. This deliberate exception
keeps medical search terms out of request URLs. `GET /api/v1/releases` exposes
only pinned source metadata. `/healthz` is data-free; `/readyz` verifies a bounded
read against the startup-verified database. Unknown API routes and raw archive
downloads remain unavailable.

Each lookup explicitly selects `system` and `release_id`. JSON retains the CLI's
source attributes, complete ancestor graph, morphological explanations and
labelled context evidence, adding `api: 1`. It does not interpret a status/date
as billability or select latest versions. Concept-detail/service-date endpoints
and the complete product API remain separate work.

The API pins FastAPI 0.142.2 (MIT) and Uvicorn 0.54.0 (BSD-3-Clause) in its own
optional boundary; they do not enter the watcher runtime. Starlette's maintained
test client uses development-only HTTPX2 2.13.1 (BSD-3-Clause). No OpenTelemetry
SDK/exporter is installed; the validation runtime explicitly disables its SDK.
Uvicorn access logs are disabled. Application events contain only generated
request IDs, allowlisted routes/methods, status classes, bounded durations and
dataset identities, never request bodies, medical queries, full URLs or IPs.

Cloudflare terminates public TLS and can process request bodies; origin logging
controls are not a guarantee about provider retention. Its documented Security
Events retention is 24 hours for Free/Pro, three days for Business and 30 days
for Enterprise; Security Analytics retention is respectively seven, seven,
31 and 90 days. This rollout does not enable Logpush, Logpull retention,
payload logging or DLP forensic copies. The active zone's detailed retention
settings have not been independently audited, so the full #28 provider-retention
acceptance remains open. Never submit patient data. Documentation checked on
2026-10-01: [Security Events](https://developers.cloudflare.com/waf/analytics/security-events/)
and [Logpull retention](https://developers.cloudflare.com/logs/logpull/enabling-log-retention/).

## Limits And Targets Defined Before Load

These are the original rollout budgets. The 2026-10-05
[dictionary/search recovery](dictionary-search-recovery.md) supersedes the
immediate lease refusal and 250/400 ms query deadlines with bounded waiting,
longer cooperative budgets and distinct capacity/timeout responses. Historical
measurements below retain their original context.

- Four startup-verified, exclusively borrowed read-only SQLite connections.
  Database digest must equal the selected deployment identity; file identity
  changes fail closed. No request queue waits for an occupied SQL connection.
- Uvicorn limits total connections/tasks to 16. Capacity saturation is explicit
  HTTP 429/503, not partial results or an unbounded queue.
- JSON bodies: 4 KiB; body-read deadline: 2 seconds; query: 256 characters and
  eight tokens; output: at most 20 results and the shared 512 Ki-character budget.
- SQL retrieval retains the shared 250 ms / two-million-VM-step ceiling.
- API: non-root UID 10004, read-only root/data, no capabilities or outbound
  network, one CPU, 512 MiB memory and 64 MiB temporary storage.
- Local-only load acceptance: 48 requests at concurrency four, no unexpected
  errors, warm API p95 below 500 ms. A local concurrency-eight burst must return
  only success or explicit capacity/limit errors. The checker rejects non-loopback
  targets. These are validation budgets, not an incumbent comparison or launch SLO.
- Mikr.us receives only readiness and one search per selected terminology, plus
  desktop/mobile visual review, query-free URLs/telemetry and at least 300 MiB
  host memory headroom with existing services running. The owner stopped VPS
  stress/profiling after a provider warning; no load acceptance runs on the host.
  Earlier concurrency-related query deadlines remain unresolved and are not a
  throughput claim. Keep the existing VPS and query limits, without tuning.

## Artifact And Cutover Policy

Private validation API and edge images are built from the reviewed `main` commit
and selected by exact runnable `linux/amd64` digests, not mutable tags. The code
image receives one checksummed, read-only, versioned reference bundle; no raw
sources or generated medical bundle are committed or uploaded to PR jobs.
The immutable edge includes the checksummed static artifact. Its data-free
`/zrodla` metadata behavior remains unchanged.

The separate `manifests/sources/reference-public-validation.json` records the owner's
validation authorization without retroactively rewriting the private-development
source manifests or claiming a publisher licence. It pins the same raw captures.
`scripts/render_validation_compose.py` preserves existing operational services
and replaces only the edge while adding the isolated API. Local container/browser
acceptance uses the same API service definition.
Generated bundles retain private-development filesystem permissions by default.
For this explicitly authorized deployment, install the selected directory as
root-owned mode 0755 with bundle files mode 0444 before mounting it read-only
into UID 10004. Do not change private raw archive permissions.

```sh
uv run python scripts/build_validation_edge.py /path/to/new/edge-artifacts
docker build --platform linux/amd64 --file packaging/api/Dockerfile \
  --tag minos-reference-api:validation-local .
docker build --platform linux/amd64 --file packaging/validation-edge/Dockerfile \
  --build-context edge-artifacts=/path/to/new/edge-artifacts \
  --tag minos-reference-edge:validation-local .
uv run python scripts/smoke_reference_web.py /path/to/new/acceptance \
  --bundle /path/to/verified/bundle --chromium /usr/bin/chromium
uv run python scripts/check_reference_web.py /path/to/new/public-acceptance.json \
  --base http://127.0.0.1:8080 \
  --expected-digest <database-sha256>
```

The search and procedure-rank browser scripts map `minoshealth.pl` to loopback
only for `http:` targets, which is how local containers are exercised. The
public origin redirects plain HTTP to HTTPS, so an `https:` target uses real DNS
and the same assertions serve as the post-activation desktop/mobile review:

```sh
node tests/browser/procedure-rank-browser.mjs /usr/bin/chromium \
  https://minoshealth.pl/ /path/to/new/public-review 89.02 4
```

The sources-page script stays local-only because it serves fixture provenance.

The `reference-validation.yml` PR workflow exercises synthetic data only. A manual
dispatch on reviewed `main` additionally publishes private, code/static-only GHCR
images using a short-lived GitHub token. Publication does not deploy a host or
upload a medical bundle. Package visibility and pull permission must be verified
before host activation.

Later updates follow [Validation Deployment Updates](validation-deploy.md).

This temporary validation deployment uses an operator-recorded commit/digest/
dataset receipt and retained exact previous Compose/static configuration. It
does not claim #41's independent signatures or signed receipt chain. Only the
new reference API and edge change; watcher, tunnel, Kuma, credentials, schedules
and control state remain untouched. Rollback restores the exact previous edge
and restores or removes the validation API according to the previous Compose
configuration. No watcher database restore is involved.

Do not run the maintenance-only Ansible playbook over an active validation
deployment. Restore maintenance first, or integrate the validated services into
the formal signed-release playbook. This limitation is explicit rather than
pretending the validation cutover is the complete #27 product deployment.

## Acceptance Evidence

Local full-source acceptance on 2026-10-01 used both pinned NFZ releases, 20,238
concepts, 23,231 evidence nodes and 12,950 explicit containment relations in a
64,864,256-byte database. Database SHA-256:
`ca43c87835bda2a8f9776f4f3cfa137edb840465acbe324bce7b2bd1499083a8`.
Dataset identity:
`9d178912d05eb193239fcc35a6003d54de51ebc90f49c2e64ce7e5c38f4dc938`.

Real Chromium desktop (1440 px) and mobile (390 px) checks passed; screenshots
were visually reviewed. Morphology, separate source selection, evidence panels,
keyboard navigation and empty-query behavior passed. No query URL, browser
storage or search-page analytics request was observed.

The one-CPU, 512 MiB, read-only Compose API served all 48 concurrency-four load
samples successfully: p50 35.572 ms, p95 82.992 ms. A concurrency-eight burst
returned six successes and 26 explicit HTTP 429 capacity responses, with healthy
readiness afterward. API cgroup peak memory was 411,168,768 bytes. Stopping the
API made edge readiness fail with a bounded upstream transport error; restarting
the same immutable service restored readiness. Independent synthetic-data
container/browser/load acceptance also passed.

Missing, changed and incorrectly selected artifacts fail closed in API integration
tests. The 4 KiB/256-character/eight-token/result-count limits and query-free log
allowlist are exercised at the HTTP boundary. The workflow security test permits
package write only for the exact manual-main publication job after verification.

Public deployment and host-load evidence are recorded in the operator receipt
after activation; no deployment success is inferred from local acceptance or
an idle production interval. These results do not compare Minos with incumbents
or claim completion of the full #42 SLO investigation.

Pre-promotion Trivy 0.70.0 scanning found fixable `CVE-2026-93990` in the old
maintenance edge's libexpat 2.8.4-r0. The validation edge now uses the refreshed
NGINX 1.31.6 Alpine `linux/amd64` base
`sha256:c18d735d33a1c3ccb5ef201d504e5b4aa4d003e81f034b329011267f4c4d0f58`,
containing libexpat 2.8.5-r0. Both validation images then passed fixable
high/critical scans, and full-source container/browser/load/failure acceptance
was repeated successfully. The PR/manual-publication workflow now enforces
those scans so an image regression blocks promotion. The unchanged old base
is retained only as the exact maintenance rollback target, not the new release.

On 2026-10-08 the same gate found fixable `CVE-2026-4775` in tiff 4.7.1-r0.
The validation edge moved to the upstream rebuild
`sha256:b9241c6e7b8e9a862f129d8d4199ab64b10390949a78bdd5603379b32c844083`
(NGINX 1.31.6, Alpine 3.24.2), which ships pcre2 10.49-r0, so the earlier
pcre2 pin was dropped; tiff 4.7.2-r0 is pinned until upstream rebuilds.

On 2026-10-19 the gate found fixable `CVE-2026-77214` (libexpat 2.8.5-r0) and
`CVE-2026-88806` (libx11 1.8.13-r0). The validation edge moved to the upstream
NGINX 1.31.7 Alpine 3.24.2 `linux/amd64` rebuild
`sha256:c3bcf3d1a53a027bfa515b9fe4fbbe7bf6b791476a5af853de439ad494b145bd`,
which ships libexpat 2.9.0-r0, libx11 1.8.14-r0 and tiff 4.7.2-r0, so the tiff
pin was dropped. A local Trivy 0.70.0 scan of that base found no fixable high
or critical vulnerabilities.

Host pre-cutover canaries stopped promotion when ordinary cold searches returned
HTTP 429. Profiling isolated 320 ms in the first native dictionary construction
inside the 250 ms request budget; later searches in that same process took
7-44 ms. The HTTP pool now verifies and retains the pinned dictionary before
readiness, while request analyzers remain independent. Missing/incompatible
morphology cannot produce a ready service. Neither the deadline, SQL step limit,
source evidence nor worker capacity was weakened. The original candidate was
removed without changing the maintenance edge or operational services.

Container acceptance now tests the first text request before browser/code
warm-up. Local full-source revalidation passed: first text request 14.599 ms;
48 successful concurrency-four requests, p95 80.953 ms; overload produced only
success/HTTP 429; API peak memory 416,358,400 bytes. On 2026-10-01 at 13:02 UTC
the NFZ-only release was activated with readiness and exactly one public browser
search, desktop/mobile visual review, retained maintenance rollback and unchanged
operational services. No VPS stress/profiling was run during that cutover. The
subsequent canonical ICD-9 plus ICD-10 deployment needs its own artifact receipt
and the same minimal host checks, not a replay of the earlier host load plan.
