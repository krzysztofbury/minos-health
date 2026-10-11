"""Catalog row populations, exact base values and separate applicability."""

import sqlite3
from collections import defaultdict
from typing import cast

from minos.store.diff_records import Projection, canonical, rows


def project_catalog(
    connection: sqlite3.Connection,
    release: str,
    output: Projection,
    *,
    schedule_only: bool = False,
) -> None:
    measures: dict[tuple[str, int], list[tuple[str, str, int]]] = defaultdict(list)
    for catalog, row, measure, unit, amount in cast(
        list[tuple[str, int, str, str, int]],
        rows(
            connection,
            """
        SELECT catalog,source_row,measure,unit,amount FROM group_base_value
        WHERE release_id=? ORDER BY catalog,source_row,measure""",
            (release,),
        ),
    ):
        measures[catalog, row].append((measure, unit, amount))
    scopes: dict[tuple[str, int], list[tuple[str, int]]] = defaultdict(list)
    for catalog, row, label, marker in cast(
        list[tuple[str, int, str, int]],
        rows(
            connection,
            """
        SELECT g.catalog,g.source_row,s.label,g.marker FROM group_scope g
        JOIN catalog_scope s ON s.release_id=g.release_id AND s.catalog=g.catalog AND s.position=g.position
        WHERE g.release_id=? ORDER BY g.catalog,g.source_row,s.label,g.marker""",
            (release,),
        ),
    ):
        scopes[catalog, row].append((label, marker))
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    values: dict[tuple[str, str], list[object]] = defaultdict(list)
    windows: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    locations: dict[tuple[str, str], list[object]] = defaultdict(list)
    schedules: dict[tuple[str, str], list[object]] = defaultdict(list)
    source = cast(
        list[
            tuple[
                str, int, int, str, str, str, int, str, str | None, str, str | None, str
            ]
        ],
        rows(
            connection,
            """
        SELECT catalog,source_row,ordinal,code,product_code,name,procedural,chapter,notes,
        service_from,service_to,applicability_evidence FROM group_definition
        WHERE release_id=? ORDER BY catalog,source_row""",
            (release,),
        ),
    )
    for (
        catalog,
        row,
        ordinal,
        code,
        product,
        name,
        procedural,
        chapter,
        notes,
        start,
        end,
        evidence,
    ) in source:
        key = (catalog, code)
        definition: dict[str, object] = {
            "product_code": product,
            "name": name,
            "procedural": bool(procedural),
            "chapter": chapter,
            "notes": notes,
            "scopes": scopes[catalog, row],
        }
        groups[key].append(definition)
        values[key].append({"product_code": product, "values": measures[catalog, row]})
        window: dict[str, object] = {
            "source_occurrence": len(groups[key]),
            "service_from": start,
            "service_to": end,
            "evidence": evidence,
        }
        windows[key].append(window)
        schedules[key].append(
            {
                "definition": definition,
                "values": measures[catalog, row],
                "window": window,
            }
        )
        locations[key].append((row, ordinal))
    for key in sorted(groups):
        # The dated web comparison consumes schedules only. Avoid serializing
        # the independent offline audit categories it would immediately discard.
        # Source reads, row/byte bounds and the schedule projection are shared.
        if schedule_only:
            output.add("catalog_schedule", key, schedules[key])
            continue
        output.add("catalog_group", key, sorted(groups[key], key=canonical))
        output.add(
            "base_values", key, sorted(values[key], key=canonical), context=windows[key]
        )
        output.add("catalog_applicability", key, sorted(windows[key], key=canonical))
        output.add("evidence_location", key, locations[key])
        output.add("catalog_schedule", key, schedules[key])
    if not schedule_only:
        output.add(
            "source_order",
            ("catalog_rows",),
            [(catalog, code) for catalog, _, _, code, *_ in source],
        )
