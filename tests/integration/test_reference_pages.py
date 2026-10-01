import json
import re
from collections.abc import Iterator
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from minos.http.pages import DICTIONARIES, code_page
from minos.ingest.reference_artifact import build_reference_artifact
from tests.integration.test_canonical_reference import canonical_manifest


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
