# Grouping API, Builder And Trace

Issues #39 and #36. The browser entry point is `/gruper`; scripts use
`POST /api/v1/group`, and the offline CLI uses `minos group --data BUNDLE`.
All three evaluate the same verified bundle and serializer. No case is stored.

## Version 1 Request

An artificial example, not clinical validation:

```sh
curl --fail-with-body https://minoshealth.pl/api/v1/group \
  -H 'Content-Type: application/json' --data-binary @- <<'JSON'
{
  "schema_version": 1,
  "service_date": "2026-10-02",
  "reporting_month": "2026-10-01",
  "declared_modifiers": [],
  "encounter": {
    "admitted_on": "2026-09-30",
    "discharged_on": "2026-10-02",
    "age_completed_years": 51,
    "hospitalization_type": "hospitalization",
    "principal_diagnosis": {"code": "K40.9", "hospitalization_level": true},
    "stays": [{
      "identifier": "surgery", "department": "4500",
      "admitted_on": "2026-09-30", "discharged_on": "2026-10-02"
    }],
    "procedures": [{"code": "53.03", "performed_on": "2026-10-01", "stay_id": "surgery"}],
    "product_reporting": "complete",
    "settlement_products": []
  }
}
JSON
```

The same JSON goes to standard input of `minos group --data BUNDLE --json`.
Omitting `--json` prints the human trace. Do not put encounter data into command
arguments or URL parameters. The public route becomes available only after
activation of the corresponding API and edge images and a grouping-capable bundle.

The [encounter contract](grouper-encounter.md) defines facts and engineering
limits. The envelope is strict: duplicate keys, extra fields, coercible types,
non-first-of-month reporting dates, and service dates outside the episode fail.
Declared modifier symbols are bounded, distinct, explicit assertions of all
required provider/encounter conditions, following the 2026-10-07 decision.

The builder is a sequence of steps (hospitalization, ward stays, diagnoses,
procedures and products, result) with Back/Next navigation. Each step is
validated before the next one opens; problems are listed above the step and
point to the field. A case card lists each step's facts as code chips with
names and links back to the step. Grouping is requested only on the result step,
not on every edit.

Dates are typed as DD.MM.RRRR (months as MM.RRRR) whatever the browser language;
digits are formatted as they are typed and sent as ISO dates. The service date
and the reporting month are explicit choices between the discharge date (shown
with its value) and another date; neither is preselected. Discharge mode is a
select of the pinned rule label (9 - zgon pacjenta), "not given" or another
code, with an explanation; ward specialty is a 4-digit code with guidance until
the official dictionaries of #151 provide named search. The first ward stay
starts with the hospitalization dates, visibly and editably.

Modifiers are not typed. For each qualifying group the result lists the
possible and applied modifiers returned by the rules; confirming one adds it to
`declared_modifiers` and re-evaluates. The result lists qualifying groups first,
then a plain-language condition checklist in catalog vernacular (list labels,
`< 18 r.ż.`), documentation checks, other candidates with their first failing or
unresolved condition, and technical details.

