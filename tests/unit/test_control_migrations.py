import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from minos.ingest.watchers.control.migrations import (
    MigrationError,
    apply_control_migrations,
    load_control_migrations,
    rollback_control_migrations_for_testing,
)

_PROJECT_ROOT = Path(__file__).parents[2]
_SQL_ROOT = _PROJECT_ROOT / "database/control/sqlite"
_MIGRATION_MANIFEST = "minos.ingest.watchers.control.migrations._MIGRATION_SPECS"
_SCHEMA_VERSION = "minos.ingest.watchers.control.migrations.CONTROL_SCHEMA_VERSION"
_NOW_MS = 1_758_225_600_000


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    database = sqlite3.connect(":memory:", isolation_level=None)
    try:
        yield database
    finally:
        database.close()


def test_fresh_apply_and_reopen_validate_exact_migration_history(
    connection: sqlite3.Connection,
) -> None:
    first = apply_control_migrations(connection, _SQL_ROOT)
    second = apply_control_migrations(connection, _SQL_ROOT)

    assert first.schema_version == 1
    assert first.applied_versions == (1,)
    assert first.forward_compatible is False
    assert second.applied_versions == ()
    assert connection.execute("PRAGMA user_version").fetchone() == (1,)
    assert connection.execute(
        "SELECT state_revision, schema_version FROM control_state"
    ).fetchall() == [(0, 1)]

    expected = load_control_migrations(_SQL_ROOT)[0]
    assert connection.execute(
        """
        SELECT version, name, up_sha256, down_sha256, minimum_reader_version
        FROM control_schema_migrations
        """
    ).fetchall() == [
        (
            expected.version,
            expected.name,
            expected.up_sha256,
            expected.down_sha256,
            expected.minimum_reader_version,
        )
    ]
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)


def test_migration_validation_is_independent_of_row_factory(
    connection: sqlite3.Connection,
) -> None:
    connection.row_factory = sqlite3.Row

    first = apply_control_migrations(connection, _SQL_ROOT)
    second = apply_control_migrations(connection, _SQL_ROOT)

    assert first.applied_versions == (1,)
    assert second.applied_versions == ()


def test_unsupported_connection_factories_fail_with_migration_error(
    connection: sqlite3.Connection,
) -> None:
    connection.row_factory = _dictionary_row_factory
    with pytest.raises(MigrationError, match="row factory"):
        _ = apply_control_migrations(connection, _SQL_ROOT)

    connection.row_factory = None
    connection.text_factory = bytes
    with pytest.raises(MigrationError, match="text factory"):
        _ = apply_control_migrations(connection, _SQL_ROOT)


def test_canonical_ddl_matches_the_full_migration_chain() -> None:
    migrated = sqlite3.connect(":memory:", isolation_level=None)
    canonical = sqlite3.connect(":memory:", isolation_level=None)
    try:
        _ = apply_control_migrations(migrated, _SQL_ROOT)
        _ = canonical.execute("PRAGMA foreign_keys = ON")
        _ = canonical.executescript(
            (_SQL_ROOT / "schema.sql").read_text(encoding="utf-8")
        )

        assert _schema_objects(migrated) == _schema_objects(canonical)
        assert migrated.execute("PRAGMA user_version").fetchone() == (1,)
        assert canonical.execute("PRAGMA user_version").fetchone() == (1,)
        assert _control_state(migrated) == _control_state(canonical)
        assert _migration_identities(migrated) == _migration_identities(canonical)
        assert canonical.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        migrated.close()
        canonical.close()


