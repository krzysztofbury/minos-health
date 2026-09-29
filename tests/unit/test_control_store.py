import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path
from typing import ClassVar, cast, final, override

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

import minos.ingest.watchers.control.store as control_store_module
from minos.ingest.watchers.config import (
    RSK_SOURCE_CONFIGURATION_CODECS,
    RskSourceConfigurationCodec,
)
from minos.ingest.watchers.control import (
    BawDispatchDraft,
    BawDispatchRequest,
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
    JsonValue,
    MigrationError,
    ObservationBlob,
    ObservationCommit,
    ObservationDraft,
    RecoveryReceipt,
    RetryPolicy,
    RunClaim,
    ScheduledRunMode,
    SourceConfiguration,
    SourceConfigurationCodec,
    SourceConfigurationCodecError,
    SourceConfigurationCodecRegistry,
    SourceSchedule,
    StaleClaimError,
    UncertainControlOutcomeError,
)
from minos.ingest.watchers.control.migrations import (
    rollback_control_migrations_for_testing,
)

_NOW_MS = 1_800_000_000_000
_SOURCE_ID = "rsk"
_OBSERVATION_ID = "observation-0001"
_CHANGE_ID = "change-000000001"
_INCIDENT_ID = "incident-0000001"
_SHA256 = "a" * 64
_NEXT_DAILY_FIRE_MS = 1_800_068_400_000


class _FixtureConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    batch_size: int = Field(default=10, ge=1, le=100)
    claim_timeout_ms: int = Field(default=12_345, ge=1, le=3_600_000)


