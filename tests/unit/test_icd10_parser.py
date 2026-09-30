from dataclasses import replace
from io import BytesIO
from pathlib import Path
from typing import override

import pytest

from minos.domain.normalization import normalize_description
from minos.domain.reference import (
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd10 import XmlLimits, parse_icd10

FIXTURES = Path(__file__).parents[1] / "fixtures" / "icd10-pl"


def test_synthetic_source_preserves_structure_and_distinguishes_codes() -> None:
    with (FIXTURES / "minimal.xml").open("rb") as source:
        events = tuple(parse_icd10(source))
    assert isinstance(events[0], SourceHeader)
    assert dict(events[0].attributes)["version"] == "7"
    nodes = {
        event.ordinal: event for event in events if isinstance(event, ReferenceNode)
    }
    assert len(nodes) == 10
    assert nodes[2].kind == "container"
    assert nodes[2].source_text == ""
    assert nodes[3].source_text == "  Próbna etykieta Łódź  "
    assert nodes[4].source_code == ".0"
    assert nodes[4].lookup_code == "E11.0"
    assert nodes[7].source_code == ".2†"
    assert nodes[7].lookup_code == "E11.2"
    assert dict(nodes[7].attributes)["annotationPL"] == "Notatka próbna"
    assert nodes[9].kind == "modifier"
    assert nodes[9].lookup_code is None
    assert nodes[5].source_text == nodes[6].source_text == "Przykład opisowy"
    assert dict(nodes[6].attributes)["EN"] == ""
    assert set(event for event in events if isinstance(event, ReferenceRelation)) == {
        ReferenceRelation(1, 2, "contains"),
        ReferenceRelation(2, 3, "contains"),
        ReferenceRelation(3, 4, "contains"),
        ReferenceRelation(4, 5, "contains"),
        ReferenceRelation(4, 6, "contains"),
        ReferenceRelation(3, 7, "contains"),
        ReferenceRelation(8, 9, "contains"),
    }


@pytest.mark.parametrize(
    ("filename", "reason"),
    [("dtd.xml", "prohibited"), ("unsupported.xml", "unsupported XML structure")],
)
def test_committed_adversarial_structures_fail(filename: str, reason: str) -> None:
    with (FIXTURES / filename).open("rb") as source:
        with pytest.raises(SourceError, match=reason):
            _ = tuple(parse_icd10(source))


@pytest.mark.parametrize(
    ("field", "limit", "reason"),
    [
        ("max_bytes", 100, "bytes"),
        ("max_depth", 4, "depth"),
        ("max_elements", 10, "element count"),
        ("max_nodes", 5, "node count"),
        ("max_text_characters", 8, "text"),
        ("max_attributes", 6, "attribute count"),
        ("max_attribute_characters", 4, "attribute value"),
    ],
)
def test_each_resource_bound_rejects_a_valid_source(
    field: str, limit: int, reason: str
) -> None:
    limits = replace(XmlLimits(), **{field: limit})
    with (FIXTURES / "minimal.xml").open("rb") as source:
        with pytest.raises(SourceError, match=reason):
            _ = tuple(parse_icd10(source, limits=limits))


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (b"<hcd xmlns=", b"<wrong xmlns=", "root"),
        (b"rsk.rejestrymedyczne.csioz.gov.pl", b"example.invalid", "namespace"),
        (b'hcd="00CD10"', b'hcd="00CD09"', "root attributes"),
        (b'visible="true"', b'visible="perhaps"', "visibility"),
        (b'version="7"', b'version="unknown"', "numeric"),
        (b'applicationdate="2017-04-24"', b'applicationdate="2017-02-30"', "date"),
        (b'<node code="E11">', b'<node code="E11" unknown="x">', "attribute"),
        (b'<node code="E11">', b'<node code="invalid">', "code shape"),
        (b'<node code="E11">', b'<node code="E11.1">', "category parent"),
        (
            b'<attribute name="EN">Synthetic category',
            b'<attribute name="new">Synthetic category',
            "node attribute",
        ),
        (b"<name>Wariant zerowy</name>", b"<name> </name>", "empty name"),
        (b"<name>Wariant zerowy</name>", b"<name>x</name><name>y</name>", "duplicate"),
        (b"<name>Wariant zerowy</name>", b"<name>x<extra/>y</name>", "structure"),
        (
            b"<name>Wariant zerowy</name>",
            b"x<name>Wariant zerowy</name>",
            "mixed content",
        ),
        (b"<expirationreason>synthetic</expirationreason>", b"", "missing root field"),
        (
            b"<common_name>ICD10</common_name>",
            b"<common_name></common_name>",
            "empty root field",
        ),
        (b'encoding="UTF-8"', b'encoding="ISO-8859-1"', "encoding"),
        (b"</hcd>", b"</hcd><extra/>", "malformed XML"),
    ],
)
def test_meaning_changing_structures_fail(old: bytes, new: bytes, reason: str) -> None:
    content = (FIXTURES / "minimal.xml").read_bytes()
    assert old in content
    with pytest.raises(SourceError, match=reason):
        _ = tuple(parse_icd10(BytesIO(content.replace(old, new, 1))))


def test_empty_node_cannot_be_silently_dropped() -> None:
    content = (FIXTURES / "minimal.xml").read_bytes()
    content = content.replace(b'<node code="0">', b'<node code="">').replace(
        b"<name>Pr\xc3\xb3bny modyfikator</name>", b"<name/>"
    )
    with pytest.raises(SourceError, match="empty container"):
        _ = tuple(parse_icd10(BytesIO(content)))


@pytest.mark.parametrize(
    "declaration",
    [
        b'<!DOCTYPE hcd SYSTEM "https://example.invalid/never-fetch">',
        b'<!DOCTYPE hcd [<!ENTITY x SYSTEM "file:///never-read">]>',
        b"<?unreviewed instruction?>",
    ],
)
def test_external_access_and_processing_instructions_are_rejected(
    declaration: bytes,
) -> None:
    content = (FIXTURES / "minimal.xml").read_bytes().split(b"\n", 1)[1]
    with pytest.raises(SourceError, match="prohibited"):
        _ = tuple(parse_icd10(BytesIO(declaration + content)))


def test_fragmented_utf8_and_cdata_preserve_source_text() -> None:
    class FragmentedSource(BytesIO):
        @override
        def read(self, size: int | None = -1) -> bytes:
            return super().read(min(size if size is not None else 7, 7))

    content = (
        (FIXTURES / "minimal.xml")
        .read_bytes()
        .replace(
            "<name>Żółć próbna</name>".encode(),
            "<name><![CDATA[Żółć próbna]]></name>".encode(),
        )
    )
    nodes = [
        event
        for event in parse_icd10(FragmentedSource(content))
        if isinstance(event, ReferenceNode)
    ]
    assert (
        next(node for node in nodes if node.ordinal == 10).source_text == "Żółć próbna"
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("  ŁÓDŹ\tŻółć\n", "łódź żółć"),
        ("Cafe\u0301", "café"),
        ("A-B (próbny)", "a-b (próbny)"),
        ("Straße", "strasse"),
    ],
)
def test_normalization_keeps_diacritics_and_punctuation(
    source: str, expected: str
) -> None:
    assert normalize_description(source) == expected


def test_implicit_utf16_is_not_accepted_as_the_reviewed_utf8_contract() -> None:
    content = (FIXTURES / "minimal.xml").read_text().split("\n", 1)[1]
    with pytest.raises(SourceError, match="encoding"):
        _ = tuple(parse_icd10(BytesIO(content.encode("utf-16"))))
