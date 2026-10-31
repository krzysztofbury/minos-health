import json
import re
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import MAX_IN_FLIGHT, create_app, render_code
from minos.http.pages import DICTIONARIES, code_page, recovery_page
from minos.ingest.reference_artifact import build_reference_artifact
from tests.integration.test_canonical_reference import canonical_manifest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    root = tmp_path_factory.mktemp("pages")
    bundle = root / "bundle"
    _ = build_reference_artifact(canonical_manifest(root), root, bundle)
    with TestClient(create_app(bundle)) as started:
        yield started


def structured_data(page: str) -> list[dict[str, object]]:
    blocks: list[str] = re.findall(
        r'<script type="application/ld\+json">(.*?)</script>', page
    )
    assert len(blocks) == 1
    return cast(list[dict[str, object]], json.loads(blocks[0]))


def test_code_page_is_crawlable_html_with_hierarchy_and_structured_data(
    client: TestClient,
) -> None:
    response = client.get("/slowniki/icd-10/E11.0")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "public, max-age=3600"
    page = response.text
    assert "<title>E11.0 Wariant zerowy - ICD-10 - Minos Health</title>" in page
    assert (
        '<meta name="twitter:title" content="E11.0 Wariant zerowy - ICD-10 - Minos Health">'
        in page
    )
    assert (
        '<meta property="og:image" content="https://minoshealth.pl/social-preview-v1.png">'
        in page
    )
    assert '<meta name="robots" content="index, follow">' in page
    assert 'href="https://minoshealth.pl/slowniki/icd-10/E11.0"' in page
    # Ancestors are real links, so crawlers can walk the hierarchy.
    assert 'href="/slowniki/icd-10/E00-E99"' in page
    assert 'href="/slowniki/icd-10/E11"' in page
    assert "Przykład opisowy" in page
    code, breadcrumb = structured_data(page)
    assert code["@type"] == "MedicalCode"
    assert code["codeValue"] == "E11.0"
    assert breadcrumb["@type"] == "BreadcrumbList"
    parent = client.get("/slowniki/icd-10/E11").text
    assert 'href="/slowniki/icd-10/E11.0"' in parent


def test_unplaced_icd9_code_has_a_canonical_page(client: TestClient) -> None:
    response = client.get("/slowniki/icd-9/00.00001")
    assert "Źródła: słownik ICD-9" in response.text
    assert "detail-source" not in response.text.split("Źródła: słownik ICD-9")[0]
    assert response.status_code == 200
    assert "Synthetic numeric procedure" in response.text
    assert "Obowiązuje od" in response.text


def test_code_urls_are_canonical_and_unknown_codes_are_real_404s(
    client: TestClient,
) -> None:
    redirect = client.get("/slowniki/icd-10/e11.0", follow_redirects=False)
    assert redirect.status_code == 301
    assert redirect.headers["location"] == "/slowniki/icd-10/E11.0"
    missing = client.get("/slowniki/icd-10/Z99.9")
    assert missing.status_code == 404
    assert '<meta name="robots" content="noindex, follow">' in missing.text
    assert missing.headers["cache-control"] == "no-store"
    assert client.get("/slowniki/icd-11/A01").status_code == 404
    assert client.get("/slowniki/icd-10/E11.0?q=cukrzyca").status_code == 400


def test_dictionary_sitemaps_list_every_code_page(client: TestClient) -> None:
    icd10 = client.get("/sitemap-icd-10.xml")
    assert icd10.status_code == 200
    assert icd10.headers["content-type"].startswith("application/xml")
    assert "<loc>https://minoshealth.pl/slowniki/icd-10/E11.0</loc>" in icd10.text
    icd9 = client.get("/sitemap-icd-9.xml").text
    assert "<loc>https://minoshealth.pl/slowniki/icd-9/A01</loc>" in icd9
    assert "<loc>https://minoshealth.pl/slowniki/icd-9/00.00001</loc>" in icd9
    assert client.get("/sitemap-icd-11.xml").status_code == 404


