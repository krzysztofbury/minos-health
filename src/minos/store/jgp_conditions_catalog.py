"""Exact catalog correspondence, not inferred attachment applicability or points."""

import sqlite3
from typing import cast

from minos.domain import jgp_conditions as c
from minos.domain.reference import ArtifactError
from minos.ingest.jgp_conditions_manifest import ConditionsManifest

type GroupCorrespondence = tuple[str, str | None, str]
type CatalogRow = tuple[str, str, int]


def expected_catalog(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
) -> tuple[list[GroupCorrespondence], list[CatalogRow]]:
    selected = manifest.catalog_release_id
    if selected is None:
        return ([(g.code, None, "catalog_unbound") for g in sheet.groups], [])
    if connection.execute(
        "SELECT baw_document_id FROM catalog_release WHERE id=?", (selected,)
    ).fetchone() != (manifest.baw_document_id,):
        raise ArtifactError("characteristics catalog pin is absent or another document")
    rows = cast(
        list[tuple[str, int]],
        connection.execute(
            "SELECT code, source_row FROM group_definition WHERE release_id=? AND catalog=? ORDER BY source_row LIMIT 20001",
            (selected, sheet.catalog),
        ).fetchall(),
    )
    if len(rows) > 20_000:
        raise ArtifactError("characteristics catalog exceeds reviewed row bound")
    codes = {code for code, _ in rows}
    groups = {g.code for g in sheet.groups}
    return (
        [
            (g.code, selected, "matched" if g.code in codes else "not_in_catalog")
            for g in sheet.groups
        ],
        [(code, selected, row) for code, row in rows if code in groups],
    )


def insert_catalog_correspondence(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
) -> None:
    groups, rows = expected_catalog(connection, manifest, sheet)
    key = (manifest.release_id, sheet.catalog)
    _ = connection.executemany(
        "INSERT INTO characteristics_group_catalog VALUES (?,?,?,?,?)",
        ((*key, *group) for group in groups),
    )
    _ = connection.executemany(
        "INSERT INTO characteristics_catalog_row VALUES (?,?,?,?,?)",
        ((*key, *row) for row in rows),
    )


def validate_catalog_correspondence(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
) -> None:
    groups, rows = expected_catalog(connection, manifest, sheet)
    key = (manifest.release_id, sheet.catalog)
    actual_groups = cast(
        list[GroupCorrespondence],
        connection.execute(
            "SELECT code, catalog_release_id, correspondence FROM characteristics_group_catalog WHERE release_id=? AND catalog=? LIMIT 2001",
            key,
        ).fetchall(),
    )
    actual_rows = cast(
        list[CatalogRow],
        connection.execute(
            "SELECT code, catalog_release_id, source_row FROM characteristics_catalog_row WHERE release_id=? AND catalog=? LIMIT 20001",
            key,
        ).fetchall(),
    )
    if set(groups) != set(actual_groups) or set(rows) != set(actual_rows):
        raise ArtifactError("characteristics catalog correspondence conflicts")
