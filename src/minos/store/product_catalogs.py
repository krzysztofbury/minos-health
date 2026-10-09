"""Build and read reviewed product catalogs of every pinned act (#145)."""

import json
import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.grouper import ProductClassification
from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.product_catalogs import parse_product_catalog
from minos.ingest.product_catalogs_manifest import ProductCatalogs
from minos.store.artifact import canonical_json
from minos.store.query_budget import query_budget

MAX_MEMBERS = 10_000
MAX_JGP_PRODUCTS = 5_000


def import_product_catalogs(
    connection: sqlite3.Connection,
    manifests: tuple[ProductCatalogs, ...],
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("product catalog import requires a build transaction")
    for manifest in sorted(manifests, key=lambda item: item.release_id):
        _ = connection.execute(
            "INSERT INTO product_catalog_release VALUES (?, ?, ?)",
            (
                manifest.release_id,
                manifest.catalog_release_id,
                canonical_json(manifest).decode(),
            ),
        )
        for pin in sorted(manifest.files, key=lambda item: item.sheet):
            with verified_source(archive, pin, staging_directory=staging) as source:
                if not pin.parsed:
                    continue
                entries = parse_product_catalog(source, pin)
            _ = connection.executemany(
                "INSERT INTO product_catalog_member VALUES (?, ?, ?, ?, ?)",
                (
                    (manifest.release_id, pin.sheet, entry.code, entry.name, entry.row)
                    for entry in entries
                ),
            )


def validate_product_bindings(
    connection: sqlite3.Connection, manifests: tuple[ProductCatalogs, ...]
) -> None:
    """Stored releases must be exactly the manifest's reviewed pins."""
    with query_budget(connection):
        stored = cast(
            list[tuple[str, str, str]],
            connection.execute(
                """SELECT id, catalog_release_id, manifest_json
                FROM product_catalog_release ORDER BY id LIMIT 65"""
            ).fetchall(),
        )
    expected = sorted(
        (item.release_id, item.catalog_release_id, canonical_json(item).decode())
        for item in manifests
    )
    if stored != expected:
        raise ArtifactError("product catalog bindings do not match the manifest")


def product_catalog_releases(connection: sqlite3.Connection) -> tuple[str, ...]:
    """Catalog releases whose act has pinned product catalogs, for warm-up."""
    with query_budget(connection):
        version = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())
        if version[0] < 10:
            return ()
        rows = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT catalog_release_id FROM product_catalog_release
                ORDER BY catalog_release_id LIMIT 65"""
            ).fetchall(),
        )
    if len(rows) > 64:
        raise ArtifactError("product catalog releases exceed their bound")
    return tuple(row for (row,) in rows)


def read_product_classification(
    connection: sqlite3.Connection, catalog_release_id: str
) -> ProductClassification | None:
    """The governing act's classification, or None when it was not pinned.

    A bundle older than schema 10 holds no product catalogs at all.
    """
    with query_budget(connection):
        version = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())
        if version[0] < 10:
            return None
        release = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT id FROM product_catalog_release WHERE catalog_release_id=?",
                (catalog_release_id,),
            ).fetchone(),
        )
        if release is None:
            return None
        members = cast(
            list[tuple[str, str]],
            connection.execute(
                """SELECT sheet, code FROM product_catalog_member WHERE release_id=?
                ORDER BY sheet, code LIMIT ?""",
                (release[0], MAX_MEMBERS + 1),
            ).fetchall(),
        )
        jgp = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT DISTINCT product_code FROM group_definition
                WHERE release_id=? AND product_code <> '' ORDER BY product_code LIMIT ?""",
                (catalog_release_id, MAX_JGP_PRODUCTS + 1),
            ).fetchall(),
        )
    if len(members) > MAX_MEMBERS or len(jgp) > MAX_JGP_PRODUCTS:
        raise ArtifactError("product catalogs exceed their read bound")
    excluded = frozenset(code for sheet, code in members if sheet == "1w")
    if not excluded:
        raise ArtifactError("the governing act lacks its type W catalog")
    neutral = (
        frozenset(
            {code for sheet, code in members if sheet != "1w"}
            | {code for (code,) in jgp}
        )
        - excluded
    )
    return ProductClassification(release[0], catalog_release_id, excluded, neutral)


def product_names(
    connection: sqlite3.Connection, release_id: str, codes: frozenset[str]
) -> dict[str, tuple[str, str]]:
    """Catalog sheet and name of reported codes, for explanations only."""
    if not codes:
        return {}
    ordered = sorted(codes)[:1000]
    with query_budget(connection):
        rows = cast(
            list[tuple[str, str, str]],
            connection.execute(
                """SELECT code, sheet, name FROM product_catalog_member
                WHERE release_id=? AND code IN (SELECT value FROM json_each(?))
                ORDER BY code, sheet='1w' DESC, sheet""",
                (release_id, json.dumps(ordered)),
            ).fetchall(),
        )
    names: dict[str, tuple[str, str]] = {}
    for code, sheet, name in rows:
        _ = names.setdefault(code, (sheet, name))
    return names
