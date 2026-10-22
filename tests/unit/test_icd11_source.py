from io import BytesIO

import pytest

from minos.domain.reference import (
    ReferenceNode,
    ReferenceRelation,
    SourceError,
    SourceHeader,
)
from minos.ingest.icd11 import parse_icd11
from tests.fixtures.icd11 import BASE, export


def _events(content: bytes) -> list[object]:
    return list(parse_icd11(BytesIO(content)))


def test_out_of_order_export_becomes_a_preorder_tree_with_verbatim_text() -> None:
    events = _events(export())

    header, *rest = events
    assert header == SourceHeader(
        (
            ("linearization", "mms"),
            ("release_date", "2026-01-17"),
            ("release_id", "2026-01"),
        ),
        (("languages", "pl"),),
    )
    nodes = [event for event in rest if isinstance(event, ReferenceNode)]
    assert [(n.ordinal, n.kind, n.lookup_code, n.source_text) for n in nodes] == [
        (1, "range", "01", "Syntetyczny rozdział"),
        (2, "container", None, "Syntetyczny blok"),
        (3, "classification", "1A00", "Syntetyczna choroba"),
        (4, "text", None, "syntetyczne włączenie"),
        # The index term equal to the title is not repeated.
        (5, "text", None, "syntetyczny termin indeksu"),
        (6, "classification", "1A00.Z", "Syntetyczna choroba, nieokreślona"),
        (7, "classification", "1A01", "Druga syntetyczna choroba"),
    ]
    assert dict(nodes[2].attributes) == {
        "uri": f"{BASE}/3",
        "opis": "Syntetyczna definicja.",
        "wylaczenia": "Syntetyczne wyłączenie",
    }
    assert nodes[3].attributes == (("evidence_kind", "Obejmuje"),)
    assert nodes[4].attributes == ()
    relations = [
        (r.parent_ordinal, r.child_ordinal)
        for r in rest
        if isinstance(r, ReferenceRelation)
    ]
    assert relations == [(1, 2), (2, 3), (3, 4), (3, 5), (3, 6), (2, 7)]


@pytest.mark.parametrize(
    "content,reason",
    [
        (export(language="en"), "label is not Polish"),
        (
            export().replace(b"<icd11>", b'<!DOCTYPE icd11 [<!ENTITY x "y">]><icd11>'),
            "malformed or declares entities",
        ),
        (
            export().replace(
                f"<parent>{BASE}/2</parent><code>1A01".encode(),
                f"<parent>{BASE}/1</parent><code>1A01".encode(),
            ),
            "does not name its parent",
        ),
        (
            export(
                extra=f"<element><parent>{BASE}/9</parent><code>1A02</code><id>{BASE}/8</id><classKind>category</classKind><title><value>x</value><language>pl</language></title></element>"
            ),
            "not reachable",
        ),
        (export().replace(b"<code>1A01</code>", b"<code>1a01</code>"), "code shape"),
        (
            export().replace(
                b"/release/11/2026-01/mms/4<", b"/release/11/2025-01/mms/4<"
            ),
            "not of this MMS release",
        ),
    ],
    ids=["english", "entity", "wrong_parent", "orphan", "code", "release"],
)
def test_unreviewed_structure_fails_the_source(content: bytes, reason: str) -> None:
    with pytest.raises(SourceError, match=reason):
        _ = _events(content)


def test_a_source_over_its_byte_bound_fails_while_reading() -> None:
    with pytest.raises(SourceError, match="bytes exceed limit"):
        _ = list(parse_icd11(BytesIO(export()), max_bytes=1024))
