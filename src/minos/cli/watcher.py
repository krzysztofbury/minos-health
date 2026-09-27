import os
import sqlite3
import stat
import time
from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Protocol, cast, final

import httpx
from loguru import logger
from pydantic import AliasChoices, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from minos.ingest.watchers.archive import (
    BlobArchive,
    BlobStagingArea,
    FilesystemBlobArchive,
    S3BlobArchive,
)
from minos.ingest.watchers.baw.collector import BawSourceCollector
from minos.ingest.watchers.baw.publication import BawPublicProjectionPublisher
from minos.ingest.watchers.config import (
    WATCHER_SOURCE_CONFIGURATION_CODECS,
    RskSettings,
)
from minos.ingest.watchers.control import (
    CONTROL_SCHEMA_VERSION,
    CommittedRun,
    ControlStore,
    RecoveryReceipt,
    SourceConfiguration,
    SourceConfigurationCodecRegistry,
    SourceSchedule,
)
from minos.ingest.watchers.orchestrator import (
    RunRecoveryPublisher,
    WatcherOrchestrator,
)
from minos.ingest.watchers.preflight import (
    CapacityLimits,
    ControlDatabaseCopyProbe,
    ImageDigestProbe,
    NumericIdentityProbe,
    PausedPreflight,
    ReadableSecretsProbe,
    RecoveryRoundTripProbe,
    RuntimeCapacityGate,
    S3ChecksumProbe,
    copy_control_database,
)
from minos.ingest.watchers.protocols import (
    CollectorRegistry,
    HeartbeatClient,
    RecoveryPublisher,
    SourcePublicProjectionPublisher,
    WatcherRuntimeError,
)
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)
from minos.ingest.watchers.recovery import (
    FilesystemRecoveryStore,
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryImageStagingArea,
    S3RecoveryStore,
)
from minos.ingest.watchers.rsk.collector import RskSourceCollector
from minos.ingest.watchers.rsk.publication import RskPublicProjectionPublisher
from minos.ingest.watchers.scheduler import WatcherScheduler
from minos.ingest.watchers.service import (
    WatcherProcessLock,
    WatcherReadiness,
    WatcherService,
    check_watcher_database_readiness,
    watcher_process_lock_path,
)
from minos.ingest.watchers.uptime_kuma import UptimeKumaClient, UptimeKumaTargets

_DEPLOYMENT_COPY_TIMEOUT_SECONDS = 240.0
# Production mounts /tmp as a private mode-0700 container tmpfs.
_DEFAULT_READINESS_FILE = Path("/tmp/minos-watcher-ready.json")  # nosec B108
_RSK_SOURCE_ID = "rsk"
_BAW_SOURCE_ID = "baw"
_RSK_DAILY_CRON = "0 3 * * *"
_RSK_WEEKLY_VERIFICATION_CRON = "0 4 * * 0"
_BAW_DAILY_CRON = "0 5 * * *"
type RskClientFactory = Callable[[RskSettings], AbstractContextManager[httpx.Client]]


@dataclass(frozen=True, slots=True)
class WatcherPaths:
    database: Path
    staging_directory: Path
    archive_directory: Path
    process_lock: Path
    recovery_staging_directory: Path
    public_directory: Path | None = None


class WatcherRuntimeSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MINOS_WATCHER_",
        extra="ignore",
    )

    archive_backend: Literal["filesystem", "s3"] | None = None
    control_max_bytes: int = Field(
        default=2 * 1024 * 1024 * 1024,
        ge=4096,
        le=2 * 1024 * 1024 * 1024,
    )
    download_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        ge=1,
        le=512 * 1024 * 1024,
    )
    staging_quota_bytes: int = Field(
        default=600 * 1024 * 1024,
        ge=1,
        le=600 * 1024 * 1024,
    )
    min_free_bytes: int = Field(
        default=1024 * 1024 * 1024,
        ge=1024 * 1024 * 1024,
        le=1024 * 1024 * 1024 * 1024,
    )
    s3_bucket: str | None = None
    s3_expected_bucket_owner: str | None = None
    aws_region: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION"),
    )
    aws_shared_credentials_file: Path | None = Field(
        default=None,
        validation_alias="AWS_SHARED_CREDENTIALS_FILE",
    )
    scheduler_paused: bool = False
    uptime_kuma_targets_file: Path | None = None
    uptime_kuma_required: bool = False
    image_digest: str | None = None
    runtime_image_digest: str | None = None
    readiness_file: Path = _DEFAULT_READINESS_FILE


