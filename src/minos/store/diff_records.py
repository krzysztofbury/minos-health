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
    connection: sqlite3.Connection,
    sql: str,
    parameters: tuple[str, ...],
    *,
    max_rows: int | None = None,
    max_bytes: int | None = None,
) -> list[tuple[object, ...]]:
    max_rows = MAX_ROWS if max_rows is None else min(MAX_ROWS, max_rows)
    max_bytes = (
        MAX_PROJECTION_BYTES
        if max_bytes is None
        else min(MAX_PROJECTION_BYTES, max_bytes)
    )
    result: list[tuple[object, ...]] = []
    size = 0
    cursor = connection.execute(sql + f" LIMIT {max_rows + 1}", parameters)
    for row in cast(Iterable[tuple[object, ...]], cursor):
        size += sum(
            len(value.encode()) if isinstance(value, str) else 8 for value in row
        )
        if len(result) >= max_rows or size > max_bytes:
            raise QueryLimitError("release diff row or byte bound exceeded")
        result.append(row)
    return result


@final
class Projection:
    def __init__(
        self,
        budget: QueryBudget,
        *,
        max_facts: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        self.budget = budget
        self._facts: list[Fact] = []
        self._bytes = 0
        self._max_facts = MAX_FACTS if max_facts is None else min(MAX_FACTS, max_facts)
        self._max_bytes = (
            MAX_PROJECTION_BYTES
            if max_bytes is None
            else min(MAX_PROJECTION_BYTES, max_bytes)
        )

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
        if self._bytes > self._max_bytes or len(self._facts) >= self._max_facts:
            raise QueryLimitError("release diff projection bound exceeded")
        self._facts.append(Fact(category, identity, encoded, context_value))

    def facts(self) -> tuple[Fact, ...]:
        self.budget.check()
        return tuple(self._facts)
