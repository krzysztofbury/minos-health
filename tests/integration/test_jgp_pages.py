import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from minos.domain.jgp_ruleset import TimeZoneUnavailable
from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from tests.integration.test_jgp_api import lineage_bundle
from tests.integration.test_komunikat_reference import rank_manifest
from tests.unit.test_validation_edge_jgp_routes import PAGE_CORPUS, edge_page_pattern

NBSP = " "


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    bundle = lineage_bundle(tmp_path_factory.mktemp("jgp-pages"))
    with TestClient(create_app(bundle), follow_redirects=False) as test_client:
        yield test_client


def listed_codes(page: str) -> list[str]:
    return re.findall(
        r'<li data-search="[^"]*"><a class="tree-row jgp-row" href="/jgp/[^/]+/([A-Z0-9]+)"',
        page,
    )


@pytest.mark.parametrize(
    ("path", "location"),
    [("/jgp", "/jgp/2026-10-03"), ("/jgp/PZD02", "/jgp/2026-10-03/PZD02")],
)
def test_undated_pages_redirect_to_today_in_poland_without_caching(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, path: str, location: str
) -> None:
    monkeypatch.setattr("minos.http.jgp.today_in_poland", lambda: date(2026, 10, 3))
    response = client.get(path)
    assert (response.status_code, response.headers["location"]) == (302, location)
    assert response.headers["Cache-Control"] == "no-store"


def test_missing_time_zone_data_is_explicit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable() -> date:
        raise TimeZoneUnavailable("no tzdata")

    monkeypatch.setattr("minos.http.jgp.today_in_poland", unavailable)
    response = client.get("/jgp")
    assert response.status_code == 503
    assert "Nie można ustalić dzisiejszej daty" in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("path", "location"),
    [("/jgp/pzd02", "/jgp/PZD02"), ("/jgp/2026-07-10/pzd02", "/jgp/2026-07-10/PZD02")],
)
def test_lowercase_codes_move_permanently(
    client: TestClient, path: str, location: str
) -> None:
    response = client.get(path)
    assert (response.status_code, response.headers["location"]) == (301, location)


def test_catalog_lists_the_release_for_the_day_by_chapter(client: TestClient) -> None:
    response = client.get("/jgp/2026-07-10")
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("public")
    page = response.text
    assert listed_codes(page) == ["A01", "PZD02", "H21", "J01AJ", "AU35C", "SU52"]
    assert re.findall(r'id="k-(1ae?-\d)"', page) == ["1a-1", "1a-2", "1ae-1"]
    assert f"12{NBSP}871,72{NBSP}pkt" not in page  # list shows hospitalization only
    assert f"13{NBSP}991,00{NBSP}pkt" in page
    assert '<div class="tree-search jgp-filter" hidden>' in page  # shown by jgp.js
    assert "6 grup w 3 rozdziałach." in page
    assert (
        '<meta property="og:image" content="https://minoshealth.pl/social-preview-v2.png">'
        in page
    )
    assert '<meta name="twitter:card" content="summary_large_image">' in page
    assert 'href="/jgp/2026-07-10/zakres/15">chirurgia ogólna</a>' in page
    assert "?" not in "".join(re.findall(r'href="([^"]*)"', page))


def test_june_uses_the_original_act(client: TestClient) -> None:
    page = client.get("/jgp/2026-06-15").text
    assert listed_codes(page) == ["A01", "PZD02"]
    assert "43868" in page and "43943" not in page


@pytest.mark.parametrize(
    ("day", "codes"),
    [("2026-07-10", ["A01", "H21"]), ("2027-01-01", ["A01"])],
)
def test_scope_filter_follows_the_row_valid_on_the_day(
    client: TestClient, day: str, codes: list[str]
) -> None:
    # H21's chirurgia ogólna marker belongs to the twin that ends 2026-12-31.
    page = client.get(f"/jgp/{day}/zakres/15").text
    assert listed_codes(page) == codes
    assert '<span id="jgp-scope-value">chirurgia ogólna</span>' in page
    assert "pierwszy z zakresów wymienionych w nagłówku" in page
    assert f'href="/jgp/{day}/zakres/15" aria-current="page"' in page


