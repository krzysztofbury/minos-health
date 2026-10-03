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
    SectionNotOnDate,
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
    connection: sqlite3.Connection, service_date: date, known_on: date | None
) -> dict[str, object]:
    """The release governing `service_date` as published by `known_on`.

    `None` uses the bundle's reviewed horizon. A service date after the horizon
    is answered from what the bundle knows and flagged, because a later act may
    exist that the bundle has not reviewed.
    """
    horizon = catalog_knowledge(connection)
    known_on = horizon if known_on is None else known_on
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
    modifiers: dict[str, object] = dict(unbound_modifiers())
    if connection.execute("PRAGMA user_version").fetchone()[0] >= 6:
        from minos.store.jgp_modifiers import modifier_review_candidate

        candidate = modifier_review_candidate(connection, governing.release_id)
        if candidate is not None:
            modifiers["review_candidate"] = candidate
    return {
        "service_date": service_date.isoformat(),
        "known_on": known_on.isoformat(),
        "knowledge_as_of": horizon.isoformat(),
        "service_date_after_knowledge": service_date > horizon,
        **_release_provenance(connection, governing.release_id),
        "modifiers": modifiers,
    }


def _release_provenance(
    connection: sqlite3.Connection, release_id: str
) -> dict[str, object]:
    with query_budget(connection):
        release = cast(
            tuple[str, int, str, str, str, str, str, str | None, str, str, str] | None,
            connection.execute(
                """SELECT act_number, baw_document_id, act_date, published_on,
                    service_from, service_evidence, modifier_binding, supersedes,
                    workbook_sha256, recorded_at,
                    json_extract(manifest_json, '$.document_url')
                FROM catalog_release WHERE id = ?""",
                (release_id,),
            ).fetchone(),
        )
    if release is None or release[6] != "unbound":
        raise ArtifactError("resolved catalog release is inconsistent")
    # Checked again on read: pages turn this URL into a link.
    if not release[10].startswith(f"https://baw.nfz.gov.pl/NFZ/document/{release[1]}/"):
        raise ArtifactError("catalog release document URL is not its BAW document")
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
        "document_url": release[10],
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
                    str,
                    int,
                    int,
                    str,
                    str,
                    int,
                    str,
                    str | None,
                    str,
                    str | None,
                    str,
                    int,
                ]
            ],
            connection.execute(
                """SELECT catalog, source_row, ordinal, product_code, name, procedural,
                    chapter, notes, service_from, service_to, applicability_evidence,
                    chapter_position
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
            "chapter_position": row[11],
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


MAX_SECTION_GROUPS = 500
# The manifest caps chapters at 200 per catalog; two catalogs per release.
MAX_SECTIONS = 400


def catalog_sections(
    connection: sqlite3.Connection, ruleset: dict[str, object]
) -> list[dict[str, object]]:
    """Every chapter of the governing release at its stable source position.

    Positions come from the source sheet, not from the date's rows, so a
    section URL names the same chapter on every date. Chapters with no rows on
    the date report zero groups instead of shifting the others.
    """
    service_date = cast(str, ruleset["service_date"])
    with query_budget(connection):
        rows = cast(
            list[tuple[str, int, str, int]],
            connection.execute(
                """SELECT catalog, chapter_position, chapter,
                    sum(CASE WHEN service_from <= ?
                         AND (service_to IS NULL OR ? <= service_to) THEN 1 ELSE 0 END)
                FROM group_definition WHERE release_id = ?
                GROUP BY catalog, chapter_position, chapter
                ORDER BY catalog, chapter_position LIMIT ?""",
                (service_date, service_date, ruleset["release_id"], MAX_SECTIONS + 1),
            ).fetchall(),
        )
    if len(rows) > MAX_SECTIONS:
        raise ArtifactError("catalog sections exceed their bound")
    return [
        {"catalog": catalog, "position": position, "chapter": chapter, "groups": groups}
        for catalog, position, chapter, groups in rows
    ]


def catalog_section(
    connection: sqlite3.Connection,
    ruleset: dict[str, object],
    catalog: str,
    position: int,
) -> dict[str, object]:
    """Groups of one chapter applying on the ruleset's service date."""
    chosen = [
        section
        for section in catalog_sections(connection, ruleset)
        if section["catalog"] == catalog and section["position"] == position
    ]
    if len(chosen) != 1:
        raise SectionNotOnDate("catalog has no chapter at that position")
    service_date = cast(str, ruleset["service_date"])
    with query_budget(connection):
        rows = cast(
            list[tuple[str, str, str, int, int | None]],
            connection.execute(
                """SELECT grouped.code, grouped.product_code, grouped.name,
                    grouped.procedural, value.amount
                FROM group_definition AS grouped
                LEFT JOIN group_base_value AS value
                  ON value.release_id = grouped.release_id
                 AND value.catalog = grouped.catalog
                 AND value.source_row = grouped.source_row
                 AND value.measure = 'hospitalization'
                WHERE grouped.release_id = ? AND grouped.catalog = ?
                  AND grouped.chapter_position = ? AND grouped.service_from <= ?
                  AND (grouped.service_to IS NULL OR ? <= grouped.service_to)
                ORDER BY grouped.source_row LIMIT ?""",
                (
                    ruleset["release_id"],
                    catalog,
                    position,
                    service_date,
                    service_date,
                    MAX_SECTION_GROUPS + 1,
                ),
            ).fetchall(),
        )
    if len(rows) > MAX_SECTION_GROUPS:
        raise ArtifactError("catalog section exceeds its bound")
    return {
        "ruleset": ruleset,
        "section": chosen[0],
        "points_kind": POINTS_KIND,
        "points_label": POINTS_LABEL,
        "groups": [
            {
                "code": code,
                "product_code": product,
                "name": name,
                "procedural": bool(procedural),
                "hospitalization_hundredths": hospitalization,
                "hospitalization_display": None
                if hospitalization is None
                else points_display(hospitalization),
            }
            for code, product, name, procedural, hospitalization in rows
        ],
    }


def points_display(hundredths: int) -> str:
    """Exact decimal text for integer hundredths; clients never divide floats."""
    return f"{hundredths // 100}.{hundredths % 100:02d}"


# About 773 groups per reviewed release; well above it means a source change.
MAX_INDEX_GROUPS = 2_000
MAX_CALENDAR_DAYS = 50


def catalog_calendar(connection: sqlite3.Connection) -> list[str]:
    """Every day on which the bundle's catalog answer can change, ascending.

    Release starts plus row window edges (102/2026 section 4(5) rows switch on
    2027-01-01). The first day is the earliest covered service date.
    """
    with query_budget(connection):
        rows = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT day FROM (
                    SELECT service_from AS day FROM catalog_release
                    UNION SELECT service_from FROM group_definition
                    UNION SELECT date(service_to, '+1 day') FROM group_definition
                    WHERE service_to IS NOT NULL
                ) ORDER BY day LIMIT ?""",
                (MAX_CALENDAR_DAYS + 1,),
            ).fetchall(),
        )
    if not rows or len(rows) > MAX_CALENDAR_DAYS:
        raise ArtifactError("catalog calendar is empty or exceeds its bound")
    return [day for (day,) in rows]


def catalog_scope_labels(
    connection: sqlite3.Connection, ruleset: dict[str, object]
) -> list[dict[str, object]]:
    """The "Zakresy świadczeń" columns of the governing release, in source order.

    Both catalogs share one reviewed label list, so positions name the same
    scope in 1a and 1ae.
    """
    with query_budget(connection):
        rows = cast(
            list[tuple[int, str]],
            connection.execute(
                """SELECT DISTINCT position, label FROM catalog_scope
                WHERE release_id = ? ORDER BY position LIMIT 201""",
                (ruleset["release_id"],),
            ).fetchall(),
        )
    if len({position for position, _ in rows}) != len(rows) or len(rows) > 200:
        raise ArtifactError("catalog scope labels differ between catalogs")
    return [{"position": position, "label": label} for position, label in rows]


def catalog_index(
    connection: sqlite3.Connection,
    ruleset: dict[str, object],
    scope_position: int | None,
) -> list[dict[str, object]]:
    """Every group applying on the date, optionally only those marked for one
    "zakres świadczeń", with chapter placement and hospitalization points."""
    service_date = cast(str, ruleset["service_date"])
    with query_budget(connection):
        rows = cast(
            list[tuple[str, int, str, str, str, str, int, int | None, int | None]],
            connection.execute(
                """SELECT grouped.catalog, grouped.chapter_position, grouped.chapter,
                    grouped.code, grouped.product_code, grouped.name, grouped.procedural,
                    value.amount,
                    (SELECT mark.marker FROM group_scope AS mark
                     WHERE mark.release_id = grouped.release_id
                       AND mark.catalog = grouped.catalog
                       AND mark.source_row = grouped.source_row
                       AND mark.position = :scope)
                FROM group_definition AS grouped
                LEFT JOIN group_base_value AS value
                  ON value.release_id = grouped.release_id
                 AND value.catalog = grouped.catalog
                 AND value.source_row = grouped.source_row
                 AND value.measure = 'hospitalization'
                WHERE grouped.release_id = :release AND grouped.service_from <= :day
                  AND (grouped.service_to IS NULL OR :day <= grouped.service_to)
                  AND (:scope IS NULL OR EXISTS (SELECT 1 FROM group_scope AS mark
                    WHERE mark.release_id = grouped.release_id
                      AND mark.catalog = grouped.catalog
                      AND mark.source_row = grouped.source_row
                      AND mark.position = :scope))
                ORDER BY grouped.catalog, grouped.chapter_position, grouped.source_row
                LIMIT :limit""",
                {
                    "release": ruleset["release_id"],
                    "day": service_date,
                    "scope": scope_position,
                    "limit": MAX_INDEX_GROUPS + 1,
                },
            ).fetchall(),
        )
    if len(rows) > MAX_INDEX_GROUPS:
        raise ArtifactError("catalog index exceeds its bound")
    return [
        {
            "catalog": catalog,
            "chapter_position": chapter_position,
            "chapter": chapter,
            "code": code,
            "product_code": product,
            "name": name,
            "procedural": bool(procedural),
            "hospitalization_hundredths": hospitalization,
            "scope_marker": marker,
        }
        for catalog, chapter_position, chapter, code, product, name, procedural, hospitalization, marker in rows
    ]
