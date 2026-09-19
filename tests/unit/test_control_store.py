import json
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast, override

import pytest

from minos.ingest.watchers.control import (
    BlobReceipt,
    BlobVerificationError,
    ChangeDraft,
    ClaimBlockedError,
    ClaimRequest,
    CommittedRun,
    ControlStorageError,
    ControlStore,
    ControlValidationError,
    IllegalRunTransitionError,
    IncidentDraft,
    ObservationBlob,
    ObservationCommit,
    ObservationDraft,
    RecoveryReceipt,
    SourceConfiguration,
    SourceSchedule,
    StaleClaimError,
)

_NOW_MS = 1_800_000_000_000
_SOURCE_ID = "rsk"
_OBSERVATION_ID = "observation-0001"
_CHANGE_ID = "change-000000001"
_INCIDENT_ID = "incident-0000001"
_SHA256 = "a" * 64
_NEXT_DAILY_FIRE_MS = 1_800_068_400_000


class _TestClock:
    def __init__(self) -> None:
        self.now_ms: int = _NOW_MS

    def __call__(self) -> int:
        return self.now_ms

    def set(self, now_ms: int) -> None:
        self.now_ms = now_ms


class _FaultingConnection(sqlite3.Connection):
    fail_commit: bool = False
    fail_after_commit: bool = False
    fail_rollback: bool = False

    @override
    def commit(self) -> None:
        if self.fail_commit:
            raise sqlite3.OperationalError("injected commit failure")
        super().commit()
        if self.fail_after_commit:
            raise sqlite3.OperationalError("injected post-commit failure")

    @override
    def rollback(self) -> None:
        if self.fail_rollback:
            raise sqlite3.OperationalError("injected rollback failure")
        super().rollback()


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "control.sqlite3"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _TestClock:
    test_clock = _TestClock()
    monkeypatch.setattr(
        "minos.ingest.watchers.control.store._utc_now_ms",
        test_clock,
    )
    return test_clock


@pytest.fixture
def store(database_path: Path, clock: _TestClock) -> Iterator[ControlStore]:
    _ = clock
    control_store = ControlStore.open(database_path)
    try:
        yield control_store
    finally:
        control_store.close()


def test_configuration_revisions_are_canonical_audited_and_idempotent(
    store: ControlStore,
    database_path: Path,
) -> None:
    first = store.configure_source(
        SourceConfiguration(
            _SOURCE_ID,
            "rsk",
            {"timeout_seconds": 20, "connect_timeout_seconds": 5},
            True,
        ),
    )
    repeated = store.configure_source(
        SourceConfiguration(
            _SOURCE_ID,
            "rsk",
            {"connect_timeout_seconds": 5, "timeout_seconds": 20},
            True,
        ),
    )
    changed = store.configure_source(
        SourceConfiguration(
            _SOURCE_ID,
            "rsk",
            {"connect_timeout_seconds": 5, "timeout_seconds": 30},
            False,
        ),
    )

    assert first.configuration_revision == 1
    assert first.state_revision == 1
    assert first.changed is True
    assert repeated.configuration_revision == 1
    assert repeated.state_revision == 1
    assert repeated.changed is False
    assert changed.configuration_revision == 2
    assert changed.state_revision == 2
    assert changed.changed is True
    revisions = _query(
        database_path,
        """
        SELECT configuration_revision, configuration_json
        FROM source_configuration_revisions
        ORDER BY configuration_revision
        """,
    )
    assert [row[0] for row in revisions] == [1, 2]
    assert [json.loads(str(row[1]))["timeout_seconds"] for row in revisions] == [20, 30]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(2,)]
    assert _query(
        database_path, "SELECT event_type FROM audit_events ORDER BY rowid"
    ) == [
        ("source_configured",),
        ("source_configured",),
    ]