def test_editing_an_applied_migration_fails_closed(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    sql_root = _copy_sql_root(tmp_path)
    _ = apply_control_migrations(connection, sql_root)
    up_path = sql_root / "migrations/0001_initial_control_schema.up.sql"
    _ = up_path.write_text(up_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(MigrationError, match="differs from the bundled SQL"):
        _ = apply_control_migrations(connection, sql_root)


def test_all_pending_migrations_roll_back_as_one_transaction(
    connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sql_root = _copy_sql_root(tmp_path)
    _add_failing_second_migration(sql_root)
    monkeypatch.setattr(
        _MIGRATION_MANIFEST,
        ((1, "initial_control_schema", 1), (2, "failing_probe", 1)),
    )
    monkeypatch.setattr(_SCHEMA_VERSION, 2)

    with pytest.raises(MigrationError, match="failed and were rolled back"):
        _ = apply_control_migrations(connection, sql_root)

    assert connection.execute("PRAGMA user_version").fetchone() == (0,)
    assert _schema_objects(connection) == []


@pytest.mark.parametrize(
    ("name", "forbidden_sql"),
    [
        ("hidden_commit", "COMMIT/**/;"),
        ("leading_empty_commit", "; COMMIT;"),
        ("hidden_attach", "ATTACH/**/DATABASE ':memory:' AS escaped;"),
        ("hidden_pragma", "PRAGMA/**/journal_mode = DELETE;"),
    ],
)
def test_sqlite_authorizer_blocks_migration_boundary_bypasses(
    connection: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    forbidden_sql: str,
) -> None:
    sql_root = _copy_sql_root(tmp_path)
    _add_forbidden_second_migration(sql_root, name, forbidden_sql)
    monkeypatch.setattr(
        _MIGRATION_MANIFEST,
        ((1, "initial_control_schema", 1), (2, name, 1)),
    )
    monkeypatch.setattr(_SCHEMA_VERSION, 2)

    with pytest.raises(MigrationError, match="failed and were rolled back"):
        _ = apply_control_migrations(connection, sql_root)

    assert connection.execute("PRAGMA user_version").fetchone() == (0,)
    assert _schema_objects(connection) == []


def test_migration_sql_rejects_utf8_byte_order_mark(
    tmp_path: Path,
) -> None:
    sql_root = _copy_sql_root(tmp_path)
    up_path = sql_root / "migrations/0001_initial_control_schema.up.sql"
    _ = up_path.write_bytes(b"\xef\xbb\xbf" + up_path.read_bytes())

    with pytest.raises(MigrationError, match="byte-order mark"):
        _ = load_control_migrations(sql_root)


def test_testing_rollback_returns_the_database_to_empty(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)

    result = rollback_control_migrations_for_testing(connection, 0, _SQL_ROOT)

    assert result.schema_version == 0
    assert result.applied_versions == (1,)
    assert connection.execute("PRAGMA user_version").fetchone() == (0,)
    assert _schema_objects(connection) == []


def test_older_reader_accepts_only_explicitly_compatible_newer_schema(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)
    _install_unknown_additive_migration(connection, minimum_reader_version=1)

    result = apply_control_migrations(connection, _SQL_ROOT)

    assert result.schema_version == 2
    assert result.applied_versions == ()
    assert result.forward_compatible is True
    assert connection.execute("PRAGMA query_only").fetchone()[0] == 1


def test_older_reader_rejects_incompatible_newer_schema(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)
    _install_unknown_additive_migration(connection, minimum_reader_version=2)

    with pytest.raises(MigrationError, match="not forward-readable"):
        _ = apply_control_migrations(connection, _SQL_ROOT)


def test_untracked_schema_and_unexpected_migration_files_are_rejected(
    connection: sqlite3.Connection,
    tmp_path: Path,
) -> None:
    _ = connection.execute("CREATE TABLE untracked (value TEXT)")
    with pytest.raises(MigrationError, match="without migration history"):
        _ = apply_control_migrations(connection, _SQL_ROOT)

    sql_root = _copy_sql_root(tmp_path)
    _ = (sql_root / "migrations/0002_unlisted.up.sql").write_text(
        "SELECT 1;\n", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="do not match the manifest"):
        _ = load_control_migrations(sql_root)


def test_migration_manifest_must_be_contiguous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        _MIGRATION_MANIFEST,
        ((1, "initial_control_schema", 1), (3, "skipped_version", 1)),
    )
    monkeypatch.setattr(_SCHEMA_VERSION, 3)

    with pytest.raises(MigrationError, match="manifest is not contiguous"):
        _ = load_control_migrations(_SQL_ROOT)


def test_schema_rejects_invalid_configuration_and_overlapping_runs(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)
    _insert_source(connection, "rsk")
    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO source_configuration_revisions (
                source_id, configuration_revision, configuration_sha256,
                configuration_json, created_at_ms
            ) VALUES ('rsk', 2, ?, 'not-json', ?)
            """,
            ("0" * 64, _NOW_MS),
        )

    _insert_running_run(connection, "0000000000000001", "a" * 32)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_running_run(connection, "0000000000000002", "b" * 32)


def test_schema_rejects_cross_source_provenance(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)
    _insert_source(connection, "rsk")
    _insert_source(connection, "baw")
    _insert_running_run(connection, "0000000000000001", "a" * 32)
    _ = connection.execute(
        """
        INSERT INTO blobs (sha256, size_bytes, media_type, created_at_ms)
        VALUES (?, 2, 'application/json', ?)
        """,
        ("1" * 64, _NOW_MS),
    )

    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO observations (
                observation_id, run_id, source_id, observation_kind,
                source_identity, response_sha256, observed_at_ms,
                metadata_json, state_revision
            ) VALUES (?, ?, 'baw', 'catalog', 'catalog', ?, ?, '{}', 1)
            """,
            ("observation-0001", "0000000000000001", "1" * 64, _NOW_MS),
        )

    _ = connection.execute(
        """
        INSERT INTO observations (
            observation_id, run_id, source_id, observation_kind,
            source_identity, response_sha256, observed_at_ms,
            metadata_json, state_revision
        ) VALUES (?, ?, 'rsk', 'catalog', 'catalog', ?, ?, '{}', 1)
        """,
        ("observation-0001", "0000000000000001", "1" * 64, _NOW_MS),
    )
    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO source_changes (
                change_id, run_id, source_id, observation_id,
                change_category, summary_json, state_revision, created_at_ms
            ) VALUES (?, ?, 'baw', ?, 'metadata', '{}', 1, ?)
            """,
            (
                "change-000000001",
                "0000000000000001",
                "observation-0001",
                _NOW_MS,
            ),
        )


def test_schema_rejects_invalid_epoch_milliseconds_and_deadline_order(
    connection: sqlite3.Connection,
) -> None:
    _ = apply_control_migrations(connection, _SQL_ROOT)
    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO sources (
                source_id, watcher_type, enabled, current_configuration_revision,
                fencing_epoch, created_at_ms, updated_at_ms
            ) VALUES ('invalid', 'rsk', 0, NULL, 0, -1, -1)
            """
        )

    _insert_source(connection, "rsk")
    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO watcher_runs (
                run_id, source_id, run_mode, status, claim_token, fencing_epoch,
                configuration_revision, scheduled_at_ms, started_at_ms,
                deadline_at_ms
            ) VALUES (?, 'rsk', 'manual', 'running', ?, 1, 1, ?, ?, ?)
            """,
            (
                "0000000000000001",
                "a" * 32,
                _NOW_MS,
                _NOW_MS,
                _NOW_MS - 1,
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        _ = connection.execute(
            """
            INSERT INTO watcher_runs (
                run_id, source_id, run_mode, status, claim_token, fencing_epoch,
                configuration_revision, scheduled_at_ms, started_at_ms,
                deadline_at_ms, finished_at_ms, error_category
            ) VALUES (?, 'rsk', 'manual', 'expired', ?, 1, 1, ?, ?, ?, ?, 'deadline')
            """,
            (
                "0000000000000002",
                "b" * 32,
                _NOW_MS,
                _NOW_MS,
                _NOW_MS + 100,
                _NOW_MS + 99,
            ),
        )


def _copy_sql_root(tmp_path: Path) -> Path:
    destination = tmp_path / "sqlite"
    _ = shutil.copytree(_SQL_ROOT, destination)
    return destination


def _dictionary_row_factory(
    cursor: sqlite3.Cursor,
    row: tuple[object, ...],
) -> dict[str, object]:
    return {
        description[0]: value
        for description, value in zip(cursor.description, row, strict=True)
    }


def _add_failing_second_migration(sql_root: Path) -> None:
    migration_root = sql_root / "migrations"
    _ = migration_root.joinpath("0002_failing_probe.up.sql").write_text(
        """
