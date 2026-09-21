# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import os
import re
from collections.abc import Generator

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.config import Config  # type: ignore[import-untyped]

from scripts.aws_watcher_acceptance_guard import (
    ACCOUNT_ID,
    GuardError,
    canonical_operator_arn,
    empty_bucket_bounded,
    validate_aws_resources,
    validate_preflight_environment,
)

from .harness import AcceptanceObjectManifest, AwsHarness, ManifestRecordingS3Client

_ROLE_ARN = re.compile(r"arn:[a-z0-9-]+:iam::([0-9]{12}):role/(.+)")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if os.environ.get("MINOS_AWS_ACCEPTANCE") == "1":
        return
    skip = pytest.mark.skip(reason="real-AWS acceptance requires explicit opt-in")
    for item in items:
        if "aws" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def aws_harness() -> Generator[AwsHarness]:
    if os.environ.get("MINOS_AWS_ACCEPTANCE") != "1":
        pytest.skip("real-AWS acceptance requires MINOS_AWS_ACCEPTANCE=1")

    try:
        (
            profile,
            account_id,
            region,
            bucket,
            role_arn,
            role_id,
            role_name,
            run_id,
            operator_arn,
            permissions_boundary_arn,
            object_manifest_path,
        ) = validate_preflight_environment(os.environ)
    except GuardError as error:
        raise pytest.UsageError(str(error)) from error

    role_match = _ROLE_ARN.fullmatch(role_arn)
    if role_match is None or role_match.group(1) != ACCOUNT_ID:
        raise pytest.UsageError("the watcher role output is outside the test account")
    if role_match.group(2) != role_name:
        raise pytest.UsageError("the randomized Terraform outputs are inconsistent")

    config = Config(
        connect_timeout=10,
        read_timeout=60,
        retries={"total_max_attempts": 1, "mode": "standard"},
    )
    session = boto3.Session(profile_name=profile, region_name=region)
    sts = session.client("sts", config=config)
    identity = sts.get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise pytest.UsageError("STS identity does not match the approved test account")
    try:
        if canonical_operator_arn(identity.get("Arn")) != operator_arn:
            raise pytest.UsageError("STS identity does not match the approved operator")
    except GuardError as error:
        raise pytest.UsageError(str(error)) from error

    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"minos-watcher-{run_id}",
        DurationSeconds=900,
    )["Credentials"]
    watcher_session = boto3.Session(
        aws_access_key_id=assumed["AccessKeyId"],
        aws_secret_access_key=assumed["SecretAccessKey"],
        aws_session_token=assumed["SessionToken"],
        region_name=region,
    )
    watcher_identity = watcher_session.client(
        "sts", config=config
    ).get_caller_identity()
    if watcher_identity.get("Account") != account_id:
        raise pytest.UsageError("assumed watcher role is outside the test account")
    if f":assumed-role/{role_name}/" not in str(watcher_identity.get("Arn")):
        raise pytest.UsageError("STS did not assume the expected watcher role")

    object_manifest = AcceptanceObjectManifest(
        object_manifest_path,
        bucket=bucket,
        run_id=run_id,
    )
    admin_s3 = ManifestRecordingS3Client(
        session.client("s3", config=config),
        object_manifest,
    )
    watcher_s3 = ManifestRecordingS3Client(
        watcher_session.client("s3", config=config),
        object_manifest,
    )
    harness = AwsHarness(
        account_id,
        region,
        bucket,
        role_arn,
        role_id,
        role_name,
        run_id,
        operator_arn,
        permissions_boundary_arn,
        object_manifest,
        sts,
        admin_s3,
        session.client("iam", config=config),
        watcher_s3,
    )
    _empty_disposable_bucket(harness)
    try:
        yield harness
    finally:
        _empty_disposable_bucket(harness)


def _empty_disposable_bucket(harness: AwsHarness) -> None:
    try:
        _ = validate_aws_resources(
            harness.admin_sts,
            harness.admin_s3,
            harness.admin_iam,
            bucket=harness.bucket,
            role_name=harness.role_name,
            role_id=harness.role_id,
            run_id=harness.run_id,
            operator_arn=harness.operator_arn,
            region=harness.region,
        )
        _ = empty_bucket_bounded(
            harness.admin_s3,
            bucket=harness.bucket,
            allowed_objects=harness.object_manifest.identities(),
        )
    except GuardError as error:
        raise pytest.UsageError(
            f"refusing to clean unverified AWS acceptance resources: {error}"
        ) from error
