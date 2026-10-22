"""Bounded adapter for the RSK ICD-11 PL MMS export (#53).

The export is the WHO ICD API linearization serialized as XML: a flat list of
entities that name their parent and children by URI, in no tree order. The
adapter keeps only the fields Minos shows, links them into the published child
order and emits the existing reference events in preorder, so no schema change
is needed. The text is reproduced word for word (CC BY-ND): titles become
preferred descriptions, inclusions and index terms become source text nodes,
and definitions and exclusions stay attributes of their concept. Every concept
keeps its WHO URI, which the ICD-11 licence requires beside code and title.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO, NoReturn, cast, final, override
from xml.etree.ElementTree import (  # nosec B405 - types only; defusedxml parses.
    Element,
    ParseError,
)

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import iterparse

from minos.domain.normalization import normalize_description
from minos.domain.reference import (
    ConceptDescription,
    NodeKind,
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd10 import CHUNK_SIZE_BYTES, ReferenceEvent

MAX_BYTES = 160 * 1024 * 1024
MAX_ENTITIES = 60_000
MAX_CHILDREN = 4_000
MAX_TERMS = 1_000
MAX_TEXT_CHARACTERS = 16_384
_RELEASE = re.compile(r"20[0-9]{2}-[0-9]{2}")
_DAY = re.compile(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}")
_URI = re.compile(
    r"http://id\.who\.int/icd/release/11/(20[0-9]{2}-[0-9]{2})/mms(/[0-9a-z/]+)?"
)
_CODE = re.compile(r"[0-9A-Z]{1,6}(\.[0-9A-Z]{1,4})?")
_KINDS = frozenset(("chapter", "block", "category", "window"))
# The tree page reads this attribute to label a source text node.
INCLUSION_LABEL = "Obejmuje"


@dataclass
class _Entity:
    uri: str
    parent: str | None
    children: list[str]
    kind: str
    code: str
    title: str
    definition: str | None
    inclusions: list[str] = field(default_factory=list)
    index_terms: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)


@final
class _BoundedReader:
    """Counts bytes for iterparse, which reads the stream on its own."""

    def __init__(self, source: BinaryIO, limit: int) -> None:
        self._source = source
        self._limit = limit
        self.consumed = 0

    def read(self, size: int = CHUNK_SIZE_BYTES) -> bytes:
        chunk = self._source.read(min(size, CHUNK_SIZE_BYTES))
        if b"\x00" in chunk:
            _fail("unsupported XML encoding")
        self.consumed += len(chunk)
        if self.consumed > self._limit:
            _fail("XML bytes exceed limit")
        return chunk

    @override
    def __repr__(self) -> str:
        return "<icd11 source>"


def _fail(reason: str) -> NoReturn:
    # Structure only: no source text or path reaches the error.
    raise SourceError(f"ICD-11 {reason}")


def parse_icd11(
    source: BinaryIO, *, max_bytes: int = MAX_BYTES
) -> Iterator[ReferenceEvent]:
    """Yield the header, then nodes and relations in preorder of the MMS tree."""
    release, day, entities = _read(_BoundedReader(source, max_bytes))
    yield SourceHeader(
        (("linearization", "mms"), ("release_date", day), ("release_id", release)),
        (("languages", "pl"),),
    )
    roots = [uri for uri, entity in entities.items() if entity.parent is None]
    if len(roots) != 1:
        _fail("export does not have exactly one root")
    root = entities[roots[0]]
    if root.kind != "root":
        _fail("root entity is not the linearization")
    ordinals: dict[str, int] = {}
    next_ordinal = 1
    # Explicit stack: the MMS is shallow, but recursion depth is not reviewed.
    stack: list[tuple[str, str, int | None]] = [
        (child, root.uri, None) for child in reversed(root.children)
    ]
    while stack:
        uri, parent_uri, parent_ordinal = stack.pop()
        entity = entities.get(uri)
        if entity is None or entity.parent != parent_uri:
            _fail("child does not name its parent")
        if uri in ordinals:
            _fail("entity is reached twice")
        ordinal = next_ordinal
        ordinals[uri] = ordinal
        next_ordinal += 1
        yield _node(entity, ordinal)
        if parent_ordinal is not None:
            yield ReferenceRelation(parent_ordinal, ordinal, "contains")
        for text, label in _texts(entity):
            text_ordinal = next_ordinal
            next_ordinal += 1
            attributes = (("evidence_kind", label),) if label else ()
            yield ReferenceNode(text_ordinal, "", text, attributes, "text", None)
            yield ReferenceRelation(ordinal, text_ordinal, "contains")
        stack.extend((child, uri, ordinal) for child in reversed(entity.children))
    if len(ordinals) != len(entities) - 1:
        _fail("entity is not reachable from the root")


def _node(entity: _Entity, ordinal: int) -> ReferenceNode:
    attributes: list[tuple[str, str]] = [("uri", entity.uri)]
    if entity.definition:
        attributes.append(("opis", entity.definition))
    if entity.exclusions:
        attributes.append(("wylaczenia", "; ".join(entity.exclusions)))
    kind: NodeKind
    if entity.kind == "chapter":
        kind = "range"
    elif entity.kind == "category":
        kind = "classification"
    else:
        # Blocks and windows group categories but carry no ICD-11 code.
        return ReferenceNode(
            ordinal, "", entity.title, tuple(attributes), "container", None
        )
    description = ConceptDescription(
        "pl", "preferred", entity.title, normalize_description(entity.title)
    )
    return ReferenceNode(
        ordinal,
        entity.code,
        entity.title,
        tuple(attributes),
        kind,
        entity.code,
        (description,),
    )


def _texts(entity: _Entity) -> list[tuple[str, str | None]]:
    """Inclusions, then index terms not already shown as title or inclusion."""
    seen = {normalize_description(entity.title)}
    texts: list[tuple[str, str | None]] = []
    for value, label in [
        *((text, INCLUSION_LABEL) for text in entity.inclusions),
        *((text, None) for text in entity.index_terms),
    ]:
        key = normalize_description(value)
        if key in seen:
            continue
        seen.add(key)
        texts.append((value, label))
    return texts


def _read(reader: _BoundedReader) -> tuple[str, str, dict[str, _Entity]]:
    release: str | None = None
    day: str | None = None
    entities: dict[str, _Entity] = {}
    try:
        events = cast(
            Iterator[tuple[str, Element]],
            iterparse(reader, events=("end",), forbid_dtd=True),
        )
        for _, element in events:
            if element.tag == "releaseId":
                release = (element.text or "").strip()
            elif element.tag == "releaseDate":
                day = (element.text or "").strip()
            elif element.tag == "element":
                entity = _entity(element)
                if entity.uri in entities:
                    _fail("entity URI is duplicated")
                entities[entity.uri] = entity
                if len(entities) > MAX_ENTITIES:
                    _fail("entity count exceeds limit")
                element.clear()
    except (ParseError, DefusedXmlException) as error:
        raise SourceError("ICD-11 XML is malformed or declares entities") from error
    except OSError as error:
        raise SourceError("source read failed") from error
    if release is None or not _RELEASE.fullmatch(release):
        _fail("release identifier is invalid")
    if day is None or not _DAY.fullmatch(day):
        _fail("release date is invalid")
    for entity in entities.values():
        match = _URI.fullmatch(entity.uri)
        if match is None or match.group(1) != release:
            _fail("entity URI is not of this MMS release")
    return release, day, entities


def _entity(element: Element) -> _Entity:
    uri = _single(element, "id")
    parent = element.findtext("parent")
    children = [(child.text or "").strip() for child in element.findall("child")]
    if len(children) > MAX_CHILDREN:
        _fail("child count exceeds limit")
    if parent is None:
        if element.find("title") is not None:
            _fail("root has a title")
        return _Entity(uri, None, children, "root", "", "", None)
    kind = _single(element, "classKind")
    if kind not in _KINDS:
        _fail("class kind is unsupported")
    code = (element.findtext("code") or "").strip()
    if (kind in ("chapter", "category")) != bool(code):
        _fail("code presence does not match the class kind")
    if code and not _CODE.fullmatch(code):
        _fail("code shape is unsupported")
    entity = _Entity(
        uri,
        parent.strip(),
        children,
        kind,
        code,
        _label(element.find("title")),
        _label(element.find("definition"))
        if element.find("definition") is not None
        else None,
    )
    for name, target in (
        ("inclusion", entity.inclusions),
        ("indexTerm", entity.index_terms),
        ("exclusion", entity.exclusions),
    ):
        for item in element.findall(name):
            target.append(_label(item.find("label")))
            if len(target) > MAX_TERMS:
                _fail("term count exceeds limit")
    return entity


def _single(element: Element, tag: str) -> str:
    values = element.findall(tag)
    if len(values) != 1 or not (values[0].text or "").strip():
        _fail(f"{tag} is missing or repeated")
    return (values[0].text or "").strip()


def _label(element: Element | None) -> str:
    """One Polish value, as published."""
    if element is None:
        _fail("label is missing")
    value = element.findtext("value")
    if element.findtext("language") != "pl":
        _fail("label is not Polish")
    if value is None or not value.strip():
        _fail("label is empty")
    if len(value) > MAX_TEXT_CHARACTERS:
        _fail("label exceeds text limit")
    return value
