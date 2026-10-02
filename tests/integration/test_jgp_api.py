import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from tests.fixtures.jgp_catalog import (
    Row,
    catalog_workbook,
    default_sheets,
    stage_catalog,
)
from tests.integration.test_komunikat_reference import rank_manifest

# The original act's PZD02 before 74/2026 changed its planned value.
ORIGINAL_ROWS = (
    Row(1, "A01", "5.51.01.0001001", "Zabiegi wewnątrzczaszkowe *", {"E": 13586}),
    Row(2, "PZD02", "5.51.01.0018028", "Duże zabiegi klatki piersiowej *",
        {"E": 8739, "F": 8040, "H": 21}),
)  # fmt: skip
# A second 1a chapter starting at H21, so section positions are exercised.
HEADINGS = {"nfz-1a-74-2026": {2: "Choroby narządu ruchu"}}
type Json = dict[str, object]


def dataset(root: Path, catalogs: list[Json]) -> DatasetManifest:
    data = rank_manifest(root).model_dump(mode="json")
    data["catalogs"] = catalogs
    data["catalog_knowledge_as_of"] = "2026-10-02"
    data["catalog_knowledge_evidence"] = "Synthetic lineage review on 2026-10-02."
    return DatasetManifest.model_validate_json(json.dumps(data))


def original_release(root: Path, **changes: object) -> Json:
    return stage_catalog(
        root,
        catalog_workbook({"nfz-1a-46-2026": ORIGINAL_ROWS}),
        sheets={"nfz-1a-46-2026": ORIGINAL_ROWS},
        release={
            "release_id": "jgp-catalog-original",
            "baw_document_id": 43868,
            "automatic_type": 1,
            "source_document_id": None,
            "introductory_document_id": None,
            "act_date": date(2026, 4, 30),
            "entry_into_force": date(2026, 5, 1),
            "published_on": date(2026, 4, 30),
            "service_from": date(2026, 5, 1),
            "service_evidence": "Synthetic original act from 2026-05-01.",
            "document_url": "https://baw.nfz.gov.pl/NFZ/document/43868/Synthetic",
            "row_applicability": (),
            **changes,
        },
    ).model_dump(mode="json")


def lineage_bundle(root: Path) -> Path:
    """Original act plus a consolidation that supersedes it from 2026-07-01."""
    amended = stage_catalog(
        root,
        catalog_workbook(default_sheets(), headings=HEADINGS),
        release={"supersedes": "jgp-catalog-original"},
        headings=HEADINGS,
    )
    manifest = dataset(root, [original_release(root), amended.model_dump(mode="json")])
    _ = build_reference_artifact(manifest, root, root / "bundle")
    return root / "bundle"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    bundle = lineage_bundle(tmp_path_factory.mktemp("jgp-api"))
    with TestClient(create_app(bundle)) as test_client:
        yield test_client


def get(client: TestClient, path: str) -> tuple[int, Json]:
    response = client.get(path)
    return response.status_code, cast(Json, response.json())


def values(body: Json) -> dict[str, tuple[int, str]]:
    return {
        cast(str, value["measure"]): (
            cast(int, value["amount"]),
            cast(str, value["display"]),
        )
        for value in cast(list[Json], body["base_values"])
    }


def test_group_page_data_and_provenance_come_from_one_call(client: TestClient) -> None:
    response = client.get("/api/v1/jgp/2026-07-10/groups/PZD02")
    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("public")
    body = cast(Json, response.json())
    ruleset = cast(Json, body["ruleset"])
    group = cast(Json, body["group"])
    assert ruleset["service_date"] == "2026-07-10"
    assert ruleset["release_id"] == "jgp-catalog-synthetic"
    assert ruleset["knowledge_as_of"] == ruleset["known_on"] == "2026-10-02"
    assert cast(Json, group["applicability"])["service_from"] == ruleset["service_from"]
    assert group["product_code"] == "5.51.01.0018028"
    assert values(body)["planned_hospitalization"] == (1287172, "12871.72")
    assert values(body)["financed_stay"] == (21, "21")
    assert body["points_kind"] == "catalog_base_points_without_modifiers"
    assert body["points_label"] == "punkty bazowe wg katalogu, bez modyfikatorów"
    assert cast(Json, ruleset["modifiers"])["binding"] == "unbound"
    assert not {"points", "final_points", "tariff"} & set(body)


