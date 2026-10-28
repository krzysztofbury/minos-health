"""Build the pinned NFZ monthly JGP report figures into the bundle (#283)."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_statistics_extract import read_extract
from minos.ingest.jgp_statistics_manifest import JgpStatistics
from minos.store.artifact import canonical_json
from minos.store.query_budget import query_budget


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
