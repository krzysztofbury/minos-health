import base64
import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol, cast, final

import pytest
from botocore.exceptions import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
    ClientError,
    ConnectionClosedError,
    EndpointConnectionError,
    HTTPClientError,
    ParamValidationError,
    ReadTimeoutError,
)

from minos.ingest.watchers.archive import (
    BlobArchive,
    BlobArchiveConflictError,
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    BlobStagingArea,
    FilesystemBlobArchive,
    S3BlobArchive,
)
from minos.ingest.watchers.control import BlobReceipt

_BUCKET = "minos-test-archive"


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...

    def tell(self) -> int: ...


@final
class _FakeConfig:
    def __init__(self) -> None:
        self.retries: Mapping[str, object] = {
            "total_max_attempts": 1,
            "mode": "standard",
        }


@final
class _FakeMeta:
    def __init__(self) -> None:
        self.config = _FakeConfig()


@final
class _FakeS3Client:
    def __init__(self) -> None:
        self.meta = _FakeMeta()
        self.put_outcomes: list[BaseException | None] = []
        self.object_bytes: bytes | None = None
        self.object_checksum: str | None = None
        self.put_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.head_outcomes: list[BaseException] = []
        self.store_before_error = False
        self.body_initial_positions: list[int] = []
        # R2 omits ChecksumType for single-part objects.
        self.checksum_type: object = None
        self.before_body_read: Callable[[], None] | None = None
        self.omitted_head_fields: set[str] = set()
        self.wrap_body_errors = False

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        body = cast(_Readable, kwargs["Body"])
        self.body_initial_positions.append(body.tell())
        if self.before_body_read is not None:
            self.before_body_read()
        chunks: list[bytes] = []
        try:
            while chunk := body.read(3):
                chunks.append(chunk)
        except BlobArchiveVerificationError as error:
            if self.wrap_body_errors:
                raise HTTPClientError(error="wrapped staged-body failure") from error
            raise
        content = b"".join(chunks)
        assert len(content) == kwargs["ContentLength"]
        computed_checksum = base64.b64encode(hashlib.sha256(content).digest()).decode(
            "ascii"
        )
        assert computed_checksum == kwargs["ChecksumSHA256"]
        self.put_calls.append(
            {key: value for key, value in kwargs.items() if key != "Body"}
        )
        outcome = (
            self.put_outcomes.pop(0)
            if self.put_outcomes
            else _client_error(412)
            if self.object_bytes is not None
            else None
        )
        if outcome is None or self.store_before_error:
            self.object_bytes = content
            self.object_checksum = computed_checksum
        if outcome is not None:
            raise outcome
        return {}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        self.head_calls.append(dict(kwargs))
        if self.head_outcomes:
            raise self.head_outcomes.pop(0)
        if self.object_bytes is None or self.object_checksum is None:
            raise _client_error(404)
        response: dict[str, object] = {
            "ContentLength": len(self.object_bytes),
            "ChecksumSHA256": self.object_checksum,
            "ChecksumType": self.checksum_type,
        }
        for field in self.omitted_head_fields:
            _ = response.pop(field, None)
        return response


