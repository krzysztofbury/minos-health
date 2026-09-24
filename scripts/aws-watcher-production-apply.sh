#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-watcher-common.sh
source "$SCRIPT_DIR/aws-watcher-common.sh"
WATCHER_TERRAFORM_ROOT="$WATCHER_ROOT/infra/terraform/watcher"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Required environment:' \
    '  AWS_PROFILE=<dedicated-production-operator-profile>' \
    '  MINOS_AWS_PRODUCTION_PROFILE=<same-profile>' \
    '  MINOS_AWS_PRODUCTION_REGION=<watcher-region>' \
    '  MINOS_AWS_STATE_BUCKET=<existing-bootstrap-state-bucket>' \
    '  MINOS_AWS_STATE_REGION=<bootstrap-state-region>' \
    'Second invocation only:' \
    '  MINOS_AWS_PRODUCTION_APPLY_CONFIRM=APPLY:218375352108:<reviewed-plan-sha256>' \
    'Read-only alert verification after apply:' \
    '  scripts/aws-watcher-production-apply.sh --verify-alerts' \
    'Optional: MINOS_AWS_COMMAND_TIMEOUT_SECONDS=300'
  exit 0
fi
mode="apply"
if [[ "${1:-}" == "--verify-alerts" ]]; then
  mode="verify-alerts"
  shift
