# Deterministic Grouper Execution: Preparation And Candidates

Issue #34, following the reviewed encounter boundary. Entry points:
`minos.grouper.registry.EngineRegistry.execute` runs algorithm preparation, and
`minos.grouper.pipeline.derive_candidates` runs it from one verified bundle and
derives directional candidate groups. These preparation entry points do not
return final eligibility or settlement points. The full `group_request` use
case subsequently delivered supported eligibility, tariff and trace evaluation
(#35/#36), exposed by the [grouping interface](grouping-interface.md) (#39).
The latest verified deployment is #156 (`5458ef2`, 2026-10-09), including #145
product exclusions. The preparation evidence below retains its original scope.

## Exact Dispatch And Source Ownership

An immutable registry explicitly binds a ruleset identity, algorithm version,
engine version, reporting month and exact `AlgorithmIdentity` to one immutable
algorithm snapshot. Every request value must match. Missing registrations and
unknown combinations
fail; no latest selector or engine fallback exists. The registry starts empty.
The supported combination is `1.1.2026` / `szp-preparation-2`, which adds
settlement product exclusions (#145) to `szp-preparation-1`.

The reporting month is supplied explicitly, not inferred from the service or
admission date. The algorithm's first reporting month is checked, but does not
establish the reporting/service applicability of a complete composite ruleset.
Registering a name is not proof of legal review. Results retain the exact source
release identity and three source digests. Those identities are now part of the
request key, so rotating any source requires a different exact request, even
across separately constructed registries. A binding rejects mismatched sources.

The engine accepts only the reviewed algorithm DOCX digest shown below. A file
with matching version metadata but different bytes cannot execute with the
reviewed paragraph coordinates. Registry resolution alone records `dispatch /
exact_binding`; directly calling `prepare` checks source/version compatibility
but does not claim a registration was resolved. `AlgorithmData` is trusted domain
data, not an untrusted interchange format: production source tables must come
through the verifying archive adapter. Unit fixtures construct synthetic tables
directly and are not proof of archive provenance.

`minos.ingest.grouper_algorithm.load_algorithm` reuses the existing strict
Pydantic komunikat manifest and hardened Office readers. It verifies and parses
all three archived files before returning data, consumes full companion tables
including their count/duplicate checks, then selects **SZP only**. Ranks never
fall back to AOS/REH. Domain constructors reject mutable/unbounded tables,
cross-scope evidence and duplicate code or row identities.

Existing manifest pins:

| Component | SHA-256 |
| --- | --- |
| Algorithm DOCX | `a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c` |
| Procedure ranks | `51e587c2df590fa23cb10c1773edeb9cc8a0b2e630cbb2e360206337b7aed13e` |
| Specialty exclusions | `ad6616ce9d426f08e423d6b1b4c11000a2a150f5936f4d1993dd839570f128d2` |

These pins are reference-only, not an accepted composite legal pairing. No RSK
content, source descriptions, complete publisher workbooks or patient encounters
are copied into execution fixtures or a commercial artifact.

## Executed Decisions

- Each stay consults the complete selected specialty table. An absent row means
  no listed specialty exclusion (DOCX paragraph 125), not an empty/missing table.
- Supported SZP predicates are exactly `WSZYSTKIE/WSZYSTKIE` and
  `4/WSZYSTKIE`, the two forms observed in the verified pinned SZP source. The
  service code is the reported service-type token. A product identifier is not
  silently decoded into that token. Missing/unsupported predicates remain
  unresolved. This is not service-code dictionary validation.
- Hospitalization-level diagnoses (paragraph 143) are retained regardless of
  stay exclusions; they are not codes of a ward stay (owner decision
  2026-10-07).
- Diagnoses and procedures attributed to excluded stays are excluded before
  ranking. Unattributed facts remain unresolved if any stay could be excluded;
  no ward is guessed from a procedure's date. All reported occurrences survive
  in the result, including excluded and unresolved ones.
- Rank 0 is ignored; ranks 1-3 record once-per-code policy; rank 4 records
  label-dependent repetition. Occurrences are **not** deduplicated yet. Missing
  rank is unresolved, never zero. Rank-2 routing, rank-4 allocation and E11/N24
  date exceptions require the later characteristic/routing binding.
- Completed years use exact birth date at admission or the explicitly reported
  years-at-admission alternative. A February-29 birth completes a year at the
  start of February 28 in a non-leap year (Civil Code art. 112, owner decision
  2026-10-07); the trace records `birth_date_years_leap_day` on that date.
  Week/month age conditions are not evaluated.
- A `one_day` report must have equal admission and discharge dates
  (paragraphs 146-147); otherwise ward duration is unresolved with
  `one_day_dates_inconsistent`, never silently computed.
- Ward-adjusted days use same-day duration 1, otherwise discharge minus
  admission, then subtract the union of wholly excluded stays. Overlapping and
  adjacent-day intervals are merged as in paragraphs 169-186. With bound
  product catalogs, each type W product then subtracts its end minus start
  (owner interpretation, decision 2026-10-09). Unknown stay exclusions, unknown
  products, touching W intervals, a W product in a same-day stay, and an adjusted
  multi-day duration of 0 or less produce an unknown duration rather than a
  partial numeric value.
- Reported stays must cover the admission-to-discharge interval before ward
  duration or exclusion-free unattributed facts can be resolved. Missing coverage
  leaves those results unknown; coverage is not inferred from the episode dates.
- Reported settlement products retain all occurrences and independently missing
  start/end dates. `product_reporting=unknown` (the default) distinguishes omitted
  or partial reporting from an explicitly complete report, including a confirmed
  empty list. Results preserve the canonical product tuple. With a complete
  report and the product catalogs of the act governing the service date, the
  result reports `product_binding=bound`, the classifying release and one class
  per product (`W`, `neutral` or `unknown`). A W product excludes every
  procedure dated within its start and end dates, inclusive; an undated
  procedure is bounded by its attributed stay, else by the episode. A procedure
  the W union only partly covers, or one an unknown product could cover, stays
  unresolved. A ward exclusion outranks a product "unresolved" and a W exclusion
  outranks a ward "unresolved". Without that binding the result reports
  `product_binding=unbound` and a typed unresolved event cites the product rules
  (#145, decisions 2026-10-09).

Every stay, reported fact, rank decision, interval merge, age/duration result
and scope limitation has a typed, bounded trace event with source coordinates.
The snapshot digests resolve those coordinates to exact archived bytes. Sorting
is canonical across secondary diagnoses, procedure occurrences, stays and source
table order; unknown dates cannot tie known `date.min` dates. Principal diagnosis
role and duplicate occurrences are never erased as "irrelevant order".

Results and trace events suppress medical facts in default repr output. Neither
loading nor execution logs or persists encounters. Explicit serialization and
debugger inspection still expose facts and must obey the no-encounter-logging
contract. Execution owns fresh per-call state; no clinical state enters the
registry. Structural collection caps bound work; no faster-than-incumbent or
representative production capacity claim follows from them.

## Evidence And Remaining Acceptance

Unit tests own exact source-aware dispatch, conservative unknowns,
exclusion-before-ranking,
rank classes, age boundaries, the source's `25 - 11 - 4 = 10` duration example
and maximum-sized encounters. Integration tests exercise the real archive
verification/Office parsing boundary and reject corruption of each component.
The six-paragraph synthetic DOCX remains importable reference metadata but is
rejected by the engine: it cannot supply real-source execution evidence.

Hypothesis is development-only (`6.x`, currently locked to `6.168.5`, MPL-2.0).
Its production/stable status and Python requirement were checked in published
package metadata. It adds generated inputs, permutations and shrinking for
complete result/trace determinism. The licence policy accepts the SPDX spelling
of the already accepted MPL-2.0 licence; no runtime dependency was added.
Tests disable the example database. Agent runs redirect Hypothesis storage and
pytest temporary files to unique `agent-scratch` directories.

Real-source opt-in: `MINOS_GROUPER_PRIVATE_ARCHIVE` supplies `blobs/sha256/` for
the three pinned files to `tests/compatibility/test_grouper_private_capture.py`.
Fresh checked publisher bytes exercised the archive adapter and engine: retained
`00.121` has SZP rank 3 at row 4047; its ICU-attributed occurrence is excluded
using specialty 4260 at row 352; L91 remains missing in SZP instead of borrowing
its AOS rank. Synthetic episode age, ward duration and reversed input order
were checked. This is real-source engineering preparation evidence, **not**
evaluation against a reviewed complete pinned ruleset or clinical validation.

Six manual mutations were killed at their owning boundary: registry fallback,
skipping exclusions before ranking, missing-rank-as-zero, omitted adjacent-day
merging, discharge-anchored age and retained unattributed facts. Two edge cases
were reproduced as failing tests before their fixes: a missing/`date.min` sort
tie and missing stay coverage producing an unjustified duration. An installed
wheel smoke exercised the actual registry/engine from an isolated environment
without development dependencies.

Composite binding, candidate derivation and real pinned-rule acceptance are
covered below. #35 delivered supported condition evaluation, group selection,
tariff calculation and unbound-modifier refusal; #36 delivered explanatory
and near-miss presentation. Settlement
product exclusions are implemented under #145 (`szp-preparation-2`).

## Candidate Derivation From One Bundle

`derive_candidates(connection, dataset_id, encounter, service_date=...,
reporting_month=...)` reads every source from one opened, checksum-verified
reference bundle. The dataset manifest is the owner-reviewed composite: it pins
catalog, attachment 9, terminologies and the algorithm notice together.

- The algorithm notice, its three digests and the SZP rank and specialty
  tables come from the bundle (`algorithm_release`, `procedure_rank`,
  `department_specialty`). Exactly one notice must be present, and the engine
  still accepts only the reviewed DOCX digest.
- The service date is explicit and must lie within the episode. It selects the
  attachment 9 release (`resolve_characteristics`) and the catalog release
  (`resolve_ruleset`). The attachment 9 manifest must name that same catalog
  release and the algorithm's ICD-9 release, and both sheets (1a, 1ae) must
  resolve to the same attachment 9 release. Any disagreement is an error.
- Preparation then runs through the exact registry binding built from those
  sources, keyed by the dataset identity and the explicit reporting month.

A candidate is a group whose attachment 9 directional lists (paragraph 103,
"warunki kierunkowe") contain a prepared fact:

- Requirements come from every list-bearing condition of the group (presence,
  count, all-members, list coverage, date relation, laterality, procedure
  duration), following unions. Lists under a `not` never create a candidate.
  Unsupported nodes still contribute their lists, so the set errs towards
  inclusion; such candidates report `fully_interpreted=False`.
- A principal-role diagnosis list matches only the principal diagnosis; every
  other role matches any prepared diagnosis. Procedure lists match prepared
  procedures.
- Only retained facts make a `retained_fact` candidate. Unresolved facts
  (unknown attribution, missing rank) and classification families, which are
  never expanded, can only make an `unresolved_fact` candidate. Excluded facts,
  including rank-0 procedures, never count.
- Groups with no directional list are reported as `underivable`, never
  silently dropped.

Candidates carry the matched nodes, lists and attachment 9 evidence rows, and
`conditions="not_evaluated"`. Absence from the list is not proof that a group
fails; presence is not eligibility. Ordering is canonical (catalog, group).

The original uncached local measurement was about 200 ms, most of it building
the directional index. #149 subsequently delivered release-static caching;
#157 tracks the observed production warm-up timeout. Neither measurement nor
cache delivery establishes representative host capacity.

### #34 Acceptance Evidence

| #34 step | Evidence |
| --- | --- |
| 1. Bounded encounter model | `minos.domain.encounter`, `tests/unit/test_encounter.py` |
| 2. Exact registry, no fallback | `EngineRegistry`, unknown-binding tests |
| 3. Qualification, exclusions, ranking, candidates | `szp_2026.prepare`, `grouper.candidates`, `store.grouper_bundle` |
| 4. Typed trace | `Decision` events with source coordinates; candidate evidence rows |
| 5. Property-based determinism | Hypothesis tests for preparation and candidates |
| Acceptance: real pinned ruleset | `test_real_pinned_ruleset_prepares_and_derives_dated_candidates` |

The real-ruleset test is opt-in (`MINOS_GROUPER_BUNDLE`, a bundle built from
`manifests/sources/reference-conditions-public-validation.json`) and was run
locally on 2026-10-07. Hand-checked against attachment 9 46/2026 after 102:

- Head injury (principal S06.0, hospitalization level) with 01.01 performed in
  ICU (specialty 4260): the procedure is excluded, ward duration is 12 - 5 = 7
  days, and A01 is a candidate only through its principal-diagnosis list.
- Hernia repair 53.03 (SZP rank 3): F72 is a candidate citing row 7991,
  "wymagane wskazanie procedury z listy procedur F72".
- The same hernia in June resolves the original 46/2026 catalog and attachment
  9 together; an April date has no attachment 9 release and is refused.
- Reordered stays produce identical candidates and preparation traces.

## Eligibility Contract (#35, delivered supported subset)

Directional candidates are not eligibility. Reporting a group as valid requires
all of the following to hold together:

1. Exact catalog, attachment 9, terminology, algorithm, ranks and specialty source
   identities, plus product and contract/routing evidence needed by the pathway.
2. Explicit service date, reporting month and knowledge date within separately
   reviewed applicability windows. Neither the algorithm reporting month nor a
   catalog date resolver establishes the complete pairing by itself.
3. A versioned executable interpretation of each complete group expression,
   including guards, role, counting/allocation, missing-fact policy and all
   required roots. `proposed` or `review_required` is not executable approval.
4. An explicit supported-pathway contract. Missing product classification,
   contract facts, oncology attachment 3b or unresolved expression scope must
   produce a typed unsupported/unresolved result, not a skipped requirement or
   a fallback to another catalog/rank scope.

The #145 decision of 2026-10-09 binds the governing act's product catalogs:
W intervals exclude procedures inclusively and subtract end minus start under
the recorded owner interpretation. All five pinned acts lack a 1v catalog, so
V is not applicable to the supported source set; no V implementation is claimed.
Missing endpoints and ambiguous overlaps remain unresolved under the documented
policies. No exclusion type can be supplied by the encounter caller.

The selection-policy conflict is resolved (owner decision 2026-10-07, see
`decisions.md`): paragraph 113 leaves the choice among valid JGPs to the
reporting provider, so #35 returns every qualifying group ordered by base
points and highlights the highest value instead of reporting one winner.

## Source Binding And Product Boundary Verification

The direct-preparation dispatch claim and six-paragraph DOCX acceptance were
reproduced as two failing regressions before changing production code. Both now
pass. Source rotation is rejected at binding and dispatch boundaries; changing
the description digest with the same version also fails direct preparation.

Fresh byte-count/hash-verified publisher captures passed 91 focused tests,
including the opt-in pinned-source test. An installed wheel without development
dependencies exercised JSON parsing, all 4,889 SZP ranks and 521 specialties,
exact dispatch, ICU exclusion, missing SZP rank refusal, preserved duplicate and
unknown-date product occurrences, and complete result/trace determinism. The
wheel result remained preparation-only with an unbound product catalog.

Five manual mutations were killed in isolated copies: removal of the exact source
identity check, removal of the reviewed DOCX check, product deduplication, removal
of the domain product cap, and treating omitted reporting as complete. These
checks protect the owning behavior and add no test-only production hooks.

All repository pre-commit checks passed in a clean `uv sync --locked`
environment, including the full pytest suite, both type checkers and the license
policy. The first local-environment run passed other hooks but its license scan
found an unrelated installed `tqdm` absent from `uv.lock`; no license allowance
or local package was changed to bypass that result. Disposable captures, wheel,
virtual environments and mutation copies were cleaned. No issue closure or
deployment is included in this chunk.
