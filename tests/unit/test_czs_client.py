import httpx
import pytest

from minos.ingest.watchers.czs.client import (
    CzsSourceError,
    CzsVersion,
    create_czs_client,
    discover_version,
    fetch_pinned_xml,
)
from minos.ingest.watchers.czs.display import parse_display_page


def test_pinned_modtar_rejects_wrong_grouper_and_no_content() -> None:
    version = CzsVersion("MODTAR", 102, "2026-09-24T10:58:10", 2026, "SZP")
    incorrect = (
        b'<komunikat typ="CZS-PUB" kod_slow="MODTAR" rok="2026" nr-gen="102" '
        b'data_gen="2026-09-24T10:58:10"><gruper typ_grupera="AOS">'
        b"<modyfikatory/></gruper></komunikat>"
    )

    def wrong_grouper(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=incorrect, headers={"content-type": "text/xml"}
        )

    with create_czs_client(transport=httpx.MockTransport(wrong_grouper)) as client:
        with pytest.raises(CzsSourceError, match="grouper"):
            _ = fetch_pinned_xml(client, version)

    def no_content(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=incorrect,
            headers={"content-type": "text/xml", "XML_STATUS": "NO_CONTENT"},
        )

    with create_czs_client(transport=httpx.MockTransport(no_content)) as client:
        with pytest.raises(CzsSourceError, match="NO_CONTENT"):
            _ = fetch_pinned_xml(client, version)


def test_pinned_icd9_rejects_html_truncation_and_wrong_generation() -> None:
    version = CzsVersion("ICD9", 93, "2026-09-24T08:58:10", None, None)
    wrong = b'<komunikat typ="ICD9" symbol="5.90" nr_gen="92" czas_gen="2026-09-24T08:58:10"><pozycja/></komunikat>'
    for body, media, expected in (
        (b"<html>challenge</html>", "text/html", "not XML"),
        (b"<komunikat", "text/xml", "invalid"),
        (wrong, "text/xml", "version"),
    ):
        with create_czs_client(
            transport=httpx.MockTransport(
                lambda _request, b=body, m=media: httpx.Response(
                    200, content=b, headers={"content-type": m}
                )
            )
        ) as client:
            with pytest.raises(CzsSourceError, match=expected):
                _ = fetch_pinned_xml(client, version)


def test_soap_failure_does_not_supply_version() -> None:
    with create_czs_client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=b"<html>unexpected</html>",
                headers={"content-type": "text/html"},
            )
        )
    ) as client:
        with pytest.raises(CzsSourceError, match="SOAP multipart"):
            _ = discover_version(client, "ICD9")


def test_czs_rejects_wrong_host_without_network_call() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("network call should be blocked")

    with create_czs_client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CzsSourceError, match="allowlisted"):
            _ = client.get("https://example.net/unsafe")


def test_modtar_display_requires_explicit_link_to_pinned_generation() -> None:
    version = CzsVersion("MODTAR", 102, "2026-09-24T10:58:10", 2026, "SZP")

    def page(generation: str, *, xml_link: bool = True) -> bytes:
        fields = {
            "Kod słownika": "MODTAR",
            "Data publikacji": "2026-09-24",
            "Nr wersji": "202664",
            "Nr wersji technicznej": generation,
            "Obowiązuje od": "2026-09-24",
        }
        html = '<a href="/ModyfikatoryTaryfyJgp/SlownikModyfikatorowTaryfJgp/7364">current</a>'
        for label, value in fields.items():
            html += (
                '<div class="filter-name hidden-sm"><strong>'
                + label
                + ': </strong></div><div class="filter-val">'
                + value
                + "</div>"
            )
        if xml_link:
            html += '<a href="/WersjeSlownikow/PobierzXml?nrGen=102&amp;kodSlownika=MODTAR&amp;idWersji=7364">XML</a>'
        return html.encode()

    display = parse_display_page(page("102"), 7364, version)
    assert (display.label, display.technical_generation, display.effective_from) == (
        "202664",
        102,
        "2026-09-24",
    )
    with pytest.raises(CzsSourceError, match="SOAP generation"):
        _ = parse_display_page(page("100"), 7364, version)
    with pytest.raises(CzsSourceError, match="pinned XML"):
        _ = parse_display_page(page("102", xml_link=False), 7364, version)
