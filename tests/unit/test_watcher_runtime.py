import base64
import hashlib
import json
import re
import sqlite3
import threading
import time
from collections.abc import Callable, Generator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast, final

import httpx
import pytest

from minos.cli.watcher import WatcherCliAdapter, WatcherPaths
from minos.ingest.watchers.archive import (
    BlobArchiveVerificationError,
    BlobStagingArea,
    FilesystemBlobArchive,
    StagedBlob,
)
from minos.ingest.watchers.control import (
    BlobReceipt,
    ClaimIdentity,
    CommittedRun,
    ControlStore,
    ObservationCommit,
    ObservationDraft,
    RecoveryReceipt,
    RetryPolicy,
    RunTransition,
    ScheduledSource,
    SourceConfiguration,
    UncertainControlOutcomeError,
)
from minos.ingest.watchers.healthchecks import (
    HealthcheckError,
    HealthchecksClient,
    HealthchecksFileSettings,
    HealthcheckTargets,
)
from minos.ingest.watchers.orchestrator import RunRecoveryPublisher, WatcherOrchestrator
from minos.ingest.watchers.preflight import (
    CapacityLimits,
    ControlDatabaseCopyProbe,
    DiskUsage,
    ImageDigestProbe,
    NamedPreflightProbe,
    PausedPreflight,
    PreflightError,
    ReadableSecretsProbe,
    RuntimeCapacityGate,
    S3ChecksumProbe,
    copy_control_database,
)
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    CollectorRegistry,
    RetryableWatcherError,
)
from minos.ingest.watchers.recovery import (
    ListedRecoveryImage,
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryImageStagingArea,
    StagedRecoveryImage,
    recovery_key,
)
from minos.ingest.watchers.scheduler import (
    SchedulerBackend,
    SchedulerError,
    WatcherScheduler,
)
from minos.ingest.watchers.service import (
    ProcessLockError,
    WatcherProcessLock,
    WatcherReadiness,
    WatcherReadinessError,
    WatcherService,
)


@final
class _Collector:
    def __init__(self, staging: BlobStagingArea, events: list[str]) -> None:
        self._staging = staging
        self._events = events

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        self._events.append("collect")
        with self._staging.stage() as writer:
            assert writer.write(b"{}") == 2
            blob = writer.seal(media_type="application/json")
            _ = context.archive.put(blob)
            observation = ObservationDraft(
                "runtime-observation-0001",
                "catalog",
                "opaque-source",
                blob.sha256,
                context.run.started_at_ms,
                {},
            )
            yield CollectedRun((observation,), {"cursor": "next"})


@final
class _MultiBlobCollector:
    def __init__(self, staging: BlobStagingArea, events: list[str]) -> None:
        self._staging = staging
        self._events = events

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        self._events.append("collect")
        observations: list[ObservationDraft] = []
        for index in range(3):
            content = f'{{"index":{index}}}'.encode()
            with self._staging.stage() as writer:
                assert writer.write(content) == len(content)
                blob = writer.seal(media_type="application/json")
                _ = context.archive.put(blob)
            observations.append(
                ObservationDraft(
                    f"runtime-observation-{index:04d}",
                    "metadata",
                    f"opaque-source-{index}",
                    blob.sha256,
                    context.run.started_at_ms,
                    {"index": index},
                )
            )
        yield CollectedRun(tuple(observations), {"cursor": "multi"})


@final
class _RetryCollector:
    def __init__(self, delegate: _Collector, failures: int) -> None:
        self._delegate = delegate
        self._failures = failures
        self.attempts = 0

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        self.attempts += 1
        if self.attempts <= self._failures:
            raise RetryableWatcherError("retryable test failure")
        with self._delegate.collect(context) as collected:
            yield collected


@final
class _RetryAfterArchiveCollector:
    def __init__(self, staging: BlobStagingArea) -> None:
        self._staging = staging
        self.attempts = 0

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        self.attempts += 1
        with self._staging.stage() as writer:
            assert writer.write(b"retry") == 5
            blob = writer.seal(media_type="application/octet-stream")
            _ = context.archive.put(blob)
        if self.attempts == 1:
            raise RetryableWatcherError("retry after archive")
        observation = ObservationDraft(
            "runtime-retry-observation",
            "metadata",
            "opaque-retry-source",
            blob.sha256,
            context.run.started_at_ms,
            {},
        )
        yield CollectedRun((observation,), {"cursor": "retried"})


@final
class _TeardownFailureCollector:
    def __init__(self, delegate: _Collector) -> None:
        self._delegate = delegate

    @contextmanager
    def collect(self, context: CollectionContext) -> Generator[CollectedRun]:
        with self._delegate.collect(context) as collected:
            yield collected
        raise RuntimeError("injected collector teardown failure")


