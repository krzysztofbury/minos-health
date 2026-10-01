"""Bounded CZS ICD-9 PL XML adapter, preserving status without inferring parents."""

from collections import deque
from collections.abc import Iterator
from datetime import date, datetime
from typing import Annotated, BinaryIO, ClassVar, Literal, NoReturn, final
from xml.parsers import expat

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from minos.domain.normalization import normalize_description
from minos.domain.reference import (
    ConceptDescription,
    ReferenceNode,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd10 import CHUNK_SIZE_BYTES, ReferenceEvent, XmlLimits
from minos.ingest.reference_manifest import SourceManifest

DEFAULT_ICD9_XML_LIMITS = XmlLimits(
    max_bytes=16 * 1024 * 1024,
    max_depth=3,
    max_elements=200_001,
    max_attribute_characters=65_536,
)
type _Timestamp = Annotated[
    str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
]
type _Date = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]
type _Text = Annotated[str, Field(max_length=65_536)]


class _Record(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class _Root(_Record):
    typ: Literal["ICD9"]
    wersja: Literal["1.1"]
    symbol: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,2}\.[0-9]{1,3}$")]
    edycja: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,2}$")]
    instytucja: Literal["NFZ"]
    czas_pub: _Timestamp
    nr_gen: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,5}$")]
    czas_gen: _Timestamp

    @field_validator("czas_pub", "czas_gen")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        _ = datetime.fromisoformat(value)
        return value


class _Entry(_Record):
    kod: Annotated[
        str,
        Field(pattern=r"^(?:[A-Z]{1,2}|[A-Z][0-9]{2,3}|[0-9]{2,3}(?:\.[0-9]{1,5})?)$"),
    ]
    nazwa: Annotated[str, Field(min_length=1, max_length=65_536)]
    status: Literal["A", "N"]
    opis: _Text | None = None
    wlaczenia: _Text | None = None
    wylaczenia: _Text | None = None

    @field_validator("nazwa")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("empty name")
        return value


class _Dates(_Record):
    data_od: _Date
    data_do: _Date | None = None

    @field_validator("data_od", "data_do")
    @classmethod
    def validate_date(cls, value: str | None) -> str | None:
        if value is not None:
            _ = date.fromisoformat(value)
        return value


