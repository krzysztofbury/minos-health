import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import cast, final

import httpx
import pytest

from minos.ingest.watchers.archive import BlobStagingArea, FilesystemBlobArchive
from minos.ingest.watchers.baw.client import create_baw_client
from minos.ingest.watchers.baw.collector import BawSourceCollector
from minos.ingest.watchers.baw.models import BawSourceError
from minos.ingest.watchers.baw.publication import (
    BawPublicProjectionPublisher,
    build_baw_projection,
)
from minos.ingest.watchers.config import WATCHER_SOURCE_CONFIGURATION_CODECS
from minos.ingest.watchers.control import (
    CommittedRun,
    ControlStore,
    RecoveryReceipt,
    SourceConfiguration,
)
from minos.ingest.watchers.orchestrator import WatcherOrchestrator
from minos.ingest.watchers.protocols import CollectorRegistry
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass


@final
class _Recovery:
    def publish_run(self, store: ControlStore, run: CommittedRun) -> RecoveryReceipt:
        _ = store
        digest = "a" * 64
        revision = run.committed_state_revision
        return RecoveryReceipt(
            run.run_id,
            revision,
            revision,
            digest,
            4096,
            f"control-recovery/run/{revision:020d}/{digest}.sqlite3",
            "version-1",
            digest,
            4096,
        )

    def publish_periodic(
        self, store: ControlStore, recovery_class: RecoveryClass
    ) -> PublishedRecoveryImage:
        _ = store, recovery_class
        raise AssertionError("not used")

    def ensure_periodic(
        self, store: ControlStore, recovery_class: RecoveryClass
    ) -> PublishedRecoveryImage | None:
        _ = store, recovery_class
        return None


@final
class _Heartbeat:
    def start(self, source_id: str) -> None:
        assert source_id == "baw"

    def success(self, source_id: str) -> None:
        assert source_id == "baw"

    def failure(self, source_id: str) -> None:
        assert source_id == "baw"


def _zip_file() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        entry = zipfile.ZipInfo("document.xml", date_time=(2026, 1, 1, 0, 0, 0))
        archive.writestr(entry, "<act />")
    return data.getvalue()


def _document(
    document_id: int,
    *,
    amendment: bool = False,
    generated_id: str,
) -> bytes:
    def file(
        file_id: int, name: str, parent: int | None, children: list[dict[str, object]]
    ) -> dict[str, object]:
        return {
            "Id": file_id,
            "Name": name,
            "DisplayName": None,
            "DocumentId": document_id,
            "ParentId": parent,
            "IsZipxAttachment": parent is not None,
            "Children": children,
        }

    root = document_id * 10
    attachment = "Zalacznik1.xlsx" if amendment else "Zalacznik1a.xlsx"
    payload = {
        "Id": document_id,
        "InstitutionId": 4,
        "IsPrivate": False,
        "IsRemoved": False,
        "WasEverPublished": True,
        "ActNumber": "74/2026/DSOZ" if amendment else "46/2026/DSOZ",
        "LegalActTypeDescription": "Zarządzenie",
        "ActDate": "2026-07-23T00:00:00",
        "PublishedDate": None,
        "ValidFrom": "2026-07-24T00:00:00",
        "ValidTo": None,
        "LegalActStatus": {"Name": "Obowiązujący"},
        "LegalBasisActs": [
            {
                "ObjectIdentifier": generated_id,
                "LegalActRegulations": [
                    {"ObjectIdentifier": generated_id, "Section": "unchanged"}
                ],
            }
        ],
        "Files": [
            file(
                root,
                "Zarządzenie_74.2026.DSOZ.zipx" if amendment else "LegalAct.zipx",
                None,
                [
                    file(root + 1, attachment, root, []),
                    file(root + 2, "Zalacznik9.xlsx", root, []),
                ],
            )
        ],
    }
    return json.dumps(payload).encode()


def _relations() -> bytes:
    return json.dumps(
        [
            [
                {
                    "Id": document_id,
                    "AutomaticType": role,
                    "IsProject": False,
                    "SourceDocumentId": None if role == 1 else 43868,
                    "IntroductoryDocumentId": 43942 if role == 3 else None,
                }
                for document_id, role in ((43868, 1), (43942, 6), (43943, 3))
            ]
        ]
    ).encode()


