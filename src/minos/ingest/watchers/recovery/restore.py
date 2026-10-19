import os
import secrets
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeIs

from minos.ingest.watchers.recovery.image import (
    prepare_control_database_restore,
    validate_prepared_control_database,
    validate_recovery_image,
)
from minos.ingest.watchers.recovery.inventory import (
    RecoveryInventory,
    TrustedObjectIdentity,
)
from minos.ingest.watchers.recovery.models import RecoveryImageMetadata

_RESTORE_PREFIX = ".minos-restore-"
_INVESTIGATION_MARKER = ".pre-restore-"
_MAX_NAME_ATTEMPTS = 10


class RecoveryRestoreError(RuntimeError):
    """A local recovery installation could not be proven safe."""


class TrustedRecoveryDownloader(Protocol):
    def download_trusted(
        self,
        identity: TrustedObjectIdentity,
        destination_descriptor: int,
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class InstalledRecovery:
    metadata: RecoveryImageMetadata
    investigation_directory: Path | None


def install_trusted_recovery(
    downloader: TrustedRecoveryDownloader,
    inventory: RecoveryInventory,
    database_path: str | Path,
    *,
    watcher_is_stopped: Callable[[], bool],
    expected_uid: int | None = None,
    expected_gid: int | None = None,
) -> InstalledRecovery:
    _require_watcher_stopped(watcher_is_stopped)
    uid = os.geteuid() if expected_uid is None else expected_uid
    gid = os.getegid() if expected_gid is None else expected_gid
    _validate_expected_identity(uid, gid)
    path = Path(database_path)
    _validate_database_name(path.name)
    parent_descriptor = _open_parent(path.parent, uid, gid)
    staging_name: str | None = None
    staging_descriptor = -1
    investigation_name: str | None = None
    existing_names: tuple[str, ...] = ()
    mutation_started = False
    staged_identity: tuple[int, int] | None = None
    try:
        existing_names = _validated_existing_names(
            parent_descriptor, path.name, uid, gid
        )
        staging_name, staging_descriptor = _reserve_staging(parent_descriptor)
        identity = _trusted_identity(inventory)
        _download(downloader, identity, staging_descriptor)
        metadata = _validate_downloaded(
            _descriptor_path(parent_descriptor, staging_name),
            inventory,
        )
        try:
            prepare_control_database_restore(
                _descriptor_path(parent_descriptor, staging_name),
                expected_state_revision=metadata.state_revision,
            )
            os.fsync(staging_descriptor)
        except Exception:
            raise RecoveryRestoreError(
                "downloaded recovery image preparation failed"
            ) from None
        _require_watcher_stopped(watcher_is_stopped)
        if existing_names:
            investigation_name = _preserve_existing(
                parent_descriptor,
                path.name,
                existing_names,
            )
        _require_watcher_stopped(watcher_is_stopped)
        staged_status = os.fstat(staging_descriptor)
        staged_identity = (staged_status.st_dev, staged_status.st_ino)
        mutation_started = True
        _remove_sidecars(parent_descriptor, path.name, existing_names)
        os.replace(
            staging_name,
            path.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        staging_name = None
        os.fsync(parent_descriptor)
        _validate_installed(
            parent_descriptor,
            path.name,
            metadata.state_revision,
            uid,
            gid,
        )
        investigation = (
            path.parent / investigation_name if investigation_name is not None else None
        )
        return InstalledRecovery(metadata, investigation)
    except BaseException as error:
        rollback_failed = False
        if mutation_started:
            try:
                _rollback_installation(
                    parent_descriptor,
                    path.name,
                    existing_names,
                    investigation_name,
                    staged_identity=staged_identity,
                )
                investigation_name = None
            except OSError:
                rollback_failed = True
        if rollback_failed:
            raise RecoveryRestoreError(
                "local recovery installation and rollback failed"
            ) from None
        if isinstance(error, RecoveryRestoreError):
            raise
        if isinstance(error, OSError):
            raise RecoveryRestoreError("local recovery installation failed") from None
        raise
    finally:
        if staging_descriptor >= 0:
            _close_quietly(staging_descriptor)
        if staging_name is not None:
            _unlink_quietly(parent_descriptor, staging_name)
        if investigation_name is not None and not mutation_started:
            _remove_investigation_quietly(
                parent_descriptor,
                investigation_name,
                path.name,
            )
        _close_quietly(parent_descriptor)


def _trusted_identity(inventory: RecoveryInventory) -> TrustedObjectIdentity:
    return TrustedObjectIdentity(
        inventory.trusted_object_key,
        inventory.trusted_object_checksum_sha256,
        inventory.trusted_object_size_bytes,
        inventory.trusted_object_last_modified_ms,
    )


def _download(
    downloader: TrustedRecoveryDownloader,
    identity: TrustedObjectIdentity,
    descriptor: int,
) -> None:
    try:
        downloader.download_trusted(identity, descriptor)
        os.fsync(descriptor)
    except Exception:
        raise RecoveryRestoreError("trusted recovery download failed") from None


def _validate_downloaded(
    path: Path,
    inventory: RecoveryInventory,
) -> RecoveryImageMetadata:
    try:
        metadata = validate_recovery_image(
            path,
            expected_sha256=inventory.trusted_object_checksum_sha256,
            expected_size_bytes=inventory.trusted_object_size_bytes,
        )
    except Exception:
        raise RecoveryRestoreError("downloaded recovery image is invalid") from None
    if metadata.state_revision != inventory.trusted_object_revision:
        raise RecoveryRestoreError("downloaded recovery revision differs")
    return metadata


def _validate_installed(
    parent_descriptor: int,
    database_name: str,
    expected_state_revision: int,
    expected_uid: int,
    expected_gid: int,
) -> None:
    try:
        status = os.stat(
            database_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
    except OSError:
        raise RecoveryRestoreError("installed recovery image is unavailable") from None
    _validate_regular_file(status, expected_uid, expected_gid)
    try:
        validate_prepared_control_database(
            _descriptor_path(parent_descriptor, database_name),
            expected_state_revision=expected_state_revision,
        )
    except Exception:
        raise RecoveryRestoreError("installed recovery database is invalid") from None


def _open_parent(path: Path, expected_uid: int, expected_gid: int) -> int:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        status = os.fstat(descriptor)
    except OSError:
        if descriptor >= 0:
            _close_quietly(descriptor)
        raise RecoveryRestoreError(
            "recovery database directory is unavailable"
        ) from None
    if not stat.S_ISDIR(status.st_mode):
        _close_quietly(descriptor)
        raise RecoveryRestoreError("recovery database parent is not a directory")
    if status.st_uid != expected_uid or status.st_gid != expected_gid:
        _close_quietly(descriptor)
        raise RecoveryRestoreError("recovery database directory ownership is unsafe")
    if stat.S_IMODE(status.st_mode) & 0o077:
        _close_quietly(descriptor)
        raise RecoveryRestoreError("recovery database directory mode is unsafe")
    return descriptor


def _validated_existing_names(
    parent_descriptor: int,
    database_name: str,
    expected_uid: int,
    expected_gid: int,
) -> tuple[str, ...]:
    names: list[str] = []
    candidates = (database_name, f"{database_name}-wal", f"{database_name}-shm")
    for name in candidates:
        try:
            status = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            raise RecoveryRestoreError(
                "existing recovery artifact is unavailable"
            ) from None
        _validate_regular_file(status, expected_uid, expected_gid)
        names.append(name)
    if database_name not in names and names:
        raise RecoveryRestoreError("database sidecar exists without its database")
    return tuple(names)


def _validate_regular_file(
    status: os.stat_result,
    expected_uid: int,
    expected_gid: int,
) -> None:
    if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
        raise RecoveryRestoreError("recovery database artifact type is unsafe")
    if status.st_uid != expected_uid or status.st_gid != expected_gid:
        raise RecoveryRestoreError("recovery database artifact ownership is unsafe")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise RecoveryRestoreError("recovery database artifact mode is unsafe")


def _reserve_staging(parent_descriptor: int) -> tuple[str, int]:
    for _attempt in range(_MAX_NAME_ATTEMPTS):
        name = f"{_RESTORE_PREFIX}{secrets.token_hex(16)}.sqlite3"
        try:
            descriptor = os.open(
                name,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent_descriptor,
            )
            return name, descriptor
        except FileExistsError:
            continue
        except OSError:
            raise RecoveryRestoreError("restore staging file creation failed") from None
    raise RecoveryRestoreError("restore staging name attempts were exhausted")


def _preserve_existing(
    parent_descriptor: int,
    database_name: str,
    existing_names: tuple[str, ...],
) -> str:
    investigation_name = _reserve_investigation_directory(
        parent_descriptor, database_name
    )
    try:
        for name in existing_names:
            os.link(
                name,
                f"{investigation_name}/{name}",
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        investigation_descriptor = os.open(
            investigation_name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        try:
            os.fsync(investigation_descriptor)
        finally:
            os.close(investigation_descriptor)
        os.fsync(parent_descriptor)
    except OSError:
        _remove_investigation_quietly(
            parent_descriptor,
            investigation_name,
            database_name,
        )
        raise RecoveryRestoreError("existing database preservation failed") from None
    return investigation_name


def _reserve_investigation_directory(
    parent_descriptor: int,
    database_name: str,
) -> str:
    for _attempt in range(_MAX_NAME_ATTEMPTS):
        name = f".{database_name}{_INVESTIGATION_MARKER}{secrets.token_hex(16)}"
        try:
            os.mkdir(name, 0o700, dir_fd=parent_descriptor)
            return name
        except FileExistsError:
            continue
        except OSError:
            raise RecoveryRestoreError(
                "investigation directory creation failed"
            ) from None
    raise RecoveryRestoreError("investigation directory attempts were exhausted")


def _remove_sidecars(
    parent_descriptor: int,
    database_name: str,
    existing_names: tuple[str, ...],
) -> None:
    for suffix in ("-wal", "-shm"):
        name = f"{database_name}{suffix}"
        if name in existing_names:
            os.unlink(name, dir_fd=parent_descriptor)


def _rollback_installation(
    parent_descriptor: int,
    database_name: str,
    existing_names: tuple[str, ...],
    investigation_name: str | None,
    *,
    staged_identity: tuple[int, int] | None,
) -> None:
    if staged_identity is None:
        raise OSError("restore staging identity is unavailable")
    if investigation_name is None:
        installed_status = _stat_optional(parent_descriptor, database_name)
        if (
            installed_status is not None
            and _identity(installed_status) == staged_identity
        ):
            os.unlink(database_name, dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        return
    for name in existing_names:
        preserved_name = f"{investigation_name}/{name}"
        installed_status = _stat_optional(parent_descriptor, name)
        preserved_status = os.stat(
            preserved_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if installed_status is None:
            os.replace(
                preserved_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        elif _identity(installed_status) == _identity(preserved_status):
            os.unlink(preserved_name, dir_fd=parent_descriptor)
        elif name == database_name and _identity(installed_status) == staged_identity:
            os.replace(
                preserved_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        else:
            raise OSError("restore rollback artifact identity differs")
    os.rmdir(investigation_name, dir_fd=parent_descriptor)
    os.fsync(parent_descriptor)


def _stat_optional(parent_descriptor: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _identity(status: os.stat_result) -> tuple[int, int]:
    return status.st_dev, status.st_ino


def _require_watcher_stopped(check: Callable[[], bool]) -> None:
    try:
        stopped = check()
    except Exception:
        raise RecoveryRestoreError("watcher stopped precondition failed") from None
    if stopped is not True:
        raise RecoveryRestoreError("watcher must be stopped before recovery")


def _validate_expected_identity(uid: object, gid: object) -> None:
    if not _is_strict_integer(uid) or uid < 0 or uid != os.geteuid():
        raise RecoveryRestoreError("restore owner identity is unsupported")
    if not _is_strict_integer(gid) or gid < 0 or gid != os.getegid():
        raise RecoveryRestoreError("restore group identity is unsupported")


def _validate_database_name(name: str) -> None:
    if len(name) < 1 or len(name) > 128 or name in {".", ".."}:
        raise RecoveryRestoreError("recovery database name is invalid")
    if "/" in name or "\x00" in name:
        raise RecoveryRestoreError("recovery database name is invalid")


def _descriptor_path(parent_descriptor: int, name: str) -> Path:
    return Path(f"/proc/self/fd/{parent_descriptor}/{name}")


def _remove_investigation_quietly(
    parent_descriptor: int,
    investigation_name: str,
    database_name: str,
) -> None:
    for name in (database_name, f"{database_name}-wal", f"{database_name}-shm"):
        _unlink_quietly(parent_descriptor, f"{investigation_name}/{name}")
    try:
        os.rmdir(investigation_name, dir_fd=parent_descriptor)
    except OSError:
        pass


def _unlink_quietly(parent_descriptor: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=parent_descriptor)
    except OSError:
        pass


def _close_quietly(descriptor: int) -> None:
    try:
        os.close(descriptor)
    except OSError:
        pass


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)
