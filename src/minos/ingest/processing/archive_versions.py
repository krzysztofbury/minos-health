# pyright: reportMissingTypeStubs=false
"""Bounded metadata-only version resolution at already pinned immutable blob keys."""

import base64
import sys
import time
from collections.abc import Mapping
from typing import Annotated, ClassVar, Protocol, Self, cast, final

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import (  # type: ignore[import-untyped]
    BotoCoreError,
    ClientError,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.reference import SourceError
from minos.ingest.archive_build import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_OBJECTS,
    MAX_TRANSFER_SECONDS,
    ArchiveBlob,
    ArchiveObject,
    ArchiveSelection,
    dataset_archive_blobs,
)
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import dataset_identity


class ArchiveLocation(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    bucket: Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$")]
    region: Annotated[
        str, Field(pattern=r"^[a-z]{2}(?:-[a-z]+){1,2}-[1-9][0-9]?$", max_length=32)
    ]
    expected_bucket_owner: Annotated[str, Field(pattern=r"^[0-9]{12}$")]


class VersionClient(Protocol):
    def list_object_versions(self, **kwargs: object) -> Mapping[str, object]: ...

    def head_object(self, **kwargs: object) -> Mapping[str, object]: ...

    def close(self) -> None: ...


class _Boto3(Protocol):
    def client(
        self, service_name: str, *, region_name: str, config: object
    ) -> object: ...


@final
class S3ArchiveVersionSelector:
    def __init__(self, client: VersionClient, location: ArchiveLocation) -> None:
        self._client = client
        self._location = location

    @classmethod
    def create(cls, location: ArchiveLocation) -> Self:
        sdk = cast(_Boto3, boto3)  # pyright: ignore[reportInvalidCast]
        try:
            client = cast(
                VersionClient,
                sdk.client(
                    "s3",
                    region_name=location.region,
                    config=Config(
                        connect_timeout=10,
                        read_timeout=30,
                        retries={"total_max_attempts": 1, "mode": "standard"},
                        ignore_configured_endpoint_urls=True,
                    ),
                ),
            )
        except BotoCoreError, ClientError:
            raise SourceError("archive version client could not be created") from None
        return cls(client, location)

    def close(self) -> None:
        primary = sys.exception()
        try:
            self._client.close()
        except BotoCoreError, OSError:
            if primary is None:
                raise SourceError("archive version client cleanup failed") from None
            primary.add_note("archive version client cleanup also failed")

    def select_dataset(self, manifest: DatasetManifest) -> ArchiveSelection:
        """Return the existing transport contract, not a parallel data manifest."""
        objects = self.select_blobs(dataset_archive_blobs(manifest))
        result = ArchiveSelection(
            schema_version=1,
            dataset_id=dataset_identity(manifest),
            bucket=self._location.bucket,
            region=self._location.region,
            expected_bucket_owner=self._location.expected_bucket_owner,
            objects=objects,
        )
        result.require_dataset(manifest)
        return result

    def select_blobs(self, blobs: tuple[ArchiveBlob, ...]) -> tuple[ArchiveObject, ...]:
        """Resolve all or fail; HEAD checks do not replace subsequent byte verification."""
        if not 1 <= len(blobs) <= MAX_ARCHIVE_OBJECTS:
            raise SourceError("archive version selection object bound exceeded")
        if len({item.sha256 for item in blobs}) != len(blobs):
            raise SourceError("archive version selection has duplicate identities")
        if sum(item.size_bytes for item in blobs) > MAX_ARCHIVE_BYTES:
            raise SourceError("archive version selection byte bound exceeded")
        deadline = time.monotonic() + MAX_TRANSFER_SECONDS
        try:
            return tuple(
                self._select_one(item, deadline)
                for item in sorted(blobs, key=lambda item: item.sha256)
            )
        except BotoCoreError, ClientError, ValidationError:
            raise SourceError(
                "archive version evidence could not be verified"
            ) from None

    def _select_one(self, item: ArchiveBlob, deadline: float) -> ArchiveObject:
        _check_deadline(deadline)
        key = f"blobs/sha256/{item.sha256}"
        listed = self._client.list_object_versions(
            Bucket=self._location.bucket,
            Prefix=key,
            MaxKeys=2,
            ExpectedBucketOwner=self._location.expected_bucket_owner,
        )
        _check_deadline(deadline)
        version = _listed_version(listed, key, item.size_bytes)
        pin = ArchiveObject(
            sha256=item.sha256, size_bytes=item.size_bytes, version_id=version
        )
        response = self._client.head_object(
            Bucket=self._location.bucket,
            Key=key,
            VersionId=pin.version_id,
            ExpectedBucketOwner=self._location.expected_bucket_owner,
            ChecksumMode="ENABLED",
        )
        _check_deadline(deadline)
        checksum = base64.b64encode(bytes.fromhex(item.sha256)).decode("ascii")
        length = response.get("ContentLength")
        if (
            type(length) is not int
            or length != item.size_bytes
            or response.get("VersionId") != pin.version_id
            or response.get("ChecksumSHA256") != checksum
            or response.get("ChecksumType") != "FULL_OBJECT"
            or response.get("DeleteMarker", False) is not False
        ):
            raise SourceError("archive version metadata differs from pinned blob")
        return pin


def _listed_version(response: Mapping[str, object], key: str, size_bytes: int) -> str:
    # Immutable conditional-create keys should have one version. Never paginate a
    # partial listing or choose an IsLatest version from ambiguous history.
    if response.get("IsTruncated") is not False:
        raise SourceError("archive version listing is incomplete")
    if response.get("DeleteMarkers", []) != []:
        raise SourceError("archive version listing contains a delete marker")
    versions = response.get("Versions")
    if not isinstance(versions, list) or len(cast(list[object], versions)) != 1:
        raise SourceError("archive blob version is absent or ambiguous")
    raw: object = cast(list[object], versions)[0]
    if not isinstance(raw, dict):
        raise SourceError("archive blob version identity is unsupported")
    version = cast(dict[str, object], raw)
    if (
        version.get("Key") != key
        or type(version.get("Size")) is not int
        or version["Size"] != size_bytes
    ):
        raise SourceError("archive listed version differs from pinned blob")
    identity = version.get("VersionId")
    if not isinstance(identity, str):
        raise SourceError("archive blob version identity is unsupported")
    return identity


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SourceError("archive version selection deadline exceeded")
