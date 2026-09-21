#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-watcher-common.sh
source "$SCRIPT_DIR/aws-watcher-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Required environment:' \
    '  MINOS_AWS_ACCEPTANCE=1' \
    '  AWS_PROFILE=<dedicated-profile>' \
    '  MINOS_AWS_TEST_PROFILE=<same-dedicated-profile>' \
    '  MINOS_AWS_TEST_ACCOUNT_ID=218375352108' \
    '  MINOS_AWS_PRODUCTION_ACCOUNT_ID=218375352108' \
    '  MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM=ACCEPT-DISPOSABLE-RESOURCES:218375352108' \
    '  Operator is fixed in tracked configuration as:' \
    '  arn:aws:iam::218375352108:role/minos-health-watcher-acceptance-operator' \
    '  Disposable-role boundary must already exist as:' \
    '  arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary' \
    '  MINOS_AWS_TEST_REGION=<region>' \
    'Second invocation only:' \
    '  MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:<test-account-id>:<saved-plan-sha256>' \
    'Optional: MINOS_AWS_COMMAND_TIMEOUT_SECONDS=300'
  exit 0
fi
[[ $# -eq 0 ]] || watcher_die "no arguments are accepted; use --help"

watcher_require_command aws
watcher_require_command python3
watcher_require_command terraform
watcher_require_command timeout
watcher_require_command uv
watcher_require_environment

umask 077
mkdir -p "$WATCHER_ARTIFACT_DIR" "$WATCHER_TF_DATA_DIR"
watcher_terraform init -backend=false -lockfile=readonly
watcher_require_empty_setup_state
watcher_capture_identity "$WATCHER_ARTIFACT_DIR/apply-identity.json"
if [[ -z "${MINOS_AWS_TEST_APPLY_CONFIRM:-}" ]]; then
  rm -f -- "$WATCHER_RUN_JSON" "$WATCHER_RUNTIME_JSON" "$WATCHER_OBJECT_MANIFEST"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    initialize-run-identity "$WATCHER_RUN_JSON"
fi
run_id="$(timeout "$WATCHER_TIMEOUT_SECONDS" uv run python \
  "$WATCHER_ACCEPTANCE_GUARD" read-run-identity "$WATCHER_RUN_JSON")"
export TF_VAR_test_run_id="$run_id"
watcher_terraform validate
if [[ -z "${MINOS_AWS_TEST_APPLY_CONFIRM:-}" ]]; then
  rm -f -- "$WATCHER_ARTIFACT_DIR/apply.tfplan" "$WATCHER_ARTIFACT_DIR/apply-plan.json"
  watcher_terraform plan \
    -state="$WATCHER_STATE_PATH" \
    -out="$WATCHER_ARTIFACT_DIR/apply.tfplan"
  watcher_terraform show -json "$WATCHER_ARTIFACT_DIR/apply.tfplan" \
    >"$WATCHER_ARTIFACT_DIR/apply-plan.json"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    validate-apply-plan "$WATCHER_ARTIFACT_DIR/apply-plan.json" \
    --region "$MINOS_AWS_TEST_REGION" \
    --run-id "$run_id"
  chmod 0400 "$WATCHER_ARTIFACT_DIR/apply.tfplan"
  plan_digest="$(watcher_file_sha256 "$WATCHER_ARTIFACT_DIR/apply.tfplan")"
  printf '%s\n' \
    "Review $WATCHER_ARTIFACT_DIR/apply-plan.json, then run again with:" \
    "MINOS_AWS_TEST_APPLY_CONFIRM=APPLY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$WATCHER_ARTIFACT_DIR/apply.tfplan" ]] || watcher_die \
  "saved apply plan is missing; run setup once without confirmation"
watcher_terraform show -json "$WATCHER_ARTIFACT_DIR/apply.tfplan" \
  >"$WATCHER_ARTIFACT_DIR/apply-plan.json"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
  validate-apply-plan "$WATCHER_ARTIFACT_DIR/apply-plan.json" \
  --region "$MINOS_AWS_TEST_REGION" \
  --run-id "$run_id"
watcher_open_confirmed_plan \
  "$WATCHER_ARTIFACT_DIR/apply.tfplan" \
  "APPLY:$MINOS_AWS_TEST_ACCOUNT_ID" \
  "$MINOS_AWS_TEST_APPLY_CONFIRM"
watcher_require_create_targets_absent "$run_id"
watcher_apply_opened_plan \
  "$WATCHER_ARTIFACT_DIR/apply.tfplan" \
  "$WATCHER_STATE_PATH"

applied_account="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw aws_account_id)"
applied_region="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw aws_region)"
bucket="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw bucket_name)"
operator_arn="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw operator_principal_arn)"
role_arn="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw watcher_role_arn)"
permissions_boundary_arn="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw watcher_role_permissions_boundary_arn)"
role_id="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw watcher_role_unique_id)"
role_name="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw watcher_role_name)"
applied_run_id="$(watcher_terraform output -state="$WATCHER_STATE_PATH" -raw test_run_id)"
[[ "$applied_account" == "$MINOS_AWS_TEST_ACCOUNT_ID" ]] || watcher_die \
  "Terraform output account does not match the approved account"
[[ "$applied_region" == "$MINOS_AWS_TEST_REGION" ]] || watcher_die \
  "Terraform output region does not match the approved region"
[[ "$operator_arn" == "$WATCHER_OPERATOR_ARN" ]] || watcher_die \
  "Terraform role trust does not match the verified principal"
[[ "$applied_run_id" == "$run_id" ]] || watcher_die \
  "Terraform output run ID does not match the pre-generated identity"
watcher_validate_resource_identities "$run_id" "$bucket" "$role_name"
[[ "$role_arn" == "arn:aws:iam::$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:role/$role_name" ]] || watcher_die \
  "Terraform role ARN does not match the fixed resource identity"
[[ "$permissions_boundary_arn" == "$MINOS_AWS_EXPECTED_PERMISSIONS_BOUNDARY_ARN" ]] || watcher_die \
  "Terraform role permissions boundary does not match the tracked policy"
watcher_guard_aws_resources verify-aws "$bucket" "$role_name" "$run_id" "$role_id"
watcher_terraform show -json "$WATCHER_STATE_PATH" \
  >"$WATCHER_ARTIFACT_DIR/apply-state-after.json"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
  validate-state "$WATCHER_ARTIFACT_DIR/apply-state-after.json" \
  --region "$MINOS_AWS_TEST_REGION" \
  --operator-arn "$WATCHER_OPERATOR_ARN"

timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
  initialize-object-manifest "$WATCHER_OBJECT_MANIFEST" \
  --account-id "$applied_account" \
  --bucket "$bucket" \
  --run-id "$run_id"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
  write-runtime "$WATCHER_RUNTIME_JSON" \
  --account-id "$applied_account" \
  --region "$applied_region" \
  --bucket "$bucket" \
  --operator-arn "$operator_arn" \
  --permissions-boundary-arn "$permissions_boundary_arn" \
  --role-arn "$role_arn" \
  --role-id "$role_id" \
  --role-name "$role_name" \
  --run-id "$run_id" \
  --object-manifest "$WATCHER_OBJECT_MANIFEST"
rm -f -- "$WATCHER_ARTIFACT_DIR/apply.tfplan"

printf 'Applied disposable run %s in bucket %s. Run tests, then destroy it.\n' \
  "$run_id" "$bucket"
