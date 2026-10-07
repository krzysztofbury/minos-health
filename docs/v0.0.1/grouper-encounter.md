# Grouper Encounter Boundary

Issue #34, first implementation chunk. This is an internal reported-fact model,
not an executable ruleset, public grouping endpoint or settlement result.
No existing reference route or deployed artifact is changed.

## Facts And Bounds

`minos.domain.encounter` uses frozen standard-library dataclasses. The principal
diagnosis is an episode-level fact; secondary diagnoses and procedures retain
optional episode-local ward attribution. Procedures retain each occurrence and
its date, including unknown dates. Duplicate procedures are not removed: rank
4, distinct-date conditions and source-specific counting need those facts.

Ward stays retain department specialty, optional service code and calendar
admission/discharge dates. Overlaps and adjacency remain reported facts, not
silently repaired intervals. Stays must be within the episode. Known procedure
dates must fall within the episode and, when attributed, their ward stay.
Unknown attribution must never be interpreted as proof that no exclusion applies.

Settlement products retain reported code occurrences with independently optional
`started_on` and `ended_on` dates. Duplicate, overlapping and same-day products
are preserved. Known endpoints must be within the episode and ordered when both
are known. `product_reporting` is `unknown` by default (omitted or partial report)
or explicitly `complete`; a complete empty tuple reports no products, whereas
an unknown empty tuple cannot establish absence. Complete reporting can still
contain unknown dates. No product classification or W/V exclusion type is inferred
or accepted from the caller. The preparation result reports classification as
unbound until a reviewed product catalog and interpretation policy exist.

Diagnoses carry one reporting level: an episode-local stay attribution, an
explicit `hospitalization_level` report (the SWIAD `hospitalizacja` causes of
paragraph 143), or neither, which means attribution is unknown. A diagnosis
cannot claim both levels.

Age is either an exact birth date or reported completed years **at admission**,
never both. Completed years cannot supply week/month precision by inventing a
birth date. No age or duration computation is performed in this chunk. Discharge
mode is an optional reported numeric token, not a claim that a code is supported.
Hospitalization types are `hospitalization`, `planned`, and `one_day`; future
evaluation must use the selected source's duration rules, not a universal
inclusive-day formula.

Engineering input caps: 100 secondary diagnoses, 1,000 procedure occurrences,
100 stays, 1,000 settlement product occurrences, 16-character reported code
tokens, 32-character stay identifiers,
completed years 0-200, discharge tokens 1-99 and 256 KiB JSON. These are bounded
implementation limits, not clinically validated maxima or source completeness
claims. Exceeding them fails; facts are never truncated. Codes are preserved
exactly, without normalization, dictionary substitution or validity inference.

`minos.ingest.encounter.parse_encounter` is the strict Pydantic JSON boundary.
It rejects coercions, unknown fields and duplicate JSON keys, then constructs
domain facts. The domain independently enforces its invariants for Python
callers. Both layers avoid input-bearing error messages and default repr output.
The adapter performs no logging, persistence or network access. Explicit
serialization and debugger inspection still expose facts; callers must obey
the stateless, no-encounter-logging contract.

## Source Review And Next Gate

Reviewed the pinned NFZ algorithm `1.1.2026`, SHA-256
`a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c`,
after checking exact downloaded bytes against the existing komunikat manifest.
Paragraph numbers below are capture-local `w:p` positions, including table cells:

- 67, 103: whole episode and directional evidence across stays.
- 119-125: specialty/service/code exclusions require stay attribution.
- 129, 140-141, 153-154: product procedure-date exclusion and duration semantics
  require reviewed classification and interpretation. The model now preserves
  their input facts but does not evaluate them.
- 134-138: procedure repetition and missing dates affect counting.
- 143: principal diagnosis is episode-level; secondary input order is irrelevant.
- 145-186: duration depends on hospitalization type and exclusions, with explicit
  overlapping/adjacent-stay rules.
- 189: patient age is anchored at admission.

Before dispatch/evaluation, review the executable ruleset binding and supported
input subset. Existing attachment 9 condition roots are **reference roots**, not
an implicit conjunction; their policies remain `proposed`/`review_required`.
Neither this chunk nor a future empty registry may promote them automatically
to eligibility. Unknown ruleset/engine combinations must fail explicitly.

Outstanding #34 acceptance includes the registry, qualification, exclusions,
ranking, candidate derivation, typed intermediate trace, real pinned-rule
fixture encounters and property-based determinism tests. #35 owns tariff
calculation and must reject unbound modifiers; #36 owns the complete explanatory
and near-miss presentation. No #34 completion or settlement-accuracy claim is made.

The next chunk now executes a strictly labelled algorithm preparation slice;
see [grouper execution](grouper-execution.md) for exact registry/source ownership,
implemented exclusions/ranks, real-source evidence and the remaining #34 gates.

## First-Chunk Verification

`tests/unit/test_encounter.py` owns the reported-fact boundary. Synthetic inputs
exercise preservation of procedure occurrences and unknown facts, strict JSON
shape, immutable domain inputs, timeline/stay attribution, exact-limit and
over-limit collections, malformed/deep/oversized JSON, duplicate keys in
otherwise valid encounters, and non-disclosing failures/default representations.
These are not real clinical encounters or pinned-rule acceptance fixtures.

Full repository pre-commit checks passed, including pytest, Ruff, mypy,
BasedPyright, Bandit, lock/license policy and existing artifact checks. Manual
mutation checks in disposable scratch independently removed the domain procedure
cap, procedure episode-date guard, exclusive age-input guard and duplicate JSON
key check. All four were killed by observable boundary assertions. No test-only
production hook or new dependency was added. The in-memory JSON-to-domain path
also receives a separate smoke run; no production deployment is involved.
