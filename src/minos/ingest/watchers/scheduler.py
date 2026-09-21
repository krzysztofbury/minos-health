from collections.abc import Callable
from datetime import UTC
from typing import Protocol, cast, final

from minos.ingest.watchers.recovery import RecoveryClass
from minos.ingest.watchers.service import WatcherService

_PERIODIC_RECOVERY_JOBS: tuple[tuple[RecoveryClass, str], ...] = (
    ("daily", "15 0 * * *"),
    ("weekly", "30 0 * * 1"),
    ("monthly", "45 0 1 * *"),
)
_RECOVERY_MISFIRE_GRACE_SECONDS = 3600
_READINESS_INTERVAL_SECONDS = 30


class SchedulerError(RuntimeError):
    """The in-memory watcher scheduler could not be configured safely."""


class SchedulerBackend(Protocol):
    def add_job(self, function: object, **kwargs: object) -> object: ...

    def add_listener(self, callback: object, mask: int) -> None: ...

    def start(self) -> None: ...


class _CronFactory(Protocol):
    @staticmethod
    def from_crontab(expression: str, timezone: object) -> object: ...


@final
class WatcherScheduler:
    def __init__(
        self,
        service: WatcherService,
        *,
        backend: SchedulerBackend | None = None,
        projection_limit: int = 100,
    ) -> None:
        if (
            isinstance(projection_limit, bool)
            or projection_limit < 1
            or projection_limit > 1000
        ):
            raise SchedulerError("scheduler projection limit is unsupported")
        self._service = service
        self._backend = backend or create_scheduler_backend()
        self._projection_limit = projection_limit

    def start(self, *, on_ready: Callable[[], None] | None = None) -> None:
        self._service.reconcile(limit=self._projection_limit)
        self._service.expire_stale_claims(limit=self._projection_limit)
        self._service.validate_scheduled_collectors(limit=self._projection_limit)
        self._install_future_jobs()
        if on_ready is not None:
            self._install_readiness_job(on_ready)
            self._install_started_listener(on_ready)
        _ = self._service.run_one_catch_up()
        for recovery_class, _expression in _PERIODIC_RECOVERY_JOBS:
            _ = self._service.ensure_periodic_recovery(recovery_class)
        self._backend.start()

    def _install_started_listener(self, on_ready: Callable[[], None]) -> None:
        from apscheduler.events import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
            EVENT_SCHEDULER_STARTED,
        )

        def mark_started(_event: object) -> None:
            on_ready()

        self._backend.add_listener(mark_started, EVENT_SCHEDULER_STARTED)

    def _install_readiness_job(self, on_ready: Callable[[], None]) -> None:
        _ = self._backend.add_job(
            on_ready,
            trigger="interval",
            seconds=_READINESS_INTERVAL_SECONDS,
            id="watcher:readiness",
            name="watcher:readiness",
            coalesce=True,
            max_instances=1,
            misfire_grace_time=_READINESS_INTERVAL_SECONDS,
            replace_existing=False,
        )

    def _install_future_jobs(self) -> None:
        from apscheduler.triggers.cron import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
            CronTrigger,
        )

        cron_factory = cast(_CronFactory, cast(object, CronTrigger))
        for recovery_class, expression in _PERIODIC_RECOVERY_JOBS:
            try:
                trigger = cron_factory.from_crontab(expression, timezone=UTC)
            except ValueError as error:
                raise SchedulerError("periodic recovery schedule is invalid") from error
            _ = self._backend.add_job(
                self._run_periodic_recovery_safely,
                trigger=trigger,
                args=(recovery_class,),
                id=f"recovery:{recovery_class}",
                name=f"recovery:{recovery_class}",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=_RECOVERY_MISFIRE_GRACE_SECONDS,
                replace_existing=False,
            )
        for source in self._service.scheduled_sources(limit=self._projection_limit):
            try:
                trigger = cron_factory.from_crontab(
                    source.cron_expression,
                    timezone=UTC,
                )
            except ValueError as error:
                raise SchedulerError("persisted cron expression is invalid") from error
            _ = self._backend.add_job(
                self._run_due_safely,
                trigger=trigger,
                args=(source.source_id,),
                id=f"watcher:{source.source_id}",
                name=f"watcher:{source.source_id}",
                coalesce=True,
                max_instances=1,
                misfire_grace_time=source.misfire_grace_seconds,
                replace_existing=False,
            )

    def _run_due_safely(self, source_id: str) -> None:
        try:
            _ = self._service.run_due(source_id)
        except Exception:
            raise SchedulerError("scheduled watcher run failed") from None

    def _run_periodic_recovery_safely(self, recovery_class: RecoveryClass) -> None:
        try:
            _ = self._service.ensure_periodic_recovery(recovery_class)
        except Exception:
            raise SchedulerError("scheduled recovery publication failed") from None


def create_scheduler_backend() -> SchedulerBackend:
    from apscheduler.executors.pool import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
        ThreadPoolExecutor,
    )
    from apscheduler.jobstores.memory import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
        MemoryJobStore,
    )
    from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped] # pyright: ignore[reportMissingTypeStubs]
        BlockingScheduler,
    )

    scheduler = BlockingScheduler(
        jobstores={"default": MemoryJobStore()},
        executors={"default": ThreadPoolExecutor(max_workers=1)},
        job_defaults={"coalesce": True, "max_instances": 1},
        timezone=UTC,
    )
    return cast(SchedulerBackend, cast(object, scheduler))
