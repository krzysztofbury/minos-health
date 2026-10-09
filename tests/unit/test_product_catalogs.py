"""Product-code column reader of BAW product catalogs (#145)."""

import hashlib
from datetime import UTC, datetime
from io import BytesIO

import pytest

from minos.domain.reference import SourceError
from minos.ingest.product_catalogs import parse_product_catalog
from minos.ingest.product_catalogs_manifest import ProductCatalogFile

HEADER = "Kod produktu rozliczeniowego"
A, B = "5.52.01.0000001", "5.52.01.0000002"


def _workbook(*rows: list[object]) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Arkusz1"
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _pin(content: bytes, rows: int, codes: int) -> ProductCatalogFile:
    return ProductCatalogFile(
        sheet="1w",
        file_name="Zalacznik1w.xlsx",
        source_url="https://baw.nfz.gov.pl/api/file/4/1",
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        fetched_at=datetime(2026, 10, 9, tzinfo=UTC),
        parsed=True,
        worksheet="Arkusz1",
        code_header=HEADER,
        expected_rows=rows,
        expected_codes=codes,
    )


def _layout(*body: list[object]) -> bytes:
    return _workbook(
        ["Załącznik nr 1w"],
        [],
        ["Lp.", "Uwagi", HEADER, "Nazwa produktu rozliczeniowego"],
        [1, 2, 3, 4],
        *body,
    )


def test_only_the_reviewed_code_column_defines_members() -> None:
    content = _layout(
        [1, f"rozliczany z {B}", A, "Pierwszy"],
        [2, None, A, "Pierwszy, inny zakres"],
        [3, None, None, None],
        [4, None, f" {B}\xa0", "Drugi"],
    )
    entries = parse_product_catalog(BytesIO(content), _pin(content, 3, 2))
    # The cross reference to B in the remarks column is not a membership.
    assert [(e.code, e.name, e.row) for e in entries] == [
        (A, "Pierwszy", 5),
        (B, "Drugi", 8),
    ]


@pytest.mark.parametrize(
    ("content", "rows", "codes", "message"),
    [
        (_layout([1, None, A, "x"]), 2, 1, "reviewed counts"),
        (_layout([1, None, A, "x"], [2, None, "patrz 1b", "y"]), 1, 1, "unexpected"),
        (_layout([1, None, A, "x"], [2, None, "5", "y"]), 1, 1, "unexpected"),
        (
            _workbook(["Lp.", HEADER, "Opis"], [1, A, "x"]),
            1,
            1,
            "name column",
        ),
        (_workbook(["Lp.", "Kod", "Nazwa produktu"], [1, A, "x"]), 1, 1, "header"),
    ],
)
def test_a_layout_or_count_drift_fails_review(
    content: bytes, rows: int, codes: int, message: str
) -> None:
    with pytest.raises(SourceError, match=message):
        _ = parse_product_catalog(BytesIO(content), _pin(content, rows, codes))
