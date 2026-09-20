#!/usr/bin/env bash

set -euo pipefail

PHASE0_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE0_TERRAFORM_ROOT="$PHASE0_ROOT/infra/terraform/test-phase0"
readonly MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID="218375352108"

phase0_die() {
  printf 'error: %s\n' "$1" >&2
  exit 2
}

phase0_require_command() {
  command -v "$1" >/dev/null 2>&1 || phase0_die "required command is missing: $1"
}

phase0_require_environment() {
  [[ "${MINOS_AWS_ACCEPTANCE:-}" == "1" ]] || phase0_die \
    "set MINOS_AWS_ACCEPTANCE=1 to opt in"
  [[ -n "${MINOS_AWS_TEST_PROFILE:-}" ]] || phase0_die \
    "MINOS_AWS_TEST_PROFILE is required"
  [[ "$MINOS_AWS_TEST_PROFILE" != "default" ]] || phase0_die \
    "the default AWS profile is not accepted"
  [[ "${AWS_PROFILE:-}" == "$MINOS_AWS_TEST_PROFILE" ]] || phase0_die \
    "AWS_PROFILE must exactly match MINOS_AWS_TEST_PROFILE"
  [[ "${MINOS_AWS_TEST_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ ]] || phase0_die \
    "MINOS_AWS_TEST_ACCOUNT_ID must contain twelve digits"
  [[ "${MINOS_AWS_PRODUCTION_ACCOUNT_ID:-}" =~ ^[0-9]{12}$ ]] || phase0_die \
    "MINOS_AWS_PRODUCTION_ACCOUNT_ID must contain twelve digits"
  [[ "$MINOS_AWS_PRODUCTION_ACCOUNT_ID" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
    "MINOS_AWS_PRODUCTION_ACCOUNT_ID must match the repository-owned production account"
  [[ "$MINOS_AWS_TEST_ACCOUNT_ID" != "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
    "the known production AWS account cannot be used for acceptance"
  [[ "$MINOS_AWS_TEST_ACCOUNT_ID" != "$MINOS_AWS_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
    "the test and production AWS accounts must differ"
  [[ "${MINOS_AWS_TEST_REGION:-}" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || phase0_die \
    "MINOS_AWS_TEST_REGION is invalid"

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

  PHASE0_ARTIFACT_DIR="$PHASE0_ROOT/artifacts/aws-phase0/$MINOS_AWS_TEST_ACCOUNT_ID"
  PHASE0_TF_DATA_DIR="$PHASE0_ARTIFACT_DIR/terraform-data"
  PHASE0_STATE_PATH="$PHASE0_ARTIFACT_DIR/terraform.tfstate"
  PHASE0_RUNTIME_ENV="$PHASE0_ARTIFACT_DIR/runtime.env"
  export PHASE0_ARTIFACT_DIR PHASE0_RUNTIME_ENV PHASE0_STATE_PATH
  export TF_DATA_DIR="$PHASE0_TF_DATA_DIR"
  export TF_IN_AUTOMATION=1
  export TF_INPUT=0
  export AWS_PAGER=""
  export TF_VAR_aws_region="$MINOS_AWS_TEST_REGION"
  export TF_VAR_expected_test_account_id="$MINOS_AWS_TEST_ACCOUNT_ID"
  export TF_VAR_production_account_id="$MINOS_AWS_PRODUCTION_ACCOUNT_ID"
}

phase0_capture_identity() {
  local destination="$1"
  timeout "$PHASE0_TIMEOUT_SECONDS" aws \
    --profile "$MINOS_AWS_TEST_PROFILE" \
    --region "$MINOS_AWS_TEST_REGION" \
    sts get-caller-identity --output json >"$destination"

  local actual_account
  actual_account="$(python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["Account"])' \
    "$destination")"
  [[ "$actual_account" == "$MINOS_AWS_TEST_ACCOUNT_ID" ]] || phase0_die \
    "STS returned account $actual_account, expected $MINOS_AWS_TEST_ACCOUNT_ID"
  [[ "$actual_account" != "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
    "STS resolved to the known production account"
  [[ "$actual_account" != "$MINOS_AWS_PRODUCTION_ACCOUNT_ID" ]] || phase0_die \
    "STS resolved to the configured production account"

  PHASE0_OPERATOR_ARN="$(python3 "$PHASE0_ROOT/scripts/aws-phase0-principal.py" "$destination")"
  export PHASE0_OPERATOR_ARN
  export TF_VAR_operator_principal_arn="$PHASE0_OPERATOR_ARN"
  printf 'Verified AWS account %s as %s\n' "$actual_account" "$PHASE0_OPERATOR_ARN"
}

phase0_terraform() {
  timeout "$PHASE0_TIMEOUT_SECONDS" terraform \
    -chdir="$PHASE0_TERRAFORM_ROOT" "$@"
}

phase0_file_sha256() {
  python3 -c \
    'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' \
    "$1"
}

phase0_state_resource_value() {
  local state_json="$1"
  local resource_address="$2"
  local attribute="$3"
  python3 -c '
import json
import sys

document = json.load(open(sys.argv[1], encoding="utf-8"))
pending = [document.get("values", {}).get("root_module", {})]
while pending:
    module = pending.pop()
    pending.extend(module.get("child_modules", []))
    for resource in module.get("resources", []):
        if resource.get("address") == sys.argv[2]:
            value = resource.get("values", {}).get(sys.argv[3])
            if isinstance(value, str):
                print(value)
            raise SystemExit(0)
' "$state_json" "$resource_address" "$attribute"
}

phase0_load_runtime() {
  [[ -f "$PHASE0_RUNTIME_ENV" ]] || phase0_die \
    "runtime outputs are missing; run the setup script first"
  # This file is generated exclusively from validated Terraform outputs.
  # shellcheck disable=SC1090
  source "$PHASE0_RUNTIME_ENV"
  [[ "$MINOS_AWS_TEST_ACCOUNT_ID" == "${MINOS_AWS_TEST_ACCOUNT_ID_APPLIED:-}" ]] || phase0_die \
    "runtime outputs belong to another AWS account"
  [[ "$MINOS_AWS_TEST_REGION" == "${MINOS_AWS_TEST_REGION_APPLIED:-}" ]] || phase0_die \
    "runtime outputs belong to another AWS region"
  [[ "$PHASE0_OPERATOR_ARN" == "${MINOS_AWS_TEST_OPERATOR_ARN:-}" ]] || phase0_die \
    "runtime outputs were applied by another principal"
  export MINOS_AWS_TEST_BUCKET MINOS_AWS_TEST_OPERATOR_ARN
  export MINOS_AWS_TEST_ROLE_ARN MINOS_AWS_TEST_ROLE_NAME MINOS_AWS_TEST_RUN_ID
}
