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
    def __init__(self, content: bytes, last_modified: datetime) -> None:
        self.content = content
        self.last_modified = last_modified
        self.etag = f'"{hashlib.md5(content, usedforsecurity=False).hexdigest()}"'


@final
class _FakeS3Client:
    """R2 semantics: one object per key, no versions, ETag and SHA-256."""

    def __init__(self) -> None:
        self.meta = _FakeMeta()
        self.objects: dict[str, _Stored] = {}
        self.put_calls: list[dict[str, object]] = []
        self.get_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.list_calls: list[dict[str, object]] = []
        self.repeat_token = False
        self.override_get_content: bytes | None = None
        self.override_get_body: object | None = None
        self.checksum_type: str | None = None
        self.put_error_after_store = False
        self.lock_existing = False

    def add(self, key: str, content: bytes) -> _Stored:
        stored = _Stored(content, _LAST_MODIFIED + timedelta(seconds=len(self.objects)))
        self.objects[key] = stored
        return stored

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        self.put_calls.append(
            {key: value for key, value in kwargs.items() if key != "Body"}
        )
        _assert_r2_request(kwargs)
        assert kwargs["IfNoneMatch"] == "*"
        key = cast(str, kwargs["Key"])
        if key in self.objects:
            # R2 checks bucket lock rules before If-None-Match.
            raise (
                _client_error(409, code="ObjectLockedByBucketPolicy")
                if self.lock_existing
                else _client_error(412)
            )
        body = kwargs["Body"]
        content = body if isinstance(body, bytes) else cast(_Readable, body).read()
        assert len(content) == kwargs["ContentLength"]
        assert _checksum(content) == kwargs["ChecksumSHA256"]
        stored = self.add(key, content)
        if self.put_error_after_store:
            raise ReadTimeoutError(endpoint_url="https://r2.invalid")
        return {"ETag": stored.etag}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.head_calls.append(dict(kwargs))
        _assert_r2_request(kwargs)
        return self._identity(self.objects[cast(str, kwargs["Key"])])

    def get_object(self, **kwargs: object) -> Mapping[str, object]:
        self.get_calls.append(dict(kwargs))
        _assert_r2_request(kwargs)
        stored = self.objects[cast(str, kwargs["Key"])]
        if kwargs.get("IfMatch") not in (None, stored.etag):
            raise _client_error(412)
        content = self.override_get_content or stored.content
        body = self.override_get_body or io.BytesIO(content)
        return {**self._identity(stored), "Body": body}

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]:
        self.list_calls.append(dict(kwargs))
        _assert_r2_request(kwargs)
        prefix = cast(str, kwargs["Prefix"])
        maximum = cast(int, kwargs["MaxKeys"])
        raw_token = kwargs.get("ContinuationToken")
        offset = int(cast(str, raw_token)) if raw_token is not None else 0
        keys = sorted(key for key in self.objects if key.startswith(prefix))
        selected = keys[offset : offset + maximum]
        next_offset = offset + len(selected)
        truncated = next_offset < len(keys)
        response: dict[str, object] = {
            "Contents": [
                {"Key": key, "Size": len(self.objects[key].content)} for key in selected
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

    def _identity(self, stored: _Stored) -> dict[str, object]:
        identity: dict[str, object] = {
            "ContentLength": len(stored.content),
            "ChecksumSHA256": _checksum(stored.content),
            "ETag": stored.etag,
            "LastModified": stored.last_modified,
        }
        if self.checksum_type is not None:
            identity["ChecksumType"] = self.checksum_type
        return identity


def _assert_r2_request(kwargs: Mapping[str, object]) -> None:
    assert kwargs["Bucket"] == _BUCKET
    # R2 answers 501 to bucket-owner headers and has no object versions.
    assert "ExpectedBucketOwner" not in kwargs
    assert "VersionId" not in kwargs


@final
class _Signer:
    def sign(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()


@final
class _Verifier:
    def verify(self, content: bytes, signature: str) -> bool:
        return signature == _Signer().sign(content)


def test_operator_store_copies_etag_pinned_source_and_appends_inventory() -> None:
    client = _FakeS3Client()
    source_content = b"verified recovery bytes"
    source_sha256 = hashlib.sha256(source_content).hexdigest()
    source_key = f"control-recovery/daily/2025-09-20/{7:020d}/{source_sha256}.sqlite3"
    stored = client.add(source_key, source_content)
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET, page_size=1)
    source = VerifiedRecoverySource(
        source_key,
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
    # R2 CopyObject ignores If-None-Match, so the copy is a pinned GET and a
    # create-once PUT that carries the SHA-256.
    source_reads = [call for call in client.get_calls if call["Key"] == source_key]
    assert source_reads[0]["IfMatch"] == stored.etag
    trusted_put = client.put_calls[0]
    assert trusted_put["Key"] == chain.head.inventory.trusted_object_key
    assert trusted_put["IfNoneMatch"] == "*"
    assert trusted_put["ChecksumSHA256"] == _checksum(source_content)
    assert client.objects[chain.head.inventory.trusted_object_key].content == (
        source_content
    )


@pytest.mark.parametrize("lock_existing", [False, True])
def test_conditional_copy_and_put_accept_only_exact_existing_bytes(
    lock_existing: bool,
) -> None:
    client = _FakeS3Client()
    client.lock_existing = lock_existing
    content = b"source"
    sha256 = hashlib.sha256(content).hexdigest()
    source = VerifiedRecoverySource(
        f"control-recovery/run/{1:020d}/{sha256}.sqlite3",
        sha256,
        len(content),
        1,
    )
    _ = client.add(source.key, content)
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)
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

    client.objects[request.destination_key].content = b"wrong"
    with pytest.raises(RecoveryInventoryStorageError, match="bytes differ"):
        _ = store.copy_trusted_if_absent(request)


def test_inventory_listing_is_bounded_complete_and_paginated() -> None:
    client = _FakeS3Client()
    _ = client.add(recovery_inventory_key(1), b"one")
    _ = client.add(recovery_inventory_key(2), b"two")
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET, page_size=1)

    complete = store.list_inventories(maximum_objects=2)
    assert complete.complete
    assert len(complete.objects) == 2
    assert len(client.list_calls) == 2

    bounded = store.list_inventories(maximum_objects=1)
    assert not bounded.complete
    assert len(bounded.objects) == 1

    client.repeat_token = True
    _ = client.add(recovery_inventory_key(3), b"three")
    with pytest.raises(RecoveryInventoryStorageError, match="token repeated"):
        _ = store.list_inventories(maximum_objects=3)


def test_ambiguous_conditional_writes_are_resolved_only_by_exact_objects() -> None:
    client = _FakeS3Client()
    source_content = b"source"
    sha256 = hashlib.sha256(source_content).hexdigest()
    source = VerifiedRecoverySource(
        f"control-recovery/run/{1:020d}/{sha256}.sqlite3",
        sha256,
        len(source_content),
        1,
    )
    _ = client.add(source.key, source_content)
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)

    client.put_error_after_store = True
    copied = store.copy_trusted_if_absent(
        TrustedCopyRequest(source, trusted_recovery_key(1, sha256))
    )
    assert copied is ConditionalWriteResult.PRECONDITION_FAILED

    written = store.put_inventory_if_absent(
        recovery_inventory_key(1), b'{"exact":true}'
    )
    assert written is ConditionalWriteResult.PRECONDITION_FAILED


