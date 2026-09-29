import httpx
import pytest

from minos.ingest.watchers.nfz.discovery import NfzListingError
from minos.ingest.watchers.nfz.jgp import fetch_jgp_file, parse_jgp_files
from minos.ingest.watchers.nfz.signals import parse_signal_listing


def test_general_news_detects_icd9_without_jgp_notice() -> None:
    url = "/aktualnosci/aktualnosci-centrali/opublikowano-nowa-wersje-icd-9,9020.html"
    html = (
        '<div class="news row collapse"><div><h3 class="title"><a href="'
        + url
        + '">ICD-9 PL 5.90</a></h3><div class="date">25.09.2026</div>'
        + '<div class="desc">Nowy słownik ICD-9</div></div></div>'
    ).encode()
    items = parse_signal_listing(html, "news")
    assert [(item.item_id, item.channel) for item in items] == [(9020, "news")]


def test_signal_rejects_duplicate_id_with_conflicting_cards() -> None:
    def card(slug: str) -> bytes:
        return (
            '<div class="news row collapse"><div><h3 class="title"><a href="'
            + "/aktualnosci/aktualnosci-centrali/"
            + slug
            + ',9020.html">ICD-9</a></h3><div class="date">25.09.2026</div>'
            + '<div class="desc">ICD-9 PL</div></div></div>'
        ).encode()

    with pytest.raises(NfzListingError, match="conflicting cards"):
        _ = parse_signal_listing(card("release-a") + card("release-b"), "news")


def test_jgp_files_reject_wrong_notice_duplicate_and_empty() -> None:
    path = "/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/opis_algorytmu_2026.docx"

    def linked(url: str) -> bytes:
        return (
            '<ul class="files-list"><li><a class="files-link" href="'
            + url
            + '">file</a></li></ul>'
        ).encode()

    assert parse_jgp_files(linked(path), 54) == ("https://www.nfz.gov.pl" + path,)
    for body in (
        linked(path.replace("/54/", "/53/")),
        linked(path) + linked(path),
        b"<html/>",
    ):
        with pytest.raises(NfzListingError):
            _ = parse_jgp_files(body, 54)


@pytest.mark.parametrize("body", [b"<html>challenge</html>", b"PK\x03\x04truncated"])
def test_jgp_file_rejects_invalid_office_archive(body: bytes) -> None:
    url = "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/opis_algorytmu_2026.docx"
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                content=body,
                headers={"content-type": "application/octetstream"},
            )
        ),
        follow_redirects=False,
    ) as client:
        with pytest.raises(NfzListingError, match="Office ZIP"):
            _ = fetch_jgp_file(client, url, 54)
