#!/usr/bin/env python3
# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import argparse
import json
import os
import re
import secrets
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, cast

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

ACCOUNT_ID = "218375352108"
RESOURCE_PREFIX = "minos-health-watcher-acceptance"
PRODUCTION_CONFIRMATION = f"ACCEPT-DISPOSABLE-RESOURCES:{ACCOUNT_ID}"
EXPECTED_OPERATOR_ARN = (
    f"arn:aws:iam::{ACCOUNT_ID}:role/minos-health-watcher-acceptance-operator"
)
EXPECTED_PERMISSIONS_BOUNDARY_ARN = (
    f"arn:aws:iam::{ACCOUNT_ID}:policy/minos-health-watcher-acceptance-boundary"
)
RUN_ID_PATTERN = re.compile(r"[0-9a-f]{16}")
ROLE_ID_PATTERN = re.compile(r"AROA[A-Z0-9]{17}")
REGION_PATTERN = re.compile(r"[a-z]{2}(-[a-z]+)+-[0-9]+")
IAM_PRINCIPAL_PATTERN = re.compile(
    r"^(arn:[a-z0-9-]+):iam::([0-9]{12}):(user|role)/([A-Za-z0-9+=,.@_/-]+)$"
)
ASSUMED_ROLE_PATTERN = re.compile(
    r"^(arn:[a-z0-9-]+):sts::([0-9]{12}):assumed-role/([A-Za-z0-9+=,.@_/-]+)/[^/]+$"
)
REQUIRED_TAGS = {
    "Disposable": "true",
    "Environment": "acceptance",
    "ManagedBy": "Terraform",
    "Project": "minos-health",
    "TestHarness": "watcher-acceptance",
}
MANAGED_RESOURCE_ADDRESSES = {
    "aws_iam_role.watcher",
    "aws_iam_role_policy.watcher_archive",
    "aws_s3_bucket.snapshots",
    "aws_s3_bucket_lifecycle_configuration.snapshots",
    "aws_s3_bucket_ownership_controls.snapshots",
    "aws_s3_bucket_policy.snapshots",
    "aws_s3_bucket_public_access_block.snapshots",
    "aws_s3_bucket_server_side_encryption_configuration.snapshots",
    "aws_s3_bucket_versioning.snapshots",
    "terraform_data.account_guard",
    "terraform_data.run_guard",
}
REVOKED_RESOURCE_ADDRESSES = MANAGED_RESOURCE_ADDRESSES - {
    "aws_iam_role_policy.watcher_archive"
}
MAX_CLEANUP_PAGES = 4
MAX_CLEANUP_OBJECTS = 2048
RUNTIME_KEYS = {
    "account_id",
    "bucket",
    "object_manifest",
    "operator_arn",
    "permissions_boundary_arn",
    "region",
    "role_arn",
    "role_id",
    "role_name",
    "run_id",
    "schema_version",
}
ObjectIdentity = tuple[str, str, str]
_ACCEPTANCE_KEY_PATTERNS = (
    re.compile(r"blobs/sha256/[0-9a-f]{64}"),
    re.compile(r"control-recovery/run/[0-9]{20}/[0-9a-f]{64}\.sqlite3"),
    re.compile(
        r"control-recovery/daily/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9]{20}/[0-9a-f]{64}\.sqlite3"
    ),
    re.compile(
        r"control-recovery/weekly/[0-9]{4}-W[0-9]{2}/[0-9]{20}/[0-9a-f]{64}\.sqlite3"
    ),
    re.compile(
        r"control-recovery/monthly/[0-9]{4}-[0-9]{2}/[0-9]{20}/[0-9a-f]{64}\.sqlite3"
    ),
    re.compile(r"harness/[0-9a-f]{32}/versioning"),
    re.compile(r"probes/[0-9]{4}-[0-9]{2}-[0-9]{2}/[0-9a-f]{32}/[0-9a-f]{64}"),
    re.compile(r"recovery-inventories/[0-9]{20}\.json"),
    re.compile(r"trusted-recovery/[0-9]{20}/[0-9a-f]{64}\.sqlite3"),
)


class GuardError(RuntimeError):
    pass


