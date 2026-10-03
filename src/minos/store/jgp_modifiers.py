"""Read-only MODTAR provenance and pending reconciliation, never a latest pin."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.jgp_modifiers import mappings
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.jgp_modifiers_build import validate_modifier_rows
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


def modifier_reconciliation_lines(
    database: Path, dataset: DatasetManifest
) -> list[str]:
    if not dataset.modifiers:
        return []
    lines = [
        "",
        "MODTAR source evidence (#46): legal review pending; all catalog modifier bindings remain unbound.",
    ]
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        releases = {
            item.release_id: validate_modifier_rows(connection, item)
            for item in dataset.modifiers
        }
        for manifest in dataset.modifiers:
            display = (
                manifest.display.label
                if manifest.display is not None
                else "not independently observed"
            )
            lines.append(
                f"- {manifest.release_id}: {manifest.year} {manifest.grouper_type}, generation {manifest.generation}, display {display}; "
                + f"{manifest.expected_modifiers} source records, {manifest.expected_periods} periods, "
                + f"{manifest.expected_group_mappings} group mappings, {manifest.expected_inactive_empty} inactive empty records."
            )
        ordered = sorted(
            dataset.modifiers, key=lambda item: (item.year, item.generation)
        )
        for older, newer in zip(ordered, ordered[1:], strict=False):
            previous = set(mappings(releases[older.release_id]))
            current = set(mappings(releases[newer.release_id]))
            lines.append(
                f"- Source mapping diff {older.generation} -> {newer.generation}: "
                + f"{len(current - previous)} added, {len(previous - current)} removed (includes inactive empty records)."
            )
        for candidate in dataset.modifier_candidates:
            referenced = {
                group
                for modifier in releases[candidate.modifier_release_id].modifiers
                for period in modifier.periods
                for group in period.groups
            }
            known = {
                code
                for (code,) in cast(
                    list[tuple[str]],
                    connection.execute(
                        "SELECT DISTINCT code FROM group_definition WHERE release_id=? ORDER BY code LIMIT 5001",
                        (candidate.catalog_release_id,),
                    ).fetchall(),
                )
            }
            missing = sorted(referenced - known)
            lines.append(
                f"- Pending candidate for {candidate.catalog_release_id}: {len(missing)} literal source group references absent from its catalog: "
                + (", ".join(missing) if missing else "none")
                + ". Absence blocks complete binding; no substitution or family expansion."
            )
    lines.append(
        "- Legal overlays, external governing acts and section 17 applicability are not approved by this build. Synthetic transition tests are not real-source legal validation."
    )
    return lines
