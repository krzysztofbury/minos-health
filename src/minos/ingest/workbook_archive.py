"""Resource-bounded ZIP and XML preflight before any spreadsheet reader runs."""

import re
import stat
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, NoReturn, final
from xml.parsers import expat
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo

from defusedxml.ElementTree import fromstring

from minos.domain.reference import SourceError


@dataclass(frozen=True)
class WorkbookLimits:
    max_source_bytes: int = 32 * 1024 * 1024
    max_entries: int = 128
    max_entry_bytes: int = 16 * 1024 * 1024
    max_expanded_bytes: int = 32 * 1024 * 1024
    max_compression_ratio: int = 100
    max_xml_elements: int = 750_000
    max_xml_depth: int = 32
    max_text_characters: int = 65_536
    max_rows: int = 20_000
    max_columns: int = 300
    max_nodes: int = 100_000

    def __post_init__(self) -> None:
        if any(
            value < 1
            for value in (
                self.max_source_bytes,
                self.max_entries,
                self.max_entry_bytes,
                self.max_expanded_bytes,
                self.max_compression_ratio,
                self.max_xml_elements,
                self.max_xml_depth,
                self.max_text_characters,
                self.max_rows,
                self.max_columns,
                self.max_nodes,
            )
        ):
            raise ValueError("workbook limits must be positive")


DEFAULT_WORKBOOK_LIMITS = WorkbookLimits()


@dataclass(frozen=True)
class WorksheetLayout:
    rows: int
    columns: int
    merges: tuple[str, ...]


def _fail(reason: str) -> NoReturn:
    raise SourceError("workbook " + reason)


def _central_directory(source: BinaryIO, limits: WorkbookLimits) -> int:
    """Bound central records before ZipFile allocates its per-entry objects."""
    size = source.seek(0, 2)
    if not 22 <= size <= limits.max_source_bytes:
        _fail("source byte count exceeds limit or is truncated")
    _ = source.seek(0)
    if source.read(4) != b"PK\x03\x04":
        _fail("is not an XLSX ZIP")
    start = max(0, size - 65_557)
    _ = source.seek(start)
    tail = source.read(65_557)
    offset = tail.rfind(b"PK\x05\x06")
    if offset < 0 or len(tail) - offset < 22:
        _fail("ZIP end record is missing or truncated")
    # mypy types unpack_from as tuple[Any, ...] while basedpyright reads the
    # format string, so declared names satisfy both where a cast satisfies one.
    disk: int
    central_disk: int
    disk_entries: int
    entries: int
    length: int
    position: int
    comment: int
    _, disk, central_disk, disk_entries, entries, length, position, comment = (
        struct.unpack_from("<4s4H2IH", tail, offset)
    )
    if disk or central_disk or disk_entries != entries:
        _fail("multi-disk ZIP is unsupported")
    if not 1 <= entries <= limits.max_entries or entries == 65_535:
        _fail("ZIP entry count exceeds limit")
    if position == 0xFFFFFFFF or length == 0xFFFFFFFF:
        _fail("ZIP64 is unsupported")
    if offset + 22 + comment != len(tail) or position + length != start + offset:
        _fail("ZIP end record or central directory is inconsistent")
    if not entries * 46 <= length <= entries * (46 + 512 + 4096 + 256):
        _fail("ZIP central directory exceeds limit")
    _ = source.seek(position)
    central = source.read(length)
    cursor = 0
    for _ in range(entries):
        if central[cursor : cursor + 4] != b"PK\x01\x02" or len(central) - cursor < 46:
            _fail("ZIP central record is invalid")
        name: int
        extra: int
        note: int
        name, extra, note = struct.unpack_from("<3H", central, cursor + 28)
        if not 1 <= name <= 512 or extra > 4096 or note > 256:
            _fail("ZIP central field exceeds limit")
        cursor += 46 + name + extra + note
        if cursor > len(central):
            _fail("ZIP central record is truncated")
    if cursor != len(central):
        _fail("ZIP central entry count conflicts")
    _ = source.seek(0)
    return entries