@final
class _InterruptContext:
    def __enter__(self) -> CollectedRun:
        raise KeyboardInterrupt

    def __exit__(self, *_error: object) -> None:
        pass


@final
class _InterruptCollector:
    def collect(
        self,
        context: CollectionContext,
    ) -> AbstractContextManager[CollectedRun]:
        _ = context
        return _InterruptContext()


@final
class _Archive:
    def __init__(
        self,
        events: list[str],
        *,
        fail: bool = False,
        fail_on_call: int | None = None,
    ) -> None:
        self._events = events
        self._fail = fail
        self._fail_on_call = fail_on_call
        self._calls = 0

    def put(self, blob: StagedBlob) -> BlobReceipt:
        self._calls += 1
        self._events.append("archive")
        if self._fail or self._calls == self._fail_on_call:
            raise BlobArchiveVerificationError("injected archive failure")
        return _receipt(blob)


@final
class _MalformedReceiptArchive:
    def __init__(self, fault: str) -> None:
        self._fault = fault

    def put(self, blob: StagedBlob) -> BlobReceipt:
        receipt = _receipt(blob)
        if self._fault == "sha256":
            return replace(receipt, sha256="a" * 64)
        if self._fault == "size_bytes":
            return replace(receipt, size_bytes=cast(int, float(receipt.size_bytes)))
        if self._fault == "media_type":
            return replace(receipt, media_type=cast(str, cast(object, 42)))
        if self._fault == "archive_key":
            return replace(receipt, archive_key="blobs/sha256/incorrect")
        if self._fault == "verified_sha256":
            return replace(receipt, verified_sha256="b" * 64)
        if self._fault == "verified_size_bytes":
            return replace(
                receipt,
                verified_size_bytes=cast(int, float(receipt.verified_size_bytes)),
            )
        raise AssertionError("unsupported receipt fault")


def _receipt(blob: StagedBlob) -> BlobReceipt:
    return BlobReceipt(
        blob.sha256,
        blob.size_bytes,
        blob.media_type,
        f"blobs/sha256/{blob.sha256}",
        blob.sha256,
        blob.size_bytes,
    )


@final
class _RecoveryPublisher:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def publish_run(self, store: ControlStore, run: CommittedRun) -> RecoveryReceipt:
        _ = store
        self._events.append("recovery")
        digest = "b" * 64
        return RecoveryReceipt(
            run.run_id,
            run.committed_state_revision,
            run.committed_state_revision,
            digest,
            4096,
            (
                "control-recovery/run/"
                f"{run.committed_state_revision:020d}/{digest}.sqlite3"
            ),
            "runtime-version-1",
            digest,
            4096,
        )

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        _ = store
        self._events.append(f"recovery_{recovery_class}")
        return cast(PublishedRecoveryImage, object())

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        _ = store
        self._events.append(f"ensure_recovery_{recovery_class}")
        return None


@final
class _PeriodicRecoveryStore:
    def __init__(self) -> None:
        self.published: list[PublishedRecoveryImage] = []

    def publish(self, image: StagedRecoveryImage) -> PublishedRecoveryImage:
        published = PublishedRecoveryImage(
            recovery_key(image.metadata, image.sha256),
            f"version-{len(self.published) + 1}",
            image.sha256,
            image.size_bytes,
            image.metadata,
        )
        self.published.append(published)
        return published

    def list_images(
        self,
        recovery_class: RecoveryClass,
        *,
        maximum_objects: int,
        page_size: int = 1000,
    ) -> tuple[ListedRecoveryImage, ...]:
        assert maximum_objects == 1024
        assert page_size == 1000
        return tuple(
            ListedRecoveryImage(item.key, item.size_bytes, item.metadata.created_at_ms)
            for item in self.published
            if item.metadata.recovery_class == recovery_class
        )


@final
class _Heartbeat:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def start(self, source_id: str) -> None:
        _ = source_id
        self._events.append("heartbeat_start")

    def success(self, source_id: str) -> None:
        _ = source_id
        self._events.append("heartbeat_success")

    def failure(self, source_id: str) -> None:
        _ = source_id
        self._events.append("heartbeat_failure")


