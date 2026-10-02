"""Synthetic Office packages shaped like the reviewed 2026 komunikat files."""

import hashlib
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, cast
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from minos.ingest.komunikat_manifest import KomunikatFile, KomunikatManifest

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet

RANK_ROWS: dict[str, tuple[tuple[object, ...], ...]] = {
    "SZP": (
        ("A01", "  Synthetic rank title  ", 4),
        ("00.00001", "Recorded zero", 0),
        ("05.01", "Not a canonical concept", 3),
    ),
    "AOS": (("A01", "Other grouper rank", 2),),
}
SPECIALTY_ROWS: dict[str, tuple[tuple[object, ...], ...]] = {
    "SZP": (("0010", "4", "WSZYSTKIE"),),
    "AOS": (("0010", "WSZYSTKIE", "WSZYSTKIE"),),
    "REH": (("0010", "WSZYSTKIE", "WSZYSTKIE"),),
}
PARAGRAPHS = (
    "Opis działania algorytmu JGP",
    "wersja 1.1.2026",
    "X.Y.ZZZZ",
    "Y - oznacza okres sprawozdawczy, od którego w danym roku obowiązuje publikowana wersja algorytmu grupera",
    "ZZZZ - oznacza rok, dla którego opublikowany został opis działania algorytmu grupera.",
    "Wersja algorytmu grupera JGP obowiązuje z dokładnością do okresu sprawozdawczego (miesiąca). Oznacza to, że dla danego okresu sprawozdawczego obowiązuje tylko jedna wersja algorytmu.",
)


def docx_bytes(paragraphs: tuple[str, ...] = PARAGRAPHS) -> bytes:
    parts = {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        "_rels/.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/document.xml": (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + "".join(
                "<w:p><w:r><w:t>"
                + escape(text[:5])
                + "</w:t></w:r><w:r><w:t>"
                + escape(text[5:])
                + "</w:t></w:r></w:p>"
                for text in paragraphs
            )
            + "</w:body></w:document>"
        ).encode(),
    }
    return package_bytes(parts)


def package_bytes(parts: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for name, body in parts.items():
            archive.writestr(name, body)
    return output.getvalue()


def companion_bytes(
    *, ranks: bool, rows: dict[str, tuple[tuple[object, ...], ...]] | None = None
) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    active = book.active
    assert active is not None
    book.remove(active)
    chosen = rows if rows is not None else RANK_ROWS if ranks else SPECIALTY_ROWS
    for scope, entries in chosen.items():
        sheet = cast("Worksheet", book.create_sheet(scope + " 2026"))
        sheet.append(
            (
                f"Tabela 2. Wykaz rang procedur medycznych ({scope})"
                if ranks
                else f"Tabela 1. Wykaz specjalności komórek ({scope})",
            )
        )
        sheet.append(
            ("Kod procedury ICD-9", "Nazwa procedury ICD-9\n", "Ranga procedury ICD-9")
            if ranks
            else (
                "Kod specjalności komórki organizacyjnej",
                "Kod świadczenia",
                "Kody ICD",
            )
        )
        sheet.append(("1", "2", "3") if ranks else (1, "2", 3))
        for row in entries:
            sheet.append(cast(tuple[str | int | float | bool | None, ...], row))
        sheet.append((None, None, None))
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


def stage_komunikat(root: Path, icd9_release_id: str) -> KomunikatManifest:
    prefix = "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultaktualnosci/354/54/1/"
    archive = root / "blobs/sha256"
    archive.mkdir(parents=True, exist_ok=True)
    files: list[KomunikatFile] = []
    for name, content in (
        ("opis_algorytmu_grupera_2026.docx", docx_bytes()),
        (
            "tabela_2_wykaz_rang_procedur_medycznych_2026.xlsx",
            companion_bytes(ranks=True),
        ),
        ("tabela_1_wykaz_specjalnosci_komorek_2026.xlsx", companion_bytes(ranks=False)),
    ):
        digest = hashlib.sha256(content).hexdigest()
        _ = (archive / digest).write_bytes(content)
        files.append(
            KomunikatFile(
                source_url=prefix + name,
                sha256=digest,
                size_bytes=len(content),
                fetched_at=datetime(2026, 10, 2, tzinfo=UTC),
            )
        )
    return KomunikatManifest(
        release_id="jgp-komunikat-synthetic",
        algorithm_version="1.1.2026",
        icd9_release_id=icd9_release_id,
        pairing="reference-only",
        publisher="Synthetic NFZ fixture",
        license="Synthetic fixture, not publisher source bytes",
        notices=(
            "Reference-only evidence. No service-date ruleset pairing or billability inference.",
        ),
        algorithm=files[0],
        ranks=files[1],
        specialties=files[2],
        expected_ranks={"SZP": 3, "AOS": 1},
        expected_specialties={"SZP": 1, "AOS": 1, "REH": 1},
    )
