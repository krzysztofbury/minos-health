import httpx
import pytest

from minos.ingest.watchers.baw.models import BawResponse
from minos.ingest.watchers.nfz.discovery import (
    NfzListingError,
    create_nfz_client,
    fetch_hospital_listing,
    fetch_order_detail,
    parse_hospital_listing,
    resolve_baw_document_id,
    verify_baw_candidate,
)


def _item(number: str, path: str, subject: str) -> str:
    return (
        '<div class="news row collapse"><div><div>'
        f'<h3 class="title"><a href="{path}">Zarządzenie nr {number}</a></h3>'
        '<div class="date">23.09.2026</div>'
        f'<div class="desc">{subject}</div>'
        "</div></div></div>"
    )


_HOSPITAL = (
    "zmieniające zarządzenie w sprawie określenia warunków zawierania "
    "i realizacji umów w rodzaju leczenie szpitalne oraz leczenie szpitalne "
    "- świadczenia wysokospecjalistyczne"
)


def test_listing_filters_exact_hospital_subject_and_non_dsoz_orders() -> None:
    html = "".join(
        (
            _item(
                "104/2026/DSOZ",
                "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1042026dsoz,7984.html",
                "zmieniające zarządzenie w rodzaju leczenie szpitalne - świadczenia kompleksowe",
            ),
            _item(
                "102/2026/DSOZ",
                "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
                _HOSPITAL,
            ),
            _item(
                "97/2026/DGL",
                "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-972026dgl,7976.html",
                _HOSPITAL,
            ),
        )
    )
    candidates = parse_hospital_listing(html.encode())
    assert [
        (item.act_number, item.listing_id, item.published_date) for item in candidates
    ] == [("102/2026/DSOZ", 7982, "2026-09-23")]
    assert candidates[0].url == (
        "https://www.nfz.gov.pl/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/"
        "zarzadzenie-nr-1022026dsoz,7982.html"
    )


@pytest.mark.parametrize(
    "html",
    [
        "<html><body>Brak wyników</body></html>",
        _item(
            "102/2026/DSOZ",
            "https://other.example/zarzadzenie-nr-1022026dsoz,7982.html",
            _HOSPITAL,
        ),
        _item(
            "102/2026/DSOZ",
            "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-9992026dsoz,7982.html",
            _HOSPITAL,
        ),
        _item(
            "102/2026/DSOZ",
            "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
            _HOSPITAL,
        ).replace("23.09.2026", "31.02.2026"),
    ],
)
def test_listing_rejects_empty_drifted_or_conflicting_source(html: str) -> None:
    with pytest.raises(NfzListingError):
        _ = parse_hospital_listing(html.encode())


def test_listing_rejects_duplicate_id_and_oversized_body() -> None:
    item = _item(
        "102/2026/DSOZ",
        "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
        _HOSPITAL,
    )
    with pytest.raises(NfzListingError):
        _ = parse_hospital_listing((item + item).encode())
    with pytest.raises(NfzListingError):
        _ = parse_hospital_listing(b"x" * (256 * 1024 + 1))


