import os
import shutil
import sqlite3
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    BlobReceipt,
    ClaimRequest,
    ControlSnapshotError,
    ControlStore,
    MigrationError,
    ObservationCommit,
    ObservationDraft,
    SourceConfiguration,
    apply_control_migrations,
    validate_control_database,
)
from minos.ingest.watchers.orchestrator import RunRecoveryPublisher
from minos.ingest.watchers.recovery import (
    FilesystemRecoveryStore,
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    RecoveryListingBoundError,
    RecoveryStagingError,
    RecoveryStorageError,
    RecoveryVerificationError,
    parse_recovery_key,
    recovery_key,
    validate_recovery_image,
)
from minos.ingest.watchers.recovery import filesystem as filesystem_module
from minos.ingest.watchers.recovery.models import RecoveryImageLease

_CREATED_AT_MS = 1_758_225_600_000
_DIGEST = "a" * 64


def test_daily_image_embeds_metadata_only_in_consistent_copy(tmp_path: Path) -> None:
    database_path = tmp_path / "control.sqlite3"
    store = ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    staging = _staging(tmp_path / "staging")
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            assert stat.S_IMODE(image.path.stat().st_mode) == 0o600
            assert image.size_bytes == image.path.stat().st_size
            assert recovery_key(image.metadata, image.sha256) == (
                "control-recovery/daily/2025-09-18/"
                f"{image.metadata.state_revision:020d}/{image.sha256}.sqlite3"
            )
            assert (
                validate_recovery_image(
                    image.path,
                    expected_key=recovery_key(image.metadata, image.sha256),
                    expected_sha256=image.sha256,
                    expected_size_bytes=image.size_bytes,
                )
                == image.metadata
            )
            assert _query(image.path, "PRAGMA integrity_check") == [("ok",)]
            assert _query(image.path, "PRAGMA foreign_key_check") == []
        assert list((tmp_path / "staging").iterdir()) == []
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            _ = _query(database_path, "SELECT * FROM recovery_image_metadata")
    finally:
        store.close()


def test_filesystem_recovery_publish_recovers_linked_temporary_on_same_store_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "control.sqlite3"
    archive_root = tmp_path / "archive"
    store = ControlStore.open(database_path)
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    original_unlink = Path.unlink
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            key = recovery_key(image.metadata, image.sha256)
            metadata = image.metadata
            sha256 = image.sha256
            size_bytes = image.size_bytes

            def interrupt_temporary_unlink(
                path: Path,
                missing_ok: bool = False,
            ) -> None:
                if path.parent.name == "recovery-temporary":
                    raise OSError("injected crash before temporary unlink")
                original_unlink(path, missing_ok=missing_ok)

            monkeypatch.setattr(Path, "unlink", interrupt_temporary_unlink)
            with pytest.raises(
                RecoveryStorageError,
                match="temporary file cannot be removed",
            ):
                _ = recovery_store.publish(image)
            monkeypatch.setattr(Path, "unlink", original_unlink)

            destination = archive_root / key
            temporary_entries = list((archive_root / "recovery-temporary").iterdir())
            assert len(temporary_entries) == 1
            assert temporary_entries[0].stat().st_ino == destination.stat().st_ino
            assert destination.stat().st_nlink == 2

            published = recovery_store.publish(image)

        assert list((archive_root / "recovery-temporary").iterdir()) == []
        assert destination.stat().st_nlink == 1
        assert published.key == key
        assert (
            validate_recovery_image(
                destination,
                expected_key=key,
                expected_sha256=sha256,
                expected_size_bytes=size_bytes,
                validation_time_ms=metadata.created_at_ms,
            )
            == metadata
        )
    finally:
        monkeypatch.setattr(Path, "unlink", original_unlink)
        store.close()