@final
class _FixtureConfigurationCodec:
    def canonicalize(
        self,
        configuration: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        try:
            validated = _FixtureConfiguration.model_validate(configuration)
        except ValidationError as error:
            raise SourceConfigurationCodecError(
                "fixture source configuration is invalid"
            ) from error
        return cast(dict[str, JsonValue], validated.model_dump(mode="json"))

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int:
        try:
            configuration = _FixtureConfiguration.model_validate_json(
                canonical_configuration_json
            )
        except ValidationError as error:
            raise SourceConfigurationCodecError(
                "stored fixture source configuration is invalid"
            ) from error
        return configuration.claim_timeout_ms


@final
class _HostileConfigurationCodec:
    def __init__(
        self,
        *,
        canonicalized: dict[str, JsonValue] | None = None,
        claim_timeout: object = 12_345,
        canonicalize_error: Exception | None = None,
        timeout_error: Exception | None = None,
        mutate_input: bool = False,
    ) -> None:
        self._canonicalized = canonicalized
        self._claim_timeout = claim_timeout
        self._canonicalize_error = canonicalize_error
        self._timeout_error = timeout_error
        self._mutate_input = mutate_input

    def canonicalize(
        self,
        configuration: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if self._canonicalize_error is not None:
            raise self._canonicalize_error
        if self._mutate_input:
            configuration["batch_size"] = 99
        if self._canonicalized is not None:
            return dict(self._canonicalized)
        return configuration

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int:
        _ = canonical_configuration_json
        if self._timeout_error is not None:
            raise self._timeout_error
        return cast(int, self._claim_timeout)


@final
class _OversizedCodecMapping(Mapping[str, SourceConfigurationCodec]):
    @override
    def __getitem__(self, _key: str) -> SourceConfigurationCodec:
        raise AssertionError("oversized registry must not be copied")

    @override
    def __iter__(self) -> Iterator[str]:
        raise AssertionError("oversized registry must not be copied")

    @override
    def __len__(self) -> int:
        return 65


@final
class _RepeatedKeyCodecMapping(Mapping[str, SourceConfigurationCodec]):
    def __init__(self) -> None:
        self.visits = 0
        self._codec = _FixtureConfigurationCodec()

    @override
    def __getitem__(self, key: str) -> SourceConfigurationCodec:
        assert key == "fixture"
        self.visits += 1
        return self._codec

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(("fixture",) * 65)

    @override
    def __len__(self) -> int:
        return 1


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
    control_store = ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
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


def test_idempotent_configuration_revalidates_corrupt_stored_payload(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    connection = sqlite3.connect(database_path)
    try:
        _ = connection.execute(
            """
            UPDATE source_configuration_revisions
            SET configuration_json = '{}'
            WHERE source_id = ? AND configuration_revision = 1
            """,
            (_SOURCE_ID,),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ControlStorageError, match="checksum differs"):
        _configure_source(store)
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]
    assert _query(database_path, "SELECT event_type FROM audit_events") == [
        ("source_configured",)
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

    with pytest.raises(ControlValidationError, match="invalid") as captured:
        _ = store.configure_source(
            SourceConfiguration(
                _SOURCE_ID,
                "rsk",
                {"file_max_bytes": str(512 * 1024 * 1024)},
                True,
            ),
        )
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    with pytest.raises(ControlValidationError, match="invalid"):
        _ = store.configure_source(
            SourceConfiguration(
                _SOURCE_ID,
                "rsk",
                {"file_max_bytes": 512 * 1024 * 1024 + 1},
                True,
            ),
        )


def test_second_watcher_codec_configures_loads_and_claims_without_store_changes(
    tmp_path: Path,
    clock: _TestClock,
) -> None:
    codecs = SourceConfigurationCodecRegistry(
        {
            "fixture": _FixtureConfigurationCodec(),
            "rsk": RskSourceConfigurationCodec(),
        }
    )
    with ControlStore.open(
        tmp_path / "second-watcher.sqlite3",
        source_configuration_codecs=codecs,
    ) as store:
        configured = store.configure_source(
            SourceConfiguration(
                "fixture-source",
                "fixture",
                {"batch_size": 7, "claim_timeout_ms": 12_345},
                True,
            )
        )
        loaded = store.load_source_state("fixture-source")
        claim = store.claim_manual("fixture-source")

    assert configured.configuration_revision == 1
    assert loaded.watcher_type == "fixture"
    assert loaded.configuration == {"batch_size": 7, "claim_timeout_ms": 12_345}
    assert claim.identity.deadline_at_ms == clock.now_ms + 12_345


def test_codec_cannot_inject_secret_like_configuration(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "secret-codec.sqlite3"
    secret_codec = _HostileConfigurationCodec(
        canonicalized={
            "batch_size": 7,
            "claim_timeout_ms": 12_345,
            "api_token": "private-value",
        }
    )
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": secret_codec}
        ),
    ) as store:
        with pytest.raises(ControlValidationError, match="configuration is invalid"):
            _ = store.configure_source(
                SourceConfiguration("fixture-source", "fixture", {}, True)
            )
        assert _query(database_path, "SELECT source_id FROM sources") == []

    _create_fixture_source(database_path)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": secret_codec}
        ),
    ) as store:
        with pytest.raises(ControlStorageError, match="configuration is invalid"):
            _ = store.load_source_state("fixture-source")


def test_codec_input_mutation_is_isolated_and_noncanonical_reads_fail(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mutating-codec.sqlite3"
    configuration: dict[str, JsonValue] = {
        "batch_size": 7,
        "claim_timeout_ms": 12_345,
    }
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": _HostileConfigurationCodec(mutate_input=True)}
        ),
    ) as store:
        _ = store.configure_source(
            SourceConfiguration("fixture-source", "fixture", configuration, True)
        )
    assert configuration == {"batch_size": 7, "claim_timeout_ms": 12_345}

    changed_codec = _HostileConfigurationCodec(
        canonicalized={"batch_size": 98, "claim_timeout_ms": 12_345}
    )
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": changed_codec}
        ),
    ) as store:
        with pytest.raises(ControlStorageError, match="not canonical"):
            _ = store.load_source_state("fixture-source")


@pytest.mark.parametrize("invalid_timeout", (0, True, 3_600_001))
def test_codec_claim_timeout_is_validated_on_write(
    tmp_path: Path,
    invalid_timeout: object,
) -> None:
    database_path = tmp_path / f"invalid-timeout-{invalid_timeout}.sqlite3"
    codec = _HostileConfigurationCodec(
        canonicalized={"batch_size": 7, "claim_timeout_ms": 12_345},
        claim_timeout=invalid_timeout,
    )
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": codec}
        ),
    ) as store:
        with pytest.raises(ControlValidationError, match="claim timeout is invalid"):
            _ = store.configure_source(
                SourceConfiguration("fixture-source", "fixture", {}, True)
            )
        assert _query(database_path, "SELECT source_id FROM sources") == []


