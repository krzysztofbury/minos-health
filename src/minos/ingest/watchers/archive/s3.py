# pyright: reportMissingTypeStubs=false

import base64
import binascii
import io
import math
import os
import re
import time
from collections.abc import Callable, Mapping
from typing import BinaryIO, Never, Protocol, Self, TypeIs, cast, final, override

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
    ConnectionClosedError,
    ReadTimeoutError,
)

from minos.ingest.object_storage import R2_REGION, validate_r2_endpoint
from minos.ingest.watchers.archive.files import open_verified_staged_blob
from minos.ingest.watchers.archive.models import (
    BlobArchiveConflictError,
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    StagedBlob,
    blob_key,
)
from minos.ingest.watchers.control import BlobReceipt

_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


class _S3Client(Protocol):
    @property
    def meta(self) -> _ClientMeta: ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _Boto3(Protocol):
    def client(
        self,
        service_name: str,
        *,
        region_name: str,
        endpoint_url: str,
        config: object,
    ) -> object: ...


class _ClientConfig(Protocol):
    @property
    def retries(self) -> Mapping[str, object] | None: ...


class _ClientMeta(Protocol):
    @property
    def config(self) -> _ClientConfig: ...


class _ClientErrorLike(Protocol):
    response: Mapping[str, object]


@final
class S3BlobArchive:
    def __init__(
        self,
        client: _S3Client,
        *,
        bucket: str,
        maximum_conflict_attempts: int = 3,
        conflict_delay_seconds: float = 0.05,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            _S3_BUCKET.fullmatch(bucket) is None
            or ".." in bucket
            or ".-" in bucket
            or "-." in bucket
        ):
            raise BlobArchiveStorageError("S3 bucket name is unsupported")
        if (
            not _is_strict_integer(maximum_conflict_attempts)
            or maximum_conflict_attempts < 1
            or maximum_conflict_attempts > 10
        ):
            raise BlobArchiveStorageError("S3 conflict attempt bound is unsupported")
        if (
            not _is_real_number(conflict_delay_seconds)
            or not math.isfinite(conflict_delay_seconds)
            or conflict_delay_seconds < 0
            or conflict_delay_seconds > 5
        ):
            raise BlobArchiveStorageError("S3 conflict delay is unsupported")
        _validate_client_retry_configuration(client)
        self._client = client
        self._bucket = bucket
        self._maximum_conflict_attempts = maximum_conflict_attempts
        self._conflict_delay_seconds = conflict_delay_seconds
        self._sleep = sleep

    @classmethod
    def create(
        cls,
        *,
        bucket: str,
        endpoint: str,
        maximum_conflict_attempts: int = 3,
        conflict_delay_seconds: float = 0.05,
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 60,
    ) -> Self:
        try:
            endpoint = validate_r2_endpoint(endpoint)
        except ValueError as error:
            raise BlobArchiveStorageError(str(error)) from None
        _validate_timeout(connect_timeout_seconds, "S3 connect timeout")
        _validate_timeout(read_timeout_seconds, "S3 read timeout")
        boto3_api = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
        try:
            client = cast(
                _S3Client,
                boto3_api.client(
                    "s3",
                    region_name=R2_REGION,
                    endpoint_url=endpoint,
                    config=Config(
                        connect_timeout=connect_timeout_seconds,
                        read_timeout=read_timeout_seconds,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                    ),
                ),
            )
        except BotoCoreError as error:
            raise BlobArchiveStorageError("S3 client creation failed") from error
        return cls(
            client,
            bucket=bucket,
            maximum_conflict_attempts=maximum_conflict_attempts,
            conflict_delay_seconds=conflict_delay_seconds,
        )

    def put(self, blob: StagedBlob) -> BlobReceipt:
        key = blob_key(blob.sha256)
        checksum_base64 = base64.b64encode(bytes.fromhex(blob.sha256)).decode("ascii")
        with open_verified_staged_blob(blob) as source:
            body = _BoundedBody(source, blob.size_bytes)
            for attempt in range(1, self._maximum_conflict_attempts + 1):
                body.reset()
                try:
                    self._put_once(blob, key, checksum_base64, body)
                    body.verify_complete()
                    break
                except ClientError as error:
                    if _client_error_code(error) in _CHECKSUM_ERROR_CODES:
                        raise BlobArchiveVerificationError(
                            "S3 rejected the staged blob checksum"
                        ) from error
                    status = _client_error_status(error)
                    if status == 412:
                        break
                    if status != 409:
                        raise BlobArchiveStorageError(
                            "S3 conditional blob write failed"
                        ) from error
                    if attempt == self._maximum_conflict_attempts:
                        raise BlobArchiveConflictError(
                            "S3 conditional blob write exhausted conflict retries"
                        ) from error
                    self._sleep(self._conflict_delay_seconds)
                except _AMBIGUOUS_WRITE_ERRORS as error:
                    try:
                        return self._verified_receipt(blob, key)
                    except BlobArchiveVerificationError:
                        raise
                    except BlobArchiveStorageError:
                        raise BlobArchiveStorageError(
                            "S3 blob write outcome could not be resolved"
                        ) from error
                except BotoCoreError as error:
                    if body.verification_error is not None:
                        raise body.verification_error from error
                    raise BlobArchiveStorageError(
                        "S3 blob write failed before its outcome became ambiguous"
                    ) from error
        return self._verified_receipt(blob, key)

    def _put_once(
        self,
        blob: StagedBlob,
        key: str,
        checksum_base64: str,
        body: _BoundedBody,
    ) -> None:
        parameters: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ContentLength": blob.size_bytes,
            "ChecksumAlgorithm": "SHA256",
            "ChecksumSHA256": checksum_base64,
            "IfNoneMatch": "*",
            "Body": body,
        }
        if blob.media_type is not None:
            parameters["ContentType"] = blob.media_type
        _ = self._client.put_object(**parameters)

    def _verified_receipt(self, blob: StagedBlob, key: str) -> BlobReceipt:
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=key,
                ChecksumMode="ENABLED",
            )
        except (BotoCoreError, ClientError) as error:
            raise BlobArchiveStorageError(
                "S3 blob verification request failed"
            ) from error
        verified_size_bytes = response.get("ContentLength")
        checksum_base64 = response.get("ChecksumSHA256")
        checksum_type = response.get("ChecksumType")
        if (
            not isinstance(verified_size_bytes, int)
            or isinstance(verified_size_bytes, bool)
            or not isinstance(checksum_base64, str)
            # R2 omits ChecksumType for a single-part object; never composite.
            or checksum_type not in (None, "FULL_OBJECT")
        ):
            raise BlobArchiveVerificationError(
                "S3 blob verification response is incomplete"
            )
        verified_sha256 = _decode_sha256(checksum_base64)
        if verified_size_bytes != blob.size_bytes or verified_sha256 != blob.sha256:
            raise BlobArchiveVerificationError(
                "S3 archive blob does not match the staged identity"
            )
        return BlobReceipt(
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            media_type=blob.media_type,
            archive_key=key,
            verified_sha256=verified_sha256,
            verified_size_bytes=verified_size_bytes,
        )


