#!/usr/bin/env bash

set -euo pipefail

WATCHER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WATCHER_TERRAFORM_ROOT="$WATCHER_ROOT/infra/terraform/watcher-acceptance"
readonly MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"
readonly MINOS_AWS_ACCEPTANCE_RESOURCE_PREFIX="minos-health-watcher-acceptance"
readonly MINOS_AWS_EXPECTED_ACCEPTANCE_OPERATOR_ARN="arn:aws:iam::218375352108:role/minos-health-watcher-acceptance-operator"
readonly MINOS_AWS_EXPECTED_PERMISSIONS_BOUNDARY_ARN="arn:aws:iam::218375352108:policy/minos-health-watcher-acceptance-boundary"
readonly MINOS_AWS_REQUIRED_PRODUCTION_CONFIRMATION="ACCEPT-DISPOSABLE-RESOURCES:218375352108"
readonly WATCHER_ACCEPTANCE_GUARD="$WATCHER_ROOT/scripts/aws_watcher_acceptance_guard.py"
readonly WATCHER_PRODUCTION_GUARD="$WATCHER_ROOT/scripts/aws_watcher_production_guard.py"

watcher_die() {
  printf 'error: %s\n' "$1" >&2
  exit 2
}

watcher_require_command() {
  command -v "$1" >/dev/null 2>&1 || watcher_die "required command is missing: $1"
}

