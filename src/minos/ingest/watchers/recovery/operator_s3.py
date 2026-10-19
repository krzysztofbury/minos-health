# pyright: reportMissingTypeStubs=false

import base64
import binascii
import hashlib
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, Self, TypeIs, cast, final

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)

from minos.ingest.object_storage import R2_REGION, validate_r2_endpoint
from minos.ingest.watchers.recovery.inventory import (
    MAX_INVENTORY_BYTES,
    MAX_INVENTORY_OBJECTS,
    MAX_RECOVERY_OBJECT_BYTES,
    BoundedInventoryListing,
    ConditionalWriteResult,
    ListedInventoryObject,
    RecoveryInventoryStorageError,
    TrustedCopyRequest,
    TrustedObjectIdentity,
)
from minos.ingest.watchers.recovery.keys import parse_recovery_key
from minos.ingest.watchers.recovery.models import RecoveryVerificationError
from minos.ingest.watchers.recovery.trusted_keys import (
    TrustedRecoveryKeyError,
    parse_recovery_inventory_key,
    parse_trusted_recovery_key,
)

_S3_BUCKET = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")
_ETAG = re.compile(r'"[0-9a-f]{32}(?:-[0-9]{1,5})?"')
_INVENTORY_PREFIX = "recovery-inventories/"
_MAX_LIST_PAGE_SIZE = 1000
_STREAM_CHUNK_BYTES = 1024 * 1024
_MIN_STREAM_CHUNK_BYTES = 4096
_MAX_TIMESTAMP_MS = 253_402_300_799_999


class _StreamingBody(Protocol):
    def read(self, amount: int | None = None) -> object: ...

    def close(self) -> None: ...


class _S3Client(Protocol):
    @property
    def meta(self) -> _ClientMeta: ...

    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def list_objects_v2(self, **kwargs: object) -> Mapping[str, object]: ...

    def put_object(self, **kwargs: object) -> Mapping[str, object]: ...


class _ClientConfig(Protocol):
    @property
    def retries(self) -> Mapping[str, object] | None: ...


class _ClientMeta(Protocol):
    @property
    def config(self) -> _ClientConfig: ...


class _Boto3(Protocol):
    def client(
        self,
        service_name: str,
        *,
        region_name: str,
        endpoint_url: str,
        config: object,
    ) -> object: ...


class _ClientErrorLike(Protocol):
    response: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _ObjectIdentity:
    # The ETag pins a read to the object HEAD verified (R2 has no versions).
    etag: str
    sha256: str
    size_bytes: int
    last_modified_ms: int


