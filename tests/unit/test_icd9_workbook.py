import struct
from dataclasses import replace
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import openpyxl
import pytest

from minos.domain.reference import (
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd9_workbook import parse_icd9_workbook
from minos.ingest.workbook_archive import DEFAULT_WORKBOOK_LIMITS, preflight_workbook
from tests.fixtures.icd9_workbook import (
    archive_bytes,
    workbook_manifest,
    workbook_parts,
)


def test_explicit_table_preserves_raw_cells_levels_conflicts_and_notes() -> None:
    content = archive_bytes()
    events = list(parse_icd9_workbook(BytesIO(content), workbook_manifest(content)))
    assert isinstance(events[0], SourceHeader)
    assert dict(events[0].attributes) == {
        "worksheet": "ICD-9_PL ",
        "status_evidence": "not supplied by workbook",
    }
    nodes = [event for event in events if isinstance(event, ReferenceNode)]
    assert [(node.lookup_code, node.source_code) for node in nodes[:5]] == [
        ("AA", "AA"),
        ("A", "A"),
        ("A01", "A01 "),
        ("A03", "A03"),
        ("88", "88"),
    ]
    assert nodes[2].source_text == "  Badanie moczu  "
    assert dict(nodes[2].attributes)["level"] == "3"
    assert dict(nodes[4].attributes)["cell_type"] == "int"
    assert all("status" not in dict(node.attributes) for node in nodes)
    assert [(node.kind, node.source_text) for node in nodes[5:]] == [
        ("text", "Different synthetic title"),
        ("text", "Synthetic instruction"),
        ("text", "Synthetic orphan note"),
    ]
    edges = [event for event in events if isinstance(event, ReferenceRelation)]
    assert [(edge.parent_ordinal, edge.child_ordinal) for edge in edges] == [
        (1, 2),
        (2, 3),
        (2, 4),
        (3, 6),
        (3, 7),
    ]
    other = workbook_manifest(content).model_copy(
        update={"source_file_id": "icd-9_pl_cm_v_5.90.xlsx#ICD-9_PL"}
    )
    selected = [
        event
        for event in parse_icd9_workbook(BytesIO(content), other)
        if isinstance(event, ReferenceNode)
    ]
    assert selected[0].source_text == "Other table title"
    assert selected[2].source_text == "Other preferred title"


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (b'<mergeCell ref="A1:B1"/>', b'<mergeCell ref="A3:B3"/>', "merges"),
        ("Rozdział".encode(), b"Unexpected header", "header"),
        (
            b'<c r="E3" t="inlineStr"><is><t xml:space="preserve">A01 </t></is></c>',
            b'<c r="E3"><f>1+1</f><v>2</v></c>',
            "formula",
        ),
        (b'<c r="A6" t="n"><v>88</v></c>', b'<c r="A6" t="n"><v>88.25</v></c>', "type"),
        (b'<c r="A6" t="n"><v>88</v></c>', b'<c r="A6" t="b"><v>1</v></c>', "type"),
        (
            b'<c r="A6" t="n"><v>88</v></c>',
            b'<c r="A6" t="d"><v>2026-09-24T00:00:00</v></c>',
            "date",
        ),
        (
            b'<c r="A6" t="n"><v>88</v></c>',
            b'<c r="A6" t="e"><v>#REF!</v></c>',
            "error",
        ),
    ],
)
def test_adversarial_typed_cells_or_layout_stop_ingestion(
    old: bytes, new: bytes, reason: str
) -> None:
    parts = workbook_parts()
    original = parts["xl/worksheets/sheet1.xml"]
    parts["xl/worksheets/sheet1.xml"] = original.replace(old, new)
    assert parts["xl/worksheets/sheet1.xml"] != original
    content = archive_bytes(parts)
    with pytest.raises(SourceError, match=reason):
        _ = list(parse_icd9_workbook(BytesIO(content), workbook_manifest(content)))


@pytest.mark.parametrize("dimension", [b"A1:A1", b"A1:XFD1048576"])
def test_actual_xml_extents_override_untrusted_dimensions(dimension: bytes) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b"A1:H6", dimension
    )
    content = archive_bytes(parts)
    nodes = [
        event
        for event in parse_icd9_workbook(BytesIO(content), workbook_manifest(content))
        if isinstance(event, ReferenceNode)
    ]
    assert len(nodes) == 8
    assert nodes[4].lookup_code == "88"


