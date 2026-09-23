import json
import math
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from hashlib import sha256
from pathlib import Path
from typing import Protocol, TypeIs, cast
from uuid import uuid4

from minos.ingest.watchers.control.configuration import (
    SourceConfigurationCodec,
    SourceConfigurationCodecRegistry,
)
from minos.ingest.watchers.control.migrations import (
    CONTROL_SCHEMA_VERSION,
    ControlDatabaseIdentity,
    MigrationError,
    apply_control_migrations,
    validate_control_database,
)
from minos.ingest.watchers.control.models import (
    BlobReceipt,
    BootstrapResult,
    ChangeDraft,
    ClaimIdentity,
    ClaimRequest,
    CommittedBlobEvidence,
    CommittedObservation,
    CommittedObservationHistory,
    CommittedObservationRevision,
    CommittedProductEvidence,
    CommittedRun,
    ConfiguredSource,
    IncidentDraft,
    JsonValue,
    ObservationCommit,
    ObservationDraft,
    PersistedSourceState,
    ReconcilableStatus,
    RecoveryReceipt,
    RetryPolicy,
    RunClaim,
    RunMode,
    RunTransition,
    ScheduledRunMode,
    ScheduledSource,
    ScheduleResult,
    SourceConfiguration,
    SourceSchedule,
    StaleRunClaim,
)

_MAX_TIMESTAMP_MS = 253_402_300_799_999
_MAX_BLOBS_PER_COMMIT = 256
_MAX_OBSERVATIONS_PER_COMMIT = 256
_MAX_OBSERVATION_BLOBS_PER_COMMIT = 1024
_MAX_CHANGES_PER_COMMIT = 256
_MAX_INCIDENTS_PER_COMMIT = 256
_MAX_RECONCILABLE_RUNS = 1000
_MAX_PUBLIC_HISTORY_REVISIONS = 512
_MAX_PUBLIC_HISTORY_INPUT_ROWS = 16_384
_MAX_PUBLIC_HISTORY_INPUT_BYTES = 32 * 1024 * 1024
_PUBLIC_HISTORY_PAGE_ROWS = 32
_MAX_PUBLIC_PRODUCT_EVIDENCE = 512
_MAX_PUBLIC_PRODUCT_BLOBS = 16
_MAX_ENABLED_SOURCES = 1000
_MAX_SCHEDULED_SOURCES = 2000
_MAX_STALE_CLAIMS = 1000
_MAX_DURABLE_RETRY_FAILURES = 31
_MAX_BLOB_SIZE_BYTES = 512 * 1024 * 1024
_MAX_CONTROL_DATABASE_SIZE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_BACKUP_DURATION_SECONDS = 900.0
_MIN_SQLITE_PAGE_SIZE_BYTES = 512
_BACKUP_PAGES_PER_STEP = 256
_SENSITIVE_CONFIGURATION_SUFFIXES = (
    "_access_key",
    "_authorization",
    "_cookie",
    "_credential",
    "_credentials",
    "_password",
    "_private_key",
    "_secret",
    "_secret_key",
    "_token",
)
_SENSITIVE_CONFIGURATION_KEYS = frozenset(
    item[1:] for item in _SENSITIVE_CONFIGURATION_SUFFIXES
)
_CONTROL_STORE_OWNER = object()


class _CronSchedule(Protocol):
    def get_next_fire_time(
        self,
        previous_fire_time: datetime | None,
        now: datetime,
    ) -> datetime | None: ...


class _CronTriggerFactory(Protocol):
    @staticmethod
    def from_crontab(expression: str, timezone: tzinfo) -> _CronSchedule: ...


class ControlStoreError(RuntimeError):
    """The watcher control state could not be changed safely."""


class ControlValidationError(ControlStoreError):
    """A control transaction command violates the persistence contract."""


class BlobVerificationError(ControlValidationError):
    """An archive receipt does not match the requested immutable blob."""


class SourceNotFoundError(ControlStoreError):
    """The requested watcher source does not exist."""


class BootstrapConflictError(ControlStoreError):
    """Existing source state differs from the explicit bootstrap contract."""


class ClaimBlockedError(ControlStoreError):
    """A source cannot be claimed in its current state."""


class StaleClaimError(ControlStoreError):
    """The collector no longer owns the claimed source run."""


class IllegalRunTransitionError(ControlStoreError):
    """A watcher run cannot make the requested state transition."""


class ControlStorageError(ControlStoreError):
    """SQLite rejected or could not complete a control transaction."""


class UncertainControlOutcomeError(ControlStorageError):
    """A control transaction may have committed despite a reported failure."""


class ControlSnapshotError(ControlStorageError):
    """The live control database could not be copied and proven consistent."""


