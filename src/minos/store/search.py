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
    SupportingContext,
    fold_polish,
    prefix_upper_bound,
    use_token_prefix,
)
from minos.store.query_budget import QueryBudget, query_budget
from minos.store.reference_build import reference_sql
from minos.store.search_jgp import membership_counts
from minos.store.search_synonyms import synonym_requests

_PROTECTED_WORDS = frozenset(
    ("bez", "nie", "z", "typ", "without", "with", "not", "type")
)


def search_reference(
    connection: sqlite3.Connection,
    request: SearchRequest,
    *,
    analyzer: PolishMorphology | None = None,
) -> SearchResult:
    # Latency is a target, not a reason to discard an otherwise valid 500 ms
    # result. Keep the operation/output bounds and a finite recovery deadline.
    with query_budget(connection, seconds=2.0) as budget:
        search_version = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT value FROM build_metadata WHERE key='search_version'"
            ).fetchone(),
        )
        if search_version not in (("lexical-2",), (SEARCH_VERSION,)):
            raise ArtifactError("incompatible reference search version")
        if connection.execute(
            "SELECT value FROM build_metadata WHERE key='morphology_version'"
        ).fetchone() != (MORPHOLOGY_VERSION,):
            raise ArtifactError("incompatible reference morphology version")
        release = _resolve_release(connection, request)
        index = _select_index(connection, request.index)
        request = replace(request, release_id=release)
        counts, day = membership_counts(connection, request)
        selected, morphology = _select_matches(
            connection, request, index, analyzer, counts
        )
        selected, rewrites = _add_synonyms(
            connection, request, index, analyzer, counts, selected
        )
        hits: list[SearchHit] = []
        characters = 0
        for ordinal, document, match_type in selected[: request.limit]:
            budget.check()
            hit = _hydrate_hit(connection, release, ordinal, document, match_type)
            hit = replace(
                hit,
                jgp_group_count=counts.get(hit.code or "", 0),
                jgp_service_date=day,
                synonym_source_wording=rewrites.get(ordinal),
            )
            characters += len(hit.matched_text) + sum(
                len(node.label) for node in hit.context_nodes
            )
            characters += sum(
                len(name) + len(text) for name, text in hit.source_attributes
            )
            characters += sum(
                len(name) + len(text) for name, text in hit.matched_source_attributes
            )
            characters += sum(
                len(node.label)
                for context in hit.supporting_contexts
                for node in context.nodes
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
            search_version[0],
        )


