"""Measure the broadest searches of a bundle against the search budget.

Search stops at 2,000,000 SQLite operations and answers `limit_exceeded`. The
broadest terms sit close to that, so a larger bundle can break them with no code
change. Run this on a new bundle before it is released. Exit 1 names every term
past the margin or refused. Single terms only: longer queries of common words
reach further, and a refusal there is the closed answer search is meant to give.
"""

import argparse
import json
import sqlite3
import string
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ReferenceError
from minos.domain.search import SearchInputError, SearchRequest, prepare_query
from minos.store.query_budget import query_budget
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference

SEARCH_OPERATIONS = 2_000_000
MARGIN = 0.95
TOP_WORDS = 25
# Far above the search budget, so this only counts and never stops the search.
UNBOUNDED_OPERATIONS = 1_000_000_000_000
UNBOUNDED_SECONDS = 600.0
# The most HTTP and MCP searches ask for.
LIMIT = 20


@dataclass(frozen=True)
class Measure:
    system: str
    term: str
    operations: int
    error: str | None = None


def _releases(connection: sqlite3.Connection) -> list[tuple[str, str]]:
    return cast(
        list[tuple[str, str]],
        connection.execute(
            """SELECT system, id FROM source_release
            WHERE id NOT IN (SELECT key FROM json_each(coalesce(
                (SELECT value FROM build_metadata WHERE key='supporting_sources'), '{}')))
            ORDER BY system, id"""
        ).fetchall(),
    )


def _broadest(connection: sqlite3.Connection, release: str) -> list[str]:
    rows = cast(
        list[tuple[str]],
        connection.execute(
            """SELECT word FROM search_token
            WHERE source_release_id = ? AND language = 'pl'
            GROUP BY word ORDER BY count(*) DESC, word LIMIT ?""",
            (release, TOP_WORDS),
        ).fetchall(),
    )
    return list(dict.fromkeys((*string.ascii_lowercase, *(row[0] for row in rows))))


def measure(bundle: Path) -> list[Measure]:
    measures: list[Measure] = []
    analyzer = PolishMorphology()
    with open_reference_bundle(bundle) as (connection, _manifest):
        for system, release in _releases(connection):
            for term in _broadest(connection, release):
                try:
                    query = prepare_query(term)
                except SearchInputError:
                    continue
                request = SearchRequest(system, query, release, limit=LIMIT)
                with query_budget(
                    connection,
                    seconds=UNBOUNDED_SECONDS,
                    operations=UNBOUNDED_OPERATIONS,
                ) as counter:
                    error: str | None = None
                    try:
                        _ = search_reference(connection, request, analyzer=analyzer)
                    except ReferenceError as refused:
                        error = type(refused).__name__
                measures.append(Measure(system, term, counter.operations, error))
    return measures


def over_margin(measures: list[Measure], margin: float = MARGIN) -> list[Measure]:
    return [
        item
        for item in measures
        if item.error is not None or item.operations >= SEARCH_OPERATIONS * margin
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    measures = measure(cast(Path, args.bundle))
    over = over_margin(measures)
    broadest = sorted(measures, key=lambda item: -item.operations)[:5]
    print(
        json.dumps(
            {
                "event": "search_budget_checked",
                "sqlite": sqlite3.sqlite_version,
                "terms": len(measures),
                "budget": SEARCH_OPERATIONS,
                "margin": MARGIN,
                "broadest": [vars(item) for item in broadest],
                "over_margin": [vars(item) for item in over],
            }
        )
    )
    sys.exit(1 if over else 0)


if __name__ == "__main__":
    main()
