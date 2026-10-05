# Routine Data Signing Authority: Proposal

## Status And Approval Boundary

Status: authority model accepted for offline verification work, 2026-10-05.
The owner accepted the delegated trust model for offline manifest/certificate
verification and refusal tests, and retained SPEC's canonical embedded dataset.
The owner selected an isolated Mikr.us signer for the next custody design, with
the same-host compromise exposure explicitly presented. That selection is not
approval to provision a key, install a service or grant any cloud/host access.
Custody, finite authority limits, ledger/IAM changes and production enablement
still require separate review. Only disposable synthetic test keys may be generated;
no operational key, secret, IAM grant, release setting or production change is
authorized. #41's live release contract remains unchanged until its full boundary
is implemented/reviewed. This is not an enabled release policy.

## Existing Contract And Conflict

- #21 requires automatic routine data activation without per-update approval.
- #41 and [deployment](deployment.md#promotion-evidence) require a complete
  independently signed release manifest, signed `v*` tag, commit-bound build
  provenance and signed off-host deployment receipt chain. The operator's
  private Ed25519 key must never enter GitHub Actions or Mikr.us.
- An offline operator key cannot sign each newly computed manifest unattended.
  Putting that key online or treating build provenance as an operator signature
  would silently weaken the accepted contract.

Proposed resolution: retain independent operator signatures for full runtime,
policy and authority changes. For a routine data release, verify an operator-signed
delegation certificate plus a distinct delegated signature over the complete
manifest. This changes the authorization model explicitly; it is not already
equivalent to #41's per-release independent operator signature.

Current implementation is narrower than this design. `scripts/deploy_validation.py`
verifies reviewed remote `main`, a successful manual publication run, API/edge
identities and a bundle, then uses operator SSH for staged host checks/receipts.
It does not verify the complete signed authority proposed here. The validation
publication workflow uses `--provenance=false`; image revision labels and Actions
artifact identities are not substitutes for the required attestations.
The existing recovery-inventory signer in
`src/minos/ingest/watchers/recovery/inventory_signing.py` has its own namespace;
it must not acquire release authority through reuse of its key or signature.

## Trust Roots And Separation

| Boundary | Authority | Explicit exclusions |
| --- | --- | --- |
| Offline owner | Sign full baseline, policy/delegation, revocation and exceptional recovery authorization | No owner private key in a job, host or image |
| Archive processor | Read pinned archived bytes; generate a bounded proposal/evidence | No source-bucket writes, signing key, host access or Telegram credentials |
| Protected build/gate jobs | Build and test the candidate once; attest exact outputs under a reviewed workflow | No delegated key, production/notification secret or PR-supplied command |
| Trusted routine signer | Independently validate evidence and sign within its delegation | No owner/recovery key, policy edits, arbitrary manifest, refs or command execution |
| Trusted activator | Independently verify certificate, candidate, provenance and chain; activate or roll back | No source interpretation shortcuts or reliance on a signer's Boolean verdict |
| Trusted notifier | Consume durable typed incidents and delivery intent | No authority to change activation result or consume failed changes |

The existing GitHub archive reader remains read-only. Any receipt, authority or
processing-ledger writer needs a separately reviewed role and exact resource
scope, not an expansion of this archive role or the watcher credential.
Untrusted PR jobs receive none of these privileged identities.

## Signed Delegation Scope

Use a new, versioned certificate with strict Pydantic input validation and canonical
UTF-8 JSON bytes. Reject duplicate JSON keys, unknown fields/versions, noncanonical
representations and excessive bytes/counts before cryptographic verification.
Domain-separate its OpenSSH signature as `minos-data-delegation-v1`; do not reuse
`minos-release`, `minos-deployment`, `minos-recovery` or inventory signatures.
Initial byte/count/time limits require implementation review and refusal tests.

The offline owner signs these explicit constraints:

- Certificate ID, authority generation, prior-authority digest, owner-key identity,
  delegated public-key fingerprint and allowed signing purpose.
- Fixed repository, protected ref and exact build/gate workflow identities and
  revisions. Workflow identity alone is insufficient if its code can drift.
