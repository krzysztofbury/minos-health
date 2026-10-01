# Offline Reference CLI

Implementation of #14. Jev, embeddings and model-assisted results are deferred
to #95. This is dictionary lookup, not clinical query interpretation.

## Commands

Install the pinned application and its `reference` extra for Python reference
build/search use (`uv sync --locked --extra reference` from this checkout).
Morfeusz and the workbook reader are deliberately absent from the musl-based
operational watcher image; the standalone application contains both. Supported reference runtime:
Linux x86-64 glibc. Other platforms require separate packaging acceptance.

```sh
minos data build --manifest manifests/sources/icd10-synthetic.json \
  --archive-root /path/to/archive --output /path/to/new-bundle --json
minos search --system icd10-pl --query "E11" --data /path/to/bundle --json
minos search --system icd10-pl --query "pokaz mi mozliwe kody dla cukrzycy" \
  --data /path/to/bundle
```

`--manifest` accepts the strict reviewed JSON source manifest already used by
the reference pipeline. `--archive-root` contains `blobs/sha256/<digest>`.
Source bytes are verified before parsing. There is no download or latest-source
resolution in these commands. `--output` must not exist. The output is a complete
directory containing `minos.db`, `manifest.json` and `review.txt`; `--data` accepts
that directory, never a bare database. `--previous-bundle` supplies count/digest
comparison evidence, not a semantic or legal change review.

Optional search parameters:

| Flag | Contract |
| --- | --- |
| `--release ID` | Required when the selected system has multiple releases; never choose latest |
| `--language pl\|en` | Defaults to `pl`; text lookup stays in this language |
| `--limit N` | Default 20, maximum 100 |
| `--index auto\|portable` | Default `auto`: FTS5 when available; otherwise the B-tree token index |
| `--json` | Version 1 machine-readable output instead of the table |

Database SHA-256, manifest/schema/migration identity, normalization version and
search version are checked before returning results. Schema 2 is a fresh artifact
format: rebuild from pinned source manifests; existing schema-1 bundles are
rejected, not changed in place. Parser transformation `icd10-reference-v1` and
normalization v1 are unchanged; the additional projection is `lexical-2`.
The analyzer and dictionary identities are also checked at build/search time.

CZS ICD-9 PL XML builds are also supported under `icd9-pl` with `origin: czs`.
The reviewed historical input is `manifests/sources/icd9-czs-reviewed.json`.
Root version and `ICD9:<generation>` identity must match the manifest. All source
statuses and supplied procedure date bounds are retained, including `N` entries.
The CZS XML has no parent links, so its release has no inferred hierarchy.
NFZ workbook builds use `origin: nfz-workbook` and the pinned `openpyxl==3.1.5`
reader in the `reference` extra. The reviewed historical companion is
`manifests/sources/icd9-nfz-workbook-reviewed.json`. Its `source_file_id` is the
exact filename followed by `#` and the exact selected worksheet name, including
the trailing space in `ICD-9_PL `. The distinct `ICD-9_PL` table is not merged.
Only explicit column containment is retained; code prefixes and preceding rows
do not supply absent parents. Raw titles/code forms and conflicting later titles
remain source evidence, not corrections or hidden synonyms. The workbook has no
status/date evidence; it cannot substitute for the complete CZS catalog.

Bundling both ICD-9 sources requires an explicit `--release` on every lookup.
`review.txt` reports same-version code/title disagreement counts without joining
their graphs. New reviewed files use new release IDs and new output directories;
old bundles are never modified. See [the source contract](../validation/icd9-source-contract.md)
for bounded ZIP/XML validation, typed-cell policy and full-source evidence.

## Lookup And Context

Results are deduplicated by release-local source node. Ordering is exact code,
exact literal normalized description, indexed code/token prefixes, explicitly
labelled Polish morphology tokens, then substring
only if the indexed stages returned nothing. Within a stage ties use source node
order. Code-shaped requests use code prefixes, never fuzzy spelling or invented
punctuation. Code-shaped detection is conservative, not a classification grammar
or a claim that an unknown input is a valid code.

Text tokens are ANDed, independent of order. Tokens containing numbers and tokens
shorter than four characters are exact; longer words use prefixes. Substring
fallback still requires those exact tokens as complete source words. Operator-looking
words and punctuation cannot inject FTS or SQL expressions. Polish letters remain
distinct; there is no accent stripping or English Porter stemming.

Search includes preferred/alternate/translated concept descriptions and uncoded
text nodes, including their source English `EN` attribute. Other annotations and
modifier/container labels do not become extra diagnosis descriptions. An uncoded
match is attributed to the first concept on each containment path, with the exact
matched text retained. Unowned text remains explicitly uncoded. This projection
references the original graph; it is not another classification tree.

