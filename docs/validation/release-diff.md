# Release Diff And Conditions Integrity Evidence

Date: 2026-10-05. Scope: #20 and #31 offline selected-release comparisons on
`feat/release-diff-condition-integrity`, plus the owner-confirmed #21 policy.
The implementation validation below did not connect to production, activate a
release, send notifications or change the UI. Subsequent deployment receipts
are recorded on the delivering PR rather than inferred from these local checks.

## Contract And Review

See [the CLI contract](../v0.0.1/release-diff.md). Before/after TigerStyle review
identified and corrected anonymous hierarchy occurrence collapse, missing
node/selector evidence bindings, incomplete provenance identity, ambiguous
Markdown pair attribution and cumulative retained-value bounds. The final
remaining byte-accounting correction includes pair/category/identity text;
workbook `worksheet` coordinates are now provenance, not concept content.

Focused tests exercise complete synthetic build/open/CLI paths and logical graph
boundaries: explicit pair rejection, corrupt/bare bundles, deterministic output,
add/remove/title/status/date changes, exact base-point deltas and absent cells,
twin applicability rows, all nested reference-ID/row shifts, duplicate list/member
populations, guard occurrence identity, unsupported-only text, evidence-binding
changes, identical anonymous-container reparenting, safe URL identity and resource
exhaustion without partial success output. Existing ingestion/integrity/date tests
remain the owning proof for source validity; this increment does not duplicate
them or introduce an executable grouper.

## Pinned Real-Source CLI Acceptance

Private complete-bundle build: schema 8, 437,612,544 database bytes, SHA-256
`84e1731e3ac6dc0512c4f461145c7f433cbd014aa439c16dfca37d50185e5ee8`.
Build elapsed 132.258 seconds on this development machine, not the VPS.
Each pair ran through full CLI JSON twice plus Markdown preview, including
verified bundle opening. Repeated full outputs were byte-identical within each
pinned bundle. Full reports had no presentation truncation or local scratch path.
Retained evidence contains counts/digests, not raw titles or source rows.

| Selected pair | Reviewed logical boundary | Evidence |
| --- | --- | --- |
| CZS generation 92 / 5.89 -> generation 93 / 5.90 | Concepts | 1 added, 12 changed, 0 removed; 10,178 -> 10,179 concepts |
| Catalog original 46/2026 -> after 74/2026 | Group populations/base values | 166 added, 120 changed, 120 removed group populations; 31 changed existing base-value populations |
| Catalog after 74/2026 -> after 102/2026 | Group populations/base values | 44 changed group populations, 28 changed base-value populations; no added/removed group populations |
| Conditions original 46/2026 -> after 74/2026 | Group graphs | 166 added, 116 changed, 120 removed; separate 1a/1ae contexts |
| Conditions after 74/2026 -> after 102/2026 | Group graphs | 2 added, 13 changed, 0 removed in 1a; no graph changes in 1ae |

For conditions after 74 -> after 102, directional-list populations report 9 added,
15 changed and 4 removed identities; exact evidence and its graph associations
remain visible. Catalog correspondence changes also include the explicitly
changed catalog release pin, so their counts are not counts of clinical changes.
Source movement, applicability and provenance categories are separate evidence,
not additional independent diagnoses or billable rule changes.

The predecessor is now pinned in `icd9-czs-previous-reviewed.json` with SHA-256
`73e9ed9531b21af782fbb45f2ded9354ccad5a83d8bc5a5771e2e553758604ae`
and 1,630,632 bytes. The capture used the explicit generation-92 URL and bounded
download, not a latest-version selector. Raw bytes were disposed with task scratch.
An opt-in compatibility golden test rebuilds both pinned releases from a private
archive via `MINOS_RELEASE_DIFF_PRIVATE_ARCHIVE`; ordinary CI skips it when the
licensed source bytes are absent.

Three CLI invocations together took about 2.1 seconds for ICD-9, 15.6-16.1 seconds
for catalog pairs and 27.4-27.5 seconds for conditions pairs. These are development
observations including repeated integrity verification, not benchmarks of search,
production capacity or superiority over incumbent workflows.

## Limits And Remaining Acceptance

Final checks: 1,602 tests passed, 15 skipped in the ordinary suite. Both private
gold tests were run separately and passed with their exact source opt-ins.
All repository pre-commit checks passed, with pytest run separately inside owned
scratch; Ruff, mypy and BasedPyright were warning-clean. A fresh built wheel in
an isolated environment executed the real CLI, rebuilt two synthetic bundles
and reported the expected E11.0 title change. Three disposable source mutations
were killed by their owning regression tests: anonymous-container collapse,
duplicate guard-occurrence collapse and omission of identity bytes from the
retained-change budget. These checks were recorded before submission.

- This is selected-reference comparison, never a complete #21 promotion audit.
  Detailed MODTAR, algorithm/rank and search-index changes remain outside scope.
- Readable code/list/group evidence is not independent legal or practitioner
  confirmation. Owner acceptance of the two-minute ICD-9 review remains a human
  usability gate; machine tests do not establish it.
- Conditions parsing currently preserves proposed/unsupported states. Comparing
  them does not validate encounter qualification or missing MODTAR applicability.
- #21's automatic policy is documented, but selection, trusted promotion,
  rollback, durable notification delivery and actual private Telegram acceptance
  remain unimplemented. No routine automatic deployment is enabled here.
- Scratch initially hit read-only fixture-directory cleanup and cross-device
  hard-link issues; only owned directory permissions were repaired, no privilege
  escalation was used. Full-suite fixture/browser failures from scratch's restrictive
  umask passed after explicitly restoring the repository's 0022 test umask.

Compact local receipts are retained under ignored
`test-output/release-diff-policy-ljyz7b2_/`. They are not production receipts.
