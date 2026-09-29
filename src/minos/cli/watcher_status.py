import os
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

_STATUS_FORMAT_VERSION = 1
_CONTROL_SCHEMA_VERSION = 4
_MAX_SOURCES = 128
_MAX_SCHEDULES_PER_SOURCE = 2
_MAX_RECENT_CHANGES_PER_SOURCE = 10
_MAX_OPEN_INCIDENTS = 10
_MAX_PRODUCTS_PER_SOURCE = 10
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_MAX_QUERY_SECONDS = 10.0
_PROGRESS_INTERVAL_INSTRUCTIONS = 1000
_MAX_PROGRESS_CALLBACKS = 100_000
# Setup, BEGIN, validation, source listing, and global incidents use nine
# executes; each of the at most 128 sources uses exactly six more.
_FIXED_SQL_EXECUTES = 9
_SQL_EXECUTES_PER_SOURCE = 6
_MAX_SQL_EXECUTES = _FIXED_SQL_EXECUTES + (_MAX_SOURCES * _SQL_EXECUTES_PER_SOURCE)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Immutable reader metadata duplicated from database/control/sqlite/migrations/*.sql
# and schema.sql so this command never imports migration execution/runtime code.
_EXPECTED_MIGRATIONS: tuple[tuple[int, str, str, str, int], ...] = (
    (
        1,
        "initial_control_schema",
        "be2adaa27b180d1b6f5677887b5e649a536cdd81b13a252018ab43b8cb63832b",
        "936380d5b230f7521bd409d18022af96d83b1befccde676d52cee404f8ba116c",
        1,
    ),
    (
        2,
        "durable_schedule_obligations",
        "f965414badfdc0f8d2be1366cc8ef1f1f9ab11c0f5347e99689bd7de4a32b402",
        "4ede51a550339ddca1201c0cd5a045e6de4a33f9745ecdf26a9df5ff4b56bf90",
        2,
    ),
    (
        3,
        "retryable_schedule_obligations",
        "05acc0a63711bc39b2921d30e882548cca3a5c42c6943886fa3ff3ce6a57a075",
        "9431ca930c74c5d8a61652fde7b93eee46f50b2000857d99cbe3db2a09ef4a90",
        3,
    ),
    (
        4,
        "baw_dispatch_outbox",
        "dadacbe0259f8b3156bf67fb88f44a767d0ba9856d7925356b87c61f1d64cc91",
        "ea5f5712689f677c573a3001644c9b1271aa6eb3061182198bcd40edfb45ecec",
        4,
    ),
)

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)
type JsonObject = dict[str, JsonValue]


class WatcherRuntimeError(RuntimeError):
    """Watcher status cannot be read without violating its safety contract."""


class _StatusConnection(sqlite3.Connection):
    deadline: float = 0.0
    execute_count: int = 0


def resolve_status_database(database: Path | None) -> Path:
    if database is not None:
        return database
    configured = os.environ.get("MINOS_WATCHER_DATABASE")
    if configured:
        return Path(configured)
    raise WatcherRuntimeError("watcher status database is not configured")


def read_watcher_status(database: Path, source_id: str | None = None) -> JsonObject:
    if source_id is not None and not 1 <= len(source_id) <= 64:
        raise WatcherRuntimeError("watcher source does not exist")
    connection: _StatusConnection | None = None
    try:
        connection = _open_read_only(database)
        _ = _execute(connection, "BEGIN")
        state_revision, updated_at_ms = _validate_snapshot(connection)
        sources = _read_sources(connection, source_id)
        global_incidents, global_incidents_truncated = _read_incidents(connection, None)
        return {
            "format_version": _STATUS_FORMAT_VERSION,
            "schema_version": _CONTROL_SCHEMA_VERSION,
            "state_revision": state_revision,
            "updated_at": _timestamp(updated_at_ms),
            "open_incidents": global_incidents,
            "open_incidents_truncated": global_incidents_truncated,
            "sources": sources,
        }
    except WatcherRuntimeError:
        raise
    except OSError, sqlite3.Error, TypeError, ValueError, OverflowError:
        raise WatcherRuntimeError("watcher status is unavailable") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                raise WatcherRuntimeError("watcher status is unavailable") from None


def format_watcher_status(snapshot: JsonObject) -> str:
    lines = [
        "Watcher status",
        f"format version: {_display(snapshot.get('format_version'))}",
        f"schema version: {_display(snapshot.get('schema_version'))}",
        f"state revision: {_display(snapshot.get('state_revision'))}",
        f"updated at: {_display(snapshot.get('updated_at'))}",
    ]
    incidents = snapshot.get("open_incidents")
    lines.extend(
        _format_collection(
            "global open incidents",
            incidents,
            0,
            _truncated(snapshot.get("open_incidents_truncated")),
        )
    )
    sources = snapshot.get("sources")
    if not isinstance(sources, list):
        raise WatcherRuntimeError("watcher status snapshot is invalid")
    lines.append(f"sources: {len(sources)}")
    for source in sources:
        if not isinstance(source, dict):
            raise WatcherRuntimeError("watcher status snapshot is invalid")
        lines.extend(_format_source(source))
    return "\n".join(lines)


def _open_read_only(database: Path) -> _StatusConnection:
    """Open without logical writes or main DB creation.

    SQLite may still create or manage a shared-memory sidecar while reading a
    live WAL database; filesystem sidecar immutability is not part of this API.
    """
    uri = f"{database.absolute().as_uri()}?mode=ro"
    connection = sqlite3.connect(
        uri,
        uri=True,
        isolation_level=None,
        timeout=5.0,
        factory=_StatusConnection,
    )
    try:
        connection.deadline = time.monotonic() + _MAX_QUERY_SECONDS
        connection.execute_count = 0
        progress_callbacks = 0

        def enforce_query_bound() -> int:
            nonlocal progress_callbacks
            progress_callbacks += 1
            if progress_callbacks > _MAX_PROGRESS_CALLBACKS:
                return 1
            return int(time.monotonic() >= connection.deadline)

        connection.set_progress_handler(
            enforce_query_bound,
            _PROGRESS_INTERVAL_INSTRUCTIONS,
        )
        _ = _execute(connection, "PRAGMA query_only = ON")
        row = _fetch_row(connection, "PRAGMA query_only")
        if row != (1,):
            raise WatcherRuntimeError("watcher status cannot enforce read-only access")
        return connection
    except BaseException as error:
        try:
            connection.close()
        except sqlite3.Error:
            error.add_note("watcher status connection cleanup also failed")
        raise


def _validate_snapshot(connection: _StatusConnection) -> tuple[int, int]:
    if _fetch_rows(connection, "PRAGMA quick_check(1)") != [("ok",)]:
        raise WatcherRuntimeError("watcher status database is corrupt")
    version_row = _fetch_row(connection, "PRAGMA user_version")
    if version_row != (_CONTROL_SCHEMA_VERSION,):
        raise WatcherRuntimeError("watcher status schema is unsupported")
    migration_rows = _fetch_rows(
        connection,
        """
        SELECT version, name, up_sha256, down_sha256, minimum_reader_version
        FROM control_schema_migrations
        ORDER BY version
        LIMIT ?
        """,
        (len(_EXPECTED_MIGRATIONS) + 1,),
    )
    if migration_rows != list(_EXPECTED_MIGRATIONS):
        raise WatcherRuntimeError("watcher status schema is unsupported")
    rows = _fetch_rows(
        connection,
        """
        SELECT state_revision, schema_version, updated_at_ms
        FROM control_state
        WHERE singleton_id = 1
        LIMIT 2
        """,
    )
    if len(rows) != 1 or rows[0][1] != _CONTROL_SCHEMA_VERSION:
        raise WatcherRuntimeError("watcher status schema is unsupported")
    state_revision = _integer(rows[0][0], minimum=0)
    updated_at_ms = _timestamp_integer(rows[0][2])
    return state_revision, updated_at_ms


def _read_sources(
    connection: _StatusConnection,
    source_id: str | None,
) -> list[JsonValue]:
    if source_id is None:
        rows = _fetch_rows(
            connection,
            """
            SELECT source_id, watcher_type, enabled,
                   current_configuration_revision, fencing_epoch
            FROM sources
            ORDER BY source_id
            LIMIT ?
            """,
            (_MAX_SOURCES + 1,),
        )
        if len(rows) > _MAX_SOURCES:
            raise WatcherRuntimeError("watcher status source limit is exceeded")
    else:
        rows = _fetch_rows(
            connection,
            """
            SELECT source_id, watcher_type, enabled,
                   current_configuration_revision, fencing_epoch
            FROM sources
            WHERE source_id = ?
            LIMIT 2
            """,
            (source_id,),
        )
        if len(rows) != 1:
            raise WatcherRuntimeError("watcher source does not exist")
    return [_source_snapshot(connection, row) for row in rows]


def _source_snapshot(
    connection: _StatusConnection,
    row: tuple[object, ...],
) -> JsonValue:
    source_id = _text(row[0])
    recent_changes, recent_changes_truncated = _read_changes(connection, source_id)
    open_incidents, open_incidents_truncated = _read_incidents(connection, source_id)
    products, products_truncated = _read_products(connection, source_id)
    return {
        "source_id": source_id,
        "watcher_type": _text(row[1]),
        "enabled": _boolean(row[2]),
        "configuration_revision": _optional_integer(row[3], minimum=1),
        "fencing_epoch": _integer(row[4], minimum=0),
        "schedules": _read_schedules(connection, source_id),
        "latest_run": _read_run(connection, source_id, successful_only=False),
        "latest_successful_run": _read_run(connection, source_id, successful_only=True),
        "recent_changes": recent_changes,
        "recent_changes_truncated": recent_changes_truncated,
        "open_incidents": open_incidents,
        "open_incidents_truncated": open_incidents_truncated,
        "verified_downloaded_products": products,
        "verified_downloaded_products_truncated": products_truncated,
    }


def _read_schedules(
    connection: _StatusConnection,
    source_id: str,
) -> list[JsonValue]:
    rows = _fetch_rows(
        connection,
        """
        SELECT run_mode, cron_expression, next_due_at_ms,
               misfire_grace_seconds, retry_max_attempts,
               retry_base_seconds, retry_max_seconds,
               pending_scheduled_at_ms, pending_run_mode,
               retry_not_before_ms, retry_failure_count, updated_at_ms
        FROM source_schedules
        WHERE source_id = ?
        ORDER BY run_mode
        LIMIT ?
        """,
        (source_id, _MAX_SCHEDULES_PER_SOURCE + 1),
    )
    if len(rows) > _MAX_SCHEDULES_PER_SOURCE:
        raise WatcherRuntimeError("watcher status schedule limit is exceeded")
    return [_schedule_snapshot(row) for row in rows]


def _schedule_snapshot(row: tuple[object, ...]) -> JsonValue:
    return {
        "run_mode": _text(row[0]),
        "cron_expression": _text(row[1]),
        "next_due_at": _timestamp(_timestamp_integer(row[2])),
        "misfire_grace_seconds": _integer(row[3], minimum=1),
        "retry_max_attempts": _integer(row[4], minimum=1),
        "retry_base_seconds": _number(row[5]),
        "retry_max_seconds": _number(row[6]),
        "pending_scheduled_at": _optional_timestamp(row[7]),
        "pending_run_mode": _optional_text(row[8]),
        "retry_not_before": _optional_timestamp(row[9]),
        "retry_failure_count": _integer(row[10], minimum=0),
        "updated_at": _timestamp(_timestamp_integer(row[11])),
    }


def _read_run(
    connection: _StatusConnection,
    source_id: str,
    *,
    successful_only: bool,
) -> JsonValue:
    status_filter = "AND status = 'successful'" if successful_only else ""
    row = _fetch_row(
        connection,
        f"""
        SELECT run_id, run_mode, status, fencing_epoch, configuration_revision,
               scheduled_at_ms, started_at_ms, deadline_at_ms, committed_at_ms,
               finished_at_ms, committed_state_revision, error_category
        FROM watcher_runs
        WHERE source_id = ? {status_filter}
        ORDER BY started_at_ms DESC, run_id DESC
        LIMIT 1
        """,  # nosec B608 - the fragment is selected from a fixed boolean branch.
        (source_id,),
    )
    if row is None:
        return None
    return {
        "run_id": _text(row[0]),
        "run_mode": _text(row[1]),
        "status": _text(row[2]),
        "fencing_epoch": _integer(row[3], minimum=1),
        "configuration_revision": _integer(row[4], minimum=1),
        "scheduled_at": _timestamp(_timestamp_integer(row[5])),
        "started_at": _timestamp(_timestamp_integer(row[6])),
        "deadline_at": _timestamp(_timestamp_integer(row[7])),
        "committed_at": _optional_timestamp(row[8]),
        "finished_at": _optional_timestamp(row[9]),
        "committed_state_revision": _optional_integer(row[10], minimum=1),
        "error_category": _optional_text(row[11]),
    }


def _read_changes(
    connection: _StatusConnection,
    source_id: str,
) -> tuple[list[JsonValue], bool]:
    rows = _fetch_rows(
        connection,
        """
        SELECT change_id, run_id, observation_id, change_category,
               state_revision, created_at_ms
        FROM source_changes
        WHERE source_id = ?
        ORDER BY created_at_ms DESC, change_id DESC
        LIMIT ?
        """,
        (source_id, _MAX_RECENT_CHANGES_PER_SOURCE + 1),
    )
    truncated = len(rows) > _MAX_RECENT_CHANGES_PER_SOURCE
    return [
        {
            "change_id": _text(row[0]),
            "run_id": _text(row[1]),
            "observation_id": _text(row[2]),
            "category": _text(row[3]),
            "state_revision": _integer(row[4], minimum=1),
            "created_at": _timestamp(_timestamp_integer(row[5])),
        }
        for row in rows[:_MAX_RECENT_CHANGES_PER_SOURCE]
    ], truncated


def _read_incidents(
    connection: _StatusConnection,
    source_id: str | None,
) -> tuple[list[JsonValue], bool]:
    predicate = "source_id IS NULL" if source_id is None else "source_id = ?"
    parameters: tuple[object, ...] = (
        (_MAX_OPEN_INCIDENTS + 1,)
        if source_id is None
        else (source_id, _MAX_OPEN_INCIDENTS + 1)
    )
    rows = _fetch_rows(
        connection,
        f"""
        SELECT incident_id, run_id, incident_category,
               state_revision, created_at_ms
        FROM incidents
        WHERE {predicate} AND resolved_at_ms IS NULL
        ORDER BY created_at_ms DESC, incident_id DESC
        LIMIT ?
        """,  # nosec B608 - the predicate is selected from a fixed branch.
        parameters,
    )
    truncated = len(rows) > _MAX_OPEN_INCIDENTS
    return [
        {
            "incident_id": _text(row[0]),
            "run_id": _optional_text(row[1]),
            "category": _text(row[2]),
            "state_revision": _integer(row[3], minimum=1),
            "created_at": _timestamp(_timestamp_integer(row[4])),
        }
        for row in rows[:_MAX_OPEN_INCIDENTS]
    ], truncated


def _read_products(
    connection: _StatusConnection,
    source_id: str,
) -> tuple[list[JsonValue], bool]:
    rows = _fetch_rows(
        connection,
        """
        SELECT o.observation_id, o.run_id, o.observation_kind, o.observed_at_ms,
               o.state_revision, ob.ordinal, b.sha256, b.size_bytes, b.media_type,
               r.status
        FROM observations AS o
        JOIN watcher_runs AS r
          ON r.run_id = o.run_id AND r.source_id = o.source_id
        JOIN observation_blobs AS ob ON ob.observation_id = o.observation_id
        JOIN blobs AS b ON b.sha256 = ob.sha256
        WHERE o.source_id = ?
           AND ob.blob_role = 'product'
          AND r.status IN ('committed', 'heartbeat_pending', 'successful')
        ORDER BY o.observed_at_ms DESC, o.observation_id DESC,
                 ob.ordinal ASC, b.sha256 ASC
        LIMIT ?
        """,
        (source_id, _MAX_PRODUCTS_PER_SOURCE + 1),
    )
    truncated = len(rows) > _MAX_PRODUCTS_PER_SOURCE
    return [
        {
            "observation_id": _text(row[0]),
            "run_id": _text(row[1]),
            "observation_kind": _text(row[2]),
            "observed_at": _timestamp(_timestamp_integer(row[3])),
            "state_revision": _integer(row[4], minimum=1),
            "ordinal": _integer(row[5], minimum=0),
            "sha256": _digest(row[6]),
            "size_bytes": _integer(row[7], minimum=0),
            "media_type": _optional_text(row[8]),
            "run_status": _text(row[9]),
        }
        for row in rows[:_MAX_PRODUCTS_PER_SOURCE]
    ], truncated


def _format_source(source: dict[str, JsonValue]) -> list[str]:
    source_id = _display(source.get("source_id"))
    lines = [f"\nSource {source_id}"]
    scalar_fields = (
        "watcher_type",
        "enabled",
        "configuration_revision",
        "fencing_epoch",
        "latest_run",
        "latest_successful_run",
    )
    for field in scalar_fields:
        value = source.get(field)
        if isinstance(value, dict):
            lines.append(f"  {field.replace('_', ' ')}:")
            lines.extend(_format_mapping(value, 4))
        else:
            lines.append(f"  {field.replace('_', ' ')}: {_display(value)}")
    for field, truncated_field in (
        ("schedules", None),
        ("recent_changes", "recent_changes_truncated"),
        ("open_incidents", "open_incidents_truncated"),
        (
            "verified_downloaded_products",
            "verified_downloaded_products_truncated",
        ),
    ):
        truncated = (
            False
            if truncated_field is None
            else _truncated(source.get(truncated_field))
        )
        lines.extend(
            _format_collection(field.replace("_", " "), source.get(field), 2, truncated)
        )
    return lines


def _format_collection(
    label: str,
    value: JsonValue,
    indent: int,
    truncated: bool,
) -> list[str]:
    prefix = " " * indent
    if not isinstance(value, list):
        raise WatcherRuntimeError("watcher status snapshot is invalid")
    count = f"{len(value)}+" if truncated else str(len(value))
    lines = [f"{prefix}{label}: {count}"]
    for item in value:
        if not isinstance(item, dict):
            raise WatcherRuntimeError("watcher status snapshot is invalid")
        lines.append(f"{prefix}  -")
        lines.extend(_format_mapping(item, indent + 4))
    return lines


def _format_mapping(value: dict[str, JsonValue], indent: int) -> list[str]:
    prefix = " " * indent
    return [
        f"{prefix}{key.replace('_', ' ')}: {_display(item)}"
        for key, item in sorted(value.items())
    ]


def _display(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return "".join(
            character if character.isprintable() else ascii(character)[1:-1]
            for character in value
        )
    if isinstance(value, int | float):
        return str(value)
    raise WatcherRuntimeError("watcher status snapshot is invalid")


def _truncated(value: JsonValue) -> bool:
    if type(value) is not bool:
        raise WatcherRuntimeError("watcher status snapshot is invalid")
    return value


def _timestamp(value_ms: int) -> str:
    if value_ms < 1 or value_ms > _MAX_TIMESTAMP_MS:
        raise WatcherRuntimeError("watcher status contains invalid state")
    return (
        (_EPOCH + timedelta(milliseconds=value_ms))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _fetch_rows(
    connection: _StatusConnection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> list[tuple[object, ...]]:
    rows = cast(
        list[tuple[object, ...]],
        _execute(connection, query, parameters).fetchall(),
    )
    _enforce_deadline(connection)
    return rows


def _fetch_row(
    connection: _StatusConnection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> tuple[object, ...] | None:
    row = cast(
        tuple[object, ...] | None,
        _execute(connection, query, parameters).fetchone(),
    )
    _enforce_deadline(connection)
    return row


def _execute(
    connection: _StatusConnection,
    query: str,
    parameters: tuple[object, ...] = (),
) -> sqlite3.Cursor:
    _enforce_deadline(connection)
    connection.execute_count += 1
    if connection.execute_count > _MAX_SQL_EXECUTES:
        raise WatcherRuntimeError("watcher status query limit is exceeded")
    try:
        return connection.execute(query, parameters)
    finally:
        _enforce_deadline(connection)


def _enforce_deadline(connection: _StatusConnection) -> None:
    if time.monotonic() >= connection.deadline:
        raise WatcherRuntimeError("watcher status query deadline is exceeded")


def _timestamp_integer(value: object) -> int:
    return _integer(value, minimum=1, maximum=_MAX_TIMESTAMP_MS)


def _optional_timestamp(value: object) -> JsonValue:
    if value is None:
        return None
    return _timestamp(_timestamp_integer(value))


def _text(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WatcherRuntimeError("watcher status contains invalid state")
    return value


def _optional_text(value: object) -> JsonValue:
    return None if value is None else _text(value)


def _integer(
    value: object,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int or value < minimum:
        raise WatcherRuntimeError("watcher status contains invalid state")
    if maximum is not None and value > maximum:
        raise WatcherRuntimeError("watcher status contains invalid state")
    return value


def _optional_integer(value: object, *, minimum: int) -> JsonValue:
    return None if value is None else _integer(value, minimum=minimum)


def _boolean(value: object) -> bool:
    if type(value) is not int:
        raise WatcherRuntimeError("watcher status contains invalid state")
    if value not in (0, 1):
        raise WatcherRuntimeError("watcher status contains invalid state")
    return bool(value)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WatcherRuntimeError("watcher status contains invalid state")
    number = float(value)
    if number <= 0:
        raise WatcherRuntimeError("watcher status contains invalid state")
    return number


def _digest(value: object) -> str:
    digest = _text(value)
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise WatcherRuntimeError("watcher status contains invalid state")
    return digest
