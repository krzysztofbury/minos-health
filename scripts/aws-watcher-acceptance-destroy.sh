#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-watcher-common.sh
source "$SCRIPT_DIR/aws-watcher-common.sh"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Uses the setup script environment.' \
    'Requires MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM=ACCEPT-DISPOSABLE-RESOURCES:218375352108.' \
    'Writer revocation stage:' \
    '  MINOS_AWS_TEST_REVOKE_CONFIRM=REVOKE:<account-id>:<test-run-id>' \
    'Second invocation only:' \
    '  MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:<test-account-id>:<saved-plan-sha256>'
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
watcher_capture_identity "$WATCHER_ARTIFACT_DIR/destroy-identity.json"
[[ -s "$WATCHER_STATE_PATH" ]] || watcher_die \
  "Terraform state is missing; there is no state-backed destroy to approve"

watcher_terraform init -backend=false -lockfile=readonly
watcher_terraform show -json "$WATCHER_STATE_PATH" \
  >"$WATCHER_ARTIFACT_DIR/destroy-state-before.json"
if [[ ! -f "$WATCHER_RUNTIME_JSON" ]]; then
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    initialize-teardown-runtime \
    "$WATCHER_ARTIFACT_DIR/destroy-state-before.json" \
    "$WATCHER_RUNTIME_JSON" \
    "$WATCHER_OBJECT_MANIFEST" \
    --region "$MINOS_AWS_TEST_REGION" \
    --operator-arn "$WATCHER_OPERATOR_ARN"
fi
watcher_load_runtime
run_id="$MINOS_AWS_TEST_RUN_ID"
bucket="$MINOS_AWS_TEST_BUCKET"
role_name="$MINOS_AWS_TEST_ROLE_NAME"
watcher_validate_resource_identities "$run_id" "$bucket" "$role_name"
role_id="$MINOS_AWS_TEST_ROLE_ID"
export TF_VAR_test_run_id="$run_id"
teardown_status="$(watcher_verify_teardown "$bucket" "$role_name" "$run_id" "$role_id")"
IFS=: read -r writer_state bucket_present role_present <<<"$teardown_status"
[[ "$bucket_present" == "true" || "$bucket_present" == "false" ]] || watcher_die \
  "teardown bucket status is invalid"
[[ "$role_present" == "true" || "$role_present" == "false" ]] || watcher_die \
  "teardown role status is invalid"
if [[ "$writer_state" == "empty" ]]; then
  printf 'Disposable Terraform state is already empty for account %s.\n' \
    "$MINOS_AWS_TEST_ACCOUNT_ID"
  exit 0
fi
if [[ "$writer_state" == "active" ]]; then
  expected_revoke="REVOKE:$MINOS_AWS_TEST_ACCOUNT_ID:$run_id"
  if [[ -z "${MINOS_AWS_TEST_REVOKE_CONFIRM:-}" ]]; then
    printf '%s\n' \
      'The acceptance writer must be revoked before cleanup or destroy.' \
      "Run again with MINOS_AWS_TEST_REVOKE_CONFIRM=$expected_revoke"
    exit 0
  fi
  [[ "$MINOS_AWS_TEST_REVOKE_CONFIRM" == "$expected_revoke" ]] || watcher_die \
    "writer revocation confirmation is not exact"
  watcher_guard_aws_resources revoke-writer "$bucket" "$role_name" "$run_id" "$role_id"
  printf '%s\n' \
    'Acceptance writer permissions were revoked and verified.' \
    'Run destroy again without the revocation confirmation to create the final plan.'
  exit 0
fi
[[ "$writer_state" == "revoked" || "$writer_state" == "partial" ]] || watcher_die \
  "acceptance teardown state is invalid"
