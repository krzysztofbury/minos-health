import sqlite3
from collections.abc import Iterator

import pytest

from minos.store.hierarchy_walk import walk_hierarchy


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    database = sqlite3.connect(":memory:")
    _ = database.executescript(
        """
        CREATE TABLE reference_node (
            source_release_id TEXT, ordinal INTEGER, source_code TEXT,
            source_text TEXT, kind TEXT
        );
        CREATE TABLE reference_relation (
            source_release_id TEXT, parent_ordinal INTEGER, child_ordinal INTEGER,
            relation_type TEXT
        );
        """
    )
    for release in ("a", "b"):
        _ = database.executemany(
            "INSERT INTO reference_node VALUES (?, ?, ?, ?, 'container')",
            [(release, node, f"C{node}", f"node {node}") for node in range(1, 9)],
        )
    # 5 reaches 1 twice: directly at depth 1 and through 4, 3 and 2 at depth 4.
    edges = [(4, 5), (3, 4), (2, 3), (1, 2), (1, 5), (7, 8), (8, 7)]
    _ = database.executemany(
        "INSERT INTO reference_relation VALUES ('a', ?, ?, 'contains')", edges
    )
    _ = database.execute(
        "INSERT INTO reference_relation VALUES ('b', 6, 5, 'contains')"
    )
    _ = database.execute("INSERT INTO reference_relation VALUES ('a', 6, 5, 'is_a')")
    yield database
    database.close()


def _walk(
    connection: sqlite3.Connection,
    ordinal: int,
    *,
    direction: str = "ancestors",
    max_depth: int = 32,
    row_limit: int = 33,
) -> list[tuple[int, int]]:
    rows = walk_hierarchy(
        connection,
        release_id="a",
        ordinal=ordinal,
        direction="ancestors" if direction == "ancestors" else "descendants",
        relation_type="contains",
        max_depth=max_depth,
        row_limit=row_limit,
    )
    return [(row[0], row[1]) for row in rows]


def test_each_node_keeps_its_nearest_depth_in_this_release_and_relation(
    connection: sqlite3.Connection,
) -> None:
    assert _walk(connection, 5) == [(1, 1), (2, 3), (3, 2), (4, 1)]


def test_rows_carry_the_node_fields_the_callers_read(
    connection: sqlite3.Connection,
) -> None:
    rows = walk_hierarchy(
        connection,
        release_id="a",
        ordinal=2,
        direction="ancestors",
        relation_type="contains",
        max_depth=32,
        row_limit=33,
    )
    assert rows == [(1, 1, "C1", "node 1", "container")]


def test_descendants_walk_the_other_way(connection: sqlite3.Connection) -> None:
    assert _walk(connection, 3, direction="descendants") == [(4, 1), (5, 2)]
    # 6 has children only in another release and in another relation.
    assert _walk(connection, 6, direction="descendants") == []


def test_a_node_without_edges_has_an_empty_walk(
    connection: sqlite3.Connection,
) -> None:
    assert _walk(connection, 6) == []


def test_a_walk_past_the_bound_shows_one_level_beyond_it(
    connection: sqlite3.Connection,
) -> None:
    # Callers refuse any depth above their bound, so the walk must report it.
    assert _walk(connection, 2, direction="descendants", max_depth=1) == [
        (3, 1),
        (4, 2),
    ]


def test_a_cycle_stops_at_the_bound(connection: sqlite3.Connection) -> None:
    assert _walk(connection, 7, max_depth=4) == [(7, 2), (8, 1)]


def test_the_row_limit_keeps_the_lowest_ordinals(
    connection: sqlite3.Connection,
) -> None:
    assert _walk(connection, 5, row_limit=2) == [(1, 1), (2, 3)]
