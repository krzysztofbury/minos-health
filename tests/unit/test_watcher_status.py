import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import cast

import pytest

from minos.cli import watcher_status
from minos.cli.main import main
from minos.cli.watcher_status import WatcherRuntimeError, read_watcher_status

_PROJECT_ROOT = Path(__file__).parents[2]
_SCHEMA = _PROJECT_ROOT / "database/control/sqlite/schema.sql"
_NOW_MS = 1_700_000_000_123
_SUCCESS_RUN_ID = "successful-run-01"
_FAILED_RUN_ID = "failed-run-00001"
_RESPONSE_SHA256 = "f" * 64

type JsonValue = (
    None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
)


@pytest.fixture
def status_database(tmp_path: Path) -> Iterator[Path]:
    database = tmp_path / "control.sqlite3"
    connection = sqlite3.connect(database)
    try:
        _ = connection.executescript(_SCHEMA.read_text())
        _insert_sources(connection)
        _insert_schedules(connection)
        _insert_runs(connection)
        _insert_evidence(connection)
        _insert_incidents(connection)
        _ = connection.execute(
            """
            UPDATE control_state
            SET state_revision = 99, updated_at_ms = ?
            WHERE singleton_id = 1
            """,
            (_NOW_MS,),
        )
        connection.commit()
    finally:
        connection.close()
    yield database


