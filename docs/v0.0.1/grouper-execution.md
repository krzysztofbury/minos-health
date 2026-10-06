# Deterministic Grouper Execution: Preparation Slice

Issue #34 implementation chunk following the reviewed encounter boundary.
Entry point: `minos.grouper.registry.EngineRegistry.execute`. This slice runs
algorithm preparation; it does **not** return candidate JGPs, settlement points,
final eligibility or a legally complete ruleset. No HTTP/CLI grouping endpoint,
web change or deployment is included.

## Exact Dispatch And Source Ownership

An immutable registry explicitly binds a ruleset identity, algorithm version,
engine version, reporting month and exact `AlgorithmIdentity` to one immutable
algorithm snapshot. Every request value must match. Missing registrations and
unknown combinations
fail; no latest selector or engine fallback exists. The registry starts empty.
The first supported combination is `1.1.2026` / `szp-preparation-1`.

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
- Diagnoses and procedures attributed to excluded stays are excluded before
  ranking. Unattributed facts remain unresolved if any stay could be excluded;
  no ward is guessed from a procedure's date. All reported occurrences survive
  in the result, including excluded and unresolved ones.
- Rank 0 is ignored; ranks 1-3 record once-per-code policy; rank 4 records
  label-dependent repetition. Occurrences are **not** deduplicated yet. Missing
  rank is unresolved, never zero. Rank-2 routing, rank-4 allocation and E11/N24
  date exceptions require the later characteristic/routing binding.
- Completed years use exact birth date at admission or the explicitly reported
  years-at-admission alternative. A February-29 birth observed on February 28
  in a non-leap year remains unresolved: no anniversary convention is invented.
  Week/month age conditions are not evaluated.
- Ward-adjusted days use same-day duration 1, otherwise discharge minus
  admission, then subtract the union of wholly excluded stays. Overlapping and
  adjacent-day intervals are merged as in paragraphs 169-186. This is **not**
  final grouping duration: excluded products are not bound. Unknown stay
  exclusions produce unknown ward duration rather than a partial numeric value.
- Reported stays must cover the admission-to-discharge interval before ward
  duration or exclusion-free unattributed facts can be resolved. Missing coverage
  leaves those results unknown; coverage is not inferred from the episode dates.
- Reported settlement products retain all occurrences and independently missing
  start/end dates. `product_reporting=unknown` (the default) distinguishes omitted
  or partial reporting from an explicitly complete report, including a confirmed
  empty list. Neither status classifies products. Results preserve the canonical
  product tuple and report `product_binding=unbound`; a typed unresolved event
  cites the product rules. No W/V exclusions or product-adjusted duration run yet.

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

Still required for #34: composite source/date binding, reviewed executable
condition roots and supported policies, candidate derivation/routing, excluded
product classification and semantics, and real pinned-rule encounter acceptance. The
existing proposed/unsupported attachment 9 records are untouched. #35 retains
tariff calculation and unbound-modifier refusal; #36 retains full explanatory
and near-miss presentation. #34 remains open and nothing is deployed.

## Next Review Gate: Composite And Executable Binding

The source-aware preparation key is not a reviewed composite ruleset. Candidate
derivation must validate all of the following together before any eligible
candidate can be returned:

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

Product interpretation must distinguish inclusive W procedure intervals from
strict-interior V intervals (paragraphs 129, 140-141), and the different duration
adjustments (153-154). Missing endpoints, overlaps and short V intervals need
source-backed policies before subtraction; reported completeness does not resolve
them. No exclusion type can be supplied by the encounter caller.

The review also identified a selection-policy conflict: #35 asks for best-group
point ordering, while algorithm paragraph 113 leaves selection among valid JGPs
to the reporting provider. This chunk does not change the accepted specification
or implement a highest-points winner. Resolve that conflict explicitly before
implementing selection.

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