def test_orchestrator_orders_archive_commit_recovery_and_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    store = ControlStore.open(tmp_path / "control.sqlite3")
    original_commit = ControlStore.commit_observations
    original_pending = ControlStore.mark_heartbeat_pending
    original_successful = ControlStore.mark_successful

    def commit(
        self: ControlStore,
        claim: ClaimIdentity,
        command: ObservationCommit,
    ) -> CommittedRun:
        events.append("commit")
        return original_commit(self, claim, command)

    def pending(
        self: ControlStore,
        run_id: str,
        *,
        committed_state_revision: int,
        recovery_receipt: RecoveryReceipt,
    ) -> RunTransition:
        events.append("heartbeat_pending")
        return original_pending(
            self,
            run_id,
            committed_state_revision=committed_state_revision,
            recovery_receipt=recovery_receipt,
        )

    def successful(
        self: ControlStore,
        run_id: str,
        *,
        committed_state_revision: int,
    ) -> RunTransition:
        events.append("final_success")
        return original_successful(
            self,
            run_id,
            committed_state_revision=committed_state_revision,
        )

    monkeypatch.setattr(ControlStore, "commit_observations", commit)
    monkeypatch.setattr(ControlStore, "mark_heartbeat_pending", pending)
    monkeypatch.setattr(ControlStore, "mark_successful", successful)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk", _Collector(BlobStagingArea(tmp_path / "stage"), events)
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )
        claim = store.claim_manual("rsk")

        _ = orchestrator.run_claim(claim, RetryPolicy(1, 1.0, 1.0))

        assert events == [
            "heartbeat_start",
            "collect",
            "archive",
            "commit",
            "recovery",
            "heartbeat_pending",
            "heartbeat_success",
            "final_success",
        ]
    finally:
        store.close()


def test_orchestrator_archives_multiple_blobs_with_one_staging_file(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database = tmp_path / "control.sqlite3"
    staging_path = tmp_path / "stage"
    archive_path = tmp_path / "archive"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            _MultiBlobCollector(BlobStagingArea(staging_path), events),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(archive_path),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        completed = orchestrator.run_claim(
            store.claim_manual("rsk"),
            RetryPolicy(1, 1.0, 1.0),
        )

        assert completed.committed_state_revision == 3
        assert events[:2] == ["heartbeat_start", "collect"]
        assert list(staging_path.iterdir()) == []
        assert len(list((archive_path / "blobs" / "sha256").iterdir())) == 3
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(3,)]
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(3,)]
        assert store.load_source_state("rsk").cursor == {"cursor": "multi"}
    finally:
        store.close()


def test_later_multi_blob_archive_failure_leaves_no_control_commit(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database = tmp_path / "control.sqlite3"
    staging_path = tmp_path / "stage"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            _MultiBlobCollector(BlobStagingArea(staging_path), events),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events, fail_on_call=2),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        with pytest.raises(BlobArchiveVerificationError):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(1, 1.0, 1.0),
            )

        assert list(staging_path.iterdir()) == []
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(0,)]
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(0,)]
        assert store.load_source_state("rsk").cursor == {}
        assert _query(database, "SELECT status FROM watcher_runs") == [("failed",)]
        assert _query(database, "SELECT incident_category FROM incidents") == [
            ("archive_failure",)
        ]
        incident_details = _query(database, "SELECT details_json FROM incidents")
        assert json.loads(cast(str, incident_details[0][0])) == {"phase": "archive"}
    finally:
        store.close()


@pytest.mark.parametrize(
    "fault",
    (
        "sha256",
        "size_bytes",
        "media_type",
        "archive_key",
        "verified_sha256",
        "verified_size_bytes",
    ),
)
def test_malformed_archive_receipt_is_rejected_before_commit(
    tmp_path: Path,
    fault: str,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            _Collector(BlobStagingArea(tmp_path / "stage"), []),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_MalformedReceiptArchive(fault),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher([]),
            heartbeat=_Heartbeat([]),
        )

        with pytest.raises(BlobArchiveVerificationError):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(1, 1.0, 1.0),
            )

        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(0,)]
        assert _query(database, "SELECT status FROM watcher_runs") == [("failed",)]
        assert _query(
            database,
            "SELECT incident_category, details_json FROM incidents",
        ) == [("archive_failure", '{"phase":"archive"}')]
    finally:
        store.close()


def test_collector_teardown_failure_prevents_control_commit(tmp_path: Path) -> None:
    events: list[str] = []
    database = tmp_path / "control.sqlite3"
    staging_path = tmp_path / "stage"
    archive_path = tmp_path / "archive"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            _TeardownFailureCollector(
                _Collector(BlobStagingArea(staging_path), events)
            ),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(archive_path),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        with pytest.raises(RuntimeError, match="teardown failure"):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(1, 1.0, 1.0),
            )

        assert list(staging_path.iterdir()) == []
        assert len(list((archive_path / "blobs" / "sha256").iterdir())) == 1
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(0,)]
        assert store.load_source_state("rsk").cursor == {}
        assert _query(
            database,
            "SELECT incident_category, details_json FROM incidents",
        ) == [("collection_failure", '{"phase":"collect"}')]
    finally:
        store.close()


def test_periodic_recovery_startup_publishes_only_missing_current_period(
    tmp_path: Path,
) -> None:
    store = ControlStore.open(tmp_path / "control.sqlite3")
    remote = _PeriodicRecoveryStore()
    publisher = RunRecoveryPublisher(
        RecoveryImageStagingArea(tmp_path / "recovery"),
        remote,
    )
    try:
        first = publisher.ensure_periodic(store, "daily")
        second = publisher.ensure_periodic(store, "daily")
    finally:
        store.close()

    assert first is not None
    assert second is None
    assert len(remote.published) == 1


