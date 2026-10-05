# GitHub OIDC Subject Rehearsal: Approval Request

Status: proposed, not applied. This requests one repository setting change for
nonprivileged claim observation only. It authorizes no AWS IAM change, KMS key,
host access, production activation or exception to source/significance policy.

## Verified Starting Point

Authenticated GitHub reads on 2026-10-05 confirm a private repository, ID
`1325001960`, owned by ID `5102389`. Remote `main` is
`1d1e8b4d0abc5da0c3416756225435adfa266f34`. Its complete Git tree contains four
workflows and no OIDC consumer permissions. On this phase branch the only two
`id-token: write` declarations are the nonprivileged rehearsal caller and its
reusable job. Ordinary CI, image publication and the production watcher do not
request OIDC tokens. Recheck this inventory and the settings before any write.

The current repository GET is:

```json
{
  "use_default": true,
  "use_immutable_subject": true,
  "sub_claim_prefix": "repo:krzysztofbury@5102389/minos-health@1325001960"
}
```

Hosted run [37369389042](https://github.com/krzysztofbury/minos-health/actions/runs/37369389042)
passes at caller head `35834365ecfe9a00f011cc6637f6a9648abcce22`. It observed:

```text
aud = minos-health-nonprivileged-rehearsal
sub = repo:krzysztofbury@5102389/minos-health@1325001960:pull_request
job_workflow_ref = krzysztofbury/minos-health/.github/workflows/freshness-oidc-reusable.yml@ccffdf593ed4eab8da0a4730bf6d301fd86b5a89
job_workflow_sha = ccffdf593ed4eab8da0a4730bf6d301fd86b5a89
repository_id = 1325001960
repository_owner_id = 5102389
runner_environment = github-hosted
```

These are deliberately unverified diagnostic claims, not retained JWTs or
authenticated artifact provenance. They demonstrate GitHub's real PR/reusable
claim shape, not the main-context AWS role assumption. The default subject does
**not** bind the reusable workflow; the exact job identity is a separate claim.
AWS's standard `sub` restriction therefore needs the customized template below.

## Exact Proposed Repository Change

Only after approval, PUT the following body to
`repos/krzysztofbury/minos-health/actions/oidc/customization/sub`:

```json
{
  "use_default": false,
  "use_immutable_subject": true,
  "include_claim_keys": ["repo", "context", "job_workflow_ref"]
}
```

Keep the order exact. The GitHub REST schema supports these fields; the actual
resulting subject still needs hosted observation, not a locally synthesized
string presented as live evidence. Publish/test the diagnostic inspector that
can report the optional exact `job_workflow_ref` suffix, then pin a new tool and
reusable workflow revision before changing the setting. The inspector checks
that any suffix agrees with the separate workflow reference/SHA, and still
uses only `minos-health-nonprivileged-rehearsal`, never `sts.amazonaws.com`.

After the change, GET must show `use_default: false`, immutable subjects and the
exact ordered inclusion keys. Rerun the same-repository PR rehearsal at its
published immutable workflow/tool pins. Require an immutable-ID subject carrying
the matching exact reusable-workflow suffix. Reject a moving ref, another SHA,
missing suffix or conflicting claim. Save only the allowlisted observation and
run/head identities. No raw token, bearer, arbitrary claim or credential is saved.

## Impact And Refusal Conditions

This template is repository-wide. Jobs outside a reusable workflow may fail
token issuance because the required `job_workflow_ref` claim is absent; that is
intentional default denial, not permission to remove the claim or add wildcards.
Stop if the refreshed consumer inventory differs, IDs/privacy change, current
settings differ, authentication expires or a new consumer needs separate review.

The live archive-reader role was previously observed to trust an old name-only
main subject. It is already incompatible with the repository's immutable default.
This proposed GitHub change does not repair or broaden that role, and no IAM
fallback is installed. The deployed watcher's existing external-host credentials
and current application are unchanged. The private audience cannot assume that
AWS role. No KMS signer, notification credential or host grant is introduced.

This is **not** authorization to install trust for the diagnostic PR workflow.
The final privileged main-context processing/signing jobs must exist at reviewed
exact SHAs, reject PR/fork/tag contexts and satisfy the complete source/runtime
and provenance contracts. A subsequent saved IAM plan must use their actually
observed customized main subjects and `sts.amazonaws.com`, with no old-subject,
wildcard, PR or name-only fallback. That requires a separate approval.

## Bounded Rollback

If GET or the nonprivileged rehearsal fails, stop before any IAM change. Restore
the prior effective template with this exact PUT body:

```json
{"use_default": true, "use_immutable_subject": true}
```

Re-read the setting and confirm the original immutable default prefix. Stored
inclusion keys, if GitHub retains them, are ignored while `use_default` is true.
Record that distinction rather than claim byte-identical settings. This restores
the earlier effective configuration, not a working AWS role. Once a later IAM
cutover is approved/applied, never restore old name-only trust as a workaround;
its rollback plan must be separately reviewed and keep privileged jobs disabled.

## Approval Scope

Requested now: the single GitHub template PUT, verification through the updated
non-AWS pinned PR rehearsal and the bounded effective-template rollback above.
Not requested: IAM trust/permission changes, KMS provisioning, new host secrets,
ledger placement, deployment, notification setup or a phase-order exception.

Reference: [GitHub repository OIDC REST schema](https://docs.github.com/en/rest/actions/oidc#set-the-customization-template-for-an-oidc-subject-claim-for-a-repository).
