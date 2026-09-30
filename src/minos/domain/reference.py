"""Source-preserving values shared by ingestion and reference queries."""

from dataclasses import dataclass
from typing import Literal

NodeKind = Literal["classification", "range", "modifier", "text", "container"]
RelationType = Literal["contains", "is_a"]


class ReferenceError(Exception):
    """A reference ingestion, integrity or read boundary failed."""


class SourceError(ReferenceError):
    """Pinned source bytes or structure violate the reviewed contract."""


class ArtifactError(ReferenceError):
    """A reference artifact cannot be built or safely read."""


class QueryLimitError(ReferenceError):
    """A bounded query could not return a complete result."""


@dataclass(frozen=True)
class SourceHeader:
    attributes: tuple[tuple[str, str], ...]
    fields: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ReferenceNode:
    ordinal: int
    source_code: str
    source_text: str
    attributes: tuple[tuple[str, str], ...]
    kind: NodeKind
    lookup_code: str | None
    descriptions: tuple[ConceptDescription, ...] = ()


@dataclass(frozen=True)
class ReferenceRelation:
    parent_ordinal: int
    child_ordinal: int
    relation_type: RelationType


@dataclass(frozen=True)
class ConceptDescription:
    language: str
    description_type: str
    source_text: str
    normalized_text: str


@dataclass(frozen=True)
class Concept:
    release_id: str
    system: str
    node_ordinal: int
    source_code: str
    lookup_code: str
    kind: NodeKind
    descriptions: tuple[ConceptDescription, ...]


@dataclass(frozen=True)
class HierarchyNode:
    ordinal: int
    depth: int
    source_code: str
    source_text: str
    kind: NodeKind
