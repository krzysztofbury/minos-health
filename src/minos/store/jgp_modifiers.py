"""Read-only MODTAR provenance and pending reconciliation, never a latest pin."""

import calendar
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import cast

from minos.domain.jgp_modifiers import (
    CatalogWindow,
    governing_windows,
    mappings,
    resolve_group_reference,
)
from minos.domain.reference import SourceError
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.jgp_modifiers_build import validate_modifier_rows
from minos.store.query_budget import query_budget


def catalog_windows(connection: sqlite3.Connection) -> tuple[CatalogWindow, ...]:
    """Dated stretches of the bundle's catalog lineage, each with its governing
    release (as known at the bundle horizon) and the groups having a row in it."""
    from minos.store.jgp_ruleset import catalog_calendar, resolve_ruleset

    days = [date.fromisoformat(day) for day in catalog_calendar(connection)]
    windows: list[CatalogWindow] = []
    for index, start in enumerate(days):
        end = days[index + 1] - timedelta(days=1) if index + 1 < len(days) else None
        release_id = cast(str, resolve_ruleset(connection, start, None)["release_id"])
        with query_budget(connection):
            groups = frozenset(
                code
                for (code,) in cast(
                    list[tuple[str]],
                    connection.execute(
                        """SELECT DISTINCT code FROM group_definition
                        WHERE release_id = :release AND (:end IS NULL OR service_from <= :end)
                          AND (service_to IS NULL OR service_to >= :start)
                        LIMIT 5001""",
                        {
                            "release": release_id,
                            "start": start.isoformat(),
                            "end": end.isoformat() if end else None,
                        },
                    ).fetchall(),
                )
            )
        windows.append(CatalogWindow(release_id, start, end, groups))
    return tuple(windows)


def _reference_lines(
    windows: tuple[CatalogWindow, ...], year: int, rows: tuple[tuple[object, ...], ...]
) -> list[str]:
    """Classify each literal group reference against every catalog window its
    own months overlap; months before reviewed coverage are reported, not
    matched against a later catalog."""
    exact: set[str] = set()
    normalized: dict[str, str] = {}
    unresolved: dict[str, set[str]] = {}
    uncovered: set[str] = set()
    for row in rows:
        literal, month_from, month_to = row[6], row[3], row[4]
        if not isinstance(literal, str) or not isinstance(month_from, int):
            continue
        last = cast(int, month_to)
        start = date(year, month_from, 1)
        end = date(year, last, calendar.monthrange(year, last)[1])
        first_covered = min(window.service_from for window in windows)
        if start < first_covered:
            uncovered.add(literal)
            start = first_covered
        if start > end:
            continue
        try:
            overlapping = governing_windows(windows, start, end)
        except SourceError:
            uncovered.add(literal)
            continue
        for window in overlapping:
            match = resolve_group_reference(literal, window.groups)
            if match is None:
                unresolved.setdefault(literal, set()).add(window.release_id)
            elif match[1] == "exact":
                exact.add(literal)
            else:
                normalized[literal] = match[0]
    return [
        f"  - {len(exact)} references resolve exactly in the catalog governing their months.",
        f"  - {len(normalized)} resolve after removing one trailing '.' or '_' (owner rule 2026-10-03): "
        + (", ".join(f"{k} -> {v}" for k, v in sorted(normalized.items())) or "none")
        + ".",
        f"  - {len(unresolved)} remain unresolved: "
        + (
            "; ".join(
                f"{k} in {', '.join(sorted(v))}" for k, v in sorted(unresolved.items())
            )
            or "none"
        )
        + ". They block binding of their mappings.",
        f"  - {len(uncovered)} references also carry months before reviewed catalog coverage"
        + f" (from {min(w.service_from for w in windows).isoformat()}); those months stay unbound.",
    ]


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
        if not dataset.catalogs:
            lines.append(
                "- No reviewed catalog in this bundle: every group reference stays unbound."
            )
            return lines + [
                "- Legal overlays, external governing acts and section 17 applicability are not approved by this build."
            ]
        windows = catalog_windows(connection)
        for manifest in sorted(dataset.modifiers, key=lambda item: item.generation):
            release = releases[manifest.release_id]
            lines.append(
                f"- Generation {manifest.generation} group references, per catalog window of each mapping's months:"
            )
            lines.extend(_reference_lines(windows, release.year, mappings(release)))
        for candidate in dataset.modifier_candidates:
            lines.append(
                f"- Pending candidate {candidate.modifier_release_id} for {candidate.catalog_release_id}:"
                + " a research pointer only; binding is checked per catalog window, never against one catalog."
            )
    lines.append(
        "- Legal overlays, external governing acts and section 17 applicability are not approved by this build. Synthetic transition tests are not real-source legal validation."
    )
    return lines
