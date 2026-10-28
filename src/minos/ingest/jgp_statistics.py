"""Parse Tables 2 and 4 of one pinned NFZ monthly JGP report (#283).

The report is SpreadsheetML. Only two tables are read: Table 2 (hospitalizations
and settled points per group and hospital level) and Table 4 (count, mean stay
and mean age per JGP section, admission mode and stay character). The others
name providers and stay out of scope. NFZ suppresses counts below five as
``<5``; a suppressed cell is a bound of one to four, and an empty cell means no
figure, so the two are never merged.
"""

import hashlib
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation
from typing import BinaryIO, Literal, cast
from xml.etree.ElementTree import Element  # nosec B405 - types only; defusedxml parses.

from defusedxml.ElementTree import iterparse

from minos.domain.reference import SourceError
from minos.ingest.jgp_statistics_manifest import MonthlyReport

_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
_MONTHS = (
    "Styczeń",
    "Luty",
    "Marzec",
    "Kwiecień",
    "Maj",
    "Czerwiec",
    "Lipiec",
    "Sierpień",
    "Wrzesień",
    "Październik",
    "Listopad",
    "Grudzień",
)
_LEVELS = frozenset(
    {
        "POZA PSZ",
        "SZPITAL I STOPNIA",
        "SZPITAL II STOPNIA",
        "SZPITAL III STOPNIA",
        "SZPITAL OGÓLNOPOLSKI",
        "SZPITAL ONKOLOGICZNY",
        "SZPITAL PEDIATRYCZNY",
        "SZPITAL PULMONOLOGICZNY",
    }
)
_ADMISSIONS = {"Planowy": "planned", "Inny niż planowy": "other"}
_CHARACTERS = {"Zabiegowy": "procedural", "Zachowawczy": "conservative"}
# Ages of the three reported months, in months before the reporting month.
AGES = (0, 1, 12)
_MAX_CELLS = 32
_MAX_TEXT = 512
# Every data row ends at the last column of its table's header, so a missing
# or extra cell cannot shift a value into another month.
_WIDTHS = {"Tabela 2": 14, "Tabela 4": 19}
_INDEX = re.compile(r"[1-9][0-9]?")
_COUNT = re.compile(r"0|[1-9][0-9]{0,8}")
# Report values are binary floats written as text, sometimes in exponent form
# (a mean stay of 9.72E-2 days).
_DECIMAL = re.compile(r"[0-9]{1,13}(?:\.[0-9]{1,20})?(?:E[+-]?[0-9]{1,2})?")
_CONTEXT = Context(prec=50, rounding=ROUND_HALF_EVEN)

type Count = int | Literal["<5"] | None


@dataclass(frozen=True, slots=True)
class GroupFigure:
    section: str
    code: str
    hospital_level: str
    month: str
    age_months: int
    hospitalizations: Count
    # Ten-thousandths of a point, the precision NFZ reports; None when absent.
    points_ten_thousandths: int | None


@dataclass(frozen=True, slots=True)
class SectionFigure:
    section: str
    admission: str
    character: str
    month: str
    age_months: int
    hospitalizations: Count
    mean_stay_hundredths: int | None
    mean_age_tenths: int | None


@dataclass(frozen=True, slots=True)
class ReportFigures:
    groups: tuple[GroupFigure, ...]
    sections: tuple[SectionFigure, ...]


def _month(report: str, age: int) -> str:
    year, month = (int(part) for part in report.split("-"))
    index = year * 12 + month - 1 - age
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _label(month: str) -> str:
    year, number = month.split("-")
    return f"{_MONTHS[int(number) - 1]} {year}"