def test_archive_failure_records_incident_without_advancing_cursor(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    database_path = tmp_path / "control.sqlite3"
    store = ControlStore.open(database_path)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk", _Collector(BlobStagingArea(tmp_path / "stage"), events)
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events, fail=True),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        with pytest.raises(BlobArchiveVerificationError):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(3, 0.01, 0.01),
            )

        assert store.load_source_state("rsk").cursor == {}
        assert _query(database_path, "SELECT status FROM watcher_runs") == [("failed",)]
        assert _query(database_path, "SELECT incident_category FROM incidents") == [
            ("archive_failure",)
        ]
        assert events[-1] == "heartbeat_failure"
    finally:
        store.close()


def test_retry_policy_retries_only_explicit_precommit_failure(tmp_path: Path) -> None:
    events: list[str] = []
    sleeps: list[float] = []
    store = ControlStore.open(tmp_path / "control.sqlite3")
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        collector = _RetryCollector(
            _Collector(BlobStagingArea(tmp_path / "stage"), events),
            failures=2,
        )
        registry = CollectorRegistry()
        registry.register("rsk", collector)
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
            sleep=sleeps.append,
        )

        _ = orchestrator.run_claim(
            store.claim_manual("rsk"),
            RetryPolicy(3, 0.5, 0.75),
        )

        assert collector.attempts == 3
        assert sleeps == [0.5, 0.75]
    finally:
        store.close()


def test_retry_after_archive_uses_a_fresh_receipt_set(tmp_path: Path) -> None:
    events: list[str] = []
    sleeps: list[float] = []
    database = tmp_path / "control.sqlite3"
    staging_path = tmp_path / "stage"
    archive_path = tmp_path / "archive"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        collector = _RetryAfterArchiveCollector(BlobStagingArea(staging_path))
        registry = CollectorRegistry()
        registry.register("rsk", collector)
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(archive_path),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
            sleep=sleeps.append,
        )

        _ = orchestrator.run_claim(
            store.claim_manual("rsk"),
            RetryPolicy(2, 0.01, 0.01),
        )

        assert collector.attempts == 2
        assert sleeps == [0.01]
        assert list(staging_path.iterdir()) == []
        assert len(list((archive_path / "blobs" / "sha256").iterdir())) == 1
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(1,)]
        assert store.load_source_state("rsk").cursor == {"cursor": "retried"}
        assert _query(database, "SELECT status FROM watcher_runs") == [("successful",)]
    finally:
        store.close()


def test_retry_policy_does_not_sleep_through_claim_deadline(tmp_path: Path) -> None:
    events: list[str] = []
    sleeps: list[float] = []
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        collector = _RetryCollector(
            _Collector(BlobStagingArea(tmp_path / "stage"), events),
            failures=1,
        )
        registry = CollectorRegistry()
        registry.register("rsk", collector)
        claim = store.claim_manual("rsk")
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
            sleep=sleeps.append,
            now_ms=lambda: claim.identity.deadline_at_ms - 250,
        )

        with pytest.raises(
            RuntimeError,
            match="claim deadline has insufficient retry budget",
        ):
            _ = orchestrator.run_claim(claim, RetryPolicy(3, 0.5, 0.75))

        assert collector.attempts == 1
        assert sleeps == []
        assert _query(database, "SELECT status FROM watcher_runs") == [("failed",)]
        assert _query(database, "SELECT incident_category FROM incidents") == [
            ("claim_deadline",)
        ]
    finally:
        store.close()


def test_cancellation_aborts_claim_and_sends_failure_heartbeat(tmp_path: Path) -> None:
    events: list[str] = []
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register("rsk", _InterruptCollector())
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        with pytest.raises(KeyboardInterrupt):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(1, 1.0, 1.0),
            )

        assert _query(database, "SELECT status FROM watcher_runs") == [("failed",)]
        assert events[-1] == "heartbeat_failure"
    finally:
        store.close()


def test_uncertain_commit_is_not_followed_by_abort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(database)
    original_commit = ControlStore.commit_observations

    def uncertain_commit(
        self: ControlStore,
        claim: ClaimIdentity,
        command: ObservationCommit,
    ) -> CommittedRun:
        _ = original_commit(self, claim, command)
        raise UncertainControlOutcomeError("injected uncertainty")

    monkeypatch.setattr(ControlStore, "commit_observations", uncertain_commit)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk", _Collector(BlobStagingArea(tmp_path / "stage"), events)
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=_Archive(events),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(events),
            heartbeat=_Heartbeat(events),
        )

        with pytest.raises(UncertainControlOutcomeError):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(3, 0.01, 0.01),
            )

        assert _query(database, "SELECT status FROM watcher_runs") == [("committed",)]
        assert _query(database, "SELECT incident_category FROM incidents") == []
        assert events[-1] == "heartbeat_failure"
    finally:
        store.close()