if [[ -z "${MINOS_AWS_TEST_DESTROY_CONFIRM:-}" ]]; then
  rm -f -- "$WATCHER_ARTIFACT_DIR/destroy.tfplan" "$WATCHER_ARTIFACT_DIR/destroy-plan.json"
  watcher_terraform plan \
    -destroy \
    -state="$WATCHER_STATE_PATH" \
    -out="$WATCHER_ARTIFACT_DIR/destroy.tfplan"
  watcher_terraform show -json "$WATCHER_ARTIFACT_DIR/destroy.tfplan" \
    >"$WATCHER_ARTIFACT_DIR/destroy-plan.json"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" validate-destroy-plan \
    "$WATCHER_ARTIFACT_DIR/destroy-plan.json" \
    --region "$MINOS_AWS_TEST_REGION" \
    --operator-arn "$WATCHER_OPERATOR_ARN" \
    --role-id "$role_id" \
    --current-state-json "$WATCHER_ARTIFACT_DIR/destroy-state-before.json"
  chmod 0400 "$WATCHER_ARTIFACT_DIR/destroy.tfplan"
  plan_digest="$(watcher_file_sha256 "$WATCHER_ARTIFACT_DIR/destroy.tfplan")"
  printf '%s\n' \
    "Review $WATCHER_ARTIFACT_DIR/destroy-plan.json, then run again with:" \
    "MINOS_AWS_TEST_DESTROY_CONFIRM=DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$WATCHER_ARTIFACT_DIR/destroy.tfplan" ]] || watcher_die \
  "saved destroy plan is missing; run destroy once without confirmation"
watcher_terraform show -json "$WATCHER_ARTIFACT_DIR/destroy.tfplan" \
  >"$WATCHER_ARTIFACT_DIR/destroy-plan.json"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" validate-destroy-plan \
  "$WATCHER_ARTIFACT_DIR/destroy-plan.json" \
  --region "$MINOS_AWS_TEST_REGION" \
  --operator-arn "$WATCHER_OPERATOR_ARN" \
  --role-id "$role_id" \
  --current-state-json "$WATCHER_ARTIFACT_DIR/destroy-state-before.json"
watcher_open_confirmed_plan \
  "$WATCHER_ARTIFACT_DIR/destroy.tfplan" \
  "DESTROY:$MINOS_AWS_TEST_ACCOUNT_ID" \
  "$MINOS_AWS_TEST_DESTROY_CONFIRM"
if [[ "$bucket_present" == "true" ]]; then
  watcher_guard_aws_resources cleanup-bucket "$bucket" "$role_name" "$run_id" "$role_id"
fi
preapply_status="$(watcher_verify_teardown "$bucket" "$role_name" "$run_id" "$role_id")"
[[ "$preapply_status" == "$teardown_status" ]] || watcher_die \
  "teardown identities or writer state changed immediately before destroy apply"
watcher_apply_opened_plan \
  "$WATCHER_ARTIFACT_DIR/destroy.tfplan" \
  "$WATCHER_STATE_PATH"

if [[ "$bucket_present" == "true" ]]; then
  bucket_error="$WATCHER_ARTIFACT_DIR/destroy-bucket-check.txt"
  if timeout "$WATCHER_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    s3api head-bucket --bucket "$bucket" >/dev/null 2>"$bucket_error"; then
    watcher_die "bucket still exists after Terraform destroy"
  fi
  bucket_result="$(<"$bucket_error")"
  [[ "$bucket_result" == *"(404)"* || "$bucket_result" == *"(NoSuchBucket)"* ]] || watcher_die \
    "bucket absence could not be verified after Terraform destroy"
fi

if [[ "$role_present" == "true" ]]; then
  role_error="$WATCHER_ARTIFACT_DIR/destroy-role-check.txt"
  if timeout "$WATCHER_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    iam get-role --role-name "$role_name" >/dev/null 2>"$role_error"; then
    watcher_die "watcher role still exists after Terraform destroy"
  fi
  role_result="$(<"$role_error")"
  [[ "$role_result" == *"(NoSuchEntity)"* ]] || watcher_die \
    "watcher role absence could not be verified after Terraform destroy"
fi
if [[ -n "$(watcher_terraform state list -state="$WATCHER_STATE_PATH")" ]]; then
  watcher_die "Terraform state still contains resources after destroy"
fi

printf 'Destroyed and verified disposable state for account %s.\n' \
  "$MINOS_AWS_TEST_ACCOUNT_ID"
rm -f -- "$WATCHER_ARTIFACT_DIR/destroy.tfplan"
