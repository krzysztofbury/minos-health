import re
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import cast

_MAX_MIGRATION_BYTES = 1024 * 1024
_MIGRATION_HISTORY_TABLE = "control_schema_migrations"
_MIGRATION_SPECS: tuple[tuple[int, str, int], ...] = ((1, "initial_control_schema", 1),)
CONTROL_SCHEMA_VERSION = _MIGRATION_SPECS[-1][0]
_FORBIDDEN_SQL_KEYWORDS = frozenset(
    {
        "ATTACH",
        "BEGIN",
        "COMMIT",
        "DETACH",
        "END",
        "RELEASE",
        "ROLLBACK",
        "SAVEPOINT",
        "VACUUM",
    }
)
_LEADING_SQL_COMMENTS = re.compile(r"\A(?:\s|--[^\n]*(?:\n|\Z)|/\*.*?\*/)*", re.DOTALL)
_SHA256_PATTERN = re.compile(r"\A[0-9a-f]{64}\Z")
_MIGRATION_NAME_PATTERN = re.compile(r"\A[a-z][a-z0-9_]{0,99}\Z")
_USER_VERSION_PRAGMA = re.compile(
    r"\APRAGMA\s+user_version\s*=\s*[0-9]+\s*;\s*\Z", re.IGNORECASE
)


