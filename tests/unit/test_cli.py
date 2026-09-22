import json
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast, final

import pytest

import minos.cli.watcher as watcher_module
from minos.cli.main import WatcherCommandArguments, main
from minos.cli.watcher import create_watcher_command, paths_from_environment
from minos.ingest.watchers.archive import BlobArchive, FilesystemBlobArchive
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    ControlStore,
    SourceConfiguration,
    SourceSchedule,
)
from minos.ingest.watchers.control.migrations import (
    rollback_control_migrations_for_testing,
)
from minos.ingest.watchers.protocols import WatcherRuntimeError
from minos.ingest.watchers.service import WatcherReadiness, WatcherService


def test_module_reports_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "minos", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == "minos 0.0.1"


@final
class _WatcherCommand:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def preflight(self) -> None:
        self.calls.append("preflight")

    def run(self) -> None:
        self.calls.append("run")

    def run_once(self, source_id: str) -> None:
        self.calls.append(f"run-once:{source_id}")

    def health(self) -> None:
        self.calls.append("health")

    def bootstrap_rsk(self) -> None:
        self.calls.append("bootstrap-rsk")

    def migrate_control_database(self) -> tuple[int, int]:
        self.calls.append("migrate-control-database")
        return 3, 3

    def rebuild_public_rsk(
        self,
        *,
        expected_current_revision: int,
        expected_current_sha256: str,
        target_revision: int,
        target_sha256: str,
    ) -> str:
        call = (
            f"rebuild-public-rsk:{expected_current_revision}:"
            f"{expected_current_sha256}:{target_revision}:{target_sha256}"
        )
        self.calls.append(call)
        return f"{target_revision}-{target_sha256}"


def test_watcher_cli_commands_use_the_adapter_seam(tmp_path: Path) -> None:
    command = _WatcherCommand()
    received: list[WatcherCommandArguments] = []

    def factory(arguments: WatcherCommandArguments) -> _WatcherCommand:
        received.append(arguments)
        return command

    common = [
        "--database",
        str(tmp_path / "control.sqlite3"),
        "--staging-directory",
        str(tmp_path / "staging"),
        "--archive-directory",
        str(tmp_path / "archive"),
        "--process-lock",
        str(tmp_path / "watcher.lock"),
    ]
    main(["watcher", "preflight", *common], watcher_factory=factory)
    main(["watcher", "run", *common], watcher_factory=factory)
    main(["watcher", "run-once", "rsk", *common], watcher_factory=factory)
    main(["watcher", "health", *common], watcher_factory=factory)
    main(["watcher", "bootstrap-rsk", *common], watcher_factory=factory)
    main(["watcher", "migrate-control-database", *common], watcher_factory=factory)
    current_digest = "a" * 64
    target_digest = "b" * 64
    main(
        [
            "watcher",
            "rebuild-public-rsk",
            "--expected-current-revision",
            "2",
            "--expected-current-sha256",
            current_digest,
            "--target-revision",
            "1",
            "--target-sha256",
            target_digest,
            *common,
        ],
        watcher_factory=factory,
    )

    assert command.calls == [
        "preflight",
        "run",
        "run-once:rsk",
        "health",
        "bootstrap-rsk",
        "migrate-control-database",
        f"rebuild-public-rsk:2:{current_digest}:1:{target_digest}",
    ]
    assert [arguments.command for arguments in received] == [
        "preflight",
        "run",
        "run-once",
        "health",
        "bootstrap-rsk",
        "migrate-control-database",
        "rebuild-public-rsk",
    ]


def test_watcher_copy_control_database_command_creates_verified_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "control.sqlite3"
    destination = tmp_path / "evidence.sqlite3"
    with ControlStore.open(source):
        pass

    main(
        [
            "watcher",
            "copy-control-database",
            "--source-database",
            str(source),
            "--destination-database",
            str(destination),
        ]
    )

    connection = sqlite3.connect(destination)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