def test_persisted_configuration_ignores_environment_and_rejects_coercion(
    store: ControlStore,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINOS_RSK_TIMEOUT_SECONDS", "99")
    configured = store.configure_source(
        SourceConfiguration(_SOURCE_ID, "rsk", {}, True)
    )

    assert configured.configuration_revision == 1
    stored = _query(
        database_path,
        "SELECT configuration_json FROM source_configuration_revisions",
    )
    assert json.loads(str(stored[0][0]))["timeout_seconds"] == 20.0

    with pytest.raises(ControlValidationError, match="invalid"):
        _ = store.configure_source(
            SourceConfiguration(
                _SOURCE_ID,
                "rsk",
                {"file_max_bytes": str(512 * 1024 * 1024)},
                True,
            ),
        )
    with pytest.raises(ControlValidationError, match="invalid"):
        _ = store.configure_source(
            SourceConfiguration(
                _SOURCE_ID,
                "rsk",
                {"file_max_bytes": 512 * 1024 * 1024 + 1},
                True,
            ),
        )


def test_enabling_source_does_not_duplicate_unchanged_configuration(
    store: ControlStore,
    database_path: Path,
) -> None:
    command = SourceConfiguration(
        _SOURCE_ID,
        "rsk",
        {},
        False,
    )
    first = store.configure_source(command)
    enabled = store.configure_source(replace(command, enabled=True))

    assert first.configuration_revision == 1
    assert enabled.configuration_revision == 1
    assert enabled.state_revision == 2
    assert _query(
        database_path,
        "SELECT configuration_revision FROM source_configuration_revisions",
    ) == [(1,)]


def test_configuration_rejects_secrets_and_immutable_watcher_type(
    store: ControlStore,
    database_path: Path,
) -> None:
    with pytest.raises(ControlValidationError, match="secret-like"):
        _ = store.configure_source(
            SourceConfiguration(_SOURCE_ID, "rsk", {"apiToken": "secret"}, True),
        )
    assert _query(database_path, "SELECT source_id FROM sources") == []

    with pytest.raises(ControlValidationError, match="unsupported keys"):
        _ = store.configure_source(
            SourceConfiguration(_SOURCE_ID, "rsk", {"unknown": True}, True),
        )
    assert _query(database_path, "SELECT source_id FROM sources") == []

    _configure_source(store)
    with pytest.raises(ControlValidationError, match="registered configuration"):
        _ = store.configure_source(
            SourceConfiguration(
                _SOURCE_ID, "baw", {"url": "https://example.test"}, True
            ),
        )
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]


def test_schedule_update_is_revisioned_and_idempotent(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    schedule = _schedule()

    first = store.set_schedule(schedule)
    repeated = store.set_schedule(schedule)

    assert first.state_revision == 2
    assert first.next_due_at_ms == _NEXT_DAILY_FIRE_MS
    assert first.changed is True
    assert repeated.state_revision == 2
    assert repeated.changed is False
    assert _query(
        database_path,
        "SELECT next_due_at_ms, retry_max_attempts FROM source_schedules",
    ) == [(_NEXT_DAILY_FIRE_MS, 3)]


def test_schedule_rejects_invalid_cron_before_write(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)

    with pytest.raises(ControlValidationError, match="cron expression is invalid"):
        _ = store.set_schedule(
            replace(_schedule(), cron_expression="99 99 * * *"),
        )

    assert _query(database_path, "SELECT source_id FROM source_schedules") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]