def test_s3_archive_uses_single_part_conditional_write_and_head(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    archive = S3BlobArchive(client, bucket=_BUCKET)
    staging = BlobStagingArea(tmp_path / "staging")

    with staging.stage() as writer:
        _ = writer.write(b"source")
        blob = writer.seal(media_type="application/json")
        receipt = archive.put(blob)

    checksum = base64.b64encode(hashlib.sha256(b"source").digest()).decode("ascii")
    assert client.put_calls == [
        {
            "Bucket": _BUCKET,
            "Key": f"blobs/sha256/{receipt.sha256}",
            "ContentLength": 6,
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": checksum,
            "IfNoneMatch": "*",
            "ContentType": "application/json",
        }
    ]
    assert client.head_calls == [
        {
            "Bucket": _BUCKET,
            "Key": receipt.archive_key,
            "ChecksumMode": "ENABLED",
        }
    ]
    assert receipt.verified_sha256 == receipt.sha256
    assert receipt.verified_size_bytes == receipt.size_bytes == 6


def test_local_and_s3_adapters_share_receipt_and_reuse_contract(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    archives: tuple[BlobArchive, ...] = (
        FilesystemBlobArchive(tmp_path / "archive"),
        S3BlobArchive(client, bucket=_BUCKET),
    )
    receipts: list[BlobReceipt] = []

    for index, archive in enumerate(archives):
        staging = BlobStagingArea(tmp_path / f"staging-{index}")
        with staging.stage() as writer:
            _ = writer.write(b"contract")
            first = archive.put(writer.seal(media_type="application/octet-stream"))
        with staging.stage() as writer:
            _ = writer.write(b"contract")
            repeated = archive.put(writer.seal(media_type="application/octet-stream"))
        assert repeated == first
        receipts.append(first)

    assert receipts[0] == receipts[1]


# R2 checks bucket lock rules before If-None-Match: an existing locked object
# answers 409 ObjectLockedByBucketPolicy instead of 412.
@pytest.mark.parametrize(
    ("status", "code"), [(412, None), (409, "ObjectLockedByBucketPolicy")]
)
def test_s3_archive_verifies_reuse_after_precondition_failure(
    tmp_path: Path, status: int, code: str | None
) -> None:
    content = b"existing"
    checksum = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    client = _FakeS3Client()
    client.object_bytes = content
    client.object_checksum = checksum
    client.put_outcomes = [_client_error(status, code=code)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        receipt = archive.put(writer.seal(media_type=None))

    assert receipt.sha256 == hashlib.sha256(content).hexdigest()
    assert len(client.put_calls) == 1
    assert len(client.head_calls) == 1


def test_s3_archive_retries_only_bounded_conflicts(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [_client_error(409), _client_error(409), None]
    delays: list[float] = []
    archive = S3BlobArchive(
        client,
        bucket=_BUCKET,
        maximum_conflict_attempts=3,
        conflict_delay_seconds=0.25,
        sleep=delays.append,
    )

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"eventual")
        _ = archive.put(writer.seal(media_type=None))

    assert len(client.put_calls) == 3
    assert client.body_initial_positions == [0, 0, 0]
    assert delays == [0.25, 0.25]


def test_s3_archive_fails_after_conflict_bound(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [_client_error(409), _client_error(409)]
    archive = S3BlobArchive(
        client,
        bucket=_BUCKET,
        maximum_conflict_attempts=2,
        conflict_delay_seconds=0,
    )

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"conflict")
        with pytest.raises(BlobArchiveConflictError, match="exhausted"):
            _ = archive.put(writer.seal(media_type=None))

    assert len(client.put_calls) == 2
    assert client.head_calls == []


def test_s3_archive_resolves_post_upload_timeout_with_head(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [ReadTimeoutError(endpoint_url="https://s3.test")]
    client.store_before_error = True
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"uploaded")
        receipt = archive.put(writer.seal(media_type=None))

    assert receipt.verified_sha256 == hashlib.sha256(b"uploaded").hexdigest()
    assert len(client.head_calls) == 1


def test_s3_archive_rejects_unresolved_timeout(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [ReadTimeoutError(endpoint_url="https://s3.test")]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"missing")
        with pytest.raises(BlobArchiveStorageError, match="could not be resolved"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_head_checksum_mismatch(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.object_bytes = b"same-size"
    client.object_checksum = base64.b64encode(b"x" * 32).decode("ascii")
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"same-size")
        with pytest.raises(BlobArchiveVerificationError, match="does not match"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_requires_full_object_checksum(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.object_bytes = b"composite"
    client.object_checksum = base64.b64encode(
        hashlib.sha256(b"composite").digest()
    ).decode("ascii")
    client.checksum_type = "COMPOSITE"
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"composite")
        with pytest.raises(BlobArchiveVerificationError, match="incomplete"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_does_not_resolve_local_botocore_failure_with_head(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [ParamValidationError(report="injected")]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"invalid")
        with pytest.raises(BlobArchiveStorageError, match="before.*ambiguous"):
            _ = archive.put(writer.seal(media_type=None))

    assert client.head_calls == []


def test_s3_archive_does_not_retry_permission_failure(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [_client_error(403)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"denied")
        with pytest.raises(BlobArchiveStorageError, match="conditional blob write"):
            _ = archive.put(writer.seal(media_type=None))

    assert len(client.put_calls) == 1
    assert client.head_calls == []


def test_s3_archive_does_not_resolve_preconnection_failure_with_head(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [EndpointConnectionError(endpoint_url="https://s3.test")]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"unreachable")
        with pytest.raises(BlobArchiveStorageError, match="before.*ambiguous"):
            _ = archive.put(writer.seal(media_type=None))

    assert client.head_calls == []


def test_s3_archive_resolves_closed_connection_with_head(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [ConnectionClosedError(endpoint_url="https://s3.test")]
    client.store_before_error = True
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"uploaded")
        receipt = archive.put(writer.seal(media_type=None))

    assert receipt.verified_sha256 == hashlib.sha256(b"uploaded").hexdigest()
    assert len(client.head_calls) == 1


def test_s3_archive_rejects_malformed_head_checksum(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.object_bytes = b"malformed"
    client.object_checksum = "not-base64"
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"malformed")
        with pytest.raises(BlobArchiveVerificationError, match="malformed"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_head_size_mismatch(tmp_path: Path) -> None:
    content = b"expected"
    client = _FakeS3Client()
    client.object_bytes = b"wrong-size"
    client.object_checksum = base64.b64encode(hashlib.sha256(content).digest()).decode(
        "ascii"
    )
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        with pytest.raises(BlobArchiveVerificationError, match="does not match"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_head_failure_after_reuse(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.object_bytes = b"existing"
    client.object_checksum = base64.b64encode(
        hashlib.sha256(b"existing").digest()
    ).decode("ascii")
    client.put_outcomes = [_client_error(412)]
    client.head_outcomes = [_client_error(403)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"existing")
        with pytest.raises(BlobArchiveStorageError, match="verification request"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_client_retries() -> None:
    client = _FakeS3Client()
    client.meta.config.retries = {"total_max_attempts": 2, "mode": "standard"}

    with pytest.raises(BlobArchiveStorageError, match="retries must be disabled"):
        _ = S3BlobArchive(client, bucket=_BUCKET)


def test_s3_archive_bounds_body_if_staged_file_grows(tmp_path: Path) -> None:
    client = _FakeS3Client()
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"bounded")
        blob = writer.seal(media_type=None)

        def grow_staged_file() -> None:
            _ = blob.path.write_bytes(b"bounded-extra")

        client.before_body_read = grow_staged_file

        with pytest.raises(BlobArchiveVerificationError, match="size changed during"):
            _ = archive.put(blob)

    assert client.object_bytes == b"bounded"


def test_s3_archive_rejects_staged_file_truncation_during_upload(
    tmp_path: Path,
) -> None:
    client = _FakeS3Client()
    client.wrap_body_errors = True
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"complete")
        blob = writer.seal(media_type=None)

        def truncate_staged_file() -> None:
            _ = blob.path.write_bytes(b"short")

        client.before_body_read = truncate_staged_file

        with pytest.raises(BlobArchiveVerificationError, match="shrank"):
            _ = archive.put(blob)

    assert client.object_bytes is None


def test_s3_archive_classifies_native_checksum_rejection(tmp_path: Path) -> None:
    client = _FakeS3Client()
    client.put_outcomes = [_client_error(400, code="BadDigest")]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(b"checksum")
        with pytest.raises(BlobArchiveVerificationError, match="rejected.*checksum"):
            _ = archive.put(writer.seal(media_type=None))

    assert client.head_calls == []


def test_s3_archive_rejects_missing_head_checksum(tmp_path: Path) -> None:
    # A missing ChecksumType is R2's normal single-part answer; a missing
    # SHA-256 never is.
    field = "ChecksumSHA256"
    content = b"missing-field"
    client = _FakeS3Client()
    client.object_bytes = content
    client.object_checksum = base64.b64encode(hashlib.sha256(content).digest()).decode(
        "ascii"
    )
    client.omitted_head_fields = {field}
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        with pytest.raises(BlobArchiveVerificationError, match="incomplete"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_wrongly_typed_head_checksum(tmp_path: Path) -> None:
    content = b"wrong-type"
    client = _FakeS3Client()
    client.object_bytes = content
    client.object_checksum = base64.b64encode(hashlib.sha256(content).digest()).decode(
        "ascii"
    )
    client.checksum_type = 1
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        with pytest.raises(BlobArchiveVerificationError, match="incomplete"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_non_sha256_head_checksum(tmp_path: Path) -> None:
    content = b"short-checksum"
    client = _FakeS3Client()
    client.object_bytes = content
    client.object_checksum = base64.b64encode(b"x" * 31).decode("ascii")
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        with pytest.raises(BlobArchiveVerificationError, match="non-SHA-256"):
            _ = archive.put(writer.seal(media_type=None))


def test_s3_archive_rejects_noncanonical_head_checksum(tmp_path: Path) -> None:
    content = b"noncanonical"
    canonical = base64.b64encode(hashlib.sha256(content).digest()).decode("ascii")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    final_index = alphabet.index(canonical[-2])
    noncanonical = f"{canonical[:-2]}{alphabet[final_index + 1]}="
    assert base64.b64decode(noncanonical, validate=True) == base64.b64decode(canonical)
    client = _FakeS3Client()
    client.object_bytes = content
    client.object_checksum = noncanonical
    client.put_outcomes = [_client_error(412)]
    archive = S3BlobArchive(client, bucket=_BUCKET)

    with BlobStagingArea(tmp_path / "staging").stage() as writer:
        _ = writer.write(content)
        with pytest.raises(BlobArchiveVerificationError, match="non-canonical"):
            _ = archive.put(writer.seal(media_type=None))


def _client_error(status: int, *, code: str | None = None) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": code or str(status), "Message": "injected"},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )
