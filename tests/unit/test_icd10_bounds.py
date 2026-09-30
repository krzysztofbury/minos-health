from dataclasses import replace
from io import BytesIO

import pytest

from minos.domain.reference import SourceError
from minos.ingest.icd10 import XmlLimits, parse_icd10

DOCUMENT = (
    b'<hcd xmlns="http://rsk.rejestrymedyczne.csioz.gov.pl" hcd="00CD10" '
    b'visible="true" closed="2" version="7" applicationdate="2017-04-24" '
    b'registrationdate="2017-06-22" maxchars="100">'
    b"<expirationreason>x</expirationreason><name>x</name><common_name>x</common_name>"
    b"<codesformat>x</codesformat><charset>x</charset><minreusetime>x</minreusetime>"
    b"<languages>x</languages><link>x</link>"
    b'<nodes><node code="E11"><name>x</name></node></nodes></hcd>'
)


def test_exact_resource_limits_are_inclusive() -> None:
    limits = XmlLimits(
        max_bytes=len(DOCUMENT),
        max_depth=4,
        max_elements=12,
        max_nodes=1,
        max_text_characters=1,
        max_attributes=7,
        max_attribute_characters=10,
    )
    assert len(tuple(parse_icd10(BytesIO(DOCUMENT), limits=limits))) == 2


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("max_bytes", len(DOCUMENT) - 1, "bytes"),
        ("max_depth", 3, "depth"),
        ("max_elements", 11, "element count"),
        ("max_attributes", 6, "attribute count"),
        ("max_attribute_characters", 9, "attribute value"),
    ],
)
def test_one_above_the_limit_is_rejected(field: str, value: int, reason: str) -> None:
    with pytest.raises(SourceError, match=reason):
        _ = tuple(
            parse_icd10(
                BytesIO(DOCUMENT), limits=replace(XmlLimits(), **{field: value})
            )
        )
