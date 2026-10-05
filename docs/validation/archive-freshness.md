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

## Data-Only Image Boundary (2026-10-05)

After owner acceptance of the revised private authorization direction, offline
OCI packaging now preserves the approved runnable application manifest's original
layer descriptors and runtime configuration. It appends one deterministic layer
with exactly three artifact files, independently verifies actual tar bytes and
returns `data_image_content_only`. The CLI writes a metadata/layer delta, not a
complete image layout, signature or activation envelope. It never invokes Docker,
contacts a registry, signs an arbitrary digest or rebuilds application code.

Before-change TigerStyle review required paired verification of metadata/content,
exact inherited runtime identities, fixed paths/permissions and refusal of links,
whiteouts, traversal, extra entries and malformed or ambiguous metadata. The
owning tests are `tests/integration/test_data_image.py`; existing bundle tests
continue owning database/schema validation. No test-only production hooks were added.

Local changed-boundary evidence:

- 36 integration cases use real synthetic SQLite bundles. They cover repeated
  packaging with identical input bytes, independent tar inventory/content checks,
  self-consistent malicious image metadata/layers, bad base/artifact pins,
  nonregular inputs, size bounds, duplicate JSON fields, expired deadlines,
  cleanup on failed flush and CLI overwrite refusal with redacted diagnostics.
- Four scratch-only mutations have passing baselines and are killed at the intended
  assertion: omitted base-manifest pin, omitted fixed tar-header check, omitted
  file-content check and changed inherited layer inventory. Repository source
  was never mutated; temporary copied packages/databases were removed.
- An isolated installed wheel, with locked reference dependencies and no source
  checkout on its import path, ran the packaging CLI successfully and rejected
  a wrong application-base pin without publishing output.
- A current-source Docker application base was built through the existing API
  Dockerfile. `scripts/smoke_data_image.py` assembled/loaded an OCI image preserving
  the original compressed blobs, verified Docker's exact runnable descriptor,
  unchanged runtime settings and diff IDs, and ran the real API entry point.
  Readiness and a synthetic ICD-9 search passed with no mount, network disabled,
  nonroot user, read-only filesystem/files, dropped capabilities and the existing
  512 MiB API limit. This is compatibility evidence, not representative capacity.
- Tested application runnable manifest:
  `b37a2cfc51bf628c7bcc39a47759deb87f71fa17dabf4cddca1cd3cb461cc1f3`.
  Candidate runnable manifest:
  `a2f752856fe34c8ab35685d5ac61662352258bc96f06db6207eda4412fe5a8b2`.
  The 563,200-byte data layer contained synthetic data only. These local identities
  are not published artifacts or production authority.

After-change review corrected an access-time false refusal by comparing inode,
permissions, size and nanosecond write/change times instead of the whole stat
tuple. The regression failed before the fix. Generated metadata now passes the
same bounded parser as received metadata, and unsupported incomplete history fails
closed. Docker inspection uses its runnable descriptor rather than assuming an
image ID is a configuration hash. An initial 256 MiB smoke process was killed with
exit 137; the successful run uses the established 512 MiB setting, not a new
capacity claim or changed production limit.

The full standard test suite passes: 1,726 passed, 23 opt-in skips. An initial
private-umask scratch run broke three preexisting directory/browser fixtures;
rerunning with the expected `022` child umask inside the private `0700` scratch
root restored all three without changing production checks. Read-only task-owned
fixture directories were made owner-writable for cleanup, without elevation or
changes outside that allocated tree. The first cleanup refusal was resolved.
All repository pre-commit checks pass, including the actual browser-backed source
page test. These local checks do not establish hosted results for the new head.

The caller must still establish approved-base directory/volume semantics and all
authority/provenance gates. The cooperative 300-second overlay deadline cannot
interrupt a blocking filesystem call or the existing bundle/SQLite verifier.
One output path has one writer; directory publication is not a crash-durable
activation checkpoint. KMS, OIDC, registry publication, hosted provenance and
automatic activation have not been enabled. Task-owned Docker tags/containers and
scratch archives were removed; unrelated Docker/build caches were not pruned.

Reproduce with a locally built base under an owned scratch wrapper:

```bash
~/.local/bin/agent-scratch --root /tmp/opencode -- bash -c \
  'uv run python scripts/smoke_data_image.py --base-image <local-api-base> --work "$AGENT_SCRATCH_DIR/runtime"'
```

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

## Offline Delegated Signature/Scope Checks (2026-10-05)

The owner accepted the delegated trust model for offline verifier/refusal work,
retaining SPEC's API image with embedded immutable data. Key custody, finite live
authority limits, ledger/IAM changes and production enablement remain separate
review gates. No operational key, secret or production permission was created.

`release_authority.py` returns `signature_and_scope_only`, not an activation
authorization token. It verifies real domain-separated OpenSSH Ed25519 signatures
over bounded canonical bytes for owner baseline, owner delegation and routine
manifest, then checks the pinned scope. Actual artifact contents, runnable/repo
identity, signed Git tag, attestations, authority freshness/revocation and receipt
chain are not established by this partial model and cannot be skipped downstream.

