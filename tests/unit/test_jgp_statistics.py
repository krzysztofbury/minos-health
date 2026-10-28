import hashlib
import io
import json
from datetime import UTC, date, datetime
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
from minos.ingest.jgp_statistics_manifest import JgpStatistics, MonthlyReport, ReportPin

# Tables 2 and 4 of the August 2026 report (dane.gov.pl resource 2834329),
# trimmed to six groups and four section rows; cell text is unchanged.
EXCERPT = (
    Path(__file__).parents[1] / "fixtures/jgp_statistics/raport-2026-08-excerpt.xml"
)
URL = "https://api.dane.gov.pl/media/resources/20261020/raport_SZP_i_kardiologia_2026_8.xml"


def _pin(content: bytes, **changes: object) -> ReportPin:
    return ReportPin.model_validate(
        {
            "month": "2026-08",
            "resource_id": 2834329,
            "source_url": URL,
            "published_on": date(2026, 10, 20),
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
        section, "A13", "POZA PSZ", "2026-07", 1, "<5", 46883200
    )
    assert groups["A14", "POZA PSZ", "2026-08"] == GroupFigure(
        section, "A14", "POZA PSZ", "2026-08", 0, "<5", 82419000
    )
    assert groups["A22", "POZA PSZ", "2025-08"].hospitalizations == 27
    # Points keep the four decimals NFZ reports (411923.06).
    assert groups["A22", "POZA PSZ", "2025-08"].points_ten_thousandths == 4119230600
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


def _edited(old: bytes, new: bytes, *, within: bytes | None = None) -> bytes:
    """Replace the first ``old``, inside the row holding ``within`` if given."""
    content = EXCERPT.read_bytes()
    start, end = 0, len(content)
    if within is not None:
        at = content.index(within)
        start = content.rindex(b"<ss:Row>", 0, at)
        end = content.index(b"</ss:Row>", at)
    position = content.index(old, start, end)
    return content[:position] + new + content[position + len(old) :]


_A22 = b">A22<"


def _case_id(value: object) -> str | None:
    # Name each case by its expected message, not by its edited bytes.
    return value if isinstance(value, str) else None


_EMPTY = b"<ss:Cell />"


@pytest.mark.parametrize(
    ("content", "changes", "message"),
    [
        # The reporting month is read from the headers, never assumed.
        (None, {"month": "2026-09", "source_url": URL.replace("_8.xml", "_9.xml")},
         "header differs"),
        (None, {"group_rows": 35}, "exceeds its reviewed group rows"),
        (None, {"group_rows": 37}, "reviewed row counts"),
        (None, {"sha256": "0" * 64}, "differ from the pin"),
        (_edited(b">8241.9<", b">-8241.9<"), {}, "not a plain number"),
        (_edited(b">8241.9<", b">8.2419e3<"), {}, "not a plain number"),
        (_edited(b">8241.9<", b">8_241.9<"), {}, "not a plain number"),
        (_edited(b">8241.9<", ">٨٢٤١<".encode()), {}, "not a plain number"),
        (_edited(b">759<", b">759.5<"), {}, "not a whole number"),
        (_edited(b">759<", b">0759<"), {}, "not a whole number"),
        # A missing empty cell or a backward index would move a value into
        # another month; both are refused.
        (_edited(_EMPTY + _EMPTY, b"", within=_A22), {}, "row width differs"),
        (_edited(b"<ss:Cell><ss:Data ss:Type=\"String\">27<",
                 b"<ss:Cell ss:Index=\"5\"><ss:Data ss:Type=\"String\">27<", within=_A22),
         {}, "cell index is invalid"),
        (_edited(b">A14<", b"><ss:B>A14</ss:B><"), {}, "formatted or repeated"),
        (_edited(b">Sekcja JGP<", b">Sekcja<"), {}, "header differs"),
        (_edited(b">Kod produktu<", b">Kod grupy<"), {}, "header differs"),
        (_edited(b"liczba punkt", b"liczba z\xc5\x82ot"), {}, "no longer reports points"),
        (_edited(b">POZA PSZ<", b">SZPITAL IV STOPNIA<"), {}, "row is incomplete"),
        (_edited(b"?>", b"?><!DOCTYPE x>"), {}, "not readable"),
    ],
    ids=_case_id,
)  # fmt: skip
def test_drifted_or_unpinned_reports_fail_closed(
    content: bytes | None, changes: dict[str, object], message: str
) -> None:
    source = content if content is not None else EXCERPT.read_bytes()
    with pytest.raises(SourceError, match=message):
        _ = _parse(source, **changes)


def _row(content: bytes, within: bytes) -> bytes:
    at = content.index(within)
    start = content.rindex(b"<ss:Row>", 0, at)
    return content[start : content.index(b"</ss:Row>", at) + len(b"</ss:Row>")]


@pytest.mark.parametrize(
    ("within", "changes", "message"),
    [
        (b">A14<", {"group_rows": 37}, "repeats a group"),
        (b">Zabiegowy<", {"section_rows": 5}, "repeats a section row"),
    ],
)
def test_a_repeated_row_fails_closed(
    within: bytes, changes: dict[str, object], message: str
) -> None:
    content = EXCERPT.read_bytes()
    row = _row(content, within)
    with pytest.raises(SourceError, match=message):
        _ = _parse(content.replace(row, row + row, 1), **changes)


def test_one_sided_and_exponent_values_parse() -> None:
    # A count without points is still a figure; an exponent mean stay is read.
    content = _edited(b">411923.06<", b"><", within=_A22)
    content = content.replace(b">7.8971999999999998<", b">9.7199999999999995E-2<", 1)
    figures = _parse(content)
    a22 = next(item for item in figures.groups if item.code == "A22")
    assert (a22.month, a22.hospitalizations, a22.points_ten_thousandths) == (
        "2025-08",
        27,
        None,
    )
    assert figures.sections[0].mean_stay_hundredths == 10


def test_a_january_report_spans_the_year_boundary() -> None:
    content = EXCERPT.read_bytes()
    for old, new in (
        ("Sierpień 2026", "Styczeń 2027"),
        ("Sierpień 2025", "Styczeń 2026"),
        ("Lipiec 2026", "Grudzień 2026"),
    ):
        content = content.replace(old.encode(), new.encode())
    figures = _parse(
        content,
        month="2027-01",
        source_url=URL.replace("2026_8.xml", "2027_1.xml"),
        published_on=date(2027, 3, 1),
    )
    assert sorted({(item.month, item.age_months) for item in figures.groups}) == [
        ("2026-01", 12),
        ("2026-12", 1),
        ("2027-01", 0),
    ]


def test_manifest_binds_url_month_and_order() -> None:
    content = EXCERPT.read_bytes()
    with pytest.raises(ValidationError, match="another month"):
        _ = _pin(content, month="2026-07")
    with pytest.raises(ValidationError, match="must be published after its month"):
        _ = _pin(content, published_on=date(2026, 8, 31))
    extract = {
        "sha256": "e" * 64,
        "size_bytes": 1,
        "group_figures": 1,
        "section_figures": 1,
    }

    def monthly(**changes: object) -> MonthlyReport:
        pin = _pin(content, **changes).model_dump(mode="json")
        return MonthlyReport.model_validate_json(
            json.dumps({**pin, "extract": extract})
        )

    report = monthly()
    july = monthly(
        month="2026-07",
        source_url=URL.replace("_8.xml", "_7.xml"),
        published_on=date(2026, 10, 1),
    )
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
    with pytest.raises(ValidationError, match="unique and in month order"):
        _ = JgpStatistics.model_validate_json(manifest([report, july]))
    late_july = july.model_copy(update={"published_on": date(2026, 11, 1)})
    with pytest.raises(ValidationError, match="published in month order"):
        _ = JgpStatistics.model_validate_json(manifest([late_july, report]))
