import json
import sqlite3
from pathlib import Path
from typing import cast, final

import httpx
import pytest

from minos.cli.main import WatcherCommandArguments
from minos.cli.watcher import create_runtime_collectors, create_watcher_command
from minos.ingest.watchers.archive import FilesystemBlobArchive
from minos.ingest.watchers.config import RSK_SOURCE_CONFIGURATION_CODECS, RskSettings
from minos.ingest.watchers.control import (
    CommittedRun,
    ControlStore,
    RecoveryReceipt,
    SourceConfiguration,
)
from minos.ingest.watchers.orchestrator import WatcherOrchestrator
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    PublicPublicationError,
    prepare_public_root,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass
from minos.ingest.watchers.rsk.client import create_rsk_client
from minos.ingest.watchers.rsk.publication import RskPublicProjectionPublisher

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "rsk"
_XML_UUID = "00000000-0000-4000-8000-000000000011"


@final
class _RecoveryPublisher:
    def publish_run(self, store: ControlStore, run: CommittedRun) -> RecoveryReceipt:
        _ = store
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
            digest,
            4096,
        )

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        _ = store, recovery_class
        raise AssertionError("not used")

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        _ = store, recovery_class
        return None


@final
class _Heartbeat:
    def start(self, source_id: str) -> None:
        assert source_id == "rsk"

    def success(self, source_id: str) -> None:
        assert source_id == "rsk"

    def failure(self, source_id: str) -> None:
        raise AssertionError(f"unexpected failure heartbeat for {source_id}")


def test_rsk_collector_commits_one_atomic_all_hcd_observation(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        payloads = {
            "/api/rsk/resource/role/UZYNLG": (
                "role-catalog.json",
                "application/json",
            ),
            "/api/rsk/resource/basic-info/00CD10": (
                "basic-info-00CD10.json",
                "application/json",
            ),
            "/api/rsk/resource/basic-info/99ZZ99": (
                "basic-info-99ZZ99.json",
                "application/json",
            ),
            f"/api/rsk/file/get/{_XML_UUID}": (
                "product-00CD10.xml",
                "application/xml",
            ),
        }
        file_name, media_type = payloads[path]
        body = (_FIXTURES / file_name).read_bytes()
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={"Content-Type": media_type, "Content-Length": str(len(body))},
        )

    def client_factory(settings: RskSettings) -> httpx.Client:
        return create_rsk_client(
            settings,
            transport=httpx.MockTransport(handler),
        )

    store = ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = create_runtime_collectors(
            tmp_path / "staging",
            maximum_size_bytes=512 * 1024 * 1024,
            rsk_client_factory=client_factory,
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(tmp_path / "archive"),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(),
            heartbeat=_Heartbeat(),
        )

        claim = store.claim_manual("rsk")
        completed = orchestrator.run_claim(claim, claim.retry_policy)

        assert completed.source_id == "rsk"
        assert store.load_source_state("rsk").cursor["schema_version"] == 1
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(5,)]
        assert _query(database, "SELECT COUNT(*) FROM source_changes") == [(1,)]
        assert _query(database, "SELECT status FROM watcher_runs") == [("successful",)]
        assert requests == [
            "/api/rsk/resource/role/UZYNLG",
            "/api/rsk/resource/basic-info/00CD10",
            "/api/rsk/resource/basic-info/99ZZ99",
            f"/api/rsk/file/get/{_XML_UUID}",
            "/api/rsk/resource/basic-info/00CD10",
        ]
    finally:
        store.close()


