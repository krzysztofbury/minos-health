"""Transactional attachment 1a import and manifest correspondence checks."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.jgp_catalog import CatalogSheet
from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_catalog import parse_catalog_workbook
from minos.ingest.jgp_catalog_manifest import CatalogManifest
from minos.store.artifact import canonical_json


def import_catalog(
    connection: sqlite3.Connection,
    manifest: CatalogManifest,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("catalog import requires a build transaction")
    with verified_source(
        archive, manifest.workbook, staging_directory=staging
    ) as source:
        sheets = parse_catalog_workbook(source, manifest)
    _ = connection.execute(
        "INSERT INTO catalog_release VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            manifest.release_id,
            manifest.act_number,
            manifest.baw_document_id,
            manifest.automatic_type,
            manifest.act_date.isoformat(),
            manifest.entry_into_force.isoformat()
            if manifest.entry_into_force
            else None,
            manifest.workbook.sha256,
            canonical_json(manifest).decode(),
        ),
    )
    for sheet in sheets:
        _insert_sheet(connection, manifest.release_id, sheet)


def _insert_sheet(
    connection: sqlite3.Connection, release_id: str, sheet: CatalogSheet
) -> None:
    key = (release_id, sheet.catalog)
    _ = connection.execute(
        "INSERT INTO catalog_sheet VALUES (?, ?, ?, ?, ?)",
        (*key, sheet.layout, sheet.worksheet, sheet.title),
    )
    _ = connection.executemany(
        "INSERT INTO catalog_scope VALUES (?, ?, ?, ?, ?)",
        [(*key, scope.position, scope.column, scope.label) for scope in sheet.scopes],
    )
    _ = connection.executemany(
        "INSERT INTO group_definition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                *key,
                group.row,
                group.ordinal,
                group.code,
                group.product_code,
                group.name,
                int(group.procedural),
                group.chapter,
                group.notes,
            )
            for group in sheet.groups
        ],
    )
    _ = connection.executemany(
        "INSERT INTO group_base_value VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (*key, group.row, value.measure, value.unit, value.amount, value.column)
            for group in sheet.groups
            for value in group.values
        ],
    )
    _ = connection.executemany(
        "INSERT INTO group_scope VALUES (?, ?, ?, ?, ?)",
        [
            (*key, group.row, mark.position, mark.marker)
            for group in sheet.groups
            for mark in group.scopes
        ],
    )


def validate_catalog_bindings(
    connection: sqlite3.Connection, manifests: tuple[CatalogManifest, ...]
) -> None:
    """Paired read-side check of what import wrote from the reviewed manifest."""
    releases = cast(
        list[tuple[str, str, int, str]],
        connection.execute(
            "SELECT id, act_number, baw_document_id, manifest_json FROM catalog_release"
        ).fetchall(),
    )
    expected_releases = {
        (
            manifest.release_id,
            manifest.act_number,
            manifest.baw_document_id,
            canonical_json(manifest).decode(),
        )
        for manifest in manifests
    }
    if set(releases) != expected_releases:
        raise ArtifactError("catalog source policy does not match manifest")
    sheets = cast(
        list[tuple[str, str, str, int, int]],
        connection.execute(
            """SELECT sheet.release_id, sheet.catalog, sheet.layout,
                (SELECT count(*) FROM group_definition AS grouped
                 WHERE grouped.release_id = sheet.release_id
                   AND grouped.catalog = sheet.catalog),
                (SELECT count(*) FROM (
                    SELECT code FROM group_definition AS grouped
                    WHERE grouped.release_id = sheet.release_id
                      AND grouped.catalog = sheet.catalog
                    GROUP BY code HAVING count(*) > 1))
            FROM catalog_sheet AS sheet"""
        ).fetchall(),
    )
    expected_sheets = {
        (
            manifest.release_id,
            catalog,
            sheet.layout,
            sheet.expected_groups,
            sheet.expected_repeated_codes,
        )
        for manifest in manifests
        for catalog, sheet in manifest.sheets.items()
    }
    if set(sheets) != expected_sheets:
        raise ArtifactError("catalog rows do not match the reviewed manifest")
    _validate_catalog_invariants(connection)


def _validate_catalog_invariants(connection: sqlite3.Connection) -> None:
    """Read-side twins of the parser's row rules; each query must return 0."""
    checks = {
        "a catalog row has no base points": """SELECT count(*) FROM group_definition
            AS grouped WHERE NOT EXISTS (SELECT 1 FROM group_base_value AS value
            WHERE value.release_id = grouped.release_id
              AND value.catalog = grouped.catalog
              AND value.source_row = grouped.source_row
              AND value.unit = 'points_hundredths')""",
        "a catalog sheet lacks its 68 scope columns": """SELECT count(*) FROM
            catalog_sheet AS sheet WHERE (SELECT count(*) FROM catalog_scope AS scope
            WHERE scope.release_id = sheet.release_id
              AND scope.catalog = sheet.catalog) != 68""",
        "a catalog code repeats more than twice": """SELECT count(*) FROM (
            SELECT 1 FROM group_definition GROUP BY release_id, catalog, code
            HAVING count(*) > 2)""",
        "a catalog code maps to several product codes": """SELECT count(*) FROM (
            SELECT 1 FROM group_definition GROUP BY release_id, code
            HAVING count(DISTINCT product_code) > 1
                OR count(DISTINCT catalog) > 1)""",
        "a catalog product code maps to several codes": """SELECT count(*) FROM (
            SELECT 1 FROM group_definition GROUP BY release_id, product_code
            HAVING count(DISTINCT code) > 1)""",
    }
    for message, query in checks.items():
        if connection.execute(query).fetchone() != (0,):
            raise ArtifactError(message)
