# Attachment 9 Reference Ingestion (#30)

Status: implemented on the review branch, not merged or deployed. This is
source-backed reference data, not an executable ruleset or settlement result.
The [reviewed taxonomy](jgp-conditions.md) and
[correspondence policy](jgp-code-correspondence.md) remain authoritative.

## Implemented Boundary

- The original single-sheet 46/2026 layout and the separate 1a/1ae layout used
  after 74 and 102 are explicit, bounded parser profiles. The latter retains
  the `nfz-9-102-2026` identifier; accepting after-74 bytes is a separately
  pinned, count-checked selection, not a latest-file fallback.
- Frozen domain data represents only the 16 reviewed atomic kinds and
  `all`, `any`, `not`, `when`. Ordered group roots are reference fragments,
  never an implicit conjunction. Every node and selector cites physical cells.
  Parent expressions inherit unsupported children. Included mandatory subsets
  belong to one count; they do not add another total count.
- Source evidence retains exact A/B/C values, worksheet and row identities,
  member descriptions, duplicate occurrences, marks and naming declarations.
  Header identity and the source workbook digest are separately verified.
  List/code set membership never turns duplicate source rows into occurrences.
- Missing or ambiguous list references produce unsupported fragments without a
  dangling selector. Family expansion stays unbound. Annotation guards bind
  exact marked physical members, not every use of the same code. Missing
  diagnosis roles and clinical units are unsupported, not defaults.
- The existing `minos data build` publishes schema 5 only when characteristics
  are selected. Earlier source manifests keep their schema version. Ingestion
  remains offline, transactional and deterministic; failure publishes no bundle.
- Bundle opening rechecks manifest bindings, dictionaries, counts, provenance,
  payload types, graph order/arity/depth, source occurrences and exact catalog
  correspondence. It also reconstructs extraction and interpretation from stored
  source cells, rejecting a payload/source disagreement even after an attacker
  recomputes the database checksum. This is structural validation, not a
  replacement for trusted artifact provenance or commercial legal review.
- The store resolves service and knowledge dates without a latest fallback.
  Knowledge uses reviewed publication dates; verified recapture time remains
  separate. Characteristics have their own unchanged 2026-10-02 horizon.
  Original and amended open windows are disambiguated by explicit supersession.
  Uncovered contexts/windows fail explicitly. Ambiguous release chronology is
  rejected before building; publication order or closed endpoints never invent
  a missing supersession edge.
- Catalog correspondence is pinned separately to the same reviewed BAW document.
  It retains every exact-code row window. A dated lookup selects the matching
  row, while attachment 9 retains its own window. N09/N11 are `not_in_catalog`
  after 102 and matched in the original catalog. An omitted catalog pin is
  `catalog_unbound`, not a zero-point product. Future/deferred rows never shift
  the whole condition release to 2027.

The production manifest, application URLs, shared ICD/JGP interface, query
privacy, canonical dictionary search, watcher behavior and source terms are
unchanged. #32 owns public condition/cross-link consumers. #21 owns reviewed
manifest PR automation. #35 still requires reviewed executable policies and
algorithm/dictionary/oncology bindings; #46 owns modifiers. RSK correspondence
does not clear content for commercial calculations.

## Conservative Source Gaps

Not every source code block has an explicit ICD marker. Code-title rows outside
a verified physical list are `unbound_member` evidence, not label predicates.
Current 1a has 438 such cells: 239 in A57A (4), E47 (9), H84O (102), M16 (2),
PZC02 (31), S02 (91), plus 199 in shared-list contexts. The original has 46
across A57A/E47/M16/PZC02. Their exact cells survive, but no classification,
list membership or dictionary link is manufactured. They are not included in
the resolved-member counts below. Group-local blocks also carry unsupported
`list_definition_unbound` roots; shared blocks retain their unsupported evidence
category and their users' unresolved list references.

This is intentionally conservative. Explicit contextual naming in A03 is
supported with its declaration cell; resolving other markerless/contextual
blocks requires reviewed fixtures and source evidence. Likewise, inline
alternatives, rank compatibility, allocation, units and oncology scope remain
explicitly unsupported/unbound. No unsupported fragment acts as true or false.
The 19 RSK gaps are a separate exact-code correspondence observation, not these
structural source gaps. Every persisted selector resolves source-locally;
unresolved syntax never persists a supposedly resolved target.

## Reviewed Complete-Source Expectations

Counts are parser output, not the overlapping lexical census in #29. All
kind/reason counts are pinned in
`manifests/sources/jgp-conditions-reference.json`. `unsupported` in `kind_counts`
counts unknown-kind nodes; `unsupported_reasons` also includes typed nodes and
operators whose interpretation is unsupported.