def test_filesystem_recovery_preserves_both_names_when_link_sync_is_uncertain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "control.sqlite3"
    archive_root = tmp_path / "archive"
    store = ControlStore.open(database_path)
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    original_fsync_directory = cast(
        "Callable[[Path], None]",
        filesystem_module.__dict__["_fsync_directory"],
    )
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            key = recovery_key(image.metadata, image.sha256)
            destination = archive_root / key
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            current = destination.parent
            while current != archive_root:
                current.chmod(0o700)
                current = current.parent
            failure_injected = False

            def fail_destination_sync_once(directory: Path) -> None:
                nonlocal failure_injected
                if directory == destination.parent and not failure_injected:
                    failure_injected = True
                    raise RecoveryStorageError("injected destination sync failure")
                original_fsync_directory(directory)

            monkeypatch.setattr(
                filesystem_module,
                "_fsync_directory",
                fail_destination_sync_once,
            )
            with pytest.raises(RecoveryStorageError, match="injected destination"):
                _ = recovery_store.publish(image)
            monkeypatch.setattr(
                filesystem_module,
                "_fsync_directory",
                original_fsync_directory,
            )

            temporary_entries = list((archive_root / "recovery-temporary").iterdir())
            assert len(temporary_entries) == 1
            assert temporary_entries[0].stat().st_ino == destination.stat().st_ino
            assert destination.stat().st_nlink == 2

        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as next_image:
            _ = FilesystemRecoveryStore(archive_root).publish(next_image)

        assert list((archive_root / "recovery-temporary").iterdir()) == []
        assert destination.stat().st_nlink == 1
    finally:
        store.close()


