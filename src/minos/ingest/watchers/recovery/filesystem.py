import os
import re
import secrets
import stat
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, final

from minos.ingest.watchers.recovery.image import validate_recovery_image
from minos.ingest.watchers.recovery.keys import parse_recovery_key, recovery_key
from minos.ingest.watchers.recovery.models import (
    MAX_RECOVERY_IMAGE_SIZE_BYTES,
    ListedRecoveryImage,
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryListingBoundError,
    RecoveryStorageError,
    StagedRecoveryImage,
)

_TEMPORARY_PREFIX = ".minos-recovery-publish-"
_TEMPORARY_NAME = re.compile(
    rf"{re.escape(_TEMPORARY_PREFIX)}(?P<sha256>[0-9a-f]{{64}})-[0-9a-f]{{32}}\.sqlite3"
)
_MAX_CREATE_ATTEMPTS = 10
_MAX_STALE_FILES = 16
_MAX_LISTING_KEY_NODES = 3


@final
class FilesystemRecoveryStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._recovery_root = self._root / "control-recovery"
        self._temporary_root = self._root / "recovery-temporary"
        _prepare_private_directory(self._root)
        _prepare_private_directory(self._recovery_root)
        _prepare_private_directory(self._temporary_root)
        self._cleanup_temporary_files()

    def publish(self, image: StagedRecoveryImage) -> PublishedRecoveryImage:
        key = recovery_key(image.metadata, image.sha256)
        destination = self._root / key
        self._prepare_destination_directory(destination.parent)
        self._cleanup_temporary_files()
        temporary = self._reserve_temporary_path(image.sha256)
        destination_may_exist = False
        destination_durable = False
        try:
            with image.open() as source:
                _copy_image(source, temporary, image.size_bytes)
            _ = validate_recovery_image(
                temporary,
                expected_sha256=image.sha256,
                expected_size_bytes=image.size_bytes,
                validation_time_ms=image.metadata.created_at_ms,
            )
            destination_may_exist = True
            try:
                try:
                    os.link(temporary, destination, follow_symlinks=False)
                except FileExistsError:
                    _ = validate_recovery_image(
                        destination,
                        expected_key=key,
                        expected_sha256=image.sha256,
                        expected_size_bytes=image.size_bytes,
                        validation_time_ms=image.metadata.created_at_ms,
                    )
            finally:
                _fsync_directory(destination.parent)
            destination_durable = True
            self._remove_temporary_file(temporary)
        except BaseException as error:
            if not destination_may_exist or destination_durable:
                try:
                    self._remove_temporary_file(temporary)
                except BaseException as cleanup_error:
                    error.add_note(
                        f"filesystem recovery cleanup also failed: {cleanup_error!r}"
                    )
            raise
        metadata = validate_recovery_image(
            destination,
            expected_key=key,
            expected_sha256=image.sha256,
            expected_size_bytes=image.size_bytes,
            validation_time_ms=image.metadata.created_at_ms,
        )
        if metadata != image.metadata:
            raise RecoveryStorageError(
                "filesystem recovery metadata differs after publication"
            )
        return PublishedRecoveryImage(
            key,
            image.sha256,
            image.size_bytes,
            image.metadata,
        )

    def list_images(
        self,
        recovery_class: RecoveryClass,
        *,
        maximum_objects: int,
        page_size: int = 1000,
    ) -> tuple[ListedRecoveryImage, ...]:
        if maximum_objects < 1 or maximum_objects > 2048:
            raise RecoveryStorageError("recovery listing bound is unsupported")
        if page_size < 1 or page_size > 1000:
            raise RecoveryStorageError("recovery listing page size is unsupported")
        self._cleanup_temporary_files()
        class_root = self._recovery_root / recovery_class
        if not class_root.exists():
            return ()
        _validate_directory(class_root.lstat())
        results: list[ListedRecoveryImage] = []
        # One object uses at most period/revision/file nodes. page_size is the
        # bounded allowance for empty directories left by interrupted publishes.
        visit_limit = 1 + _MAX_LISTING_KEY_NODES * maximum_objects + page_size
        for path, status in _bounded_image_files(class_root, visit_limit):
            key = path.relative_to(self._root).as_posix()
            parsed = parse_recovery_key(key)
            if parsed.recovery_class != recovery_class:
                raise RecoveryStorageError(
                    "filesystem recovery key has an unexpected class"
                )
            results.append(
                ListedRecoveryImage(
                    key,
                    status.st_size,
                    status.st_mtime_ns // 1_000_000,
                )
            )
            if len(results) > maximum_objects:
                raise RecoveryListingBoundError(
                    "filesystem recovery listing exceeds its object bound"
                )
        return tuple(sorted(results, key=lambda item: item.key))

    def _reserve_temporary_path(self, sha256: str) -> Path:
        for _attempt in range(_MAX_CREATE_ATTEMPTS):
            path = self._temporary_root / (
                f"{_TEMPORARY_PREFIX}{sha256}-{secrets.token_hex(16)}.sqlite3"
            )
            try:
                descriptor = os.open(
                    path,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                )
            except FileExistsError:
                continue
            except OSError as error:
                raise RecoveryStorageError(
                    "filesystem recovery temporary file is unavailable"
                ) from error
            os.close(descriptor)
            return path
        raise RecoveryStorageError(
            "filesystem recovery temporary name attempts were exhausted"
        )

    def _prepare_destination_directory(self, destination: Path) -> None:
        relative = destination.relative_to(self._root)
        current = self._root
        for part in relative.parts:
            current /= part
            created = False
            try:
                current.mkdir(mode=0o700)
                created = True
            except FileExistsError:
                pass
            except OSError as error:
                raise RecoveryStorageError(
                    "filesystem recovery directory is unavailable"
                ) from error
            _validate_directory(current.lstat())
            if created:
                _fsync_directory(current.parent)

    def _cleanup_temporary_files(self) -> None:
        try:
            with _open_directory_descriptor(self._root) as root_descriptor:
                with _open_relative_directory_descriptor(
                    root_descriptor,
                    ("recovery-temporary",),
                ) as temporary_directory_descriptor:
                    for name in _bounded_temporary_names(
                        temporary_directory_descriptor
                    ):
                        match = _TEMPORARY_NAME.fullmatch(name)
                        if match is None:
                            raise RecoveryStorageError(
                                "filesystem recovery temporary directory has an unknown entry"
                            )
                        with _open_file_descriptor(
                            temporary_directory_descriptor,
                            name,
                        ) as (temporary_descriptor, temporary_status):
                            _validate_temporary_file(temporary_status)
                            if temporary_status.st_nlink == 2:
                                self._complete_linked_publication(
                                    root_descriptor,
                                    temporary_directory_descriptor,
                                    name,
                                    temporary_descriptor,
                                    temporary_status,
                                    match.group("sha256"),
                                )
                                continue
                            _unlink_descriptor_entry(
                                temporary_directory_descriptor,
                                name,
                                temporary_status,
                            )
                            os.fsync(temporary_directory_descriptor)
        except RecoveryStorageError:
            raise
        except OSError as error:
            raise RecoveryStorageError(
                "filesystem recovery temporary directory is unavailable"
            ) from error

    def _complete_linked_publication(
        self,
        root_descriptor: int,
        temporary_directory_descriptor: int,
        temporary_name: str,
        temporary_descriptor: int,
        temporary_status: os.stat_result,
        sha256: str,
    ) -> None:
        metadata = validate_recovery_image(
            self._temporary_root / temporary_name,
            expected_sha256=sha256,
            expected_size_bytes=temporary_status.st_size,
            validation_time_ms=temporary_status.st_mtime_ns // 1_000_000,
            _expected_link_count=2,
            _descriptor=temporary_descriptor,
        )
        key = recovery_key(metadata, sha256)
        key_parts = Path(key).parts
        with _open_relative_directory_descriptor(
            root_descriptor,
            key_parts[:-1],
        ) as destination_directory_descriptor:
            with _open_file_descriptor(
                destination_directory_descriptor,
                key_parts[-1],
            ) as (destination_descriptor, destination_status):
                _validate_linked_file(destination_status)
                if not _same_file(temporary_status, destination_status):
                    raise RecoveryStorageError(
                        "filesystem recovery linked publication identity differs"
                    )
                _ = validate_recovery_image(
                    self._root / key,
                    expected_key=key,
                    expected_sha256=sha256,
                    expected_size_bytes=temporary_status.st_size,
                    validation_time_ms=metadata.created_at_ms,
                    _expected_link_count=2,
                    _descriptor=destination_descriptor,
                )
                _require_entry_identity(
                    destination_directory_descriptor,
                    key_parts[-1],
                    destination_status,
                )
                os.fsync(destination_directory_descriptor)
                _unlink_descriptor_entry(
                    temporary_directory_descriptor,
                    temporary_name,
                    temporary_status,
                )
                os.fsync(temporary_directory_descriptor)

    def _remove_temporary_file(self, temporary: Path) -> None:
        try:
            temporary.unlink(missing_ok=True)
        except OSError as error:
            raise RecoveryStorageError(
                "filesystem recovery temporary file cannot be removed"
            ) from error
        _fsync_directory(self._temporary_root)


