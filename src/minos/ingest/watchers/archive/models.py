import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Protocol

from minos.ingest.watchers.control import BlobReceipt

BLOB_KEY_PREFIX = "blobs/sha256/"
MAX_BLOB_SIZE_BYTES = 512 * 1024 * 1024


class BlobArchiveError(RuntimeError):
    """A raw blob could not be staged or archived safely."""


class BlobStagingError(BlobArchiveError):
    """A local staging operation violated its bounded contract."""


class BlobSizeLimitError(BlobStagingError):
    """A staged blob exceeded its configured byte ceiling."""


class BlobArchiveStorageError(BlobArchiveError):
    """The archive backend could not complete a storage operation."""


class BlobArchiveConflictError(BlobArchiveStorageError):
    """The archive exhausted its bounded conditional-write retries."""


class BlobArchiveVerificationError(BlobArchiveError):
    """Archived bytes do not match the locally staged blob identity."""


@dataclass(slots=True)
class StagedBlobLease:
    _descriptor: int
    _active: bool = True
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def open_descriptor(self, path: Path) -> int:
        with self._lock:
            if not self._active:
                raise BlobArchiveStorageError("staged blob lease is closed")
            descriptor = -1
            try:
                reference_status = os.fstat(self._descriptor)
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                )
                opened_status = os.fstat(descriptor)
            except OSError as error:
                if descriptor >= 0:
                    try:
                        os.close(descriptor)
                    except OSError as cleanup_error:
                        error.add_note(
                            f"descriptor cleanup also failed: {cleanup_error!r}"
                        )
                raise BlobArchiveStorageError(
                    "staged blob descriptor is unavailable"
                ) from error
            if (
                opened_status.st_dev != reference_status.st_dev
                or opened_status.st_ino != reference_status.st_ino
            ):
                failure = BlobArchiveVerificationError(
                    "staged blob path identity changed"
                )
                try:
                    os.close(descriptor)
                except OSError as cleanup_error:
                    failure.add_note(
                        f"descriptor cleanup also failed: {cleanup_error!r}"
                    )
                raise failure
            return descriptor

    def close(self) -> None:
        with self._lock:
            if not self._active:
                return
            self._active = False
            os.close(self._descriptor)


@dataclass(frozen=True, slots=True)
class StagedBlob:
    path: Path
    sha256: str
    size_bytes: int
    media_type: str | None
    _lease: StagedBlobLease = field(repr=False, compare=False)


class BlobArchive(Protocol):
    def put(self, blob: StagedBlob) -> BlobReceipt:
        """Create or verify one immutable content-addressed blob."""
        ...


def blob_key(sha256: str) -> str:
    if len(sha256) != 64 or any(
        character not in "0123456789abcdef" for character in sha256
    ):
        raise BlobArchiveVerificationError("blob SHA-256 must be lowercase hexadecimal")
    return f"{BLOB_KEY_PREFIX}{sha256}"


def validate_media_type(media_type: object) -> None:
    if media_type is not None and (
        not isinstance(media_type, str) or len(media_type) < 1 or len(media_type) > 255
    ):
        raise BlobArchiveVerificationError(
            "blob media type is outside the supported range"
        )