Every result includes the matching node and all containment ancestors through
the root as nodes and edges. Multiple parents remain multiple edges. Ancestors
are context, not additional search matches; containment does not imply `is_a`,
billability or descendant match propagation. Stable source order is not clinical
ranking. One evidence document is selected deterministically per result, not an
exhaustive list of all matching descriptions.

## Polish Phrases And Spelling

A small explicit lookup wrapper handles `pokaż/pokaz [mi] [możliwe/mozliwe] kody
[icd-10] dla/dotyczące ...` and the corresponding `wyświetl/wyswietl` form.
The literal query remains available for exact matching.

Morfeusz2 1.99.15 with embedded SGJP `pl.sgjp.sgjp-2026.06.01` (BSD-2-Clause)
owns Polish inflection analysis. There is no maintained medical inflection map,
per-classification analyzer or spelling dictionary. Literal source words remain
separate from the indexed lemma relation, so literal matches always precede
morphological matches. Each distinct token keeps its literal form and all
accepted single-word lemma alternatives, rather than selecting one meaning.
Numbers, code tokens, unknown words and words shorter than four characters stay
literal. Short grammatical lemmas cannot be introduced by longer ambiguous forms.
This prevents `bez` (negation) from matching `bzami` through the plant homonym.
SGJP homonym suffixes are removed only from additional lemma search keys.

`morphology_tokens` results retain exact source evidence and expose analyzed query
alternatives in JSON and text. Ambiguity is not contextual disambiguation, synonym
enrichment, clinical interpretation or arbitrary natural-language reasoning.
Qualifiers, numbers and negation are not discarded. Parent labels are not
implicitly copied into child descriptions. English lookup remains literal.
Source-word analysis is cached only during a bounded build; queries are never
persistently cached. An incompatible/missing analyzer or dictionary fails closed.

On zero matches, RapidFuzz 3.14.6 (MIT) scores the source-derived release/language
vocabulary using Levenshtein distance. There is no custom fuzzy algorithm,
trigram index, provider call or ICD-specific spelling dictionary. Words shorter
than four characters, numeric/code-shaped input, and protected qualifiers are
not corrected. Maximum distance is one below eight characters and two otherwise.
Up to three suggestions are returned, ordered by distance and lexical value.
They do not execute a replacement query or appear as authoritative results.

## Bounds And Exit Codes

Search accepts at most 256 characters, eight tokens, and 64 characters per token.
Substring fallback needs at least three characters and never handles code-shaped
input. A shared 250 ms query deadline and two million SQLite virtual-machine
operations cover all retrieval/context stages. Each hit permits 256 ancestors,
64 graph levels and 1,024 context edges; source-label/evidence/attribute content
across the response is bounded to 512 Ki characters. Spelling permits at most 100,000
length-filtered vocabulary candidates per word. Native scoring is count-bounded;
the deadline is checked before and after it, not a preemptive native interrupt.
Morphological analysis accepts at most 256 dictionary analyses and 16 alternatives
per bounded word. The native analyzer is count/input-bounded, not preemptively
interrupted by SQLite's progress handler.
Artifact verification and interpreter/process startup are measured separately
and are not inside the SQL query deadline. No incomplete context is returned as
complete results; resource-limit failures produce no partial response.
For hardened read-only-root execution, supply bounded writable temporary storage
for SQLite sorting. The full ICD-10/ICD-9 smoke run used a 64 MiB `/tmp` tmpfs for
both build and query acceptance; database/source mounts remained read-only.

| Exit | Meaning |
| --- | --- |
| 0 | Search results, successful build, help or version |
| 1 | No results, possibly with explicit spelling suggestions |
| 2 | Invalid CLI/request, unsupported system/language, ambiguous release |
| 3 | Missing, incompatible, altered or unreadable data; failed build |
| 4 | Query resource/deadline limit exceeded |

No application logs or query caches are created. JSON intentionally contains
the query and matching source text for the caller; do not log or upload it as
telemetry. CLI arguments may remain in shell history or be visible in process
listings. Do not put patient data into reference lookup commands.

## JSON Version 1 Contract

Search success and no-results each write one UTF-8 JSON object to stdout:

- `version`: integer `1`; `status`: `ok` or `no_results`.
- `system`, `release_id`, `language`, `dataset_id`, `database_sha256`,
  `search_version`, `morphology_version`, `index_used`: strings. Dataset identity is the SHA-256 of
  the canonical source manifest; database identity is its verified file hash.
- `literal_query`, `effective_query`: strings; `interpretation`: array of
  explicit interpretation strings; `truncated`: boolean.
- `morphology`: array of `token` strings and `alternatives` arrays of strings;
  empty when the morphological stage was not needed or is inapplicable.