@final
class _CzsXmlState:
    def __init__(self, source: SourceManifest, limits: XmlLimits) -> None:
        self.source = source
        self.limits = limits
        self.parser = expat.ParserCreate(namespace_separator="}")
        self.parser.StartElementHandler = self.start
        self.parser.EndElementHandler = self.end
        self.parser.CharacterDataHandler = self.text
        self.parser.StartDoctypeDeclHandler = self.reject
        self.parser.EntityDeclHandler = self.reject
        self.parser.ProcessingInstructionHandler = self.reject
        self.parser.ExternalEntityRefHandler = self.reject_external
        self.parser.StartNamespaceDeclHandler = self.reject_namespace
        self.parser.XmlDeclHandler = self.xml_declaration
        self.parser.CommentHandler = self.comment
        self.stack: list[str] = []
        self.text_counts: list[int] = []
        self.events: deque[ReferenceEvent] = deque()
        self.elements = 0
        self.nodes = 0
        self.codes: set[str] = set()
        self.entry: _Entry | None = None
        self.entry_attributes: dict[str, str] = {}
        self.dates: dict[str, str] | None = None
        self.closed = False

    def fail(self, reason: str) -> NoReturn:
        raise SourceError(
            f"{reason} (line {self.parser.CurrentLineNumber}, node {self.nodes})"
        )

    def reject(self, *_arguments: object) -> None:
        self.fail("prohibited XML declaration or processing instruction")

    def reject_external(self, *_arguments: object) -> int:
        self.fail("prohibited external XML entity")

    def reject_namespace(self, *_arguments: object) -> None:
        self.fail("unsupported ICD-9 root namespace")

    def xml_declaration(
        self, _version: str, encoding: str | None, _standalone: int
    ) -> None:
        if encoding is not None and encoding.casefold() != "utf-8":
            self.fail("unsupported XML encoding")

    def comment(self, value: str) -> None:
        if len(value) > self.limits.max_text_characters:
            self.fail("XML comment exceeds text limit")

    def start(self, name: str, attributes: dict[str, str]) -> None:
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
        if not self.stack:
            self._root(name, attributes)
        elif self.stack == ["komunikat"] and name == "pozycja_slownika":
            self._entry(attributes)
        elif self.stack == ["komunikat", "pozycja_slownika"] and name == "daty_proc":
            self._dates(attributes)
        else:
            self.fail("unsupported ICD-9 XML structure")
        self.stack.append(name)
        self.text_counts.append(0)

    def _root(self, name: str, attributes: dict[str, str]) -> None:
        if name != "komunikat" or self.closed:
            self.fail("unsupported ICD-9 XML root")
        try:
            root = _Root.model_validate(attributes)
        except ValidationError:
            self.fail("unsupported ICD-9 root attributes")
        if root.edycja != root.symbol.split(".", 1)[0]:
            self.fail("ICD-9 edition conflicts with version")
        if self.source.system != "icd9-pl" or self.source.origin != "czs":
            self.fail("ICD-9 manifest adapter conflicts")
        if root.symbol != self.source.source_version:
            self.fail("ICD-9 version conflicts with manifest")
        if "ICD9:" + root.nr_gen != self.source.source_file_id:
            self.fail("ICD-9 generation conflicts with manifest")
        expected_url = (
            "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen="
            + root.nr_gen
        )
        if self.source.source_url != expected_url:
            self.fail("ICD-9 source URL conflicts with manifest")
        self.events.append(SourceHeader(tuple(sorted(attributes.items())), ()))

    def _entry(self, attributes: dict[str, str]) -> None:
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            self.fail("XML node count exceeds limit")
        try:
            entry = _Entry.model_validate(attributes)
        except ValidationError:
            self.fail("unsupported ICD-9 entry attributes")
        if entry.kod in self.codes:
            self.fail("duplicate ICD-9 source code")
        self.codes.add(entry.kod)
        self.entry = entry
        self.entry_attributes = attributes
        self.dates = None

    def _dates(self, attributes: dict[str, str]) -> None:
        if self.dates is not None:
            self.fail("duplicate ICD-9 date structure")
        try:
            dates = _Dates.model_validate(attributes)
        except ValidationError:
            self.fail("invalid ICD-9 date attributes")
        if dates.data_do is not None and dates.data_do < dates.data_od:
            self.fail("invalid ICD-9 procedure period")
        self.dates = attributes

    def text(self, value: str) -> None:
        if value.strip():
            self.fail("unsupported ICD-9 XML text")
        if self.text_counts:
            self.text_counts[-1] += len(value)
            if self.text_counts[-1] > self.limits.max_text_characters:
                self.fail("XML text exceeds limit")

    def end(self, name: str) -> None:
        _ = self.stack.pop()
        _ = self.text_counts.pop()
        if name == "pozycja_slownika":
            if self.entry is None:
                self.fail("incomplete ICD-9 entry")
            entry = self.entry
            attributes = {
                key: value
                for key, value in self.entry_attributes.items()
                if key not in ("kod", "nazwa")
            }
            if self.dates is not None:
                attributes.update(self.dates)
            self.events.append(
                ReferenceNode(
                    self.nodes,
                    entry.kod,
                    entry.nazwa,
                    tuple(sorted(attributes.items())),
                    "classification",
                    entry.kod,
                    (
                        ConceptDescription(
                            "pl",
                            "preferred",
                            entry.nazwa,
                            normalize_description(entry.nazwa),
                        ),
                    ),
                )
            )
            self.entry = None
            self.entry_attributes = {}
            self.dates = None
        elif name == "komunikat":
            if not self.nodes:
                self.fail("empty ICD-9 catalog")
            self.closed = True


def parse_icd9_xml(
    source: BinaryIO,
    manifest: SourceManifest,
    *,
    limits: XmlLimits = DEFAULT_ICD9_XML_LIMITS,
) -> Iterator[ReferenceEvent]:
    """Consume through EOF before committing; source order never implies parents."""
    state = _CzsXmlState(manifest, limits)
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
        raise SourceError("ICD-9 source read failed") from error
    if not state.closed or state.stack:
        state.fail("incomplete ICD-9 XML source")