def _cells(row: Element) -> tuple[dict[int, str], int]:
    """Column number (1-based, as Excel numbers them) to text, and the last column."""
    cells: dict[int, str] = {}
    column = 0
    for cell in row.findall(_NS + "Cell"):
        index = cell.get(_NS + "Index")
        if index is None:
            column += 1
        elif _INDEX.fullmatch(index) is None or int(index) <= column:
            raise SourceError("monthly report cell index is invalid")
        else:
            column = int(index)
        if column > _MAX_CELLS:
            raise SourceError("monthly report row exceeds its column bound")
        data = cell.findall(_NS + "Data")
        # Formatted text keeps its characters in child elements, out of .text.
        if len(data) > 1 or any(len(item) for item in data):
            raise SourceError("monthly report cell holds formatted or repeated data")
        text = (data[0].text or "") if data else ""
        if len(text) > _MAX_TEXT:
            raise SourceError("monthly report cell exceeds its text bound")
        if text:
            cells[column] = text
        merge = cell.get(_NS + "MergeAcross")
        if merge is not None:
            if _INDEX.fullmatch(merge) is None:
                raise SourceError("monthly report cell merge is invalid")
            column += int(merge)
    return cells, column


def _count(text: str | None) -> Count:
    if text is None:
        return None
    if text == "<5":
        return "<5"
    if _COUNT.fullmatch(text) is None:
        raise SourceError("monthly report count is not a whole number")
    return int(text)


def _scaled(text: str | None, places: int) -> int | None:
    if text is None:
        return None
    if _DECIMAL.fullmatch(text) is None:
        raise SourceError("monthly report value is not a plain number")
    try:
        value = Decimal(text, context=_CONTEXT)
    except InvalidOperation:
        raise SourceError("monthly report value is not a number") from None
    if value > Decimal(10) ** 12:
        raise SourceError("monthly report value is outside its bound")
    # Fixed places in a local context keep the stored integers, and the bundle
    # bytes, deterministic whatever the global decimal context is.
    return int(
        value.scaleb(places, context=_CONTEXT).quantize(Decimal(1), context=_CONTEXT)
    )


def _require_header(
    rows: dict[int, dict[int, str]], expected: dict[tuple[int, int], str]
) -> None:
    for (row, column), text in expected.items():
        if rows.get(row, {}).get(column) != text:
            raise SourceError("monthly report header differs from the reviewed layout")


def parse_report(source: BinaryIO, pin: MonthlyReport) -> ReportFigures:
    """Every Table 2 and Table 4 figure of the report, in source order."""
    content = source.read(pin.size_bytes + 1)
    if (
        len(content) != pin.size_bytes
        or hashlib.sha256(content).hexdigest() != pin.sha256
    ):
        raise SourceError("monthly report bytes differ from the pin")
    months = [_label(_month(pin.month, age)) for age in AGES]
    headers: dict[str, dict[int, dict[int, str]]] = {"Tabela 2": {}, "Tabela 4": {}}
    groups: list[GroupFigure] = []
    sections: list[SectionFigure] = []
    seen_groups: set[tuple[str, str]] = set()
    seen_sections: set[tuple[str, str, str]] = set()
    sheet: str | None = None
    number = 0
    try:
        events = cast(
            Iterator[tuple[str, Element]],
            iterparse(io.BytesIO(content), events=("start", "end"), forbid_dtd=True),
        )
        for event, element in events:
            if event == "start" and element.tag == _NS + "Worksheet":
                sheet, number = element.get(_NS + "Name"), 0
                continue
            if event != "end":
                continue
            if element.tag == _NS + "Row":
                number += 1
                if sheet in headers:
                    cells, width = _cells(element)
                    if number <= 4:
                        headers[sheet][number] = cells
                    elif width != _WIDTHS[sheet]:
                        raise SourceError("monthly report row width differs")
                    elif sheet == "Tabela 2":
                        _group_row(cells, pin, groups, seen_groups)
                    else:
                        _section_row(cells, pin, sections, seen_sections)
                element.clear()
            elif element.tag == _NS + "Worksheet":
                element.clear()
    except SourceError:
        raise
    except ValueError, KeyError, IndexError, SyntaxError:
        raise SourceError("monthly report is not readable SpreadsheetML") from None
    group_header = {(3, 2): "Sekcja", (3, 3): "Kod produktu"}
    group_header |= {(3, 4): "Poziom szpitala w ramach ryczałtu PSZ"}
    section_header = {(3, 2): "Tryb przyjęcia", (3, 3): "Charakter hospitalizacji"}
    section_header |= {(3, 4): "Sekcja JGP"}
    changes = (f"{months[0]} / {months[1]}", f"{months[0]} / {months[2]}")
    group_header |= {(3, 11): changes[0], (3, 13): changes[1]}
    section_header |= {(3, 14): changes[0], (3, 17): changes[1]}
    for offset, label in enumerate(months):
        group_header |= {
            (3, 5 + 2 * offset): label,
            (4, 5 + 2 * offset): "Hospitalizacje",
            (4, 6 + 2 * offset): "Wartość",
        }
        section_header |= {
            (3, 5 + 3 * offset): label,
            (4, 5 + 3 * offset): "Średnia długość hospitalizacji",
            (4, 6 + 3 * offset): "Liczba hospitalizacji",
            (4, 7 + 3 * offset): "Średni wiek hospitalizowanego pacjenta",
        }
    _require_header(headers["Tabela 2"], group_header)
    _require_header(headers["Tabela 4"], section_header)
    # Table 2's title names the values: points settled for the hospitalizations.
    if (
        not headers["Tabela 2"]
        .get(2, {})
        .get(2, "")
        .startswith("Tabela 2: Liczba hospitalizacji i liczba punktów")
    ):
        raise SourceError("monthly report Table 2 no longer reports points")
    if len(seen_groups) != pin.group_rows or len(seen_sections) != pin.section_rows:
        raise SourceError("monthly report does not match the reviewed row counts")
    return ReportFigures(tuple(groups), tuple(sections))


