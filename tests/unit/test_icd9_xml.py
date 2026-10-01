from dataclasses import replace
from io import BytesIO

import pytest

from minos.domain.reference import (
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd9 import DEFAULT_ICD9_XML_LIMITS, parse_icd9_xml
from tests.fixtures.icd9_xml import XML, source_manifest


def test_stream_preserves_complete_catalog_without_inventing_hierarchy() -> None:
    events = list(parse_icd9_xml(BytesIO(XML), source_manifest()))
    assert isinstance(events[0], SourceHeader)
    assert dict(events[0].attributes)["nr_gen"] == "93"
    nodes = [event for event in events if isinstance(event, ReferenceNode)]
    assert [(node.ordinal, node.lookup_code) for node in nodes] == [
        (1, "A"),
        (2, "AA"),
        (3, "A01"),
        (4, "00.00001"),
    ]
    assert not any(isinstance(event, ReferenceRelation) for event in events)
    assert nodes[2].source_text == "  Badanie moczu  "
    assert nodes[2].descriptions[0].normalized_text == "badanie moczu"
    assert dict(nodes[2].attributes) == {
        "status": "N",
        "opis": "Synthetic note",
        "wlaczenia": "Includes evidence",
        "wylaczenia": "Excludes evidence",
        "data_od": "2008-07-01",
        "data_do": "2026-09-24",
    }
    assert len(nodes[2].descriptions) == 1


@pytest.mark.parametrize(
    ("old", "new", "reason"),
    [
        (b'symbol="5.90"', b'symbol="5.89"', "manifest"),
        (b'nr_gen="93"', b'nr_gen="92"', "manifest"),
        (b'typ="ICD9"', b'typ="MODTAR"', "root"),
        (b'wersja="1.1"', b'wersja="2.0"', "root"),
        (b'edycja="5"', b'edycja="4"', "edition"),
        (b'instytucja="NFZ"', b'instytucja="OTHER"', "root"),
        (b'status="N"', b'status="?"', "entry"),
        (b'kod="A01"', b'kod=" A01"', "entry"),
        (b'data_od="2008-07-01"', b'data_od="2008-02-30"', "date"),
        (b'data_od="2008-07-01"', b'data_od="2026-09-25"', "period"),
        (b'czas_pub="2026-09-24T08:35:45"', b'czas_pub="2026-02-30T08:35:45"', "root"),
        (
            b'<daty_proc data_od="2026-09-24"/>',
            b'<daty_proc data_od="2026-09-24"/><daty_proc data_od="2026-09-24"/>',
            "structure",
        ),
        (b'<daty_proc data_od="2026-09-24"/>', b"<unknown/>", "structure"),
        (b'<pozycja_slownika kod="A"', b'<pozycja_slownika other="x" kod="A"', "entry"),
        (b"<komunikat typ=", b'<komunikat xmlns="urn:unexpected" typ=', "root"),
        (b"Synthetic chapter", b"", "entry"),
        (b"</komunikat>", b"not whitespace</komunikat>", "text"),
        (
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b'<?xml version="1.0"?><!DOCTYPE komunikat [<!ENTITY value "x">]>',
            "prohibited",
        ),
        (
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b'<?xml version="1.0" encoding="ISO-8859-2"?>',
            "encoding",
        ),
        (
            b'<?xml version="1.0" encoding="UTF-8"?>',
            b'<?xml version="1.0"?><?source file="secret"?>',
            "prohibited",
        ),
    ],
)
def test_rejects_unsupported_source_contract(
    old: bytes, new: bytes, reason: str
) -> None:
    content = XML.replace(old, new)
    assert content != XML
    with pytest.raises(SourceError, match=reason):
        _ = list(parse_icd9_xml(BytesIO(content), source_manifest(content)))


@pytest.mark.parametrize(
    "content", [XML[:-12], b"", XML.replace(b'kod="A01"', b'kod="A"')]
)
def test_truncated_empty_or_duplicate_catalog_fails(content: bytes) -> None:
    with pytest.raises(SourceError):
        _ = list(parse_icd9_xml(BytesIO(content), source_manifest()))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_bytes", 128),
        ("max_nodes", 2),
        ("max_elements", 3),
        ("max_depth", 2),
        ("max_attributes", 2),
        ("max_attribute_characters", 8),
    ],
)
def test_resource_limits_stop_the_actual_stream(field: str, value: int) -> None:
    limits = replace(DEFAULT_ICD9_XML_LIMITS, **{field: value})
    with pytest.raises(SourceError, match="limit"):
        _ = list(parse_icd9_xml(BytesIO(XML), source_manifest(), limits=limits))
