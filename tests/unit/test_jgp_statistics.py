import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from minos.domain.reference import SourceError
from minos.ingest.jgp_statistics import (
    GroupFigure,
    ReportFigures,
    SectionFigure,
    parse_report,
)
from minos.ingest.jgp_statistics_manifest import JgpStatistics, MonthlyReport

# Tables 2 and 4 of the August 2026 report (dane.gov.pl resource 2834329),
# trimmed to six groups and four section rows; cell text is unchanged.
EXCERPT = (
    Path(__file__).parents[1] / "fixtures/jgp_statistics/raport-2026-08-excerpt.xml"
)
URL = "https://api.dane.gov.pl/media/resources/20261020/raport_SZP_i_kardiologia_2026_8.xml"


def _pin(content: bytes, **changes: object) -> MonthlyReport:
    return MonthlyReport.model_validate(
        {
            "month": "2026-08",
            "resource_id": 2834329,
            "source_url": URL,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "fetched_at": datetime(2026, 10, 28, tzinfo=UTC),
            "group_rows": 36,
            "section_rows": 4,
            **changes,
        }
    )


def _parse(content: bytes, **changes: object) -> ReportFigures:
    return parse_report(io.BytesIO(content), _pin(content, **changes))


def test_excerpt_keeps_suppressed_absent_and_exact_figures_apart() -> None:
    figures = _parse(EXCERPT.read_bytes())
    groups = {
        (item.code, item.hospital_level, item.month): item for item in figures.groups
    }
    section = "A Choroby układu nerwowego"
    # A13 outside PSZ has only a suppressed July 2026 figure: the empty August
    # cells are no figure, not a zero.
    assert [key for key in groups if key[:2] == ("A13", "POZA PSZ")] == [
        ("A13", "POZA PSZ", "2026-07")
    ]
    assert groups["A13", "POZA PSZ", "2026-07"] == GroupFigure(
        section, "A13", "POZA PSZ", "2026-07", 1, "<5", 468832
    )
    assert groups["A14", "POZA PSZ", "2026-08"] == GroupFigure(
        section, "A14", "POZA PSZ", "2026-08", 0, "<5", 824190
    )
    assert groups["A22", "POZA PSZ", "2025-08"].hospitalizations == 27
    assert groups["A22", "POZA PSZ", "2025-08"].points_hundredths == 41192306
    planned = [
        item
        for item in figures.sections
        if (item.admission, item.character) == ("planned", "procedural")
    ]
    # Source text: 7.8972/759/56.8248, 7.3456/900/57.45, 4.2974/3712/56.8928.
    assert planned == [
        SectionFigure(section, "planned", "procedural", "2026-08", 0, 759, 790, 568),
        SectionFigure(section, "planned", "procedural", "2026-07", 1, 900, 735, 574),
        SectionFigure(section, "planned", "procedural", "2025-08", 12, 3712, 430, 569),
    ]


def _edited(old: bytes, new: bytes) -> bytes:
    content = EXCERPT.read_bytes()
    assert content.count(old) >= 1
    return content.replace(old, new, 1)


@pytest.mark.parametrize(
    ("content", "changes", "message"),
    [
        # The reporting month is read from the headers, never assumed.
        (None, {"month": "2026-09", "source_url": URL.replace("_8.xml", "_9.xml")},
         "header differs"),
        (None, {"group_rows": 35}, "reviewed row counts"),
        (None, {"sha256": "0" * 64}, "differ from the pin"),
        (_edited(b">8241.9<", b">-8241.9<"), {}, "outside its bound"),
        (_edited(b">759<", b">759.5<"), {}, "not a whole number"),
        (_edited(b">Kod produktu<", b">Kod grupy<"), {}, "header differs"),
        (_edited(b"liczba punkt", b"liczba z\xc5\x82ot"), {}, "no longer reports points"),
        (_edited(b">POZA PSZ<", b">SZPITAL IV STOPNIA<"), {}, "row is incomplete"),
        (_edited(b"?>", b"?><!DOCTYPE x [<!ENTITY a 'b'>]>"), {}, "not readable"),
    ],
)  # fmt: skip
def test_drifted_or_unpinned_reports_fail_closed(
    content: bytes | None, changes: dict[str, object], message: str
) -> None:
    source = content if content is not None else EXCERPT.read_bytes()
    with pytest.raises(SourceError, match=message):
        if "sha256" in changes:
            _ = parse_report(io.BytesIO(source), _pin(source, **changes))
        else:
            _ = _parse(source, **changes)


def test_a_repeated_group_row_fails_closed() -> None:
    content = EXCERPT.read_bytes()
    start = content.index(b"<ss:Row>", content.index(b">A14<") - 400)
    row = content[start : content.index(b"</ss:Row>", start) + len(b"</ss:Row>")]
    assert b">A14<" in row
    with pytest.raises(SourceError, match="repeats a group"):
        _ = _parse(content.replace(row, row + row, 1), group_rows=37)


def test_manifest_binds_url_month_and_order() -> None:
    content = EXCERPT.read_bytes()
    with pytest.raises(ValidationError, match="another month"):
        _ = _pin(content, month="2026-07")
    report = _pin(content)
    july = _pin(content, month="2026-07", source_url=URL.replace("_8.xml", "_7.xml"))
    base: dict[str, object] = {
        "release_id": "jgp-statistics-test",
        "dataset": "https://dane.gov.pl/pl/dataset/2581",
        "publisher": "Narodowy Fundusz Zdrowia",
        "license": "CC0 1.0",
        "notices": ["Synthetic."],
    }

    def manifest(reports: list[MonthlyReport]) -> str:
        items = ",".join(item.model_dump_json() for item in reports)
        return json.dumps(base)[:-1] + ', "reports": [' + items + "]}"

    assert JgpStatistics.model_validate_json(manifest([july, report]))
    with pytest.raises(ValidationError, match="month order"):
        _ = JgpStatistics.model_validate_json(manifest([report, july]))