def _add_synonyms(
    connection: sqlite3.Connection,
    request: SearchRequest,
    index: str,
    analyzer: PolishMorphology | None,
    counts: dict[str, int],
    original: list[tuple[int, int, MatchType]],
) -> tuple[list[tuple[int, int, MatchType]], dict[int, str]]:
    selected = [
        row for row in original if row[2] in ("exact_code", "exact_description")
    ]
    known = {row[0] for row in selected}
    literal = [row for row in original if row[0] not in known]
    # Rewrites rank before literal matches but never push them off the page:
    # up to half of it stays reserved for what the user actually typed.
    slots = request.limit - min(len(literal), request.limit // 2)
    overflow: list[tuple[int, int, MatchType]] = []
    rewrites: dict[int, str] = {}
    for synonym_request, wording in synonym_requests(connection, request):
        alternatives, _ = _select_matches(
            connection, synonym_request, index, analyzer, counts
        )
        for ordinal, document, _match_type in alternatives:
            if ordinal in known:
                continue
            known.add(ordinal)
            rewrites[ordinal] = wording
            target = selected if len(selected) < slots else overflow
            target.append((ordinal, document, "synonym_tokens"))
        if len(selected) + len(overflow) > request.limit:
            break
    selected.extend(row for row in literal if row[0] not in rewrites)
    selected.extend(overflow)
    return selected[: request.limit + 1], rewrites


def _resolve_release(connection: sqlite3.Connection, request: SearchRequest) -> str:
    rows = cast(
        list[tuple[str]],
        connection.execute(
            """SELECT id FROM source_release WHERE system=? AND (? IS NULL OR id=?)
            AND id NOT IN (SELECT key FROM json_each(coalesce(
                (SELECT value FROM build_metadata WHERE key='supporting_sources'), '{}')))
            ORDER BY id LIMIT 2""",
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
    connection: sqlite3.Connection,
    request: SearchRequest,
    index: str,
    analyzer: PolishMorphology | None,
    counts: dict[str, int],
) -> tuple[list[tuple[int, int, MatchType]], tuple[tuple[str, tuple[str, ...]], ...]]:
    parameters = _parameters(request)
    parameters["jgp_counts"] = json.dumps(counts)
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
        # Offline callers load on demand; the HTTP pool supplies a warm analyzer.
        analyzer = analyzer or PolishMorphology()
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
    if (
        len(selected) < min(5, request.limit)
        and request.language == "pl"
        and not request.query.code_like
        # Typed diacritics are deliberate: folding them would make "rąk"
        # (hands) retrieve "rak" (cancer) under a "no diacritics" label.
        and fold_polish(request.query.effective_text) == request.query.effective_text
        and connection.execute("PRAGMA user_version").fetchone()[0] >= 8
    ):
        analyzer = analyzer or PolishMorphology()
        parameters["excluded"] = json.dumps([item[0] for item in selected])
        parameters["row_limit"] = request.limit + 1 - len(selected)
        parameters["folded_ranges"] = _folded_ranges(request, analyzer)
        parameters["folded_text"] = fold_polish(request.query.effective_text)
        parameters["folded_token_count"] = len(request.query.tokens)
        rows = cast(
            list[tuple[int, int]],
            connection.execute(
                reference_sql("queries/search_folded.sql"), parameters
            ).fetchall(),
        )
        selected.extend(
            (ordinal, document, "folded_tokens") for ordinal, document in rows
        )
    return selected, morphology


def _folded_ranges(request: SearchRequest, analyzer: PolishMorphology) -> str:
    ranges: list[dict[str, object]] = []
    for position, token in enumerate(request.query.tokens):
        variants = sorted(
            {
                fold_polish(token),
                *(fold_polish(value) for value in analyzer.variants(token)),
            }
        )
        for value in variants:
            ranges.append(
                {
                    "position": position,
                    "lower": value,
                    "upper": prefix_upper_bound(value),
                    "prefix": use_token_prefix(token) and value == fold_polish(token),
                }
            )
    return json.dumps(ranges)


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
    attributes = _source_attributes(connection, release, ordinal)
    matched_attributes = (
        attributes
        if matched[0] == ordinal
        else _source_attributes(connection, release, matched[0])
    )
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
        attributes,
        matched_attributes,
        _supporting_contexts(connection, release, target.code),
    )


def _supporting_contexts(
    connection: sqlite3.Connection, release: str, code: str | None
) -> tuple[SupportingContext, ...]:
    if code is None:
        return ()
    rows = cast(
        list[tuple[str, int]],
        connection.execute(
            """SELECT concept.source_release_id, concept.node_ordinal
            FROM concept JOIN json_each(coalesce(
                (SELECT value FROM build_metadata WHERE key='supporting_sources'), '{}')) AS policy
            ON policy.key=concept.source_release_id
            WHERE policy.value=? AND concept.lookup_code=?
            ORDER BY concept.source_release_id LIMIT 17""",
            (release, code),
        ).fetchall(),
    )
    if len(rows) > 16:
        raise QueryLimitError("supporting context exceeds source limit")
    contexts: list[SupportingContext] = []
    for source, ordinal in rows:
        nodes, edges = _context(connection, source, ordinal)
        contexts.append(SupportingContext(source, ordinal, nodes, edges))
    return tuple(contexts)


def _source_attributes(
    connection: sqlite3.Connection, release: str, ordinal: int
) -> tuple[tuple[str, str], ...]:
    attributes = cast(
        list[tuple[str, str]],
        connection.execute(
            reference_sql("queries/search_source_attributes.sql"),
            {"release_id": release, "ordinal": ordinal},
        ).fetchall(),
    )
    if len(attributes) > 16:
        raise QueryLimitError("search source attributes exceed limit")
    return tuple(attributes)


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