class MigrationError(RuntimeError):
    """The control schema cannot be proven safe to migrate or read."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    minimum_reader_version: int
    up_sql: str
    down_sql: str
    up_sha256: str
    down_sha256: str


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    minimum_reader_version: int
    up_sha256: str
    down_sha256: str


@dataclass(frozen=True, slots=True)
class MigrationResult:
    schema_version: int
    applied_versions: tuple[int, ...]
    forward_compatible: bool


def default_control_sql_root() -> Traversable:
    packaged_root = resources.files("minos").joinpath("database/control/sqlite")
    if packaged_root.is_dir():
        return packaged_root

    source_root = Path(__file__).resolve().parents[5] / "database/control/sqlite"
    if source_root.is_dir():
        return source_root
    raise MigrationError("control SQL resources are not installed")


def load_control_migrations(
    sql_root: Traversable | None = None,
) -> tuple[Migration, ...]:
    root = sql_root or default_control_sql_root()
    _validate_migration_manifest()
    migration_root = root.joinpath("migrations")
    if not migration_root.is_dir():
        raise MigrationError("control migration directory is missing")

    expected_names = {
        f"{version:04d}_{name}.{direction}.sql"
        for version, name, _ in _MIGRATION_SPECS
        for direction in ("up", "down")
    }
    actual_names = {
        child.name for child in migration_root.iterdir() if child.name.endswith(".sql")
    }
    if actual_names != expected_names:
        raise MigrationError("control migration files do not match the manifest")

    return tuple(
        _load_migration(migration_root, version, name, minimum_reader_version)
        for version, name, minimum_reader_version in _MIGRATION_SPECS
    )


def apply_control_migrations(
    connection: sqlite3.Connection,
    sql_root: Traversable | None = None,
) -> MigrationResult:
    migrations = load_control_migrations(sql_root)
    _prepare_connection(connection)
    applied = _read_applied_migrations(connection)
    _validate_applied_migrations(applied, migrations)

    latest_applied = applied[-1].version if applied else 0
    if latest_applied > CONTROL_SCHEMA_VERSION:
        _validate_database_version(connection, latest_applied)
        _enable_query_only(connection)
        return MigrationResult(latest_applied, (), True)

    pending = tuple(item for item in migrations if item.version > latest_applied)
    if not pending:
        _validate_database_version(connection, latest_applied)
        return MigrationResult(latest_applied, (), False)

    _apply_pending_migrations(connection, pending)
    _validate_database_version(connection, pending[-1].version)
    return MigrationResult(
        schema_version=pending[-1].version,
        applied_versions=tuple(item.version for item in pending),
        forward_compatible=False,
    )


def rollback_control_migrations_for_testing(
    connection: sqlite3.Connection,
    target_version: int,
    sql_root: Traversable | None = None,
) -> MigrationResult:
    migrations = load_control_migrations(sql_root)
    _prepare_connection(connection)
    applied = _read_applied_migrations(connection)
    _validate_applied_migrations(applied, migrations)
    current_version = applied[-1].version if applied else 0
    if current_version > CONTROL_SCHEMA_VERSION:
        raise MigrationError("testing rollback cannot cross an unknown migration")
    if target_version < 0 or target_version > current_version:
        raise MigrationError("testing rollback target is outside the applied range")

    selected = tuple(
        item
        for item in reversed(migrations)
        if target_version < item.version <= current_version
    )
    if selected:
        _rollback_selected_migrations(
            connection,
            selected,
            target_version,
            migrations,
        )
    if target_version > 0:
        _validate_database_version(connection, target_version)
    else:
        _validate_empty_database(connection)
    return MigrationResult(
        schema_version=target_version,
        applied_versions=tuple(item.version for item in selected),
        forward_compatible=False,
    )


def _load_migration(
    root: Traversable,
    version: int,
    name: str,
    minimum_reader_version: int,
) -> Migration:
    up_bytes = _read_bounded(root.joinpath(f"{version:04d}_{name}.up.sql"))
    down_bytes = _read_bounded(root.joinpath(f"{version:04d}_{name}.down.sql"))
    up_sql = _decode_sql(up_bytes)
    down_sql = _decode_sql(down_bytes)
    _ = tuple(_iter_sql_statements(up_sql))
    _ = tuple(_iter_sql_statements(down_sql))
    return Migration(
        version=version,
        name=name,
        minimum_reader_version=minimum_reader_version,
        up_sql=up_sql,
        down_sql=down_sql,
        up_sha256=sha256(up_bytes).hexdigest(),
        down_sha256=sha256(down_bytes).hexdigest(),
    )


def _validate_migration_manifest() -> None:
    versions = tuple(item[0] for item in _MIGRATION_SPECS)
    if versions != tuple(range(1, CONTROL_SCHEMA_VERSION + 1)):
        raise MigrationError("control migration manifest is not contiguous")
    for version, name, minimum_reader_version in _MIGRATION_SPECS:
        if _MIGRATION_NAME_PATTERN.fullmatch(name) is None:
            raise MigrationError("control migration manifest has an invalid name")
        if minimum_reader_version < 1 or minimum_reader_version > version:
            raise MigrationError("control migration has an invalid reader version")


def _read_bounded(source: Traversable) -> bytes:
    if not source.is_file():
        raise MigrationError(f"missing migration file: {source.name}")
    content = source.read_bytes()
    if not content or len(content) > _MAX_MIGRATION_BYTES:
        raise MigrationError(f"migration file has an invalid size: {source.name}")
    return content


def _decode_sql(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        raise MigrationError("migration SQL must not contain a UTF-8 byte-order mark")
    try:
        return content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise MigrationError("migration SQL must be valid UTF-8") from error


def _iter_sql_statements(script: str) -> Iterator[str]:
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                _validate_sql_statement(statement)
                yield statement
    if _strip_leading_comments(buffer):
        raise MigrationError("migration SQL ends with an incomplete statement")


def _validate_sql_statement(statement: str) -> None:
    normalized = _strip_leading_comments(statement)
    if not normalized:
        return
    keyword = normalized.split(maxsplit=1)[0].rstrip(";").upper()
    if keyword in _FORBIDDEN_SQL_KEYWORDS:
        raise MigrationError(f"migration contains forbidden {keyword} statement")
    if keyword == "PRAGMA" and _USER_VERSION_PRAGMA.fullmatch(normalized) is None:
        raise MigrationError("migrations may set only PRAGMA user_version")


def _strip_leading_comments(statement: str) -> str:
    return _LEADING_SQL_COMMENTS.sub("", statement).strip()


def _prepare_connection(connection: sqlite3.Connection) -> None:
    if connection.in_transaction:
        raise MigrationError("migration requires a connection outside a transaction")
    if connection.row_factory not in (None, sqlite3.Row):
        raise MigrationError(
            "migration requires the default or sqlite3.Row row factory"
        )
    text_factory = cast(object, connection.text_factory)
    if text_factory is not str:
        raise MigrationError("migration requires the default string text factory")
    _ = connection.execute("PRAGMA foreign_keys = ON")
    foreign_keys = cast(
        tuple[int] | None,
        connection.execute("PRAGMA foreign_keys").fetchone(),
    )
    if foreign_keys is None or foreign_keys[0] != 1:
        raise MigrationError("SQLite foreign-key enforcement is unavailable")


def _enable_query_only(connection: sqlite3.Connection) -> None:
    _ = connection.execute("PRAGMA query_only = ON")
    query_only = cast(
        tuple[int] | None,
        connection.execute("PRAGMA query_only").fetchone(),
    )
    if query_only is None or query_only[0] != 1:
        raise MigrationError("forward-compatible database cannot be made read-only")


def _read_applied_migrations(
    connection: sqlite3.Connection,
) -> tuple[AppliedMigration, ...]:
    exists = cast(
        tuple[int] | None,
        connection.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = ?",
            (_MIGRATION_HISTORY_TABLE,),
        ).fetchone(),
    )
    if exists is None:
        _validate_empty_database(connection)
        return ()
    try:
        rows = cast(
            list[tuple[int, str, int, str, str]],
            connection.execute(
                """
                SELECT version, name, minimum_reader_version, up_sha256, down_sha256
                FROM control_schema_migrations
                ORDER BY version
                """
            ).fetchall(),
        )
    except sqlite3.Error as error:
        raise MigrationError("migration history cannot be read") from error
    return tuple(_parse_applied_migration(row) for row in rows)


def _parse_applied_migration(row: tuple[int, str, int, str, str]) -> AppliedMigration:
    version, name, minimum_reader_version, up_sha256, down_sha256 = row
    return AppliedMigration(
        version=version,
        name=name,
        minimum_reader_version=minimum_reader_version,
        up_sha256=up_sha256,
        down_sha256=down_sha256,
    )


def _validate_applied_migrations(
    applied: tuple[AppliedMigration, ...],
    known: tuple[Migration, ...],
) -> None:
    known_by_version = {item.version: item for item in known}
    expected_versions = tuple(range(1, len(applied) + 1))
    if tuple(item.version for item in applied) != expected_versions:
        raise MigrationError("applied migration history is not contiguous")
    for item in applied:
        if not _SHA256_PATTERN.fullmatch(item.up_sha256):
            raise MigrationError("applied migration has an invalid up checksum")
        if not _SHA256_PATTERN.fullmatch(item.down_sha256):
            raise MigrationError("applied migration has an invalid down checksum")
        expected = known_by_version.get(item.version)
        if expected is None:
            if item.minimum_reader_version > CONTROL_SCHEMA_VERSION:
                raise MigrationError("database schema is not forward-readable")
            continue
        if item != _as_applied(expected):
            raise MigrationError("an applied migration differs from the bundled SQL")


def _as_applied(migration: Migration) -> AppliedMigration:
    return AppliedMigration(
        version=migration.version,
        name=migration.name,
        minimum_reader_version=migration.minimum_reader_version,
        up_sha256=migration.up_sha256,
        down_sha256=migration.down_sha256,
    )


def _apply_pending_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...],
) -> None:
    try:
        _ = connection.execute("BEGIN IMMEDIATE")
        for migration in migrations:
            _execute_script(connection, migration.up_sql)
            _ = connection.execute(
                """
                INSERT INTO control_schema_migrations (
                    version, name, up_sha256, down_sha256,
                    minimum_reader_version, applied_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    migration.version,
                    migration.name,
                    migration.up_sha256,
                    migration.down_sha256,
                    migration.minimum_reader_version,
                    time.time_ns() // 1_000_000,
                ),
            )
        _validate_database_version(connection, migrations[-1].version)
        connection.commit()
    except (MigrationError, sqlite3.Error) as error:
        if connection.in_transaction:
            connection.rollback()
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(
            "control migrations failed and were rolled back"
        ) from error


