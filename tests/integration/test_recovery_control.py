import time
from pathlib import Path

from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    BlobReceipt,
    ClaimRequest,
    ControlStore,
    ObservationCommit,
    ObservationDraft,
    SourceConfiguration,
)
from minos.ingest.watchers.recovery import (
    PublishedRecoveryImage,
    RecoveryImageRequest,
    RecoveryImageStagingArea,
    recovery_key,
    recovery_receipt,
)

_DIGEST = "d" * 64


def test_verified_run_image_allows_reconciliation_and_cleans_up(tmp_path: Path) -> None:
    store = ControlStore.open(
        tmp_path / "control.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    staging = RecoveryImageStagingArea(tmp_path / "staging")
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        now_ms = time.time_ns() // 1_000_000
        claim = store.acquire_claim(
            ClaimRequest("rsk", "manual", now_ms, deadline_after_ms=300_000)
        )
        blob = BlobReceipt(
            _DIGEST,
            2,
            "application/json",
            f"blobs/sha256/{_DIGEST}",
            _DIGEST,
            2,
        )
        observation = ObservationDraft(
            "recovery-integration-observation",
            "catalog",
            "source",
            _DIGEST,
            now_ms,
            {},
        )
        committed = store.commit_observations(
            claim.identity,
            ObservationCommit((blob,), (observation,), {"cursor": "v1"}),
        )

        request = RecoveryImageRequest(
            "run",
            now_ms,
            committed.run_id,
            committed.committed_state_revision,
        )
        with staging.create(store, request) as image:
            published = PublishedRecoveryImage(
                recovery_key(image.metadata, image.sha256),
                "integration-version-1",
                image.sha256,
                image.size_bytes,
                image.metadata,
            )
            pending = store.mark_heartbeat_pending(
                committed.run_id,
                committed_state_revision=committed.committed_state_revision,
                recovery_receipt=recovery_receipt(published),
            )
        successful = store.mark_successful(
            committed.run_id,
            committed_state_revision=committed.committed_state_revision,
        )

        assert pending.status == "heartbeat_pending"
        assert successful.status == "successful"
        assert list((tmp_path / "staging").iterdir()) == []
    finally:
        store.close()
