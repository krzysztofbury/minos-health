"""Deterministic OCI data overlay; content verification is not release authority."""

import hashlib
import json
import math
import os
import stat
import tarfile
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, BinaryIO, ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError

from minos.domain.reference import ReferenceError
from minos.ingest.manifest_types import Digest
from minos.store.artifact import (
    MAX_MANIFEST_BYTES,
    canonical_json,
    dataset_identity,
    file_identity,
    read_regular_file,
)
from minos.store.reference import open_reference_bundle

MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
LAYER_MEDIA_TYPE = "application/vnd.oci.image.layer.v1.tar"
MAX_IMAGE_METADATA_BYTES = 1024 * 1024
MAX_DATA_LAYER_BYTES = 514 * 1024 * 1024 + 10240
IMAGE_CONTENT_TIMEOUT_SECONDS = 300
_CHUNK_BYTES = 64 * 1024
_HISTORY = {"created_by": "minos verified data-only bundle v1"}
type ImageDigest = Annotated[str, Field(pattern=r"^sha256:[a-f0-9]{64}$")]


class ImageContentError(RuntimeError):
    """Fixed redacted diagnostics, without source text, credentials or paths."""


class _ImageModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class DataImagePins(_ImageModel):
    application_manifest_sha256: Digest
    artifact_manifest_sha256: Digest


class DataImageContent(_ImageModel):
    status: Literal["data_image_content_only"]
    application_manifest_sha256: Digest
    artifact_manifest_sha256: Digest
    image_manifest_sha256: Digest
    image_config_sha256: Digest
    data_layer_sha256: Digest
    data_layer_size_bytes: int = Field(ge=10240, le=MAX_DATA_LAYER_BYTES)
    database_sha256: Digest
    dataset_id: Digest


class _Descriptor(_ImageModel):
    mediaType: str = Field(min_length=1, max_length=128)
    digest: ImageDigest
    size: int = Field(ge=1, le=2 * 1024 * 1024 * 1024)
    annotations: Annotated[dict[str, str], Field(max_length=64)] | None = None


class _Manifest(_ImageModel):
    schemaVersion: Literal[2]
    mediaType: Literal["application/vnd.oci.image.manifest.v1+json"]
    config: _Descriptor
    layers: Annotated[tuple[_Descriptor, ...], Field(max_length=128)]
    annotations: Annotated[dict[str, str], Field(max_length=64)] | None = None


class _RootFs(_ImageModel):
    type: Literal["layers"]
    diff_ids: Annotated[tuple[ImageDigest, ...], Field(max_length=128)]


class _History(_ImageModel):
    created: str | None = Field(default=None, max_length=128)
    created_by: str | None = Field(default=None, max_length=4096)
    author: str | None = Field(default=None, max_length=1024)
    comment: str | None = Field(default=None, max_length=1024)
    empty_layer: bool = False


class _Config(_ImageModel):
    architecture: Literal["amd64"]
    os: Literal["linux"]
    created: str | None = Field(default=None, max_length=128)
    author: str | None = Field(default=None, max_length=1024)
    config: Annotated[dict[str, JsonValue], Field(max_length=64)]
    rootfs: _RootFs
    history: Annotated[tuple[_History, ...], Field(max_length=512)] = ()


@dataclass(frozen=True, slots=True)
class _BundleFile:
    name: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _Bundle:
    files: tuple[_BundleFile, ...]
    database_sha256: str
    dataset_id: str


def package_data_image(
    *,
    application_manifest: bytes,
    application_config: bytes,
    bundle: Path,
    pins: DataImagePins,
    output: Path,
) -> DataImageContent:
    """Publish only a new metadata/layer delta; never rebuild application code.

    Inherited blobs remain in the approved registry. This is not a complete OCI
    layout or an activation envelope. Concurrent writers to one output are unsupported.
    """
    deadline = time.monotonic() + IMAGE_CONTENT_TIMEOUT_SECONDS
    manifest, config = _application(application_manifest, application_config, pins)
    selected = _bundle(bundle, pins)
    _check_deadline(deadline)
    if output.exists() or output.is_symlink():
        raise ImageContentError("data image output already exists")
    try:
        with tempfile.TemporaryDirectory(
            prefix=".minos-data-image-", dir=output.parent
        ) as name:
            temporary = Path(name)
            layer = temporary / "data-layer.tar"
            _write_layer(layer, bundle, selected.files, deadline)
            layer_digest, layer_size = _verify_layer(layer, selected.files, deadline)
            image_manifest, image_config = _metadata(
                manifest, config, layer_digest, layer_size
            )
            _ = (temporary / "image-manifest.json").write_bytes(image_manifest)
            _ = (temporary / "image-config.json").write_bytes(image_config)
            result = _content(
                pins, selected, image_manifest, image_config, layer_digest, layer_size
            )
            _ = (temporary / "image-content.json").write_bytes(canonical_json(result))
            _check_deadline(deadline)
            if output.exists() or output.is_symlink():
                raise ImageContentError("data image output appeared during packaging")
            os.rename(temporary, output)
            return result
    except OSError:
        raise ImageContentError("data image packaging or cleanup failed") from None


