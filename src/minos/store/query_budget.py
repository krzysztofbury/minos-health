"""A shared deadline covers SQL and Python work across a whole search request."""

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import final

from minos.domain.reference import ArtifactError, QueryLimitError, QueryTimeoutError


@final
class QueryBudget:
    def __init__(self, *, seconds: float = 1.0, operations: int = 2_000_000) -> None:
        self._deadline = time.monotonic() + seconds
        self._maximum_operations = operations
        self._operations = 0

    def check(self) -> None:
        if time.monotonic() > self._deadline:
            raise QueryTimeoutError("search exceeded elapsed deadline")
        if self._operations > self._maximum_operations:
            raise QueryLimitError("search exceeded operation limit")

    def interrupted(self) -> int:
        self._operations += 1000
        return int(
            self._operations > self._maximum_operations
            or time.monotonic() > self._deadline
        )


_ACTIVE: ContextVar[tuple[tuple[sqlite3.Connection, QueryBudget], ...]] = ContextVar(
    "reference_query_budgets", default=()
)


@contextmanager
def query_budget(
    connection: sqlite3.Connection,
    *,
    seconds: float = 1.0,
    operations: int = 2_000_000,
) -> Generator[QueryBudget]:
    budget = QueryBudget(seconds=seconds, operations=operations)
    previous = _ACTIVE.get()
    active = (*previous, (connection, budget))
    token = _ACTIVE.set(active)

    def interrupted() -> int:
        return int(
            any(item.interrupted() for owner, item in active if owner is connection)
        )

    connection.set_progress_handler(interrupted, 1000)
    try:
        for owner, item in active:
            if owner is connection:
                item.check()
        yield budget
        for owner, item in active:
            if owner is connection:
                item.check()
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
            for owner, item in active:
                if owner is connection:
                    item.check()
            raise QueryLimitError("search exceeded execution limit") from error
        raise ArtifactError("reference search storage failed") from error
    finally:
        _ACTIVE.reset(token)
        parents = tuple(item for owner, item in previous if owner is connection)
        if parents:
            connection.set_progress_handler(
                lambda: int(any(item.interrupted() for item in parents)), 1000
            )
        else:
            connection.set_progress_handler(None, 0)
