import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, final
from uuid import uuid4

from minos.ingest.watchers.archive import BlobArchive, BlobArchiveError
from minos.ingest.watchers.control import (
    CommittedRun,
    ControlStore,
    IncidentDraft,
    ObservationCommit,
    RecoveryReceipt,
    RetryPolicy,
    RunClaim,
    UncertainControlOutcomeError,
)
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    CollectorNotRegisteredError,
    CollectorRegistry,
    HeartbeatClient,
    RecoveryPublisher,
    RetryableWatcherError,
    WatcherRuntimeError,
)
from minos.ingest.watchers.recovery import (
    ListedRecoveryImage,
    PublishedRecoveryImage,
    RecoveryClass,
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    StagedRecoveryImage,
    parse_recovery_key,
    recovery_period,
    recovery_receipt,
)

_MAX_BLOBS = 256
_MAX_OBSERVATIONS = 256
_MAX_CHANGES = 256
_MAX_INCIDENTS = 256
_MAX_PERIODIC_RECOVERY_IMAGES = 1024


class _RecoveryStore(Protocol):
    def publish(self, image: StagedRecoveryImage) -> PublishedRecoveryImage: ...

    def list_images(
        self,
        recovery_class: RecoveryClass,
        *,
        maximum_objects: int,
        page_size: int = 1000,
    ) -> tuple[ListedRecoveryImage, ...]: ...


@dataclass(frozen=True, slots=True)
class CompletedRun:
    source_id: str
    run_id: str
    committed_state_revision: int


@final
class RunRecoveryPublisher:
    def __init__(
        self,
        staging: RecoveryImageStagingArea,
        recovery_store: _RecoveryStore,
    ) -> None:
        self._staging = staging
        self._recovery_store = recovery_store

    def publish_run(
        self,
        store: ControlStore,
        run: CommittedRun,
    ) -> RecoveryReceipt:
        request = RecoveryImageRequest(
            "run",
            _utc_now_ms(),
            run.run_id,
            run.committed_state_revision,
        )
        with self._staging.create(store, request) as image:
            published = self._recovery_store.publish(image)
        return recovery_receipt(published)

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        if recovery_class not in ("daily", "weekly", "monthly"):
            raise WatcherRuntimeError("periodic recovery class is unsupported")
        request = RecoveryImageRequest(
            recovery_class,
            _utc_now_ms(),
            None,
            None,
        )
        with self._staging.create(store, request) as image:
            return self._recovery_store.publish(image)

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        if recovery_class not in ("daily", "weekly", "monthly"):
            raise WatcherRuntimeError("periodic recovery class is unsupported")
        created_at_ms = _utc_now_ms()
        expected_period = recovery_period(recovery_class, created_at_ms)
        listed = self._recovery_store.list_images(
            recovery_class,
            maximum_objects=_MAX_PERIODIC_RECOVERY_IMAGES,
        )
        if any(
            parse_recovery_key(item.key).period == expected_period for item in listed
        ):
            return None
        request = RecoveryImageRequest(
            recovery_class,
            created_at_ms,
            None,
            None,
        )
        with self._staging.create(store, request) as image:
            return self._recovery_store.publish(image)


