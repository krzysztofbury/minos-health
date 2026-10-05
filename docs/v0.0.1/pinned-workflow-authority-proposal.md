# Pinned Workflow Authority Without Branch Protection

Status: revised direction accepted for offline implementation on 2026-10-05.
After review, the owner instructed implementation of pinned workflow execution,
fixed application base plus a constrained embedded-data layer, and maintained
private Cosign/in-toto signing through a dedicated AWS KMS build-signing key.
The repository stays private. Neither a GitHub plan upgrade nor a public
repository is an inherent requirement for that alternative authorization path.
Live IAM/GitHub changes, operational keys, host grants and deployment still
require exact reviewed plans. This is not activation authorization.

## Decisive Change

Treat `main` as an untrusted trigger and candidate-data location. Authorization
comes from an offline-owner-approved workflow implementation, runtime baseline
and source policy, independently enforced outside the mutable repository head.
An account with repository write access may request work, but cannot authorize a
new workflow, parser, dependency, policy or runtime merely by pushing to `main`.

This can replace branch protection's role in privileged release authorization.
It cannot prevent direct repository writes or protect scheduling availability:
a writer can remove the dispatcher, exhaust Actions capacity or delete artifacts.
The last-good runtime remains active; such interference stops updates. Do not
describe this as equivalent repository protection or guaranteed availability.

## Observed Inputs

