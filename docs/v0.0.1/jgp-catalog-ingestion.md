# JGP Catalog Ingestion (Attachment 1a)

Issue #17 ingests the JGP group catalog of attachment 1a (`Zalacznik1a.xlsx`)
into release-scoped group rows and catalog base points per settlement mode.
It does not resolve a service date, select a release or apply modifiers:
#18 owns service-date applicability, #46 and #35 own modifiers and final
points. Every stored value is a catalog base point without modifiers
("punkty bazowe wg katalogu, bez modyfikatorów").

## Pinned Releases

`manifests/sources/reference-catalog-public-validation.json` extends the
ranks validation dataset with three releases of the `46/2026/DSOZ` lineage.
The bytes were fetched from the anonymous BAW file endpoint on 2026-10-02 and
accepted only because their size and SHA-256 equal the BAW watcher's captured
evidence in the public provenance projection.

| Release | BAW document | Role | Act date | File | SHA-256 | Bytes |
| --- | ---: | --- | --- | ---: | --- | ---: |
| Original act | 43868 | original (1) | 2026-04-30 | 29620 | `3d4197ceb459bcd813f315de7558cffbf4827f989571e7064b336b554a4ec7a0` | 276556 |
| After 74/2026 | 43943 | consolidated (3) | 2026-07-23 | 30983 | `fbe73d5fb7a3466677e1c8488206de0ff71b945f801b6032f2256f484f6592e0` | 296386 |
| After 102/2026 | 44042 | consolidated (3) | 2026-09-23 | 32994 | `32dc947fc6ac9353d17ca9ba18b495ae2b5458ca5edc971eb47dd0a0c70e92b2` | 304191 |

Act dates, entry into force and BAW lineage are recorded as publication
evidence only. 74/2026 applies from 2026-07-01 although published later; that
service applicability is #18's reviewed evidence, not something this import
infers from dates.

## Reviewed Layouts

The workbook has catalog `1a` and, from 74/2026, catalog `1ae` as a second
worksheet. `1ae` is a separate catalog inside attachment 1a, not a duration
filter. `src/minos/ingest/jgp_catalog_layout.py` pins three layouts:

| Layout | Worksheet | Values | Scope columns | Notes |
| --- | --- | --- | --- | --- |
| `nfz-1a-46-2026` | `1a - katalog grup` | E-L | M-CB | CC |
| `nfz-1a-74-2026` | `1a - katalog grup` | E-L | M-CB | CC |
| `nfz-1ae-74-2026` | `1ae - katalog grup` | E-J | K-BZ | CA |

The two `1a` layouts share column meanings but not header placement: the
original act puts the E, J and K headers on row 4 with different merges. Each
layout pins every non-empty header cell in rows 1-5, the column-number row
(which skips 5), all header merges, the 68 scope labels, the accepted titles
("Katalog grup", plus "Katalog grup 1a"/"1ae" after 102/2026) and the
four-line footer legend. Any moved, renamed or added column, an extra header
cell beyond the layout, a merged group row or a changed legend fails the
build. Column meaning is never found by searching header text.

| Column (1a) | Measure | Unit |
| --- | --- | --- |
| E | `hospitalization` | points |
| F | `planned_hospitalization` | points |
| G | `one_day_treatment` | points |
| H | `financed_stay` | days |
| I | `same_day_discharge` | points |
| J | `one_day_stay` | points |
| K | `two_day_stay` | points |
| L | `extra_stay_day` | points |

| Column (1ae) | Measure | Unit |
| --- | --- | --- |
| E | `hospitalization` | points |
| F | `planned_hospitalization` | points |
| G | `one_day_treatment` | points |
| H | `day_stay_under_12_hours` | points |
| I | `financed_stay_excluding_day_stay` | days |
| J | `extra_stay_day_excluding_day_stay` | points |

The `1ae` stay measures explicitly exclude day stays under 12 hours, so they
keep distinct codes rather than sharing the `1a` ones.

## Row Rules

- Rows are classified as blank, chapter heading, group row (integer `Lp.`)
  or footer legend. Anything else fails, including a group before the first
  chapter. A chapter heading is text in column A only and must carry the
  full-width merge every reviewed heading has; text in column A without it is
  rejected, as is a chapter with no groups. The manifest pins the chapter
  count per sheet (19 in `1a`, 16 in `1ae`). The only other body merges
  accepted are footer legend lines.