def _setup(
    tmp_path: Path,
    *,
    rotate: bool = False,
    invalid_zip: bool = False,
    volatile_identifiers: bool = False,
) -> tuple[ControlStore, WatcherOrchestrator, list[str], Path]:
    database = tmp_path / "control.sqlite3"
    calls: list[str] = []
    read_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal read_count
        path = request.url.path
        calls.append(path)
        if "/details/" in path:
            document_id = int(path.split("/")[-2])
            if document_id == 43868:
                read_count += 1
            generated_id = (
                "00000000-0000-4000-8000-000000000002"
                if volatile_identifiers and document_id == 43868 and read_count % 2 == 0
                else "00000000-0000-4000-8000-000000000001"
            )
            body = _document(
                document_id,
                amendment=document_id == 43942,
                generated_id=generated_id,
            )
            if rotate and document_id == 43868 and read_count == 2:
                body = body.replace(b"2026-07-23", b"2026-07-24")
            return httpx.Response(
                200, content=body, headers={"content-type": "application/json"}
            )
        if "/GetAmendments/" in path:
            return httpx.Response(
                200, content=_relations(), headers={"content-type": "application/json"}
            )
        if "/GetDocumentAttachments/" in path:
            return httpx.Response(
                200, content=b"[]", headers={"content-type": "application/json"}
            )
        if "/api/file/" in path:
            file_id = int(path.rsplit("/", 1)[1])
            name = (
                "Zarządzenie_74.2026.DSOZ.zipx"
                if file_id == 439420
                else "LegalAct.zipx"
                if file_id % 10 == 0
                else (
                    "Zalacznik9.xlsx"
                    if file_id % 10 == 2
                    else "Zalacznik1.xlsx"
                    if file_id // 10 == 43942
                    else "Zalacznik1a.xlsx"
                )
            )
            content = (
                b"<html>challenge</html>"
                if invalid_zip and file_id == 439431
                else _zip_file()
            )
            disposition = (
                "attachment; filename=Zarz_dzenie_74.2026.DSOZ.zipx; "
                + "filename*=UTF-8''Zarz%C4%85dzenie_74.2026.DSOZ.zipx"
                if file_id == 439420
                else f"attachment; filename={name}"
            )
            return httpx.Response(
                200,
                content=content,
                headers={
                    "content-type": "application/attachment",
                    "content-length": str(len(content)),
                    "content-disposition": disposition,
                },
            )
        raise AssertionError(f"unexpected request: {path}")

    def client_factory() -> httpx.Client:
        return create_baw_client(transport=httpx.MockTransport(handler))

    store = ControlStore.open(
        database, source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS
    )
    _ = store.configure_source(SourceConfiguration("baw", "baw", {}, True))
    # Use the same registry contract as production with an in-memory HTTP boundary.
    collectors = CollectorRegistry()
    collectors.register(
        "baw",
        BawSourceCollector(
            BlobStagingArea(tmp_path / "staging", maximum_size_bytes=1024 * 1024),
            client_factory=client_factory,
        ),
    )
    prepare_public_root(tmp_path / "public")
    prepare_public_root(tmp_path / "public" / "baw")
    orchestrator = WatcherOrchestrator(
        store=store,
        archive=FilesystemBlobArchive(tmp_path / "archive"),
        collectors=collectors,
        recovery_publisher=_Recovery(),
        heartbeat=_Heartbeat(),
        public_projection_publisher=BawPublicProjectionPublisher(
            ImmutableGenerationPublisher(tmp_path / "public" / "baw", source="BAW")
        ),
    )
    return store, orchestrator, calls, database


