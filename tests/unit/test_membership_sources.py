import sqlite3
from collections.abc import Iterator
from datetime import date

import pytest

from minos.domain.reference import ArtifactError
from minos.store.jgp_condition_reference import (
    _catalog_rows,  # pyright: ignore[reportPrivateUsage]
    _source_clauses,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture
def connection() -> Iterator[sqlite3.Connection]:
    database = sqlite3.connect(":memory:")
    _ = database.executescript(
        """
        CREATE TABLE characteristics_evidence (
            release_id TEXT, catalog TEXT, row INTEGER, context TEXT,
            source_text TEXT, category TEXT
        );
        CREATE TABLE group_definition (
            release_id TEXT, catalog TEXT, source_row INTEGER, code TEXT, name TEXT,
            procedural INTEGER, notes TEXT, service_from TEXT, service_to TEXT
        );
        CREATE TABLE group_base_value (
            release_id TEXT, catalog TEXT, source_row INTEGER, measure TEXT,
            unit TEXT, amount INTEGER, source_column TEXT
        );
        """
    )
    _ = database.executemany(
        "INSERT INTO characteristics_evidence VALUES ('r', '1a', ?, ?, ?, ?)",
        [
            (1, "A03 ", "A03 Tytuł", "title"),
            (2, "A03 ", "lista   A03", "label"),
            (3, "A03 ", "lub ", "connector"),
            (4, "A03 ", "lista A03A", "label"),
            (5, "A03 ", "NYHA > II", "label"),
            # Printed after A03 but belonging to the section's list heading.
            (6, "Qpw", "Qpw Lista powikłań", "label"),
            (7, "A04", "lista A04", "label"),
        ],
    )
    _ = database.executemany(
        "INSERT INTO group_definition VALUES ('k', '1a', ?, ?, ?, 1, ?, ?, ?)",
        [
            (1, "G01", "Stara *", " do\n31.12 ", "2026-07-01", "2026-12-31"),
            (2, "G01", "Nowa *", None, "2027-01-01", None),
        ],
    )
    _ = database.execute(
        "INSERT INTO group_base_value VALUES"
        + " ('k', '1a', 1, 'hospitalization', 'points_hundredths', 100, 'E')"
    )
    yield database
    database.close()


def test_a_label_belongs_to_the_group_its_context_names(
    connection: sqlite3.Connection,
) -> None:
    clauses = _source_clauses(connection, "r", "1a")

    assert clauses["A03"] == (
        {"text": "lista A03", "connector": None},
        {"text": "lista A03A", "connector": "lub"},
        {"text": "NYHA > II", "connector": None},
    )
    assert clauses["A04"] == ({"text": "lista A04", "connector": None},)
    assert clauses["Qpw"] == ({"text": "Qpw Lista powikłań", "connector": None},)


@pytest.mark.parametrize(
    ("day", "name", "notes"),
    [
        (date(2026, 7, 1), "Stara *", "do 31.12"),
        (date(2026, 12, 31), "Stara *", "do 31.12"),
        (date(2027, 1, 1), "Nowa *", None),
    ],
)
def test_the_catalog_row_is_the_one_in_force_on_the_day(
    connection: sqlite3.Connection, day: date, name: str, notes: str | None
) -> None:
    row = _catalog_rows(connection, "k", "1a", ["G01"], day)["G01"]

    assert (row["name"], row["notes"]) == (name, notes)


def test_a_code_outside_every_window_has_no_row(
    connection: sqlite3.Connection,
) -> None:
    assert _catalog_rows(connection, "k", "1a", ["G01"], date(2026, 6, 30)) == {}


def test_two_rows_on_one_day_are_refused(connection: sqlite3.Connection) -> None:
    _ = connection.execute(
        "INSERT INTO group_definition VALUES"
        + " ('k', '1a', 3, 'G01', 'Druga', 1, NULL, '2026-07-01', NULL)"
    )
    with pytest.raises(ArtifactError, match="several catalog rows"):
        _ = _catalog_rows(connection, "k", "1a", ["G01"], date(2026, 8, 1))
