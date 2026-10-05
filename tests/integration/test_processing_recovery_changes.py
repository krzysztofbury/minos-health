import hashlib
import json
import sqlite3
import subprocess
import sys
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from typing import cast

import pytest
from pydantic import ValidationError

from minos.ingest.processing.journal import (
    FailureCode,
    JournalError,
    ProcessingJournal,
    ProcessingSelection,
)
from minos.ingest.processing.recovery_changes import (
    ChangeBatch,
    ChangeIdentity,
    ChangeWindow,
    ProcessingReadError,
    read_change_batch,
    require_selected_change,
)
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    BlobReceipt,
    ChangeDraft,
    ClaimRequest,
    ControlStore,
    ObservationCommit,
    ObservationDraft,
    SourceConfiguration,
)
from minos.ingest.watchers.recovery import (
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    VerifiedRecoveryCandidate,
    VerifiedRecoveryImage,
    recovery_key,
)


@contextmanager
def captured_changes(root: Path, *, count: int = 2) -> Generator[VerifiedRecoveryImage]:
    store = ControlStore.open(
        root / "control.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        _ = store.configure_source(SourceConfiguration("other", "rsk", {}, True))
        now_ms = time.time_ns() // 1_000_000
        for source in ("rsk", "other"):
            claim = store.acquire_claim(ClaimRequest(source, "manual", now_ms, 300_000))
            digest = hashlib.sha256(b"{}").hexdigest()
            observation = ObservationDraft(
                f"{source}-catalog-observation",
                "catalog",
                "PRIVATE_SOURCE_URL?TOKEN",
                digest,
                now_ms,
                {"headers": {"authorization": "PRIVATE_SECRET"}},
            )
            _ = store.commit_observations(
                claim.identity,
                ObservationCommit(
                    (
                        BlobReceipt(
                            digest,
                            2,
                            "application/json",
                            f"blobs/sha256/{digest}",
                            digest,
                            2,
                        ),
                    ),
                    (observation,),
                    {"cursor": "captured-only"},
                    tuple(
                        ChangeDraft(
                            f"{source}-source-change-{index:04d}",
                            observation.observation_id,
                            "rsk_source_change",
                            {"private": "PRIVATE_SUMMARY", "ordinal": index},
                        )
                        for index in reversed(range(count))
                    ),
                ),
            )
        staging = RecoveryImageStagingArea(root / "staging")
        with staging.create(store, RecoveryImageRequest("daily", now_ms)) as image:
            yield VerifiedRecoveryImage(
                image.path,
                VerifiedRecoveryCandidate(
                    recovery_key(image.metadata, image.sha256),
                    "selected-version",
                    image.sha256,
                    image.size_bytes,
                    now_ms,
                    image.metadata,
                ),
            )
    finally:
        store.close()


def journal_selection(root: Path) -> ProcessingSelection:
    with captured_changes(root) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        return ProcessingSelection(
            schema_version=1,
            batch=batch,
            baseline_dataset_sha256="a" * 64,
            source_profile_sha256="b" * 64,
        )


def test_journal_coalesces_complete_revision_idempotently_and_reopens_without_activation_state(
    tmp_path: Path,
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        candidate = journal.enqueue(selection)
        assert candidate.selection.batch.changes == selection.batch.changes
        assert candidate.status == "queued"
        assert journal.enqueue(selection) == candidate
        assert journal.pending("rsk") == candidate
        assert journal.checkpoint("rsk").through_state_revision == 0
        claim = journal.claim(
            candidate.candidate_id, worker="processor-1", lease_ms=300_000
        )
        assert journal.require_claim(claim).status == "processing"
        completed = journal.complete(claim, audit_sha256="c" * 64)
        assert completed.through_state_revision == 4  # Not global recovery revision 6.
        assert completed.candidate_id == candidate.candidate_id
        assert completed.audit_sha256 == "c" * 64
        assert completed.status == "processing_checkpoint_only"
        assert journal.complete(claim, audit_sha256="c" * 64) == completed
        with pytest.raises(JournalError, match="conflicts"):
            _ = journal.complete(claim, audit_sha256="d" * 64)
        assert journal.pending("rsk") is None
        assert journal.enqueue(selection).status == "processed"
    finally:
        journal.close()
    journal = ProcessingJournal.open(path)
    try:
        assert journal.checkpoint("rsk") == completed
        assert journal.complete(claim, audit_sha256="c" * 64) == completed
        assert journal.checkpoint("other").through_state_revision == 0
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT status,attempt,audit_sha256 FROM processing_candidates"
        ).fetchall() == [("processed", 1, "c" * 64)]
        assert connection.execute(
            "SELECT status,audit_sha256 FROM processing_attempts"
        ).fetchall() == [("processed", "c" * 64)]
        assert connection.execute(
            "SELECT source_id FROM processing_checkpoints"
        ).fetchall() == [("rsk",)]
        tables = cast(
            list[tuple[str]],
            connection.execute(
                "SELECT name FROM sqlite_schema WHERE type='table'"
            ).fetchall(),
        )
        assert not any("activation" in row[0] or "watcher" in row[0] for row in tables)
    assert path.stat().st_mode & 0o777 == 0o600
    assert b"PRIVATE_" not in path.read_bytes()


def test_failed_candidate_blocks_new_windows_and_persists_each_retry_without_advancing(
    tmp_path: Path,
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        candidate = journal.enqueue(selection)
        claim = journal.claim(
            candidate.candidate_id, worker="processor", lease_ms=300_000
        )
        journal.fail(claim, "parse_failed")
        assert journal.checkpoint("rsk").through_state_revision == 0
        failed = journal.pending("rsk")
        assert failed is not None and failed.failure_code == "parse_failed"
        with pytest.raises(JournalError, match="not claimable"):
            _ = journal.claim(candidate.candidate_id, worker="retry", lease_ms=300_000)
        different = selection.model_copy(update={"source_profile_sha256": "d" * 64})
        with pytest.raises(JournalError, match="unresolved candidate"):
            _ = journal.enqueue(different)
        journal.retry(candidate.candidate_id)
        retry = journal.claim(candidate.candidate_id, worker="retry", lease_ms=300_000)
        assert retry.attempt == 2
        for operation in (
            lambda: journal.require_claim(claim),
            lambda: journal.fail(claim, "build_failed"),
            lambda: journal.complete(claim, audit_sha256="c" * 64),
        ):
            with pytest.raises(JournalError, match="stale"):
                _ = operation()
        journal.fail(retry, "policy_review_required")
    finally:
        journal.close()
    journal = ProcessingJournal.open(path)
    try:
        assert journal.checkpoint("rsk").through_state_revision == 0
        pending = journal.pending("rsk")
        assert pending is not None and pending.failure_code == "policy_review_required"
        assert pending.attempt == 2 and pending.status == "failed"
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT attempt,status,failure_code FROM processing_attempts ORDER BY attempt"
        ).fetchall() == [
            (1, "failed", "parse_failed"),
            (2, "failed", "policy_review_required"),
        ]


def test_expired_claim_is_fenced_across_connections_and_reclaimed_only_for_same_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    first = ProcessingJournal.open(path)
    second = ProcessingJournal.open(path)
    clock_ms = time.time_ns() // 1_000_000
    monkeypatch.setattr(time, "time_ns", lambda: clock_ms * 1_000_000)
    try:
        candidate = first.enqueue(selection)
        old = first.claim(candidate.candidate_id, worker="old", lease_ms=1000)
        with pytest.raises(JournalError, match="already held"):
            _ = second.claim(candidate.candidate_id, worker="new", lease_ms=1000)
        clock_ms += 1000
        with pytest.raises(JournalError, match="stale"):
            _ = first.complete(old, audit_sha256="c" * 64)
        new = second.claim(candidate.candidate_id, worker="new", lease_ms=1000)
        assert new.attempt == 2
        with pytest.raises(JournalError, match="stale"):
            _ = first.complete(old, audit_sha256="c" * 64)
        _ = second.complete(new, audit_sha256="c" * 64)
        assert first.checkpoint("rsk").through_state_revision == 4
    finally:
        first.close()
        second.close()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT attempt,status FROM processing_attempts ORDER BY attempt"
        ).fetchall() == [(1, "expired"), (2, "processed")]