def test_schedule_policy_edit_preserves_overdue_cursor(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    _configure_source(store)
    scheduled = store.set_schedule(_schedule())
    clock.set(_NOW_MS)

    updated = store.set_schedule(replace(_schedule(), retry_max_attempts=4))

    assert scheduled.next_due_at_ms < _NOW_MS
    assert updated.next_due_at_ms == scheduled.next_due_at_ms
    assert _query(
        database_path,
        "SELECT next_due_at_ms, retry_max_attempts FROM source_schedules",
    ) == [(scheduled.next_due_at_ms, 4)]


def test_schedule_rejects_boolean_and_nonnumeric_policy_values(
    store: ControlStore,
) -> None:
    _configure_source(store)

    with pytest.raises(
        ControlValidationError, match="misfire grace must be an integer"
    ):
        _ = store.set_schedule(replace(_schedule(), misfire_grace_seconds=True))
    with pytest.raises(ControlValidationError, match="retry base must be numeric"):
        _ = store.set_schedule(
            replace(
                _schedule(),
                retry_base_seconds=cast(float, cast(object, "slow")),
            )
        )
    with pytest.raises(ControlValidationError, match="retry base.*supported range"):
        _ = store.set_schedule(
            replace(_schedule(), retry_base_seconds=cast(float, 10**1000))
        )
    with pytest.raises(ControlValidationError, match="retry maximum.*supported range"):
        _ = store.set_schedule(
            replace(_schedule(), retry_max_seconds=cast(float, 10**1000))
        )


def test_restorable_transaction_rejects_clock_regression(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    clock.set(_NOW_MS - 1)

    with pytest.raises(ControlStorageError, match="monotonic timestamp"):
        _ = store.set_schedule(_schedule())

    assert _query(database_path, "SELECT source_id FROM source_schedules") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]


def test_due_claim_advances_cursor_and_blocks_overlap(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    _configure_source(store)
    schedule = store.set_schedule(_schedule())
    due_at_ms = schedule.next_due_at_ms
    clock.set(_NOW_MS)

    claim = store.acquire_claim(
        ClaimRequest(
            _SOURCE_ID,
            "catch_up",
            due_at_ms,
            300_000,
            expected_due_at_ms=due_at_ms,
        ),
    )

    assert claim.identity.fencing_epoch == 1
    assert claim.identity.configuration_revision == 1
    assert len(claim.identity.claim_token) == 64
    assert claim.state_revision == 3
    assert _query(
        database_path,
        "SELECT next_due_at_ms FROM source_schedules",
    ) == [(_NEXT_DAILY_FIRE_MS,)]
    with pytest.raises(ClaimBlockedError, match="blocking run"):
        _ = store.acquire_claim(_manual_claim_request(_NOW_MS))


def test_due_cursor_compare_and_swap_failure_rolls_back_claim(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    _configure_source(store)
    _ = store.set_schedule(_schedule())
    clock.set(_NOW_MS)

    with pytest.raises(ClaimBlockedError, match="cursor changed"):
        _ = store.acquire_claim(
            ClaimRequest(
                _SOURCE_ID,
                "scheduled",
                _NOW_MS - 1,
                300_000,
                expected_due_at_ms=_NOW_MS - 1,
            ),
        )

    assert _query(database_path, "SELECT fencing_epoch FROM sources") == [(0,)]
    assert _query(database_path, "SELECT run_id FROM watcher_runs") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(2,)]


def test_stale_claim_cannot_fail_or_commit_after_ownership_changes(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    stale = replace(claim.identity, claim_token="0" * 64)

    with pytest.raises(StaleClaimError):
        _ = store.fail_claim(stale, error_category="network")
    with pytest.raises(StaleClaimError):
        _ = store.commit_observations(
            stale,
            _observation_commit(),
        )

    assert _query(database_path, "SELECT status FROM watcher_runs") == [("running",)]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(2,)]
    assert _query(database_path, "SELECT observation_id FROM observations") == []


def test_failure_releases_source_and_increments_fencing_epoch(
    store: ControlStore,
) -> None:
    _configure_source(store)
    first = store.acquire_claim(_manual_claim_request(_NOW_MS))

    failed = store.fail_claim(
        first.identity,
        error_category="network",
    )
    second = store.acquire_claim(
        _manual_claim_request(_NOW_MS),
    )

    assert failed.status == "failed"
    assert failed.state_revision == 3
    assert second.identity.fencing_epoch == 2
    assert second.state_revision == 4


def test_claim_deadline_is_derived_after_lock_and_bounded_by_configuration(
    store: ControlStore,
) -> None:
    _configure_source(store)

    with pytest.raises(ControlValidationError, match="configured operation deadline"):
        _ = store.acquire_claim(
            _manual_claim_request(_NOW_MS, deadline_after_ms=300_001)
        )

    claim = store.acquire_claim(
        _manual_claim_request(_NOW_MS, deadline_after_ms=300_000)
    )
    assert claim.identity.deadline_at_ms == claim.started_at_ms + 300_000


def test_expiry_requires_deadline_and_atomically_records_incident(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(
        _manual_claim_request(_NOW_MS, deadline_after_ms=10),
    )
    incident = IncidentDraft(_INCIDENT_ID, "claim_deadline", {"phase": "download"})

    clock.set(_NOW_MS + 9)
    with pytest.raises(StaleClaimError, match="not eligible"):
        _ = store.expire_claim(claim.identity, incident)
    clock.set(_NOW_MS + 10)
    expired = store.expire_claim(claim.identity, incident)

    assert expired.status == "expired"
    assert expired.state_revision == 3
    assert _query(
        database_path,
        "SELECT status, finished_at_ms, error_category FROM watcher_runs",
    ) == [("expired", _NOW_MS + 10, "claim_deadline")]
    assert _query(
        database_path,
        "SELECT incident_id, state_revision FROM incidents",
    ) == [(_INCIDENT_ID, 3)]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(3,)]


def test_observation_commit_advances_all_state_atomically(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))

    committed = store.commit_observations(
        claim.identity,
        _observation_commit(),
    )

    assert committed.committed_state_revision == 3
    assert store.list_reconcilable_runs() == (committed,)
    assert _query(
        database_path,
        "SELECT status, committed_state_revision FROM watcher_runs",
    ) == [("committed", 3)]
    assert _query(database_path, "SELECT sha256, size_bytes FROM blobs") == [
        (_SHA256, 2)
    ]
    assert _query(
        database_path,
        "SELECT observation_id, state_revision FROM observations",
    ) == [(_OBSERVATION_ID, 3)]
    assert _query(
        database_path,
        "SELECT blob_role, ordinal, sha256 FROM observation_blobs",
    ) == [("response", 0, _SHA256)]
    assert _query(
        database_path,
        "SELECT cursor_json, state_revision FROM source_cursors",
    ) == [('{"etag":"v1"}', 3)]
    assert _query(
        database_path, "SELECT change_id, state_revision FROM source_changes"
    ) == [(_CHANGE_ID, 3)]
    assert _query(
        database_path, "SELECT incident_id, state_revision FROM incidents"
    ) == [(_INCIDENT_ID, 3)]
    with pytest.raises(ClaimBlockedError):
        _ = store.acquire_claim(_manual_claim_request(_NOW_MS))


def test_duplicate_write_failure_rolls_back_entire_observation_commit(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    commit = _observation_commit()
    duplicate_change = replace(commit.changes[0], summary={"duplicate": True})
    failing_commit = replace(commit, changes=(commit.changes[0], duplicate_change))

    with pytest.raises(ControlStorageError, match="rolled back"):
        _ = store.commit_observations(
            claim.identity,
            failing_commit,
        )

    assert _query(database_path, "SELECT status FROM watcher_runs") == [("running",)]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(2,)]
    assert _query(database_path, "SELECT sha256 FROM blobs") == []
    assert _query(database_path, "SELECT observation_id FROM observations") == []
    assert _query(database_path, "SELECT cursor_json FROM source_cursors") == []


def test_post_deadline_commit_cannot_advance_cursor(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(
        _manual_claim_request(_NOW_MS, deadline_after_ms=10),
    )
    clock.set(_NOW_MS + 10)

    with pytest.raises(StaleClaimError):
        _ = store.commit_observations(
            claim.identity,
            _observation_commit(),
        )

    assert _query(database_path, "SELECT status FROM watcher_runs") == [("running",)]
    assert _query(database_path, "SELECT cursor_json FROM source_cursors") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(2,)]


def test_blob_receipt_mismatch_fails_before_transaction(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    commit = _observation_commit()
    bad_receipt = replace(commit.blobs[0], verified_sha256="b" * 64)

    with pytest.raises(BlobVerificationError, match="checksum verification"):
        _ = store.commit_observations(
            claim.identity,
            replace(commit, blobs=(bad_receipt,)),
        )
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(3,)]
    assert _query(database_path, "SELECT status FROM watcher_runs") == [("failed",)]
    assert _query(database_path, "SELECT incident_category FROM incidents") == [
        ("blob_verification_mismatch",)
    ]


def test_blob_receipt_mismatch_expires_a_past_deadline_claim(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(
        _manual_claim_request(_NOW_MS, deadline_after_ms=10),
    )
    commit = _observation_commit()
    bad_receipt = replace(commit.blobs[0], verified_size_bytes=3)
    clock.set(_NOW_MS + 10)

    with pytest.raises(BlobVerificationError, match="byte verification"):
        _ = store.commit_observations(
            claim.identity,
            replace(commit, blobs=(bad_receipt,)),
        )

    assert _query(database_path, "SELECT status FROM watcher_runs") == [("expired",)]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(3,)]


def test_stored_blob_size_conflict_atomically_fails_claim(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    first_claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    committed = store.commit_observations(
        first_claim.identity,
        _observation_commit(),
    )
    _ = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed),
    )
    _ = store.mark_successful(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
    )
    second_claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    commit = _observation_commit()
    conflicting_receipt = replace(
        commit.blobs[0],
        size_bytes=3,
        verified_size_bytes=3,
    )

    with pytest.raises(BlobVerificationError, match="stored blob byte count"):
        _ = store.commit_observations(
            second_claim.identity,
            replace(commit, blobs=(conflicting_receipt,)),
        )

    assert _query(
        database_path,
        "SELECT status FROM watcher_runs ORDER BY status",
    ) == [("failed",), ("successful",)]
    assert _query(
        database_path,
        """
        SELECT incident_category FROM incidents
        WHERE incident_category = 'blob_verification_mismatch'
        """,
    ) == [("blob_verification_mismatch",)]


