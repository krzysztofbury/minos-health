# Archive Freshness Evidence

Status: useful #19 subset extracted to `feat/validation-archive-build`, 2026-10-06.
The original branch and #140 preserve deferred prototypes under #141. This is
an operator build tool, not an enabled/deployed unattended freshness pipeline.
Contract and remaining boundaries: [archive processing](../v0.0.1/archive-processing.md).

## Useful-Subset Review (2026-10-06)

The extracted branch retains the operational archive CLI/build adapter and its
owning integration/real-S3 tests, the previously approved acceptance-policy
repair, and the bounded Chromium startup fix. It excludes every processing,
authority, OCI-overlay, signing and OIDC-rehearsal prototype. #141 preserves those
on the original branch; no test or production seam is added just to keep them.

TigerStyle review checked #2 bounded transfer/startup, #4 paired input/response
identity, #6 unsupported-state refusal and #12 stream/client cleanup and redaction.
Existing archive integration cases exercise the actual CLI and transactional
builder, not mocked parser outcomes; real-S3 cases remain separate wire/IAM proof.

After extraction, all repository pre-commit checks passed, including the full
Python suite, warning-clean Ruff/mypy/BasedPyright, security and workflow checks.
All five Node startup regressions passed. An isolated installed wheel, run outside
the checkout, built both local and SDK-stubbed S3 synthetic bundles with matching
database bytes. Offline imports remained free of cloud/watcher runtime adapters.
Both task scratch directories, wheels, virtualenvs and synthetic bundles were
removed. Prior real-S3 results below are retained evidence, not a new cloud run.
Hosted container/browser and general CI evidence must pass on the extracted PR
before merge. No throughput, representative capacity or production update is
claimed by this scope split.

## Pinned API Package Repair (2026-10-06)

The extracted PR's first hosted general CI run `37430396866` passed. Validation
run `37430396791` passed the real container/browser smoke, then refused the API
image for seven fixable HIGH/CRITICAL findings in `perl-base=5.36.0-7+deb12u3`.
The pinned Python base predates Debian's fixed `5.36.0-7+deb12u4` package.

The API Dockerfile now installs that exact security version alongside the existing
pinned PCRE2 fix. A disposable base container verified package availability and
installation. Trivy v0.70.0, with a fresh database and the unchanged hosted
HIGH/CRITICAL, fixable-vulnerability policy, found zero such findings in the
rebuilt local API image. No vulnerability exclusion or threshold was changed.
The rebuilt API/edge smoke passed through real Chromium, including search,
preferences, fallback, conditions, JGP and ranks, privacy, bounded synthetic load,
and stopped-API readiness recovery. These are regression smoke results, not
representative production-capacity evidence. The full Python suite passed with
1,643 tests and 23 opt-in skips. Owned images, containers, browser profiles,
scanner cache and scratch were removed; shared build caches were not pruned.
Hosted checks must rerun on the pushed repair before merge. The owner explicitly
requested no deployment for this operator-tooling-only PR.

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

## Browser Startup Race Recovery (2026-10-05)

General hosted CI `37361709217` passed at `c873b31`. Validation run `37361709208`
failed in `jgp-browser.mjs` with an empty port after Chrome's port file became
readable. Search, preferences, fallback, conditions and JGP-search browser checks
had already passed; this was not evidence that JGP page assertions passed.

The five harnesses with the same read-and-break pattern now share a bounded
`waitForDevToolsPort` reader. It waits for a complete valid port line, not mere
file existence, and checks browser exit/signal/spawn failure before returning.
It retains the 60-second refusal bound and uses fixed diagnostics. The separate
page-target and all UI/privacy assertions are unchanged. The empty-file and dead
browser regressions failed against the extracted old behavior before the fix.
Five Node tests now pass and run in the hosted validation workflow.

Before/after TigerStyle review focused on #2 bounded startup, #6 refusing invalid
ports and #12 handling process failure without extending waits indefinitely.
Local full API/edge/browser smoke passes through real Chromium, including JGP
catalog/filter/date/group, procedure ranks and the existing filter/privacy gates.
An initial local image smoke used the scratch wrapper's private umask for nginx
inputs and failed before browsers; the corrected child umask matches hosted CI
without changing the private scratch root or any application check. Both attempts'
task-owned images, containers, profiles and scratch were removed. No application
or production configuration changed. Hosted validation for the fix remains pending
until its new pushed revision passes.

## Remaining Build Gates And Deferred Automation

- Protected-default-branch OIDC role evidence and hosted complete real-selection
  repeated builds. The disposable S3 results below do not establish that boundary.
- Artifact metadata/provenance reconciliation, including how the candidate audit
  retains the canonical transport selection and verified build identity.

The transport selection stays separate from the existing `manifest.json` artifact
contract. Build identity/source/checksum/count fields are already present. Hosted
complete real-source determinism and workflow-role acceptance remain #19 evidence
gaps. The following #21 automation gates are parked in #141, not client-validation
blockers:
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
private tagged disposable stack in the project's AWS account, `eu-central-1`.
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
