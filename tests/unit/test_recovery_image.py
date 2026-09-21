import sqlite3
import stat
import time
from pathlib import Path

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
from minos.ingest.watchers.recovery import (
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    RecoveryStagingError,
    RecoveryVerificationError,
    parse_recovery_key,
    recovery_key,
    validate_recovery_image,
)
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
                assert migration_result.schema_version == 1
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


def _query(path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
