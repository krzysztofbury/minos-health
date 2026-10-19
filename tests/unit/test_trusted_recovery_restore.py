import hashlib
import os
import stat
from pathlib import Path
from typing import final

import pytest

from minos.ingest.watchers.control import ControlStore
from minos.ingest.watchers.recovery.image import (
    RecoveryImageStagingArea,
    validate_prepared_control_database,
)
from minos.ingest.watchers.recovery.inventory import (
    RecoveryInventory,
    TrustedObjectIdentity,
)
from minos.ingest.watchers.recovery.models import RecoveryImageRequest
from minos.ingest.watchers.recovery.restore import (
    RecoveryRestoreError,
    install_trusted_recovery,
)
from minos.ingest.watchers.recovery.trusted_keys import trusted_recovery_key

_CREATED_AT_MS = 1_758_225_600_000
type _PathArgument = str | bytes | os.PathLike[str] | os.PathLike[bytes]


@final
class _Downloader:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.identities: list[TrustedObjectIdentity] = []
        self.calls = 0
        self.error: BaseException | None = None

    def download_trusted(
        self,
        identity: TrustedObjectIdentity,
        destination_descriptor: int,
    ) -> None:
        self.calls += 1
        self.identities.append(identity)
        if self.error is not None:
            raise self.error
        view = memoryview(self.content)
        offset = 0
        while offset < len(view):
            offset += os.write(destination_descriptor, view[offset:])


def test_restore_requires_stopped_watcher_before_download(tmp_path: Path) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    database_path = _private_parent(tmp_path) / "control.sqlite3"
    downloader = _Downloader(content)

    with pytest.raises(RecoveryRestoreError, match="must be stopped"):
        _ = install_trusted_recovery(
            downloader,
            inventory,
            database_path,
            watcher_is_stopped=lambda: False,
        )
    assert downloader.calls == 0
    assert not database_path.exists()