def _group_row(
    cells: dict[int, str],
    pin: MonthlyReport,
    groups: list[GroupFigure],
    seen: set[tuple[str, str]],
) -> None:
    section, code, level = cells.get(2), cells.get(3), cells.get(4)
    if section is None or code is None or level not in _LEVELS:
        raise SourceError("monthly report group row is incomplete")
    if not code.isascii() or not code.isalnum() or not 2 <= len(code) <= 8:
        raise SourceError("monthly report group code is invalid")
    key = (code, level)
    if key in seen:
        raise SourceError("monthly report repeats a group and hospital level")
    seen.add(key)
    if len(seen) > pin.group_rows:
        raise SourceError("monthly report exceeds its reviewed group rows")
    for offset, age in enumerate(AGES):
        count = _count(cells.get(5 + 2 * offset))
        points = _scaled(cells.get(6 + 2 * offset), 4)
        if count is None and points is None:
            continue
        groups.append(
            GroupFigure(
                section, code, level, _month(pin.month, age), age, count, points
            )
        )


def _section_row(
    cells: dict[int, str],
    pin: MonthlyReport,
    sections: list[SectionFigure],
    seen: set[tuple[str, str, str]],
) -> None:
    admission = _ADMISSIONS.get(cells.get(2, ""))
    character = _CHARACTERS.get(cells.get(3, ""))
    section = cells.get(4)
    if admission is None or character is None or section is None:
        raise SourceError("monthly report section row is incomplete")
    key = (admission, character, section)
    if key in seen:
        raise SourceError("monthly report repeats a section row")
    seen.add(key)
    if len(seen) > pin.section_rows:
        raise SourceError("monthly report exceeds its reviewed section rows")
    for offset, age in enumerate(AGES):
        count = _count(cells.get(6 + 3 * offset))
        stay = _scaled(cells.get(5 + 3 * offset), 2)
        mean_age = _scaled(cells.get(7 + 3 * offset), 1)
        if count is None and stay is None and mean_age is None:
            continue
        sections.append(
            SectionFigure(
                section,
                admission,
                character,
                _month(pin.month, age),
                age,
                count,
                stay,
                mean_age,
            )
        )
