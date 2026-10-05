"""Search/date/privacy contracts exercised through the real bundle and HTTP pool."""

import logging
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.ingest.search_synonyms import SearchSynonym
from tests.fixtures.jgp_catalog import catalog_workbook, default_sheets, stage_catalog
from tests.integration.test_jgp_api import dataset, lineage_bundle


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    bundle = lineage_bundle(tmp_path_factory.mktemp("group-search"))
    with TestClient(create_app(bundle)) as value:
        yield value


@pytest.mark.parametrize(
    ("query", "code", "kind", "catalog"),
    [
        ("h21", "H21", "exact_code", "1a"),
        ("AU35", "AU35C", "prefix_tokens", "1ae"),
        ("rekonstrukcja", "H21", "prefix_tokens", "1a"),
        ("rekonstrukcją", "H21", "morphology_tokens", "1a"),
        ("niedobory odpornosci", "SU52", "folded_tokens", "1ae"),
        ("zabiegi klatki", "PZD02", "prefix_tokens", "1a"),
    ],
)
def test_group_search_gold(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    code: str,
    kind: str,
    catalog: str,
) -> None:
    monkeypatch.setattr("minos.http.app.today_in_poland", lambda: date(2026, 10, 4))
    response = client.post("/api/v1/search", json={"system": "jgp", "query": query})
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    answer = cast(dict[str, object], response.json())
    first = cast(list[dict[str, object]], answer["results"])[0]
    ruleset = cast(dict[str, object], answer["ruleset"])
    assert (first["code"], first["catalog"], first["match_type"]) == (
        code,
        catalog,
        kind,
    )
    assert first["href"] == f"/jgp/2026-10-04/{code}"
    assert first["act_number"] == ruleset["act_number"]
    assert ruleset["release_id"] == "jgp-catalog-synthetic"
    assert answer["system"] == "jgp-szp"
    assert not {"points", "final_points"} & first.keys()


@pytest.mark.parametrize(
    ("day", "status", "count"),
    [
        (date(2026, 4, 30), "not_covered", 0),
        (date(2026, 6, 30), "ok", 0),
        (date(2026, 7, 1), "ok", 1),
        (date(2026, 12, 31), "ok", 1),
        (date(2027, 1, 1), "ok", 1),
    ],
)
def test_today_selects_one_valid_row_and_never_latest(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    day: date,
    status: str,
    count: int,
) -> None:
    monkeypatch.setattr("minos.http.app.today_in_poland", lambda: day)
    answer = cast(
        dict[str, object],
        client.post("/api/v1/search", json={"system": "jgp", "query": "H21"}).json(),
    )
    assert (answer["status"], len(cast(list[object], answer["results"]))) == (
        status,
        count,
    )
    assert answer["service_date"] == day.isoformat()


@pytest.mark.parametrize(
    "extra",
    [
        {"release_id": "jgp-catalog-original"},
        {"service_date": "2026-05-01"},
        {"limit": 21},
        {"language": "en"},
        {"catalog": "1ae"},
    ],
)
def test_home_search_refuses_client_catalog_overrides(
    client: TestClient, extra: dict[str, object]
) -> None:
    response = client.post(
        "/api/v1/search", json={"system": "jgp", "query": "H21", **extra}
    )
    assert response.status_code == 400


def test_group_query_is_body_only_and_absent_from_logs(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    query = "syntetyczne prywatne wyszukiwanie"
    with caplog.at_level(logging.INFO, logger="minos.http"):
        assert (
            client.post(
                "/api/v1/search", json={"system": "jgp", "query": query}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/api/v1/search?q=H21", json={"system": "jgp", "query": "H21"}
            ).status_code
            == 400
        )
    assert query not in caplog.text
    assert "H21" not in " ".join(
        record.message for record in caplog.records if record.name == "minos.http"
    )
    assert client.get("/api/v1/releases").json()["jgp_search"] is True


@pytest.mark.parametrize(("elapsed", "status"), [(0.3, 200), (0.6, 200), (2.001, 504)])
def test_group_search_allows_slow_results_but_keeps_a_total_deadline(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    elapsed: float,
    status: int,
) -> None:
    from minos.store.jgp_ruleset import resolve_ruleset

    clock = [1000.0]

    def timed_resolver(
        connection: sqlite3.Connection, day: date, known_on: date | None
    ) -> dict[str, object]:
        result = resolve_ruleset(connection, day, known_on)
        clock[0] += elapsed
        return result

    monkeypatch.setattr("minos.store.query_budget.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("minos.store.jgp_search.resolve_ruleset", timed_resolver)
    response = client.post("/api/v1/search", json={"system": "jgp", "query": "H21"})
    assert response.status_code == status
    if status == 504:
        assert response.json()["error"]["code"] == "query_timeout"


@pytest.mark.parametrize("reviewed", [False, True])
def test_reviewed_group_expansion_keeps_qualifiers_date_and_source_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    reviewed: bool,
) -> None:
    catalog = stage_catalog(tmp_path, catalog_workbook(default_sheets()))
    base = dataset(tmp_path, [catalog.model_dump(mode="json")])
    entry = SearchSynonym(
        id="synthetic-group-alias",
        version=1,
        system="jgp-szp",
        term="syntetyczny implant",
        source_wording="artroskopowa rekonstrukcja",
        source_url="https://example.org/synthetic-related-procedure",
        evidence="Synthetic test only, not medical equivalence.",
        relation="related_procedure",
        whole_query_only=True,
        status="reviewed" if reviewed else "proposed",
        reviewer="Synthetic reviewer" if reviewed else None,
        reviewed_on=date(2026, 10, 5) if reviewed else None,
        release_id=catalog.release_id,
    )
    manifest = DatasetManifest.model_validate(
        {
            **base.model_dump(),
            "search_profile": "polish-folded-1",
            "search_synonyms": (entry,),
        }
    )
    bundle = tmp_path / "expanded"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    monkeypatch.setattr("minos.http.app.today_in_poland", lambda: date(2026, 10, 4))
    with TestClient(create_app(bundle)) as client:
        answer = cast(
            dict[str, object],
            client.post(
                "/api/v1/search", json={"system": "jgp", "query": "syntetyczny implant"}
            ).json(),
        )
        if reviewed:
            hit = cast(list[dict[str, object]], answer["results"])[0]
            assert hit["code"] == "H21"
            assert hit["label"] == "Artroskopowa rekonstrukcja *"
            assert hit["match_type"] == "related_tokens"
            assert hit["expansion_relation"] == "related_procedure"
            assert hit["href"] == "/jgp/2026-10-04/H21"
        else:
            assert not answer["results"]
        for query in (
            "bez syntetyczny implant",
            "stan po syntetyczny implant",
            "syntetyczny implant kolana",
        ):
            assert not client.post(
                "/api/v1/search", json={"system": "jgp", "query": query}
            ).json()["results"]
        monkeypatch.setattr("minos.http.app.today_in_poland", lambda: date(2026, 6, 1))
        assert not client.post(
            "/api/v1/search", json={"system": "jgp", "query": "syntetyczny implant"}
        ).json()["results"]
