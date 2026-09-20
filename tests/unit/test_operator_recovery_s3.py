# pyright: reportMissingTypeStubs=false

import base64
import hashlib
import io
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol, cast, final

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped]
    ClientError,
    ReadTimeoutError,
)

from minos.ingest.watchers.recovery.inventory import (
    MAX_INVENTORY_BYTES,
    ConditionalWriteResult,
    RecoveryInventoryStorageError,
    TrustedCopyRequest,
    TrustedObjectIdentity,
    VerifiedRecoverySource,
    append_trusted_recovery,
)
from minos.ingest.watchers.recovery.operator_s3 import S3OperatorRecoveryStorage
from minos.ingest.watchers.recovery.trusted_keys import (
    recovery_inventory_key,
    trusted_recovery_key,
)

_BUCKET = "minos-operator-test"
_OWNER = "123456789012"
_LAST_MODIFIED = datetime(2025, 9, 20, tzinfo=UTC)


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _InjectedPrimaryFailure(BaseException):
    pass


@final
class _FaultingBody:
    def __init__(
        self,
        content: bytes,
        *,
        read_error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._content = content
        self._read_error = read_error
        self._close_error = close_error
        self.closed = False

    def read(self, _size: int = -1, /) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        content = self._content
        self._content = b""
        return content

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


@final
class _FakeConfig:
    def __init__(self) -> None:
        self.retries: Mapping[str, object] = {"total_max_attempts": 1}


@final
class _FakeMeta:
    def __init__(self) -> None:
        self.config = _FakeConfig()


@final
class _Stored:
    def __init__(
        self,
        content: bytes,
        version_id: str,
        last_modified: datetime,
    ) -> None:
        self.content = content
        self.version_id = version_id
        self.last_modified = last_modified


@final
class _FakeS3Client:
    def __init__(self) -> None:
        self.meta = _FakeMeta()
        self.versions: dict[tuple[str, str], _Stored] = {}
        self.current: dict[str, str] = {}
        self.copy_calls: list[dict[str, object]] = []
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.repeat_token = False
        self.override_get_content: bytes | None = None
        self.override_get_body: object | None = None
        self.checksum_type = "FULL_OBJECT"
        self.copy_error_after_store = False
        self.put_error_after_store = False

    def add(self, key: str, content: bytes, *, version_id: str) -> _Stored:
        stored = _Stored(
            content,
            version_id,
            _LAST_MODIFIED + timedelta(seconds=len(self.versions)),
        )
        self.versions[(key, version_id)] = stored
        self.current[key] = version_id
        return stored

    def copy_object(self, **kwargs: object) -> Mapping[str, object]:
        self.copy_calls.append(dict(kwargs))
        self._assert_owner(kwargs)
        assert kwargs["ExpectedSourceBucketOwner"] == _OWNER
        assert kwargs["IfNoneMatch"] == "*"
        destination = cast(str, kwargs["Key"])
        if destination in self.current:
            raise _client_error(412)
        source = cast(dict[str, str], kwargs["CopySource"])
        stored = self.versions[(source["Key"], source["VersionId"])]
        version_id = f"copy-{len(self.versions) + 1}"
        _ = self.add(destination, stored.content, version_id=version_id)
        if self.copy_error_after_store:
            raise ReadTimeoutError(endpoint_url="https://s3.invalid")
        return {"VersionId": version_id}

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        self.put_calls.append(dict(kwargs))
        self._assert_owner(kwargs)
        assert kwargs["IfNoneMatch"] == "*"
        key = cast(str, kwargs["Key"])
        if key in self.current:
            raise _client_error(412)
        body = kwargs["Body"]
        content = body if isinstance(body, bytes) else cast(_Readable, body).read()
        assert len(content) == kwargs["ContentLength"]
        assert _checksum(content) == kwargs["ChecksumSHA256"]
        version_id = f"put-{len(self.versions) + 1}"
        _ = self.add(key, content, version_id=version_id)
        if self.put_error_after_store:
            raise ReadTimeoutError(endpoint_url="https://s3.invalid")
        return {"VersionId": version_id}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.head_calls.append(dict(kwargs))
        self._assert_owner(kwargs)
        return self._identity(self._resolve(kwargs))

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        self.get_calls.append(dict(kwargs))
        self._assert_owner(kwargs)
        stored = self._resolve(kwargs)
        content = self.override_get_content or stored.content
        body = self.override_get_body or io.BytesIO(content)
        return {**self._identity(stored), "Body": body}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.list_calls.append(dict(kwargs))
        self._assert_owner(kwargs)
        prefix = cast(str, kwargs["Prefix"])
        maximum = cast(int, kwargs["MaxKeys"])
        raw_token = kwargs.get("ContinuationToken")
        offset = int(cast(str, raw_token)) if raw_token is not None else 0
        keys = sorted(key for key in self.current if key.startswith(prefix))
        selected = keys[offset : offset + maximum]
        next_offset = offset + len(selected)
        truncated = next_offset < len(keys)
        response: dict[str, object] = {
            "Contents": [
                {
                    "Key": key,
                    "Size": len(self._current(key).content),
                }
                for key in selected
            ],
            "IsTruncated": truncated,
        }
        if truncated:
            response["NextContinuationToken"] = (
                cast(str, raw_token)
                if self.repeat_token and raw_token is not None
                else str(next_offset)
            )
        return response

    def _resolve(self, kwargs: Mapping[str, object]) -> _Stored:
        key = cast(str, kwargs["Key"])
        version_id = cast(str | None, kwargs.get("VersionId"))
        return (
            self._current(key)
            if version_id is None
            else self.versions[(key, version_id)]
        )

    def _current(self, key: str) -> _Stored:
        return self.versions[(key, self.current[key])]

    @staticmethod
    def _assert_owner(kwargs: Mapping[str, object]) -> None:
        assert kwargs["Bucket"] == _BUCKET
        assert kwargs["ExpectedBucketOwner"] == _OWNER

    def _identity(self, stored: _Stored) -> dict[str, object]:
        return {
            "ContentLength": len(stored.content),
            "ChecksumSHA256": _checksum(stored.content),
            "ChecksumType": self.checksum_type,
            "VersionId": stored.version_id,
            "LastModified": stored.last_modified,
        }


@final
class _Signer:
    def sign(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


@final
class _Verifier:
    def verify(self, content: bytes, signature: str) -> bool:
        return signature == _Signer().sign(content)


def test_operator_store_copies_version_pinned_source_and_appends_inventory() -> None:
    client = _FakeS3Client()
    source_content = b"verified recovery bytes"
    source_sha256 = hashlib.sha256(source_content).hexdigest()
    source_key = f"control-recovery/daily/2025-09-20/{7:020d}/{source_sha256}.sqlite3"
    _ = client.add(source_key, source_content, version_id="source-version")
    store = S3OperatorRecoveryStorage(
        client,
        bucket=_BUCKET,
        expected_bucket_owner=_OWNER,
        page_size=1,
    )
    source = VerifiedRecoverySource(
        source_key,
        "source-version",
        source_sha256,
        len(source_content),
        7,
    )

    chain = append_trusted_recovery(
        store,
        _Signer(),
        _Verifier(),
        source,
        maximum_objects=4,
    )

    assert chain.head is not None
    assert chain.head.inventory.trusted_object_revision == 7
    assert client.copy_calls[0]["CopySource"] == {
        "Bucket": _BUCKET,
        "Key": source_key,
        "VersionId": "source-version",
    }
    assert client.copy_calls[0]["ExpectedSourceBucketOwner"] == _OWNER
    assert client.put_calls[0]["IfNoneMatch"] == "*"
    assert all(call["ExpectedBucketOwner"] == _OWNER for call in client.head_calls)
    assert all(call["ExpectedBucketOwner"] == _OWNER for call in client.get_calls)


def test_conditional_copy_and_put_accept_only_exact_existing_bytes() -> None:
    client = _FakeS3Client()
    content = b"source"
    sha256 = hashlib.sha256(content).hexdigest()
    source = VerifiedRecoverySource(
        f"control-recovery/run/{1:020d}/{sha256}.sqlite3",
        "source-version",
        sha256,
        len(content),
        1,
    )
    _ = client.add(source.key, content, version_id=source.version_id)
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )
    request = TrustedCopyRequest(source, trusted_recovery_key(1, sha256))
    assert store.copy_trusted_if_absent(request) is ConditionalWriteResult.CREATED
    assert (
        store.copy_trusted_if_absent(request)
        is ConditionalWriteResult.PRECONDITION_FAILED
    )

    key = recovery_inventory_key(1)
    inventory = b'{"canonical":true}'
    assert (
        store.put_inventory_if_absent(key, inventory) is ConditionalWriteResult.CREATED
    )
    assert (
        store.put_inventory_if_absent(key, inventory)
        is ConditionalWriteResult.PRECONDITION_FAILED
    )
    with pytest.raises(RecoveryInventoryStorageError, match="conflicts"):
        _ = store.put_inventory_if_absent(key, b'{"canonical":false}')

    destination_version = client.current[request.destination_key]
    client.versions[(request.destination_key, destination_version)].content = b"wrong"
    with pytest.raises(RecoveryInventoryStorageError, match="bytes differ"):
        _ = store.copy_trusted_if_absent(request)


def test_inventory_listing_is_bounded_complete_and_paginated() -> None:
    client = _FakeS3Client()
    _ = client.add(recovery_inventory_key(1), b"one", version_id="one")
    _ = client.add(recovery_inventory_key(2), b"two", version_id="two")
    store = S3OperatorRecoveryStorage(
        client,
        bucket=_BUCKET,
        expected_bucket_owner=_OWNER,
        page_size=1,
    )

    complete = store.list_inventories(maximum_objects=2)
    assert complete.complete
    assert len(complete.objects) == 2
    assert len(client.list_calls) == 2

    bounded = store.list_inventories(maximum_objects=1)
    assert not bounded.complete
    assert len(bounded.objects) == 1

    client.repeat_token = True
    _ = client.add(recovery_inventory_key(3), b"three", version_id="three")
    with pytest.raises(RecoveryInventoryStorageError, match="token repeated"):
        _ = store.list_inventories(maximum_objects=3)


def test_ambiguous_conditional_writes_are_resolved_only_by_exact_objects() -> None:
    client = _FakeS3Client()
    source_content = b"source"
    sha256 = hashlib.sha256(source_content).hexdigest()
    source = VerifiedRecoverySource(
        f"control-recovery/run/{1:020d}/{sha256}.sqlite3",
        "source-version",
        sha256,
        len(source_content),
        1,
    )
    _ = client.add(source.key, source_content, version_id=source.version_id)
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )

    client.copy_error_after_store = True
    copied = store.copy_trusted_if_absent(
        TrustedCopyRequest(source, trusted_recovery_key(1, sha256))
    )
    assert copied is ConditionalWriteResult.PRECONDITION_FAILED

    client.put_error_after_store = True
    written = store.put_inventory_if_absent(
        recovery_inventory_key(1), b'{"exact":true}'
    )
    assert written is ConditionalWriteResult.PRECONDITION_FAILED


