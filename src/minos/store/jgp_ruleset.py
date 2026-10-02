"""Read-only JGP catalog answers for one resolved service date.

Every answer carries the selected service and knowledge dates, the bundle's
reviewed knowledge horizon, the governing release and its evidence, the row's
own applicability evidence and the explicit unbound modifier state. Values are
catalog base points without modifiers, never final settlement points.
"""

import sqlite3
from datetime import date
from typing import cast

from minos.domain.jgp_ruleset import (
    GroupNotOnDate,
    KnowledgeBeyondBundle,
    ReleaseWindow,
    resolve_release,
)
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchInputError
from minos.store.query_budget import query_budget

POINTS_KIND = "catalog_base_points_without_modifiers"
POINTS_LABEL = "punkty bazowe wg katalogu, bez modyfikatorów"
UNBOUND_MEANING = (
    "No reviewed MODTAR binding yet; this is not an empty set of applicable "
    + "modifiers and final points cannot be calculated."
)


def unbound_modifiers() -> dict[str, str]:
    # A fresh object per answer: callers may serialize or annotate it.
    return {"binding": "unbound", "meaning": UNBOUND_MEANING}


def catalog_knowledge(connection: sqlite3.Connection) -> date:
    """The reviewed date through which this bundle's catalog lineage is known."""
    with query_budget(connection):
        row = cast(
            tuple[str] | None,
            connection.execute("SELECT as_of FROM catalog_knowledge").fetchone(),
        )
    if row is None:
        raise ArtifactError("catalog knowledge horizon is missing")
    return date.fromisoformat(row[0])


def resolve_ruleset(
    connection: sqlite3.Connection, service_date: date, known_on: date
) -> dict[str, object]:
    """The release governing `service_date` as published by `known_on`."""
    horizon = catalog_knowledge(connection)
    if known_on > horizon:
        raise KnowledgeBeyondBundle(
            "bundle catalog knowledge ends on " + horizon.isoformat()
        )
    with query_budget(connection):
        rows = cast(
            list[tuple[str, str, str, str | None]],
            connection.execute(
                "SELECT id, service_from, published_on, supersedes FROM catalog_release"
            ).fetchall(),
        )
    governing = resolve_release(
        tuple(
            ReleaseWindow(
                release_id,
                date.fromisoformat(service_from),
                date.fromisoformat(published_on),
                supersedes,
            )
            for release_id, service_from, published_on, supersedes in rows
        ),
        service_date,
        known_on,
    )
    return {
        "service_date": service_date.isoformat(),
        "known_on": known_on.isoformat(),
        "knowledge_as_of": horizon.isoformat(),
        **_release_provenance(connection, governing.release_id),
        "modifiers": unbound_modifiers(),
    }


def _release_provenance(
    connection: sqlite3.Connection, release_id: str
) -> dict[str, object]:
    with query_budget(connection):
        release = cast(
            tuple[str, int, str, str, str, str, str, str | None, str, str] | None,
            connection.execute(
                """SELECT act_number, baw_document_id, act_date, published_on,
                    service_from, service_evidence, modifier_binding, supersedes,
                    workbook_sha256, recorded_at
                FROM catalog_release WHERE id = ?""",
                (release_id,),
            ).fetchone(),
        )
    if release is None or release[6] != "unbound":
        raise ArtifactError("resolved catalog release is inconsistent")
    return {
        "release_id": release_id,
        "act_number": release[0],
        "baw_document_id": release[1],
        "act_date": release[2],
        "published_on": release[3],
        "service_from": release[4],
        "service_evidence": release[5],
        "supersedes": release[7],
        "workbook_sha256": release[8],
        "recorded_at": release[9],
    }


def catalog_group(
    connection: sqlite3.Connection, ruleset: dict[str, object], code: str
) -> dict[str, object]:
    """The one catalog row of `code` applying on the ruleset's service date."""
    if not 1 <= len(code) <= 16:
        raise SearchInputError("invalid group code")
    release_id = cast(str, ruleset["release_id"])
    service_date = cast(str, ruleset["service_date"])
    with query_budget(connection):
        rows = cast(
            list[
                tuple[
                    str, int, int, str, str, int, str, str | None, str, str | None, str
                ]
            ],
            connection.execute(
                """SELECT catalog, source_row, ordinal, product_code, name, procedural,
                    chapter, notes, service_from, service_to, applicability_evidence
                FROM group_definition
                WHERE release_id = ? AND code = ? AND service_from <= ?
                  AND (service_to IS NULL OR ? <= service_to)""",
                (release_id, code, service_date, service_date),
            ).fetchall(),
        )
    if not rows:
        raise GroupNotOnDate("group has no catalog row on that service date")
    if len(rows) != 1:
        raise ArtifactError("several catalog rows apply to one code and date")
    row = rows[0]
    values, scopes = _row_details(connection, release_id, row[0], row[1])
    return {
        "ruleset": ruleset,
        "group": {
            "code": code,
            "catalog": row[0],
            "source_row": row[1],
            "ordinal": row[2],
            "product_code": row[3],
            "name": row[4],
            "procedural": bool(row[5]),
            "chapter": row[6],
            "notes": row[7],
            "applicability": {
                "service_from": row[8],
                "service_to": row[9],
                "evidence": row[10],
            },
        },
        "points_kind": POINTS_KIND,
        "points_label": POINTS_LABEL,
        "base_values": values,
        "scopes": scopes,
    }


def _row_details(
    connection: sqlite3.Connection, release_id: str, catalog: str, source_row: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    key = (release_id, catalog, source_row)
    with query_budget(connection):
        values = cast(
            list[tuple[str, str, int, str]],
            connection.execute(
                """SELECT measure, unit, amount, source_column FROM group_base_value
                WHERE release_id = ? AND catalog = ? AND source_row = ?
                ORDER BY source_column""",
                key,
            ).fetchall(),
        )
        scopes = cast(
            list[tuple[int, str, int]],
            connection.execute(
                """SELECT scope.position, scope.label, mark.marker
                FROM group_scope AS mark JOIN catalog_scope AS scope
                  ON scope.release_id = mark.release_id
                 AND scope.catalog = mark.catalog AND scope.position = mark.position
                WHERE mark.release_id = ? AND mark.catalog = ? AND mark.source_row = ?
                ORDER BY scope.position""",
                key,
            ).fetchall(),
        )
    return (
        [
            {
                "measure": measure,
                "unit": unit,
                "amount": amount,
                "source_column": column,
            }
            for measure, unit, amount, column in values
        ],
        [
            {"position": position, "label": label, "marker": marker}
            for position, label, marker in scopes
        ],
    )
