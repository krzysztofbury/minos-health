"""Bounded offline search of dated catalog names, independent of conditions.

The immutable source-name index is built once per bundle, before readiness.
Only query tokens are analyzed at request time; no medical query is cached.
"""

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import cast

from minos.domain.morphology import PolishMorphology
from minos.domain.normalization import normalize_description
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchQuery, fold_polish, text_tokens, use_token_prefix
from minos.store.jgp_ruleset import MAX_INDEX_GROUPS, resolve_ruleset
from minos.store.query_budget import QueryBudget


@dataclass(frozen=True)
class GroupName:
    code: str
    name: str
    catalog: str
    start: str
    end: str | None
    tokens: frozenset[str]
    lemmas: frozenset[str]
    folded: frozenset[str]


type GroupIndex = Mapping[str, tuple[GroupName, ...]]


def build_group_index(
    connection: sqlite3.Connection, analyzer: PolishMorphology
) -> GroupIndex:
    rows = cast(
        list[tuple[str, str, str, str, str, str | None]],
        connection.execute(
            """SELECT release_id, code, name, catalog, service_from, service_to
            FROM group_definition ORDER BY release_id, catalog, source_row, code
            LIMIT 20001"""
        ).fetchall(),
    )
    if len(rows) > 20_000:
        raise ArtifactError("group search index exceeds row bound")
    index: dict[str, list[GroupName]] = {}
    vocabulary: dict[str, tuple[str, ...]] = {}
    characters = 0
    for release, code, name, catalog, start, end in rows:
        characters += len(name)
        if characters > 8 * 1024 * 1024 or len(name) > 4096:
            raise ArtifactError("group search index exceeds text bound")
        tokens = frozenset(text_tokens(normalize_description(name)))
        lemmas: set[str] = set()
        for token in tokens:
            if token not in vocabulary:
                vocabulary[token] = analyzer.variants(token)
            lemmas.update(vocabulary[token])
        groups = index.setdefault(release, [])
        groups.append(
            GroupName(
                code,
                name,
                catalog,
                start,
                end,
                tokens,
                frozenset(lemmas),
                frozenset(map(fold_polish, lemmas)),
            )
        )
        if len(groups) > MAX_INDEX_GROUPS:
            raise ArtifactError("group search release exceeds row bound")
    return MappingProxyType(
        {release: tuple(groups) for release, groups in index.items()}
    )


def _literal(tokens: tuple[str, ...], words: frozenset[str]) -> bool:
    return all(
        token in words
        or (use_token_prefix(token) and any(word.startswith(token) for word in words))
        for token in tokens
    )


def _match(
    group: GroupName,
    query: SearchQuery,
    variants: tuple[tuple[str, ...], ...],
    folded: tuple[frozenset[str], ...],
) -> tuple[int, str] | None:
    if group.code == query.code_key:
        return 0, "exact_code"
    if group.code.startswith(query.code_key):
        return 1, "prefix_tokens"
    if not query.code_like and _literal(query.tokens, group.tokens):
        return 2, "prefix_tokens"
    if variants and all(group.lemmas.intersection(values) for values in variants):
        return 3, "morphology_tokens"
    # As in ICD search, deliberately typed diacritics are not broadened.
    if (
        folded
        and fold_polish(query.effective_text) == query.effective_text
        and all(
            group.folded.intersection(values)
            or (
                use_token_prefix(token)
                and any(word.startswith(token) for word in group.folded)
            )
            for token, values in zip(query.tokens, folded, strict=True)
        )
    ):
        return 4, "folded_tokens"
    return None


def search_groups(
    connection: sqlite3.Connection,
    index: GroupIndex,
    query: SearchQuery,
    day: date,
    analyzer: PolishMorphology,
    *,
    limit: int = 10,
) -> dict[str, object]:
    if not 1 <= limit <= 20:
        raise ValueError("invalid group search limit")
    # The resolver's bounded SQL stages retain their own operation limits. This
    # deadline additionally covers their total time and all Python matching.
    budget = QueryBudget(seconds=0.4)
    ruleset = resolve_ruleset(connection, day, None)
    budget.check()
    release = cast(str, ruleset["release_id"])
    if release not in index:
        raise ArtifactError("resolved catalog has no group search index")
    date_text = day.isoformat()
    variants = (
        tuple(analyzer.variants(token) for token in query.tokens)
        if not query.code_like
        else ()
    )
    folded = tuple(frozenset(map(fold_polish, values)) for values in variants)
    matches: list[tuple[int, int, GroupName, str]] = []
    fallback: list[tuple[int, int, GroupName, str]] = []
    seen: set[str] = set()
    for ordinal, group in enumerate(index[release]):
        budget.check()
        if group.start > date_text or (group.end is not None and group.end < date_text):
            continue
        if group.code in seen:
            raise ArtifactError("several catalog rows apply to one code and date")
        seen.add(group.code)
        match = _match(group, query, variants, folded)
        if match is not None:
            rank, kind = match
            target = fallback if rank == 4 else matches
            target.append((rank, ordinal, group, kind))
    if len(matches) < min(5, limit):
        matches.extend(fallback)
    matches.sort(key=lambda item: (item[0], item[1], item[2].code))
    answer: dict[str, object] = {
        "api": 1,
        "system": "jgp-szp",
        "status": "ok",
        "service_date": date_text,
        "ruleset": ruleset,
        "results": [
            {
                "code": group.code,
                "label": group.name,
                "catalog": group.catalog,
                "act_number": ruleset["act_number"],
                "match_type": kind,
                "href": f"/jgp/{date_text}/{group.code}",
            }
            for _rank, _ordinal, group, kind in matches[:limit]
        ],
        "truncated": len(matches) > limit,
        "suggestions": [],
        "morphology": [
            {"token": token, "alternatives": values}
            for token, values in zip(query.tokens, variants, strict=False)
        ],
    }
    budget.check()
    return answer
