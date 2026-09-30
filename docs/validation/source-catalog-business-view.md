# Source catalog business view

Date: 2026-09-30. Scope: the owner's three live-site review points, on branch
`fix/source-catalog-business-view`. This is not completion of the wider issue
#80 audit or implementation of NFZ projection issue #92.

## Plan review

The pair-programmer review required:

- TigerStyle #4: Paired assertions. Persist verified BAW role and lineage,
  validate them again before publication and at the browser input boundary.
  Never group records by a shared display name alone.
- TigerStyle #2: Bounded loops. Retain 32 documents, four selected files per
  document, 512 MiB per file, file-tree and ZIP limits, and the run deadline.
- TigerStyle #6: Positive and negative space. Capture full and shortened
  comparison packages privately. Missing required packages or conflicting
  source relationships fail the run instead of producing an empty file list.
- TigerStyle #12: Full error handling. Preserve immutable legacy output and
  per-provider last-good/error behavior. Never manufacture legacy lineage.

The accepted implementation uses the existing static page, bounded JSON
projection, private archive, and watcher runtime. No service, dependency,
public download route, patient input, or reference-data import was added.

## Implementation

- Homepage and sources copy address physicians and hospital settlement staff,
  distinguish available catalog browsing from planned code and JGP search,
  explain source names, and keep legal dates separate from checking dates.
- RSK file rows place filename, size/format, and an accessible external-source
  icon on one desktop row. Mobile uses two lines. Technical checksums remain
  in a separate collapsed disclosure. Polish ICD-10 and ICD-9 precede other
  dictionaries; base acts precede amending acts.
- New BAW observations publish schema 2 with verified original and introductory
  act relationships. Consolidated and comparison documents share one act card
  and states keyed by introductory amendment. Amending acts stay distinct and
  linked. Amendments without a separate consolidated snapshot are explicitly
  listed rather than omitted or given an invented version.
- Root ZIPX packages for both comparison roles now use the existing validated
  private capture path. Selected individual catalogs retain their exact file
  identities. Publication filters retained evidence by the current selected
  inventory and rejects missing required evidence.

## Executed evidence

An isolated filesystem archive exercised the actual CLI against anonymous BAW
APIs with both original roots, `43718` and `43868`. Production was not accessed
or modified by the collector smoke test.

| Check | Result |
| --- | --- |
| First complete manual capture | 19 documents, 52 selected files, 1,069,864,050 selected bytes, 251.454 seconds |
| Repeat manual byte verification | 234.063 seconds; no additional source-change record |
| Former metadata-only comparison records | `43771` and `43835` have verified archived ZIPX products |
| Other discovered comparisons | All eight full/short comparison records have archived products |
| Unchanged scheduled path | 23 metadata requests, zero binary downloads, 79.762 seconds |
| Public projection | Schema 2, about 18 KB, empty redistribution artifact list |
| Legacy restore comparison | Byte-identical to the pre-change builder for both legacy metadata shapes |
| Local Chromium with real RSK metadata and new BAW output | 15 cards; one `101/2025/DSOZ` card, three published snapshots, seven document variants |
| Fixture browser checks | Desktop, 390 px and 320 px mobile, expanded history/files, reduced-width zoom layout, keyboard tabs/disclosures, provider failure/retry, legacy data, hostile text |
| Private-file boundary | Exact public metadata routes pass; artifact routes remain 404; no archive or file downloads during browser rendering |
| Full pytest | 811 passed, 8 environment-dependent skips |
| Repository gates | Pre-commit passed, including Ruff, mypy, BasedPyright, Bandit, tests, lock and license policy, Terraform format, and workflow lint |
| Other gates | Git-history secret scan and dependency audit passed; source distribution and wheel built |

The scheduled test made only the disposable local schedule due; it used real
collector HTTP, recovery publication, reconciliation, and public publication.
It did not change any production schedule or disable any production service.
Manual capture verifies every selected product again; its transfer and duration
must not be confused with the unchanged daily path.

Raw bytes, temporary runtime databases, and real-data screenshots remain
outside Git. Synthetic browser fixtures are not legal applicability evidence.
The zoom check is a reduced CSS viewport at increased device scale, not a claim
of manually verified browser zoom or assistive-technology task completion.

## Final pair-programmer review

Safety findings resolved:

- Pydantic's integer `Literal` matcher accepts `True` and `1.0` even under the
  strict model setting. The role field now explicitly rejects non-integers;
  publication regression cases cover that installed-library behavior.
- Historical file evidence must not appear as a current inventory item after
  source removal. Current selected IDs fence publication; tests cover removal
  and missing evidence.
- Comparison download failure, missing root package, source-document drift,
  and relationship drift must not advance the cursor or public generation.
  Integration cases exercise those rollback boundaries.

Performance and DX review: capture bounds and streaming are unchanged; the
expanded real graph completed inside the existing deadline. Unchanged daily
collection reused all products. New projection helpers stay below 70 lines;
no frontend framework or test-only production export was introduced. No claim
of being faster than another product follows from these measurements.

## Deployment boundary

These are local implementation and verification results, not deployed evidence.
Production must receive the reviewed branch through its normal publishing and
activation path, followed by a fresh BAW run. Old schema 1 generations remain
immutable and show a labeled lineage limitation until that run succeeds.
Measure the real activation and expanded production capture, verify private
S3 receipts and representative host capacity, then inspect both live pages in
desktop and mobile Chromium before declaring production completion.