- `results`: array of objects with `node_ordinal` integer, `code` string or
  null, `source_code`, `label`, `kind`, `match_type` strings. Kinds are
  `classification`, `range`, `modifier`, `text`, `container`; match types are
  `exact_code`, `exact_description`, `prefix_tokens`, `morphology_tokens`, `substring`.
- Each result's `source_attributes` is an object of exact source attribute names
  to source strings, empty when absent. CZS ICD-9 entries expose `status` (`A` or
  `N`) and supplied `data_od`/`data_do` bounds, plus source annotations. Text
  output displays these too. No value is an inferred billability decision.
- Each result's `match` contains `node_ordinal` integer, exact source `text`,
  `description_type` (`preferred`, `alternate`, `translation`, `context`),
   and `language` strings. Context evidence may belong to a different node.
   Its `source_attributes` preserves that matching node's exact evidence, including
   workbook inclusion/exclusion/note markers and worksheet/row or cell identity.
   Text output exposes these markers too; context matches are not synonyms.
- Each result's `context.nodes` contains `ordinal` integer, `code` string or
  null, `source_code`, `label`, `kind` strings. `context.edges` contains
  `parent`, `child` integers and `type: "contains"`.
- `suggestions`: array of `query`, `replacement` strings and `distance` integer.
- `provenance`: `publisher`, `source_version`, `source_sha256`, `license`
  strings and `notices` array of strings. `disclaimer`: reference-only string.

Build stdout contains `version: 1`, `status: "built"`, `dataset_id`,
`database_sha256`, `row_counts` (table names to integer counts) and `disclaimer`.
Application errors with `--json` write an object to stderr with `version: 1`,
`status: "error"`, and `error.code`/`error.message` strings. Error codes are
`invalid_request`, `data_error`, `limit_exceeded`. Argument-parser errors occur
before adapter dispatch and use a fixed privacy-safe text message, even with
`--json`. No raw arguments, file paths or tracebacks appear in those errors.

The same bundle/query/configuration yields identical ordered JSON bytes. Changing
index mode changes `index_used`, not match ordering. JSON field additions or
breaking changes require an explicit contract/version review.
The morphology fields finalize the unreleased version-1 envelope in this PR;
there is no published version-1 client to migrate.
The additive `source_attributes` field was explicitly approved for version 1
on 2026-10-01 during #16. Existing fields and exit codes are unchanged.

## Standalone Linux Build

```sh
bash scripts/build_standalone_cli.sh /path/to/new-output
uv run python scripts/smoke_standalone_cli.py /path/to/new-output/build/dist/minos
```

Docker builds the application in digest-pinned Debian Bookworm, with the separately
locked builder tools, uv 0.12.7 and Python 3.14.7. Bookworm's C++ runtime prevents
the host compiler runtime from imposing a newer glibc floor. This is an isolated
packaging environment, not a new production service. System package repositories
are not an immutable build snapshot; byte-identical standalone application builds
across arbitrary dates are not claimed. Signed promotion remains #41.

The application directory must be kept intact alongside its `_internal` directory;
the data bundle is separate. Runtime needs no installed Python, uv or network.
This slice supports Linux x86-64 glibc only. Windows, macOS, musl and broader
desktop portability are not claimed. Build on the oldest supported glibc target
before broader distribution. This PR verifies Bookworm, not every glibc version.

PyInstaller 6.22.3 and its build dependencies use the isolated lockfile in
`packaging/standalone/uv.lock`. The application uses root `uv.lock` and an installed
wheel, not editable source imports. The approved PyInstaller GPL exception permits
generated bundles to keep their own license; the application license allowlist
is not broadened. See <https://pyinstaller.org/en/stable/license.html>.
The builder's dependencies do not become application runtime dependencies.
Bundled notices cover Morfeusz/SGJP and the collected system C++ runtime; these
do not change medical source redistribution rights.

CI builds the synthetic ICD-10/CZS/workbook artifact through the frozen application inside a
digest-pinned Debian container, without installed Python, with networking
disabled, no capabilities and a read-only root filesystem. It checks both
indexes, known source identity, ancestors, deterministic bytes and no-result exit.
An installed-wheel sentinel independently exercises the regular `minos search`
entry point. No licensed full-source data is sent to CI.

Builds require a writable output parent and SQLite scratch storage. The hardened
container smoke supplies `/tmp` as a bounded 64 MiB tmpfs (`nosuid,nodev,noexec`);
complete-source sorting can spill there even when the tiny synthetic fixture
does not. Pure read-only search does not require a writable data directory.

Local checksums are integrity evidence, not signed promotion. #41 still owns
immutable publication, provenance and signed release promotion; public data
redistribution still requires the separate rights review. Neither issue is
completed by a successful local standalone build.
