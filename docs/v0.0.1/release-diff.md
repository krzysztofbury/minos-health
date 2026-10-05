# Logical Reference Release Comparisons

The offline `minos data diff` command compares explicitly selected, pinned
releases in verified bundle directories. It does not fetch sources, choose
"latest", modify a bundle, classify an update as routine or activate it.
Issue #20 owns this contract; #31 adds the conditions comparison and the
[new-base-act checklist](operations.md#new-base-act-and-conditions-review-checklist).
The future #104 UI can consume its versioned JSON.

```sh
uv run minos data diff /private/old-bundle /private/new-bundle \
  --pair terminology:OLD_ICD9_RELEASE:NEW_ICD9_RELEASE

uv run minos data diff /private/bundle /private/bundle \
  --pair catalog:OLD_CATALOG_RELEASE:NEW_CATALOG_RELEASE \
  --pair conditions:OLD_CONDITIONS_RELEASE:NEW_CONDITIONS_RELEASE \
  --json --full
```

Bare `.db` files are rejected: the verified manifest, complete schema and pinned
database checksum are part of the comparison input. The same bundle may contain
both selected releases and is verified once. Repeating `--pair` adds explicit
comparisons, not inferred pairings. Missing releases, duplicate pairs, mismatched
terminology namespaces/origins and canonical-versus-supporting roles fail.
Use a separate terminology pair for supporting workbook hierarchy.

## Compared Boundaries

- Terminology concepts match source-local lookup codes within the selected
  namespace. Changes retain raw titles, typed descriptions, annotations, status
  and source date attributes. Label changes do not imply a clinical rename.
- Hierarchy resolves references to coded anchors and distinct source-ordered
  anonymous occurrences. Anonymous structural correspondence is conservative,
  not an inferred semantic identity. Moving a child between identical anonymous
  siblings must remain visible. Workbook cell/sheet movement is provenance,
  not changed concept meaning. Source order is reported separately.
- Catalog populations match catalog context plus group code. Repeated group
  rows and separate service windows remain intact, with linked full schedules.
  Base values retain exact integer hundredths/days and absent cells remain absent.
  Deltas are emitted only for unambiguously comparable single-row products, with
  both applicability contexts. Twin rows are shown without invented alignment.
  These are catalog base points without modifiers, not final tariffs.
- Conditions compare separate 1a/1ae group graphs, ordered roots, operators,
  selectors, guards, counting policies, thresholds, units, age anchors, unsupported
  fragments and reasons. Every graph/list/member reference is remapped to logical
  descriptors; physical row or sequential IDs do not define cross-version identity.
  Shared DAGs are emitted once rather than recursively expanded.
- Directional lists retain code/marker/text, member multiplicity and source order.
  Repeated declarations are compared as source-ordered populations with explicit
  ambiguous correspondence, never guessed renames. Exact evidence populations
  and node/selector-to-evidence bindings remain separately reviewable.
- Applicability, explicit ICD/catalog correspondence, supporting-source pins,
  algorithm identities, modifier binding/pending state and knowledge horizons
  are compared separately from authoritative content. Selected manifest and
  source-header SHA identities cover complete provenance, including URLs without
  printing potentially credential-bearing addresses. Layout metadata remains
  visible. A changed licence or transform can be significant even with unchanged
  clinical content.

## Output And Limits

Default Markdown starts with complete category/action counts, exact selected
pairs and verified before/after dataset/database identities. Details repeat their
pair and escape Markdown/control characters. JSON uses a version-1 envelope
with the same counts and old/new values. The default preview shows 20 changes
and excerpts values longer than 2,048 characters, explicitly marking omitted
changes and truncated values. `--full` removes presentation truncation only.

Resource limits remain enforced: 16 pairs, 250,000 rows per projection query,
250,000 facts and 64 MiB serialized bytes per projection, 100,000 changes and
32 MiB retained change values across all pairs, and 16 MiB output. SQL work shares
a 100-million-operation budget and a 120-second computation deadline across
connections and pairs. Output decoding/serialization is bounded and checked
before printing success. Bundle opening first uses the existing artifact byte,
schema and integrity bounds; its verification phase has no separate whole-command
interruptible deadline. These are operational limits, not a memory or latency SLA.

Failure emits no partial successful stdout. Existing CLI exits apply: invalid
request 2, artifact failure 3, resource limit 4. No source rows or local paths
are echoed in error diagnostics. Successful full reports contain private source
reference text: keep them in reviewed private storage, not public logs or Telegram.

Every report says `complete_candidate_audit: false`. Unpaired releases, detailed
MODTAR mappings, algorithm/rank contents and search index/synonym contents are
outside this increment. Explicit bindings are evidence of pairing, not executable
encounter qualification. #21 must compose a complete reviewed candidate policy,
trusted promotion/rollback and durable detailed Telegram alerts before enabling
automatic routine activation. This command alone is not that policy or pipeline.