def test_service_date_selects_the_release_and_the_2027_row(client: TestClient) -> None:
    # Retroactive 2026-07-01 start: June uses the original act, July the amendment.
    june = get(client, "/api/v1/jgp/2026-06-15/groups/PZD02")[1]
    assert cast(Json, june["ruleset"])["release_id"] == "jgp-catalog-original"
    assert values(june)["planned_hospitalization"] == (804000, "8040.00")
    rows = {
        day: cast(Json, get(client, f"/api/v1/jgp/{day}/groups/H21")[1]["group"])
        for day in ("2026-12-31", "2027-01-01")
    }
    assert (rows["2026-12-31"]["ordinal"], rows["2027-01-01"]["ordinal"]) == (3, 4)
    assert cast(Json, rows["2027-01-01"]["applicability"])["evidence"] == (
        "Synthetic § 4 ust. 5."
    )


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/api/v1/jgp/2026-04-30/groups/PZD02", 404, "no_ruleset_for_date"),
        ("/api/v1/jgp/2026-06-15/groups/H21", 404, "group_not_on_date"),
        ("/api/v1/jgp/2026-02-30/groups/PZD02", 404, "not_found"),
        ("/api/v1/jgp/2026-07-10/groups/pzd02", 404, "not_found"),
        ("/api/v1/jgp/2026-07-10/sections/1a/99", 404, "section_not_on_date"),
        ("/api/v1/jgp/2026-07-10/sections/2a/1", 404, "not_found"),
        ("/api/v1/jgp/2026-07-10/groups/PZD02?date=2026-07-10", 400, "queries_must_not_be_in_urls"),
    ],
)  # fmt: skip
def test_gaps_fail_explicitly_instead_of_using_another_release(
    client: TestClient, path: str, status: int, code: str
) -> None:
    response = client.get(path)
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    assert response.headers["Cache-Control"] == "no-store"


def test_jgp_routes_are_read_only(client: TestClient) -> None:
    assert client.post("/api/v1/jgp/2026-07-10/groups/PZD02").status_code == 405
    assert client.head("/api/v1/jgp/2026-07-10/sections").status_code == 405


def test_sections_keep_their_source_position_on_every_date(client: TestClient) -> None:
    for day in ("2026-07-10", "2027-01-01"):
        status, body = get(client, f"/api/v1/jgp/{day}/sections")
        assert status == 200
        assert body["sections"] == [
            {"catalog": "1a", "position": 1, "chapter": "Choroby układu nerwowego", "groups": 2},
            {"catalog": "1a", "position": 2, "chapter": "Choroby narządu ruchu", "groups": 2},
            {"catalog": "1ae", "position": 1, "chapter": "Choroby układu nerwowego", "groups": 2},
        ]  # one H21 twin per date, never both  # fmt: skip
        listed = cast(
            list[Json], get(client, f"/api/v1/jgp/{day}/sections/1a/2")[1]["groups"]
        )
        assert [group["code"] for group in listed] == ["H21", "J01AJ"]
    first = get(client, "/api/v1/jgp/2026-07-10/sections/1a/1")[1]
    groups = cast(list[Json], first["groups"])
    assert [group["code"] for group in groups] == ["A01", "PZD02"]
    assert (
        groups[0]["hospitalization_hundredths"],
        groups[0]["hospitalization_display"],
    ) == (
        1358600,
        "13586.00",
    )
    assert first["points_kind"] == "catalog_base_points_without_modifiers"


def test_1ae_groups_keep_their_own_measures(client: TestClient) -> None:
    body = get(client, "/api/v1/jgp/2026-07-10/groups/AU35C")[1]
    assert cast(Json, body["group"])["catalog"] == "1ae"
    assert values(body) == {
        "hospitalization": (352600, "3526.00"),
        "day_stay_under_12_hours": (352600, "3526.00"),
        "financed_stay_excluding_day_stay": (19, "19"),
        "extra_stay_day_excluding_day_stay": (55900, "559.00"),
    }


def test_service_dates_after_the_knowledge_horizon_are_flagged(
    client: TestClient,
) -> None:
    later = cast(Json, get(client, "/api/v1/jgp/2099-12-31/groups/A01")[1]["ruleset"])
    current = cast(Json, get(client, "/api/v1/jgp/2026-07-10/groups/A01")[1]["ruleset"])
    assert (later["service_date_after_knowledge"], later["knowledge_as_of"]) == (
        True,
        "2026-10-02",
    )
    assert current["service_date_after_knowledge"] is False


def test_unsuperseded_overlap_is_a_conflict(tmp_path: Path) -> None:
    rival = original_release(
        tmp_path,
        release_id="jgp-catalog-rival",
        baw_document_id=43999,
        document_url="https://baw.nfz.gov.pl/NFZ/document/43999/Rival",
        service_from=date(2026, 7, 1),
    )
    current = stage_catalog(tmp_path, catalog_workbook(default_sheets()))
    manifest = dataset(tmp_path, [rival, current.model_dump(mode="json")])
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    with TestClient(create_app(tmp_path / "bundle")) as conflict:
        status, body = get(conflict, "/api/v1/jgp/2026-07-10/groups/PZD02")
    assert (status, cast(Json, body["error"])["code"]) == (409, "ambiguous_ruleset")


def test_bundles_without_catalogs_say_so(tmp_path: Path) -> None:
    _ = build_reference_artifact(rank_manifest(tmp_path), tmp_path, tmp_path / "bundle")
    with TestClient(create_app(tmp_path / "bundle")) as client:
        for path in (
            "/api/v1/jgp/2026-07-10/groups/PZD02",
            "/api/v1/jgp/2026-07-10/sections",
            "/api/v1/jgp/2026-07-10/sections/1a/1",
        ):
            status, body = get(client, path)
            assert (status, cast(Json, body["error"])["code"]) == (
                404,
                "no_jgp_catalog",
            )
