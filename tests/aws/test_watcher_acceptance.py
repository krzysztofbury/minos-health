# pyright: reportAny=false, reportExplicitAny=false, reportMissingTypeStubs=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import base64
import hashlib
import secrets
import time
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, cast

import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from minos.ingest.watchers.archive import BlobStagingArea, S3BlobArchive
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import ControlStore, SourceConfiguration
from minos.ingest.watchers.preflight import S3ChecksumProbe
from minos.ingest.watchers.recovery import (
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    RecoveryListingBoundError,
    S3RecoveryStore,
)
from minos.ingest.watchers.recovery.inventory import (
    MAX_INVENTORY_BYTES,
    ConditionalWriteResult,
    TrustedCopyRequest,
    VerifiedRecoverySource,
)
from minos.ingest.watchers.recovery.operator_s3 import S3OperatorRecoveryStorage
from minos.ingest.watchers.recovery.trusted_keys import (
    recovery_inventory_key,
    trusted_recovery_key,
)

from .harness import AwsHarness

pytestmark = pytest.mark.aws


def test_blob_archive_create_reuse_native_sha256_and_expected_owner(
    aws_harness: AwsHarness,
    tmp_path: Path,
) -> None:
    namespace = _random_namespace()
    content = f"minos-watcher-{aws_harness.run_id}-{namespace}".encode()
    checksum = _checksum(content)
    archive = S3BlobArchive(
        aws_harness.watcher_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
        conflict_delay_seconds=0,
    )
    staging = BlobStagingArea(tmp_path / f"blob-{namespace}")

    with staging.stage() as writer:
        _ = writer.write(content)
        created = archive.put(writer.seal(media_type="application/octet-stream"))
    with staging.stage() as writer:
        _ = writer.write(content)
        reused = archive.put(writer.seal(media_type="application/octet-stream"))

    assert reused == created
    assert created.archive_key == f"blobs/sha256/{hashlib.sha256(content).hexdigest()}"
    assert created.verified_sha256 == created.sha256
    assert created.verified_size_bytes == len(content)
    key = created.archive_key
    head = aws_harness.watcher_s3.head_object(
        Bucket=aws_harness.bucket,
        Key=key,
        ChecksumMode="ENABLED",
        ExpectedBucketOwner=aws_harness.account_id,
    )
    version_id = head.get("VersionId")
    assert isinstance(version_id, str) and version_id
    assert head["VersionId"] == version_id
    assert head["ContentLength"] == len(content)
    assert head["ChecksumSHA256"] == checksum
    assert head["ChecksumType"] == "FULL_OBJECT"
    assert head["ServerSideEncryption"] == "AES256"

    wrong_owner = (
        "0" if aws_harness.account_id[0] != "0" else "1"
    ) + aws_harness.account_id[1:]
    _expect_client_error(
        403,
        lambda: aws_harness.watcher_s3.head_object(
            Bucket=aws_harness.bucket,
            Key=key,
            ChecksumMode="ENABLED",
            ExpectedBucketOwner=wrong_owner,
        ),
    )

    mutable_key = f"harness/{namespace}/versioning"
    first = _admin_put(aws_harness, mutable_key, b"first")
    second = _admin_put(aws_harness, mutable_key, b"second")
    assert first["VersionId"] != second["VersionId"]
    versions = aws_harness.admin_s3.list_object_versions(
        Bucket=aws_harness.bucket,
        Prefix=mutable_key,
        ExpectedBucketOwner=aws_harness.account_id,
    )
    assert {item["VersionId"] for item in versions["Versions"]} == {
        first["VersionId"],
        second["VersionId"],
    }