def test_codec_claim_timeout_is_validated_on_read(tmp_path: Path) -> None:
    database_path = tmp_path / "invalid-read-timeout.sqlite3"
    _create_fixture_source(database_path)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {
                "fixture": _HostileConfigurationCodec(
                    canonicalized={"batch_size": 7, "claim_timeout_ms": 12_345},
                    claim_timeout=0,
                )
            }
        ),
    ) as store:
        with pytest.raises(ControlStorageError, match="claim timeout is invalid"):
            _ = store.load_source_state("fixture-source")


@pytest.mark.parametrize(
    "codec",
    (
        _HostileConfigurationCodec(
            canonicalize_error=RuntimeError("private canonicalization value")
        ),
        _HostileConfigurationCodec(
            canonicalized={"batch_size": 7, "claim_timeout_ms": 12_345},
            timeout_error=ValueError("private timeout value"),
        ),
    ),
)
def test_unexpected_codec_exceptions_are_redacted_at_store_boundary(
    tmp_path: Path,
    codec: _HostileConfigurationCodec,
) -> None:
    with ControlStore.open(
        tmp_path / f"codec-exception-{id(codec)}.sqlite3",
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": codec}
        ),
    ) as store:
        with pytest.raises(ControlValidationError) as captured:
            _ = store.configure_source(
                SourceConfiguration("fixture-source", "fixture", {}, True)
            )

    assert "private" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.parametrize(
    "codec",
    (
        _HostileConfigurationCodec(
            canonicalize_error=RuntimeError("private stored value")
        ),
        _HostileConfigurationCodec(
            canonicalized={"batch_size": 7, "claim_timeout_ms": 12_345},
            timeout_error=ValueError("private stored timeout"),
        ),
    ),
)
def test_unexpected_codec_read_exceptions_are_redacted(
    tmp_path: Path,
    codec: _HostileConfigurationCodec,
) -> None:
    database_path = tmp_path / f"codec-read-exception-{id(codec)}.sqlite3"
    _create_fixture_source(database_path)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": codec}
        ),
    ) as store:
        with pytest.raises(ControlStorageError) as captured:
            _ = store.load_source_state("fixture-source")

    assert "private" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