def _bounded_temporary_names(directory_descriptor: int) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(directory_descriptor) as entries:
        for entry in entries:
            names.append(entry.name)
            if len(names) > _MAX_STALE_FILES:
                raise RecoveryStorageError(
                    "filesystem recovery stale-file count exceeds its bound"
                )
    return tuple(sorted(names))


def _bounded_image_files(
    class_root: Path,
    visit_limit: int,
) -> Generator[tuple[Path, os.stat_result]]:
    pending = [class_root]
    visited = 1
    while pending:
        directory = pending.pop()
        directories: list[Path] = []
        files: list[tuple[Path, os.stat_result]] = []
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited += 1
                    if visited > visit_limit:
                        raise RecoveryListingBoundError(
                            "filesystem recovery listing exceeds its traversal bound"
                        )
                    path = directory / entry.name
                    status = entry.stat(follow_symlinks=False)
                    if stat.S_ISDIR(status.st_mode):
                        _validate_directory(status)
                        directories.append(path)
                    else:
                        _validate_file(status)
                        files.append((path, status))
        except RecoveryListingBoundError, RecoveryStorageError:
            raise
        except OSError as error:
            raise RecoveryStorageError(
                "filesystem recovery listing is unavailable"
            ) from error
        pending.extend(reversed(sorted(directories)))
        yield from sorted(files, key=lambda item: item[0])


