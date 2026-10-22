import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.reference import ArtifactError
from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference import open_reference_connection, read_artifact_manifest
from tests.fixtures import icd11
from tests.fixtures.grouping_interface import stage_dictionaries, stage_product_catalogs
from tests.fixtures.komunikat import stage_komunikat
from tests.integration.test_jgp_condition_consumers import consumer_bundle

type Json = dict[str, object]


def icd11_bundle(root: Path) -> Path:
    """The full synthetic dataset with an ICD-11 PL release and the WHO maps."""
    base = read_artifact_manifest(consumer_bundle(root)).dataset
    notice = stage_komunikat(root, "icd9-czs-synthetic-v1")
    source = icd11.source(root)
    manifest = DatasetManifest.model_validate_json(
        json.dumps(
            {
                **base.model_dump(mode="json"),
                "sources": [*base.model_dump(mode="json")["sources"], source],
                "komunikaty": [notice.model_dump(mode="json")],
                "search_profile": "polish-folded-1",
                "grouping_dictionaries": stage_dictionaries(root),
                "product_catalogs": stage_product_catalogs(
                    root, base.catalogs[0].baw_document_id
                ),
                "icd11_maps": icd11.maps(root, cast(str, source["release_id"])),
            }
        )
    )
    output = root / "icd11-bundle"
    _ = build_reference_artifact(manifest, root, output)
    return output


@pytest.fixture(scope="module")
def bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return icd11_bundle(tmp_path_factory.mktemp("icd11"))


@pytest.fixture(scope="module")
def client(bundle: Path) -> Iterator[TestClient]:
    with TestClient(create_app(bundle)) as started:
        yield started


def _release(bundle: Path, system: str) -> str:
    dataset = read_artifact_manifest(bundle).dataset
    return next(s.release_id for s in dataset.sources if s.system == system)


def test_index_terms_and_inclusions_find_their_icd11_category(
    client: TestClient, bundle: Path
) -> None:
    release = _release(bundle, "icd11-pl")
    # An undated ICD-11 release is listed and answers dated questions too.
    listed = cast(Json, client.get("/api/v1/releases").json())
    assert release in [
        item["release_id"] for item in cast(list[Json], listed["releases"])
    ]
    dated = client.post(
        "/api/v1/search",
        json={
            "system": "icd11-pl",
            "release_id": release,
            "query": "1A00",
            "service_date": "2026-10-22",
        },
    )
    assert dated.status_code == 200
    found: dict[str, list[str]] = {}
    for query in ("termin indeksu", "włączenie", "1A01"):
        answer = cast(
            Json,
            client.post(
                "/api/v1/search",
                json={"system": "icd11-pl", "release_id": release, "query": query},
            ).json(),
        )
        found[query] = [
            cast(str, hit["code"]) for hit in cast(list[Json], answer["results"])
        ]
    assert found["termin indeksu"][0] == "1A00"
    assert found["włączenie"][0] == "1A00"
    assert found["1A01"][0] == "1A01"


def test_icd11_page_shows_uri_notice_and_who_map_but_no_jgp_context(
    client: TestClient,
) -> None:
    page = client.get("/slowniki/icd-11/1A00")

    assert page.status_code == 200
    text = page.text
    assert "Wersja przedostateczna ICD-11" in text
    assert f"{icd11.BASE}/3" in text
    assert "syntetyczny termin indeksu" in text
    # The 11-to-10 table links to the ICD-10 page with WHO's own title.
    assert '<a class="code-chip" href="/slowniki/icd-10/E11">E11</a>' in text
    assert "Syntetyczny tytuł WHO" in text
    assert "Występuje w grupach" not in text
    assert client.get("/slowniki/icd-11/1A00/jgp/2026-10-02").status_code == 404


def test_icd10_page_and_concept_lists_who_rows_verbatim(
    client: TestClient, bundle: Path
) -> None:
    page = client.get("/slowniki/icd-10/E11.0").text
    concept = cast(
        Json,
        client.get(
            f"/api/v1/concepts/icd10-pl/{_release(bundle, 'icd10-pl')}/E11.0"
        ).json(),
    )

    # A postcoordinated cluster keeps its published code and is not linked.
    assert '<span class="code-chip">1A00&amp;XN0</span> Synthetic / cluster' in page
    rows = cast(list[Json], cast(Json, concept["icd11_map"])["rows"])
    assert [(row["table"], row["source_row"], row["icd11_code"]) for row in rows] == [
        ("10to11_many", 3, "1A00&XN0"),
        ("10to11_one", 3, "1A00&XN0"),
    ]


def test_numeric_chapter_cells_keep_their_two_digit_code(bundle: Path) -> None:
    with open_reference_connection(bundle) as connection:
        rows = connection.execute(
            "SELECT icd11_code FROM icd11_map_row WHERE kind='11to10_one' ORDER BY source_row"
        ).fetchall()
    assert rows == [("1A00",), ("01",)]


def test_a_map_release_differing_from_the_manifest_is_refused(
    bundle: Path, tmp_path: Path
) -> None:
    copy = tmp_path / "copy"
    copy.mkdir()
    for name in ("manifest.json", "review.txt", "minos.db"):
        _ = (copy / name).write_bytes((bundle / name).read_bytes())
    with sqlite3.connect(copy / "minos.db") as connection:
        _ = connection.execute("UPDATE icd11_map_release SET manifest_json='{}'")
    manifest = cast(Json, json.loads((copy / "manifest.json").read_text()))
    import hashlib

    from minos.store.artifact import ArtifactManifest, canonical_json

    manifest["database_sha256"] = hashlib.sha256(
        (copy / "minos.db").read_bytes()
    ).hexdigest()
    manifest["database_size_bytes"] = (copy / "minos.db").stat().st_size
    _ = (copy / "manifest.json").write_bytes(
        canonical_json(ArtifactManifest.model_validate_json(json.dumps(manifest)))
    )

    with pytest.raises(ArtifactError, match="ICD-11 map binding"):
        with open_reference_connection(copy):
            pass
