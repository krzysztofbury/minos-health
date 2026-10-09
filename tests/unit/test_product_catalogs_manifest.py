"""Reviewed product catalog pins, bindings and classification reads (#145)."""
# pyright: reportAny=false, reportExplicitAny=false

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from minos.domain.reference import ArtifactError
from minos.ingest.product_catalogs_manifest import ProductCatalogs
from minos.store.artifact import canonical_json
from minos.store.product_catalogs import (
    read_product_classification,
    validate_product_bindings,
)

MANIFEST = Path(__file__).parents[2] / "manifests/sources/product-catalogs.json"


def _act() -> dict[str, Any]:
    """The reviewed pins of act 46/2026 after amendment 102."""
    acts = json.loads(MANIFEST.read_text(encoding="utf-8"))["product_catalogs"]
    return next(a for a in acts if a["baw_document_id"] == 44042)


def _files(act: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(f) for f in act["files"]]


def test_the_reviewed_pins_validate() -> None:
    act = ProductCatalogs.model_validate_json(json.dumps(_act()))
    assert {f.sheet for f in act.files} == {"1b", "1c", "1d", "1ows", "1ts", "1w"}


def _without_sheet(sheet: str) -> dict[str, Any]:
    act = _act()
    act["attachments"] = [
        a for a in act["attachments"] if a != f"Zalacznik{sheet}.xlsx"
    ]
    act["files"] = [f for f in _files(act) if f["sheet"] != sheet]
    return act


def _with_v() -> dict[str, Any]:
    act = _act()
    act["attachments"] = [*act["attachments"], "Zalacznik1v.xlsx"]
    return act


def _unparsed_w() -> dict[str, Any]:
    act = _act()
    files = _files(act)
    for f in files:
        if f["sheet"] == "1w":
            f.update(parsed=False, worksheet=None, code_header=None, expected_codes=0)
            f["file_name"] = "Zalacznik1w.xls"
    act["files"] = files
    act["attachments"] = [
        "Zalacznik1w.xls" if a == "Zalacznik1w.xlsx" else a for a in act["attachments"]
    ]
    return act


def _uncovered() -> dict[str, Any]:
    act = _act()
    act["files"] = [f for f in _files(act) if f["sheet"] != "1d"]
    return act


@pytest.mark.parametrize(
    ("act", "message"),
    [
        (_with_v(), "type V is not supported"),
        # An act without 1w fails review, never with a raw KeyError.
        (_without_sheet("1w"), "type W catalog must be parsed"),
        (_unparsed_w(), "type W catalog must be parsed"),
        (_uncovered(), "do not cover the act's attachments"),
    ],
)
def test_an_attachment_set_other_than_the_reviewed_one_fails(
    act: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        _ = ProductCatalogs.model_validate_json(json.dumps(act))


def _database(version: int = 10) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    _ = connection.executescript(
        f"""
        CREATE TABLE product_catalog_release (
            id TEXT PRIMARY KEY, catalog_release_id TEXT UNIQUE, manifest_json TEXT);
        CREATE TABLE product_catalog_member (
            release_id TEXT, sheet TEXT, code TEXT, name TEXT, source_row INTEGER);
        CREATE TABLE group_definition (release_id TEXT, product_code TEXT);
        INSERT INTO product_catalog_release VALUES ('products-a', 'catalog-a', '{{}}');
        INSERT INTO product_catalog_member VALUES
            ('products-a', '1w', '5.52.01.0000001', 'W', 4),
            ('products-a', '1b', '5.52.01.0000001', 'both', 4),
            ('products-a', '1b', '5.53.01.0000002', 'separate', 5);
        INSERT INTO group_definition VALUES
            ('catalog-a', '5.51.01.0001001'), ('catalog-b', '5.51.01.0009999');
        PRAGMA user_version = {version};
        """
    )
    return connection


def test_classification_reads_exactly_the_requested_act() -> None:
    with _database() as connection:
        found = read_product_classification(connection, "catalog-a")
        assert found is not None
        assert found.release_id == "products-a"
        assert [
            found.classify(code)
            for code in (
                "5.52.01.0000001",  # in 1w and 1b: W wins
                "5.53.01.0000002",
                "5.51.01.0001001",  # this act's JGP product
                "5.51.01.0009999",  # another act's JGP product
            )
        ] == ["W", "neutral", "neutral", "unknown"]
        assert read_product_classification(connection, "catalog-b") is None
    with _database(version=9) as older:
        assert read_product_classification(older, "catalog-a") is None


def test_stored_releases_must_equal_the_manifest_pins() -> None:
    act = ProductCatalogs.model_validate_json(json.dumps(_act()))
    with sqlite3.connect(":memory:") as connection:
        _ = connection.execute(
            "CREATE TABLE product_catalog_release (id, catalog_release_id, manifest_json)"
        )
        row = (act.release_id, act.catalog_release_id, canonical_json(act).decode())
        _ = connection.execute(
            "INSERT INTO product_catalog_release VALUES (?, ?, ?)", row
        )
        validate_product_bindings(connection, (act,))
        _ = connection.execute(
            "UPDATE product_catalog_release SET manifest_json = '{}'"
        )
        with pytest.raises(ArtifactError, match="do not match the manifest"):
            validate_product_bindings(connection, (act,))
        with pytest.raises(ArtifactError):
            validate_product_bindings(connection, ())
