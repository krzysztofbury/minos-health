# MODTAR Reference Import And Review Gate

Status reconciled 2026-10-11: source ingestion, reviewed-overlay import and
compatible binding are delivered. #46/#129 closed under the owner assumptions
of 2026-10-07 recorded below, not an NFZ clarification. The latest verified
deployment (#156, `5458ef2`, 2026-10-09) includes that reviewed binding in its
schema-10 bundle. Earlier investigation sections retain their dated evidence;
their then-unbound state is not the current deployment state.

## Reviewed Binding Interface

`DatasetManifest.modifier_bindings` explicitly pins a catalog release, a MODTAR
release and the checksum/size of a canonical manual-overlay JSON blob in the
archive. It records reviewer, review date, knowledge-start date and evidence.
Pending candidates cannot coexist with a reviewed binding for the same catalog.
There is no latest selection or automatic promotion from candidate to binding.

A catalog release has at most one binding, and the binding's MODTAR year must
equal the catalog's first service year. A catalog still in force in the next
calendar year therefore stays unbound for that year. This fails safe; binding a
second MODTAR year needs a key of catalog and MODTAR release before any such
catalog is reviewed.

Schema 7 stores the complete overlay and its independently reconstructed date
windows. Previous migration checksums and unbound schema 2-6 bundles are
unchanged. Build-time and open-time checks reconcile every dictionary mapping,
year, generation, grouper type, source bytes, group references, governing catalog
windows and review identity. Missing, extra or conflicting rows refuse the
artifact. Before dictionary publication and outside reviewed service windows,
the ruleset remains unbound. Reference pages still show base points only.

Supplementary legal provisions have separate `manual_legal_overlay` provenance,
exact coefficients, source evidence, dates and eligibility text. They cannot
overwrite a MODTAR symbol or silently become XML source rows. This supports a
reviewed governing clause absent from the operational dictionary, without
pretending the dictionary supplied it.

```sh
uv run python -m minos jgp modifiers --data /path/to/bundle --code F72 \
  --service-date 2026-10-01 --known-on 2026-10-03 --json
```

The command refuses an unbound bundle. A bound bundle returns dated reference
mappings and separately attributed legal supplements. It does not establish
encounter/provider eligibility, combine coefficients or calculate final points.
Those remain #35's runtime responsibility. A reviewed source binding is one
necessary capability gate, not an entire working tariff engine.

## Governing-Act Findings On 2026-10-04

These are source findings, not approval of a complete overlay:

- 102/2026/DSOZ, BAW document 44041, section 4(2)-(3): quality-indicator
  coefficients start in 2027 after publication of indicator values. Until then,
  the prior section 17(3), (4), (8), (19) and (20) rules continue. Reading the
  consolidated text's repealed clauses without this transition would be wrong.
- Section 4(4) starts the added section 17(6a) on 2026-10-01, not July.
  The consolidated act, BAW document 44042, specifies 1.4 for simultaneous
  bilateral inguinal-hernia operations settled as F72 or F73. Neither pinned
  MODTAR generation contains this clause. A reviewed manual supplement is
  necessary; source-only reconciliation cannot prove legal completeness.
- 74/2026/DSOZ, BAW document 43942, applies from July 1. Its section 17(19)
  amendment replaces the earlier N01/N02/N03 threshold scheme with the
  N01/N02 decile/quartile scheme, with coefficients 0.9, 0.95, 1.05 and 1.1.
  Earlier dates must not inherit that amendment.
- 14/2026/DSOZ, BAW document 43768, section 2: the food-standard adjustment
  uses an old/new catalog point-value ratio, not one global fixed MODTAR value.
  Services from January 1 through August 31, declaration timing and the stated
  February exception matter. The fixed-value supplement interface must not
  represent this as a made-up scalar; ratio/eligibility evaluation needs a
  separately reviewed tariff rule before claiming complete final tariffs.

Independently fetched source pins for these findings:

| BAW document | XML attachment ID (`/api/file/GetZipxAttachment/4/`) | SHA-256 |
| --- | --- | --- |
| 44041 | 32982 | `c759934958ae287dacd5dca66a55f7536830cc67dedb64d34dc83a0eb6a9053b` |
| 44042 | 32991 | `4ae6874619f5330732de798fdd4e0861a4c5179a7350feff794365e322812877` |
| 43942 | 30966 | `26a96f5aae760298d117b1865323bd2e4196c3a01bd9288e8bd9db3d6f054251` |
| 43768 | 27978 | `f24b47227a584d45430588a1cd830efbde1d3a3dd2c894ae41265caae1bb2ef8` |

The complete symbol-to-clause review, external PSZ/oncology/comprehensive-care
acts and the historical applicability matrix are still required before adding
real-source bindings to the committed manifests. Synthetic binding and October
boundary tests are not a substitute for that review and do not justify closing
#46 by themselves.

### Follow-up Independent Table Audit

The current service window (2026-10-04, after-102 catalog) was checked against
the complete embedded tables in checksum-pinned BAW XML, not against a copy of
MODTAR. Both generations 100 and 102 agree on these partial inventories:

| Governing source table | Operational symbols | Exact group/coefficient pairs |
| --- | --- | --- |
| 46/2026 after-102, attachment 3d | active `17.16*` | 31 |
| 46/2026 after-102, attachment 3e | `INT_1`, `AOT_1`, `AOT_2`, `AOT_3` | 9 |
| 80/2026, attachment 3 (embedded attachment 2) | `PSZ_2`, `PSZ_2D02`, `PSZ_2PZD01` | 21 |
| 80/2026, attachment 3b (embedded attachment 4) | `PSZ_R`, `PSZ_R2` | 38 |
| 105/2026, replacement 3a (embedded attachment 1) | `PSZ_ONK_*` | 10 |

No missing, extra or conflicting pairs occurred in those checks. This is only
a dated subset, not complete historical applicability or binding approval.
The consolidated 3c and 3e separate attachment endpoints returned HTTP 200 with
zero bytes; those responses were rejected as evidence. The complete embedded
tables in BAW 44042 supplied the comparison instead.

Additional independently fetched XML pins:

| BAW document | XML attachment ID | SHA-256 |
| --- | --- | --- |
| 43868 | 29617 | `bfd5fa0ef89747d60028ebc3703604694508db2dd8338bac598c24088ef464f7` |
| 43718 | 27122 | `f8efd065e4cd105a2ad5dbfc17ba2cc9544887435773379b36b021c989b5a5dc` |
| 43963 | 31438 | `04688e1ed340f5ed4316622c2ed447f682a2be4835ac4b2e018e4a66ef401cdc` |
| 44053 | 33261 | `7dd992154c872f0df96a6364e21a3ba5034ca690698981fe56c3e6c705366b9b` |
| 43955 | 31380 | `a11a2a1ffcd7d38b2b2bd5a5890cd55ea620a82a7c428bb1cc600ea1b10bbc40` |
| 44049 | 33228 | `bfaa9b3ea875a24bd8102544a735f5a9b0d204b8a44d3a998f5379b6fc25d207` |
| 43217 | 17682 | `1b9d169ae69a30c0d4840665707e1c11e6477b9e67b245aba2506e0ada54db51` |

The orthopedic citation shift is now explained by the original 37/2024/DSOZ
act (BAW 43217). Its section 17(4) wording equals section 17(3) in both
101/2025 and 46/2026 after removing whitespace; its section 17(5) similarly
equals their section 17(4). The original act was independently fetched, not
inferred from a MODTAR description. Both dictionary generations retain the old
citations for these otherwise matching group/coefficient inventories:

| MODTAR symbols | Preserved source citation | Governing 2026 clause | Coefficient |
| --- | --- | --- | --- |
| `M6`, `M7`, `M8`, `M11-H01` | section 17(4), respective points | section 17(3), respective points | 1.2 |
| `M9` | section 17(5) | section 17(4) | 1.5 |

102/2026 section 4(3) retains those prior 2026 rules during the quality-indicator
transition. This crosswalk removes the numbering question for these five
symbols; it does not change the source citation or approve unrelated scope rules.

Scope-derived inventories still need legal interpretation, not automatic
approval. Comparing catalog scope markers with MODTAR inventories finds
differences for `M5`, `17.1`, `17.10`, `17.11` and `17.18`; the queue below
separates those explained by pinned sources from those still open. Catalog markers alone do not establish clinical
eligibility or a legal conflict. Until the exact statutory exclusions and
scope semantics explain these differences, they cannot be rubber-stamped into
an independent overlay. The orthopedic citation crosswalk above is resolved;
the scope questions are separate and remain pending.

These findings keep #46 open and every real catalog binding unbound. The
blocker is independent legal reconciliation, not the #35 final-tariff engine.

### Narrowed Clarification Queue

The age restriction explains why K28F/KU28F must not be mechanically included
in a below-18 inventory: their pinned catalog titles explicitly require age
above 17. Similarly, the section 17(10)-(11) literal group ranges exclude the
PZN newborn-surgery family; using the entire PZ chapter as the legal inventory
would overstate those missing mappings. These explanations are distinct from
the still-unexplained cases below.

#### Explained from pinned sources on 2026-10-04

The consolidated 46/2026 text (BAW 44042, the pin above) and the catalog
footer legend explain these differences. Scope cells use the legend in
`jgp_catalog_layout.LEGEND`: 1 is the first scope named in the column header,
2 the second and 3 every named scope.

| Symbol | Groups | Source explanation |
| --- | --- | --- |
| `M5` (section 17(7), vascular surgery second reference level, 1.1) | Q23, Q24 | Section 17(7) excludes them by name. |
| `M5` | H83O, H84O | Section 19(8) reserves both for providers meeting the comprehensive-oncology criterion, and their catalog notes repeat that condition ("grupa do rozliczenia przez podmioty realizujące leczenie onkologiczne w sposób kompleksowy"). Section 17(7) applies to services in the vascular surgery scope, so they fall outside it. |
| `M5` | PZH12, PZH13, PZJU03 | Marker 2 in "chirurgia naczyniowa / chirurgia naczyniowa- drugi p. ref." means the second-level scope only, so their inclusion is consistent. |
| `17.18` (section 17(17), oncology surgery, 1.17) | C11O, F32O, F42O, G31O, H83O, H84O | Section 17(17), second sentence: the 1.17 coefficient does not apply to the groups listed in section 19(8). |
| `17.18` | H11, PZB03 | Marker 2 in "chirurgia onkologiczna / chirurgia onkologiczna dla dzieci" means the pediatric scope only. Section 17(17) concerns hospitalization in the oncology surgery scope. |

These explanations still need owner acceptance before an overlay uses them.

#### Answered by owner assumption on 2026-10-07

NFZ will not answer these questions (#129). The owner decided that MODTAR as
published wins (`decisions.md`, Tariff Assumptions Without NFZ Answers), so
each pair below follows the dictionary and final points carry a `#129` note.
The table is kept as the record of the differences.


| Service date / governing catalog | Symbols | Question for source clarification |
| --- | --- | --- |
| 2026-07-01 onward / after-74, after-102 | `M5`, generations 100/102 | DU58 (1ae, new from July) carries the same scope markers as D16, which `M5` includes, and other July 1ae twins (HU83, HU84, QU20, PZJU03) were added. Is DU58's absence intentional? |
| All 2026 windows | `M5`, generations 100/102 | Q16E is marked 1 (general vascular surgery only, not the second reference level), yet `M5` includes it. Which provision places it under section 17(7)? |
| All 2026 windows | `17.1`, generations 100/102 | S21/S22/S23 carry only hematology and transplantology markers, not pediatric oncology and hematology. On what contract-scope basis are they included? |
| All 2026 windows | `17.18`, generations 100/102 | F45R/L31R/M22R (robotic) are absent. Section 19(7) excludes them only from the 1.25 DiLO rule, and section 19(8) does not list them. Which provision excludes them from 1.17? |
| January-June 2026 | `17.10`, `17.11`, generations 100/102/103 | PZA04/PZF13 fall within "PZA01-PZL13" and have the same scope markers as the included PZA03/PZF12. Generation 103 adds them for months 7-12 only. Is their absence for January-June intentional? |
| July-December 2026 | `17.10`, `17.11` | PZD04A is identical in the after-74 and after-102 catalogs. Generation 100 (published 2026-08-20, after 74/2026) lacks it; generation 102 adds it for months 7-12. Does the correction apply to the full July-December period? |

How earlier knowledge-time bindings disclose the PZD04A gap is a Minos design
question, not one for NFZ: generation 100 bindings must keep reporting it.

No clarification request has been sent. The queue contains source-comparison
questions, not a claim that NFZ has made a confirmed legal error. A request
should attach exact source pins and affected dates, ask whether the mapping is
intentional or corrected, and identify the governing provision for each answer.
Do not compensate with an uncited exclusion, silently relabel generation 100
as compatible, or substitute MODTAR's own inventory for independent legal proof.

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
complete binding/applicability overlay. Approved overlay persistence and the
approved-binding API state are implemented and tested at the synthetic boundary;
real-source approval and source-backed transition acceptance remain #46 work.

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
The conditions-only schema 6 selector was subsequently measured in an isolated
VPS container with the installed API image, without live activation. Startup
plus the first three successful probes took 96.02 and 109.99 seconds in two
runs; process RSS was about 189 MiB. These are startup/sequential-probe results,
not concurrent-load suitability. Schema 8's optional search-expanded bundle is
418 MiB and has not had that VPS measurement; the schema 6 result does not
authorize its activation. See [search improvements](search-improvements.md).

Raw sources and generated databases remain private and uncommitted. Receipts
for this investigation are retained under the ignored
`test-output/jgp-reference-32-46-6tsdi3pb/` directory. The private MODTAR capture
is under `data/private-fixtures/jgp-modtar-32-46-2e2fmme3/`.

## Owner-Assumed Reviewed Binding (2026-10-07)

`scripts/build_modtar_overlay.py` deterministically writes the overlay for
generation 102 into the content-addressed archive. Each MODTAR mapping keeps its
dictionary months as its service window, and the section 17(6a) supplement
(F72/F73, 1.4, from 2026-10-01) is attached as a separately attributed manual
provision. Reconciliation against the real catalog windows passes for all
1,612 mappings: 101/2025 after 14 governs January-April, 46/2026 original
May-June and 46/2026 after 102 July-December.

`manifests/sources/jgp-modtar-reference.json` binds that overlay (SHA-256
`60f960b1...`) to those three catalogs, knowable from 2026-09-24 and reviewed on
2026-10-07 by owner decision; the research candidates it supersedes are
removed. The reviewed conditions selector composes it with transform
`jgp-reference-conditions-modifiers-v4` and builds a schema 8 bundle (422 MiB)
whose ruleset reports a reviewed generation 102 binding on every 2026 date.
That 2026-10-07 build did not itself change production. Subsequent grouping
releases activated the binding; see the
[#156 receipt](https://github.com/krzysztofbury/minos-health/pull/156#issuecomment-6077733204).

```sh
uv run python -m scripts.build_modtar_overlay \
  --modifiers manifests/sources/jgp-modtar-reference.json --generation 102 \
  --archive-root data/archive --catalog-bundle data/bundles/<conditions-bundle>
```

### Generation 103 (2026-10-13, #184)

Generation 103 (page 7389, label 202665, published and valid from 2026-10-09)
was fetched on 2026-10-13 and matched the watcher's SHA-256
`8cda7960...`. Parsed with the reviewed reader and compared ignoring record
ordinals, it equals generation 102 plus four records: `17.10` (1.2) and `17.11`
(1.1) for PZF13 and PZA04, months 7-12. Nothing was removed or changed; the
ordinals shift because the records are inserted. Counts: 1,616 modifiers, 1,615
periods and group mappings, one inactive empty record.

Under the owner rule above, all three 2026 catalogs now bind generation 103
from knowledge date 2026-10-09 with overlay `ab7b3884...` (1,616 rows, reconciled
against the real catalog windows). Regenerating generation 102 reproduced its
pinned overlay `60f960b1...` byte for byte. Generations 100 and 102 stay pinned
as history. The catalog and characteristics knowledge horizons moved to
2026-10-13 on BAW evidence that no act after 102/2026 exists, so the binding is
visible at the bundle horizon.

```sh
uv run python -m scripts.build_modtar_overlay \
  --modifiers manifests/sources/jgp-modtar-reference.json --generation 103 \
  --archive-root data/archive --catalog-bundle data/bundles/<conditions-bundle> \
  --reviewed-on 2026-10-13
```