fi
[[ $# -eq 0 ]] || watcher_die "only --verify-alerts is accepted; use --help"

watcher_require_command aws
watcher_require_command python3
watcher_require_command terraform
watcher_require_command timeout

[[ -n "${MINOS_AWS_PRODUCTION_PROFILE:-}" ]] || watcher_die \
  "MINOS_AWS_PRODUCTION_PROFILE is required"
[[ "$MINOS_AWS_PRODUCTION_PROFILE" != "default" ]] || watcher_die \
  "the default AWS profile is not accepted"
[[ "${AWS_PROFILE:-}" == "$MINOS_AWS_PRODUCTION_PROFILE" ]] || watcher_die \
  "AWS_PROFILE must exactly match MINOS_AWS_PRODUCTION_PROFILE"
[[ "${MINOS_AWS_PRODUCTION_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || watcher_die \
  "MINOS_AWS_PRODUCTION_REGION is invalid"
[[ "${MINOS_AWS_STATE_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || watcher_die \
  "MINOS_AWS_STATE_REGION is invalid"
[[ "${MINOS_AWS_STATE_BUCKET:-}" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || watcher_die \
  "MINOS_AWS_STATE_BUCKET is invalid"
[[ "$MINOS_AWS_STATE_BUCKET" != *".."* ]] || watcher_die \
  "MINOS_AWS_STATE_BUCKET is invalid"
[[ -f "$WATCHER_TERRAFORM_ROOT/terraform.tfvars" ]] || watcher_die \
  "the reviewed production terraform.tfvars file is missing"

for credential_name in \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
  AWS_WEB_IDENTITY_TOKEN_FILE AWS_CONTAINER_CREDENTIALS_RELATIVE_URI \
  AWS_CONTAINER_CREDENTIALS_FULL_URI; do
  [[ -z "${!credential_name:-}" ]] || watcher_die \
    "$credential_name must be unset; use only the dedicated profile"
done

WATCHER_TIMEOUT_SECONDS="${MINOS_AWS_COMMAND_TIMEOUT_SECONDS:-300}"
[[ "$WATCHER_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || watcher_die \
  "MINOS_AWS_COMMAND_TIMEOUT_SECONDS must be an integer"
((WATCHER_TIMEOUT_SECONDS >= 30 && WATCHER_TIMEOUT_SECONDS <= 1800)) || watcher_die \
  "MINOS_AWS_COMMAND_TIMEOUT_SECONDS must be between 30 and 1800"

WATCHER_ARTIFACT_DIR="$WATCHER_ROOT/artifacts/aws-watcher-production"
export TF_DATA_DIR="$WATCHER_ARTIFACT_DIR/terraform-data"
export TF_IN_AUTOMATION=1
export TF_INPUT=0
export AWS_PAGER=""
export TF_VAR_aws_region="$MINOS_AWS_PRODUCTION_REGION"
export TF_VAR_expected_aws_account_id="$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID"

umask 077
mkdir -p "$WATCHER_ARTIFACT_DIR" "$TF_DATA_DIR"
identity_path="$WATCHER_ARTIFACT_DIR/apply-identity.json"
timeout "$WATCHER_TIMEOUT_SECONDS" aws \
  --profile "$MINOS_AWS_PRODUCTION_PROFILE" \
  --region "$MINOS_AWS_PRODUCTION_REGION" \
  sts get-caller-identity --output json >"$identity_path"
actual_account="$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["Account"])' \
  "$identity_path")"
[[ "$actual_account" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || watcher_die \
  "STS did not resolve to the repository-owned production account"
actual_arn="$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["Arn"])' \
  "$identity_path")"
[[ "$actual_arn" =~ ^arn:aws:sts::${MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID}:assumed-role/minos-health-watcher-terraform-operator/[^/]+$ ]] || watcher_die \
  "production watcher Terraform requires the dedicated scoped operator role"

state_bucket_name="$(timeout "$WATCHER_TIMEOUT_SECONDS" aws \
  --profile "$MINOS_AWS_PRODUCTION_PROFILE" \
  --region "$MINOS_AWS_STATE_REGION" \
  s3api list-objects-v2 \
  --bucket "$MINOS_AWS_STATE_BUCKET" \
  --prefix watcher/terraform.tfstate \
  --max-keys 1 --no-paginate \
  --expected-bucket-owner "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
  --query Name --output text)"
[[ "$state_bucket_name" == "$MINOS_AWS_STATE_BUCKET" ]] || watcher_die \
  "the state bucket identity differs from the reviewed bucket"

watcher_terraform init -reconfigure -lockfile=readonly \
  -backend-config="bucket=$MINOS_AWS_STATE_BUCKET" \
  -backend-config="region=$MINOS_AWS_STATE_REGION" \
  -backend-config="allowed_account_ids=[\"$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID\"]"
watcher_terraform validate

if [[ "$mode" == "verify-alerts" ]]; then
  watcher_verify_budget_subscription \
    "$MINOS_AWS_PRODUCTION_PROFILE" \
    "$MINOS_AWS_PRODUCTION_REGION" \
    "$WATCHER_ARTIFACT_DIR"
  printf 'Verified the exact confirmed budget alert subscription.\n'
  exit 0
fi

plan_path="$WATCHER_ARTIFACT_DIR/watcher.tfplan"
plan_json_path="$WATCHER_ARTIFACT_DIR/watcher-plan.json"
if [[ -z "${MINOS_AWS_PRODUCTION_APPLY_CONFIRM:-}" ]]; then
  watcher_terraform plan \
    -var="aws_region=$MINOS_AWS_PRODUCTION_REGION" \
    -var="expected_aws_account_id=$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
    -out="$plan_path"
  watcher_terraform show -json "$plan_path" >"$plan_json_path"
  chmod 0400 "$plan_path"
  plan_digest="$(watcher_file_sha256 "$plan_path")"
  printf '%s\n' \
    "Review $plan_json_path and the SHA-256 digest $plan_digest, then run again with:" \
    "MINOS_AWS_PRODUCTION_APPLY_CONFIRM=APPLY:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$plan_path" ]] || watcher_die \
  "saved production apply plan is missing; run once without confirmation"
watcher_terraform show -json "$plan_path" >"$plan_json_path"
watcher_apply_immutable_plan \
  "$plan_path" \
  "APPLY:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
  "$MINOS_AWS_PRODUCTION_APPLY_CONFIRM"
plan_digest="${MINOS_AWS_PRODUCTION_APPLY_CONFIRM##*:}"

applied_account="$(watcher_terraform output -raw expected_aws_account_id)"
[[ "$applied_account" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || watcher_die \
  "Terraform output account does not match the repository-owned production account"
rm -f "$plan_path"
watcher_verify_budget_subscription \
  "$MINOS_AWS_PRODUCTION_PROFILE" \
  "$MINOS_AWS_PRODUCTION_REGION" \
  "$WATCHER_ARTIFACT_DIR"
printf 'Applied reviewed production plan with SHA-256 %s and verified budget alerts.\n' \
  "$plan_digest"
