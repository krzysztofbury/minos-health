"""Pinned reviewed synonym import and bounded, one-pass query rewrites."""

import re
import sqlite3
from dataclasses import replace
from typing import cast

from minos.domain.normalization import normalize_description
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchRequest, fold_polish, prepare_query, text_tokens
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
    validate_search_synonyms(connection, entries)


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
    for entry in entries:
        if entry.status != "reviewed":
            continue
        if entry.system == "jgp-szp":
            expected_words = set(
                text_tokens(normalize_description(entry.source_wording))
            )
            labels = cast(
                list[tuple[str]],
                connection.execute(
                    "SELECT DISTINCT name FROM group_definition WHERE (? IS NULL OR release_id=?) LIMIT 20001",
                    (entry.release_id, entry.release_id),
                ).fetchall(),
            )
            if len(labels) > 20000:
                raise ArtifactError("group synonym validation exceeds label bound")
            if not any(
                expected_words <= set(text_tokens(normalize_description(label)))
                for (label,) in labels
            ):
                raise ArtifactError(
                    "reviewed group wording is absent from its pinned catalog"
                )
            continue
        # An unscoped terminology synonym applies to every release of its
        # system, so each release must contain the reviewed wording.
        releases = [
            release
            for (release,) in cast(
                list[tuple[str]],
                connection.execute(
                    "SELECT id FROM source_release WHERE system=? AND (? IS NULL OR id=?) ORDER BY id LIMIT 17",
                    (entry.system, entry.release_id, entry.release_id),
                ).fetchall(),
            )
        ]
        if not releases:
            raise ArtifactError(
                "reviewed synonym wording is absent from its pinned dictionary"
            )
        for release in releases:
            found = cast(
                tuple[int] | None,
                connection.execute(
                    """SELECT 1 FROM concept_description
                WHERE source_release_id=? AND instr(normalized_text,?)>0 LIMIT 1""",
                    (release, normalize_description(entry.source_wording)),
                ).fetchone(),
            )
            if found is None:
                raise ArtifactError(
                    "reviewed synonym wording is absent from its pinned dictionary"
                )


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
        list[tuple[str, str, int]],
        connection.execute(
            """SELECT term,source_wording,coalesce(json_extract(entry_json,'$.whole_query_only'),0)
            FROM search_synonym WHERE system=? AND
            (json_extract(entry_json,'$.release_id') IS NULL OR json_extract(entry_json,'$.release_id')=?)
            ORDER BY length(term) DESC,id LIMIT 501""",
            (request.system, request.release_id),
        ).fetchall(),
    )
    if len(rows) > 500:
        raise ArtifactError("synonym inventory exceeds bound")
    return rewrite_requests(request, rows)


def rewrite_requests(
    request: SearchRequest, rows: list[tuple[str, str, int]]
) -> list[tuple[SearchRequest, str]]:
    if len(rows) > 500:
        raise ArtifactError("synonym inventory exceeds bound")
    if request.query.code_like or request.language != "pl":
        return []
    folded = fold_polish(request.query.effective_text)
    result: list[tuple[SearchRequest, str]] = []
    for term, wording, whole_query_only in rows:
        if whole_query_only and folded != term:
            continue
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
