"""Dated reference-only selection for later shared application consumers (#32)."""

import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Literal, cast

from minos.domain.jgp_ruleset import (
    KnowledgeBeyondBundle,
    NoRulesetForDate,
    ReleaseWindow,
    resolve_release,
)
from minos.domain.reference import ArtifactError
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.store.query_budget import query_budget


def resolve_characteristics(
    connection: sqlite3.Connection,
    catalog: Literal["1a", "1ae"],
    service_date: date,
    known_on: date | None = None,
) -> str:
    with query_budget(connection):
        row = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT as_of FROM characteristics_knowledge"
            ).fetchone(),
        )
        if row is None:
            raise NoRulesetForDate("no reviewed characteristics knowledge horizon")
        horizon = date.fromisoformat(row[0])
        knowledge = horizon if known_on is None else known_on
        if knowledge > horizon:
            raise KnowledgeBeyondBundle(
                "characteristics knowledge ends on " + horizon.isoformat()
            )
        rows = cast(
            list[tuple[str]],
            connection.execute(
                "SELECT manifest_json FROM characteristics_release ORDER BY id LIMIT 9"
            ).fetchall(),
        )
        if len(rows) > 8:
            raise ArtifactError("characteristics releases exceed bound")
        manifests = tuple(
            ConditionsManifest.model_validate_json(value) for (value,) in rows
        )
        windows = tuple(
            ReleaseWindow(m.release_id, m.valid_from, m.published_on, m.supersedes)
            for m in manifests
        )
        chosen = resolve_release(windows, service_date, knowledge)
        manifest = next(m for m in manifests if m.release_id == chosen.release_id)
        if catalog not in manifest.sheets or (
            manifest.valid_to is not None and service_date > manifest.valid_to
        ):
            raise NoRulesetForDate(
                "selected characteristics do not cover this date or context"
            )
        return chosen.release_id


@dataclass(frozen=True)
class CatalogCorrespondence:
    catalog_release_id: str | None
    status: Literal["matched", "not_in_catalog", "catalog_unbound", "not_on_date"]
    source_rows: tuple[int, ...]


def group_catalog_correspondence(
    connection: sqlite3.Connection,
    release_id: str,
    catalog: Literal["1a", "1ae"],
    code: str,
    service_date: date,
) -> CatalogCorrespondence:
    """Select linked catalog row windows without narrowing the conditions window.

    Call with a release from resolve_characteristics. No zero-point product is
    manufactured for N09/N11, an unbound catalog, or an uncovered row date.
    """
    with query_budget(connection):
        key = (release_id, catalog, code)
        row = cast(
            tuple[str | None, Literal["matched", "not_in_catalog", "catalog_unbound"]]
            | None,
            connection.execute(
                "SELECT catalog_release_id, correspondence FROM characteristics_group_catalog WHERE release_id=? AND catalog=? AND code=?",
                key,
            ).fetchone(),
        )
        if row is None:
            raise ArtifactError("characteristics group is not in the selected context")
        if row[1] != "matched":
            return CatalogCorrespondence(row[0], row[1], ())
        rows = cast(
            list[tuple[int]],
            connection.execute(
                """SELECT link.source_row FROM characteristics_catalog_row AS link
            JOIN group_definition AS grouped ON grouped.release_id=link.catalog_release_id
              AND grouped.catalog=link.catalog AND grouped.source_row=link.source_row
            WHERE link.release_id=? AND link.catalog=? AND link.code=?
              AND grouped.service_from<=? AND (grouped.service_to IS NULL OR ?<=grouped.service_to)
            ORDER BY link.source_row LIMIT 3""",
                (*key, service_date.isoformat(), service_date.isoformat()),
            ).fetchall(),
        )
        if len(rows) > 1:
            raise ArtifactError("multiple catalog rows correspond on one date")
        return CatalogCorrespondence(
            row[0], "matched" if rows else "not_on_date", tuple(r[0] for r in rows)
        )
