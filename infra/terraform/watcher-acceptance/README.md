# Disposable watcher AWS acceptance root

This root exists only for explicit real-AWS acceptance of the watcher AWS
contract. It uses local Terraform state, creates a randomized versioned bucket
and an assumable watcher role, and creates no IAM user or access key.

Use the bounded scripts in `scripts/` so STS identity, live ownership tags,
saved plans, and separate apply and destroy approvals are enforced. Direct
Terraform use still requires the explicit boolean
`production_acceptance_enabled=true` and the exact string
`production_acceptance_confirmation=ACCEPT-DISPOSABLE-RESOURCES:218375352108`.
Ordinary pytest only skips these tests and never plans or applies
infrastructure.

Disposable acceptance intentionally runs in the existing repository production
account `218375352108`. Terraform, the scripts, and pytest all require both
`MINOS_AWS_TEST_ACCOUNT_ID` and `MINOS_AWS_PRODUCTION_ACCOUNT_ID` to equal that
built-in value. They also require the exact independent shell confirmation
`MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM=ACCEPT-DISPOSABLE-RESOURCES:218375352108`.
The caller must resolve through STS to the tracked operator role
`arn:aws:iam::218375352108:role/minos-health-watcher-acceptance-operator` using
a dedicated non-default profile with ambient credentials unset. This ARN is
fixed independently in Terraform, the shell guard, and the Python guard. It is
not selected by an environment variable. If
`MINOS_AWS_ACCEPTANCE_OPERATOR_ARN` is present for compatibility with an
operator shell profile, it must equal the tracked ARN and cannot override it.
An STS assumed-role ARN is normalized to its IAM role ARN before the exact
comparison. Any other same-account principal fails preflight.

The operator role and
`arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary`
are external prerequisites. This root cannot safely create the principal or
boundary that constrain its own apply. Provision them separately with operator
trust limited to the reviewed human or identity-provider path and require MFA
where that path supports it. The operator identity policy must constrain
`iam:CreateRole` to the disposable role ARN pattern and include this exact
condition:

```json
{
  "StringEquals": {
    "iam:PermissionsBoundary": "arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary"
  }
}
```

The operator identity policy, and any separate boundary applied to the operator
itself, are constrained to:

- `sts:GetCallerIdentity` and `sts:AssumeRole` only for the disposable watcher
  roles;
- S3 create, read-control, write-control, list-version, object test, cleanup,
  and bucket-delete actions only on
  `minos-health-watcher-acceptance-*` buckets and their objects;
- IAM create, read, tag, update, inline-policy, and delete actions only on
  `arn:aws:iam::218375352108:role/minos-health-watcher-acceptance-*-watcher`;
- request-tag and resource-tag conditions for `Disposable=true`,
  `Environment=acceptance`, `ManagedBy=Terraform`, `Project=minos-health`,
  `TestHarness=watcher-acceptance`, and the current `TestRunId` wherever the
  AWS action supports those condition keys.

The operator must not receive `iam:PutRolePermissionsBoundary` or
`iam:DeleteRolePermissionsBoundary`. Its policies must explicitly deny
`iam:CreatePolicyVersion`, `iam:SetDefaultPolicyVersion`, `iam:DeletePolicyVersion`,
and `iam:DeletePolicy` on the fixed boundary ARN, and must not otherwise allow
managed-policy mutation or attachment. The operator must not receive IAM user
or access-key management, `iam:PassRole`, Organizations, account
administration, Terraform-state access, or unrelated S3 access.

The fixed disposable-role boundary permits only the watcher S3 list, read, and
conditional-create contract on `minos-health-watcher-acceptance-*` buckets. It
permits no IAM, boundary-management, unrelated S3, or account-administration
actions. The disposable watcher role is always created with this fixed boundary
and cannot contain role-inline policy blocks or attached managed policies; its
one reviewed writer policy remains a separately managed inline policy resource
so revocation is explicit.

