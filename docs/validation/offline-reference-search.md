# Offline Reference Search Validation

Local #14/#15 and approved morphology/catalog extension evidence, 2026-09-30.
This supersedes the earlier narrow inflection-example measurements in this PR.
This is not a production benchmark or a claim of
clinical relevance, broad desktop portability or comparative speed.

## Representative Artifact

The reviewed archive and parser identity are unchanged from
[the pipeline validation](icd10-reference-pipeline.md). The root lock now adds
RapidFuzz 3.14.6 and Morfeusz2 1.99.15 with SGJP `pl.sgjp.sgjp-2026.06.01`;
both source manifests pin the updated lock SHA-256. Medical source bytes and
parser transformation are unchanged. No hand-maintained inflection map remains.

Schema 2, Python 3.14.7, SQLite 3.53.1:

| Measurement | Local result |
| --- | --- |
| Source nodes / containment edges | 24,040 / 24,018 |
| Concepts / concept descriptions | 12,371 / 24,735 |
| Search documents / attribution rows | 45,757 / 45,757 |
| Vocabulary / literal token postings | 21,716 / 189,259 |
| Release/language-scoped lemma mappings | 30,525 |
| Database bytes | 68,558,848 |
| Database SHA-256 | `7721c42a92fd5f74cb1a2a6475719b8cacbce75b30f0681119d9e81377b7d696` |
| Build duration / process-image peak RSS | 3.74 s / 101,632 KiB |

The schema adds language-specific documents, source-graph attribution, token
postings, a source-derived vocabulary and a separately indexed lemma relation.
Literal postings no longer contain three injected diabetes form keys. There is
no trigram table, bespoke
edit-distance implementation or hand-maintained spelling corpus. The increased
bundle size is reported rather than hidden; baseline schema-1 bytes are not
comparable product-search artifacts.

## Search Measurements

One thousand sequential mixed queries per index on an already verified/open
full-source connection: `E11`, `E11.0`, the reviewed Polish diabetes lookup
sentence, `cukrzycca`, `przewlekła niewydolność`, and unknown code `E99.9`.
These are non-patient dictionary queries. They cover exact code, text, spelling,
context and no-results. The final corpus additionally includes `cukrzycą`,
`niewydolności`, and `cukrzycy typu 2 bez powikłań`. The last query has no full-source
match; morphology does not invent missing qualifiers or copy ancestor labels.
This is not concurrent service-load evidence.

| Boundary | Median | p95 | Maximum |
| --- | --- | --- | --- |
| FTS5 mixed query (1,000) | 13.75 ms | 17.86 ms | 26.61 ms |
| Portable mixed query (1,000) | 13.98 ms | 18.61 ms | 38.86 ms |
| Installed wheel startup + verified E11 lookup (20) | 166.26 ms | 174.51 ms | 191.53 ms |
| Standalone startup + verified E11 lookup (20) | 237.78 ms | 243.58 ms | 256.32 ms |
| Installed wheel startup + verified inflected lookup (20) | 196.82 ms | 198.86 ms | 198.93 ms |
| Standalone startup + verified inflected lookup (20) | 281.96 ms | 295.07 ms | 299.68 ms |

Startup includes process/interpreter initialization, complete database hashing,
verification, search and JSON serialization. It is not cold-disk/cache eviction.
These timings do not establish a 250 ms end-to-end latency guarantee; that bound
is the shared query deadline, excluding startup and verification.

The standalone application occupied 85,113,587 bytes. Sampled Linux process-image
peak RSS was 53,424 KiB for E11 and 88,028 KiB for `cukrzycą`. RSS was
sampled from `/proc/<pid>/status` at 1 ms intervals, so this is sampled evidence,
not an exact maximum or inherited launcher `ru_maxrss` attribution.

Both indexes returned identical hits/context/suggestions for the mixed corpus.
Twenty startup runs per application returned identical JSON bytes. E11 returned
the category followed by its ten direct children; the child set matches the
prior Remedium E11 comparison. That comparison does not establish corpus-wide
equivalence. `cukrzycca` produced a clearly separated `cukrzyca` suggestion.
The expanded lexical corpus exercises real pinned morphological analysis, not
contextual disambiguation or arbitrary natural-language reasoning. The higher
build memory, application size and inflected-query startup cost are explicit.
The earlier six-query lexical baseline was approximately 11 ms warm median; the
new nine-query workload is not an apples-to-apples performance comparison.

## Standalone Offline Boundary

The installed-wheel standalone build uses PyInstaller 6.22.3 and hooks 2026.8 in
the separate builder lock. Its runtime was exercised against the complete
artifact in a non-root Debian Bookworm container with networking disabled,
all capabilities removed, `no-new-privileges`, read-only application/data mounts
and a read-only root filesystem. The base image contains no installed Python:

Final full-source acceptance exercised E11, `cukrzycą`, a spelling suggestion and
`przewlekła niewydolność` with `--limit 100` in both indexes. Complete JSON output,
apart from the explicitly different `index_used`, matched across indexes.
The synthetic smoke also asserts a genuine morphological `etykietą` match.

```text
debian@sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251
```

The committed synthetic smoke independently checks that no Python executable is
installed, builds source-pinned data through the frozen CLI, then verifies both
indexes, release/file identity, graph context, deterministic output and no-result
exit status. CI contains that smoke plus a real installed-wheel sentinel search.
No raw source or generated licensed database is committed or uploaded.