PRAGMA user_version = 2;
CREATE TABLE migration_probe (value INTEGER) STRICT;
UPDATE control_state SET schema_version = 2 WHERE singleton_id = 1;
INSERT INTO table_that_does_not_exist (value) VALUES (1);
""".lstrip(),
        encoding="utf-8",
    )
    _ = migration_root.joinpath("0002_failing_probe.down.sql").write_text(
        """
DROP TABLE migration_probe;
UPDATE control_state SET schema_version = 1 WHERE singleton_id = 1;
PRAGMA user_version = 1;
""".lstrip(),
        encoding="utf-8",
    )


def _add_forbidden_second_migration(
    sql_root: Path,
    name: str,
    forbidden_sql: str,
) -> None:
    migration_root = sql_root / "migrations"
    _ = migration_root.joinpath(f"0002_{name}.up.sql").write_text(
        f"""
PRAGMA user_version = 2;
CREATE TABLE committed_probe (value INTEGER) STRICT;
{forbidden_sql}
INSERT INTO table_that_does_not_exist (value) VALUES (1);
""".lstrip(),
        encoding="utf-8",
    )
    _ = migration_root.joinpath(f"0002_{name}.down.sql").write_text(
        """
DROP TABLE committed_probe;
UPDATE control_state SET schema_version = 1 WHERE singleton_id = 1;
PRAGMA user_version = 1;
""".lstrip(),
        encoding="utf-8",
    )


def _install_unknown_additive_migration(
    connection: sqlite3.Connection,
    minimum_reader_version: int,
) -> None:
    _ = connection.execute("BEGIN IMMEDIATE")
    _ = connection.execute("CREATE TABLE additive_probe (value INTEGER) STRICT")
    _ = connection.execute("PRAGMA user_version = 2")
    _ = connection.execute(
        "UPDATE control_state SET schema_version = 2 WHERE singleton_id = 1"
    )
    _ = connection.execute(
        """
        INSERT INTO control_schema_migrations (
            version, name, up_sha256, down_sha256,
            minimum_reader_version, applied_at_ms
        ) VALUES (2, 'additive_probe', ?, ?, ?, ?)
        """,
        ("1" * 64, "2" * 64, minimum_reader_version, _NOW_MS),
    )
    connection.commit()


def _insert_running_run(
    connection: sqlite3.Connection,
    run_id: str,
    claim_token: str,
) -> None:
    _ = connection.execute(
        """
        INSERT INTO watcher_runs (
            run_id, source_id, run_mode, status, claim_token, fencing_epoch,
            configuration_revision, scheduled_at_ms, started_at_ms, deadline_at_ms
        ) VALUES (?, 'rsk', 'manual', 'running', ?, 1, 1, ?, ?, ?)
        """,
        (run_id, claim_token, _NOW_MS, _NOW_MS, _NOW_MS + 300_000),
    )


def _insert_source(connection: sqlite3.Connection, source_id: str) -> None:
    _ = connection.execute("BEGIN")
    _ = connection.execute(
        """
        INSERT INTO sources (
            source_id, watcher_type, enabled, current_configuration_revision,
            fencing_epoch, created_at_ms, updated_at_ms
        ) VALUES (?, ?, 0, NULL, 0, ?, ?)
        """,
        (source_id, source_id, _NOW_MS, _NOW_MS),
    )
    _ = connection.execute(
        """
        INSERT INTO source_configuration_revisions (
            source_id, configuration_revision, configuration_sha256,
            configuration_json, created_at_ms
        ) VALUES (?, 1, ?, '{}', ?)
        """,
        (source_id, "0" * 64, _NOW_MS),
    )
    _ = connection.execute(
        """
        UPDATE sources
        SET enabled = 1, current_configuration_revision = 1
        WHERE source_id = ?
        """,
        (source_id,),
    )
    connection.commit()


def _schema_objects(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        """
        SELECT type, name, tbl_name, sql
        FROM sqlite_schema
        WHERE name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall()


def _control_state(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT singleton_id, state_revision, schema_version FROM control_state"
    ).fetchall()


def _migration_identities(
    connection: sqlite3.Connection,
) -> list[tuple[object, ...]]:
    return connection.execute(
        """
        SELECT version, name, up_sha256, down_sha256, minimum_reader_version
        FROM control_schema_migrations
        ORDER BY version
        """
    ).fetchall()
