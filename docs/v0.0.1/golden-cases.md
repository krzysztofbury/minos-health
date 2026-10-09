# Golden Grouping Cases (#37)

`tests/golden/grouping-cases.json` holds reviewed encounters with expected
grouping outcomes. `tests/integration/test_golden_cases.py` sends each one
through the HTTP boundary. The cases verify the implementation against the
stated rules; they are not NFZ settlement or adjudication evidence. The binding
result remains NFZ's own settlement system.

## Evidence Levels

Every case records one evidence level, one interpretation basis, its sources
and the rule paths it covers.

| Evidence level | Meaning | Runs |
| --- | --- | --- |
| `public_verified` | Complete public case: inputs, ruleset and expected result checked against the publication | CI, once one exists |
| `derived_from_pinned_source` | Hand-derived from the pinned real acts and algorithm | Opt-in, `MINOS_GROUPER_BUNDLE` |
| `synthetic_rule_fixture` | Synthetic bundle shaped like the reviewed sources, expected values derived from its rules | CI |

`interpretation` is `algorithm_text` when the expectation follows from the
algorithm wording, or `owner_decision_2026_10_09` when it depends on the
settlement product decisions in `decisions.md` (for example, a W product
subtracting end minus start from the duration). Expected values are derived by
hand from the cited rules, never recorded from engine output. The real bundle
holds licensed generated data, so its cases stay out of CI.

## Public-Source Search (2026-10-09)

A timeboxed search of NFZ JGP communications, the algorithm 1.1.2026 publication
notice and the NFZ training material (version 3.1) found no complete worked
case: published material explains the method without full inputs, and the
training material predates algorithm 1.1.2026 and the pinned acts. No case is
therefore `public_verified`. A public case enters the suite only with its URL,
date, governing ruleset, licence, complete inputs and expected result.

## Covered Rule Paths

- Directional candidates and conditions: procedure and principal-diagnosis
  presence, `all` expressions, a group that no retained fact reaches
  (`not_candidate`).
- Duration without exclusions.
- Settlement products: W exclusion, inclusive end date, W outside the procedure
  date, neutral product of another catalog, the case's own JGP product, unknown
  product, undated procedure partly covered by a W product, adjacent W products,
  W duration subtraction, incomplete product report.
- Act selection: the same product code is neutral for an October 2026 service
  date (act 46/2026 after amendment 102 lists it in 1b) and unknown for May 2026
  (the original act does not); each case asserts the classifying release.

Engine unit tests cover further boundaries (ward interplay, same-day stays,
missing endpoints, a duration of 0 or less) at the preparation boundary.

## Divergences

Each divergence gets a written disposition here: our defect, the other
source's defect, or an ambiguous description that the trace must expose.

- 2026-10-09, `real-hernia-w-product`: the first hand derivation expected F72 to
  be invalid once a W product excludes 53.03. The engine answered "not
  evaluated". Disposition: our derivation error. Candidates come only from
  retained or unresolved facts on directional lists, and K40.9 is not on list
  F72, so F72 is correctly not a candidate. The expectation now reads
  `not_candidate`.
