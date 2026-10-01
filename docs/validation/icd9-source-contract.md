# ICD-9 PL Source Contract Inspection

Issue: #16. Inspection date: 2026-10-01. Status: separate source releases approved;
CZS XML and NFZ workbook adapters implemented and locally validated. This report
records pre-submission acceptance, not deployment or source publication.

## Scope And Evidence

The comparison uses the privately archived NFZ watcher capture from 2026-09-29,
not a newly downloaded or presumed current release. Local archive SHA-256 values
were verified before inspection. No production access, watcher state change,
public data upload, or redistribution was needed. Source files remain outside
Git.

| Evidence | Version identity | Size bytes | SHA-256 |
| --- | --- | --- | --- |
| CZS ICD-9 XML | 5.90, generation 93 | 1,630,852 | `e3c744634e68f23d5ecbfecb50ece831140eb8533ab72b8a1b3ee54594024b1b` |
| NFZ companion XLSX | 5.90, linked to the same XML by the watcher observation | 1,228,323 | `6c607dd5e564d6e8033b5314946a7f0e43103cc067b9d26346a64dcd05b76a2e` |

Pinned source locations:

- <https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=93>
- <https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/icd-9_pl_cm_v_5.90.xlsx>

These historical identities must not become default latest-version selectors.
The existing watcher discovers new CZS generations and archives matching NFZ
workbook evidence. A reference build must still select reviewed, pinned bytes.

## XML Contract Observed

The unnamespaced `komunikat` root identifies `typ=ICD9`, `wersja=1.1`,
`symbol=5.90`, `edycja=5`, `instytucja=NFZ`, `nr_gen=93`,
`czas_pub=2026-09-24T08:35:45`, and `czas_gen=2026-09-24T08:58:10`.
Local generation timestamps have no timezone; they must not silently be
interpreted as UTC or as clinical validity dates.

- 10,179 unique direct `pozycja_slownika` children, with `kod`, `nazwa`, and
  `status`; 10,058 have status `A`, 121 have status `N`.
- Optional populated `opis` on 13 entries, `wlaczenia` on 158, and `wylaczenia`
  on 1,078. These source annotations are not synonyms or inferred codes.
- 8,224 entries have one `daty_proc` child: 8,195 with `data_od`, and 29 with
  both `data_od` and `data_do`. Neither bound is empty in this capture.
- No source parent identifiers or nested classification hierarchy. Code shapes
  include letter chapters, two-letter chapters, letter-plus-number laboratory
  codes, numeric codes, and dotted suffixes up to five digits.
- Source order starts with `A`, then `AA`; workbook hierarchy instead places
  `AA` above `A`. XML order and code prefixes do not establish containment.

All entries, including status `N`, must remain source-preserving and searchable
in a complete reference release. Status and dates are evidence, not a billability
decision. A root header/version mismatch must reject a build, even when its file
checksum matches a manifest.

## XLSX Contract Observed

Archive inspection before the workbook reader found 27 entries, 10,104,677
expanded bytes, no duplicate entry names, and no rejected paths, encryption,
DTD, or entity declarations under the inspection bounds. Every entry was read
to its recorded size, exercising decompression and CRC validation. This is
inspection evidence, not a production ZIP-preflight implementation.

The workbook has six sheets:

| Sheet | Rows | Role |
| --- | --- | --- |
| `ICD-9_PL `, with trailing space | 8,150 | First explicit four-level classification table |
| `ICD-9_PL` | 8,148 | Another table with materially different content |
| `wł_wył_rozdz` | 301 | Chapter inclusion/exclusion evidence |
| `wł_wył. podrozdz` | 2,275 | Subchapter inclusion/exclusion evidence |
| `wł_wył_kat gl` | 8,307 | Main-category inclusion/exclusion evidence |
| `Uwagi` | 675 | Notes |

The first two sheets use code/title column pairs for chapter, subchapter,
main category, and detailed category. Their merged cells are header ranges
`A1:B1`, `C1:D1`, `E1:F1`, and `G1:H1`; data rows repeat explicit context.
Reader selection must not trim sheet names and conflate these two sheets, or
silently pick whichever appears first in a future workbook.

