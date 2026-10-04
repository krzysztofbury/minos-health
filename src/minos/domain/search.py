"""Deterministic lexical query interpretation and source-grounded results."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Literal

from minos.domain.normalization import normalize_description
from minos.domain.reference import NodeKind, ReferenceError

SEARCH_VERSION = "lexical-3"
_POLISH_FOLD = str.maketrans("ąćęłńóśźż", "acelnoszz")
IndexMode = Literal["auto", "portable"]
MatchType = Literal[
    "exact_code",
    "exact_description",
    "prefix_tokens",
    "morphology_tokens",
    "substring",
    "folded_tokens",
    "synonym_tokens",
]


class SearchInputError(ReferenceError):
    """The lookup request is invalid, unsupported or release-ambiguous."""


@dataclass(frozen=True)
class SearchQuery:
    literal_text: str
    effective_text: str
    tokens: tuple[str, ...]
    code_key: str
    code_like: bool
    interpretation: tuple[str, ...]


@dataclass(frozen=True)
class SearchRequest:
    system: str
    query: SearchQuery
    release_id: str | None = None
    language: str = "pl"
    limit: int = 20
    index: IndexMode = "auto"
    service_date: date | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,79}", self.system):
            raise SearchInputError("invalid terminology system")
        if self.release_id is not None and not 1 <= len(self.release_id) <= 80:
            raise SearchInputError("invalid release identity")
        if self.language not in ("pl", "en"):
            raise SearchInputError("unsupported search language")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise SearchInputError("invalid result limit")
        if self.index not in ("auto", "portable"):
            raise SearchInputError("unsupported search index")


@dataclass(frozen=True)
class ContextNode:
    ordinal: int
    code: str | None
    source_code: str
    label: str
    kind: NodeKind


@dataclass(frozen=True)
class SupportingContext:
    release_id: str
    node_ordinal: int
    nodes: tuple[ContextNode, ...]
    edges: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class SearchHit:
    node_ordinal: int
    code: str | None
    source_code: str
    label: str
    kind: NodeKind
    match_type: MatchType
    matched_node_ordinal: int
    matched_text: str
    description_type: str
    language: str
    context_nodes: tuple[ContextNode, ...]
    context_edges: tuple[tuple[int, int], ...]
    source_attributes: tuple[tuple[str, str], ...] = ()
    matched_source_attributes: tuple[tuple[str, str], ...] = ()
    supporting_contexts: tuple[SupportingContext, ...] = ()
    jgp_group_count: int = 0
    jgp_service_date: date | None = None
    synonym_source_wording: str | None = None


@dataclass(frozen=True)
class SpellingSuggestion:
    query: str
    replacement: str
    distance: int


@dataclass(frozen=True)
class SearchResult:
    system: str
    release_id: str
    language: str
    query: SearchQuery
    hits: tuple[SearchHit, ...]
    truncated: bool
    suggestions: tuple[SpellingSuggestion, ...]
    index_used: str
    morphology: tuple[tuple[str, tuple[str, ...]], ...]
    search_version: str = "lexical-2"


_WRAPPER = re.compile(
    r"^(?:pokaż|pokaz|wyświetl|wyswietl)(?: mi)? "
    + r"(?:możliwe |mozliwe )?kody(?: icd-10)? (?:dla|dotyczące)\s+"
)
# Conservative code-shaped input, not an ICD grammar or code-validity assertion.
_CODE = re.compile(r"(?=.*[0-9])[a-z0-9]+(?:[.-][a-z0-9]+)*\.?$")


def text_tokens(text: str) -> tuple[str, ...]:
    """Unicode word tokens; underscores and punctuation are separators."""
    if len(text) > 65_536:
        raise SearchInputError("search text exceeds character limit")
    return tuple(re.findall(r"[^\W_]+", text))


def index_tokens(text: str) -> tuple[str, ...]:
    words = set(text_tokens(text))
    return tuple(sorted(word for word in words if len(word) <= 64))


def fold_polish(text: str) -> str:
    """Only Polish diacritics; source values and morphology keys stay untouched."""
    return text.translate(_POLISH_FOLD)


def prepare_query(text: str, *, language: str = "pl") -> SearchQuery:
    if not 1 <= len(text) <= 256 or any(
        unicodedata.category(character).startswith("C") for character in text
    ):
        raise SearchInputError("invalid search query")
    effective = normalize_description(text)
    literal_text = effective
    interpretation: list[str] = []
    if language == "pl":
        without_wrapper = _WRAPPER.sub("", effective, count=1)
        if without_wrapper != effective:
            interpretation.append("polish_lookup_wrapper")
            effective = without_wrapper
    tokens = text_tokens(effective)
    if not 1 <= len(tokens) <= 8 or any(len(token) > 64 for token in tokens):
        raise SearchInputError("search query exceeds token limits or has no words")
    return SearchQuery(
        literal_text,
        effective,
        tokens,
        effective.upper(),
        bool(_CODE.fullmatch(effective)),
        tuple(interpretation),
    )


def prefix_upper_bound(token: str) -> str:
    return token[:-1] + chr(ord(token[-1]) + 1)


def use_token_prefix(token: str) -> bool:
    return len(token) >= 4 and not any(character.isdecimal() for character in token)