def test_watcher_overwrite_delete_policy_and_operator_prefixes_are_denied(
    aws_harness: AwsHarness,
    tmp_path: Path,
) -> None:
    namespace = _random_namespace()
    content = f"immutable-{namespace}".encode()
    archive = S3BlobArchive(
        aws_harness.watcher_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
        conflict_delay_seconds=0,
    )
    with BlobStagingArea(tmp_path / f"denial-{namespace}").stage() as writer:
        _ = writer.write(content)
        receipt = archive.put(writer.seal(media_type=None))
    key = receipt.archive_key
    head = aws_harness.watcher_s3.head_object(
        Bucket=aws_harness.bucket,
        Key=key,
        ChecksumMode="ENABLED",
        ExpectedBucketOwner=aws_harness.account_id,
    )
    version_id = head["VersionId"]
    assert isinstance(version_id, str) and version_id

    _expect_client_error(
        403,
        lambda: aws_harness.watcher_s3.put_object(
            Bucket=aws_harness.bucket,
            Key=key,
            Body=b"overwrite",
            ContentLength=9,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=_checksum(b"overwrite"),
            ExpectedBucketOwner=aws_harness.account_id,
        ),
    )
    _expect_client_error(
        403,
        lambda: aws_harness.watcher_s3.delete_object(
            Bucket=aws_harness.bucket,
            Key=key,
            VersionId=version_id,
            ExpectedBucketOwner=aws_harness.account_id,
        ),
    )

    policy = aws_harness.admin_s3.get_bucket_policy(
        Bucket=aws_harness.bucket,
        ExpectedBucketOwner=aws_harness.account_id,
    )["Policy"]
    _expect_client_error(
        403,
        lambda: aws_harness.watcher_s3.get_bucket_policy(
            Bucket=aws_harness.bucket,
            ExpectedBucketOwner=aws_harness.account_id,
        ),
    )
    _expect_client_error(
        403,
        lambda: aws_harness.watcher_s3.put_bucket_policy(
            Bucket=aws_harness.bucket,
            Policy=policy,
            ExpectedBucketOwner=aws_harness.account_id,
        ),
    )

    forbidden_keys = (
        f"terraform-state/{namespace}.tfstate",
        f"trusted-recovery/{namespace}/object.sqlite3",
        f"recovery-inventories/{namespace}.json",
        f"release-manifests/{namespace}.json",
        f"deployment-receipts/{namespace}.json",
    )
    for forbidden_key in forbidden_keys:
        _expect_client_error(
            403,
            partial(
                _conditional_put,
                aws_harness,
                forbidden_key,
                b"must-be-denied",
            ),
        )
        _expect_client_error(
            403,
            partial(
                aws_harness.watcher_s3.get_object,
                Bucket=aws_harness.bucket,
                Key=forbidden_key,
                ExpectedBucketOwner=aws_harness.account_id,
            ),
        )


def test_lifecycle_configuration_is_exact(aws_harness: AwsHarness) -> None:
    response = aws_harness.admin_s3.get_bucket_lifecycle_configuration(
        Bucket=aws_harness.bucket,
        ExpectedBucketOwner=aws_harness.account_id,
    )
    rules = {rule["ID"]: rule for rule in response["Rules"]}
    expected_expirations = {
        "expire-run-recovery": ("control-recovery/run/", 30),
        "expire-daily-recovery": ("control-recovery/daily/", 31),
        "expire-weekly-recovery": ("control-recovery/weekly/", 91),
        "expire-monthly-recovery": ("control-recovery/monthly/", 400),
        "expire-trusted-recovery": ("trusted-recovery/", 400),
        "expire-checksum-probes": ("probes/", 7),
    }
    expected_markers = {
        "remove-run-recovery-delete-markers": "control-recovery/run/",
        "remove-daily-recovery-delete-markers": "control-recovery/daily/",
        "remove-weekly-recovery-delete-markers": "control-recovery/weekly/",
        "remove-monthly-recovery-delete-markers": "control-recovery/monthly/",
        "remove-trusted-recovery-delete-markers": "trusted-recovery/",
        "remove-checksum-probe-delete-markers": "probes/",
    }
    assert set(rules) == {
        *expected_expirations,
        *expected_markers,
        "abort-incomplete-multipart-uploads",
    }
    for identifier, (prefix, days) in expected_expirations.items():
        rule = rules[identifier]
        assert rule["Status"] == "Enabled"
        assert rule["Filter"] == {"Prefix": prefix}
        assert rule["Expiration"] == {"Days": days}
        assert rule["NoncurrentVersionExpiration"] == {"NoncurrentDays": 1}
    for identifier, prefix in expected_markers.items():
        rule = rules[identifier]
        assert rule["Status"] == "Enabled"
        assert rule["Filter"] == {"Prefix": prefix}
        assert rule["Expiration"] == {"ExpiredObjectDeleteMarker": True}
    multipart = rules["abort-incomplete-multipart-uploads"]
    assert multipart["Status"] == "Enabled"
    assert multipart["Filter"] == {}
    assert multipart["AbortIncompleteMultipartUpload"] == {"DaysAfterInitiation": 7}