def test_reconciliation_enforces_each_state_and_is_idempotent(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    committed = store.commit_observations(
        claim.identity,
        _observation_commit(),
    )

    with pytest.raises(IllegalRunTransitionError, match="skip"):
        _ = store.mark_successful(
            committed.run_id,
            committed_state_revision=committed.committed_state_revision,
        )
    pending = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed),
    )
    repeated_pending = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed),
    )
    successful = store.mark_successful(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
    )
    repeated_success = store.mark_successful(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
    )

    assert pending.state_revision == 4
    assert pending.changed is True
    assert repeated_pending.state_revision == 4
    assert repeated_pending.changed is False
    assert successful.state_revision == 5
    assert successful.changed is True
    assert repeated_success.state_revision == 5
    assert repeated_success.changed is False
    assert store.list_reconcilable_runs() == ()
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(5,)]


def test_recovery_receipt_must_match_current_database_revision(
    store: ControlStore,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    committed = store.commit_observations(claim.identity, _observation_commit())
    stale_receipt = _recovery_receipt(committed)
    _ = store.configure_source(
        SourceConfiguration(
            _SOURCE_ID,
            "rsk",
            {"timeout_seconds": 30.0},
            True,
        )
    )

    with pytest.raises(IllegalRunTransitionError, match="current state revision"):
        _ = store.mark_heartbeat_pending(
            committed.run_id,
            committed_state_revision=committed.committed_state_revision,
            recovery_receipt=stale_receipt,
        )

    pending = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed, state_revision=4),
    )
    assert pending.state_revision == 5


