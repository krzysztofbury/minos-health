# ICD-10 Reference Pipeline

## Agreed Scope

Issues #11, #12 and #13 share `feat/icd10-reference-pipeline`. Issue #10's
storage checks move into the real artifact pipeline, not a disposable spike.
Search precedence, standalone packaging and container search acceptance remain
with #14 and the relevant runtime work; they are not satisfied by creating an
FTS5 table. No public search or data redistribution is enabled by this slice.

The reviewed model is one generic source hierarchy, not one tree per ICD
variant. `reference_node` and typed `reference_relation` preserve source
containment. Concepts and multilingual descriptions link to those nodes;
publisher, version, timestamps, checksum and notices belong to `source_release`.
Code lookup identity is release-scoped and distinct from raw node identity.
XML containment does not assert classification `is_a` or billability.
Numeric modifier labels are preserved but not expanded into diagnosis codes.

## Source Contract Evidence

The ignored capture `20260921T182646Z` passed `validate_private_capture` and the
existing opt-in capture test before structural inspection. The pinned XML is
12,738,027 bytes with SHA-256
`f3dc9811520e45079655eecb123e857818c5a14189fa605996daa3aa3fe7b88f`.

Observed totals: 24,040 nodes, 12,936 coded nodes, 11,104 uncoded nodes,
593 empty-name uncoded containers, maximum XML depth 21 and node depth 9.
There are 1,054 dotted relative-code nodes under three-character categories
and 565 single-digit modifier nodes. The remaining 11,317 coded nodes have
self-contained codes, including 296 range nodes. Repeated raw labels are not
duplicate concept identities. Attributes are `EN` (23,441), `annotationPL`
(7), and `annotationEN` (4); one `EN` value is empty.

The parser must preserve every node and annotation, including empty containers
and repeated descriptive text. Synthetic fixtures protect this contract without
committing the licensed source or asserting that uncoded text is a synonym.

## Acceptance Boundaries

- Parser: hardened incremental XML, exact source fields, explicit contextual
  failures and bounded input, depth, elements, nodes, values and attributes.
- Store: one fresh transaction, generic release-scoped graph, code uniqueness,
  resolved references, hierarchy integrity, exact descriptions plus versioned
  search keys, FTS integrity, reviewed counts and read-only reopening.
- Artifact: verify pinned bytes before parsing, deterministic database, external
  checksum manifest, complete-bundle publication and preservation of prior
  valid artifacts on failure.
- Queries: bounded recursive CTEs for hierarchy, typed rows and deterministic
  ordering; product search ranking remains #14.
- Verification: focused synthetic tests, private full-source parse/build and
  measurements, fault cases, wheel runtime smoke and full repository checks.

## Implemented Boundaries

- `minos.ingest.icd10`: incremental Expat adapter with no DOM or network access,
  immutable header/node/edge events and explicit `SourceError` context.
- `minos.domain.reference` and `normalization`: library-independent values and
  normalization v1. Source fields retain decoded XML text, including spaces,
  Polish letters, markers, annotations and empty containers. XML's own entity
  and line-ending decoding is not byte-for-byte source preservation; raw archived
  bytes remain the authority.
- `minos.store`: schema v1, checksummed migration, generic release-local graph,
  linked concepts, multilingual descriptions, normalization checks, indexed
  lookup keys, external-content FTS5 and typed read-only queries.
- `minos.ingest.reference_artifact`: copy and checksum pinned archive bytes into
  a private temporary snapshot before parsing, then build a fresh database in
  one transaction. Finalize FTS, integrity and expected counts before publishing
  a complete nonempty directory containing `minos.db`, `manifest.json` and
  `review.txt`. Existing output directories are rejected, not updated in place.
- `scripts/validate_icd10_reference.py`: local Linux build/read validation, not
  the #14 CLI. The development input manifest is JSON schema v1; it is not yet
  the composite JGP ruleset manifest illustrated in the product design.

Header and release validity are preserved separately. The capture manifest's
fetch time identifies the batch start, not an invented individual transfer
completion timestamp. Node-level status, billability and legal reporting-month
applicability are not inferred from the XML root's historical attributes.