def test_watcher_validate_public_rsk_bypasses_runtime_factory(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    public_directory = tmp_path / "public"
    public_directory.mkdir(mode=0o755)

    main(
        [
            "watcher",
            "validate-public-rsk",
            "--public-directory",
            str(public_directory),
        ],
        watcher_factory=lambda _arguments: pytest.fail("factory must not be called"),
    )

    assert capsys.readouterr().out == '{"status":"absent"}\n'


def test_filesystem_runtime_factory_contains_rsk_collector(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.bootstrap_source(
            SourceConfiguration("rsk", "rsk", {}, True),
            (
                SourceSchedule("rsk", "0 3 * * *", 3600, 3, 0.5, 30.0),
                SourceSchedule(
                    "rsk",
                    "0 4 * * 0",
                    3600,
                    3,
                    0.5,
                    30.0,
                    "weekly_verification",
                ),
            ),
        )

    main(
        [
            "watcher",
            "preflight",
            "--database",
            str(database),
            "--staging-directory",
            str(tmp_path / "staging"),
            "--archive-directory",
            str(tmp_path / "archive"),
            "--process-lock",
            str(tmp_path / "watcher.lock"),
        ]
    )

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT run_id FROM watcher_runs").fetchall() == []
        assert connection.execute("SELECT fencing_epoch FROM sources").fetchall() == [
            (0,)
        ]
    finally:
        connection.close()


def test_rsk_bootstrap_is_exact_idempotent_and_preserves_disabled_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    arguments = [
        "watcher",
        "bootstrap-rsk",
        "--database",
        str(database),
        "--staging-directory",
        str(tmp_path / "staging"),
        "--archive-directory",
        str(tmp_path / "archive"),
        "--process-lock",
        str(tmp_path / "watcher.lock"),
    ]

    main(arguments)
    main(arguments)

    expected_configuration = (
        '{"basic_info_base_url":"https://rsk3.ezdrowie.gov.pl/api/rsk/resource/'
        'basic-info/","connect_timeout_seconds":5.0,"file_download_base_url":'
        '"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/","file_max_bytes":'
        '536870912,"metadata_max_bytes":5242880,"operation_deadline_seconds":'
        '300.0,"retry_base_seconds":0.5,"retry_max_attempts":3,'
        '"retry_max_seconds":30.0,"role_catalog_url":"https://rsk3.ezdrowie.gov.pl/'
        'api/rsk/resource/role/UZYNLG","timeout_seconds":20.0,'
        '"user_agent":"minos-health/0.0.1 rsk-watcher"}'
    )
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT watcher_type, enabled FROM sources WHERE source_id = 'rsk'"
        ).fetchone() == ("rsk", 1)
        assert connection.execute(
            "SELECT configuration_json FROM source_configuration_revisions"
        ).fetchone() == (expected_configuration,)
        assert connection.execute(
            """
            SELECT run_mode, cron_expression
            FROM source_schedules
            ORDER BY run_mode
            """
        ).fetchall() == [
            ("scheduled", "0 3 * * *"),
            ("weekly_verification", "0 4 * * 0"),
        ]
        assert connection.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (1,)
    finally:
        connection.close()

    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, False))
    with pytest.raises(SystemExit) as captured:
        main(arguments)
    assert captured.value.code == 1
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT enabled FROM sources WHERE source_id = 'rsk'"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (2,)
    finally:
        connection.close()


def test_rsk_bootstrap_preserves_custom_schedule(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    common = [
        "--database",
        str(database),
        "--staging-directory",
        str(tmp_path / "staging"),
        "--archive-directory",
        str(tmp_path / "archive"),
        "--process-lock",
        str(tmp_path / "watcher.lock"),
    ]
    main(["watcher", "bootstrap-rsk", *common])
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.set_schedule(SourceSchedule("rsk", "30 3 * * *", 3600, 3, 0.5, 30.0))

    with pytest.raises(SystemExit):
        main(["watcher", "bootstrap-rsk", *common])

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            """
            SELECT cron_expression FROM source_schedules
            WHERE source_id = 'rsk' AND run_mode = 'scheduled'
            """
        ).fetchone() == ("30 3 * * *",)
        assert connection.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (2,)
    finally:
        connection.close()


