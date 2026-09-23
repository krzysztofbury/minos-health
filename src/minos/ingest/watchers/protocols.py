from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol, final

from minos.ingest.watchers.archive import BlobArchive
from minos.ingest.watchers.control import (
    ChangeDraft,
    CommittedRun,
    ControlStore,
    IncidentDraft,
    JsonValue,
    ObservationDraft,
    RecoveryReceipt,
    RunClaim,
    ScheduledRunMode,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass

_MAX_COLLECTORS = 64


class WatcherRuntimeError(RuntimeError):
    """The watcher cannot continue without violating its runtime contract."""


class CollectorNotRegisteredError(WatcherRuntimeError):
    """No source collector is registered for a persisted watcher type."""


class RetryableWatcherError(WatcherRuntimeError):
    """A pre-commit watcher operation may be attempted again safely."""


class SourceDriftWatcherError(WatcherRuntimeError):
    """An upstream response changed outside the collector's accepted contract."""


@dataclass(frozen=True, slots=True)
class CollectionContext:
    source_id: str
    configuration: dict[str, JsonValue]
    cursor: dict[str, JsonValue]
    run: RunClaim
    archive: BlobArchive


@dataclass(frozen=True, slots=True)
class CollectedRun:
    observations: tuple[ObservationDraft, ...]
    cursor: dict[str, JsonValue]
    changes: tuple[ChangeDraft, ...] = ()
    incidents: tuple[IncidentDraft, ...] = ()


class SourceCollector(Protocol):
    def collect(
        self,
        context: CollectionContext,
    ) -> AbstractContextManager[CollectedRun]: ...


class RecoveryPublisher(Protocol):
    def publish_run(
        self,
        store: ControlStore,
        run: CommittedRun,
    ) -> RecoveryReceipt: ...

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage: ...

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None: ...


class PublicProjectionPublisher(Protocol):
    def publish(self, store: ControlStore, run: CommittedRun) -> None: ...

    def restore(self, store: ControlStore) -> None: ...


@final
class NoopPublicProjectionPublisher:
    def publish(self, store: ControlStore, run: CommittedRun) -> None:
        _ = store, run

    def restore(self, store: ControlStore) -> None:
        _ = store


class HeartbeatClient(Protocol):
    def start(self, source_id: str) -> None: ...

    def success(self, source_id: str) -> None: ...

    def failure(self, source_id: str) -> None: ...


class CapacityGate(Protocol):
    def require_capacity(self) -> None: ...


class PreflightProbe(Protocol):
    def check(self) -> object: ...


@final
class CollectorRegistry:
    def __init__(self) -> None:
        self._collectors: dict[str, SourceCollector] = {}
        self._required_schedule_modes: dict[str, tuple[ScheduledRunMode, ...]] = {}

    def register(
        self,
        watcher_type: str,
        collector: SourceCollector,
        *,
        required_schedule_modes: tuple[ScheduledRunMode, ...] = (),
    ) -> None:
        _validate_watcher_type(watcher_type)
        if len(required_schedule_modes) > 2 or len(set(required_schedule_modes)) != len(
            required_schedule_modes
        ):
            raise WatcherRuntimeError("required schedule modes are invalid")
        if any(
            mode not in ("scheduled", "weekly_verification")
            for mode in required_schedule_modes
        ):
            raise WatcherRuntimeError("required schedule mode is unsupported")
        if watcher_type in self._collectors:
            raise WatcherRuntimeError("watcher type is already registered")
        if len(self._collectors) >= _MAX_COLLECTORS:
            raise WatcherRuntimeError("collector registry exceeds its bound")
        self._collectors[watcher_type] = collector
        self._required_schedule_modes[watcher_type] = tuple(
            sorted(required_schedule_modes)
        )

    def resolve(self, watcher_type: str) -> SourceCollector:
        _validate_watcher_type(watcher_type)
        collector = self._collectors.get(watcher_type)
        if collector is None:
            raise CollectorNotRegisteredError("source collector is not registered")
        return collector

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._collectors))

    def required_schedule_modes(
        self,
        watcher_type: str,
    ) -> tuple[ScheduledRunMode, ...]:
        _ = self.resolve(watcher_type)
        return self._required_schedule_modes[watcher_type]


def _validate_watcher_type(watcher_type: object) -> None:
    if not isinstance(watcher_type, str):
        raise WatcherRuntimeError("watcher type must be text")
    if len(watcher_type) < 1 or len(watcher_type) > 64:
        raise WatcherRuntimeError("watcher type length is outside the supported range")