def verify_data_image(
    *,
    application_manifest: bytes,
    application_config: bytes,
    image_manifest: bytes,
    image_config: bytes,
    data_layer: Path,
    bundle: Path,
    pins: DataImagePins,
) -> DataImageContent:
    """Verify actual OCI bytes and exact bundle content, not labels or history claims.

    The caller must separately prove approved base path semantics, signatures,
    provenance, compatibility, source policy, authority freshness and receipt chain.
    """
    deadline = time.monotonic() + IMAGE_CONTENT_TIMEOUT_SECONDS
    manifest, config = _application(application_manifest, application_config, pins)
    selected = _bundle(bundle, pins)
    _ = _parse(_Manifest, image_manifest)
    _ = _parse(_Config, image_config)
    digest, size = _verify_layer(data_layer, selected.files, deadline)
    expected_manifest, expected_config = _metadata(manifest, config, digest, size)
    if image_config != expected_config:
        raise ImageContentError("data image changes application configuration")
    if image_manifest != expected_manifest:
        raise ImageContentError("data image changes application layers or descriptors")
    _check_deadline(deadline)
    return _content(pins, selected, image_manifest, image_config, digest, size)


def _application(
    manifest_bytes: bytes, config_bytes: bytes, pins: DataImagePins
) -> tuple[dict[str, object], dict[str, object]]:
    if hashlib.sha256(manifest_bytes).hexdigest() != pins.application_manifest_sha256:
        raise ImageContentError("application manifest differs from approved pin")
    manifest = _parse(_Manifest, manifest_bytes)
    config = _parse(_Config, config_bytes)
    if manifest.config.mediaType != CONFIG_MEDIA_TYPE or (
        manifest.config.digest != "sha256:" + hashlib.sha256(config_bytes).hexdigest()
        or manifest.config.size != len(config_bytes)
    ):
        raise ImageContentError("application configuration differs from descriptor")
    if len(manifest.layers) >= 128 or len(manifest.layers) != len(
        config.rootfs.diff_ids
    ):
        raise ImageContentError("application layer inventory is unsupported")
    if any(
        layer.mediaType not in (LAYER_MEDIA_TYPE, LAYER_MEDIA_TYPE + "+gzip")
        for layer in manifest.layers
    ):
        raise ImageContentError("application layer media type is unsupported")
    if config.config.get("User") != "10004:10004":
        raise ImageContentError("application user is unsupported")
    if sum(not item.empty_layer for item in config.history) != len(manifest.layers):
        raise ImageContentError("application history differs from layer inventory")
    return _raw_json(manifest_bytes), _raw_json(config_bytes)


def _bundle(bundle: Path, pins: DataImagePins) -> _Bundle:
    try:
        if bundle.is_symlink():
            raise ImageContentError("data image bundle is not a regular directory")
        content = read_regular_file(bundle / "manifest.json")
        if hashlib.sha256(content).hexdigest() != pins.artifact_manifest_sha256:
            raise ImageContentError("data image artifact differs from approved pin")
        with open_reference_bundle(bundle) as (_, manifest):
            if canonical_json(manifest) != content:
                raise ImageContentError("data image artifact changed during validation")
            review_digest, review_size = file_identity(
                bundle / "review.txt", max_bytes=MAX_MANIFEST_BYTES
            )
            if review_digest != manifest.review_summary_sha256:
                raise ImageContentError("data image review differs from artifact pin")
            return _Bundle(
                (
                    _BundleFile(
                        "manifest.json", pins.artifact_manifest_sha256, len(content)
                    ),
                    _BundleFile(
                        "minos.db",
                        manifest.database_sha256,
                        manifest.database_size_bytes,
                    ),
                    _BundleFile("review.txt", review_digest, review_size),
                ),
                manifest.database_sha256,
                dataset_identity(manifest.dataset),
            )
    except OSError, ReferenceError, ValidationError:
        raise ImageContentError("data image bundle could not be verified") from None


def _header(item: _BundleFile) -> bytes:
    header = tarfile.TarInfo("app/data/" + item.name)
    header.size = item.size_bytes
    header.mode = 0o444
    header.uid = header.gid = 10004
    header.mtime = 0
    return header.tobuf(format=tarfile.USTAR_FORMAT)


def _layer_size(files: tuple[_BundleFile, ...]) -> int:
    used = sum(512 + math.ceil(item.size_bytes / 512) * 512 for item in files)
    return math.ceil((used + 1024) / 10240) * 10240