def test_restore_downloads_version_pinned_image_and_preserves_old_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    old_content = b"old database evidence"
    artifacts = {
        database_path: old_content,
        Path(f"{database_path}-wal"): b"old wal evidence",
        Path(f"{database_path}-shm"): b"old shm evidence",
    }
    for path, artifact_content in artifacts.items():
        _ = path.write_bytes(artifact_content)
        path.chmod(0o600)
    downloader = _Downloader(content)
    stopped_checks: list[bool] = []
    real_replace = os.replace
    replace_calls: list[
        tuple[_PathArgument, _PathArgument, int | None, int | None]
    ] = []

    def record_replace(
        source: _PathArgument,
        destination: _PathArgument,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        replace_calls.append((source, destination, src_dir_fd, dst_dir_fd))
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", record_replace)

    def watcher_stopped() -> bool:
        stopped_checks.append(True)
        return True

    installed = install_trusted_recovery(
        downloader,
        inventory,
        database_path,
        watcher_is_stopped=watcher_stopped,
    )

    assert len(stopped_checks) == 3
    assert downloader.identities == [
        TrustedObjectIdentity(
            inventory.trusted_object_key,
            inventory.trusted_object_checksum_sha256,
            inventory.trusted_object_size_bytes,
            inventory.trusted_object_last_modified_ms,
        )
    ]
    assert replace_calls[0][2] == replace_calls[0][3]
    assert installed.investigation_directory is not None
    for old_path, artifact_content in artifacts.items():
        preserved = installed.investigation_directory / old_path.name
        assert preserved.read_bytes() == artifact_content
    assert not Path(f"{database_path}-wal").exists()
    assert not Path(f"{database_path}-shm").exists()
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    validate_prepared_control_database(
        database_path,
        expected_state_revision=inventory.trusted_object_revision,
    )
    assert installed.metadata.state_revision == inventory.trusted_object_revision
    restored = ControlStore.open(database_path)
    restored.close()


def test_restore_fails_before_mutation_on_bad_mode_or_final_stop_check(
    tmp_path: Path,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    _ = database_path.write_bytes(b"old")
    database_path.chmod(0o644)
    downloader = _Downloader(content)
    with pytest.raises(RecoveryRestoreError, match="mode is unsafe"):
        _ = install_trusted_recovery(
            downloader,
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )
    assert downloader.calls == 0
    assert database_path.read_bytes() == b"old"

    database_path.chmod(0o600)
    checks = iter((True, True, False))
    with pytest.raises(RecoveryRestoreError, match="must be stopped"):
        _ = install_trusted_recovery(
            downloader,
            inventory,
            database_path,
            watcher_is_stopped=lambda: next(checks),
        )
    assert database_path.read_bytes() == b"old"
    assert list(parent.glob(".minos-restore-*")) == []
    assert list(parent.glob("*.pre-restore-*")) == []


def test_restore_rejects_tampering_and_keeps_errors_path_free(tmp_path: Path) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    downloader = _Downloader(content[:-1] + b"x")
    with pytest.raises(RecoveryRestoreError, match="image is invalid") as captured:
        _ = install_trusted_recovery(
            downloader,
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )
    assert str(database_path) not in str(captured.value)
    assert not database_path.exists()
    assert list(parent.iterdir()) == []

    wrong_revision = inventory.model_copy(
        update={"trusted_object_revision": inventory.trusted_object_revision + 1}
    )
    with pytest.raises(RecoveryRestoreError, match="revision differs"):
        _ = install_trusted_recovery(
            _Downloader(content),
            wrong_revision,
            database_path,
            watcher_is_stopped=lambda: True,
        )
    assert not database_path.exists()


def test_replace_failure_restores_old_database_and_sidecars(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    _ = database_path.write_bytes(b"old database")
    database_path.chmod(0o600)
    wal_path = Path(f"{database_path}-wal")
    _ = wal_path.write_bytes(b"old wal")
    wal_path.chmod(0o600)

    real_replace = os.replace
    failed = False

    def fail_replace_once(
        source: _PathArgument,
        destination: _PathArgument,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected replace failure")
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", fail_replace_once)
    with pytest.raises(RecoveryRestoreError, match="installation failed"):
        _ = install_trusted_recovery(
            _Downloader(content),
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )

    assert database_path.read_bytes() == b"old database"
    assert wal_path.read_bytes() == b"old wal"
    assert list(parent.glob("*.pre-restore-*")) == []


def test_post_replace_validation_failure_restores_complete_old_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    artifacts = {
        database_path: b"old database",
        Path(f"{database_path}-wal"): b"old wal",
        Path(f"{database_path}-shm"): b"old shm",
    }
    for path, artifact_content in artifacts.items():
        _ = path.write_bytes(artifact_content)
        path.chmod(0o600)

    def fail_validation(*_arguments: object, **_keywords: object) -> None:
        raise RecoveryRestoreError("injected validation failure")

    monkeypatch.setattr(
        "minos.ingest.watchers.recovery.restore._validate_installed",
        fail_validation,
    )
    with pytest.raises(RecoveryRestoreError, match="injected validation failure"):
        _ = install_trusted_recovery(
            _Downloader(content),
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )

    for path, artifact_content in artifacts.items():
        assert path.read_bytes() == artifact_content
        assert path.stat().st_nlink == 1
    assert list(parent.glob("*.pre-restore-*")) == []


def test_partial_sidecar_removal_failure_restores_complete_old_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    artifacts = {
        database_path: b"old database",
        Path(f"{database_path}-wal"): b"old wal",
        Path(f"{database_path}-shm"): b"old shm",
    }
    for path, artifact_content in artifacts.items():
        _ = path.write_bytes(artifact_content)
        path.chmod(0o600)
    real_unlink = os.unlink
    failed = False

    def fail_second_sidecar_once(
        path: _PathArgument,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if path == f"{database_path.name}-shm" and not failed:
            failed = True
            raise OSError("injected sidecar removal failure")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_second_sidecar_once)
    with pytest.raises(RecoveryRestoreError, match="installation failed"):
        _ = install_trusted_recovery(
            _Downloader(content),
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )

    for path, artifact_content in artifacts.items():
        assert path.read_bytes() == artifact_content
        assert path.stat().st_nlink == 1
    assert list(parent.glob("*.pre-restore-*")) == []


def test_replace_success_followed_by_failure_restores_complete_old_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    artifacts = {
        database_path: b"old database",
        Path(f"{database_path}-wal"): b"old wal",
    }
    for path, artifact_content in artifacts.items():
        _ = path.write_bytes(artifact_content)
        path.chmod(0o600)
    real_replace = os.replace
    interrupted = False

    def replace_then_interrupt(
        source: _PathArgument,
        destination: _PathArgument,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal interrupted
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if not interrupted:
            interrupted = True
            raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", replace_then_interrupt)
    with pytest.raises(KeyboardInterrupt):
        _ = install_trusted_recovery(
            _Downloader(content),
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )

    for path, artifact_content in artifacts.items():
        assert path.read_bytes() == artifact_content
        assert path.stat().st_nlink == 1
    assert list(parent.glob("*.pre-restore-*")) == []


def test_post_replace_directory_fsync_failure_restores_old_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content, inventory = _recovery_fixture(tmp_path)
    parent = _private_parent(tmp_path)
    database_path = parent / "control.sqlite3"
    _ = database_path.write_bytes(b"old database")
    database_path.chmod(0o600)
    real_replace = os.replace
    real_fsync = os.fsync
    replaced = False
    failed = False

    def record_replace(
        source: _PathArgument,
        destination: _PathArgument,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal replaced
        real_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        replaced = True

    def fail_fsync_once(descriptor: int) -> None:
        nonlocal failed
        if replaced and not failed:
            failed = True
            raise OSError("injected directory fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(os, "replace", record_replace)
    monkeypatch.setattr(os, "fsync", fail_fsync_once)
    with pytest.raises(RecoveryRestoreError, match="installation failed"):
        _ = install_trusted_recovery(
            _Downloader(content),
            inventory,
            database_path,
            watcher_is_stopped=lambda: True,
        )

    assert database_path.read_bytes() == b"old database"
    assert database_path.stat().st_nlink == 1
    assert list(parent.glob("*.pre-restore-*")) == []


def _recovery_fixture(tmp_path: Path) -> tuple[bytes, RecoveryInventory]:
    source = ControlStore.open(tmp_path / "source.sqlite3")
    staging = RecoveryImageStagingArea(
        tmp_path / "recovery-staging",
        clock_ms=lambda: _CREATED_AT_MS,
    )
    try:
        with staging.create(
            source,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            content = image.path.read_bytes()
            inventory = RecoveryInventory(
                sequence=1,
                prior_inventory_sha256=None,
                trusted_object_key=trusted_recovery_key(1, image.sha256),
                trusted_object_checksum_sha256=image.sha256,
                trusted_object_revision=image.metadata.state_revision,
                trusted_object_size_bytes=image.size_bytes,
                trusted_object_last_modified_ms=_CREATED_AT_MS,
                signature=hashlib.sha256(content).hexdigest(),
            )
    finally:
        source.close()
    return content, inventory


def _private_parent(tmp_path: Path) -> Path:
    path = tmp_path / "control"
    path.mkdir(mode=0o700, exist_ok=True)
    path.chmod(0o700)
    return path