class WatcherCommand(Protocol):
    def preflight(self) -> None: ...

    def run(self) -> None: ...

    def run_once(self, source_id: str) -> None: ...

    def health(self) -> None: ...

    def bootstrap_rsk(self) -> None: ...

    def bootstrap_baw(self) -> None: ...

    def migrate_control_database(self) -> tuple[int, int]: ...

    def rebuild_public_rsk(
        self,
        *,
        expected_current_revision: int,
        expected_current_sha256: str,
        target_revision: int,
        target_sha256: str,
    ) -> str: ...


class WatcherFactoryArguments(Protocol):
    @property
    def command(self) -> str: ...

    @property
    def database(self) -> Path | None: ...

    @property
    def staging_directory(self) -> Path | None: ...

    @property
    def archive_directory(self) -> Path | None: ...

    @property
    def process_lock(self) -> Path | None: ...


@final
class _UnavailableRecoveryPublisher:
    def publish_run(
        self,
        store: ControlStore,
        run: CommittedRun,
    ) -> RecoveryReceipt:
        _ = store
        _ = run
        raise WatcherRuntimeError("recovery publisher is not configured")

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        _ = store
        _ = recovery_class
        raise WatcherRuntimeError("recovery publisher is not configured")

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        _ = store
        _ = recovery_class
        return None


@final
class _UnavailableHeartbeat:
    def start(self, source_id: str) -> None:
        _ = source_id
        raise WatcherRuntimeError("heartbeat client is not configured")

    def success(self, source_id: str) -> None:
        _ = source_id
        raise WatcherRuntimeError("heartbeat client is not configured")

    def failure(self, source_id: str) -> None:
        _ = source_id


@final
class _LocalHeartbeat:
    def start(self, source_id: str) -> None:
        _ = source_id

    def success(self, source_id: str) -> None:
        _ = source_id

    def failure(self, source_id: str) -> None:
        _ = source_id