@pytest.mark.parametrize(
    "damage",
    [
        "truncated",
        "crc",
        "deflate",
        "duplicate",
        "dtd",
        "external",
        "path",
        "count",
        "zip64",
        "deep",
        "rows",
        "missing",
        "duplicate_row",
        "duplicate_cell",
        "cell_row_conflict",
    ],
)
def test_malformed_archives_fail_before_workbook_reader(
    monkeypatch: pytest.MonkeyPatch, damage: str
) -> None:
    parts = workbook_parts()
    if damage == "dtd":
        parts["xl/worksheets/sheet1.xml"] = (
            b'<!DOCTYPE worksheet [<!ENTITY x "secret">]>'
            + parts["xl/worksheets/sheet1.xml"]
        )
    elif damage == "external":
        parts["xl/_rels/workbook.xml.rels"] = parts[
            "xl/_rels/workbook.xml.rels"
        ].replace(
            b'Target="worksheets/sheet1.xml"',
            b'Target="https://example.invalid/private" TargetMode="External"',
        )
    elif damage == "path":
        parts["../escape.xml"] = b"<root/>"
    elif damage == "deep":
        parts["docProps/core.xml"] = b"<a>" * 40 + b"</a>" * 40
    elif damage == "rows":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="6">', b'<row r="20001">'
        )
    elif damage == "missing":
        del parts["[Content_Types].xml"]
    elif damage == "duplicate_row":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<row r="6">', b'<row r="5">'
        )
    elif damage == "duplicate_cell":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<c r="B6"', b'<c r="A6"'
        )
    elif damage == "cell_row_conflict":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<c r="A6"', b'<c r="A5"'
        )
    content = archive_bytes(
        parts, compression=ZIP_DEFLATED if damage == "deflate" else 0
    )
    if damage == "truncated":
        content = content[:-10]
    elif damage == "deflate":
        corrupted = bytearray(content)
        name, extra = struct.unpack_from("<HH", corrupted, 26)
        corrupted[30 + name + extra] = 0xFF
        content = bytes(corrupted)
    elif damage == "crc":
        content = content.replace(b"Synthetic parent", b"Synthetic qarest", 1)
    elif damage == "duplicate":
        buffer = BytesIO(content)
        with pytest.warns(UserWarning, match="Duplicate"):
            with ZipFile(buffer, "a") as archive:
                archive.writestr("xl/workbook.xml", parts["xl/workbook.xml"])
        content = buffer.getvalue()
    elif damage in ("count", "zip64"):
        records = bytearray(content)
        offset = records.rfind(b"PK\x05\x06")
        struct.pack_into(
            "<HH",
            records,
            offset + 8,
            1 if damage == "count" else 65535,
            1 if damage == "count" else 65535,
        )
        content = bytes(records)
    called = False

    def forbidden(*_args: object, **_kwargs: object) -> None:
        nonlocal called
        called = True
        pytest.fail("reader was reached before ZIP/XML rejection")

    monkeypatch.setattr(openpyxl, "load_workbook", forbidden)
    with pytest.raises(SourceError):
        _ = list(parse_icd9_workbook(BytesIO(content), workbook_manifest(content)))
    assert not called


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_source_bytes", 128),
        ("max_entries", 3),
        ("max_entry_bytes", 128),
        ("max_expanded_bytes", 128),
        ("max_compression_ratio", 1),
        ("max_xml_elements", 10),
        ("max_xml_depth", 2),
        ("max_text_characters", 8),
        ("max_rows", 3),
        ("max_columns", 3),
    ],
)
def test_archive_limits_are_enforced_before_reader(field: str, value: int) -> None:
    content = archive_bytes(compression=ZIP_DEFLATED)
    with pytest.raises(SourceError, match="limit"):
        _ = preflight_workbook(
            BytesIO(content), limits=replace(DEFAULT_WORKBOOK_LIMITS, **{field: value})
        )


def test_manifest_cannot_silently_choose_a_table_or_version() -> None:
    content = archive_bytes()
    source = workbook_manifest(content)
    for update in (
        {"source_version": "5.91"},
        {"source_file_id": "icd-9_pl_cm_v_5.90.xlsx"},
    ):
        with pytest.raises(SourceError, match="manifest|identity"):
            _ = list(
                parse_icd9_workbook(BytesIO(content), source.model_copy(update=update))
            )
