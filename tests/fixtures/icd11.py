"""Synthetic ICD-11 PL export and WHO map workbooks; no real ICD-11 text."""

import hashlib
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from minos.ingest.icd11_maps_manifest import MAP_FILE_NAMES, MapKind

RELEASE = "2026-01"
BASE = f"http://id.who.int/icd/release/11/{RELEASE}/mms"
XML_UUID = "00000000-0000-4000-8000-000000000011"
MAP_UUIDS: dict[MapKind, str] = {
    "10to11_one": "00000000-0000-4000-8000-000000000101",
    "10to11_many": "00000000-0000-4000-8000-000000000102",
    "11to10_one": "00000000-0000-4000-8000-000000000103",
}


def _label(tag: str, text: str, language: str = "pl") -> str:
    return (
        f"<{tag}><label><value>{escape(text)}</value>"
        + f"<language>{language}</language></label></{tag}>"
    )


def export(*, language: str = "pl", extra: str = "", reorder: bool = True) -> bytes:
    """Root, one chapter, one block and two categories, children listed out of order."""
    root = f"<element><id>{BASE}</id><child>{BASE}/1</child></element>"
    chapter = (
        f"<element><parent>{BASE}</parent><code>01</code><id>{BASE}/1</id>"
        + "<classKind>chapter</classKind>"
        + f"<title><value>Syntetyczny rozdział</value><language>{language}</language></title>"
        + f"<child>{BASE}/2</child></element>"
    )
    block = (
        f"<element><parent>{BASE}/1</parent><code /><id>{BASE}/2</id>"
        + "<classKind>block</classKind><title><value>Syntetyczny blok</value>"
        + f"<language>pl</language></title><child>{BASE}/3</child><child>{BASE}/4</child></element>"
    )
    first = (
        f"<element><parent>{BASE}/2</parent><code>1A00</code><id>{BASE}/3</id>"
        + "<classKind>category</classKind><title><value>Syntetyczna choroba</value>"
        + "<language>pl</language></title>"
        + "<definition><value>Syntetyczna definicja.</value><language>pl</language></definition>"
        + _label("inclusion", "syntetyczne włączenie")
        + _label("exclusion", "Syntetyczne wyłączenie")
        + _label("indexTerm", "Syntetyczna choroba")
        + _label("indexTerm", "syntetyczny termin indeksu")
        + f"<child>{BASE}/3/unspecified</child></element>"
    )
    residual = (
        f"<element><parent>{BASE}/3</parent><code>1A00.Z</code><id>{BASE}/3/unspecified</id>"
        + "<classKind>category</classKind><title><value>Syntetyczna choroba, nieokreślona</value>"
        + "<language>pl</language></title></element>"
    )
    second = (
        f"<element><parent>{BASE}/2</parent><code>1A01</code><id>{BASE}/4</id>"
        + "<classKind>category</classKind><title><value>Druga syntetyczna choroba</value>"
        + "<language>pl</language></title></element>"
    )
    elements = [root, chapter, block, first, residual, second]
    if reorder:
        # The WHO export lists entities in no tree order.
        elements = [
            elements[0],
            elements[5],
            elements[3],
            elements[1],
            elements[4],
            elements[2],
        ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<icd11>'
        + f"<releaseId>{RELEASE}</releaseId><releaseDate>2026-01-17</releaseDate>"
        + "<linearization><code>mms</code><availableLanguages>pl</availableLanguages><elements>"
        + "".join(elements)
        + extra
        + "</elements></linearization></icd11>"
    ).encode()


# Nodes: chapter, block, 1A00 with an inclusion and one new index term, 1A00.Z, 1A01.
EXPECTED = {"nodes": 7, "relations": 6, "concepts": 4, "descriptions": 4}

_TO_11 = (
    "10ClassKind", "10DepthInKind", "icd10Code", "icd10Chapter", "icd10Title",
    "11ClassKind", "11DepthInKind", "ICD-11 FoundationURI", "Linearization (releaseURI)",
    "icd11Code", "icd11Chapter", "icd11Title", "2026-Jan-17",
)  # fmt: skip
_TO_11_MANY = (
    "10ClassKind", "Depth", "icd10Code", "icd10Chapter", "icd10Title",
    "11ClassKind", "Depth", "ICD-11 Foundation URI", "Linearization (release) URI",
    "icd11Code", "icd11Chapter", "icd11Title", "2026-Jan-17",
)  # fmt: skip
_TO_10 = (
    "Linearization (release) URI", "icd11Code", "icd11Chapter", "icd11Title",
    "icd10Code", "icd10Chapter", "icd10Title", "2026-Jan-17",
)  # fmt: skip