The initial workstation-built bundle failed because PyInstaller collected a host
`libstdc++` requiring glibc 2.38. The Bookworm 2.36 runtime correctly rejected it.
The builder now runs inside digest-pinned Bookworm, preserving its C++ runtime
and license notices. The hardened offline smoke passed after that packaging fix.
The operational Alpine watcher still builds without Morfeusz and its shell-free,
non-root, network-disabled `--version` path passed. A separate reference API image
will need its own glibc runtime acceptance.

The complete-source standalone build also reproduced the wheel-built database
SHA-256 offline; the final checkout, fresh installed wheel (outside the repo),
and frozen full-source builds all produced the same final hash above.
The baseline hardened-container attempt had failed without writable
SQLite scratch storage; the small synthetic fixture had not needed a sort spill.
Supplying a bounded 64 MiB `/tmp` tmpfs (`nosuid,nodev,noexec`) resolved that
configuration issue while retaining a read-only root filesystem. The committed
smoke now includes this setting. No resource bound or integrity check was relaxed.

## Reproduction And Limits

Targeted manual mutation checks used disposable package copies, never production
data or repository modifications. The integration contracts rejected removal of
FTS release scoping, acceptance of ambiguous release selection, and substitution
of interpreted text for literal exact-description matching. This proves those
specific guards, not complete mutation coverage.

The complete local suite passed 959 tests with eight environment-dependent
skips. All pre-commit gates passed, including Ruff, mypy, BasedPyright, Bandit,
pytest, source lock/license policy and workflow lint. Python functions added at
the production/build boundaries remain within the 70-line review limit.

Negation regressions were observed failing before the fixes in both indexes:
`bez` admitted the plant form `bzami` through an ambiguous lemma, and substring
fallback admitted `bez` inside `babez`. The real SQLite regression passes after
short grammatical lemma exclusion and exact short-token checks in fallback.
Dictionary/analyzer identity drift is rejected. Synthetic ICD-9-shaped and
ICD-11-shaped reference records use the same morphology and spelling boundaries;
these are not new source adapters or redistribution approvals.

## Catalog And Pair-Programmer Review

MiniSearch 7.2.0 (MIT) is pinned by code and license SHA-256, served locally within
the existing generated JavaScript route, with its full notice embedded. No extra
public route, CDN, content extraction or query request was introduced. Tests
cover exact identifier/name tiers, token order, numeric non-correction, labelled
suggestions, reused RSK UUID history, and matching historical BAW filename/date.
Real Chromium/pinned nginx verifies desktop, 390/320 px layouts, safe hostile text,
suggestion acceptance/focus, invalid/reset/empty states, provider retry/stale state,
query-free URLs and storage, and 50-card pagination. Search and suggestion screenshots
were visually inspected. Browser tooling attached to the user desktop was unavailable;
the installed Chromium/CDP test provided the real browser verification instead.

A local Node stress sample used 4,096 synthetic RSK observations, nine BAW
documents, 515 cards and 4,629 search records. Its RSK JSON was 4,023,808 bytes,
within the unchanged response limit. Schema validation plus index construction
took 131.04 ms; 1,000 mixed queries observed 1.83 ms median, 6.86 ms p95 and
13.05 ms maximum. Sampled Node heap usage was 86,072,776 bytes, not a browser
memory bound. The generated asset was 128,980 bytes. Public projection retrieval
for a current-data benchmark returned HTTP 403, so no current production catalog
performance claim is made. This is synthetic index evidence, not device/render or
competitive workflow latency evidence.

Final review applied TigerStyle #2 (Bounded loops), #4 (Paired assertions),
#6 (Positive and negative space), #9 (Function length), #11 (Warning clean),
#12 (Full error handling), #13 (Explicit defaults), and #15 (Hot loop isolation).
Review findings fixed: negation homonyms/fallback, preservation of reused-UUID
observations, explicit ambiguity/output provenance, missing bundled license text,
and host C++ ABI leakage. Both SQL retrieval modes keep exact precedence,
release/language scope and original multi-parent context. No unaddressed release
blocker was found for this CLI/catalog chunk; the remaining operational gates
below are not waived.

See [the CLI contract](../v0.0.1/offline-search.md) for build/search/standalone
commands, bounds, license boundaries and output. The full-source build additionally
uses the reviewed manifest and the ignored pinned archive documented by the
pipeline validation. Private results remain outside Git.

The first PR #96 run failed before CDP startup; the browser harness did not drain
Chromium's output pipes. A local probe emitting 1 MiB before launching the real
browser reproduced the same timeout. Draining both pipes immediately made that
probe and the normal full browser assertions pass. Startup failure diagnostics
are bounded to 8 KiB and sanitize local paths/control characters; they are only
reported before navigating to fixture pages. Remote CI confirmation is pending.

Remaining gates: remote PR CI must pass for the final head; no merge
gate is enforced on the current private GitHub plan; deployed API/container
search, representative concurrent host load, signed #41 promotion and public
redistribution are not established. This standalone acceptance does not replace
the application's future API-image or deployed-search acceptance.
Production deployment preflight found the operator's 1Password session unauthenticated.
No production SSH investigation or change was attempted; re-authentication is required.
