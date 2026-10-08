"""Executable rule inputs for chosen groups: nodes, roots and catalog rows."""

import sqlite3
from collections.abc import Iterable
from datetime import date
from typing import cast

from minos.domain.grouper_rules import GroupRules, SelectorCodes
from minos.domain.jgp_conditions_validation import selector_ids
from minos.store.grouper_bundle import CharacteristicsGraph, owned_nodes, selector_codes
from minos.store.query_budget import query_budget


def group_rules(
    connection: sqlite3.Connection,
    graph: CharacteristicsGraph,
    catalog_release_id: str,
    service_date: date,
    codes: Iterable[str],
) -> tuple[GroupRules, ...]:
    roots = {code: group_roots for code, _row, group_roots in graph.groups}
    by_group = owned_nodes(graph)
    day = service_date.isoformat()
    result: list[GroupRules] = []
    for code in sorted(set(codes)):
        nodes = tuple(by_group.get(code, ()))
        with query_budget(connection):
            rows = cast(
                list[tuple[int, int]],
                connection.execute(
                    """SELECT source_row, procedural FROM group_definition
                    WHERE release_id=? AND catalog=? AND code=? AND service_from<=?
                      AND (service_to IS NULL OR service_to>=?)
                    ORDER BY source_row LIMIT 9""",
                    (catalog_release_id, graph.catalog, code, day, day),
                ).fetchall(),
            )
            values = (
                cast(
                    list[tuple[str, int]],
                    connection.execute(
                        """SELECT measure, amount FROM group_base_value
                        WHERE release_id=? AND catalog=? AND source_row=?
                        ORDER BY measure LIMIT 17""",
                        (catalog_release_id, graph.catalog, rows[0][0]),
                    ).fetchall(),
                )
                if len(rows) == 1
                else []
            )
        referenced = sorted(
            {identity for node in nodes for identity in selector_ids(node.payload)}
        )
        selectors: list[SelectorCodes] = []
        for identity in referenced:
            found, families, lists, source_rows = selector_codes(graph, identity)
            selectors.append(
                SelectorCodes(
                    identity,
                    frozenset(found),
                    tuple(sorted(families)),
                    tuple(sorted(lists)),
                    tuple(sorted(source_rows)),
                )
            )
        result.append(
            GroupRules(
                graph.catalog,
                code,
                len(rows),
                len(rows) == 1 and rows[0][1] == 1,
                roots.get(code, ()),
                nodes,
                tuple(selectors),
                rows[0][0] if len(rows) == 1 else None,
                tuple(values),
            )
        )
    return tuple(result)
