# Grouper Eligibility: Approved Condition Subset

Issue #35, first chunk. Entry point: `minos.grouper.pipeline.evaluate_candidates`.
It derives candidates (#34, [grouper execution](grouper-execution.md)) and
evaluates each against the owner-approved executable subset of attachment 9
(decision 2026-10-07 in `decisions.md`). Base points and modifiers come in later
chunks; this result carries `scope="conditions_without_points"`.

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
| Conservative routing (105-111) | For non-procedural groups: allowed without procedures, with ranks below 2, or with rank 2 on stays over 1 day. A retained rank 3 or 4 procedure makes routing `undetermined` (paragraph 110: contract scope is not an encounter fact). Rank 2 on a stay under 2 days blocks it. N24 carries the paragraph 111 marker and is not restricted. |
| Governing catalog | No catalog row on the service date makes the group `invalid`; several rows make it `undetermined`. |

Not executable and always `undetermined`: procedure and diagnosis counts, list
coverage, date relations, laterality, procedure duration, ward duration,
discharge mode, clinical measures, programme membership, ORPHA codes, `not`,
`when`, unsupported extractions, unexpanded classification families and groups
with several reference roots.

## Evidence

Unit tests cover each rule; 13 deliberate breakages of the evaluator were each
caught. The opt-in real-bundle test hand-checks these #35 paths against
attachment 9 labels (46/2026 after 102):

- Surgical: an adult hernia repair (53.03, rank 3) makes F72 valid. PZF09 is
  invalid on age for the adult and valid for a 5-year-old.
- Conservative: a head injury (S06.0) whose only procedure was in ICU makes A01
  invalid (no retained procedure from list A01) and T07, "Leczenie zachowawcze
  urazów", valid.
- Stay length: a stroke (I63.9) without procedures makes A49 ("czas pobytu > 3
  dni") invalid at 2 days and `undetermined` at 10 days, because its
  three-procedure count is outside the subset.
- Death discharge: every extracted discharge-mode condition is unsupported, so
  that path stays `undetermined` until a reviewed interpretation exists.

Real-data note: on the 46/2026 after-102 sheet, 491 of 629 `1a` groups are fully
extracted and 612 have a single root. Groups using unapproved kinds stay
`undetermined`; widening the subset is an owner decision per kind.
