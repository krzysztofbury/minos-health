# Komunikat Algorithm Metadata And Procedure Ranks

Issue #33 adds separately attributed reference evidence to the existing ICD-9
code pages. It does not implement a grouper, resolve a service date or bind a
complete legal ruleset. #18 supplies that later binding boundary.

## Reviewed Source Layout

The NFZ watcher captured all three files linked by komunikat 54. The new
`manifests/sources/reference-ranks-public-validation.json` pins those exact
archive bytes and preserves the prior terminology selection unchanged. The
previous public manifest and historical bundles are not rewritten.

| File | SHA-256 | Bytes |
| --- | --- | --- |
| Algorithm DOCX | `a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c` | 186371 |
| Rank XLSX | `51e587c2df590fa23cb10c1773edeb9cc8a0b2e630cbb2e360206337b7aed13e` | 331428 |
| Specialty XLSX | `ad6616ce9d426f08e423d6b1b4c11000a2a150f5936f4d1993dd839570f128d2` | 39024 |

The DOCX identifies algorithm `1.1.2026`. Its versioning section explicitly
defines Y as the first reporting month and ZZZZ as the year. Thus the first
reporting month is January 2026, not the April publication month. No validity
end or compatibility with an arbitrary service-date ruleset is inferred.

Both workbooks have three columns and three header rows, without merges. The
parser checks exact titles, worksheets, header values and cell types. Blank
styled rows are not records. Source descriptions, leading zeros, worksheet
identity and original row numbers are preserved.

| Table | Worksheet | Populated rows |
| --- | --- | --- |
| Ranks | SZP 2026 | 4889 |
| Ranks | AOS 2026 | 1662 |
| Specialties | SZP 2026 | 521 |
| Specialties | AOS 2026 | 462 |
| Specialties | REH 2026 | 616 |

The specialty workbook is not a dictionary of department names. Its columns
are department-specialty code, service code(s), and ICD code(s). Values such
as `WSZYSTKIE` and service code `4` remain raw evidence; exclusions/filtering
are not executed during ingestion. SZP, AOS and REH records are never merged.

The SZP rank distribution is two rank-1 rows, 4881 rank-3 rows and six rank-4
rows. It has no explicit rank-0 or rank-2 rows. Missing rows must not become
rank zero. Rank 4 retains its distinct repeat-use semantics; it is not
silently relabelled as rank 3. All 4889 SZP codes have exact correspondence in
the pinned CZS 5.90 catalog. Correspondence does not establish legal ruleset
pairing or billability, and rank-file titles do not override CZS titles.

## Artifact And Lookup Contract

Dataset manifests optionally carry `komunikaty`. Its omission preserves the
historical serialized manifest and dataset identity. Each entry contains the
three pinned files, reviewed worksheet counts, algorithm identity, terms and
an explicit `reference-only` correspondence to one canonical CZS release.
Ambiguous correspondence, supporting-workbook targets and mixed notices fail.

Terminology-only builds retain schema 2. Builds containing komunikat evidence
use schema 3 and append `003_komunikat.sql`; migrations 1 and 2 are unchanged.
The reader checks the appropriate migration checksum and table set, and
compares persisted algorithm source/version/correspondence metadata with the
artifact manifest. Old immutable schema-2 bundles remain readable.

New tables are `algorithm_release`, `procedure_rank` and
`department_specialty`. Rank and specialty keys include release, grouper type
and exact source code. They do not add canonical concepts or search matches.
The algorithm release stores its first reporting date, literal version text
and paragraph identity. The source manifests carry observation timestamps and
checksums. These are release-scoped evidence, not #18's bitemporal legal model.

`GET /api/v1/procedure-ranks/{icd9_release_id}/{code}` returns only SZP evidence
for an exact canonical ICD-9 code. It provides:

- `recorded` plus a source rank, including an explicitly recorded zero;
- `not_listed` plus null rank when the selected SZP sheet has no row;
- `not_selected` plus null rank for a terminology-only bundle;
- algorithm/rank source identities, checksums, terms, worksheet and row where
  present, plus the first reporting month;
- `pairing: reference-only` when evidence is selected, and
  `ruleset_binding: not_bound`, never implied legal compatibility.

Unknown codes or noncanonical releases return 404. Queries in URLs remain
rejected. Telemetry uses a fixed rank-route label, not the code or free text.
Existing ICD-9 pages render that same evidence in the #102 shared layout.
ICD-10 pages do not gain a procedure-rank section. Raw archive access stays
private; download links point upstream.

## Algorithm Code Boundary

Only version identity/reporting-month metadata enters the database from DOCX.
The following sections inform later versioned engine code, not inferred data
rows or executable document content:

| Opis section | Future owner |
| --- | --- |
| 1.1: rank definitions, including distinct ranks 1-4 | #34 rank interpretation/input preparation |
| 4.1-4.4: grouping order and surgical/conservative candidate flow | #35 candidate evaluation and selection |
| 4.5: specialty-table stay exclusions | #34 input preparation |
| 4.6: procedure/date exclusions for separately reported products | #34 input preparation |
| 4.7: grouping conditions, counts and rank-4 repeat use | #35 condition evaluation and selection |
| 4.7, hospitalization duration and age subsections | #34 fact preparation, #35 condition evaluation |
| 4.8: verification against the contract scope | Future explicitly supplied eligibility facts; no inference from ranks or reference pages |
| 4.9: base points, additional bed-days, short-stay values and modifiers | #35 tariff determination after #46 |
| 4.10: reporting and permitted summation with separately settled products | Future reporting boundary; not implemented by #33 or the first base-point browser |
| Evaluation decisions and source citations across these sections | #36 trace and near-miss reporting |

The 2026 DOCX must be reviewed again when those issues are implemented. In
particular, rank-4 behavior cannot be replaced by a generic `rank >= 3` label.

## Security And Acceptance

XLSX and DOCX share bounded ZIP/XML preflight: entry counts, source/expanded
bytes, CRC, paths, expansion ratio, XML depth/elements/text and external/active
content guards. DOCX media entries are bounded inert bytes, never decoded or
executed. Tracked revisions, an unexpected main-document relationship,
ambiguous version identity and unknown reporting-month semantics fail.

Synthetic generated Office fixtures exercise parsing, drift and hostile-input
guards without committing publisher bytes. Real SQLite, HTTP, CLI and HTML
tests protect source/title separation, grouper isolation, absent-versus-zero
behavior, deterministic builds, rollback and manifest/database drift. The
container acceptance includes rank-page checks as well as existing #102
search/tree/keyboard/privacy checks. UI acceptance requires visual review of
real Chromium desktop/mobile screenshots, not HTML grep alone.

This source selection is for noncommercial public validation, not commercial
legal clearance. RSK content remains outside any paid calculation dependency.
