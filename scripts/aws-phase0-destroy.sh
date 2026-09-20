#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-phase0-common.sh
source "$SCRIPT_DIR/aws-phase0-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Uses the setup script environment.' \
    'Second invocation only:' \
    '  MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:<test-account-id>:<saved-plan-sha256>'
  exit 0
fi
[[ $# -eq 0 ]] || phase0_die "no arguments are accepted; use --help"

phase0_require_command aws
phase0_require_command python3
phase0_require_command terraform
phase0_require_command timeout
phase0_require_environment

umask 077
mkdir -p "$PHASE0_ARTIFACT_DIR" "$PHASE0_TF_DATA_DIR"
phase0_capture_identity "$PHASE0_ARTIFACT_DIR/destroy-identity.json"
[[ -s "$PHASE0_STATE_PATH" ]] || phase0_die \
  "Terraform state is missing; there is no state-backed destroy to approve"

phase0_terraform init -backend=false -lockfile=readonly
phase0_terraform show -json "$PHASE0_STATE_PATH" \
  >"$PHASE0_ARTIFACT_DIR/destroy-state-before.json"
bucket="$(phase0_state_resource_value \
  "$PHASE0_ARTIFACT_DIR/destroy-state-before.json" \
  "aws_s3_bucket.snapshots" "id")"
role_name="$(phase0_state_resource_value \
  "$PHASE0_ARTIFACT_DIR/destroy-state-before.json" \
  "aws_iam_role.watcher" "name")"
if [[ -z "${MINOS_AWS_TEST_DESTROY_CONFIRM:-}" ]]; then
  phase0_terraform plan \
    -destroy \
    -state="$PHASE0_STATE_PATH" \
    -out="$PHASE0_ARTIFACT_DIR/destroy.tfplan"
  phase0_terraform show -json "$PHASE0_ARTIFACT_DIR/destroy.tfplan" \
    >"$PHASE0_ARTIFACT_DIR/destroy-plan.json"
  plan_digest="$(phase0_file_sha256 "$PHASE0_ARTIFACT_DIR/destroy.tfplan")"
  printf '%s\n' \
    "Review $PHASE0_ARTIFACT_DIR/destroy-plan.json, then run again with:" \
    "MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$PHASE0_ARTIFACT_DIR/destroy.tfplan" ]] || phase0_die \
  "saved destroy plan is missing; run destroy once without confirmation"
plan_digest="$(phase0_file_sha256 "$PHASE0_ARTIFACT_DIR/destroy.tfplan")"
[[ "$MINOS_AWS_TEST_DESTROY_CONFIRM" == "DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest" ]] || phase0_die \
  "destroy confirmation does not match the exact saved plan"
phase0_terraform apply \
  -state="$PHASE0_STATE_PATH" \
  "$PHASE0_ARTIFACT_DIR/destroy.tfplan"

if [[ -n "$bucket" ]]; then
  bucket_error="$PHASE0_ARTIFACT_DIR/destroy-bucket-check.txt"
  if timeout "$PHASE0_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    s3api head-bucket --bucket "$bucket" >/dev/null 2>"$bucket_error"; then
    phase0_die "bucket still exists after Terraform destroy"
  fi
  bucket_result="$(<"$bucket_error")"
  [[ "$bucket_result" == *"(404)"* || "$bucket_result" == *"(NoSuchBucket)"* ]] || phase0_die \
    "bucket absence could not be verified after Terraform destroy"
fi

if [[ -n "$role_name" ]]; then
  role_error="$PHASE0_ARTIFACT_DIR/destroy-role-check.txt"
  if timeout "$PHASE0_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    iam get-role --role-name "$role_name" >/dev/null 2>"$role_error"; then
    phase0_die "watcher role still exists after Terraform destroy"
  fi
  role_result="$(<"$role_error")"
  [[ "$role_result" == *"(NoSuchEntity)"* ]] || phase0_die \
    "watcher role absence could not be verified after Terraform destroy"
fi
if [[ -n "$(phase0_terraform state list -state="$PHASE0_STATE_PATH")" ]]; then
  phase0_die "Terraform state still contains resources after destroy"
fi

printf 'Destroyed and verified disposable state for account %s.\n' \
  "$MINOS_AWS_TEST_ACCOUNT_ID"
