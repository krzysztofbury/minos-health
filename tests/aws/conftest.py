# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import os
import re
from collections.abc import Generator
from typing import Any, cast

import boto3  # type: ignore[import-untyped]
import pytest
from botocore.config import Config  # type: ignore[import-untyped]

from .harness import AwsHarness

_ACCOUNT_ID = re.compile(r"[0-9]{12}")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_ROLE_ARN = re.compile(r"arn:[a-z0-9-]+:iam::([0-9]{12}):role/(.+)")
_RUN_ID = re.compile(r"[0-9a-f]{16}")
KNOWN_PRODUCTION_ACCOUNT_ID = "218375352108"


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

    profile = _required("MINOS_AWS_TEST_PROFILE")
    if profile == "default" or os.environ.get("AWS_PROFILE") != profile:
        raise pytest.UsageError(
            "a matching dedicated non-default AWS profile is required"
        )
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        if os.environ.get(name):
            raise pytest.UsageError(f"{name} must be unset; use the dedicated profile")

    account_id = _account("MINOS_AWS_TEST_ACCOUNT_ID")
    production_account_id = _account("MINOS_AWS_PRODUCTION_ACCOUNT_ID")
    if production_account_id != KNOWN_PRODUCTION_ACCOUNT_ID:
        raise pytest.UsageError(
            "the configured production account must match the repository-owned account"
        )
    if account_id == KNOWN_PRODUCTION_ACCOUNT_ID:
        raise pytest.UsageError("the AWS acceptance account must not be production")
    if account_id == production_account_id:
        raise pytest.UsageError("the AWS acceptance account must not be production")
    region = _required("MINOS_AWS_TEST_REGION")
    bucket = _required("MINOS_AWS_TEST_BUCKET")
    role_arn = _required("MINOS_AWS_TEST_ROLE_ARN")
    role_name = _required("MINOS_AWS_TEST_ROLE_NAME")
    run_id = _required("MINOS_AWS_TEST_RUN_ID")
    operator_arn = _required("MINOS_AWS_TEST_OPERATOR_ARN")
    if _BUCKET.fullmatch(bucket) is None or account_id not in bucket:
        raise pytest.UsageError("the disposable bucket output is invalid")
    role_match = _ROLE_ARN.fullmatch(role_arn)
    if role_match is None or role_match.group(1) != account_id:
        raise pytest.UsageError("the watcher role output is outside the test account")
    if role_match.group(2) != role_name or _RUN_ID.fullmatch(run_id) is None:
        raise pytest.UsageError("the randomized Terraform outputs are inconsistent")

    config = Config(
        connect_timeout=10,
        read_timeout=60,
        retries={"total_max_attempts": 1, "mode": "standard"},
    )
    session = boto3.Session(profile_name=profile, region_name=region)
    sts = session.client("sts", config=config)
    identity = sts.get_caller_identity()
    if identity.get("Account") != account_id:
        raise pytest.UsageError("STS identity does not match the approved test account")
    if identity.get("Account") == KNOWN_PRODUCTION_ACCOUNT_ID:
        raise pytest.UsageError("STS resolved to the known production account")
    if identity.get("Account") == production_account_id:
        raise pytest.UsageError("STS resolved to the configured production account")

    assumed = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"minos-p0-{run_id}",
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

    harness = AwsHarness(
        account_id,
        region,
        bucket,
        role_arn,
        role_name,
        run_id,
        operator_arn,
        session.client("s3", config=config),
        session.client("iam", config=config),
        watcher_session.client("s3", config=config),
    )
    _empty_disposable_bucket(harness)
    try:
        yield harness
    finally:
        _empty_disposable_bucket(harness)


def _assert_disposable_bucket(harness: AwsHarness) -> None:
    response = harness.admin_s3.get_bucket_tagging(
        Bucket=harness.bucket,
        ExpectedBucketOwner=harness.account_id,
    )
    tags = {item["Key"]: item["Value"] for item in response["TagSet"]}
    if (
        tags.get("Disposable") != "true"
        or tags.get("Environment") != "acceptance"
        or tags.get("TestHarness") != "phase0-real-aws"
        or tags.get("TestRunId") != harness.run_id
    ):
        raise pytest.UsageError("refusing to clean an unverified AWS test bucket")


def _empty_disposable_bucket(harness: AwsHarness) -> None:
    _assert_disposable_bucket(harness)
    objects: list[dict[str, str]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    for _page in range(4):
        parameters: dict[str, object] = {
            "Bucket": harness.bucket,
            "MaxKeys": 1000,
            "ExpectedBucketOwner": harness.account_id,
        }
        if key_marker is not None:
            parameters["KeyMarker"] = key_marker
        if version_marker is not None:
            parameters["VersionIdMarker"] = version_marker
        response = cast(
            dict[str, Any],
            harness.admin_s3.list_object_versions(**parameters),
        )
        for field in ("Versions", "DeleteMarkers"):
            objects.extend(
                {"Key": item["Key"], "VersionId": item["VersionId"]}
                for item in response.get(field, [])
            )
        if len(objects) > 2048:
            raise pytest.UsageError("AWS test bucket cleanup exceeded its object bound")
        if response.get("IsTruncated") is False:
            break
        key_marker = response.get("NextKeyMarker")
        version_marker = response.get("NextVersionIdMarker")
        if not isinstance(key_marker, str) or not key_marker:
            raise pytest.UsageError("AWS test bucket cleanup key marker is invalid")
        if not isinstance(version_marker, str) or not version_marker:
            raise pytest.UsageError("AWS test bucket cleanup version marker is invalid")
    else:
        raise pytest.UsageError("AWS test bucket cleanup exceeded its page bound")

    for offset in range(0, len(objects), 1000):
        response = harness.admin_s3.delete_objects(
            Bucket=harness.bucket,
            Delete={"Objects": objects[offset : offset + 1000], "Quiet": True},
            ExpectedBucketOwner=harness.account_id,
        )
        if response.get("Errors"):
            raise pytest.UsageError("AWS test bucket cleanup was incomplete")


def _required(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not value:
        raise pytest.UsageError(f"{name} is required for real-AWS acceptance")
    return value


def _account(name: str) -> str:
    value = _required(name)
    if _ACCOUNT_ID.fullmatch(value) is None:
        raise pytest.UsageError(f"{name} must contain twelve digits")
    return value
