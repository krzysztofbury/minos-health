import fcntl
import hashlib
import io
import os
import secrets
import stat
from collections.abc import Buffer, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeIs, final, override

from minos.ingest.watchers.archive.models import (
    MAX_BLOB_SIZE_BYTES,
    BlobArchiveVerificationError,
    BlobSizeLimitError,
    BlobStagingError,
    StagedBlob,
    StagedBlobLease,
    blob_key,
    validate_media_type,
)

_STAGING_PREFIX = ".minos-blob-"
_MAX_CREATE_ATTEMPTS = 10
_MAX_STALE_FILES = 16


@final
class StagedBlobWriter(io.RawIOBase):
    def __init__(
        self,
        path: Path,
        file_name: str,
        file: io.FileIO,
        maximum_size_bytes: int,
    ) -> None:
        super().__init__()
        self._path = path
        self._file_name = file_name
        self._file = file
        self._maximum_size_bytes = maximum_size_bytes
        self._sha256 = hashlib.sha256()
        self._size_bytes = 0
        self._sealed = False
        self._sealed_lease: StagedBlobLease | None = None

    @property
    def path(self) -> Path:
        return self._path

    @property
    def size_bytes(self) -> int:
        return self._size_bytes

    @property
    def file_name(self) -> str:
        return self._file_name

    @override
    def writable(self) -> bool:
        return True

    @override
    def seekable(self) -> bool:
        return True

    @override
    def fileno(self) -> int:
        return self._file.fileno()

    @override
    def tell(self) -> int:
        return self._file.tell()

    @override
    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        self._require_mutable()
        if offset != 0 or whence != os.SEEK_SET:
            raise BlobStagingError("staged blob writer only supports rewind to zero")
        return self._file.seek(offset, whence)

    @override
    def truncate(self, size: int | None = None) -> int:
        self._require_mutable()
        target_size = self.tell() if size is None else size
        if target_size != 0 or self.tell() != 0:
            raise BlobStagingError("staged blob writer only supports reset to empty")
        truncated = self._file.truncate(0)
        self._sha256 = hashlib.sha256()
        self._size_bytes = 0
        return truncated

    @override
    def write(self, buffer: Buffer, /) -> int:
        self._require_mutable()
        if self.tell() != self._size_bytes:
            raise BlobStagingError("staged blob writes must append sequentially")
        view = memoryview(buffer)
        next_size_bytes = self._size_bytes + view.nbytes
        if next_size_bytes > self._maximum_size_bytes:
            raise BlobSizeLimitError("staged blob exceeds its configured byte ceiling")
        written = self._file.write(view)
        if written is None or written < 0 or written > view.nbytes:
            raise BlobStagingError("staged blob write returned an invalid byte count")
        self._sha256.update(view[:written])
        self._size_bytes += written
        return written

    @override
    def flush(self) -> None:
        if not self.closed:
            self._file.flush()

    def seal(
        self,
        *,
        media_type: str | None,
        expected_sha256: str | None = None,
        expected_size_bytes: int | None = None,
    ) -> StagedBlob:
        self._require_mutable()
        validate_media_type(media_type)
        if (expected_sha256 is None) != (expected_size_bytes is None):
            raise BlobStagingError(
                "producer checksum and byte count must be provided together"
            )
        if expected_sha256 is not None:
            try:
                _ = blob_key(expected_sha256)
            except BlobArchiveVerificationError as error:
                raise BlobStagingError("producer checksum is invalid") from error
        if expected_size_bytes is not None and (
            not _is_strict_integer(expected_size_bytes)
            or expected_size_bytes < 0
            or expected_size_bytes > self._maximum_size_bytes
        ):
            raise BlobStagingError("producer byte count is outside the supported range")
        self.flush()
        os.fsync(self.fileno())
        digest = self._sha256.hexdigest()
        if expected_sha256 is not None and expected_sha256 != digest:
            raise BlobStagingError("staged blob checksum does not match the producer")
        if expected_size_bytes is not None and expected_size_bytes != self._size_bytes:
            raise BlobStagingError("staged blob byte count does not match the producer")
        writable_status = os.fstat(self.fileno())
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        sealed_descriptor = -1
        try:
            sealed_descriptor = os.open(self._path, flags)
            sealed_status = os.fstat(sealed_descriptor)
        except OSError as error:
            failure = BlobStagingError("staged blob could not be sealed read-only")
            if sealed_descriptor >= 0:
                _close_with_note(sealed_descriptor, failure)
            raise failure from error
        if (
            sealed_status.st_dev != writable_status.st_dev
            or sealed_status.st_ino != writable_status.st_ino
        ):
            failure = BlobStagingError("staged blob identity changed before sealing")
            _close_with_note(sealed_descriptor, failure)
            raise failure
        lease = StagedBlobLease(sealed_descriptor)
        self._sealed_lease = lease
        self._sealed = True
        return StagedBlob(
            self._path,
            digest,
            self._size_bytes,
            media_type,
            lease,
        )

    @override
    def close(self) -> None:
        if self.closed:
            return
        cleanup_errors: list[BaseException] = []
        try:
            super().close()
        except BaseException as error:
            cleanup_errors.append(error)
        try:
            self._file.close()
        except BaseException as error:
            cleanup_errors.append(error)
        if self._sealed_lease is not None:
            try:
                self._sealed_lease.close()
            except BaseException as error:
                cleanup_errors.append(error)
        if cleanup_errors:
            _raise_cleanup_failures(
                cleanup_errors,
                "staged blob writer cleanup failed",
            )

    def _require_mutable(self) -> None:
        if self.closed:
            raise BlobStagingError("staged blob writer is closed")
        if self._sealed:
            raise BlobStagingError("staged blob writer is sealed")