def test_unknown_and_missing_configuration_codecs_fail_closed(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "missing-codec.sqlite3"
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as configured_store:
        with pytest.raises(ControlValidationError, match="registered configuration"):
            _ = configured_store.configure_source(
                SourceConfiguration("unknown", "unknown", {}, True)
            )
        _configure_source(configured_store)

    with ControlStore.open(database_path) as unconfigured_store:
        with pytest.raises(ControlValidationError, match="registered configuration"):
            _ = unconfigured_store.configure_source(
                SourceConfiguration("rsk-2", "rsk", {}, True)
            )
        with pytest.raises(ControlStorageError, match="registered configuration"):
            _ = unconfigured_store.load_source_state(_SOURCE_ID)
        with pytest.raises(ControlStorageError, match="registered configuration"):
            _ = unconfigured_store.claim_manual(_SOURCE_ID)


def test_source_configuration_codec_registry_is_bounded() -> None:
    with pytest.raises(ValueError, match="exceeds its bound"):
        _ = SourceConfigurationCodecRegistry(_OversizedCodecMapping())


def test_source_configuration_codec_registry_bounds_repeated_item_visits() -> None:
    codecs = _RepeatedKeyCodecMapping()

    with pytest.raises(ValueError, match="exceeds its bound"):
        _ = SourceConfigurationCodecRegistry(codecs)

    assert codecs.visits == 65


def test_source_configuration_codec_registry_rejects_non_text_keys() -> None:
    invalid = cast(
        Mapping[str, SourceConfigurationCodec],
        cast(object, {1: _FixtureConfigurationCodec()}),
    )

    with pytest.raises(TypeError, match="watcher type must be text"):
        _ = SourceConfigurationCodecRegistry(invalid)


def test_source_reads_and_claims_reject_invalid_persisted_configuration(
    store: ControlStore,
    database_path: Path,
) -> None:
    _configure_source(store)
    invalid_json = '{"operation_deadline_seconds":"300"}'
    invalid_sha256 = hashlib.sha256(invalid_json.encode()).hexdigest()
    connection = sqlite3.connect(database_path)
    try:
        _ = connection.execute(
            """
            UPDATE source_configuration_revisions
            SET configuration_json = ?, configuration_sha256 = ?
            WHERE source_id = ? AND configuration_revision = 1
            """,
            (invalid_json, invalid_sha256, _SOURCE_ID),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(ControlStorageError, match="invalid"):
        _ = store.load_source_state(_SOURCE_ID)
    with pytest.raises(ControlStorageError, match="invalid"):
        _ = store.claim_manual(_SOURCE_ID)
    assert _query(database_path, "SELECT run_id FROM watcher_runs") == []
    assert _query(database_path, "SELECT fencing_epoch FROM sources") == [(0,)]
    assert _query(database_path, "SELECT state_revision FROM control_state") == [(1,)]


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

    with pytest.raises(ControlValidationError, match="invalid"):
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
    assert store.list_scheduled_sources()[0].retry_policy == RetryPolicy(
        max_attempts=3,
        base_seconds=0.5,
        max_seconds=30.0,
    )
    assert store.claim_manual(_SOURCE_ID).retry_policy == RetryPolicy(
        max_attempts=3,
        base_seconds=0.5,
        max_seconds=30.0,
    )


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


def test_new_claim_atomically_expires_its_stale_predecessor(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    first = store.acquire_claim(
        _manual_claim_request(_NOW_MS, deadline_after_ms=10),
    )
    clock.set(_NOW_MS + 10)

    second = store.claim_manual(_SOURCE_ID)

    assert second.identity.fencing_epoch == first.identity.fencing_epoch + 1
    assert second.state_revision == 4
    assert _query(
        database_path,
        "SELECT status, error_category FROM watcher_runs ORDER BY started_at_ms, run_id",
    ) == [("expired", "claim_deadline"), ("running", None)]
    assert _query(
        database_path,
        "SELECT incident_category FROM incidents",
    ) == [("claim_deadline",)]


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


def test_baw_dispatch_survives_retry_and_only_acknowledges_committed_evidence(
    database_path: Path, clock: _TestClock
) -> None:
    _ = clock
    url = (
        "https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
        "zarzadzenie-nr-1022026dsoz,7982.html"
    )
    codecs = SourceConfigurationCodecRegistry(
        {"nfz": _FixtureConfigurationCodec(), "baw": _FixtureConfigurationCodec()}
    )
    with ControlStore.open(database_path, source_configuration_codecs=codecs) as store:
        for source_id in ("nfz", "baw"):
            _ = store.configure_source(
                SourceConfiguration(source_id, source_id, {}, True)
            )
        source_commit = replace(
            _observation_commit(),
            observations=(
                replace(
                    _observation_commit().observations[0],
                    observation_kind="nfz_order_detail",
                    source_identity=url,
                ),
            ),
            changes=(),
            incidents=(),
            baw_dispatches=(BawDispatchDraft(44041, url, _OBSERVATION_ID),),
        )
        claim = store.claim_manual("nfz")
        committed = store.commit_observations(claim.identity, source_commit)
        assert store.list_pending_baw_dispatches() == (
            BawDispatchRequest("nfz", 44041, url, _SHA256, _NOW_MS),
        )
        assert _query(
            database_path,
            "SELECT discovery_run_id, observation_id, delivered_run_id FROM baw_dispatch_outbox",
        ) == [(committed.run_id, _OBSERVATION_ID, None)]

        _ = store.mark_heartbeat_pending(
            committed.run_id,
            committed_state_revision=committed.committed_state_revision,
            recovery_receipt=_recovery_receipt(committed),
        )
        _ = store.mark_successful(
            committed.run_id,
            committed_state_revision=committed.committed_state_revision,
        )
        repeated = replace(
            source_commit,
            observations=(
                replace(
                    source_commit.observations[0],
                    observation_id="observation-0000000002",
                ),
            ),
            baw_dispatches=(BawDispatchDraft(44041, url, "observation-0000000002"),),
        )
        second = store.claim_manual("nfz")
        with pytest.raises(ControlValidationError, match="batch exceeds its bound"):
            _ = store.commit_observations(
                second.identity,
                replace(repeated, baw_dispatches=repeated.baw_dispatches * 9),
            )
        assert _query(database_path, "SELECT count(*) FROM baw_dispatch_outbox") == [
            (1,)
        ]
        _ = store.commit_observations(second.identity, repeated)
        assert store.list_pending_baw_dispatches() == (
            BawDispatchRequest("nfz", 44041, url, _SHA256, _NOW_MS),
        )

        baw_claim = store.claim_manual("baw")
        delivery = replace(
            _observation_commit(),
            observations=(
                replace(
                    _observation_commit().observations[0],
                    observation_id="baw-observation-0001",
                    observation_kind="baw_document",
                    source_identity="44041",
                ),
            ),
            changes=(),
            incidents=(),
            delivered_baw_ids=(44041,),
            retained_baw_roots=(44041,),
        )
        with pytest.raises(ControlValidationError, match="lacks its document"):
            _ = store.commit_observations(
                baw_claim.identity,
                replace(
                    delivery,
                    observations=(
                        replace(delivery.observations[0], source_identity="99999"),
                    ),
                ),
            )
        assert store.list_pending_baw_dispatches()[0].document_id == 44041
        delivered = store.commit_observations(baw_claim.identity, delivery)
        assert store.list_pending_baw_dispatches()[0].document_id == 44041
        assert store.list_retained_baw_roots() == ()
        _ = store.mark_heartbeat_pending(
            delivered.run_id,
            committed_state_revision=delivered.committed_state_revision,
            recovery_receipt=_recovery_receipt(delivered),
        )
        assert store.list_pending_baw_dispatches()[0].document_id == 44041
        _ = store.mark_successful(
            delivered.run_id,
            committed_state_revision=delivered.committed_state_revision,
        )
        assert store.list_pending_baw_dispatches() == ()
        assert store.list_retained_baw_roots() == (44041,)
        assert _query(
            database_path,
            "SELECT delivered_run_id, delivered_observation_id FROM baw_dispatch_outbox",
        ) == [(delivered.run_id, "baw-observation-0001")]

    with sqlite3.connect(database_path, isolation_level=None) as connection:
        with pytest.raises(MigrationError, match="rollback failed and was rolled back"):
            _ = rollback_control_migrations_for_testing(connection, 3)
        assert connection.execute("PRAGMA user_version").fetchone() == (4,)
        assert connection.execute(
            "SELECT count(*) FROM baw_dispatch_outbox"
        ).fetchone() == (1,)


def test_committed_history_is_revision_fenced_and_truncates_complete_runs(
    store: ControlStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_source(store)
    first_claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    first = store.commit_observations(first_claim.identity, _observation_commit())
    _ = store.mark_heartbeat_pending(
        first.run_id,
        committed_state_revision=first.committed_state_revision,
        recovery_receipt=_recovery_receipt(first),
    )
    _ = store.mark_successful(
        first.run_id,
        committed_state_revision=first.committed_state_revision,
    )

    second_claim = store.acquire_claim(_manual_claim_request(_NOW_MS))
    digest = "c" * 64
    second_observation = ObservationDraft(
        "observation-0000000002",
        "catalog",
        "UZYNLG",
        digest,
        _NOW_MS,
        {"status": 200, "revision": 2},
        (ObservationBlob("response", 0, digest),),
    )
    second = store.commit_observations(
        second_claim.identity,
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
            (second_observation,),
            {"etag": "v2"},
        ),
    )

    fenced = store.load_committed_observation_history(
        _SOURCE_ID,
        "catalog",
        through_state_revision=first.committed_state_revision,
    )
    latest_only = store.load_committed_observation_history(
        _SOURCE_ID,
        "catalog",
        through_state_revision=second.committed_state_revision,
        maximum_revisions=1,
    )

    assert [item.state_revision for item in fenced.revisions] == [
        first.committed_state_revision
    ]
    assert fenced.older_revisions_exist is False
    assert [item.state_revision for item in latest_only.revisions] == [
        second.committed_state_revision
    ]
    assert latest_only.older_revisions_exist is True

    monkeypatch.setattr(control_store_module, "_MAX_PUBLIC_HISTORY_INPUT_ROWS", 1)
    work_bounded = store.load_committed_observation_history(
        _SOURCE_ID,
        "catalog",
        through_state_revision=second.committed_state_revision,
    )
    assert [item.state_revision for item in work_bounded.revisions] == [
        second.committed_state_revision
    ]
    assert work_bounded.older_revisions_exist is True

    monkeypatch.setattr(
        control_store_module,
        "_MAX_PUBLIC_HISTORY_INPUT_ROWS",
        16_384,
    )
    monkeypatch.setattr(control_store_module, "_MAX_PUBLIC_HISTORY_INPUT_BYTES", 1)
    with pytest.raises(ControlStorageError, match="input budget"):
        _ = store.load_committed_observation_history(
            _SOURCE_ID,
            "catalog",
            through_state_revision=second.committed_state_revision,
        )


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
    assert store.list_reconcilable_runs() == (
        replace(committed, status="heartbeat_pending"),
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
    first = ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    second = ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
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
    contender = ControlStore.open(
        database_path,
        busy_timeout_ms=1,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
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

    with pytest.raises(
        UncertainControlOutcomeError,
        match="rollback failed; writer was closed",
    ):
        _configure_source(store)
    with pytest.raises(ControlStorageError, match="closed"):
        _configure_source(store)


def test_post_commit_failure_closes_writer_with_uncertain_outcome(
    database_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, connection = _open_faulting_store(database_path, monkeypatch)
    connection.fail_after_commit = True

    with pytest.raises(UncertainControlOutcomeError, match="outcome is uncertain"):
        _configure_source(store)
    with pytest.raises(ControlStorageError, match="closed"):
        _configure_source(store)

    replacement = ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
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


def test_public_listings_fail_closed_instead_of_truncating(
    store: ControlStore,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    for source_id in ("rsk-a", "rsk-b"):
        _ = store.configure_source(SourceConfiguration(source_id, "rsk", {}, True))
        _ = store.set_schedule(replace(_schedule(), source_id=source_id))

    with pytest.raises(ControlStorageError, match="listing exceeds"):
        _ = store.list_scheduled_sources(limit=1)
    with pytest.raises(ControlStorageError, match="listing exceeds"):
        store.validate_scheduled_source_configurations(limit=1)
    with pytest.raises(ControlValidationError, match="limit"):
        _ = store.list_scheduled_sources(limit=True)


def test_stale_and_reconcilable_listings_fail_closed(
    store: ControlStore,
    clock: _TestClock,
) -> None:
    for index, source_id in enumerate(("rsk-a", "rsk-b"), start=1):
        _ = store.configure_source(SourceConfiguration(source_id, "rsk", {}, True))
        claim = store.acquire_claim(
            ClaimRequest(source_id, "manual", _NOW_MS, deadline_after_ms=10)
        )
        commit = _observation_commit()
        observation_id = f"observation-source-{index:04d}"
        observation = replace(
            commit.observations[0],
            observation_id=observation_id,
        )
        change = replace(
            commit.changes[0],
            change_id=f"change-source-{index:08d}",
            observation_id=observation_id,
        )
        incident = replace(
            commit.incidents[0],
            incident_id=f"incident-source-{index:08d}",
        )
        _ = store.commit_observations(
            claim.identity,
            replace(
                commit,
                observations=(observation,),
                changes=(change,),
                incidents=(incident,),
            ),
        )

    with pytest.raises(ControlStorageError, match="listing exceeds"):
        _ = store.list_reconcilable_runs(limit=1)

    for source_id in ("rsk-c", "rsk-d"):
        _ = store.configure_source(SourceConfiguration(source_id, "rsk", {}, True))
        _ = store.acquire_claim(
            ClaimRequest(source_id, "manual", _NOW_MS, deadline_after_ms=10)
        )
    clock.set(_NOW_MS + 10)

    with pytest.raises(ControlStorageError, match="listing exceeds"):
        _ = store.list_stale_claims(limit=1)


def test_claim_loading_uses_pinned_configuration_and_exact_cursor(
    store: ControlStore,
) -> None:
    _configure_source(store)
    first = store.acquire_claim(_manual_claim_request(_NOW_MS))
    committed = store.commit_observations(first.identity, _observation_commit())
    _ = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed),
    )
    _ = store.mark_successful(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
    )
    second = store.acquire_claim(_manual_claim_request(_NOW_MS))
    _ = store.configure_source(
        SourceConfiguration(_SOURCE_ID, "rsk", {"timeout_seconds": 30.0}, True)
    )

    state = store.load_claim_state(second.identity)

    assert state.configuration_revision == 1
    assert state.configuration["timeout_seconds"] == 20.0
    assert state.cursor == {"etag": "v1"}
    assert state.cursor_state_revision == committed.committed_state_revision


def test_stale_claim_listing_and_generic_abort_are_atomic(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    _configure_source(store)
    claim = store.acquire_claim(_manual_claim_request(_NOW_MS, deadline_after_ms=10))
    clock.set(_NOW_MS + 10)

    assert store.list_stale_claims(limit=1)[0].identity == claim.identity
    transition = store.abort_claim(
        claim.identity,
        error_category="archive_failure",
        incident=IncidentDraft(
            "incident-archive-failure",
            "archive_failure",
            {"phase": "archive"},
        ),
    )

    assert transition.status == "expired"
    assert _query(database_path, "SELECT cursor_json FROM source_cursors") == []
    assert _query(database_path, "SELECT incident_category FROM incidents") == [
        ("archive_failure",)
    ]


def test_atomic_overdue_and_named_due_claims(
    store: ControlStore,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    for source_id in ("rsk-a", "rsk-b"):
        _ = store.configure_source(SourceConfiguration(source_id, "rsk", {}, True))
        _ = store.set_schedule(replace(_schedule(), source_id=source_id))
    clock.set(_NOW_MS)

    catch_up = store.claim_one_overdue()

    assert catch_up is not None
    assert catch_up.identity.source_id == "rsk-a"
    assert catch_up.run_mode == "catch_up"
    assert store.claim_named_due("rsk-a") is None
    named = store.claim_named_due("rsk-b")
    assert named is not None
    assert named.run_mode == "scheduled"


def test_co_due_weekly_claim_has_priority_and_preserves_daily_obligation(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    _configure_source(store)
    daily = store.set_schedule(_schedule())
    weekly = store.set_schedule(replace(_schedule(), run_mode="weekly_verification"))
    assert daily.next_due_at_ms == weekly.next_due_at_ms
    clock.set(_NOW_MS)

    verification = store.claim_one_overdue()

    assert verification is not None
    assert verification.run_mode == "weekly_verification"
    _ = store.fail_claim(verification.identity, error_category="exhausted")
    daily_catch_up = store.claim_one_overdue()
    assert daily_catch_up is not None
    assert daily_catch_up.run_mode == "catch_up"
    assert daily_catch_up.scheduled_at_ms == daily.next_due_at_ms
    cursors = _query(
        database_path,
        """
        SELECT run_mode, next_due_at_ms
        FROM source_schedules
        ORDER BY run_mode
        """,
    )
    assert cursors == [
        ("scheduled", _NEXT_DAILY_FIRE_MS),
        ("weekly_verification", _NEXT_DAILY_FIRE_MS),
    ]


def test_restart_claims_overdue_weekly_with_forced_mode(
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as first:
        _configure_source(first)
        _ = first.set_schedule(replace(_schedule(), run_mode="weekly_verification"))
    clock.set(_NOW_MS)

    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as restarted:
        claim = restarted.claim_one_overdue()

    assert claim is not None
    assert claim.run_mode == "weekly_verification"


def test_concurrent_due_claimers_create_one_fenced_claim(
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as setup:
        _configure_source(setup)
        _ = setup.set_schedule(_schedule())
    clock.set(_NOW_MS)
    barrier = threading.Barrier(2)
    claims: list[RunClaim | None] = []
    errors: list[BaseException] = []

    def claim() -> None:
        try:
            with ControlStore.open(
                database_path,
                source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
            ) as contender:
                _ = barrier.wait(timeout=5)
                claims.append(contender.claim_named_due(_SOURCE_ID))
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=claim) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert len([claim for claim in claims if claim is not None]) == 1
    assert _query(database_path, "SELECT COUNT(*) FROM watcher_runs") == [(1,)]
    assert _query(database_path, "SELECT fencing_epoch FROM sources") == [(1,)]


@pytest.mark.parametrize(
    ("scheduled_mode", "expected_run_mode"),
    (("scheduled", "catch_up"), ("weekly_verification", "weekly_verification")),
)
def test_failed_due_obligation_retries_after_persisted_backoff_and_restart(
    database_path: Path,
    clock: _TestClock,
    scheduled_mode: ScheduledRunMode,
    expected_run_mode: str,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as first:
        _configure_source(first)
        schedule = first.set_schedule(replace(_schedule(), run_mode=scheduled_mode))
    clock.set(_NOW_MS)

    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as first:
        claim = first.claim_one_overdue()
        assert claim is not None
        assert claim.run_mode == expected_run_mode
        _ = first.fail_claim(claim.identity, error_category="exhausted")
        assert first.claim_one_overdue() is None

    clock.set(_NOW_MS + 499)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as before_backoff:
        assert before_backoff.claim_one_overdue() is None

    clock.set(_NOW_MS + 500)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as restarted:
        retry = restarted.claim_one_overdue()

    assert retry is not None
    assert retry.run_mode == expected_run_mode
    assert retry.scheduled_at_ms == schedule.next_due_at_ms


def test_concurrent_retry_claimers_create_one_fenced_claim(
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as setup:
        _configure_source(setup)
        _ = setup.set_schedule(_schedule())
    clock.set(_NOW_MS)
    with ControlStore.open(
        database_path,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as setup:
        claim = setup.claim_one_overdue()
        assert claim is not None
        _ = setup.fail_claim(claim.identity, error_category="exhausted")
    clock.set(_NOW_MS + 500)
    barrier = threading.Barrier(2)
    claims: list[RunClaim | None] = []
    errors: list[BaseException] = []

    def claim_retry() -> None:
        try:
            with ControlStore.open(
                database_path,
                source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
            ) as contender:
                _ = barrier.wait(timeout=5)
                claims.append(contender.claim_one_overdue())
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=claim_retry) for _index in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert len([claim for claim in claims if claim is not None]) == 1
    assert _query(database_path, "SELECT COUNT(*) FROM watcher_runs") == [(2,)]


def test_successful_due_run_completes_pending_obligation(
    store: ControlStore,
    database_path: Path,
    clock: _TestClock,
) -> None:
    clock.set(_NOW_MS - 86_400_000)
    _configure_source(store)
    _ = store.set_schedule(_schedule())
    clock.set(_NOW_MS)
    claim = store.claim_one_overdue()
    assert claim is not None
    committed = store.commit_observations(claim.identity, _observation_commit())
    _ = store.mark_heartbeat_pending(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
        recovery_receipt=_recovery_receipt(committed),
    )

    _ = store.mark_successful(
        committed.run_id,
        committed_state_revision=committed.committed_state_revision,
    )

    assert _query(
        database_path,
        """
        SELECT pending_scheduled_at_ms, pending_run_mode,
               retry_not_before_ms, retry_failure_count
        FROM source_schedules
        """,
    ) == [(None, None, None, 0)]
    assert store.claim_one_overdue() is None


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


def _create_fixture_source(database_path: Path) -> None:
    with ControlStore.open(
        database_path,
        source_configuration_codecs=SourceConfigurationCodecRegistry(
            {"fixture": _FixtureConfigurationCodec()}
        ),
    ) as store:
        _ = store.configure_source(
            SourceConfiguration(
                "fixture-source",
                "fixture",
                {"batch_size": 7, "claim_timeout_ms": 12_345},
                True,
            )
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
        version_id="recovery-version-1",
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
        store = ControlStore.open(
            database_path,
            source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
        )
    finally:
        monkeypatch.setattr(sqlite3, "connect", original_connect)
    return store, connection
