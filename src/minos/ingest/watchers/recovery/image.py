import fcntl
import hashlib
import os
import secrets
import sqlite3
import stat
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeIs, cast, final
from urllib.parse import quote

from minos.ingest.watchers.control import (
    CONTROL_SCHEMA_VERSION,
    ControlSchemaObject,
    ControlSnapshotError,
    ControlStore,
    MigrationError,
    migration_set_sha256,
    validate_control_database,
    validate_control_database_with_schema_extension,
)
from minos.ingest.watchers.recovery.keys import parse_recovery_key, recovery_key
from minos.ingest.watchers.recovery.models import (
    MAX_RECOVERY_IMAGE_SIZE_BYTES,
    RecoveryClass,
    RecoveryImageLease,
    RecoveryImageMetadata,
    RecoveryImageRequest,
    RecoveryStagingError,
    RecoveryVerificationError,
    StagedRecoveryImage,
)

_STAGING_PREFIX = ".minos-recovery-"
_MAX_CREATE_ATTEMPTS = 10
_MAX_STALE_FILES = 16
_HASH_CHUNK_BYTES = 1024 * 1024
_MAX_TIMESTAMP_MS = 253_402_300_799_999
_MAX_CREATION_CLOCK_SKEW_MS = 5 * 60 * 1000
_DEFAULT_BACKUP_TIMEOUT_SECONDS = 300.0
_MAX_BACKUP_TIMEOUT_SECONDS = 900.0
_RECOVERY_METADATA_TABLE = "recovery_image_metadata"
_RECOVERY_METADATA_SQL = """CREATE TABLE recovery_image_metadata (
    singleton_id INTEGER PRIMARY KEY,
    recovery_class TEXT NOT NULL,
    state_revision INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    migration_set_sha256 TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL,
    run_id TEXT,
    committed_state_revision INTEGER,
    CHECK (singleton_id = 1),
    CHECK (recovery_class IN ('run', 'daily', 'weekly', 'monthly')),
    CHECK (state_revision >= 0),
    CHECK (schema_version > 0),
    CHECK (
        length(migration_set_sha256) = 64
        AND migration_set_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    CHECK (created_at_ms BETWEEN 1 AND 253402300799999),
    CHECK (run_id IS NULL OR length(run_id) BETWEEN 16 AND 128),
    CHECK (committed_state_revision IS NULL OR committed_state_revision > 0),
    CHECK (
        (recovery_class = 'run' AND run_id IS NOT NULL
            AND committed_state_revision IS NOT NULL
            AND committed_state_revision <= state_revision)
        OR (recovery_class != 'run' AND run_id IS NULL
            AND committed_state_revision IS NULL)
    )
) STRICT"""
_RECOVERY_SCHEMA_EXTENSION: tuple[ControlSchemaObject, ...] = (
    (
        "table",
        _RECOVERY_METADATA_TABLE,
        _RECOVERY_METADATA_TABLE,
        _RECOVERY_METADATA_SQL,
    ),
)