def _client_error_status(error: ClientError) -> int | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    typed_metadata = cast(Mapping[str, object], metadata)
    status = typed_metadata.get("HTTPStatusCode")
    if isinstance(status, int) and not isinstance(status, bool):
        return status
    return None


def _client_error_code(error: ClientError) -> str | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    error_details = response.get("Error")
    if not isinstance(error_details, Mapping):
        return None
    code = cast(Mapping[str, object], error_details).get("Code")
    return code if isinstance(code, str) else None


def _decode_sha256(checksum_base64: str) -> str:
    try:
        decoded = base64.b64decode(checksum_base64, validate=True)
    except (binascii.Error, ValueError) as error:
        raise BlobArchiveVerificationError(
            "S3 returned a malformed SHA-256 checksum"
        ) from error
    if len(decoded) != 32:
        raise BlobArchiveVerificationError("S3 returned a non-SHA-256 checksum")
    if base64.b64encode(decoded).decode("ascii") != checksum_base64:
        raise BlobArchiveVerificationError("S3 returned a non-canonical checksum")
    return decoded.hex()


def _is_real_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_timeout(value: object, label: str) -> None:
    if (
        not _is_real_number(value)
        or not math.isfinite(value)
        or value <= 0
        or value > 300
    ):
        raise BlobArchiveStorageError(f"{label} is outside the supported range")


def _validate_client_retry_configuration(client: _S3Client) -> None:
    retries = client.meta.config.retries
    if retries is None or retries.get("total_max_attempts") != 1:
        raise BlobArchiveStorageError("S3 client retries must be disabled")


_AMBIGUOUS_WRITE_ERRORS = (
    ConnectionClosedError,
    ReadTimeoutError,
)
_CHECKSUM_ERROR_CODES = frozenset({"BadDigest", "InvalidDigest"})


@final
class _BoundedBody(io.RawIOBase):
    def __init__(self, source: BinaryIO, size_bytes: int) -> None:
        super().__init__()
        self._source = source
        self._size_bytes = size_bytes
        self._position_bytes = 0
        self._verification_error: BlobArchiveVerificationError | None = None

    @property
    def verification_error(self) -> BlobArchiveVerificationError | None:
        return self._verification_error

    @override
    def readable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def tell(self) -> int:
        return self._position_bytes

    @override
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target_bytes = offset
        elif whence == os.SEEK_CUR:
            target_bytes = self._position_bytes + offset
        elif whence == os.SEEK_END:
            target_bytes = self._size_bytes + offset
        else:
            raise ValueError("unsupported seek origin")
        if target_bytes < 0 or target_bytes > self._size_bytes:
            raise ValueError("S3 request body seek is outside the staged blob")
        self._position_bytes = self._source.seek(target_bytes)
        if self._position_bytes != target_bytes:
            self._fail("staged blob seek changed during S3 publication")
        return self._position_bytes

    @override
    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""
        remaining_bytes = self._size_bytes - self._position_bytes
        if remaining_bytes == 0:
            return b""
        requested_bytes = remaining_bytes if size < 0 else min(size, remaining_bytes)
        chunk = self._source.read(requested_bytes)
        if not chunk:
            self._fail("staged blob shrank during S3 publication")
        if len(chunk) > requested_bytes:
            self._fail("staged blob read exceeded the S3 publication bound")
        self._position_bytes += len(chunk)
        return chunk

    def reset(self) -> None:
        self._verification_error = None
        end_position_bytes = self._source.seek(0, os.SEEK_END)
        if end_position_bytes != self._size_bytes:
            self._fail("staged blob size changed before S3 publication")
        _ = self.seek(0)

    def verify_complete(self) -> None:
        if self._position_bytes != self._size_bytes:
            self._fail("S3 client did not consume the complete staged blob")
        end_position_bytes = self._source.seek(0, os.SEEK_END)
        if end_position_bytes != self._size_bytes:
            self._fail("staged blob size changed during S3 publication")

    def _fail(self, message: str) -> Never:
        error = BlobArchiveVerificationError(message)
        self._verification_error = error
        raise error