@contextmanager
def _open_directory_descriptor(directory: Path) -> Generator[int]:
    descriptor = -1
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
        _validate_directory(os.fstat(descriptor))
        yield descriptor
    except RecoveryStorageError as error:
        primary_error = error
        raise
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception):
            raise
        raise RecoveryStorageError(
            "filesystem recovery directory descriptor is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            _close_descriptors((descriptor,), primary_error)


@contextmanager
def _open_relative_directory_descriptor(
    root_descriptor: int,
    parts: tuple[str, ...],
) -> Generator[int]:
    if len(parts) > 8 or any(
        not part or part in (".", "..") or "/" in part for part in parts
    ):
        raise RecoveryStorageError("filesystem recovery directory path is invalid")
    descriptors: list[int] = []
    current_descriptor = root_descriptor
    primary_error: BaseException | None = None
    try:
        for part in parts:
            current_descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=current_descriptor,
            )
            descriptors.append(current_descriptor)
            _validate_directory(os.fstat(current_descriptor))
        yield current_descriptor
    except RecoveryStorageError as error:
        primary_error = error
        raise
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception):
            raise
        raise RecoveryStorageError(
            "filesystem recovery relative directory is unavailable"
        ) from error
    finally:
        _close_descriptors(tuple(reversed(descriptors)), primary_error)