def test_baw_graph_archives_selected_bytes_and_does_not_repeat_change(
    tmp_path: Path,
) -> None:
    store, orchestrator, calls, database = _setup(tmp_path, volatile_identifiers=True)
    try:
        first = store.claim_manual("baw")
        _ = orchestrator.run_claim(first, first.retry_policy)
        state = store.load_source_state("baw").cursor
        documents = cast(dict[str, str], state["documents"])
        products = cast(dict[str, str], state["products"])
        assert set(documents) == {"43868", "43942", "43943"}
        assert len(products) == 9
        assert products["43942:439421"] == products["43943:439431"]
        assert "/api/file/GetZipxAttachment/4/439431" in calls
        assert "/api/file/4/438680" in calls
        assert _query(database, "SELECT COUNT(*) FROM source_changes") == [(1,)]
        metadata = cast(
            dict[str, object],
            json.loads(
                cast(
                    str,
                    _query(
                        database,
                        "SELECT metadata_json FROM observations WHERE observation_kind = 'baw_document' AND source_identity = '43942'",
                    )[0][0],
                )
            ),
        )
        assert metadata["entry_into_force"] == "2026-07-24T00:00:00"
        assert metadata["legal_act_type"] == "Zarządzenie"
        assert metadata["service_applicability_date"] is None
        public = cast(
            dict[str, object],
            json.loads(
                (
                    tmp_path
                    / "public"
                    / "baw"
                    / "current"
                    / "api"
                    / "v1"
                    / "provenance"
                    / "baw.json"
                ).read_text()
            ),
        )
        assert public["source"] == "BAW"
        redistribution = cast(dict[str, object], public["redistribution"])
        public_documents = cast(list[dict[str, object]], public["documents"])
        assert redistribution["artifacts"] == []
        assert len(public_documents) == 3
        assert (
            sum(len(cast(list[object], item["files"])) for item in public_documents)
            == 9
        )
        assert all(
            item["service_applicability_date"] is None for item in public_documents
        )
        assert {item["source_url"] for item in public_documents} == {
            "https://baw.nfz.gov.pl/NFZ/document/43868/Zarzadzenie-46_2026_DSOZ",
            "https://baw.nfz.gov.pl/NFZ/document/43942/Zarzadzenie-74_2026_DSOZ",
            "https://baw.nfz.gov.pl/NFZ/document/43943/Zarzadzenie-46_2026_DSOZ",
        }

        second = store.claim_manual("baw")
        _ = orchestrator.run_claim(second, second.retry_policy)
        assert _query(database, "SELECT COUNT(*) FROM source_changes") == [(1,)]
    finally:
        store.close()


def test_legacy_baw_observation_restores_immutable_public_generation(
    tmp_path: Path,
) -> None:
    store, orchestrator, _, _ = _setup(tmp_path)
    try:
        claim = store.claim_manual("baw")
        _ = orchestrator.run_claim(claim, claim.retry_policy)
        latest = store.latest_recovery_reconciled_run("baw")
        assert latest is not None
        history = store.load_committed_observation_history(
            "baw",
            "baw_document",
            through_state_revision=latest.committed_state_revision,
            product_kind="baw_file",
        )
        assert len(history.revisions) == 1
        revision = history.revisions[0]
        legacy = replace(
            history,
            revisions=(
                replace(
                    revision,
                    observations=tuple(
                        replace(
                            observation,
                            metadata={
                                key: value
                                for key, value in observation.metadata.items()
                                if key != "legal_act_type"
                            },
                        )
                        for observation in revision.observations
                    ),
                ),
            ),
        )
        before = build_baw_projection(legacy)
        public = cast(dict[str, object], json.loads(before))
        documents = cast(list[dict[str, object]], public["documents"])
        assert {item["source_url"] for item in documents} == {
            f"https://baw.nfz.gov.pl/NFZ/document/{document_id}"
            for document_id in (43868, 43942, 43943)
        }
        current = (
            tmp_path / "public/baw/current/api/v1/provenance/baw.json"
        ).read_bytes()
        assert before != current
    finally:
        store.close()


@pytest.mark.parametrize(
    ("rotate", "invalid_zip", "message"),
    [(True, False, "BAW document changed"), (False, True, "not a valid ZIP")],
)
def test_baw_invalid_source_cannot_advance(
    tmp_path: Path, rotate: bool, invalid_zip: bool, message: str
) -> None:
    store, orchestrator, _, database = _setup(
        tmp_path, rotate=rotate, invalid_zip=invalid_zip
    )
    try:
        claim = store.claim_manual("baw")
        with pytest.raises(BawSourceError, match=message):
            _ = orchestrator.run_claim(claim, claim.retry_policy)
        assert store.load_source_state("baw").cursor == {}
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(0,)]
        assert not (tmp_path / "public" / "baw" / "current").exists()
    finally:
        store.close()


def _query(database: Path, sql: str) -> list[tuple[object, ...]]:
    with sqlite3.connect(database) as connection:
        return connection.execute(sql).fetchall()