Before-change TigerStyle review required independent root/delegate authority (#6),
paired byte/signature/scope assertions (#4), bounded input/verification time (#2)
and fixed redacted subprocess diagnostics (#12). After-change review added an
explicit same-key refusal and kept the result separate from full authorization.
The valid data case changes the API digest but preserves every non-data runtime
pin, consistent with embedded-data packaging; digests alone do not prove contents.

Nineteen integration cases use actual disposable Ed25519 keys and signatures,
not mocked cryptographic verdicts. Wrong owner/delegate keys, namespaces, baseline
or delegation binding, runtime changes, disabled source profile, data tag, validity
interval, generation/policy/sequence, predecessor, duplicate JSON and noncanonical
bytes are refused. Model tests check finite interval, Boolean-generation refusal,
payload byte bound and mandatory delegated bindings. An initial duplicate-JSON
case performed a no-op replacement against compact JSON; its unexpected acceptance
exposed the fixture defect. The fixture now asserts that its target exists before
inserting the duplicate. The same-key case signs with that very key so an unrelated
wrong-key check cannot supply the refusal.

Four isolated in-memory mutations were killed by the intended refusal assertions:
cryptographic exit result, separate owner/delegate key, disabled source-profile
scope and unchanged runtime baseline. No production source file was mutated.
All fixture keys are disposable synthetic test credentials and are removed with
their unique scratch directory; none is a provisioned operator/delegate key.

The isolated installed-wheel smoke verified a real signed fixture and refused
the wrong delegate key, confirming the installed public function rather than an
editable source import. It produced only a scope-check result. The wheel, venv,
test keys and signature scratch were removed by the task wrapper.

Final local checks passed: 1,690 tests, 23 opt-in skips, plus every pre-commit
check (Ruff, mypy, BasedPyright, Bandit, browser fixtures and infrastructure/static
guards). Receipt: `test-output/archive-freshness-y71ymgd2/local-authority-scope.xml`.
The first full check caught a mypy-only heterogeneous test-dictionary inference;
an explicit dictionary type fixed it without changing production behavior.
Hosted CI and validation-container/browser runs passed for the preceding reader
and design revision `319769a648f967e6fb6f29cea998d16b310d440a` (runs
`37341267904` and `37341267949`). That is not hosted evidence for later verifier
code until its own pushed revision passes.

## Remote Release-Safeguard Preflight (2026-10-05)

Read-only authenticated GitHub API checks during continuation found a concrete
protected-processing prerequisite, not an implementation checkpoint:

- `gh auth status` confirms the active repository owner account is authenticated.
- `gh repo view --json nameWithOwner,isPrivate,defaultBranchRef` confirms
  `krzysztofbury/minos-health` remains private and its default branch is `main`.
- Both `GET /repos/krzysztofbury/minos-health/branches/main/protection` and
  `GET /repos/krzysztofbury/minos-health/rulesets` return HTTP 403 with GitHub's
  message: `Upgrade to GitHub Pro or make this repository public to enable this feature.`
  Authentication succeeds elsewhere; these responses identify a feature/plan
  restriction, not an absent local credential. No branch-protection or ruleset
  configuration could be verified through these endpoints.
- `GET /repos/krzysztofbury/minos-health/immutable-releases` returns
  `{"enabled":false,"enforced_by_owner":false}`. This is a fresh remote check,
  separate from the earlier #41 settings observation.
- `gh variable list` returns no repository variables. Workflow/archive settings
  have not been provisioned by this preflight.
- PR #140 is still open/draft at
  `17f3bdf67520846cf1369549dd44459efda93f26` at the time of these checks.

The archive-reader trust policy in `infra/terraform/watcher/iam.tf` matches an
OIDC subject for the configured ref. A matching `refs/heads/main` subject proves
the requesting ref, not enforced branch review, required checks or protection
against direct writes. Do not label that check protected-branch evidence or
grant privileged automatic processing on its strength alone.

Owner action is required to make private-repository protection available and
review its actual rules, or to approve a separately designed equivalent trust
boundary. Changing the account plan, publishing the repository, changing release
settings or relaxing protection is not authorized by this preflight. Source bytes,
credentials and personal operational data must remain private. The already
accepted automatic routine-update policy is unchanged; this is a release-safeguard
blocker, not a request to approve each future data update.

No GitHub setting, IAM policy, operational key, host service, notification or
production release was changed. Implementation remains on the same freshness
branch; no partial merge or freshness deployment is justified by these checks.

The owner subsequently chose to design alternative safeguards while retaining
the private repository/current plan. The
[pinned-workflow authority proposal](../v0.0.1/pinned-workflow-authority-proposal.md)
documents that alternative and its limits; implementation/enablement is not approved
by the design request. An additional read-only GitHub OIDC customization check
returns `use_default: true`, `use_immutable_subject: true`, and prefix
`repo:krzysztofbury@5102389/minos-health@1325001960`. The repository API independently
confirms owner ID `5102389` and repository ID `1325001960`. The checked-in IAM
template uses the older name-only subject; no live AWS trust comparison or hosted
assumption is claimed. GitHub's current documentation also limits private artifact
attestations to Enterprise Cloud; available local CLI verification flags do not
establish private issuance entitlement. No settings or credentials were changed.

### Live Archive-Reader Trust Comparison (2026-10-05)

After the owner's instruction to proceed with the revised offline direction,
`aws sts get-caller-identity --profile minos-health-watcher-operator` succeeded
for the existing Terraform operator role in the built-in account. A read-only
`iam get-role` on `minos-health-watcher-github-archive-reader` returned the exact
name-only subject `repo:krzysztofbury/minos-health:ref:refs/heads/main`, audience
`sts.amazonaws.com`, and the existing GitHub reader permissions boundary.
GitHub's OIDC customization GET still returns the immutable-ID prefix above.

The mismatch is now established against live AWS, not inferred from Terraform.
Do not repair it by accepting both prefixes or by changing it only to immutable
`main`: that still lacks exact pinned-workflow authorization. The final policy
must be derived from the actual nonprivileged reusable-job claim rehearsal and
reviewed with the repository-wide customization plan. No AWS/GitHub setting,
key, role grant or production deployment was changed by these reads.
