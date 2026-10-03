# JGP Reference Delivery Scope

Accepted scope clarification: 2026-10-02. This is a delivery contract, not
evidence that the JGP data, endpoints or grouper are implemented.

## Sequence And Ownership

| Issue | First delivery | Deferred work |
| --- | --- | --- |
| #33 | Komunikat algorithm identity/effective reporting month, specialty dictionary and procedure ranks per pinned release, surfaced on ICD-9 code pages | Algorithm execution belongs to #34-#36 |
| #17 | Catalog group definitions and exact base points per settlement mode | Modifier calculations belong to #46/#35 |
| #18 | Bitemporal base-catalog model, explicit service-date resolution, per-provision applicability and an explicit unbound modifier state | MODTAR parsing, compatibility and binding belong to #46; condition rows belong to #30 |
| #24 | One bounded group-detail response with labelled base points, catalog parameters and governing release provenance; bounded group list | Structured conditions follow in #32 |
| #26 | Shareable date-specific group browsing/detail in the existing site layout | Version diffs belong to #104 after #20; condition-based ICD links belong to #32 |
| #29 | Source-backed condition taxonomy in parallel with the base-catalog work | Parsing cannot start in #30 before the taxonomy is reviewed |
| #30/#32 | Parsed conditions, date-consistent ICD cross-links and reverse group lookup | No claim that membership alone qualifies an encounter |
| #46 | Pinned MODTAR mappings, legal applicability and compatible ruleset binding | Final calculation consumes these in #35 |
| #35 | Condition evaluation, group selection and final points with full trace | Cannot return final points for a ruleset whose modifiers are unbound |

The estimated 4-8 hours on #33 covers ingestion, not an independently estimated
end-to-end API/UI delivery. Inspect the archived files before refining it.

## Base Points Are Not Final Points

API and UI must identify the displayed values as catalog base points without
modifiers. The visible Polish label is:

> punkty bazowe wg katalogu, bez modyfikatorów

The API must provide a machine-readable distinction as well as source/release
provenance. Exact field names are settled in the implementation PR; an
unqualified `points` or final-points value does not satisfy this contract.

A ruleset without a reviewed MODTAR binding carries an explicit unbound state.
That state is not an empty modifier list, a zero coefficient adjustment or a
claim that no modifiers apply. #46 checks pinned year, grouper type, version
and technical generation against applicability evidence before binding.

#35 rejects unbound, missing, incompatible or conflicting modifier evidence
with an explicit error before final tariff calculation. Only a valid binding
and completed eligibility evaluation can establish that no modifier applies
to a particular encounter. Regression tests must distinguish those two states.

## Temporal Acceptance

#18 retains separate publication, entry-into-force, service-applicability and
knowledge-time evidence. Service-date resolution uses reviewed release/row
applicability; it does not choose the latest act or dictionary.

Required base-catalog scenarios:

- Retroactivity: 74/2026 published on 2026-07-23 applies to services from
  2026-07-01. A service date before publication can resolve amended values;
  a knowledge-time filter can recover the earlier known state.
- Per-provision applicability: section 4(5) of 102/2026 defers specified catalog
  rows to 2027-01-01. Tests on 2026-12-31 and 2027-01-01 must resolve those
  rows and base values according to reviewed source evidence, without applying
  them early or extending superseded values implicitly.
- Overlapping release validity is resolved explicitly and deterministically;
  an unresolved ambiguity is an error.
- No reviewed ruleset covers the chosen date: return `NoRulesetForDate` and
  a visible error, never a latest-release fallback.
- Group data, catalog parameters and provenance use the same resolved date
  and release. A shareable URL retains the group and service-date selection.

Section 4(3)'s prior section 17 coefficients during the quality-indicator
transition and section 4(4)'s section 17(6a) start on 2026-10-01 belong to
#46's modifier applicability tests. Moving those tests must not move section
4(5)'s base-catalog boundary out of #18.

## Group Browser Acceptance Split

First #26 delivery: a coder can find a group, choose a service date, see base
points per settlement mode and the applicable time-limited catalog parameters,
identify the governing act and catalog version, and share the same answer by
URL. Group detail requires one API call under #24. An uncovered date yields
an explicit error.

The deferred version-diff issue #104 depends on #20, #24 and #26. It owns the two
date/act selectors, changes to group membership and base points, shareable
comparison URLs and the original acceptance:

> What changed for kardiologia in 102/2026 is answerable in two clicks.

Condition-based group-to-ICD links and the ICD-page reverse lookup belong to
#32 after #30. They must use the same selected date/release and distinguish
list membership from full qualification conditions.

The owner-approved [#30 correspondence contract](jgp-code-correspondence.md)
keeps NFZ attachment code/title identities source-local and stores exact
dictionary matches or absence separately. An unmatched member remains visible;
it must not disappear, become an invented dictionary concept or link to an
unreviewed replacement. The same contract defines watcher capture, reviewed
manifest changes, rebuilding and immutable-artifact publication across consumers.

## UI And Review Gates

The 2026-10-01 #102 interface is the baseline, not the superseded #101 page.
Reuse `apps/web/site/layout.html`, `apps/web/site.css`, the site builder and
the server-rendered code-page shell. Preserve search-first home, dictionary
trees, code-page URLs, keyboard/mobile behavior, typography and source/privacy
notices. Do not reintroduce the old standalone search page or stylesheet.

For #33, add rank evidence to existing ICD-9 code pages without redesigning
them or interpreting a procedure rank as clinical priority or billability.
Show the pinned algorithm/rank source identity and reporting applicability;
missing evidence is not rank zero. Do not infer an unreviewed ruleset pairing.

Substantial or safety-sensitive changes require pair-programmer review,
relevant boundary tests and full repository gates before submission. Actual
UI changes additionally require real-browser desktop/mobile verification
against the #102 baseline. Each implementation is submitted as a reviewable
PR; no merge or deployment happens before owner approval.

## External Evidence

After rank and base-point pages are live, show them to the surgeon and request
the three complete, independently reviewed anonymized cases for #37/#38.
Do not retain patient identifiers or full clinical records. Missing access
does not block source/reference work. Public/synthetic rule fixtures remain
distinct from real ward validation and cannot justify a settlement-accuracy
claim.
