import hashlib
import io
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest

from minos.domain.reference import SourceError
from minos.ingest.jgp_statistics import parse_report
from minos.ingest.jgp_statistics_extract import read_extract, write_extract
from minos.ingest.jgp_statistics_manifest import MonthlyReport, ReportPin

EXCERPT = (
    Path(__file__).parents[1] / "fixtures/jgp_statistics/raport-2026-08-excerpt.xml"
)


def _raw() -> tuple[ReportPin, bytes]:
    content = EXCERPT.read_bytes()
    pin = ReportPin.model_validate(
        {
            "month": "2026-08",
            "resource_id": 2834329,
            "source_url": "https://api.dane.gov.pl/media/resources/20261020/raport_SZP_i_kardiologia_2026_8.xml",
            "published_on": date(2026, 10, 20),
            "sha256": hashlib.sha256(content).hexdigest(),
            "size_bytes": len(content),
            "fetched_at": datetime(2026, 10, 28, tzinfo=UTC),
            "group_rows": 36,
            "section_rows": 4,
        }
    )
    return pin, content


def _pinned(pin: ReportPin, extract: bytes, **counts: int) -> MonthlyReport:
    figures = cast(dict[str, list[object]], json.loads(extract))
    return MonthlyReport.model_validate_json(
        json.dumps(
            {
                **pin.model_dump(mode="json"),
                "extract": {
                    "sha256": hashlib.sha256(extract).hexdigest(),
                    "size_bytes": len(extract),
                    "group_figures": len(figures["groups"]),
                    "section_figures": len(figures["sections"]),
                    **counts,
                },
            }
        )
    )


def test_an_extract_round_trips_to_the_parsed_figures() -> None:
    pin, content = _raw()
    figures = parse_report(io.BytesIO(content), pin)
    extract = write_extract(figures, pin)
    # One report has one extract: canonical bytes, whatever the source order.
    reversed_figures = type(figures)(
        tuple(reversed(figures.groups)), tuple(reversed(figures.sections))
    )
    assert write_extract(reversed_figures, pin) == extract
    read = read_extract(io.BytesIO(extract), _pinned(pin, extract))
    assert set(read.groups) == set(figures.groups)
    assert set(read.sections) == set(figures.sections)
    assert len(read.groups) == len(figures.groups)


def _tampered(change: str) -> tuple[MonthlyReport, bytes]:
    pin, content = _raw()
    extract = write_extract(parse_report(io.BytesIO(content), pin), pin)
    document = cast(dict[str, object], json.loads(extract))
    groups = cast(list[dict[str, object]], document["groups"])
    if change == "report":
        document["report_sha256"] = "0" * 64
    elif change == "age":
        groups[0]["age_months"] = 12 if groups[0]["age_months"] != 12 else 0
    elif change == "order":
        groups[0], groups[1] = groups[1], groups[0]
    elif change == "level":
        # The last row keeps the sort order, so only the level check can fail.
        groups[-1]["hospital_level"] = "ZZZ"
    elif change == "float":
        groups[0]["points_ten_thousandths"] = 1.5
    tampered = json.dumps(document).encode()
    return _pinned(pin, tampered), tampered


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("report", "names another report"),
        ("age", "month and age disagree"),
        ("order", "not unique and sorted"),
        ("level", "unknown hospital level"),
        ("float", "extract is invalid"),
    ],
)
def test_a_tampered_extract_is_refused(change: str, message: str) -> None:
    pin, extract = _tampered(change)
    with pytest.raises(SourceError, match=message):
        _ = read_extract(io.BytesIO(extract), pin)


def test_an_extract_must_match_its_reviewed_counts() -> None:
    pin, content = _raw()
    extract = write_extract(parse_report(io.BytesIO(content), pin), pin)
    with pytest.raises(SourceError, match="reviewed counts"):
        _ = read_extract(io.BytesIO(extract), _pinned(pin, extract, group_figures=1))
