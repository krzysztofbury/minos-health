"""Release-scoped lexical search with exact source evidence and graph context."""

import json
import sqlite3
from dataclasses import replace
from typing import cast

from rapidfuzz.distance import Levenshtein
from rapidfuzz.process import extract

from minos.domain.morphology import MORPHOLOGY_VERSION, PolishMorphology
from minos.domain.reference import ArtifactError, NodeKind, QueryLimitError
from minos.domain.search import (
    SEARCH_VERSION,
    ContextNode,
    MatchType,
    SearchHit,
    SearchInputError,
    SearchRequest,
    SearchResult,
    SpellingSuggestion,
    prefix_upper_bound,
    use_token_prefix,
)
from minos.store.query_budget import QueryBudget, query_budget
from minos.store.reference_build import reference_sql

_PROTECTED_WORDS = frozenset(
    ("bez", "nie", "z", "typ", "without", "with", "not", "type")
)


def search_reference(
    connection: sqlite3.Connection, request: SearchRequest
) -> SearchResult:
    with query_budget(connection) as budget:
        if connection.execute(
            "SELECT value FROM build_metadata WHERE key='search_version'"
        ).fetchone() != (SEARCH_VERSION,):
            raise ArtifactError("incompatible reference search version")
        if connection.execute(
            "SELECT value FROM build_metadata WHERE key='morphology_version'"
        ).fetchone() != (MORPHOLOGY_VERSION,):
            raise ArtifactError("incompatible reference morphology version")
        release = _resolve_release(connection, request)
        index = _select_index(connection, request.index)
        request = replace(request, release_id=release)
        selected, morphology = _select_matches(connection, request, index)
        hits: list[SearchHit] = []
        characters = 0
        for ordinal, document, match_type in selected[: request.limit]:
            budget.check()
            hit = _hydrate_hit(connection, release, ordinal, document, match_type)
            characters += len(hit.matched_text) + sum(
                len(node.label) for node in hit.context_nodes
            )
            if characters > 512 * 1024:
                raise QueryLimitError("search output exceeds character limit")
            hits.append(hit)
        suggestions = _spelling(connection, request, budget) if not selected else ()
        return SearchResult(
            request.system,
            release,
            request.language,
            request.query,
            tuple(hits),
            len(selected) > request.limit,
            suggestions,
            index,
            morphology,
        )


def _resolve_release(connection: sqlite3.Connection, request: SearchRequest) -> str:
    rows = cast(
        list[tuple[str]],
        connection.execute(
            "SELECT id FROM source_release WHERE system=? AND (? IS NULL OR id=?) ORDER BY id LIMIT 2",
            (request.system, request.release_id, request.release_id),
        ).fetchall(),
    )
    if not rows:
        raise SearchInputError("terminology system or release is not in the dataset")
    if len(rows) != 1:
        raise SearchInputError("multiple releases require explicit selection")
    return rows[0][0]


def _select_index(connection: sqlite3.Connection, requested: str) -> str:
    if requested == "portable":
        return "portable"
    try:
        _ = cast(
            tuple[str] | None, connection.execute("SELECT fts5_source_id()").fetchone()
        )
    except sqlite3.OperationalError as error:
        if str(error) == "no such function: fts5_source_id":
            return "portable"
        raise
    return "fts5"


def _parameters(request: SearchRequest) -> dict[str, str | int]:
    tokens = tuple(dict.fromkeys(request.query.tokens))
    ranges = [
        {
            "lower": token,
            "upper": prefix_upper_bound(token),
            "prefix": use_token_prefix(token),
        }
        for token in tokens
    ]
    match = " AND ".join(
        '"' + token + '"' + ("*" if use_token_prefix(token) else "") for token in tokens
    )
    return {
        "release_id": request.release_id or "",
        "language": request.language,
        "text": request.query.effective_text,
        "literal_text": request.query.literal_text,
        "code_key": request.query.code_key,
        "code_upper": prefix_upper_bound(request.query.code_key),
        "code_like": int(request.query.code_like),
        "ranges": json.dumps(ranges),
        "match": "token_text : (" + match + ")",
        "excluded": "[]",
        "row_limit": request.limit + 1,
    }


def _select_matches(
    connection: sqlite3.Connection, request: SearchRequest, index: str
) -> tuple[list[tuple[int, int, MatchType]], tuple[tuple[str, tuple[str, ...]], ...]]:
    parameters = _parameters(request)
    selected: list[tuple[int, int, MatchType]] = []
    morphology: tuple[tuple[str, tuple[str, ...]], ...] = ()
    stages: tuple[tuple[str, MatchType], ...] = (
        ("search_exact_code.sql", "exact_code"),
        ("search_exact_description.sql", "exact_description"),
        (
            "search_fts.sql" if index == "fts5" else "search_portable.sql",
            "prefix_tokens",
        ),
    )
    for filename, match_type in stages:
        parameters["excluded"] = json.dumps([item[0] for item in selected])
        parameters["row_limit"] = request.limit + 1 - len(selected)
        rows = cast(
            list[tuple[int, int]],
            connection.execute(
                reference_sql("queries/" + filename), parameters
            ).fetchall(),
        )
        selected.extend((ordinal, document, match_type) for ordinal, document in rows)
        if len(selected) > request.limit:
            break
    if (
        len(selected) <= request.limit
        and request.language == "pl"
        and not request.query.code_like
    ):
        parameters["excluded"] = json.dumps([item[0] for item in selected])
        parameters["row_limit"] = request.limit + 1 - len(selected)
        analyzer = PolishMorphology()
        morphology = tuple(
            (token, analyzer.variants(token))
            for token in dict.fromkeys(request.query.tokens)
        )
        parameters["variants"] = json.dumps(
            [variants for _token, variants in morphology]
        )
        # Both runtimes use the same indexed lemma relation, independent of FTS5.
        rows = cast(
            list[tuple[int, int]],
            connection.execute(
                reference_sql("queries/search_morphology.sql"), parameters
            ).fetchall(),
        )
        selected.extend(
            (ordinal, document, "morphology_tokens") for ordinal, document in rows
        )
    if (
        not selected
        and len(request.query.effective_text) >= 3
        and not request.query.code_like
    ):
        rows = cast(
            list[tuple[int, int]],
            connection.execute(
                reference_sql("queries/search_substring.sql"), parameters
            ).fetchall(),
        )
        selected.extend((ordinal, document, "substring") for ordinal, document in rows)
    return selected, morphology