@final
class S3OperatorRecoveryStorage:
    def __init__(
        self,
        client: _S3Client,
        *,
        bucket: str,
        page_size: int = _MAX_LIST_PAGE_SIZE,
    ) -> None:
        _validate_configuration(client, bucket, page_size)
        self._client = client
        self._bucket = bucket
        self._page_size = page_size

    @classmethod
    def create(
        cls,
        *,
        bucket: str,
        endpoint: str,
        page_size: int = _MAX_LIST_PAGE_SIZE,
        connect_timeout_seconds: float = 10,
        read_timeout_seconds: float = 60,
    ) -> Self:
        _validate_timeout(connect_timeout_seconds)
        _validate_timeout(read_timeout_seconds)
        try:
            endpoint = validate_r2_endpoint(endpoint)
        except ValueError:
            raise RecoveryInventoryStorageError(
                "R2 operator endpoint is invalid"
            ) from None
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
        except BotoCoreError:
            raise RecoveryInventoryStorageError(
                "S3 operator client creation failed"
            ) from None
        return cls(client, bucket=bucket, page_size=page_size)

    def list_inventories(self, *, maximum_objects: int) -> BoundedInventoryListing:
        _validate_inventory_bound(maximum_objects)
        results: list[ListedInventoryObject] = []
        continuation_token: str | None = None
        seen_tokens: set[str] = set()
        maximum_pages = math.ceil((maximum_objects + 1) / self._page_size)
        for _page in range(maximum_pages):
            response = self._list_page(continuation_token)
            contents = response.get("Contents", [])
            if not isinstance(contents, list):
                raise RecoveryInventoryStorageError(
                    "S3 inventory listing contents are malformed"
                )
            raw_contents = cast(list[object], contents)
            if len(raw_contents) > self._page_size:
                raise RecoveryInventoryStorageError(
                    "S3 inventory listing page exceeds its bound"
                )
            for raw_item in raw_contents:
                results.append(_parse_listed_inventory(raw_item))
                if len(results) > maximum_objects:
                    return BoundedInventoryListing(
                        tuple(results[:maximum_objects]), False
                    )
            truncated = response.get("IsTruncated")
            if truncated is False:
                return BoundedInventoryListing(tuple(results), True)
            if truncated is not True:
                raise RecoveryInventoryStorageError(
                    "S3 inventory listing state is malformed"
                )
            token = response.get("NextContinuationToken")
            if not isinstance(token, str) or len(token) < 1 or len(token) > 2048:
                raise RecoveryInventoryStorageError(
                    "S3 inventory listing token is malformed"
                )
            if token in seen_tokens:
                raise RecoveryInventoryStorageError(
                    "S3 inventory listing token repeated"
                )
            seen_tokens.add(token)
            continuation_token = token
        return BoundedInventoryListing(tuple(results), False)

    def read_inventory(self, key: str, *, maximum_bytes: int) -> bytes:
        _validate_inventory_key(key)
        if (
            not _is_strict_integer(maximum_bytes)
            or maximum_bytes < 1
            or maximum_bytes > MAX_INVENTORY_BYTES
        ):
            raise RecoveryInventoryStorageError("inventory read bound is invalid")
        content, _identity = self._get_bytes(key, maximum_bytes=maximum_bytes)
        return content

    def copy_trusted_if_absent(
        self, request: TrustedCopyRequest
    ) -> ConditionalWriteResult:
        _validate_copy_request(request)
        source = self._head(request.source.key)
        _require_source_identity(source, request)
        # R2 CopyObject ignores If-None-Match and drops the SHA-256 checksum, so
        # the copy is a pinned, verified download and a create-once upload.
        with tempfile.TemporaryFile() as staged:
            descriptor = staged.fileno()
            self._download_pinned(request.source.key, source, descriptor)
            _ = os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                _ = self._client.put_object(
                    Bucket=self._bucket,
                    Key=request.destination_key,
                    ContentLength=source.size_bytes,
                    ChecksumAlgorithm="SHA256",
                    ChecksumSHA256=_encode_sha256(source.sha256),
                    IfNoneMatch="*",
                    Body=staged,
                )
            except ClientError as error:
                if _client_error_status(error) != 412:
                    raise RecoveryInventoryStorageError(
                        "S3 trusted recovery copy failed"
                    ) from None
                _ = self._require_trusted_copy(request)
                return ConditionalWriteResult.PRECONDITION_FAILED
            except BotoCoreError:
                try:
                    _ = self._require_trusted_copy(request)
                except RecoveryInventoryStorageError:
                    raise RecoveryInventoryStorageError(
                        "S3 trusted recovery copy outcome is unresolved"
                    ) from None
                return ConditionalWriteResult.PRECONDITION_FAILED
        _ = self._require_trusted_copy(request)
        return ConditionalWriteResult.CREATED

    def head_trusted(self, key: str) -> TrustedObjectIdentity:
        try:
            parsed = parse_trusted_recovery_key(key)
        except TrustedRecoveryKeyError:
            raise RecoveryInventoryStorageError(
                "trusted recovery key is invalid"
            ) from None
        identity = self._head(key)
        if identity.sha256 != parsed.sha256:
            raise RecoveryInventoryStorageError("S3 trusted recovery key differs")
        return TrustedObjectIdentity(
            key,
            identity.sha256,
            identity.size_bytes,
            identity.last_modified_ms,
        )

    def put_inventory_if_absent(
        self, key: str, content: bytes
    ) -> ConditionalWriteResult:
        _validate_inventory_key(key)
        if len(content) < 1 or len(content) > MAX_INVENTORY_BYTES:
            raise RecoveryInventoryStorageError("inventory write size is invalid")
        sha256 = hashlib.sha256(content).hexdigest()
        try:
            _ = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                ContentLength=len(content),
                ChecksumAlgorithm="SHA256",
                ChecksumSHA256=_encode_sha256(sha256),
                IfNoneMatch="*",
                Body=content,
            )
        except ClientError as error:
            if _client_error_status(error) != 412:
                raise RecoveryInventoryStorageError(
                    "S3 recovery inventory write failed"
                ) from None
            existing = self._read_inventory_bytes(key)
            if existing != content:
                raise RecoveryInventoryStorageError(
                    "S3 recovery inventory write conflicts"
                ) from None
            return ConditionalWriteResult.PRECONDITION_FAILED
        except BotoCoreError:
            try:
                existing = self._read_inventory_bytes(key)
            except RecoveryInventoryStorageError:
                raise RecoveryInventoryStorageError(
                    "S3 recovery inventory write outcome is unresolved"
                ) from None
            if existing != content:
                raise RecoveryInventoryStorageError(
                    "S3 recovery inventory write conflicts"
                ) from None
            return ConditionalWriteResult.PRECONDITION_FAILED
        stored, identity = self._get_bytes(key, maximum_bytes=MAX_INVENTORY_BYTES)
        if stored != content or identity.sha256 != sha256:
            raise RecoveryInventoryStorageError(
                "S3 recovery inventory verification differs"
            )
        return ConditionalWriteResult.CREATED

    def download_trusted(
        self,
        identity: TrustedObjectIdentity,
        destination_descriptor: int,
    ) -> None:
        _validate_trusted_identity(identity)
        _validate_empty_destination(destination_descriptor)
        head = self._head(identity.key)
        _require_public_identity(head, identity)
        self._download_pinned(identity.key, head, destination_descriptor)

    def _download_pinned(
        self, key: str, head: _ObjectIdentity, destination_descriptor: int
    ) -> None:
        response = self._get(key, etag=head.etag)
        primary_error: BaseException | None = None
        try:
            try:
                downloaded = _parse_object_identity(response)
                if downloaded != head:
                    raise RecoveryInventoryStorageError(
                        "S3 trusted recovery download identity differs"
                    )
                body = _response_body(response)
                sha256, size_bytes = _stream_to_descriptor(
                    body,
                    destination_descriptor,
                    maximum_size_bytes=head.size_bytes,
                )
                if sha256 != head.sha256 or size_bytes != head.size_bytes:
                    raise RecoveryInventoryStorageError(
                        "S3 trusted recovery download bytes differ"
                    )
            except BotoCoreError, OSError:
                raise RecoveryInventoryStorageError(
                    "S3 trusted recovery download failed"
                ) from None
        except BaseException as error:
            primary_error = error
            raise
        finally:
            _close_body(response, primary_error)
        try:
            os.fsync(destination_descriptor)
        except OSError:
            raise RecoveryInventoryStorageError(
                "trusted recovery destination sync failed"
            ) from None

    def _require_trusted_copy(self, request: TrustedCopyRequest) -> _ObjectIdentity:
        copied = self._head(request.destination_key)
        if (
            copied.sha256 != request.source.sha256
            or copied.size_bytes != request.source.size_bytes
        ):
            raise RecoveryInventoryStorageError("S3 trusted recovery bytes differ")
        return copied

    def _list_page(self, continuation_token: str | None) -> Mapping[str, object]:
        parameters: dict[str, object] = {
            "Bucket": self._bucket,
            "Prefix": _INVENTORY_PREFIX,
            "MaxKeys": self._page_size,
        }
        if continuation_token is not None:
            parameters["ContinuationToken"] = continuation_token
        try:
            return self._client.list_objects_v2(**parameters)
        except BotoCoreError, ClientError:
            raise RecoveryInventoryStorageError(
                "S3 recovery inventory listing failed"
            ) from None

    def _head(self, key: str) -> _ObjectIdentity:
        try:
            response = self._client.head_object(
                Bucket=self._bucket, Key=key, ChecksumMode="ENABLED"
            )
        except BotoCoreError, ClientError:
            raise RecoveryInventoryStorageError(
                "S3 operator object verification failed"
            ) from None
        return _parse_object_identity(response)

    def _get_bytes(
        self,
        key: str,
        *,
        maximum_bytes: int,
    ) -> tuple[bytes, _ObjectIdentity]:
        response = self._get(key, etag=None)
        primary_error: BaseException | None = None
        try:
            try:
                identity = _parse_object_identity(response)
                if identity.size_bytes > maximum_bytes:
                    raise RecoveryInventoryStorageError(
                        "S3 inventory object exceeds its bound"
                    )
                body = _response_body(response)
                content, sha256 = _read_bounded(body, maximum_bytes)
                if len(content) != identity.size_bytes or sha256 != identity.sha256:
                    raise RecoveryInventoryStorageError("S3 inventory bytes differ")
            except BotoCoreError:
                raise RecoveryInventoryStorageError(
                    "S3 inventory read failed"
                ) from None
        except BaseException as error:
            primary_error = error
            raise
        finally:
            _close_body(response, primary_error)
        return content, identity

    def _read_inventory_bytes(self, object_key: str) -> bytes:
        limit = MAX_INVENTORY_BYTES
        content, _identity = self._get_bytes(object_key, maximum_bytes=limit)
        return content

    def _get(self, key: str, *, etag: str | None) -> Mapping[str, object]:
        parameters: dict[str, object] = {
            "Bucket": self._bucket,
            "Key": key,
            "ChecksumMode": "ENABLED",
        }
        if etag is not None:
            parameters["IfMatch"] = etag
        try:
            return self._client.get_object(**parameters)
        except BotoCoreError, ClientError:
            raise RecoveryInventoryStorageError(
                "S3 operator object read failed"
            ) from None