With explicitly recorded surrounding-code whitespace conversion and integer
cell conversion for this inspection, the first table contains 10,059 unique
codes:

- 121 XML entries are absent: 120 status `N` entries and one status `A` entry,
  `92.221`.
- One workbook-only code, `27.10`, does not exist in the XML capture.
- Six codes have multiple source titles within the first table.
- There are material title disagreements between XML and XLSX, not only
  whitespace changes. Canonical titles must not silently be replaced with
  whichever source looks more plausible.
- 21 code cells are integers, representing repeated chapter codes `88` and
  `93`, with number formats `General` or `@`. Other populated code cells are
  strings. Decimal numeric codes cannot safely be round-tripped as strings.
- Two rows omit the chapter but retain explicit subchapter/category evidence.
  Missing parents must not be invented or filled from preceding rows.
- Code trimming eliminates an apparent multiple-parent case in the raw-cell
  comparison. Raw cell evidence must remain distinguishable from lookup keys.

Both main sheets are therefore inadequate substitutes for the complete CZS
release. The second table also has substantially more title and whitespace
disagreements. Its presence is evidence to preserve, not permission to merge
tables without a reviewed contract.

## Workbook Reader Probe

PyPI identified `openpyxl 3.1.5` as the current published version at inspection,
with MIT licensing and dependency `et-xmlfile`. The reader was installed only in
an ephemeral `uv run --with openpyxl==3.1.5` environment; neither repository
dependencies nor lockfile changed.

The real workbook was opened with `read_only=True`, `data_only=False`, and
`keep_links=False`, then closed explicitly. Rows and columns were bounded for
iteration rather than trusting inflated sheet dimensions: two auxiliary sheets
declare 252/253 columns despite only a few populated columns. The probe retained
cell types and number formats. No formulas or date-valued cells were present in
the inspected ranges; this does not establish behavior for such adversarial
cells. A production adapter needs dedicated probes and rejection tests.

A single local run covering all sheets' first ten columns and comparing both
main tables took 1.294 seconds; whole-process peak RSS was 124.48 MiB. That RSS
includes XML DOM, ZIP/XML preflight DOMs, workbook shared strings, and comparison
collections. It is not a streaming-adapter benchmark or a memory-budget promise.

## Approved Source Decision

Use CZS XML as the complete authoritative code/title/status/date release, and
retain the NFZ workbook as separately attributed companion evidence for explicit
hierarchy and source annotations. Do not infer hierarchy from prefixes, overwrite
XML titles with workbook titles, drop inactive entries, or disguise disagreement
as a successful join.

The user approved separate release-local reference graphs with explicit CLI
release selection when both are bundled. The existing graph schema attributes
each release to a single blob; adding workbook relationships to an XML-only
release would falsely attribute them to the XML. Separate graphs reuse that
schema. No composite provenance migration is needed or authorized here.

The user also explicitly approved additive JSON version-1 `source_attributes`
on 2026-10-01. Source status and supplied date bounds are visible in JSON and
text output without interpreting their legal applicability or billability.

Whichever contract is selected, new generations must pass the same bounded
validation and generate count/provenance/disagreement evidence. Unsupported
sheet layouts and source structures stop ingestion instead of triggering a
hand-maintained correction map. Old manifests and bundles remain immutable.

## Acceptance Gates

The approved implementation follows these gates:

1. Add source-specific typed adapters and dispatch through the shared build
   pipeline, keeping ICD-10 and ICD-9 releases distinct.
2. If a workbook adapter is included, pin the used reader at the offline
   reference boundary, not in the Alpine watcher. Implement ZIP/XML preflight
   before opening untrusted workbooks.
3. Use synthetic structural fixtures and private-capture compatibility checks;
   raw official files are not committed. Test archive limits, CRC/truncation,
   DTD/entities, duplicate identities, typed cells, unexpected sheets, version
   conflicts, atomic failure, and release-specific search.
4. Exercise real full-source build, deterministic rebuild, installed-wheel and
   offline CLI runtime paths, morphology, FTS5/portable agreement, and bounded
   resource measurements before claiming #16 complete.

No production deployment, signed release publication, public medical-data
redistribution, ICD-11 ingestion, or semantic release-diff implementation belongs
to this chunk.