| Selection | Groups | Evidence cells | Physical lists | Resolved member occurrences | Unique per-list members | Nodes | Unbound member cells | Unmatched distinct codes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Original 1a | 702 | 40,042 | 892 | 35,081 | 35,058 | 3,358 | 46 | 19 |
| After 74 1a | 627 | 37,133 | 872 | 32,130 | 32,109 | 3,260 | 438 | 19 |
| After 74 1ae | 121 | 10,418 | 176 | 9,540 | 9,532 | 443 | 0 | 12 |
| After 102 1a | 629 | 37,461 | 877 | 32,437 | 32,423 | 3,306 | 438 | 19 |
| After 102 1ae | 121 | 10,413 | 176 | 9,535 | 9,534 | 443 | 0 | 12 |

The complete-source check also corrects a #29 shorthand: after 74, 1ae's
`Lista Ogólna` lacks J98.4/J98.8 relative to 1a; after 102 both contain 2,750
distinct codes. They still have separate physical identities, even when their
sets coincide. Synthetic differing same-name lists protect against accidental
cross-sheet resolution independently of that current equality.

All 16 kinds are represented in current 1a; the five title-only qualifiers are
exactly N03, N03A, N06A, N06B and PD01. Current 1a's 19 unmatched identities
are ICD-10; 1ae contains 12 of those. No unresolved canonical dictionary pin
is accepted. An exact dictionary-only pin change recomputes correspondence
without changing NFZ cells or member identities.

The workbooks were refetched and checksum-verified on 2026-10-03; those actual
UTC acquisition times are pinned. Retained before/after BAW metadata receipts
are identical for 43943/44042. For 43868 only `LegalBasisActs` differs; file
inventory, identity, publication, validity and version relations remain equal.
Neither metadata equality nor a reused filename substitutes for byte checks.

## Reproducible Build

The reviewed selection is deliberately separate from the production selector.
Provide a private archive with all verified base-manifest files and the three
attachment 9 blobs under `blobs/sha256/<digest>`. Sources and generated licensed
artifacts must never be committed. `DEST` is an operator-chosen output directory;
the composer refuses to overwrite an existing file.

```sh
uv run python scripts/compose_conditions_manifest.py --output "$DEST/dataset.json"
uv run minos data build --manifest "$DEST/dataset.json" \
  --archive-root "$ARCHIVE" --output "$DEST/bundle" --json
uv run minos search --system icd10-pl --query E11 --data "$DEST/bundle" --json
MINOS_JGP_CONDITIONS_PRIVATE_ARCHIVE="$ARCHIVE" uv run pytest \
  tests/compatibility/test_jgp_conditions_private_capture.py
```

The composer combines the unchanged reviewed terminology/catalog manifest with
the #30 selection, validates the combined manifest and selects transform
`jgp-conditions-reference-v1`. It neither fetches sources nor advances either
knowledge horizon. Every kind, unsupported reason and correspondence count
must match before publication. `review.txt` includes these expectations and
retains canonical source licenses/notices alongside NFZ condition notices.

## Acceptance Evidence And Limits

Synthetic tests exercise the public reader/interpreter, real SQLite artifact
build/open boundaries, source tampering after recomputed checksums, failed-build
cleanup, canonical codec refusal, dictionary-only correspondence transitions,
dated selection and absent catalog products. Fixtures cover every kind/operator,
guard families, D52A's limited exemption, included/union counts, source duplicates,
case-sensitive lists, contextual naming, resumed labels, title-only groups,
markerless code titles, unknown phrases, dates/sites and missing units/roles.
The opt-in compatibility test checks pinned complete sources and literal cells.
Neither suite is a patient fixture or external grouper validation.

Two actual full-source CLI builds on the development workstation took 24.89 s
and 23.81 s; peak child-process RSS was 176,180 KiB. Repository-wide checks ran
concurrently during this measurement. Both builds produced the same
database SHA-256:

```text
914b94ee810c4e7ad7789d562b3394ab7a5eb4e6f3efb896a52eb12069bdee75
```

Validated bundle opening plus dated reads and one reconstructed current sheet
took 5.19 s. The real offline search CLI returned the exact E11 result against
that schema-5 bundle.
These are bounded local acquisition/build/read measurements, not API latency,
incumbent comparisons, production throughput or VPS suitability. Revalidation
is intentionally paid at the immutable-bundle boundary; #32 must measure its
consumer path rather than validating the whole artifact on every request.
Disposable copied archives and generated databases were removed after the
checks; only ignored receipts, count reports and pinned research bytes remain.

Pair-programmer review focused on TigerStyle #2 (bounds), #4 (write/read
symmetry), #6 (unsupported states), #9 (function length) and #12 (error handling).
It tightened source/payload reconstruction, guard occurrences, parent status,
scalar bounds and canonical codec refusal, and separated markerless member
evidence from predicates. The extraction/reader functions slightly exceed 70
lines to keep one finite layout scan and its error/cleanup boundary together;
they are not recursive grammars. Human review is still required before merge;
deployment needs separate approval. No UI change needs a browser check in #30.