def _hydrate_hit(
    connection: sqlite3.Connection,
    release: str,
    ordinal: int,
    document: int,
    match_type: MatchType,
) -> SearchHit:
    matched = cast(
        tuple[int, str, str, str] | None,
        connection.execute(
            reference_sql("queries/search_document.sql"),
            {"release_id": release, "document_id": document},
        ).fetchone(),
    )
    if matched is None:
        raise ArtifactError("search evidence is unresolved")
    nodes, edges = _context(connection, release, matched[0])
    target = next((node for node in nodes if node.ordinal == ordinal), None)
    if target is None:
        raise ArtifactError("search attribution is outside source context")
    return SearchHit(
        ordinal,
        target.code,
        target.source_code,
        target.label,
        target.kind,
        match_type,
        matched[0],
        matched[3],
        matched[2],
        matched[1],
        nodes,
        edges,
    )


def _context(
    connection: sqlite3.Connection, release: str, ordinal: int
) -> tuple[tuple[ContextNode, ...], tuple[tuple[int, int], ...]]:
    ancestors = cast(
        list[tuple[int, int, str, str, str]],
        connection.execute(
            reference_sql("queries/ancestors.sql"),
            {
                "release_id": release,
                "ordinal": ordinal,
                "relation_type": "contains",
                "max_depth": 64,
                "row_limit": 257,
            },
        ).fetchall(),
    )
    if len(ancestors) > 256 or any(row[1] > 64 for row in ancestors):
        raise QueryLimitError("search context exceeds graph limits")
    parameters = {
        "release_id": release,
        "ordinals": json.dumps([ordinal, *(row[0] for row in ancestors)]),
    }
    rows = cast(
        list[tuple[int, str | None, str, str, str]],
        connection.execute(
            reference_sql("queries/search_context_nodes.sql"), parameters
        ).fetchall(),
    )
    nodes = tuple(
        ContextNode(row[0], row[1], row[2], row[3], cast(NodeKind, row[4]))
        for row in rows
    )
    edges = cast(
        list[tuple[int, int]],
        connection.execute(
            reference_sql("queries/search_context_edges.sql"), parameters
        ).fetchall(),
    )
    if len(edges) > 1024:
        raise QueryLimitError("search context exceeds edge limit")
    return nodes, tuple(edges)


def _spelling(
    connection: sqlite3.Connection, request: SearchRequest, budget: QueryBudget
) -> tuple[SpellingSuggestion, ...]:
    if request.query.code_like:
        return ()
    suggestions: list[SpellingSuggestion] = []
    for token in dict.fromkeys(request.query.tokens):
        if (
            len(token) < 4
            or token in _PROTECTED_WORDS
            or any(character.isdecimal() for character in token)
        ):
            continue
        if (
            connection.execute(
                "SELECT 1 FROM search_word WHERE source_release_id=? AND language=? AND word=?",
                (request.release_id, request.language, token),
            ).fetchone()
            is not None
        ):
            continue
        maximum = 1 if len(token) < 8 else 2
        words = _spelling_candidates(connection, request, token, maximum)
        if len(words) > 100_000:
            raise QueryLimitError("spelling vocabulary exceeds candidate limit")
        budget.check()
        matches = extract(
            token,
            [word for (word,) in words if word not in _PROTECTED_WORDS],
            scorer=Levenshtein.distance,
            processor=None,
            score_cutoff=maximum,
            limit=3,
        )
        budget.check()
        for word, distance, _index in matches:
            # A replacement is an explicit suggestion; it never runs a second hidden search.
            effective = " ".join(
                word if item == token else item for item in request.query.tokens
            )
            suggestions.append(SpellingSuggestion(effective, word, int(distance)))
    suggestions.sort(key=lambda item: (item.distance, item.replacement, item.query))
    return tuple(suggestions[:3])


def _spelling_candidates(
    connection: sqlite3.Connection, request: SearchRequest, token: str, maximum: int
) -> list[tuple[str]]:
    return cast(
        list[tuple[str]],
        connection.execute(
            "SELECT word FROM search_word WHERE source_release_id=? AND language=? AND characters BETWEEN ? AND ? ORDER BY word LIMIT 100001",
            (
                request.release_id,
                request.language,
                len(token) - maximum,
                len(token) + maximum,
            ),
        ).fetchall(),
    )