@final
class WatcherCliAdapter:
    def __init__(
        self,
        paths: WatcherPaths,
        *,
        collectors: CollectorRegistry | None = None,
        archive: BlobArchive | None = None,
        recovery_publisher: RecoveryPublisher | None = None,
        heartbeat: HeartbeatClient | None = None,
        paused_preflight: PausedPreflight | None = None,
        capacity_limits: CapacityLimits | None = None,
        scheduling_paused: bool = False,
        readiness_file: Path | None = None,
        source_configuration_codecs: SourceConfigurationCodecRegistry | None = None,
        heartbeat_source_validator: Callable[[tuple[str, ...]], None] | None = None,
        close: Callable[[], None] | None = None,
    ) -> None:
        self._paths = paths
        self._collectors = collectors or CollectorRegistry()
        self._archive = archive
        self._recovery_publisher = recovery_publisher or _UnavailableRecoveryPublisher()
        self._heartbeat = heartbeat or _UnavailableHeartbeat()
        self._paused_preflight = paused_preflight
        self._capacity_limits = capacity_limits or CapacityLimits()
        self._scheduling_paused = scheduling_paused
        self._readiness = WatcherReadiness(readiness_file or _DEFAULT_READINESS_FILE)
        self._source_configuration_codecs = (
            source_configuration_codecs or SourceConfigurationCodecRegistry()
        )
        self._heartbeat_source_validator = heartbeat_source_validator
        self._close = close

    def preflight(self) -> None:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                if self._paused_preflight is not None:
                    _ = self._paused_preflight.run(
                        scheduling_paused=self._scheduling_paused
                    )
                    self._validate_heartbeat_sources_without_creation()
                    return
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ) as store:
                    gate = RuntimeCapacityGate(
                        self._paths.database,
                        self._paths.staging_directory,
                        limits=self._capacity_limits,
                    )
                    _ = gate.check()
                    self._build_service(store).validate_scheduled_collectors()
                    self._validate_heartbeat_sources(store)
                    _ = store.list_reconcilable_runs()
                    _ = store.list_stale_claims()

    def run(self) -> None:
        with self._manage_resources():
            self._prepare_directories()
            self._readiness.reset()
            if self._scheduling_paused:
                raise WatcherRuntimeError("watcher scheduling is paused")
            try:
                with WatcherProcessLock(self._paths.process_lock):
                    with ControlStore.open(
                        self._paths.database,
                        allow_thread_handoff=True,
                        source_configuration_codecs=self._source_configuration_codecs,
                    ) as store:
                        self._validate_heartbeat_sources(store)
                        service = self._build_service(store)
                        WatcherScheduler(service).start(
                            on_ready=self._readiness.mark_ready
                        )
            finally:
                self._readiness.reset()

    def run_once(self, source_id: str) -> None:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ) as store:
                    self._validate_heartbeat_sources(store)
                    service = self._build_service(store)
                    _ = service.run_manual(source_id)

    def health(self) -> None:
        with self._manage_resources():
            if self._scheduling_paused:
                raise WatcherRuntimeError("paused watcher cannot report ready")
            self._readiness.check()
            enabled_source_ids = check_watcher_database_readiness(
                self._paths.database,
                collectors=self._collectors,
                source_configuration_codecs=self._source_configuration_codecs,
            )
            if self._heartbeat_source_validator is not None:
                self._heartbeat_source_validator(enabled_source_ids)

    def bootstrap_rsk(self) -> None:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ) as store:
                    _ = store.bootstrap_source(
                        SourceConfiguration(_RSK_SOURCE_ID, "rsk", {}, True),
                        _rsk_schedules(),
                    )

    def bootstrap_baw(self) -> None:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ) as store:
                    _ = store.bootstrap_source(
                        SourceConfiguration(_BAW_SOURCE_ID, "baw", {}, True),
                        (
                            SourceSchedule(
                                _BAW_SOURCE_ID, _BAW_DAILY_CRON, 3600, 3, 0.5, 30.0
                            ),
                        ),
                    )

    def migrate_control_database(self) -> tuple[int, int]:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                prior_version = _read_existing_control_schema_version(
                    self._paths.database
                )
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ):
                    pass
                return prior_version, CONTROL_SCHEMA_VERSION

    def rebuild_public_rsk(
        self,
        *,
        expected_current_revision: int,
        expected_current_sha256: str,
        target_revision: int,
        target_sha256: str,
    ) -> str:
        with self._manage_resources():
            self._prepare_directories()
            with WatcherProcessLock(self._paths.process_lock):
                with ControlStore.open(
                    self._paths.database,
                    source_configuration_codecs=self._source_configuration_codecs,
                ) as store:
                    publisher = RskPublicProjectionPublisher(
                        ImmutableGenerationPublisher(self._public_directory())
                    )
                    return publisher.rebuild(
                        store,
                        expected_current_revision=expected_current_revision,
                        expected_current_sha256=expected_current_sha256,
                        target_revision=target_revision,
                        target_sha256=target_sha256,
                    )

    def _build_service(self, store: ControlStore) -> WatcherService:
        gate = RuntimeCapacityGate(
            self._paths.database,
            self._paths.staging_directory,
            limits=self._capacity_limits,
        )
        archive = self._archive or FilesystemBlobArchive(self._paths.archive_directory)
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=archive,
            collectors=self._collectors,
            recovery_publisher=self._recovery_publisher,
            heartbeat=self._heartbeat,
            public_projection_publisher=SourcePublicProjectionPublisher(
                {
                    "rsk": RskPublicProjectionPublisher(
                        ImmutableGenerationPublisher(self._public_directory())
                    ),
                    "baw": BawPublicProjectionPublisher(
                        ImmutableGenerationPublisher(
                            self._public_directory() / "baw", source="BAW"
                        )
                    ),
                }
            ),
        )
        return WatcherService(
            store=store,
            orchestrator=orchestrator,
            collectors=self._collectors,
            capacity_gate=gate,
        )

    def _prepare_directories(self) -> None:
        for directory in (
            self._paths.database.parent,
            self._paths.staging_directory,
            self._paths.archive_directory,
            self._paths.process_lock.parent,
            self._paths.recovery_staging_directory,
        ):
            _prepare_private_directory(directory)
        prepare_public_root(self._public_directory())
        prepare_public_root(self._public_directory() / "baw")
        BlobStagingArea(
            self._paths.staging_directory,
            maximum_size_bytes=self._capacity_limits.maximum_download_size_bytes,
        ).cleanup_stale()

    def _validate_heartbeat_sources(self, store: ControlStore) -> None:
        if self._heartbeat_source_validator is None:
            return
        enabled_sources = store.list_enabled_source_kinds(limit=128)
        self._heartbeat_source_validator(
            tuple(source_id for source_id, _watcher_type in enabled_sources)
        )

    def _validate_heartbeat_sources_without_creation(self) -> None:
        if self._heartbeat_source_validator is None:
            return
        if not self._paths.database.exists():
            self._heartbeat_source_validator(())
            return
        with ControlStore.open(
            self._paths.database,
            source_configuration_codecs=self._source_configuration_codecs,
        ) as store:
            self._validate_heartbeat_sources(store)

    def _public_directory(self) -> Path:
        return self._paths.public_directory or self._paths.database.parent / "public"

    @contextmanager
    def _manage_resources(self) -> Generator[None]:
        try:
            yield
        finally:
            if self._close is not None:
                try:
                    self._close()
                except BaseException:
                    logger.bind(event="watcher_monitor_cleanup_failed").warning(
                        "Watcher monitor cleanup failed"
                    )


