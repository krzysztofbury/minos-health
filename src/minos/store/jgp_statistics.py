"""Build the pinned NFZ monthly JGP report figures into the bundle (#283)."""

import json
import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_statistics import AGES, LEVELS, month_before
from minos.ingest.jgp_statistics_extract import read_extract
from minos.ingest.jgp_statistics_manifest import JgpStatistics
from minos.store.artifact import canonical_json
from minos.store.query_budget import query_budget

type Json = dict[str, object]
type GroupRow = tuple[str, str, str, int | None, int, int | None]


def import_jgp_statistics(
    connection: sqlite3.Connection,
    statistics: JgpStatistics,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("JGP statistics import requires a build transaction")
    release = statistics.release_id
    _ = connection.execute(
        "INSERT INTO jgp_statistics_release VALUES (?, ?)",
        (release, canonical_json(statistics).decode()),
    )
    for pin in statistics.reports:
        # The reviewed extract, not the 60 MB raw report (#283).
        with verified_source(archive, pin.extract, staging_directory=staging) as source:
            figures = read_extract(source, pin)
        _ = connection.execute(
            "INSERT INTO jgp_statistics_report VALUES (?, ?, ?, ?)",
            (release, pin.month, pin.published_on.isoformat(), pin.sha256),
        )
        _ = connection.executemany(
            "INSERT INTO jgp_group_statistic VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    release,
                    item.code,
                    item.hospital_level,
                    item.month,
                    pin.month,
                    item.age_months,
                    item.section,
                    None if item.hospitalizations == "<5" else item.hospitalizations,
                    int(item.hospitalizations == "<5"),
                    item.points_ten_thousandths,
                )
                for item in figures.groups
            ),
        )
        _ = connection.executemany(
            "INSERT INTO jgp_section_statistic VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (
                    release,
                    item.section,
                    item.admission,
                    item.character,
                    item.month,
                    pin.month,
                    item.age_months,
                    None if item.hospitalizations == "<5" else item.hospitalizations,
                    int(item.hospitalizations == "<5"),
                    item.mean_stay_hundredths,
                    item.mean_age_tenths,
                )
                for item in figures.sections
            ),
        )


def validate_jgp_statistics_binding(
    connection: sqlite3.Connection, statistics: JgpStatistics | None
) -> None:
    """The stored release must be exactly the manifest's reviewed pins."""
    with query_budget(connection):
        stored = cast(
            list[tuple[str, str]],
            connection.execute(
                "SELECT id, manifest_json FROM jgp_statistics_release LIMIT 2"
            ).fetchall(),
        )
    expected = (
        [(statistics.release_id, canonical_json(statistics).decode())]
        if statistics is not None
        else []
    )
    if stored != expected:
        raise ArtifactError("JGP statistics binding does not match the manifest")


# A figure this many months older than its report is taken as settled; later
# reports revise younger months as late settlements arrive (#283).
MATURE_MONTHS = 12
# At most 36 pinned reports, each with three months for eight hospital levels.
_MAX_ROWS = 36 * len(AGES) * len(LEVELS)


def _months_between(earlier: str, later: str) -> int:
    def index(month: str) -> int:
        return int(month[:4]) * 12 + int(month[5:])

    return index(later) - index(earlier)


def _hospitalizations(rows: list[GroupRow]) -> dict[str, int] | None:
    # A suppressed cell is one to four hospitalizations. A cell with no count and
    # no marker leaves the total unknown; zero would read as none settled.
    if any(row[3] is None and not row[4] for row in rows):
        return None
    exact = sum(row[3] or 0 for row in rows)
    suppressed = sum(row[4] for row in rows)
    return {"min": exact + suppressed, "max": exact + 4 * suppressed}


def _points(rows: list[GroupRow]) -> int | None:
    # No rows is a real zero: the report covers the month and lists no settlement.
    points = [row[5] for row in rows]
    return None if None in points else sum(item or 0 for item in points)


def group_statistics(connection: sqlite3.Connection, code: str) -> Json | None:
    """Monthly NFZ settlement figures of one group, or None before schema 12.

    A report covers its month, the previous month and the same month a year
    earlier, and lists a group only where it was settled. So every covered
    month comes from the newest report that covers it, and a group absent
    from that report had no settlements in that month: zero, not an older
    figure. The figures do not depend on a service date.
    """
    with query_budget(connection):
        version = cast(tuple[int], connection.execute("PRAGMA user_version").fetchone())
        if version[0] < 12:
            return None
        release = cast(
            tuple[str, str] | None,
            connection.execute(
                "SELECT id, manifest_json FROM jgp_statistics_release LIMIT 1"
            ).fetchone(),
        )
        if release is None:
            return None
        reports = cast(
            list[tuple[str, str]],
            connection.execute(
                """SELECT report_month, published_on FROM jgp_statistics_report
                WHERE release_id = ? ORDER BY report_month LIMIT 37""",
                (release[0],),
            ).fetchall(),
        )
        rows = cast(
            list[GroupRow],
            connection.execute(
                """SELECT month, report_month, hospital_level, hospitalizations,
                suppressed, points_ten_thousandths FROM jgp_group_statistic
                WHERE release_id = ? AND code = ? LIMIT ?""",
                (release[0], code, _MAX_ROWS + 1),
            ).fetchall(),
        )
    if len(rows) > _MAX_ROWS or len(reports) > 36:
        raise ArtifactError("JGP statistics rows exceed their bound")
    published = dict(reports)
    # The newest report that covers each month, whatever the group's rows say.
    covering: dict[str, str] = {}
    for report_month in published:
        for age in AGES:
            month = month_before(report_month, age)
            covering[month] = max(covering.get(month, report_month), report_month)
    chosen = [row for row in rows if covering.get(row[0]) == row[1]]
    months: list[Json] = []
    if rows:
        for month in sorted(covering):
            cells = [row for row in chosen if row[0] == month]
            report_month = covering[month]
            months.append(
                {
                    "month": month,
                    "report_month": report_month,
                    "published_on": published[report_month],
                    "provisional": _months_between(month, report_month) < MATURE_MONTHS,
                    "hospitalizations": _hospitalizations(cells),
                    "points_ten_thousandths": _points(cells),
                }
            )
    mature = [item for item in months if not item["provisional"]][-12:]
    window = {cast(str, item["month"]) for item in mature}
    settled = [row for row in chosen if row[0] in window]
    levels: dict[str, list[GroupRow]] = {}
    for row in settled:
        levels.setdefault(row[2], []).append(row)
    manifest = cast(Json, json.loads(release[1]))
    return {
        "source": {
            "dataset": manifest["dataset"],
            "publisher": manifest["publisher"],
            "license": manifest["license"],
            "release_id": release[0],
        },
        "months": months,
        "mature": {
            "from": mature[0]["month"] if mature else None,
            "to": mature[-1]["month"] if mature else None,
            "months": len(mature),
            "hospitalizations": _hospitalizations(settled),
            "points_ten_thousandths": _points(settled),
            "levels": [
                {
                    "hospital_level": level,
                    "hospitalizations": _hospitalizations(rows),
                    "points_ten_thousandths": _points(rows),
                }
                for level, rows in sorted(levels.items())
            ],
        },
    }