- Points are stored as integer hundredths. Workbook floats such as
  `12871.720000000001` (PZD02) must be exact hundredths; three decimals fail.
  Financed stay is a day count and must be an integer. Amounts are bounded
  (1,000,000 points, 366 days), far above every reviewed value.
- An empty or `''` cell has no stored value. It is never zero, and zero or
  negative values fail. Every group needs at least one points value.
- Group codes match `[A-Z]{1,4}[0-9]{2,3}[A-Z]{0,3}` (for example `A01`,
  `AU35C`, `DU05III`); the issue's "letter plus two digits" assumption does
  not hold. Product codes are `5.51.01.*` in `1a` and `5.51.02.*` in `1ae`.
  Surrounding whitespace, including NBSP (`'J01AJ '`, `'\xa05.51.01.0014052'`),
  is trimmed; nothing inside a code is altered.
- Names, notes (`Uwagi`) and chapter headings are preserved; chapters have
  whitespace normalized. A whitespace-only note is no note. A trailing `*` marks a procedural group, as the
  footer legend defines.
- Scope cells hold the raw legend markers: 1 first, 2 second, 3 every scope
  named in the column header. Splitting a header into its named scopes is
  deferred; labels are stored exactly as published.
- `Lp.` is evidence only. The after-74 `1ae` sheet repeats 65-68, so it is
  never a key.
- Cells outside the reviewed columns are ignored as data. The real workbooks
  keep 138 stray formulas (mostly `=#REF!`) from column DL; a formula, error or
  date inside the reviewed columns fails.

## Repeated Codes

After 102/2026, 19 `1a` codes and 6 `1ae` codes appear on two rows. In the
pinned release each pair has the same base points (asserted by the `jgp_live`
test) but a different scope set and note: one row may be
reported in a scope until 31.12.2026, its twin applies to services reported
from 1.01.2027. This is section 4(5)'s deferred catalog boundary. #17 keeps
both as separate rows keyed by release, catalog and source row; #18 decides
which applies to a service date. The manifest pins the repeated-code count per
sheet, a third occurrence fails, and a code may not map to two product codes
or appear in both catalogs.

## Storage

Schema 4 is used only for catalog-bearing builds and contains everything in
schema 3, so schema 2 and 3 bundles stay valid. Tables:

| Table | Content |
| --- | --- |
| `catalog_release` | BAW document identity, lineage role, act date, entry into force, workbook SHA-256, canonical manifest |
| `catalog_sheet` | layout version, worksheet and title per catalog |
| `catalog_scope` | the 68 scope columns with source column and label |
| `group_definition` | one row per source row: ordinal, code, product code, name, procedural flag, chapter, notes |
| `group_base_value` | base points (hundredths) or financed days per measure, with source column |
| `group_scope` | raw scope markers per group row |

SPEC's `tariff_value` is named `group_base_value` here so stored values cannot
be mistaken for final tariffs; validity periods arrive with #18. The schema
ties `days` to the two financed-stay measures and points to every other
measure. Opening a bundle re-checks that stored releases, layouts, group counts
and repeated-code counts equal the reviewed manifest, and that every row still
has base points, every sheet has its 68 scope columns, no code repeats more
than twice and codes and product codes correspond one to one.

The Office preflight additionally accepts exactly `docProps/thumbnail.emf`,
Excel's saved preview image in the original act's workbook. It is size-bounded
and never decoded; other images remain unsupported.

## Verification

- Synthetic per-layout workbooks cover parsing, a shifted-column file, header,
  merge, title, scope-label, chapter and legend drift, formula, error and date
  cells, value bounds and identity rejections, reviewed counts and the
  thumbnail allowance. Each negative case asserts the message of the guard it
  targets. Layout constants are self-checked at import for exact column
  coverage.
- During development, targeted mutations of the header fingerprint,
  absent-versus-zero handling, fraction precision, repeated-code count and
  header merges each made the suite fail.
- Tampering with a built database copy proves the open-time invariants and the
  measure/unit constraint.
- The opt-in `jgp_live` test (`MINOS_JGP_PRIVATE_ARCHIVE`) parses all three
  pinned workbooks and checks A01, PZD02, H21 (and its 2026 twin), AU35C and
  K40 against values read by hand from the source cells, PZD02 across all three
  releases, and base-point equality of all 25 twin pairs.
- A full-source build with all terminology, rank and catalog sources is
  deterministic (two identical 139,399,168-byte databases, about 10 s each)
  with 2,223 group rows, 9,086 base values and 11,596 scope markers. PZD02's
  planned-hospitalization base points change from 8040 (original act) to
  12871.72 after 74/2026.
