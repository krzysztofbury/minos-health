# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnannotatedClassAttribute=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnusedCallResult=false, reportUnusedParameter=false

import hashlib
import os
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from scripts.aws_watcher_acceptance_guard import (
    ACCOUNT_ID,
    EXPECTED_OPERATOR_ARN,
    EXPECTED_PERMISSIONS_BOUNDARY_ARN,
    MANAGED_RESOURCE_ADDRESSES,
    PRODUCTION_CONFIRMATION,
    REVOKED_RESOURCE_ADDRESSES,
    GuardError,
    _expected_bucket_policy,
    _expected_trust_policy,
    _expected_watcher_policy,
    empty_bucket_bounded,
    expected_bucket_name,
    expected_role_name,
    initialize_teardown_runtime,
    load_object_manifest,
    load_runtime_document,
    runtime_document,
    validate_apply_plan,
    validate_aws_resources,
    validate_create_targets_absent,
    validate_destroy_plan,
    validate_empty_state,
    validate_partial_teardown_resources,
    validate_preflight_environment,
    validate_state,
    validate_teardown_state,
    write_json_atomic,
)

RUN_ID = "0123456789abcdef"
REGION = "eu-central-1"
OPERATOR_ARN = EXPECTED_OPERATOR_ARN
ROLE_ID = "AROA0123456789ABCDEFG"


def _environment() -> dict[str, str]:
    role_name = expected_role_name(RUN_ID)
    return {
        "MINOS_AWS_ACCEPTANCE": "1",
        "AWS_PROFILE": "minos-acceptance",
        "MINOS_AWS_TEST_PROFILE": "minos-acceptance",
        "MINOS_AWS_TEST_ACCOUNT_ID": ACCOUNT_ID,
        "MINOS_AWS_PRODUCTION_ACCOUNT_ID": ACCOUNT_ID,
        "MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM": PRODUCTION_CONFIRMATION,
        "MINOS_AWS_TEST_REGION": REGION,
        "MINOS_AWS_TEST_BUCKET": expected_bucket_name(RUN_ID),
        "MINOS_AWS_TEST_ROLE_ARN": f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}",
        "MINOS_AWS_TEST_ROLE_ID": ROLE_ID,
        "MINOS_AWS_TEST_ROLE_NAME": role_name,
        "MINOS_AWS_TEST_RUN_ID": RUN_ID,
        "MINOS_AWS_TEST_OPERATOR_ARN": OPERATOR_ARN,
        "MINOS_AWS_TEST_PERMISSIONS_BOUNDARY_ARN": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        "MINOS_AWS_TEST_OBJECT_MANIFEST": "/tmp/acceptance-objects.json",
    }


def _tags() -> dict[str, str]:
    return {
        "Disposable": "true",
        "Environment": "acceptance",
        "ManagedBy": "Terraform",
        "Project": "minos-health",
        "TestHarness": "watcher-acceptance",
        "TestRunId": RUN_ID,
    }


def _state() -> dict[str, Any]:
    bucket = expected_bucket_name(RUN_ID)
    role_name = expected_role_name(RUN_ID)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    resources: list[dict[str, Any]] = [
        {
            "address": "terraform_data.run_guard",
            "mode": "managed",
            "values": {"input": RUN_ID},
        },
        {
            "address": "terraform_data.account_guard",
            "mode": "managed",
            "values": {"input": ACCOUNT_ID},
        },
        {
            "address": "aws_s3_bucket.snapshots",
            "mode": "managed",
            "values": {
                "bucket": bucket,
                "id": bucket,
                "tags": _tags(),
                "tags_all": _tags(),
            },
        },
        {
            "address": "aws_iam_role.watcher",
            "mode": "managed",
            "values": {
                "name": role_name,
                "arn": role_arn,
                "permissions_boundary": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
                "inline_policy": [],
                "managed_policy_arns": [],
                "unique_id": ROLE_ID,
                "tags": _tags(),
                "tags_all": _tags(),
            },
        },
        {
            "address": "aws_iam_role_policy.watcher_archive",
            "mode": "managed",
            "values": {"name": f"{role_name}-archive", "role": role_name},
        },
    ]
    for address in sorted(
        MANAGED_RESOURCE_ADDRESSES
        - {
            "terraform_data.run_guard",
            "terraform_data.account_guard",
            "aws_s3_bucket.snapshots",
            "aws_iam_role.watcher",
            "aws_iam_role_policy.watcher_archive",
        }
    ):
        resources.append(
            {"address": address, "mode": "managed", "values": {"bucket": bucket}}
        )
    outputs = {
        "aws_account_id": {"value": ACCOUNT_ID},
        "aws_region": {"value": REGION},
        "bucket_name": {"value": bucket},
        "operator_principal_arn": {"value": OPERATOR_ARN},
        "test_run_id": {"value": RUN_ID},
        "watcher_role_arn": {"value": role_arn},
        "watcher_role_permissions_boundary_arn": {
            "value": EXPECTED_PERMISSIONS_BOUNDARY_ARN
        },
        "watcher_role_unique_id": {"value": ROLE_ID},
        "watcher_role_name": {"value": role_name},
    }
    return {"values": {"outputs": outputs, "root_module": {"resources": resources}}}


def _resource(document: dict[str, Any], address: str) -> dict[str, Any]:
    resources = document["values"]["root_module"]["resources"]
    return next(resource for resource in resources if resource["address"] == address)


def _revoked_state() -> dict[str, Any]:
    state = _state()
    resources = state["values"]["root_module"]["resources"]
    state["values"]["root_module"]["resources"] = [
        resource
        for resource in resources
        if resource["address"] != "aws_iam_role_policy.watcher_archive"
    ]
    return state


