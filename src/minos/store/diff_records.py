"""Bounded canonical projections; physical IDs never become logical identity."""

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from typing import cast, final

from minos.domain.reference import QueryLimitError
from minos.domain.release_diff import Fact
from minos.store.query_budget import QueryBudget

MAX_ROWS = 250_000
MAX_FACTS = 250_000
MAX_PROJECTION_BYTES = 64 * 1024 * 1024


def canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def fingerprint(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def rows(
    connection: sqlite3.Connection, sql: str, parameters: tuple[str, ...]
) -> list[tuple[object, ...]]:
    result: list[tuple[object, ...]] = []
    size = 0
    cursor = connection.execute(sql + f" LIMIT {MAX_ROWS + 1}", parameters)
    for row in cast(Iterable[tuple[object, ...]], cursor):
        size += sum(
            len(value.encode()) if isinstance(value, str) else 8 for value in row
        )
        if len(result) >= MAX_ROWS or size > MAX_PROJECTION_BYTES:
            raise QueryLimitError("release diff row or byte bound exceeded")
        result.append(row)
    return result


@final
class Projection:
    def __init__(self, budget: QueryBudget) -> None:
        self.budget = budget
        self._facts: list[Fact] = []
        self._bytes = 0

    def add(
        self,
        category: str,
        identity: tuple[str, ...],
        value: object,
        *,
        context: object = None,
    ) -> None:
        self.budget.check()
        encoded = canonical(value)
        context_value = canonical(context) if context is not None else None
        self._bytes += (
            len(encoded.encode())
            + len(canonical(identity).encode())
            + (len(context_value.encode()) if context_value else 0)
        )
        if self._bytes > MAX_PROJECTION_BYTES or len(self._facts) >= MAX_FACTS:
            raise QueryLimitError("release diff projection bound exceeded")
        self._facts.append(Fact(category, identity, encoded, context_value))

    def facts(self) -> tuple[Fact, ...]:
        self.budget.check()
        return tuple(self._facts)