def _parse_listed_inventory(raw_item: object) -> ListedInventoryObject:
    if not isinstance(raw_item, Mapping):
        raise RecoveryInventoryStorageError("S3 inventory listing item is malformed")
    item = cast(Mapping[str, object], raw_item)
    key = item.get("Key")
    size_bytes = item.get("Size")
    if not isinstance(key, str) or not _is_strict_integer(size_bytes):
        raise RecoveryInventoryStorageError("S3 inventory listing item is malformed")
    _validate_inventory_key(key)
    if size_bytes < 1 or size_bytes > MAX_INVENTORY_BYTES:
        raise RecoveryInventoryStorageError("S3 inventory listing size is invalid")
    return ListedInventoryObject(key)


def _parse_object_identity(response: Mapping[str, object]) -> _ObjectIdentity:
    size_bytes = response.get("ContentLength")
    checksum = response.get("ChecksumSHA256")
    checksum_type = response.get("ChecksumType")
    etag = response.get("ETag")
    last_modified = response.get("LastModified")
    if (
        not _is_strict_integer(size_bytes)
        or size_bytes < 1
        or not isinstance(checksum, str)
        # R2 omits ChecksumType for a single-part object; never accept composite.
        or checksum_type not in (None, "FULL_OBJECT")
        or not isinstance(etag, str)
        or _ETAG.fullmatch(etag) is None
        or not isinstance(last_modified, datetime)
        or last_modified.tzinfo is None
    ):
        raise RecoveryInventoryStorageError("S3 operator object identity is incomplete")
    last_modified_ms = int(last_modified.astimezone(UTC).timestamp() * 1000)
    if last_modified_ms < 1 or last_modified_ms > _MAX_TIMESTAMP_MS:
        raise RecoveryInventoryStorageError("S3 operator object timestamp is invalid")
    return _ObjectIdentity(
        etag,
        _decode_sha256(checksum),
        size_bytes,
        last_modified_ms,
    )


