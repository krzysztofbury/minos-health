# Watcher AWS Terraform

This remote-state-ready root creates only the static watcher AWS foundation:

- one private, versioned, SSE-S3 snapshot bucket;
- immutable-prefix and TLS bucket-policy guards;
- exact recovery and probe lifecycle rules;
- IAM user `minos-health-watcher` and managed policy
  `minos-health-watcher-archive`, but no access key;
- a protected-ref GitHub OIDC role with read-only archive access;
- a monthly project budget with confirmed SNS email alerts.

The budget filters AWS cost allocation data to the `Project=minos-health` tag.
AWS costs that cannot carry that tag, or whose cost-allocation tag has not yet
become active, remain an account-wide billing residual outside this project
budget and must be reviewed separately in account billing reports.

Raw blobs, recovery inventories, release manifests, and deployment receipts have
no expiration rule. Terraform state remains in the separate bootstrap bucket.

## Approval Inputs

Before planning, obtain explicit approval for all of these values:

- the AWS account ID and region;
- the snapshot bucket prefix and resulting bucket name;
- the exact protected GitHub `owner/repository` and fully qualified ref;
- the monthly budget amount, capped at the approved USD 20 ceiling;
- the budget recipient email address.

Issue #55 owns the missing scoped production operator and IAM boundary review.
A saved plan made using the existing broad bootstrap SSO role is review evidence
only, not an apply authorization. Do not apply until the dedicated operator is
provisioned, the watcher and GitHub reader IAM boundaries are reviewed, and a
fresh exact plan is generated and approved under that operator.

No credential, access key, subscriber secret, or real identifier belongs in the
committed example. Copy it to the ignored `terraform.tfvars` and replace every
placeholder. The example budget amount is deliberately invalid until reviewed.
The example email is also deliberately invalid until replaced with a real
operator-controlled address.

## Initialize And Validate

Authenticate with a non-root operator profile and verify its identity before
Terraform contacts AWS:

```bash
umask 077
export AWS_PROFILE=<watcher-operator-profile>
mise exec -- aws sso login --profile "$AWS_PROFILE"
mise exec -- aws sts get-caller-identity --profile "$AWS_PROFILE"
cp infra/terraform/watcher/terraform.tfvars.example \
  infra/terraform/watcher/terraform.tfvars
```

Initialize the existing bootstrap state bucket using its exact approved name:

```bash
mise exec -- aws s3api list-objects-v2 \
  --profile "$AWS_PROFILE" \
  --region "<state-bucket-region>" \
  --bucket "<state-bucket-name>" \
  --prefix watcher/terraform.tfstate \
  --max-keys 1 --no-paginate \
  --expected-bucket-owner "<twelve-digit-account-id>" \
  --query Name --output text
mise exec -- terraform -chdir=infra/terraform/watcher init \
  -backend-config="bucket=<state-bucket-name>" \
  -backend-config="region=<state-bucket-region>" \
  -backend-config='allowed_account_ids=["<twelve-digit-account-id>"]' \
  -lockfile=readonly
mise exec -- terraform -chdir=infra/terraform/watcher validate
```

The bounded `list-objects-v2` command must return the exact reviewed bucket
name before Terraform initializes. It checks the S3 bucket owner explicitly
using only the existing watcher state-key prefix permission. Bucket region was
independently verified during the operator bootstrap and cannot be changed on
an existing bucket; the supported
Terraform backend `allowed_account_ids` option independently checks the caller's
AWS account. Terraform 1.16 does not support `expected_bucket_owner` as an S3
backend configuration argument.

For local static validation only, use `terraform init -backend=false`. It does
not prove AWS behavior and must not be used as apply evidence.

## Plan Approval Gate

Use the repository script to create and inspect one saved plan. Never apply an
unsaved or newly regenerated plan without reviewing it again. The script also
requires STS to resolve to the repository-owned production account and binds
approval to the saved plan's SHA-256 digest. On apply, it opens the plan once,
validates the digest through that descriptor, rejects pathname replacement,
and gives Terraform the same opened bytes.

```bash
export AWS_PROFILE=<watcher-operator-profile>
export MINOS_AWS_PRODUCTION_PROFILE="$AWS_PROFILE"
export MINOS_AWS_PRODUCTION_REGION=<watcher-region>
export MINOS_AWS_STATE_BUCKET=<state-bucket-name>
export MINOS_AWS_STATE_REGION=<state-bucket-region>
scripts/aws-watcher-production-apply.sh
```

Review `artifacts/aws-watcher-production/watcher-plan.json` and independently
record the SHA-256 printed by the script. Apply only by repeating the command
with the exact confirmation it prints:

```bash
export MINOS_AWS_PRODUCTION_APPLY_CONFIRM=APPLY:218375352108:<reviewed-plan-sha256>
scripts/aws-watcher-production-apply.sh
```

Approval must confirm that the exact plan:

- targets the intended account, region, remote-state key, and bucket names;
- creates no public access and no watcher access key;
- grants the watcher only conditional create and read access to `blobs/sha256/`,
  `control-recovery/`, and `probes/`;
- grants GitHub only read access from the approved repository and ref;
- contains the approved retention periods and no expiry for raw blobs,
  inventories, release manifests, or deployment receipts;
- contains a USD budget no higher than 20 and the approved subscriber;
- has no unexpected replacement, deletion, policy, or administrator access.

Only the named approver may authorize applying that exact saved plan. This root
must never be planned or applied from a pull request or with unreviewed inputs.

## Apply And Confirm Alerts

After exact-plan and digest approval, keep access to the subscriber mailbox open
and let the script apply only the reviewed saved file. The script removes that
plan after a successful apply.

AWS sends an SNS confirmation link. The subscriber must confirm it within the
configured ten-minute Terraform wait. After apply, the script performs a
bounded lookup for at most two subscriptions on the exact Terraform topic and
accepts only one subscription whose email endpoint and real ARN exactly match
the Terraform outputs. `PendingConfirmation`, missing, mismatched, paginated,
or multiple subscriptions make production acceptance fail.

If confirmation finishes after the apply command fails its verification, rerun
only the read-only verification path:

```bash
scripts/aws-watcher-production-apply.sh --verify-alerts
```

Publish one non-sensitive test message to that topic and confirm receipt before
production acceptance. Record the confirmation and test outside Git without
including the recipient address in logs or screenshots.

## Watcher Access Key

Terraform intentionally creates no `aws_iam_access_key` resource. After policy
review, an operator creates one watcher key manually, stores its recovery copy
in the password manager, and installs it only in the service-restricted host
credentials file. Never place it in Terraform inputs or state, GitHub, `.env`,
an image, or logs. Rotate it immediately after suspected exposure.

Real-AWS acceptance remains separate and disposable. It must cover versioning,
conditional `PutObject` responses, full-object native SHA-256 checksums, IAM
denials, lifecycle configuration, and bounded paginated listing. Do not treat
an emulator or this static validation as that evidence.
