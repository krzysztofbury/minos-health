# Terraform State Bootstrap

This root creates the dedicated S3 bucket used by later Terraform roots. It is
intentionally backed by local state because a state bucket cannot safely own the
state required to create itself.

The bucket name is deterministic:

```text
minos-health-tfstate-<aws-account-id>-<aws-region>
```

The bucket is private, encrypted, versioned, rejects non-TLS requests, and has
Terraform configuration-level destroy protection. Manual AWS changes or removal
of that lifecycle rule remain break-glass risks. Later roots use native S3
lockfiles under distinct state keys.

## Plan

Authenticate with the non-root SSO profile and inspect the account identity:

```bash
umask 077
export AWS_PROFILE=minos-health-bootstrap
mise install
mise exec -- aws sso login --profile "$AWS_PROFILE"
mise exec -- aws sts get-caller-identity --profile "$AWS_PROFILE"
cp infra/bootstrap/terraform.tfvars.example infra/bootstrap/terraform.tfvars
```

Replace the placeholder in the ignored `terraform.tfvars` with the exact account
ID shown by both the command and the AWS console. Terraform rejects any other
authenticated account through the provider `allowed_account_ids` guard.

Initialize with the committed provider lockfile and review a saved plan:

```bash
mise exec -- terraform -chdir=infra/bootstrap init -lockfile=readonly
mise exec -- terraform -chdir=infra/bootstrap plan -out=bootstrap.tfplan
chmod 600 infra/bootstrap/bootstrap.tfplan
mise exec -- terraform -chdir=infra/bootstrap show bootstrap.tfplan
```

Do not apply until the plan shows only the expected state bucket and its
versioning, encryption, ownership, public-access, and TLS policy resources.

## Apply And Back Up

After approval:

```bash
mise exec -- terraform -chdir=infra/bootstrap apply bootstrap.tfplan
mise exec -- terraform -chdir=infra/bootstrap output
sha256sum infra/bootstrap/terraform.tfstate \
  > infra/bootstrap/terraform.tfstate.sha256
chmod 600 infra/bootstrap/terraform.tfstate \
  infra/bootstrap/terraform.tfstate.sha256
rm infra/bootstrap/bootstrap.tfplan
```

Create one document named `MinosHealth AWS Terraform Bootstrap Recovery` in the
`MinosHealth` 1Password vault. Attach `terraform.tfstate` and record the checksum
as a `State SHA-256` text field on that same item; do not create a separate
checksum item. Keep both ignored files locally until a recovery test has
reconstructed the bucket configuration from that backup. Never commit these
files or upload them to GitHub Actions artifacts.

If bootstrap recovery is required, restore the exact state and checksum, verify
the checksum locally, authenticate to the expected AWS account, and run
`terraform plan` before making any change.

## Remote Backend Contract

Every later root declares only invariant backend fields:

```hcl
terraform {
  backend "s3" {
    key          = "watcher/terraform.tfstate"
    encrypt      = true
    use_lockfile = true
  }
}
```

Initialization supplies the deployment-specific controls explicitly:

```bash
mise exec -- aws s3api head-bucket \
  --profile "$AWS_PROFILE" \
  --region eu-central-1 \
  --bucket "<state-bucket-name>" \
  --expected-bucket-owner "<twelve-digit-account-id>" \
  --query BucketRegion --output text
mise exec -- terraform -chdir=infra/terraform/watcher init \
  -backend-config="bucket=<state-bucket-name>" \
  -backend-config="region=eu-central-1" \
  -backend-config='allowed_account_ids=["<twelve-digit-account-id>"]' \
  -lockfile=readonly
```

The watcher infrastructure identity receives bounded `s3:ListBucket` access and
`s3:GetObject` plus `s3:PutObject` only for
`watcher/terraform.tfstate`. It receives `s3:GetObject`, `s3:PutObject`, and
`s3:DeleteObject` only for `watcher/terraform.tfstate.tflock`. It receives no
`s3:DeleteObject` or `s3:DeleteObjectVersion` permission for state objects.