@final
class _FailingCapacityGate:
    def require_capacity(self) -> None:
        raise PreflightError("injected capacity failure")


def test_capacity_failure_is_incidented_before_manual_claim(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(database)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register("rsk", _Collector(BlobStagingArea(tmp_path / "stage"), []))
        service = WatcherService(
            store=store,
            orchestrator=cast(WatcherOrchestrator, object()),
            collectors=registry,
            capacity_gate=_FailingCapacityGate(),
        )

        with pytest.raises(PreflightError, match="capacity"):
            _ = service.run_manual("rsk")
        with pytest.raises(PreflightError, match="capacity"):
            _ = service.run_manual("rsk")

        assert _query(database, "SELECT run_id FROM watcher_runs") == []
        assert _query(database, "SELECT incident_category FROM incidents") == [
            ("capacity_failure",)
        ]
    finally:
        store.close()


def test_control_store_allows_explicit_single_worker_thread_handoff(
    tmp_path: Path,
) -> None:
    store = ControlStore.open(
        tmp_path / "control.sqlite3",
        allow_thread_handoff=True,
    )
    errors: list[BaseException] = []
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))

        def use_store() -> None:
            try:
                assert store.load_source_state("rsk").source_id == "rsk"
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=use_store)
        worker.start()
        worker.join(timeout=5)

        assert not worker.is_alive()
        assert errors == []
    finally:
        store.close()


def test_paused_runtime_refuses_to_start_scheduler(tmp_path: Path) -> None:
    paths = WatcherPaths(
        tmp_path / "control" / "control.sqlite3",
        tmp_path / "staging",
        tmp_path / "archive",
        tmp_path / "control" / "watcher.lock",
        tmp_path / "control" / "recovery-staging",
    )
    command = WatcherCliAdapter(paths, scheduling_paused=True)

    with pytest.raises(RuntimeError, match="scheduling is paused"):
        command.run()

    assert not paths.database.exists()


def test_watcher_readiness_is_bound_to_the_live_process(tmp_path: Path) -> None:
    readiness_path = tmp_path / "watcher-ready.json"
    readiness = WatcherReadiness(readiness_path)

    readiness.mark_ready()
    readiness.check()

    raw_payload = cast(object, json.loads(readiness_path.read_text()))
    assert isinstance(raw_payload, dict)
    payload = cast(dict[str, int], raw_payload)
    payload["process_start_ticks"] += 1
    _ = readiness_path.write_text(json.dumps(payload))
    with pytest.raises(WatcherReadinessError, match="not running"):
        readiness.check()

    readiness.reset()
    with pytest.raises(WatcherReadinessError, match="unavailable"):
        readiness.check()


def test_watcher_readiness_reset_removes_interrupted_marker(tmp_path: Path) -> None:
    readiness_path = tmp_path / "watcher-ready.json"
    interrupted = tmp_path / ".watcher-ready.json.tmp"
    _ = interrupted.write_bytes(b"partial")

    WatcherReadiness(readiness_path).reset()

    assert not interrupted.exists()


def test_watcher_readiness_rejects_a_stale_scheduler_worker(tmp_path: Path) -> None:
    readiness_path = tmp_path / "watcher-ready.json"
    readiness = WatcherReadiness(readiness_path)
    readiness.mark_ready()
    raw_payload = cast(object, json.loads(readiness_path.read_text()))
    assert isinstance(raw_payload, dict)
    payload = cast(dict[str, int], raw_payload)
    payload["updated_at_ms"] = 1
    _ = readiness_path.write_text(json.dumps(payload))

    with pytest.raises(WatcherReadinessError, match="not responsive"):
        readiness.check()


@final
class _SchedulerService:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.catch_up_count = 0
        self.fail_due = False

    def reconcile(self, *, limit: int) -> None:
        assert limit == 100
        self.events.append("reconcile")

    def expire_stale_claims(self, *, limit: int) -> None:
        assert limit == 100
        self.events.append("expire")

    def validate_scheduled_collectors(self, *, limit: int) -> None:
        assert limit == 100
        self.events.append("validate_collectors")

    def scheduled_sources(self, *, limit: int) -> tuple[ScheduledSource, ...]:
        assert limit == 100
        return (
            ScheduledSource(
                "rsk",
                "rsk",
                "0 3 * * *",
                1,
                300,
                RetryPolicy(3, 0.5, 30.0),
            ),
        )

    def run_one_catch_up(self) -> None:
        self.catch_up_count += 1
        self.events.append("catch_up")

    def run_due(self, source_id: str) -> None:
        _ = source_id
        if self.fail_due:
            raise RuntimeError("private upstream URL")
        self.events.append("due")

    def publish_periodic_recovery(self, recovery_class: RecoveryClass) -> None:
        self.events.append(f"recovery_{recovery_class}")

    def ensure_periodic_recovery(self, recovery_class: RecoveryClass) -> None:
        self.events.append(f"ensure_recovery_{recovery_class}")