- Independently signed full baseline-manifest digest and component provenance.
  Schema, parser/transform, normalization, dependencies, runtime files and policy
  are pinned by content, not an unchecked semantic version or branch name.
- Exact supported source/transform profiles and their licensing/applicability
  policy digests. Default deny: an unlisted source, layout or governing act is
  not routine merely because a parser accepts it.
- Exact static, edge, watcher and cloudflared artifacts and compatibility contract.
  Their digests cannot change under data authority. Any changed component needs
  a new owner-approved full baseline and delegation.
- Allowed API data packaging: this project currently uses code-only validation
  images and a separately staged bundle, while SPEC's canonical API image embeds
  the database. The owner retained the canonical embedded dataset for this work.
  If data changes the API digest, verified provenance must bind the unchanged
  approved non-data filesystem/runtime inputs and exact new data bytes. A
  self-reported `code_sha256` or free-form image label cannot establish that.
- Exact compatible CLI artifact digests and the narrowly permitted new data-bundle
  identity. No CLI rebuild or binary change is authorized by data delegation.
- Allowed release-tag/key grammars, maximum delegated activation sequence and
  bounded validity/refresh period. Real policy thresholds are reviewed separately;
  this draft does not invent count, relevance, latency or expiry values.
- Predecessor/anti-replay rule: each routine manifest names the unique verified
  last-successful complete release and signed receipt head. Delegation cannot
  rebase over a different or newer runtime baseline.
- Revocation generation and a signed rollback rule identifying admissible prior
  complete manifests. No arbitrary downgrade is an implicit recovery capability.

Changing the policy, delegation constraints, verifier, workflow code, trust roots
or packaging boundary requires owner review and a new certificate. Data-only
manifest changes cannot edit these files, including through rename, symlink,
submodule, generated executable or path traversal.

## Candidate And Signature Envelope

Keep the existing dataset/artifact manifest and S3 transport selection separate.
The candidate audit envelope binds their canonical digests to the exact tested
database, schema, review summary and complete artifact set. It additionally binds:

1. Verified recovery key/version/digest/revision/migration identity, exact source,
   change/run/observation IDs and normalized source-specific before/after product
   identities. A catalog observation or previous aggregate-state digest is not
   automatically a product pin.
2. Exact complete archive object selection, expected owner and verified versions,
   native checksums and byte counts, with no latest-object fallback.
3. Source-specific applicability, pairing, correspondence and licence evidence;
   complete candidate-audit results, not #20's selected-scope diff alone.
4. Delegation/policy/baseline identity, exact data-only commit/tree delta, protected
   workflow run/attempt and digest-bound build/gate attestations.
5. The immutable full manifest, exact tested artifacts, release tag/commit and
   expected predecessor/receipt head. Promote these bytes without rebuilding.

The delegated key signs under the existing `minos-release` namespace only when
the envelope identifies delegated authority explicitly. Verification dispatches
by strict envelope schema and certificate purpose, not any key in a shared
allowed-signers file. Owner-signed and delegated manifests remain distinguishable.
If signed data tags are permitted, the delegated Git SSH signature is valid only
for the certificate's data-tag grammar and the independently verified data-only
protected commit; it conveys no authority over code-release tags.

Keep the #41 conditional immutable envelope key `release-manifests/<tag>.json`
and its off-host recovery authority. A `412` is a retry only after exact byte
comparison; conflicting bytes stop. GitHub Release asset identity and immutable
release settings must be verified remotely. No rebuild at tag promotion and no
assumption that repository immutable releases are already enabled.

## Independent Activation Verification

The activator must enforce the following even if the signer is compromised:

1. Validate the offline-owner signature and the certificate's currently authorized
   generation, purpose, baseline, validity and revocation status.
2. Verify the delegated signature over the exact canonical complete manifest;
   reject owner/delegate namespace or purpose substitution.
3. Resolve remote protected commit/tag identities and verify digest-bound build
   and gate attestations against the pinned repository/workflow/implementation.
   Never accept an arbitrary fork, PR run, caller-supplied issuer or moving ref.
