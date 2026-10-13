"""Bounded, source-specific streaming adapter for the reviewed RSK XML."""

import re
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import BinaryIO, NoReturn, final
from xml.parsers import expat

from minos.domain.normalization import normalize_description
from minos.domain.reference import (
    ConceptDescription,
    NodeKind,
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)

# RSK moved its export namespace with the rsk3 platform (#184): the reviewed
# 013 XML of 2026-09-22 differs from the deployed one only in this namespace,
# the root version attribute and the export link. A document uses one of them.
NAMESPACES: frozenset[str] = frozenset(
    ("http://rsk.rejestrymedyczne.csioz.gov.pl", "https://rsk3.ezdrowie.gov.pl/")
)
CHUNK_SIZE_BYTES = 16 * 1024
ROOT_ATTRIBUTES: frozenset[str] = frozenset(
    (
        "hcd",
        "visible",
        "closed",
        "version",
        "applicationdate",
        "registrationdate",
        "maxchars",
    )
)
HEADER_FIELDS = (
    "expirationreason",
    "name",
    "common_name",
    "codesformat",
    "charset",
    "minreusetime",
    "languages",
    "link",
)
ATTRIBUTE_NAMES = frozenset(("EN", "annotationPL", "annotationEN"))
type ReferenceEvent = SourceHeader | ReferenceNode | ReferenceRelation


@dataclass(frozen=True)
class XmlLimits:
    max_bytes: int = 64 * 1024 * 1024
    max_depth: int = 64
    max_elements: int = 500_000
    max_nodes: int = 100_000
    max_text_characters: int = 65_536
    max_attributes: int = 16
    max_attribute_characters: int = 256

    def __post_init__(self) -> None:
        values = (
            self.max_bytes,
            self.max_depth,
            self.max_elements,
            self.max_nodes,
            self.max_text_characters,
            self.max_attributes,
            self.max_attribute_characters,
        )
        if any(value < 1 for value in values):
            raise ValueError("XML limits must be positive")


@dataclass
class _Frame:
    name: str
    attributes: dict[str, str]
    children: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    text_characters: int = 0
    ordinal: int | None = None
    parent_ordinal: int | None = None
    source_name: str | None = None
    node_attributes: list[tuple[str, str]] = field(default_factory=list)
    kind: NodeKind | None = None
    lookup_code: str | None = None


DEFAULT_XML_LIMITS = XmlLimits()


