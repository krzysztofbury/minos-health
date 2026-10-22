"""Parse one pinned WHO ICD-10/ICD-11 map workbook as published (#53).

Every data row is kept with its source row number and its cells unchanged. The
header is checked against the reviewed 2026-01 layout of each table, so a WHO
layout change fails the build instead of shifting columns.
"""

import re
from dataclasses import dataclass
from typing import BinaryIO

from minos.domain.reference import SourceError
from minos.ingest.icd11_maps_manifest import Icd11MapFile, MapKind
from minos.ingest.workbook_archive import WorkbookLimits, preflight_workbook

# Four WHO columns of text per row and up to 18,000 rows per table.
MAP_WORKBOOK_LIMITS = WorkbookLimits(max_xml_elements=2_000_000, max_rows=40_001)
_URI = re.compile(
    r"http://id\.who\.int/icd/release/11/20[0-9]{2}-[0-9]{2}/mms(/[0-9a-z/]+)?"
)
_VERSION = re.compile(r"20[0-9]{2}-[A-Z][a-z]{2}-[0-9]{2}")
_TO_11 = (
    "10ClassKind",
    "10DepthInKind",
    "icd10Code",
    "icd10Chapter",
    "icd10Title",
    "11ClassKind",
    "11DepthInKind",
    "ICD-11 FoundationURI",
    "Linearization (releaseURI)",
    "icd11Code",
    "icd11Chapter",
    "icd11Title",
)
_TO_11_MANY = (
    "10ClassKind",
    "Depth",
    "icd10Code",
    "icd10Chapter",
    "icd10Title",
    "11ClassKind",
    "Depth",
    "ICD-11 Foundation URI",
    "Linearization (release) URI",
    "icd11Code",
    "icd11Chapter",
    "icd11Title",
)
_TO_10 = (
    "Linearization (release) URI",
    "icd11Code",
    "icd11Chapter",
    "icd11Title",
    "icd10Code",
    "icd10Chapter",
    "icd10Title",
)
_HEADERS: dict[MapKind, tuple[str, ...]] = {
    "10to11_one": _TO_11,
    "10to11_many": _TO_11_MANY,
    "11to10_one": _TO_10,
}


@dataclass(frozen=True)
class MapRow:
    source_row: int
    icd10_code: str | None
    icd10_title: str | None
    icd11_code: str | None
    icd11_uri: str | None
    icd11_title: str | None


def parse_icd11_map(source: BinaryIO, pin: Icd11MapFile) -> tuple[MapRow, ...]:
    try:
        import openpyxl
    except ImportError:
        raise SourceError(
            "workbook reader requires the pinned reference extra"
        ) from None
    if openpyxl.__version__ != "3.1.5":
        raise SourceError("workbook reader version is incompatible")
    layouts = preflight_workbook(source, limits=MAP_WORKBOOK_LIMITS)
    if len(layouts) != 1:
        raise SourceError("ICD-11 map workbook must have one worksheet")
    ((sheet_name, layout),) = layouts.items()
    book = openpyxl.load_workbook(
        source, read_only=True, data_only=False, keep_links=False
    )
    try:
        rows = book[sheet_name].iter_rows(max_row=layout.rows, values_only=True)
        header = next(rows, None)
        expected = _HEADERS[pin.kind]
        if header is None or tuple(header[: len(expected)]) != expected:
            raise SourceError("ICD-11 map header differs from the reviewed layout")
        # WHO puts the table version in the last header cell, over an empty column.
        tail = header[len(expected) :]
        if (
            len(tail) != 1
            or not isinstance(tail[0], str)
            or not _VERSION.fullmatch(tail[0])
        ):
            raise SourceError("ICD-11 map version column is missing")
        names = {name: index for index, name in enumerate(expected)}
        uri_column = next(
            index
            for index, name in enumerate(expected)
            if name.startswith("Linearization")
        )
        result: list[MapRow] = []
        for number, values in enumerate(rows, 2):
            cells = tuple(values)
            if len(cells) > len(header) or any(
                cell is not None for cell in cells[len(expected) :]
            ):
                raise SourceError("ICD-11 map row has cells beyond its columns")
            uri = _cell(cells, uri_column)
            if uri is not None and not _URI.fullmatch(uri):
                raise SourceError("ICD-11 map linearization URI is invalid")
            result.append(
                MapRow(
                    number,
                    _cell(cells, names["icd10Code"]),
                    _cell(cells, names["icd10Title"]),
                    _cell(cells, names["icd11Code"], code=True),
                    uri,
                    _cell(cells, names["icd11Title"]),
                )
            )
    finally:
        book.close()
    if len(result) != pin.expected_rows:
        raise SourceError("ICD-11 map row count differs from the review")
    return tuple(result)


def _cell(cells: tuple[object, ...], index: int, *, code: bool = False) -> str | None:
    value = cells[index] if index < len(cells) else None
    if value is None:
        return None
    # Excel stores the two-digit chapter codes 10 to 26 as numbers.
    if code and type(value) is int and 1 <= value <= 99:
        return f"{value:02d}"
    if not isinstance(value, str):
        raise SourceError("ICD-11 map cell is not text")
    return value