4. Verify each runnable manifest, architecture, component provenance, content
   constraints and compatibility. Reject mutable tags, OCI indexes, attestation
   objects, missing components and unapproved runtime changes.
5. Verify the current authority generation and unique signed receipt head before
   reserving activation. Compare the expected predecessor and fence stale workers.
6. Stage and verify the exact tested bundle/artifacts, then exercise data/source/date
   lookups and the unchanged compatible UI/API. A signature does not replace
   complete source, correctness or representative runtime gates.
7. Recheck authority validity and fencing immediately before activation and before
   publishing success. Expiry/revocation while processing never becomes a reason
   to advance an unverified success checkpoint.

The host command must be a separately deployed, reviewed verifier/activator. Do
not send arbitrary Python/shell source from a CI job to the host as the new
unattended interface. Restrict requests to validated manifest/receipt identities;
keep Docker authority and the signature key in distinct least-privilege processes.
The current operator SSH validation script remains manual and unchanged.

## Checkpoints, Receipts And Rollback

Processing, source capture, signing, activation and notification delivery have
independent durable checkpoints. Neither a signed candidate nor successful
watcher capture consumes a failed activation. Reserve/fence the exact candidate
and expected receipt head before any effect; serialize per target, with durable
idempotency for crashes and concurrent workers. GitHub concurrency alone is not
the durable lock, and expiring Actions artifacts are not the checkpoint store.

Keep #41's signed append-only `deployment-receipts/<20-digit-sequence>.json`
chain, full prior/new manifest identities, verification evidence and off-host
conditional writes. Model reservation and failure records separately from the
last-successful head, so a failed candidate or rollback does not masquerade as a
successful deployment. The durable ledger location and writer role are a further
review boundary; this signing design does not implicitly choose or provision one.

Success requires verified activation and a durable valid receipt. If receipt
publication fails after runtime activation, preserve a recoverable in-doubt record,
block newer activation and retry exact-byte publication; do not guess success or
reactivate blindly. Recovery must compare actual host state with the verified
intent/chain before deciding to finalize or restore the prior complete set.

An activation failure restores the complete authorized predecessor, not separate
container tags. A root-signed rollback grant must permit completion of an already
started rollback after delegation expiry/revocation, narrowly to the pinned
predecessor and captured attempt. It must not permit new forward activation under
an expired certificate. Define the no-valid-predecessor recovery path explicitly;
default to stopped automation and owner investigation, not a guessed fallback.
The rollback exception must prove the reservation preceded expiry/revocation from
trusted durable evidence. A delegate-supplied timestamp or newly signed "old"
attempt is not that proof; a revoked key cannot mint fresh rollback authority.
Historical signature validity is a separate check from present forward authority.
Expired delegation does not erase the receipt chain; a prior manifest is usable
only when its anchored historical validity and current owner-authorized rollback
scope pass. Revocation must identify the last trusted receipt/cutoff for recovery,
not let the revoked signer choose its own supposedly safe historical head.

Persist a privacy-safe incident and delivery intent before sending Telegram.
Use the existing destination only through a reviewed notifier; distinct failed
candidates alert while the monitor is down, with bounded retries and deduplication.
Delivery outcome never determines signing or activation success.

## Key Custody, Rotation And Revocation

Selected topology for design: keep the offline owner root as specified in
#41; put a separate, restricted delegated key only in an isolated Mikr.us signing
component outside ordinary Actions build/PR jobs. A dedicated host-side signer avoids
copying any owner key or adding KMS/HSM cloud services outside the accepted AWS
scope. It still needs explicit host-secret/install/IAM and isolation review.
The signer must not run the API, watcher, untrusted parsers, builds or submitted
commands. Its key is not the SSH login, watcher/archive or inventory key.

Minimum custody design constraints, pending implementation/install review:

- A distinct pinned signer image in the accepted Compose topology, unique numeric
  UID/GID established by preflight, read-only root, dropped capabilities,
  no-new-privileges, bounded resources and a small private writable temporary area.
  No Docker/SSH-agent socket, host source checkout, application database, watcher
  control volume, user home or shared Docker credential store is mounted.
