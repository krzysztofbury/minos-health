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
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass

_MAX_COLLECTORS = 64


class WatcherRuntimeError(RuntimeError):
    """The watcher cannot continue without violating its runtime contract."""


class CollectorNotRegisteredError(WatcherRuntimeError):
    """No source collector is registered for a persisted watcher type."""


class RetryableWatcherError(WatcherRuntimeError):
    """A pre-commit watcher operation may be attempted again safely."""


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

    def register(self, watcher_type: str, collector: SourceCollector) -> None:
        _validate_watcher_type(watcher_type)
        if watcher_type in self._collectors:
            raise WatcherRuntimeError("watcher type is already registered")
        if len(self._collectors) >= _MAX_COLLECTORS:
            raise WatcherRuntimeError("collector registry exceeds its bound")
        self._collectors[watcher_type] = collector

    def resolve(self, watcher_type: str) -> SourceCollector:
        _validate_watcher_type(watcher_type)
        collector = self._collectors.get(watcher_type)
        if collector is None:
            raise CollectorNotRegisteredError("source collector is not registered")
        return collector

    def registered_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._collectors))


def _validate_watcher_type(watcher_type: object) -> None:
    if not isinstance(watcher_type, str):
        raise WatcherRuntimeError("watcher type must be text")
    if len(watcher_type) < 1 or len(watcher_type) > 64:
        raise WatcherRuntimeError("watcher type length is outside the supported range")