def test_trusted_download_is_version_pinned_streamed_and_checksum_verified(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    content = b"trusted version"
    sha256 = hashlib.sha256(content).hexdigest()
    key = trusted_recovery_key(1, sha256)
    selected = client.add(key, content, version_id="selected")
    _ = client.add(key, b"new current", version_id="replacement")
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )
    identity = TrustedObjectIdentity(
        key,
        selected.version_id,
        sha256,
        len(content),
        int(selected.last_modified.timestamp() * 1000),
    )
    path = tmp_path / "download.sqlite3"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        store.download_trusted(identity, descriptor)
    finally:
        os.close(descriptor)
    assert path.read_bytes() == content
    assert client.get_calls[-1]["VersionId"] == "selected"

    client.override_get_content = b"tampered bytes"
    second_path = tmp_path / "tampered.sqlite3"
    descriptor = os.open(second_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with pytest.raises(RecoveryInventoryStorageError, match="bytes differ"):
            store.download_trusted(identity, descriptor)
    finally:
        os.close(descriptor)


def test_operator_store_rejects_non_full_checksums_and_oversized_inventory() -> None:
    client = _FakeS3Client()
    source_content = b"source"
    sha256 = hashlib.sha256(source_content).hexdigest()
    source = VerifiedRecoverySource(
        f"control-recovery/run/{1:020d}/{sha256}.sqlite3",
        "source-version",
        sha256,
        len(source_content),
        1,
    )
    _ = client.add(source.key, source_content, version_id=source.version_id)
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )
    client.checksum_type = "COMPOSITE"
    with pytest.raises(RecoveryInventoryStorageError, match="identity is incomplete"):
        _ = store.copy_trusted_if_absent(
            TrustedCopyRequest(source, trusted_recovery_key(1, sha256))
        )

    client.checksum_type = "FULL_OBJECT"
    key = recovery_inventory_key(1)
    _ = client.add(key, b"x" * (MAX_INVENTORY_BYTES + 1), version_id="oversized")
    with pytest.raises(RecoveryInventoryStorageError, match="exceeds its bound"):
        _ = store.read_inventory(key, maximum_bytes=MAX_INVENTORY_BYTES)


