"""Derived search projections reference the existing source graph, not a new tree."""

import sqlite3
from collections.abc import Iterable
from functools import lru_cache
from typing import cast

from minos.domain.morphology import MORPHOLOGY_VERSION, PolishMorphology
from minos.domain.normalization import normalize_description
from minos.domain.reference import ArtifactError
from minos.domain.search import SEARCH_VERSION, fold_polish, index_tokens
from minos.store.query_budget import query_budget


def build_search_index(
    connection: sqlite3.Connection, sources_sql: str, owners_sql: str, jgp_sql: str
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("search indexing requires the build transaction")
    _ = connection.execute(
        "INSERT INTO build_metadata VALUES ('search_version', ?)",
        (
            SEARCH_VERSION
            if connection.execute("PRAGMA user_version").fetchone()[0] >= 8
            else "lexical-2",
        ),
    )
    _ = connection.execute(
        "INSERT INTO build_metadata VALUES ('morphology_version', ?)",
        (MORPHOLOGY_VERSION,),
    )
    _populate_documents(connection, sources_sql)
    _attribute_documents(connection, owners_sql)
    if connection.execute("PRAGMA user_version").fetchone()[0] >= 8:
        from minos.store.search_jgp_build import build_search_jgp

        build_search_jgp(connection, jgp_sql)
    _ = connection.execute(
        "INSERT INTO search_document_fts(search_document_fts) VALUES ('rebuild')"
    )
    _ = connection.execute(
        "INSERT INTO search_document_fts(search_document_fts, rank) VALUES ('integrity-check', 1)"
    )


def _populate_documents(connection: sqlite3.Connection, sources_sql: str) -> None:
    analyzer = PolishMorphology()
    folded = (
        cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())[0] >= 8
    )
    forms = lru_cache(maxsize=100_000)(analyzer.variants)
    rows = cast(
        Iterable[tuple[str, int, str, str, int | None, str | None, str]],
        connection.execute(sources_sql),
    )
    for document_id, row in enumerate(rows, 1):
        if document_id > 3_200_000:
            raise ArtifactError("search document count exceeds limit")
        release, ordinal, language, kind, description, attribute, text = row
        normalized = normalize_description(text)
        words = index_tokens(normalized)
        variants = {
            word: forms(word) if language == "pl" else (word,) for word in words
        }
        values: tuple[object, ...] = (
            document_id,
            release,
            ordinal,
            language,
            kind,
            description,
            attribute,
            normalized,
            " ".join(words),
        )
        if folded:
            values += (fold_polish(normalized),)
        _ = connection.execute(
            "INSERT INTO search_document VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            if folded
            else "INSERT INTO search_document VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        _index_words(connection, release, language, document_id, words)
        _ = connection.executemany(
            "INSERT OR IGNORE INTO search_word_lemma VALUES (?, ?, ?, ?)",
            (
                (release, language, variant, word)
                for word, values in variants.items()
                for variant in values
            ),
        )
        if language == "pl" and folded:
            _ = connection.executemany(
                "INSERT OR IGNORE INTO search_word_folded VALUES (?,?,?,?)",
                (
                    (release, language, key, word)
                    for word, values in variants.items()
                    for key in sorted(
                        {fold_polish(word), *(fold_polish(value) for value in values)}
                    )
                ),
            )


def _attribute_documents(connection: sqlite3.Connection, owners_sql: str) -> None:
    # The walk is linear in releases, and history accumulates them (#141), so
    # the bound is per release rather than per dataset.
    releases = max(
        1,
        cast(
            tuple[int],
            connection.execute(
                "SELECT count(DISTINCT source_release_id) FROM search_document"
            ).fetchone(),
        )[0],
    )
    with query_budget(
        connection, seconds=10 * releases, operations=50_000_000 * releases
    ):
        attributions = cast(
            Iterable[tuple[int, str, int, int]], connection.execute(owners_sql)
        )
        for count, attribution in enumerate(attributions, 1):
            if count > 6_400_000:
                raise ArtifactError("search attribution count exceeds limit")
            document_id, release, ordinal, depth = attribution
            if depth >= 65:
                raise ArtifactError("search attribution exceeds hierarchy depth limit")
            _ = connection.execute(
                """INSERT OR IGNORE INTO search_owner
                SELECT ?, ?, node_ordinal FROM concept WHERE source_release_id = ? AND node_ordinal = ?""",
                (release, document_id, release, ordinal),
            )


def _index_words(
    connection: sqlite3.Connection,
    release: str,
    language: str,
    document_id: int,
    words: tuple[str, ...],
) -> None:
    _ = connection.executemany(
        "INSERT OR IGNORE INTO search_word VALUES (?, ?, ?, ?)",
        ((release, language, word, len(word)) for word in words),
    )
    _ = connection.executemany(
        "INSERT INTO search_token VALUES (?, ?, ?, ?)",
        ((release, language, word, document_id) for word in words),
    )