### Generic Graph And Source-Specific Adapter

Node identity is `(source_release_id, ordinal)` with source preorder independent
of code. Both relation endpoints use composite foreign keys within that release.
Multiple parents are representable. Recursive CTE results are unique nodes in
stable source order with shortest-path depth. Both edge directions are indexed.

The current adapter emits only `contains`; it never invents `is_a`. The store's
current containment importer requires parent preorder before child preorder,
which proves acyclicity without a second complete in-memory hierarchy. The
schema and reader can distinguish `is_a`, but a future source adapter must
provide and validate its semantics before publishing those edges. The generic
store test uses unrelated code shapes and French/Spanish descriptions; the
artifact orchestrator currently accepts only the reviewed RSK ICD-10 adapter.

Description language and type are adapter decisions, not RSK-specific SQL
behavior. Annotations remain source-node attributes. Range concepts are
distinguished from classification concepts; neither kind asserts billability.

### Bounds And Activation Limits

Parser defaults: 64 MiB input, XML depth 64, 500,000 elements, 100,000 nodes,
65,536 characters per direct text field, 16 attributes/namespaces per element
and 256 characters per attribute value. Values and records are not retained
beyond the bounded ancestor stack and chunk-local event queue. Comments and
processing instructions have explicit handling; only UTF-8 is supported.

