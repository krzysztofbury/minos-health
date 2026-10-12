# Release-pinned concept JSON (#23)

Contract and curl examples: [API documentation](../api.md#pinned-concept-detail).
The owner approved explicit release selection and optional dated JGP context
on 2026-10-12. No query parameters, latest-release inference, dictionary date
selection or date-scoped rank claim were introduced.

## Boundary evidence

- HTTP integration builds the same synthetic canonical/companion, rank and
  conditions artifacts used by CLI/reference consumers. It verifies exact source
  wording, translations, status, direct typed relations, supporting attribution,
  explicit zero versus unknown ranks, date gaps, pagination, release-binding
  conflicts, legacy bundles, unchanged dictionary facts across dated lookups,
  pool exhaustion, altered-bundle refusal and masked telemetry.
- Real SQLite overflow cases verify description, attribute, neighbour and UTF-8
  output bounds. Error mapping reuses the HTTP failure-contract test.
- Real installed-wheel API and Nginx images pass exact concept JSON, dated and
  paged routes, one no-store header, query-string refusal and method rejection.
  The existing full browser/search/readiness/privacy smoke also passes, including
  shutdown/recovery. Peak API cgroup memory was 148,377,600 bytes on its synthetic
  fixture. Existing search p95 was 65.53 ms at four concurrent requests.
- The edge-builder/log-category route corpus verifies matching path acceptance.
- Targeted mutation checks removed the terminology-binding guard and the output
  byte guard in isolated source copies. Owning tests failed respectively on an
  incorrect HTTP 200 instead of 409 and the missing overflow refusal.

## Current-bundle local measurement

The complete deployed bundle, database SHA-256
`8ae874b13a7a8648980c133822b4955f38ef4996440f8759fc53d68c28c2e0ac`, was
exercised through FastAPI TestClient on 16 undated/dated ICD-10 and ICD-9 routes.
Across 160 requests at concurrency four: median 22.87 ms, p95 60.36 ms, maximum
69.59 ms. The first pass maximum was 240.57 ms. Largest response: 14,868 bytes.
A separate single request peaked at 685,326 Python allocation bytes. All requests
returned 200 under the unchanged shared query budget. These are local evidence,
not production capacity or a replacement for deferred #42.

Detailed local evidence is under `test-output/issue-23/`. Deployment acceptance
must verify public bytes against the same store result and pinned bundle,
positive dated/paged routes, invalid/missing identities, date gaps, query-string
rejection, readiness and unchanged operational containers before closing #23.