def test_real_pagination_fails_closed_and_recovery_download_is_version_pinned(
    aws_harness: AwsHarness,
    tmp_path: Path,
) -> None:
    namespace = _random_namespace()
    remote = S3RecoveryStore(
        aws_harness.watcher_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
        conflict_delay_seconds=0,
    )
    staging = RecoveryImageStagingArea(tmp_path / f"staging-{namespace}")
    store = ControlStore.open(
        tmp_path / f"control-{namespace}.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    published = []
    try:
        _ = store.configure_source(
            SourceConfiguration(f"accept-{namespace}", "rsk", {}, True)
        )
        created_at_ms = time.time_ns() // 1_000_000 - 5000
        for offset in range(3):
            with staging.create(
                store,
                RecoveryImageRequest("daily", created_at_ms + offset),
            ) as image:
                published.append(remote.publish(image))

        listed = remote.list_images("daily", maximum_objects=3, page_size=1)
        assert {item.key for item in listed} == {item.key for item in published}
        with pytest.raises(RecoveryListingBoundError, match="object bound"):
            _ = remote.list_images("daily", maximum_objects=2, page_size=1)

        selected = published[0]
        marker = aws_harness.admin_s3.delete_object(
            Bucket=aws_harness.bucket,
            Key=selected.key,
            ExpectedBucketOwner=aws_harness.account_id,
        )
        assert marker["DeleteMarker"] is True
        assert marker["VersionId"] != selected.version_id
        with remote.download_published_verified(selected, staging) as verified:
            assert verified.candidate.version_id == selected.version_id
            assert verified.candidate.sha256 == selected.sha256
            assert verified.path.is_file()
    finally:
        store.close()


def test_operator_storage_conditionally_copies_and_reuses_inventory(
    aws_harness: AwsHarness,
    tmp_path: Path,
) -> None:
    namespace = _random_namespace()
    recovery_store = S3RecoveryStore(
        aws_harness.watcher_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
        conflict_delay_seconds=0,
    )
    staging = RecoveryImageStagingArea(tmp_path / f"operator-{namespace}")
    control = ControlStore.open(
        tmp_path / f"operator-{namespace}.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    try:
        _ = control.configure_source(
            SourceConfiguration(f"accept-{namespace}", "rsk", {}, True)
        )
        with staging.create(
            control,
            RecoveryImageRequest("daily", time.time_ns() // 1_000_000),
        ) as image:
            published = recovery_store.publish(image)
    finally:
        control.close()

    source = VerifiedRecoverySource(
        published.key,
        published.version_id,
        published.sha256,
        published.size_bytes,
        published.metadata.state_revision,
    )
    sequence = _random_sequence()
    trusted_key = trusted_recovery_key(sequence, source.sha256)
    operator = S3OperatorRecoveryStorage(
        aws_harness.admin_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
        page_size=2,
    )
    request = TrustedCopyRequest(source, trusted_key)
    assert operator.copy_trusted_if_absent(request) is ConditionalWriteResult.CREATED
    assert (
        operator.copy_trusted_if_absent(request)
        is ConditionalWriteResult.PRECONDITION_FAILED
    )
    trusted = operator.head_trusted(trusted_key)
    assert trusted.sha256 == source.sha256
    assert trusted.size_bytes == source.size_bytes
    assert trusted.version_id

    inventory_key = recovery_inventory_key(sequence)
    inventory = f'{{"test_namespace":"{namespace}"}}'.encode()
    assert (
        operator.put_inventory_if_absent(inventory_key, inventory)
        is ConditionalWriteResult.CREATED
    )
    assert (
        operator.put_inventory_if_absent(inventory_key, inventory)
        is ConditionalWriteResult.PRECONDITION_FAILED
    )
    assert (
        operator.read_inventory(
            inventory_key,
            maximum_bytes=MAX_INVENTORY_BYTES,
        )
        == inventory
    )
    listing = operator.list_inventories(maximum_objects=128)
    assert listing.complete
    assert inventory_key in {item.key for item in listing.objects}

    inventory_head = aws_harness.admin_s3.head_object(
        Bucket=aws_harness.bucket,
        Key=inventory_key,
        ChecksumMode="ENABLED",
        ExpectedBucketOwner=aws_harness.account_id,
    )
    assert inventory_head["ChecksumSHA256"] == _checksum(inventory)
    assert inventory_head["ChecksumType"] == "FULL_OBJECT"
    assert isinstance(inventory_head["VersionId"], str)
    assert inventory_head["VersionId"]


def test_checksum_probe_uses_a_conditional_native_checksum_object(
    aws_harness: AwsHarness,
) -> None:
    before = _list_keys(aws_harness, "probes/")
    S3ChecksumProbe(
        aws_harness.watcher_s3,
        bucket=aws_harness.bucket,
        expected_bucket_owner=aws_harness.account_id,
    ).check()
    created = _list_keys(aws_harness, "probes/") - before
    assert len(created) == 1
    head = aws_harness.watcher_s3.head_object(
        Bucket=aws_harness.bucket,
        Key=created.pop(),
        ChecksumMode="ENABLED",
        ExpectedBucketOwner=aws_harness.account_id,
    )
    assert head["ContentLength"] == 32
    assert head["ChecksumType"] == "FULL_OBJECT"
    assert isinstance(head["ChecksumSHA256"], str)


def test_resources_are_tagged_bounded_and_ready_for_guarded_destroy(
    aws_harness: AwsHarness,
) -> None:
    tags = {
        item["Key"]: item["Value"]
        for item in aws_harness.admin_s3.get_bucket_tagging(
            Bucket=aws_harness.bucket,
            ExpectedBucketOwner=aws_harness.account_id,
        )["TagSet"]
    }
    assert tags["Disposable"] == "true"
    assert tags["Environment"] == "acceptance"
    assert tags["ManagedBy"] == "Terraform"
    assert tags["Project"] == "minos-health"
    assert tags["TestHarness"] == "watcher-acceptance"
    assert tags["TestRunId"] == aws_harness.run_id

    uploads = aws_harness.admin_s3.list_multipart_uploads(
        Bucket=aws_harness.bucket,
        MaxUploads=1000,
        ExpectedBucketOwner=aws_harness.account_id,
    )
    assert uploads.get("Uploads", []) == []
    keys = _bounded_version_keys(aws_harness, maximum_items=128)
    assert all(
        key.startswith(
            (
                "blobs/sha256/",
                "control-recovery/",
                "harness/",
                "probes/",
                "recovery-inventories/",
                "trusted-recovery/",
            )
        )
        for key in keys
    )
    assert (
        aws_harness.admin_iam.list_attached_role_policies(
            RoleName=aws_harness.role_name,
        )["AttachedPolicies"]
        == []
    )
    assert aws_harness.admin_iam.list_role_policies(RoleName=aws_harness.role_name)[
        "PolicyNames"
    ] == [f"minos-health-watcher-acceptance-{aws_harness.run_id}-watcher-archive"]
    _expect_client_error(
        404,
        lambda: aws_harness.admin_iam.get_user(UserName=aws_harness.role_name),
    )


def _conditional_put(
    harness: AwsHarness,
    key: str,
    content: bytes,
) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        harness.watcher_s3.put_object(
            Bucket=harness.bucket,
            Key=key,
            Body=content,
            ContentLength=len(content),
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=_checksum(content),
            IfNoneMatch="*",
            ExpectedBucketOwner=harness.account_id,
        ),
    )


def _admin_put(harness: AwsHarness, key: str, content: bytes) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        harness.admin_s3.put_object(
            Bucket=harness.bucket,
            Key=key,
            Body=content,
            ContentLength=len(content),
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=_checksum(content),
            ExpectedBucketOwner=harness.account_id,
        ),
    )


def _checksum(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def _random_namespace() -> str:
    return secrets.token_hex(16)


def _random_sequence() -> int:
    return 10**19 + secrets.randbelow(9 * 10**19)


def _expect_client_error(status: int, operation: Callable[[], object]) -> None:
    with pytest.raises(ClientError) as captured:
        _ = operation()
    assert captured.value.response["ResponseMetadata"]["HTTPStatusCode"] == status


def _list_keys(harness: AwsHarness, prefix: str) -> set[str]:
    response = harness.watcher_s3.list_objects_v2(
        Bucket=harness.bucket,
        Prefix=prefix,
        MaxKeys=100,
        ExpectedBucketOwner=harness.account_id,
    )
    assert response["IsTruncated"] is False
    return {item["Key"] for item in response.get("Contents", [])}


def _bounded_version_keys(harness: AwsHarness, *, maximum_items: int) -> list[str]:
    keys: list[str] = []
    key_marker: str | None = None
    version_marker: str | None = None
    for _page in range(8):
        parameters: dict[str, object] = {
            "Bucket": harness.bucket,
            "MaxKeys": min(maximum_items + 1, 1000),
            "ExpectedBucketOwner": harness.account_id,
        }
        if key_marker is not None:
            parameters["KeyMarker"] = key_marker
        if version_marker is not None:
            parameters["VersionIdMarker"] = version_marker
        response = harness.admin_s3.list_object_versions(**parameters)
        keys.extend(item["Key"] for item in response.get("Versions", []))
        keys.extend(item["Key"] for item in response.get("DeleteMarkers", []))
        if len(keys) > maximum_items:
            pytest.fail("disposable bucket exceeded the cleanup object bound")
        if response["IsTruncated"] is False:
            return keys
        key_marker = response.get("NextKeyMarker")
        version_marker = response.get("NextVersionIdMarker")
        assert isinstance(key_marker, str) and key_marker
        assert isinstance(version_marker, str) and version_marker
    pytest.fail("disposable bucket version listing exceeded the cleanup page bound")