class ControlStore:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        busy_timeout_ms: int = 5000,
        source_configuration_codecs: SourceConfigurationCodecRegistry | None = None,
        _owner: object | None = None,
    ) -> None:
        if _owner is not _CONTROL_STORE_OWNER:
            raise ControlStorageError(
                "control store connections must be owned by open()"
            )
        if busy_timeout_ms < 1 or busy_timeout_ms > 60_000:
            raise ControlValidationError("busy timeout must be between 1 and 60000 ms")
        _initialize_writer_connection(connection, busy_timeout_ms)
        self._connection: sqlite3.Connection = connection
        self._source_configuration_codecs: SourceConfigurationCodecRegistry = (
            source_configuration_codecs or SourceConfigurationCodecRegistry()
        )
        self._closed: bool = False

    @classmethod
    def open(
        cls,
        database_path: str | Path,
        *,
        busy_timeout_ms: int = 5000,
        allow_thread_handoff: bool = False,
        source_configuration_codecs: SourceConfigurationCodecRegistry | None = None,
    ) -> ControlStore:
        connection = sqlite3.connect(
            database_path,
            timeout=busy_timeout_ms / 1000,
            isolation_level=None,
            check_same_thread=not allow_thread_handoff,
        )
        try:
            return cls(
                connection,
                busy_timeout_ms=busy_timeout_ms,
                source_configuration_codecs=source_configuration_codecs,
                _owner=_CONTROL_STORE_OWNER,
            )
        except BaseException as error:
            try:
                connection.close()
            except BaseException as cleanup_error:
                error.add_note(
                    f"control connection cleanup also failed: {cleanup_error!r}"
                )
            raise

    def __enter__(self) -> ControlStore:
        self._require_open()
        return self

    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        error: BaseException | None,
        _traceback: object,
    ) -> None:
        try:
            self.close()
        except BaseException as cleanup_error:
            if error is not None:
                error.add_note(f"control store cleanup also failed: {cleanup_error!r}")
            else:
                raise

    def close(self) -> None:
        if not self._closed:
            self._connection.close()
            self._closed = True

    def backup_to(
        self,
        destination: sqlite3.Connection,
        *,
        deadline_monotonic: float,
        monotonic: Callable[[], float] = time.monotonic,
        maximum_size_bytes: int = _MAX_CONTROL_DATABASE_SIZE_BYTES,
    ) -> ControlDatabaseIdentity:
        self._require_open()
        if self._connection.in_transaction or destination.in_transaction:
            raise ControlSnapshotError("control backup requires idle connections")
        if (
            not _is_strict_integer(maximum_size_bytes)
            or maximum_size_bytes < 4096
            or maximum_size_bytes > _MAX_CONTROL_DATABASE_SIZE_BYTES
        ):
            raise ControlSnapshotError("control backup byte ceiling is unsupported")
        started_monotonic = monotonic()
        if (
            not _is_real_number(deadline_monotonic)
            or not math.isfinite(deadline_monotonic)
            or not _is_real_number(started_monotonic)
            or not math.isfinite(started_monotonic)
            or deadline_monotonic <= started_monotonic
            or deadline_monotonic - started_monotonic > _MAX_BACKUP_DURATION_SECONDS
        ):
            raise ControlSnapshotError("control backup deadline is unsupported")
        maximum_progress_callbacks = (
            maximum_size_bytes // _MIN_SQLITE_PAGE_SIZE_BYTES // _BACKUP_PAGES_PER_STEP
            + 2
        )
        progress_callbacks = 0
        backup_completed = False

        def require_time_remaining() -> None:
            current = monotonic()
            if not _is_real_number(current) or not math.isfinite(current):
                raise ControlSnapshotError("control backup clock is invalid")
            if current >= deadline_monotonic:
                raise ControlSnapshotError("control backup deadline expired")

        try:
            require_time_remaining()
            live_identity = validate_control_database(self._connection)
            require_time_remaining()
            quick_check = self._connection.execute("PRAGMA quick_check(1)").fetchall()
            if quick_check != [("ok",)]:
                raise ControlSnapshotError("live control database quick check failed")
            require_time_remaining()
            page_size = _read_positive_pragma(self._connection, "page_size")
            page_count = _read_positive_pragma(self._connection, "page_count")
            if page_size * page_count > maximum_size_bytes:
                raise ControlSnapshotError(
                    "control database exceeds the backup byte ceiling"
                )

            def validate_progress(status: int, remaining: int, total: int) -> None:
                nonlocal backup_completed, progress_callbacks
                progress_callbacks += 1
                if progress_callbacks > maximum_progress_callbacks:
                    raise ControlSnapshotError(
                        "control backup exceeded its progress callback bound"
                    )
                require_time_remaining()
                if (
                    not _is_strict_integer(status)
                    or status
                    not in (
                        sqlite3.SQLITE_OK,
                        sqlite3.SQLITE_BUSY,
                        sqlite3.SQLITE_LOCKED,
                        sqlite3.SQLITE_DONE,
                    )
                    or not _is_strict_integer(remaining)
                    or not _is_strict_integer(total)
                    or remaining < 0
                    or total < 0
                    or remaining > total
                    or page_size * total > maximum_size_bytes
                ):
                    raise ControlSnapshotError(
                        "control backup reported invalid bounded progress"
                    )
                if status == sqlite3.SQLITE_DONE:
                    if remaining != 0:
                        raise ControlSnapshotError(
                            "completed control backup retained pages"
                        )
                    backup_completed = True

            self._connection.backup(
                destination,
                pages=_BACKUP_PAGES_PER_STEP,
                progress=validate_progress,
                sleep=0.01,
            )
            require_time_remaining()
            if not backup_completed or progress_callbacks < 1:
                raise ControlSnapshotError("control backup did not report completion")
            copied_identity = validate_control_database(
                destination,
                expected_state_revision=live_identity.state_revision,
            )
            require_time_remaining()
        except ControlSnapshotError:
            raise
        except (MigrationError, sqlite3.Error) as error:
            raise ControlSnapshotError("control database backup failed") from error
        if copied_identity != live_identity:
            raise ControlSnapshotError("control backup identity changed during copying")
        return copied_identity

    def configure_source(
        self,
        command: SourceConfiguration,
    ) -> ConfiguredSource:
        self._require_open()
        _validate_identifier(command.source_id, "source ID", 64)
        _validate_identifier(command.watcher_type, "watcher type", 64)
        _validate_boolean(command.enabled, "source enabled state")
        _reject_sensitive_configuration(command.configuration)
        codec, validated_configuration = _validate_source_configuration(
            command,
            self._source_configuration_codecs,
        )
        configuration_json = _canonical_source_configuration_json(
            validated_configuration
        )
        if configuration_json is None:
            raise ControlValidationError("source configuration is invalid")
        if _codec_claim_timeout_ms(codec, configuration_json) is None:
            raise ControlValidationError(
                "source configuration claim timeout is invalid"
            )
        configuration_sha256 = sha256(configuration_json.encode("utf-8")).hexdigest()

        with self._write_transaction() as now_ms:
            current = self._read_source_configuration(command.source_id)
            if current is not None:
                (
                    watcher_type,
                    revision,
                    current_sha256,
                    current_json,
                    enabled,
                ) = current
                _ = _validate_stored_source_configuration(
                    watcher_type,
                    current_json,
                    current_sha256,
                    self._source_configuration_codecs,
                )
                if watcher_type != command.watcher_type:
                    raise ControlValidationError("a source watcher type is immutable")
                if (
                    current_sha256 == configuration_sha256
                    and enabled == command.enabled
                ):
                    return ConfiguredSource(
                        command.source_id,
                        revision,
                        configuration_sha256,
                        self._read_state_revision(),
                        False,
                    )
                configuration_changed = current_sha256 != configuration_sha256
                next_configuration_revision = revision + int(configuration_changed)
            else:
                configuration_changed = True
                next_configuration_revision = 1
                self._insert_disabled_source(command, now_ms)

            if configuration_changed:
                self._insert_configuration_revision(
                    command.source_id,
                    next_configuration_revision,
                    configuration_sha256,
                    configuration_json,
                    now_ms,
                )
            _ = self._connection.execute(
                """
                UPDATE sources
                SET enabled = ?, current_configuration_revision = ?, updated_at_ms = ?
                WHERE source_id = ?
                """,
                (
                    command.enabled,
                    next_configuration_revision,
                    now_ms,
                    command.source_id,
                ),
            )
            state_revision = self._advance_state_revision(now_ms)
            self._insert_audit(
                source_id=command.source_id,
                run_id=None,
                event_type="source_configured",
                details={
                    "configuration_revision": next_configuration_revision,
                    "enabled": command.enabled,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return ConfiguredSource(
                command.source_id,
                next_configuration_revision,
                configuration_sha256,
                state_revision,
                True,
            )

    def bootstrap_source(
        self,
        command: SourceConfiguration,
        schedules: tuple[SourceSchedule, ...],
    ) -> BootstrapResult:
        self._require_open()
        _validate_identifier(command.source_id, "source ID", 64)
        _validate_identifier(command.watcher_type, "watcher type", 64)
        if command.enabled is not True:
            raise ControlValidationError("bootstrapped source must be enabled")
        _reject_sensitive_configuration(command.configuration)
        codec, validated_configuration = _validate_source_configuration(
            command,
            self._source_configuration_codecs,
        )
        configuration_json = _canonical_source_configuration_json(
            validated_configuration
        )
        if (
            configuration_json is None
            or _codec_claim_timeout_ms(codec, configuration_json) is None
        ):
            raise ControlValidationError("source configuration is invalid")
        configuration_sha256 = sha256(configuration_json.encode("utf-8")).hexdigest()
        _validate_bootstrap_schedules(command.source_id, schedules)

        with self._write_transaction() as now_ms:
            current = self._read_source_configuration(command.source_id)
            if current is not None:
                self._require_matching_bootstrap(
                    command,
                    schedules,
                    configuration_json,
                    configuration_sha256,
                    current,
                )
                return BootstrapResult(
                    command.source_id,
                    self._read_state_revision(),
                    False,
                )
            if self._source_exists(command.source_id):
                raise BootstrapConflictError(
                    "existing source state differs from bootstrap contract"
                )

            self._insert_disabled_source(command, now_ms)
            self._insert_configuration_revision(
                command.source_id,
                1,
                configuration_sha256,
                configuration_json,
                now_ms,
            )
            _ = self._connection.execute(
                """
                UPDATE sources
                SET enabled = 1, current_configuration_revision = 1,
                    updated_at_ms = ?
                WHERE source_id = ?
                """,
                (now_ms, command.source_id),
            )
            for schedule in schedules:
                self._insert_schedule(
                    schedule,
                    _next_cron_fire_ms(schedule.cron_expression, now_ms),
                    now_ms,
                )
            state_revision = self._advance_state_revision(now_ms)
            self._insert_audit(
                source_id=command.source_id,
                run_id=None,
                event_type="source_bootstrapped",
                details={
                    "configuration_revision": 1,
                    "run_modes": [schedule.run_mode for schedule in schedules],
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return BootstrapResult(command.source_id, state_revision, True)

    def set_schedule(self, schedule: SourceSchedule) -> ScheduleResult:
        self._require_open()
        _validate_schedule(schedule)
        values = (
            schedule.cron_expression,
            schedule.misfire_grace_seconds,
            schedule.retry_max_attempts,
            schedule.retry_base_seconds,
            schedule.retry_max_seconds,
        )
        with self._write_transaction() as now_ms:
            self._require_source(schedule.source_id)
            existing = cast(
                tuple[str, int, int, int, float, float] | None,
                self._connection.execute(
                    """
                    SELECT cron_expression, next_due_at_ms, misfire_grace_seconds,
                           retry_max_attempts, retry_base_seconds, retry_max_seconds
                    FROM source_schedules
                    WHERE source_id = ? AND run_mode = ?
                    """,
                    (schedule.source_id, schedule.run_mode),
                ).fetchone(),
            )
            if existing is not None and (existing[0], *existing[2:]) == values:
                return ScheduleResult(
                    schedule.source_id,
                    existing[1],
                    self._read_state_revision(),
                    False,
                )
            if existing is not None and (
                existing[0] == schedule.cron_expression or existing[1] <= now_ms
            ):
                next_due_at_ms = existing[1]
            else:
                next_due_at_ms = _next_cron_fire_ms(schedule.cron_expression, now_ms)
            _ = self._connection.execute(
                """
                INSERT INTO source_schedules (
                    source_id, run_mode, cron_expression, next_due_at_ms,
                    misfire_grace_seconds, retry_max_attempts,
                    retry_base_seconds, retry_max_seconds, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, run_mode) DO UPDATE SET
                    cron_expression = excluded.cron_expression,
                    next_due_at_ms = excluded.next_due_at_ms,
                    misfire_grace_seconds = excluded.misfire_grace_seconds,
                    retry_max_attempts = excluded.retry_max_attempts,
                    retry_base_seconds = excluded.retry_base_seconds,
                    retry_max_seconds = excluded.retry_max_seconds,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    schedule.source_id,
                    schedule.run_mode,
                    schedule.cron_expression,
                    next_due_at_ms,
                    schedule.misfire_grace_seconds,
                    schedule.retry_max_attempts,
                    schedule.retry_base_seconds,
                    schedule.retry_max_seconds,
                    now_ms,
                ),
            )
            state_revision = self._advance_state_revision(now_ms)
            self._insert_audit(
                source_id=schedule.source_id,
                run_id=None,
                event_type="schedule_configured",
                details={"next_due_at_ms": next_due_at_ms},
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return ScheduleResult(
                schedule.source_id,
                next_due_at_ms,
                state_revision,
                True,
            )

    def list_scheduled_sources(
        self,
        *,
        limit: int = 100,
    ) -> tuple[ScheduledSource, ...]:
        self._require_open()
        _validate_list_limit(limit, _MAX_SCHEDULED_SOURCES, "schedule")
        rows = cast(
            list[tuple[str, str, str, str, int, int, int, float, float]],
            self._connection.execute(
                """
                 SELECT s.source_id, s.watcher_type, ss.run_mode,
                        ss.cron_expression,
                       ss.next_due_at_ms, ss.misfire_grace_seconds,
                       ss.retry_max_attempts, ss.retry_base_seconds,
                       ss.retry_max_seconds
                FROM source_schedules AS ss
                JOIN sources AS s ON s.source_id = ss.source_id
                WHERE s.enabled = 1
                 ORDER BY s.source_id,
                          CASE ss.run_mode
                              WHEN 'weekly_verification' THEN 0
                              ELSE 1
                          END
                LIMIT ?
                """,
                (limit + 1,),
            ).fetchall(),
        )
        _reject_truncated_rows(rows, limit, "scheduled source")
        schedules = tuple(_scheduled_source_from_row(row) for row in rows)
        for schedule in schedules:
            _validate_scheduled_projection(schedule)
        return schedules

    def validate_scheduled_source_configurations(
        self,
        *,
        limit: int = 100,
    ) -> None:
        self._require_open()
        _validate_list_limit(limit, _MAX_ENABLED_SOURCES, "schedule")
        rows = cast(
            list[tuple[str, str, str | None, str | None]],
            self._connection.execute(
                """
                SELECT s.source_id, s.watcher_type, c.configuration_json,
                       c.configuration_sha256
                FROM sources AS s
                LEFT JOIN source_configuration_revisions AS c
                  ON c.source_id = s.source_id
                 AND c.configuration_revision = s.current_configuration_revision
                WHERE s.enabled = 1
                ORDER BY s.source_id
                LIMIT ?
                """,
                (limit + 1,),
            ).fetchall(),
        )
        _reject_truncated_rows(rows, limit, "scheduled source configuration")
        for source_id, watcher_type, configuration_json, configuration_sha256 in rows:
            _validate_identifier(source_id, "stored source ID", 64)
            if configuration_json is None or configuration_sha256 is None:
                raise ControlStorageError("scheduled source configuration is missing")
            _ = _validate_stored_source_configuration(
                watcher_type,
                configuration_json,
                configuration_sha256,
                self._source_configuration_codecs,
            )

    def list_enabled_source_kinds(
        self,
        *,
        limit: int = 100,
    ) -> tuple[tuple[str, str], ...]:
        self._require_open()
        _validate_list_limit(limit, _MAX_ENABLED_SOURCES, "source")
        rows = cast(
            list[tuple[str, str]],
            self._connection.execute(
                """
                SELECT source_id, watcher_type
                FROM sources
                WHERE enabled = 1
                ORDER BY source_id
                LIMIT ?
                """,
                (limit + 1,),
            ).fetchall(),
        )
        _reject_truncated_rows(rows, limit, "enabled source")
        for source_id, watcher_type in rows:
            _validate_identifier(source_id, "stored source ID", 64)
            _validate_identifier(watcher_type, "stored watcher type", 64)
        return tuple(rows)

    def load_retry_policy(
        self,
        source_id: str,
        run_mode: ScheduledRunMode = "scheduled",
    ) -> RetryPolicy | None:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        _validate_scheduled_run_mode(run_mode)
        row = cast(
            tuple[int, float, float] | None,
            self._connection.execute(
                """
                SELECT retry_max_attempts, retry_base_seconds, retry_max_seconds
                FROM source_schedules
                WHERE source_id = ? AND run_mode = ?
                """,
                (source_id, run_mode),
            ).fetchone(),
        )
        if row is None:
            return None
        policy = RetryPolicy(*row)
        _validate_retry_policy(policy)
        return policy

    def load_source_state(self, source_id: str) -> PersistedSourceState:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        row = self._read_persisted_source_row(source_id, configuration_revision=None)
        if row is None:
            raise SourceNotFoundError("watcher source does not exist")
        return _persisted_source_state(row, self._source_configuration_codecs)

    def load_claim_state(self, claim: ClaimIdentity) -> PersistedSourceState:
        self._require_open()
        _validate_claim_identity(claim)
        run = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                SELECT 1 FROM watcher_runs
                WHERE run_id = ? AND source_id = ? AND claim_token = ?
                  AND fencing_epoch = ? AND configuration_revision = ?
                  AND deadline_at_ms = ? AND status = 'running'
                """,
                (
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    claim.configuration_revision,
                    claim.deadline_at_ms,
                ),
            ).fetchone(),
        )
        if run is None:
            raise StaleClaimError("watcher claim is stale or not running")
        row = self._read_persisted_source_row(
            claim.source_id,
            configuration_revision=claim.configuration_revision,
        )
        if row is None:
            raise ControlStorageError("claimed source configuration is missing")
        return _persisted_source_state(row, self._source_configuration_codecs)

    def acquire_claim(self, request: ClaimRequest) -> RunClaim:
        self._require_open()
        _validate_claim_request(request)
        with self._write_transaction() as now_ms:
            _validate_claim_timing(request, now_ms)
            return self._acquire_claim_in_transaction(request, now_ms)

    def claim_named_due(
        self,
        source_id: str,
        run_mode: ScheduledRunMode = "scheduled",
    ) -> RunClaim | None:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        _validate_scheduled_run_mode(run_mode)
        with self._write_transaction() as now_ms:
            obligation = self._read_due_obligation(source_id, run_mode, now_ms)
            if obligation is None:
                return None
            due_at_ms, claim_run_mode = obligation
            request = self._due_claim_request(
                source_id,
                due_at_ms,
                run_mode=claim_run_mode,
            )
            return self._acquire_claim_in_transaction(request, now_ms)

    def claim_one_overdue(self) -> RunClaim | None:
        self._require_open()
        with self._write_transaction() as now_ms:
            row = cast(
                tuple[str, str, str, int, str, str, str] | None,
                self._connection.execute(
                    """
                    SELECT s.source_id, s.watcher_type, ss.run_mode,
                           CASE
                               WHEN ss.pending_scheduled_at_ms IS NOT NULL
                               THEN ss.pending_scheduled_at_ms
                               ELSE ss.next_due_at_ms
                           END,
                           CASE
                               WHEN ss.pending_run_mode IS NOT NULL
                               THEN ss.pending_run_mode
                               WHEN ss.run_mode = 'weekly_verification'
                               THEN 'weekly_verification'
                               ELSE 'catch_up'
                           END,
                           c.configuration_json, c.configuration_sha256
                    FROM source_schedules AS ss
                    JOIN sources AS s ON s.source_id = ss.source_id
                    JOIN source_configuration_revisions AS c
                      ON c.source_id = s.source_id
                     AND c.configuration_revision = s.current_configuration_revision
                    WHERE s.enabled = 1
                      AND (
                          (ss.pending_scheduled_at_ms IS NULL
                              AND ss.next_due_at_ms <= ?)
                          OR ss.retry_not_before_ms <= ?
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM watcher_runs AS r
                          WHERE r.source_id = s.source_id
                            AND r.status IN (
                                'running', 'committed', 'heartbeat_pending'
                            )
                      )
                    ORDER BY
                        CASE ss.run_mode
                            WHEN 'weekly_verification' THEN 0
                            ELSE 1
                        END,
                        CASE
                            WHEN ss.retry_not_before_ms IS NOT NULL
                            THEN ss.retry_not_before_ms
                            ELSE ss.next_due_at_ms
                        END,
                        s.source_id
                    LIMIT 1
                    """,
                    (now_ms, now_ms),
                ).fetchone(),
            )
            if row is None:
                return None
            (
                source_id,
                watcher_type,
                scheduled_run_mode,
                due_at_ms,
                claim_run_mode,
                configuration_json,
                configuration_sha256,
            ) = row
            if not _is_scheduled_run_mode(scheduled_run_mode):
                raise ControlStorageError("stored scheduled run mode is invalid")
            if not _is_run_mode(claim_run_mode) or claim_run_mode == "manual":
                raise ControlStorageError("stored pending run mode is invalid")
            request = ClaimRequest(
                source_id=source_id,
                run_mode=claim_run_mode,
                scheduled_at_ms=due_at_ms,
                deadline_after_ms=_read_operation_deadline_ms(
                    watcher_type,
                    configuration_json,
                    configuration_sha256,
                    self._source_configuration_codecs,
                ),
                expected_due_at_ms=due_at_ms,
            )
            return self._acquire_claim_in_transaction(request, now_ms)

    def claim_manual(self, source_id: str) -> RunClaim:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        with self._write_transaction() as now_ms:
            (
                watcher_type,
                configuration_json,
                configuration_sha256,
            ) = self._read_enabled_configuration(source_id)
            request = ClaimRequest(
                source_id=source_id,
                run_mode="manual",
                scheduled_at_ms=now_ms,
                deadline_after_ms=_read_operation_deadline_ms(
                    watcher_type,
                    configuration_json,
                    configuration_sha256,
                    self._source_configuration_codecs,
                ),
            )
            return self._acquire_claim_in_transaction(request, now_ms)

    def _acquire_claim_in_transaction(
        self,
        request: ClaimRequest,
        now_ms: int,
    ) -> RunClaim:
        if not self._connection.in_transaction:
            raise ControlStorageError("claim acquisition requires a write transaction")
        _validate_claim_request(request)
        _validate_claim_timing(request, now_ms)
        source = cast(
            tuple[str, int, int | None, int, str | None, str | None] | None,
            self._connection.execute(
                """
                SELECT s.watcher_type, s.enabled, s.current_configuration_revision,
                       s.fencing_epoch, c.configuration_json,
                       c.configuration_sha256
                FROM sources AS s
                LEFT JOIN source_configuration_revisions AS c
                  ON c.source_id = s.source_id
                 AND c.configuration_revision = s.current_configuration_revision
                WHERE s.source_id = ?
                """,
                (request.source_id,),
            ).fetchone(),
        )
        if source is None:
            raise SourceNotFoundError("watcher source does not exist")
        (
            watcher_type,
            enabled,
            configuration_revision,
            fencing_epoch,
            configuration_json,
            configuration_sha256,
        ) = source
        if (
            enabled != 1
            or configuration_revision is None
            or configuration_json is None
            or configuration_sha256 is None
        ):
            raise ClaimBlockedError("watcher source is disabled or unconfigured")
        operation_deadline_ms = _read_operation_deadline_ms(
            watcher_type,
            configuration_json,
            configuration_sha256,
            self._source_configuration_codecs,
        )
        if request.deadline_after_ms > operation_deadline_ms:
            raise ControlValidationError(
                "claim deadline exceeds the configured operation deadline"
            )
        deadline_at_ms = now_ms + request.deadline_after_ms
        _validate_timestamp(deadline_at_ms, "claim deadline")
        self._expire_stale_source_claim(request.source_id, now_ms)
        self._reject_blocking_run(request.source_id)
        self._advance_due_cursor(request, now_ms)
        return self._insert_claim(
            request,
            now_ms=now_ms,
            deadline_at_ms=deadline_at_ms,
            configuration_revision=configuration_revision,
            fencing_epoch=fencing_epoch + 1,
        )

    def _insert_claim(
        self,
        request: ClaimRequest,
        *,
        now_ms: int,
        deadline_at_ms: int,
        configuration_revision: int,
        fencing_epoch: int,
    ) -> RunClaim:
        run_id = uuid4().hex
        claim_token = secrets.token_hex(32)
        _ = self._connection.execute(
            "UPDATE sources SET fencing_epoch = ?, updated_at_ms = ? WHERE source_id = ?",
            (fencing_epoch, now_ms, request.source_id),
        )
        _ = self._connection.execute(
            """
            INSERT INTO watcher_runs (
                run_id, source_id, run_mode, status, claim_token, fencing_epoch,
                configuration_revision, scheduled_at_ms, started_at_ms, deadline_at_ms
            ) VALUES (?, ?, ?, 'running', ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                request.source_id,
                request.run_mode,
                claim_token,
                fencing_epoch,
                configuration_revision,
                request.scheduled_at_ms,
                now_ms,
                deadline_at_ms,
            ),
        )
        state_revision = self._advance_state_revision(now_ms)
        self._insert_audit(
            source_id=request.source_id,
            run_id=run_id,
            event_type="run_claimed",
            details={
                "fencing_epoch": fencing_epoch,
                "run_mode": request.run_mode,
                "scheduled_at_ms": request.scheduled_at_ms,
            },
            state_revision=state_revision,
            now_ms=now_ms,
        )
        identity = ClaimIdentity(
            source_id=request.source_id,
            run_id=run_id,
            claim_token=claim_token,
            fencing_epoch=fencing_epoch,
            configuration_revision=configuration_revision,
            deadline_at_ms=deadline_at_ms,
        )
        return RunClaim(
            identity,
            request.run_mode,
            request.scheduled_at_ms,
            now_ms,
            state_revision,
            self.load_retry_policy(
                request.source_id,
                _scheduled_mode_for_claim(request.run_mode),
            )
            or RetryPolicy(1, 1.0, 1.0),
        )

    def fail_claim(
        self,
        claim: ClaimIdentity,
        *,
        error_category: str,
        incident: IncidentDraft | None = None,
    ) -> RunTransition:
        self._require_open()
        _validate_identifier(error_category, "error category", 64)
        if incident is not None:
            _validate_incident(incident)
        with self._write_transaction() as now_ms:
            self._require_live_claim(claim, now_ms)
            state_revision = self._advance_state_revision(now_ms)
            updated = self._connection.execute(
                """
                UPDATE watcher_runs
                SET status = 'failed', finished_at_ms = ?, error_category = ?
                WHERE run_id = ? AND source_id = ? AND claim_token = ?
                  AND fencing_epoch = ? AND status = 'running' AND deadline_at_ms > ?
                """,
                (
                    now_ms,
                    error_category,
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    now_ms,
                ),
            )
            _require_one_updated(updated, StaleClaimError("watcher claim became stale"))
            retry_not_before_ms = self._defer_claim_obligation(claim, now_ms)
            if incident is not None:
                self._insert_incident(
                    incident,
                    source_id=claim.source_id,
                    run_id=claim.run_id,
                    state_revision=state_revision,
                    now_ms=now_ms,
                )
            self._insert_audit(
                source_id=claim.source_id,
                run_id=claim.run_id,
                event_type="run_failed",
                details={
                    "error_category": error_category,
                    "incident_id": incident.incident_id
                    if incident is not None
                    else None,
                    "retry_not_before_ms": retry_not_before_ms,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return RunTransition(
                claim.source_id,
                claim.run_id,
                "failed",
                None,
                state_revision,
                True,
            )

    def abort_claim(
        self,
        claim: ClaimIdentity,
        *,
        error_category: str,
        incident: IncidentDraft,
    ) -> RunTransition:
        self._require_open()
        _validate_identifier(error_category, "error category", 64)
        _validate_incident(incident)
        with self._write_transaction() as now_ms:
            deadline_at_ms = self._require_running_claim(claim)
            status = "failed" if deadline_at_ms > now_ms else "expired"
            state_revision = self._advance_state_revision(now_ms)
            updated = self._connection.execute(
                """
                UPDATE watcher_runs
                SET status = ?, finished_at_ms = ?, error_category = ?
                WHERE run_id = ? AND source_id = ? AND claim_token = ?
                  AND fencing_epoch = ? AND configuration_revision = ?
                  AND deadline_at_ms = ? AND status = 'running'
                """,
                (
                    status,
                    now_ms,
                    error_category,
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    claim.configuration_revision,
                    claim.deadline_at_ms,
                ),
            )
            _require_one_updated(updated, StaleClaimError("watcher claim became stale"))
            retry_not_before_ms = self._defer_claim_obligation(claim, now_ms)
            self._insert_incident(
                incident,
                source_id=claim.source_id,
                run_id=claim.run_id,
                state_revision=state_revision,
                now_ms=now_ms,
            )
            self._insert_audit(
                source_id=claim.source_id,
                run_id=claim.run_id,
                event_type=f"run_{status}",
                details={
                    "error_category": error_category,
                    "incident_id": incident.incident_id,
                    "retry_not_before_ms": retry_not_before_ms,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return RunTransition(
                claim.source_id,
                claim.run_id,
                status,
                None,
                state_revision,
                True,
            )

    def list_stale_claims(self, *, limit: int = 100) -> tuple[StaleRunClaim, ...]:
        self._require_open()
        _validate_list_limit(limit, _MAX_STALE_CLAIMS, "stale claim")
        now_ms = _utc_now_ms()
        _validate_timestamp(now_ms, "stale claim read time")
        rows = cast(
            list[tuple[str, str, str, int, int, str, int, int, int]],
            self._connection.execute(
                """
                SELECT source_id, run_id, claim_token, fencing_epoch,
                       configuration_revision, run_mode, scheduled_at_ms,
                       started_at_ms, deadline_at_ms
                FROM watcher_runs
                WHERE status = 'running' AND deadline_at_ms <= ?
                ORDER BY deadline_at_ms, run_id
                LIMIT ?
                """,
                (now_ms, limit + 1),
            ).fetchall(),
        )
        _reject_truncated_rows(rows, limit, "stale claim")
        claims: list[StaleRunClaim] = []
        for row in rows:
            (
                source_id,
                run_id,
                claim_token,
                fencing_epoch,
                configuration_revision,
                run_mode,
                scheduled_at_ms,
                started_at_ms,
                deadline_at_ms,
            ) = row
            if not _is_run_mode(run_mode):
                raise ControlStorageError("stored run mode is invalid")
            identity = ClaimIdentity(
                source_id,
                run_id,
                claim_token,
                fencing_epoch,
                configuration_revision,
                deadline_at_ms,
            )
            _validate_claim_identity(identity)
            claims.append(
                StaleRunClaim(
                    identity,
                    run_mode,
                    scheduled_at_ms,
                    started_at_ms,
                )
            )
        return tuple(claims)

    def record_incident(
        self,
        incident: IncidentDraft,
        *,
        source_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        self._require_open()
        _validate_incident(incident)
        if source_id is None and run_id is not None:
            raise ControlValidationError("an incident run requires a source")
        if source_id is not None:
            _validate_identifier(source_id, "source ID", 64)
        if run_id is not None:
            _validate_identifier(run_id, "run ID", 128, minimum=16)
        with self._write_transaction() as now_ms:
            if source_id is not None:
                self._require_source(source_id)
            if run_id is not None:
                row = cast(
                    tuple[int] | None,
                    self._connection.execute(
                        "SELECT 1 FROM watcher_runs WHERE run_id = ? AND source_id = ?",
                        (run_id, source_id),
                    ).fetchone(),
                )
                if row is None:
                    raise ControlValidationError("incident run does not exist")
            state_revision = self._advance_state_revision(now_ms)
            details_json = _encode_json(incident.details, 65_536, "incident details")
            self._insert_incident_json(
                incident,
                details_json,
                source_id=source_id,
                run_id=run_id,
                state_revision=state_revision,
                now_ms=now_ms,
            )
            self._insert_audit(
                source_id=source_id,
                run_id=run_id,
                event_type="incident_recorded",
                details={
                    "incident_category": incident.incident_category,
                    "incident_id": incident.incident_id,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return state_revision

    def expire_claim(
        self,
        claim: ClaimIdentity,
        incident: IncidentDraft,
    ) -> RunTransition:
        self._require_open()
        _validate_incident(incident)
        with self._write_transaction() as now_ms:
            self._require_expired_claim(claim, now_ms)
            return self._expire_claim_in_transaction(claim, incident, now_ms)

    def _expire_stale_source_claim(self, source_id: str, now_ms: int) -> None:
        if not self._connection.in_transaction:
            raise ControlStorageError("stale claim expiry requires a write transaction")
        row = cast(
            tuple[str, str, int, int, int] | None,
            self._connection.execute(
                """
                SELECT run_id, claim_token, fencing_epoch,
                       configuration_revision, deadline_at_ms
                FROM watcher_runs
                WHERE source_id = ? AND status = 'running'
                  AND deadline_at_ms <= ?
                """,
                (source_id, now_ms),
            ).fetchone(),
        )
        if row is None:
            return
        run_id, claim_token, fencing_epoch, configuration_revision, deadline_at_ms = row
        claim = ClaimIdentity(
            source_id,
            run_id,
            claim_token,
            fencing_epoch,
            configuration_revision,
            deadline_at_ms,
        )
        _ = self._expire_claim_in_transaction(
            claim,
            IncidentDraft(
                uuid4().hex,
                "claim_deadline",
                {"phase": "pre_claim_expiry"},
            ),
            now_ms,
        )

    def _expire_claim_in_transaction(
        self,
        claim: ClaimIdentity,
        incident: IncidentDraft,
        now_ms: int,
    ) -> RunTransition:
        if not self._connection.in_transaction:
            raise ControlStorageError("claim expiry requires a write transaction")
        state_revision = self._advance_state_revision(now_ms)
        updated = self._connection.execute(
            """
            UPDATE watcher_runs
            SET status = 'expired', finished_at_ms = ?, error_category = ?
            WHERE run_id = ? AND source_id = ? AND claim_token = ?
              AND fencing_epoch = ? AND status = 'running' AND deadline_at_ms <= ?
            """,
            (
                now_ms,
                incident.incident_category,
                claim.run_id,
                claim.source_id,
                claim.claim_token,
                claim.fencing_epoch,
                now_ms,
            ),
        )
        _require_one_updated(updated, StaleClaimError("watcher claim became stale"))
        retry_not_before_ms = self._defer_claim_obligation(claim, now_ms)
        self._insert_incident(
            incident,
            source_id=claim.source_id,
            run_id=claim.run_id,
            state_revision=state_revision,
            now_ms=now_ms,
        )
        self._insert_audit(
            source_id=claim.source_id,
            run_id=claim.run_id,
            event_type="run_expired",
            details={
                "incident_id": incident.incident_id,
                "retry_not_before_ms": retry_not_before_ms,
            },
            state_revision=state_revision,
            now_ms=now_ms,
        )
        return RunTransition(
            claim.source_id,
            claim.run_id,
            "expired",
            None,
            state_revision,
            True,
        )

    def commit_observations(
        self,
        claim: ClaimIdentity,
        commit: ObservationCommit,
    ) -> CommittedRun:
        self._require_open()
        try:
            prepared = _prepare_observation_commit(commit)
            return self._commit_prepared_observations(claim, commit, prepared)
        except BlobVerificationError as error:
            self._record_blob_verification_failure(claim, reason=str(error))
            raise

    def _commit_prepared_observations(
        self,
        claim: ClaimIdentity,
        commit: ObservationCommit,
        prepared: _PreparedCommit,
    ) -> CommittedRun:
        with self._write_transaction() as now_ms:
            _validate_observation_times(commit, now_ms)
            self._require_live_claim(claim, now_ms)
            state_revision = self._advance_state_revision(now_ms)
            for receipt in commit.blobs:
                self._insert_or_validate_blob(receipt, now_ms)
            for observation, metadata_json in prepared.observations:
                self._insert_observation(
                    claim, observation, metadata_json, state_revision
                )
            for observation in commit.observations:
                for blob in observation.blobs:
                    _ = self._connection.execute(
                        """
                        INSERT INTO observation_blobs (
                            observation_id, blob_role, ordinal, sha256
                        ) VALUES (?, ?, ?, ?)
                        """,
                        (
                            observation.observation_id,
                            blob.blob_role,
                            blob.ordinal,
                            blob.sha256,
                        ),
                    )
            _ = self._connection.execute(
                """
                INSERT INTO source_cursors (
                    source_id, cursor_json, state_revision, updated_at_ms
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (source_id) DO UPDATE SET
                    cursor_json = excluded.cursor_json,
                    state_revision = excluded.state_revision,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (claim.source_id, prepared.cursor_json, state_revision, now_ms),
            )
            self._insert_changes(
                claim, commit, prepared.change_json, state_revision, now_ms
            )
            for incident, details_json in zip(
                commit.incidents,
                prepared.incident_json,
                strict=True,
            ):
                self._insert_incident_json(
                    incident,
                    details_json,
                    source_id=claim.source_id,
                    run_id=claim.run_id,
                    state_revision=state_revision,
                    now_ms=now_ms,
                )
            updated = self._connection.execute(
                """
                UPDATE watcher_runs
                SET status = 'committed', committed_at_ms = ?, committed_state_revision = ?
                WHERE run_id = ? AND source_id = ? AND claim_token = ?
                  AND fencing_epoch = ? AND status = 'running' AND deadline_at_ms > ?
                """,
                (
                    now_ms,
                    state_revision,
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    now_ms,
                ),
            )
            _require_one_updated(updated, StaleClaimError("watcher claim became stale"))
            self._insert_audit(
                source_id=claim.source_id,
                run_id=claim.run_id,
                event_type="observations_committed",
                details={
                    "change_count": len(commit.changes),
                    "incident_count": len(commit.incidents),
                    "observation_count": len(commit.observations),
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return CommittedRun(claim.source_id, claim.run_id, state_revision, now_ms)

    def _record_blob_verification_failure(
        self,
        claim: ClaimIdentity,
        *,
        reason: str,
    ) -> None:
        incident = IncidentDraft(
            uuid4().hex,
            "blob_verification_mismatch",
            {"reason": reason},
        )
        with self._write_transaction() as now_ms:
            deadline_at_ms = self._require_running_claim(claim)
            status = "failed" if deadline_at_ms > now_ms else "expired"
            state_revision = self._advance_state_revision(now_ms)
            updated = self._connection.execute(
                """
                UPDATE watcher_runs
                SET status = ?, finished_at_ms = ?, error_category = ?
                WHERE run_id = ? AND source_id = ? AND claim_token = ?
                  AND fencing_epoch = ? AND status = 'running'
                """,
                (
                    status,
                    now_ms,
                    incident.incident_category,
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                ),
            )
            _require_one_updated(updated, StaleClaimError("watcher claim became stale"))
            retry_not_before_ms = self._defer_claim_obligation(claim, now_ms)
            self._insert_incident(
                incident,
                source_id=claim.source_id,
                run_id=claim.run_id,
                state_revision=state_revision,
                now_ms=now_ms,
            )
            self._insert_audit(
                source_id=claim.source_id,
                run_id=claim.run_id,
                event_type="blob_verification_failed",
                details={
                    "incident_id": incident.incident_id,
                    "retry_not_before_ms": retry_not_before_ms,
                    "status": status,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )

    def mark_heartbeat_pending(
        self,
        run_id: str,
        *,
        committed_state_revision: int,
        recovery_receipt: RecoveryReceipt,
    ) -> RunTransition:
        _validate_recovery_receipt(
            recovery_receipt,
            run_id=run_id,
            committed_state_revision=committed_state_revision,
        )
        return self._reconcile_transition(
            run_id,
            expected_status="committed",
            target_status="heartbeat_pending",
            committed_state_revision=committed_state_revision,
            recovery_receipt=recovery_receipt,
        )

    def mark_successful(
        self,
        run_id: str,
        *,
        committed_state_revision: int,
    ) -> RunTransition:
        return self._reconcile_transition(
            run_id,
            expected_status="heartbeat_pending",
            target_status="successful",
            committed_state_revision=committed_state_revision,
            recovery_receipt=None,
        )

    def list_reconcilable_runs(self, *, limit: int = 100) -> tuple[CommittedRun, ...]:
        self._require_open()
        _validate_list_limit(limit, _MAX_RECONCILABLE_RUNS, "reconciliation")
        rows = cast(
            list[tuple[str, str, int, int, str]],
            self._connection.execute(
                """
                SELECT source_id, run_id, committed_state_revision,
                       committed_at_ms, status
                FROM watcher_runs
                WHERE status IN ('committed', 'heartbeat_pending')
                ORDER BY committed_state_revision, run_id
                LIMIT ?
                """,
                (limit + 1,),
            ).fetchall(),
        )
        _reject_truncated_rows(rows, limit, "reconcilable run")
        runs: list[CommittedRun] = []
        for source_id, run_id, revision, committed_at_ms, status in rows:
            if not _is_reconcilable_status(status):
                raise ControlStorageError("stored reconciliation status is invalid")
            runs.append(
                CommittedRun(
                    source_id,
                    run_id,
                    revision,
                    committed_at_ms,
                    status,
                )
            )
        return tuple(runs)

    def latest_recovery_reconciled_run(self, source_id: str) -> CommittedRun | None:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        row = cast(
            tuple[str, str, int, int, str] | None,
            self._connection.execute(
                """
                SELECT source_id, run_id, committed_state_revision,
                       committed_at_ms, status
                FROM watcher_runs
                WHERE source_id = ? AND committed_state_revision IS NOT NULL
                  AND status IN ('heartbeat_pending', 'successful')
                ORDER BY committed_state_revision DESC, run_id DESC
                LIMIT 1
                """,
                (source_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        stored_source_id, run_id, revision, committed_at_ms, status = row
        if status == "successful":
            return CommittedRun(
                stored_source_id,
                run_id,
                revision,
                committed_at_ms,
                "heartbeat_pending",
            )
        if not _is_reconcilable_status(status):
            raise ControlStorageError("stored committed run status is invalid")
        return CommittedRun(
            stored_source_id,
            run_id,
            revision,
            committed_at_ms,
            status,
        )

    def recovery_reconciled_run_at_revision(
        self,
        source_id: str,
        state_revision: int,
    ) -> CommittedRun:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        _validate_positive(state_revision, "public rebuild revision")
        row = cast(
            tuple[str, str, int, int, str] | None,
            self._connection.execute(
                """
                SELECT source_id, run_id, committed_state_revision,
                       committed_at_ms, status
                FROM watcher_runs
                WHERE source_id = ? AND committed_state_revision = ?
                  AND status IN ('heartbeat_pending', 'successful')
                LIMIT 1
                """,
                (source_id, state_revision),
            ).fetchone(),
        )
        if row is None:
            raise ControlStorageError(
                "public rebuild revision is not recovery reconciled"
            )
        stored_source_id, run_id, revision, committed_at_ms, _status = row
        return CommittedRun(
            stored_source_id,
            run_id,
            revision,
            committed_at_ms,
            "heartbeat_pending",
        )

    def load_committed_observation_history(
        self,
        source_id: str,
        observation_kind: str,
        *,
        through_state_revision: int,
        maximum_revisions: int = _MAX_PUBLIC_HISTORY_REVISIONS,
    ) -> CommittedObservationHistory:
        self._require_open()
        _validate_identifier(source_id, "source ID", 64)
        _validate_identifier(observation_kind, "observation kind", 64)
        _validate_positive(through_state_revision, "history fence revision")
        _validate_list_limit(
            maximum_revisions,
            _MAX_PUBLIC_HISTORY_REVISIONS,
            "history revision",
        )
        product_evidence, input_rows, input_bytes = self._load_product_evidence(
            source_id,
            through_state_revision,
        )
        revision_rows = cast(
            list[tuple[int, int]],
            self._connection.execute(
                """
                SELECT committed_state_revision, committed_at_ms
                FROM watcher_runs
                WHERE source_id = ? AND committed_state_revision <= ?
                  AND status IN ('committed', 'heartbeat_pending', 'successful')
                  AND EXISTS (
                      SELECT 1 FROM observations AS o
                      WHERE o.run_id = watcher_runs.run_id
                        AND o.source_id = watcher_runs.source_id
                        AND o.state_revision = watcher_runs.committed_state_revision
                        AND o.observation_kind = ?
                  )
                ORDER BY committed_state_revision DESC, run_id DESC
                LIMIT ?
                """,
                (
                    source_id,
                    through_state_revision,
                    observation_kind,
                    maximum_revisions + 1,
                ),
            ).fetchall(),
        )
        older_revisions_exist = len(revision_rows) > maximum_revisions
        selected_rows = revision_rows[:maximum_revisions]
        if not selected_rows:
            return CommittedObservationHistory(
                source_id,
                through_state_revision,
                (),
                False,
                product_evidence,
            )
        revisions_newest_first: list[CommittedObservationRevision] = []
        for revision, committed_at_ms in selected_rows:
            loaded = self._load_committed_observation_revision(
                source_id,
                observation_kind,
                revision,
                committed_at_ms,
                maximum_rows=_MAX_PUBLIC_HISTORY_INPUT_ROWS - input_rows,
                maximum_bytes=_MAX_PUBLIC_HISTORY_INPUT_BYTES - input_bytes,
            )
            if loaded is None:
                if not revisions_newest_first:
                    raise ControlStorageError(
                        "latest committed observation revision exceeds its input budget"
                    )
                older_revisions_exist = True
                break
            observations, revision_rows_used, revision_bytes = loaded
            input_rows += revision_rows_used
            input_bytes += revision_bytes
            revisions_newest_first.append(
                CommittedObservationRevision(
                    revision,
                    committed_at_ms,
                    observations,
                )
            )
        revisions = tuple(reversed(revisions_newest_first))
        return CommittedObservationHistory(
            source_id,
            through_state_revision,
            revisions,
            older_revisions_exist,
            product_evidence,
        )

    def _load_committed_observation_revision(
        self,
        source_id: str,
        observation_kind: str,
        revision: int,
        expected_committed_at_ms: int,
        *,
        maximum_rows: int,
        maximum_bytes: int,
    ) -> tuple[tuple[CommittedObservation, ...], int, int] | None:
        observations: list[CommittedObservation] = []
        input_bytes = 0
        after_observation_id = ""
        while True:
            rows = cast(
                list[tuple[int, str, str, str, str, int, str]],
                self._connection.execute(
                    """
                    SELECT r.committed_at_ms, o.observation_id,
                           o.observation_kind, o.source_identity,
                           o.response_sha256, o.observed_at_ms, o.metadata_json
                    FROM observations AS o
                    JOIN watcher_runs AS r
                      ON r.run_id = o.run_id AND r.source_id = o.source_id
                    WHERE o.source_id = ? AND o.observation_kind = ?
                      AND o.state_revision = ? AND o.observation_id > ?
                      AND r.committed_state_revision = o.state_revision
                      AND r.status IN ('committed', 'heartbeat_pending', 'successful')
                    ORDER BY o.observation_id
                    LIMIT ?
                    """,
                    (
                        source_id,
                        observation_kind,
                        revision,
                        after_observation_id,
                        _PUBLIC_HISTORY_PAGE_ROWS,
                    ),
                ).fetchall(),
            )
            if not rows:
                break
            for row in rows:
                (
                    committed_at_ms,
                    observation_id,
                    stored_kind,
                    identity,
                    digest,
                    observed_at_ms,
                    metadata_json,
                ) = row
                if committed_at_ms != expected_committed_at_ms:
                    raise ControlStorageError(
                        "committed observation fence is inconsistent"
                    )
                row_size_bytes = _public_history_row_size_bytes(row[1:])
                if (
                    len(observations) >= maximum_rows
                    or input_bytes + row_size_bytes > maximum_bytes
                ):
                    return None
                observations.append(
                    CommittedObservation(
                        observation_id,
                        stored_kind,
                        identity,
                        digest,
                        observed_at_ms,
                        _decode_stored_json(metadata_json, "observation metadata"),
                    )
                )
                input_bytes += row_size_bytes
            after_observation_id = rows[-1][1]
            if len(rows) < _PUBLIC_HISTORY_PAGE_ROWS:
                break
        if not observations:
            raise ControlStorageError("committed observation revision is incomplete")
        return tuple(observations), len(observations), input_bytes

    def _load_product_evidence(
        self,
        source_id: str,
        through_state_revision: int,
    ) -> tuple[tuple[CommittedProductEvidence, ...], int, int]:
        rows = cast(
            list[tuple[int, str, str, str, int, str, int, str | None]],
            self._connection.execute(
                """
                SELECT o.state_revision, o.observation_id, o.source_identity,
                       o.response_sha256, o.observed_at_ms, o.metadata_json,
                       response.size_bytes, response.media_type
                FROM observations AS o
                JOIN watcher_runs AS r
                  ON r.run_id = o.run_id AND r.source_id = o.source_id
                JOIN blobs AS response ON response.sha256 = o.response_sha256
                WHERE o.source_id = ? AND o.observation_kind = 'rsk_product'
                  AND o.state_revision <= ?
                  AND length(o.metadata_json) <= 1024
                  AND r.committed_state_revision = o.state_revision
                  AND r.status IN ('committed', 'heartbeat_pending', 'successful')
                ORDER BY o.state_revision DESC, o.observation_id DESC
                LIMIT ?
                """,
                (source_id, through_state_revision, _MAX_PUBLIC_PRODUCT_EVIDENCE + 1),
            ).fetchall(),
        )
        rows = rows[:_MAX_PUBLIC_PRODUCT_EVIDENCE]
        evidence: list[CommittedProductEvidence] = []
        input_rows = len(rows)
        input_bytes = 0
        for row in reversed(rows):
            (
                revision,
                observation_id,
                identity,
                digest,
                observed_at_ms,
                metadata_json,
                size_bytes,
                media_type,
            ) = row
            blobs = self._load_product_blob_evidence(observation_id)
            input_rows += len(blobs)
            input_bytes += _public_history_row_size_bytes(row) + sum(
                _public_history_row_size_bytes(
                    (
                        blob.blob_role,
                        blob.ordinal,
                        blob.sha256,
                        blob.size_bytes,
                        blob.media_type,
                    )
                )
                for blob in blobs
            )
            evidence.append(
                CommittedProductEvidence(
                    revision,
                    observation_id,
                    identity,
                    digest,
                    observed_at_ms,
                    _decode_stored_json(metadata_json, "product observation metadata"),
                    size_bytes,
                    media_type,
                    blobs,
                )
            )
        if (
            input_rows > _MAX_PUBLIC_HISTORY_INPUT_ROWS
            or input_bytes > _MAX_PUBLIC_HISTORY_INPUT_BYTES
        ):
            raise ControlStorageError(
                "committed product evidence exceeds its input budget"
            )
        return tuple(evidence), input_rows, input_bytes

    def _load_product_blob_evidence(
        self,
        observation_id: str,
    ) -> tuple[CommittedBlobEvidence, ...]:
        rows = cast(
            list[tuple[str, int, str, int, str | None]],
            self._connection.execute(
                """
                SELECT ob.blob_role, ob.ordinal, ob.sha256,
                       b.size_bytes, b.media_type
                FROM observation_blobs AS ob
                JOIN blobs AS b ON b.sha256 = ob.sha256
                WHERE ob.observation_id = ?
                ORDER BY ob.blob_role, ob.ordinal
                LIMIT ?
                """,
                (observation_id, _MAX_PUBLIC_PRODUCT_BLOBS + 1),
            ).fetchall(),
        )
        if len(rows) > _MAX_PUBLIC_PRODUCT_BLOBS:
            raise ControlStorageError("product blob evidence exceeds its bound")
        return tuple(CommittedBlobEvidence(*row) for row in rows)

    def _reconcile_transition(
        self,
        run_id: str,
        *,
        expected_status: str,
        target_status: str,
        committed_state_revision: int,
        recovery_receipt: RecoveryReceipt | None,
    ) -> RunTransition:
        self._require_open()
        _validate_identifier(run_id, "run ID", 128, minimum=16)
        _validate_positive(committed_state_revision, "committed state revision")
        with self._write_transaction() as now_ms:
            row = cast(
                tuple[str, str, int, int | None, str, int] | None,
                self._connection.execute(
                    """
                    SELECT source_id, status, committed_state_revision, finished_at_ms,
                           run_mode, scheduled_at_ms
                    FROM watcher_runs
                    WHERE run_id = ?
                    """,
                    (run_id,),
                ).fetchone(),
            )
            if row is None:
                raise IllegalRunTransitionError("watcher run does not exist")
            (
                source_id,
                status,
                stored_revision,
                finished_at_ms,
                run_mode,
                scheduled_at_ms,
            ) = row
            if not _is_run_mode(run_mode):
                raise ControlStorageError("stored run mode is invalid")
            if stored_revision != committed_state_revision:
                raise IllegalRunTransitionError(
                    "committed state revision does not match"
                )
            if status == target_status:
                return RunTransition(
                    source_id,
                    run_id,
                    status,
                    stored_revision,
                    self._read_state_revision(),
                    False,
                )
            if status != expected_status:
                raise IllegalRunTransitionError(
                    "watcher run transition would skip a state"
                )
            if target_status == "successful" and finished_at_ms is not None:
                raise IllegalRunTransitionError(
                    "successful watcher run is already finished"
                )
            if recovery_receipt is not None:
                current_state_revision = self._read_state_revision()
                if recovery_receipt.state_revision != current_state_revision:
                    raise IllegalRunTransitionError(
                        "recovery image does not match the current state revision"
                    )
            state_revision = self._advance_state_revision(now_ms)
            if target_status == "successful":
                updated = self._connection.execute(
                    """
                    UPDATE watcher_runs
                    SET status = 'successful', finished_at_ms = ?
                    WHERE run_id = ? AND status = 'heartbeat_pending'
                      AND committed_state_revision = ?
                    """,
                    (now_ms, run_id, committed_state_revision),
                )
            else:
                updated = self._connection.execute(
                    """
                    UPDATE watcher_runs
                    SET status = 'heartbeat_pending'
                    WHERE run_id = ? AND status = 'committed'
                      AND committed_state_revision = ?
                    """,
                    (run_id, committed_state_revision),
                )
            _require_one_updated(
                updated,
                IllegalRunTransitionError("watcher run transition lost its guard"),
            )
            if target_status == "successful":
                self._complete_claim_obligation(
                    source_id,
                    run_mode,
                    scheduled_at_ms,
                    now_ms,
                )
            self._insert_audit(
                source_id=source_id,
                run_id=run_id,
                event_type=f"run_{target_status}",
                details={
                    "committed_state_revision": committed_state_revision,
                    "recovery_archive_key": recovery_receipt.archive_key
                    if recovery_receipt is not None
                    else None,
                    "recovery_sha256": recovery_receipt.sha256
                    if recovery_receipt is not None
                    else None,
                    "recovery_state_revision": recovery_receipt.state_revision
                    if recovery_receipt is not None
                    else None,
                    "recovery_version_id": recovery_receipt.version_id
                    if recovery_receipt is not None
                    else None,
                },
                state_revision=state_revision,
                now_ms=now_ms,
            )
            return RunTransition(
                source_id,
                run_id,
                target_status,
                committed_state_revision,
                state_revision,
                True,
            )

    @contextmanager
    def _write_transaction(self) -> Generator[int]:
        if self._connection.in_transaction:
            raise ControlStorageError(
                "control writer connection is already in a transaction"
            )
        try:
            _ = self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as error:
            raise ControlStorageError(
                "control transaction could not acquire the writer lock"
            ) from error
        try:
            now_ms = _utc_now_ms()
            _validate_timestamp(now_ms, "transaction time")
            yield now_ms
        except BaseException as error:
            self._rollback_or_poison(error)
            if isinstance(error, sqlite3.Error):
                raise ControlStorageError(
                    "control transaction failed and was rolled back"
                ) from error
            raise
        try:
            self._connection.commit()
        except BaseException as error:
            self._rollback_or_poison(error)
            raise ControlStorageError(
                "control transaction commit failed and was rolled back"
            ) from error

    def _rollback_or_poison(self, cause: BaseException) -> None:
        if not self._connection.in_transaction:
            self.close()
            raise UncertainControlOutcomeError(
                "control transaction outcome is uncertain; writer was closed"
            ) from cause
        try:
            self._connection.rollback()
        except BaseException as rollback_error:
            self.close()
            raise UncertainControlOutcomeError(
                "control transaction rollback failed; writer was closed"
            ) from rollback_error

    def _read_source_configuration(
        self,
        source_id: str,
    ) -> tuple[str, int, str, str, bool] | None:
        row = cast(
            tuple[str, int, str, str, int] | None,
            self._connection.execute(
                """
                SELECT s.watcher_type, s.current_configuration_revision,
                       c.configuration_sha256, c.configuration_json, s.enabled
                FROM sources AS s
                JOIN source_configuration_revisions AS c
                  ON c.source_id = s.source_id
                 AND c.configuration_revision = s.current_configuration_revision
                WHERE s.source_id = ?
                """,
                (source_id,),
            ).fetchone(),
        )
        if row is None:
            return None
        watcher_type, revision, configuration_sha256, configuration_json, enabled = row
        return (
            watcher_type,
            revision,
            configuration_sha256,
            configuration_json,
            bool(enabled),
        )

    def _require_matching_bootstrap(
        self,
        command: SourceConfiguration,
        schedules: tuple[SourceSchedule, ...],
        configuration_json: str,
        configuration_sha256: str,
        current: tuple[str, int, str, str, bool],
    ) -> None:
        watcher_type, _revision, current_sha256, current_json, enabled = current
        _ = _validate_stored_source_configuration(
            watcher_type,
            current_json,
            current_sha256,
            self._source_configuration_codecs,
        )
        if (
            watcher_type != command.watcher_type
            or not enabled
            or current_sha256 != configuration_sha256
            or current_json != configuration_json
        ):
            raise BootstrapConflictError(
                "existing source state differs from bootstrap contract"
            )
        rows = cast(
            list[tuple[str, str, int, int, float, float]],
            self._connection.execute(
                """
                SELECT run_mode, cron_expression, misfire_grace_seconds,
                       retry_max_attempts, retry_base_seconds, retry_max_seconds
                FROM source_schedules
                WHERE source_id = ?
                ORDER BY run_mode
                """,
                (command.source_id,),
            ).fetchall(),
        )
        expected = sorted(
            (
                schedule.run_mode,
                schedule.cron_expression,
                schedule.misfire_grace_seconds,
                schedule.retry_max_attempts,
                schedule.retry_base_seconds,
                schedule.retry_max_seconds,
            )
            for schedule in schedules
        )
        if rows != expected:
            raise BootstrapConflictError(
                "existing source schedules differ from bootstrap contract"
            )

    def _read_persisted_source_row(
        self,
        source_id: str,
        *,
        configuration_revision: int | None,
    ) -> tuple[str, str, int, str, str, str | None, int | None] | None:
        if configuration_revision is None:
            return cast(
                tuple[str, str, int, str, str, str | None, int | None] | None,
                self._connection.execute(
                    """
                    SELECT s.source_id, s.watcher_type, c.configuration_revision,
                           c.configuration_sha256, c.configuration_json,
                           sc.cursor_json, sc.state_revision
                    FROM sources AS s
                    JOIN source_configuration_revisions AS c
                      ON c.source_id = s.source_id
                     AND c.configuration_revision = s.current_configuration_revision
                    LEFT JOIN source_cursors AS sc ON sc.source_id = s.source_id
                    WHERE s.source_id = ?
                    """,
                    (source_id,),
                ).fetchone(),
            )
        _validate_positive(configuration_revision, "configuration revision")
        return cast(
            tuple[str, str, int, str, str, str | None, int | None] | None,
            self._connection.execute(
                """
                SELECT s.source_id, s.watcher_type, c.configuration_revision,
                       c.configuration_sha256, c.configuration_json,
                       sc.cursor_json, sc.state_revision
                FROM sources AS s
                JOIN source_configuration_revisions AS c ON c.source_id = s.source_id
                LEFT JOIN source_cursors AS sc ON sc.source_id = s.source_id
                WHERE s.source_id = ? AND c.configuration_revision = ?
                """,
                (source_id, configuration_revision),
            ).fetchone(),
        )

    def _read_enabled_configuration(self, source_id: str) -> tuple[str, str, str]:
        row = cast(
            tuple[str, int, str | None, str | None] | None,
            self._connection.execute(
                """
                SELECT s.watcher_type, s.enabled, c.configuration_json,
                       c.configuration_sha256
                FROM sources AS s
                LEFT JOIN source_configuration_revisions AS c
                  ON c.source_id = s.source_id
                 AND c.configuration_revision = s.current_configuration_revision
                WHERE s.source_id = ?
                """,
                (source_id,),
            ).fetchone(),
        )
        if row is None:
            raise SourceNotFoundError("watcher source does not exist")
        watcher_type, enabled, configuration_json, configuration_sha256 = row
        if enabled != 1 or configuration_json is None or configuration_sha256 is None:
            raise ClaimBlockedError("watcher source is disabled or unconfigured")
        return watcher_type, configuration_json, configuration_sha256

    def _read_due_obligation(
        self,
        source_id: str,
        run_mode: ScheduledRunMode,
        now_ms: int,
    ) -> tuple[int, RunMode] | None:
        row = cast(
            tuple[int, str] | None,
            self._connection.execute(
                """
                SELECT
                    CASE
                        WHEN ss.pending_scheduled_at_ms IS NOT NULL
                        THEN ss.pending_scheduled_at_ms
                        ELSE ss.next_due_at_ms
                    END,
                    CASE
                        WHEN ss.pending_run_mode IS NOT NULL
                        THEN ss.pending_run_mode
                        ELSE ss.run_mode
                    END
                FROM source_schedules AS ss
                JOIN sources AS s ON s.source_id = ss.source_id
                WHERE ss.source_id = ? AND ss.run_mode = ? AND s.enabled = 1
                  AND (
                      (ss.pending_scheduled_at_ms IS NULL
                          AND ss.next_due_at_ms <= ?)
                      OR ss.retry_not_before_ms <= ?
                  )
                """,
                (source_id, run_mode, now_ms, now_ms),
            ).fetchone(),
        )
        if row is None:
            return None
        due_at_ms, claim_run_mode = row
        if not _is_run_mode(claim_run_mode) or claim_run_mode == "manual":
            raise ControlStorageError("stored pending run mode is invalid")
        return due_at_ms, claim_run_mode

    def _due_claim_request(
        self,
        source_id: str,
        due_at_ms: int,
        *,
        run_mode: RunMode,
    ) -> ClaimRequest:
        (
            watcher_type,
            configuration_json,
            configuration_sha256,
        ) = self._read_enabled_configuration(source_id)
        return ClaimRequest(
            source_id=source_id,
            run_mode=run_mode,
            scheduled_at_ms=due_at_ms,
            deadline_after_ms=_read_operation_deadline_ms(
                watcher_type,
                configuration_json,
                configuration_sha256,
                self._source_configuration_codecs,
            ),
            expected_due_at_ms=due_at_ms,
        )

    def _insert_disabled_source(
        self,
        command: SourceConfiguration,
        now_ms: int,
    ) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO sources (
                source_id, watcher_type, enabled, current_configuration_revision,
                fencing_epoch, created_at_ms, updated_at_ms
            ) VALUES (?, ?, 0, NULL, 0, ?, ?)
            """,
            (command.source_id, command.watcher_type, now_ms, now_ms),
        )

    def _insert_configuration_revision(
        self,
        source_id: str,
        configuration_revision: int,
        configuration_sha256: str,
        configuration_json: str,
        now_ms: int,
    ) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO source_configuration_revisions (
                source_id, configuration_revision, configuration_sha256,
                configuration_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                source_id,
                configuration_revision,
                configuration_sha256,
                configuration_json,
                now_ms,
            ),
        )

    def _insert_schedule(
        self,
        schedule: SourceSchedule,
        next_due_at_ms: int,
        now_ms: int,
    ) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO source_schedules (
                source_id, run_mode, cron_expression, next_due_at_ms,
                misfire_grace_seconds, retry_max_attempts,
                retry_base_seconds, retry_max_seconds, updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                schedule.source_id,
                schedule.run_mode,
                schedule.cron_expression,
                next_due_at_ms,
                schedule.misfire_grace_seconds,
                schedule.retry_max_attempts,
                schedule.retry_base_seconds,
                schedule.retry_max_seconds,
                now_ms,
            ),
        )

    def _require_source(self, source_id: str) -> None:
        if not self._source_exists(source_id):
            raise SourceNotFoundError("watcher source does not exist")

    def _source_exists(self, source_id: str) -> bool:
        exists = cast(
            tuple[int] | None,
            self._connection.execute(
                "SELECT 1 FROM sources WHERE source_id = ?",
                (source_id,),
            ).fetchone(),
        )
        return exists is not None

    def _reject_blocking_run(self, source_id: str) -> None:
        blocking = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                SELECT 1 FROM watcher_runs
                WHERE source_id = ?
                  AND status IN ('running', 'committed', 'heartbeat_pending')
                """,
                (source_id,),
            ).fetchone(),
        )
        if blocking is not None:
            raise ClaimBlockedError("watcher source already has a blocking run")

    def _advance_due_cursor(self, request: ClaimRequest, now_ms: int) -> None:
        if request.expected_due_at_ms is None:
            return
        scheduled_run_mode = _scheduled_mode_for_claim(request.run_mode)
        schedule = cast(
            tuple[str, int, int | None, str | None, int | None] | None,
            self._connection.execute(
                """
                SELECT cron_expression, next_due_at_ms, pending_scheduled_at_ms,
                       pending_run_mode, retry_not_before_ms
                FROM source_schedules
                WHERE source_id = ? AND run_mode = ?
                """,
                (request.source_id, scheduled_run_mode),
            ).fetchone(),
        )
        if schedule is None:
            raise ClaimBlockedError(
                "durable schedule cursor changed before claim acquisition"
            )
        (
            cron_expression,
            next_due_at_ms,
            pending_scheduled_at_ms,
            pending_run_mode,
            retry_not_before_ms,
        ) = schedule
        if pending_scheduled_at_ms is None:
            if next_due_at_ms != request.expected_due_at_ms:
                raise ClaimBlockedError(
                    "durable schedule cursor changed before claim acquisition"
                )
            next_due_at_ms = _next_cron_fire_ms(cron_expression, now_ms)
            updated = self._connection.execute(
                """
                UPDATE source_schedules
                SET next_due_at_ms = ?, pending_scheduled_at_ms = ?,
                    pending_run_mode = ?, retry_failure_count = 0,
                    updated_at_ms = ?
                WHERE source_id = ? AND run_mode = ? AND next_due_at_ms = ?
                  AND pending_scheduled_at_ms IS NULL
                """,
                (
                    next_due_at_ms,
                    request.scheduled_at_ms,
                    request.run_mode,
                    now_ms,
                    request.source_id,
                    scheduled_run_mode,
                    request.expected_due_at_ms,
                ),
            )
        else:
            if (
                pending_scheduled_at_ms != request.expected_due_at_ms
                or pending_run_mode != request.run_mode
                or retry_not_before_ms is None
                or retry_not_before_ms > now_ms
            ):
                raise ClaimBlockedError(
                    "durable schedule retry changed before claim acquisition"
                )
            updated = self._connection.execute(
                """
                UPDATE source_schedules
                SET retry_not_before_ms = NULL, updated_at_ms = ?
                WHERE source_id = ? AND run_mode = ?
                  AND pending_scheduled_at_ms = ? AND pending_run_mode = ?
                  AND retry_not_before_ms = ?
                """,
                (
                    now_ms,
                    request.source_id,
                    scheduled_run_mode,
                    request.expected_due_at_ms,
                    request.run_mode,
                    retry_not_before_ms,
                ),
            )
        _require_one_updated(
            updated,
            ClaimBlockedError(
                "durable schedule cursor changed before claim acquisition"
            ),
        )

    def _defer_claim_obligation(
        self,
        claim: ClaimIdentity,
        now_ms: int,
    ) -> int | None:
        row = cast(
            tuple[str, int, float, float, int, str | None] | None,
            self._connection.execute(
                """
                SELECT r.run_mode, r.scheduled_at_ms, ss.retry_base_seconds,
                       ss.retry_max_seconds, ss.retry_failure_count,
                       ss.pending_run_mode
                FROM watcher_runs AS r
                LEFT JOIN source_schedules AS ss
                  ON ss.source_id = r.source_id
                 AND ss.run_mode = CASE
                     WHEN r.run_mode = 'weekly_verification'
                     THEN 'weekly_verification'
                     ELSE 'scheduled'
                 END
                WHERE r.run_id = ? AND r.source_id = ?
                """,
                (claim.run_id, claim.source_id),
            ).fetchone(),
        )
        if row is None:
            raise ControlStorageError("failed watcher run is missing")
        (
            run_mode,
            scheduled_at_ms,
            retry_base_seconds,
            retry_max_seconds,
            failure_count,
            pending_run_mode,
        ) = row
        if run_mode == "manual":
            return None
        if not _is_run_mode(run_mode) or pending_run_mode != run_mode:
            raise ControlStorageError("durable schedule obligation is inconsistent")
        next_failure_count = min(failure_count + 1, _MAX_DURABLE_RETRY_FAILURES)
        delay_seconds = retry_base_seconds
        for _failure in range(1, next_failure_count):
            delay_seconds = min(delay_seconds * 2, retry_max_seconds)
        retry_not_before_ms = now_ms + max(1, math.ceil(delay_seconds * 1000))
        _validate_timestamp(retry_not_before_ms, "schedule retry time")
        scheduled_mode = _scheduled_mode_for_claim(run_mode)
        updated = self._connection.execute(
            """
            UPDATE source_schedules
            SET retry_not_before_ms = ?, retry_failure_count = ?, updated_at_ms = ?
            WHERE source_id = ? AND run_mode = ?
              AND pending_scheduled_at_ms = ? AND pending_run_mode = ?
              AND retry_not_before_ms IS NULL
            """,
            (
                retry_not_before_ms,
                next_failure_count,
                now_ms,
                claim.source_id,
                scheduled_mode,
                scheduled_at_ms,
                run_mode,
            ),
        )
        _require_one_updated(
            updated,
            ControlStorageError("durable schedule obligation could not be deferred"),
        )
        return retry_not_before_ms

    def _complete_claim_obligation(
        self,
        source_id: str,
        run_mode: RunMode,
        scheduled_at_ms: int,
        now_ms: int,
    ) -> None:
        if run_mode == "manual":
            return
        scheduled_mode = _scheduled_mode_for_claim(run_mode)
        updated = self._connection.execute(
            """
            UPDATE source_schedules
            SET pending_scheduled_at_ms = NULL, pending_run_mode = NULL,
                retry_not_before_ms = NULL, retry_failure_count = 0,
                updated_at_ms = ?
            WHERE source_id = ? AND run_mode = ?
              AND pending_scheduled_at_ms = ? AND pending_run_mode = ?
              AND retry_not_before_ms IS NULL
            """,
            (now_ms, source_id, scheduled_mode, scheduled_at_ms, run_mode),
        )
        _require_one_updated(
            updated,
            ControlStorageError("durable schedule obligation could not be completed"),
        )

    def _require_live_claim(self, claim: ClaimIdentity, now_ms: int) -> None:
        _validate_claim_identity(claim)
        row = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                SELECT 1
                FROM watcher_runs AS r
                JOIN sources AS s ON s.source_id = r.source_id
                WHERE r.run_id = ? AND r.source_id = ? AND r.claim_token = ?
                  AND r.fencing_epoch = ? AND r.configuration_revision = ?
                  AND r.deadline_at_ms = ? AND r.status = 'running'
                  AND r.deadline_at_ms > ? AND s.fencing_epoch = r.fencing_epoch
                """,
                (
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    claim.configuration_revision,
                    claim.deadline_at_ms,
                    now_ms,
                ),
            ).fetchone(),
        )
        if row is None:
            raise StaleClaimError("watcher claim is stale, expired, or not running")

    def _require_running_claim(self, claim: ClaimIdentity) -> int:
        _validate_claim_identity(claim)
        row = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                SELECT r.deadline_at_ms
                FROM watcher_runs AS r
                JOIN sources AS s ON s.source_id = r.source_id
                WHERE r.run_id = ? AND r.source_id = ? AND r.claim_token = ?
                  AND r.fencing_epoch = ? AND r.configuration_revision = ?
                  AND r.deadline_at_ms = ? AND r.status = 'running'
                  AND s.fencing_epoch = r.fencing_epoch
                """,
                (
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    claim.configuration_revision,
                    claim.deadline_at_ms,
                ),
            ).fetchone(),
        )
        if row is None:
            raise StaleClaimError("watcher claim is stale or not running")
        return row[0]

    def _require_expired_claim(self, claim: ClaimIdentity, now_ms: int) -> None:
        _validate_claim_identity(claim)
        row = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                SELECT 1
                FROM watcher_runs AS r
                JOIN sources AS s ON s.source_id = r.source_id
                WHERE r.run_id = ? AND r.source_id = ? AND r.claim_token = ?
                  AND r.fencing_epoch = ? AND r.configuration_revision = ?
                  AND r.deadline_at_ms = ? AND r.status = 'running'
                  AND r.deadline_at_ms <= ? AND s.fencing_epoch = r.fencing_epoch
                """,
                (
                    claim.run_id,
                    claim.source_id,
                    claim.claim_token,
                    claim.fencing_epoch,
                    claim.configuration_revision,
                    claim.deadline_at_ms,
                    now_ms,
                ),
            ).fetchone(),
        )
        if row is None:
            raise StaleClaimError("watcher claim is not eligible for expiry")

    def _advance_state_revision(self, now_ms: int) -> int:
        row = cast(
            tuple[int] | None,
            self._connection.execute(
                """
                UPDATE control_state
                SET state_revision = state_revision + 1, updated_at_ms = ?
                WHERE singleton_id = 1 AND updated_at_ms <= ?
                RETURNING state_revision
                """,
                (now_ms, now_ms),
            ).fetchone(),
        )
        if row is None or row[0] < 1:
            raise ControlStorageError(
                "control state revision could not advance with a monotonic timestamp"
            )
        return row[0]

    def _read_state_revision(self) -> int:
        row = cast(
            tuple[int] | None,
            self._connection.execute(
                "SELECT state_revision FROM control_state WHERE singleton_id = 1"
            ).fetchone(),
        )
        if row is None:
            raise ControlStorageError("control state singleton is missing")
        return row[0]

    def _insert_audit(
        self,
        *,
        source_id: str | None,
        run_id: str | None,
        event_type: str,
        details: dict[str, JsonValue],
        state_revision: int,
        now_ms: int,
    ) -> None:
        details_json = _encode_json(details, 65_536, "audit details")
        _ = self._connection.execute(
            """
            INSERT INTO audit_events (
                audit_event_id, source_id, run_id, event_type,
                details_json, state_revision, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                uuid4().hex,
                source_id,
                run_id,
                event_type,
                details_json,
                state_revision,
                now_ms,
            ),
        )

    def _insert_or_validate_blob(self, receipt: BlobReceipt, now_ms: int) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO blobs (sha256, size_bytes, media_type, created_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (sha256) DO NOTHING
            """,
            (receipt.sha256, receipt.size_bytes, receipt.media_type, now_ms),
        )
        stored = cast(
            tuple[int, str | None] | None,
            self._connection.execute(
                "SELECT size_bytes, media_type FROM blobs WHERE sha256 = ?",
                (receipt.sha256,),
            ).fetchone(),
        )
        if stored is None or stored[0] != receipt.size_bytes:
            raise BlobVerificationError("stored blob byte count conflicts with receipt")

    def _insert_observation(
        self,
        claim: ClaimIdentity,
        observation: ObservationDraft,
        metadata_json: str,
        state_revision: int,
    ) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO observations (
                observation_id, run_id, source_id, observation_kind,
                source_identity, response_sha256, observed_at_ms,
                metadata_json, state_revision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                observation.observation_id,
                claim.run_id,
                claim.source_id,
                observation.observation_kind,
                observation.source_identity,
                observation.response_sha256,
                observation.observed_at_ms,
                metadata_json,
                state_revision,
            ),
        )

    def _insert_changes(
        self,
        claim: ClaimIdentity,
        commit: ObservationCommit,
        encoded_changes: tuple[str, ...],
        state_revision: int,
        now_ms: int,
    ) -> None:
        for change, summary_json in zip(commit.changes, encoded_changes, strict=True):
            _ = self._connection.execute(
                """
                INSERT INTO source_changes (
                    change_id, run_id, source_id, observation_id,
                    change_category, summary_json, state_revision, created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.change_id,
                    claim.run_id,
                    claim.source_id,
                    change.observation_id,
                    change.change_category,
                    summary_json,
                    state_revision,
                    now_ms,
                ),
            )

    def _insert_incident(
        self,
        incident: IncidentDraft,
        *,
        source_id: str,
        run_id: str,
        state_revision: int,
        now_ms: int,
    ) -> None:
        details_json = _encode_json(incident.details, 65_536, "incident details")
        self._insert_incident_json(
            incident,
            details_json,
            source_id=source_id,
            run_id=run_id,
            state_revision=state_revision,
            now_ms=now_ms,
        )

    def _insert_incident_json(
        self,
        incident: IncidentDraft,
        details_json: str,
        *,
        source_id: str | None,
        run_id: str | None,
        state_revision: int,
        now_ms: int,
    ) -> None:
        _ = self._connection.execute(
            """
            INSERT INTO incidents (
                incident_id, source_id, run_id, incident_category,
                details_json, state_revision, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident.incident_id,
                source_id,
                run_id,
                incident.incident_category,
                details_json,
                state_revision,
                now_ms,
            ),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise ControlStorageError("control store is closed")


@dataclass(frozen=True, slots=True)
class _PreparedCommit:
    cursor_json: str
    observations: tuple[tuple[ObservationDraft, str], ...]
    change_json: tuple[str, ...]
    incident_json: tuple[str, ...]


def _initialize_writer_connection(
    connection: sqlite3.Connection,
    busy_timeout_ms: int,
) -> None:
    try:
        if connection.isolation_level is not None:
            raise ControlStorageError("control writer requires explicit transactions")
        if connection.row_factory is not None:
            raise ControlStorageError("control writer requires the default row factory")
        migration_result = apply_control_migrations(connection)
        if migration_result.forward_compatible:
            raise ControlStorageError(
                "a forward-compatible reader cannot become the writer"
            )
        _ = validate_control_database(connection)
        _ = connection.execute(f"PRAGMA busy_timeout = {busy_timeout_ms}")
        _ = connection.execute("PRAGMA journal_mode = WAL")
        _ = connection.execute("PRAGMA synchronous = FULL")
        _ = connection.execute("PRAGMA trusted_schema = OFF")
        _ = connection.execute("PRAGMA ignore_check_constraints = OFF")
        _ = connection.execute("PRAGMA read_uncommitted = OFF")
        _ = connection.execute("PRAGMA locking_mode = NORMAL")
        _ = connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
        _validate_writer_connection(connection, busy_timeout_ms)
    except MigrationError as error:
        raise ControlStorageError(
            "control database migration validation failed"
        ) from error
    except sqlite3.Error as error:
        raise ControlStorageError(
            "control writer connection initialization failed"
        ) from error


def _validate_writer_connection(
    connection: sqlite3.Connection,
    busy_timeout_ms: int,
) -> None:
    checks = (
        ("foreign_keys", 1),
        ("query_only", 0),
        ("synchronous", 2),
        ("trusted_schema", 0),
        ("ignore_check_constraints", 0),
        ("read_uncommitted", 0),
        ("busy_timeout", busy_timeout_ms),
        ("user_version", CONTROL_SCHEMA_VERSION),
    )
    for pragma, expected in checks:
        row = cast(
            tuple[int] | None,
            connection.execute(f"PRAGMA {pragma}").fetchone(),
        )
        if row is None or row[0] != expected:
            raise ControlStorageError(f"control writer requires PRAGMA {pragma}")
    journal_mode = cast(
        tuple[str] | None,
        connection.execute("PRAGMA journal_mode").fetchone(),
    )
    if journal_mode is None or journal_mode[0].lower() != "wal":
        raise ControlStorageError("control writer requires WAL journal mode")
    locking_mode = cast(
        tuple[str] | None,
        connection.execute("PRAGMA locking_mode").fetchone(),
    )
    if locking_mode is None or locking_mode[0].lower() != "normal":
        raise ControlStorageError("control writer requires normal locking mode")
    if not connection.getconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE):
        raise ControlStorageError("control writer requires SQLite defensive mode")
    if connection.in_transaction:
        raise ControlStorageError("control writer initialized inside a transaction")


def _read_positive_pragma(connection: sqlite3.Connection, name: str) -> int:
    row = cast(tuple[int] | None, connection.execute(f"PRAGMA {name}").fetchone())
    if row is None or not _is_strict_integer(row[0]) or row[0] < 1:
        raise ControlSnapshotError(f"control database PRAGMA {name} is invalid")
    return row[0]


def _validate_schedule(schedule: SourceSchedule) -> None:
    _validate_identifier(schedule.source_id, "source ID", 64)
    _validate_scheduled_run_mode(schedule.run_mode)
    _validate_identifier(
        schedule.cron_expression,
        "cron expression",
        256,
        minimum=9,
    )
    if " ".join(schedule.cron_expression.split()) != schedule.cron_expression:
        raise ControlValidationError("cron expression must use canonical spacing")
    _validate_cron_expression(schedule.cron_expression)
    if not _is_strict_integer(schedule.misfire_grace_seconds):
        raise ControlValidationError("misfire grace must be an integer")
    if schedule.misfire_grace_seconds < 1 or schedule.misfire_grace_seconds > 86_400:
        raise ControlValidationError("misfire grace is outside the supported range")
    _validate_retry_policy(
        RetryPolicy(
            schedule.retry_max_attempts,
            schedule.retry_base_seconds,
            schedule.retry_max_seconds,
        )
    )


def _validate_bootstrap_schedules(
    source_id: str,
    schedules: tuple[SourceSchedule, ...],
) -> None:
    if len(schedules) < 1 or len(schedules) > 2:
        raise ControlValidationError("bootstrap schedule count is unsupported")
    run_modes: set[ScheduledRunMode] = set()
    for schedule in schedules:
        _validate_schedule(schedule)
        if schedule.source_id != source_id:
            raise ControlValidationError("bootstrap schedule source differs")
        if schedule.run_mode in run_modes:
            raise ControlValidationError("bootstrap schedule run mode is duplicated")
        run_modes.add(schedule.run_mode)


def _validate_retry_policy(policy: RetryPolicy) -> None:
    if not _is_strict_integer(policy.max_attempts):
        raise ControlValidationError("retry attempts must be an integer")
    if policy.max_attempts < 1 or policy.max_attempts > 10:
        raise ControlValidationError("retry attempts are outside the supported range")
    if not _is_real_number(policy.base_seconds):
        raise ControlValidationError("retry base must be numeric")
    if policy.base_seconds <= 0 or policy.base_seconds > 300:
        raise ControlValidationError("retry base is outside the supported range")
    if not math.isfinite(policy.base_seconds):
        raise ControlValidationError("retry base must be finite")
    if not _is_real_number(policy.max_seconds):
        raise ControlValidationError("retry maximum must be numeric")
    if policy.max_seconds <= 0 or policy.max_seconds > 3600:
        raise ControlValidationError("retry maximum is outside the supported range")
    if not math.isfinite(policy.max_seconds):
        raise ControlValidationError("retry maximum must be finite")
    if policy.max_seconds < policy.base_seconds:
        raise ControlValidationError("retry maximum is lower than retry base")


def _validate_claim_request(request: ClaimRequest) -> None:
    _validate_identifier(request.source_id, "source ID", 64)
    if request.run_mode not in (
        "scheduled",
        "weekly_verification",
        "manual",
        "catch_up",
    ):
        raise ControlValidationError("run mode is unsupported")
    _validate_timestamp(request.scheduled_at_ms, "scheduled time")
    if not _is_strict_integer(request.deadline_after_ms):
        raise ControlValidationError("claim deadline duration must be an integer")
    if request.deadline_after_ms < 1 or request.deadline_after_ms > 3_600_000:
        raise ControlValidationError(
            "claim deadline duration is outside the supported range"
        )
    if (
        request.run_mode in ("scheduled", "weekly_verification", "catch_up")
        and request.expected_due_at_ms is None
    ):
        raise ControlValidationError("scheduled claims require a durable due cursor")
    if request.run_mode == "manual" and request.expected_due_at_ms is not None:
        raise ControlValidationError(
            "manual claims must not advance the durable due cursor"
        )
    if request.expected_due_at_ms is not None:
        _validate_timestamp(request.expected_due_at_ms, "expected due time")
        if request.scheduled_at_ms != request.expected_due_at_ms:
            raise ControlValidationError(
                "scheduled time must equal the durable due cursor"
            )


def _validate_claim_timing(request: ClaimRequest, now_ms: int) -> None:
    if request.scheduled_at_ms > now_ms:
        raise ControlValidationError(
            "a watcher claim cannot start before its scheduled time"
        )
    if request.expected_due_at_ms is not None:
        if request.expected_due_at_ms > now_ms:
            raise ControlValidationError("durable due cursor is not due")


def _validate_cron_expression(expression: str) -> None:
    _ = _parse_cron_expression(expression)


def _parse_cron_expression(expression: str) -> _CronSchedule:
    from apscheduler.triggers.cron import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
        CronTrigger,
    )

    cron_trigger = cast(_CronTriggerFactory, cast(object, CronTrigger))
    try:
        return cron_trigger.from_crontab(expression, timezone=UTC)
    except ValueError as error:
        raise ControlValidationError("cron expression is invalid") from error


def _next_cron_fire_ms(expression: str, now_ms: int) -> int:
    trigger = _parse_cron_expression(expression)
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    try:
        after_now = epoch + timedelta(milliseconds=now_ms + 1)
    except OverflowError as error:
        raise ControlValidationError(
            "cron schedule has no representable next fire"
        ) from error
    next_fire = trigger.get_next_fire_time(None, after_now)
    if next_fire is None:
        raise ControlValidationError("cron schedule has no next fire")
    normalized = next_fire.astimezone(UTC)
    elapsed = normalized - epoch
    next_fire_ms = (
        elapsed.days * 86_400_000
        + elapsed.seconds * 1000
        + elapsed.microseconds // 1000
    )
    _validate_timestamp(next_fire_ms, "next due time")
    if next_fire_ms <= now_ms:
        raise ControlValidationError(
            "cron next fire is not strictly after transaction time"
        )
    return next_fire_ms


def _validate_claim_identity(claim: ClaimIdentity) -> None:
    _validate_identifier(claim.source_id, "source ID", 64)
    _validate_identifier(claim.run_id, "run ID", 128, minimum=16)
    _validate_identifier(claim.claim_token, "claim token", 128, minimum=32)
    _validate_positive(claim.fencing_epoch, "fencing epoch")
    _validate_positive(claim.configuration_revision, "configuration revision")
    _validate_timestamp(claim.deadline_at_ms, "claim deadline")


def _prepare_observation_commit(
    commit: ObservationCommit,
) -> _PreparedCommit:
    _validate_bounded_count(commit.blobs, 1, _MAX_BLOBS_PER_COMMIT, "blobs")
    _validate_bounded_count(
        commit.observations,
        1,
        _MAX_OBSERVATIONS_PER_COMMIT,
        "observations",
    )
    _validate_bounded_count(commit.changes, 0, _MAX_CHANGES_PER_COMMIT, "changes")
    _validate_bounded_count(commit.incidents, 0, _MAX_INCIDENTS_PER_COMMIT, "incidents")
    blob_digests: set[str] = set()
    for receipt in commit.blobs:
        _validate_blob_receipt(receipt)
        if receipt.sha256 in blob_digests:
            raise ControlValidationError(
                "observation commit contains duplicate blob receipts"
            )
        blob_digests.add(receipt.sha256)
    observation_ids: set[str] = set()
    observation_blob_count = 0
    prepared_observations: list[tuple[ObservationDraft, str]] = []
    for observation in commit.observations:
        _validate_observation(observation, blob_digests)
        if observation.observation_id in observation_ids:
            raise ControlValidationError(
                "observation commit contains duplicate observations"
            )
        observation_ids.add(observation.observation_id)
        observation_blob_count += len(observation.blobs)
        prepared_observations.append(
            (
                observation,
                _encode_json(observation.metadata, 262_144, "observation metadata"),
            )
        )
    if observation_blob_count > _MAX_OBSERVATION_BLOBS_PER_COMMIT:
        raise ControlValidationError(
            "observation blob reference count exceeds its bound"
        )
    prepared_changes = tuple(
        _prepare_change(change, observation_ids) for change in commit.changes
    )
    prepared_incidents = tuple(_prepare_incident(item) for item in commit.incidents)
    return _PreparedCommit(
        cursor_json=_encode_json(commit.cursor, 262_144, "source cursor"),
        observations=tuple(prepared_observations),
        change_json=prepared_changes,
        incident_json=prepared_incidents,
    )


def _validate_observation_times(commit: ObservationCommit, now_ms: int) -> None:
    for observation in commit.observations:
        if observation.observed_at_ms > now_ms:
            raise ControlValidationError("observation time cannot be in the future")


def _validate_blob_receipt(receipt: BlobReceipt) -> None:
    _validate_sha256(receipt.sha256, "blob SHA-256")
    _validate_sha256(receipt.verified_sha256, "verified blob SHA-256")
    if receipt.verified_sha256 != receipt.sha256:
        raise BlobVerificationError("blob receipt checksum verification does not match")
    if (
        not _is_strict_integer(receipt.size_bytes)
        or receipt.size_bytes < 0
        or receipt.size_bytes > _MAX_BLOB_SIZE_BYTES
        or not _is_strict_integer(receipt.verified_size_bytes)
        or receipt.verified_size_bytes != receipt.size_bytes
    ):
        raise BlobVerificationError("blob receipt byte verification does not match")
    if receipt.archive_key != f"blobs/sha256/{receipt.sha256}":
        raise BlobVerificationError("blob receipt archive key is not digest-derived")
    if receipt.media_type is not None:
        _validate_identifier(receipt.media_type, "blob media type", 255)


def _validate_recovery_receipt(
    receipt: RecoveryReceipt,
    *,
    run_id: str,
    committed_state_revision: int,
) -> None:
    _validate_identifier(receipt.run_id, "recovery run ID", 128, minimum=16)
    _validate_positive(receipt.committed_state_revision, "recovery committed revision")
    _validate_positive(receipt.state_revision, "recovery state revision")
    _validate_sha256(receipt.sha256, "recovery image SHA-256")
    _validate_sha256(receipt.verified_sha256, "verified recovery image SHA-256")
    if receipt.run_id != run_id:
        raise ControlValidationError("recovery receipt belongs to another run")
    if receipt.committed_state_revision != committed_state_revision:
        raise ControlValidationError("recovery receipt has another committed revision")
    if receipt.state_revision < committed_state_revision:
        raise ControlValidationError("recovery image predates the committed run")
    if receipt.verified_sha256 != receipt.sha256:
        raise ControlValidationError(
            "recovery image checksum verification does not match"
        )
    if (
        not _is_strict_integer(receipt.size_bytes)
        or receipt.size_bytes < 1
        or receipt.size_bytes > _MAX_CONTROL_DATABASE_SIZE_BYTES
        or not _is_strict_integer(receipt.verified_size_bytes)
        or receipt.verified_size_bytes != receipt.size_bytes
    ):
        raise ControlValidationError("recovery image byte verification does not match")
    expected_key = (
        f"control-recovery/run/{receipt.state_revision:020d}/{receipt.sha256}.sqlite3"
    )
    if receipt.archive_key != expected_key:
        raise ControlValidationError(
            "recovery image archive key is not revision-derived"
        )
    if (
        len(receipt.version_id) < 1
        or len(receipt.version_id) > 1024
        or any(
            ord(character) < 0x21 or ord(character) > 0x7E
            for character in receipt.version_id
        )
    ):
        raise ControlValidationError("recovery image version ID is invalid")


def _validate_observation(
    observation: ObservationDraft,
    blob_digests: set[str],
) -> None:
    _validate_identifier(observation.observation_id, "observation ID", 128, minimum=16)
    _validate_identifier(observation.observation_kind, "observation kind", 64)
    _validate_identifier(observation.source_identity, "source identity", 512)
    _validate_sha256(observation.response_sha256, "response SHA-256")
    _validate_timestamp(observation.observed_at_ms, "observation time")
    if observation.response_sha256 not in blob_digests:
        raise ControlValidationError(
            "observation response blob has no verified receipt"
        )
    references: set[tuple[str, int]] = set()
    for blob in observation.blobs:
        _validate_identifier(blob.blob_role, "blob role", 64)
        if blob.ordinal < 0:
            raise ControlValidationError("blob ordinal must not be negative")
        _validate_sha256(blob.sha256, "observation blob SHA-256")
        if blob.sha256 not in blob_digests:
            raise ControlValidationError("observation blob has no verified receipt")
        identity = (blob.blob_role, blob.ordinal)
        if identity in references:
            raise ControlValidationError(
                "observation contains a duplicate blob role and ordinal"
            )
        references.add(identity)


def _prepare_change(change: ChangeDraft, observation_ids: set[str]) -> str:
    _validate_identifier(change.change_id, "change ID", 128, minimum=16)
    _validate_identifier(change.observation_id, "observation ID", 128, minimum=16)
    _validate_identifier(change.change_category, "change category", 64)
    if change.observation_id not in observation_ids:
        raise ControlValidationError(
            "change refers to an observation outside its commit"
        )
    return _encode_json(change.summary, 65_536, "change summary")


def _validate_incident(incident: IncidentDraft) -> None:
    _ = _prepare_incident(incident)


def _prepare_incident(incident: IncidentDraft) -> str:
    _validate_identifier(incident.incident_id, "incident ID", 128, minimum=16)
    _validate_identifier(incident.incident_category, "incident category", 64)
    return _encode_json(incident.details, 65_536, "incident details")


def _reject_sensitive_configuration(configuration: dict[str, JsonValue]) -> None:
    pending: list[JsonValue] = [configuration]
    visited = 0
    while pending:
        value = pending.pop()
        visited += 1
        if visited > 100_000:
            raise ControlValidationError(
                "configuration structure exceeds its node bound"
            )
        if isinstance(value, dict):
            for key, nested in value.items():
                snake_case = re.sub(r"(?<!^)(?=[A-Z])", "_", key)
                normalized = snake_case.lower().replace("-", "_")
                if normalized in _SENSITIVE_CONFIGURATION_KEYS or normalized.endswith(
                    _SENSITIVE_CONFIGURATION_SUFFIXES
                ):
                    raise ControlValidationError(
                        "configuration contains a secret-like key"
                    )
                pending.append(nested)
        elif isinstance(value, list):
            pending.extend(value)


def _validate_source_configuration(
    command: SourceConfiguration,
    codecs: SourceConfigurationCodecRegistry,
) -> tuple[SourceConfigurationCodec, dict[str, JsonValue]]:
    codec = codecs.resolve(command.watcher_type)
    if codec is None:
        raise ControlValidationError(
            "watcher type has no registered configuration codec"
        )
    validated = _canonicalize_source_configuration(codec, command.configuration)
    if validated is None:
        raise ControlValidationError("source configuration is invalid")
    return codec, validated


def _canonicalize_source_configuration(
    codec: SourceConfigurationCodec,
    configuration: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    copied = _copy_source_configuration(configuration)
    if copied is None:
        return None
    try:
        validated = _configuration_dict(codec.canonicalize(copied))
    except Exception:
        return None
    return validated


def _configuration_dict(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise TypeError("source configuration codec did not return an object")
    return cast(dict[str, JsonValue], value)


def _copy_source_configuration(
    configuration: dict[str, JsonValue],
) -> dict[str, JsonValue] | None:
    try:
        encoded = _encode_json(configuration, 65_536, "configuration")
        decoded = cast(object, json.loads(encoded))
    except Exception:
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(dict[str, JsonValue], decoded)


def _canonical_source_configuration_json(
    configuration: dict[str, JsonValue],
) -> str | None:
    try:
        _reject_sensitive_configuration(configuration)
        return _encode_json(configuration, 65_536, "configuration")
    except Exception:
        return None


def _codec_claim_timeout_ms(
    codec: SourceConfigurationCodec,
    canonical_configuration_json: str,
) -> int | None:
    try:
        timeout_ms = codec.claim_timeout_ms(canonical_configuration_json)
    except Exception:
        return None
    if not _is_strict_integer(timeout_ms) or timeout_ms < 1 or timeout_ms > 3_600_000:
        return None
    return timeout_ms


def _read_operation_deadline_ms(
    watcher_type: str,
    configuration_json: str,
    configuration_sha256: str,
    codecs: SourceConfigurationCodecRegistry,
) -> int:
    _configuration, deadline_ms = _validate_stored_source_configuration(
        watcher_type,
        configuration_json,
        configuration_sha256,
        codecs,
    )
    return deadline_ms


def _persisted_source_state(
    row: tuple[str, str, int, str, str, str | None, int | None],
    codecs: SourceConfigurationCodecRegistry,
) -> PersistedSourceState:
    (
        source_id,
        watcher_type,
        configuration_revision,
        configuration_sha256,
        configuration_json,
        cursor_json,
        cursor_state_revision,
    ) = row
    _validate_identifier(source_id, "stored source ID", 64)
    _validate_identifier(watcher_type, "stored watcher type", 64)
    _validate_positive(configuration_revision, "stored configuration revision")
    validated, _deadline_ms = _validate_stored_source_configuration(
        watcher_type,
        configuration_json,
        configuration_sha256,
        codecs,
    )
    if cursor_json is None:
        if cursor_state_revision is not None:
            raise ControlStorageError("stored source cursor revision has no cursor")
        cursor: dict[str, JsonValue] = {}
    else:
        if cursor_state_revision is None:
            raise ControlStorageError("stored source cursor has no revision")
        _validate_positive(cursor_state_revision, "stored cursor state revision")
        cursor = _decode_stored_json(cursor_json, "source cursor")
        try:
            if _encode_json(cursor, 262_144, "source cursor") != cursor_json:
                raise ControlStorageError("stored source cursor is not canonical")
        except ControlValidationError as error:
            raise ControlStorageError("stored source cursor is invalid") from error
    return PersistedSourceState(
        source_id,
        watcher_type,
        configuration_revision,
        validated,
        cursor,
        cursor_state_revision,
    )


def _resolve_stored_configuration_codec(
    watcher_type: str,
    codecs: SourceConfigurationCodecRegistry,
) -> SourceConfigurationCodec:
    _validate_identifier(watcher_type, "stored watcher type", 64)
    codec = codecs.resolve(watcher_type)
    if codec is None:
        raise ControlStorageError(
            "stored watcher type has no registered configuration codec"
        )
    return codec


def _validate_stored_source_configuration(
    watcher_type: str,
    configuration_json: str,
    configuration_sha256: str,
    codecs: SourceConfigurationCodecRegistry,
) -> tuple[dict[str, JsonValue], int]:
    if sha256(configuration_json.encode("utf-8")).hexdigest() != configuration_sha256:
        raise ControlStorageError("stored source configuration checksum differs")
    configuration = _decode_stored_json(configuration_json, "source configuration")
    codec = _resolve_stored_configuration_codec(watcher_type, codecs)
    validated = _canonicalize_source_configuration(codec, configuration)
    if validated is None:
        raise ControlStorageError("stored source configuration is invalid")
    canonical_json = _canonical_source_configuration_json(validated)
    if canonical_json is None:
        raise ControlStorageError("stored source configuration is invalid")
    if canonical_json != configuration_json:
        raise ControlStorageError("stored source configuration is not canonical")
    deadline_ms = _codec_claim_timeout_ms(codec, configuration_json)
    if deadline_ms is None:
        raise ControlStorageError("stored source claim timeout is invalid")
    return validated, deadline_ms


def _decode_stored_json(encoded: str, label: str) -> dict[str, JsonValue]:
    try:
        decoded = cast(object, json.loads(encoded))
    except (json.JSONDecodeError, RecursionError) as error:
        raise ControlStorageError(f"stored {label} is invalid") from error
    if not isinstance(decoded, dict):
        raise ControlStorageError(f"stored {label} is not an object")
    return cast(dict[str, JsonValue], decoded)


def _validate_scheduled_projection(schedule: ScheduledSource) -> None:
    _validate_identifier(schedule.source_id, "stored source ID", 64)
    _validate_identifier(schedule.watcher_type, "stored watcher type", 64)
    _validate_timestamp(schedule.next_due_at_ms, "stored next due time")
    _validate_schedule(
        SourceSchedule(
            schedule.source_id,
            schedule.cron_expression,
            schedule.misfire_grace_seconds,
            schedule.retry_policy.max_attempts,
            schedule.retry_policy.base_seconds,
            schedule.retry_policy.max_seconds,
            schedule.run_mode,
        )
    )


def _scheduled_source_from_row(
    row: tuple[str, str, str, str, int, int, int, float, float],
) -> ScheduledSource:
    (
        source_id,
        watcher_type,
        run_mode,
        cron_expression,
        next_due_at_ms,
        misfire_grace_seconds,
        retry_max_attempts,
        retry_base_seconds,
        retry_max_seconds,
    ) = row
    if not _is_scheduled_run_mode(run_mode):
        raise ControlStorageError("stored scheduled run mode is invalid")
    return ScheduledSource(
        source_id,
        watcher_type,
        cron_expression,
        next_due_at_ms,
        misfire_grace_seconds,
        RetryPolicy(
            retry_max_attempts,
            retry_base_seconds,
            retry_max_seconds,
        ),
        run_mode,
    )


def _validate_list_limit(limit: int, maximum: int, label: str) -> None:
    if not _is_strict_integer(limit) or limit < 1 or limit > maximum:
        raise ControlValidationError(f"{label} limit is outside the supported range")


def _public_history_row_size_bytes(values: tuple[object, ...]) -> int:
    size_bytes = 8 * len(values)
    for value in values:
        if isinstance(value, str):
            size_bytes += len(value.encode("utf-8"))
    return size_bytes


def _reject_truncated_rows(rows: Sequence[object], limit: int, label: str) -> None:
    if len(rows) > limit:
        raise ControlStorageError(f"{label} listing exceeds its configured bound")


def _encode_json(value: dict[str, JsonValue], maximum_bytes: int, label: str) -> str:
    _validate_json_shape(value, label)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (RecursionError, TypeError, ValueError) as error:
        raise ControlValidationError(f"{label} is not canonical JSON") from error
    encoded_size = len(encoded.encode())
    if encoded_size < 2 or encoded_size > maximum_bytes:
        raise ControlValidationError(f"{label} size is outside the supported range")
    return encoded


def _validate_identifier(
    value: object,
    label: str,
    maximum: int,
    *,
    minimum: int = 1,
) -> None:
    if not isinstance(value, str):
        raise ControlValidationError(f"{label} must be text")
    if len(value) < minimum or len(value) > maximum:
        raise ControlValidationError(f"{label} length is outside the supported range")


def _validate_timestamp(value: object, label: str) -> None:
    if not _is_strict_integer(value):
        raise ControlValidationError(f"{label} must be an integer")
    if value < 1 or value > _MAX_TIMESTAMP_MS:
        raise ControlValidationError(f"{label} is outside the supported range")


def _validate_positive(value: object, label: str) -> None:
    if not _is_strict_integer(value):
        raise ControlValidationError(f"{label} must be an integer")
    if value < 1:
        raise ControlValidationError(f"{label} must be positive")


def _validate_boolean(value: object, label: str) -> None:
    if not isinstance(value, bool):
        raise ControlValidationError(f"{label} must be boolean")


def _is_strict_integer(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_real_number(value: object) -> TypeIs[int | float]:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_run_mode(value: object) -> TypeIs[RunMode]:
    return value in ("scheduled", "weekly_verification", "manual", "catch_up")


def _is_scheduled_run_mode(value: object) -> TypeIs[ScheduledRunMode]:
    return value in ("scheduled", "weekly_verification")


def _validate_scheduled_run_mode(value: object) -> None:
    if not _is_scheduled_run_mode(value):
        raise ControlValidationError("scheduled run mode is unsupported")


def _scheduled_mode_for_claim(run_mode: RunMode) -> ScheduledRunMode:
    if run_mode == "weekly_verification":
        return "weekly_verification"
    return "scheduled"


def _is_reconcilable_status(value: object) -> TypeIs[ReconcilableStatus]:
    return value in ("committed", "heartbeat_pending")


def _validate_sha256(value: object, label: str) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ControlValidationError(f"{label} must be lowercase hexadecimal")


def _validate_bounded_count(
    values: tuple[object, ...],
    minimum: int,
    maximum: int,
    label: str,
) -> None:
    if len(values) < minimum or len(values) > maximum:
        raise ControlValidationError(f"{label} count is outside the supported range")


def _require_one_updated(cursor: sqlite3.Cursor, error: ControlStoreError) -> None:
    if cursor.rowcount != 1:
        raise error


def _utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _validate_json_shape(value: dict[str, JsonValue], label: str) -> None:
    pending: list[tuple[JsonValue, int]] = [(value, 1)]
    visited = 0
    seen_containers: set[int] = set()
    while pending:
        current, depth = pending.pop()
        visited += 1
        if visited > 100_000 or depth > 64:
            raise ControlValidationError(f"{label} structure exceeds its bound")
        if isinstance(current, dict):
            identity = id(current)
            if identity in seen_containers:
                raise ControlValidationError(f"{label} contains a repeated container")
            seen_containers.add(identity)
            for _key, nested in current.items():
                pending.append((nested, depth + 1))
        elif isinstance(current, list):
            identity = id(current)
            if identity in seen_containers:
                raise ControlValidationError(f"{label} contains a repeated container")
            seen_containers.add(identity)
            pending.extend((nested, depth + 1) for nested in current)