@final
class _XmlState:
    def __init__(self, limits: XmlLimits) -> None:
        self.limits = limits
        self.parser = expat.ParserCreate(namespace_separator="}")
        self.parser.StartElementHandler = self.start
        self.parser.EndElementHandler = self.end
        self.parser.CharacterDataHandler = self.text
        self.parser.StartDoctypeDeclHandler = self.reject_declaration
        self.parser.EntityDeclHandler = self.reject_declaration
        self.parser.ExternalEntityRefHandler = self.reject_external_entity
        self.parser.ProcessingInstructionHandler = self.reject_declaration
        self.parser.XmlDeclHandler = self.xml_declaration
        self.parser.CommentHandler = self.comment
        self.parser.StartNamespaceDeclHandler = self.namespace
        self.stack: list[_Frame] = []
        self.events: deque[ReferenceEvent] = deque()
        self.header_fields: list[tuple[str, str]] = []
        self.elements = 0
        self.nodes = 0
        self.namespace_declarations = 0
        self.document_namespace: str | None = None
        self.root_closed = False

    def fail(self, reason: str) -> NoReturn:
        # Context identifies structure without echoing source text or local paths.
        raise SourceError(
            f"{reason} (line {self.parser.CurrentLineNumber}, node {self.nodes})"
        )

    def reject_declaration(self, *_arguments: object) -> None:
        self.fail("prohibited XML declaration or processing instruction")

    def reject_external_entity(self, *_arguments: object) -> int:
        self.fail("prohibited external entity")

    def xml_declaration(
        self, _version: str, encoding: str | None, _standalone: int
    ) -> None:
        if encoding is not None and encoding.casefold() != "utf-8":
            self.fail("unsupported XML encoding")

    def namespace(self, prefix: str | None, uri: str | None) -> None:
        self.namespace_declarations += 1
        if self.namespace_declarations > self.limits.max_attributes:
            self.fail("namespace declaration count exceeds limit")
        if (
            uri is None
            or uri not in NAMESPACES
            or (self.document_namespace not in (None, uri))
            or (prefix and len(prefix) > 64)
        ):
            self.fail("unsupported XML namespace declaration")
        self.document_namespace = uri

    def comment(self, value: str) -> None:
        if len(value) > self.limits.max_text_characters:
            self.fail("XML comment exceeds text limit")

    def start(self, qualified_name: str, attributes: dict[str, str]) -> None:
        self.namespace_declarations = 0
        self.elements += 1
        if self.elements > self.limits.max_elements:
            self.fail("XML element count exceeds limit")
        if len(self.stack) >= self.limits.max_depth:
            self.fail("XML depth exceeds limit")
        if len(attributes) > self.limits.max_attributes:
            self.fail("XML attribute count exceeds limit")
        if any(
            len(value) > self.limits.max_attribute_characters
            for value in attributes.values()
        ):
            self.fail("XML attribute value exceeds limit")
        if self.document_namespace is None:
            self.fail("unsupported XML namespace")
        prefix = self.document_namespace + "}"
        if not qualified_name.startswith(prefix):
            self.fail("unsupported XML namespace")
        name = qualified_name[len(prefix) :]
        self._validate_location(name, attributes)
        frame = _Frame(name, attributes)
        if name == "node":
            self._start_node(frame)
        self.stack.append(frame)

    def _validate_location(self, name: str, attributes: dict[str, str]) -> None:
        if not self.stack:
            if name != "hcd" or self.root_closed:
                self.fail("unsupported XML root")
            self._validate_root(attributes)
            return
        parent = self.stack[-1]
        allowed: dict[str, tuple[str, ...]] = {
            "hcd": (*HEADER_FIELDS, "nodes"),
            "nodes": ("node",),
            "node": ("name", "attributes", "nodes"),
            "attributes": ("attribute",),
        }
        if name not in allowed.get(parent.name, ()):
            self.fail("unsupported XML structure")
        if parent.name in ("hcd", "node"):
            if name in parent.children:
                self.fail("duplicate XML field")
            if parent.children and allowed[parent.name].index(name) <= allowed[
                parent.name
            ].index(parent.children[-1]):
                self.fail("unsupported XML field order")
            parent.children.append(name)
        expected = {"node": {"code"}, "attribute": {"name"}}.get(name, set())
        if set(attributes) != expected:
            self.fail("unsupported or missing XML attribute")
        if name == "attribute" and attributes["name"] not in ATTRIBUTE_NAMES:
            self.fail("unsupported node attribute")
        if name == "nodes" and parent.name == "hcd":
            if tuple(parent.children) != (*HEADER_FIELDS, "nodes"):
                self.fail("missing root field")
            self.events.append(
                SourceHeader(
                    tuple(sorted(parent.attributes.items())), tuple(self.header_fields)
                )
            )

    def _validate_root(self, attributes: dict[str, str]) -> None:
        if (
            frozenset(attributes) != ROOT_ATTRIBUTES
            or attributes.get("hcd") != "00CD10"
        ):
            self.fail("unsupported ICD-10 root attributes")
        if attributes["visible"] not in ("true", "false"):
            self.fail("invalid root visibility")
        for key in ("closed", "version", "maxchars"):
            if not re.fullmatch(r"[0-9]{1,9}", attributes[key]):
                self.fail("invalid root numeric value")
        for key in ("applicationdate", "registrationdate"):
            try:
                _ = date.fromisoformat(attributes[key])
            except ValueError:
                self.fail("invalid root date")

    def _start_node(self, frame: _Frame) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            self.fail("XML node count exceeds limit")
        frame.ordinal = self.nodes
        parent = self.stack[-2] if len(self.stack) >= 2 else None
        if parent is not None and parent.name == "node":
            frame.parent_ordinal = parent.ordinal
        code = frame.attributes["code"]
        if not code:
            return
        if re.fullmatch(r"[A-Z][0-9]{2}[-\u2013][A-Z][0-9]{2}", code):
            frame.kind, frame.lookup_code = "range", code.replace("\u2013", "-")
        elif re.fullmatch(r"[A-Z][0-9]{2}(\.[0-9]{1,2})?[*†]?", code):
            frame.kind, frame.lookup_code = "classification", code.rstrip("*†")
        elif re.fullmatch(r"\.[0-9][*†]?", code):
            parent_code = parent.attributes.get("code", "") if parent else ""
            if not re.fullmatch(r"[A-Z][0-9]{2}", parent_code):
                self.fail("relative code lacks a category parent")
            frame.kind, frame.lookup_code = (
                "classification",
                parent_code + code.rstrip("*†"),
            )
        elif re.fullmatch(r"[0-9]", code):
            frame.kind = "modifier"
        else:
            self.fail("unsupported ICD-10 code shape")

    def text(self, value: str) -> None:
        if not self.stack:
            if value.strip():
                self.fail("text outside XML root")
            return
        frame = self.stack[-1]
        frame.text_characters += len(value)
        if frame.text_characters > self.limits.max_text_characters:
            self.fail("XML text exceeds limit")
        if frame.name in ("hcd", "nodes", "node", "attributes"):
            if value.strip():
                self.fail("unsupported XML mixed content")
        else:
            frame.text.append(value)

    def end(self, _qualified_name: str) -> None:
        frame = self.stack.pop()
        text = "".join(frame.text)
        if frame.name == "node":
            self._end_node(frame)
        elif frame.name == "hcd":
            if tuple(frame.children) != (*HEADER_FIELDS, "nodes") or self.nodes == 0:
                self.fail("incomplete ICD-10 document")
            self.root_closed = True
        elif frame.name in HEADER_FIELDS and self.stack[-1].name == "hcd":
            if not text.strip():
                self.fail("empty root field")
            self.header_fields.append((frame.name, text))
        elif frame.name == "name" and self.stack[-1].name == "node":
            self.stack[-1].source_name = text
        elif frame.name == "attribute":
            node = self.stack[-2]
            key = frame.attributes["name"]
            if any(existing == key for existing, _ in node.node_attributes):
                self.fail("duplicate node attribute")
            node.node_attributes.append((key, text))

    def _end_node(self, frame: _Frame) -> None:
        if frame.ordinal is None or frame.source_name is None:
            self.fail("missing node identity or name")
        if frame.attributes["code"] and not frame.source_name.strip():
            self.fail("coded node has an empty name")
        if (
            not frame.attributes["code"]
            and not frame.source_name.strip()
            and "nodes" not in frame.children
        ):
            self.fail("empty container has no child structure")
        kind = frame.kind or ("text" if frame.source_name.strip() else "container")
        descriptions: list[ConceptDescription] = []
        if frame.lookup_code is not None:
            descriptions.append(
                ConceptDescription(
                    "pl",
                    "preferred",
                    frame.source_name,
                    normalize_description(frame.source_name),
                )
            )
            for name, text in frame.node_attributes:
                if name == "EN" and text.strip():
                    descriptions.append(
                        ConceptDescription(
                            "en", "translation", text, normalize_description(text)
                        )
                    )
        self.events.append(
            ReferenceNode(
                frame.ordinal,
                frame.attributes["code"],
                frame.source_name,
                tuple(frame.node_attributes),
                kind,
                frame.lookup_code,
                tuple(descriptions),
            )
        )
        if frame.parent_ordinal is not None:
            self.events.append(
                ReferenceRelation(frame.parent_ordinal, frame.ordinal, "contains")
            )


