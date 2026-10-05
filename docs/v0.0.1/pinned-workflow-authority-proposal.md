# Pinned Workflow Authority Without Branch Protection

Status: design requested by the owner on 2026-10-05, pending security review.
The owner chose to keep the repository private and design alternative safeguards,
not to buy a plan, publish the repository or waive the protected-processing gate.
This proposal is not approved policy, an IAM plan or activation authorization.

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
  That declared template is not proof of the live AWS trust policy or a successful
  hosted assumption. No live IAM read or change is claimed by these GitHub checks.
- Immutable releases are currently disabled. This alternative does not waive
  #41's immutable-release, signed-tag or off-host recovery requirements.

## 1. Cloud Access Only From Immutable Reusable Jobs

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

For embedded-data API packaging, prove actual non-data image contents/configuration
against the authorized baseline, not just a Git tree or revision label. Build once,
test those exact bytes and promote them without rebuilding. Re-select the exact
data commit/tag remotely before signing or activation; stale or moved heads fail.

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

## 5. Private Provenance Is A Separate Proof Gate

GitHub documents that private/internal artifact attestations require Enterprise
Cloud; Free, Pro and Team support them only for public repositories. Do not claim
that upgrading to Pro solves #41 or use the public Sigstore transparency service
to disclose private repository/source identities as an unreviewed workaround.
No private attestation generation or entitlement test has been performed here.

Pinned OIDC access authenticates the requesting workflow, but does not by itself
bind a database/image digest, build inputs or test results. A stored JWT, Docker
BuildKit provenance document, run-success API response, signed self-declared hash
or repository label is not the required independent artifact/gate attestation.

Before live enablement, choose and review either supported private attestation
service access or a private independently verifiable evidence protocol. The latter
must bind complete canonical build/gate statements to the immutable GitHub-issued
job identity and exact subjects, with cryptographic issuer verification, trusted
freshness/witness time, historical key validation and private durable retention.
It must resist an arbitrary job presenting a valid token plus fabricated digest
statements. Do not invent an ad-hoc JWT audience/signature protocol in implementation
or let the delegated release key attest its own supposedly independent build proof.

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
4. Establish private build/gate provenance and independent historical verification.
   Keep it a hard gate if service access or a reviewed equivalent is unavailable.
5. Complete #41's artifact/tag/authority/receipt chain and the separate custody,
   off-host access, ledger, limits and significance-policy reviews. No new host
   AWS key or receipt writer is implicitly authorized by this design.
6. Preserve the existing phase branch/PR. Do not merge the foundation merely to
   create a main trigger. Pinned reusable-workflow publication and subsequent
   default-branch automatic-trigger acceptance need an explicit reviewed bootstrap
   sequence; changing the one-phase/one-PR delivery order is not implicit.
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

This keeps the repository private and avoids purchasing a branch-protection plan,
but adds independent verifier/provenance work and manual approval for new code
baselines. It preserves unattended routine data updates within approved scope.
There is no measured performance claim or new operational credential in this design.

## Sources Checked On 2026-10-05

- [GitHub OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
- [OIDC with reusable workflows](https://docs.github.com/en/actions/how-tos/security-for-github-actions/security-hardening-your-deployments/using-openid-connect-with-reusable-workflows)
- [Reusable workflow SHA references](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Repository OIDC REST API](https://docs.github.com/en/rest/actions/oidc)
- [Private artifact-attestation availability](https://docs.github.com/en/code-security/getting-started/github-security-features#artifact-attestations)
- [Immutable release guarantees](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases)
- Local `gh attestation verify --help`, GitHub CLI 2.102.0: reusable workflow signer
  identity, exact digest flags and certificate/predicate trust distinction.
