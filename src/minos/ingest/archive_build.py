# pyright: reportMissingTypeStubs=false
"""Checksum-pinned read-only R2 materialization for the existing local builder."""

import base64
import hashlib
import os
import re
import sys
import tempfile
import time
from collections.abc import Generator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Protocol, Self, cast, final

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

from minos.domain.reference import SourceError
from minos.ingest.manifest_types import Digest
from minos.ingest.object_storage import R2_REGION, validate_r2_endpoint
from minos.ingest.reference_manifest import MAX_ICD11_SOURCE_BYTES, DatasetManifest
from minos.store.artifact import dataset_identity

MAX_ARCHIVE_OBJECTS = 128
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TRANSFER_SECONDS = 600
STREAM_CHUNK_BYTES = 64 * 1024
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_DIGEST = re.compile(r"[0-9a-f]{64}")


class ArchiveObject(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    # The key embeds the SHA-256 and R2 refuses overwrites under blobs/, so
    # checksum and size are the complete pin (see minos.ingest.object_storage).
    sha256: Digest
    # A live input may be as large as any reviewed source (ICD-11 PL is 112 MiB).
    size_bytes: int = Field(ge=1, le=MAX_ICD11_SOURCE_BYTES)


class ArchiveSelection(BaseModel):
    """Transport pins only; source meaning remains in DatasetManifest."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    schema_version: Literal[2]
    dataset_id: Digest
    bucket: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")]
    endpoint: Annotated[str, AfterValidator(validate_r2_endpoint)]
    objects: Annotated[
        tuple[ArchiveObject, ...], Field(min_length=1, max_length=MAX_ARCHIVE_OBJECTS)
    ]

    @model_validator(mode="after")
    def require_unique_bounded_objects(self) -> Self:
        if len({item.sha256 for item in self.objects}) != len(self.objects):
            raise ValueError("duplicate archive object selection")
        if sum(item.size_bytes for item in self.objects) > MAX_ARCHIVE_BYTES:
            raise ValueError("archive selection exceeds cumulative byte limit")
        return self

    def require_dataset(self, manifest: DatasetManifest) -> None:
        if self.dataset_id != dataset_identity(manifest):
            raise SourceError("archive selection belongs to another dataset")
        expected: dict[str, int] = {}
        for item in source_files(manifest):
            if item.sha256 in expected and expected[item.sha256] != item.size_bytes:
                raise SourceError("source manifest has conflicting blob sizes")
            expected[item.sha256] = item.size_bytes
        selected = {item.sha256: item.size_bytes for item in self.objects}
        if selected != expected:
            raise SourceError("archive selection does not exactly cover pinned inputs")


class BlobIdentity(Protocol):
    @property
    def sha256(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...


def source_files(manifest: DatasetManifest) -> Iterator[BlobIdentity]:
    yield from manifest.sources
    for notice in manifest.komunikaty:
        yield notice.algorithm
        yield notice.ranks
        yield notice.specialties
    for catalog in manifest.catalogs:
        yield catalog.workbook
    for conditions in manifest.characteristics:
        yield conditions.workbook
    for modifier in manifest.modifiers:
        yield modifier.xml
        if modifier.display is not None:
            yield modifier.display.file
    for binding in manifest.modifier_bindings:
        yield binding.overlay
    if manifest.icd11_maps is not None:
        yield from manifest.icd11_maps.files
    if manifest.jgp_statistics is not None:
        yield from manifest.jgp_statistics.reports


class ArchiveS3Client(Protocol):
    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class BlobS3Client(ArchiveS3Client, Protocol):
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


class _StreamingBody(Protocol):
    def read(self, amount: int) -> object: ...

    def close(self) -> None: ...


@final
class S3ArchiveReader:
    def __init__(self, client: ArchiveS3Client, selection: ArchiveSelection) -> None:
        self._client = client
        self._selection = selection

    @classmethod
    def create(cls, selection: ArchiveSelection) -> Self:
        return cls(create_r2_client(selection.endpoint), selection)

    def close(self) -> None:
        """The caller that creates the SDK client owns its connection pool."""
        _close_client(self._client)

    @contextmanager
    def materialize(
        self, manifest: DatasetManifest, *, staging_directory: Path
    ) -> Generator[Path]:
        """Offer no local archive until every pinned object has been verified."""
        self._selection.require_dataset(manifest)
        deadline = time.monotonic() + MAX_TRANSFER_SECONDS
        try:
            with tempfile.TemporaryDirectory(
                prefix=".minos-archive-", dir=staging_directory
            ) as temporary:
                archive = Path(temporary)
                target = archive / "blobs" / "sha256"
                target.mkdir(parents=True, mode=0o700)
                for item in sorted(self._selection.objects, key=lambda pin: pin.sha256):
                    _download(
                        self._client,
                        self._selection.bucket,
                        item,
                        target / item.sha256,
                        deadline,
                    )
                yield archive
        except OSError:
            raise SourceError("archive materialization or cleanup failed") from None


@final
class R2BlobReader:
    """Read archive blobs named only by digest, with the same verified writes.

    Public projections carry digests but not every size (#141 B3). A HEAD
    answer bounded by the caller supplies the size; the download then pins
    both, and the body digest must still equal the key.
    """

    def __init__(self, client: BlobS3Client, bucket: str) -> None:
        if _BUCKET.fullmatch(bucket) is None:
            raise SourceError("archive bucket name is unsupported")
        self._client = client
        self._bucket = bucket

    @classmethod
    def create(cls, *, bucket: str, endpoint: str) -> Self:
        try:
            endpoint = validate_r2_endpoint(endpoint)
        except ValueError:
            raise SourceError("archive endpoint is unsupported") from None
        return cls(cast(BlobS3Client, create_r2_client(endpoint)), bucket)

    def close(self) -> None:
        _close_client(self._client)

    def pin(self, sha256: str, *, max_bytes: int) -> ArchiveObject:
        if _DIGEST.fullmatch(sha256) is None:
            raise SourceError("archive digest is invalid")
        try:
            response = self._client.head_object(
                Bucket=self._bucket,
                Key=f"blobs/sha256/{sha256}",
                ChecksumMode="ENABLED",
            )
        except BotoCoreError, ClientError:
            raise SourceError("archive object metadata read failed") from None
        length = response.get("ContentLength")
        checksum = response.get("ChecksumSHA256")
        if (
            not isinstance(length, int)
            or isinstance(length, bool)
            or not 1 <= length <= max_bytes
            or (
                checksum is not None
                and checksum != base64.b64encode(bytes.fromhex(sha256)).decode()
            )
        ):
            raise SourceError("archive object metadata differs from its bound")
        try:
            return ArchiveObject(sha256=sha256, size_bytes=length)
        except ValueError:
            raise SourceError(
                "archive object metadata differs from its bound"
            ) from None

    def download(self, objects: Iterable[ArchiveObject], archive: Path) -> None:
        """Write each blob under ``archive``; an existing file is never replaced."""
        deadline = time.monotonic() + MAX_TRANSFER_SECONDS
        target = archive / "blobs" / "sha256"
        try:
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            for item in sorted(objects, key=lambda pin: pin.sha256):
                _download(
                    self._client, self._bucket, item, target / item.sha256, deadline
                )
        except OSError:
            raise SourceError("archive blob write failed") from None


def create_r2_client(endpoint: str) -> ArchiveS3Client:
    boto3_api = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
    try:
        return cast(
            ArchiveS3Client,
            boto3_api.client(
                "s3",
                region_name=R2_REGION,
                endpoint_url=endpoint,
                config=Config(
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"total_max_attempts": 1, "mode": "standard"},
                    ignore_configured_endpoint_urls=True,
                ),
            ),
        )
    except BotoCoreError, ClientError:
        raise SourceError("archive read client could not be created") from None


def _close_client(client: ArchiveS3Client) -> None:
    primary_error = sys.exception()
    try:
        client.close()
    except BotoCoreError, OSError:
        if primary_error is None:
            raise SourceError("archive client cleanup failed") from None
        primary_error.add_note("archive client cleanup also failed")


def _download(
    client: ArchiveS3Client,
    bucket: str,
    item: ArchiveObject,
    output: Path,
    deadline: float,
) -> None:
    _check_deadline(deadline)
    try:
        response = client.get_object(
            Bucket=bucket,
            Key=f"blobs/sha256/{item.sha256}",
            ChecksumMode="ENABLED",
        )
    except BotoCoreError, ClientError:
        raise SourceError("checksum-pinned archive read failed") from None
    with _response_stream(response) as stream:
        _check_deadline(deadline)
        _require_response_identity(response, item)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            _copy_verified(stream, target, item, deadline)
            target.flush()
            os.fsync(target.fileno())


def _require_response_identity(
    response: Mapping[str, object], item: ArchiveObject
) -> None:
    length = response.get("ContentLength")
    checksum = base64.b64encode(bytes.fromhex(item.sha256)).decode("ascii")
    if (
        not isinstance(length, int)
        or isinstance(length, bool)
        or length != item.size_bytes
        or response.get("ChecksumSHA256") != checksum
        # R2 omits ChecksumType for a single-part object; never accept composite.
        or response.get("ChecksumType") not in (None, "FULL_OBJECT")
    ):
        raise SourceError("archive response differs from pinned identity")


@contextmanager
def _response_stream(response: Mapping[str, object]) -> Generator[_StreamingBody]:
    body = response.get("Body")
    if not callable(getattr(body, "close", None)):
        raise SourceError("archive response has no closable body")
    stream = cast(_StreamingBody, body)
    try:
        if not callable(getattr(body, "read", None)):
            raise SourceError("archive response has no readable body")
        yield stream
    finally:
        primary_error = sys.exception()
        try:
            stream.close()
        except BotoCoreError, OSError:
            if primary_error is None:
                raise SourceError("archive response cleanup failed") from None
            primary_error.add_note("archive response cleanup also failed")


class _Writable(Protocol):
    def write(self, data: bytes, /) -> int: ...


def _copy_verified(
    stream: _StreamingBody, target: _Writable, item: ArchiveObject, deadline: float
) -> None:
    digest = hashlib.sha256()
    consumed = 0
    # Each iteration either stops or consumes at least one byte; the extra byte
    # detects a body larger than the independently checked ContentLength.
    while consumed <= item.size_bytes:
        _check_deadline(deadline)
        amount = min(STREAM_CHUNK_BYTES, item.size_bytes - consumed + 1)
        try:
            chunk = stream.read(amount)
        except BotoCoreError, OSError:
            raise SourceError("archive response read failed") from None
        _check_deadline(deadline)
        if not isinstance(chunk, bytes) or len(chunk) > amount:
            raise SourceError("archive response exceeded its read bound")
        if not chunk:
            break
        consumed += len(chunk)
        if consumed > item.size_bytes:
            raise SourceError("archive body exceeds pinned byte count")
        digest.update(chunk)
        if target.write(chunk) != len(chunk):
            raise SourceError("archive snapshot write was incomplete")
    if consumed != item.size_bytes or digest.hexdigest() != item.sha256:
        raise SourceError("archive body checksum or byte count differs")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SourceError("archive materialization deadline exceeded")