def test_rsk_bootstrap_preserves_custom_configuration(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        configured = store.configure_source(
            SourceConfiguration("rsk", "rsk", {"timeout_seconds": 30.0}, True)
        )
    common = [
        "--database",
        str(database),
        "--staging-directory",
        str(tmp_path / "staging"),
        "--archive-directory",
        str(tmp_path / "archive"),
        "--process-lock",
        str(tmp_path / "watcher.lock"),
    ]

    with pytest.raises(SystemExit):
        main(["watcher", "bootstrap-rsk", *common])

    connection = sqlite3.connect(database)
    try:
        stored = cast(
            tuple[str, str] | None,
            connection.execute(
                """
                SELECT configuration_sha256, configuration_json
                FROM source_configuration_revisions
                """
            ).fetchone(),
        )
        assert stored is not None
        assert stored[0] == configured.configuration_sha256
        assert json.loads(stored[1])["timeout_seconds"] == 30.0
        assert (
            connection.execute("SELECT source_id FROM source_schedules").fetchall()
            == []
        )
        assert connection.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_rsk_bootstrap_rejects_partial_canonical_registration(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
    common = [
        "--database",
        str(database),
        "--staging-directory",
        str(tmp_path / "staging"),
        "--archive-directory",
        str(tmp_path / "archive"),
        "--process-lock",
        str(tmp_path / "watcher.lock"),
    ]

    with pytest.raises(SystemExit) as captured:
        main(["watcher", "bootstrap-rsk", *common])

    assert captured.value.code == 1
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT watcher_type, enabled FROM sources WHERE source_id = 'rsk'"
        ).fetchone() == ("rsk", 1)
        assert (
            connection.execute("SELECT source_id FROM source_schedules").fetchall()
            == []
        )
        assert connection.execute(
            "SELECT state_revision FROM control_state"
        ).fetchone() == (1,)
    finally:
        connection.close()


@pytest.mark.parametrize("live_schema_version", [1, 2])
def test_migrate_control_database_upgrades_supported_live_schema(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    live_schema_version: int,
) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(database):
        pass
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        _ = rollback_control_migrations_for_testing(connection, live_schema_version)
    finally:
        connection.close()

    main(
        [
            "watcher",
            "migrate-control-database",
            "--database",
            str(database),
            "--staging-directory",
            str(tmp_path / "staging"),
            "--archive-directory",
            str(tmp_path / "archive"),
            "--process-lock",
            str(tmp_path / "watcher.lock"),
        ]
    )

    assert json.loads(capsys.readouterr().out) == {
        "from_version": live_schema_version,
        "migrated": True,
        "schema_version": 3,
    }
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()


def test_real_runtime_factory_rejects_unknown_enabled_kind(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.bootstrap_source(
            SourceConfiguration("rsk", "rsk", {}, True),
            (
                SourceSchedule("rsk", "0 3 * * *", 3600, 3, 0.5, 30.0),
                SourceSchedule(
                    "rsk",
                    "0 4 * * 0",
                    3600,
                    3,
                    0.5,
                    30.0,
                    "weekly_verification",
                ),
            ),
        )
    connection = sqlite3.connect(database)
    try:
        _ = connection.execute(
            "UPDATE sources SET watcher_type = 'unknown' WHERE source_id = 'rsk'"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(SystemExit):
        main(
            [
                "watcher",
                "preflight",
                "--database",
                str(database),
                "--staging-directory",
                str(tmp_path / "staging"),
                "--archive-directory",
                str(tmp_path / "archive"),
                "--process-lock",
                str(tmp_path / "watcher.lock"),
            ]
        )


def test_watcher_process_lock_is_derived_from_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MINOS_WATCHER_PROCESS_LOCK", raising=False)
    database = tmp_path / "control" / "control.sqlite3"

    paths = paths_from_environment(
        database=database,
        staging_directory=tmp_path / "staging",
        archive_directory=tmp_path / "archive",
        process_lock=None,
    )

    assert paths.process_lock == database.parent / "watcher.lock"
    with pytest.raises(WatcherRuntimeError, match="not canonical"):
        _ = paths_from_environment(
            database=database,
            staging_directory=tmp_path / "staging",
            archive_directory=tmp_path / "archive",
            process_lock=tmp_path / "unrelated" / "watcher.lock",
        )


@final
class _FactoryHeartbeat:
    def __init__(self, _targets: object) -> None:
        self.closed = False

    def start(self, _source_id: str) -> None:
        pass

    def success(self, _source_id: str) -> None:
        pass

    def failure(self, _source_id: str) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_production_factory_wires_s3_recovery_and_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_file = tmp_path / "credentials"
    target_file = tmp_path / "targets.json"
    _ = credential_file.write_text("credential")
    _ = target_file.write_text("target")
    environment = {
        "MINOS_WATCHER_ARCHIVE_BACKEND": "s3",
        "MINOS_WATCHER_S3_BUCKET": "minos-test-bucket",
        "MINOS_WATCHER_S3_EXPECTED_BUCKET_OWNER": "123456789012",
        "AWS_REGION": "eu-central-1",
        "AWS_SHARED_CREDENTIALS_FILE": str(credential_file),
        "MINOS_WATCHER_HEALTHCHECKS_TARGETS_FILE": str(target_file),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    archive_calls: list[dict[str, object]] = []
    recovery_calls: list[dict[str, object]] = []
    heartbeat_instances: list[_FactoryHeartbeat] = []
    scheduler_starts: list[bool] = []
    validated_heartbeat_sources: list[tuple[str, ...]] = []
    database = tmp_path / "control" / "control.sqlite3"
    database.parent.mkdir()
    database.parent.chmod(0o700)
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.bootstrap_source(
            SourceConfiguration("rsk", "rsk", {}, True),
            (
                SourceSchedule("rsk", "0 3 * * *", 3600, 3, 0.5, 30.0),
                SourceSchedule(
                    "rsk",
                    "0 4 * * 0",
                    3600,
                    3,
                    0.5,
                    30.0,
                    "weekly_verification",
                ),
            ),
        )

    class ArchiveFactory:
        @classmethod
        def create(cls, **kwargs: object) -> BlobArchive:
            _ = cls
            archive_calls.append(kwargs)
            return FilesystemBlobArchive(tmp_path / "fake-archive")

    class RecoveryFactory:
        @classmethod
        def create(cls, **kwargs: object) -> object:
            _ = cls
            recovery_calls.append(kwargs)
            return object()

    class Targets:
        def require_sources(self, source_ids: tuple[str, ...]) -> None:
            validated_heartbeat_sources.append(source_ids)

    class TargetsFactory:
        @classmethod
        def load(cls, _path: object) -> object:
            _ = cls
            return Targets()

    class SchedulerFactory:
        def __init__(self, service: WatcherService) -> None:
            self._service: WatcherService = service

        def start(self, *, on_ready: Callable[[], None] | None = None) -> None:
            self._service.validate_scheduled_collectors()
            assert on_ready is not None
            on_ready()
            scheduler_starts.append(True)

    def create_heartbeat(targets: object) -> _FactoryHeartbeat:
        heartbeat = _FactoryHeartbeat(targets)
        heartbeat_instances.append(heartbeat)
        return heartbeat

    monkeypatch.setattr(
        watcher_module,
        "S3BlobArchive",
        ArchiveFactory,
    )
    monkeypatch.setattr(
        watcher_module,
        "S3RecoveryStore",
        RecoveryFactory,
    )
    monkeypatch.setattr(
        watcher_module,
        "HealthcheckTargets",
        TargetsFactory,
    )
    monkeypatch.setattr(watcher_module, "HealthchecksClient", create_heartbeat)
    monkeypatch.setattr(
        watcher_module,
        "WatcherScheduler",
        SchedulerFactory,
    )
    arguments = WatcherCommandArguments(
        "run",
        database,
        tmp_path / "staging",
        tmp_path / "archive",
        tmp_path / "control" / "watcher.lock",
        None,
    )

    command = create_watcher_command(arguments)
    command.run()

    expected = {
        "bucket": "minos-test-bucket",
        "region": "eu-central-1",
        "expected_bucket_owner": "123456789012",
    }
    assert archive_calls == [expected]
    assert recovery_calls == [expected]
    assert scheduler_starts == [True]
    assert validated_heartbeat_sources == [("rsk",)]
    assert heartbeat_instances[0].closed is True


def test_production_preflight_factory_requires_complete_fail_closed_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINOS_WATCHER_ARCHIVE_BACKEND", "s3")
    arguments = WatcherCommandArguments(
        "preflight",
        tmp_path / "control.sqlite3",
        tmp_path / "staging",
        tmp_path / "archive",
        tmp_path / "watcher.lock",
        None,
    )

    with pytest.raises(WatcherRuntimeError, match="S3 bucket"):
        _ = create_watcher_command(arguments)


def test_real_health_rejects_incomplete_source_composition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "control.sqlite3"
    readiness = tmp_path / "watcher-ready.json"
    monkeypatch.setenv("MINOS_WATCHER_READINESS_FILE", str(readiness))
    monkeypatch.setenv("MINOS_WATCHER_SCHEDULER_PAUSED", "false")
    arguments = WatcherCommandArguments(
        "health",
        database,
        tmp_path / "staging",
        tmp_path / "archive",
        tmp_path / "watcher.lock",
        None,
    )
    WatcherReadiness(readiness).mark_ready()
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ):
        pass

    with pytest.raises(WatcherRuntimeError, match="no enabled sources"):
        create_watcher_command(arguments).health()

    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, False))
    with pytest.raises(WatcherRuntimeError, match="no enabled sources"):
        create_watcher_command(arguments).health()

    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
    with pytest.raises(WatcherRuntimeError, match="required schedule"):
        create_watcher_command(arguments).health()

    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.set_schedule(SourceSchedule("rsk", "0 3 * * *", 3600, 3, 0.5, 30.0))
    with pytest.raises(WatcherRuntimeError, match="required schedule"):
        create_watcher_command(arguments).health()

    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.set_schedule(
            SourceSchedule(
                "rsk",
                "0 4 * * 0",
                3600,
                3,
                0.5,
                30.0,
                "weekly_verification",
            )
        )
    create_watcher_command(arguments).health()

    connection = sqlite3.connect(database)
    try:
        _ = connection.execute(
            "UPDATE sources SET watcher_type = 'unknown' WHERE source_id = 'rsk'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(WatcherRuntimeError, match="configuration codec"):
        create_watcher_command(arguments).health()