- Mount only its dedicated delegated key and root-owned verification/authority
  configuration, read-only, restricted to the required UID/GID. No owner key or
  recovery/inventory key is accessible. Backup/recovery custody stays external.
- Expose a private bounded request channel only to the trusted coordinator. Verify
  peer/service identity and an exact typed candidate/authority identity; never
  accept a script, shell command, path, arbitrary ref, archive endpoint or a
  caller's pass/fail claim. Do not add an anonymous public signing route.
- Fetch/reverify independent gate/provenance/authority evidence through an explicit
  reviewed access path. A coordinator's summary is not proof, and ordinary build
  jobs cannot replace the signer's verifier/configuration.
- Keep the activator/Docker broker separate. The signer has no runtime replacement
  permission; the activator cannot rewrite authority policy or fetch the owner's
  private key. Neither reuses the watcher credential or notification secret.
- Signature output is bounded immutable evidence, not a deployment command.
  Broker/activator must still verify exact artifact and predecessor/fencing state.
  Signer outage stops new candidates while last-good service remains active.

No accepted host credential currently grants this component fresh authority/chain
reads or off-host receipt writes. In particular the accepted external-host AWS key
exception is scoped to the watcher, not this new component. Choose and review a
separate trusted publication/access path before provisioning: a new narrowly
scoped host principal would be an additional credential-policy exception; a
protected OIDC publisher would require authenticated bounded handoff and durable
outage/reconciliation behavior. Do not silently reuse the watcher key, broaden
the archive reader, use an administrator profile or put the owner key in Actions.
Every IAM plan and any additional host credential needs its own approval.

This trades unattended availability for a bounded online-key compromise risk.
Sharing Mikr.us means a full host compromise can affect signer, activator and
runtime evidence. Process isolation, signed policy and independent build/gate
attestations reduce exposure; they do not make same-host reports independent or
restore offline-operator scrutiny for each data release. The owner must accept
that residual risk or select a separately trusted signer before enablement.

Only the owner root authorizes a new delegated fingerprint, renewal, revocation
or new authority generation. No self-renewal or certificate replacement by the
delegate. Publish owner-signed authority updates immutably off-host, retain their
chain and establish freshness through a reviewed current-generation protocol.
Reject missing, stale, conflicting or unavailable authority evidence. A signature
or cached certificate alone does not prove that it has not been revoked.

An independent recovery verifier must enforce the authority constraints and
attestations too, rather than trust a delegated receipt's claimed pass result.
Record delegation identity and key generation in every release and deployment
receipt. Recovery after compromise must use the owner's separately signed trust
decision; the compromised signer cannot declare its own receipts safe.

## Adversarial Acceptance Cases

These are required test contracts for the implementation, not tests already run:

| Owning boundary | Required refusal or observable result |
| --- | --- |
| Certificate parser/signature | Unknown/duplicate fields, noncanonical bytes, excessive input, wrong owner key/purpose/namespace, altered scope fail before signing or activation |
| Authority freshness | Expired/not-yet-valid, revoked/old generation, conflicting head or unavailable authority stops forward activation; cached old authority cannot override revocation |
| Runtime baseline | Valid delegated signature cannot authorize a code/schema/dependency/policy/workflow/static/watcher/tunnel change or mismatched API non-data contents |
| Data proposal | Wrong recovery/version/change/source/product/before digest, incomplete object selection, native checksum mismatch and ambiguous applicability fail; last-good remains active |
| Significant-change policy | Unsupported layout, new act, unexplained correspondence/count drift, licence change, unresolved mapping and measured runtime regression stop independently |
| Attestation verifier | Another repo/ref/PR/workflow revision, forged issuer, unsigned gate result or evidence for other candidate bytes fails even with a valid delegated signature |
| Tag/artifact promotion | Moved tag/head, unreviewed data-tree delta, wrong architecture, OCI index, mutable tag, missing component or rebuild after testing fails |
| Conditional publication | Duplicate identical envelope succeeds idempotently; same tag/sequence with different bytes stops; receipt gaps/forks are refused |
| Activation fencing | Two workers cannot activate conflicting candidates; stale predecessor/lease token loses authority before any effect; interrupted workers reconcile intent/host/chain |
| Post-activation failure | Known data/date lookup or browser/runtime failure restores the full exact predecessor and does not advance success checkpoint |
| Receipt outage | Runtime activation followed by receipt-write failure leaves a fenced in-doubt attempt; retry publishes exact bytes without reactivation; no newer attempt skips it |
| Mid-flight revocation | Forward activation stops; a narrowly root-authorized rollback can finish only that recorded attempt's pinned predecessor |
| Notification outage | Incident/intent persists before send; distinct candidates alert; retries deduplicate; delivery recovery does not double-activate or change disposition |
| Least privilege | Real denied writes to source archive/state, no PR signing/host credentials, no arbitrary host command, no cross-purpose use of delegate or recovery keys |
| Empty-host recovery | Independent off-host chain/authority/attestation verification selects the complete compatible predecessor without trusting mutable host reports |

