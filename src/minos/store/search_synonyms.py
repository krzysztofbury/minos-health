"""Pinned reviewed synonym import and bounded, one-pass query rewrites."""

import re
import sqlite3
from dataclasses import replace
from typing import cast

from minos.domain.normalization import normalize_description
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchRequest, fold_polish, prepare_query
from minos.ingest.search_synonyms import SearchSynonym
from minos.store.artifact import canonical_json


def import_search_synonyms(
    connection: sqlite3.Connection, entries: tuple[SearchSynonym, ...]
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("synonym import requires a build transaction")
    _ = connection.executemany(
        "INSERT INTO search_synonym VALUES (?,?,?,?,?)",
        (
            (
                entry.id,
                entry.system,
                fold_polish(normalize_description(entry.term)),
                normalize_description(entry.source_wording),
                canonical_json(entry).decode(),
            )
            for entry in sorted(entries, key=lambda item: item.id)
            if entry.status == "reviewed"
        ),
    )


def validate_search_synonyms(
    connection: sqlite3.Connection, entries: tuple[SearchSynonym, ...]
) -> None:
    rows = connection.execute(
        "SELECT id,system,term,source_wording,entry_json FROM search_synonym ORDER BY id LIMIT 501"
    ).fetchall()
    expected = [
        (
            entry.id,
            entry.system,
            fold_polish(normalize_description(entry.term)),
            normalize_description(entry.source_wording),
            canonical_json(entry).decode(),
        )
        for entry in sorted(entries, key=lambda item: item.id)
        if entry.status == "reviewed"
    ]
    if rows != expected:
        raise ArtifactError("reviewed synonym policy conflicts with pinned manifest")


def synonym_requests(
    connection: sqlite3.Connection, request: SearchRequest
) -> list[tuple[SearchRequest, str]]:
    if (
        request.query.code_like
        or request.language != "pl"
        or connection.execute("PRAGMA user_version").fetchone()[0] < 8
    ):
        return []
    rows = cast(
        list[tuple[str, str]],
        connection.execute(
            "SELECT term,source_wording FROM search_synonym WHERE system=? ORDER BY length(term) DESC,id LIMIT 501",
            (request.system,),
        ).fetchall(),
    )
    if len(rows) > 500:
        raise ArtifactError("synonym inventory exceeds bound")
    return rewrite_requests(request, rows)


def rewrite_requests(
    request: SearchRequest, rows: list[tuple[str, str]]
) -> list[tuple[SearchRequest, str]]:
    if len(rows) > 500:
        raise ArtifactError("synonym inventory exceeds bound")
    if request.query.code_like or request.language != "pl":
        return []
    folded = fold_polish(request.query.effective_text)
    result: list[tuple[SearchRequest, str]] = []
    for term, wording in rows:
        found = re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", folded)
        if found is None:
            continue
        # Polish folding preserves character positions. Keep all surrounding
        # qualifiers; replace one evidenced phrase, never chain rewrites.
        replacement = (
            request.query.effective_text[: found.start()]
            + wording
            + request.query.effective_text[found.end() :]
        )
        if len(replacement) > 256:
            continue
        result.append((replace(request, query=prepare_query(replacement)), wording))
        if len(result) == 3:
            break
    return result