def test_group_page_quotes_source_headers_and_keeps_absent_values(
    client: TestClient,
) -> None:
    response = client.get("/jgp/2026-07-10/PZD02")
    assert response.status_code == 200
    page = response.text
    assert (
        "<dt>Wartość punktowa - hospitalizacja planowa</dt>"
        + f'<dd class="jgp-value">12{NBSP}871,72{NBSP}pkt</dd>'
        in page
    )
    assert f'<dd class="jgp-value">21{NBSP}dni</dd>' in page
    assert "brak wartości w katalogu" in page
    assert "Punkty bazowe wg katalogu, bez modyfikatorów" in page
    assert "Grupa zabiegowa (*)" in page
    assert 'href="/jgp/2026-07-10#k-1a-1"' in page
    # The day before is governed by the original act, which has PZD02 too.
    assert 'href="/jgp/2026-06-30/PZD02">Zobacz 30.06.2026' in page
    # Quick dates come from the bundle: release starts and the 2027 row switch.
    assert re.findall(
        r'<a class="dictionary-tab" href="/jgp/([0-9-]+)/PZD02">([0-9.]+)</a>', page
    ) == [
        ("2026-05-01", "01.05.2026"),
        ("2026-07-01", "01.07.2026"),
        ("2027-01-01", "01.01.2027"),
    ]
    assert 'min="2026-05-01"' in page


def test_group_page_links_across_the_2027_row_switch(client: TestClient) -> None:
    before = client.get("/jgp/2026-12-31/H21").text
    after = client.get("/jgp/2027-01-01/H21").text
    assert 'href="/jgp/2027-01-01/H21">Zobacz od 01.01.2027' in before
    assert "do 31.12.2026" in before and "chirurgia ogólna" in before
    assert "Synthetic § 4 ust. 5." in after
    assert 'href="/jgp/2026-12-31/H21">Zobacz 31.12.2026' in after
    # The original act has no H21, so June gets no link: only verified days.
    july = client.get("/jgp/2026-07-10/H21").text
    assert "Zobacz 30.06.2026" not in july


@pytest.mark.parametrize(
    ("path", "status", "text"),
    [
        ("/jgp/2026-04-30", 404, "Brak zweryfikowanego katalogu JGP dla tego dnia"),
        ("/jgp/2026-04-30/PZD02", 404, 'data-suffix="/PZD02"'),
        ("/jgp/2026-06-15/H21", 404, "Tej grupy nie ma w katalogu na ten dzień"),
        ("/jgp/2026-07-10/zakres/69", 404, "Nie ma takiego zakresu świadczeń"),
        ("/jgp/2026-02-30", 404, "Nie znaleziono strony"),
        ("/jgp/2026-07-10/zakres/0", 404, "Nie znaleziono strony"),
        ("/jgp/2026-07-10?x=1", 400, ""),
    ],
)  # fmt: skip
def test_gaps_render_explicit_uncached_errors(
    client: TestClient, path: str, status: int, text: str
) -> None:
    response = client.get(path)
    assert response.status_code == status
    assert text in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_pages_are_read_only(client: TestClient) -> None:
    assert client.post("/jgp/2026-07-10/PZD02").status_code == 405


def test_bundles_without_catalogs_have_no_jgp_pages(tmp_path: Path) -> None:
    _ = build_reference_artifact(rank_manifest(tmp_path), tmp_path, tmp_path / "bundle")
    with TestClient(create_app(tmp_path / "bundle")) as client:
        response = client.get("/jgp/2026-07-10/PZD02")
    assert response.status_code == 404
    assert "Nie znaleziono strony" in response.text


@pytest.mark.parametrize("path", PAGE_CORPUS)
def test_app_routing_agrees_with_the_edge_allowlist(
    client: TestClient, path: str
) -> None:
    response = client.get(path)
    if edge_page_pattern().search(path):
        # Accepted paths always reach a page handler: HTML or a redirect.
        assert response.status_code in (200, 301, 302, 404)
        if response.status_code not in (301, 302):
            assert response.headers["content-type"].startswith("text/html")
    else:
        assert response.status_code != 200
