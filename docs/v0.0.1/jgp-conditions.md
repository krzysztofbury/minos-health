# JGP Attachment 9 Condition Taxonomy

Status: #29 research complete and reviewed on 2026-10-03 against the pinned
sources (see [Review Verification](#review-verification)); #30 may start from
this taxonomy. This is not a condition importer, a reviewed executable ruleset,
or evidence of settlement accuracy. #32 remains responsible for
date-consistent reference links.

## Findings

The inspected current attachment requires **16 atomic condition kinds**, plus
four expression operators: `all`, `any`, `not`, and `when`. Diagnosis role,
selector shape, counting policy and measurement units are typed parameters,
not additional kinds. The census below counts overlapping source features,
not future database rows or eligible encounters.

The difficult cases are not just procedure counts:

- Clinical measurements include ECOG, mMRC, NYHA, LVEF and EGFR.
- Procedure annotations impose exclusions, prerequisites, diagnosis restrictions
  and repeated-use requirements. List membership cannot be separated from them.
- Some labels require procedures from different lists, different dates, both
  breasts, a minimum procedure duration, or every member of a list repeatedly.
- N09 and N11 are KOC-only characteristics without matching current catalog 1a
  rows (the original 46/2026 catalog had both). They must not be discarded or
  turned into invented base-point products.
- Five current groups state a condition only in the group title, with no
  matching label clause: N03, N03A, N06A and N06B (`> 2 dni`) and PD01
  (`< 18 r.ż.`). A label-only parser would silently drop them.
- 1a and 1ae have separate shared lists, including different `Lista Ogólna`
  membership. Identical names do not establish interchangeable list identities.
- DiLO/oncology exceptions and grouper preprocessing are not completely described
  in attachment 9. The algorithm and other attachments are additional evidence.

## Source Identity And Method

These are bounded research captures from official endpoints, not a new
production selection or an extension of `catalog_knowledge_as_of`. The available
local reference bundle supplied catalog correspondence; attachment 9 was
captured afresh because its archived bytes were not available in that local
bundle. Publisher checksums are unavailable. SHA-256 values below were computed
at capture and checked again before inspection. Original publication dates and
this research observation date are different facts.

| Evidence | BAW document | File | Bytes | Official endpoint |
| --- | ---: | ---: | ---: | --- |
| Original 46/2026 attachment 9 | 43868 | 29652 | 1,237,745 | <https://baw.nfz.gov.pl/api/file/4/29652> |
| After 74/2026 attachment 9 | 43943 | 31015 | 1,502,155 | <https://baw.nfz.gov.pl/api/file/4/31015> |
| 74/2026 replacement attachment 9 | 43942 | 30976 | 1,502,155 | <https://baw.nfz.gov.pl/api/file/4/30976> |
| After 102/2026 attachment 9 | 44042 | 33027 | 1,517,326 | <https://baw.nfz.gov.pl/api/file/4/33027> |
| 102/2026 attachment 6, replacing attachment 9 | 44041 | 32989 | 1,517,326 | <https://baw.nfz.gov.pl/api/file/4/32989> |
| Algorithm 1.1.2026 | NFZ komunikat | DOCX | 186,371 | [Official algorithm](https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/opis_algorytmu_grupera_2026.docx) |

```text
43868/29652:
915c13de1b40ced7a45ce61d371b66ea5a1bb0aceb76d3162a7f880dca73636b
43943/31015 = 43942/30976:
f5e360996b3c2b9fb2d12469a1fd06576ebb9cb20a4856162a0341771bfb5dcf
44042/33027 = 44041/32989:
618a476ab3d11f9935a2a74d66db1ce77b2fb5376af776d36afa189f712405c4
Algorithm DOCX:
a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c
```

The DOCX matches the existing #33 manifest pin. Both amendment replacement
workbooks were byte-compared with their consolidated counterparts. BAW details
were read before and after downloads: file inventories and act/publication/
validity/version-relation fields matched. Unrelated `LegalBasisActs` metadata
changed in responses for 43868/44041; this is not a byte-identical metadata
observation claim. The act text was read from
<https://baw.nfz.gov.pl/api/file/GetActHtmlString/4/32981>.

Office ZIP/XML preflight used the existing hardened implementation. The default
20,000-row limit rejects all three attachment 9 workbooks. A research-only
`WorkbookLimits(max_rows=50_000)` passes without changing other limits; #30
needs its own reviewed profile, not a global limit increase. Reads used pinned
openpyxl 3.1.5, read-only mode, formulas retained for detection and external
links disabled. The DOCX passed existing preflight before XML text inspection.
No Office application, macro, external resource or formula was executed.

### Physical Layout

| Document / worksheet | Last XML row | XML last column | Expanded package bytes |
| --- | ---: | --- | ---: |
| 43868 / `Charakterystyka` | 40,046 | I | 10,237,310 |
| 43943 / `Charakterystyka 1a` | 37,138 | F | 12,381,316 (whole workbook) |
| 43943 / `Charakterystyka 1ae` | 10,421 | C | same package |
| 44042 / `Charakterystyka 1a` | 37,466 | G | 12,478,037 (whole workbook) |
| 44042 / `Charakterystyka 1ae` | 10,416 | F | same package |

The last XML row includes trailing formatted empty rows: the last nonempty row
is 40,044 / 37,136 / 37,464 for the three 1a-context sheets above and equal to
the XML row for both 1ae sheets.

Columns A/B/C hold section, group/list context, and text respectively. All
values beyond C are empty in these captures; saved worksheet dimensions alone
do not identify the data columns. There are no merged cells. Current headers
are on row 3 after an empty row and a filtering warning; the original header
is on row 2. The current sheets cannot be read as the original single sheet.

Column C interleaves group titles, label clauses, standalone `lub`, inline
alternatives, footnotes, ICD markers, code-plus-title members and shared-list
headings. Multiple clauses can be in one cell; clauses can also resume after
an ICD list. Column B is context, not a unique list identifier: S34 has ICD-10a,
ICD-9a, then a later alternative with ICD-10b/ICD-9b. E31's marker changed from
`ICD-10 (B)` to `ICD-10b`. A first-ICD-marker stop or a blank-row separator would
lose conditions. Titles of ICD codes are not label predicates: P07.0's title
contains weight and `lub`, which must not create an extra age/weight/OR rule.
Group titles are a separate case, covered in
[Group Title Qualifiers](#group-title-qualifiers).

## Proposed Row Contract

This is a logical proposal, not a migration or a promise of schema names.

Every `group_condition` node carries:

```text
characteristics_release_id, characteristics_group_id, catalog_context
condition_id, parent_id, child_position, kind, typed_payload
source_release_id, source_sheet, source_cells[], source_text, source_order
valid_from, valid_to, published_on, recorded_at, applicability_evidence
interpretation_status = proposed | reviewed | unsupported
```

`catalog_context` is the attachment's `1a`/`1ae` context, not proof of a catalog
row. Link separately to catalog source-row windows where correspondence exists;
N09/N11 need an explicit `not_in_catalog` state. Source cells can cite a label,
its footnote and the triggering marked list member. Reused conditions retain
each provenance edge rather than replacing it with one source string.
Date windows use inclusive day bounds, with null for an open end, consistently
with the current catalog contract; attachment and catalog applicability remain
separately evidenced rather than inferred from a matching group code.

Selectors are release- and sheet-local tagged values:

```text
list(list_id, system)
codes(system, exact_codes[])
union(selector_ids[])
classification_family(system, category, expansion_evidence)
```

List identity includes the characteristics release, sheet/catalog context,
system, raw list label, physical marker and block occurrence. Keep source case
and suffixes: `A46R`, `A46r`, `B18R` and `B18r` cannot be silently case-folded
into one identity. Resolve a contextual `poniższej listy`, list range such as
`T1-T8`, or `N23 (a-d)` only against verified physical definitions. A count over
`union(DD01d, DD01e)` is not a separate count for each list. `z rozwinięciami`
requires paired-classification evidence, never unrestricted string-prefix
matching or a generated code family from a title.

The future reference build must reject dangling selectors, unknown phrasing,
cycles, conflicting windows and unresolved terminology correspondence. Known
source omissions, such as an unspecified diagnosis role/unit, remain explicit
typed evidence. Promotion to an executable ruleset must additionally reject
ambiguous boolean grouping and unreviewed interpretation policies. Raw evidence
may remain `unsupported` for reference display, but it must never act as an
executable `true`, an empty conjunction or an omitted requirement. #30 must
define explicit source/node/depth/child/count bounds from these captures and
fixtures. Expression evaluation can use ordered, validated flat nodes without
recursive descent into untrusted source structures.

### Atomic Kinds And Real Examples

All cell references below are column C of **44042**, unless the sheet is marked
`1ae`. Excerpts retain source spelling; line breaks are collapsed for reading.
Each payload below supplements the common row envelope. Parameters shown as
`review_required` deliberately do not authorize future evaluation.

| # | Kind | Real source example | Proposed typed payload |
| --- | --- | --- | --- |
| 1 | `procedure_presence` | A01, 1a C7: `procedury z listy procedur A01`; A12 C591 directly names `04.41` and `01.24` | `{selector_id, required: true}`; literal code sets and shared/directional lists use the same kind |
| 2 | `procedure_count` | H12, 1a C9520: `dwóch różnych procedur`; DU10EF, 1ae C1166: `pięciokrotne wskazanie procedury L91` | `{selector_id, required_count, comparison, counting_policy_id}`; ordinary distinct-code and reviewed repeat-occurrence policies remain distinct |
| 3 | `procedure_all_members` | K47L, 1a C15562: `co najmniej dwukrotne wszystkich procedur ... K47La`; PD02 C22446: `obu procedur ze zbioru` | `{selector_id, minimum_per_member, counting_policy_id}`; do not replace all-members with a total count |
| 4 | `procedure_list_coverage` | N23, 1a C20163: `co najmniej dwóch procedur pochodzących z rożnych list N23 (a-d)` | `{list_ids[], minimum_distinct_lists: 2, minimum_procedures: 2, assignment_policy: review_required}` |
| 5 | `procedure_date_relation` | E11, 1a C4948: `wykonanie w innym terminie w czasie jednej hospitalizacji` for E11/E1 after E12G/E1 | `{occurrence_set_ids[], relation: different_dates, scope: episode, missing_date_policy_id}` |
| 6 | `procedure_laterality` | J02BO, 1a C14043: `jednokrotnie dla piersi prawej i jednokrotnie dla piersi lewej` | `{selector_id, required_sites: [right_breast, left_breast], minimum_per_site: 1, same_code_policy_id}` |
| 7 | `procedure_duration` | N24, 1a C20320: `99.831 ... (czas trwania min 24h)(min 1 raz)` | `{selector_id: code(99.831), comparison: ge, threshold_hours: 24, qualifying_occurrences: 1}` |
| 8 | `diagnosis_presence` | A01, 1a C7: `rozpoznania zasadniczego`; A03 C350: `rozpoznania współistniejącego`; A47 C1201 only says `rozpoznania` | `{selector_id, role: enum(principal, secondary, source_unspecified), required: true}`; unspecified role is not silently promoted to principal |
| 9 | `diagnosis_count` | E05G, 1a C4871: `co najmniej 2 rozpoznań współistniejącychz listy rozpoznań E05G` | `{selector_id, role: secondary, minimum_distinct_codes: 2, allocation_policy_id}` |
| 10 | `patient_age` | A32E, 1a C865: `wiek > 65r.ż.`; N20 C19673: `< 8 tyg. ż.`; PZN03 C27933: `< 18 miesiąca życia` | `{comparison, threshold_integer, unit: enum(completed_years, elapsed_weeks, calendar_months), anchor: admission, boundary_policy_id}` |
| 11 | `episode_duration` | S03, 1a C29088: `czas pobytu > 1 dnia i < 11 dni` | `{lower: {gt, days: 1}, upper: {lt, days: 11}, duration_policy_id}`; absent endpoint is null, not zero |
| 12 | `ward_duration` | E50, 1a C5404/C5407: `czas pobytu w OINK > 2 dni` | `{ward_selector: OINK, comparison: gt, threshold_days: 2, ward_mapping_policy_id, duration_policy_id}` |
| 13 | `discharge_mode` | D45, 1a C3617: `tryb wypisu 9 - zgon pacjenta`; J29 C14468: `tryb wypisu 3` | `{allowed_reported_codes: [9]}` or `[3]`; source labels accompany codes |
| 14 | `clinical_measure` | D28C, 1a C3405: `ECOG ≥ 3`; D50 C3704: `mMRC ≥ 2`; E05G C4873-C4875: `NYHA > II`, `LVEF <32`, `EGFR <62` | `{measure_id, comparison, exact_threshold, unit_or_scale, observation_anchor: review_required}`; roman scale levels are not numeric lab values |
| 15 | `program_membership` | N09, 1a C19367 and N11 C19577: `(dotyczy wyłącznie KOC)` | `{program: KOC, required: true, program_evidence_policy_id}`; not inferred from principal diagnosis |
| 16 | `external_disease_code` | PZN03, 1a C27933: `kod ORPHA 157826` together with K06.8 and procedure 24.31 | `{system: orpha, exact_code: "157826", role_or_reporting_field: review_required}`; this does not authorize importing an ORPHA dictionary |

`procedure_presence` and `diagnosis_presence` are evidence families that can be
represented by a richer count/all-members node rather than a redundant persisted
presence node. Required procedure counts without an explicit upper bound must
not become `exactly N`. Preserve explicit `co najmniej`, `minimum`, plain
cardinality wording and their interpretation evidence. Never infer a count from
the number of rows in a list except for a reviewed all-members requirement.

### Boolean And Guarded Structures

| Operator | Real evidence | Proposed row payload / boundary |
| --- | --- | --- |
| `all` | A01 C7 requires a procedure **and** a principal diagnosis. N03 C18428 adds a three-procedure count containing `89.393 oraz (88.781 lub 88.789)` | `{children[], occurrence_allocation_policy_id}`; included mandatory codes contribute to, not automatically add to, the three-procedure requirement |
| `any` | A03 C348-C358 has alternatives on separate rows; HK11A C13456 has complete alternatives in one cell; DD01 C4020 uses a union inside a count | `{children[]}` with explicit scope; list union is a selector, not necessarily a group-level alternative |
| `not` | N22 C19981: `(*1) brak możliwości wykazania łącznie z procedurą 39.951` | `{child: procedure_presence(code(39.951)), absence_scope_policy_id}`; guarded by the annotated member, not applied unconditionally to the whole group |
| `when` | E05G C4872-C4876 conditions marked diagnoses on clinical values or procedure 37.61; N24 C20329-C20331 constrains marked procedures; D52A C3793 exempts J80/J95 families and R09.2 from one secondary-diagnosis requirement | `{trigger_node_id, consequence_node_id, guard_evidence_cells[]}`; trigger false is not the same as missing trigger evidence |

Guard families present in current 1a are E05G, N22, N24 and D52A; none was found
in 1ae. N22 `(*4)` at C19984 requires one of 93.90/93.91/96.781/96.782 when its
marked procedure is used. N24 `(*11)`/`(*12)` at C20329-C20330 allow only the
listed diagnoses; the label does not specify their principal/secondary role.
N24 `(*13)` at C20331 requires at least three occurrences of the marked
procedure. Attach all these rules to the exact marked membership evidence.
Keep whether a guard applies to every reported occurrence or only to a
count-allocated occurrence as an explicit interpretation policy; this research
does not silently choose between those scopes.

The source includes spelling inconsistencies: `wymagagne` (P35), `proceudry`
(DD02), `dwukrone` (J02BO), omitted spaces and differing suffix case. Preserve
them as evidence. Do not build a source-correction dictionary or fix labels
silently. A future parser recognizes reviewed structural variants with fixtures;
unreviewed ambiguity blocks executable interpretation.

## Frequency Census

Denominator is a distinct group code with a label in the specified sheet,
including KOC-only characteristics. Shared-list-only codes are excluded.
The original has 702 groups; after 74 there are 627 + 121; after 102 there are
629 + 121. The current 1a catalog has only 627 distinct group codes despite
646 source rows, and 1ae has 121 despite 127 source rows. Catalog date twins
are not extra characteristic groups or extra frequency observations.

Counts are **groups containing the feature**, once per group regardless of
alternatives, repeated annotation text or shared-list reuse. They overlap:
procedure presence includes count/all-members/guard evidence; nontrivial count
means explicit cardinality greater than one or repetition, not every `1
procedury` spelling. Duration in a procedure's code title is not an additional
episode-length predicate. The counts cover label clauses only: they exclude
the five title-only qualifiers listed in
[Group Title Qualifiers](#group-title-qualifiers). These are research counts, not #30 import acceptance
counts. The [census recipe](jgp-conditions-census.md) makes their lexical scope
and exact source pins reproducible without parsing executable conditions.

| Kind | Original 1a | After 74 1a | Current 1a | Current 1ae |
| --- | ---: | ---: | ---: | ---: |
| `procedure_presence` | 509 | 496 | 498 | 77 |
| `procedure_count` | 47 | 71 | 71 | 12 |
| `procedure_all_members` | 7 | 7 | 7 | 1 |
| `procedure_list_coverage` | 2 | 2 | 2 | 0 |
| `procedure_date_relation` | 1 | 1 | 1 | 0 |
| `procedure_laterality` | 0 | 1 | 1 | 0 |
| `procedure_duration` | 1 | 1 | 1 | 0 |
| `diagnosis_presence` | 481 | 440 | 442 | 76 |
| `diagnosis_count` | 5 | 5 | 5 | 0 |
| `patient_age` | 257 | 230 | 230 | 33 |
| `episode_duration` | 114 | 82 | 84 | 29 |
| `ward_duration` | 1 | 1 | 1 | 0 |
| `discharge_mode` | 19 | 19 | 21 | 0 |
| `clinical_measure` | 2 | 5 | 5 | 0 |
| `program_membership` | 0 | 0 | 2 | 0 |
| `external_disease_code` | 1 | 1 | 1 | 0 |

After-74 1ae has the same feature counts as current 1ae, but **not identical
shared-list membership**. Rare current cases can be checked in full:

- All-members: K47L, PD02, S11, S12, S13, S14, S15; 1ae SU16.
- Different-list coverage: N22A, N23. Different dates: E11.
- Laterality: J02BO. Explicit procedure duration: N24. Ward duration: E50.
- Diagnosis counts: E05G, N22, T14, T15, T16.
- Clinical measures: D28C, D50, D55, E05G, E47.
- KOC: N09, N11. ORPHA: PZN03.
- 1ae nontrivial counts: AU35C, DU10EF, DU46, DU57, DU58, DU59, EU89, HU95,
  KU47D, PU01, PU10, SU16.

## Algorithm Cross-Check

References are to the headings in the pinned DOCX, with one-based paragraph
numbers from all `w:p` elements in `word/document.xml`, including table cells.
Paragraph numbers are capture-local evidence, not official legal numbering.

| Algorithm evidence | Taxonomy consequence / owner |
| --- | --- |
| Glossary, paragraphs 64-79 | Directional procedure/diagnosis lists and ranks define candidate routing, not clinical priority. Rank 0 is ignored, ranks 1-3 are once-only, and rank 4 permits reviewed repeated use. Missing rank is not zero. #33 supplies evidence, #34-#36 execute. |
| 4.1, paragraphs 97-103 | Whole-episode conditions, negative lists and oncology exceptions. The rehabilitation-specific distinct-code/distinct-day rule must not be generalized to SZP. |
| 4.2-4.3, paragraphs 105-113 | Procedure-first routing, conservative fallback and the special marker described for N24. An `any` label is not a cheapest/highest-points selection algorithm; provider selection among valid groups remains distinct. |
| 4.5-4.6, paragraphs 119-129 | Specialty/service/code exclusions remove data and sometimes duration before evaluation. Missing specialty row means no listed exclusion, not rank zero. Adult continuation consent can bypass the pediatric age restriction only after consent-type verification. |
| 4.7, paragraphs 133-143 | Rank 0-3 procedures cannot satisfy two slots with the same code, except the E11/N24 different-date `z` mechanism. Missing dates are treated as the same day. Rank 4 repeats depend on label definitions. Two required diagnoses cannot reuse one diagnosis code; secondary order is irrelevant. |
| 4.7, paragraphs 144-186 | Same-day admission/discharge duration is 1 for the described one-day case; otherwise hospital duration is discharge minus admission. Excluded stays/services and adjacent stays need the specified adjustments. Use an algorithm policy, not a generic inclusive date difference. |
| 4.7, paragraphs 188-199 | Age is anchored to admission and exact birth date. `>17 r.ż.` starts at the 18th birthday. Month/week boundaries are not justified by the year examples; #35 requires an explicit reviewed policy for them. |
| 4.8, paragraphs 200-201 | Contract/specialty compatibility is an external eligibility gate, not proved by characteristic-list membership or by the browser's scope filter. |
| 4.9, paragraphs 202-213 | Base points and final tariff are separate. Missing catalog values are absent; discharge 9 also affects short-stay reductions, separately from attachment 9's duration-or-death qualification. Modifier binding is #46, calculation #35. |
| 4.10, paragraphs 214-216 | One reported JGP per episode and allowed accompanying services are settlement rules, not attachment 9 condition rows. |

### Material Evidence Gaps, Not Implicit Defaults

1. **The published description is not an exhaustive machine specification.** Its
   rank-4 examples name K47D/K47L/N25/P05/S56/S11/S12/S16, while current labels
   include KU47D, SU16, DU10EF's repeated L91 and J02BO laterality. Preserve
   each exact identity; do not rename groups, generalize `rank >= 3`, or allow
   repeated use only by that historical example list.
   The pinned #33 tables make two concrete gaps visible: L91 is absent from
   `SZP 2026` but present with rank 4 in `AOS 2026` (row 1175); J02BJa's single
   member 85.261 has SZP rank 3, yet J02BO requires it once for each breast.
   Do not borrow an AOS rank for 1ae or silently override the SZP once-only
   rule. These require reviewed compatibility/exception evidence before any
   repeat-sensitive evaluation; the source characteristics remain visible.
2. **The conservative marker description is stale in scope.** DOCX paragraph
   111 says it is currently used only for N24. The 102/2026 justification
   explicitly describes applying that mechanism to conservative respiratory
   groups. A scalar list-membership predicate cannot encode routing precedence.
   #34/#35 need separately reviewed marker/binding evidence; this PR does not
   silently amend the algorithm or establish a date-specific pairing.
3. **Clinical values and special facts are not supplied by ICD membership.**
   Neither clinical units/observation dates nor OINK specialty mapping, KOC
   eligibility, ORPHA reporting field or laterality encoding may be invented.
   LVEF `<32` and EGFR `<62` footnotes have no explicit unit; E47 separately
   states LVEF `<40%`. Record that difference rather than adding a unit to the
   former from medical background knowledge.
4. **Diagnosis role is sometimes unspecified.** A47 C1201, F26F C6813 and
   P35 C23626 do not name principal/secondary; N24's annotation restrictions
   also do not. Reference evidence can be complete while executable
   interpretation remains blocked for review.
5. **Count allocation matters.** Distinct-code requirements across lists,
   union counts, overlap between lists and annotated eligible members require
   explicit occurrence allocation. N23 cannot be satisfied by counting one
   reported procedure twice solely because it occurs in two lists. Never make
   rank-4 repeat permission imply unlimited reuse of one occurrence.
6. **Inline precedence is not automatically clear.** DD04 C4117/C4119 mixes
   a principal literal/list alternative, secondary diagnosis and procedures.
   N03's added parentheses demonstrate that punctuation changes can alter the
   interpretation. Retain the original expression and require reviewed scope
   where syntax does not establish it; do not infer a generic Polish precedence
   grammar from commas or `lub` alone.

7. **Group-title qualifiers can be the only source of a condition.** See
   [Group Title Qualifiers](#group-title-qualifiers).

### Group Title Qualifiers

Algorithm paragraph 64 defines a group as having a code, a name and a
characteristic described by parameters; the name is not itself a parameter.
In the original 46/2026 workbook every comparative qualifier in a group title
also appears as a label clause (206 age titles, 46 duration titles). In the
current 1a sheet, 188 of 189 age titles and 41 of 45 duration titles do, but
five do not:

| Group | Title qualifier | Current label | Evidence |
| --- | --- | --- | --- |
| N03, N03A | `z porodem > 2 dni` | no duration clause | Original N03 label C20416 ended `czas pobytu > 5 dni`; after 74 the title became `> 2 dni` and the clause was removed |
| N06A, N06B | `leczenie > 2 dni`, `leczenie zabiegowe > 2 dni` | no duration clause | Current C18769-C18772, C18945-C18946 |
| PD01 | `< 18 r.ż.` | no age clause | Current C22424-C22425; new group after 74 |

The catalog (attachment 1a) names carry the same qualifiers, so this is
consistent source wording, not a workbook typo. Whether such a qualifier is an
eligibility condition, an algorithm routing artifact or a naming remnant is
not established by attachment 9. #30 must record it as a typed
`title_qualifier` evidence node with `interpretation_status = unsupported`
until reviewed. It must neither drop it nor promote it to an executable
`episode_duration`/`patient_age` node from the title alone.

Descriptive title qualifiers are a different class: ward (`w oddziale
udarowym` in A48/A48T, `przed przeniesieniem do oddziału specjalistycznego` in
J29), complications (`z powikłaniami`, `bez pw`) and newborn wording appear in
titles without the same words in labels. They are realized, if at all, by
lists (for example the Dpw/Epw complication lists), group precedence or the
contract/specialty gate (algorithm 4.8), and stay title evidence rather than
parsed predicates.

### DiLO / Oncology Boundary

No DiLO/card predicate was found in the inspected attachment 9 labels. That is
not evidence that oncology exceptions do not exist. Algorithm 4.1 paragraph 98
allows dedicated oncology-scope qualification beyond attachment 9, based on
principal cancer diagnoses and directional procedures in attachment 3b; when
3b has no restricted procedure list, the described fallback is attachment 9's
directional procedures. 102/2026 section 1(5) also replaces attachment 3b.

Proposed supplemental shape is `when(oncology_pathway_context,
oncology_directional_conditions)`, with its own 3b release, scope/card evidence
where required by the governing source, and legal applicability. This is not
a seventeenth observed attachment 9 kind and must not be synthesized from a
group name or cancer ICD prefix. #30 can import attachment 9 reference evidence
without claiming complete DiLO eligibility; #35 cannot claim complete grouping
until supplemental evidence is explicitly bound and reviewed. No 3b importer
or encounter/card field is implemented here.

## Structural Drift

### Original 46/2026 Versus Current Consolidation

The original single `Charakterystyka` sheet has 702 labelled groups. The current
two-sheet layout has 629 labelled 1a groups and 121 labelled 1ae groups.
This is not a rename-only layout change. The feature mix changes substantially:
47 to 71 nontrivial-count groups in 1a, new laterality, three additional
clinical-measure groups, and the KOC context. No positional parser for the
original workbook may silently ingest the current workbook under the same
layout identity. 1ae introduces its own procedure-count and all-members cases.

### Isolating 102/2026 Rather Than Attributing All Drift To It

BAW 44041 is the **amending act**, not the base act. Its section 1(8) replaces
attachment 9 with its attachment 6 (32989), byte-identical to 44042's attachment
9 (33027). Compare original 43868 to current 44042 for total drift, then 43943
to 44042 to isolate changes after 74. Section 4(1) establishes the general
2026-07-01 service applicability; section 4(5)'s deferred 2027-01-01 catalog
rows do not automatically defer the whole condition workbook. Knowledge time
uses reviewed publication, with fetch time separately retained, as in #18.

Between 43943 and 44042 the two worksheet names and A/B/C layout are stable,
but row positions change. Comparing nonblank column-C sequences by column-B
context yields 20 changed existing 1a contexts and two added ones; 1ae has one
changed context (`Lista Ogólna`). This is a structural research comparison,
not #20's production version-diff implementation.

| Change after 74 | Current evidence / impact |
| --- | --- |
| N09/N11 added | C19366-C19367 / C19576-C19577; KOC-only technical restoration, confirmed in the amending act justification. Neither code exists in the current 1a base catalog. |
| D45/D52A | C3617/C3619 and C3793/C3795 add duration-or-death alternatives; D45 adds a branch with a D52A procedure. D52A also adds list members. |
| D55 | C3894/C3896 drop the independent D55c count; former c members move into b. The secondary list expands N17/N18 into explicitly listed subcodes. |
| DL03 | C4257 adds another alternative requiring a DD01a procedure. |
| N03/N03A | C18428/C18472/C18474 add parentheses around `(88.781 lub 88.789)` inside the mandatory subset. |
| HK11A/PZB01 | C13456 changes HK11 to HK11a; C25357-C25393 changes PZB01 sublist labels/markers and adds the secondary diagnosis list. Exact reference identity changes. |
| D46/D50/DL01/DL02 | Procedure-list membership changes although main label text is unchanged: each gains 33.22 and 33.24 (D52A also gains them, plus 34.041). D50 also moves 99.998 between physical lists. |
| G1/H96D | G1 gains 88.976, affecting G36 indirectly; H96D gains M30.1/M31.0/M31.3, also used by H96C. |
| `Lista Ogólna` in both sheets | Both drop the second occurrence of M46.0/M46.1/M46.2/M46.3/M46.4/M46.5/M46.8, each listed twice after 74; 1a membership is unchanged. 1ae gains J98.4/J98.8, so unchanged 1ae group labels still have changed qualification evidence. Duplicate members are source evidence, not extra list entries. |
| DD01/DD03/D19/D20/E31 | Whitespace/reordering, procedure-title casing and an ICD-list-marker spelling change. Preserve them; do not label all raw changes as medical-semantic changes. |

The ten existing 1a groups with changed label/annotation strings are D45,
D52A, D55, DD01, DD03, DL03, HK11A, N03, N03A and PZB01. The full 20 changed
contexts additionally include D19, D20, D46, D50, DL01, DL02, E31, G1, H96D
and `Lista Ogólna`. No 1ae group label changed, but its shared general list
gained two members.
Counts alone would miss these changes.

## Review And #30 Handoff

Review this taxonomy and the proposed typed boundaries before implementing any
condition rows. It deliberately favors source completeness and explicit
unsupported states over a prematurely executable, flattened predicate table.
That requires more typed evidence than strings, but avoids silent eligibility
changes and still leaves source inspection offline and deterministic.

Required #30 fixtures and build checks:

- Each of the 16 kinds, each operator, a literal selector, a union selector,
  physical sublist suffixes and contextual/family selectors.
- Full original/current layouts and a condition resuming after a list, shared
  lists used by many groups, separate 1a/1ae general lists, and an unknown phrase.
- N22/N24 marked membership guards, E05G clinical guards, D52A's limited
  secondary-diagnosis exemption, N03's included mandatory subset, E11 dates,
  J02BO sites and K47L's per-member repetition.
- The five title-only qualifiers (N03, N03A, N06A, N06B, PD01) as unsupported
  `title_qualifier` evidence, and a duplicated list member (current
  `Lista Ogólna` history) kept as evidence without changing set membership.
- N09/N11 characteristics without catalog values, unspecified diagnosis roles,
  missing measurements/units and ambiguous inline alternatives. Unknown is not
  false, true, zero, or evidence that a condition does not apply.
- Paired-code resolution, exact original code/title/marker preservation, bounds,
  duplicate identities, invalid/dangling list references, and checksum drift.
- Date-specific condition/ICD/catalog correspondence without inheriting the
  latest source or 2027 catalog exceptions indiscriminately. Binding of
  supplemental algorithm/oncology evidence is a separate explicit gate.

#30's acceptance counts must come from its reviewed complete parser, not from
the lexical census. #32 may expose source-backed list membership with these
limitations; membership alone never establishes encounter eligibility. No
database schema, ingestion behavior, API, UI, deployment, Cloudflare setting,
MODTAR binding or settlement calculation changes in #29.

## Review Verification

The 2026-10-03 review reproduced, against the pinned captures and the
deployed reference bundle, rather than trusting the research output:

- all four SHA-256 values and the DOCX pin in the #33 manifests;
- all five census tables, exactly, from the published recipe;
- 55 cited cell references (sheet, group context and quoted text);
- the rank facts (85.261 SZP rank 3; L91 only in `AOS 2026`, rank 4, row 1175)
  and catalog counts (1a 646 rows / 627 codes, 1ae 127 / 121; N09/N11 only in
  the original catalog);
- the cited algorithm paragraphs, including paragraph 111's N24-only marker;
- the 43943 to 44042 drift: 20 changed and 2 added 1a contexts, 10 changed
  label strings, one changed 1ae context, and every membership claim above.

It corrected two statements: `Lista Ogólna` lost duplicate rows, not members,
in 1a; and group-title qualifiers, absent from the first draft, can be the only
source of a condition.