def parse_icd10(
    source: BinaryIO, *, limits: XmlLimits = DEFAULT_XML_LIMITS
) -> Iterator[ReferenceEvent]:
    """Yield bounded chunk-local events; callers must consume to EOF before commit.

    Expat is incremental, never opens network resources and has explicit DTD,
    entity and processing-instruction rejection handlers. No XML DOM is retained.
    XML's own line-ending/entity decoding is preserved, with no text cleanup.
    """
    state = _XmlState(limits)
    consumed_bytes = 0
    try:
        while chunk := source.read(CHUNK_SIZE_BYTES):
            if b"\x00" in chunk or (
                consumed_bytes == 0 and chunk[:1] in (b"\xff", b"\xfe")
            ):
                state.fail("unsupported XML encoding")
            consumed_bytes += len(chunk)
            if consumed_bytes > limits.max_bytes:
                state.fail("XML bytes exceed limit")
            _ = state.parser.Parse(chunk, False)
            while state.events:
                yield state.events.popleft()
        _ = state.parser.Parse(b"", True)
        while state.events:
            yield state.events.popleft()
    except expat.ExpatError as error:
        raise SourceError(f"malformed XML (line {error.lineno})") from error
    except OSError as error:
        raise SourceError("source read failed") from error
    if not state.root_closed or state.stack:
        state.fail("incomplete XML source")
