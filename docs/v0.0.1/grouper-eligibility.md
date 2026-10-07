# Grouper Eligibility, Base Points And Ranking

Issue #35. Entry point: `minos.grouper.pipeline.evaluate_candidates`. It
derives candidates (#34, [grouper execution](grouper-execution.md)), evaluates
each against the owner-approved executable subset of attachment 9 (decision
2026-10-07 in `decisions.md`), attaches catalog base points for the settlement
mode and ranks the valid groups. The result carries
`scope="base_points_without_modifiers"`; `final_points` always refuses.

## Result

Each candidate gets a `GroupEvaluation`: `status` (`valid`, `invalid`,
`undetermined`), the `conditions` and `routing` verdicts, reasons and one traced
verdict per evaluated condition node. Evaluation is three-valued (Kleene): an
unknown fact, rank role, contract scope or interpretation never becomes met or
not met. A group is `valid` only when its conditions and routing are both met and
the governing catalog has exactly one row for it on the service date.
Following paragraph 113, every valid group is reported; the provider chooses.

## Executed Rules (algorithm 1.1.2026)

| Rule | Behaviour |
| --- | --- |
| Diagnosis list | Principal role uses the principal diagnosis, secondary role the secondary ones, unspecified roles both. Only retained diagnoses meet a list. |
| Procedure list, ranks (75-79) | Ranks 3 and 4 meet a list. Rank 2 meets it only when the stay is under 2 days. Rank 1 never directs, so a rank-1 match is `undetermined` (whether the list is an additional condition is unreviewed). Excluded procedures never count. |
| `all` / `any` | Kleene AND / OR over child verdicts. |
| One code, two requirements (134, 143) | Two required procedures (or diagnoses) in one `all` must use different codes; if only a shared code fits, the `all` is not met. E11 and N24 (different-date exception) and requirements a nested expression might reuse stay `undetermined`. |
| Age | Completed years at admission against the threshold. Weeks and months stay `undetermined`. |
| Stay length | Algorithm duration (paragraphs 145-186) against the bounds, only with `product_reporting=complete` and no products. Otherwise `undetermined` (#145). |
| Procedure count | The largest set of list procedures with pairwise distinct codes on pairwise distinct days (a maximum matching, paragraphs 103 and 134); undated procedures share one day (136). Labels allowing repeats count distinct days. Ranks follow the procedure-list rule; rank 1, unresolved or family matches only make the count `undetermined`. Counts spanning other nodes, or overlapping a sibling procedure requirement, stay `undetermined`. |
| Discharge mode | The reported SWIAD discharge code against the extracted allowed codes (for example 9, death); unreported is `undetermined`. |
| Several reference roots | Alternatives: met if any root is met, not met if all are not met (owner decision 2026-10-07). |
| Conservative routing (105-111) | For non-procedural groups: allowed without procedures, with ranks below 2, or with rank 2 on stays over 1 day. A retained rank 3 or 4 procedure from the group's own lists directs to it (paragraph 105). Any other retained rank 3 or 4 procedure makes routing `undetermined` (paragraph 110: contract scope is not an encounter fact). Rank 2 on a stay under 2 days blocks it. N24 carries the paragraph 111 marker and is not restricted. |
| Governing catalog | No catalog row on the service date makes the group `invalid`; several rows make it `undetermined`. |

Not executable and always `undetermined`: diagnosis counts, list coverage, date
relations, laterality, procedure duration, ward duration, clinical measures,
programme membership, ORPHA codes, `not` (negative lists), `when`, unsupported
extractions and unexpanded classification families.

## Base Points And Ranking

`base_points` reads the governing catalog row's measures (points in hundredths)
and labels every answer `catalog_base_points_without_modifiers`. Columns follow
the attachment 1a headers:

| Settlement | Catalog 1a column |
| --- | --- |
| `one_day` type, admission = discharge date | G, one-day treatment |
| Admission = discharge date | I, same-day value |
| Algorithm duration 1 or 2 days | J or K |
| Otherwise | E (hospitalization) or F (planned) |
| Hospitalization type, duration over H financed days | plus (duration - H) x L |

A blank short-stay column keeps the group's standard value and is labelled
`short_stay_column_blank`; this reading of a blank cell is visible in every
answer. Catalog 1ae uses its own columns; its same-day and one-day stays depend
on a stay under 12 hours, which the encounter does not report, so they stay
undetermined. Points are also undetermined without a unique governing row, a
known product-free duration, a value for the reported mode, or consistent
one-day dates.

`ranking` lists exactly the valid groups, highest known total first (unknown
totals last, then catalog and code). Per paragraph 113 it is a choice for the
provider, not a single answer.

## Final Points Refusal

`final_points(connection, result)` never returns base points as final points.
Without a reviewed MODTAR binding for the service date it raises
`ModifierBindingUnbound` (every real bundle today; #46, #129). With a reviewed
binding it raises `ModifierEligibilityUnsupported`, because contract, provider
and legal-clause eligibility facts are not evaluated. That is a distinct
error, never "no modifiers apply".

## Evidence

Unit tests cover each rule; 13 deliberate breakages of the evaluator were each
caught. The opt-in real-bundle test hand-checks these #35 paths against
attachment 9 labels (46/2026 after 102):

- Surgical: an adult hernia repair (53.03, rank 3) makes F72 valid. PZF09 is
  invalid on age for the adult and valid for a 5-year-old.
- Conservative: a head injury (S06.0) whose only procedure was in ICU makes A01
  invalid (no retained procedure from list A01) and T07, "Leczenie zachowawcze
  urazów", valid.
- Stay length and counts: for a stroke (I63.9), A49 ("3 procedur z listy
  procedur A49; czas pobytu > 3 dni") is valid at 10 days with three distinct
  A49 imaging procedures (rank 3) on different days, and invalid at 2 days,
  without procedures, or with one code repeated.
- Death discharge: every extracted discharge-mode condition is unsupported, so
  that path stays `undetermined` until a reviewed interpretation exists.

- Ranking: for a 2-day child hernia, F72 (column K, 3959.00 points) ranks
  above PZF09 (no 2-day column, standard 3598.00 points, labelled
  `short_stay_column_blank`), and `final_points` refuses with
  `ModifierBindingUnbound`.

Base-point rules have their own unit tests; seven of eight deliberate
breakages were caught, and the eighth (`<=` to `<` on the financed-stay limit)
is equivalent at the boundary, where both give zero extra days.

- Death discharge: D45 (owner label grammar, see
  [conditions ingest](jgp-conditions-ingest.md)) needs principal J96.x from
  list D45, a secondary diagnosis from list D3, a procedure from list D45, age
  over 17 and a stay over 7 days or discharge mode 9. A 3-day stay is valid
  with death (9), invalid with an ordinary discharge and `undetermined` when
  the mode is not reported; a 10-day stay is valid either way.

Real-data note: on the 46/2026 after-102 sheet, 499 of 629 `1a` groups are fully
extracted (491 before the label grammar) and 612 have a single root. Groups using unapproved kinds stay
`undetermined`; widening the subset is an owner decision per kind.