@final
class WatcherOrchestrator:
    def __init__(
        self,
        *,
        store: ControlStore,
        archive: BlobArchive,
        collectors: CollectorRegistry,
        recovery_publisher: RecoveryPublisher,
        heartbeat: HeartbeatClient,
        sleep: Callable[[float], None] = time.sleep,
        now_ms: Callable[[], int] | None = None,
    ) -> None:
        self._store = store
        self._archive = archive
        self._collectors = collectors
        self._recovery_publisher = recovery_publisher
        self._heartbeat = heartbeat
        self._sleep = sleep
        self._now_ms = now_ms or _utc_now_ms

    def publish_periodic_recovery(
        self,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        return self._recovery_publisher.publish_periodic(
            self._store,
            recovery_class,
        )

    def ensure_periodic_recovery(
        self,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        return self._recovery_publisher.ensure_periodic(
            self._store,
            recovery_class,
        )

    def run_claim(
        self,
        claim: RunClaim,
        retry_policy: RetryPolicy,
    ) -> CompletedRun:
        phase = "load_state"
        committed: CommittedRun | None = None
        try:
            source = self._store.load_claim_state(claim.identity)
            phase = "collector_dispatch"
            collector = self._collectors.resolve(source.watcher_type)
            phase = "heartbeat_start"
            self._heartbeat.start(source.source_id)
            context = CollectionContext(
                source.source_id,
                source.configuration,
                source.cursor,
                claim,
            )
            for attempt in range(1, retry_policy.max_attempts + 1):
                phase = "retry_deadline"
                self._require_retry_budget(claim)
                phase = "collect"
                try:
                    with collector.collect(context) as collected:
                        _validate_collected_run(collected)
                        phase = "archive"
                        receipts = tuple(
                            self._archive.put(blob) for blob in collected.blobs
                        )
                        phase = "commit"
                        committed = self._store.commit_observations(
                            claim.identity,
                            ObservationCommit(
                                receipts,
                                collected.observations,
                                collected.cursor,
                                collected.changes,
                                collected.incidents,
                            ),
                        )
                    break
                except RetryableWatcherError:
                    if committed is not None or phase == "commit":
                        raise
                    if attempt == retry_policy.max_attempts:
                        raise
                    retry_delay_seconds = _retry_delay_seconds(retry_policy, attempt)
                    phase = "retry_deadline"
                    self._require_retry_budget(
                        claim,
                        delay_seconds=retry_delay_seconds,
                    )
                    phase = "retry_wait"
                    self._sleep(retry_delay_seconds)
            if committed is None:
                raise WatcherRuntimeError(
                    "watcher retry loop completed without a commit"
                )
            phase = "reconcile"
            self.reconcile_run(committed)
            return CompletedRun(
                committed.source_id,
                committed.run_id,
                committed.committed_state_revision,
            )
        except BaseException as error:
            if committed is None:
                if isinstance(error, UncertainControlOutcomeError):
                    self._send_failure_heartbeat(claim.identity.source_id, error)
                else:
                    self._abort_running_claim(claim, phase, error)
            else:
                self._record_reconciliation_failure(committed, phase, error)
            raise

    def _require_retry_budget(
        self,
        claim: RunClaim,
        *,
        delay_seconds: float = 0.0,
    ) -> None:
        now_ms = self._now_ms()
        remaining_ms = claim.identity.deadline_at_ms - now_ms
        delay_ms = math.ceil(delay_seconds * 1000)
        if remaining_ms <= 0 or delay_ms >= remaining_ms:
            raise WatcherRuntimeError(
                "watcher claim deadline has insufficient retry budget"
            )

    def reconcile_all(self, *, limit: int = 100) -> None:
        for run in self._store.list_reconcilable_runs(limit=limit):
            try:
                self.reconcile_run(run)
            except BaseException as error:
                if isinstance(error, UncertainControlOutcomeError):
                    self._send_failure_heartbeat(run.source_id, error)
                else:
                    self._record_reconciliation_failure(run, "reconcile", error)
                raise

    def reconcile_run(self, run: CommittedRun) -> None:
        if run.status == "committed":
            receipt = self._recovery_publisher.publish_run(self._store, run)
            _ = self._store.mark_heartbeat_pending(
                run.run_id,
                committed_state_revision=run.committed_state_revision,
                recovery_receipt=receipt,
            )
        elif run.status != "heartbeat_pending":
            raise WatcherRuntimeError("run has an unsupported reconciliation status")
        self._heartbeat.success(run.source_id)
        _ = self._store.mark_successful(
            run.run_id,
            committed_state_revision=run.committed_state_revision,
        )

    def _abort_running_claim(
        self,
        claim: RunClaim,
        phase: str,
        primary_error: BaseException,
    ) -> None:
        category = _failure_category(phase, primary_error)
        incident = IncidentDraft(
            uuid4().hex,
            category,
            {"phase": phase},
        )
        try:
            _ = self._store.abort_claim(
                claim.identity,
                error_category=category,
                incident=incident,
            )
        except BaseException as abort_error:
            primary_error.add_note(
                f"claim abort also failed: {type(abort_error).__name__}"
            )
        self._send_failure_heartbeat(claim.identity.source_id, primary_error)

    def _record_reconciliation_failure(
        self,
        run: CommittedRun,
        phase: str,
        primary_error: BaseException,
    ) -> None:
        try:
            _ = self._store.record_incident(
                IncidentDraft(
                    uuid4().hex,
                    "reconciliation_failure",
                    {"phase": phase},
                ),
                source_id=run.source_id,
                run_id=run.run_id,
            )
        except BaseException as incident_error:
            primary_error.add_note(
                f"reconciliation incident also failed: {type(incident_error).__name__}"
            )
        self._send_failure_heartbeat(run.source_id, primary_error)

    def _send_failure_heartbeat(
        self,
        source_id: str,
        primary_error: BaseException,
    ) -> None:
        try:
            self._heartbeat.failure(source_id)
        except BaseException as heartbeat_error:
            primary_error.add_note(
                f"failure heartbeat also failed: {type(heartbeat_error).__name__}"
            )


def _validate_collected_run(collected: CollectedRun) -> None:
    _validate_count(len(collected.blobs), 1, _MAX_BLOBS, "collected blobs")
    _validate_count(
        len(collected.observations),
        1,
        _MAX_OBSERVATIONS,
        "collected observations",
    )
    _validate_count(len(collected.changes), 0, _MAX_CHANGES, "collected changes")
    _validate_count(len(collected.incidents), 0, _MAX_INCIDENTS, "incidents")
    digests = [blob.sha256 for blob in collected.blobs]
    if len(digests) != len(set(digests)):
        raise WatcherRuntimeError("collector returned duplicate staged blobs")
    digest_set = set(digests)
    for observation in collected.observations:
        if observation.response_sha256 not in digest_set:
            raise WatcherRuntimeError("observation response has no staged blob")
        if any(blob.sha256 not in digest_set for blob in observation.blobs):
            raise WatcherRuntimeError("observation reference has no staged blob")


def _validate_count(count: int, minimum: int, maximum: int, label: str) -> None:
    if count < minimum or count > maximum:
        raise WatcherRuntimeError(f"{label} count is outside the supported range")


def _failure_category(phase: str, error: BaseException) -> str:
    if isinstance(error, BlobArchiveError):
        return "archive_failure"
    if isinstance(error, CollectorNotRegisteredError):
        return "collector_unavailable"
    categories = {
        "load_state": "control_state_failure",
        "collector_dispatch": "collector_unavailable",
        "heartbeat_start": "heartbeat_failure",
        "retry_deadline": "claim_deadline",
        "retry_wait": "collection_failure",
        "collect": "collection_failure",
        "commit": "control_commit_failure",
    }
    return categories.get(phase, "watcher_runtime_failure")


def _utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _retry_delay_seconds(policy: RetryPolicy, completed_attempts: int) -> float:
    delay_seconds = policy.base_seconds
    for _attempt in range(1, completed_attempts):
        delay_seconds = min(delay_seconds * 2, policy.max_seconds)
    return delay_seconds
