#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-phase0-common.sh
source "$SCRIPT_DIR/aws-phase0-common.sh"
PHASE0_TERRAFORM_ROOT="$PHASE0_ROOT/infra/terraform/phase0"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Required environment:' \
    '  AWS_PROFILE=<dedicated-production-operator-profile>' \
    '  MINOS_AWS_PRODUCTION_PROFILE=<same-profile>' \
    '  MINOS_AWS_PRODUCTION_REGION=<phase0-region>' \
    '  MINOS_AWS_STATE_BUCKET=<existing-bootstrap-state-bucket>' \
    '  MINOS_AWS_STATE_REGION=<bootstrap-state-region>' \
    'Second invocation only:' \
    '  MINOS_AWS_PRODUCTION_APPLY_CONFIRM=APPLY:218375352108:<reviewed-plan-sha256>' \
    'Optional: MINOS_AWS_COMMAND_TIMEOUT_SECONDS=300'
  exit 0
fi
[[ $# -eq 0 ]] || phase0_die "no arguments are accepted; use --help"

phase0_require_command aws
phase0_require_command python3
phase0_require_command terraform
phase0_require_command timeout

[[ -n "${MINOS_AWS_PRODUCTION_PROFILE:-}" ]] || phase0_die \
  "MINOS_AWS_PRODUCTION_PROFILE is required"
[[ "$MINOS_AWS_PRODUCTION_PROFILE" != "default" ]] || phase0_die \
  "the default AWS profile is not accepted"
[[ "${AWS_PROFILE:-}" == "$MINOS_AWS_PRODUCTION_PROFILE" ]] || phase0_die \
  "AWS_PROFILE must exactly match MINOS_AWS_PRODUCTION_PROFILE"
[[ "${MINOS_AWS_PRODUCTION_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || phase0_die \
  "MINOS_AWS_PRODUCTION_REGION is invalid"
[[ "${MINOS_AWS_STATE_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || phase0_die \
  "MINOS_AWS_STATE_REGION is invalid"
[[ "${MINOS_AWS_STATE_BUCKET:-}" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || phase0_die \
  "MINOS_AWS_STATE_BUCKET is invalid"
[[ "$MINOS_AWS_STATE_BUCKET" != *".."* ]] || phase0_die \
  "MINOS_AWS_STATE_BUCKET is invalid"
[[ -f "$PHASE0_TERRAFORM_ROOT/terraform.tfvars" ]] || phase0_die \
  "the reviewed production terraform.tfvars file is missing"

for credential_name in \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
  AWS_WEB_IDENTITY_TOKEN_FILE AWS_CONTAINER_CREDENTIALS_RELATIVE_URI \
  AWS_CONTAINER_CREDENTIALS_FULL_URI; do
  [[ -z "${!credential_name:-}" ]] || phase0_die \
    "$credential_name must be unset; use only the dedicated profile"
done

PHASE0_TIMEOUT_SECONDS="${MINOS_AWS_COMMAND_TIMEOUT_SECONDS:-300}"
[[ "$PHASE0_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || phase0_die \
  "MINOS_AWS_COMMAND_TIMEOUT_SECONDS must be an integer"
((PHASE0_TIMEOUT_SECONDS >= 30 && PHASE0_TIMEOUT_SECONDS <= 1800)) || phase0_die \
  "MINOS_AWS_COMMAND_TIMEOUT_SECONDS must be between 30 and 1800"

PHASE0_ARTIFACT_DIR="$PHASE0_ROOT/artifacts/aws-phase0-production"
export TF_DATA_DIR="$PHASE0_ARTIFACT_DIR/terraform-data"
export TF_IN_AUTOMATION=1
export TF_INPUT=0
export AWS_PAGER=""
export TF_VAR_aws_region="$MINOS_AWS_PRODUCTION_REGION"
export TF_VAR_expected_aws_account_id="$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID"

umask 077
mkdir -p "$PHASE0_ARTIFACT_DIR" "$TF_DATA_DIR"
identity_path="$PHASE0_ARTIFACT_DIR/apply-identity.json"
timeout "$PHASE0_TIMEOUT_SECONDS" aws \
  --profile "$MINOS_AWS_PRODUCTION_PROFILE" \
  --region "$MINOS_AWS_PRODUCTION_REGION" \
  sts get-caller-identity --output json >"$identity_path"
actual_account="$(python3 -c \
  'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["Account"])' \
  "$identity_path")"
[[ "$actual_account" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
  "STS did not resolve to the repository-owned production account"

phase0_terraform init -reconfigure -lockfile=readonly \
  -backend-config="bucket=$MINOS_AWS_STATE_BUCKET" \
  -backend-config="region=$MINOS_AWS_STATE_REGION" \
  -backend-config="expected_bucket_owner=$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID"
phase0_terraform validate

plan_path="$PHASE0_ARTIFACT_DIR/phase0.tfplan"
plan_json_path="$PHASE0_ARTIFACT_DIR/phase0-plan.json"
if [[ -z "${MINOS_AWS_PRODUCTION_APPLY_CONFIRM:-}" ]]; then
  phase0_terraform plan \
    -var="aws_region=$MINOS_AWS_PRODUCTION_REGION" \
    -var="expected_aws_account_id=$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
    -out="$plan_path"
  phase0_terraform show -json "$plan_path" >"$plan_json_path"
  plan_digest="$(phase0_file_sha256 "$plan_path")"
  printf '%s\n' \
    "Review $plan_json_path and the SHA-256 digest $plan_digest, then run again with:" \
    "MINOS_AWS_PRODUCTION_APPLY_CONFIRM=APPLY:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$plan_path" ]] || phase0_die \
  "saved production apply plan is missing; run once without confirmation"
plan_digest="$(phase0_file_sha256 "$plan_path")"
expected_confirmation="APPLY:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:$plan_digest"
[[ "$MINOS_AWS_PRODUCTION_APPLY_CONFIRM" == "$expected_confirmation" ]] || phase0_die \
  "production apply confirmation does not include the exact reviewed plan digest"
phase0_terraform apply "$plan_path"

applied_account="$(phase0_terraform output -raw expected_aws_account_id)"
[[ "$applied_account" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
  "Terraform output account does not match the repository-owned production account"
rm -f "$plan_path"
printf 'Applied reviewed production plan with SHA-256 %s.\n' "$plan_digest"