def test_pytest_preflight_accepts_only_the_exact_same_account_opt_in() -> None:
    result = validate_preflight_environment(_environment())

    assert result[1] == ACCOUNT_ID
    assert result[3] == expected_bucket_name(RUN_ID)
    assert result[5] == ROLE_ID
    assert result[6] == expected_role_name(RUN_ID)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MINOS_AWS_TEST_ACCOUNT_ID", "111111111111"),
        ("MINOS_AWS_PRODUCTION_ACCOUNT_ID", "111111111111"),
        ("MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM", "ACCEPT-DISPOSABLE-RESOURCES"),
        ("MINOS_AWS_ACCEPTANCE_OPERATOR_ARN", f"arn:aws:iam::{ACCOUNT_ID}:role/other"),
        ("MINOS_AWS_TEST_BUCKET", "minos-health-watcher-acceptance-wrong"),
        ("MINOS_AWS_TEST_ROLE_NAME", "minos-health-watcher-acceptance-wrong"),
        ("MINOS_AWS_TEST_ROLE_ID", "wrong"),
        ("MINOS_AWS_TEST_OPERATOR_ARN", "arn:aws:iam::111111111111:role/minos"),
    ],
)
def test_pytest_preflight_rejects_mismatched_identity(name: str, value: str) -> None:
    environment = _environment()
    environment[name] = value

    with pytest.raises(GuardError):
        validate_preflight_environment(environment)


def test_pytest_preflight_rejects_ambient_credentials() -> None:
    environment = _environment()
    environment["AWS_ACCESS_KEY_ID"] = "not-a-real-key"

    with pytest.raises(GuardError, match="must be unset"):
        validate_preflight_environment(environment)


def test_runtime_json_rejects_unknown_fields_without_execution(tmp_path: Path) -> None:
    marker = tmp_path / "must-not-exist"
    runtime_path = tmp_path / "runtime.json"
    document = runtime_document(
        account_id=ACCOUNT_ID,
        region=REGION,
        bucket=expected_bucket_name(RUN_ID),
        operator_arn=OPERATOR_ARN,
        permissions_boundary_arn=EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/{expected_role_name(RUN_ID)}",
        role_id=ROLE_ID,
        role_name=expected_role_name(RUN_ID),
        run_id=RUN_ID,
        object_manifest=str(tmp_path / "objects.json"),
    )
    document["shell"] = f"touch {marker}"
    write_json_atomic(runtime_path, document)

    with pytest.raises(GuardError, match="schema"):
        load_runtime_document(runtime_path)
    assert not marker.exists()


def test_runtime_json_requires_fixed_permissions_boundary(tmp_path: Path) -> None:
    runtime_path = tmp_path / "runtime.json"
    document = runtime_document(
        account_id=ACCOUNT_ID,
        region=REGION,
        bucket=expected_bucket_name(RUN_ID),
        operator_arn=OPERATOR_ARN,
        permissions_boundary_arn=EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/{expected_role_name(RUN_ID)}",
        role_id=ROLE_ID,
        role_name=expected_role_name(RUN_ID),
        run_id=RUN_ID,
        object_manifest=str(tmp_path / "objects.json"),
    )
    document["permissions_boundary_arn"] = "arn:aws:iam::218375352108:policy/wrong"
    write_json_atomic(runtime_path, document)

    with pytest.raises(GuardError, match="permissions boundary"):
        load_runtime_document(runtime_path)


def test_teardown_runtime_recovery_requires_full_state_and_preserves_manifest(
    tmp_path: Path,
) -> None:
    runtime_path = tmp_path / "runtime.json"
    manifest_path = tmp_path / "keys.json"
    manifest_identity = ("blobs/sha256/" + "a" * 64, "version-1", "version")
    manifest = {
        "schema_version": 2,
        "account_id": ACCOUNT_ID,
        "bucket": expected_bucket_name(RUN_ID),
        "run_id": RUN_ID,
        "objects": [
            {
                "key": manifest_identity[0],
                "version_id": manifest_identity[1],
                "kind": manifest_identity[2],
            }
        ],
    }
    write_json_atomic(manifest_path, manifest)

    initialize_teardown_runtime(
        _state(),
        runtime_path=runtime_path,
        manifest_path=manifest_path,
        region=REGION,
        operator_arn=OPERATOR_ARN,
    )

    assert load_object_manifest(
        manifest_path,
        account_id=ACCOUNT_ID,
        bucket=expected_bucket_name(RUN_ID),
        run_id=RUN_ID,
    ) == {manifest_identity}
    assert load_runtime_document(runtime_path)["object_manifest"] == str(manifest_path)

    runtime_path.unlink()
    with pytest.raises(GuardError, match="resource allowlist"):
        initialize_teardown_runtime(
            _revoked_state(),
            runtime_path=runtime_path,
            manifest_path=manifest_path,
            region=REGION,
            operator_arn=OPERATOR_ARN,
        )
    assert not runtime_path.exists()


def test_shell_preflight_accepts_only_exact_same_account_confirmation() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AWS_") and not key.startswith("MINOS_AWS_")
    }
    environment.update(
        {
            "MINOS_AWS_ACCEPTANCE": "1",
            "AWS_PROFILE": "minos-acceptance",
            "MINOS_AWS_TEST_PROFILE": "minos-acceptance",
            "MINOS_AWS_TEST_ACCOUNT_ID": ACCOUNT_ID,
            "MINOS_AWS_PRODUCTION_ACCOUNT_ID": ACCOUNT_ID,
            "MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM": PRODUCTION_CONFIRMATION,
            "MINOS_AWS_ACCEPTANCE_OPERATOR_ARN": OPERATOR_ARN,
            "MINOS_AWS_TEST_REGION": REGION,
        }
    )
    command = "source scripts/aws-watcher-common.sh; watcher_require_environment"

    accepted = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert accepted.returncode == 0, accepted.stderr

    environment["MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM"] = "wrong"
    rejected = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM must equal" in rejected.stderr