def _rollback_selected_migrations(
    connection: sqlite3.Connection,
    migrations: tuple[Migration, ...],
    target_version: int,
    known_migrations: tuple[Migration, ...],
) -> None:
    try:
        _ = connection.execute("BEGIN IMMEDIATE")
        for migration in migrations:
            _ = connection.execute(
                "DELETE FROM control_schema_migrations WHERE version = ?",
                (migration.version,),
            )
            _execute_script(
                connection,
                migration.down_sql,
                allow_history_writes=True,
            )
        retained = _read_applied_migrations(connection)
        _validate_applied_migrations(retained, known_migrations)
        retained_version = retained[-1].version if retained else 0
        if retained_version != target_version:
            raise MigrationError(
                "testing rollback retained an unexpected migration set"
            )
        if target_version > 0:
            _validate_database_version(connection, target_version)
        else:
            _validate_empty_database(connection)
        connection.commit()
    except (MigrationError, sqlite3.Error) as error:
        if connection.in_transaction:
            connection.rollback()
        if isinstance(error, MigrationError):
            raise
        raise MigrationError("testing rollback failed and was rolled back") from error


def _execute_script(
    connection: sqlite3.Connection,
    script: str,
    *,
    allow_history_writes: bool = False,
) -> None:
    authorizer = partial(
        _migration_authorizer,
        allow_history_writes=allow_history_writes,
    )
    connection.set_authorizer(authorizer)
    try:
        for statement in _iter_sql_statements(script):
            _ = connection.execute(statement)
    finally:
        connection.set_authorizer(None)


