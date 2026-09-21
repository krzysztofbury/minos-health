import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import final

import pytest

import minos.cli.watcher as watcher_module
from minos.cli.main import WatcherCommandArguments, main
from minos.cli.watcher import create_watcher_command, paths_from_environment
from minos.ingest.watchers.archive import BlobArchive, FilesystemBlobArchive
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import ControlStore, SourceConfiguration
from minos.ingest.watchers.protocols import WatcherRuntimeError


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

    assert command.calls == ["preflight", "run", "run-once:rsk", "health"]
    assert [arguments.command for arguments in received] == [
        "preflight",
        "run",
        "run-once",
        "health",
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


def test_default_run_once_fails_before_claim_without_registered_collector(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))

    with pytest.raises(SystemExit) as captured:
        main(
            [
                "watcher",
                "run-once",
                "rsk",
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

    assert captured.value.code == 1
    connection = sqlite3.connect(database)
    try:
        assert connection.execute("SELECT run_id FROM watcher_runs").fetchall() == []
        assert connection.execute("SELECT fencing_epoch FROM sources").fetchall() == [
            (0,)
        ]
    finally:
        connection.close()


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

    class TargetsFactory:
        @classmethod
        def load(cls, _path: object) -> object:
            _ = cls
            return object()

    class SchedulerFactory:
        def __init__(self, _service: object) -> None:
            pass

        def start(self, *, on_ready: Callable[[], None] | None = None) -> None:
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
        tmp_path / "control" / "control.sqlite3",
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
