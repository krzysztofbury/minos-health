"""Parse the product-code column of one pinned BAW product catalog (#145).

Only the verified code column is read: other columns carry cross references
to codes of other catalogs, which a whole-sheet scan would misread as members.
"""

import re
from dataclasses import dataclass
from typing import BinaryIO

from minos.domain.reference import SourceError
from minos.ingest.product_catalogs_manifest import ProductCatalogFile
from minos.ingest.workbook_archive import (
    DEFAULT_WORKBOOK_LIMITS,
    WorkbookLimits,
    preflight_workbook,
)

_CODE = re.compile(r"^5\.[0-9]{2}\.[0-9]{2}\.[0-9]{7}$")
_HEADER_ROWS = 40


@dataclass(frozen=True)
class ProductEntry:
    code: str
    name: str
    row: int


def _text(value: object) -> str:
    return " ".join(str(value).split()) if value is not None else ""


def parse_product_catalog(
    source: BinaryIO,
    pin: ProductCatalogFile,
    *,
    limits: WorkbookLimits = DEFAULT_WORKBOOK_LIMITS,
) -> tuple[ProductEntry, ...]:
    """Unique product codes in sheet order, each with its first row and name."""
    if not pin.parsed or pin.worksheet is None or pin.code_header is None:
        raise SourceError("product catalog is not reviewed for parsing")
    try:
        import openpyxl
        from openpyxl.utils.exceptions import InvalidFileException
    except ImportError:
        raise SourceError(
            "workbook reader requires the pinned reference extra"
        ) from None
    if openpyxl.__version__ != "3.1.5":
        raise SourceError("workbook reader version is incompatible")
    layouts = preflight_workbook(source, limits=limits)
    if set(layouts) != {pin.worksheet}:
        raise SourceError("product catalog worksheet identity conflicts")
    book = None
    try:
        book = openpyxl.load_workbook(
            source, read_only=True, data_only=False, keep_links=False
        )
        sheet = book[pin.worksheet]
        rows = list(
            sheet.iter_rows(max_row=layouts[pin.worksheet].rows, values_only=True)
        )
        header = next(
            (
                (number, column)
                for number, values in enumerate(rows[:_HEADER_ROWS], 1)
                for column, value in enumerate(values)
                if _text(value) == pin.code_header
            ),
            None,
        )
        if header is None:
            raise SourceError("product catalog code header is missing")
        header_row, code_column = header
        names = rows[header_row - 1]
        name_column = code_column + 1
        if name_column >= len(names) or not _text(names[name_column]).startswith(
            "Nazwa produktu"
        ):
            raise SourceError("product catalog name column is missing")
        entries: dict[str, ProductEntry] = {}
        counted = 0
        numbering_seen = False
        for number, values in enumerate(rows[header_row:], header_row + 1):
            cell = _text(values[code_column]) if code_column < len(values) else ""
            if not cell:
                continue
            if _CODE.fullmatch(cell):
                counted += 1
                name = _text(values[name_column]) if name_column < len(values) else ""
                if cell not in entries:
                    entries[cell] = ProductEntry(cell, name, number)
                continue
            # One row between the header and the first code numbers the columns.
            if not numbering_seen and counted == 0 and cell.isdigit():
                numbering_seen = True
                continue
            raise SourceError("product catalog code column holds unexpected text")
    except ValueError, KeyError, TypeError, IndexError, OSError, InvalidFileException:
        raise SourceError("product catalog reader rejected source structure") from None
    finally:
        if book is not None:
            book.close()
    if counted != pin.expected_rows or len(entries) != pin.expected_codes:
        raise SourceError("product catalog does not match the reviewed counts")
    return tuple(entries.values())