@final
class RecoveryImageStagingArea:
    def __init__(
        self,
        directory: str | Path,
        *,
        maximum_size_bytes: int = MAX_RECOVERY_IMAGE_SIZE_BYTES,
        backup_timeout_seconds: float = _DEFAULT_BACKUP_TIMEOUT_SECONDS,
        clock_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if (
            not _is_strict_integer(maximum_size_bytes)
            or maximum_size_bytes < 4096
            or maximum_size_bytes > MAX_RECOVERY_IMAGE_SIZE_BYTES
        ):
            raise RecoveryStagingError("recovery staging byte ceiling is unsupported")
        if (
            not _is_real_number(backup_timeout_seconds)
            or not 0 < backup_timeout_seconds <= _MAX_BACKUP_TIMEOUT_SECONDS
        ):
            raise RecoveryStagingError("recovery backup timeout is unsupported")
        self._directory = Path(directory)
        self._maximum_size_bytes = maximum_size_bytes
        self._backup_timeout_seconds = float(backup_timeout_seconds)
        self._clock_ms = clock_ms
        self._monotonic = monotonic
        self._directory_identity = self._prepare_directory()

    @contextmanager
    def create(
        self,
        source: ControlStore,
        request: RecoveryImageRequest,
    ) -> Generator[StagedRecoveryImage]:
        captured_at_ms = self._clock_ms()
        _validate_request(request, captured_at_ms=captured_at_ms)
        started_monotonic = self._monotonic()
        if not _is_real_number(started_monotonic):
            raise RecoveryStagingError("recovery backup clock is invalid")
        deadline_monotonic = started_monotonic + self._backup_timeout_seconds
        with self._reserved_file() as reserved:
            destination = sqlite3.connect(reserved.path, isolation_level=None)
            try:
                _ = destination.execute("PRAGMA journal_mode = DELETE")
                _ = destination.execute("PRAGMA synchronous = FULL")
                identity = source.backup_to(
                    destination,
                    deadline_monotonic=deadline_monotonic,
                    monotonic=self._monotonic,
                    maximum_size_bytes=self._maximum_size_bytes,
                )
                _embed_metadata(destination, identity.state_revision, request)
                _validate_sqlite_image(destination, identity.state_revision)
            except (ControlSnapshotError, MigrationError, sqlite3.Error) as cause:
                error = RecoveryVerificationError(
                    "control recovery image creation failed"
                )
                _close_sqlite_connection(destination, error)
                raise error from cause
            except BaseException as error:
                _close_sqlite_connection(destination, error)
                raise
            else:
                _close_sqlite_connection(destination, None)
            os.fsync(reserved.descriptor)
            digest, size_bytes = _descriptor_identity(
                reserved.descriptor,
                maximum_size_bytes=self._maximum_size_bytes,
            )
            metadata = validate_recovery_image(
                reserved.path,
                expected_sha256=digest,
                expected_size_bytes=size_bytes,
                validation_time_ms=captured_at_ms,
            )
            image = StagedRecoveryImage(
                path=reserved.path,
                metadata=metadata,
                sha256=digest,
                size_bytes=size_bytes,
                _lease=RecoveryImageLease(os.dup(reserved.descriptor)),
            )
            active_error: BaseException | None = None
            try:
                yield image
            except BaseException as error:
                active_error = error
                raise
            finally:
                try:
                    image._lease.close()  # pyright: ignore[reportPrivateUsage]
                except BaseException as cleanup_error:
                    if active_error is not None:
                        active_error.add_note(
                            f"recovery image lease cleanup also failed: {cleanup_error!r}"
                        )
                    elif isinstance(cleanup_error, Exception):
                        raise RecoveryStagingError(
                            "recovery image lease cleanup failed"
                        ) from cleanup_error
                    else:
                        raise

    @contextmanager
    def reserve_download(self) -> Generator[tuple[Path, int]]:
        with self._reserved_file() as reserved:
            yield reserved.path, reserved.descriptor

    @contextmanager
    def _reserved_file(self) -> Generator[_ReservedFile]:
        directory_descriptor = self._acquire_directory()
        reserved: _ReservedFile | None = None
        active_error: BaseException | None = None
        try:
            self._cleanup_stale_files(directory_descriptor)
            reserved = self._create_file(directory_descriptor)
            try:
                yield reserved
            except BaseException as caught_error:
                active_error = caught_error
                raise
        finally:
            failures = _cleanup_reserved(reserved, directory_descriptor)
            if failures and active_error is not None:
                for failure in failures:
                    active_error.add_note(
                        f"recovery staging cleanup also failed: {failure!r}"
                    )
            elif failures:
                control_flow_error = next(
                    (
                        failure
                        for failure in failures
                        if not isinstance(failure, Exception)
                    ),
                    None,
                )
                if control_flow_error is not None:
                    for failure in failures:
                        if failure is not control_flow_error:
                            control_flow_error.add_note(
                                f"additional cleanup failure: {failure!r}"
                            )
                    raise control_flow_error
                cleanup_error = RecoveryStagingError("recovery staging cleanup failed")
                for failure in failures[1:]:
                    cleanup_error.add_note(f"additional cleanup failure: {failure!r}")
                raise cleanup_error from failures[0]

    def _prepare_directory(self) -> tuple[int, int]:
        try:
            self._directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            status = self._directory.lstat()
        except OSError as error:
            raise RecoveryStagingError(
                "recovery staging directory is unavailable"
            ) from error
        _validate_directory(status)
        return status.st_dev, status.st_ino

    def _acquire_directory(self) -> int:
        flags = (
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = -1
        try:
            descriptor = os.open(self._directory, flags)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            status = os.fstat(descriptor)
            _validate_directory(status)
            if (status.st_dev, status.st_ino) != self._directory_identity:
                raise RecoveryStagingError(
                    "recovery staging directory identity changed"
                )
            return descriptor
        except BaseException as error:
            if descriptor >= 0:
                _close_with_note(descriptor, error)
            if isinstance(error, RecoveryStagingError):
                raise
            if not isinstance(error, Exception):
                raise
            raise RecoveryStagingError(
                "recovery staging area is already active"
            ) from error

    def _create_file(self, directory_descriptor: int) -> _ReservedFile:
        for _attempt in range(_MAX_CREATE_ATTEMPTS):
            file_name = f"{_STAGING_PREFIX}{secrets.token_hex(16)}.sqlite3"
            try:
                descriptor = os.open(
                    file_name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_descriptor,
                )
                return _ReservedFile(self._directory / file_name, file_name, descriptor)
            except FileExistsError:
                continue
            except OSError as error:
                raise RecoveryStagingError(
                    "recovery staging file could not be created"
                ) from error
        raise RecoveryStagingError("recovery staging name attempts were exhausted")

    @staticmethod
    def _cleanup_stale_files(directory_descriptor: int) -> None:
        names: list[str] = []
        try:
            with os.scandir(directory_descriptor) as entries:
                for entry in entries:
                    if not entry.name.startswith(_STAGING_PREFIX):
                        raise RecoveryStagingError(
                            "recovery staging directory contains an unknown entry"
                        )
                    names.append(entry.name)
                    if len(names) > _MAX_STALE_FILES:
                        raise RecoveryStagingError(
                            "recovery staging stale-file count exceeds its bound"
                        )
            for name in sorted(names):
                status = os.stat(
                    name, dir_fd=directory_descriptor, follow_symlinks=False
                )
                _validate_file(status)
                os.unlink(name, dir_fd=directory_descriptor)
            if names:
                os.fsync(directory_descriptor)
        except RecoveryStagingError:
            raise
        except OSError as error:
            raise RecoveryStagingError("recovery staging cleanup failed") from error


@final
class _ReservedFile:
    __slots__ = ("descriptor", "file_name", "path")

    def __init__(self, path: Path, file_name: str, descriptor: int) -> None:
        self.path = path
        self.file_name = file_name
        self.descriptor = descriptor


def validate_recovery_image(
    path: Path,
    *,
    expected_key: str | None = None,
    expected_sha256: str | None = None,
    expected_size_bytes: int | None = None,
    validation_time_ms: int | None = None,
    progress_check: Callable[[], None] | None = None,
) -> RecoveryImageMetadata:
    if progress_check is not None:
        progress_check()
    status = path.lstat()
    _validate_file(status)
    digest, size_bytes = _path_identity(
        path,
        expected_size_bytes,
        progress_check=progress_check,
    )
    if expected_sha256 is not None and digest != expected_sha256:
        raise RecoveryVerificationError("recovery image checksum does not match")
    if expected_size_bytes is not None and size_bytes != expected_size_bytes:
        raise RecoveryVerificationError("recovery image byte count does not match")
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA trusted_schema = OFF")
        _validate_sqlite_image(connection, None, progress_check=progress_check)
        identity = validate_control_database_with_schema_extension(
            connection,
            _RECOVERY_SCHEMA_EXTENSION,
        )
        metadata = _read_metadata(connection)
        _validate_embedded_run(connection, metadata)
    except (MigrationError, sqlite3.Error) as cause:
        error = RecoveryVerificationError("recovery SQLite validation failed")
        _close_sqlite_connection(connection, error)
        raise error from cause
    except BaseException as error:
        _close_sqlite_connection(connection, error)
        raise
    else:
        _close_sqlite_connection(connection, None)
    if metadata.state_revision != identity.state_revision:
        raise RecoveryVerificationError("recovery metadata state revision differs")
    if metadata.schema_version != identity.schema_version:
        raise RecoveryVerificationError("recovery metadata schema version differs")
    if metadata.migration_set_sha256 != migration_set_sha256(identity.migrations):
        raise RecoveryVerificationError("recovery metadata migration set differs")
    captured_at_ms = (
        time.time_ns() // 1_000_000
        if validation_time_ms is None
        else validation_time_ms
    )
    _validate_image_creation_time(metadata.created_at_ms, captured_at_ms)
    if expected_key is not None:
        parsed = parse_recovery_key(expected_key)
        if parsed.sha256 != digest or parsed.state_revision != metadata.state_revision:
            raise RecoveryVerificationError("recovery key identity differs from image")
        if parsed.recovery_class != metadata.recovery_class:
            raise RecoveryVerificationError("recovery key class differs from image")
        if recovery_key(metadata, digest) != expected_key:
            raise RecoveryVerificationError("recovery key period differs from image")
    if progress_check is not None:
        progress_check()
    return metadata


def prepare_control_database_restore(
    path: Path,
    *,
    expected_state_revision: int,
) -> None:
    """Remove image-only metadata and prove the resulting live v1 database."""
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        _ = connection.execute("PRAGMA trusted_schema = OFF")
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = validate_control_database_with_schema_extension(
            connection,
            _RECOVERY_SCHEMA_EXTENSION,
            expected_state_revision=expected_state_revision,
        )
        _ = connection.execute("BEGIN IMMEDIATE")
        try:
            _ = connection.execute("DROP TABLE recovery_image_metadata")
            connection.commit()
        except BaseException as mutation_error:
            if connection.in_transaction:
                try:
                    connection.rollback()
                except BaseException as cleanup_error:
                    mutation_error.add_note(
                        f"recovery restore rollback also failed: {cleanup_error!r}"
                    )
            raise
        _validate_prepared_control_database(connection, expected_state_revision)
    except (MigrationError, sqlite3.Error) as cause:
        verification_error = RecoveryVerificationError(
            "recovery restore preparation failed"
        )
        _close_sqlite_connection(connection, verification_error)
        raise verification_error from cause
    except BaseException as error:
        _close_sqlite_connection(connection, error)
        raise
    else:
        _close_sqlite_connection(connection, None)


def validate_prepared_control_database(
    path: Path,
    *,
    expected_state_revision: int,
) -> None:
    uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None)
    try:
        _ = connection.execute("PRAGMA query_only = ON")
        _ = connection.execute("PRAGMA trusted_schema = OFF")
        _validate_prepared_control_database(connection, expected_state_revision)
    except (MigrationError, sqlite3.Error) as cause:
        error = RecoveryVerificationError("prepared control database is invalid")
        _close_sqlite_connection(connection, error)
        raise error from cause
    except BaseException as error:
        _close_sqlite_connection(connection, error)
        raise
    else:
        _close_sqlite_connection(connection, None)


def _validate_prepared_control_database(
    connection: sqlite3.Connection,
    expected_state_revision: int,
) -> None:
    _ = validate_control_database(
        connection,
        expected_state_revision=expected_state_revision,
    )
    integrity = connection.execute("PRAGMA integrity_check(1)").fetchall()
    if integrity != [("ok",)]:
        raise RecoveryVerificationError(
            "prepared control database integrity check failed"
        )
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RecoveryVerificationError(
            "prepared control database foreign-key check failed"
        )


def _embed_metadata(
    connection: sqlite3.Connection,
    state_revision: int,
    request: RecoveryImageRequest,
) -> None:
    identity = validate_control_database(
        connection,
        expected_state_revision=state_revision,
    )
    if request.recovery_class == "run":
        row = cast(
            tuple[int] | None,
            connection.execute(
                """
                SELECT committed_state_revision
                FROM watcher_runs
                WHERE run_id = ?
                  AND status IN ('committed', 'heartbeat_pending', 'successful')
                """,
                (request.run_id,),
            ).fetchone(),
        )
        if row != (request.committed_state_revision,):
            raise RecoveryVerificationError(
                "run recovery metadata does not match a committed run"
            )
    _ = connection.execute("BEGIN IMMEDIATE")
    try:
        _ = connection.execute(_RECOVERY_METADATA_SQL)
        _ = connection.execute(
            """
            INSERT INTO recovery_image_metadata (
                singleton_id, recovery_class, state_revision, schema_version,
                migration_set_sha256, created_at_ms, run_id,
                committed_state_revision
            ) VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request.recovery_class,
                state_revision,
                identity.schema_version,
                migration_set_sha256(identity.migrations),
                request.created_at_ms,
                request.run_id,
                request.committed_state_revision,
            ),
        )
        connection.commit()
    except BaseException as error:
        if connection.in_transaction:
            try:
                connection.rollback()
            except BaseException as cleanup_error:
                error.add_note(
                    f"recovery metadata rollback also failed: {cleanup_error!r}"
                )
        raise


def _read_metadata(connection: sqlite3.Connection) -> RecoveryImageMetadata:
    rows = cast(
        list[tuple[str, int, int, str, int, str | None, int | None]],
        connection.execute(
            """
            SELECT recovery_class, state_revision, schema_version,
                   migration_set_sha256, created_at_ms, run_id,
                   committed_state_revision
            FROM recovery_image_metadata
            WHERE singleton_id = 1
            """
        ).fetchall(),
    )
    if len(rows) != 1:
        raise RecoveryVerificationError("recovery image metadata singleton is missing")
    (
        recovery_class,
        state_revision,
        schema_version,
        checksum,
        created_at_ms,
        run_id,
        committed,
    ) = rows[0]
    if not _is_recovery_class(recovery_class):
        raise RecoveryVerificationError("recovery metadata class is invalid")
    request = RecoveryImageRequest(
        recovery_class,
        created_at_ms,
        run_id,
        committed,
    )
    _validate_request(request, captured_at_ms=created_at_ms)
    if state_revision < 0 or state_revision >= 10**20:
        raise RecoveryVerificationError("recovery metadata state revision is invalid")
    if schema_version != CONTROL_SCHEMA_VERSION:
        raise RecoveryVerificationError("recovery metadata schema version is invalid")
    _validate_sha256(checksum, "recovery migration set")
    return RecoveryImageMetadata(
        request.recovery_class,
        state_revision,
        schema_version,
        checksum,
        created_at_ms,
        run_id,
        committed,
    )


def _validate_embedded_run(
    connection: sqlite3.Connection,
    metadata: RecoveryImageMetadata,
) -> None:
    if metadata.recovery_class != "run":
        return
    row = cast(
        tuple[int] | None,
        connection.execute(
            """
            SELECT committed_state_revision
            FROM watcher_runs
            WHERE run_id = ?
              AND status IN ('committed', 'heartbeat_pending', 'successful')
            """,
            (metadata.run_id,),
        ).fetchone(),
    )
    if row != (metadata.committed_state_revision,):
        raise RecoveryVerificationError(
            "recovery metadata does not identify its committed run"
        )


def _validate_sqlite_image(
    connection: sqlite3.Connection,
    expected_state_revision: int | None,
    *,
    progress_check: Callable[[], None] | None = None,
) -> None:
    interrupted_errors: list[BaseException] = []
    operation_error: BaseException | None = None

    def check_progress() -> int:
        if progress_check is None:
            return 0
        try:
            progress_check()
        except BaseException as error:
            interrupted_errors.append(error)
            return 1
        return 0

    if progress_check is not None:
        progress_check()
        connection.set_progress_handler(check_progress, 1000)
    try:
        integrity = connection.execute("PRAGMA integrity_check(1)").fetchall()
        if integrity != [("ok",)]:
            raise RecoveryVerificationError("recovery image integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RecoveryVerificationError("recovery image foreign-key check failed")
        _ = validate_control_database_with_schema_extension(
            connection,
            _RECOVERY_SCHEMA_EXTENSION,
            expected_state_revision=expected_state_revision,
        )
    except sqlite3.OperationalError as sqlite_error:
        if interrupted_errors:
            operation_error = interrupted_errors[0]
            raise operation_error from sqlite_error
        operation_error = sqlite_error
        raise
    except BaseException as caught_error:
        operation_error = caught_error
        raise
    finally:
        if progress_check is not None:
            try:
                connection.set_progress_handler(None, 0)
            except BaseException as cleanup_error:
                if operation_error is not None:
                    operation_error.add_note(
                        f"recovery SQLite progress cleanup also failed: {cleanup_error!r}"
                    )
                elif isinstance(cleanup_error, Exception):
                    raise RecoveryStagingError(
                        "recovery SQLite progress cleanup failed"
                    ) from cleanup_error
                else:
                    raise
    if progress_check is not None:
        progress_check()


def _validate_request(
    request: RecoveryImageRequest,
    *,
    captured_at_ms: int,
) -> None:
    if request.recovery_class not in ("run", "daily", "weekly", "monthly"):
        raise RecoveryVerificationError("recovery class is unsupported")
    if (
        not _is_strict_integer(request.created_at_ms)
        or request.created_at_ms < 1
        or request.created_at_ms > _MAX_TIMESTAMP_MS
    ):
        raise RecoveryVerificationError("recovery creation time is invalid")
    _validate_captured_time(captured_at_ms)
    if abs(request.created_at_ms - captured_at_ms) > _MAX_CREATION_CLOCK_SKEW_MS:
        raise RecoveryVerificationError(
            "recovery creation time differs from the captured clock"
        )
    if request.recovery_class == "run":
        if (
            request.run_id is None
            or len(request.run_id) < 16
            or len(request.run_id) > 128
        ):
            raise RecoveryVerificationError("run recovery requires a valid run ID")
        if (
            not _is_strict_integer(request.committed_state_revision)
            or request.committed_state_revision < 1
        ):
            raise RecoveryVerificationError(
                "run recovery requires a committed revision"
            )
    elif request.run_id is not None or request.committed_state_revision is not None:
        raise RecoveryVerificationError("scheduled recovery cannot identify a run")


def _path_identity(
    path: Path,
    expected_size_bytes: int | None,
    *,
    progress_check: Callable[[], None] | None = None,
) -> tuple[str, int]:
    maximum = expected_size_bytes or MAX_RECOVERY_IMAGE_SIZE_BYTES
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    active_error: BaseException | None = None
    try:
        return _descriptor_identity(
            descriptor,
            maximum_size_bytes=maximum,
            progress_check=progress_check,
        )
    except BaseException as error:
        active_error = error
        raise
    finally:
        try:
            os.close(descriptor)
        except BaseException as cleanup_error:
            if active_error is not None:
                active_error.add_note(
                    f"recovery descriptor cleanup also failed: {cleanup_error!r}"
                )
            elif isinstance(cleanup_error, Exception):
                raise RecoveryStagingError(
                    "recovery descriptor cleanup failed"
                ) from cleanup_error
            else:
                raise


def _descriptor_identity(
    descriptor: int,
    *,
    maximum_size_bytes: int,
    progress_check: Callable[[], None] | None = None,
) -> tuple[str, int]:
    _validate_file(os.fstat(descriptor))
    digest = hashlib.sha256()
    offset_bytes = 0
    while chunk := os.pread(descriptor, _HASH_CHUNK_BYTES, offset_bytes):
        if progress_check is not None:
            progress_check()
        offset_bytes += len(chunk)
        if offset_bytes > maximum_size_bytes:
            raise RecoveryVerificationError("recovery image exceeds its byte ceiling")
        digest.update(chunk)
    if offset_bytes < 1:
        raise RecoveryVerificationError("recovery image is empty")
    return digest.hexdigest(), offset_bytes


def _cleanup_reserved(
    reserved: _ReservedFile | None,
    directory_descriptor: int,
) -> list[BaseException]:
    failures: list[BaseException] = []
    if reserved is not None:
        try:
            os.close(reserved.descriptor)
        except BaseException as error:
            failures.append(error)
        try:
            os.unlink(reserved.file_name, dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        except BaseException as error:
            failures.append(error)
    try:
        os.close(directory_descriptor)
    except BaseException as error:
        failures.append(error)
    return failures


def _validate_directory(status: os.stat_result) -> None:
    if not stat.S_ISDIR(status.st_mode):
        raise RecoveryStagingError("recovery staging path must be a directory")
    if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) & 0o077:
        raise RecoveryStagingError(
            "recovery staging directory must be private and owned"
        )


def _validate_file(status: os.stat_result) -> None:
    if not stat.S_ISREG(status.st_mode):
        raise RecoveryVerificationError("recovery image must be a regular file")
    if stat.S_IMODE(status.st_mode) != 0o600:
        raise RecoveryVerificationError("recovery image mode must be 0600")
    if status.st_uid != os.geteuid() or status.st_nlink != 1:
        raise RecoveryVerificationError("recovery image ownership is unsafe")


def _validate_sha256(value: str, label: str) -> None:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise RecoveryVerificationError(f"{label} SHA-256 is invalid")


def _close_with_note(descriptor: int, primary_error: BaseException) -> None:
    try:
        os.close(descriptor)
    except BaseException as cleanup_error:
        primary_error.add_note(f"descriptor cleanup also failed: {cleanup_error!r}")


def _close_sqlite_connection(
    connection: sqlite3.Connection,
    primary_error: BaseException | None,
) -> None:
    try:
        connection.close()
    except BaseException as cleanup_error:
        if primary_error is not None:
            primary_error.add_note(
                f"recovery SQLite cleanup also failed: {cleanup_error!r}"
            )
        elif isinstance(cleanup_error, Exception):
            raise RecoveryStagingError(
                "recovery SQLite cleanup failed"
            ) from cleanup_error
        else:
            raise


def _validate_image_creation_time(created_at_ms: int, captured_at_ms: int) -> None:
    _validate_captured_time(captured_at_ms)
    if created_at_ms > captured_at_ms + _MAX_CREATION_CLOCK_SKEW_MS:
        raise RecoveryVerificationError("recovery image creation time is in the future")


def _validate_captured_time(captured_at_ms: int) -> None:
    if (
        not _is_strict_integer(captured_at_ms)
        or captured_at_ms < 1
        or captured_at_ms > _MAX_TIMESTAMP_MS
    ):
        raise RecoveryVerificationError("captured recovery clock is invalid")


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_recovery_class(value: object) -> TypeIs[RecoveryClass]:
    return value in ("run", "daily", "weekly", "monthly")
