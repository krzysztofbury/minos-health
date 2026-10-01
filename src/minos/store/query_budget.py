"""A shared deadline covers SQL and Python work across a whole search request."""

import sqlite3
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import final

from minos.domain.reference import ArtifactError, QueryLimitError


@final
class QueryBudget:
    def __init__(self, *, seconds: float = 0.25, operations: int = 2_000_000) -> None:
        self._deadline = time.monotonic() + seconds
        self._maximum_operations = operations
        self._operations = 0

    def check(self) -> None:
        if time.monotonic() > self._deadline:
            raise QueryLimitError("search exceeded execution limit")

    def interrupted(self) -> int:
        self._operations += 1000
        return int(
            self._operations > self._maximum_operations
            or time.monotonic() > self._deadline
        )


@contextmanager
def query_budget(
    connection: sqlite3.Connection,
    *,
    seconds: float = 0.25,
    operations: int = 2_000_000,
) -> Generator[QueryBudget]:
    budget = QueryBudget(seconds=seconds, operations=operations)
    connection.set_progress_handler(budget.interrupted, 1000)
    try:
        yield budget
        budget.check()
    except sqlite3.OperationalError as error:
        if error.sqlite_errorcode == sqlite3.SQLITE_INTERRUPT:
            raise QueryLimitError("search exceeded execution limit") from error
        raise ArtifactError("reference search storage failed") from error
    finally:
        connection.set_progress_handler(None, 0)