The builder supports repeated diagnoses, procedure occurrences and ward stays.
It does not collect a stay's optional `service_code` (the API and CLI accept it).
The pinned SZP dictionary has 94 specialty codes, all in the 1xxx range, whose
exclusion depends on service token 4; from the builder those stays remain
unresolved, as do unattributed facts. Whether coders report such codes on SZP
stays is an open question for practitioner validation (#39). If they do, the
owner-chosen design is an optional field shown only for those specialty codes.
Dates and attribution are explicit. Missing procedure dates and attribution
remain unknown. No products are inferred: an unknown/nonempty report receives
`unsupported_product_report` with no eligibility or points. Product exclusions
remain #145. The API accepts such reports to explain this limitation; the form
displays it before sending a grouping request.

## Response Contract

Success has `schema="minos.grouping.v1"`, `schema_version=1`. Integer points are
hundredths; coefficients are millionths. Unknown values remain JSON `null`.

- `status`: `evaluated` or `unsupported_product_report`.
- `dataset_id`, `database_sha256`, `sources`: immutable composite and component
  identities, engine and algorithm versions, source knowledge dates and tariff
  binding identities. A source/date mismatch fails without fallback.
- `groups`: every derived candidate, with `valid`, `invalid` or `undetermined`
  status, separate conditions/routing verdicts, source rows, root identities,
  directional evidence, base points and optional final points.
- `trace`: all consulted condition nodes, deterministically ordered by identity.
  Each event includes its source rows, parameters, matched codes, observed
  scalars/count, selector/list identities, directional list labels
  (`list_labels`, aligned with `list_ids`) and indexes into prepared facts.
  Shared subexpressions are represented once; expressions refer to child IDs.
- `preparation`: preserved fact occurrences, ranks, exclusions, age, ward-adjusted
  duration, and source-linked preparation events with readable explanations.
  Ward stays are ordered by admission, discharge and identifier, matching the
  one-based ward trace ordinals. Condition fact indexes are zero-based.
- `ranking`: every valid group in deterministic point order. `ranking_basis`
  distinguishes final from base points; `choice="provider"` implements paragraph
  113. The first result is not an automatically selected settlement group.
- `tariff_status`: `bound` or `unbound`. Unbound final points stay absent; base
  points are never relabeled final. Possible/unknown modifiers and owner-approved
  tariff assumptions remain visible. Invalid groups are never ranked.
  `unapplied_declared_modifiers` exposes declarations not applied to each group.
- `not_evaluated` and `underivable`: explain groups outside candidate evaluation.
  `not_evaluated` has one entry per reason with its explanation and the
  governing catalog's groups keyed by catalog. Failure to hit a directional
  list is not a proof of clinical ineligibility.
- `near_misses`: a bounded subset of documentation checks for reported age,
  duration or discharge mode. Age and duration are checked only one year or day
  from the reported value; discharge mode 9 (death) is never checked. Each is
  emitted only after whole-group reevaluation (including routing) changes an
  invalid group to valid. It gives the affected
  fact and rule, never a proposed replacement value or code. This is not an
  exhaustive enumeration of possible coding changes; `near_miss_coverage` says so.
- `disclaimer` and `limitations`: assessment scope and completeness framing.

The domain events are frozen Python data classes, independent of transport and
Pydantic. Explicit serializers control the wire schema. Scalar sensitivity
checks are never mixed into the trace of the actual reported encounter.

## Resource And Privacy Boundary

- JSON body limit: 256 KiB at both edge and API, with a two-second stream read
  timeout. Compressed request bodies are unsupported.
- Two simultaneous grouping workers, process-wide token bucket of eight with
  two tokens per second. A token is spent before the upload; a worker is taken
  only after the complete body arrives. No per-user/IP state in the API, so one
  client above two requests per second can exhaust the shared bucket. Owner
  decision 2026-10-08: per-client limits belong to a Cloudflare per-IP rate-limit
  rule for `POST /api/v1/group`, a separate edge change required before release. The four-lease reference pool
  leaves capacity for search. Busy requests receive `429` and the shared browser
  request helper uses its existing bounded recovery.
- Whole-evaluation budget: three seconds, 200,000 cooperative work checkpoints,
  and 20 million SQLite VM operations. SQL readers share the enclosing deadline.
  Limits return explicit errors rather than partial results.
- Release-static inputs (algorithm tables, characteristics graphs and directional
  indexes) are decoded and validated once per running app, which serves exactly
  one verified bundle (#149). The first request after a start builds them.
  Before this, production evaluations took 1.4-3.3 s and some exceeded the
  budget; locally, repeated requests fell from about 240 ms to about 38 ms.
- Response limit: 4 MiB, refused as a whole when exceeded. Error codes are fixed:
  `invalid_request` (400), `body_too_large` (413), `json_required` or
  `content_encoding_unsupported` (415), `no_ruleset_for_date`,
  `grouping_unavailable` or `evaluation_limit` (422), `busy` (429),
  `evaluation_timeout` (504), `unavailable` (503), `request_timeout` (408).
- No body/query logging, encounter persistence, analytics, localStorage or
  sessionStorage. JSON is returned only to the caller. Edge request buffering
  and response buffering are disabled, and request buffers cover the body cap.
  The route rejects URL query parameters. API responses are `no-store`.
- Every edit clears the old result immediately; obsolete responses cannot replace
  newer state. Leaving the result step and changing a fact requests a new answer. The builder clears its case on page exit and offers explicit reset.

These are engineering limits, not validated clinical maxima or a host-capacity
claim. The browser uses the existing POST search API, source selection rules,
request recovery helper, suggestion styling and 220 ms search debounce.

## Verification And Activation

`tests/integration/test_grouping_interface.py` owns CLI/HTTP schema equivalence,
log privacy across valid/invalid/exception paths, parser rejection, resource
refusal, search capacity during grouping and recovery after errors.
`tests/unit/test_grouper_trace.py` owns condition-branch completeness, source/fact
references, stable ordering and whole-group near-miss sensitivity.

`scripts/smoke_grouping_web.py OUTPUT` runs synthetic source fixtures through the
actual bounded API and nginx images, tests body/query/method refusals, concurrent
grouping, log privacy and Chromium desktop/mobile interaction. It is wired into
the existing private image verification workflow. `--bundle BUNDLE` uses the
pinned publisher bundle with an artificial hernia case and records timing,
memory and screenshots. It copies the bundle into disposable runtime space;
the original archive and bundle are not modified.

Local verification on 2026-10-08:

- Full repository checks used a clean locked environment; the final test suite
  passed 1,868 tests, with 34 opt-in/environment cases skipped.
- Eleven opt-in pinned-source grouper checks passed; the source bundle database
  digest was `55e1936c4f9bb3aadcae07f65119560fd9afad50a105d11675addfdba2033bab`.
- Synthetic and pinned-source grouping ran through actual API/nginx images;
  the installed-wheel CLI returned the same JSON as HTTP. Chromium checked
  desktop/mobile, keyboard search, trace display, stale-response refusal,
  unsupported products, reset, no storage and same-origin POST-only case data.
  Desktop/mobile screenshots were visually reviewed.
- With the existing one-CPU/512 MiB API container limit, the artificial pinned
  hernia case took 325-350 ms cold across two runs. Four simultaneous calls
  produced two 200 results in 861-887 ms and two admission-limit 429 results in
  about 3-5 ms. Peak API memory was 221,868,032 bytes. These samples are local boundary evidence, not clinical,
  comparative performance, or production-host capacity acceptance.
- A direct HTTP stress probe preserved 1,000 repeated procedure occurrences
  (200, 1,083,534 response bytes); a 1,000-distinct-code probe returned an explicit
  `evaluation_limit` (422). Readiness succeeded immediately after both.
- Existing search/filter/dictionary/JGP/rank browser checks, bounded search load,
  log privacy and stopped-API recovery passed through the same edge images.

Release acceptance still requires the user's review, practitioner entry-time
and explanation checks, the Cloudflare per-IP rate-limit rule, then the
production-edge/Cloudflare Tunnel POST and log-privacy checks after authorized
activation. Local containers cannot prove
the tunnel path. Keep #39 open until that evidence exists; the implementation
does not itself authorize deployment or change the production data selector.