@final
class _XmlGuard:
    def __init__(self, limits: WorkbookLimits, part: str) -> None:
        self.limits = limits
        self.part = part
        self.elements = 0
        self.text: list[int] = []
        self.namespaces = 0
        self.rows = 0
        self.columns = 0
        self.current_row: int | None = None
        self.current_column = 0
        self.merges: list[str] = []
        self.parser = expat.ParserCreate(namespace_separator="}")
        self.parser.StartElementHandler = self.start
        self.parser.EndElementHandler = self.end
        self.parser.CharacterDataHandler = self.characters
        self.parser.StartDoctypeDeclHandler = self.reject
        self.parser.EntityDeclHandler = self.reject
        self.parser.ProcessingInstructionHandler = self.reject
        self.parser.ExternalEntityRefHandler = self.external
        self.parser.XmlDeclHandler = self.declaration
        self.parser.CommentHandler = self.comment
        self.parser.StartNamespaceDeclHandler = self.namespace

    def reject(self, *_arguments: object) -> None:
        _fail("contains prohibited XML declarations or instructions")

    def external(self, *_arguments: object) -> int:
        _fail("contains an external XML entity")

    def declaration(
        self, _version: str, encoding: str | None, _standalone: int
    ) -> None:
        if encoding is not None and encoding.casefold() != "utf-8":
            _fail("XML encoding is unsupported")

    def namespace(self, prefix: str | None, uri: str | None) -> None:
        self.namespaces += 1
        if self.namespaces > 32 or len(prefix or "") > 64 or len(uri or "") > 2048:
            _fail("XML namespace declarations exceed limit")

    def start(self, name: str, attributes: dict[str, str]) -> None:
        self.namespaces = 0
        self.elements += 1
        if self.elements > self.limits.max_xml_elements:
            _fail("XML element count exceeds limit")
        if self.part == "xl/styles.xml" and self.elements > 10_000:
            _fail("style element count exceeds limit")
        if len(self.text) >= self.limits.max_xml_depth:
            _fail("XML depth exceeds limit")
        if len(attributes) > 64 or any(
            len(key) > 512 or len(value) > self.limits.max_text_characters
            for key, value in attributes.items()
        ):
            _fail("XML attributes exceed limit")
        if attributes.get("TargetMode", "Internal") != "Internal":
            _fail("external package relationship is unsupported")
        content_type = attributes.get("ContentType", "").lower()
        if any(
            word in content_type
            for word in ("macroenabled", "vba", "activex", "oleobject")
        ):
            _fail("active workbook content is unsupported")
        if self.part.startswith("xl/worksheets/"):
            self._coordinate(name.rsplit("}", 1)[-1], attributes)
        self.text.append(0)

    def _coordinate(self, name: str, attributes: dict[str, str]) -> None:
        if name == "row":
            raw = attributes.get("r", "")
            if (
                re.fullmatch(r"[1-9][0-9]{0,4}", raw) is None
                or int(raw) > self.limits.max_rows
            ):
                _fail("worksheet row exceeds limit")
            if self.current_row is not None or int(raw) <= self.rows:
                _fail("worksheet rows are duplicated or out of order")
            self.current_row = int(raw)
            self.current_column = 0
            self.rows = int(raw)
        elif name == "c":
            match = re.fullmatch(
                r"([A-Z]{1,3})([1-9][0-9]{0,4})", attributes.get("r", "")
            )
            if match is None:
                _fail("worksheet cell coordinate is invalid")
            column = 0
            for character in match[1]:
                column = column * 26 + ord(character) - ord("A") + 1
            if column > self.limits.max_columns or int(match[2]) > self.limits.max_rows:
                _fail("worksheet cell coordinate exceeds limit")
            if self.current_row != int(match[2]) or column <= self.current_column:
                _fail(
                    "worksheet cells are duplicated, out of order or outside their row"
                )
            self.current_column = column
            self.columns = max(self.columns, column)
        elif name == "mergeCell":
            if len(self.merges) >= 128:
                _fail("worksheet merge count exceeds limit")
            self.merges.append(attributes.get("ref", ""))

    def end(self, name: str) -> None:
        if self.part.startswith("xl/worksheets/") and name.rsplit("}", 1)[-1] == "row":
            self.current_row = None
        _ = self.text.pop()

    def characters(self, value: str) -> None:
        if self.text:
            self.text[-1] += len(value)
            if self.text[-1] > self.limits.max_text_characters:
                _fail("XML text exceeds limit")

    def comment(self, value: str) -> None:
        if len(value) > self.limits.max_text_characters:
            _fail("XML comment exceeds limit")


def preflight_workbook(
    source: BinaryIO, *, limits: WorkbookLimits = DEFAULT_WORKBOOK_LIMITS
) -> dict[str, WorksheetLayout]:
    return _preflight_office(source, limits, document=False)


def preflight_docx(
    source: BinaryIO, *, limits: WorkbookLimits = DEFAULT_WORKBOOK_LIMITS
) -> None:
    """Check every DOCX part without decoding images or executing office content."""
    _ = _preflight_office(source, limits, document=True)