def paths_from_environment(
    *,
    database: Path | None,
    staging_directory: Path | None,
    archive_directory: Path | None,
    process_lock: Path | None,
    recovery_staging_directory: Path | None = None,
) -> WatcherPaths:
    resolved = (
        database or _environment_path("MINOS_WATCHER_DATABASE"),
        staging_directory or _environment_path("MINOS_WATCHER_STAGING_DIRECTORY"),
        archive_directory or _environment_path("MINOS_WATCHER_ARCHIVE_DIRECTORY"),
    )
    if not all(isinstance(path, Path) for path in resolved):
        raise WatcherRuntimeError("watcher runtime paths are not configured")
    database_path, staging_path, archive_path = cast(
        tuple[Path, Path, Path],
        resolved,
    )
    lock_path = watcher_process_lock_path(database_path)
    configured_lock = process_lock or _environment_path("MINOS_WATCHER_PROCESS_LOCK")
    if configured_lock is not None and configured_lock != lock_path:
        raise WatcherRuntimeError("watcher process lock path is not canonical")
    recovery_staging_path = (
        recovery_staging_directory
        or _environment_path("MINOS_WATCHER_RECOVERY_STAGING_DIRECTORY")
        or database_path.parent / "recovery-staging"
    )
    public_path = (
        _environment_path("MINOS_WATCHER_PUBLIC_DIRECTORY")
        or database_path.parent / "public"
    )
    return WatcherPaths(
        database_path,
        staging_path,
        archive_path,
        lock_path,
        recovery_staging_path,
        public_path,
    )


