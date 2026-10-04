"""Pending MODTAR research pointer for one catalog release; never a binding."""

import sqlite3
from typing import cast

from minos.store.query_budget import query_budget


def modifier_review_candidate(
    connection: sqlite3.Connection, catalog_release_id: str
) -> dict[str, object] | None:
    with query_budget(connection):
        row = cast(
            tuple[str, int, int, str | None, str, str] | None,
            connection.execute(
                """SELECT r.id,r.year,r.generation,json_extract(r.manifest_json,'$.display.label'),
            c.status,c.evidence FROM modifier_candidate c JOIN modifier_release r
              ON r.id=c.modifier_release_id WHERE c.catalog_release_id=?""",
                (catalog_release_id,),
            ).fetchone(),
        )
    if row is None:
        return None
    return {
        "release_id": row[0],
        "year": row[1],
        "generation": row[2],
        "independently_observed_display_version": row[3],
        "status": row[4],
        "evidence": row[5],
    }