Use real Ed25519 signatures for deterministic verification tests, real disposable
databases for ledger/fencing/crash tests, controlled signed artifacts for verifier
tests, and separately approved cloud/host/Telegram exercises for those boundaries.
Unit mocks are not IAM, independent provenance or actual delivery evidence.
Measure signing, full verification and activation under representative data/load
before enabling a source profile. Optimize only with evidence; certificates and
resource limits are not substitutes for correctness or performance acceptance.

## Review Decisions Before Implementation

The private-repository protection preflight returned a GitHub plan restriction.
The owner requested an [alternative pinned-workflow authority design](pinned-workflow-authority-proposal.md)
instead of purchasing protection or making the repository public. That request
authorizes design only: the replacement trust boundary, exact OIDC/IAM cutover,
private provenance protocol and bootstrap sequence still require review. `main`
is not silently relabeled protected, and #41's proof requirements remain intact.

1. Preserve the owner-accepted offline delegated model, with independent offline
   root authority for non-data changes. Acceptance of models/tests is not approval
   to enable delegated release authority in production or waive #41's remaining gates.
2. Implement the owner-selected embedded API/data packaging and independently
   provable unchanged-runtime boundary; do not infer proof from the validation path.
3. Implement/review the selected isolated Mikr.us custody design and its same-host
   compromise limits before installing it. Review signing purposes/tag grammar,
   concrete finite certificate lifetime and the off-host access/publication path.
4. Define fresh revocation/current-authority resolution and monotonic recovery
   anchors, including unavailable authority and clock-skew behavior.
5. Review durable processing/activation ledger location, off-host receipt writers,
   rollback grant and exact least-privilege IAM/host changes separately.
6. Review real source-specific routine/significant-change thresholds and complete
   gate evidence. No source profile enables unattended promotion by default.

Implementation order after acceptance: exact full-manifest/provenance verification,
certificate and delegated verification with refusal tests, durable fenced attempt
and receipt handling, then separately approved key provisioning and synthetic
end-to-end activation/rollback/notification acceptance. This remains part of the
freshness phase; relevance and grouper phases do not start ahead of its review/merge.

## Offline Scope Verifier: Current Limit

`src/minos/ingest/processing/release_authority.py` checks real domain-separated
OpenSSH Ed25519 signatures over a bounded canonical manifest, owner-signed
delegation and owner-signed baseline. It enforces independent root/delegate keys,
baseline/delegation binding, finite interval/sequence, exact caller-pinned policy
and generation, explicit source profile, data-tag prefix, predecessor pointers
and unchanged non-data/runtime profile digests. An empty source profile list denies
all releases. Duplicate JSON keys, noncanonical bytes and unknown fields fail.

Its result is deliberately `signature_and_scope_only`, not activation authorization.
The currently typed digests describe scope; they do not prove actual image
filesystem contents, repository/runnable identity, signed Git tags, workflow/gate
attestations, authority freshness/revocation or the off-host receipt chain.
Caller-pinned generation/predecessor values need those independent proofs before
any signing/activation path can consume the result. The partial manifest is not
the final complete #41 release/recovery envelope. No operational root/key/config
or activation caller exists in this chunk. All fixture authority values and keys
are synthetic and convey no live authority.