@final
class _SchedulerBackend:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.job_options: dict[str, object] = {}
        self.function: object | None = None
        self.jobs: list[tuple[object, dict[str, object]]] = []
        self.fail_start = False
        self.listener: Callable[[object], None] | None = None

    def add_listener(self, callback: object, mask: int) -> None:
        assert mask > 0
        self.listener = cast(Callable[[object], None], callback)

    def add_job(self, function: object, **kwargs: object) -> object:
        self.function = function
        self.events.append("future_jobs")
        self.job_options = kwargs
        self.jobs.append((function, kwargs))
        return object()

    def start(self) -> None:
        self.events.append("start")
        if self.fail_start:
            raise RuntimeError("scheduler backend unavailable")
        if self.listener is not None:
            self.listener(object())


def test_scheduler_startup_order_and_exactly_one_catch_up() -> None:
    events: list[str] = []
    service = _SchedulerService(events)
    backend = _SchedulerBackend(events)
    scheduler = WatcherScheduler(
        cast(WatcherService, cast(object, service)),
        backend=cast(SchedulerBackend, cast(object, backend)),
    )

    scheduler.start(on_ready=lambda: events.append("ready"))

    assert events == [
        "reconcile",
        "expire",
        "validate_collectors",
        "future_jobs",
        "future_jobs",
        "future_jobs",
        "future_jobs",
        "future_jobs",
        "catch_up",
        "ensure_recovery_daily",
        "ensure_recovery_weekly",
        "ensure_recovery_monthly",
        "start",
        "ready",
    ]
    assert service.catch_up_count == 1
    assert [job[1]["id"] for job in backend.jobs[:3]] == [
        "recovery:daily",
        "recovery:weekly",
        "recovery:monthly",
    ]
    source_options = next(
        options for _function, options in backend.jobs if options["id"] == "watcher:rsk"
    )
    assert source_options["coalesce"] is True
    assert source_options["max_instances"] == 1
    assert source_options["misfire_grace_time"] == 300
    readiness_options = next(
        options
        for _function, options in backend.jobs
        if options["id"] == "watcher:readiness"
    )
    assert readiness_options["seconds"] == 30
    readiness_callback = cast(
        Callable[[], None],
        next(
            function
            for function, options in backend.jobs
            if options["id"] == "watcher:readiness"
        ),
    )
    readiness_callback()
    assert events[-1] == "ready"
    periodic_callback = cast(Callable[[RecoveryClass], object], backend.jobs[0][0])
    _ = periodic_callback("daily")
    assert events[-1] == "ensure_recovery_daily"


def test_scheduler_callback_replaces_sensitive_exception() -> None:
    events: list[str] = []
    service = _SchedulerService(events)
    backend = _SchedulerBackend(events)
    scheduler = WatcherScheduler(
        cast(WatcherService, cast(object, service)),
        backend=cast(SchedulerBackend, cast(object, backend)),
    )
    scheduler.start()

    service.fail_due = True
    callback = cast(
        Callable[[str], object],
        next(
            function
            for function, options in backend.jobs
            if options["id"] == "watcher:rsk"
        ),
    )
    with pytest.raises(SchedulerError) as captured:
        _ = callback("rsk")
    assert str(captured.value) == "scheduled watcher run failed"
    assert captured.value.__suppress_context__ is True


def test_scheduler_does_not_report_ready_when_backend_start_fails() -> None:
    events: list[str] = []
    service = _SchedulerService(events)
    backend = _SchedulerBackend(events)
    backend.fail_start = True
    scheduler = WatcherScheduler(
        cast(WatcherService, cast(object, service)),
        backend=cast(SchedulerBackend, cast(object, backend)),
    )

    with pytest.raises(RuntimeError, match="backend unavailable"):
        scheduler.start(on_ready=lambda: events.append("ready"))

    assert "ready" not in events


def test_process_lock_rejects_a_second_owner(tmp_path: Path) -> None:
    first = WatcherProcessLock(tmp_path / "watcher.lock")
    second = WatcherProcessLock(tmp_path / "watcher.lock")

    with first:
        with pytest.raises(ProcessLockError, match="already held"):
            second.acquire()
    with second:
        pass