def test_two_writers_cannot_claim_the_same_source(
    database_path: Path,
    clock: _TestClock,
) -> None:
    _ = clock
    first = ControlStore.open(database_path)
    second = ControlStore.open(database_path)
    try:
        _configure_source(first)
        claim = first.acquire_claim(_manual_claim_request(_NOW_MS))

        with pytest.raises(ClaimBlockedError):
            _ = second.acquire_claim(
                _manual_claim_request(_NOW_MS),
            )
        assert claim.identity.fencing_epoch == 1
    finally:
        second.close()
        first.close()


def test_lock_contention_times_out_without_partial_write(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    contender = ControlStore.open(database_path, busy_timeout_ms=1)
    blocker = sqlite3.connect(database_path, isolation_level=None)
    try:
        _ = blocker.execute("BEGIN IMMEDIATE")

        with pytest.raises(ControlStorageError, match="writer lock"):
            _ = contender.set_schedule(_schedule())
    finally:
        blocker.rollback()
        blocker.close()
        contender.close()

    assert _query(database_path, "SELECT source_id FROM source_schedules") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]


def test_base_exception_rolls_back_transaction(
    store: ControlStore,
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audit_method_name = "_insert_" + "audit"
    original_insert_audit = cast(
        Callable[..., None], getattr(ControlStore, audit_method_name)
    )

    def interrupt_audit(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(ControlStore, audit_method_name, interrupt_audit)
    with pytest.raises(KeyboardInterrupt):
        _configure_source(store)
    monkeypatch.setattr(ControlStore, audit_method_name, original_insert_audit)

    assert _query(database_path, "SELECT source_id FROM sources") == []
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(0,)]
    _configure_source(store)
    assert _query(database_path, "SELECT source_id FROM sources") == [(_SOURCE_ID,)]


def test_commit_failure_rolls_back_and_keeps_writer_usable(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, connection = _open_faulting_store(database_path, monkeypatch)
    try:
        connection.fail_commit = True
        with pytest.raises(
            ControlStorageError, match="commit failed and was rolled back"
        ):
            _configure_source(store)
        connection.fail_commit = False

        assert _query(database_path, "SELECT source_id FROM sources") == []
        _configure_source(store)
        assert _query(database_path, "SELECT source_id FROM sources") == [(_SOURCE_ID,)]
    finally:
        store.close()


def test_rollback_failure_closes_writer(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, connection = _open_faulting_store(database_path, monkeypatch)
    connection.fail_commit = True
    connection.fail_rollback = True

    with pytest.raises(ControlStorageError, match="rollback failed; writer was closed"):
        _configure_source(store)
    with pytest.raises(ControlStorageError, match="closed"):
        _configure_source(store)


def test_post_commit_failure_closes_writer_with_uncertain_outcome(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, connection = _open_faulting_store(database_path, monkeypatch)
    connection.fail_after_commit = True

    with pytest.raises(ControlStorageError, match="outcome is uncertain"):
        _configure_source(store)
    with pytest.raises(ControlStorageError, match="closed"):
        _configure_source(store)

    replacement = ControlStore.open(database_path)
    try:
        repeated = replacement.configure_source(
            SourceConfiguration(_SOURCE_ID, "rsk", {}, True)
        )
        assert repeated.changed is False
        assert repeated.state_revision == 1
    finally:
        replacement.close()


def test_closed_store_and_reconciliation_limit_fail_explicitly(
    store: ControlStore,
) -> None:
    with pytest.raises(ControlValidationError, match="limit"):
        _ = store.list_reconcilable_runs(limit=1001)
    store.close()
    with pytest.raises(ControlStorageError, match="closed"):
        _ = store.list_reconcilable_runs()


def test_store_rejects_caller_owned_connections(tmp_path: Path) -> None:
    connection = sqlite3.connect(
        tmp_path / "caller-owned.sqlite3",
        isolation_level=None,
    )
    try:
        with pytest.raises(ControlStorageError, match="owned by open"):
            _ = ControlStore(connection)
    finally:
        connection.close()


def _configure_source(store: ControlStore) -> None:
    _ = store.configure_source(
        SourceConfiguration(
            _SOURCE_ID,
            "rsk",
            {},
            True,
        ),
    )


def _schedule() -> SourceSchedule:
    return SourceSchedule(
        source_id=_SOURCE_ID,
        cron_expression="0 3 * * *",
        misfire_grace_seconds=300,
        retry_max_attempts=3,
        retry_base_seconds=0.5,
        retry_max_seconds=30.0,
    )


def _manual_claim_request(
    now_ms: int,
    *,
    deadline_after_ms: int = 300_000,
) -> ClaimRequest:
    return ClaimRequest(
        source_id=_SOURCE_ID,
        run_mode="manual",
        scheduled_at_ms=now_ms,
        deadline_after_ms=deadline_after_ms,
    )


def _recovery_receipt(
    committed: CommittedRun,
    *,
    state_revision: int | None = None,
) -> RecoveryReceipt:
    run_id = committed.run_id
    committed_state_revision = committed.committed_state_revision
    image_state_revision = state_revision or committed_state_revision
    digest = "b" * 64
    return RecoveryReceipt(
        run_id=run_id,
        committed_state_revision=committed_state_revision,
        state_revision=image_state_revision,
        sha256=digest,
        size_bytes=4096,
        archive_key=(
            f"control-recovery/run/{image_state_revision:020d}/{digest}.sqlite3"
        ),
        verified_sha256=digest,
        verified_size_bytes=4096,
    )


def _observation_commit() -> ObservationCommit:
    receipt = BlobReceipt(
        sha256=_SHA256,
        size_bytes=2,
        media_type="application/json",
        archive_key=f"blobs/sha256/{_SHA256}",
        verified_sha256=_SHA256,
        verified_size_bytes=2,
    )
    observation = ObservationDraft(
        observation_id=_OBSERVATION_ID,
        observation_kind="catalog",
        source_identity="UZYNLG",
        response_sha256=_SHA256,
        observed_at_ms=_NOW_MS,
        metadata={"status": 200},
        blobs=(ObservationBlob("response", 0, _SHA256),),
    )
    return ObservationCommit(
        blobs=(receipt,),
        observations=(observation,),
        cursor={"etag": "v1"},
        changes=(
            ChangeDraft(_CHANGE_ID, _OBSERVATION_ID, "metadata", {"changed": True}),
        ),
        incidents=(IncidentDraft(_INCIDENT_ID, "source_drift", {"field": "schema"}),),
    )


def _query(
    database_path: Path,
    sql: str,
) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def _open_faulting_store(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ControlStore, _FaultingConnection]:
    original_connect = sqlite3.connect
    connection = original_connect(
        database_path,
        isolation_level=None,
        factory=_FaultingConnection,
    )

    def return_faulting_connection(
        *_args: object, **_kwargs: object
    ) -> sqlite3.Connection:
        return connection

    monkeypatch.setattr(sqlite3, "connect", return_faulting_connection)
    try:
        store = ControlStore.open(database_path)
    finally:
        monkeypatch.setattr(sqlite3, "connect", original_connect)
    return store, connection