## CZS Adapter Chunk Evidence

Earlier CZS-only checkpoint on `feat/icd9-reference-ingestion`, not committed or
submitted at that checkpoint. The workbook evidence below supersedes its pending
items and lockfile identity:

- `src/minos/ingest/icd9.py`: bounded incremental Expat parsing and strict
  Pydantic records for root, entries, and procedure dates. Root version,
  generation, source URL, and edition must agree with the pinned manifest.
  DTD/entities, processing instructions, namespaces, unknown fields, duplicate
  codes/date structures, malformed dates, inverted intervals, unsupported
  encoding, empty catalogs, and resource overruns fail closed.
- The shared builder dispatches `icd9-pl` / `czs` independently of `icd10-pl` /
  `rsk`. Existing schema-2 storage and release selection are reused; no source
  graph or status is synthesized.
- `manifests/sources/icd9-czs-reviewed.json` pins the inspected capture and all
  10,179 entries. The observation timestamp was checked against archived watcher
  evidence, not inferred from root publication time.
- Search/CLI hydration exposes bounded exact source attributes with the approved
  additive version-1 JSON field. Status `N` entries remain searchable and visibly
  retain their source status. Annotations are attributes, not synonyms.
- Unit tests protect the XML contract; real SQLite/subprocess integration tests
  protect mixed ingestion, morphological search, output status/date evidence,
  immutable generation updates, explicit release selection, and atomic failure
  after a checksummed source/header conflict.

The full reviewed ICD-10 capture and full ICD-9 XML produced identical database
bytes in two source builds, an installed-wheel build outside the checkout, and a
network-disabled frozen build on the digest-pinned Bookworm runtime:

```text
SHA-256: 775536fe8a833c13d1f1972d8117459e0bdf54190f7582ecdeb494de07aedfa6
Size: 95,313,920 bytes
source_release: 2
reference_node: 34,219
reference_relation: 24,018 (all from ICD-10; none invented for ICD-9)
concept: 22,550
concept_description: 34,914
search_document: 55,936
search_word: 29,312
search_word_lemma: 43,300
```

All 10,058 `A` and 121 `N` source statuses were verified in the built database.
Six representative code/text cases agreed between FTS5 and portable retrieval,
including an XML-only active entry and an inactive entry. Installed-wheel and
frozen CLI paths exposed source status and executed maintained Polish morphology.
The frozen runtime had no installed Python and no network access.

The first mixed build took 5.132 seconds. Sixty warm queries observed a 2.999 ms
median and 12.165 ms p95. Whole-process peak RSS across staging, two builds, and
queries was 215.75 MiB; it is not a parser-only or per-query RSS measurement.
These are local measurements, not an incumbent comparison or general latency
guarantee.

The frozen build passed with a bounded writable 64 MiB `/tmp` tmpfs. A first
read-only-root lookup without writable temporary storage returned the fixed
data-error response; the same lookup passed with the tmpfs. Read-only reference
data does not imply that SQLite sorting needs no writable temporary storage.
No validation or resource bounds were loosened to make it pass.

Validation at this chunk: 993 Python tests passed, eight environment-dependent
tests skipped; all repository pre-commit gates passed, including Ruff, mypy,
BasedPyright, Bandit, lockfile, dependency-license, Terraform, and workflow
checks. Test-first header/edition-conflict evidence reproduced a missing guard
before adding it. No dependency or lockfile change was made.

