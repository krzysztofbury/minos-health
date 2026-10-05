# Archive Freshness Evidence

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

- Protected-default-branch OIDC role evidence and hosted complete real-selection
  repeated builds. The disposable S3 results below do not establish that boundary.
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

## Disposable Real S3 Acceptance (2026-10-05)

Eight focused tests in `tests/aws/test_archive_build.py` passed against the
private tagged disposable stack in account `218375352108`, `eu-central-1`.
Only synthetic fixtures were uploaded, never real source bytes or owner diffs.
The test reader used a 900-second session policy intersected with the bounded
test role: only `s3:GetObjectVersion` on that bucket's `blobs/sha256/*`.
This is real S3 and session-policy evidence, not GitHub OIDC evidence.

- The actual operational CLI built terminology, ranks, catalog, conditions,
  MODTAR and reviewed synthetic binding inputs twice. Both database byte streams,
  identities and table counts matched the local builder. The source dataset and
  canonical transport-selection digest were retained in the CLI result checks.
- A delete marker followed by a newer corrupt current version did not replace
  the selected predecessor. Reads continued to request its exact version.
- Wrong owner, another key's real version, pinned size, native digest, absent
  native SHA-256 and a delete-marker version all failed before offering bytes to
  a parser. The size case also changes the dataset pin, so the real S3 response
  check, rather than local manifest coverage, owns that refusal.
- Real writes, version deletion, unversioned latest reads, recovery-image keys
  and unrelated state keys were denied. A pinned blob remained readable.
- Transfer staging was removed and the fixture's exact-version object manifest
  was used for guarded cleanup. These small fixtures are not production-capacity,
  throughput or full real-source determinism evidence.

Setup exposed three existing acceptance-path defects, repaired without relaxing
source trust or switching the test runner to administrator credentials:

1. Two S3 API names were used as IAM actions. The owner approved exact two-policy
   repair plan `6f724013626d7d1bb185c6e818b3cd1b7a2db4c7e7be13c371b6fffb2cb4c9d9`:
   `GetBucketAccelerateConfiguration` to `GetAccelerateConfiguration`, and
   `GetBucketReplication` to `GetReplicationConfiguration`. Resource scopes,
   trust, conditions and write permissions are unchanged. The saved-plan guard
   permits only these two replacements from the exact reviewed prior policy;
   baseline, scope, extra-action, identity and inline-policy changes fail.
   AWS simulated 34 allow/deny decisions before apply; live verification checked
   eleven decisions and the exact boundary/inline-policy identities afterward.
2. IAM's live boundary type is `Policy`, unlike the SDK model spelling
   `PermissionsBoundaryPolicy`. Both are accepted only with the exact ARN and
   fields. Unknown types and wrong ARNs remain rejected.
3. S3 returns the whole-bucket multipart-abort filter as `{"Prefix":""}`.
   Only that equivalent spelling is normalized to `{}`; narrower filters and
   altered retention/transition rules remain rejected. Both wire-format
   regressions failed before their corrections.

After-change TigerStyle review also found #4/#12, paired assertions and full
error handling: the test recording client validated key grammar only after a
PUT/COPY. The new refusal test reproduced the remote mutation before failure.
The wrapper now checks grammar before either mutation. One task-created
out-of-grammar synthetic version from the failed test was separately approved,
verified by exact bytes/owner/version/task tags, deleted by exact version and
checked absent. The cleanup grammar was not expanded.

The broad legacy AWS suite remains unsuccessful: after the eight archive-build
cases passed it stopped at its older `harness/*` write, outside the current
acceptance-operator write policy. No additional IAM access was granted, and the
focused result must not be reported as full watcher/recovery AWS acceptance.

Both disposable stacks were separately approved for teardown and checked absent:
the initial four-resource failed apply (`c2007a0c0d2fc3f4`) and the completed
test stack (`18d4041ad74a26c4`, writer revoked before ten-resource destruction).
No production archive, service, source selection or notification was changed.
The narrowly scoped acceptance-operator repair remains applied.

Ignored receipts: `test-output/archive-freshness-y71ymgd2/aws-archive-build.xml`,
the earlier failed-suite XML files, partial-setup receipts, and the guarded
operator/acceptance plan and state records under `artifacts/`. No generated
database, private source data, Terraform state or credential is committed.

The final standard suite passed: 1,643 tests, 23 opt-in skips. All repository
pre-commit checks passed, including warning-clean type checks and real browser
fixtures. An initial full-suite attempt used the scratch wrapper's private umask
for fixtures that require explicit public-directory permissions; it failed three
existing publication/browser cases. Re-running with the expected `0022` fixture
umask passed without changing application guards. Read-only test directories
also prevented the first scratch cleanup; owner-only directory permissions were
restored in that exact task directory and it was removed. Later full runs install
a task-scoped fixture-permission cleanup trap before the wrapper removes scratch.

## Local Recovery/Change Reader (2026-10-05)

Before-change TigerStyle review required paired transport/read assertions (#4),
bounded enumeration (#2), read-only immutable access and explicit exclusion of
raw state (#6), plus redacted errors and cleanup (#12). The SQL boundary now has
28 integration cases using real disposable control databases and staged recovery
images. They exercise deterministic per-source enumeration, exact reselection,
captured revisions/configuration hashes, private-state exclusion, serialized
batch validation, foreign-key/migration tampering, input bounds, deadlines and
path replacement after the reader connection opens.

After-change review tightened two boundaries before submission: raw text must
not enter an unbounded sort or `fetchall` allocation (#2/#7), and joined queries
must not silently drop malformed changes (#6). A materialized narrow-ID selection
now precedes left joins and row-at-a-time hashing. Exact recovery validation owns
foreign-key refusal; independent query checks own revision/configuration drift.
Transport-version reselection includes the version even for identical bytes.
The fixture uses a catalog observation, deliberately not a parsed product release.

Four targeted in-memory mutations were killed by the intended refusal assertions:
configuration-digest comparison, observation/change revision equality, recovery
identity equality during reselection and serialized deterministic order. No source
file was changed for mutation runs. An isolated installed-wheel smoke created a
real staged recovery image, enumerated/reselected one change, excluded private
state and verified lease cleanup. No cloud resource or production state was used.

The earlier hosted Verify job `37337714745` exposed a stale Terraform test digest
for the owner-approved IAM repair. Only the expected acceptance-policy digest
was changed to the already reviewed `467f052b...be57ef`; the provider-backed
Terraform validation/test now passes locally with AWS v6.66.0. This is not a
cloud policy mutation. Hosted rerun evidence remains separate.

Final local checks passed: 1,671 tests, 23 opt-in skips, and all pre-commit
checks (including Ruff, mypy, BasedPyright, Bandit and browser fixtures).
Receipt: `test-output/archive-freshness-y71ymgd2/local-recovery-reader.xml`.

The reader has no automatic trigger or promotion/deployment path. Complete
before/after source selection, durable processing state, hosted real-source
builds, signing authority and actual notification/activation acceptance remain
open; this local reader does not close #19 or #21.