def test_fetch_listing_rejects_html_challenge_and_external_redirect() -> None:
    def challenge(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"Content-Type": "text/html"}, text="<html>challenge</html>"
        )

    with httpx.Client(transport=httpx.MockTransport(challenge)) as client:
        with pytest.raises(NfzListingError):
            _ = fetch_hospital_listing(client)

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example"})

    with httpx.Client(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(NfzListingError):
            _ = fetch_hospital_listing(client)


def test_fetch_listing_returns_exact_bytes_and_candidates() -> None:
    html = _item(
        "102/2026/DSOZ",
        "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
        _HOSPITAL,
    ).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.nfz.gov.pl"
        assert request.url.path.endswith("/1,strona.html")
        assert request.url.params.get("szukaj") == "szpitaln"
        return httpx.Response(
            200, headers={"Content-Type": "text/html; charset=utf-8"}, content=html
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        body, candidates = fetch_hospital_listing(client)
    assert body == html
    assert [item.act_number for item in candidates] == ["102/2026/DSOZ"]


def test_nfz_client_never_replays_publisher_cookies() -> None:
    html = _item(
        "102/2026/DSOZ",
        "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
        _HOSPITAL,
    ).encode()
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Set-Cookie": "CMS=session-value"},
            content=html,
        )

    with create_nfz_client(transport=httpx.MockTransport(respond)) as client:
        _ = fetch_hospital_listing(client)
        _ = fetch_hospital_listing(client)
    assert len(requests) == 2
    assert all("cookie" not in request.headers for request in requests)


def test_listing_rejects_ambiguous_link_attributes() -> None:
    item = _item(
        "102/2026/DSOZ",
        "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
        _HOSPITAL,
    )
    ambiguous = item.replace(
        '<a href="/zarzadzenia-prezesa/',
        '<a href="https://attacker.example" href="/zarzadzenia-prezesa/',
    )
    with pytest.raises(NfzListingError):
        _ = parse_hospital_listing(ambiguous.encode())


def test_detail_resolves_only_the_matching_canonical_baw_document() -> None:
    candidate = parse_hospital_listing(
        _item(
            "102/2026/DSOZ",
            "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
            _HOSPITAL,
        ).encode()
    )[0]
    page = (
        '<a href="https://baw.nfz.gov.pl/NFZ/tabBrowser/mainPage">BAW</a>'
        '<a href="https://baw.nfz.gov.pl/NFZ/document/44041/Zarzadzenie-102_2026_DSOZ">'
        "Zarządzenie Prezesa NFZ nr 102/2026/DSOZ</a>"
    ).encode()
    assert resolve_baw_document_id(page, candidate) == 44041
    with pytest.raises(NfzListingError):
        _ = resolve_baw_document_id(page + page, candidate)
    with pytest.raises(NfzListingError):
        _ = resolve_baw_document_id(page.replace(b"44041", b"0"), candidate)
    with pytest.raises(NfzListingError):
        _ = resolve_baw_document_id(
            page.replace(b"102_2026_DSOZ", b"104_2026_DSOZ"), candidate
        )
    with pytest.raises(NfzListingError):
        _ = resolve_baw_document_id(
            page.replace(b"/44041/Zarzadzenie-102_2026_DSOZ", b"/44041"),
            candidate,
        )


def test_candidate_requires_matching_issued_baw_metadata() -> None:
    import json

    candidate = parse_hospital_listing(
        _item(
            "102/2026/DSOZ",
            "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
            _HOSPITAL,
        ).encode()
    )[0]
    metadata: dict[str, object] = {
        "Id": 44041,
        "InstitutionId": 4,
        "IsPrivate": False,
        "IsRemoved": False,
        "WasEverPublished": True,
        "ActNumber": "102/2026/DSOZ",
        "LegalActTypeDescription": "Zarządzenie",
        "PublishedDate": "2026-09-23T00:00:00",
        "LegalActStatus": {"Name": "Obowiązujący"},
        "Files": [],
    }

    def response() -> BawResponse:
        return BawResponse(json.dumps(metadata).encode(), "application/json")

    assert verify_baw_candidate(response(), candidate, 44041).Id == 44041
    for field, wrong in (
        ("ActNumber", "104/2026/DSOZ"),
        ("PublishedDate", "2026-09-24T00:00:00"),
        ("LegalActTypeDescription", "Projekt"),
        ("WasEverPublished", False),
    ):
        metadata[field] = wrong
        with pytest.raises(NfzListingError):
            _ = verify_baw_candidate(response(), candidate, 44041)
        metadata[field] = {
            "ActNumber": "102/2026/DSOZ",
            "PublishedDate": "2026-09-23T00:00:00",
            "LegalActTypeDescription": "Zarządzenie",
            "WasEverPublished": True,
        }[field]


def test_detail_fetch_preserves_bytes_and_rejects_redirects() -> None:
    candidate = parse_hospital_listing(
        _item(
            "102/2026/DSOZ",
            "/zarzadzenia-prezesa/zarzadzenia-prezesa-nfz/zarzadzenie-nr-1022026dsoz,7982.html",
            _HOSPITAL,
        ).encode()
    )[0]
    body = (
        '<a href="https://baw.nfz.gov.pl/NFZ/document/44041/'
        'Zarzadzenie-102_2026_DSOZ">Zarządzenie 102/2026/DSOZ</a>'
    ).encode()

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == candidate.url
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=body)

    with create_nfz_client(transport=httpx.MockTransport(respond)) as client:
        observed, document_id = fetch_order_detail(client, candidate)
    assert observed == body
    assert document_id == 44041

    def redirect(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "https://attacker.example"})

    with create_nfz_client(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(NfzListingError):
            _ = fetch_order_detail(client, candidate)