def test_source_text_is_escaped_in_markup_and_structured_data() -> None:
    hostile = '</script><img src=x onerror="alert(1)">'
    page = code_page(
        DICTIONARIES["icd-10"],
        {"code": "A01", "label": hostile, "attributes": [], "notes": [], "path": []},
        [],
        "013",
        supporting=False,
    )
    assert "<img" not in page
    assert hostile not in page
    assert structured_data(page)[0]["name"] == hostile


def test_a_shedding_api_answers_in_the_visitors_format_and_keeps_monitoring(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Uvicorn's own limit answers bare text, which visitors read as an outage (#296).
    monkeypatch.setattr("minos.http.app.MAX_IN_FLIGHT", 0)

    page = client.get("/slowniki/icd-10/E11.0")
    assert page.status_code == 429
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["retry-after"] == "1"
    assert '<script src="/retry.js" defer></script>' in page.text
    assert 'href="/slowniki/icd-10/E11.0"' in page.text

    api = client.get("/api/v1/releases")
    assert (api.status_code, api.json()["error"]["code"]) == (429, "busy")

    assert client.get("/readyz").status_code == 200
    assert client.get("/healthz").status_code == 200
    assert client.get("/sitemap-icd-10.xml").status_code == 200


def test_mcp_is_not_shed_at_the_door_and_reports_busy_from_its_tools(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tool spends a slot only for the API route it calls; counting the outer
    # call too let eight agents shut out the website.
    monkeypatch.setattr("minos.http.app.MAX_IN_FLIGHT", 0)
    answer = client.post(
        "/mcp",
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search_codes",
                "arguments": {"query": "cukrzyca", "systems": ["icd10-pl"]},
            },
        },
    )
    assert answer.status_code == 200
    systems = cast(
        list[dict[str, object]],
        answer.json()["result"]["structuredContent"]["systems"],
    )
    assert [item["status"] for item in systems] == ["BUSY"]


def test_a_shed_page_escapes_the_path_it_echoes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("minos.http.app.MAX_IN_FLIGHT", 0)
    page = client.get("/%22%3E%3Cscript%3Ealert(1)%3C/script%3E")
    assert page.status_code == 429
    assert "<script>alert(1)" not in page.text


def test_the_slot_returns_after_success_and_after_a_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A leaked slot would answer 429 to every visitor while health stays green.
    monkeypatch.setattr("minos.http.app.MAX_IN_FLIGHT", 1)
    entered, release = threading.Event(), threading.Event()
    real: Callable[..., tuple[int, str]] = render_code

    def held(*arguments: object) -> tuple[int, str]:
        entered.set()
        assert release.wait(5)
        return real(*arguments)

    monkeypatch.setattr("minos.http.app.render_code", held)
    with ThreadPoolExecutor(1) as pool:
        first = pool.submit(client.get, "/slowniki/icd-10/E11.0")
        assert entered.wait(5)
        assert client.get("/slowniki/icd-10/E11.0").status_code == 429
        release.set()
        assert first.result(5).status_code == 200
    monkeypatch.setattr("minos.http.app.render_code", real)
    assert client.get("/slowniki/icd-10/E11.0").status_code == 200

    def broken(*_arguments: object) -> tuple[int, str]:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr("minos.http.app.render_code", broken)
    assert client.get("/slowniki/icd-10/E11.0").status_code == 503
    monkeypatch.setattr("minos.http.app.render_code", real)
    assert client.get("/slowniki/icd-10/E11.0").status_code == 200


def test_only_a_busy_recovery_page_retries_by_itself() -> None:
    assert "/retry.js" in recovery_page("icd-10", "E11.0", "busy", "/x")
    assert "/retry.js" not in recovery_page("icd-10", "E11.0", "limit_exceeded", "/x")


def test_uvicorn_stays_a_backstop_above_the_guard() -> None:
    entrypoint = (ROOT / "packaging/api/Dockerfile").read_text()
    limit = re.search(r'"--limit-concurrency", "(\d+)"', entrypoint)
    assert limit is not None and int(limit[1]) > MAX_IN_FLIGHT