def _migration_authorizer(
    action_code: int,
    argument_one: str | None,
    _argument_two: str | None,
    _database_name: str | None,
    _trigger_name: str | None,
    *,
    allow_history_writes: bool,
) -> int:
    forbidden_actions = {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_TRANSACTION,
    }
    if action_code in forbidden_actions:
        return sqlite3.SQLITE_DENY
    if action_code == sqlite3.SQLITE_PRAGMA:
        if argument_one is None or argument_one.lower() != "user_version":
            return sqlite3.SQLITE_DENY
    history_writes = {
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
    }
    if (
        not allow_history_writes
        and action_code in history_writes
        and argument_one == _MIGRATION_HISTORY_TABLE
    ):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _validate_database_version(
    connection: sqlite3.Connection,
    expected_version: int,
) -> None:
    user_version = cast(
        tuple[int] | None,
        connection.execute("PRAGMA user_version").fetchone(),
    )
    state_rows = cast(
        list[tuple[int]],
        connection.execute(
            "SELECT schema_version FROM control_state WHERE singleton_id = 1"
        ).fetchall(),
    )
    if user_version is None or user_version[0] != expected_version:
        raise MigrationError("PRAGMA user_version differs from migration history")
    state_versions = tuple(row[0] for row in state_rows)
    if state_versions != (expected_version,):
        raise MigrationError(
            "control_state schema version differs from migration history"
        )


def _validate_empty_database(connection: sqlite3.Connection) -> None:
    objects = cast(
        tuple[str] | None,
        connection.execute(
            """
            SELECT name
            FROM sqlite_schema
            WHERE name NOT LIKE 'sqlite_%'
              AND type IN ('table', 'index', 'view', 'trigger')
            LIMIT 1
            """
        ).fetchone(),
    )
    user_version = cast(
        tuple[int] | None,
        connection.execute("PRAGMA user_version").fetchone(),
    )
    if objects is not None or user_version is None or user_version[0] != 0:
        raise MigrationError("database has schema objects without migration history")
