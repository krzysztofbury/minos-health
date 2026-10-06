# pyright: reportMissingTypeStubs=false
"""Version-pinned read-only S3 materialization for the existing local builder."""

import base64
import hashlib
import os
import sys
import tempfile
import time
from collections.abc import Generator, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Protocol, Self, cast, final

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from minos.domain.reference import SourceError
from minos.ingest.manifest_types import Digest
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import dataset_identity

MAX_ARCHIVE_OBJECTS = 128
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_TRANSFER_SECONDS = 600
STREAM_CHUNK_BYTES = 64 * 1024


class ArchiveObject(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    sha256: Digest
    size_bytes: int = Field(ge=1, le=64 * 1024 * 1024)
    version_id: Annotated[str, Field(pattern=r"^[!-~]{1,1024}$")]

    @model_validator(mode="after")
    def require_versioned_object(self) -> Self:
        if self.version_id == "null":
            raise ValueError("unversioned archive object is not supported")
        return self


class ArchiveSelection(BaseModel):
    """Transport pins only; source meaning remains in DatasetManifest."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    schema_version: Literal[1]
    dataset_id: Digest
    bucket: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")]
    region: Annotated[
        str, Field(pattern=r"^[a-z]{2}(?:-[a-z]+){1,2}-[1-9][0-9]?$", max_length=32)
    ]
    expected_bucket_owner: Annotated[str, Field(pattern=r"^[0-9]{12}$")]
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
        for item in _source_files(manifest):
            if item.sha256 in expected and expected[item.sha256] != item.size_bytes:
                raise SourceError("source manifest has conflicting blob sizes")
            expected[item.sha256] = item.size_bytes
        selected = {item.sha256: item.size_bytes for item in self.objects}
        if selected != expected:
            raise SourceError("archive selection does not exactly cover pinned inputs")


class _BlobIdentity(Protocol):
    @property
    def sha256(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...


def _source_files(manifest: DatasetManifest) -> Iterator[_BlobIdentity]:
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


class ArchiveS3Client(Protocol):
    def get_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class _Boto3(Protocol):
    def client(
        self, service_name: str, *, region_name: str, config: object
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
        boto3_api = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
        try:
            client = cast(
                ArchiveS3Client,
                boto3_api.client(
                    "s3",
                    region_name=selection.region,
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
        return cls(client, selection)

    def close(self) -> None:
        """The caller that creates the SDK client owns its connection pool."""
        primary_error = sys.exception()
        try:
            self._client.close()
        except BotoCoreError, OSError:
            if primary_error is None:
                raise SourceError("archive client cleanup failed") from None
            primary_error.add_note("archive client cleanup also failed")

    @contextmanager
    def materialize(
        self, manifest: DatasetManifest, *, staging_directory: Path
    ) -> Generator[Path]:
        """Offer no local archive until all pinned versions have been verified."""
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
                    self._download(item, target / item.sha256, deadline)
                yield archive
        except OSError:
            raise SourceError("archive materialization or cleanup failed") from None

    def _download(self, item: ArchiveObject, output: Path, deadline: float) -> None:
        _check_deadline(deadline)
        try:
            response = self._client.get_object(
                Bucket=self._selection.bucket,
                Key=f"blobs/sha256/{item.sha256}",
                VersionId=item.version_id,
                ChecksumMode="ENABLED",
                ExpectedBucketOwner=self._selection.expected_bucket_owner,
            )
        except BotoCoreError, ClientError:
            raise SourceError("version-pinned archive read failed") from None
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
        or response.get("VersionId") != item.version_id
        or response.get("ChecksumSHA256") != checksum
        or response.get("ChecksumType") != "FULL_OBJECT"
        or response.get("DeleteMarker", False) is not False
    ):
        raise SourceError("archive response differs from pinned version identity")


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