def test_capacity_gate_enforces_database_free_space_and_quota(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    staging = tmp_path / "staging"
    staging.mkdir()
    _ = database.write_bytes(b"1234")
    limits = CapacityLimits(4, 1, 2, 1)
    gate = RuntimeCapacityGate(database, staging, limits=limits)
    with pytest.raises(PreflightError, match="database reached"):
        gate.require_capacity()

    database.unlink()
    with ControlStore.open(database):
        pass

    def low_space(_path: Path) -> DiskUsage:
        return DiskUsage(100, 92, 8)

    gate = RuntimeCapacityGate(
        database,
        staging,
        limits=CapacityLimits(1024 * 1024, 5, 10, 10),
        disk_usage=low_space,
    )
    with pytest.raises(PreflightError, match="free space"):
        gate.require_capacity()

    _ = (staging / "partial").write_bytes(b"12")
    gate = RuntimeCapacityGate(
        database,
        staging,
        limits=CapacityLimits(1024 * 1024, 3, 4, 1),
        disk_usage=lambda _path: DiskUsage(100, 0, 100),
    )
    with pytest.raises(PreflightError, match="quota"):
        gate.require_capacity()


def test_capacity_gate_accepts_absent_database_on_empty_host(tmp_path: Path) -> None:
    database = tmp_path / "control" / "control.sqlite3"
    database.parent.mkdir()
    staging = tmp_path / "staging"
    staging.mkdir()

    report = RuntimeCapacityGate(
        database,
        staging,
        limits=CapacityLimits(1024 * 1024, 1, 1, 1),
        disk_usage=lambda _path: DiskUsage(100, 0, 100),
        allow_missing_database=True,
    ).check()

    assert report.database_size_bytes == 0
    assert report.database_logical_size_bytes == 0

    with pytest.raises(PreflightError, match="database is unavailable"):
        _ = RuntimeCapacityGate(
            database,
            staging,
            limits=CapacityLimits(1024 * 1024, 1, 1, 1),
            disk_usage=lambda _path: DiskUsage(100, 0, 100),
        ).check()


def test_capacity_gate_accounts_for_wal_shm_and_logical_pages(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    staging = tmp_path / "staging"
    staging.mkdir()
    with ControlStore.open(database):
        pass
    main_size = database.stat().st_size
    _ = Path(f"{database}-wal").write_bytes(b"w" * 7)
    _ = Path(f"{database}-shm").write_bytes(b"s" * 11)

    report = RuntimeCapacityGate(
        database,
        staging,
        limits=CapacityLimits(1024 * 1024, 1, 1, 1),
        disk_usage=lambda _path: DiskUsage(10**7, 0, 10**7),
    ).check()

    assert report.database_size_bytes == main_size + 18
    assert report.database_logical_size_bytes >= 4096


@final
class _Probe:
    def __init__(self) -> None:
        self.called = False

    def check(self) -> None:
        self.called = True


def test_preflight_requires_paused_scheduling() -> None:
    probe = _Probe()
    preflight = PausedPreflight((NamedPreflightProbe("capacity", probe),))

    with pytest.raises(PreflightError, match="paused"):
        _ = preflight.run(scheduling_paused=False)
    assert probe.called is False
    assert preflight.run(scheduling_paused=True).completed == ("capacity",)


def test_production_preflight_probes_exact_identity_secrets_and_control_copy(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    disposable = tmp_path / "disposable"
    disposable.mkdir(mode=0o700)
    with ControlStore.open(database):
        pass
    digest = f"sha256:{'a' * 64}"
    ImageDigestProbe(actual_digest=digest, expected_digest=digest).check()
    with pytest.raises(PreflightError, match="does not match"):
        ImageDigestProbe(
            actual_digest=digest,
            expected_digest=f"sha256:{'b' * 64}",
        ).check()
    secret = tmp_path / "secret.json"
    _ = secret.write_text("{}")
    ReadableSecretsProbe((secret,)).check()
    database_before = database.read_bytes()
    ControlDatabaseCopyProbe(database, disposable).check()

    assert database.read_bytes() == database_before
    assert list(disposable.iterdir()) == []


def test_control_copy_probe_allows_an_initial_empty_host(tmp_path: Path) -> None:
    database = tmp_path / "missing-control.sqlite3"
    disposable = tmp_path / "disposable"
    disposable.mkdir(mode=0o700)

    ControlDatabaseCopyProbe(database, disposable).check()

    assert not database.exists()
    assert list(disposable.iterdir()) == []


def test_deployment_copy_rejects_schema_mismatch_and_preserves_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "control.sqlite3"
    destination = tmp_path / "evidence.sqlite3"
    with ControlStore.open(source):
        pass
    connection = sqlite3.connect(source, isolation_level=None)
    try:
        _ = connection.execute(
            """
            INSERT INTO control_schema_migrations (
                version, name, up_sha256, down_sha256,
                minimum_reader_version, applied_at_ms
            ) VALUES (2, 'future_schema', ?, ?, 2, 1)
            """,
            ("a" * 64, "b" * 64),
        )
        _ = connection.execute(
            "UPDATE control_state SET schema_version = 2 WHERE singleton_id = 1"
        )
        _ = connection.execute("PRAGMA user_version = 2")
    finally:
        connection.close()
    source_before = source.read_bytes()

    with pytest.raises(PreflightError, match="differs from live"):
        _ = copy_control_database(
            source,
            destination,
            deadline_monotonic=time.monotonic() + 5,
        )

    assert destination.is_file()
    assert destination.stat().st_size <= 2 * 1024 * 1024 * 1024
    assert source.read_bytes() == source_before
    evidence = sqlite3.connect(destination)
    try:
        assert evidence.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert evidence.execute("PRAGMA user_version").fetchone() == (2,)
    finally:
        evidence.close()


def test_deployment_copy_removes_incomplete_evidence(tmp_path: Path) -> None:
    source = tmp_path / "invalid.sqlite3"
    destination = tmp_path / "evidence.sqlite3"
    _ = source.write_bytes(b"not a sqlite database")

    with pytest.raises(PreflightError, match="copy failed"):
        _ = copy_control_database(
            source,
            destination,
            deadline_monotonic=time.monotonic() + 5,
        )

    assert not destination.exists()


def test_watcher_preflight_cleans_interrupted_download_staging(tmp_path: Path) -> None:
    staging = tmp_path / "staging"
    _ = BlobStagingArea(staging, maximum_size_bytes=1)
    stale = staging / ".minos-blob-interrupted"
    _ = stale.write_bytes(b"x")
    stale.chmod(0o600)
    paths = WatcherPaths(
        tmp_path / "control" / "control.sqlite3",
        staging,
        tmp_path / "archive",
        tmp_path / "control" / "watcher.lock",
        tmp_path / "control" / "recovery-staging",
    )

    WatcherCliAdapter(
        paths,
        capacity_limits=CapacityLimits(1024 * 1024, 1, 1, 1),
    ).preflight()

    assert not stale.exists()


@final
class _ChecksumProbeClient:
    def __init__(self) -> None:
        self.put_call: dict[str, object] = {}

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        self.put_call = dict(kwargs)
        return {"VersionId": "probe-version"}

    def head_object(self, **_kwargs: object) -> Mapping[str, object]:
        payload = cast(bytes, self.put_call["Body"])
        return {
            "ContentLength": len(payload),
            "ChecksumSHA256": base64.b64encode(hashlib.sha256(payload).digest()).decode(
                "ascii"
            ),
            "ChecksumType": "FULL_OBJECT",
        }


def test_s3_checksum_probe_uses_conditional_full_object_checksum() -> None:
    client = _ChecksumProbeClient()

    S3ChecksumProbe(
        client,
        bucket="minos-test-bucket",
        expected_bucket_owner="123456789012",
    ).check()

    key = cast(str, client.put_call["Key"])
    match = re.fullmatch(
        r"probes/(\d{4}-\d{2}-\d{2})/[0-9a-f]{32}/([0-9a-f]{64})",
        key,
    )
    assert match is not None
    assert match.group(1) == datetime.now(UTC).date().isoformat()
    assert (
        match.group(2)
        == hashlib.sha256(cast(bytes, client.put_call["Body"])).hexdigest()
    )
    assert len(key) == 115
    assert client.put_call["IfNoneMatch"] == "*"
    assert client.put_call["ChecksumAlgorithm"] == "SHA256"
    assert client.put_call["ExpectedBucketOwner"] == "123456789012"


def test_healthchecks_file_transport_and_errors_keep_urls_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "opaque-healthcheck-identifier"
    base_url = f"https://hc-ping.com/{secret}"
    target_file = tmp_path / "healthchecks.json"
    _ = target_file.write_text(
        json.dumps(
            {
                "version": 1,
                "sources": {"rsk": base_url},
                "preflight": f"https://hc-ping.com/{secret}-preflight",
            }
        )
    )
    monkeypatch.setenv(
        "MINOS_WATCHER_HEALTHCHECKS_TARGETS_FILE",
        str(target_file),
    )
    settings = HealthchecksFileSettings(healthchecks_targets_file=target_file)
    targets = HealthcheckTargets.load(settings.healthchecks_targets_file)
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(204)

    with HealthchecksClient(
        targets,
        transport=httpx.MockTransport(handle),
    ) as client:
        client.start("rsk")
        client.success("rsk")
        client.failure("rsk")

    assert requested == [f"{base_url}/start", base_url, f"{base_url}/fail"]
    assert secret not in repr(targets)

    def reject(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with HealthchecksClient(
        targets,
        transport=httpx.MockTransport(reject),
    ) as client:
        with pytest.raises(HealthcheckError) as captured:
            client.success("rsk")
    assert secret not in str(captured.value)
    assert secret not in repr(captured.value)


def _query(database_path: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database_path)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