def create_watcher_command(
    arguments: WatcherFactoryArguments,
    *,
    rsk_client_factory: RskClientFactory | None = None,
) -> WatcherCommand:
    try:
        settings = WatcherRuntimeSettings.model_validate({})
    except ValidationError as error:
        raise WatcherRuntimeError("watcher runtime settings are invalid") from error
    paths = paths_from_environment(
        database=arguments.database,
        staging_directory=arguments.staging_directory,
        archive_directory=arguments.archive_directory,
        process_lock=arguments.process_lock,
    )
    limits = CapacityLimits(
        settings.control_max_bytes,
        settings.download_max_bytes,
        settings.staging_quota_bytes,
        settings.min_free_bytes,
    )
    collectors = create_runtime_collectors(
        paths.staging_directory,
        maximum_size_bytes=limits.maximum_download_size_bytes,
        rsk_client_factory=rsk_client_factory,
    )
    archive_backend = settings.archive_backend
    if archive_backend is None:
        if any(
            value is not None
            for value in (
                settings.s3_bucket,
                settings.s3_expected_bucket_owner,
                settings.aws_region,
                settings.aws_shared_credentials_file,
            )
        ):
            raise WatcherRuntimeError("archive backend is not configured")
        archive_backend = "filesystem"
    if archive_backend == "filesystem":
        recovery_staging = RecoveryImageStagingArea(
            paths.recovery_staging_directory,
            maximum_size_bytes=settings.control_max_bytes,
        )
        recovery_publisher = RunRecoveryPublisher(
            recovery_staging,
            FilesystemRecoveryStore(paths.archive_directory),
        )
        return WatcherCliAdapter(
            paths,
            collectors=collectors,
            recovery_publisher=recovery_publisher,
            heartbeat=_LocalHeartbeat(),
            capacity_limits=limits,
            scheduling_paused=settings.scheduler_paused,
            readiness_file=settings.readiness_file,
            source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS,
        )
    return _create_s3_watcher_command(
        arguments,
        settings,
        paths,
        limits,
        collectors,
    )


def _create_s3_watcher_command(
    arguments: WatcherFactoryArguments,
    settings: WatcherRuntimeSettings,
    paths: WatcherPaths,
    limits: CapacityLimits,
    collectors: CollectorRegistry,
) -> WatcherCommand:
    bucket = _required_setting(settings.s3_bucket, "S3 bucket")
    region = _required_setting(settings.aws_region, "AWS region")
    owner = _required_setting(
        settings.s3_expected_bucket_owner,
        "expected S3 bucket owner",
    )
    credential_file = _required_path(
        settings.aws_shared_credentials_file,
        "AWS credential file",
    )
    target_file = settings.uptime_kuma_targets_file
    if settings.uptime_kuma_required and target_file is None:
        raise WatcherRuntimeError("required Uptime Kuma target file is not configured")
    archive = S3BlobArchive.create(
        bucket=bucket,
        region=region,
        expected_bucket_owner=owner,
    )
    recovery_store = S3RecoveryStore.create(
        bucket=bucket,
        region=region,
        expected_bucket_owner=owner,
    )
    _prepare_private_directory(paths.database.parent)
    recovery_staging = RecoveryImageStagingArea(
        paths.recovery_staging_directory,
        maximum_size_bytes=settings.control_max_bytes,
    )
    recovery_publisher = RunRecoveryPublisher(recovery_staging, recovery_store)
    heartbeat: HeartbeatClient = _LocalHeartbeat()
    heartbeat_source_validator: Callable[[tuple[str, ...]], None] | None = None
    close: Callable[[], None] | None = None
    if target_file is not None:
        targets = UptimeKumaTargets.load(target_file)
        client = UptimeKumaClient(targets)
        heartbeat = client
        close = client.close
        if settings.uptime_kuma_required:
            heartbeat_source_validator = targets.require_sources
    try:
        paused_preflight = (
            _create_production_preflight(
                settings=settings,
                paths=paths,
                limits=limits,
                bucket=bucket,
                region=region,
                owner=owner,
                credential_file=credential_file,
                target_file=target_file,
                recovery_staging=recovery_staging,
                recovery_store=recovery_store,
            )
            if arguments.command == "preflight"
            else None
        )
    except BaseException:
        if close is not None:
            try:
                close()
            except BaseException:
                pass
        raise
    return WatcherCliAdapter(
        paths,
        collectors=collectors,
        archive=archive,
        recovery_publisher=recovery_publisher,
        heartbeat=heartbeat,
        paused_preflight=paused_preflight,
        capacity_limits=limits,
        scheduling_paused=settings.scheduler_paused,
        readiness_file=settings.readiness_file,
        source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS,
        heartbeat_source_validator=heartbeat_source_validator,
        close=close,
    )


def create_runtime_collectors(
    staging_directory: Path,
    *,
    maximum_size_bytes: int,
    rsk_client_factory: RskClientFactory | None = None,
) -> CollectorRegistry:
    staging = BlobStagingArea(
        staging_directory,
        maximum_size_bytes=maximum_size_bytes,
    )
    collector = (
        RskSourceCollector(staging)
        if rsk_client_factory is None
        else RskSourceCollector(staging, client_factory=rsk_client_factory)
    )
    registry = CollectorRegistry()
    registry.register(
        "rsk",
        collector,
        required_schedule_modes=("scheduled", "weekly_verification"),
    )
    registry.register(
        "baw", BawSourceCollector(staging), required_schedule_modes=("scheduled",)
    )
    return registry


