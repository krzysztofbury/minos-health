import os
from collections.abc import Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import BinaryIO, Literal

type RecoveryClass = Literal["run", "daily", "weekly", "monthly"]

MAX_RECOVERY_IMAGE_SIZE_BYTES = 2 * 1024 * 1024 * 1024


class RecoveryError(RuntimeError):
    """A control recovery image operation could not be proven safe."""


class RecoveryStagingError(RecoveryError):
    """Recovery staging violated its local filesystem contract."""


class RecoveryStorageError(RecoveryError):
    """The recovery object store did not complete an operation safely."""


class RecoveryConflictError(RecoveryStorageError):
    """A bounded conditional recovery write exhausted its retries."""


class RecoveryVerificationError(RecoveryError):
    """Recovery bytes or metadata failed integrity verification."""


class RecoveryListingBoundError(RecoveryStorageError):
    """A recovery listing exceeded its configured object bound."""


@dataclass(frozen=True, slots=True)
class RecoveryImageRequest:
    recovery_class: RecoveryClass
    created_at_ms: int
    run_id: str | None = None
    committed_state_revision: int | None = None


@dataclass(frozen=True, slots=True)
class RecoveryImageMetadata:
    recovery_class: RecoveryClass
    state_revision: int
    schema_version: int
    migration_set_sha256: str
    created_at_ms: int
    run_id: str | None
    committed_state_revision: int | None


@dataclass(slots=True)
class RecoveryImageLease:
    descriptor: int
    _active: bool = True
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    @contextmanager
    def open(self) -> Generator[BinaryIO]:
        with self._lock:
            if not self._active:
                raise RecoveryStagingError("recovery image lease is closed")
            duplicate = os.dup(self.descriptor)
        try:
            _ = os.lseek(duplicate, 0, os.SEEK_SET)
            file = os.fdopen(duplicate, "rb", closefd=True)
        except BaseException as error:
            try:
                os.close(duplicate)
            except BaseException as cleanup_error:
                error.add_note(
                    f"recovery duplicate cleanup also failed: {cleanup_error!r}"
                )
            raise
        active_error: BaseException | None = None
        try:
            yield file
        except BaseException as error:
            active_error = error
            raise
        finally:
            try:
                file.close()
            except BaseException as cleanup_error:
                if active_error is not None:
                    active_error.add_note(
                        f"recovery image file cleanup also failed: {cleanup_error!r}"
                    )
                elif isinstance(cleanup_error, Exception):
                    raise RecoveryStagingError(
                        "recovery image file cleanup failed"
                    ) from cleanup_error
                else:
                    raise

    def close(self) -> None:
        with self._lock:
            if not self._active:
                return
            os.close(self.descriptor)
            self._active = False


@dataclass(frozen=True, slots=True)
class StagedRecoveryImage:
    path: Path
    metadata: RecoveryImageMetadata
    sha256: str
    size_bytes: int
    _lease: RecoveryImageLease = field(repr=False, compare=False)

    def open(self) -> AbstractContextManager[BinaryIO]:
        return self._lease.open()


@dataclass(frozen=True, slots=True)
class ListedRecoveryImage:
    key: str
    size_bytes: int
    last_modified_ms: int


@dataclass(frozen=True, slots=True)
class PublishedRecoveryImage:
    key: str
    sha256: str
    size_bytes: int
    metadata: RecoveryImageMetadata


@dataclass(frozen=True, slots=True)
class VerifiedRecoveryCandidate:
    key: str
    sha256: str
    size_bytes: int
    last_modified_ms: int
    metadata: RecoveryImageMetadata


@dataclass(frozen=True, slots=True)
class VerifiedRecoveryImage:
    path: Path
    candidate: VerifiedRecoveryCandidate
