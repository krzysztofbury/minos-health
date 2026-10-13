# CI scope and Actions budget

Issue: #174. First slice: documentation-only checks and obsolete PR cancellation,
merged in #178 at `19082cf` on 2026-10-13. Full hosted PR verification passed.
The documentation-only delivery record is the next hosted measurement; no savings
are claimed until its job receipts are available.

## Baseline

The 2026-10-12 audit measured 192.77 execution minutes across 36 runs, or 212
minutes after rounding each executed job up separately. These are runner-time
estimates, not billing records. The documentation-only #170 path used 23.60
execution minutes across PR CI, PR validation and main CI. The presentation-only
#172 path used 27.39 minutes including publication of all three images.

## Scope contract

Both existing verification jobs always start and keep their existing check names.
There are no event-level path filters and no extra classifier runner. After the
locked dependencies are installed, `scripts/ci_scope.py` inspects the complete
base-to-checked-out-revision diff, not just the last commit:

- PR: event base SHA to the checked-out merge SHA.
- Push: event before SHA to the checked-out push SHA.
- Manual run: always full checks, including publication verification.

Only ordinary, non-executable Markdown files under `docs/`, plus content updates
to `README.md`, `TODO.md` and `CHANGELOG.md`, qualify. Agent instructions do not
qualify. README deletion still exercises packaging. Runtime files renamed into
documentation remain visible as a deletion plus an addition. Unknown paths,
symlinks, mode changes, empty diffs, missing/invalid refs, unavailable Git evidence
and exceeded bounds all select the full path. Diff inspection has a 30-second
limit per Git command, a 1 MiB read limit and a 10,000-path classification limit.

Documentation-only CI runs the locked dependency check, changed-file pre-commit
checks and complete-history secret scanning. It omits unrelated Python/runtime,
Terraform, Ansible, standalone and container builds. The validation job records
the scope decision and completes successfully without container/browser work.
The separate CI check remains responsible for document and secret acceptance.

All other changes retain the full existing pipeline. Publication still verifies
the exact main revision. PR validation cancels obsolete runs; manual publication
uses a separate concurrency group and remains serialized. Read-only verification
permissions and private package publication guards are unchanged.

## Verification and remaining work

The full local repository checks passed, including pytest, Ruff, mypy,
BasedPyright, Bandit, Terraform formatting and Zizmor. There are 40 focused
classifier/workflow checks. The historical #176 documentation-only change also
selected the lightweight path when inspected using its exact Git revisions.

Hosted #178 PR verification of the workflow change retained the full path:

| Run | Boundary | Execution time | Rounded job minutes |
| --- | --- | ---: | ---: |
| [38385878346](https://github.com/krzysztofbury/minos-health/actions/runs/38385878346) | Full CI | 9m18s | 10 |
| [38385878327](https://github.com/krzysztofbury/minos-health/actions/runs/38385878327) | Containers/browser | 3m36s | 4 |

Both succeeded on their first attempt. PR publication remained skipped. The
main-push run and this documentation-only follow-up have separate receipts.

Local executable tests use real Git repositories to cover the whole-change-set
contract, renamed runtime files, deleted README, symlinks/modes, manual dispatch,
invalid/missing baselines, checkout mismatch and Actions output serialization.
Workflow policy tests protect always-reporting checks, full runtime guards,
unconditional history scanning and publication serialization.

Before accepting measured savings, record real hosted job durations for this
workflow change and the next documentation-only delivery record. Do not skip CI
on that documentation update. Count executed job time and per-job rounded minutes,
including the PR and main paths; parallel wall-clock savings are not cost savings.

The owner approved the next image-promotion design: transfer the tested API/edge
images within the same manual-main workflow, retaining read-only verification and
an immutable artifact-ID handoff to the private publisher. Retain this temporary
image archive for one day. A local representative archive measured 106,585,088
bytes; transfer time and hosted/storage costs still need measurement. This approval
does not activate new images on the production host.

Implementation of that image-promotion boundary, more granular web/runtime/
infrastructure classification, reviewed private build caching, representative after-change
measurements and an owner-reviewed monthly budget remain in #174. This first
slice does not claim the entire issue is complete.