def test_status_json_is_read_only_bounded_deterministic_and_private(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_digest = hashlib.sha256(status_database.read_bytes()).hexdigest()

    main(
        ["watcher", "status", "--database", str(status_database), "--json"],
        watcher_factory=lambda _arguments: pytest.fail("factory must not be called"),
    )

    output = capsys.readouterr().out
    snapshot = _decode_json_object(output)
    assert output == json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"
    assert snapshot["format_version"] == 1
    assert snapshot["schema_version"] == 4
    assert snapshot["state_revision"] == 99
    assert snapshot["updated_at"] == "2023-11-14T22:13:20.123Z"
    assert snapshot["open_incidents_truncated"] is False
    sources = [_object(item) for item in _list(snapshot["sources"])]
    assert [source["source_id"] for source in sources] == [
        "alpha",
        "zulu",
    ]
    assert snapshot["open_incidents"] == [
        {
            "category": "database_capacity",
            "created_at": "1970-01-01T00:00:07.000Z",
            "incident_id": "global-incident01",
            "run_id": None,
            "state_revision": 44,
        }
    ]

    alpha = sources[0]
    assert alpha["enabled"] is True
    assert alpha["watcher_type"] == "generic"
    assert alpha["configuration_revision"] == 2
    assert alpha["fencing_epoch"] == 7
    latest_run = _object(alpha["latest_run"])
    latest_success = _object(alpha["latest_successful_run"])
    assert latest_run["run_id"] == _FAILED_RUN_ID
    assert latest_run["status"] == "failed"
    assert latest_success["run_id"] == _SUCCESS_RUN_ID
    assert latest_success["status"] == "successful"
    schedules = [_object(item) for item in _list(alpha["schedules"])]
    assert [schedule["run_mode"] for schedule in schedules] == [
        "scheduled",
        "weekly_verification",
    ]
    assert schedules[0]["pending_run_mode"] == "catch_up"
    assert schedules[0]["retry_failure_count"] == 2
    assert schedules[0]["retry_not_before"] == ("1970-01-01T00:00:09.000Z")
    changes = [_object(item) for item in _list(alpha["recent_changes"])]
    assert len(changes) == 10
    assert [change["change_id"] for change in changes] == [
        f"change-{index:09d}" for index in range(11, 1, -1)
    ]
    assert alpha["recent_changes_truncated"] is True
    assert alpha["open_incidents"] == [
        {
            "category": "upstream_failure",
            "created_at": "1970-01-01T00:00:06.000Z",
            "incident_id": "open-incident-01",
            "run_id": _FAILED_RUN_ID,
            "state_revision": 42,
        }
    ]
    assert alpha["open_incidents_truncated"] is False
    products = [_object(item) for item in _list(alpha["verified_downloaded_products"])]
    assert len(products) == 10
    assert [product["ordinal"] for product in products] == list(range(11, 1, -1))
    assert all(product["run_status"] == "successful" for product in products)
    assert all(len(_string(product["sha256"])) == 64 for product in products)
    assert alpha["verified_downloaded_products_truncated"] is True

    serialized = json.dumps(snapshot, sort_keys=True)
    for forbidden in (
        "configuration_json",
        "configuration_sha256",
        "details_json",
        "summary_json",
        "metadata_json",
        "source_identity",
        "claim_token",
        "https://secret.example",
        "secret-token",
        "/private/archive",
        "resolved-incident",
        "failed-product-observation",
        "response_body",
    ):
        assert forbidden not in serialized
    assert hashlib.sha256(status_database.read_bytes()).hexdigest() == original_digest


def test_status_exposes_incident_truncation_in_json_and_human_output(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    connection = sqlite3.connect(status_database)
    try:
        for index in range(10):
            for prefix, source_id in (("source", "alpha"), ("global", None)):
                _ = connection.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, source_id, run_id, incident_category,
                        details_json, state_revision, created_at_ms
                    ) VALUES (?, ?, NULL, 'bounded_failure', '{}', ?, ?)
                    """,
                    (
                        f"{prefix}-incident-{index:02d}",
                        source_id,
                        100 + index,
                        8000 + index,
                    ),
                )
        connection.commit()
    finally:
        connection.close()

    main(["watcher", "status", "alpha", "--database", str(status_database), "--json"])
    snapshot = _decode_json_object(capsys.readouterr().out)
    source = _object(_list(snapshot["sources"])[0])
    assert len(_list(snapshot["open_incidents"])) == 10
    assert snapshot["open_incidents_truncated"] is True
    assert len(_list(source["open_incidents"])) == 10
    assert source["open_incidents_truncated"] is True

    main(["watcher", "status", "alpha", "--database", str(status_database)])
    output = capsys.readouterr().out
    assert "global open incidents: 10+" in output
    assert "  open incidents: 10+" in output
    assert "  recent changes: 10+" in output
    assert "  verified downloaded products: 10+" in output


def test_status_filters_source_and_uses_database_environment(
    status_database: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MINOS_WATCHER_DATABASE", str(status_database))
    monkeypatch.delenv("MINOS_WATCHER_STAGING_DIRECTORY", raising=False)
    monkeypatch.delenv("MINOS_WATCHER_ARCHIVE_DIRECTORY", raising=False)
    monkeypatch.delenv("MINOS_WATCHER_PROCESS_LOCK", raising=False)

    main(["watcher", "status", "zulu", "--json"])

    snapshot = _decode_json_object(capsys.readouterr().out)
    sources = [_object(item) for item in _list(snapshot["sources"])]
    assert [source["source_id"] for source in sources] == ["zulu"]
    assert sources[0]["enabled"] is False
    assert sources[0]["configuration_revision"] is None
    assert sources[0]["latest_run"] is None


def test_status_process_does_not_import_watcher_runtime(status_database: Path) -> None:
    command = repr(["watcher", "status", "--database", str(status_database), "--json"])
    script = f"""
import sys
from minos.cli.main import main
main({command})
for module in (
    "boto3",
    "minos.cli.watcher",
    "minos.ingest.watchers.control.migrations",
    "minos.ingest.watchers.rsk.collector",
):
    if module in sys.modules:
        raise RuntimeError(module)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_status_human_output_is_explicit_about_nulls(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["watcher", "status", "zulu", "--database", str(status_database)])

    output = capsys.readouterr().out
    assert output.startswith("Watcher status\nformat version: 1\n")
    assert "updated at: 2023-11-14T22:13:20.123Z" in output
    assert "Source zulu" in output
    assert "configuration revision: null" in output
    assert "latest successful run: null" in output


def test_status_rejects_unknown_source(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "watcher",
                "status",
                "missing",
                "--database",
                str(status_database),
            ]
        )

    assert captured.value.code == 1
    assert capsys.readouterr().err == "minos: watcher command failed\n"


