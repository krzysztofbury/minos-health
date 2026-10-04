# MODTAR Reference Import And Review Gate

Status: combined #32 / #46 branch, 2026-10-03. Source ingestion and pending
reconciliation are implemented. Legal overlays and compatible final-tariff
binding are not approved; #46 must remain open. No public bundle activation,
merge or deployment is implied.

## Source Boundary

NFZ is the source of truth. The governing NFZ act controls legal applicability;
MODTAR is the official machine-readable operational mapping. Review reconciles
these official sources and our selected catalog scope, not a competing source
of rules. Unexplained differences remain visible and block complete binding;
they do not authorize silently correcting or replacing NFZ source values.

`manifests/sources/jgp-modtar-reference.json` pins generations 100 and 102 of
2026 SZP MODTAR. Both XML and independent version-info HTML observations are
checksummed. Display labels 202662 and 202664 are associated only after checking
the version-info page against technical generation, year and grouper type.
XML timestamps remain timezone-unspecified source text. Dictionary validity
and publication dates do not establish legal or reporting-period applicability.

The bounded Expat reader consumes the complete supported structure and rejects
unknown attributes, unknown elements, non-whitespace text, namespaces, DTDs,
entities and unsupported status values. Source order, repeated symbols, group
references, citations, inactive status and exact decimal text survive import.
Coefficients use integer millionths, never binary floating point. An inactive
empty modifier remains a record, not a discarded row. Both inspected generations
contain one such record, symbol `17.16A22`.

| Generation | XML bytes | Modifier records | Periods / group mappings | Independently observed display |
| --- | --- | --- | --- | --- |
| 100 | 520252 | 1608 | 1607 | 202662 |
| 102 | 521552 | 1612 | 1611 | 202664 |

The source mapping diff has four additions and no removals. Exact source
references `C14.`, `P42.`, `P52_` and `Z01_S` occur in both releases. They are
stored literally; resolution against catalogs follows the owner rules below,
never trimming, aliasing or family expansion of stored source text.

## Persistence And Reconciliation

Schema 6 adds `modifier_release`, `tariff_modifier`, `modifier_period`,
`modifier_group` and `modifier_candidate`. A preserved canonical source envelope
allows symmetric validation of every normalized row at build and bundle-open.
Validation is cached only for the exact verified database/dataset identity;
checksums and structural checks still run when a connection opens. A changed
coefficient, header, group reference or missing inactive row fails validation,
even after an artifact checksum is recomputed.

Research candidates point generation 100 at the after-74 catalog and
generation 102 at the after-102 catalog for the API's `review_candidate`. They
are **pending** research pointers, not compatible bindings. Checked against one selected catalog, each candidate showed 71
absent literal group references; `review.txt` now classifies every reference
per catalog window instead (below). The operational source
also cites other governing acts; it has no explicit section 17(6a) citation.
These discrepancies block complete binding and require legal/source review.

Owner review (2026-10-03) traced them: 67 of the 71 are groups of the
original 46/2026 catalog that the after-74 consolidation dropped, and every one
of their 149 mappings carries January-June months only. MODTAR is a
calendar-year dictionary spanning several catalog releases, so binding resolves
each mapping against the catalog release governing the mapping's own service
window, never against one selected catalog (`CatalogWindow`,
`governing_windows`, `reconcile_legal_overlay`).

Owner rule for literal references (2026-10-03): one trailing `.` or `_` is
removed only when the resulting code is a group in the governing window's
catalog, and the literal is kept beside the resolved code. No prefix family is
ever derived.

`Z01_S` (owner decision 2026-10-03): no public definition of the `_S` suffix
was found in the MODTAR XML, the CZS dictionary-service documentation
(`docs_usluga_pub_slowniki.pdf`), the CZS MODTAR browser or a web search. It
occurs only under section 17 (17.1, 17.18) and KON_1 (comprehensive oncology,
section 18) modifiers. It is read as Z01 ("Kompleksowa diagnostyka
onkologiczna") through an explicit `OWNER_ASSUMPTIONS` entry for exactly this
literal, reported separately as an owner assumption, until NFZ evidence says
otherwise. There is no general `_S` rule.

With the 101/2025 catalogs added (January-April 2026) and both pinned
generations, per window of each mapping's months: 535 (generation 100) and 537
(generation 102) references resolve exactly; `C14.`, `P42.` and `P52_` resolve
to C14, P42 and P52 under the owner rule; `Z01_S` resolves to Z01 by owner
assumption; none remain unresolved and no month lacks a reviewed catalog.
`review.txt` reports these classes per generation. Binding still requires a
reviewed legal overlay.

The domain reconciliation boundary requires exact source inventory, matching
year, generation, grouper type and catalog identity, plus explicit reviewed
service windows and legal evidence. Missing, extra or conflicting mappings and
unresolved group references fail. A missing overlay is `unbound`, not an empty
set of eligible modifiers. Combination rules and encounter eligibility remain
outside this reference importer.

Synthetic tests prove January/July month selection and independent October
legal windows. They **do not** prove real 102/2026 section 4(3)/4(4) compliance.
Before #46 completion, the owner must review historical/out-of-scope group
references, governing acts, the quality transition, section 17(6a), and the
complete binding/applicability overlay. Approved overlay persistence and an
approved-binding API state remain #46 work.

## Composition And Validation

```sh
uv run python scripts/compose_conditions_manifest.py \
  --output /owned/research-manifest.json \
  --base manifests/sources/reference-catalog-public-validation.json \
  --conditions manifests/sources/jgp-conditions-reference.json \
  --modifiers manifests/sources/jgp-modtar-reference.json
```

Composition without `--modifiers` is a conditions-only build. Since the owner
review it is also schema 6 (with empty modifier tables), because the dated
condition consumers rely on schema 6's lookup indexes; schema 5 bundles remain
readable.
Neither form changes the production selector. Full-source schema-6 builds on
this branch were byte-identical across two runs: 253018112 database bytes.
Dated APIs, structured and unsupported group pages, conditions-only N09,
ICD-9/ICD-10 reverse pages and uncovered-date refusal were exercised locally.
This is local reference validation, not representative VPS performance evidence.
The larger bundle still requires separately authorized startup/memory
measurements before public activation, without a stress test or deployment.

Raw sources and generated databases remain private and uncommitted. Receipts
for this investigation are retained under the ignored
`test-output/jgp-reference-32-46-6tsdi3pb/` directory. The private MODTAR capture
is under `data/private-fixtures/jgp-modtar-32-46-2e2fmme3/`.