def _response_body(response: Mapping[str, object]) -> _StreamingBody:
    body = response.get("Body")
    if body is None or not hasattr(body, "read") or not hasattr(body, "close"):
        raise RecoveryInventoryStorageError("S3 operator response body is missing")
    return cast(_StreamingBody, body)


def _close_body(
    response: Mapping[str, object], primary_error: BaseException | None
) -> None:
    body = response.get("Body")
    if body is not None and hasattr(body, "close"):
        try:
            cast(_StreamingBody, body).close()
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(
                    "S3 operator response body cleanup also failed: "
                    + type(cleanup_error).__name__
                )
            elif isinstance(cleanup_error, Exception):
                raise RecoveryInventoryStorageError(
                    "S3 operator response body cleanup failed"
                ) from None
            else:
                raise


def _read_bounded(body: _StreamingBody, maximum_bytes: int) -> tuple[bytes, str]:
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    size_bytes = 0
    maximum_reads = maximum_bytes // _MIN_STREAM_CHUNK_BYTES + 2
    for _read in range(maximum_reads):
        remaining_bytes = maximum_bytes - size_bytes
        chunk = body.read(min(_STREAM_CHUNK_BYTES, remaining_bytes + 1))
        if not chunk:
            return b"".join(chunks), digest.hexdigest()
        if not isinstance(chunk, bytes):
            raise RecoveryInventoryStorageError("S3 inventory body returned non-bytes")
        size_bytes += len(chunk)
        if size_bytes > maximum_bytes:
            raise RecoveryInventoryStorageError("S3 inventory object exceeds its bound")
        chunks.append(chunk)
        digest.update(chunk)
    raise RecoveryInventoryStorageError(
        "S3 inventory read exceeded its operation bound"
    )