def test_filesystem_recovery_syncs_link_interrupted_before_state_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class InjectedPowerLoss(BaseException):
        pass

    archive_root = tmp_path / "archive"
    store = ControlStore.open(tmp_path / "control.sqlite3")
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    original_link = os.link
    original_fsync_directory = cast(
        "Callable[[Path], None]",
        filesystem_module.__dict__["_fsync_directory"],
    )
    synchronized: list[Path] = []

    def record_fsync(directory: Path) -> None:
        original_fsync_directory(directory)
        synchronized.append(directory)

    def link_then_interrupt(
        source: Path,
        destination: Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        raise InjectedPowerLoss

    monkeypatch.setattr(filesystem_module, "_fsync_directory", record_fsync)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            destination = archive_root / recovery_key(image.metadata, image.sha256)
            monkeypatch.setattr(os, "link", link_then_interrupt)

            with pytest.raises(InjectedPowerLoss):
                _ = recovery_store.publish(image)

            assert destination.parent in synchronized
            temporary_entries = list((archive_root / "recovery-temporary").iterdir())
            assert len(temporary_entries) == 1
            assert temporary_entries[0].stat().st_ino == destination.stat().st_ino
            assert destination.stat().st_nlink == 2

            monkeypatch.setattr(os, "link", original_link)
            synchronized.clear()
            restarted_store = FilesystemRecoveryStore(archive_root)

            assert list((archive_root / "recovery-temporary").iterdir()) == []
            assert destination.stat().st_nlink == 1

            synchronized.clear()
            published = restarted_store.publish(image)

            assert destination.parent in synchronized
            assert published.key == destination.relative_to(archive_root).as_posix()
            assert list((archive_root / "recovery-temporary").iterdir()) == []
    finally:
        monkeypatch.setattr(os, "link", original_link)
        store.close()


@pytest.mark.parametrize(
    "surviving_names",
    ["neither", "temporary", "temporary_and_destination", "destination"],
)
@pytest.mark.parametrize("reconcile_via", ["constructor", "list_images"])
def test_filesystem_recovery_replays_every_power_loss_namespace_state(
    tmp_path: Path,
    surviving_names: str,
    reconcile_via: str,
) -> None:
    database_path = tmp_path / "control.sqlite3"
    archive_root = tmp_path / "archive"
    store = ControlStore.open(database_path)
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            key = recovery_key(image.metadata, image.sha256)
            metadata = image.metadata
            sha256 = image.sha256
            destination = archive_root / key
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            current = destination.parent
            while current != archive_root:
                current.chmod(0o700)
                current = current.parent
            temporary = (
                archive_root
                / "recovery-temporary"
                / (f".minos-recovery-publish-{image.sha256}-{'a' * 32}.sqlite3")
            )
            if surviving_names != "neither":
                _ = shutil.copyfile(image.path, temporary)
                temporary.chmod(0o600)
            if surviving_names in ("temporary_and_destination", "destination"):
                os.link(temporary, destination, follow_symlinks=False)
            if surviving_names == "destination":
                temporary.unlink()

        if reconcile_via == "constructor":
            recovery_store = FilesystemRecoveryStore(archive_root)
            assert list((archive_root / "recovery-temporary").iterdir()) == []
            if surviving_names in ("temporary_and_destination", "destination"):
                assert destination.stat().st_nlink == 1
        listed = recovery_store.list_images("daily", maximum_objects=8)

        assert list((archive_root / "recovery-temporary").iterdir()) == []
        if surviving_names in ("temporary_and_destination", "destination"):
            assert len(listed) == 1
            assert listed[0].key == key
            assert destination.stat().st_nlink == 1
        else:
            assert listed == ()
            assert not destination.exists()

        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as next_image:
            _ = recovery_store.publish(next_image)

        assert list((archive_root / "recovery-temporary").iterdir()) == []
        assert destination.stat().st_nlink == 1
        assert (
            validate_recovery_image(
                destination,
                expected_key=key,
                expected_sha256=sha256,
                expected_size_bytes=destination.stat().st_size,
                validation_time_ms=metadata.created_at_ms,
            )
            == metadata
        )
    finally:
        store.close()


@pytest.mark.parametrize("reconcile_via", ["constructor", "list_images"])
@pytest.mark.parametrize(
    "unsafe_state",
    ["extra_link", "unrelated_link", "mismatched_contents", "symlink"],
)
def test_filesystem_recovery_rejects_unsafe_stale_publication_states(
    tmp_path: Path,
    reconcile_via: str,
    unsafe_state: str,
) -> None:
    archive_root = tmp_path / "archive"
    store = ControlStore.open(tmp_path / "control.sqlite3")
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            destination = archive_root / recovery_key(image.metadata, image.sha256)
            _prepare_private_test_parents(destination.parent, archive_root)
            temporary = (
                archive_root
                / "recovery-temporary"
                / (f".minos-recovery-publish-{image.sha256}-{'b' * 32}.sqlite3")
            )
            unrelated = archive_root / "unrelated.sqlite3"
            if unsafe_state == "symlink":
                _ = shutil.copyfile(image.path, destination)
                destination.chmod(0o600)
                temporary.symlink_to(destination)
            else:
                _ = shutil.copyfile(image.path, temporary)
                temporary.chmod(0o600)
                if unsafe_state == "unrelated_link":
                    os.link(temporary, unrelated, follow_symlinks=False)
                else:
                    os.link(temporary, destination, follow_symlinks=False)
                    if unsafe_state == "extra_link":
                        os.link(temporary, unrelated, follow_symlinks=False)
                    else:
                        with temporary.open("r+b", buffering=0) as corrupted:
                            _ = corrupted.write(b"corrupt")
                            os.fsync(corrupted.fileno())

        with pytest.raises((RecoveryStorageError, RecoveryVerificationError)):
            if reconcile_via == "constructor":
                _ = FilesystemRecoveryStore(archive_root)
            else:
                _ = recovery_store.list_images("daily", maximum_objects=8)

        if unsafe_state == "symlink":
            assert temporary.is_symlink()
        else:
            assert temporary.exists()
            assert temporary.stat().st_nlink in (2, 3)
    finally:
        store.close()


def test_filesystem_recovery_listing_rejects_destination_with_an_extra_link(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    store = ControlStore.open(tmp_path / "control.sqlite3")
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            destination = archive_root / recovery_key(image.metadata, image.sha256)
            _prepare_private_test_parents(destination.parent, archive_root)
            _ = shutil.copyfile(image.path, destination)
            destination.chmod(0o600)
            os.link(
                destination,
                archive_root / "unrelated.sqlite3",
                follow_symlinks=False,
            )

        with pytest.raises(RecoveryStorageError, match="image is unsafe"):
            _ = recovery_store.list_images("daily", maximum_objects=8)

        assert destination.stat().st_nlink == 2
    finally:
        store.close()


def test_filesystem_recovery_listing_bounds_empty_directories(tmp_path: Path) -> None:
    archive_root = tmp_path / "archive"
    recovery_store = FilesystemRecoveryStore(archive_root)
    class_root = archive_root / "control-recovery" / "daily"
    class_root.mkdir(mode=0o700)
    for index in range(5):
        (class_root / f"empty-{index}").mkdir(mode=0o700)

    with pytest.raises(RecoveryListingBoundError, match="traversal bound"):
        _ = recovery_store.list_images("daily", maximum_objects=1, page_size=1)

    assert recovery_store.list_images("daily", maximum_objects=1, page_size=2) == ()


def test_periodic_recovery_startup_reconciles_linked_publication(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    store = ControlStore.open(tmp_path / "control.sqlite3")
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            key = recovery_key(image.metadata, image.sha256)
            destination = archive_root / key
            _prepare_private_test_parents(destination.parent, archive_root)
            temporary = (
                archive_root
                / "recovery-temporary"
                / (f".minos-recovery-publish-{image.sha256}-{'c' * 32}.sqlite3")
            )
            _ = shutil.copyfile(image.path, temporary)
            temporary.chmod(0o600)
            os.link(temporary, destination, follow_symlinks=False)

        publisher = RunRecoveryPublisher(staging, recovery_store)

        assert publisher.ensure_periodic(store, "daily") is None
        assert list((archive_root / "recovery-temporary").iterdir()) == []
        assert destination.stat().st_nlink == 1
        assert recovery_store.list_images("daily", maximum_objects=8)[0].key == key
    finally:
        store.close()


def test_filesystem_recovery_startup_bounds_stale_publication_names(
    tmp_path: Path,
) -> None:
    archive_root = tmp_path / "archive"
    _ = FilesystemRecoveryStore(archive_root)
    temporary_root = archive_root / "recovery-temporary"
    for index in range(17):
        path = temporary_root / (
            f".minos-recovery-publish-{'a' * 64}-{index:032x}.sqlite3"
        )
        _ = path.write_bytes(b"stale")
        path.chmod(0o600)

    with pytest.raises(RecoveryStorageError, match="count exceeds"):
        _ = FilesystemRecoveryStore(archive_root)

    assert len(list(temporary_root.iterdir())) == 17


def test_filesystem_recovery_fsyncs_each_new_directory_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "one" / "two" / "archive"
    synchronized: list[Path] = []
    original_fsync_directory = cast(
        "Callable[[Path], None]",
        filesystem_module.__dict__["_fsync_directory"],
    )

    def record_fsync(directory: Path) -> None:
        original_fsync_directory(directory)
        synchronized.append(directory)

    monkeypatch.setattr(filesystem_module, "_fsync_directory", record_fsync)

    _ = FilesystemRecoveryStore(root)

    assert synchronized == [
        tmp_path,
        tmp_path / "one",
        tmp_path / "one" / "two",
        root,
        root,
    ]


def test_filesystem_recovery_publication_uses_durable_namespace_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "control.sqlite3"
    archive_root = tmp_path / "archive"
    store = ControlStore.open(database_path)
    created_at_ms = time.time_ns() // 1_000_000
    staging = RecoveryImageStagingArea(
        tmp_path / "staging",
        maximum_size_bytes=2 * 1024 * 1024 * 1024,
        clock_ms=lambda: created_at_ms,
    )
    recovery_store = FilesystemRecoveryStore(archive_root)
    events: list[tuple[str, Path]] = []
    original_copy_image = cast(
        "Callable[[BinaryIO, Path, int], None]",
        filesystem_module.__dict__["_copy_image"],
    )
    original_fsync_directory = cast(
        "Callable[[Path], None]",
        filesystem_module.__dict__["_fsync_directory"],
    )
    original_link = os.link
    original_unlink = Path.unlink

    def record_copy(source: BinaryIO, destination: Path, size_bytes: int) -> None:
        original_copy_image(source, destination, size_bytes)
        events.append(("file_fsync", destination))

    def record_fsync(directory: Path) -> None:
        original_fsync_directory(directory)
        events.append(("directory_fsync", directory))

    def record_link(
        source: Path,
        destination: Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        original_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        events.append(("link", destination))

    def record_unlink(path: Path, missing_ok: bool = False) -> None:
        original_unlink(path, missing_ok=missing_ok)
        if path.parent.name == "recovery-temporary":
            events.append(("unlink", path))

    monkeypatch.setattr(filesystem_module, "_copy_image", record_copy)
    monkeypatch.setattr(filesystem_module, "_fsync_directory", record_fsync)
    monkeypatch.setattr(os, "link", record_link)
    monkeypatch.setattr(Path, "unlink", record_unlink)
    try:
        with staging.create(
            store,
            RecoveryImageRequest("daily", created_at_ms),
        ) as image:
            key = recovery_key(image.metadata, image.sha256)
            destination = archive_root / key
            _ = recovery_store.publish(image)

        period_directory = destination.parent.parent
        class_directory = period_directory.parent
        temporary_directory = archive_root / "recovery-temporary"
        temporary = events[3][1]
        assert events == [
            ("directory_fsync", archive_root / "control-recovery"),
            ("directory_fsync", class_directory),
            ("directory_fsync", period_directory),
            ("file_fsync", temporary),
            ("link", destination),
            ("directory_fsync", destination.parent),
            ("unlink", temporary),
            ("directory_fsync", temporary_directory),
        ]
    finally:
        monkeypatch.setattr(Path, "unlink", original_unlink)
        store.close()


def test_run_image_requires_exact_committed_run(tmp_path: Path) -> None:
    store = ControlStore.open(
        tmp_path / "control.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    staging = _staging(tmp_path / "staging")
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        now_ms = time.time_ns() // 1_000_000
        claim = store.acquire_claim(
            ClaimRequest("rsk", "manual", now_ms, deadline_after_ms=300_000)
        )
        receipt = BlobReceipt(
            _DIGEST,
            2,
            "application/json",
            f"blobs/sha256/{_DIGEST}",
            _DIGEST,
            2,
        )
        observation = ObservationDraft(
            "recovery-observation-0001",
            "catalog",
            "source",
            _DIGEST,
            now_ms,
            {},
        )
        committed = store.commit_observations(
            claim.identity,
            ObservationCommit((receipt,), (observation,), {}),
        )
        request = RecoveryImageRequest(
            "run",
            _CREATED_AT_MS,
            committed.run_id,
            committed.committed_state_revision,
        )

        with staging.create(store, request) as image:
            assert image.metadata.run_id == committed.run_id
            assert image.metadata.committed_state_revision == (
                committed.committed_state_revision
            )

        wrong = RecoveryImageRequest(
            "run",
            _CREATED_AT_MS,
            committed.run_id,
            committed.committed_state_revision + 1,
        )
        with pytest.raises(RecoveryVerificationError, match="committed run"):
            with staging.create(store, wrong):
                pass
        assert list((tmp_path / "staging").iterdir()) == []
    finally:
        store.close()


def test_copy_failure_and_size_limit_always_remove_staging_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging", maximum_size_bytes=4096)
    try:
        with pytest.raises(RecoveryVerificationError, match="creation failed"):
            with staging.create(store, RecoveryImageRequest("daily", _CREATED_AT_MS)):
                pass
        assert list((tmp_path / "staging").iterdir()) == []

        def fail_backup(
            _self: ControlStore,
            _destination: sqlite3.Connection,
            *,
            deadline_monotonic: float,
            monotonic: object,
            maximum_size_bytes: int,
        ) -> object:
            _ = deadline_monotonic, monotonic, maximum_size_bytes
            raise ControlSnapshotError("injected backup failure")

        monkeypatch.setattr(ControlStore, "backup_to", fail_backup)
        roomy = _staging(tmp_path / "roomy")
        with pytest.raises(RecoveryVerificationError, match="creation failed"):
            with roomy.create(store, RecoveryImageRequest("daily", _CREATED_AT_MS)):
                pass
        assert list((tmp_path / "roomy").iterdir()) == []
    finally:
        store.close()


def test_image_validation_rejects_metadata_schema_and_foreign_key_tampering(
    tmp_path: Path,
) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            store,
            RecoveryImageRequest("monthly", _CREATED_AT_MS),
        ) as image:
            connection = sqlite3.connect(image.path, isolation_level=None)
            try:
                _ = connection.execute(
                    "UPDATE recovery_image_metadata SET migration_set_sha256 = ?",
                    ("0" * 64,),
                )
            finally:
                connection.close()
            with pytest.raises(
                RecoveryVerificationError, match="migration set differs"
            ):
                _ = validate_recovery_image(image.path)

        with staging.create(
            store,
            RecoveryImageRequest("monthly", _CREATED_AT_MS),
        ) as image:
            connection = sqlite3.connect(image.path, isolation_level=None)
            try:
                _ = connection.execute("PRAGMA writable_schema = ON")
                _ = connection.execute(
                    """UPDATE sqlite_schema SET sql = sql || ' '
                    WHERE name = 'recovery_image_metadata'"""
                )
                _ = connection.execute("PRAGMA writable_schema = OFF")
            finally:
                connection.close()
            with pytest.raises(RecoveryVerificationError, match="SQLite validation"):
                _ = validate_recovery_image(image.path)

        with staging.create(
            store,
            RecoveryImageRequest("monthly", _CREATED_AT_MS),
        ) as image:
            connection = sqlite3.connect(image.path, isolation_level=None)
            try:
                _ = connection.execute(
                    """
                    INSERT INTO source_cursors (
                        source_id, cursor_json, state_revision, updated_at_ms
                    ) VALUES ('missing', '{}', 1, ?)
                    """,
                    (_CREATED_AT_MS,),
                )
            finally:
                connection.close()
            with pytest.raises(RecoveryVerificationError, match="foreign-key"):
                _ = validate_recovery_image(image.path)
    finally:
        store.close()


@pytest.mark.parametrize(
    "key",
    [
        f"control-recovery/run/1/{_DIGEST}.sqlite3",
        f"control-recovery/run/{1:020d}/{_DIGEST.upper()}.sqlite3",
        f"control-recovery/daily/2025-02-29/{1:020d}/{_DIGEST}.sqlite3",
        f"control-recovery/weekly/2025-W54/{1:020d}/{_DIGEST}.sqlite3",
        f"control-recovery/monthly/2025-13/{1:020d}/{_DIGEST}.sqlite3",
        f"control-recovery/hourly/2025-01-01/{1:020d}/{_DIGEST}.sqlite3",
        f"control-recovery/run/{1:020d}/{_DIGEST}.sqlite3/extra",
    ],
)
def test_recovery_key_parser_rejects_noncanonical_grammar(key: str) -> None:
    with pytest.raises(RecoveryVerificationError):
        _ = parse_recovery_key(key)


def test_staging_rejects_unknown_or_unsafe_stale_entries(tmp_path: Path) -> None:
    staging_path = tmp_path / "staging"
    staging = _staging(staging_path)
    _ = (staging_path / "unknown").write_text("unsafe", encoding="utf-8")
    store = ControlStore.open(tmp_path / "control.sqlite3")
    try:
        with pytest.raises(RecoveryStagingError):
            with staging.create(store, RecoveryImageRequest("daily", _CREATED_AT_MS)):
                pass
    finally:
        store.close()


def test_live_schema_validation_rejects_image_only_metadata(
    tmp_path: Path,
) -> None:
    original = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    try:
        with staging.create(
            original,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            connection = sqlite3.connect(image.path, isolation_level=None)
            try:
                migration_result = apply_control_migrations(connection)
                assert migration_result.schema_version == 3
                assert migration_result.forward_compatible is False
                with pytest.raises(MigrationError, match="schema differs"):
                    _ = validate_control_database(connection)
            finally:
                connection.close()
    finally:
        original.close()


def test_backup_succeeds_while_a_wal_writer_holds_uncommitted_changes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "control.sqlite3"
    store = ControlStore.open(database_path)
    writer = sqlite3.connect(database_path, isolation_level=None)
    destination = sqlite3.connect(":memory:", isolation_level=None)
    try:
        _ = writer.execute("PRAGMA journal_mode = WAL")
        _ = writer.execute("BEGIN IMMEDIATE")
        _ = writer.execute(
            "UPDATE control_state SET state_revision = state_revision + 1"
        )

        identity = store.backup_to(
            destination,
            deadline_monotonic=time.monotonic() + 5,
        )

        assert identity.state_revision == 0
        assert destination.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (0,)
    finally:
        writer.rollback()
        destination.close()
        writer.close()
        store.close()


def test_backup_deadline_expires_before_copying(tmp_path: Path) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    destination = sqlite3.connect(":memory:", isolation_level=None)
    monotonic_values = iter((0.0, 0.0, 0.0, 0.0, 2.0))
    try:
        with pytest.raises(ControlSnapshotError, match="deadline expired"):
            _ = store.backup_to(
                destination,
                deadline_monotonic=1.0,
                monotonic=lambda: next(monotonic_values),
            )
    finally:
        destination.close()
        store.close()


def test_creation_and_validation_reject_implausible_timestamps(
    tmp_path: Path,
) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    stale_staging = RecoveryImageStagingArea(
        tmp_path / "stale-staging",
        clock_ms=lambda: _CREATED_AT_MS + 10 * 60 * 1000,
    )
    try:
        with pytest.raises(RecoveryVerificationError, match="captured clock"):
            with stale_staging.create(
                store,
                RecoveryImageRequest("daily", _CREATED_AT_MS),
            ):
                pass

        with staging.create(
            store,
            RecoveryImageRequest("daily", _CREATED_AT_MS),
        ) as image:
            with pytest.raises(RecoveryVerificationError, match="future"):
                _ = validate_recovery_image(
                    image.path,
                    validation_time_ms=_CREATED_AT_MS - 6 * 60 * 1000,
                )
    finally:
        store.close()


def test_lease_cleanup_failure_does_not_mask_control_flow_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    staging = _staging(tmp_path / "staging")
    real_close = RecoveryImageLease.close

    def close_then_fail(lease: RecoveryImageLease) -> None:
        real_close(lease)
        raise RuntimeError("injected cleanup failure")

    monkeypatch.setattr(RecoveryImageLease, "close", close_then_fail)
    try:
        with pytest.raises(KeyboardInterrupt) as captured:
            with staging.create(
                store,
                RecoveryImageRequest("daily", _CREATED_AT_MS),
            ):
                raise KeyboardInterrupt
        assert any("lease cleanup" in note for note in captured.value.__notes__)
    finally:
        store.close()


def _staging(
    path: Path,
    *,
    maximum_size_bytes: int = 2 * 1024 * 1024 * 1024,
) -> RecoveryImageStagingArea:
    return RecoveryImageStagingArea(
        path,
        maximum_size_bytes=maximum_size_bytes,
        clock_ms=lambda: _CREATED_AT_MS + 1000,
    )


def _prepare_private_test_parents(destination: Path, root: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = destination
    while current != root:
        current.chmod(0o700)
        current = current.parent


def _query(path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