The pinned AWS provider can mark the role's computed `inline_policy` and
`managed_policy_arns` collections unknown at plan time even when neither is
configured on the role resource. The plan guard accepts that exact provider
shape only when both attributes are absent from the role configuration and the
separate writer-policy resource has its exact planned name, role, and policy.
After apply, the live guard requires exactly that one inline policy and zero
managed attachments. Unknown nested policy entries or a configured role policy
block remain fail-closed.

Because S3 bucket creation cannot be atomically conditioned on all object-style
tags, the fixed bucket name pattern, exact reviewed plan, pre-apply target
absence check, explicit parent dependencies, immediate post-apply validation,
and fail-closed teardown checks are all required. Do not use an administrator
profile for this harness.

The resource prefix is fixed to `minos-health-watcher-acceptance`. Every
taggable resource carries `Disposable=true`, `Environment=acceptance`,
`ManagedBy=Terraform`, `Project=minos-health`,
`TestHarness=watcher-acceptance`, and a pre-generated random 16-hex `TestRunId`.
No additional AWS-managed tags are expected or allowed on these bucket and role
resources. Before cleanup or destroy, the guard requires exact state-derived
identities, the immutable IAM `RoleId`, exactly these six live tags, the exact
role trust and inline policy, no attached policies, and the expected bucket
versioning, encryption, ownership, public-access, bucket-policy, and lifecycle
controls.

Setup accepts only absent state or a valid Terraform state with no managed
resources. It rejects partial-state apply recovery. Before planning, setup
generates and persists the run ID, so the plan guard can require exact known
bucket and role names, child targets, tags, account, region, operator, trust and
permission policies, lifecycle controls, and outputs. Safety-relevant unknowns
or any mutation behind an allowlisted address fail before approval or apply.
The reviewed plan is opened once, hashed through that descriptor, checked for
pathname replacement, and applied through the same descriptor. Runtime outputs
are strict non-executable JSON and include both the fixed permissions-boundary
ARN and the post-apply immutable IAM `RoleId`.

Every successful acceptance `PutObject`, `CopyObject`, and `DeleteObject`
records the returned `VersionId` and whether the identity is an object version
or delete marker. Before deleting anything, cleanup lists every bounded version
and delete marker and rejects the entire operation if any exact
`(key, VersionId, kind)` identity is absent from that manifest. Cleanup is
bounded to four pages and 2,048 identities. Terraform force-destroy is disabled,
so a raced or incomplete cleanup fails instead of starting provider-managed
unbounded cleanup.

Teardown has two explicit stages. First,
`MINOS_AWS_TEST_REVOKE_CONFIRM=REVOKE:218375352108:<test-run-id>` removes and
verifies absence of the writer inline policy. A later invocation creates the
remaining delete-only Terraform plan. Only its separate digest-bound destroy
confirmation permits manifest-checked cleanup and plan apply. Retries accept
only allowlisted partial teardown states after writer revocation; unknown state
resources, state omissions that still exist in AWS, or lost identity guards
block operation. If full apply completed but post-apply validation stopped before
runtime JSON was written, destroy may reconstruct teardown metadata only from
the exact complete state. It preserves a valid existing object manifest or creates
an empty one; therefore any unrecorded object blocks cleanup before deletion.

## Local process threat model

The artifact directory is created under umask `077`, plans are made read-only,
approval hashes the opened descriptor, pathname replacement is checked before
destructive cleanup and again before apply, and Terraform receives that same
descriptor. These controls cover accidental replacement and an unprivileged
different-user process. A hostile process already running as the operator's
same local UID, or root with ptrace or file-descriptor access, can tamper with
the operator process and is outside this harness threat model. Run acceptance
only on a trusted single-user workstation with no untrusted same-UID process.

Required operator inputs are listed by each script with `--help`. The generated
state, plans, captured identities, runtime JSON, and object manifest are stored
below the ignored
`artifacts/aws-watcher-acceptance/<test-account-id>/` directory.
