"""Build and read the pinned WHO ICD-10/ICD-11 map tables (#53)."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.reference import ArtifactError, QueryLimitError
from minos.ingest.archive_source import verified_source
from minos.ingest.icd11_maps import parse_icd11_map
from minos.ingest.icd11_maps_manifest import Icd11Maps
from minos.store.artifact import canonical_json
from minos.store.query_budget import query_budget

# The widest published fan-out is a few dozen rows for one code.
MAX_ROWS_PER_CODE = 200


def import_icd11_maps(
    connection: sqlite3.Connection, maps: Icd11Maps, archive: Path, staging: Path
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("ICD-11 map import requires a build transaction")
    _ = connection.execute(
        "INSERT INTO icd11_map_release VALUES (?, ?, ?)",
        (maps.release_id, maps.icd11_release_id, canonical_json(maps).decode()),
    )
    for pin in sorted(maps.files, key=lambda item: item.kind):
        with verified_source(archive, pin, staging_directory=staging) as source:
            rows = parse_icd11_map(source, pin)
        _ = connection.executemany(
            "INSERT INTO icd11_map_row VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    maps.release_id,
                    pin.kind,
                    row.source_row,
                    row.icd10_code,
                    row.icd10_title,
                    row.icd11_code,
                    row.icd11_uri,
                    row.icd11_title,
                )
                for row in rows
            ),
        )


def validate_icd11_map_binding(
    connection: sqlite3.Connection, maps: Icd11Maps | None
) -> None:
    """The stored release must be exactly the manifest's reviewed pins."""
    with query_budget(connection):
        stored = cast(
            list[tuple[str, str, str]],
            connection.execute(
                "SELECT id, icd11_release_id, manifest_json FROM icd11_map_release LIMIT 2"
            ).fetchall(),
        )
    expected = (
        [(maps.release_id, maps.icd11_release_id, canonical_json(maps).decode())]
        if maps is not None
        else []
    )
    if stored != expected:
        raise ArtifactError("ICD-11 map binding does not match the manifest")


def map_rows(
    connection: sqlite3.Connection, maps: Icd11Maps, system: str, code: str
) -> list[dict[str, object]]:
    """Published rows naming ``code`` on its side, table by table, in row order."""
    if system == "icd10-pl":
        query = """SELECT kind, source_row, icd10_code, icd10_title, icd11_code,
            icd11_uri, icd11_title FROM icd11_map_row
            WHERE release_id = ? AND kind IN ('10to11_one', '10to11_many')
              AND icd10_code = ? ORDER BY kind, source_row LIMIT ?"""
    elif system == "icd11-pl":
        query = """SELECT kind, source_row, icd10_code, icd10_title, icd11_code,
            icd11_uri, icd11_title FROM icd11_map_row
            WHERE release_id = ? AND kind = '11to10_one'
              AND icd11_code = ? ORDER BY kind, source_row LIMIT ?"""
    else:
        return []
    rows = cast(
        list[
            tuple[str, int, str | None, str | None, str | None, str | None, str | None]
        ],
        connection.execute(
            query, (maps.release_id, code, MAX_ROWS_PER_CODE + 1)
        ).fetchall(),
    )
    if len(rows) > MAX_ROWS_PER_CODE:
        raise QueryLimitError("ICD-11 map rows exceed bound")
    return [
        {
            "table": kind,
            "source_row": row,
            "icd10_code": icd10_code,
            "icd10_title": icd10_title,
            "icd11_code": icd11_code,
            "icd11_uri": icd11_uri,
            "icd11_title": icd11_title,
        }
        for kind, row, icd10_code, icd10_title, icd11_code, icd11_uri, icd11_title in rows
    ]