def test_retry_fences_old_epoch_even_when_worker_and_deadline_are_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = journal_selection(tmp_path)
    now_ns = time.time_ns()
    monkeypatch.setattr(time, "time_ns", lambda: now_ns)
    journal = ProcessingJournal.open(tmp_path / "processing.sqlite3")
    try:
        candidate = journal.enqueue(selection)
        old = journal.claim(
            candidate.candidate_id, worker="same-worker", lease_ms=300_000
        )
        journal.fail(old, "parse_failed")
        journal.retry(candidate.candidate_id)
        new = journal.claim(
            candidate.candidate_id, worker="same-worker", lease_ms=300_000
        )
        assert old.lease_until_ms == new.lease_until_ms and old.worker == new.worker
        assert old.attempt == 1 and new.attempt == 2
        with pytest.raises(JournalError, match="stale"):
            _ = journal.require_claim(old)
        assert journal.require_claim(new).status == "processing"
    finally:
        journal.close()


def test_simultaneous_connections_cannot_hold_conflicting_claims(
    tmp_path: Path,
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        candidate = journal.enqueue(selection)
    finally:
        journal.close()
    barrier = Barrier(2)

    def compete(worker: str) -> str:
        connection = ProcessingJournal.open(path)
        try:
            _ = barrier.wait(timeout=10)
            try:
                _ = connection.claim(
                    candidate.candidate_id, worker=worker, lease_ms=300_000
                )
            except JournalError as error:
                assert str(error) == "processing candidate lease is already held"
                return "blocked"
            return "held"
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(compete, ("one", "two")))
    assert sorted(results) == ["blocked", "held"]
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM processing_attempts").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "after_commit",
    [False, True],
    ids=["killed-before-commit", "lost-response-after-commit"],
)
def test_interrupted_completion_reopens_atomic_candidate_and_checkpoint(
    tmp_path: Path, after_commit: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        candidate = journal.enqueue(selection)
        claim = journal.claim(candidate.candidate_id, worker="child", lease_ms=300_000)
    finally:
        journal.close()
    child = """
import os, sqlite3, sys
from pathlib import Path
from minos.ingest.processing.journal import ProcessingClaim, ProcessingJournal
original = sqlite3.connect
armed = False
class Interrupted(sqlite3.Connection):
    def execute(self, statement, parameters=()):
        result = super().execute(statement, parameters)
        if armed and ((sys.argv[3] == 'True' and statement == 'COMMIT') or
                      (sys.argv[3] == 'False' and statement.startswith('UPDATE processing_checkpoints'))):
            os._exit(91)
        return result
sqlite3.connect = lambda *args, **kwargs: original(*args, factory=Interrupted, **kwargs)
journal = ProcessingJournal.open(Path(sys.argv[1]))
armed = True
journal.complete(ProcessingClaim.model_validate_json(sys.argv[2]), audit_sha256='c'*64)
raise RuntimeError('fault point was not exercised')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            child,
            str(path),
            claim.model_dump_json(),
            str(after_commit),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 91, result.stdout + result.stderr
    journal = ProcessingJournal.open(path)
    try:
        if after_commit:
            assert journal.checkpoint("rsk").through_state_revision == 4
            assert journal.pending("rsk") is None
            assert (
                journal.complete(claim, audit_sha256="c" * 64).through_state_revision
                == 4
            )
        else:
            assert journal.checkpoint("rsk").through_state_revision == 0
            interrupted = journal.pending("rsk")
            assert interrupted is not None and interrupted.status == "processing"
            monkeypatch.setattr(
                time, "time_ns", lambda: (claim.lease_until_ms + 1) * 1_000_000
            )
            renewed = journal.claim(
                candidate.candidate_id, worker="recovery", lease_ms=300_000
            )
            assert renewed.attempt == 2
            _ = journal.complete(renewed, audit_sha256="c" * 64)
    finally:
        journal.close()
    assert not path.with_name(path.name + "-journal").exists()


@pytest.mark.parametrize("damage", ["selection", "checkpoint", "audit", "attempt"])
def test_journal_rejects_tampered_persisted_identity_or_paired_evidence(
    tmp_path: Path, damage: str
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        candidate = journal.enqueue(selection)
        claim = journal.claim(
            candidate.candidate_id, worker="processor", lease_ms=300_000
        )
        if damage in ("checkpoint", "audit"):
            _ = journal.complete(claim, audit_sha256="c" * 64)
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection, connection:
        if damage == "selection":
            data = selection.model_dump(mode="json")
            data["baseline_dataset_sha256"] = "e" * 64
            _ = connection.execute(
                "UPDATE processing_candidates SET selection_json=?", (json.dumps(data),)
            )
        elif damage == "checkpoint":
            _ = connection.execute(
                "UPDATE processing_checkpoints SET through_state_revision=99"
            )
        elif damage == "audit":
            _ = connection.execute(
                "UPDATE processing_checkpoints SET audit_sha256=?", ("d" * 64,)
            )
        else:
            _ = connection.execute(
                "UPDATE processing_attempts SET worker='different-worker'"
            )
    journal = ProcessingJournal.open(path)
    try:
        with pytest.raises(JournalError) as failure:
            if damage in ("selection", "attempt"):
                _ = journal.require_claim(claim)
            else:
                _ = journal.checkpoint("rsk")
        assert "PRIVATE_" not in str(failure.value)
        assert str(tmp_path) not in str(failure.value)
    finally:
        journal.close()


def test_renewal_preserves_attempt_but_invalidates_old_lease_and_refuses_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = journal_selection(tmp_path)
    journal = ProcessingJournal.open(tmp_path / "processing.sqlite3")
    clock_ms = time.time_ns() // 1_000_000
    monkeypatch.setattr(time, "time_ns", lambda: clock_ms * 1_000_000)
    try:
        candidate = journal.enqueue(selection)
        old = journal.claim(candidate.candidate_id, worker="worker", lease_ms=1000)
        clock_ms += 500
        extended = journal.renew(old, lease_ms=1000)
        assert extended.attempt == 1 and extended.lease_until_ms == clock_ms + 1000
        with pytest.raises(JournalError, match="stale"):
            _ = journal.complete(old, audit_sha256="c" * 64)
        assert journal.require_claim(extended).status == "processing"
        with pytest.raises(JournalError, match="extend"):
            _ = journal.renew(extended, lease_ms=1)
        clock_ms += 1000
        with pytest.raises(JournalError, match="stale"):
            _ = journal.renew(extended, lease_ms=1000)
        assert journal.checkpoint("rsk").through_state_revision == 0
    finally:
        journal.close()


def test_attempt_bound_refuses_further_takeover_without_losing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    clock_ms = time.time_ns() // 1_000_000
    monkeypatch.setattr(time, "time_ns", lambda: clock_ms * 1_000_000)
    try:
        candidate = journal.enqueue(selection)
        for attempt in range(1, 33):
            assert (
                journal.claim(
                    candidate.candidate_id, worker="worker", lease_ms=1
                ).attempt
                == attempt
            )
            clock_ms += 1
        with pytest.raises(JournalError, match="attempt bound"):
            _ = journal.claim(candidate.candidate_id, worker="worker", lease_ms=1)
        assert journal.checkpoint("rsk").through_state_revision == 0
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM processing_attempts").fetchone()[0]
            == 32
        )


@pytest.mark.parametrize(
    "operation", ["window", "failure-code", "audit", "lease", "empty-window"]
)
def test_journal_refuses_unsupported_commands_without_checkpoint_advance_or_private_errors(
    tmp_path: Path, operation: str
) -> None:
    selection = journal_selection(tmp_path)
    journal = ProcessingJournal.open(tmp_path / "processing.sqlite3")
    try:
        candidate = journal.enqueue(selection)
        claim = journal.claim(candidate.candidate_id, worker="worker", lease_ms=300_000)
        with pytest.raises((JournalError, ValidationError)) as failure:
            if operation == "window":
                batch = selection.batch.model_copy(
                    update={
                        "window": ChangeWindow(source_id="rsk", after_state_revision=1)
                    }
                )
                _ = journal.enqueue(selection.model_copy(update={"batch": batch}))
            elif operation == "failure-code":
                journal.fail(claim, cast(FailureCode, cast(object, "PRIVATE_SECRET")))
            elif operation == "audit":
                _ = journal.complete(claim, audit_sha256="PRIVATE_SECRET")
            elif operation == "lease":
                _ = journal.renew(claim, lease_ms=True)
            else:
                data = selection.model_dump(mode="json")
                data["batch"]["changes"] = []
                _ = ProcessingSelection.model_validate_json(json.dumps(data))
        assert "PRIVATE_" not in str(failure.value)
        assert str(tmp_path) not in str(failure.value)
        assert journal.checkpoint("rsk").through_state_revision == 0
        assert journal.require_claim(claim).status == "processing"
    finally:
        journal.close()


@pytest.mark.parametrize(
    "kind",
    [
        "foreign-database",
        "extra-schema",
        "wrong-version",
        "symlink",
        "public-file",
        "wal-mode",
    ],
)
def test_journal_open_refuses_foreign_or_unsafe_files_without_overwriting_them(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "processing.sqlite3"
    if kind == "foreign-database":
        with closing(sqlite3.connect(path)) as connection, connection:
            _ = connection.execute("CREATE TABLE unrelated(value TEXT)")
    else:
        journal = ProcessingJournal.open(path)
        journal.close()
        if kind in ("extra-schema", "wrong-version", "wal-mode"):
            with closing(sqlite3.connect(path)) as connection, connection:
                statement = {
                    "extra-schema": "CREATE TABLE foreign_table(value TEXT)",
                    "wrong-version": "PRAGMA user_version=2",
                    "wal-mode": "PRAGMA journal_mode=WAL",
                }[kind]
                _ = connection.execute(statement)
    path.chmod(0o600)
    if kind == "symlink":
        actual = tmp_path / "actual.sqlite3"
        _ = path.rename(actual)
        path.symlink_to(actual)
    if kind == "public-file":
        path.chmod(0o644)
    before = path.read_bytes()
    with pytest.raises(JournalError, match="could not be opened") as failure:
        _ = ProcessingJournal.open(path)
    assert str(tmp_path) not in str(failure.value)
    assert path.read_bytes() == before


@pytest.mark.parametrize("bound", ["candidates", "selection-bytes"])
def test_journal_resource_bounds_refuse_without_partial_source_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    import minos.ingest.processing.journal as module

    with captured_changes(tmp_path) as image:
        selected = [
            ProcessingSelection(
                schema_version=1,
                batch=read_change_batch(
                    image, ChangeWindow(source_id=source, after_state_revision=0)
                ),
                baseline_dataset_sha256="a" * 64,
                source_profile_sha256="b" * 64,
            )
            for source in ("rsk", "other")
        ]
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        if bound == "candidates":
            monkeypatch.setattr(module, "MAX_CANDIDATES", 1)
            _ = journal.enqueue(selected[0])
            with pytest.raises(JournalError, match="candidate bound"):
                _ = journal.enqueue(selected[1])
            expected_sources = [("rsk",)]
        else:
            monkeypatch.setattr(module, "MAX_SELECTION_BYTES", 1)
            with pytest.raises(JournalError, match="byte bound"):
                _ = journal.enqueue(selected[0])
            expected_sources = []
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT source_id FROM processing_checkpoints"
            ).fetchall()
            == expected_sources
        )


@pytest.mark.parametrize("failure_kind", ["busy", "deadline"])
def test_journal_storage_outage_rolls_back_without_source_or_checkpoint_advance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_kind: str
) -> None:
    selection = journal_selection(tmp_path)
    path = tmp_path / "processing.sqlite3"
    journal = ProcessingJournal.open(path)
    try:
        if failure_kind == "busy":
            with closing(sqlite3.connect(path)) as blocker:
                _ = blocker.execute("BEGIN IMMEDIATE")
                try:
                    with pytest.raises(
                        JournalError, match="transaction could not be verified"
                    ):
                        _ = journal.enqueue(selection)
                finally:
                    blocker.rollback()
        else:
            readings = 0

            def clock() -> float:
                nonlocal readings
                readings += 1
                return 0.0 if readings == 1 else 31.0

            with monkeypatch.context() as patch:
                patch.setattr(time, "monotonic", clock)
                with pytest.raises(JournalError, match="deadline exceeded"):
                    _ = journal.enqueue(selection)
        assert journal.checkpoint("rsk").through_state_revision == 0
        assert journal.pending("rsk") is None
    finally:
        journal.close()
    with closing(sqlite3.connect(path)) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM processing_checkpoints"
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute("SELECT count(*) FROM processing_candidates").fetchone()[
                0
            ]
            == 0
        )


def test_processing_reader_is_deterministic_read_only_and_exports_no_private_state(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path) as image:
        before = image.path.read_bytes()
        window = ChangeWindow(source_id="rsk", after_state_revision=0)
        first = read_change_batch(image, window)
        assert read_change_batch(image, window) == first
        assert [change.change_id for change in first.changes] == [
            "rsk-source-change-0000",
            "rsk-source-change-0001",
        ]
        assert all(change.source_id == "rsk" for change in first.changes)
        assert first.recovery.version_id == "selected-version"
        assert first.recovery.sha256 == hashlib.sha256(before).hexdigest()
        for change in first.changes:
            assert require_selected_change(image, first.recovery, change) == change
            assert change.observation_sha256 == hashlib.sha256(b"{}").hexdigest()
        assert "PRIVATE_" not in first.model_dump_json()
        assert "headers" not in first.model_dump_json()
        assert str(tmp_path) not in first.model_dump_json()
        assert image.path.read_bytes() == before
        assert (
            read_change_batch(
                image,
                ChangeWindow(
                    source_id="rsk",
                    after_state_revision=first.changes[-1].state_revision,
                ),
            ).changes
            == ()
        )
    assert list((tmp_path / "staging").iterdir()) == []


@pytest.mark.parametrize(
    "field",
    [
        "source_id",
        "run_id",
        "observation_id",
        "state_revision",
        "observation_sha256",
        "change_summary_sha256",
        "configuration_sha256",
    ],
)
def test_reselection_checks_every_identity_not_just_change_id(
    tmp_path: Path, field: str
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        raw = batch.changes[0].model_dump(mode="json")
        raw[field] = (
            raw[field] + 1
            if field == "state_revision"
            else "a" * 64
            if field.endswith("sha256")
            else "another-identity"
        )
        changed = ChangeIdentity.model_validate_json(json.dumps(raw))
        with pytest.raises(ProcessingReadError, match="change identity changed"):
            _ = require_selected_change(image, batch.recovery, changed)


def test_processing_reader_refuses_partial_batches_and_stale_or_unknown_windows(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path, count=129) as image:
        for window, message in (
            (
                ChangeWindow(source_id="rsk", after_state_revision=0),
                "exceeds its bound",
            ),
            (
                ChangeWindow(source_id="absent", after_state_revision=0),
                "source is absent",
            ),
            (
                ChangeWindow(
                    source_id="rsk",
                    after_state_revision=image.candidate.metadata.state_revision + 1,
                ),
                "checkpoint is ahead",
            ),
        ):
            with pytest.raises(ProcessingReadError, match=message):
                _ = read_change_batch(image, window)


@pytest.mark.parametrize(
    "damage", ["bytes", "version", "revision", "migration", "symlink"]
)
def test_reader_reverifies_the_exact_image_and_pin_before_selecting(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as original:
        image = original
        if damage == "bytes":
            _ = image.path.write_bytes(b"not SQLite")
        elif damage == "version":
            image = replace(
                image, candidate=replace(image.candidate, version_id="null")
            )
        elif damage in ("revision", "migration"):
            metadata = replace(
                image.candidate.metadata,
                **(
                    {"state_revision": image.candidate.metadata.state_revision + 1}
                    if damage == "revision"
                    else {"migration_set_sha256": "a" * 64}
                ),
            )
            image = replace(
                image, candidate=replace(image.candidate, metadata=metadata)
            )
        else:
            link = tmp_path / "image-link.sqlite3"
            link.symlink_to(image.path)
            image = replace(image, path=link)
        with pytest.raises(ProcessingReadError):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


@pytest.mark.parametrize(
    "damage",
    [
        "change_revision",
        "observation_revision",
        "configuration",
        "foreign_key",
        "migration",
    ],
)
def test_sqlite_relationship_guards_are_independent_of_outer_checksum(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as original:
        connection = sqlite3.connect(original.path)
        try:
            if damage == "change_revision":
                _ = connection.execute(
                    "UPDATE source_changes SET state_revision = 1 WHERE source_id = 'rsk'"
                )
            elif damage == "observation_revision":
                _ = connection.execute(
                    "UPDATE observations SET state_revision = 1 WHERE source_id = 'rsk'"
                )
            elif damage == "configuration":
                _ = connection.execute(
                    "UPDATE source_configuration_revisions SET configuration_sha256 = ? WHERE source_id = 'rsk'",
                    ("a" * 64,),
                )
            elif damage == "foreign_key":
                _ = connection.execute(
                    "UPDATE source_changes SET observation_id = 'other-catalog-observation' WHERE source_id = 'rsk'"
                )
            else:
                _ = connection.execute(
                    "UPDATE control_schema_migrations SET up_sha256 = ? WHERE version = 1",
                    ("a" * 64,),
                )
            connection.commit()
        finally:
            connection.close()
        digest = hashlib.sha256(original.path.read_bytes()).hexdigest()
        image = replace(
            original,
            candidate=replace(
                original.candidate,
                sha256=digest,
                key=recovery_key(original.candidate.metadata, digest),
            ),
        )
        expected = {
            "change_revision": "captured run revision",
            "observation_revision": "observation revisions disagree",
            "configuration": "configuration checksum differs",
            "foreign_key": "input could not be verified",
            "migration": "input could not be verified",
        }
        with pytest.raises(ProcessingReadError, match=expected[damage]):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


def test_reader_deadline_and_input_validation_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for raw in (
        {"source_id": "rsk", "after_state_revision": True},
        {"source_id": "rsk'; DROP TABLE sources", "after_state_revision": 0},
    ):
        with pytest.raises(ValidationError):
            _ = ChangeWindow.model_validate(raw)
    with captured_changes(tmp_path) as image:
        calls = 0

        def expired() -> float:
            nonlocal calls
            calls += 1
            return 0.0 if calls == 1 else 301.0

        monkeypatch.setattr(time, "monotonic", expired)
        with pytest.raises(ProcessingReadError, match="deadline exceeded"):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )


@pytest.mark.parametrize(
    "damage", ["order", "duplicate", "source", "before-window", "after-image", "key"]
)
def test_serialized_batch_refuses_inconsistent_identities(
    tmp_path: Path, damage: str
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        raw = batch.model_dump(mode="json")
        if damage == "order":
            raw["changes"] = [raw["changes"][1], raw["changes"][0]]
        elif damage == "duplicate":
            raw["changes"] = [raw["changes"][0], raw["changes"][0]]
        elif damage == "source":
            raw["changes"][0]["source_id"] = "other"
        elif damage == "before-window":
            raw["window"]["after_state_revision"] = raw["changes"][0]["state_revision"]
        elif damage == "after-image":
            raw["changes"] = [raw["changes"][0]]
            raw["changes"][0]["state_revision"] = raw["recovery"]["state_revision"] + 1
        else:
            raw["recovery"]["key"] = "terraform.tfstate"
        with pytest.raises(ValidationError):
            _ = ChangeBatch.model_validate_json(json.dumps(raw))


def test_reselection_refuses_a_different_transport_version_even_for_identical_bytes(
    tmp_path: Path,
) -> None:
    with captured_changes(tmp_path) as image:
        batch = read_change_batch(
            image, ChangeWindow(source_id="rsk", after_state_revision=0)
        )
        rotated = replace(
            image, candidate=replace(image.candidate, version_id="another-version")
        )
        with pytest.raises(ProcessingReadError, match="recovery identity changed"):
            _ = require_selected_change(rotated, batch.recovery, batch.changes[0])


def test_file_replacement_during_read_never_returns_a_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with captured_changes(tmp_path) as image:
        real_connect = sqlite3.connect
        opened = 0

        def replacing_connect(
            database: str,
            *,
            uri: bool,
            isolation_level: None,
            timeout: float = 5.0,
        ) -> sqlite3.Connection:
            nonlocal opened
            connection = real_connect(
                database, uri=uri, isolation_level=isolation_level, timeout=timeout
            )
            opened += 1
            if opened == 2:
                # The validator owns the first immutable connection. Rotate the
                # path only after the downstream descriptor-backed reader opens.
                replacement = tmp_path / "same-bytes.sqlite3"
                _ = replacement.write_bytes(image.path.read_bytes())
                _ = replacement.replace(image.path)
            return connection

        monkeypatch.setattr(sqlite3, "connect", replacing_connect)
        with pytest.raises(ProcessingReadError, match="changed during processing read"):
            _ = read_change_batch(
                image, ChangeWindow(source_id="rsk", after_state_revision=0)
            )
