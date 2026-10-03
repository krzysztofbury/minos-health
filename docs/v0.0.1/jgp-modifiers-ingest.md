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
references `C14.`, `P42.`, `P52_` and `Z01_S` occur in both releases. Their
suffixes are not trimmed, aliased or expanded into catalog groups.

## Persistence And Reconciliation

Schema 6 adds `modifier_release`, `tariff_modifier`, `modifier_period`,
`modifier_group` and `modifier_candidate`. A preserved canonical source envelope
allows symmetric validation of every normalized row at build and bundle-open.
Validation is cached only for the exact verified database/dataset identity;
checksums and structural checks still run when a connection opens. A changed
coefficient, header, group reference or missing inactive row fails validation,
even after an artifact checksum is recomputed.

Research candidates explicitly select generation 100 for the after-74 catalog
and generation 102 for the after-102 catalog. They are **pending**, not compatible
bindings. Each candidate contains 71 distinct literal group references absent
from its selected catalog. `review.txt` enumerates them. The operational source
also cites other governing acts; it has no explicit section 17(6a) citation.
These discrepancies block complete binding and require legal/source review.

Owner review (2026-10-03) traced most of them: 67 of the 71 are groups of the
original 46/2026 catalog that the after-74 consolidation dropped, and in both
generations every one of their 149 mappings carries January-June months only
(1-1, 1-6 or 2-6), never July-December. MODTAR is therefore a calendar-year
dictionary spanning several catalog releases, not a companion of one catalog.
Binding must resolve each mapping against the catalog release governing the
mapping's own service window (original 46/2026 from 2026-05-01, after-74 and
after-102 from 2026-07-01; January-April falls under 101/2025, which the bundle
does not cover), not against a single selected catalog. The one-catalog check
in `reconcile_legal_overlay` encodes the latter and must change before an
overlay is accepted. Four references remain unexplained source identifiers:
`C14.`, `P42.`, `P52_` and `Z01_S` (the last two also carry July-December
months); they are preserved literally and need NFZ evidence.

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