The writer uses an 8 MiB SQLite cache and a 1 GiB database page ceiling (512 MiB
before #141).
Input and artifact files must be regular files; no-follow, nonblocking opens
reject symlinks and FIFOs rather than waiting indefinitely. These file-open
flags and the validation profiler are Linux-specific in this slice.

Hierarchy calls accept at most depth 64 and 10,000 results, with a 250 ms and
two-million-VM-operation execution ceiling. Overflow raises `QueryLimitError`,
not a silently incomplete hierarchy. The CTE keeps the recursive walk outermost
using `CROSS JOIN`, forcing indexed edge probes instead of release-wide scans.

Bundles are private, operator-owned immutable local generations. Concurrent
publication to one output path and durable active-pointer replacement are not
implemented. Source snapshots prevent hash/parse rotation. Runtime bundle
directories must not be modified during checksum verification and read-only
opening. Signed promotion and production activation remain separate gates.

## Measured Local Evidence

All measurements below use the pinned 12,738,027-byte archive, local Linux,
Python 3.14.7 and SQLite 3.53.1 with the standard `sqlite3` driver. The artifact
records compile options, runtime identities, lockfile SHA-256, transformation
and normalization versions and source notices. This is not a global SQLite
version pin for untested standalone or container targets.

| Boundary | Observed result |
| --- | --- |
| Parser | 24,040 nodes, 24,018 containment edges, about 0.32 s |
| Concept projection | 12,075 classification concepts plus 296 ranges |
| Descriptions | 24,735 preferred/translated rows |
| Exact node attributes | 23,452 rows, including annotations and empty `EN` |
| Full artifact build | about 1.04-1.17 s; 20,873,216 bytes |
| Installed-wheel code lookup | 1,000 codes, median about 0.0066 ms, p95 about 0.0098 ms |
| Chapter traversal | 1,875 nodes, 30 calls, median about 4.26 ms, p95 about 4.89 ms |
| Reproducibility | identical DB checksum from source checkout and installed wheel |

Final reviewed database SHA-256:
`573ed67d3bdf1f484c912ce0f7f336ae5752ddc28d9c28c07b8e9ff3e87c5ef2`.
The external manifest's build clock is deliberately isolated from deterministic
database bytes. The review file is a provenance/count summary, not a semantic
classification diff or legal applicability review.

Source-checkout and fresh installed-wheel build probes observed approximately
45-48 MiB process memory. One wheel launcher reported a much larger `ru_maxrss`
already at Python entry, with no increase during the workload. `/proc/self/status`
showed approximately 47 MiB current RSS instead. The Linux validation script
therefore reports current-image `VmHWM`, avoiding attribution of the launcher's
pre-exec peak to Minos. Do not interpret inherited `ru_maxrss` as application
growth or claim a universal memory bound from these representative samples.

The installed-wheel validation ran from outside the repository with socket
connections prohibited and exercised full-source construction, checksums,
integrity, code lookup and hierarchy. Packaged SQL resources worked without
repository fallback. This is not standalone operation without Python, an OS-level
air-gap test or container search acceptance.

## Regression And Fault Evidence

Focused tests own these boundaries: exact-limit and one-above-limit parser
behavior; UTF-8/CDATA and fragmented input; DTD/entity/unknown-structure rejection;
relative-code context; exact descriptions and separate normalization; generic
codes/languages; multiple parents; release-local code identity; missing references;
cycles/preorder; duplicate lookup identity; count mismatch; read-only writes;
checksum corruption; archive rotation; FIFO/symlink rejection; late parser,
finalization and publication failures; existing-artifact preservation; repeatable
DB bytes; and representative 3,000-child traversal within the unchanged budget.

Real SQLite rollback tests leave no schema after a failed fresh transaction.
Faults after complete parsed rows and at finalization/publication leave no output
bundle or candidate staging directory. No production-only hook was introduced
for these tests. Automated mutation tooling remains deferred; these explicit
fault and boundary cases cover the changed critical decisions.

Final local full suite: 885 passed, eight environment-dependent skips. The
complete pre-commit gate passed, including formatting, Ruff, mypy,
BasedPyright, Bandit, pytest, lockfile/license policy, secret scanning,
Terraform formatting and workflow lint. No formatter or lint side effects
remained after the gate. The existing
wheel CI smoke now also parses, builds and reopens the synthetic reference
artifact. Private full-source CI remains #15 rather than requiring raw source
bytes in Git or images.

## Remedium Comparison

The owner supplied <https://remedium.md/icd10> as a real-world comparison, not
an authoritative source. Desktop Chromium loaded the site, opened its search
control and searched a public E11 sentinel. The observed results contained the
category and ten subcategory entries. Its rendered detail page exposes
E11.0-E11.9, matching the direct child concept code set in the pinned artifact.

Remedium presents contextual child labels and related clinical/drug content.
Minos does not copy those labels or extensions into source descriptions, adopt
its ranking, scrape the complete site or use it to override RSK provenance.
This single controlled comparison does not establish full classification,
clinical correctness, source-version equivalence or competitive performance.
Screenshots and bounded comparison evidence remain outside Git.

## Local Reproduction

With the existing ignored private archive available:

```sh
uv run python scripts/validate_icd10_reference.py \
  --manifest manifests/sources/icd10-rsk-reviewed.json \
  --archive-root data/private-fixtures/rsk/20260921T182646Z \
  --output /tmp/opencode/minos-reference-review-new
uv run pytest tests/unit/test_icd10_parser.py tests/unit/test_icd10_bounds.py \
  tests/integration/test_reference_artifact.py
```

The output must not already exist. A second fresh build can select the first
bundle with `--previous-bundle` to generate a reviewed count/provenance summary.
The source UUID or upstream URL is never fetched by this command.

## Pair-Programmer Review

- TigerStyle #2: Bounded loops and #6: Positive and negative space - parser,
  import and query bounds are explicit; malformed and unsupported structures
  fail before publication.
- TigerStyle #4: Paired assertions and #12: Full error handling - checksum
  verification precedes parsing and read-only opening; SQL integrity checks,
  rollback, complete-bundle publication and failure preservation are exercised.
- TigerStyle #9: Function length - parser, builder and store functions remain
  below 70 lines; source-specific decisions stay outside generic SQL.
- TigerStyle #15: Hot loop isolation - representative full-source evidence
  exposed the recursive join-plan regression; indexed traversal fixed it without
  loosening the execution ceiling. A synthetic fanout regression guards it.
- TigerStyle #11: Warning clean and #20: Format and line length - run the full
  repository checks after final formatting and inspect the resulting diff.

No production deployment, public endpoint, archive download, standalone CLI
bundle or paid-data clearance is claimed by this slice.