def _preflight_office(
    source: BinaryIO, limits: WorkbookLimits, *, document: bool
) -> dict[str, WorksheetLayout]:
    """Read every bounded entry to EOF, checking ZIP consistency, CRC, and XML."""
    try:
        count = _central_directory(source, limits)
        with ZipFile(source) as archive:
            entries = archive.infolist()
            if (
                len(entries) != count
                or len({item.filename for item in entries}) != count
            ):
                _fail("ZIP entry names or count conflict")
            required = (
                {"word/document.xml", "[Content_Types].xml", "_rels/.rels"}
                if document
                else {
                    "xl/workbook.xml",
                    "[Content_Types].xml",
                    "xl/_rels/workbook.xml.rels",
                }
            )
            if not required <= set(archive.namelist()):
                _fail("required Office parts are missing")
            _validate_entries(entries, limits, document=document)
            elements = 0
            layouts: dict[str, WorksheetLayout] = {}
            for entry in entries:
                guard = (
                    _XmlGuard(limits, entry.filename)
                    if entry.filename.endswith((".xml", ".rels"))
                    else None
                )
                consumed = 0
                with archive.open(entry) as stream:
                    while chunk := stream.read(16 * 1024):
                        consumed += len(chunk)
                        if (
                            consumed > entry.file_size
                            or consumed > limits.max_entry_bytes
                        ):
                            _fail("expanded entry exceeds limit")
                        if guard is not None:
                            if b"\x00" in chunk:
                                _fail("XML encoding is unsupported")
                            _ = guard.parser.Parse(chunk, False)
                if consumed != entry.file_size:
                    _fail("expanded entry is truncated")
                if guard is not None:
                    _ = guard.parser.Parse(b"", True)
                    elements += guard.elements
                    if elements > limits.max_xml_elements:
                        _fail("total XML element count exceeds limit")
                    if entry.filename.startswith(
                        "xl/worksheets/"
                    ) and entry.filename.endswith(".xml"):
                        layouts[entry.filename] = WorksheetLayout(
                            guard.rows, guard.columns, tuple(guard.merges)
                        )
            selected = {} if document else _worksheet_layouts(archive, layouts)
        _ = source.seek(0)
        return selected
    except (
        BadZipFile,
        expat.ExpatError,
        OSError,
        EOFError,
        UnicodeError,
        struct.error,
        zlib.error,
    ):
        raise SourceError("workbook ZIP/XML preflight failed") from None


def _worksheet_layouts(
    archive: ZipFile, layouts: dict[str, WorksheetLayout]
) -> dict[str, WorksheetLayout]:
    relationships = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets: dict[str, tuple[str, str]] = {}
    for relation in relationships:
        identity = relation.attrib.get("Id", "")
        if not identity or identity in targets:
            _fail("workbook relationship identities conflict")
        target = relation.attrib.get("Target", "")
        targets[identity] = (
            target[1:] if target.startswith("/xl/") else "xl/" + target,
            relation.attrib.get("Type", ""),
        )
    workbook = fromstring(archive.read("xl/workbook.xml"))
    result: dict[str, WorksheetLayout] = {}
    for sheet in workbook.findall(
        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet"
    ):
        title = sheet.attrib.get("name", "")
        identity = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id",
            "",
        )
        relationship = targets.get(identity)
        if (
            not title
            or title in result
            or relationship is None
            or relationship[0] not in layouts
            or relationship[1]
            != "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
            or len(result) >= 16
        ):
            _fail("worksheet identities or relationships conflict")
        result[title] = layouts[relationship[0]]
    if not result:
        _fail("workbook has no worksheets")
    return result


def _validate_entries(
    entries: list[ZipInfo], limits: WorkbookLimits, *, document: bool = False
) -> None:
    expanded = 0
    for entry in entries:
        name = entry.filename
        if entry.orig_filename != name:
            _fail("ZIP entry name is ambiguous")
        if re.fullmatch(r"[A-Za-z0-9_.\[\]/-]{1,512}", name) is None or any(
            part in ("", ".", "..") for part in name.split("/")
        ):
            _fail("ZIP entry path is unsupported")
        if entry.flag_bits & (1 | 32 | 64) or stat.S_ISLNK(entry.external_attr >> 16):
            _fail("ZIP encryption or symlinks are unsupported")
        if entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
            _fail("ZIP compression method is unsupported")
        if any(
            word in name.lower()
            for word in ("externallinks", "vbaproject", "activex", "embeddings")
        ):
            _fail("active or linked workbook content is unsupported")
        if (
            not name.endswith((".xml", ".rels"))
            and not (
                document
                and re.fullmatch(r"word/media/[A-Za-z0-9_.-]+\.(?:emf|jpeg|png)", name)
            )
            # Excel's saved preview image; listed, size-bounded, never decoded.
            and name != "docProps/thumbnail.emf"
            and re.fullmatch(
                r"xl/printerSettings/printerSettings[1-9][0-9]*\.bin", name
            )
            is None
        ):
            _fail("workbook part type is unsupported")
        expanded += entry.file_size
        if (
            expanded > limits.max_expanded_bytes
            or entry.file_size > limits.max_entry_bytes
        ):
            _fail("expanded ZIP bytes exceed limit")
        if entry.file_size > max(entry.compress_size, 1) * limits.max_compression_ratio:
            _fail("ZIP compression ratio exceeds limit")
        metadata_limit = (
            8 * 1024 * 1024 if name == "xl/sharedStrings.xml" else 1024 * 1024
        )
        if name.startswith("xl/worksheets/") or name == "word/document.xml":
            metadata_limit = limits.max_entry_bytes
        if name.endswith((".xml", ".rels")) and entry.file_size > metadata_limit:
            _fail("eagerly loaded XML part exceeds limit")
