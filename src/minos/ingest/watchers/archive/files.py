import hashlib
import os
import stat
from collections.abc import Generator
from contextlib import contextmanager
from typing import BinaryIO, TypeIs

from minos.ingest.watchers.archive.models import (
    MAX_BLOB_SIZE_BYTES,
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    StagedBlob,
    blob_key,
    validate_media_type,
)


@contextmanager
def open_verified_staged_blob(blob: StagedBlob) -> Generator[BinaryIO]:
    _ = blob_key(blob.sha256)
    validate_media_type(blob.media_type)
    declared_size_bytes: object = blob.size_bytes
    if (
        not _is_strict_integer(declared_size_bytes)
        or declared_size_bytes < 0
        or declared_size_bytes > MAX_BLOB_SIZE_BYTES
    ):
        raise BlobArchiveVerificationError(
            "staged blob byte count is outside the supported range"
        )
    descriptor = blob._lease.open_descriptor(  # pyright: ignore[reportPrivateUsage]
        blob.path
    )
    try:
        file = os.fdopen(descriptor, "rb", closefd=True)
    except BaseException as error:
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            error.add_note(f"descriptor cleanup also failed: {cleanup_error!r}")
        raise
    primary_error: BaseException | None = None
    try:
        try:
            _validate_file_status(os.fstat(file.fileno()))
            digest, size_bytes = _read_identity(
                file,
                maximum_size_bytes=blob.size_bytes,
            )
            if digest != blob.sha256 or size_bytes != blob.size_bytes:
                raise BlobArchiveVerificationError(
                    "staged blob bytes do not match their declared identity"
                )
            _ = file.seek(0)
            yield file
        except BaseException as error:
            primary_error = error
            raise
    finally:
        try:
            file.close()
        except BaseException as cleanup_error:
            if primary_error is not None:
                primary_error.add_note(
                    f"staged blob cleanup also failed: {cleanup_error!r}"
                )
            elif isinstance(cleanup_error, Exception):
                raise BlobArchiveStorageError(
                    "staged blob descriptor cleanup failed"
                ) from cleanup_error
            else:
                raise


def verify_archive_file(
    descriptor: int,
    *,
    expected_sha256: str,
    expected_size_bytes: int,
) -> None:
    _validate_file_status(os.fstat(descriptor))
    digest, size_bytes = _read_descriptor_identity(
        descriptor,
        maximum_size_bytes=expected_size_bytes,
    )
    if digest != expected_sha256 or size_bytes != expected_size_bytes:
        raise BlobArchiveVerificationError(
            "archive blob does not match the staged identity"
        )


def _validate_file_status(status: os.stat_result) -> None:
    if not stat.S_ISREG(status.st_mode):
        raise BlobArchiveVerificationError("blob descriptor is not a regular file")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise BlobArchiveVerificationError("blob file mode is not 0600")
    if status.st_uid != os.geteuid():
        raise BlobArchiveVerificationError("blob file owner is not the watcher user")
    if status.st_nlink != 1:
        raise BlobArchiveVerificationError(
            "blob file has an unexpected hard-link count"
        )


def _read_identity(file: BinaryIO, *, maximum_size_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size_bytes = 0
    while chunk := file.read(1024 * 1024):
        size_bytes += len(chunk)
        if size_bytes > maximum_size_bytes:
            raise BlobArchiveVerificationError(
                "blob file exceeds its declared byte count"
            )
        digest.update(chunk)
    return digest.hexdigest(), size_bytes


def _read_descriptor_identity(
    descriptor: int,
    *,
    maximum_size_bytes: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    offset_bytes = 0
    while chunk := os.pread(descriptor, 1024 * 1024, offset_bytes):
        offset_bytes += len(chunk)
        if offset_bytes > maximum_size_bytes:
            raise BlobArchiveVerificationError(
                "blob file exceeds its declared byte count"
            )
        digest.update(chunk)
    return digest.hexdigest(), offset_bytes


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)
