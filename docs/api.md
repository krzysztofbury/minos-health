# Validation Reference API

This is bounded public ICD-9 PL validation, not the complete product API.
Queries are POST JSON bodies so they do not enter request URLs. Do not submit
patient data. No account, API key or external search service is required.

```sh
curl --fail https://minoshealth.pl/readyz
curl --fail https://minoshealth.pl/api/v1/releases
curl --fail https://minoshealth.pl/api/v1/search \
  --header 'Content-Type: application/json' \
  --data '{"system":"icd9-pl","release_id":"icd9-czs-5-90-gen-93-e3c744634e68","query":"A01","limit":10}'
curl --fail https://minoshealth.pl/api/v1/search \
  --header 'Content-Type: application/json' \
  --data '{"system":"icd9-pl","release_id":"icd9-nfz-5-90-main-6c607dd5e564","query":"badania moczu","limit":10}'
```

The two NFZ releases remain separate: CZS contains complete code/status/date
evidence without a hierarchy; the workbook contains explicit containment and
context without overriding CZS or implying billability. The API never silently
selects a latest release or joins these sources.

Search returns `api: 1` plus the shared CLI v1 envelope: selected release and
dataset identities, database/source checksums, search/morphology versions,
labelled match evidence, exact source attributes, complete ancestor context,
explicit morphological alternatives, spelling suggestions and a disclaimer.
Suggestions are not automatically executed. Empty results return HTTP 200 with
`status: "no_results"`, not an invented code.

Inputs are strict JSON, at most 4 KiB, with a 256-character/eight-token query,
an explicit system/release and 1-20 results. Unknown fields/types are rejected.
The optional language is `pl` (default) or `en`. Invalid requests return 400;
unsupported content types 415; oversized bodies 413; capacity/query limits 429;
unavailable/changed artifacts 503. Errors expose only fixed machine-readable
codes, never the submitted query or exception details. The edge rejects URL
query parameters and private artifact paths with 404. All responses are no-store.

`/healthz` checks API process liveness; `/readyz` also checks the verified dataset.
The website is `/szukaj`. The metadata-only catalog remains `/zrodla`.

See [deployment scope and acceptance](validation/reference-web-rollout.md).
