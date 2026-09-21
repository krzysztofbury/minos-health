import fcntl
import hashlib
import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from types import TracebackType
from typing import Self, cast, final
from uuid import uuid4

from minos.ingest.watchers.control import (
    ControlStore,
    IncidentDraft,
    ScheduledSource,
    StaleClaimError,
    validate_control_database,
)
from minos.ingest.watchers.orchestrator import CompletedRun, WatcherOrchestrator
from minos.ingest.watchers.protocols import (
    CapacityGate,
    CollectorRegistry,
    WatcherRuntimeError,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass


class ProcessLockError(WatcherRuntimeError):
    """Another watcher process owns the runtime lock."""


class WatcherReadinessError(WatcherRuntimeError):
    """The watcher process has not reached or retained its ready state."""


_READINESS_MAX_AGE_MS = 75 * 60 * 1000
_READINESS_MAX_FUTURE_SKEW_MS = 5000


def watcher_process_lock_path(database_path: str | Path) -> Path:
    return Path(database_path).parent / "watcher.lock"


@final
class WatcherProcessLock:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._descriptor = -1

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        _error_type: type[BaseException] | None,
        _error: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.release()

    def acquire(self) -> None:
        if self._descriptor >= 0:
            raise ProcessLockError("watcher process lock is already held")
        descriptor = -1
        try:
            descriptor = os.open(
                self._path,
                os.O_RDWR
                | os.O_CREAT
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            status = os.fstat(descriptor)
            if not stat.S_ISREG(status.st_mode):
                raise ProcessLockError("watcher process lock is not a regular file")
            if status.st_uid != os.geteuid() or stat.S_IMODE(status.st_mode) != 0o600:
                raise ProcessLockError(
                    "watcher process lock ownership or mode is unsafe"
                )
            if status.st_nlink != 1:
                raise ProcessLockError("watcher process lock link count is unsafe")
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except ProcessLockError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except BlockingIOError, OSError:
            if descriptor >= 0:
                os.close(descriptor)
            raise ProcessLockError("watcher process lock is already held") from None
        self._descriptor = descriptor

    def release(self) -> None:
        if self._descriptor < 0:
            return
        descriptor = self._descriptor
        self._descriptor = -1
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


@final
class WatcherReadiness:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def reset(self) -> None:
        try:
            self._path.unlink(missing_ok=True)
            self._temporary_path().unlink(missing_ok=True)
        except OSError:
            raise WatcherReadinessError(
                "watcher readiness state cannot be reset"
            ) from None

    def mark_ready(self) -> None:
        process_start_ticks = _process_start_ticks(os.getpid())
        payload = json.dumps(
            {
                "pid": os.getpid(),
                "process_start_ticks": process_start_ticks,
                "updated_at_ms": time.time_ns() // 1_000_000,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(payload) > 256:
            raise WatcherReadinessError("watcher readiness state exceeds its bound")
        temporary_path = self._temporary_path()
        descriptor = -1
        try:
            descriptor = os.open(
                temporary_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            if os.write(descriptor, payload) != len(payload):
                raise WatcherReadinessError(
                    "watcher readiness state write was incomplete"
                )
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, self._path)
        except WatcherReadinessError:
            raise
        except OSError:
            raise WatcherReadinessError(
                "watcher readiness state cannot be written"
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)

    def _temporary_path(self) -> Path:
        return self._path.with_name(f".{self._path.name}.tmp")

    def check(self) -> None:
        descriptor = -1
        try:
            descriptor = os.open(
                self._path,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            )
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != os.geteuid()
                or stat.S_IMODE(status.st_mode) != 0o600
                or status.st_nlink != 1
                or status.st_size < 1
                or status.st_size > 256
            ):
                raise WatcherReadinessError("watcher readiness state is unsafe")
            payload = os.read(descriptor, 257)
        except WatcherReadinessError:
            raise
        except OSError:
            raise WatcherReadinessError(
                "watcher readiness state is unavailable"
            ) from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            value = cast(object, json.loads(payload))
        except json.JSONDecodeError, UnicodeDecodeError:
            raise WatcherReadinessError("watcher readiness state is invalid") from None
        if not isinstance(value, dict):
            raise WatcherReadinessError("watcher readiness state is invalid")
        document = cast(dict[object, object], value)
        if set(document) != {"pid", "process_start_ticks", "updated_at_ms"}:
            raise WatcherReadinessError("watcher readiness state is invalid")
        pid = document["pid"]
        process_start_ticks = document["process_start_ticks"]
        updated_at_ms = document["updated_at_ms"]
        now_ms = time.time_ns() // 1_000_000
        if (
            not isinstance(pid, int)
            or isinstance(pid, bool)
            or pid < 2
            or not isinstance(process_start_ticks, int)
            or isinstance(process_start_ticks, bool)
            or process_start_ticks < 1
            or not isinstance(updated_at_ms, int)
            or isinstance(updated_at_ms, bool)
            or updated_at_ms < 1
            or _process_start_ticks(pid) != process_start_ticks
        ):
            raise WatcherReadinessError("watcher ready process is not running")
        if (
            updated_at_ms > now_ms + _READINESS_MAX_FUTURE_SKEW_MS
            or now_ms - updated_at_ms > _READINESS_MAX_AGE_MS
        ):
            raise WatcherReadinessError("watcher scheduler worker is not responsive")


def check_watcher_database_readiness(database_path: Path) -> None:
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        _ = validate_control_database(connection)
    except OSError, sqlite3.Error:
        raise WatcherReadinessError("watcher control database is not ready") from None
    finally:
        if connection is not None:
            connection.close()


def _process_start_ticks(pid: int) -> int:
    try:
        encoded = Path(f"/proc/{pid}/stat").read_bytes()
        if len(encoded) < 4 or len(encoded) > 4096:
            raise WatcherReadinessError("watcher process identity is invalid")
        closing_parenthesis = encoded.rfind(b")")
        fields = encoded[closing_parenthesis + 2 :].split()
        value = int(fields[19])
    except IndexError, OSError, ValueError:
        raise WatcherReadinessError("watcher process identity is unavailable") from None
    if closing_parenthesis < 1 or value < 1:
        raise WatcherReadinessError("watcher process identity is invalid")
    return value


@final
class WatcherService:
    def __init__(
        self,
        *,
        store: ControlStore,
        orchestrator: WatcherOrchestrator,
        collectors: CollectorRegistry,
        capacity_gate: CapacityGate,
    ) -> None:
        self._store = store
        self._orchestrator = orchestrator
        self._collectors = collectors
        self._capacity_gate = capacity_gate

    def validate_scheduled_collectors(self, *, limit: int = 100) -> None:
        self._store.validate_scheduled_source_configurations(limit=limit)
        for source in self._store.list_scheduled_sources(limit=limit):
            _ = self._collectors.resolve(source.watcher_type)

    def reconcile(self, *, limit: int = 100) -> None:
        self._orchestrator.reconcile_all(limit=limit)

    def expire_stale_claims(self, *, limit: int = 100) -> None:
        for stale in self._store.list_stale_claims(limit=limit):
            try:
                _ = self._store.expire_claim(
                    stale.identity,
                    IncidentDraft(
                        uuid4().hex,
                        "claim_deadline",
                        {"phase": "startup_expiry"},
                    ),
                )
            except StaleClaimError:
                continue

    def scheduled_sources(self, *, limit: int = 100) -> tuple[ScheduledSource, ...]:
        return self._store.list_scheduled_sources(limit=limit)

    def run_due(self, source_id: str) -> CompletedRun | None:
        source = self._store.load_source_state(source_id)
        _ = self._collectors.resolve(source.watcher_type)
        self._require_capacity(source_id=source_id)
        claim = self._store.claim_named_due(source_id)
        if claim is None:
            return None
        return self._orchestrator.run_claim(claim, claim.retry_policy)

    def run_one_catch_up(self) -> CompletedRun | None:
        self._require_capacity(source_id=None)
        claim = self._store.claim_one_overdue()
        if claim is None:
            return None
        return self._orchestrator.run_claim(claim, claim.retry_policy)

    def run_manual(self, source_id: str) -> CompletedRun:
        source = self._store.load_source_state(source_id)
        _ = self._collectors.resolve(source.watcher_type)
        self._require_capacity(source_id=source_id)
        claim = self._store.claim_manual(source_id)
        return self._orchestrator.run_claim(claim, claim.retry_policy)

    def publish_periodic_recovery(
        self,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        self._require_capacity(source_id=None)
        return self._orchestrator.publish_periodic_recovery(recovery_class)

    def ensure_periodic_recovery(
        self,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        self._require_capacity(source_id=None)
        return self._orchestrator.ensure_periodic_recovery(recovery_class)

    def _require_capacity(self, *, source_id: str | None) -> None:
        try:
            self._capacity_gate.require_capacity()
        except Exception as error:
            try:
                _ = self._store.record_incident(
                    IncidentDraft(
                        hashlib.sha256(
                            f"capacity_failure:{source_id or ''}".encode()
                        ).hexdigest(),
                        "capacity_failure",
                        {"phase": "pre_claim"},
                    ),
                    source_id=source_id,
                )
            except BaseException as incident_error:
                error.add_note(
                    f"capacity incident also failed: {type(incident_error).__name__}"
                )
            raise