@final
class BlobStagingArea:
    def __init__(
        self,
        directory: str | Path,
        *,
        maximum_size_bytes: int = MAX_BLOB_SIZE_BYTES,
    ) -> None:
        if (
            not _is_strict_integer(maximum_size_bytes)
            or maximum_size_bytes < 1
            or maximum_size_bytes > MAX_BLOB_SIZE_BYTES
        ):
            raise BlobStagingError(
                "staging byte ceiling is outside the supported range"
            )
        self._directory = Path(directory)
        self._maximum_size_bytes = maximum_size_bytes
        self._directory_identity = self._prepare_directory()

    @contextmanager
    def stage(self) -> Generator[StagedBlobWriter]:
        lock_descriptor = self._acquire_lock()
        writer: StagedBlobWriter | None = None
        active_error: BaseException | None = None
        try:
            self._cleanup_stale_files(lock_descriptor)
            writer = self._create_writer(lock_descriptor)
            try:
                yield writer
            except BaseException as error:
                active_error = error
                raise
            finally:
                cleanup_errors = self._cleanup(writer, lock_descriptor)
                if cleanup_errors:
                    if active_error is not None:
                        for cleanup_error in cleanup_errors:
                            active_error.add_note(
                                f"staging cleanup also failed: {cleanup_error!r}"
                            )
                    else:
                        _raise_cleanup_failures(
                            cleanup_errors,
                            "staged blob cleanup failed",
                        )
        except BaseException as error:
            if active_error is None:
                active_error = error
            raise
        finally:
            if writer is None:
                try:
                    os.close(lock_descriptor)
                except BaseException as cleanup_error:
                    if active_error is not None:
                        active_error.add_note(
                            f"staging lock cleanup also failed: {cleanup_error!r}"
                        )
                    else:
                        raise

    def _create_writer(self, directory_descriptor: int) -> StagedBlobWriter:
        descriptor = -1
        file_name: str | None = None
        try:
            for _attempt in range(_MAX_CREATE_ATTEMPTS):
                file_name = f"{_STAGING_PREFIX}{secrets.token_hex(16)}"
                try:
                    descriptor = os.open(
                        file_name,
                        os.O_RDWR
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=directory_descriptor,
                    )
                    break
                except FileExistsError:
                    continue
            if descriptor < 0 or file_name is None:
                raise BlobStagingError("staged blob name attempts were exhausted")
            file = io.FileIO(descriptor, mode="w+b", closefd=True)
            descriptor = -1
            return StagedBlobWriter(
                self._directory / file_name,
                file_name,
                file,
                self._maximum_size_bytes,
            )
        except BaseException as error:
            cleanup_errors: list[BaseException] = []
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except BaseException as close_error:
                    cleanup_errors.append(close_error)
            if file_name is not None:
                try:
                    os.unlink(file_name, dir_fd=directory_descriptor)
                except FileNotFoundError:
                    pass
                except BaseException as unlink_error:
                    cleanup_errors.append(unlink_error)
            for cleanup_failure in cleanup_errors:
                error.add_note(
                    f"staging creation cleanup also failed: {cleanup_failure!r}"
                )
            if isinstance(error, BlobStagingError):
                raise
            if not isinstance(error, Exception):
                raise
            raise BlobStagingError("staged blob file could not be created") from error

    def _acquire_lock(self) -> int:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            descriptor = os.open(self._directory, flags)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = os.fstat(descriptor)
            _validate_directory_status(status, "staging")
            if (status.st_dev, status.st_ino) != self._directory_identity:
                raise BlobStagingError("staging directory identity changed")
            return descriptor
        except BaseException as error:
            if descriptor >= 0:
                _close_with_note(descriptor, error)
            if isinstance(error, BlobStagingError):
                raise
            if not isinstance(error, Exception):
                raise
            raise BlobStagingError("staging area already has an active file") from error

    def _cleanup_stale_files(self, directory_descriptor: int) -> None:
        stale_names = _bounded_stale_names(directory_descriptor)
        for name in stale_names:
            status = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or status.st_nlink != 1
            ):
                raise BlobStagingError("staging contains an unsafe stale file")
            os.unlink(name, dir_fd=directory_descriptor)
        if stale_names:
            os.fsync(directory_descriptor)

    @staticmethod
    def _cleanup(
        writer: StagedBlobWriter,
        directory_descriptor: int,
    ) -> list[BaseException]:
        failures: list[BaseException] = []
        try:
            writer.close()
        except BaseException as error:
            failures.append(error)
        try:
            os.unlink(writer.file_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except BaseException as error:
            failures.append(error)
        try:
            os.close(directory_descriptor)
        except BaseException as error:
            failures.append(error)
        return failures

    def _prepare_directory(self) -> tuple[int, int]:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            status = self._directory.lstat()
        except OSError as error:
            raise BlobStagingError("staging directory is unavailable") from error
        _validate_directory_status(status, "staging")
        return status.st_dev, status.st_ino


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_directory_status(status: os.stat_result, label: str) -> None:
    if not stat.S_ISDIR(status.st_mode):
        raise BlobStagingError(f"{label} path must be a real directory")
    if status.st_uid != os.geteuid():
        raise BlobStagingError(f"{label} directory owner is not the watcher user")
    if stat.S_IMODE(status.st_mode) & 0o077:
        raise BlobStagingError(
            f"{label} directory must not grant group or other access"
        )


def _close_with_note(descriptor: int, primary_error: BaseException) -> None:
    try:
        os.close(descriptor)
    except BaseException as cleanup_error:
        primary_error.add_note(f"descriptor cleanup also failed: {cleanup_error!r}")


def _bounded_stale_names(directory_descriptor: int) -> list[str]:
    stale_names: list[str] = []
    try:
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                if not entry.name.startswith(_STAGING_PREFIX):
                    raise BlobStagingError(
                        "staging directory contains an unknown entry"
                    )
                stale_names.append(entry.name)
                if len(stale_names) > _MAX_STALE_FILES:
                    raise BlobStagingError("staging stale-file count exceeds its bound")
    except OSError as error:
        raise BlobStagingError("staging directory could not be scanned") from error
    return sorted(stale_names)


def _raise_cleanup_failures(
    cleanup_errors: list[BaseException],
    message: str,
) -> None:
    control_flow_error = next(
        (error for error in cleanup_errors if not isinstance(error, Exception)),
        None,
    )
    if control_flow_error is not None:
        for cleanup_error in cleanup_errors:
            if cleanup_error is not control_flow_error:
                control_flow_error.add_note(
                    f"additional cleanup failure: {cleanup_error!r}"
                )
        raise control_flow_error
    failure = BlobStagingError(message)
    for cleanup_error in cleanup_errors[1:]:
        failure.add_note(f"additional cleanup failure: {cleanup_error!r}")
    raise failure from cleanup_errors[0]
