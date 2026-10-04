"""Bounded dated binding metadata, without hydrating the complete legal overlay."""

import sqlite3
from datetime import date
from typing import cast

from minos.store.query_budget import query_budget


def modifier_binding(
    connection: sqlite3.Connection,
    catalog_release_id: str,
    service_date: date,
    known_on: date,
) -> dict[str, object] | None:
    with query_budget(connection):
        row = cast(
            tuple[str, int, int, str | None, str, str, str, str] | None,
            connection.execute(
                """SELECT r.id,r.year,r.generation,json_extract(r.manifest_json,'$.display.label'),
                b.overlay_sha256,b.reviewed_on,b.reviewer,b.evidence
            FROM modifier_binding b JOIN modifier_release r ON r.id=b.modifier_release_id
            WHERE b.catalog_release_id=? AND b.knowledge_from<=? AND EXISTS (
                SELECT 1 FROM modifier_binding_window w WHERE w.catalog_release_id=b.catalog_release_id
                    AND w.service_from<=? AND w.service_to>=?)""",
                (
                    catalog_release_id,
                    known_on.isoformat(),
                    service_date.isoformat(),
                    service_date.isoformat(),
                ),
            ).fetchone(),
        )
    if row is None or row[1] != service_date.year:
        return None
    return {
        "binding": "reviewed",
        "release_id": row[0],
        "year": row[1],
        "generation": row[2],
        "independently_observed_display_version": row[3],
        "overlay_sha256": row[4],
        "reviewed_on": row[5],
        "reviewer": row[6],
        "evidence": row[7],
        "meaning": "Reviewed reference mappings; encounter eligibility and final points are not evaluated.",
    }