def test_body_cleanup_cannot_mask_inventory_identity_verification_failure() -> None:
    client = _FakeS3Client()
    key = recovery_inventory_key(1)
    _ = client.add(key, b"inventory", version_id="inventory-version")
    body = _FaultingBody(
        b"inventory",
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    client.checksum_type = "COMPOSITE"
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )

    with pytest.raises(
        RecoveryInventoryStorageError,
        match="identity is incomplete",
    ) as captured:
        _ = store.read_inventory(key, maximum_bytes=MAX_INVENTORY_BYTES)

    assert body.closed
    assert any("cleanup" in note for note in captured.value.__notes__)


def test_body_cleanup_cannot_mask_inventory_bytes_verification_failure() -> None:
    client = _FakeS3Client()
    key = recovery_inventory_key(1)
    _ = client.add(key, b"inventory", version_id="inventory-version")
    body = _FaultingBody(
        b"tampered!",
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )

    with pytest.raises(
        RecoveryInventoryStorageError,
        match="inventory bytes differ",
    ) as captured:
        _ = store.read_inventory(key, maximum_bytes=MAX_INVENTORY_BYTES)

    assert body.closed
    assert any("cleanup" in note for note in captured.value.__notes__)


def test_body_cleanup_cannot_mask_primary_base_exception(tmp_path: Path) -> None:
    client = _FakeS3Client()
    content = b"trusted version"
    sha256 = hashlib.sha256(content).hexdigest()
    key = trusted_recovery_key(1, sha256)
    stored = client.add(key, content, version_id="selected")
    primary_error = _InjectedPrimaryFailure("injected primary failure")
    body = _FaultingBody(
        content,
        read_error=primary_error,
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    store = S3OperatorRecoveryStorage(
        client, bucket=_BUCKET, expected_bucket_owner=_OWNER
    )
    identity = TrustedObjectIdentity(
        key,
        stored.version_id,
        sha256,
        len(content),
        int(stored.last_modified.timestamp() * 1000),
    )
    descriptor = os.open(
        tmp_path / "faulted.sqlite3",
        os.O_RDWR | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with pytest.raises(_InjectedPrimaryFailure) as captured:
            store.download_trusted(identity, descriptor)
    finally:
        os.close(descriptor)

    assert captured.value is primary_error
    assert body.closed
    assert any("cleanup" in note for note in primary_error.__notes__)


def _checksum(content: bytes) -> str:
    return base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")


def _client_error(status: int) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": str(status), "Message": "injected"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "S3Operation",
    )