def _stream_to_descriptor(
    body: _StreamingBody,
    descriptor: int,
    *,
    maximum_size_bytes: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    maximum_reads = maximum_size_bytes // _MIN_STREAM_CHUNK_BYTES + 2
    for _read in range(maximum_reads):
        remaining_bytes = maximum_size_bytes - size_bytes
        chunk = body.read(min(_STREAM_CHUNK_BYTES, remaining_bytes + 1))
        if not chunk:
            return digest.hexdigest(), size_bytes
        if not isinstance(chunk, bytes):
            raise RecoveryInventoryStorageError("S3 recovery body returned non-bytes")
        size_bytes += len(chunk)
        if size_bytes > maximum_size_bytes:
            raise RecoveryInventoryStorageError("S3 recovery object exceeds its bound")
        _write_all(descriptor, chunk)
        digest.update(chunk)
    raise RecoveryInventoryStorageError("S3 recovery read exceeded its operation bound")


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    offset_bytes = 0
    for _write in range(len(content) + 1):
        if offset_bytes == len(content):
            return
        written = os.write(descriptor, view[offset_bytes:])
        if written < 1 or written > len(content) - offset_bytes:
            raise RecoveryInventoryStorageError("recovery write count is invalid")
        offset_bytes += written
    raise RecoveryInventoryStorageError("recovery write exceeded its operation bound")


def _validate_empty_destination(descriptor: int) -> None:
    status = os.fstat(descriptor)
    if not stat.S_ISREG(status.st_mode):
        raise RecoveryInventoryStorageError("restore destination is not a regular file")
    if stat.S_IMODE(status.st_mode) != 0o600 or status.st_uid != os.geteuid():
        raise RecoveryInventoryStorageError(
            "restore destination permissions are unsafe"
        )
    if (
        status.st_nlink != 1
        or status.st_size != 0
        or os.lseek(descriptor, 0, os.SEEK_CUR) != 0
    ):
        raise RecoveryInventoryStorageError("restore destination state is unsafe")


def _require_source_identity(
    identity: _ObjectIdentity, request: TrustedCopyRequest
) -> None:
    source = request.source
    if identity.sha256 != source.sha256 or identity.size_bytes != source.size_bytes:
        raise RecoveryInventoryStorageError("S3 recovery source bytes differ")


def _require_public_identity(
    actual: _ObjectIdentity, expected: TrustedObjectIdentity
) -> None:
    if (
        actual.sha256 != expected.sha256
        or actual.size_bytes != expected.size_bytes
        or actual.last_modified_ms != expected.last_modified_ms
    ):
        raise RecoveryInventoryStorageError("S3 trusted recovery identity differs")


def _validate_copy_request(request: TrustedCopyRequest) -> None:
    try:
        parsed = parse_trusted_recovery_key(request.destination_key)
        source_key = parse_recovery_key(request.source.key)
    except TrustedRecoveryKeyError, RecoveryVerificationError:
        raise RecoveryInventoryStorageError(
            "trusted recovery destination is invalid"
        ) from None
    if parsed.sha256 != request.source.sha256:
        raise RecoveryInventoryStorageError("trusted recovery destination differs")
    if (
        source_key.sha256 != request.source.sha256
        or source_key.state_revision != request.source.state_revision
    ):
        raise RecoveryInventoryStorageError("trusted recovery source is invalid")
    if not _is_strict_integer(request.source.state_revision):
        raise RecoveryInventoryStorageError("trusted recovery source is invalid")
    if (
        not _is_strict_integer(request.source.size_bytes)
        or request.source.size_bytes < 1
        or request.source.size_bytes > MAX_RECOVERY_OBJECT_BYTES
    ):
        raise RecoveryInventoryStorageError("trusted recovery source size is invalid")


def _validate_trusted_identity(identity: TrustedObjectIdentity) -> None:
    try:
        parsed = parse_trusted_recovery_key(identity.key)
    except TrustedRecoveryKeyError:
        raise RecoveryInventoryStorageError(
            "trusted recovery identity is invalid"
        ) from None
    if (
        parsed.sha256 != identity.sha256
        or not _is_strict_integer(identity.size_bytes)
        or identity.size_bytes < 1
        or identity.size_bytes > MAX_RECOVERY_OBJECT_BYTES
    ):
        raise RecoveryInventoryStorageError("trusted recovery identity is invalid")
    if (
        not _is_strict_integer(identity.last_modified_ms)
        or identity.last_modified_ms < 1
        or identity.last_modified_ms > _MAX_TIMESTAMP_MS
    ):
        raise RecoveryInventoryStorageError("trusted recovery identity is invalid")


def _validate_inventory_key(key: str) -> None:
    try:
        _ = parse_recovery_inventory_key(key)
    except TrustedRecoveryKeyError:
        raise RecoveryInventoryStorageError(
            "recovery inventory key is invalid"
        ) from None


def _validate_configuration(
    client: _S3Client,
    bucket: str,
    page_size: int,
) -> None:
    if (
        _S3_BUCKET.fullmatch(bucket) is None
        or ".." in bucket
        or ".-" in bucket
        or "-." in bucket
    ):
        raise RecoveryInventoryStorageError("S3 operator bucket is invalid")
    if not _is_strict_integer(page_size) or page_size < 1 or page_size > 1000:
        raise RecoveryInventoryStorageError("S3 operator page size is invalid")
    retries = client.meta.config.retries
    if retries is None or retries.get("total_max_attempts") != 1:
        raise RecoveryInventoryStorageError(
            "S3 operator client retries must be disabled"
        )


def _validate_inventory_bound(maximum_objects: object) -> None:
    if not _is_strict_integer(maximum_objects):
        raise RecoveryInventoryStorageError("inventory listing bound is invalid")
    if maximum_objects < 1 or maximum_objects > MAX_INVENTORY_OBJECTS:
        raise RecoveryInventoryStorageError("inventory listing bound is invalid")


def _validate_timeout(value: object) -> None:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
        or value > 300
    ):
        raise RecoveryInventoryStorageError("S3 operator timeout is invalid")


def _encode_sha256(sha256: str) -> str:
    try:
        decoded = bytes.fromhex(sha256)
    except ValueError:
        raise RecoveryInventoryStorageError("SHA-256 is invalid") from None
    if len(decoded) != 32 or decoded.hex() != sha256:
        raise RecoveryInventoryStorageError("SHA-256 is invalid")
    return base64.b64encode(decoded).decode("ascii")


def _decode_sha256(checksum: str) -> str:
    try:
        decoded = base64.b64decode(checksum, validate=True)
    except binascii.Error, ValueError:
        raise RecoveryInventoryStorageError("S3 checksum is invalid") from None
    if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != checksum:
        raise RecoveryInventoryStorageError("S3 checksum is invalid")
    return decoded.hex()


def _client_error_status(error: ClientError) -> int | None:
    response = cast(_ClientErrorLike, cast(object, error)).response
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    status = cast(Mapping[str, object], metadata).get("HTTPStatusCode")
    return status if _is_strict_integer(status) else None


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)
