import fcntl
import os
import secrets
import stat
from pathlib import Path
from typing import BinaryIO, final

from minos.ingest.watchers.archive.files import (
    open_verified_staged_blob,
    verify_archive_file,
)
from minos.ingest.watchers.archive.models import (
    BlobArchiveStorageError,
    BlobArchiveVerificationError,
    StagedBlob,
    blob_key,
)
from minos.ingest.watchers.control import BlobReceipt

_ARCHIVE_TEMP_PREFIX = ".minos-archive-"
_MAX_CREATE_ATTEMPTS = 10
_MAX_STALE_FILES = 16


@final
class FilesystemBlobArchive:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._blob_directory = self._root / "blobs" / "sha256"
        self._temporary_directory = self._root / "temporary"
        _ = self._prepare_directory(self._root)
        _ = self._prepare_directory(self._root / "blobs")
        self._blob_directory_identity = self._prepare_directory(self._blob_directory)
        self._temporary_directory_identity = self._prepare_directory(
            self._temporary_directory
        )

    def put(self, blob: StagedBlob) -> BlobReceipt:
        key = blob_key(blob.sha256)
        temporary_directory_descriptor = -1
        blob_directory_descriptor = -1
        final_descriptor = -1
        primary_error: BaseException | None = None
        try:
            temporary_directory_descriptor = self._open_directory(
                self._temporary_directory,
                self._temporary_directory_identity,
                lock=True,
            )
            blob_directory_descriptor = self._open_directory(
                self._blob_directory,
                self._blob_directory_identity,
                lock=False,
            )
            self._cleanup_stale_files(temporary_directory_descriptor)
            with open_verified_staged_blob(blob) as source:
                self._publish(
                    source,
                    blob,
                    temporary_directory_descriptor,
                    blob_directory_descriptor,
                )
            final_descriptor = self._open_final(
                blob.sha256,
                blob_directory_descriptor,
            )
            verify_archive_file(
                final_descriptor,
                expected_sha256=blob.sha256,
                expected_size_bytes=blob.size_bytes,
            )
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_errors = _close_descriptors(
                final_descriptor,
                blob_directory_descriptor,
                temporary_directory_descriptor,
            )
            _handle_cleanup_failures(
                primary_error,
                cleanup_errors,
                "filesystem archive descriptor cleanup failed",
            )
        return BlobReceipt(
            sha256=blob.sha256,
            size_bytes=blob.size_bytes,
            media_type=blob.media_type,
            archive_key=key,
            verified_sha256=blob.sha256,
            verified_size_bytes=blob.size_bytes,
        )

    def _publish(
        self,
        source: BinaryIO,
        blob: StagedBlob,
        temporary_directory_descriptor: int,
        blob_directory_descriptor: int,
    ) -> None:
        temporary_name, temporary_descriptor = self._create_temporary_file(
            temporary_directory_descriptor
        )
        primary_error: BaseException | None = None
        try:
            with os.fdopen(temporary_descriptor, "wb", closefd=False) as destination:
                _copy_exact(source, destination, blob.size_bytes)
                destination.flush()
            os.fsync(temporary_descriptor)
            _ = os.lseek(temporary_descriptor, 0, os.SEEK_SET)
            verify_archive_file(
                temporary_descriptor,
                expected_sha256=blob.sha256,
                expected_size_bytes=blob.size_bytes,
            )
            try:
                os.link(
                    temporary_name,
                    blob.sha256,
                    src_dir_fd=temporary_directory_descriptor,
                    dst_dir_fd=blob_directory_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                pass
            os.fsync(blob_directory_descriptor)
        except OSError as error:
            failure = BlobArchiveStorageError("filesystem blob publication failed")
            primary_error = failure
            raise failure from error
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_errors = _close_descriptors(temporary_descriptor)
            try:
                os.unlink(temporary_name, dir_fd=temporary_directory_descriptor)
            except FileNotFoundError:
                pass
            except BaseException as error:
                cleanup_errors.append(error)
            try:
                os.fsync(temporary_directory_descriptor)
            except BaseException as error:
                cleanup_errors.append(error)
            _handle_cleanup_failures(
                primary_error,
                cleanup_errors,
                "filesystem archive temporary cleanup failed",
            )

    @staticmethod
    def _open_final(sha256: str, directory_descriptor: int) -> int:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            return os.open(sha256, flags, dir_fd=directory_descriptor)
        except OSError as error:
            raise BlobArchiveStorageError(
                "filesystem archive blob could not be opened"
            ) from error

    @staticmethod
    def _create_temporary_file(directory_descriptor: int) -> tuple[str, int]:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        for _attempt in range(_MAX_CREATE_ATTEMPTS):
            name = f"{_ARCHIVE_TEMP_PREFIX}{secrets.token_hex(16)}"
            try:
                descriptor = os.open(
                    name,
                    flags,
                    0o600,
                    dir_fd=directory_descriptor,
                )
                return name, descriptor
            except FileExistsError:
                continue
            except OSError as error:
                raise BlobArchiveStorageError(
                    "filesystem archive temporary file could not be created"
                ) from error
        raise BlobArchiveStorageError(
            "filesystem archive temporary name attempts were exhausted"
        )

    @staticmethod
    def _cleanup_stale_files(directory_descriptor: int) -> None:
        stale_names = _bounded_stale_names(directory_descriptor)
        for name in stale_names:
            status = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or status.st_nlink != 1
            ):
                raise BlobArchiveStorageError(
                    "filesystem archive contains an unsafe stale file"
                )
            os.unlink(name, dir_fd=directory_descriptor)
        if stale_names:
            os.fsync(directory_descriptor)

    @staticmethod
    def _open_directory(
        directory: Path,
        expected_identity: tuple[int, int],
        *,
        lock: bool,
    ) -> int:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            descriptor = os.open(directory, flags)
            if lock:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = os.fstat(descriptor)
            _validate_directory_status(status)
            if (status.st_dev, status.st_ino) != expected_identity:
                raise BlobArchiveStorageError(
                    "filesystem archive directory identity changed"
                )
            return descriptor
        except BaseException as error:
            cleanup_errors = _close_descriptors(descriptor)
            for cleanup_error in cleanup_errors:
                error.add_note(f"directory open cleanup also failed: {cleanup_error!r}")
            if isinstance(error, BlobArchiveStorageError):
                raise
            if not isinstance(error, Exception):
                raise
            raise BlobArchiveStorageError(
                "filesystem archive directory could not be opened or locked"
            ) from error

    @staticmethod
    def _prepare_directory(directory: Path) -> tuple[int, int]:
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as error:
            raise BlobArchiveStorageError(
                "filesystem archive directory is unavailable"
            ) from error
        try:
            status = directory.lstat()
        except OSError as error:
            raise BlobArchiveStorageError(
                "filesystem archive directory is unavailable"
            ) from error
        _validate_directory_status(status)
        _fsync_directory(directory.parent)
        return status.st_dev, status.st_ino


