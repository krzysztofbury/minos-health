"""Walk a release's hierarchy one level at a time (#294).

A recursive CTE joined to the edges let SQLite 3.40 (the production image) add
a Bloom filter to the recursive step once the bundle carried planner
statistics, and it rebuilt that filter over the release's edges on every step:
about 600,000 operations per walk, so ten search hits passed the query budget.
Single-table statements leave the planner no join to rewrite on any version.
"""

import json
import sqlite3
from typing import Literal, cast

from minos.store.reference_build import reference_sql

type Direction = Literal["ancestors", "descendants"]

_EDGES: dict[Direction, str] = {
    "ancestors": "queries/hierarchy_parents.sql",
    "descendants": "queries/hierarchy_children.sql",
}


def walk_hierarchy(
    connection: sqlite3.Connection,
    *,
    release_id: str,
    ordinal: int,
    direction: Direction,
    relation_type: str,
    max_depth: int,
    row_limit: int,
) -> list[tuple[int, int, str, str, str]]:
    """(ordinal, nearest depth, code, text, kind) by ordinal, at most row_limit.

    Depth runs to max_depth + 1, so a caller can tell a walk that reached its
    bound from one that ended. A cycle stops at that bound, not by memory.
    """
    edges = reference_sql(_EDGES[direction])
    depths: dict[int, int] = {}
    frontier = [ordinal]
    for depth in range(1, max_depth + 2):
        rows = cast(
            list[tuple[int]],
            connection.execute(
                edges,
                {
                    "release_id": release_id,
                    "ordinals": json.dumps(frontier),
                    "relation_type": relation_type,
                },
            ).fetchall(),
        )
        frontier = [row[0] for row in rows]
        if not frontier:
            break
        for node in frontier:
            _ = depths.setdefault(node, depth)
    if not depths:
        return []
    nodes = cast(
        list[tuple[int, str, str, str]],
        connection.execute(
            reference_sql("queries/hierarchy_nodes.sql"),
            {
                "release_id": release_id,
                "ordinals": json.dumps(sorted(depths)),
                "row_limit": row_limit,
            },
        ).fetchall(),
    )
    return [(node, depths[node], code, text, kind) for node, code, text, kind in nodes]
