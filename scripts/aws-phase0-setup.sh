#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-phase0-common.sh
source "$SCRIPT_DIR/aws-phase0-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Required environment:' \
    '  MINOS_AWS_ACCEPTANCE=1' \
    '  AWS_PROFILE=<dedicated-profile>' \
    '  MINOS_AWS_TEST_PROFILE=<same-dedicated-profile>' \
    '  MINOS_AWS_TEST_ACCOUNT_ID=<12-digit-non-production-account>' \
    '  MINOS_AWS_PRODUCTION_ACCOUNT_ID=<12-digit-production-account>' \
    '  MINOS_AWS_TEST_REGION=<region>' \
    'Second invocation only:' \
    '  MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:<test-account-id>:<saved-plan-sha256>' \
    'Optional: MINOS_AWS_COMMAND_TIMEOUT_SECONDS=300'
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
phase0_capture_identity "$PHASE0_ARTIFACT_DIR/apply-identity.json"

phase0_terraform init -backend=false -lockfile=readonly
phase0_terraform validate
if [[ -z "${MINOS_AWS_TEST_APPLY_CONFIRM:-}" ]]; then
  phase0_terraform plan \
    -state="$PHASE0_STATE_PATH" \
    -out="$PHASE0_ARTIFACT_DIR/apply.tfplan"
  phase0_terraform show -json "$PHASE0_ARTIFACT_DIR/apply.tfplan" \
    >"$PHASE0_ARTIFACT_DIR/apply-plan.json"
  plan_digest="$(phase0_file_sha256 "$PHASE0_ARTIFACT_DIR/apply.tfplan")"
  printf '%s\n' \
    "Review $PHASE0_ARTIFACT_DIR/apply-plan.json, then run again with:" \
    "MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$PHASE0_ARTIFACT_DIR/apply.tfplan" ]] || phase0_die \
  "saved apply plan is missing; run setup once without confirmation"
plan_digest="$(phase0_file_sha256 "$PHASE0_ARTIFACT_DIR/apply.tfplan")"
[[ "$MINOS_AWS_TEST_APPLY_CONFIRM" == "APPLY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest" ]] || phase0_die \
  "apply confirmation does not match the exact saved plan"
phase0_terraform apply \
  -state="$PHASE0_STATE_PATH" \
  "$PHASE0_ARTIFACT_DIR/apply.tfplan"

applied_account="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw aws_account_id)"
applied_region="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw aws_region)"
bucket="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw bucket_name)"
operator_arn="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw operator_principal_arn)"
role_arn="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw watcher_role_arn)"
role_name="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw watcher_role_name)"
run_id="$(phase0_terraform output -state="$PHASE0_STATE_PATH" -raw test_run_id)"
[[ "$applied_account" == "$MINOS_AWS_TEST_ACCOUNT_ID" ]] || phase0_die \
  "Terraform output account does not match the approved account"
[[ "$applied_region" == "$MINOS_AWS_TEST_REGION" ]] || phase0_die \
  "Terraform output region does not match the approved region"
[[ "$operator_arn" == "$PHASE0_OPERATOR_ARN" ]] || phase0_die \
  "Terraform role trust does not match the verified principal"

printf '%s\n' \
  "MINOS_AWS_TEST_ACCOUNT_ID_APPLIED=$applied_account" \
  "MINOS_AWS_TEST_REGION_APPLIED=$applied_region" \
  "MINOS_AWS_TEST_BUCKET=$bucket" \
  "MINOS_AWS_TEST_OPERATOR_ARN=$operator_arn" \
  "MINOS_AWS_TEST_ROLE_ARN=$role_arn" \
  "MINOS_AWS_TEST_ROLE_NAME=$role_name" \
  "MINOS_AWS_TEST_RUN_ID=$run_id" \
  >"$PHASE0_RUNTIME_ENV"

printf 'Applied disposable run %s in bucket %s. Run tests, then destroy it.\n' \
  "$run_id" "$bucket"