def test_shell_applies_the_opened_plan_descriptor_and_rejects_existing_state(
    tmp_path: Path,
) -> None:
    plan = tmp_path / "apply.tfplan"
    applied = tmp_path / "applied.tfplan"
    state = tmp_path / "terraform.tfstate"
    plan.write_bytes(b"immutable-plan")
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    environment = {
        **os.environ,
        "PLAN": str(plan),
        "APPLIED": str(applied),
        "STATE": str(state),
        "CONFIRM": f"APPLY:{ACCOUNT_ID}:{digest}",
    }
    command = """
source scripts/aws-watcher-common.sh
WATCHER_TIMEOUT_SECONDS=30
WATCHER_STATE_PATH="$STATE"
watcher_terraform() {
  local plan_argument="${!#}"
  cp "$plan_argument" "$APPLIED"
}
watcher_apply_immutable_plan "$PLAN" "APPLY:218375352108" "$CONFIRM"
"""

    result = subprocess.run(
        ["bash", "-c", command],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert applied.read_bytes() == b"immutable-plan"

    state.write_text("partial-state")
    environment["ARTIFACTS"] = str(tmp_path)
    rejected = subprocess.run(
        [
            "bash",
            "-c",
            """
source scripts/aws-watcher-common.sh
WATCHER_STATE_PATH="$STATE"
WATCHER_ARTIFACT_DIR="$ARTIFACTS"
WATCHER_TIMEOUT_SECONDS=30
watcher_terraform() { return 1; }
watcher_require_empty_setup_state
""",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "partial apply recovery is not supported" in rejected.stderr


def test_destroy_plan_is_confirmed_before_cleanup_and_stays_open(
    tmp_path: Path,
) -> None:
    plan = tmp_path / "destroy.tfplan"
    cleanup = tmp_path / "cleanup"
    applied = tmp_path / "applied"
    plan.write_bytes(b"reviewed-destroy-plan")
    digest = hashlib.sha256(plan.read_bytes()).hexdigest()
    environment = {
        **os.environ,
        "PLAN": str(plan),
        "CLEANUP": str(cleanup),
        "APPLIED": str(applied),
        "CONFIRM": f"DESTROY:{ACCOUNT_ID}:{digest}",
    }

    wrong = subprocess.run(
        [
            "bash",
            "-c",
            """
source scripts/aws-watcher-common.sh
watcher_open_confirmed_plan "$PLAN" "DESTROY:218375352108" "wrong"
touch "$CLEANUP"
""",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert wrong.returncode == 2
    assert not cleanup.exists()

    replaced = subprocess.run(
        [
            "bash",
            "-c",
            """
source scripts/aws-watcher-common.sh
watcher_open_confirmed_plan "$PLAN" "DESTROY:218375352108" "$CONFIRM"
touch "$CLEANUP"
printf replacement >"$PLAN.new"
mv "$PLAN.new" "$PLAN"
watcher_terraform() { touch "$APPLIED"; }
watcher_apply_opened_plan "$PLAN"
""",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert replaced.returncode == 2
    assert cleanup.exists()
    assert not applied.exists()


def test_empty_state_guard_accepts_only_state_without_managed_resources() -> None:
    validate_empty_state({"values": {"root_module": {"resources": []}}})

    with pytest.raises(GuardError, match="setup state is not empty"):
        validate_empty_state(_revoked_state())


def test_state_guard_accepts_only_exact_harness_state() -> None:
    assert validate_state(_state(), region=REGION, operator_arn=OPERATOR_ARN) == (
        RUN_ID,
        expected_bucket_name(RUN_ID),
        expected_role_name(RUN_ID),
        ROLE_ID,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "extra-resource",
        "bucket",
        "tag",
        "extra-tag",
        "role-id",
        "boundary",
        "missing-boundary",
        "boundary-output",
        "inline-policy",
        "managed-policy",
        "output",
    ],
)
def test_state_guard_rejects_mismatched_state(mutation: str) -> None:
    state = _state()
    if mutation == "extra-resource":
        state["values"]["root_module"]["resources"].append(
            {"address": "aws_s3_bucket.not_harness", "values": {}}
        )
    elif mutation == "bucket":
        _resource(state, "aws_s3_bucket.snapshots")["values"]["id"] = "wrong"
    elif mutation == "tag":
        _resource(state, "aws_iam_role.watcher")["values"]["tags_all"]["TestRunId"] = (
            "fedcba9876543210"
        )
    elif mutation == "extra-tag":
        _resource(state, "aws_iam_role.watcher")["values"]["tags"]["Extra"] = "x"
    elif mutation == "role-id":
        _resource(state, "aws_iam_role.watcher")["values"]["unique_id"] = (
            "AROA9999999999ABCDEFG"
        )
    elif mutation == "boundary":
        _resource(state, "aws_iam_role.watcher")["values"]["permissions_boundary"] = (
            "arn:aws:iam::218375352108:policy/wrong"
        )
    elif mutation == "missing-boundary":
        del _resource(state, "aws_iam_role.watcher")["values"]["permissions_boundary"]
    elif mutation == "boundary-output":
        state["values"]["outputs"]["watcher_role_permissions_boundary_arn"]["value"] = (
            "arn:aws:iam::218375352108:policy/wrong"
        )
    elif mutation == "inline-policy":
        _resource(state, "aws_iam_role.watcher")["values"]["inline_policy"] = [
            {"name": "unexpected"}
        ]
    elif mutation == "managed-policy":
        _resource(state, "aws_iam_role.watcher")["values"]["managed_policy_arns"] = [
            "arn:aws:iam::aws:policy/AdministratorAccess"
        ]
    else:
        state["values"]["outputs"]["aws_account_id"]["value"] = "111111111111"

    with pytest.raises(GuardError):
        validate_state(state, region=REGION, operator_arn=OPERATOR_ARN)


def test_teardown_state_accepts_only_revoked_allowlisted_partial_states() -> None:
    revoked = _revoked_state()
    assert (
        validate_teardown_state(
            revoked,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            expected_run_id=RUN_ID,
            expected_role_id=ROLE_ID,
        )[0]
        == "revoked"
    )

    resources = revoked["values"]["root_module"]["resources"]
    revoked["values"]["root_module"]["resources"] = [
        resource
        for resource in resources
        if resource["address"] != "aws_s3_bucket_public_access_block.snapshots"
    ]
    assert (
        validate_teardown_state(
            revoked,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            expected_run_id=RUN_ID,
            expected_role_id=ROLE_ID,
        )[0]
        == "partial"
    )

    partial_addresses = {
        resource["address"]
        for resource in revoked["values"]["root_module"]["resources"]
    }
    partial_plan = {
        "prior_state": deepcopy(revoked),
        "resource_changes": [
            {"address": address, "change": {"actions": ["delete"]}}
            for address in sorted(partial_addresses)
        ],
    }
    validate_destroy_plan(
        partial_plan,
        region=REGION,
        operator_arn=OPERATOR_ARN,
        role_id=ROLE_ID,
        current_state=revoked,
    )

    revoked["values"]["root_module"]["resources"].append(
        {"address": "aws_s3_bucket.unrelated", "values": {}}
    )
    with pytest.raises(GuardError, match="unapproved"):
        validate_teardown_state(
            revoked,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            expected_run_id=RUN_ID,
            expected_role_id=ROLE_ID,
        )


def test_partial_teardown_requires_state_omissions_to_be_absent_in_aws() -> None:
    state = _revoked_state()
    resources = state["values"]["root_module"]["resources"]
    state["values"]["root_module"]["resources"] = [
        resource
        for resource in resources
        if resource["address"]
        in {"terraform_data.run_guard", "terraform_data.account_guard"}
    ]
    s3 = FakeS3([])
    s3.bucket_exists = False
    iam = FakeIam(_aws_tags())
    iam.role_exists = False

    assert validate_partial_teardown_resources(
        FakeSts(),
        s3,
        iam,
        state,
        run_id=RUN_ID,
        role_id=ROLE_ID,
        operator_arn=OPERATOR_ARN,
        region=REGION,
    ) == ("partial", False, False)

    s3.bucket_exists = True
    with pytest.raises(GuardError, match="omitted a live acceptance bucket"):
        validate_partial_teardown_resources(
            FakeSts(),
            s3,
            iam,
            state,
            run_id=RUN_ID,
            role_id=ROLE_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    s3.bucket_exists = False
    iam.role_exists = True
    with pytest.raises(GuardError, match="omitted a live acceptance role"):
        validate_partial_teardown_resources(
            FakeSts(),
            s3,
            iam,
            state,
            run_id=RUN_ID,
            role_id=ROLE_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )


def test_destroy_plan_guard_requires_exact_delete_only_allowlist() -> None:
    plan: dict[str, Any] = {
        "prior_state": _revoked_state(),
        "resource_changes": [
            {"address": address, "change": {"actions": ["delete"]}}
            for address in sorted(REVOKED_RESOURCE_ADDRESSES)
        ],
    }

    validate_destroy_plan(
        plan, region=REGION, operator_arn=OPERATOR_ARN, role_id=ROLE_ID
    )

    changed_plan = deepcopy(plan)
    changed_plan["resource_changes"][0]["change"]["actions"] = ["delete", "create"]
    with pytest.raises(GuardError, match="not exactly delete"):
        validate_destroy_plan(
            changed_plan,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            role_id=ROLE_ID,
        )

    changed_plan = deepcopy(plan)
    changed_plan["resource_changes"].append(
        {"address": "aws_s3_bucket.unrelated", "change": {"actions": ["delete"]}}
    )
    with pytest.raises(GuardError, match="allowlist mismatch"):
        validate_destroy_plan(
            changed_plan,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            role_id=ROLE_ID,
        )

    changed_plan = deepcopy(plan)
    _resource(changed_plan["prior_state"], "aws_s3_bucket.snapshots")["values"][
        "id"
    ] = "wrong"
    with pytest.raises(GuardError, match="state identity"):
        validate_destroy_plan(
            changed_plan,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            role_id=ROLE_ID,
        )

    current_state = _revoked_state()
    _resource(current_state, "aws_s3_bucket.snapshots")["values"]["id"] = "wrong"
    with pytest.raises(GuardError, match="state identity"):
        validate_destroy_plan(
            plan,
            region=REGION,
            operator_arn=OPERATOR_ARN,
            role_id=ROLE_ID,
            current_state=current_state,
        )


def _planned_lifecycle_rules() -> list[dict[str, object]]:
    expirations = {
        "run-recovery": ("control-recovery/run/", 30),
        "daily-recovery": ("control-recovery/daily/", 31),
        "weekly-recovery": ("control-recovery/weekly/", 91),
        "monthly-recovery": ("control-recovery/monthly/", 400),
        "trusted-recovery": ("trusted-recovery/", 400),
        "checksum-probes": ("probes/", 7),
    }
    marker_names = {
        "run-recovery": "remove-run-recovery-delete-markers",
        "daily-recovery": "remove-daily-recovery-delete-markers",
        "weekly-recovery": "remove-weekly-recovery-delete-markers",
        "monthly-recovery": "remove-monthly-recovery-delete-markers",
        "trusted-recovery": "remove-trusted-recovery-delete-markers",
        "checksum-probes": "remove-checksum-probe-delete-markers",
    }
    rules: list[dict[str, object]] = []
    for name, (prefix, days) in expirations.items():
        rules.extend(
            [
                {
                    "id": f"expire-{name}",
                    "status": "Enabled",
                    "filter": [{"prefix": prefix}],
                    "expiration": [{"days": days}],
                    "noncurrent_version_expiration": [{"noncurrent_days": 1}],
                    "transition": [],
                    "noncurrent_version_transition": [],
                },
                {
                    "id": marker_names[name],
                    "status": "Enabled",
                    "filter": [{"prefix": prefix}],
                    "expiration": [{"expired_object_delete_marker": True}],
                    "transition": [],
                    "noncurrent_version_transition": [],
                },
            ]
        )
    rules.append(
        {
            "id": "abort-incomplete-multipart-uploads",
            "status": "Enabled",
            "filter": [{}],
            "abort_incomplete_multipart_upload": [{"days_after_initiation": 7}],
            "transition": [],
            "noncurrent_version_transition": [],
        }
    )
    return rules


def _apply_plan() -> dict[str, Any]:
    bucket = expected_bucket_name(RUN_ID)
    role_name = expected_role_name(RUN_ID)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    after: dict[str, dict[str, Any]] = {
        "terraform_data.account_guard": {"input": ACCOUNT_ID},
        "terraform_data.run_guard": {"input": RUN_ID},
        "aws_s3_bucket.snapshots": {
            "bucket": bucket,
            "force_destroy": False,
            "object_lock_enabled": False,
            "tags": _tags(),
        },
        "aws_iam_role.watcher": {
            "name": role_name,
            "description": "Disposable watcher role for real-AWS acceptance only.",
            "path": "/",
            "max_session_duration": 3600,
            "permissions_boundary": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
            "inline_policy": [],
            "managed_policy_arns": [],
            "tags": _tags(),
            "assume_role_policy": _expected_trust_policy(OPERATOR_ARN),
        },
        "aws_iam_role_policy.watcher_archive": {
            "name": f"{role_name}-archive",
            "role": role_name,
            "policy": _expected_watcher_policy(bucket),
        },
        "aws_s3_bucket_lifecycle_configuration.snapshots": {
            "bucket": bucket,
            "rule": _planned_lifecycle_rules(),
        },
        "aws_s3_bucket_ownership_controls.snapshots": {
            "bucket": bucket,
            "rule": [{"object_ownership": "BucketOwnerEnforced"}],
        },
        "aws_s3_bucket_policy.snapshots": {
            "bucket": bucket,
            "policy": _expected_bucket_policy(bucket, role_arn),
        },
        "aws_s3_bucket_public_access_block.snapshots": {
            "bucket": bucket,
            "block_public_acls": True,
            "block_public_policy": True,
            "ignore_public_acls": True,
            "restrict_public_buckets": True,
        },
        "aws_s3_bucket_server_side_encryption_configuration.snapshots": {
            "bucket": bucket,
            "rule": [
                {
                    "apply_server_side_encryption_by_default": [
                        {"sse_algorithm": "AES256"}
                    ]
                }
            ],
        },
        "aws_s3_bucket_versioning.snapshots": {
            "bucket": bucket,
            "versioning_configuration": [{"status": "Enabled"}],
        },
    }
    expected_outputs = {
        "aws_account_id": ACCOUNT_ID,
        "aws_region": REGION,
        "bucket_name": bucket,
        "operator_principal_arn": OPERATOR_ARN,
        "test_run_id": RUN_ID,
        "watcher_role_arn": role_arn,
        "watcher_role_permissions_boundary_arn": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        "watcher_role_name": role_name,
    }
    output_changes = {
        name: {
            "actions": ["create"],
            "before": None,
            "after": value,
            "after_unknown": False,
        }
        for name, value in expected_outputs.items()
    }
    output_changes["watcher_role_unique_id"] = {
        "actions": ["create"],
        "before": None,
        "after": None,
        "after_unknown": True,
    }
    return {
        "prior_state": {"values": {"root_module": {"resources": []}}},
        "resource_changes": [
            {
                "address": address,
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": values,
                    "after_unknown": {},
                },
            }
            for address, values in sorted(after.items())
        ],
        "output_changes": output_changes,
    }


def _plan_change(plan: dict[str, Any], address: str) -> dict[str, Any]:
    return next(
        entry["change"]
        for entry in plan["resource_changes"]
        if entry["address"] == address
    )


def test_apply_plan_guard_requires_exact_known_create_targets() -> None:
    plan = _apply_plan()
    validate_apply_plan(plan, region=REGION, run_id=RUN_ID)

    changed = deepcopy(plan)
    changed["resource_changes"][0]["change"]["actions"] = ["update"]
    with pytest.raises(GuardError, match="not exactly create"):
        validate_apply_plan(changed, region=REGION, run_id=RUN_ID)

    changed = deepcopy(plan)
    changed["prior_state"] = _state()
    with pytest.raises(GuardError, match="prior state is not empty"):
        validate_apply_plan(changed, region=REGION, run_id=RUN_ID)


@pytest.mark.parametrize(
    "mutation",
    [
        "bucket",
        "role",
        "child-target",
        "tags",
        "trust",
        "policy",
        "output",
        "unknown-target",
        "unknown-transition",
        "object-lock",
        "boundary",
        "missing-boundary",
        "unknown-boundary",
        "boundary-output",
        "inline-policy",
        "managed-policy",
        "unknown-inline-policy",
        "unknown-managed-policy",
        "lifecycle",
        "transition",
    ],
)
def test_apply_plan_guard_rejects_allowlisted_address_target_mutations(
    mutation: str,
) -> None:
    plan = _apply_plan()
    if mutation == "bucket":
        _plan_change(plan, "aws_s3_bucket.snapshots")["after"]["bucket"] = "wrong"
    elif mutation == "role":
        _plan_change(plan, "aws_iam_role.watcher")["after"]["name"] = "wrong"
    elif mutation == "child-target":
        _plan_change(plan, "aws_s3_bucket_versioning.snapshots")["after"]["bucket"] = (
            "wrong"
        )
    elif mutation == "tags":
        _plan_change(plan, "aws_s3_bucket.snapshots")["after"]["tags"]["Unexpected"] = (
            "true"
        )
    elif mutation == "trust":
        _plan_change(plan, "aws_iam_role.watcher")["after"]["assume_role_policy"][
            "Statement"
        ][0]["Condition"]["ArnEquals"]["aws:PrincipalArn"] = "*"
    elif mutation == "policy":
        _plan_change(plan, "aws_iam_role_policy.watcher_archive")["after"]["policy"][
            "Statement"
        ][0]["Action"] = "s3:*"
    elif mutation == "output":
        plan["output_changes"]["aws_region"]["after"] = "us-east-1"
    elif mutation == "unknown-target":
        _plan_change(plan, "aws_s3_bucket.snapshots")["after_unknown"] = {
            "bucket": True
        }
    elif mutation == "unknown-transition":
        _plan_change(plan, "aws_s3_bucket_lifecycle_configuration.snapshots")[
            "after_unknown"
        ] = {"rule": [{"transition": True}]}
    elif mutation == "object-lock":
        _plan_change(plan, "aws_s3_bucket.snapshots")["after"][
            "object_lock_enabled"
        ] = True
    elif mutation == "boundary":
        _plan_change(plan, "aws_iam_role.watcher")["after"]["permissions_boundary"] = (
            "arn:aws:iam::218375352108:policy/wrong"
        )
    elif mutation == "missing-boundary":
        del _plan_change(plan, "aws_iam_role.watcher")["after"]["permissions_boundary"]
    elif mutation == "unknown-boundary":
        _plan_change(plan, "aws_iam_role.watcher")["after_unknown"] = {
            "permissions_boundary": True
        }
    elif mutation == "boundary-output":
        plan["output_changes"]["watcher_role_permissions_boundary_arn"]["after"] = (
            "arn:aws:iam::218375352108:policy/wrong"
        )
    elif mutation == "inline-policy":
        _plan_change(plan, "aws_iam_role.watcher")["after"]["inline_policy"] = [
            {"name": "unexpected"}
        ]
    elif mutation == "managed-policy":
        _plan_change(plan, "aws_iam_role.watcher")["after"]["managed_policy_arns"] = [
            "arn:aws:iam::aws:policy/AdministratorAccess"
        ]
    elif mutation == "unknown-inline-policy":
        _plan_change(plan, "aws_iam_role.watcher")["after_unknown"] = {
            "inline_policy": [{"name": True}]
        }
    elif mutation == "unknown-managed-policy":
        _plan_change(plan, "aws_iam_role.watcher")["after_unknown"] = {
            "managed_policy_arns": [True]
        }
    elif mutation == "lifecycle":
        _plan_change(plan, "aws_s3_bucket_lifecycle_configuration.snapshots")["after"][
            "rule"
        ][0]["expiration"][0]["days"] = 1
    else:
        _plan_change(plan, "aws_s3_bucket_lifecycle_configuration.snapshots")["after"][
            "rule"
        ][0]["transition"] = [{"days": 1, "storage_class": "GLACIER"}]

    with pytest.raises(GuardError):
        validate_apply_plan(plan, region=REGION, run_id=RUN_ID)


class FakeSts:
    def __init__(self, *, account: str = ACCOUNT_ID, arn: str = OPERATOR_ARN) -> None:
        self.account = account
        self.arn = arn

    def get_caller_identity(self) -> Mapping[str, Any]:
        return {"Account": self.account, "Arn": self.arn}


class FakeIam:
    def __init__(self, tags: list[dict[str, str]]) -> None:
        self.tags = tags
        self.role_exists = True
        self.role_error_code: str | None = None
        self.role_id = ROLE_ID
        self.permissions_boundary_arn = EXPECTED_PERMISSIONS_BOUNDARY_ARN
        self.requested_role_names: list[object] = []
        self.policy_names = [f"{expected_role_name(RUN_ID)}-archive"]
        self.attached: list[dict[str, str]] = []
        self.watcher_policy = _expected_watcher_policy(expected_bucket_name(RUN_ID))

    def get_role(self, **kwargs: object) -> Mapping[str, Any]:
        self.requested_role_names.append(kwargs.get("RoleName"))
        if self.role_error_code is not None:
            raise ClientError(
                {"Error": {"Code": self.role_error_code, "Message": "rejected"}},
                "GetRole",
            )
        if not self.role_exists:
            raise ClientError(
                {"Error": {"Code": "NoSuchEntity", "Message": "missing"}},
                "GetRole",
            )
        role_name = kwargs["RoleName"]
        return {
            "Role": {
                "RoleName": role_name,
                "Arn": f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}",
                "RoleId": self.role_id,
                "PermissionsBoundary": {
                    "PermissionsBoundaryType": "PermissionsBoundaryPolicy",
                    "PermissionsBoundaryArn": self.permissions_boundary_arn,
                },
                "Tags": self.tags,
                "Path": "/",
                "MaxSessionDuration": 3600,
                "AssumeRolePolicyDocument": _expected_trust_policy(OPERATOR_ARN),
            }
        }

    def list_role_policies(self, **kwargs: object) -> Mapping[str, Any]:
        return {"IsTruncated": False, "PolicyNames": self.policy_names}

    def get_role_policy(self, **kwargs: object) -> Mapping[str, Any]:
        return {"PolicyDocument": self.watcher_policy}

    def list_attached_role_policies(self, **kwargs: object) -> Mapping[str, Any]:
        return {"IsTruncated": False, "AttachedPolicies": self.attached}

    def delete_role_policy(self, **kwargs: object) -> Mapping[str, Any]:
        self.policy_names = []
        return {}


class FakeS3:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.deleted: list[dict[str, Any]] = []
        self.bucket_exists = True
        self.bucket_error_code: str | None = None
        self.requested_head_buckets: list[object] = []
        self.public_access = {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }
        self.lifecycle_drift = False

    def head_bucket(self, **kwargs: object) -> Mapping[str, Any]:
        self.requested_head_buckets.append(kwargs.get("Bucket"))
        if self.bucket_error_code is not None:
            raise ClientError(
                {"Error": {"Code": self.bucket_error_code, "Message": "rejected"}},
                "HeadBucket",
            )
        if not self.bucket_exists:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "missing"}},
                "HeadBucket",
            )
        return {}

    def get_bucket_tagging(self, **kwargs: object) -> Mapping[str, Any]:
        return {"TagSet": _aws_tags()}

    def get_bucket_location(self, **kwargs: object) -> Mapping[str, Any]:
        return {"LocationConstraint": REGION}

    def list_object_versions(self, **kwargs: object) -> Mapping[str, Any]:
        return self.pages.pop(0)

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]:
        return {"Status": "Enabled"}

    def get_public_access_block(self, **kwargs: object) -> Mapping[str, Any]:
        return {"PublicAccessBlockConfiguration": self.public_access}

    def get_bucket_ownership_controls(self, **kwargs: object) -> Mapping[str, Any]:
        return {
            "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
        }

    def get_bucket_encryption(self, **kwargs: object) -> Mapping[str, Any]:
        return {
            "ServerSideEncryptionConfiguration": {
                "Rules": [
                    {"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}
                ]
            }
        }

    def get_bucket_policy(self, **kwargs: object) -> Mapping[str, Any]:
        return {
            "Policy": _expected_bucket_policy(
                expected_bucket_name(RUN_ID),
                f"arn:aws:iam::{ACCOUNT_ID}:role/{expected_role_name(RUN_ID)}",
            )
        }

    def get_bucket_lifecycle_configuration(self, **kwargs: object) -> Mapping[str, Any]:
        expirations = {
            "run-recovery": ("control-recovery/run/", 30),
            "daily-recovery": ("control-recovery/daily/", 31),
            "weekly-recovery": ("control-recovery/weekly/", 91),
            "monthly-recovery": ("control-recovery/monthly/", 400),
            "trusted-recovery": ("trusted-recovery/", 400),
            "checksum-probes": ("probes/", 7),
        }
        marker_names = {
            "run-recovery": "remove-run-recovery-delete-markers",
            "daily-recovery": "remove-daily-recovery-delete-markers",
            "weekly-recovery": "remove-weekly-recovery-delete-markers",
            "monthly-recovery": "remove-monthly-recovery-delete-markers",
            "trusted-recovery": "remove-trusted-recovery-delete-markers",
            "checksum-probes": "remove-checksum-probe-delete-markers",
        }
        rules: list[dict[str, object]] = []
        for name, (prefix, days) in expirations.items():
            rules.extend(
                [
                    {
                        "ID": f"expire-{name}",
                        "Status": "Enabled",
                        "Filter": {"Prefix": prefix},
                        "Expiration": {"Days": days},
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
                    },
                    {
                        "ID": marker_names[name],
                        "Status": "Enabled",
                        "Filter": {"Prefix": prefix},
                        "Expiration": {"ExpiredObjectDeleteMarker": True},
                    },
                ]
            )
        rules.append(
            {
                "ID": "abort-incomplete-multipart-uploads",
                "Status": "Enabled",
                "Filter": {},
                "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
            }
        )
        if self.lifecycle_drift:
            rules[0]["Transition"] = {"Days": 1, "StorageClass": "GLACIER"}
        return {"Rules": rules}

    def delete_objects(self, **kwargs: object) -> Mapping[str, Any]:
        delete = kwargs.get("Delete")
        assert isinstance(delete, Mapping)
        objects = delete.get("Objects")
        assert isinstance(objects, list)
        self.deleted.extend(objects)
        return {}


def _aws_tags() -> list[dict[str, str]]:
    return [{"Key": key, "Value": value} for key, value in _tags().items()]


def test_live_resource_guard_checks_names_identity_and_tags() -> None:
    validate_aws_resources(
        FakeSts(),
        FakeS3([]),
        FakeIam(_aws_tags()),
        bucket=expected_bucket_name(RUN_ID),
        role_name=expected_role_name(RUN_ID),
        role_id=ROLE_ID,
        run_id=RUN_ID,
        operator_arn=OPERATOR_ARN,
        region=REGION,
    )

    bad_tags = _aws_tags()
    bad_tags[-1] = {"Key": "TestRunId", "Value": "fedcba9876543210"}
    with pytest.raises(GuardError, match="role ownership tags are not exact"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            FakeIam(bad_tags),
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    iam = FakeIam(_aws_tags())
    iam.role_id = "AROA9999999999ABCDEFG"
    with pytest.raises(GuardError, match="RoleId"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    iam = FakeIam(_aws_tags())
    iam.permissions_boundary_arn = "arn:aws:iam::218375352108:policy/wrong"
    with pytest.raises(GuardError, match="permissions boundary"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )


def test_create_target_guard_accepts_only_exact_absence() -> None:
    s3 = FakeS3([])
    s3.bucket_exists = False
    iam = FakeIam(_aws_tags())
    iam.role_exists = False

    validate_create_targets_absent(
        FakeSts(), s3, iam, run_id=RUN_ID, operator_arn=OPERATOR_ARN
    )
    assert s3.requested_head_buckets == [expected_bucket_name(RUN_ID)]
    assert iam.requested_role_names == [expected_role_name(RUN_ID)]

    with pytest.raises(GuardError, match="planned operator"):
        validate_create_targets_absent(
            FakeSts(arn=f"arn:aws:iam::{ACCOUNT_ID}:role/other"),
            s3,
            iam,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
        )

    s3.bucket_exists = True
    with pytest.raises(GuardError, match="planned acceptance bucket already exists"):
        validate_create_targets_absent(
            FakeSts(), s3, iam, run_id=RUN_ID, operator_arn=OPERATOR_ARN
        )

    s3.bucket_exists = False
    iam.role_exists = True
    with pytest.raises(GuardError, match="planned acceptance role already exists"):
        validate_create_targets_absent(
            FakeSts(), s3, iam, run_id=RUN_ID, operator_arn=OPERATOR_ARN
        )


@pytest.mark.parametrize(
    ("service", "error_code", "message"),
    [
        ("s3", "AccessDenied", "bucket absence could not be verified"),
        ("s3", "InternalError", "bucket absence could not be verified"),
        ("iam", "AccessDenied", "IAM role absence could not be verified"),
        ("iam", "ServiceFailure", "IAM role absence could not be verified"),
    ],
)
def test_create_target_guard_rejects_denied_or_ambiguous_absence(
    service: str, error_code: str, message: str
) -> None:
    s3 = FakeS3([])
    s3.bucket_exists = False
    iam = FakeIam(_aws_tags())
    iam.role_exists = False
    if service == "s3":
        s3.bucket_error_code = error_code
    else:
        iam.role_error_code = error_code

    with pytest.raises(GuardError, match=message):
        validate_create_targets_absent(
            FakeSts(), s3, iam, run_id=RUN_ID, operator_arn=OPERATOR_ARN
        )


def test_live_resource_guard_distinguishes_active_and_revoked_writer() -> None:
    iam = FakeIam(_aws_tags())
    iam.policy_names = []

    assert (
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
            expected_writer_state="revoked",
        )
        == "revoked"
    )
    with pytest.raises(GuardError, match="expected active"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )


def test_live_resource_guard_rejects_policy_inventory_and_control_drift() -> None:
    iam = FakeIam(_aws_tags())
    iam.attached = [
        {
            "PolicyName": "AdministratorAccess",
            "PolicyArn": "arn:aws:iam::aws:policy/AdministratorAccess",
        }
    ]
    with pytest.raises(GuardError, match="attached policy inventory"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    s3 = FakeS3([])
    s3.public_access["BlockPublicPolicy"] = False
    with pytest.raises(GuardError, match="public access controls"):
        validate_aws_resources(
            FakeSts(),
            s3,
            FakeIam(_aws_tags()),
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    iam = FakeIam(_aws_tags())
    statements = iam.watcher_policy["Statement"]
    assert isinstance(statements, list)
    first_statement = statements[0]
    assert isinstance(first_statement, dict)
    first_statement["Action"] = "s3:*"
    with pytest.raises(GuardError, match="IAM watcher policy"):
        validate_aws_resources(
            FakeSts(),
            FakeS3([]),
            iam,
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )

    s3 = FakeS3([])
    s3.lifecycle_drift = True
    with pytest.raises(GuardError, match="lifecycle rules"):
        validate_aws_resources(
            FakeSts(),
            s3,
            FakeIam(_aws_tags()),
            bucket=expected_bucket_name(RUN_ID),
            role_name=expected_role_name(RUN_ID),
            role_id=ROLE_ID,
            run_id=RUN_ID,
            operator_arn=OPERATOR_ARN,
            region=REGION,
        )


def test_bucket_cleanup_is_bounded_and_deletes_only_after_complete_listing() -> None:
    s3 = FakeS3(
        [
            {
                "Versions": [{"Key": "first", "VersionId": "1"}],
                "DeleteMarkers": [],
                "IsTruncated": True,
                "NextKeyMarker": "first",
                "NextVersionIdMarker": "1",
            },
            {
                "Versions": [],
                "DeleteMarkers": [{"Key": "first", "VersionId": "2"}],
                "IsTruncated": False,
            },
        ]
    )

    assert (
        empty_bucket_bounded(
            s3,
            bucket=expected_bucket_name(RUN_ID),
            allowed_objects=frozenset(
                {
                    ("first", "1", "version"),
                    ("first", "2", "delete_marker"),
                }
            ),
        )
        == 2
    )
    assert s3.deleted == [
        {"Key": "first", "VersionId": "1"},
        {"Key": "first", "VersionId": "2"},
    ]


def test_bucket_cleanup_refuses_to_delete_when_object_bound_is_exceeded() -> None:
    objects = [
        {"Key": f"object-{index}", "VersionId": str(index)} for index in range(2049)
    ]
    s3 = FakeS3([{"Versions": objects, "IsTruncated": False}])

    with pytest.raises(GuardError, match="object bound"):
        empty_bucket_bounded(
            s3,
            bucket=expected_bucket_name(RUN_ID),
            allowed_objects=frozenset(
                (item["Key"], item["VersionId"], "version") for item in objects
            ),
        )
    assert s3.deleted == []


@pytest.mark.parametrize("field", ["Versions", "DeleteMarkers"])
def test_bucket_cleanup_unknown_version_identity_causes_zero_deletes(
    field: str,
) -> None:
    known = "blobs/sha256/" + "a" * 64
    listed_kind = "version" if field == "Versions" else "delete_marker"
    s3 = FakeS3(
        [
            {
                field: [{"Key": known, "VersionId": "unmanifested-version"}],
                "IsTruncated": False,
            }
        ]
    )

    with pytest.raises(GuardError, match="unrecognized cleanup"):
        empty_bucket_bounded(
            s3,
            bucket=expected_bucket_name(RUN_ID),
            allowed_objects=frozenset({(known, "manifested-version", listed_kind)}),
        )
    assert s3.deleted == []
