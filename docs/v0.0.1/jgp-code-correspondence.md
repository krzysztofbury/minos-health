# JGP Source Codes And Dictionary Correspondence

Decision: owner-approved during #30 on 2026-10-03. This refines #29's
mandatory paired-dictionary gate for **reference ingestion**, not grouping or
settlement. NFZ attachment identities are authoritative for attachment list
membership; dictionary correspondence is a separately attributed exact-code
relation. Do not drop a source member because another government catalog lacks
it. Do not invent an equivalent code, restore a deleted code by medical meaning,
or extend an ICD family by prefix matching.

Status: #30 implements reference extraction, typed fragments, schema-5 storage,
strict reviewed manifests and dated store correspondence. See the
[implementation and acceptance record](jgp-conditions-ingest.md). Automated
manifest PRs (#21) and application consumers (#32) remain separate future work.
This branch does not change the production data selector or deploy conditions.

## Why This Is Necessary

The checksum-verified 102/2026 attachment 9 contains 19 distinct ICD-10 codes
absent from the bundled RSK `icd10-rsk-013-f3dc9811520e` release:

```text
C14.1 C82.3 D53.3 G60.4 I23.9 M30.4 M72.3 M72.5 O12.3 P51.1
Q31.4 Q35.0 Q35.2 Q35.4 Q35.6 Q35.8 Q61.6 R50.0 R50.1
```

This list is an observation of selected sources, not a source-correction
dictionary, a permanent allowlist or a blanket validity decision. All 19 also
occur in the pinned original 46/2026 and after-74 attachments. Their continued
presence in normative source bytes is evidence that the JGP reference importer
must retain them. It is not proof of universal coding validity, clinical
equivalence or acceptance of a particular claim by NFZ.

### Independent Government Evidence

1. **NFZ legal attachment bytes:** [#29 source pins](jgp-conditions.md#source-identity-and-method)
   identify BAW 43868/29652, 43943/31015 and 44042/33027. Each contains all 19
   codes. Current examples: G60.4 at `Charakterystyka 1a!C756`, C14.1 at C2508,
   Q35.0 at C2724, and Q35.4 at C2728. Their exact source titles remain NFZ
   text, never replacement RSK titles.
2. **Current RSK metadata:**
   <https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/00CD10> still says
   resource version `013`, but currently selects XML UUID
   `d38bfc6b-30bf-4099-8f32-dec1d16d1bbe`, created 2026-09-22, 12,738,069 bytes.
   Capture SHA-256:
   `4a82a176449c1a4f5567bbafffb6202104b1cac9e4503fbe4adb0e53b336da03`.
   Download: <https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/d38bfc6b-30bf-4099-8f32-dec1d16d1bbe>.
   Acquisition-time observation: the download script compared metadata before
   and after fetching the XML and observed equality. Only the selected metadata
   response was retained, not separately timestamped before/after responses;
   that historical fence cannot be independently reproduced from the receipts.
   The retained XML's 24,040 raw node code/title
   pairs match the old pinned XML exactly; all 19 remain absent. Root namespace
   changed to `https://rsk3.ezdrowie.gov.pl/`, which the existing importer rejects.
   This research did not relax that importer, change its pin, or update production.
   A newer filename, UUID or identical resource version is not a code repair.
3. **NFZ aggregate utilization:** the official
   [API documentation](https://api.nfz.gov.pl/app-stat-api-jgp/) links its
   [Swagger specification](https://api.nfz.gov.pl/app-stat-api-jgp/swagger/v1.1/swagger.json).
   The C27 benefit's 2024 ICD-10 table is
   <https://api.nfz.gov.pl/app-stat-api-jgp/icd10-diseases/036c400d-8fcc-d6f3-e85d-561c2fecec1e?limit=25&api-version=1.1>.
   It reports Q35.4: 8 hospitalizations, Q35.8: 4, Q35.2: 3, Q35.6: 3.
   These are aggregate historical observations, not patient fixtures or a 2026
   legal validity determination. They independently show why absence from RSK
   cannot be treated as proof that every unmatched NFZ code is invalid.

The statistical resource was discovered from `statystyki.nfz.gov.pl`, then
`/benefits` and `/index-of-tables` following documented links. A 2025-filtered
query was empty; removing the year filter showed data through 2024. No claim
of no hospitalizations or code invalidity follows from that filtered empty
result. The A31 aggregate does not establish use of G60.4; do not generalize
the positive C27 evidence to all 19 codes.

Current RSK metadata names an ICD-10-2008-PL XML while its description cites
WHO's English 2019 edition and source terms. These fields are distinct evidence;
they do not authorize mixing editions or sourcing replacement codes from
ICD-10-CM-US, WHO English, vendor tools or search snippets.

## Representation And Failure Boundaries

- A member is identified under the characteristics release, 1a/1ae context,
  physical list identity and exact source code. The local NFZ vocabulary is
  derived from explicit list members, with every source code/title/cell retained.
  It is not a new global canonical ICD dictionary.
- Repeated member rows retain all occurrences as evidence. Membership uses a
  set of exact codes within one list; two rows never prove two procedures.
  Different lists and sheets remain independent even if codes/titles match.
- Dictionary correspondence points to an explicitly pinned reference release
  and an exact concept identity when present. Absence is `not_in_reference`,
  not `invalid`, rank 0, zero points, an empty list or a license to substitute.
  A missing dictionary release/pin is a build error, not `not_in_reference`.
  An unmatched ICD-9 member follows the same explicit correspondence contract;
  the 19-code finding is ICD-10 evidence, not an ICD-9 acceptance count.
- The NFZ identity remains intact even when dictionary titles differ. Display
  the NFZ title as source evidence; any later RSK title/link has RSK attribution.
- Structured selectors must resolve to source-local list/code identities. A
  missing/ambiguous list definition or code named in a rule without defining
  evidence remains unsupported; no manufactured list or family is allowed.
  Unsupported evidence retains its raw reference and reason without persisting
  a supposedly resolved typed selector with a dangling target. A dangling
  target in a proposed/resolved structure is a build error.
- Unknown syntax, missing clinical units, ambiguous roles/boolean scope and
  title-only qualifiers remain unsupported interpretation evidence. A valid
  list-member identity does not upgrade the condition to executable.
- Build review reports distinct matched/unmatched codes and source occurrences
  separately, plus unsupported nodes and reasons. A new unmatched member is a
  review signal, not automatic deletion or automatic clinical clearance.
- Service/knowledge dates remain source-specific. An exact RSK match is
  reference correspondence, **not** a per-service-date ruleset pairing. #35
  still requires reviewed algorithm, dictionary and supplemental bindings;
  #46 still owns modifier binding. Unsupported/unbound evidence cannot act as
  an executable `true` or be omitted to manufacture eligibility.

Logical output states, independent of eventual SQL column names:

| Evidence | Reference output | What it does not establish |
| --- | --- | --- |
| NFZ code with an exact selected-dictionary concept | NFZ identity plus `matched` correspondence and pinned concept/release | Equal titles, clinical equivalence or service-date billability |
| NFZ code absent from a verified selected dictionary | NFZ identity plus `not_in_reference` and the selected dictionary release | Invalidity, a replacement code or zero rank |
| Selected dictionary release missing, unreadable or unverified | Build failure | A legitimate unmatched concept |
| Unknown clause or ambiguous selector/guard scope | Unsupported evidence with all source cells and an explicit reason | `true`, `false`, an empty conjunction or permission to drop the clause |
| Fully traced typed reference condition | Proposed evidence awaiting interpretation/binding review | Executable grouping or final settlement points |

Reference correspondence does not assert a clinical, reimbursement or licensing
equivalence between NFZ and RSK. RSK-derived titles/hierarchy still carry their
source attribution, translation/non-endorsement notices and noncommercial terms.
An NFZ-local identity does not authorize paid features to depend on RSK or
establish a new license for government-hosted material. This decision preserves
the source separation; it does not settle commercial-use permissions.

The model must retain stable identities for individual membership occurrences
and annotation marks as well as the deduplicated member set. N22/N24 guards
bind to those exact marked occurrences, not every appearance of the same ICD
code. Family selectors need separately cited expansion evidence. Neither
`classification_family` nor dictionary correspondence may be satisfied by a
prefix query over whatever dictionary happens to be installed.

The refined reference gate is: zero dangling **source-local** references,
explicit dictionary-correspondence states, and reviewed counts. It replaces
the previous requirement that every NFZ member must exist in RSK before any
reference artifact can be built. It does not weaken structural integrity or
the future executable-ruleset gate.

## Watcher To Application Population

The stages below describe ownership and required behavior, not a claim that
every remaining watcher/data-PR feature is already deployed.

Current implementation: verified attachment 9 bytes can populate a fresh
reference bundle through the existing `minos data build` command. The reviewed
#30 selection is composed separately from the unchanged production selector.
Automated manifest PR creation (#21) and condition consumers (#32) remain
unimplemented. Archiving a new attachment does not populate live conditions
automatically.

1. **Capture, not live editing (#6-#8).** NFZ discovery (#7) dispatches BAW IDs
   to #8, which captures original/amending/consolidated metadata and attachment
   bytes. #6 monitors RSK metadata and allowlisted ICD-10 bytes. Sources are
   content-addressed, byte-count/checksum verified and metadata fenced. Reused
   filenames, URLs and unchanged display versions do not excuse content drift.
   The current RSK watcher checks unchanged-identity bytes through manual or
   weekly downloads, not every ordinary metadata poll. Detected drift needs
   review; metadata equality alone does not prove unchanged bytes. Watchers
   never write `group_condition` or canonical concepts.
   Acquisition/archive state and application data promotion are separate:
   only a complete verified capture may advance successful watcher state.
   A downstream interpretation failure must not turn a partial capture into
   success or change the application bundle selected for service dates.
2. **Reviewed change record (#21).** A successful capture emits a bounded
   change record. A proposed manifest PR pins exact source digests, source
   identities, transformation/layout, temporal evidence, and selected reference
   releases. Publication time is not fetch time. Newly found attachment files
   are not automatically usable layouts or complete ruleset bindings.
3. **Full deterministic rebuild (#30).** The builder reads only those verified
   archive bytes, independently reconstructs 1a and 1ae, derives source-local
   vocabularies/members and source-cell condition evidence, and computes exact
   correspondence against the selected dictionaries. No "latest" endpoint or
   hand-curated spelling/code replacement participates in the build.
   The manifest must pair the characteristics release with explicit ICD pins,
   distinguish original single-sheet and current two-sheet layouts, record
   attachment applicability independently of the catalog, and carry reviewed
   expectations for groups, lists, member occurrences, node kinds, unsupported
   reasons and exact correspondence. Original and current rows never mix in
   one release; shared names do not merge 1a with 1ae.
4. **Review gates before promotion.** Structural drift, dangling identities,
   bounds/checksum failures block a publishable build. Known ambiguous
   interpretation stays explicitly unsupported. Review compares membership
   sets **and** duplicate evidence, source cells, unsupported reasons,
   correspondence counts and temporal windows. A dictionary-only update must
   recompute correspondence too; it must not rewrite NFZ member identity.
   Both build-time and bundle-open validation must check the same structural
   contracts. A checksum/foreign-key check alone does not prove payload type,
   operator arity, nonempty provenance, graph bounds or guard binding.
5. **Immutable application artifacts.** A reviewed source/transform pair builds
   a complete checksummed reference bundle. Compatible application/API and
   static artifacts are released together through the existing deployment
   process after separate owner approval, never an in-place database patch.
   Older releases remain available for covered service/knowledge dates; rollback
   restores a compatible artifact set rather than repairing rows on the host.
6. **Consumers (#32 and later).** CLI/store/API/UI and later offline snapshots
   consume the same immutable structured evidence. #30 does not independently
   introduce UI/API links. #32 must use source-local member identity and exact
   correspondence status: matched links may target the paired code page;
   absent dictionary links must retain visible NFZ code/title/evidence, not
   disappear or point at a nearby parent/replacement. Public dictionary search
   continues to use its canonical source and is not silently extended by JGP.

Examples: if a BAW workbook changes only duplicate rows, membership remains
unchanged but provenance changes. If RSK later adds an exact missing code,
rebuilding with that reviewed pin changes correspondence, not the NFZ member.
If an upstream source introduces an unfamiliar condition/layout, a watcher
capture succeeds independently of whether reference transformation can proceed;
the parser/build review must not advance a data release from lost cells,
unchecked structural drift or dangling identities. Complete reference evidence
with explicitly unsupported interpretation can be published after review;
unsupported evidence continues to block executable promotion.

### Cross-Application Acceptance Checklist

Before claiming automatic population is complete, verify at the real boundaries:

- A BAW attachment change is archived and proposed, not silently live; checksum
  drift or failed metadata fencing cannot advance successful capture state.
- A new NFZ member absent from RSK survives import with exact source evidence
  and `not_in_reference`; a missing selected ICD pin fails the build instead.
- A reviewed RSK-only pin change recomputes correspondence in a fresh artifact,
  including an absent-to-matched transition, without rewriting NFZ members.
- 1a/1ae same-named lists stay separate; repeated list rows preserve both cells
  while set cardinality and per-member counting remain unchanged.
- Every label fragment, annotation, marked member and title-only qualifier has
  accounted-for evidence, even when its interpretation is unsupported. No
  unknown requirement vanishes into an otherwise proposed expression.
- Dated consumers use the selected characteristics and dictionary release
  identities rather than linking an old member to an arbitrary newer concept.
  Uncovered dates fail explicitly; no fallback to the newest release is allowed.
- N09/N11 remain characteristics with explicit absent catalog correspondence,
  not zero-point products. Condition windows do not inherit the catalog's
  deferred 2027 row dates by matching a code alone.
- Bundle publication/rollback is atomic for the compatible artifact set.
  Build/import failure leaves the previously reviewed application data active.
- Later CLI/API/UI/offline consumers retain unmatched NFZ codes and source
  notices. Missing dictionary linkage is not hidden membership; canonical
  dictionary search and its privacy/noncommercial boundary remain unchanged.

These are cross-issue handoff obligations, not a claim that automatic population
is complete. The #30 implementation record identifies tested ingestion/store
boundaries; #21 and #32 must test their own automation and consumer boundaries.

No watcher scheduling, acquisition allowlist, production pin, app route,
Cloudflare setting, settlement policy or deployment is changed by this decision.

## Checkpoint Verification

Post-work pair-programmer review independently verified the retained source
bytes and receipts: all 19 absent RSK codes occur in each pinned NFZ workbook,
with 61 / 68 / 69 source occurrences in the original / after-74 / after-102
captures. It reproduced the RSK code/title inventory comparison, namespace
rejection by the existing importer, and the four cited C27 statistical counts.
These are research-evidence checks, not condition-import acceptance counts.

The documentation checkpoint passed repository pre-commit gates, including
pytest, Ruff, mypy and BasedPyright, plus local document-link/anchor checks.
Those checks predate implementation. The separate #30 acceptance record covers
the importer and actual CLI runtime; no production throughput, deployment or
new UI behavior is claimed verified.