def test_filesystem_runtime_factory_completes_manual_rsk_run(tmp_path: Path) -> None:
    database = tmp_path / "control.sqlite3"

    def handler(request: httpx.Request) -> httpx.Response:
        payloads = {
            "/api/rsk/resource/role/UZYNLG": (
                "role-catalog.json",
                "application/json",
            ),
            "/api/rsk/resource/basic-info/00CD10": (
                "basic-info-00CD10.json",
                "application/json",
            ),
            "/api/rsk/resource/basic-info/99ZZ99": (
                "basic-info-99ZZ99.json",
                "application/json",
            ),
            f"/api/rsk/file/get/{_XML_UUID}": (
                "product-00CD10.xml",
                "application/xml",
            ),
        }
        file_name, media_type = payloads[request.url.path]
        body = (_FIXTURES / file_name).read_bytes()
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={"Content-Type": media_type, "Content-Length": str(len(body))},
        )

    def client_factory(settings: RskSettings) -> httpx.Client:
        return create_rsk_client(
            settings,
            transport=httpx.MockTransport(handler),
        )

    arguments = WatcherCommandArguments(
        "run-once",
        database,
        tmp_path / "staging",
        tmp_path / "archive",
        tmp_path / "watcher.lock",
        "rsk",
    )
    command = create_watcher_command(arguments, rsk_client_factory=client_factory)
    command.bootstrap_rsk()
    command.run_once("rsk")

    assert _query(database, "SELECT status FROM watcher_runs") == [("successful",)]
    assert (
        len(
            list((tmp_path / "archive" / "control-recovery" / "run").rglob("*.sqlite3"))
        )
        == 1
    )
    public_root = tmp_path / "public"
    first_generation = (public_root / "current").readlink().name
    first_revision_text, first_digest = first_generation.split("-", 1)
    projection = cast(
        dict[str, object],
        json.loads(
            (public_root / "current" / "api/v1/provenance/rsk.json").read_bytes()
        ),
    )
    encoded = json.dumps(projection, sort_keys=True)
    assert projection["source"] == "RSK"
    assert projection["redistribution"] == {"schema_version": 1, "artifacts": []}
    entries = cast(list[dict[str, object]], projection["entries"])
    assert all(item["downloadable"] is False for item in entries)
    assert all(item["download_url"] is None for item in entries)
    archived = [
        item
        for item in entries
        if item["archive_status"] == "privately_archived_verified"
    ]
    assert len(archived) == 1
    assert archived[0]["product_sha256"] != archived[0]["metadata_observation_sha256"]
    assert "SYNTHETIC_PRIVATE_CANARY_DESCRIPTION" not in encoded
    assert "SYNTHETIC_PRIVATE_CANARY_FILE_DESCRIPTION" not in encoded
    assert "SYNTHETIC_PRIVATE_CANARY_PERSON" not in encoded
    assert "private-canary@example.invalid" not in encoded
    assert "https://example.invalid/publisher" not in encoded
    assert "example.invalid/guide" not in encoded

    command.run_once("rsk")
    second_generation = (public_root / "current").readlink().name
    second_revision_text, second_digest = second_generation.split("-", 1)
    assert int(second_revision_text) > int(first_revision_text)
    rebuilt = command.rebuild_public_rsk(
        expected_current_revision=int(second_revision_text),
        expected_current_sha256=second_digest,
        target_revision=int(first_revision_text),
        target_sha256=first_digest,
    )
    assert rebuilt == first_generation
    assert (public_root / "current").readlink().name == first_generation
    with pytest.raises(PublicPublicationError, match="expectation is stale"):
        _ = command.rebuild_public_rsk(
            expected_current_revision=int(second_revision_text),
            expected_current_sha256=second_digest,
            target_revision=int(first_revision_text),
            target_sha256=first_digest,
        )

    (public_root / "current").unlink()
    with ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    ) as store:
        RskPublicProjectionPublisher(ImmutableGenerationPublisher(public_root)).restore(
            store
        )
    assert (public_root / "current" / "api/v1/provenance/rsk.json").is_file()

    # Empty public storage recovers both issued citations, not just latest.
    retained = {
        path.name: path.read_bytes()
        for path in (public_root / "citations").glob("*.json")
    }
    assert set(retained) == {first_generation + ".json", second_generation + ".json"}
    recovered_root = tmp_path / "recovered-public"
    prepare_public_root(recovered_root)
    with ControlStore.open(
        database, source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS
    ) as store:
        RskPublicProjectionPublisher(
            ImmutableGenerationPublisher(recovered_root, retain_citations=True)
        ).restore(store)
    assert {
        path.name: path.read_bytes()
        for path in (recovered_root / "citations").glob("*.json")
    } == retained
    assert (recovered_root / "current").readlink().name == second_generation


def _query(database: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
