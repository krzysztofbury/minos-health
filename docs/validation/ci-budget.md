# CI scope and Actions budget

Issue: #174. First slice: documentation-only checks and obsolete PR cancellation,
merged in #178 at `19082cf` on 2026-10-13. Full hosted PR verification passed.
Documentation-only PR #179 passed both existing verification checks in 95 seconds
of combined job time. Main-push acceptance is recorded separately below.

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
to `README.md` and `CHANGELOG.md`, qualify. Agent instructions do not
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

| Run | Boundary | Execution time | Rounded job minutes |
| --- | --- | ---: | ---: |
| [38386993114](https://github.com/krzysztofbury/minos-health/actions/runs/38386993114) | #178 main full CI | 10m09s | 11 |
| [38387162615](https://github.com/krzysztofbury/minos-health/actions/runs/38387162615) | #179 documentation PR CI | 1m16s | 2 |
| [38387162697](https://github.com/krzysztofbury/minos-health/actions/runs/38387162697) | #179 documentation PR validation | 19s | 1 |
| [38388299143](https://github.com/krzysztofbury/minos-health/actions/runs/38388299143) | #179 documentation main CI | 1m06s | 2 |

The docs-only PR used 1.58 execution minutes compared with #170's 13.55 PR minutes,
an observed 88.3% reduction. This is one observed path, not a monthly savings
forecast. Step receipts confirm changed-document checks and complete-history secret
scanning ran, while unrelated runtime/infrastructure/container work was skipped.
Including the successful main push, the full documentation delivery used 2m41s
instead of the 23m36s baseline, an observed 88.6% reduction. Per-job rounding gives
5 versus 25 minutes. Receipts are retained locally under
`test-output/issue-174-ci-2026-10-13/`.

Local executable tests use real Git repositories to cover the whole-change-set
contract, renamed runtime files, deleted README, symlinks/modes, manual dispatch,
invalid/missing baselines, checkout mismatch and Actions output serialization.
Workflow policy tests protect always-reporting checks, full runtime guards,
unconditional history scanning and publication serialization.

Before accepting measured savings, record real hosted job durations for this
workflow change and the next documentation-only delivery record. Do not skip CI
on that documentation update. Count executed job time and per-job rounded minutes,
including the PR and main paths; parallel wall-clock savings are not cost savings.

The owner approved the image-promotion design: transfer the tested API/edge
images within the same manual-main workflow, retaining read-only verification and
an immutable artifact-ID handoff to the private publisher. Retain this temporary
image archive for one day. A local representative archive measured 106,585,088
bytes; transfer time and hosted/storage costs still need measurement. This approval
does not activate new images on the production host.

More granular web/runtime/infrastructure classification, watcher cross-workflow
promotion, reviewed private build caching, representative after-change measurements
and an owner-reviewed monthly budget remain in #174. These slices do not claim
the entire issue is complete.

## Tested validation image transfer

The second slice replaces the validation publisher's rebuild with a same-run
transfer. Hosted publication acceptance passed in #180's main run
`38391304551` at `47a236630d1e893ef80298c2430c501e4ca72b28`.

- Export runs only after both browser/container verification and vulnerability
   scans. Only a manual main publication uploads the image archive, with one-day
   retention and level-1 transport compression. The extracted Docker archive
   remains byte-identical and is verified before loading.
- Verification stays read-only. Its upload's immutable artifact ID is passed
  directly to the publisher. A missing ID or download digest mismatch fails the
  job. There is no artifact-name search, cross-run lookup or PR image artifact.
- `scripts/validation_images.py` binds the archive length/SHA-256 and exact Docker
  image IDs to the source SHA, workflow run and attempt. It refuses malformed,
  oversized, nonregular, corrupt and mismatched input before loading images.
- Loaded IDs, platform and source labels must match the verified identities.
  Publication tags those IDs, pushes them without building, pulls the resulting
  registry digests and verifies the image IDs again. Docker engine IDs may denote
  config or manifest digests depending on the image store; equality is required
  throughout the transfer on the pinned runner platform.
- The existing `validation-release.json` contract remains unchanged. A small
  `validation-build.json` receipt retains the transferred identity evidence for
  90 days alongside it. The large image archive expires after one day.
- A failed or partial publication emits no release receipt. The operator must
  inspect failed attempts; the helper does not retry or activate the host.

An owned local Docker probe used representative API/edge layers, synthetic source
labels, removed the exported images before loading, and pushed/pulled through an
isolated loopback registry. The 106,572,288-byte archive exported in 1.026 seconds,
restored in 0.924 seconds and pushed/pulled in 1.033 seconds. A deliberate byte
mutation was refused before Docker load. All owned probe containers, image
identities and scratch were removed. These local times do not estimate GitHub
network transfer or billing.

### Hosted Acceptance And Compression Follow-up (2026-10-13)

The successful manual-main run transferred immutable artifact ID `11808803527`
to the publisher, bound to run `38391304551`, attempt 1 and source `47a236630d1e893ef80298c2430c501e4ca72b28`.
The existing release consumer accepted the receipt. Independent authenticated
registry manifest reads matched both tested config digests; API and edge packages
remain private. Temporary artifacts expire after one day and receipts after 90
days. No host activation occurred.

| Publication | Verify | Publish | Total execution | Rounded job minutes |
| --- | ---: | ---: | ---: | ---: |
| Prior rebuild, `38250456248` | 214s | 50s | 264s | 5 |
| Tested transfer, `38391304551` | 199s | 66s | 265s | 6 |

These observations establish exact-image promotion, not publication savings.
The hosted Docker archive was 278,501,888 bytes, rather than the smaller local
sample; its uncompressed Actions artifact occupied 278,502,596 bytes. The previous
assumption that Docker save layers were already compressed did not hold here.

A local level-1 ZIP probe on the actual downloaded hosted archive produced
115,452,984 bytes in 2.347 seconds, a 58.5% reduction, and verified the exact
extracted archive SHA-256. The workflow now requests level 1. The first hosted
publication with it, run `38408703227` for release `736ba9f`, spent 45 seconds in
the publish job against 66 seconds for `38391304551`, one rounded minute either way.

## No main-push CI (2026-10-13)

From 1 to 13 October, full CI on pushes to `main` used 542 of about 2,150
runner minutes (each job rounded up to the minute), re-running checks that the
squash-merged PRs had already passed. The owner chose to drop the push trigger.
CI now runs on pull requests and manual dispatch only.

The remaining risk is a PR merged while behind `main`: its combined result with
the newer commits is not checked at merge time. Each release therefore dispatches
full CI on the release commit beside the publication run
(`validation-deploy.md`, Release step 2), so a release never ships an
unverified combination. Merges between releases stay unverified on `main` until
that dispatch.

## Measured per-change budget (2026-10-13)

Rounded job minutes from Actions receipts after #178, #183 and #186:

| Change | Receipts | Verify | Containers and browser | Publish | Rounded minutes |
| --- | --- | ---: | ---: | ---: | ---: |
| Web copy | #185 | 576s | 204s | | 14 |
| Python dependency | #173 | 567s | 214s | | 14 |
| Infrastructure lock | #177 | 545s | 209s | | 14 |
| Workflow pins | #175 | 565s | 209s | | 14 |
| Workflow and test | #186 | 587s | 238s | | 14 |
| Documentation only | #179 | 95s combined | | | 2 |
| Release (dispatch CI and publication) | `38408698178`, `38408703227` | 616s | 270s | 45s | 17 |

Every non-documentation PR currently costs the same 14 rounded minutes; scope
does not yet shorten runtime paths. Before #186, each merge added full CI on
`main` (#178 main run: 10m09s, 11 rounded minutes), so a code change cost about
25 minutes and now costs 14.

Proposed monthly budget, pending owner review: GitHub Pro includes 3,000 minutes.
Keeping 20% headroom allows about 2,400, for example 150 code PRs (2,100) plus
10 releases (170) plus documentation and Dependabot runs. The 2,150 minutes used
from 1 to 13 October came mostly from many small agent PRs; batching related work
into fewer PRs is the main lever. A self-hosted runner is parked as the next one.