@contextmanager
def _open_file_descriptor(
    directory_descriptor: int,
    name: str,
) -> Generator[tuple[int, os.stat_result]]:
    if not name or name in (".", "..") or "/" in name:
        raise RecoveryStorageError("filesystem recovery file name is invalid")
    descriptor = -1
    primary_error: BaseException | None = None
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_descriptor,
        )
        yield descriptor, os.fstat(descriptor)
    except RecoveryStorageError as error:
        primary_error = error
        raise
    except BaseException as error:
        primary_error = error
        if not isinstance(error, Exception):
            raise
        raise RecoveryStorageError(
            "filesystem recovery file descriptor is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            _close_descriptors((descriptor,), primary_error)


def _unlink_descriptor_entry(
    directory_descriptor: int,
    name: str,
    expected_status: os.stat_result,
) -> None:
    _require_entry_identity(directory_descriptor, name, expected_status)
    os.unlink(name, dir_fd=directory_descriptor)


def _require_entry_identity(
    directory_descriptor: int,
    name: str,
    expected_status: os.stat_result,
) -> None:
    status = os.stat(name, dir_fd=directory_descriptor, follow_symlinks=False)
    if (
        not _same_file(status, expected_status)
        or status.st_nlink != expected_status.st_nlink
    ):
        raise RecoveryStorageError(
            "filesystem recovery directory entry identity changed"
        )


def _same_file(first: os.stat_result, second: os.stat_result) -> bool:
    return (
        first.st_dev == second.st_dev
        and first.st_ino == second.st_ino
        and first.st_size == second.st_size
    )


def _close_descriptors(
    descriptors: tuple[int, ...],
    primary_error: BaseException | None,
) -> None:
    cleanup_errors: list[BaseException] = []
    for descriptor in descriptors:
        try:
            os.close(descriptor)
        except BaseException as captured_error:
            cleanup_errors.append(captured_error)
    if primary_error is not None:
        for descriptor_error in cleanup_errors:
            primary_error.add_note(
                f"filesystem recovery descriptor cleanup also failed: {descriptor_error!r}"
            )
        return
    if cleanup_errors:
        descriptor_error = cleanup_errors[0]
        for additional_error in cleanup_errors[1:]:
            descriptor_error.add_note(
                f"additional filesystem recovery descriptor cleanup failed: {additional_error!r}"
            )
        if isinstance(descriptor_error, Exception):
            raise RecoveryStorageError(
                "filesystem recovery descriptor cleanup failed"
            ) from descriptor_error
        raise descriptor_error


def _copy_image(source: BinaryIO, destination: Path, size_bytes: int) -> None:
    if size_bytes < 1 or size_bytes > MAX_RECOVERY_IMAGE_SIZE_BYTES:
        raise RecoveryStorageError("filesystem recovery image size is unsupported")
    try:
        with destination.open("r+b", buffering=0) as target:
            remaining = size_bytes
            while remaining > 0:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise RecoveryStorageError(
                        "staged recovery image shrank during publication"
                    )
                if target.write(chunk) != len(chunk):
                    raise RecoveryStorageError(
                        "filesystem recovery write was incomplete"
                    )
                remaining -= len(chunk)
            if source.read(1):
                raise RecoveryStorageError(
                    "staged recovery image grew during publication"
                )
            target.flush()
            os.fsync(target.fileno())
    except RecoveryStorageError:
        raise
    except OSError as error:
        raise RecoveryStorageError("filesystem recovery publication failed") from error


def _prepare_private_directory(directory: Path) -> None:
    missing: list[Path] = []
    current = directory
    try:
        while True:
            try:
                status = current.lstat()
            except FileNotFoundError:
                missing.append(current)
                parent = current.parent
                if parent == current:
                    raise RecoveryStorageError(
                        "filesystem recovery directory is unavailable"
                    ) from None
                current = parent
                continue
            if current == directory:
                _validate_directory(status)
            break
        for current in reversed(missing):
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            _validate_directory(current.lstat())
            _fsync_directory(current.parent)
    except OSError as error:
        raise RecoveryStorageError(
            "filesystem recovery directory is unavailable"
        ) from error


def _validate_directory(status: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) & 0o077
    ):
        raise RecoveryStorageError("filesystem recovery directory is unsafe")


def _validate_file(status: os.stat_result) -> None:
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_nlink != 1
        or status.st_size < 1
        or status.st_size > MAX_RECOVERY_IMAGE_SIZE_BYTES
    ):
        raise RecoveryStorageError("filesystem recovery image is unsafe")


def _validate_temporary_file(status: os.stat_result) -> None:
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_nlink not in (1, 2)
        or status.st_size > MAX_RECOVERY_IMAGE_SIZE_BYTES
    ):
        raise RecoveryStorageError("filesystem recovery temporary file is unsafe")


def _validate_linked_file(status: os.stat_result) -> None:
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) != 0o600
        or status.st_nlink != 2
        or status.st_size < 1
        or status.st_size > MAX_RECOVERY_IMAGE_SIZE_BYTES
    ):
        raise RecoveryStorageError("filesystem recovery linked image is unsafe")


def _fsync_directory(directory: Path) -> None:
    descriptor = -1
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        os.fsync(descriptor)
    except OSError as error:
        raise RecoveryStorageError(
            "filesystem recovery directory synchronization failed"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