Each later root uses its own fixed key and equivalent key-scoped permissions.

Before remote state becomes authoritative, the watcher stack must provision and
preflight a separately assumable break-glass recovery identity. For each root,
that identity receives bounded `s3:ListBucket` and `s3:ListBucketVersions` access,
`s3:GetObject`, `s3:GetObjectVersion`, and conditional `s3:PutObject` access to
the exact state key, plus lock inspection and removal on the exact `.tflock`
key. It receives no `s3:DeleteObject` or `s3:DeleteObjectVersion` permission for
state objects. Its SSO assignment and recovery instructions belong in the
`MinosHealth` vault and must be tested before production acceptance.

## Remote State Recovery

Recovery is a maintenance operation. First stop infrastructure workflows and
confirm that no operator or automation is running Terraform. Verify the active
profile, expected account ID, bucket owner, root key, and current state checksum.
Use the preflighted recovery identity and a private directory on an encrypted
local filesystem:

```bash
umask 077
RECOVERY_DIR="$(mktemp -d)"
trap 'rm -rf -- "$RECOVERY_DIR"' EXIT
```

List versions for the exact state key and download the selected candidate by its
exact `VersionId` into an isolated directory. Never overwrite the active object
during inspection:

```bash
mise exec -- aws s3api list-object-versions \
  --bucket "<state-bucket-name>" \
  --prefix "watcher/terraform.tfstate" \
  --expected-bucket-owner "<twelve-digit-account-id>"
mise exec -- aws s3api get-object \
  --bucket "<state-bucket-name>" \
  --key "watcher/terraform.tfstate" \
  --version-id "<version-id>" \
  --expected-bucket-owner "<twelve-digit-account-id>" \
  "$RECOVERY_DIR/candidate.tfstate"
```

Inspect the candidate's `lineage`, `serial`, Terraform version, and resources.
Its lineage must match the active state and its serial must be the intended
recovery point. Download the current object separately before restoring the
candidate as a new current version. Capture the current ETag immediately before
the download and require that same ETag during the restore:

```bash
CURRENT_ETAG="$(mise exec -- aws s3api head-object \
  --bucket "<state-bucket-name>" \
  --key "watcher/terraform.tfstate" \
  --expected-bucket-owner "<twelve-digit-account-id>" \
  --query ETag \
  --output text)"
mise exec -- aws s3api get-object \
  --bucket "<state-bucket-name>" \
  --key "watcher/terraform.tfstate" \
  --if-match "$CURRENT_ETAG" \
  --expected-bucket-owner "<twelve-digit-account-id>" \
  "$RECOVERY_DIR/current-before-restore.tfstate"
sha256sum "$RECOVERY_DIR/candidate.tfstate" \
  "$RECOVERY_DIR/current-before-restore.tfstate"
mise exec -- aws s3api put-object \
  --bucket "<state-bucket-name>" \
  --key "watcher/terraform.tfstate" \
  --body "$RECOVERY_DIR/candidate.tfstate" \
  --if-match "$CURRENT_ETAG" \
  --server-side-encryption AES256 \
  --expected-bucket-owner "<twelve-digit-account-id>"
```

S3 versioning preserves the prior current object. Pull and inspect the newly
active state, verify its lineage and serial again, and run a reviewed plan
before any apply. An HTTP 412 or `PreconditionFailed` response means the active
state changed; do not retry the write. Restart recovery from identity and writer
checks. Remove the temporary directory when verification is complete.

If a `.tflock` object exists, confirm through GitHub Actions and all operators
that no writer is active. Use `terraform force-unlock <lock-id>` only when the
lock metadata identifies an abandoned operation. Never delete a lock merely
because it is old. This procedure must be exercised against disposable state
before production acceptance.