def test_status_does_not_create_a_missing_database(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database = tmp_path / "missing.sqlite3"

    with pytest.raises(SystemExit) as captured:
        main(["watcher", "status", "--database", str(database)])

    assert captured.value.code == 1
    assert capsys.readouterr().err == "minos: watcher command failed\n"
    assert not database.exists()
    assert list(tmp_path.iterdir()) == []


def test_status_reads_a_coherent_snapshot_while_wal_writer_remains_open(
    status_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = sqlite3.connect(status_database, isolation_level=None)
    reader_validated = Event()
    allow_reader_to_continue = Event()
    original_validate = watcher_status._validate_snapshot  # pyright: ignore[reportPrivateUsage]

    def pause_after_validation(
        connection: watcher_status._StatusConnection,  # pyright: ignore[reportPrivateUsage]
    ) -> tuple[int, int]:
        identity = original_validate(connection)
        reader_validated.set()
        if not allow_reader_to_continue.wait(timeout=5.0):
            raise AssertionError("WAL writer did not release status reader")
        return identity

    monkeypatch.setattr(watcher_status, "_validate_snapshot", pause_after_validation)
    try:
        assert writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(read_watcher_status, status_database)
            try:
                assert reader_validated.wait(timeout=5.0)
                _ = writer.execute("BEGIN IMMEDIATE")
                _ = writer.execute(
                    """
                    UPDATE control_state
                    SET state_revision = 100, updated_at_ms = ?
                    WHERE singleton_id = 1
                    """,
                    (_NOW_MS + 1,),
                )
                _ = writer.execute(
                    "UPDATE sources SET enabled = 0 WHERE source_id = 'alpha'"
                )
                _ = writer.execute("COMMIT")
            finally:
                allow_reader_to_continue.set()
            snapshot = future.result(timeout=5.0)

        sources = [_object(item) for item in _list(snapshot["sources"])]
        alpha = next(source for source in sources if source["source_id"] == "alpha")
        assert snapshot["state_revision"] == 99
        assert snapshot["updated_at"] == "2023-11-14T22:13:20.123Z"
        assert alpha["enabled"] is True
        assert writer.execute(
            "SELECT state_revision FROM control_state WHERE singleton_id = 1"
        ).fetchone() == (100,)
        assert writer.execute(
            "SELECT enabled FROM sources WHERE source_id = 'alpha'"
        ).fetchone() == (0,)
    finally:
        allow_reader_to_continue.set()
        writer.close()


def test_status_enforces_deadline_after_sql_execute(
    status_database: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic_calls = 0

    def advancing_monotonic() -> float:
        nonlocal monotonic_calls
        monotonic_calls += 1
        return 0.0 if monotonic_calls <= 2 else 11.0

    monkeypatch.setattr(time, "monotonic", advancing_monotonic)
    monkeypatch.setattr(
        watcher_status, "_PROGRESS_INTERVAL_INSTRUCTIONS", 1_000_000_000
    )

    with pytest.raises(WatcherRuntimeError, match="deadline"):
        _ = read_watcher_status(status_database)


def test_status_accepts_exact_maximum_sources_and_rejects_one_more(
    status_database: Path,
) -> None:
    connection = sqlite3.connect(status_database)
    try:
        for index in range(126):
            _ = connection.execute(
                """
                INSERT INTO sources (
                    source_id, watcher_type, enabled,
                    current_configuration_revision, fencing_epoch,
                    created_at_ms, updated_at_ms
                ) VALUES (?, 'generic', 0, NULL, 0, 1000, 1000)
                """,
                (f"bounded-source-{index:03d}",),
            )
        connection.commit()
    finally:
        connection.close()

    snapshot = read_watcher_status(status_database)
    assert len(_list(snapshot["sources"])) == 128

    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(
            """
            INSERT INTO sources (
                source_id, watcher_type, enabled,
                current_configuration_revision, fencing_epoch,
                created_at_ms, updated_at_ms
            ) VALUES ('source-over-limit', 'generic', 0, NULL, 0, 1000, 1000)
            """
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(WatcherRuntimeError, match="source limit"):
        _ = read_watcher_status(status_database)


@pytest.mark.parametrize(
    ("user_version", "state_version"),
    [(2, 2), (4, 3), (5, 5)],
)
def test_status_rejects_unsupported_or_inconsistent_schema(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
    user_version: int,
    state_version: int,
) -> None:
    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(f"PRAGMA user_version = {user_version}")
        _ = connection.execute(
            "UPDATE control_state SET schema_version = ? WHERE singleton_id = 1",
            (state_version,),
        )
        connection.commit()
    finally:
        connection.close()
    original_digest = hashlib.sha256(status_database.read_bytes()).hexdigest()

    with pytest.raises(SystemExit) as captured:
        main(["watcher", "status", "--database", str(status_database)])

    assert captured.value.code == 1
    assert capsys.readouterr().err == "minos: watcher command failed\n"
    assert hashlib.sha256(status_database.read_bytes()).hexdigest() == original_digest


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("name", "tampered_migration"),
        ("up_sha256", "0" * 64),
        ("down_sha256", "1" * 64),
        ("minimum_reader_version", 2),
    ],
)
def test_status_rejects_tampered_migration_history(
    status_database: Path,
    column: str,
    value: str | int,
) -> None:
    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(
            f"UPDATE control_schema_migrations SET {column} = ? WHERE version = 3",
            (value,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(WatcherRuntimeError, match="schema is unsupported"):
        _ = read_watcher_status(status_database)


def test_status_rejects_gap_in_migration_history(status_database: Path) -> None:
    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(
            "DELETE FROM control_schema_migrations WHERE version = 2"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(WatcherRuntimeError, match="schema is unsupported"):
        _ = read_watcher_status(status_database)


def test_status_rejects_extra_migration_history(status_database: Path) -> None:
    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(
            """
            INSERT INTO control_schema_migrations (
                version, name, up_sha256, down_sha256,
                minimum_reader_version, applied_at_ms
            ) VALUES (5, 'unexpected_migration', ?, ?, 5, ?)
            """,
            ("0" * 64, "1" * 64, _NOW_MS),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(WatcherRuntimeError, match="schema is unsupported"):
        _ = read_watcher_status(status_database)


def test_status_human_output_escapes_terminal_controls(
    status_database: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    category = "failure\x1b[2J\nnext-line\u202e"
    connection = sqlite3.connect(status_database)
    try:
        _ = connection.execute(
            "UPDATE watcher_runs SET error_category = ? WHERE run_id = ?",
            (category, _FAILED_RUN_ID),
        )
        connection.commit()
    finally:
        connection.close()

    main(["watcher", "status", "alpha", "--database", str(status_database), "--json"])
    snapshot = _decode_json_object(capsys.readouterr().out)
    source = _object(_list(snapshot["sources"])[0])
    assert _object(source["latest_run"])["error_category"] == category

    main(["watcher", "status", "alpha", "--database", str(status_database)])
    output = capsys.readouterr().out
    assert "\x1b" not in output
    assert "\u202e" not in output
    assert "failure\\x1b[2J\\nnext-line\\u202e" in output


def test_status_parser_errors_remain_exit_two(status_database: Path) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "watcher",
                "status",
                "--database",
                str(status_database),
                "--unsupported",
            ]
        )

    assert captured.value.code == 2


def _insert_sources(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """
        INSERT INTO sources (
            source_id, watcher_type, enabled, current_configuration_revision,
            fencing_epoch, created_at_ms, updated_at_ms
        ) VALUES ('alpha', 'generic', 1, 2, 7, 1000, 2000)
        """
    )
    for revision in (1, 2):
        _ = connection.execute(
            """
            INSERT INTO source_configuration_revisions (
                source_id, configuration_revision, configuration_sha256,
                configuration_json, created_at_ms
            ) VALUES ('alpha', ?, ?, ?, ?)
            """,
            (
                revision,
                str(revision) * 64,
                json.dumps(
                    {
                        "archive": "/private/archive",
                        "token": "secret-token",
                        "url": "https://secret.example/path",
                    }
                ),
                1000 + revision,
            ),
        )
    _ = connection.execute(
        """
        INSERT INTO sources (
            source_id, watcher_type, enabled, current_configuration_revision,
            fencing_epoch, created_at_ms, updated_at_ms
        ) VALUES ('zulu', 'other', 0, NULL, 0, 1000, 1000)
        """
    )


def _insert_schedules(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """
        INSERT INTO source_schedules (
            source_id, run_mode, cron_expression, next_due_at_ms,
            misfire_grace_seconds, retry_max_attempts, retry_base_seconds,
            retry_max_seconds, pending_scheduled_at_ms, pending_run_mode,
            retry_not_before_ms, retry_failure_count, updated_at_ms
        ) VALUES (
            'alpha', 'scheduled', '0 3 * * *', 8000,
            300, 3, 0.5, 30.0, 7500, 'catch_up', 9000, 2, 7100
        )
        """
    )
    _ = connection.execute(
        """
        INSERT INTO source_schedules (
            source_id, run_mode, cron_expression, next_due_at_ms,
            misfire_grace_seconds, retry_max_attempts, retry_base_seconds,
            retry_max_seconds, updated_at_ms
        ) VALUES (
            'alpha', 'weekly_verification', '0 4 * * 0', 12000,
            600, 4, 1.0, 60.0, 7200
        )
        """
    )


def _insert_runs(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """
        INSERT INTO watcher_runs (
            run_id, source_id, run_mode, status, claim_token, fencing_epoch,
            configuration_revision, scheduled_at_ms, started_at_ms,
            deadline_at_ms, committed_at_ms, finished_at_ms,
            committed_state_revision
        ) VALUES (?, 'alpha', 'scheduled', 'successful', ?, 6, 2,
                  2000, 3000, 10000, 4000, 4500, 40)
        """,
        (_SUCCESS_RUN_ID, "a" * 32),
    )
    _ = connection.execute(
        """
        INSERT INTO watcher_runs (
            run_id, source_id, run_mode, status, claim_token, fencing_epoch,
            configuration_revision, scheduled_at_ms, started_at_ms,
            deadline_at_ms, finished_at_ms, error_category
        ) VALUES (?, 'alpha', 'manual', 'failed', ?, 7, 2,
                  5000, 5000, 10000, 6000, 'upstream_failure')
        """,
        (_FAILED_RUN_ID, "b" * 32),
    )


def _insert_evidence(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        """
        INSERT INTO blobs (sha256, size_bytes, media_type, created_at_ms)
        VALUES (?, 128, 'application/json', 4000)
        """,
        (_RESPONSE_SHA256,),
    )
    for index in range(12):
        digest = f"{index + 1:064x}"
        observation_id = f"observation-{index:05d}"
        _ = connection.execute(
            """
            INSERT INTO blobs (sha256, size_bytes, media_type, created_at_ms)
            VALUES (?, ?, 'application/xml', ?)
            """,
            (digest, 1000 + index, 5000 + index),
        )
        _ = connection.execute(
            """
            INSERT INTO observations (
                observation_id, run_id, source_id, observation_kind,
                source_identity, response_sha256, observed_at_ms,
                metadata_json, state_revision
            ) VALUES (?, ?, 'alpha', 'rsk_product', ?, ?, ?, ?, ?)
            """,
            (
                observation_id,
                _SUCCESS_RUN_ID,
                f"https://secret.example/source/{index}",
                _RESPONSE_SHA256,
                10000 + index,
                json.dumps({"url": "https://secret.example/metadata"}),
                50 + index,
            ),
        )
        _ = connection.execute(
            """
            INSERT INTO observation_blobs (
                observation_id, blob_role, ordinal, sha256
            ) VALUES (?, 'product', ?, ?)
            """,
            (observation_id, index, digest),
        )
        _ = connection.execute(
            """
            INSERT INTO source_changes (
                change_id, run_id, source_id, observation_id, change_category,
                summary_json, state_revision, created_at_ms
            ) VALUES (?, ?, 'alpha', ?, 'catalog_changed', ?, ?, 15000)
            """,
            (
                f"change-{index:09d}",
                _SUCCESS_RUN_ID,
                observation_id,
                json.dumps({"url": "https://secret.example/change"}),
                70 + index,
            ),
        )
    _insert_excluded_product_evidence(connection)


def _insert_excluded_product_evidence(connection: sqlite3.Connection) -> None:
    failed_digest = "e" * 64
    _ = connection.execute(
        """
        INSERT INTO blobs (sha256, size_bytes, media_type, created_at_ms)
        VALUES (?, 9999, 'application/xml', 16000)
        """,
        (failed_digest,),
    )
    _ = connection.execute(
        """
        INSERT INTO observations (
            observation_id, run_id, source_id, observation_kind,
            source_identity, response_sha256, observed_at_ms,
            metadata_json, state_revision
        ) VALUES (
            'failed-product-observation', ?, 'alpha', 'rsk_product',
            'https://secret.example/failed', ?, 20000, '{}', 90
        )
        """,
        (_FAILED_RUN_ID, _RESPONSE_SHA256),
    )
    _ = connection.execute(
        """
        INSERT INTO observation_blobs (
            observation_id, blob_role, ordinal, sha256
        ) VALUES ('failed-product-observation', 'product', 99, ?)
        """,
        (failed_digest,),
    )
    _ = connection.execute(
        """
        INSERT INTO observation_blobs (
            observation_id, blob_role, ordinal, sha256
        ) VALUES ('observation-00011', 'response_body', 0, ?)
        """,
        (_RESPONSE_SHA256,),
    )


def _insert_incidents(connection: sqlite3.Connection) -> None:
    rows = (
        (
            "open-incident-01",
            "alpha",
            _FAILED_RUN_ID,
            "upstream_failure",
            42,
            6000,
            None,
        ),
        (
            "resolved-incident",
            "alpha",
            _FAILED_RUN_ID,
            "old_failure",
            43,
            5000,
            5500,
        ),
        (
            "global-incident01",
            None,
            None,
            "database_capacity",
            44,
            7000,
            None,
        ),
    )
    for row in rows:
        _ = connection.execute(
            """
            INSERT INTO incidents (
                incident_id, source_id, run_id, incident_category,
                details_json, state_revision, created_at_ms, resolved_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                *row[:4],
                json.dumps({"url": "https://secret.example/incident"}),
                *row[4:],
            ),
        )


def _decode_json_object(value: str) -> dict[str, JsonValue]:
    decoded = cast(object, json.loads(value))
    return _object(decoded)


def _object(value: object) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise AssertionError("expected a JSON object")
    mapping = cast(dict[object, object], value)
    if not all(isinstance(key, str) for key in mapping):
        raise AssertionError("expected string JSON keys")
    return cast(dict[str, JsonValue], mapping)


def _list(value: object) -> list[JsonValue]:
    if not isinstance(value, list):
        raise AssertionError("expected a JSON list")
    return cast(list[JsonValue], value)


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise AssertionError("expected a JSON string")
    return value
