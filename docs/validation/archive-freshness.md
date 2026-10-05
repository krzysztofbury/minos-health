# Archive Freshness: First Chunk Evidence

Status: local work on `feat/archive-application-freshness`, 2026-10-05.
This is a #19 foundation for #21, not a merged/deployed freshness pipeline.
Contract and remaining boundaries: [archive processing](../v0.0.1/archive-processing.md).

## Changed Boundary

The operational `minos data build` accepts an exact S3 transport selection and
materializes only the complete pinned input set before reusing the existing
transactional builder. The standalone reference entry point remains local-only.
The reference artifact format and the active production bundle are unchanged.

## Review And Verification

Before-change TigerStyle review identified immutable source selection, bounded
transfers, verify-before-parse, offline import isolation and separation from
watcher activation checkpoints as required invariants.

After-change review corrected two risks before submission:

- TigerStyle #4, paired assertions: the CLI must pass the already-verified source
  manifest model to the builder, not reread a path after materialization. A
  manifest-rotation regression verifies the published dataset remains pinned.
- TigerStyle #12, full error handling: response bodies and the CLI-owned SDK
  connection pool close on success/failure. Cleanup errors cannot turn a failed
  read into success or discard the primary error. Output is printed only after
  materialization and client cleanup complete.

Local evidence:

- Standard suite after the final cleanup regressions: 1,629 passed, 15 skipped.
  Private live/AWS gates remain opt-in; skipped gates are not treated as passed.
- Synthetic archive tests exercise real build/open paths for terminology,
  Komunikat ranks, catalog, conditions, MODTAR and a reviewed legal overlay.
  Two archive builds equal the local builder's database bytes/counts for identical
  pinned inputs. Synthetic binding is not real MODTAR legal approval.
- Wrong/missing/native-composite checksums, response versions, sizes, corrupt,
  short/oversized bodies, delete markers and mismatched/incomplete/duplicate
  selections fail before any parser receives an archive. SDK failure output
  does not echo its supplied secret-shaped exception text or local paths.
- An actual botocore Stubber checks owner, key, version and checksum-mode request
  parameters and StreamingBody cleanup. This validates SDK shape, not AWS service
  behavior or the IAM role.
- Four scratch-source mutations are killed for the intended reasons: missing
  response-version guard, missing streamed-body checksum guard, omitted input
  coverage check and omitted request VersionId. Repository source was not mutated.
- An isolated installed wheel builds through the real operational CLI and SDK
  stub, and produces the same synthetic database as its local archive path.
  The offline reference entry point imports neither the S3 adapters nor the
  watcher runtime; existing source display parsers remain reusable.
- All repository pre-commit checks pass, including Ruff, mypy, BasedPyright,
  tests, secret checks and Terraform/workflow lint. No hosted
  CI or real S3/OIDC acceptance is claimed by these local results.

Ignored receipts and reproducible drivers are retained under
`test-output/archive-freshness-y71ymgd2/`. The real ICD-9 owner-review diff in that
directory is private source-bearing output, not a repository artifact or part
of the cloud test fixtures. Temporary bundles, copied mutation source, wheels and
virtual environments are disposed by task-owned scratch wrappers.

## Remaining Gates

- Real version-pinned S3/checksum/IAM evidence after credential/context preflight;
  scoped OIDC workflow wiring and hosted complete-selection repeated builds.
- Artifact metadata/provenance reconciliation, including how the candidate audit
  retains the canonical transport selection and verified build identity.
- #21 verified recovery-image/change selection, independent durable processing
  state, cursor/idempotency/coalescing/fencing and source-specific proposals.
- Explicit reviewed significance criteria and representative processing/resource
  evidence before unattended promotion is enabled for any profile.
- #41 signed compatible release authority before trusted automatic activation;
  actual application freshness and complete rollback evidence.
- Durable privacy-safe detailed Telegram delivery, distinct failed-candidate
  alerts while already down, deduplication and notification-outage recovery.

No production source selection, service, credential, schedule, notification
configuration or deployment changes are made by this chunk. #19 and #21 remain
open until their actual end-to-end acceptance gates pass.