def _rsk_schedules() -> tuple[SourceSchedule, SourceSchedule]:
    return (
        SourceSchedule(
            _RSK_SOURCE_ID,
            _RSK_DAILY_CRON,
            3600,
            3,
            0.5,
            30.0,
        ),
        SourceSchedule(
            _RSK_SOURCE_ID,
            _RSK_WEEKLY_VERIFICATION_CRON,
            3600,
            3,
            0.5,
            30.0,
            "weekly_verification",
        ),
    )


def _create_production_preflight(
    *,
    settings: WatcherRuntimeSettings,
    paths: WatcherPaths,
    limits: CapacityLimits,
    bucket: str,
    region: str,
    owner: str,
    credential_file: Path,
    target_file: Path | None,
    recovery_staging: RecoveryImageStagingArea,
    recovery_store: S3RecoveryStore,
) -> PausedPreflight:
    image_digest = _required_setting(settings.image_digest, "expected image digest")
    runtime_digest = _required_setting(
        settings.runtime_image_digest,
        "runtime image digest",
    )
    return PausedPreflight.production(
        capacity=RuntimeCapacityGate(
            paths.database,
            paths.staging_directory,
            limits=limits,
            allow_missing_database=True,
        ),
        image_digest=ImageDigestProbe(
            actual_digest=runtime_digest,
            expected_digest=image_digest,
        ),
        numeric_identity=NumericIdentityProbe(
            expected_uid=10001,
            expected_gid=10001,
        ),
        secrets=ReadableSecretsProbe(
            (credential_file,)
            if target_file is None
            else (credential_file, target_file)
        ),
        control_database=ControlDatabaseCopyProbe(
            paths.database,
            paths.recovery_staging_directory,
            source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS,
        ),
        s3_checksum=S3ChecksumProbe.create(
            bucket=bucket,
            region=region,
            expected_bucket_owner=owner,
        ),
        recovery_round_trip=RecoveryRoundTripProbe(
            paths.recovery_staging_directory,
            recovery_staging,
            recovery_store,
        ),
    )


def _environment_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if value is None or not value:
        return None
    return Path(value)


def _required_setting(value: str | None, label: str) -> str:
    if value is None or not value:
        raise WatcherRuntimeError(f"{label} is not configured")
    return value


def _required_path(value: Path | None, label: str) -> Path:
    if value is None:
        raise WatcherRuntimeError(f"{label} is not configured")
    return value


def _read_existing_control_schema_version(database: Path) -> int:
    if not database.exists():
        return 0
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database.resolve().as_uri()}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        row = cast(
            tuple[object] | None, connection.execute("PRAGMA user_version").fetchone()
        )
    except OSError, sqlite3.Error:
        raise WatcherRuntimeError(
            "control database schema version is unavailable"
        ) from None
    finally:
        if connection is not None:
            connection.close()
    if (
        row is None
        or len(row) != 1
        or type(row[0]) is not int
        or row[0] < 1
        or row[0] > CONTROL_SCHEMA_VERSION
    ):
        raise WatcherRuntimeError("control database schema version is unsupported")
    return row[0]


def create_deployment_control_copy(source: Path, destination: Path) -> int:
    return copy_control_database(
        source,
        destination,
        deadline_monotonic=time.monotonic() + _DEPLOYMENT_COPY_TIMEOUT_SECONDS,
    )


def _prepare_private_directory(directory: Path) -> None:
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        status = directory.lstat()
    except OSError:
        raise WatcherRuntimeError("watcher runtime directory is unavailable") from None
    if (
        not stat.S_ISDIR(status.st_mode)
        or status.st_uid != os.geteuid()
        or stat.S_IMODE(status.st_mode) & 0o077
    ):
        raise WatcherRuntimeError("watcher runtime directory ownership is unsafe")