TigerStyle review found no unresolved blocker for this CZS chunk: explicit
nonrecursive control flow (#1), bounded parsing/query evidence (#2), unsupported
state rejection (#6), typed warning-clean code (#11), and transaction-owned
publication failure (#12). At that earlier checkpoint, XLSX preflight, workbook
parsing, adversarial reader probes and reconciliation were still pending.

## Workbook Adapter And Preflight

`src/minos/ingest/icd9_workbook.py` now dispatches `icd9-pl` / `nfz-workbook`
through the same verified-snapshot, atomic build boundary as CZS and ICD-10.
`openpyxl==3.1.5` (MIT), with locked `et-xmlfile==2.0.0` (MIT), belongs only to
the reference extra and developer environment. It is not installed in the Alpine
watcher. The production path uses read-only iteration, retains formulas rather
than accepting cached values, disables external links, and closes the workbook.

`src/minos/ingest/workbook_archive.py` validates the ZIP central directory before
`ZipFile` allocates entry objects. It rejects duplicate/ambiguous paths, encrypted,
multi-disk and ZIP64 archives, unsupported compression, active content, external
relationships, DTD/entities, processing instructions and unsupported XML encoding.
Every permitted entry is streamed through decompression and CRC checking before
the spreadsheet reader runs. Printer-setting binary parts remain inert archive
evidence; nothing is extracted or executed.

Default resource ceilings:

| Boundary | Ceiling |
| --- | --- |
| Compressed source | 32 MiB |
| ZIP entries | 128 |
| One expanded entry / total expanded bytes | 16 MiB / 32 MiB |
| Per-entry expansion ratio | 100 |
| Total XML elements / depth | 750,000 / 32 |
| Element text / attribute value | 65,536 characters |
| Eager shared strings / other eager XML | 8 MiB / 1 MiB |
| Style elements | 10,000 |
| Worksheet rows / columns | 20,000 / 300 |
| Worksheets / merged ranges per worksheet | 16 / 128 |
| Classification plus evidence nodes / hierarchy edges | 100,000 / 100,000 |

Actual bounded XML coordinates, not declared dimensions, define reader iteration.
Duplicate, out-of-order or row-conflicting cell coordinates fail preflight instead
of letting the reader discard data. A test-first probe reproduced that missing
guard, followed by passing adversarial and private-capture checks. Underreported
and Excel-maximum dimensions cannot truncate results or expand reader iteration.

The reviewed manifest selects the exact filename and exact sheet identity via
`source_file_id: "icd-9_pl_cm_v_5.90.xlsx#ICD-9_PL "`. Its filename version must
match the manifest. Unknown sheets, changed headers/merges, populated extra
columns, conflicting code levels and unsupported typed cells stop ingestion.
Code cells accept only strict strings or integers, never booleans, floats,
formulas, errors or date-valued cells. Lookup keys strip surrounding code
whitespace and stringify integers; raw forms and types remain source evidence.

Within the selected table, first-seen titles are preferred only for that workbook
release. Later distinct raw title/code/type combinations become labelled text
evidence nodes. All explicit parents survive; absent context is not forward-filled.
Stable topological ordering uses first-seen rank to break ties, while original
worksheet/cell identities retain source location. No source correction map exists.

The four auxiliary sheets preserve their populated annotation text, source title,
source code, worksheet, row and inclusion/exclusion/note marker. Linked text matches
are `context`, never synonyms, and expose `match.source_attributes` in JSON and
text. Unmatched annotation codes remain unowned text; no missing concept or parent
is invented. The second main table is explicitly unselected, not silently merged
or substituted. Both tables remain in the checksummed private source archive.

The final workbook graph contains 10,059 concepts, 2,993 evidence nodes and 12,950
containment relations. Ten evidence nodes record differing title/code/type forms;
the two numeric source forms for `88` and `93` are distinguishable from string
forms. No source status or procedure date is invented. The source observation
timestamp was checked against the private watcher capture.

## Reconciliation And Regression Evidence

`review.txt` compares separately attributed, same-version CZS/workbook releases.
The real 5.90 comparison reports:

```text
CZS-only codes: 121; workbook-only codes: 1
Shared codes with different preferred source text: 42
Workbook conflicting title/code-form evidence nodes: 10
CZS-only status A entries: 1
```

No title, status, date or hierarchy is copied across releases. A workbook without
a selected same-version CZS release is explicitly reported as not reconciled.
This is a count-only source comparison, not #20's semantic release-diff feature.

Synthetic tests exercise typed cells, header/merge/sheet selection, coordinate and
dimension attacks, entry/expansion/XML limits, CRC, truncated and broken deflate
streams, duplicate entries, external relationships and DTD rejection before reader
construction. Real SQLite and CLI tests exercise complete multiple-parent context,
missing parents, orphan notes, conflicting-title and exclusion-marker output,
explicit release selection, atomic failure after a valid earlier source, and
immutable CZS/workbook version updates. Private-capture compatibility checks are
opt-in through `MINOS_ICD9_PRIVATE_ARCHIVE`; no official bytes enter Git or CI.

CI's installed-wheel and network-disabled frozen smoke paths now build synthetic
ICD-10, CZS and workbook releases together. Their runtime exercises both ICD-9
sources, exact raw code evidence, status/date visibility, hierarchy and labelled
conflicting-title evidence rather than checking imports alone.

## Final Full-Source Acceptance

The full pinned ICD-10 capture, full CZS ICD-9 capture and reviewed workbook table
coexist as three separately attributed releases. Two initial source builds, a
post-review source build, an installed-wheel build outside the checkout and a
fresh frozen build all agree byte-for-byte, including `review.txt`:

```text
Database SHA-256: d64338acb96ab557373c29630ddcd365a45ed091c64a5e1ce903ddf453a1d681
Database size: 133,939,200 bytes
source_release: 3
reference_node: 47,271
reference_relation: 36,968
concept: 32,609
concept_description: 44,973
search_document: 68,988
search_word: 37,894
search_word_lemma: 57,704
Root lock SHA-256: 25e420d51a615aae7af6593715bbd1bcb8d3e5faef7533e65c54344b1e4a4a39
```

The dependency change intentionally updates the lock identity in the reviewed
source manifests; source checksums/release identities do not change. Existing
generated bundles remain untouched. The base application dependency export has
neither `openpyxl`, `et-xmlfile` nor `morfeusz2`; the installed wheel includes both
new SQL queries and the frozen bundle includes the reader and its metadata.

Eight representative in-process code/text/annotation cases agree between FTS5
and portable retrieval. Eighty warm lookups observed a 6.015 ms median and
10.896 ms p95. Six full-source CLI cases per runtime independently verify
ICD-10, both ICD-9 releases, maintained morphology, workbook hierarchy and
CZS-only active/inactive entries, with agreement across both indexes.

Initial full-source builds took 7.683 and 7.638 seconds. The post-review source
and installed-wheel runs took 8.169 and 8.120 seconds including their process
supervisor; isolated build-process peak RSS was 122.69 and 122.84 MiB. The
initial frozen run took 9.198 seconds including Docker startup. A hardened repeat
with isolated output storage took 8.777 seconds and observed 224.12 MiB container
peak memory (including filesystem cache). It ran on digest-pinned
Bookworm without installed Python or network access, as UID 1000, with no
capabilities, a read-only root and source mounts with no writable aliases, a 512 MiB memory limit, two
CPUs, and a bounded writable 64 MiB `/tmp`. Data-output storage was writable
only for build publication; lookup remounted the data bundle read-only and had no
writable data-directory mount.

The broader initial staging/query process peaked at 403.37 MiB; this includes
source verification buffers, opened databases and warm query work, and is not
the adapter/build-only figure. These local measurements establish representative
runtime behavior under the stated bounds, not an incumbent speed comparison,
public performance guarantee or production-load certification.

Fresh frozen fault injection also checks a checksummed truncated workbook, a
formula-valued code cell, and a CZS edition conflict. Each returns exit 3 with
the fixed privacy-safe JSON error, empty stdout, no published bundle and no
remaining staging directory. No reader/validation guard was weakened to pass.

Final Python suite: 1,037 passed, 10 environment-dependent skips. Both new
private-capture checks additionally passed with their opt-in archive configured.
All repository pre-commit gates pass, including warning-clean Ruff/mypy/
BasedPyright, Bandit, lockfile, dependency-license, workflow and Terraform checks.
`pip-audit` reports no known dependency vulnerabilities; the private application
package itself is not present on PyPI and is not covered by that database.

TigerStyle review found and fixed the coordinate ambiguity before acceptance.
No unresolved blocker remains for #16's local ingestion scope: bounded control
flow and resource allocation (#1, #2, #7), explicit unsupported-state rejection
(#6), warning-clean code (#11), complete error/atomic publication handling (#12),
and pinned reader options (#13) have executable evidence. These checks do not
establish merge or production deployment. Signed promotion, semantic release
diffs and public source redistribution remain outside this ingestion scope.
