#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=aws-watcher-common.sh
source "$SCRIPT_DIR/aws-watcher-common.sh"
WATCHER_TERRAFORM_ROOT="$WATCHER_ROOT/infra/terraform/watcher-operators"
readonly OPERATOR_ROLE_SUFFIX="AWSReservedSSO_MinosAdministrator_99fe77395d256089"
readonly STATE_BUCKET="minos-health-tfstate-$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID-eu-central-1"
readonly STATE_REGION="eu-central-1"
readonly ARTIFACT_DIR="$WATCHER_ROOT/artifacts/aws-watcher-operators"

if [[ "${1:-}" == "--help" ]]; then
  printf '%s\n' \
    'Plan the standalone IAM operator bootstrap using the reviewed SSO role.' \
    'Required: AWS_PROFILE=minos-health-bootstrap' \
    'Apply only after review with:' \
    '  MINOS_AWS_OPERATORS_APPLY_CONFIRM=APPLY-OPERATORS:218375352108:<saved-plan-sha256>'
  exit 0
fi
[[ $# -eq 0 ]] || watcher_die "operator bootstrap takes no arguments"
watcher_require_command aws
watcher_require_command terraform
watcher_require_command timeout
watcher_require_command python3
watcher_require_command uv
[[ "${AWS_PROFILE:-}" == "minos-health-bootstrap" ]] || watcher_die \
  "only the reviewed SSO bootstrap profile may manage the operator foundation"

for credential_name in \
  AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN \
  AWS_WEB_IDENTITY_TOKEN_FILE AWS_CONTAINER_CREDENTIALS_RELATIVE_URI \
  AWS_CONTAINER_CREDENTIALS_FULL_URI; do
  [[ -z "${!credential_name:-}" ]] || watcher_die \
    "$credential_name must be unset; use the reviewed SSO profile"
done

umask 077
mkdir -p "$ARTIFACT_DIR"
export TF_DATA_DIR="$ARTIFACT_DIR/terraform-data"
export TF_IN_AUTOMATION=1
export TF_INPUT=0
export AWS_PAGER=""
readonly WATCHER_TIMEOUT_SECONDS=300

principal="$(timeout "$WATCHER_TIMEOUT_SECONDS" aws --profile "$AWS_PROFILE" \
  --region "$STATE_REGION" sts get-caller-identity --query Arn --output text)"
[[ "$principal" =~ ^arn:aws:sts::${MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID}:assumed-role/${OPERATOR_ROLE_SUFFIX}/[^/]+$ ]] || watcher_die \
  "operator bootstrap must be the reviewed Identity Center administrator session"

bucket_region="$(timeout "$WATCHER_TIMEOUT_SECONDS" aws --profile "$AWS_PROFILE" \
  --region "$STATE_REGION" s3api head-bucket --bucket "$STATE_BUCKET" \
  --expected-bucket-owner "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
  --query BucketRegion --output text)"
[[ "$bucket_region" == "$STATE_REGION" ]] || watcher_die \
  "bootstrap state bucket owner or region is invalid"

watcher_terraform init -reconfigure -lockfile=readonly \
  -backend-config="bucket=$STATE_BUCKET" \
  -backend-config="region=$STATE_REGION" \
  -backend-config="allowed_account_ids=[\"$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID\"]"
watcher_terraform validate

plan_path="$ARTIFACT_DIR/operator.tfplan"
plan_json_path="$ARTIFACT_DIR/operator-plan.json"
if [[ -z "${MINOS_AWS_OPERATORS_APPLY_CONFIRM:-}" ]]; then
  [[ ! -e "$plan_path" && ! -L "$plan_path" ]] || watcher_die \
    "a saved operator plan already exists; review and explicitly retire it before replanning"
  watcher_terraform plan -out="$plan_path"
  watcher_terraform show -json "$plan_path" >"$plan_json_path"
  python3 "$SCRIPT_DIR/aws_watcher_operator_plan_guard.py" "$plan_json_path"
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python -m \
    scripts.aws_watcher_operator_simulate "$plan_json_path"
  chmod 0400 "$plan_path"
  plan_digest="$(watcher_file_sha256 "$plan_path")"
  printf '%s\n' \
    "Review $plan_json_path and its SHA-256 $plan_digest. Nothing was applied." \
    "To approve exactly these bytes: MINOS_AWS_OPERATORS_APPLY_CONFIRM=APPLY-OPERATORS:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID:$plan_digest"
  exit 0
fi

[[ -f "$plan_path" ]] || watcher_die "saved operator plan is missing"
watcher_terraform show -json "$plan_path" >"$plan_json_path"
python3 "$SCRIPT_DIR/aws_watcher_operator_plan_guard.py" "$plan_json_path"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python -m \
  scripts.aws_watcher_operator_simulate "$plan_json_path"
watcher_open_confirmed_plan \
  "$plan_path" \
  "APPLY-OPERATORS:$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" \
  "$MINOS_AWS_OPERATORS_APPLY_CONFIRM"
plan_mode="$(python3 "$SCRIPT_DIR/aws_watcher_operator_plan_guard.py" "$plan_json_path" --mode)"
if [[ "$plan_mode" == "bootstrap" ]]; then
  timeout "$WATCHER_TIMEOUT_SECONDS" uv run python -m \
    scripts.aws_watcher_operator_preflight
else
  [[ "$plan_mode" == "update" ]] || watcher_die "operator plan mode is unknown"
fi
watcher_apply_opened_plan "$plan_path"

[[ "$(watcher_terraform output -raw aws_account_id)" == "$MINOS_AWS_KNOWN_PRODUCTION_ACCOUNT_ID" ]] || watcher_die \
  "applied operator foundation belongs to another account"
[[ "$(watcher_terraform output -raw acceptance_operator_role_arn)" == "$MINOS_AWS_EXPECTED_ACCEPTANCE_OPERATOR_ARN" ]] || watcher_die \
  "applied acceptance operator ARN is incorrect"
[[ "$(watcher_terraform output -raw acceptance_watcher_boundary_arn)" == "$MINOS_AWS_EXPECTED_PERMISSIONS_BOUNDARY_ARN" ]] || watcher_die \
  "applied acceptance watcher boundary ARN is incorrect"
timeout "$WATCHER_TIMEOUT_SECONDS" uv run python -m \
  scripts.aws_watcher_operator_live_guard "$plan_json_path"
rm -f -- "$plan_path"
printf 'Applied the approved operator foundation; verify IAM denial tests before watcher apply.\n'
