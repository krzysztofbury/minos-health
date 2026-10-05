import sqlite3
from typing import cast

import pytest

from minos.domain.reference import QueryLimitError, QueryTimeoutError
from minos.store import query_budget as budgets


def test_normal_half_second_work_is_not_a_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("minos.store.query_budget.time.monotonic", lambda: clock[0])
    with sqlite3.connect(":memory:") as connection, budgets.query_budget(connection):
        clock[0] = 0.6
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_nested_query_does_not_clear_outer_operation_limit() -> None:
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises(QueryLimitError):
            with budgets.query_budget(connection, operations=1000):
                with budgets.query_budget(connection):
                    assert connection.execute("SELECT 1").fetchone() == (1,)
                _ = cast(
                    object,
                    connection.execute(
                        "WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000) SELECT sum(x) FROM n"
                    ).fetchone(),
                )
        assert connection.execute("SELECT 1").fetchone() == (1,)


def test_elapsed_deadline_has_its_own_error_and_releases_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("minos.store.query_budget.time.monotonic", lambda: clock[0])
    with sqlite3.connect(":memory:") as connection:
        with pytest.raises(QueryTimeoutError):
            with budgets.query_budget(connection, seconds=2):
                with budgets.query_budget(connection, seconds=10):
                    clock[0] = 3.0
                    _ = cast(object, connection.execute("SELECT 1").fetchone())
        assert connection.execute("SELECT 1").fetchone() == (1,)
