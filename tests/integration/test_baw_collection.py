import io
import json
import sqlite3
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import cast, final

import httpx
import pytest

from minos.ingest.watchers.aotmit.client import create_aotmit_client
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
from minos.ingest.watchers.czs.client import create_czs_client
from minos.ingest.watchers.nfz.collector import NfzSourceCollector
from minos.ingest.watchers.nfz.discovery import create_nfz_client
from minos.ingest.watchers.orchestrator import WatcherOrchestrator
from minos.ingest.watchers.protocols import CollectorRegistry, SourceDriftWatcherError
from minos.ingest.watchers.publication import (
    ImmutableGenerationPublisher,
    prepare_public_root,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass
from minos.ingest.watchers.rsk.client import create_rsk_client


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
        assert source_id in ("baw", "nfz")

    def success(self, source_id: str) -> None:
        assert source_id in ("baw", "nfz")

    def failure(self, source_id: str) -> None:
        assert source_id in ("baw", "nfz")


def _zip_file() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        entry = zipfile.ZipInfo("document.xml", date_time=(2026, 1, 1, 0, 0, 0))
        archive.writestr(entry, "<act />")
    return data.getvalue()


def _czs_transport(state: dict[str, bool]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/WersjeSlownikow/MainDictionary"):
            return httpx.Response(
                302,
                content=b"current MODTAR page",
                headers={
                    "location": "/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/7364"
                },
            )
        if request.url.path.endswith("/WersjeSlownikow/Index/7364"):

            def field(label: str, value: str) -> str:
                return f'<div class="filter-name hidden-sm"><strong>{label}: </strong></div><div class="filter-val">{value}</div>'

            info = (
                '<a href="/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/7364">dictionary</a>'
                + field("Kod słownika", "MODTAR")
                + field("Nr wersji", "202664")
                + field("Nr wersji technicznej", "102")
                + field("Data publikacji", "2026-09-24")
                + field("Obowiązuje od", "2026-09-24")
                + '<a href="/WersjeSlownikow/PobierzXml?nrGen=102&amp;kodSlownika=MODTAR&amp;idWersji=7364">XML</a>'
            )
            return httpx.Response(
                200, content=info.encode(), headers={"content-type": "text/html"}
            )
        code = "ICD9" if b'kod-slownika="ICD9"' in request.content else "MODTAR"
        generation = (
            "94"
            if code == "ICD9" and state.get("new_generation")
            else "93"
            if code == "ICD9"
            else "102"
        )
        timestamp = "2026-09-24T08:58:10" if code == "ICD9" else "2026-09-24T10:58:10"
        if request.method == "POST":
            selector = (
                f'kod-slownika="{code}" nr-gen="{generation}" data-gen="{timestamp}" fl-obow="T"'
                + (' rok="2026" typ-grupera="SZP"' if code == "MODTAR" else "")
            )
            soap = (
                '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"'
                ' xmlns:b="http://xml.kamsoft.pl/ws/broker"'
                ' xmlns:l="http://xml.kamsoft.pl/ws/common"'
                ' xmlns:d="https://czs.nfz.gov.pl/ws/broker/nfz/xml/czs/get-list-slowniki-response/v1.0">'
                "<soap:Body><b:executeServiceReturn><l:location><l:namespace>https://nfz.gov.pl/ws/broker/nfz/cen/czs</l:namespace><l:localname>getListSlowniki</l:localname>"
                "<l:version>1.0</l:version></l:location><b:payload><b:textload><d:komunikat>"
                f"<d:wyniki><d:wersja-slownika {selector}/></d:wyniki>"
                "</d:komunikat></b:textload></b:payload></b:executeServiceReturn></soap:Body></soap:Envelope>"
            ).encode()
            soap_body = (
                b"--boundary\r\nContent-Type: application/xop+xml\r\n\r\n"
                + soap
                + b"\r\n--boundary--\r\n"
            )
            return httpx.Response(
                200,
                content=soap_body,
                headers={"content-type": "multipart/related; boundary=boundary"},
            )
        if request.method == "GET":
            code = request.url.params["kodSlownika"]
            if code == "MODTAR" and state.get("no_content"):
                return httpx.Response(
                    200, content=b"", headers={"XML_STATUS": "NO_CONTENT"}
                )
            generation = (
                "94"
                if code == "ICD9" and state.get("new_generation")
                else "93"
                if code == "ICD9"
                else "102"
            )
            assert request.url.params["nrGen"] == generation
            timestamp = (
                "2026-09-24T08:58:10" if code == "ICD9" else "2026-09-24T10:58:10"
            )
            xml_body = (
                f'<komunikat typ="ICD9" nr_gen="{generation}" symbol="5.90" czas_gen="{timestamp}"><pozycja/>{"<pozycja/>" if state.get("changed_icd9") else ""}</komunikat>'
                if code == "ICD9"
                else f'<komunikat typ="CZS-PUB" kod_slow="MODTAR" rok="2026" nr-gen="102" data_gen="{timestamp}"><gruper typ_grupera="SZP"><modyfikatory/></gruper></komunikat>'
            )
            return httpx.Response(
                200, content=xml_body.encode(), headers={"content-type": "text/xml"}
            )
        raise AssertionError("unsupported CZS request")

    return httpx.MockTransport(handler)


def _aotmit_transport() -> httpx.MockTransport:
    listing = (
        b'<div class="blog-item"><div class="item-content"><dd class="published">'
        b'<time datetime="2026-07-02T12:41:34+02:00">2026</time></dd>'
        b'<div class="introtext"><p><strong>Obwieszczenie leczenia szpitalnego</strong></p>'
        b'<a href="/assets/files/taryfikacja/taryfy/obwieszczenia/2026/szpital.pdf">PDF</a>'
        b"</div></div></div>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("860-taryfikacja-obwieszczenia"):
            return httpx.Response(
                200, content=listing, headers={"content-type": "text/html"}
            )
        if request.url.path.endswith("/2026/szpital.pdf"):
            return httpx.Response(
                200,
                content=b"%PDF-1.4\nfixture\n%%EOF",
                headers={"content-type": "application/pdf"},
            )
        raise AssertionError("unexpected AOTMiT request")

    return httpx.MockTransport(handler)


def _rsk_icd9_transport() -> httpx.MockTransport:
    payload = cast(
        dict[str, object],
        json.loads(
            (
                Path(__file__).parents[1] / "fixtures/rsk/basic-info-00CD10.json"
            ).read_bytes()
        ),
    )
    payload["hcd"] = "00CD09"
    files = cast(list[dict[str, object]], payload["files"])
    files[0]["fileName"] = "icd-9_pl_cm_v_5.87"
    files[0]["extension"] = "xlsx"
    body = json.dumps(payload).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/rsk/resource/basic-info/00CD09"
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={
                "content-type": "application/json",
                "content-length": str(len(body)),
            },
        )

    return httpx.MockTransport(handler)


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
    nfz_transport: httpx.BaseTransport | None = None,
    czs_state: dict[str, bool] | None = None,
    independent_root_id: int | None = None,
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
                amendment=document_id in (43942, independent_root_id),
                generated_id=generated_id,
            )
            if nfz_transport is not None and document_id in (
                43942,
                independent_root_id,
            ):
                document = cast(dict[str, object], json.loads(body))
                document["PublishedDate"] = "2026-07-23T00:00:00"
                body = json.dumps(document).encode()
            if rotate and document_id == 43868 and read_count == 2:
                body = body.replace(b"2026-07-23", b"2026-07-24")
            return httpx.Response(
                200, content=body, headers={"content-type": "application/json"}
            )
        if "/GetAmendments/" in path:
            if independent_root_id is not None and path.endswith(
                f"/{independent_root_id}"
            ):
                independent_relations = json.dumps(
                    [
                        [
                            {
                                "Id": independent_root_id,
                                "AutomaticType": 1,
                                "IsProject": False,
                                "SourceDocumentId": None,
                                "IntroductoryDocumentId": None,
                            }
                        ]
                    ]
                ).encode()
                return httpx.Response(
                    200,
                    content=independent_relations,
                    headers={"content-type": "application/json"},
                )
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
                if file_id == 439420 or file_id == 450000
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
                if file_id == 439420 or file_id == 450000
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
    if nfz_transport is not None:
        _ = store.configure_source(SourceConfiguration("nfz", "nfz", {}, True))
    # Use the same registry contract as production with an in-memory HTTP boundary.
    collectors = CollectorRegistry()
    collectors.register(
        "baw",
        BawSourceCollector(
            BlobStagingArea(tmp_path / "staging", maximum_size_bytes=1024 * 1024),
            client_factory=client_factory,
        ),
    )
    if nfz_transport is not None:
        collectors.register(
            "nfz",
            NfzSourceCollector(
                BlobStagingArea(
                    tmp_path / "nfz-staging", maximum_size_bytes=1024 * 1024
                ),
                nfz_client_factory=lambda: create_nfz_client(transport=nfz_transport),
                baw_client_factory=client_factory,
                czs_client_factory=lambda: create_czs_client(
                    transport=_czs_transport(czs_state if czs_state is not None else {})
                ),
                aotmit_client_factory=lambda: create_aotmit_client(
                    transport=_aotmit_transport()
                ),
                rsk_client_factory=lambda settings: create_rsk_client(
                    settings, transport=_rsk_icd9_transport()
                ),
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


@pytest.mark.parametrize("document_id", [43942, 45000])
def test_nfz_order_dispatches_to_baw_graph_or_retained_root_once(
    tmp_path: Path, document_id: int
) -> None:
    url = (
        "https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
        "zarzadzenie-nr-742026dsoz,7850.html"
    )
    listing = (
        '<div class="news row collapse"><div>'
        f'<h3 class="title"><a href="{url.removeprefix("https://www.nfz.gov.pl")}">'
        "Zarządzenie nr 74/2026/DSOZ</a></h3>"
        '<div class="date">23.07.2026</div>'
        '<div class="desc">zmieniające zarządzenie w sprawie określenia warunków '
        "zawierania i realizacji umów w rodzaju leczenie szpitalne oraz leczenie "
        "szpitalne - świadczenia wysokospecjalistyczne</div></div></div>"
    ).encode()
    detail = (
        f'<a href="https://baw.nfz.gov.pl/NFZ/document/{document_id}/'
        'Zarzadzenie-74_2026_DSOZ">Zarządzenie nr 74/2026/DSOZ</a>'
    ).encode()
    revised_detail = False
    revised_jgp_file = False
    czs_state: dict[str, bool] = {}

    def card(path: str, title: str, summary: str) -> bytes:
        return (
            '<div class="news row collapse"><div><h3 class="title"><a href="'
            + path
            + '">'
            + title
            + '</a></h3><div class="date">29.07.2026</div>'
            + '<div class="desc">'
            + summary
            + "</div></div></div>"
        ).encode()

    news_prefix = "/aktualnosci/aktualnosci-centrali/"
    project_prefix = "/zarzadzenia-prezesa/projekty-zarzadzen/"
    jgp_prefix = "/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/"
    jgp_file_path = "/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/opis_algorytmu_grupera_2026.docx"
    icd9_file_path = (
        "/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/icd-9_pl_cm_v_5.90.xlsx"
    )
    icd9_page = (
        '<a class="files-link" href="' + icd9_file_path + '">ICD-9 5.90</a>'
    ).encode()
    jgp_cards = card(
        jgp_prefix + "informacja-dotyczaca-publikacji-opisu-algorytmu-jgp,54.html",
        "Opis algorytmu JGP",
        "Algorytm 1.1.2026",
    )
    jgp_detail = (
        '<ul class="files-list"><li class="files-item"><a class="files-link" href="'
        + jgp_file_path
        + '">opis_algorytmu_grupera_2026.docx</a></li></ul>'
    ).encode()

    def jgp_docx(*, changed: bool) -> bytes:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as document:
            document.writestr("[Content_Types].xml", "<Types/>")
            document.writestr(
                "word/document.xml",
                "<document>revised</document>" if changed else "<document/>",
            )
        return output.getvalue()

    news_cards = card(
        news_prefix + "komunikat-dla-swiadczeniodawcow,8987.html",
        "Komunikat JGP",
        "74/2026 DSOZ 1ae",
    ) + card(
        news_prefix + "opublikowano-nowa-wersje-slownika-icd-9,9020.html",
        "Nowa wersja ICD-9",
        "ICD-9 5.90",
    )
    project_cards = card(
        project_prefix + "projekt-zarzadzenia-leczenie-szpitalne,7007.html",
        "Projekt leczenie szpitalne",
        "leczenie szpitalne",
    )

    def nfz_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.nfz.gov.pl"
        path = request.url.path
        if "zarzadzenia-prezesa-nfz" in path:
            body = (
                listing
                if path.endswith("1,strona.html")
                else detail + (b"<!-- changed -->" if revised_detail else b"")
            )
        elif path.startswith(news_prefix):
            body = (
                news_cards
                if path == news_prefix
                else card(
                    news_prefix + "komunikat-dgl,9021.html",
                    "Komunikat DGL",
                    "Koszty DGL",
                )
                if path.endswith("2,strona.html")
                else b"74/2026 1ae JGP"
            )
        elif path.startswith(project_prefix):
            body = (
                project_cards
                if path == project_prefix
                else b"Projekt leczenie szpitalne"
            )
        elif path.startswith(jgp_prefix):
            body = jgp_cards if path == jgp_prefix else jgp_detail
        elif path == jgp_file_path:
            return httpx.Response(
                200,
                content=jgp_docx(changed=revised_jgp_file),
                headers={"content-type": "application/octetstream"},
            )
        elif path.endswith("/slowniki/pliki-icd-9-pl/"):
            body = icd9_page
        elif path == icd9_file_path:
            workbook = io.BytesIO()
            with zipfile.ZipFile(workbook, "w") as document:
                document.writestr("[Content_Types].xml", "<Types/>")
                document.writestr("xl/workbook.xml", "<workbook/>")
            return httpx.Response(
                200,
                content=workbook.getvalue(),
                headers={"content-type": "application/octetstream"},
            )
        else:
            raise AssertionError("unexpected NFZ route")
        return httpx.Response(200, content=body, headers={"content-type": "text/html"})

    store, orchestrator, _calls, database = _setup(
        tmp_path,
        nfz_transport=httpx.MockTransport(nfz_handler),
        czs_state=czs_state,
        independent_root_id=45000 if document_id == 45000 else None,
    )
    try:
        first = store.claim_manual("nfz")
        _ = orchestrator.run_claim(first, first.retry_policy)
        assert [item.document_id for item in store.list_pending_baw_dispatches()] == [
            document_id
        ]
        metadata = cast(
            dict[str, object],
            json.loads(
                cast(
                    str,
                    _query(
                        database,
                        "SELECT metadata_json FROM observations WHERE source_id = 'nfz' AND observation_kind = 'nfz_order_detail'",
                    )[0][0],
                )
            ),
        )
        assert metadata["response_headers"] == {
            "content-length": str(len(detail)),
            "content-type": "text/html",
        }
        assert _query(
            database,
            "SELECT change_category FROM source_changes WHERE change_category = 'nfz_hospital_order'",
        ) == [("nfz_hospital_order",)]
        assert _query(
            database,
            "SELECT count(*) FROM source_changes WHERE change_category = 'czs_dictionary'",
        ) == [(2,)]
        assert _query(
            database,
            "SELECT change_category,count(*) FROM source_changes WHERE source_id='nfz' AND change_category IN ('nfz_news','nfz_project_notice') GROUP BY change_category ORDER BY change_category",
        ) == [("nfz_news", 2), ("nfz_project_notice", 1)]
        assert _query(
            database,
            "SELECT count(*) FROM source_changes WHERE change_category = 'nfz_jgp_notice'",
        ) == [(1,)]
        assert _query(
            database,
            "SELECT count(*) FROM source_changes WHERE change_category = 'aotmit_tariff_signal'",
        ) == [(1,)]
        assert _query(
            database,
            "SELECT count(*) FROM incidents WHERE incident_category='czs_rsk_icd9_disagreement'",
        ) == [(1,)]

        baw = store.claim_manual("baw")
        _ = orchestrator.run_claim(baw, baw.retry_policy)
        assert store.list_pending_baw_dispatches() == ()
        assert _query(
            database,
            "SELECT delivered_run_id FROM baw_dispatch_outbox",
        ) == [(baw.identity.run_id,)]
        assert store.list_retained_baw_roots() == (
            (45000,) if document_id == 45000 else ()
        )
        repeat_baw = store.claim_manual("baw")
        _ = orchestrator.run_claim(repeat_baw, repeat_baw.retry_policy)
        assert store.list_retained_baw_roots() == (
            (45000,) if document_id == 45000 else ()
        )
        assert _query(
            database, "SELECT count(*) FROM source_changes WHERE source_id = 'baw'"
        ) == [(1,)]
        second = store.claim_manual("nfz")
        _ = orchestrator.run_claim(second, second.retry_policy)
        assert store.list_pending_baw_dispatches() == ()
        assert _query(database, "SELECT count(*) FROM baw_dispatch_outbox") == [(1,)]
        assert _query(
            database,
            "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'",
        ) == [(8,)]
        assert _query(
            database,
            "SELECT count(*) FROM observations WHERE source_id = 'nfz'",
        ) == [(38,)]

        revised_jgp_file = True
        changed_file = store.claim_manual("nfz")
        _ = orchestrator.run_claim(changed_file, changed_file.retry_policy)
        assert _query(
            database,
            "SELECT count(*) FROM incidents WHERE source_id = 'nfz' AND incident_category = 'nfz_jgp_file_changed'",
        ) == [(1,)]
        assert _query(
            database, "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'"
        ) == [(8,)]
        repeat_file = store.claim_manual("nfz")
        _ = orchestrator.run_claim(repeat_file, repeat_file.retry_policy)
        assert _query(
            database,
            "SELECT count(*) FROM incidents WHERE source_id = 'nfz' AND incident_category = 'nfz_jgp_file_changed'",
        ) == [(1,)]

        revised_detail = True
        third = store.claim_manual("nfz")
        _ = orchestrator.run_claim(third, third.retry_policy)
        assert _query(
            database,
            "SELECT incident_category FROM incidents WHERE source_id = 'nfz'",
        ) == [
            ("czs_rsk_icd9_disagreement",),
            ("nfz_jgp_file_changed",),
            ("nfz_detail_changed",),
        ]
        assert _query(database, "SELECT count(*) FROM baw_dispatch_outbox") == [(1,)]
        assert _query(
            database,
            "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'",
        ) == [(8,)]
        czs_state["changed_icd9"] = True
        fourth = store.claim_manual("nfz")
        _ = orchestrator.run_claim(fourth, fourth.retry_policy)
        assert _query(
            database,
            "SELECT incident_category FROM incidents WHERE source_id = 'nfz' ORDER BY incident_category",
        ) == [
            ("czs_dictionary_bytes_changed",),
            ("czs_rsk_icd9_disagreement",),
            ("nfz_detail_changed",),
            ("nfz_jgp_file_changed",),
        ]
        assert _query(
            database, "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'"
        ) == [(8,)]
        prior_cursor = store.load_source_state("nfz").cursor
        czs_state["no_content"] = True
        rejected = store.claim_manual("nfz")
        with pytest.raises(SourceDriftWatcherError):
            _ = orchestrator.run_claim(rejected, rejected.retry_policy)
        assert store.load_source_state("nfz").cursor == prior_cursor
        assert _query(
            database, "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'"
        ) == [(8,)]
        czs_state["no_content"] = False
        czs_state["new_generation"] = True
        advanced = store.claim_manual("nfz")
        _ = orchestrator.run_claim(advanced, advanced.retry_policy)
        assert _query(
            database, "SELECT count(*) FROM source_changes WHERE source_id = 'nfz'"
        ) == [(9,)]
        advanced_cursor = store.load_source_state("nfz").cursor
        czs_state["new_generation"] = False
        regressed = store.claim_manual("nfz")
        with pytest.raises(SourceDriftWatcherError):
            _ = orchestrator.run_claim(regressed, regressed.retry_policy)
        assert store.load_source_state("nfz").cursor == advanced_cursor
    finally:
        store.close()
    with ControlStore.open(
        database, source_configuration_codecs=WATCHER_SOURCE_CONFIGURATION_CODECS
    ) as reopened:
        assert reopened.list_retained_baw_roots() == (
            (45000,) if document_id == 45000 else ()
        )


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
        assert metadata["service_applicability_date"] == "2026-07-01"
        assert "section 4" in cast(str, metadata["service_applicability_evidence"])
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
        assert [
            item["document_id"]
            for item in public_documents
            if item["service_applicability_date"] is not None
        ] == [43942]
        reviewed = next(
            item for item in public_documents if item["document_id"] == 43942
        )
        assert reviewed["service_applicability_date"] == "2026-07-01"
        assert "section 4" in cast(str, reviewed["service_applicability_evidence"])
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
