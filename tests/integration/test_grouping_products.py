"""Settlement products through the HTTP boundary (#145)."""
# pyright: reportAny=false

import json
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.http.app import create_app
from tests.fixtures.grouping_interface import (
    NEUTRAL_PRODUCT,
    W_PRODUCT,
    grouping_body,
    grouping_bundle,
)


def _with_products(*products: dict[str, object]) -> dict[str, object]:
    body = grouping_body()
    cast(dict[str, object], body["encounter"])["settlement_products"] = list(products)
    return body


def test_products_are_classified_by_the_governing_act_catalogs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from minos.grouper import pipeline

    def unexpected(*_args: object) -> object:
        raise AssertionError("a request re-read product catalogs")

    app = create_app(grouping_bundle(tmp_path, products=True))
    with TestClient(app) as client:
        prepared = cast(pipeline.PreparedSources, app.state.grouping_prepared)
        assert prepared.warmed.wait(10)
        monkeypatch.setattr(pipeline, "read_product_classification", unexpected)
        excluded = client.post(
            "/api/v1/group",
            json=_with_products(
                {
                    "code": W_PRODUCT,
                    "started_on": "2026-10-02",
                    "ended_on": "2026-10-03",
                },
                # The JGP product of group A01 in the governing catalog.
                {"code": "5.51.01.0001001", "started_on": "2026-10-01"},
            ),
        ).json()
        neutral = client.post(
            "/api/v1/group",
            json=_with_products(
                {
                    "code": NEUTRAL_PRODUCT,
                    "started_on": "2026-10-01",
                    "ended_on": "2026-10-05",
                }
            ),
        ).json()
        unknown = client.post(
            "/api/v1/group",
            json=_with_products({"code": "5.99.99.9999999"}),
        ).json()
    for answer in (excluded, neutral, unknown):
        assert answer["status"] == "evaluated"
        catalogs = answer["sources"]["product_catalogs"]
        assert catalogs["release_id"] == "products-synthetic"
        assert [f["sheet"] for f in catalogs["files"]] == ["1b", "1w"]
        assert answer["sources"]["engine_version"] == "szp-preparation-2"
    preparation = excluded["preparation"]
    assert preparation["product_binding"] == "bound"
    assert [(p["code"], p["class"], p["sheet"]) for p in preparation["products"]] == [
        ("5.51.01.0001001", "neutral", "1a"),
        (W_PRODUCT, "W", "1w"),
    ]
    # Procedure 100.01 on 2 October lies within the W product of 2-3 October;
    # the 4-day episode loses 3 - 2 = 1 day.
    assert [p["outcome"] for p in preparation["procedures"]] == ["excluded"]
    assert preparation["ward_adjusted_days"] == 3
    assert neutral["preparation"]["products"][0]["class"] == "neutral"
    assert neutral["preparation"]["ward_adjusted_days"] == 4
    assert neutral["preparation"]["procedures"][0]["outcome"] == "retained"
    assert unknown["preparation"]["products"][0]["class"] == "unknown"
    assert unknown["preparation"]["products"][0]["name"] is None
    assert unknown["preparation"]["ward_adjusted_days"] is None
    assert unknown["preparation"]["procedures"][0]["outcome"] == "unresolved"


def test_a_service_date_without_product_catalogs_refuses_reported_products(
    tmp_path: Path,
) -> None:
    with TestClient(create_app(grouping_bundle(tmp_path))) as client:
        empty = client.post("/api/v1/group", json=grouping_body()).json()
        refused = client.post(
            "/api/v1/group", json=_with_products({"code": W_PRODUCT})
        ).json()
    assert empty["status"] == "evaluated"
    assert empty["sources"]["product_catalogs"] is None
    assert refused["status"] == "product_catalog_unavailable"
    assert refused["groups"] == [] and "preparation" not in refused


def test_product_catalogs_bind_one_pinned_act_each(tmp_path: Path) -> None:
    from pydantic import ValidationError

    from minos.ingest.reference_manifest import DatasetManifest
    from minos.store.reference import read_artifact_manifest

    dataset = read_artifact_manifest(
        grouping_bundle(tmp_path, products=True)
    ).dataset.model_dump(mode="json")
    act = dataset["product_catalogs"][0]
    for broken in (
        # Pins of another BAW document cannot classify this act.
        [{**act, "baw_document_id": act["baw_document_id"] + 1}],
        # Two pin sets for one act would make the classification ambiguous.
        [act, {**act, "release_id": "products-synthetic-copy"}],
    ):
        with pytest.raises(ValidationError, match="one pinned act each"):
            _ = DatasetManifest.model_validate_json(
                json.dumps({**dataset, "product_catalogs": broken})
            )