def _validate_directory_status(status: os.stat_result) -> None:
    if not stat.S_ISDIR(status.st_mode):
        raise BlobArchiveStorageError(
            "filesystem archive path must be a real directory"
        )
    if status.st_uid != os.geteuid():
        raise BlobArchiveStorageError(
            "filesystem archive directory owner is not the watcher user"
        )
    if stat.S_IMODE(status.st_mode) & 0o077:
        raise BlobArchiveStorageError(
            "filesystem archive directory must not grant group or other access"
        )


def _copy_exact(source: BinaryIO, destination: BinaryIO, size_bytes: int) -> None:
    remaining_bytes = size_bytes
    while remaining_bytes > 0:
        chunk = source.read(min(1024 * 1024, remaining_bytes))
        if not chunk:
            raise BlobArchiveVerificationError(
                "staged blob shrank during filesystem publication"
            )
        written = destination.write(chunk)
        if written != len(chunk):
            raise BlobArchiveStorageError("filesystem archive write was incomplete")
        remaining_bytes -= written
    if source.read(1):
        raise BlobArchiveVerificationError(
            "staged blob grew during filesystem publication"
        )


def _bounded_stale_names(directory_descriptor: int) -> list[str]:
    stale_names: list[str] = []
    try:
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                if not entry.name.startswith(_ARCHIVE_TEMP_PREFIX):
                    raise BlobArchiveStorageError(
                        "filesystem archive temporary directory contains an unknown entry"
                    )
                stale_names.append(entry.name)
                if len(stale_names) > _MAX_STALE_FILES:
                    raise BlobArchiveStorageError(
                        "filesystem archive stale-file count exceeds its bound"
                    )
    except OSError as error:
        raise BlobArchiveStorageError(
            "filesystem archive temporary directory could not be scanned"
        ) from error
    return sorted(stale_names)


def _fsync_directory(directory: Path) -> None:
    descriptor = -1
    primary_error: BaseException | None = None
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except BaseException as error:
        if isinstance(error, Exception):
            failure = BlobArchiveStorageError(
                "filesystem archive parent directory could not be synchronized"
            )
            primary_error = failure
            raise failure from error
        primary_error = error
        raise
    finally:
        _handle_cleanup_failures(
            primary_error,
            _close_descriptors(descriptor),
            "filesystem archive parent descriptor cleanup failed",
        )


def _close_descriptors(*descriptors: int) -> list[BaseException]:
    failures: list[BaseException] = []
    for descriptor in descriptors:
        if descriptor < 0:
            continue
        try:
            os.close(descriptor)
        except BaseException as error:
            failures.append(error)
    return failures


def _handle_cleanup_failures(
    primary_error: BaseException | None,
    cleanup_errors: list[BaseException],
    message: str,
) -> None:
    if not cleanup_errors:
        return
    if primary_error is not None:
        for cleanup_error in cleanup_errors:
            primary_error.add_note(f"cleanup also failed: {cleanup_error!r}")
        return
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
    failure = BlobArchiveStorageError(message)
    for cleanup_error in cleanup_errors[1:]:
        failure.add_note(f"additional cleanup failure: {cleanup_error!r}")
    raise failure from cleanup_errors[0]
