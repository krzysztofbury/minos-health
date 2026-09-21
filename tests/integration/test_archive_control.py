import time
from pathlib import Path

from minos.ingest.watchers.archive import BlobStagingArea, FilesystemBlobArchive
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    ClaimRequest,
    ControlStore,
    ObservationBlob,
    ObservationCommit,
    ObservationDraft,
    SourceConfiguration,
)


def test_verified_filesystem_receipt_commits_observation(tmp_path: Path) -> None:
    store = ControlStore.open(
        tmp_path / "control.sqlite3",
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        now_ms = time.time_ns() // 1_000_000
        claim = store.acquire_claim(
            ClaimRequest(
                source_id="rsk",
                run_mode="manual",
                scheduled_at_ms=now_ms,
                deadline_after_ms=300_000,
            )
        )
        archive = FilesystemBlobArchive(tmp_path / "archive")
        with BlobStagingArea(tmp_path / "staging").stage() as writer:
            _ = writer.write(b'{"source":"rsk"}')
            receipt = archive.put(writer.seal(media_type="application/json"))

        committed = store.commit_observations(
            claim.identity,
            ObservationCommit(
                blobs=(receipt,),
                observations=(
                    ObservationDraft(
                        observation_id="observation-archive-0001",
                        observation_kind="catalog",
                        source_identity="UZYNLG",
                        response_sha256=receipt.sha256,
                        observed_at_ms=claim.started_at_ms,
                        metadata={"status": 200},
                        blobs=(ObservationBlob("response", 0, receipt.sha256),),
                    ),
                ),
                cursor={"etag": "v1"},
            ),
        )

        assert committed.source_id == "rsk"
        assert committed.committed_state_revision == 3
    finally:
        store.close()