class S3Client(Protocol):
    def head_bucket(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_tagging(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_location(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_object_versions(self, **kwargs: object) -> Mapping[str, Any]: ...

    def delete_objects(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_versioning(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_public_access_block(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_ownership_controls(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_encryption(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_policy(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_bucket_lifecycle_configuration(
        self, **kwargs: object
    ) -> Mapping[str, Any]: ...


class IamClient(Protocol):
    def get_role(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_role_policies(self, **kwargs: object) -> Mapping[str, Any]: ...

    def get_role_policy(self, **kwargs: object) -> Mapping[str, Any]: ...

    def list_attached_role_policies(self, **kwargs: object) -> Mapping[str, Any]: ...

    def delete_role_policy(self, **kwargs: object) -> Mapping[str, Any]: ...


class StsClient(Protocol):
    def get_caller_identity(self) -> Mapping[str, Any]: ...


def expected_bucket_name(run_id: str) -> str:
    _validate_run_id(run_id)
    return f"{RESOURCE_PREFIX}-{ACCOUNT_ID}-{run_id}"


def expected_role_name(run_id: str) -> str:
    _validate_run_id(run_id)
    return f"{RESOURCE_PREFIX}-{run_id}-watcher"


def validate_preflight_environment(
    environment: Mapping[str, str],
) -> tuple[str, str, str, str, str, str, str, str, str, str, str]:
    if environment.get("MINOS_AWS_ACCEPTANCE") != "1":
        raise GuardError("MINOS_AWS_ACCEPTANCE must equal 1")
    profile = _required_environment(environment, "MINOS_AWS_TEST_PROFILE")
    if profile == "default" or environment.get("AWS_PROFILE") != profile:
        raise GuardError("a matching dedicated non-default AWS profile is required")
    for name in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    ):
        if environment.get(name):
            raise GuardError(f"{name} must be unset; use the dedicated profile")

    account_id = _required_account(environment, "MINOS_AWS_TEST_ACCOUNT_ID")
    production_account_id = _required_account(
        environment, "MINOS_AWS_PRODUCTION_ACCOUNT_ID"
    )
    if account_id != ACCOUNT_ID:
        raise GuardError("the test account must match the repository-owned account")
    if production_account_id != ACCOUNT_ID:
        raise GuardError(
            "the production account must match the repository-owned account"
        )
    confirmation = _required_environment(
        environment, "MINOS_AWS_PRODUCTION_ACCEPTANCE_CONFIRM"
    )
    if confirmation != PRODUCTION_CONFIRMATION:
        raise GuardError("production acceptance confirmation is not exact")
    configured_operator_arn = environment.get("MINOS_AWS_ACCEPTANCE_OPERATOR_ARN")
    if configured_operator_arn not in {None, "", EXPECTED_OPERATOR_ARN}:
        raise GuardError(
            "caller-supplied acceptance operator ARN is not the tracked ARN"
        )

    region = _required_environment(environment, "MINOS_AWS_TEST_REGION")
    if REGION_PATTERN.fullmatch(region) is None:
        raise GuardError("the acceptance region is invalid")
    bucket = _required_environment(environment, "MINOS_AWS_TEST_BUCKET")
    role_arn = _required_environment(environment, "MINOS_AWS_TEST_ROLE_ARN")
    role_id = _required_environment(environment, "MINOS_AWS_TEST_ROLE_ID")
    role_name = _required_environment(environment, "MINOS_AWS_TEST_ROLE_NAME")
    run_id = _required_environment(environment, "MINOS_AWS_TEST_RUN_ID")
    operator_arn = _required_environment(environment, "MINOS_AWS_TEST_OPERATOR_ARN")
    permissions_boundary_arn = _required_environment(
        environment, "MINOS_AWS_TEST_PERMISSIONS_BOUNDARY_ARN"
    )
    object_manifest = _required_environment(
        environment, "MINOS_AWS_TEST_OBJECT_MANIFEST"
    )
    if operator_arn != EXPECTED_OPERATOR_ARN:
        raise GuardError(
            "runtime operator does not match the tracked acceptance operator"
        )
    if permissions_boundary_arn != EXPECTED_PERMISSIONS_BOUNDARY_ARN:
        raise GuardError(
            "runtime permissions boundary does not match the tracked policy"
        )
    if bucket != expected_bucket_name(run_id):
        raise GuardError("the disposable bucket output is invalid")
    if role_name != expected_role_name(run_id):
        raise GuardError("the disposable role output is invalid")
    if role_arn != f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}":
        raise GuardError("the disposable role ARN is invalid")
    _validate_role_id(role_id)
    return (
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
        object_manifest,
    )


def canonical_operator_arn(identity_arn: object) -> str:
    if not isinstance(identity_arn, str):
        raise GuardError("STS identity does not contain an ARN")
    if match := IAM_PRINCIPAL_PATTERN.fullmatch(identity_arn):
        if match.group(2) != ACCOUNT_ID:
            raise GuardError("operator principal is outside the repository account")
        return identity_arn
    match = ASSUMED_ROLE_PATTERN.fullmatch(identity_arn)
    if match is None or match.group(2) != ACCOUNT_ID:
        raise GuardError(
            "STS identity must be an IAM principal in the repository account"
        )
    partition, account_id, role_path = match.groups()
    return f"{partition}:iam::{account_id}:role/{role_path}"


def validate_aws_resources(
    sts: StsClient,
    s3: S3Client,
    iam: IamClient,
    *,
    bucket: str,
    role_name: str,
    role_id: str,
    run_id: str,
    operator_arn: str,
    region: str,
    expected_writer_state: str = "active",
) -> str:
    if expected_writer_state not in {"active", "revoked", "either"}:
        raise GuardError("writer state expectation is invalid")
    expected_bucket = expected_bucket_name(run_id)
    expected_role = expected_role_name(run_id)
    if bucket != expected_bucket:
        raise GuardError("bucket name does not match the fixed acceptance identity")
    if role_name != expected_role:
        raise GuardError("role name does not match the fixed acceptance identity")
    _validate_role_id(role_id)
    _validate_operator_arn(operator_arn)

    identity = sts.get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise GuardError("active AWS identity is outside the repository account")
    if canonical_operator_arn(identity.get("Arn")) != operator_arn:
        raise GuardError("active AWS identity does not match the state operator")

    bucket_response = s3.get_bucket_tagging(
        Bucket=bucket,
        ExpectedBucketOwner=ACCOUNT_ID,
    )
    bucket_tags = _tag_mapping(bucket_response.get("TagSet"))
    _validate_tags(bucket_tags, run_id, "bucket")
    location_response = s3.get_bucket_location(
        Bucket=bucket,
        ExpectedBucketOwner=ACCOUNT_ID,
    )
    location = location_response.get("LocationConstraint") or "us-east-1"
    if location != region:
        raise GuardError("bucket region does not match the approved acceptance region")

    role_response = iam.get_role(RoleName=role_name)
    role = role_response.get("Role")
    if not isinstance(role, Mapping):
        raise GuardError("IAM response does not contain the expected role")
    if role.get("RoleName") != role_name:
        raise GuardError("IAM returned a different role name")
    expected_role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    if role.get("Arn") != expected_role_arn:
        raise GuardError("IAM role ARN does not match the repository account")
    if role.get("RoleId") != role_id:
        raise GuardError("IAM RoleId does not match the immutable runtime identity")
    _validate_live_permissions_boundary(role)
    role_tags = _tag_mapping(role.get("Tags"))
    _validate_tags(role_tags, run_id, "role")
    if role.get("Path") != "/" or role.get("MaxSessionDuration") != 3600:
        raise GuardError("IAM role path or session duration is mismatched")
    _validate_policy(
        role.get("AssumeRolePolicyDocument"),
        _expected_trust_policy(operator_arn),
        "IAM role trust policy",
    )
    policy_name = f"{role_name}-archive"
    inline = iam.list_role_policies(RoleName=role_name, MaxItems=100)
    if inline.get("IsTruncated") is not False:
        raise GuardError("IAM inline policy inventory pagination is incomplete")
    policy_names = inline.get("PolicyNames")
    if policy_names == [policy_name]:
        writer_state = "active"
    elif policy_names == []:
        writer_state = "revoked"
    else:
        raise GuardError("IAM inline policy inventory is not exact")
    if expected_writer_state != "either" and writer_state != expected_writer_state:
        raise GuardError(
            f"IAM writer state is {writer_state}, expected {expected_writer_state}"
        )
    attached = iam.list_attached_role_policies(RoleName=role_name, MaxItems=100)
    if (
        attached.get("IsTruncated") is not False
        or attached.get("AttachedPolicies") != []
    ):
        raise GuardError("IAM attached policy inventory is not empty")
    if writer_state == "active":
        policy = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
        _validate_policy(
            policy.get("PolicyDocument"),
            _expected_watcher_policy(bucket),
            "IAM watcher policy",
        )
    _validate_bucket_controls(s3, bucket=bucket, role_arn=expected_role_arn)
    return writer_state


def _validate_live_permissions_boundary(role: Mapping[str, Any]) -> None:
    if role.get("PermissionsBoundary") != {
        "PermissionsBoundaryType": "PermissionsBoundaryPolicy",
        "PermissionsBoundaryArn": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
    }:
        raise GuardError("IAM role permissions boundary is not exact")


def validate_partial_teardown_resources(
    sts: StsClient,
    s3: S3Client,
    iam: IamClient,
    document: Mapping[str, Any],
    *,
    run_id: str,
    role_id: str,
    operator_arn: str,
    region: str,
) -> tuple[str, bool, bool]:
    _validate_role_id(role_id)
    _validate_operator_arn(operator_arn)
    phase, addresses = validate_teardown_state(
        document,
        region=region,
        operator_arn=operator_arn,
        expected_run_id=run_id,
        expected_role_id=role_id,
    )
    if phase == "active":
        writer_state = validate_aws_resources(
            sts,
            s3,
            iam,
            bucket=expected_bucket_name(run_id),
            role_name=expected_role_name(run_id),
            role_id=role_id,
            run_id=run_id,
            operator_arn=operator_arn,
            region=region,
            expected_writer_state="either",
        )
        return writer_state, True, True
    identity = sts.get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise GuardError("active AWS identity is outside the repository account")
    if canonical_operator_arn(identity.get("Arn")) != operator_arn:
        raise GuardError("active AWS identity does not match the state operator")
    bucket_present = "aws_s3_bucket.snapshots" in addresses
    role_present = "aws_iam_role.watcher" in addresses
    bucket = expected_bucket_name(run_id)
    role_name = expected_role_name(run_id)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    if bucket_present:
        tags = _tag_mapping(
            s3.get_bucket_tagging(
                Bucket=bucket,
                ExpectedBucketOwner=ACCOUNT_ID,
            ).get("TagSet")
        )
        _validate_tags(tags, run_id, "bucket")
        location = (
            s3.get_bucket_location(
                Bucket=bucket,
                ExpectedBucketOwner=ACCOUNT_ID,
            ).get("LocationConstraint")
            or "us-east-1"
        )
        if location != region:
            raise GuardError(
                "bucket region does not match the approved acceptance region"
            )
        _validate_remaining_bucket_controls(
            s3,
            bucket=bucket,
            role_arn=role_arn,
            addresses=addresses,
        )
    else:
        _require_bucket_absent(s3, bucket)
    if role_present:
        role_response = iam.get_role(RoleName=role_name)
        role = role_response.get("Role")
        if not isinstance(role, Mapping):
            raise GuardError("IAM response does not contain the expected role")
        if (
            role.get("RoleName") != role_name
            or role.get("Arn") != role_arn
            or role.get("RoleId") != role_id
        ):
            raise GuardError("IAM role identity is mismatched during teardown")
        _validate_live_permissions_boundary(role)
        _validate_tags(_tag_mapping(role.get("Tags")), run_id, "role")
        if role.get("Path") != "/" or role.get("MaxSessionDuration") != 3600:
            raise GuardError("IAM role path or session duration is mismatched")
        _validate_policy(
            role.get("AssumeRolePolicyDocument"),
            _expected_trust_policy(operator_arn),
            "IAM role trust policy",
        )
        inline = iam.list_role_policies(RoleName=role_name, MaxItems=100)
        attached = iam.list_attached_role_policies(RoleName=role_name, MaxItems=100)
        if inline.get("IsTruncated") is not False or inline.get("PolicyNames") != []:
            raise GuardError("IAM writer permissions returned during teardown")
        if (
            attached.get("IsTruncated") is not False
            or attached.get("AttachedPolicies") != []
        ):
            raise GuardError("IAM attached permissions returned during teardown")
    else:
        _require_role_absent(iam, role_name)
    return phase, bucket_present, role_present


def validate_create_targets_absent(
    sts: StsClient,
    s3: S3Client,
    iam: IamClient,
    *,
    run_id: str,
    operator_arn: str,
) -> None:
    _validate_run_id(run_id)
    _validate_operator_arn(operator_arn)
    identity = sts.get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise GuardError("active AWS identity is outside the repository account")
    if canonical_operator_arn(identity.get("Arn")) != operator_arn:
        raise GuardError("active AWS identity does not match the planned operator")
    _require_bucket_absent(
        s3,
        expected_bucket_name(run_id),
        present_error="planned acceptance bucket already exists",
    )
    _require_role_absent(
        iam,
        expected_role_name(run_id),
        present_error="planned acceptance role already exists",
    )


def _require_bucket_absent(
    s3: S3Client,
    bucket: str,
    *,
    present_error: str = "Terraform state omitted a live acceptance bucket",
) -> None:
    try:
        _ = s3.head_bucket(Bucket=bucket, ExpectedBucketOwner=ACCOUNT_ID)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code in {"404", "NoSuchBucket", "NotFound"}:
            return
        raise GuardError("bucket absence could not be verified") from error
    raise GuardError(present_error)


def _require_role_absent(
    iam: IamClient,
    role_name: str,
    *,
    present_error: str = "Terraform state omitted a live acceptance role",
) -> None:
    try:
        _ = iam.get_role(RoleName=role_name)
    except ClientError as error:
        code = str(error.response.get("Error", {}).get("Code", ""))
        if code == "NoSuchEntity":
            return
        raise GuardError("IAM role absence could not be verified") from error
    raise GuardError(present_error)


def empty_bucket_bounded(
    s3: S3Client, *, bucket: str, allowed_objects: frozenset[ObjectIdentity]
) -> int:
    objects: list[dict[str, str]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    for _page in range(MAX_CLEANUP_PAGES):
        parameters: dict[str, object] = {
            "Bucket": bucket,
            "MaxKeys": 1000,
            "ExpectedBucketOwner": ACCOUNT_ID,
        }
        if key_marker is not None:
            parameters["KeyMarker"] = key_marker
        if version_marker is not None:
            parameters["VersionIdMarker"] = version_marker
        response = s3.list_object_versions(**parameters)
        for field, kind in (
            ("Versions", "version"),
            ("DeleteMarkers", "delete_marker"),
        ):
            entries = response.get(field, [])
            if not isinstance(entries, Sequence):
                raise GuardError(
                    "bucket cleanup response contains an invalid object list"
                )
            for entry in entries:
                if not isinstance(entry, Mapping):
                    raise GuardError(
                        "bucket cleanup response contains an invalid object"
                    )
                key = entry.get("Key")
                version_id = entry.get("VersionId")
                if not isinstance(key, str) or not isinstance(version_id, str):
                    raise GuardError("bucket cleanup object identity is invalid")
                identity = (key, version_id, kind)
                if identity not in allowed_objects:
                    raise GuardError(
                        f"unrecognized cleanup {kind}: {key} version {version_id}"
                    )
                objects.append({"Key": key, "VersionId": version_id})
        if len(objects) > MAX_CLEANUP_OBJECTS:
            raise GuardError("bucket cleanup exceeded its object bound")
        if response.get("IsTruncated") is False:
            break
        if response.get("IsTruncated") is not True:
            raise GuardError("bucket cleanup pagination status is invalid")
        key_marker = response.get("NextKeyMarker")
        version_marker = response.get("NextVersionIdMarker")
        if not isinstance(key_marker, str) or not key_marker:
            raise GuardError("bucket cleanup key marker is invalid")
        if not isinstance(version_marker, str) or not version_marker:
            raise GuardError("bucket cleanup version marker is invalid")
    else:
        raise GuardError("bucket cleanup exceeded its page bound")

    for offset in range(0, len(objects), 1000):
        response = s3.delete_objects(
            Bucket=bucket,
            Delete={"Objects": objects[offset : offset + 1000], "Quiet": True},
            ExpectedBucketOwner=ACCOUNT_ID,
        )
        if response.get("Errors"):
            raise GuardError("bucket cleanup was incomplete")
    return len(objects)


def validate_acceptance_key(key: str) -> None:
    if not key or len(key) > 1024:
        raise GuardError("acceptance object key length is invalid")
    if not any(pattern.fullmatch(key) for pattern in _ACCEPTANCE_KEY_PATTERNS):
        raise GuardError(f"object key is outside the acceptance test grammar: {key}")


def load_object_manifest(
    path: str | Path,
    *,
    account_id: str,
    bucket: str,
    run_id: str,
    allow_missing: bool = False,
) -> frozenset[ObjectIdentity]:
    manifest_path = Path(path)
    if not manifest_path.is_file():
        if allow_missing:
            return frozenset()
        raise GuardError("acceptance object manifest is missing")
    document = _load_json(str(manifest_path))
    if set(document) != {
        "account_id",
        "bucket",
        "objects",
        "run_id",
        "schema_version",
    }:
        raise GuardError("acceptance object manifest schema is invalid")
    if document.get("schema_version") != 2:
        raise GuardError("acceptance object manifest version is invalid")
    if document.get("account_id") != account_id:
        raise GuardError("acceptance object manifest account is mismatched")
    if document.get("bucket") != bucket or document.get("run_id") != run_id:
        raise GuardError("acceptance object manifest resource identity is mismatched")
    objects = document.get("objects")
    if not isinstance(objects, list) or len(objects) > MAX_CLEANUP_OBJECTS:
        raise GuardError("acceptance object manifest count is invalid")
    result: set[ObjectIdentity] = set()
    for item in objects:
        if not isinstance(item, Mapping) or set(item) != {"key", "kind", "version_id"}:
            raise GuardError("acceptance object manifest contains an invalid identity")
        key = item.get("key")
        version_id = item.get("version_id")
        kind = item.get("kind")
        if (
            not isinstance(key, str)
            or not isinstance(version_id, str)
            or not version_id
            or len(version_id) > 1024
            or kind not in {"version", "delete_marker"}
        ):
            raise GuardError("acceptance object manifest contains an invalid identity")
        validate_acceptance_key(key)
        identity = (key, version_id, str(kind))
        if identity in result:
            raise GuardError("acceptance object manifest contains a duplicate identity")
        result.add(identity)
    return frozenset(result)


def validate_state(
    document: Mapping[str, Any],
    *,
    region: str,
    operator_arn: str,
    writer_revoked: bool = False,
) -> tuple[str, str, str, str]:
    _validate_operator_arn(operator_arn)
    resources = _managed_resources(document)
    addresses = set(resources)
    expected_addresses = (
        REVOKED_RESOURCE_ADDRESSES if writer_revoked else MANAGED_RESOURCE_ADDRESSES
    )
    if addresses != expected_addresses:
        unexpected = sorted(addresses - expected_addresses)
        missing = sorted(expected_addresses - addresses)
        raise GuardError(
            f"state resource allowlist mismatch: missing={missing}, unexpected={unexpected}"
        )

    run_id = _string_value(resources["terraform_data.run_guard"], "input")
    _validate_run_id(run_id)
    bucket = expected_bucket_name(run_id)
    role_name = expected_role_name(run_id)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    role_id = _string_value(resources["aws_iam_role.watcher"], "unique_id")
    _validate_role_id(role_id)
    policy_name = f"{role_name}-archive"

    _require_resource_value(
        resources, "terraform_data.account_guard", "input", ACCOUNT_ID
    )
    _require_resource_value(resources, "terraform_data.run_guard", "input", run_id)
    _require_resource_value(resources, "aws_s3_bucket.snapshots", "bucket", bucket)
    _require_resource_value(resources, "aws_s3_bucket.snapshots", "id", bucket)
    _require_resource_value(resources, "aws_iam_role.watcher", "name", role_name)
    _require_resource_value(resources, "aws_iam_role.watcher", "arn", role_arn)
    _require_resource_value(
        resources,
        "aws_iam_role.watcher",
        "permissions_boundary",
        EXPECTED_PERMISSIONS_BOUNDARY_ARN,
    )
    _validate_state_role_policy_attributes(resources["aws_iam_role.watcher"])
    if not writer_revoked:
        _require_resource_value(
            resources,
            "aws_iam_role_policy.watcher_archive",
            "name",
            policy_name,
        )
        _require_resource_value(
            resources,
            "aws_iam_role_policy.watcher_archive",
            "role",
            role_name,
        )
    for address in (
        "aws_s3_bucket_lifecycle_configuration.snapshots",
        "aws_s3_bucket_ownership_controls.snapshots",
        "aws_s3_bucket_policy.snapshots",
        "aws_s3_bucket_public_access_block.snapshots",
        "aws_s3_bucket_server_side_encryption_configuration.snapshots",
        "aws_s3_bucket_versioning.snapshots",
    ):
        _require_resource_value(resources, address, "bucket", bucket)
    for address in ("aws_s3_bucket.snapshots", "aws_iam_role.watcher"):
        _validate_state_tags(resources, address, run_id)

    _require_output(document, "aws_account_id", ACCOUNT_ID)
    _require_output(document, "aws_region", region)
    _require_output(document, "bucket_name", bucket)
    _require_output(document, "operator_principal_arn", operator_arn)
    _require_output(document, "test_run_id", run_id)
    _require_output(document, "watcher_role_arn", role_arn)
    _require_output(
        document,
        "watcher_role_permissions_boundary_arn",
        EXPECTED_PERMISSIONS_BOUNDARY_ARN,
    )
    _require_output(document, "watcher_role_unique_id", role_id)
    _require_output(document, "watcher_role_name", role_name)
    return run_id, bucket, role_name, role_id


def validate_destroy_plan(
    document: Mapping[str, Any],
    *,
    region: str,
    operator_arn: str,
    role_id: str,
    current_state: Mapping[str, Any] | None = None,
) -> None:
    prior_state = document.get("prior_state")
    if not isinstance(prior_state, Mapping):
        raise GuardError("destroy plan does not contain its prior state")
    prior_resources = _managed_resources_allow_empty(prior_state)
    if not prior_resources:
        raise GuardError("destroy plan prior state is empty")
    run_id = _partial_state_run_id(prior_resources)
    _, prior_addresses = validate_teardown_state(
        prior_state,
        region=region,
        operator_arn=operator_arn,
        expected_run_id=run_id,
        expected_role_id=role_id,
    )
    if "aws_iam_role_policy.watcher_archive" in prior_addresses:
        raise GuardError("destroy plan prior state still contains writer permissions")
    if current_state is not None:
        _, current_addresses = validate_teardown_state(
            current_state,
            region=region,
            operator_arn=operator_arn,
            expected_run_id=run_id,
            expected_role_id=role_id,
        )
        if current_addresses != prior_addresses and not (
            current_addresses == MANAGED_RESOURCE_ADDRESSES
            and prior_addresses == REVOKED_RESOURCE_ADDRESSES
        ):
            raise GuardError("saved destroy plan does not match current teardown state")

    changes = document.get("resource_changes")
    if not isinstance(changes, Sequence):
        raise GuardError("destroy plan does not contain resource changes")
    addresses: set[str] = set()
    for entry in changes:
        if not isinstance(entry, Mapping):
            raise GuardError("destroy plan contains an invalid resource change")
        address = entry.get("address")
        if not isinstance(address, str) or address in addresses:
            raise GuardError("destroy plan contains an invalid or duplicate address")
        change = entry.get("change")
        actions = change.get("actions") if isinstance(change, Mapping) else None
        if actions != ["delete"]:
            raise GuardError(f"destroy plan action for {address} is not exactly delete")
        addresses.add(address)
    if addresses != prior_addresses:
        unexpected = sorted(addresses - prior_addresses)
        missing = sorted(prior_addresses - addresses)
        raise GuardError(
            f"destroy plan allowlist mismatch: missing={missing}, unexpected={unexpected}"
        )


def validate_teardown_state(
    document: Mapping[str, Any],
    *,
    region: str,
    operator_arn: str,
    expected_run_id: str,
    expected_role_id: str,
) -> tuple[str, set[str]]:
    _validate_run_id(expected_run_id)
    _validate_role_id(expected_role_id)
    _validate_operator_arn(operator_arn)
    resources = _managed_resources_allow_empty(document)
    addresses = set(resources)
    if not addresses:
        return "empty", addresses
    if addresses == MANAGED_RESOURCE_ADDRESSES:
        state_identity = validate_state(
            document, region=region, operator_arn=operator_arn
        )
        if state_identity[3] != expected_role_id:
            raise GuardError("teardown state RoleId is mismatched")
        return "active", addresses
    if not addresses <= REVOKED_RESOURCE_ADDRESSES:
        raise GuardError("teardown state contains an unapproved resource set")
    run_id = _partial_state_run_id(resources)
    if run_id != expected_run_id:
        raise GuardError("teardown state run ID is mismatched")
    bucket = expected_bucket_name(run_id)
    role_name = expected_role_name(run_id)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    if "aws_s3_bucket.snapshots" in addresses:
        _require_resource_value(resources, "aws_s3_bucket.snapshots", "bucket", bucket)
        _require_resource_value(resources, "aws_s3_bucket.snapshots", "id", bucket)
        _validate_state_tags(resources, "aws_s3_bucket.snapshots", run_id)
    s3_dependents = {
        address for address in addresses if address.startswith("aws_s3_bucket_")
    }
    if s3_dependents and "aws_s3_bucket.snapshots" not in addresses:
        raise GuardError("teardown state has bucket controls without the bucket")
    if (
        "aws_s3_bucket_policy.snapshots" in addresses
        and "aws_iam_role.watcher" not in addresses
    ):
        raise GuardError(
            "teardown state has a role-bound bucket policy without the role"
        )
    for address in s3_dependents:
        _require_resource_value(resources, address, "bucket", bucket)
    if "aws_iam_role.watcher" in addresses:
        _require_resource_value(resources, "aws_iam_role.watcher", "name", role_name)
        _require_resource_value(resources, "aws_iam_role.watcher", "arn", role_arn)
        _require_resource_value(
            resources,
            "aws_iam_role.watcher",
            "permissions_boundary",
            EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        )
        _require_resource_value(
            resources, "aws_iam_role.watcher", "unique_id", expected_role_id
        )
        _validate_state_role_policy_attributes(resources["aws_iam_role.watcher"])
        _validate_state_tags(resources, "aws_iam_role.watcher", run_id)
    if (
        addresses
        & {
            "aws_s3_bucket.snapshots",
            "aws_iam_role.watcher",
        }
        and not {
            "terraform_data.account_guard",
            "terraform_data.run_guard",
        }
        <= addresses
    ):
        raise GuardError("teardown state lost identity guards before AWS resources")
    _validate_optional_outputs(
        document,
        region=region,
        operator_arn=operator_arn,
        run_id=run_id,
        bucket=bucket,
        role_name=role_name,
        role_arn=role_arn,
        role_id=expected_role_id,
    )
    phase = "revoked" if addresses == REVOKED_RESOURCE_ADDRESSES else "partial"
    return phase, addresses


def _partial_state_run_id(resources: Mapping[str, Mapping[str, Any]]) -> str:
    run_values = resources.get("terraform_data.run_guard")
    if run_values is None:
        raise GuardError("teardown state is missing its run identity")
    run_id = _string_value(run_values, "input")
    _validate_run_id(run_id)
    return run_id


def _validate_state_tags(
    resources: Mapping[str, Mapping[str, Any]], address: str, run_id: str
) -> None:
    for attribute in ("tags", "tags_all"):
        tags = resources[address].get(attribute)
        if not isinstance(tags, Mapping):
            raise GuardError(f"{address} is missing state {attribute}")
        _validate_tags(tags, run_id, f"{address}.{attribute}")


def _validate_state_role_policy_attributes(role: Mapping[str, Any]) -> None:
    for attribute in ("inline_policy", "managed_policy_arns"):
        value = role.get(attribute)
        if value is not None and value != () and value != []:
            raise GuardError(f"state IAM role {attribute} is not empty")


def _validate_optional_outputs(
    document: Mapping[str, Any],
    *,
    region: str,
    operator_arn: str,
    run_id: str,
    bucket: str,
    role_name: str,
    role_arn: str,
    role_id: str,
) -> None:
    values = document.get("values")
    outputs = values.get("outputs") if isinstance(values, Mapping) else None
    if outputs is None:
        return
    expected = {
        "aws_account_id": ACCOUNT_ID,
        "aws_region": region,
        "bucket_name": bucket,
        "operator_principal_arn": operator_arn,
        "test_run_id": run_id,
        "watcher_role_arn": role_arn,
        "watcher_role_permissions_boundary_arn": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        "watcher_role_unique_id": role_id,
        "watcher_role_name": role_name,
    }
    for name, expected_value in expected.items():
        if isinstance(outputs, Mapping) and name in outputs:
            _require_output(document, name, expected_value)


def validate_apply_plan(
    document: Mapping[str, Any], *, region: str, run_id: str
) -> None:
    _validate_run_id(run_id)
    if REGION_PATTERN.fullmatch(region) is None:
        raise GuardError("apply plan region is invalid")
    prior_state = document.get("prior_state")
    if prior_state is not None:
        if not isinstance(prior_state, Mapping):
            raise GuardError("apply plan prior state is invalid")
        if _managed_resources_allow_empty(prior_state):
            raise GuardError("apply plan prior state is not empty")
    changes = document.get("resource_changes")
    if not isinstance(changes, Sequence):
        raise GuardError("apply plan does not contain resource changes")
    entries: dict[str, Mapping[str, Any]] = {}
    for entry in changes:
        if not isinstance(entry, Mapping):
            raise GuardError("apply plan contains an invalid resource change")
        address = entry.get("address")
        if not isinstance(address, str) or address in entries:
            raise GuardError("apply plan contains an invalid or duplicate address")
        change = entry.get("change")
        actions = change.get("actions") if isinstance(change, Mapping) else None
        if actions != ["create"]:
            raise GuardError(f"apply plan action for {address} is not exactly create")
        entries[address] = entry
    addresses = set(entries)
    if addresses != MANAGED_RESOURCE_ADDRESSES:
        unexpected = sorted(addresses - MANAGED_RESOURCE_ADDRESSES)
        missing = sorted(MANAGED_RESOURCE_ADDRESSES - addresses)
        raise GuardError(
            f"apply plan allowlist mismatch: missing={missing}, unexpected={unexpected}"
        )
    _validate_apply_targets(entries, region=region, run_id=run_id)
    _validate_apply_outputs(document, region=region, run_id=run_id)


def _validate_apply_targets(
    entries: Mapping[str, Mapping[str, Any]], *, region: str, run_id: str
) -> None:
    bucket = expected_bucket_name(run_id)
    role_name = expected_role_name(run_id)
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}"
    policy_name = f"{role_name}-archive"
    tags = {**REQUIRED_TAGS, "TestRunId": run_id}
    after = {address: _planned_after(entry) for address, entry in entries.items()}

    _require_planned_value(
        after, entries, "terraform_data.account_guard", ("input",), ACCOUNT_ID
    )
    _require_planned_value(
        after, entries, "terraform_data.run_guard", ("input",), run_id
    )
    _require_planned_value(
        after, entries, "aws_s3_bucket.snapshots", ("bucket",), bucket
    )
    _require_planned_value(
        after, entries, "aws_s3_bucket.snapshots", ("force_destroy",), False
    )
    _require_planned_value(
        after, entries, "aws_s3_bucket.snapshots", ("object_lock_enabled",), False
    )
    _require_planned_value(after, entries, "aws_s3_bucket.snapshots", ("tags",), tags)
    _require_planned_value(after, entries, "aws_iam_role.watcher", ("name",), role_name)
    _require_planned_value(
        after,
        entries,
        "aws_iam_role.watcher",
        ("description",),
        "Disposable watcher role for real-AWS acceptance only.",
    )
    _require_planned_value(after, entries, "aws_iam_role.watcher", ("path",), "/")
    _require_planned_value(
        after, entries, "aws_iam_role.watcher", ("max_session_duration",), 3600
    )
    _require_planned_value(
        after,
        entries,
        "aws_iam_role.watcher",
        ("permissions_boundary",),
        EXPECTED_PERMISSIONS_BOUNDARY_ARN,
    )
    for attribute in ("inline_policy", "managed_policy_arns"):
        _require_planned_empty_collection(
            after, entries, "aws_iam_role.watcher", attribute
        )
    _require_planned_value(after, entries, "aws_iam_role.watcher", ("tags",), tags)
    _validate_planned_policy(
        after,
        entries,
        "aws_iam_role.watcher",
        "assume_role_policy",
        _expected_trust_policy(EXPECTED_OPERATOR_ARN),
    )
    _require_planned_value(
        after, entries, "aws_iam_role_policy.watcher_archive", ("name",), policy_name
    )
    _require_planned_value(
        after, entries, "aws_iam_role_policy.watcher_archive", ("role",), role_name
    )
    _validate_planned_policy(
        after,
        entries,
        "aws_iam_role_policy.watcher_archive",
        "policy",
        _expected_watcher_policy(bucket),
    )

    bucket_children = MANAGED_RESOURCE_ADDRESSES - {
        "aws_iam_role.watcher",
        "aws_iam_role_policy.watcher_archive",
        "aws_s3_bucket.snapshots",
        "terraform_data.account_guard",
        "terraform_data.run_guard",
    }
    for address in bucket_children:
        _require_planned_value(after, entries, address, ("bucket",), bucket)
    _validate_planned_bucket_controls(after, entries, bucket=bucket, role_arn=role_arn)
    _ = region


def _planned_after(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    change = entry.get("change")
    if not isinstance(change, Mapping) or change.get("before") is not None:
        raise GuardError("apply plan create change has a nonempty before value")
    after = change.get("after")
    after_unknown = change.get("after_unknown")
    if not isinstance(after, Mapping) or not isinstance(after_unknown, Mapping):
        raise GuardError("apply plan create target or unknown map is invalid")
    return after


def _require_planned_value(
    after_by_address: Mapping[str, Mapping[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
    address: str,
    path: tuple[str | int, ...],
    expected: object,
) -> None:
    actual = _nested_value(after_by_address[address], path)
    change = entries[address]["change"]
    unknown = change.get("after_unknown") if isinstance(change, Mapping) else None
    if actual != expected or _nested_unknown(unknown, path):
        joined = ".".join(str(part) for part in path)
        raise GuardError(
            f"apply plan target {address}.{joined} is unknown or mismatched"
        )


def _require_planned_empty_collection(
    after_by_address: Mapping[str, Mapping[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
    address: str,
    attribute: str,
) -> None:
    actual = after_by_address[address].get(attribute)
    change = entries[address]["change"]
    unknown = change.get("after_unknown") if isinstance(change, Mapping) else None
    unknown_collection = _nested_value(unknown, (attribute,))
    if (actual is not None and actual != () and actual != []) or (
        unknown_collection is not None
        and unknown_collection is not False
        and unknown_collection != ()
        and unknown_collection != []
        and unknown_collection != {}
    ):
        raise GuardError(
            f"apply plan IAM role {attribute} is nonempty or may be unknown"
        )


def _nested_value(value: object, path: tuple[str | int, ...]) -> object:
    current = value
    for part in path:
        if isinstance(part, str) and isinstance(current, Mapping):
            current = current.get(part)
        elif (
            isinstance(part, int)
            and isinstance(current, Sequence)
            and not isinstance(current, str)
            and part < len(current)
        ):
            current = current[part]
        else:
            return None
    return current


def _nested_unknown(value: object, path: tuple[str | int, ...]) -> bool:
    current = value
    if current is True:
        return True
    for part in path:
        if isinstance(part, str) and isinstance(current, Mapping):
            current = current.get(part, False)
        elif (
            isinstance(part, int)
            and isinstance(current, Sequence)
            and not isinstance(current, str)
            and part < len(current)
        ):
            current = current[part]
        else:
            return False
        if current is True:
            return True
    return False


def _validate_planned_policy(
    after: Mapping[str, Mapping[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
    address: str,
    attribute: str,
    expected: object,
) -> None:
    change = entries[address]["change"]
    unknown = change.get("after_unknown") if isinstance(change, Mapping) else None
    if _nested_unknown(unknown, (attribute,)):
        raise GuardError(f"apply plan policy {address}.{attribute} is unknown")
    _validate_policy(after[address].get(attribute), expected, f"apply plan {address}")


def _validate_apply_outputs(
    document: Mapping[str, Any], *, region: str, run_id: str
) -> None:
    role_name = expected_role_name(run_id)
    expected = {
        "aws_account_id": ACCOUNT_ID,
        "aws_region": region,
        "bucket_name": expected_bucket_name(run_id),
        "operator_principal_arn": EXPECTED_OPERATOR_ARN,
        "test_run_id": run_id,
        "watcher_role_arn": f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}",
        "watcher_role_permissions_boundary_arn": EXPECTED_PERMISSIONS_BOUNDARY_ARN,
        "watcher_role_name": role_name,
    }
    changes = document.get("output_changes")
    if not isinstance(changes, Mapping) or set(changes) != {
        *expected,
        "watcher_role_unique_id",
    }:
        raise GuardError("apply plan output inventory is mismatched")
    for name, value in expected.items():
        change = changes.get(name)
        if (
            not isinstance(change, Mapping)
            or change.get("actions") != ["create"]
            or change.get("before") is not None
            or change.get("after") != value
            or change.get("after_unknown") is not False
        ):
            raise GuardError(f"apply plan output {name} is unknown or mismatched")
    role_id = changes.get("watcher_role_unique_id")
    if (
        not isinstance(role_id, Mapping)
        or role_id.get("actions") != ["create"]
        or role_id.get("before") is not None
        or role_id.get("after") is not None
        or role_id.get("after_unknown") is not True
    ):
        raise GuardError("apply plan RoleId output unknown shape is mismatched")


def _validate_planned_bucket_controls(
    after: Mapping[str, Mapping[str, Any]],
    entries: Mapping[str, Mapping[str, Any]],
    *,
    bucket: str,
    role_arn: str,
) -> None:
    public = "aws_s3_bucket_public_access_block.snapshots"
    for attribute in (
        "block_public_acls",
        "block_public_policy",
        "ignore_public_acls",
        "restrict_public_buckets",
    ):
        _require_planned_value(after, entries, public, (attribute,), True)
    _require_planned_value(
        after,
        entries,
        "aws_s3_bucket_ownership_controls.snapshots",
        ("rule", 0, "object_ownership"),
        "BucketOwnerEnforced",
    )
    if len(after["aws_s3_bucket_ownership_controls.snapshots"].get("rule", [])) != 1:
        raise GuardError("apply plan ownership control inventory is mismatched")
    _require_planned_value(
        after,
        entries,
        "aws_s3_bucket_versioning.snapshots",
        ("versioning_configuration", 0, "status"),
        "Enabled",
    )
    versioning = after["aws_s3_bucket_versioning.snapshots"].get(
        "versioning_configuration"
    )
    if not isinstance(versioning, Sequence) or len(versioning) != 1:
        raise GuardError("apply plan versioning inventory is mismatched")
    mfa_delete = _nested_value(versioning, (0, "mfa_delete"))
    if mfa_delete not in {None, "Disabled"}:
        raise GuardError("apply plan MFA delete target is mismatched")
    _require_planned_value(
        after,
        entries,
        "aws_s3_bucket_server_side_encryption_configuration.snapshots",
        ("rule", 0, "apply_server_side_encryption_by_default", 0, "sse_algorithm"),
        "AES256",
    )
    encryption_rules = after[
        "aws_s3_bucket_server_side_encryption_configuration.snapshots"
    ].get("rule")
    if not isinstance(encryption_rules, Sequence) or len(encryption_rules) != 1:
        raise GuardError("apply plan encryption rule inventory is mismatched")
    encryption_defaults = _nested_value(
        encryption_rules, (0, "apply_server_side_encryption_by_default")
    )
    if not isinstance(encryption_defaults, Sequence) or len(encryption_defaults) != 1:
        raise GuardError("apply plan encryption default inventory is mismatched")
    if _nested_value(encryption_defaults, (0, "kms_master_key_id")) is not None:
        raise GuardError("apply plan encryption KMS target is unexpected")
    _validate_planned_policy(
        after,
        entries,
        "aws_s3_bucket_policy.snapshots",
        "policy",
        _expected_bucket_policy(bucket, role_arn),
    )
    _validate_planned_lifecycle(
        after["aws_s3_bucket_lifecycle_configuration.snapshots"],
        entries["aws_s3_bucket_lifecycle_configuration.snapshots"],
    )


def _validate_planned_lifecycle(
    after: Mapping[str, Any], entry: Mapping[str, Any]
) -> None:
    rules = after.get("rule")
    if not isinstance(rules, Sequence) or isinstance(rules, str) or len(rules) != 13:
        raise GuardError("apply plan lifecycle rule inventory is mismatched")
    identifiers: set[str] = set()
    rules_by_id: dict[str, tuple[int, Mapping[str, Any]]] = {}
    change = entry.get("change")
    unknown = change.get("after_unknown") if isinstance(change, Mapping) else None
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise GuardError("apply plan lifecycle rule is invalid")
        identifier = rule.get("id")
        if not isinstance(identifier, str) or identifier in identifiers:
            raise GuardError("apply plan lifecycle rule identity is invalid")
        identifiers.add(identifier)
        rules_by_id[identifier] = (index, rule)
        if rule.get("status") != "Enabled":
            raise GuardError(f"apply plan lifecycle rule {identifier} is disabled")
        for attribute in ("transition", "noncurrent_version_transition"):
            value = rule.get(attribute)
            if (value is not None and value != () and value != []) or _nested_unknown(
                unknown, ("rule", index, attribute)
            ):
                raise GuardError(
                    f"apply plan lifecycle rule {identifier} adds or may add a transition"
                )
        for field in ("id", "status"):
            if _nested_unknown(unknown, ("rule", index, field)):
                raise GuardError(f"apply plan lifecycle rule {identifier} is unknown")
    expected_ids = {
        "expire-run-recovery",
        "remove-run-recovery-delete-markers",
        "expire-daily-recovery",
        "remove-daily-recovery-delete-markers",
        "expire-weekly-recovery",
        "remove-weekly-recovery-delete-markers",
        "expire-monthly-recovery",
        "remove-monthly-recovery-delete-markers",
        "expire-trusted-recovery",
        "remove-trusted-recovery-delete-markers",
        "expire-checksum-probes",
        "remove-checksum-probe-delete-markers",
        "abort-incomplete-multipart-uploads",
    }
    if identifiers != expected_ids:
        raise GuardError("apply plan lifecycle rule inventory is mismatched")
    expirations = {
        "expire-run-recovery": ("control-recovery/run/", 30),
        "expire-daily-recovery": ("control-recovery/daily/", 31),
        "expire-weekly-recovery": ("control-recovery/weekly/", 91),
        "expire-monthly-recovery": ("control-recovery/monthly/", 400),
        "expire-trusted-recovery": ("trusted-recovery/", 400),
        "expire-checksum-probes": ("probes/", 7),
    }
    markers = {
        "remove-run-recovery-delete-markers": "control-recovery/run/",
        "remove-daily-recovery-delete-markers": "control-recovery/daily/",
        "remove-weekly-recovery-delete-markers": "control-recovery/weekly/",
        "remove-monthly-recovery-delete-markers": "control-recovery/monthly/",
        "remove-trusted-recovery-delete-markers": "trusted-recovery/",
        "remove-checksum-probe-delete-markers": "probes/",
    }
    for identifier, (prefix, days) in expirations.items():
        index, rule = rules_by_id[identifier]
        _require_lifecycle_value(rule, unknown, index, ("filter", 0, "prefix"), prefix)
        _require_lifecycle_value(rule, unknown, index, ("expiration", 0, "days"), days)
        _require_lifecycle_value(
            rule,
            unknown,
            index,
            ("noncurrent_version_expiration", 0, "noncurrent_days"),
            1,
        )
    for identifier, prefix in markers.items():
        index, rule = rules_by_id[identifier]
        _require_lifecycle_value(rule, unknown, index, ("filter", 0, "prefix"), prefix)
        _require_lifecycle_value(
            rule,
            unknown,
            index,
            ("expiration", 0, "expired_object_delete_marker"),
            True,
        )
    index, multipart = rules_by_id["abort-incomplete-multipart-uploads"]
    _require_lifecycle_value(
        multipart,
        unknown,
        index,
        ("abort_incomplete_multipart_upload", 0, "days_after_initiation"),
        7,
    )
    filter_value = _nested_value(multipart, ("filter", 0, "prefix"))
    if filter_value is not None:
        raise GuardError("apply plan multipart lifecycle prefix is mismatched")


def _require_lifecycle_value(
    rule: Mapping[str, Any],
    unknown: object,
    index: int,
    path: tuple[str | int, ...],
    expected: object,
) -> None:
    if _nested_value(rule, path) != expected or _nested_unknown(
        unknown, ("rule", index, *path)
    ):
        raise GuardError("apply plan lifecycle target is unknown or mismatched")


def validate_empty_state(document: Mapping[str, Any]) -> None:
    addresses = set(_managed_resources_allow_empty(document))
    if addresses:
        raise GuardError(
            f"setup state is not empty; managed resources={sorted(addresses)}"
        )


def runtime_document(
    *,
    account_id: str,
    region: str,
    bucket: str,
    operator_arn: str,
    permissions_boundary_arn: str,
    role_arn: str,
    role_id: str,
    role_name: str,
    run_id: str,
    object_manifest: str,
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": 3,
        "account_id": account_id,
        "region": region,
        "bucket": bucket,
        "operator_arn": operator_arn,
        "permissions_boundary_arn": permissions_boundary_arn,
        "role_arn": role_arn,
        "role_id": role_id,
        "role_name": role_name,
        "run_id": run_id,
        "object_manifest": object_manifest,
    }
    validate_runtime_document(document)
    return document


def validate_runtime_document(document: Mapping[str, object]) -> None:
    if set(document) != RUNTIME_KEYS or document.get("schema_version") != 3:
        raise GuardError("acceptance runtime JSON schema is invalid")
    account_id = document.get("account_id")
    region = document.get("region")
    run_id = document.get("run_id")
    bucket = document.get("bucket")
    role_name = document.get("role_name")
    role_arn = document.get("role_arn")
    role_id = document.get("role_id")
    operator_arn = document.get("operator_arn")
    permissions_boundary_arn = document.get("permissions_boundary_arn")
    object_manifest = document.get("object_manifest")
    if account_id != ACCOUNT_ID:
        raise GuardError("acceptance runtime account is invalid")
    if not isinstance(region, str) or REGION_PATTERN.fullmatch(region) is None:
        raise GuardError("acceptance runtime region is invalid")
    if not isinstance(run_id, str):
        raise GuardError("acceptance runtime run ID is invalid")
    if bucket != expected_bucket_name(run_id) or role_name != expected_role_name(
        run_id
    ):
        raise GuardError("acceptance runtime resource identity is invalid")
    if role_arn != f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}":
        raise GuardError("acceptance runtime role ARN is invalid")
    if not isinstance(role_id, str):
        raise GuardError("acceptance runtime RoleId is invalid")
    _validate_role_id(role_id)
    if not isinstance(operator_arn, str):
        raise GuardError("acceptance runtime operator ARN is invalid")
    _validate_operator_arn(operator_arn)
    if permissions_boundary_arn != EXPECTED_PERMISSIONS_BOUNDARY_ARN:
        raise GuardError("acceptance runtime permissions boundary is invalid")
    if not isinstance(object_manifest, str) or not object_manifest:
        raise GuardError("acceptance runtime object manifest path is invalid")


def load_runtime_document(path: str | Path) -> Mapping[str, Any]:
    document = _load_json(str(path))
    validate_runtime_document(document)
    return document


def write_json_atomic(path: str | Path, document: Mapping[str, object]) -> None:
    destination = Path(path)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            _ = stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def write_empty_object_manifest(
    path: str | Path, *, account_id: str, bucket: str, run_id: str
) -> None:
    _validate_run_id(run_id)
    if account_id != ACCOUNT_ID or bucket != expected_bucket_name(run_id):
        raise GuardError("cannot initialize a manifest for mismatched resources")
    write_json_atomic(
        path,
        {
            "schema_version": 2,
            "account_id": account_id,
            "bucket": bucket,
            "run_id": run_id,
            "objects": [],
        },
    )


def initialize_teardown_runtime(
    document: Mapping[str, Any],
    *,
    runtime_path: str | Path,
    manifest_path: str | Path,
    region: str,
    operator_arn: str,
) -> None:
    run_id, bucket, role_name, role_id = validate_state(
        document,
        region=region,
        operator_arn=operator_arn,
    )
    manifest = Path(manifest_path)
    if manifest.exists():
        _ = load_object_manifest(
            manifest,
            account_id=ACCOUNT_ID,
            bucket=bucket,
            run_id=run_id,
        )
    else:
        write_empty_object_manifest(
            manifest,
            account_id=ACCOUNT_ID,
            bucket=bucket,
            run_id=run_id,
        )
    write_json_atomic(
        runtime_path,
        runtime_document(
            account_id=ACCOUNT_ID,
            region=region,
            bucket=bucket,
            operator_arn=operator_arn,
            permissions_boundary_arn=EXPECTED_PERMISSIONS_BOUNDARY_ARN,
            role_arn=f"arn:aws:iam::{ACCOUNT_ID}:role/{role_name}",
            role_id=role_id,
            role_name=role_name,
            run_id=run_id,
            object_manifest=str(manifest_path),
        ),
    )


def _managed_resources(document: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    root = document.get("values")
    root_module = root.get("root_module") if isinstance(root, Mapping) else None
    if not isinstance(root_module, Mapping):
        raise GuardError("state does not contain a root module")
    pending = [root_module]
    result: dict[str, Mapping[str, Any]] = {}
    while pending:
        module = pending.pop()
        child_modules = module.get("child_modules", [])
        if not isinstance(child_modules, Sequence):
            raise GuardError("state contains invalid child modules")
        for child in child_modules:
            if not isinstance(child, Mapping):
                raise GuardError("state contains an invalid child module")
            pending.append(child)
        entries = module.get("resources", [])
        if not isinstance(entries, Sequence):
            raise GuardError("state contains invalid resources")
        for resource in entries:
            if not isinstance(resource, Mapping):
                raise GuardError("state contains an invalid resource")
            mode = resource.get("mode", "managed")
            if mode == "data":
                continue
            if mode != "managed":
                raise GuardError("state contains an unsupported resource mode")
            address = resource.get("address")
            values = resource.get("values")
            if not isinstance(address, str) or not isinstance(values, Mapping):
                raise GuardError("state contains an invalid managed resource")
            if address in result:
                raise GuardError(f"state contains duplicate resource {address}")
            result[address] = values
    return result


def _managed_resources_allow_empty(
    document: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    values = document.get("values")
    if values is None:
        return {}
    if not isinstance(values, Mapping):
        raise GuardError("state values are invalid")
    root_module = values.get("root_module")
    if root_module is None:
        return {}
    return _managed_resources(document)


def _validate_run_id(run_id: str) -> None:
    if RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise GuardError("test run ID must contain exactly 16 lowercase hex characters")


def _validate_role_id(role_id: str) -> None:
    if ROLE_ID_PATTERN.fullmatch(role_id) is None:
        raise GuardError("IAM RoleId must use the immutable AWS role identifier format")


def _validate_operator_arn(operator_arn: str) -> None:
    if operator_arn != EXPECTED_OPERATOR_ARN:
        raise GuardError("operator ARN does not match the tracked acceptance operator")


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name)
    if value is None or not value:
        raise GuardError(f"{name} is required for real-AWS acceptance")
    return value


def _required_account(environment: Mapping[str, str], name: str) -> str:
    value = _required_environment(environment, name)
    if re.fullmatch(r"[0-9]{12}", value) is None:
        raise GuardError(f"{name} must contain twelve digits")
    return value


def _expected_trust_policy(operator_arn: str) -> dict[str, object]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DedicatedAcceptancePrincipal",
                "Effect": "Allow",
                "Action": "sts:AssumeRole",
                "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT_ID}:root"},
                "Condition": {"ArnEquals": {"aws:PrincipalArn": operator_arn}},
            }
        ],
    }


def _expected_watcher_policy(bucket: str) -> dict[str, object]:
    bucket_arn = f"arn:aws:s3:::{bucket}"
    prefixes = ("blobs/sha256", "control-recovery", "probes")
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ListWatcherArchivePrefixes",
                "Effect": "Allow",
                "Action": ["s3:ListBucket", "s3:ListBucketVersions"],
                "Resource": bucket_arn,
                "Condition": {
                    "StringLike": {
                        "s3:prefix": [
                            value
                            for prefix in prefixes
                            for value in (prefix, f"{prefix}/*")
                        ]
                    }
                },
            },
            {
                "Sid": "ReadWatcherArchiveObjects",
                "Effect": "Allow",
                "Action": [
                    "s3:GetObject",
                    "s3:GetObjectAttributes",
                    "s3:GetObjectVersion",
                    "s3:GetObjectVersionAttributes",
                ],
                "Resource": [f"{bucket_arn}/{prefix}/*" for prefix in prefixes],
            },
            {
                "Sid": "CreateWatcherArchiveObjectsConditionally",
                "Effect": "Allow",
                "Action": "s3:PutObject",
                "Resource": [f"{bucket_arn}/{prefix}/*" for prefix in prefixes],
                "Condition": {"StringEquals": {"s3:if-none-match": "*"}},
            },
        ],
    }


def _expected_bucket_policy(bucket: str, role_arn: str) -> dict[str, object]:
    bucket_arn = f"arn:aws:s3:::{bucket}"
    immutable = (
        "blobs/sha256",
        "control-recovery",
        "deployment-receipts",
        "probes",
        "recovery-inventories",
        "release-manifests",
        "trusted-recovery",
    )
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "DenyWatcherObjectDestructionOrAclMutation",
                "Effect": "Deny",
                "Principal": {"AWS": role_arn},
                "Action": [
                    "s3:BypassGovernanceRetention",
                    "s3:DeleteObject",
                    "s3:DeleteObjectTagging",
                    "s3:DeleteObjectVersion",
                    "s3:DeleteObjectVersionTagging",
                    "s3:PutObjectAcl",
                    "s3:PutObjectLegalHold",
                    "s3:PutObjectRetention",
                    "s3:PutObjectTagging",
                    "s3:RestoreObject",
                ],
                "Resource": f"{bucket_arn}/*",
            },
            {
                "Sid": "DenyWatcherBucketAdministration",
                "Effect": "Deny",
                "Principal": {"AWS": role_arn},
                "Action": [
                    "s3:DeleteBucket",
                    "s3:DeleteBucketPolicy",
                    "s3:PutBucketAcl",
                    "s3:PutBucketOwnershipControls",
                    "s3:PutBucketPolicy",
                    "s3:PutBucketPublicAccessBlock",
                    "s3:PutBucketVersioning",
                    "s3:PutEncryptionConfiguration",
                    "s3:PutLifecycleConfiguration",
                    "s3:PutReplicationConfiguration",
                ],
                "Resource": bucket_arn,
            },
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [bucket_arn, f"{bucket_arn}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
            {
                "Sid": "DenyNonConditionalImmutableWrites",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": [f"{bucket_arn}/{prefix}/*" for prefix in immutable],
                "Condition": {
                    "Null": {"s3:if-none-match": "true"},
                    "Bool": {"s3:ObjectCreationOperation": "true"},
                },
            },
            {
                "Sid": "DenyExplicitNonSSES3Encryption",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:PutObject",
                "Resource": f"{bucket_arn}/*",
                "Condition": {
                    "Null": {"s3:x-amz-server-side-encryption": "false"},
                    "StringNotEquals": {"s3:x-amz-server-side-encryption": "AES256"},
                },
            },
        ],
    }


def _validate_policy(actual: object, expected: object, name: str) -> None:
    if isinstance(actual, str):
        actual = json.loads(actual)
    if not isinstance(actual, Mapping):
        raise GuardError(f"{name} is missing")
    if _canonical_policy(actual) != _canonical_policy(expected):
        raise GuardError(f"{name} is mismatched")


def _canonical_policy(value: object, key: str | None = None) -> object:
    if key in {"Action", "Resource"}:
        values = (
            list(value)
            if isinstance(value, Sequence) and not isinstance(value, str)
            else [value]
        )
        return sorted(
            (_canonical_policy(item) for item in values),
            key=lambda item: json.dumps(item, sort_keys=True),
        )
    if isinstance(value, Mapping):
        items = [
            (str(child_key), child_value) for child_key, child_value in value.items()
        ]
        return {
            child_key: _canonical_policy(child_value, child_key)
            for child_key, child_value in sorted(items, key=lambda item: item[0])
        }
    if isinstance(value, Sequence) and not isinstance(value, str):
        normalized = [_canonical_policy(item, key) for item in value]
        return sorted(normalized, key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _validate_bucket_controls(s3: S3Client, *, bucket: str, role_arn: str) -> None:
    owner = {"Bucket": bucket, "ExpectedBucketOwner": ACCOUNT_ID}
    versioning = s3.get_bucket_versioning(**owner)
    if versioning.get("Status") != "Enabled" or versioning.get("MFADelete") not in {
        None,
        "Disabled",
    }:
        raise GuardError("bucket versioning is not exactly enabled")
    public = s3.get_public_access_block(**owner).get("PublicAccessBlockConfiguration")
    if public != {
        "BlockPublicAcls": True,
        "BlockPublicPolicy": True,
        "IgnorePublicAcls": True,
        "RestrictPublicBuckets": True,
    }:
        raise GuardError("bucket public access controls are mismatched")
    ownership = s3.get_bucket_ownership_controls(**owner).get("OwnershipControls")
    if ownership != {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}:
        raise GuardError("bucket ownership controls are mismatched")
    encryption = s3.get_bucket_encryption(**owner).get(
        "ServerSideEncryptionConfiguration"
    )
    if not isinstance(encryption, Mapping):
        raise GuardError("bucket encryption configuration is missing")
    rules = encryption.get("Rules")
    if not isinstance(rules, Sequence) or len(rules) != 1:
        raise GuardError("bucket encryption rule inventory is mismatched")
    rule = rules[0]
    if not isinstance(rule, Mapping):
        raise GuardError("bucket encryption rule is invalid")
    default = rule.get("ApplyServerSideEncryptionByDefault")
    if default != {"SSEAlgorithm": "AES256"} or rule.get("BucketKeyEnabled") not in {
        None,
        False,
    }:
        raise GuardError("bucket encryption rule is mismatched")
    policy = s3.get_bucket_policy(**owner).get("Policy")
    _validate_policy(policy, _expected_bucket_policy(bucket, role_arn), "bucket policy")
    lifecycle = s3.get_bucket_lifecycle_configuration(**owner)
    _validate_lifecycle(lifecycle.get("Rules"))


def _validate_remaining_bucket_controls(
    s3: S3Client,
    *,
    bucket: str,
    role_arn: str,
    addresses: set[str],
) -> None:
    owner = {"Bucket": bucket, "ExpectedBucketOwner": ACCOUNT_ID}
    if "aws_s3_bucket_versioning.snapshots" in addresses:
        versioning = s3.get_bucket_versioning(**owner)
        if versioning.get("Status") != "Enabled" or versioning.get("MFADelete") not in {
            None,
            "Disabled",
        }:
            raise GuardError("remaining bucket versioning is mismatched")
    if "aws_s3_bucket_public_access_block.snapshots" in addresses:
        public = s3.get_public_access_block(**owner).get(
            "PublicAccessBlockConfiguration"
        )
        if public != {
            "BlockPublicAcls": True,
            "BlockPublicPolicy": True,
            "IgnorePublicAcls": True,
            "RestrictPublicBuckets": True,
        }:
            raise GuardError("remaining public access controls are mismatched")
    if "aws_s3_bucket_ownership_controls.snapshots" in addresses:
        ownership = s3.get_bucket_ownership_controls(**owner).get("OwnershipControls")
        if ownership != {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}:
            raise GuardError("remaining ownership controls are mismatched")
    if "aws_s3_bucket_server_side_encryption_configuration.snapshots" in addresses:
        encryption = s3.get_bucket_encryption(**owner).get(
            "ServerSideEncryptionConfiguration"
        )
        if not isinstance(encryption, Mapping):
            raise GuardError("remaining encryption configuration is missing")
        rules = encryption.get("Rules")
        if not isinstance(rules, Sequence) or len(rules) != 1:
            raise GuardError("remaining encryption rule inventory is mismatched")
        rule = rules[0]
        if (
            not isinstance(rule, Mapping)
            or rule.get("ApplyServerSideEncryptionByDefault")
            != {"SSEAlgorithm": "AES256"}
            or rule.get("BucketKeyEnabled") not in {None, False}
        ):
            raise GuardError("remaining encryption rule is mismatched")
    if "aws_s3_bucket_policy.snapshots" in addresses:
        _validate_policy(
            s3.get_bucket_policy(**owner).get("Policy"),
            _expected_bucket_policy(bucket, role_arn),
            "remaining bucket policy",
        )
    if "aws_s3_bucket_lifecycle_configuration.snapshots" in addresses:
        _validate_lifecycle(s3.get_bucket_lifecycle_configuration(**owner).get("Rules"))


def _validate_lifecycle(value: object) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise GuardError("bucket lifecycle rule inventory is mismatched")
    expirations = {
        "expire-run-recovery": ("control-recovery/run/", 30),
        "expire-daily-recovery": ("control-recovery/daily/", 31),
        "expire-weekly-recovery": ("control-recovery/weekly/", 91),
        "expire-monthly-recovery": ("control-recovery/monthly/", 400),
        "expire-trusted-recovery": ("trusted-recovery/", 400),
        "expire-checksum-probes": ("probes/", 7),
    }
    markers = {
        "remove-run-recovery-delete-markers": "control-recovery/run/",
        "remove-daily-recovery-delete-markers": "control-recovery/daily/",
        "remove-weekly-recovery-delete-markers": "control-recovery/weekly/",
        "remove-monthly-recovery-delete-markers": "control-recovery/monthly/",
        "remove-trusted-recovery-delete-markers": "trusted-recovery/",
        "remove-checksum-probe-delete-markers": "probes/",
    }
    expected: list[dict[str, object]] = [
        {
            "ID": identifier,
            "Status": "Enabled",
            "Filter": {"Prefix": prefix},
            "Expiration": {"Days": days},
            "NoncurrentVersionExpiration": {"NoncurrentDays": 1},
        }
        for identifier, (prefix, days) in expirations.items()
    ]
    expected.extend(
        {
            "ID": identifier,
            "Status": "Enabled",
            "Filter": {"Prefix": prefix},
            "Expiration": {"ExpiredObjectDeleteMarker": True},
        }
        for identifier, prefix in markers.items()
    )
    expected.append(
        {
            "ID": "abort-incomplete-multipart-uploads",
            "Status": "Enabled",
            "Filter": {},
            "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
        }
    )
    actual_by_id: dict[str, object] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise GuardError("bucket lifecycle rules are mismatched")
        identifier = item.get("ID")
        if not isinstance(identifier, str) or identifier in actual_by_id:
            raise GuardError("bucket lifecycle rules are mismatched")
        actual_by_id[identifier] = item
    expected_by_id = {str(item["ID"]): item for item in expected}
    if actual_by_id != expected_by_id:
        raise GuardError("bucket lifecycle rules are mismatched")


def _tag_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, Sequence):
        raise GuardError("AWS resource is missing ownership tags")
    tags: dict[str, str] = {}
    for item in value:
        if not isinstance(item, Mapping):
            raise GuardError("AWS resource contains an invalid tag")
        key = item.get("Key")
        tag_value = item.get("Value")
        if not isinstance(key, str) or not isinstance(tag_value, str) or key in tags:
            raise GuardError("AWS resource contains an invalid or duplicate tag")
        tags[key] = tag_value
    return tags


def _validate_tags(tags: Mapping[str, object], run_id: str, resource: str) -> None:
    expected = {**REQUIRED_TAGS, "TestRunId": run_id}
    if dict(tags) != expected:
        raise GuardError(f"{resource} ownership tags are not exact")


def _string_value(values: Mapping[str, Any], attribute: str) -> str:
    value = values.get(attribute)
    if not isinstance(value, str):
        raise GuardError(f"state attribute {attribute} is missing or invalid")
    return value


def _require_resource_value(
    resources: Mapping[str, Mapping[str, Any]],
    address: str,
    attribute: str,
    expected: str,
) -> None:
    actual = _string_value(resources[address], attribute)
    if actual != expected:
        raise GuardError(f"state identity {address}.{attribute} is mismatched")


def _require_output(document: Mapping[str, Any], name: str, expected: str) -> None:
    values = document.get("values")
    outputs = values.get("outputs") if isinstance(values, Mapping) else None
    output = outputs.get(name) if isinstance(outputs, Mapping) else None
    actual = output.get("value") if isinstance(output, Mapping) else None
    if actual != expected:
        raise GuardError(f"state output {name} is missing or mismatched")


def _load_json(path: str) -> Mapping[str, Any]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise GuardError(f"{path} does not contain a JSON object")
    return cast(Mapping[str, Any], document)


def _aws_clients(profile: str, region: str) -> tuple[StsClient, S3Client, IamClient]:
    config = Config(
        connect_timeout=10,
        read_timeout=60,
        retries={"total_max_attempts": 1, "mode": "standard"},
    )
    session = boto3.Session(profile_name=profile, region_name=region)
    return (
        cast(StsClient, session.client("sts", config=config)),
        cast(S3Client, session.client("s3", config=config)),
        cast(IamClient, session.client("iam", config=config)),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    state = subparsers.add_parser("validate-state")
    _ = state.add_argument("state_json")
    _ = state.add_argument("--region", required=True)
    _ = state.add_argument("--operator-arn", required=True)

    plan = subparsers.add_parser("validate-destroy-plan")
    _ = plan.add_argument("plan_json")
    _ = plan.add_argument("--region", required=True)
    _ = plan.add_argument("--operator-arn", required=True)
    _ = plan.add_argument("--role-id", required=True)
    _ = plan.add_argument("--current-state-json", required=True)

    apply_plan = subparsers.add_parser("validate-apply-plan")
    _ = apply_plan.add_argument("plan_json")
    _ = apply_plan.add_argument("--region", required=True)
    _ = apply_plan.add_argument("--run-id", required=True)

    empty_state = subparsers.add_parser("validate-empty-state")
    _ = empty_state.add_argument("state_json")

    write_runtime = subparsers.add_parser("write-runtime")
    _ = write_runtime.add_argument("runtime_json")
    for name in (
        "account-id",
        "region",
        "bucket",
        "operator-arn",
        "permissions-boundary-arn",
        "role-arn",
        "role-id",
        "role-name",
        "run-id",
        "object-manifest",
    ):
        _ = write_runtime.add_argument(f"--{name}", required=True)

    runtime_field = subparsers.add_parser("runtime-field")
    _ = runtime_field.add_argument("runtime_json")
    _ = runtime_field.add_argument(
        "field", choices=sorted(RUNTIME_KEYS - {"schema_version"})
    )

    manifest = subparsers.add_parser("initialize-object-manifest")
    _ = manifest.add_argument("manifest_json")
    _ = manifest.add_argument("--account-id", required=True)
    _ = manifest.add_argument("--bucket", required=True)
    _ = manifest.add_argument("--run-id", required=True)

    initialize_run = subparsers.add_parser("initialize-run-identity")
    _ = initialize_run.add_argument("run_json")
    read_run = subparsers.add_parser("read-run-identity")
    _ = read_run.add_argument("run_json")

    recover_runtime = subparsers.add_parser("initialize-teardown-runtime")
    _ = recover_runtime.add_argument("state_json")
    _ = recover_runtime.add_argument("runtime_json")
    _ = recover_runtime.add_argument("manifest_json")
    _ = recover_runtime.add_argument("--region", required=True)
    _ = recover_runtime.add_argument("--operator-arn", required=True)

    create_targets = subparsers.add_parser("verify-create-targets-absent")
    _ = create_targets.add_argument("--profile", required=True)
    _ = create_targets.add_argument("--region", required=True)
    _ = create_targets.add_argument("--run-id", required=True)
    _ = create_targets.add_argument("--operator-arn", required=True)

    for command in (
        "verify-aws",
        "writer-status",
        "revoke-writer",
        "verify-teardown",
        "cleanup-bucket",
    ):
        aws = subparsers.add_parser(command)
        _ = aws.add_argument("--profile", required=True)
        _ = aws.add_argument("--region", required=True)
        _ = aws.add_argument("--bucket", required=True)
        _ = aws.add_argument("--role-name", required=True)
        _ = aws.add_argument("--role-id", required=True)
        _ = aws.add_argument("--run-id", required=True)
        _ = aws.add_argument("--operator-arn", required=True)
        if command == "cleanup-bucket":
            _ = aws.add_argument("--object-manifest", required=True)
            _ = aws.add_argument("--state-json", required=True)
            _ = aws.add_argument("--production-confirmation", required=True)
        if command == "revoke-writer":
            _ = aws.add_argument("--revoke-confirmation", required=True)
            _ = aws.add_argument("--production-confirmation", required=True)
        if command == "verify-teardown":
            _ = aws.add_argument("--state-json", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        if args.command == "validate-state":
            _ = validate_state(
                _load_json(args.state_json),
                region=args.region,
                operator_arn=args.operator_arn,
            )
        elif args.command == "validate-destroy-plan":
            validate_destroy_plan(
                _load_json(args.plan_json),
                region=args.region,
                operator_arn=args.operator_arn,
                role_id=args.role_id,
                current_state=_load_json(args.current_state_json),
            )
        elif args.command == "validate-apply-plan":
            validate_apply_plan(
                _load_json(args.plan_json),
                region=args.region,
                run_id=args.run_id,
            )
        elif args.command == "validate-empty-state":
            validate_empty_state(_load_json(args.state_json))
        elif args.command == "write-runtime":
            write_json_atomic(
                args.runtime_json,
                runtime_document(
                    account_id=args.account_id,
                    region=args.region,
                    bucket=args.bucket,
                    operator_arn=args.operator_arn,
                    permissions_boundary_arn=args.permissions_boundary_arn,
                    role_arn=args.role_arn,
                    role_id=args.role_id,
                    role_name=args.role_name,
                    run_id=args.run_id,
                    object_manifest=args.object_manifest,
                ),
            )
        elif args.command == "runtime-field":
            document = load_runtime_document(args.runtime_json)
            value = document.get(args.field)
            if not isinstance(value, str):
                raise GuardError("acceptance runtime field is not a string")
            print(value)
        elif args.command == "initialize-object-manifest":
            write_empty_object_manifest(
                args.manifest_json,
                account_id=args.account_id,
                bucket=args.bucket,
                run_id=args.run_id,
            )
        elif args.command == "initialize-run-identity":
            write_json_atomic(
                args.run_json,
                {"schema_version": 1, "run_id": secrets.token_hex(8)},
            )
        elif args.command == "read-run-identity":
            run_document = _load_json(args.run_json)
            if (
                set(run_document) != {"run_id", "schema_version"}
                or run_document.get("schema_version") != 1
            ):
                raise GuardError("acceptance run identity schema is invalid")
            run_id = run_document.get("run_id")
            if not isinstance(run_id, str):
                raise GuardError("acceptance run identity is invalid")
            _validate_run_id(run_id)
            print(run_id)
        elif args.command == "initialize-teardown-runtime":
            initialize_teardown_runtime(
                _load_json(args.state_json),
                runtime_path=args.runtime_json,
                manifest_path=args.manifest_json,
                region=args.region,
                operator_arn=args.operator_arn,
            )
        elif args.command == "verify-create-targets-absent":
            sts, s3, iam = _aws_clients(args.profile, args.region)
            validate_create_targets_absent(
                sts,
                s3,
                iam,
                run_id=args.run_id,
                operator_arn=args.operator_arn,
            )
        else:
            sts, s3, iam = _aws_clients(args.profile, args.region)
            if args.command == "verify-teardown":
                phase, bucket_present, role_present = (
                    validate_partial_teardown_resources(
                        sts,
                        s3,
                        iam,
                        _load_json(args.state_json),
                        run_id=args.run_id,
                        role_id=args.role_id,
                        operator_arn=args.operator_arn,
                        region=args.region,
                    )
                )
                print(
                    f"{phase}:{str(bucket_present).lower()}:{str(role_present).lower()}"
                )
                return 0
            if args.command == "cleanup-bucket":
                if args.production_confirmation != PRODUCTION_CONFIRMATION:
                    raise GuardError("production acceptance confirmation is not exact")
                _, bucket_present, _ = validate_partial_teardown_resources(
                    sts,
                    s3,
                    iam,
                    _load_json(args.state_json),
                    run_id=args.run_id,
                    role_id=args.role_id,
                    operator_arn=args.operator_arn,
                    region=args.region,
                )
                if not bucket_present:
                    raise GuardError(
                        "teardown state does not contain a bucket to clean"
                    )
                allowed_objects = load_object_manifest(
                    args.object_manifest,
                    account_id=ACCOUNT_ID,
                    bucket=args.bucket,
                    run_id=args.run_id,
                    allow_missing=True,
                )
                deleted = empty_bucket_bounded(
                    s3,
                    bucket=args.bucket,
                    allowed_objects=allowed_objects,
                )
                print(f"Deleted {deleted} verified disposable bucket object versions.")
                return 0
            writer_expectation = (
                "either" if args.command == "writer-status" else "active"
            )
            writer_state = validate_aws_resources(
                sts,
                s3,
                iam,
                bucket=args.bucket,
                role_name=args.role_name,
                role_id=args.role_id,
                run_id=args.run_id,
                operator_arn=args.operator_arn,
                region=args.region,
                expected_writer_state=writer_expectation,
            )
            if args.command == "writer-status":
                print(writer_state)
            elif args.command == "revoke-writer":
                if args.production_confirmation != PRODUCTION_CONFIRMATION:
                    raise GuardError("production acceptance confirmation is not exact")
                expected = f"REVOKE:{ACCOUNT_ID}:{args.run_id}"
                if args.revoke_confirmation != expected:
                    raise GuardError("writer revocation confirmation is not exact")
                _ = iam.delete_role_policy(
                    RoleName=args.role_name,
                    PolicyName=f"{args.role_name}-archive",
                )
                _ = validate_aws_resources(
                    sts,
                    s3,
                    iam,
                    bucket=args.bucket,
                    role_name=args.role_name,
                    role_id=args.role_id,
                    run_id=args.run_id,
                    operator_arn=args.operator_arn,
                    region=args.region,
                    expected_writer_state="revoked",
                )
    except (
        BotoCoreError,
        ClientError,
        GuardError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise SystemExit(f"error: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