watcher_require_environment() {
  [[ "${MINOS_AWS_ACCEPTANCE:-}" == "1" ]] || watcher_die \
    "set MINOS_AWS_ACCEPTANCE=1 to opt in"
  [[ -n "${MINOS_AWS_TEST_PROFILE:-}" ]] || watcher_die \
    "MINOS_AWS_TEST_PROFILE is required"
  [[ "$MINOS_AWS_TEST_PROFILE" != "default" ]] || watcher_die \
    "the default AWS profile is not accepted"
  [[ "${AWS_PROFILE:-}" == "$MINOS_AWS_TEST_PROFILE" ]] || watcher_die \
    "AWS_PROFILE must exactly match MINOS_AWS_TEST_PROFILE"
  [[ "${MINOS_AWS_TEST_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ ]] || watcher_die \
    "MINOS_AWS_TEST_ACCOUNT_ID must contain twelve digits"
  [[ "${MINOS_AWS_PRODUCTION_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ ]] || watcher_die \
    "MINOS_AWS_PRODUCTION_ACCOUNT_ID must contain twelve digits"
  [[ "$MINOS_AWS_PRODUCTION_ACCOUNT_ID" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || watcher_die \
    "MINOS_AWS_PRODUCTION_ACCOUNT_ID must match the repository-owned production account"
  [[ "$MINOS_AWS_TEST_ACCOUNT_ID" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || watcher_die \
    "MINOS_AWS_TEST_ACCOUNT_ID must match the repository-owned production account"
  [[ "${MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM:-}" == "$MINOS_AWS_REQUIRED_PRODUCTION_CONFIRMATION" ]] || watcher_die \
    "MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM must equal $MINOS_AWS_REQUIRED_PRODUCTION_CONFIRMATION"
  [[ -z "${MINOS_AWS_ACCEPTANCE_OPERATOR_ARN:-}" || "$MINOS_AWS_ACCEPTANCE_OPERATOR_ARN" == "$MINOS_AWS_EXPECTED_ACCEPTANCE_OPERATOR_ARN" ]] || watcher_die \
    "MINOS_AWS_ACCEPTANCE_OPERATOR_ARN cannot override the tracked operator ARN"
  [[ "${MINOS_AWS_TEST_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || watcher_die \
    "MINOS_AWS_TEST_REGION is invalid"

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

  WATCHER_ARTIFACT_DIR="$WATCHER_ROOT/artifacts/aws-watcher-acceptance/$MINOS_AWS_TEST_ACCOUNT_ID"
  WATCHER_TF_DATA_DIR="$WATCHER_ARTIFACT_DIR/terraform-data"
  WATCHER_STATE_PATH="$WATCHER_ARTIFACT_DIR/terraform.tfstate"
  WATCHER_RUNTIME_JSON="$WATCHER_ARTIFACT_DIR/runtime.json"
  WATCHER_OBJECT_MANIFEST="$WATCHER_ARTIFACT_DIR/allowed-object-identities.json"
  WATCHER_RUN_JSON="$WATCHER_ARTIFACT_DIR/run-identity.json"
  export WATCHER_ARTIFACT_DIR WATCHER_OBJECT_MANIFEST WATCHER_RUN_JSON
  export WATCHER_RUNTIME_JSON WATCHER_STATE_PATH
  export TF_DATA_DIR="$WATCHER_TF_DATA_DIR"
  export TF_IN_AUTOMATION=1
  export TF_INPUT=0
  export AWS_PAGER=""
  export TF_VAR_aws_region="$MINOS_AWS_TEST_REGION"
  export TF_VAR_expected_test_account_id="$MINOS_AWS_TEST_ACCOUNT_ID"
  export TF_VAR_production_account_id="$MINOS_AWS_PRODUCTION_ACCOUNT_ID"
  export TF_VAR_production_acceptance_enabled=true
  export TF_VAR_production_acceptance_confirmation="$MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM"
}

watcher_capture_identity() {
  local destination="$1"
  timeout "$WATCHER_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    sts get-caller-identity --output json >"$destination"

  local actual_account
  actual_account="$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["Account"])' \
    "$destination")"
  [[ "$actual_account" == "$MINOS_AWS_TEST_ACCOUNT_ID" ]] || watcher_die \
    "STS returned account $actual_account, expected $MINOS_AWS_TEST_ACCOUNT_ID"

  WATCHER_OPERATOR_ARN="$(python3 "$WATCHER_ROOT/scripts/aws-watcher-principal.py" "$destination")"
  [[ "$WATCHER_OPERATOR_ARN" =~ ^arn:[a-z0-9-]+:iam::${MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID}:(user|role)/[A-Za-z0-9+=,.@_/-]+$ ]] || watcher_die \
    "operator principal must be in the repository-owned production account"
  [[ "$WATCHER_OPERATOR_ARN" == "$MINOS_AWS_EXPECTED_ACCEPTANCE_OPERATOR_ARN" ]] || watcher_die \
    "STS operator does not match the tracked acceptance operator ARN"
  export WATCHER_OPERATOR_ARN
  printf 'Verified AWS account %s as %s\n' "$actual_account" "$WATCHER_OPERATOR_ARN"
}

watcher_terraform() {
  timeout "$WATCHER_TIMEOUT_SECONDS" terraform \
    -chdir="$WATCHER_TERRAFORM_ROOT" "$@"
}

watcher_file_sha256() {
  python3 -c \
    'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
    "$1"
}

watcher_verify_budget_subscription() {
  local profile="$1"
  local region="$2"
  local artifact_directory="$3"
  local topic_arn subscriber_email expected_subscription_arn response_path
  topic_arn="$(watcher_terraform output -raw budget_alert_topic_arn)"
  subscriber_email="$(watcher_terraform output -raw budget_alert_subscriber_email)"
  expected_subscription_arn="$(watcher_terraform output -raw budget_alert_subscription_arn)"
  response_path="$artifact_directory/budget-subscriptions.json"

  if ! timeout "$WATCHER_TIMEOUT_SECONDS" aws \
    --profile "$profile" \
    --region "$region" \
    sns list-subscriptions-by-topic \
    --topic-arn "$topic_arn" \
    --max-items 2 \
    --no-cli-pager \
    --output json >"$response_path"; then
    rm -f -- "$response_path"
    watcher_die "AWS SNS subscription lookup failed"
  fi

  if ! timeout "$WATCHER_TIMEOUT_SECONDS" python3 \
    "$WATCHER_PRODUCTION_GUARD" \
    "$response_path" \
    "$topic_arn" \
    "$subscriber_email" \
    "$expected_subscription_arn"; then
    rm -f -- "$response_path"
    watcher_die "budget alert subscription is not exactly confirmed"
  fi
  rm -f -- "$response_path"
}

watcher_load_runtime() {
  [[ -f "$WATCHER_RUNTIME_JSON" ]] || watcher_die \
    "runtime outputs are missing; run the setup script first"
  MINOS_AWS_TEST_ACCOUNT_ID_APPLIED="$(watcher_runtime_value account_id)"
  MINOS_AWS_TEST_REGION_APPLIED="$(watcher_runtime_value region)"
  MINOS_AWS_TEST_BUCKET="$(watcher_runtime_value bucket)"
  MINOS_AWS_TEST_OPERATOR_ARN="$(watcher_runtime_value operator_arn)"
  MINOS_AWS_TEST_PERMISSIONS_BOUNDARY_ARN="$(watcher_runtime_value permissions_boundary_arn)"
  MINOS_AWS_TEST_ROLE_ARN="$(watcher_runtime_value role_arn)"
  MINOS_AWS_TEST_ROLE_ID="$(watcher_runtime_value role_id)"
  MINOS_AWS_TEST_ROLE_NAME="$(watcher_runtime_value role_name)"
  MINOS_AWS_TEST_RUN_ID="$(watcher_runtime_value run_id)"
  MINOS_AWS_TEST_OBJECT_MANIFEST="$(watcher_runtime_value object_manifest)"
  [[ "$MINOS_AWS_TEST_ACCOUNT_ID" == "$MINOS_AWS_TEST_ACCOUNT_ID_APPLIED" ]] || watcher_die \
    "runtime outputs belong to another AWS account"
  [[ "$MINOS_AWS_TEST_REGION" == "$MINOS_AWS_TEST_REGION_APPLIED" ]] || watcher_die \
    "runtime outputs belong to another AWS region"
  [[ "$WATCHER_OPERATOR_ARN" == "$MINOS_AWS_TEST_OPERATOR_ARN" ]] || watcher_die \
    "runtime outputs were applied by another principal"
  [[ "$MINOS_AWS_TEST_PERMISSIONS_BOUNDARY_ARN" == "$MINOS_AWS_EXPECTED_PERMISSIONS_BOUNDARY_ARN" ]] || watcher_die \
    "runtime permissions boundary does not match the tracked policy"
  [[ "$MINOS_AWS_TEST_OBJECT_MANIFEST" == "$WATCHER_OBJECT_MANIFEST" ]] || watcher_die \
    "runtime object manifest path is mismatched"
  watcher_validate_resource_identities \
    "${MINOS_AWS_TEST_RUN_ID:-}" \
    "${MINOS_AWS_TEST_BUCKET:-}" \
    "${MINOS_AWS_TEST_ROLE_NAME:-}"
  export MINOS_AWS_TEST_BUCKET MINOS_AWS_TEST_OPERATOR_ARN
  export MINOS_AWS_TEST_PERMISSIONS_BOUNDARY_ARN
  export MINOS_AWS_TEST_ROLE_ARN MINOS_AWS_TEST_ROLE_ID MINOS_AWS_TEST_ROLE_NAME
  export MINOS_AWS_TEST_RUN_ID MINOS_AWS_TEST_OBJECT_MANIFEST
}

watcher_runtime_value() {
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    runtime-field "$WATCHER_RUNTIME_JSON" "$1"
}

watcher_validate_resource_identities() {
  local run_id="$1"
  local bucket="$2"
  local role_name="$3"
  [[ "$run_id" =~ ^[0-9a-f]{16}$ ]] || watcher_die \
    "acceptance run ID must contain exactly 16 lowercase hex characters"
  [[ "$bucket" == "$MINOS_AWS_ACCEPTANCE_RESOURCE_PREFIX-$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID-$run_id" ]] || watcher_die \
    "acceptance bucket name does not match the fixed resource identity"
  [[ "$role_name" == "$MINOS_AWS_ACCEPTANCE_RESOURCE_PREFIX-$run_id-watcher" ]] || watcher_die \
    "acceptance role name does not match the fixed resource identity"
}

watcher_guard_aws_resources() {
  local command="$1"
  local bucket="$2"
  local role_name="$3"
  local run_id="$4"
  local role_id="$5"
  local arguments=(
    "$command" \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    --bucket "$bucket" \
    --role-name "$role_name" \
    --role-id "$role_id" \
    --run-id "$run_id" \
    --operator-arn "$WATCHER_OPERATOR_ARN"
  )
  if [[ "$command" == "cleanup-bucket" ]]; then
    arguments+=(
      --object-manifest "$WATCHER_OBJECT_MANIFEST"
      --state-json "$WATCHER_ARTIFACT_DIR/destroy-state-before.json"
      --production-confirmation "$MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM"
    )
  fi
  if [[ "$command" == "revoke-writer" ]]; then
    arguments+=(
      --revoke-confirmation "${MINOS_AWS_TEST_REVOKE_CONFIRM:-}"
      --production-confirmation "$MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM"
    )
  fi
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    "${arguments[@]}"
}

watcher_verify_teardown() {
  local bucket="$1"
  local role_name="$2"
  local run_id="$3"
  local role_id="$4"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    verify-teardown \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    --bucket "$bucket" \
    --role-name "$role_name" \
    --role-id "$role_id" \
    --run-id "$run_id" \
    --operator-arn "$WATCHER_OPERATOR_ARN" \
    --state-json "$WATCHER_ARTIFACT_DIR/destroy-state-before.json"
}

watcher_require_empty_setup_state() {
  if [[ ! -e "$WATCHER_STATE_PATH" ]]; then
    return
  fi
  if [[ ! -s "$WATCHER_STATE_PATH" ]]; then
    rm -- "$WATCHER_STATE_PATH"
    return
  fi
  if ! watcher_terraform show -json "$WATCHER_STATE_PATH" \
    >"$WATCHER_ARTIFACT_DIR/setup-state-before.json"; then
    watcher_die "setup state is unreadable; partial apply recovery is not supported"
  fi
  if ! timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    validate-empty-state "$WATCHER_ARTIFACT_DIR/setup-state-before.json"; then
    watcher_die "setup requires empty local state; partial apply recovery is not supported"
  fi
}

watcher_require_create_targets_absent() {
  local run_id="$1"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python "$WATCHER_ACCEPTANCE_GUARD" \
    verify-create-targets-absent \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    --run-id "$run_id" \
    --operator-arn "$WATCHER_OPERATOR_ARN"
}

watcher_open_confirmed_plan() {
  local plan_path="$1"
  local expected_confirmation="$2"
  local actual_confirmation="$3"
  [[ -f "$plan_path" && ! -L "$plan_path" ]] || watcher_die \
    "saved Terraform plan must be a regular non-symlink file"
  chmod 0400 "$plan_path"
  exec 9<"$plan_path"
  [[ "$plan_path" -ef "/proc/$$/fd/9" ]] || watcher_die \
    "saved Terraform plan was replaced before apply"
  local descriptor_digest
  descriptor_digest="$(watcher_file_sha256 "/proc/$$/fd/9")"
  [[ "$actual_confirmation" == "$expected_confirmation:$descriptor_digest" ]] || watcher_die \
    "plan confirmation does not match the exact opened plan"
}

watcher_apply_opened_plan() {
  local plan_path="$1"
  local state_path="${2:-}"
  [[ -r "/proc/$$/fd/9" ]] || watcher_die \
    "confirmed Terraform plan descriptor is not open"
  [[ "$plan_path" -ef "/proc/$$/fd/9" ]] || watcher_die \
    "saved Terraform plan changed immediately before apply"
  local apply_arguments=(apply)
  if [[ -n "$state_path" ]]; then
    apply_arguments+=(-state="$state_path")
  fi
  apply_arguments+=("/proc/self/fd/9")
  watcher_terraform "${apply_arguments[@]}"
  exec 9<&-
}

watcher_apply_immutable_plan() {
  local plan_path="$1"
  local expected_confirmation="$2"
  local actual_confirmation="$3"
  local state_path="${4:-}"
  watcher_open_confirmed_plan \
    "$plan_path" "$expected_confirmation" "$actual_confirmation"
  watcher_apply_opened_plan "$plan_path" "$state_path"
}