def map_workbook(kind: MapKind, *, header: tuple[str, ...] | None = None) -> bytes:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "map"
    if kind == "11to10_one":
        sheet.append(header or _TO_10)
        sheet.append(
            (
                f"{BASE}/3",
                "1A00",
                "01",
                "Syntetyczna choroba",
                "E11",
                "IV",
                "Syntetyczny tytuł WHO",
            )
        )
        sheet.append(
            (
                f"{BASE}/1",
                1,
                1,
                "Syntetyczny rozdział",
                "I",
                "I",
                "Syntetyczny rozdział WHO",
            )
        )
    else:
        sheet.append(header or (_TO_11 if kind == "10to11_one" else _TO_11_MANY))
        sheet.append(("category", 1, "E11", "IV", "Syntetyczny tytuł WHO", "category", 1,
                      "http://id.who.int/icd/entity/3", f"{BASE}/3", "1A00", "01", "Syntetyczna choroba"))  # fmt: skip
        sheet.append(("category", 2, "E11.0", "IV", "Syntetyczny podtytuł WHO", "category", 1,
                      "http://id.who.int/icd/entity/9", f"{BASE}/3", "1A00&XN0", "01", "Synthetic / cluster"))  # fmt: skip
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def stage(root: Path, content: bytes) -> tuple[str, int]:
    blobs = root / "blobs" / "sha256"
    blobs.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    path = blobs / digest
    if not path.exists():
        _ = path.write_bytes(content)
        path.chmod(0o444)
    return digest, len(content)


def source(root: Path) -> dict[str, object]:
    digest, size = stage(root, export())
    return {
        "release_id": f"icd11-rsk-{RELEASE}-{digest[:12]}",
        "system": "icd11-pl",
        "origin": "rsk",
        "source_version": RELEASE,
        "source_file_id": XML_UUID,
        "source_url": f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{XML_UUID}",
        "publisher": "World Health Organization",
        "fetched_at": "2026-10-22T10:50:55Z",
        "source_created_at": None,
        "valid_from": None,
        "valid_to": None,
        "sha256": digest,
        "size_bytes": size,
        "license": "CC BY-ND 4.0",
        "notices": ["Synthetic ICD-11 fixture."],
        "expected": EXPECTED,
    }


def maps(root: Path, icd11_release_id: str) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for kind, stem in MAP_FILE_NAMES.items():
        digest, size = stage(root, map_workbook(kind))
        files.append(
            {
                "kind": kind,
                "file_name": f"{stem}_{RELEASE}.xlsx",
                "source_url": f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{MAP_UUIDS[kind]}",
                "sha256": digest,
                "size_bytes": size,
                "fetched_at": "2026-10-22T10:50:56Z",
                "expected_rows": 2,
            }
        )
    return {
        "release_id": f"icd11-maps-who-{RELEASE}",
        "icd11_release_id": icd11_release_id,
        "notices": ["Synthetic WHO map fixture."],
        "files": files,
    }


def wide_export(blocks: int) -> bytes:
    """Chapter 01 with many empty blocks before chapter 02 and its one category."""
    parts = [
        f"<element><id>{BASE}</id><child>{BASE}/c1</child><child>{BASE}/c2</child></element>"
    ]
    parts.append(
        f"<element><parent>{BASE}</parent><code>01</code><id>{BASE}/c1</id>"
        + "<classKind>chapter</classKind><title><value>Rozdział</value><language>pl</language></title>"
        + "".join(f"<child>{BASE}/b{index}</child>" for index in range(blocks))
        + "</element>"
    )
    parts.extend(
        f"<element><parent>{BASE}/c1</parent><code /><id>{BASE}/b{index}</id>"
        + "<classKind>block</classKind><title><value>Blok</value><language>pl</language></title></element>"
        for index in range(blocks)
    )
    parts.append(
        f"<element><parent>{BASE}</parent><code>02</code><id>{BASE}/c2</id>"
        + "<classKind>chapter</classKind><title><value>Drugi rozdział</value><language>pl</language></title>"
        + f"<child>{BASE}/k</child></element>"
    )
    parts.append(
        f"<element><parent>{BASE}/c2</parent><code>2A00</code><id>{BASE}/k</id>"
        + "<classKind>category</classKind><title><value>Kategoria</value><language>pl</language></title></element>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<icd11>'
        + f"<releaseId>{RELEASE}</releaseId><releaseDate>2026-01-17</releaseDate>"
        + "<linearization><code>mms</code><elements>"
        + "".join(parts)
        + "</elements></linearization></icd11>"
    ).encode()


def ranking_export() -> bytes:
    """Chapter X listed first, with an extension code whose title starts with the query."""
    parts = [
        f"<element><id>{BASE}</id><child>{BASE}/cx</child><child>{BASE}/c1</child></element>"
    ]
    for chapter, code, title, uri in (
        ("X", "XA0001", "Szczepionka syntetyczna rozszerzenie", "x1"),
        ("01", "1A00", "Syntetyczna szczepionka", "a1"),
    ):
        node = "cx" if chapter == "X" else "c1"
        parts.append(
            f"<element><parent>{BASE}</parent><code>{chapter}</code><id>{BASE}/{node}</id>"
            + f"<classKind>chapter</classKind><title><value>Rozdział {chapter}</value><language>pl</language></title>"
            + f"<child>{BASE}/{uri}</child></element>"
        )
        parts.append(
            f"<element><parent>{BASE}/{node}</parent><code>{code}</code><id>{BASE}/{uri}</id>"
            + f"<classKind>category</classKind><title><value>{title}</value><language>pl</language></title></element>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<icd11>'
        + f"<releaseId>{RELEASE}</releaseId><releaseDate>2026-01-17</releaseDate>"
        + "<linearization><code>mms</code><elements>"
        + "".join(parts)
        + "</elements></linearization></icd11>"
    ).encode()
