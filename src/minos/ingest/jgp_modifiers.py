"""Bounded, fully consumed CZS MODTAR XML with exact source coefficients."""

from datetime import datetime
from typing import Annotated, BinaryIO, ClassVar, Literal, cast, final
from xml.parsers import expat

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.domain.jgp_modifiers import (
    Modifier,
    ModifierPeriod,
    ModifierRelease,
    exact_millionths,
    validate_modifier_release,
)
from minos.domain.reference import SourceError


class _Attributes(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )


class _Root(_Attributes):
    typ: Literal["CZS-PUB"]
    wersja: Literal["1.1"]
    kod_slow: Literal["MODTAR"]
    rok: Annotated[str, Field(pattern=r"^20[0-9]{2}$")]
    generation: Annotated[str, Field(alias="nr-gen", pattern=r"^[1-9][0-9]{0,5}$")]
    data_pub: Annotated[
        str, Field(pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
    ]
    data_gen: Annotated[
        str, Field(pattern=r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$")
    ]


class _Grouper(_Attributes):
    typ_grupera: Literal["SZP"]


class _Modifier(_Attributes):
    symbol: Annotated[str, Field(min_length=1, max_length=80)]
    opis: Annotated[str, Field(min_length=1, max_length=8192)]
    status: Literal["A", "N"]


class _Period(_Attributes):
    mies_od: Annotated[str, Field(pattern=r"^(?:[1-9]|1[0-2])$")]
    mies_do: Annotated[str, Field(pattern=r"^(?:[1-9]|1[0-2])$")]
    wart_wsp: Annotated[str, Field(pattern=r"^[0-9]{1,3}(?:\.[0-9]{1,6})?$")]


class _Group(_Attributes):
    # These are literal source references. C14., P42., P52_ and Z01_S occur
    # in both reviewed generations; they are never trimmed or prefix-expanded.
    kod_jgp: Annotated[str, Field(pattern=r"^[A-Z]{1,4}[0-9]{2,3}[A-Z._]{0,3}$")]


@final
class _Reader:
    def __init__(self) -> None:
        self.parser = expat.ParserCreate(namespace_separator="}")
        self.parser.StartElementHandler = self.start
        self.parser.EndElementHandler = self.end
        self.parser.CharacterDataHandler = self.text
        self.parser.StartDoctypeDeclHandler = self.reject
        self.parser.EntityDeclHandler = self.reject
        self.parser.ProcessingInstructionHandler = self.reject
        self.parser.StartNamespaceDeclHandler = self.reject
        self.parser.ExternalEntityRefHandler = self.external
        self.parser.XmlDeclHandler = self.declaration
        self.parser.CommentHandler = self.comment
        self.stack: list[str] = []
        self.elements = 0
        self.text_bytes = 0
        self.root: _Root | None = None
        self.grouper_seen = False
        self.modifier: _Modifier | None = None
        self.period: _Period | None = None
        self.groups: list[str] = []
        self.periods: list[ModifierPeriod] = []
        self.modifiers: list[Modifier] = []

    def reject(self, *_arguments: object) -> None:
        raise SourceError("prohibited MODTAR XML declaration or structure")

    def external(self, *_arguments: object) -> int:
        self.reject()
        return 0

    def declaration(
        self, _version: str, encoding: str | None, _standalone: int
    ) -> None:
        if encoding is not None and encoding.casefold() != "utf-8":
            self.reject()

    def comment(self, value: str) -> None:
        if len(value) > 8192:
            self.reject()

    def text(self, value: str) -> None:
        self.text_bytes += len(value)
        if value.strip() or self.text_bytes > 4 * 1024 * 1024:
            self.reject()

    def start(self, name: str, attributes: dict[str, str]) -> None:
        self.elements += 1
        if self.elements > 120000 or len(self.stack) >= 5 or len(attributes) > 8:
            self.reject()
        if any(len(value) > 8192 for value in attributes.values()):
            self.reject()
        path = (*self.stack, name)
        if path == ("komunikat",) and self.root is None:
            self.root = _Root.model_validate(attributes)
            _ = datetime.fromisoformat(self.root.data_pub)
            _ = datetime.fromisoformat(self.root.data_gen)
        elif path == ("komunikat", "gruper") and not self.grouper_seen:
            _ = _Grouper.model_validate(attributes)
            self.grouper_seen = True
        elif path == ("komunikat", "gruper", "modyfikatory"):
            if len(self.modifiers) >= 10000:
                self.reject()
            self.modifier = _Modifier.model_validate(attributes)
            self.periods = []
        elif path == ("komunikat", "gruper", "modyfikatory", "okres"):
            if len(self.periods) >= 24:
                self.reject()
            self.period = _Period.model_validate(attributes)
            self.groups = []
        elif path == ("komunikat", "gruper", "modyfikatory", "okres", "grupy_jgp"):
            if len(self.groups) >= 2000:
                self.reject()
            self.groups.append(_Group.model_validate(attributes).kod_jgp)
        else:
            self.reject()
        self.stack.append(name)

    def end(self, name: str) -> None:
        if self.stack.pop() != name:
            self.reject()
        if name == "okres":
            period = cast(_Period, self.period)
            self.periods.append(
                ModifierPeriod(
                    len(self.periods) + 1,
                    int(period.mies_od),
                    int(period.mies_do),
                    period.wart_wsp,
                    exact_millionths(period.wart_wsp),
                    tuple(self.groups),
                )
            )
            self.period = None
        elif name == "modyfikatory":
            modifier = cast(_Modifier, self.modifier)
            self.modifiers.append(
                Modifier(
                    len(self.modifiers) + 1,
                    modifier.symbol,
                    modifier.opis,
                    modifier.status,
                    tuple(self.periods),
                )
            )
            self.modifier = None


def parse_modifiers(source: BinaryIO, *, year: int, generation: int) -> ModifierRelease:
    reader = _Reader()
    size_bytes = 0
    try:
        while content := source.read(64 * 1024):
            # UTF-8 only. Expat honours a UTF-16/32 byte-order mark over any
            # requested encoding, and the declaration check cannot see a file
            # without a declaration, so refuse such bytes directly: XML 1.0 has
            # no NUL character, and every UTF-16/32 text contains NUL bytes.
            if (size_bytes == 0 and content.startswith((b"\xff\xfe", b"\xfe\xff"))) or (
                b"\x00" in content
            ):
                raise SourceError("MODTAR XML is not UTF-8")
            size_bytes += len(content)
            if size_bytes > 4 * 1024 * 1024:
                raise SourceError("MODTAR XML exceeds byte bound")
            _ = reader.parser.Parse(content, False)
        _ = reader.parser.Parse(b"", True)
        root = reader.root
        if root is None or not reader.grouper_seen or reader.stack:
            raise SourceError("incomplete MODTAR XML")
        if int(root.rok) != year or int(root.generation) != generation:
            raise SourceError("MODTAR year or generation conflicts with pin")
        result = ModifierRelease(
            year,
            generation,
            "SZP",
            root.wersja,
            root.data_pub,
            root.data_gen,
            tuple(reader.modifiers),
        )
        validate_modifier_release(result)
        return result
    except (expat.ExpatError, ValidationError, ValueError, TypeError) as error:
        raise SourceError("unsupported MODTAR XML") from error
