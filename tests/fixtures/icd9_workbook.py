"""Synthetic OOXML in the observed NFZ layout; no official titles or raw file."""

import hashlib
from io import BytesIO
from typing import cast
from xml.sax.saxutils import escape
from zipfile import ZIP_STORED, ZipFile

from minos.ingest.reference_manifest import SourceManifest
from tests.fixtures.icd9_xml import source_manifest

MAIN_ROWS: tuple[tuple[object, ...], ...] = (
    (
        "Rozdział",
        None,
        "Podrozdział",
        None,
        "Kategoria główna",
        None,
        "Kategoria szczegółowa",
        None,
    ),
    ("nr ", "tytuł", "nr", "tytuł", "nr", "tytuł", "nr", "tytuł"),
    ("AA", "Synthetic parent", "A", "Synthetic chapter", "A01 ", "  Badanie moczu  "),
    (
        "AA",
        "Synthetic parent",
        "A",
        "Synthetic chapter",
        "A01",
        "Different synthetic title",
    ),
    (
        "AA",
        "Synthetic parent",
        "A",
        "Synthetic chapter",
        "A03",
        "Synthetic companion only",
    ),
    (88, "Synthetic numeric chapter"),
)


def worksheet(rows: tuple[tuple[object, ...], ...], *, main: bool) -> bytes:
    pieces = [
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:H6"/><sheetData>'
    ]
    for number, values in enumerate(rows, 1):
        pieces.append(f'<row r="{number}">')
        for column, value in enumerate(values):
            if value is None:
                continue
            coordinate = chr(65 + column) + str(number)
            if type(value) is int:
                pieces.append(f'<c r="{coordinate}" t="n"><v>{value}</v></c>')
            else:
                pieces.append(
                    f'<c r="{coordinate}" t="inlineStr"><is><t xml:space="preserve">{escape(cast(str, value))}</t></is></c>'
                )
        pieces.append("</row>")
    pieces.append("</sheetData>")
    if main:
        pieces.append(
            '<mergeCells count="4"><mergeCell ref="A1:B1"/><mergeCell ref="C1:D1"/><mergeCell ref="E1:F1"/><mergeCell ref="G1:H1"/></mergeCells>'
        )
    pieces.append("</worksheet>")
    return "".join(pieces).encode()


def workbook_parts() -> dict[str, bytes]:
    notes = (
        ("Kod procedury", "Tytuł", "Techniki, uwagi"),
        ("A01", "Synthetic note title", "Synthetic instruction"),
        ("A99", "Synthetic orphan title", "Synthetic orphan note"),
    )
    second = (
        *MAIN_ROWS[:2],
        (
            "AA",
            "Other table title",
            "A",
            "Other branch title",
            "A01",
            "Other preferred title",
        ),
    )
    return {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet3.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        "_rels/.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml": b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="ICD-9_PL " sheetId="1" r:id="rId1"/><sheet name="ICD-9_PL" sheetId="2" r:id="rId2"/><sheet name="Uwagi" sheetId="3" r:id="rId3"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet3.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": worksheet(MAIN_ROWS, main=True),
        "xl/worksheets/sheet2.xml": worksheet(second, main=True),
        "xl/worksheets/sheet3.xml": worksheet(notes, main=False),
    }


def archive_bytes(
    parts: dict[str, bytes] | None = None, *, compression: int = ZIP_STORED
) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=compression) as archive:
        for name, content in (parts if parts is not None else workbook_parts()).items():
            archive.writestr(name, content)
    return buffer.getvalue()


def workbook_manifest(content: bytes) -> SourceManifest:
    return source_manifest().model_copy(
        update={
            "release_id": "icd9-nfz-workbook-synthetic-v1",
            "origin": "nfz-workbook",
            "source_file_id": "icd-9_pl_cm_v_5.90.xlsx#ICD-9_PL ",
            "source_url": "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/icd-9_pl_cm_v_5.90.xlsx",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "expected": source_manifest().expected.model_copy(
                update={"nodes": 8, "relations": 5, "concepts": 5, "descriptions": 5}
            ),
        }
    )