def _write_layer(
    path: Path, bundle: Path, files: tuple[_BundleFile, ...], deadline: float
) -> None:
    with path.open("xb") as target:
        for item in files:
            _ = target.write(_header(item))
            descriptor = os.open(
                bundle / item.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            )
            with os.fdopen(descriptor, "rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ImageContentError("data image input is not a regular file")
                digest = _copy_payload(source, target, item.size_bytes, deadline)
                if source.read(1) or digest != item.sha256:
                    raise ImageContentError(
                        "data image input differs from artifact pin"
                    )
            _ = target.write(bytes((-item.size_bytes) % 512))
        _ = target.write(bytes(_layer_size(files) - target.tell()))
        target.flush()
        os.fsync(target.fileno())


def _copy_payload(
    source: BinaryIO, target: BinaryIO | None, size: int, deadline: float
) -> str:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        _check_deadline(deadline)
        chunk = source.read(min(remaining, _CHUNK_BYTES))
        if not chunk:
            raise ImageContentError("data image payload is truncated")
        if target is not None and target.write(chunk) != len(chunk):
            raise ImageContentError("data image payload write is incomplete")
        digest.update(chunk)
        remaining -= len(chunk)
    return digest.hexdigest()


def _verify_layer(
    path: Path, files: tuple[_BundleFile, ...], deadline: float
) -> tuple[str, int]:
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size != _layer_size(files):
                raise ImageContentError("data image layer has unsupported type or size")
            for item in files:
                if source.read(512) != _header(item):
                    raise ImageContentError(
                        "data image layer header is not the fixed data recipe"
                    )
                if (
                    _copy_payload(source, None, item.size_bytes, deadline)
                    != item.sha256
                ):
                    raise ImageContentError(
                        "data image layer content differs from artifact pin"
                    )
                _zero_bytes(source, (-item.size_bytes) % 512)
            _zero_bytes(source, before.st_size - source.tell())
            _ = source.seek(0)
            digest = _copy_payload(source, None, before.st_size, deadline)
            if (
                source.read(1)
                or _file_snapshot(os.fstat(source.fileno())) != _file_snapshot(before)
                or _file_snapshot(path.lstat()) != _file_snapshot(before)
            ):
                raise ImageContentError("data image layer changed during verification")
            return digest, before.st_size
    except OSError:
        raise ImageContentError("data image layer could not be verified") from None


def _zero_bytes(source: BinaryIO, size: int) -> None:
    if not 0 <= size <= 10240 or source.read(size) != bytes(size):
        raise ImageContentError("data image layer padding is unsupported")


def _file_snapshot(info: os.stat_result) -> tuple[int, ...]:
    # Reads may update atime. Nanosecond change/write times, inode and ownership
    # still detect mutation or pathname replacement during the two bounded passes.
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _metadata(
    manifest: dict[str, object], config: dict[str, object], digest: str, size: int
) -> tuple[bytes, bytes]:
    # Preserve every runtime/top-level field verbatim. Only append one diff ID,
    # one informational history record and its corresponding layer descriptor.
    rootfs = cast(dict[str, object], config["rootfs"])
    new_config = dict(config)
    new_config["rootfs"] = {
        **rootfs,
        "diff_ids": [*cast(list[str], rootfs["diff_ids"]), "sha256:" + digest],
    }
    new_config["history"] = [*cast(list[object], config.get("history", [])), _HISTORY]
    config_bytes = canonical_json(new_config)
    new_manifest = dict(manifest)
    new_manifest["config"] = {
        **cast(dict[str, object], manifest["config"]),
        "digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
        "size": len(config_bytes),
    }
    new_manifest["layers"] = [
        *cast(list[object], manifest["layers"]),
        {"mediaType": LAYER_MEDIA_TYPE, "digest": "sha256:" + digest, "size": size},
    ]
    manifest_bytes = canonical_json(new_manifest)
    _ = _parse(_Manifest, manifest_bytes)
    _ = _parse(_Config, config_bytes)
    return manifest_bytes, config_bytes


def _content(
    pins: DataImagePins,
    bundle: _Bundle,
    manifest: bytes,
    config: bytes,
    digest: str,
    size: int,
) -> DataImageContent:
    return DataImageContent(
        status="data_image_content_only",
        application_manifest_sha256=pins.application_manifest_sha256,
        artifact_manifest_sha256=pins.artifact_manifest_sha256,
        image_manifest_sha256=hashlib.sha256(manifest).hexdigest(),
        image_config_sha256=hashlib.sha256(config).hexdigest(),
        data_layer_sha256=digest,
        data_layer_size_bytes=size,
        database_sha256=bundle.database_sha256,
        dataset_id=bundle.dataset_id,
    )


def _raw_json(content: bytes) -> dict[str, object]:
    return cast(dict[str, object], json.loads(content, object_pairs_hook=_unique_keys))


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ImageContentError("image metadata contains duplicate fields")
        result[key] = value
    return result


def _parse[T: BaseModel](model: type[T], content: bytes) -> T:
    try:
        if not 1 <= len(content) <= MAX_IMAGE_METADATA_BYTES:
            raise ImageContentError("image metadata exceeds its byte bound")
        _ = _raw_json(content)
        return model.model_validate_json(content)
    except ValueError, UnicodeError, RecursionError:
        raise ImageContentError("image metadata is unsupported") from None


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ImageContentError("data image content deadline exceeded")