- GitHub refuses both private-repository protection APIs with HTTP 403 and an
  explicit plan requirement. See [remote evidence](../validation/archive-freshness.md#remote-release-safeguard-preflight-2026-10-05).
- The OIDC customization GET succeeds and returns `use_default: true`,
  `use_immutable_subject: true` and this exact prefix:
  `repo:krzysztofbury@5102389/minos-health@1325001960`.
- Repository and owner IDs independently match the repository API. Numeric
  identity matters for rename, transfer and repository-recreation refusal.
- `infra/terraform/watcher/iam.tf` currently constructs a name-only subject.
  A subsequent authenticated read of the live archive-reader role confirms the
  same name-only `repo:krzysztofbury/minos-health:ref:refs/heads/main` subject and
  the existing reader permissions boundary. It does not match the observed
  immutable-ID prefix. No hosted assumption or IAM change is claimed.
- Immutable releases are currently disabled. This alternative does not waive
  #41's immutable-release, signed-tag or off-host recovery requirements.

## 1. Cloud Access Only From Immutable Reusable Jobs

The nonprivileged inspector `scripts/observe_reusable_oidc.py` now accepts only
same-repository PR contexts targeting `main`, requests audience
`minos-health-nonprivileged-rehearsal` (never `sts.amazonaws.com`), and returns
allowlisted bounded claims as `unverified_oidc_claim_observation`. It does not
verify a JWT signature, authorize a workflow, retain tokens or call AWS. A
name-only subject is reported as an immutable-subject mismatch, not accepted as
an IAM fallback. The reusable workflow now fixes the tool checkout to
`6415abb0050ac0f0882958badfbd798cb62617cb`, accepts no inputs/inherited secrets,
restores no shared cache, and executes no caller checkout. Its exact workflow
SHA will be referenced by a separate PR-only caller after publication. This
branch-only diagnostic publication does not merge a foundation or establish a
default-branch automatic trigger; the reviewed production bootstrap is pending.

Local tests exercise explicit job SHA/reference agreement, immutable IDs,
unsupported contexts before token requests, HTTPS host/redirect restrictions,
response bounds and refusal without token/bearer/extra-claim output. This is
diagnostic preparation, not GitHub service acceptance or privileged processing.

Use a fully qualified reusable-workflow reference ending in an owner-reviewed
40-character commit SHA, not `@main`, a tag, or a relative workflow reference.
Authorize its exact `job_workflow_ref` through GitHub's customized OIDC subject.
GitHub documents the template `repo`, `context`, `job_workflow_ref`; AWS can then
match the resulting standard `sub` plus the `sts.amazonaws.com` audience.

Proposed subject shape, not a value to install:

```text
repo:krzysztofbury@5102389/minos-health@1325001960:ref:refs/heads/main:job_workflow_ref:krzysztofbury/minos-health/.github/workflows/<reviewed-workflow>.yml@<reviewed-40-character-SHA>
```

Require exact equality, never a wildcard workflow/ref or acceptance of both old
and new subject formats for convenience. Observe the actual token's allowlisted
claims in a nonprivileged hosted rehearsal before deriving the final trust policy.
Do not log JWTs, request bearer tokens or AWS credentials.

The pinned job must reject PR, `pull_request_target`, fork, tag and unapproved
event contexts before requesting cloud access. The default-branch constraint is
an additional scope restriction, not a claim of branch protection. Do not attach
an environment that silently changes the subject's context.

Keep the archive role read-only on its existing source/recovery prefixes. A
publisher/receipt role must be separate and exact-workflow-bound; the custom OIDC
template changes repository-wide token issuance, so inventory every OIDC consumer
before the cutover. Review saved IAM plans and the GitHub settings change together.
Never leave the old main-only trust as a fallback after enabling privileged jobs.

## 2. No Caller-Controlled Executable Inputs

The reusable workflow pins a separately reviewed implementation commit, container
and dependencies. Workflow revision and tool revision are separate identities:
embedding a workflow's own future Git SHA into itself is not a bootstrap method.

Do not execute code from `github.sha`, the caller's checkout, a submitted ref or
a candidate data commit. Do not restore caller-writable caches, download caller
artifacts into an executable directory, interpolate inputs into shell commands,
inherit secrets, accept container/build arguments or use a caller's pass verdict.
Pin every action and nested workflow. Use fresh GitHub-hosted job boundaries.

Normal processing selects exact verified recovery/change identities internally
through the reviewed archive reader. A retry may identify an existing durable
candidate; it cannot submit a command, path, endpoint or alternative policy.
Reverify selections and full coverage before parsing. Retain only redacted
identities/reports; private raw sources and recovery databases are not job outputs.

## 3. Owner-Signed Code And Data-Tree Boundary

The offline owner authorizes the exact workflow/tool identities, complete runtime
baseline, data-file inventory, transforms and source/significance policy. Keep
that authority and verification key outside a caller-controlled `main` checkout.
The delegate cannot renew it or choose a replacement implementation.

An independent verifier compares the complete candidate tree with that authorized
baseline, using bounded exact Git objects, paths, modes and content identities.
Only explicitly scoped regular data-manifest changes are admissible. Reject
renames, deletions outside scope, executable modes, symlinks, submodules, workflow,
parser, dependency, schema, static/runtime changes and unlisted files. Do not
infer the full change set from a PR label, patch preview or limited diff output.
Reconcile the data files against the verified candidate audit and archived bytes.

For embedded-data API packaging, reuse the exact approved runnable application
manifest. Append one fixed-format uncompressed OCI data layer containing only
`app/data/manifest.json`, `app/data/minos.db` and `app/data/review.txt`. They are
regular nonexecutable files with mode `0444`, UID/GID `10004:10004`, and fixed
timestamps. Reject extra entries, whiteouts, links, traversal, padding payloads
and all runtime/configuration changes. Preserve original compressed application
layer descriptors and uncompressed diff IDs, not labels alone. No routine code
rebuild or general-purpose filesystem-equivalence engine is needed.

The reviewed application-base admission must prove that `/app` and `/app/data`
are real directories, the data directory is empty, runtime settings use that
fixed bundle location, and no ancestor/link/volume can redirect or hide the
overlay. Bind that admission to the approved runnable digest. Local byte
verification alone does not prove these inherited filesystem properties.

`src/minos/ingest/processing/data_image.py` now packages and independently verifies
the exact delta from bounded actual OCI metadata and a verified artifact bundle.
Its result is `data_image_content_only`, not provenance or permission to deploy.
The delta is not a standalone OCI layout: publication must verify/preserve all
inherited registry blobs. Test the resulting exact runnable manifest and promote
without rebuilding. Retain code-base revision and candidate-data revision as
distinct provenance inputs; re-select the data commit/tag remotely before signing
or activation. Stale or moved heads fail. Signed release verification is unchanged.

## 4. Independent Signer And Activation Enforcement

Retain the selected isolated Mikr.us signer. Its reviewed image and root-owned
trust configuration are installed through the operator-controlled host baseline,
not updated by Actions. The offline owner key never enters the host or GitHub.
The signer and activator independently enforce authorized implementation, scope,
fresh authority/revocation, complete provenance, candidate gates, exact artifacts
and unique predecessor/receipt state. Neither trusts a caller's Boolean success.

The signer has no Docker socket, builds, parser execution or arbitrary-command
interface. The activator receives only typed verified identities and cannot edit
the signer policy. An unauthorized repository change can at most stop processing,
not install its code or expand data authority. Same-host full compromise remains
the previously identified residual risk, not an independence claim for host reports.

Keep durable processing and activation checkpoints independent of capture. Fence
attempts before effects; a post-activation receipt outage blocks newer attempts
until exact reconciliation. Retain complete rollback authority and detailed durable
Telegram incidents. These controls are unchanged, not delivered by workflow pins.

## 5. Selected Private Provenance Mechanism

GitHub documents that private/internal artifact attestations require Enterprise
Cloud; Free, Pro and Team support them only for public repositories. Do not claim
that upgrading to Pro solves #41 or use the public Sigstore transparency service
to disclose private repository/source identities as an unreviewed workaround.
No private attestation generation or entitlement test has been performed here.

Pinned OIDC access authenticates the requesting workflow, but does not by itself
bind a database/image digest, build inputs or test results. A stored JWT, Docker
BuildKit provenance document, run-success API response, signed self-declared hash
or repository label is not the required independent artifact/gate attestation.

Use maintained Cosign/in-toto tooling with a dedicated asymmetric AWS KMS
build-signing key. This amends the former AWS-only-S3 design direction; no key,
role or grant is provisioned by the amendment. The private key is nonexportable
and only the exact pinned trusted signing job may request signatures through its
dedicated narrow OIDC role. It is separate from both the archive-reader role and
the Mikr.us delegated release signer. The latter cannot issue its own independent
build evidence. KMS controls key custody, not predicate truth.

The signing job must derive complete build/gate statements from its own verified
inputs/outputs, binding application-base and data revisions, workflow/tool/lock
identities, exact archive selections, subjects, test results and candidate identity.
It cannot sign arbitrary caller digests or a caller's pass verdict. Untrusted
parsers/build subprocesses have neither KMS credentials nor access to GitHub's
OIDC bearer/request environment. A job step alone is not an isolation boundary;
the reviewed design must specify the sandbox and the independent gate checks.
No executable code, restored cache or results authority comes from mutable `main`.

Store and verify attestations privately, with an exact pinned public key and
reviewed statement schema. Explicitly configure and test that Cosign publishes
neither identities nor predicates to public transparency services. Pin a concrete
Cosign version and its compatible KMS key/signature algorithm in the provisioning
plan; it need not match the Ed25519 owner/delegate algorithm. Offline owner
authorization binds the build public key and exact workflow identity. A predicate's
self-declared workflow name is not identity verification.

Fresh authority/revocation, trusted witness time, historical public-key retention
and a privately retained off-host receipt chain remain independent enablement
gates. A private signed statement without a public transparency log cannot claim
publicly witnessed issuance time. Key rotation, compromise, missing evidence and
storage outages must fail closed. Park any bespoke JWT/attestation protocol; do
not replace these checks with stored JWTs or unauthenticated workflow API results.

For supported GitHub attestations, the installed CLI exposes `--signer-workflow`,
`--signer-digest`, `--source-digest`, `--source-ref` and
`--deny-self-hosted-runners`. Exact verified certificate identity and signer digest
must pass; caller-controllable predicate fields alone do not establish identity.
These available flags are not evidence that this private repository can issue them.

## Acceptance And Cutover

1. Locally test the complete data-tree/runtime boundary with real Git objects and
   actual signatures, including direct malicious main changes and reused digests.
2. Host a nonprivileged pinned reusable-workflow rehearsal. Observe immutable
   subject and job SHA, deny unsupported contexts and verify input/cache isolation.
   Synthetic OIDC strings alone are not GitHub service acceptance.
3. Review exact authority-policy, GitHub customization and IAM plans. Prove real
   allowed pinned reads and denied arbitrary workflow, relative workflow, other SHA,
   PR/fork/tag, source writes and unrelated reads. No live change is approved here.
4. Establish KMS-backed private build/gate provenance and historical verification.
   Keep it a hard gate if service access or a reviewed equivalent is unavailable.
5. Complete #41's artifact/tag/authority/receipt chain and the separate custody,
   off-host access, ledger, limits and significance-policy reviews. No new host
   AWS key or receipt writer is implicitly authorized by this design.
6. Preserve the existing phase branch/PR. Do not merge the foundation merely to
   create a main trigger. Pinned reusable-workflow publication and subsequent
   default-branch automatic-trigger acceptance need an explicit reviewed bootstrap
   sequence; changing the one-phase/one-PR delivery order is not implicit. Keep the
   production source profiles deny-all while publishing/rehearsing the pinned
   implementation. Merging offline implementation is not freshness delivery or
   permission to enable activation. A one-PR bootstrap exception is still pending.
7. Only after actual automatic activation, forced-failure rollback, distinct
   Telegram delivery/outage and representative resource evidence may a source
   profile be enabled. Routine candidates then require no per-update owner approval.

## Review Findings And Tradeoffs

Before-design review: TigerStyle #4 requires immutable identity at both IAM and
activation; #6 requires default-deny scope; #12 requires explicit outage and
bootstrap handling. After-design review identified immutable-ID subject drift,
private-attestation availability, caller-controlled checkout/cache execution and
the distinction between release integrity and repository availability. All are
explicit gates above, not permission to relax the release contract.

This keeps the repository private without requiring a branch-protection plan.
Immutable base layering avoids a general-purpose image-equivalence system, and
maintained signing tooling avoids bespoke cryptography. Manual approval remains
necessary for new code baselines, not passing routine data updates. AWS lists
USD 1/month per KMS key plus requests; exact region/cost and policy still need review.
There is no representative performance claim or new operational credential.

## Sources Checked On 2026-10-05

- [GitHub OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
- [OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/security-for-github-actions/security-hardening-your-deployments/using-openid-connect-with-reusable-workflows)
- [Reusable workflow SHA references](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Repository OIDC REST API](https://docs.github.com/en/rest/actions/oidc)
- [Private artifact-attestation availability](https://docs.github.com/en/code-security/getting-started/github-security-features#artifact-attestations)
- [Immutable release guarantees](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- [Cosign AWS KMS key management](https://docs.sigstore.dev/cosign/key_management/overview/#aws)
- [AWS KMS pricing](https://aws.amazon.com/kms/pricing/)
- Local `gh attestation verify --help`, GitHub CLI 2.102.0: reusable workflow signer
  identity, exact digest flags and certificate/predicate trust distinction.