def test_trusted_download_is_etag_pinned_streamed_and_checksum_verified(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    content = b"trusted version"
    sha256 = hashlib.sha256(content).hexdigest()
    key = trusted_recovery_key(1, sha256)
    selected = client.add(key, content)
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)
    identity = TrustedObjectIdentity(
        key,
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
    assert client.get_calls[-1]["IfMatch"] == selected.etag

    # A replaced object (refused by the bucket lock in production) is rejected
    # by its signed identity before any byte is written.
    _ = client.add(key, b"new current")
    replaced_path = tmp_path / "replaced.sqlite3"
    descriptor = os.open(replaced_path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with pytest.raises(RecoveryInventoryStorageError, match="identity differs"):
            store.download_trusted(identity, descriptor)
    finally:
        os.close(descriptor)
    assert replaced_path.read_bytes() == b""
    client.objects[key] = selected

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
        sha256,
        len(source_content),
        1,
    )
    _ = client.add(source.key, source_content)
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)
    client.checksum_type = "COMPOSITE"
    with pytest.raises(RecoveryInventoryStorageError, match="identity is incomplete"):
        _ = store.copy_trusted_if_absent(
            TrustedCopyRequest(source, trusted_recovery_key(1, sha256))
        )

    client.checksum_type = None
    key = recovery_inventory_key(1)
    _ = client.add(key, b"x" * (MAX_INVENTORY_BYTES + 1))
    with pytest.raises(RecoveryInventoryStorageError, match="exceeds its bound"):
        _ = store.read_inventory(key, maximum_bytes=MAX_INVENTORY_BYTES)


def test_body_cleanup_cannot_mask_inventory_identity_verification_failure() -> None:
    client = _FakeS3Client()
    key = recovery_inventory_key(1)
    _ = client.add(key, b"inventory")
    body = _FaultingBody(
        b"inventory",
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    client.checksum_type = "COMPOSITE"
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)

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
    _ = client.add(key, b"inventory")
    body = _FaultingBody(
        b"tampered!",
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)

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
    stored = client.add(key, content)
    primary_error = _InjectedPrimaryFailure("injected primary failure")
    body = _FaultingBody(
        content,
        read_error=primary_error,
        close_error=RuntimeError("injected cleanup failure"),
    )
    client.override_get_body = body
    store = S3OperatorRecoveryStorage(client, bucket=_BUCKET)
    identity = TrustedObjectIdentity(
        key,
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


def _client_error(status: int, *, code: str | None = None) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code or str(status), "Message": "injected"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "S3Operation",
    )
