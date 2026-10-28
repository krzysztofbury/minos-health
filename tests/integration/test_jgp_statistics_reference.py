import hashlib
import io
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from minos.domain.reference import ArtifactError
from minos.ingest.jgp_statistics import parse_report
from minos.ingest.jgp_statistics_extract import write_extract
from minos.ingest.jgp_statistics_manifest import ReportPin
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.jgp_statistics import validate_jgp_statistics_binding
from minos.store.reference import read_artifact_manifest
from tests.integration.test_icd11_reference import icd11_bundle

FIXTURES = Path(__file__).parents[1] / "fixtures/jgp_statistics"
# Real excerpts of the July and August 2026 reports, with the same six groups.
REPORTS = (
    ("2026-07", 2724418, "20261001", "2026-10-01", 37),
    ("2026-08", 2834329, "20261020", "2026-10-20", 36),
)


def _report(root: Path, month: str, resource: int, folder: str, published: str,
            rows: int) -> dict[str, object]:  # fmt: skip
    content = (FIXTURES / f"raport-{month}-excerpt.xml").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    _ = (root / "blobs/sha256" / digest).write_bytes(content)
    number = int(month[5:])
    raw: dict[str, object] = {
        "month": month,
        "resource_id": resource,
        "source_url": f"https://api.dane.gov.pl/media/resources/{folder}/raport_SZP_i_kardiologia_{month[:4]}_{number}.xml",
        "published_on": published,
        "sha256": digest,
        "size_bytes": len(content),
        "fetched_at": "2026-10-28T12:00:00Z",
        "group_rows": rows,
        "section_rows": 4,
    }
    # The same raw-to-extract path as scripts/extract_jgp_statistics.py.
    pin = ReportPin.model_validate_json(json.dumps(raw))
    figures = parse_report(io.BytesIO(content), pin)
    extract = write_extract(figures, pin)
    extract_digest = hashlib.sha256(extract).hexdigest()
    _ = (root / "blobs/sha256" / extract_digest).write_bytes(extract)
    return {
        **raw,
        "extract": {
            "sha256": extract_digest,
            "size_bytes": len(extract),
            "group_figures": len(figures.groups),
            "section_figures": len(figures.sections),
        },
    }


def _dataset(root: Path, **changes: object) -> str:
    base = read_artifact_manifest(icd11_bundle(root)).dataset
    statistics = {
        "release_id": "jgp-statistics-excerpt",
        "dataset": "https://dane.gov.pl/pl/dataset/2581",
        "publisher": "Narodowy Fundusz Zdrowia",
        "license": "CC0 1.0",
        "notices": ["Excerpts of the July and August 2026 reports."],
        "reports": [_report(root, *report) for report in REPORTS],
    }
    return json.dumps(
        {**base.model_dump(mode="json"), "jgp_statistics": statistics, **changes}
    )


def _statistics(root: Path) -> DatasetManifest:
    return DatasetManifest.model_validate_json(_dataset(root))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    root = tmp_path_factory.mktemp("statistics")
    manifest = _statistics(root)
    first, second = root / "first", root / "second"
    _ = build_reference_artifact(manifest, root, first)
    _ = build_reference_artifact(manifest, root, second)
    return first, second


def test_statistics_build_schema_12_with_identical_bytes(
    built: tuple[Path, Path],
) -> None:
    first, second = (read_artifact_manifest(path) for path in built)
    assert first.schema_version == 12
    # Figures are integers parsed from decimal text, so two builds agree.
    assert first.database_sha256 == second.database_sha256
    assert first.row_counts["jgp_statistics_report"] == 2
    connection = sqlite3.connect(f"file:{built[0] / 'minos.db'}?mode=ro", uri=True)
    try:
        rows = cast(
            list[tuple[str, str, int, int | None, int, int | None]],
            connection.execute(
                """SELECT month, report_month, age_months, hospitalizations,
                suppressed, points_ten_thousandths FROM jgp_group_statistic
                WHERE code = 'A14' AND hospital_level = 'POZA PSZ' ORDER BY month"""
            ).fetchall(),
        )
        # A later report revises a month: both figures stay, keyed by report.
        revised = cast(
            list[tuple[str, int]],
            connection.execute(
                """SELECT report_month, hospitalizations FROM jgp_section_statistic
                WHERE month = '2026-07' AND admission = 'planned'
                AND character = 'procedural'
                AND section = 'A Choroby układu nerwowego' ORDER BY report_month"""
            ).fetchall(),
        )
        groups, suppressed = cast(
            tuple[int, int],
            connection.execute(
                "SELECT count(*), sum(suppressed) FROM jgp_group_statistic"
            ).fetchone(),
        )
    finally:
        connection.close()
    # A14 outside PSZ: July 2026 twice, as each report saw it, and August 2026.
    assert rows == [
        ("2026-07", "2026-07", 0, None, 1, 167696600),
        ("2026-07", "2026-08", 1, None, 1, 167696600),
        ("2026-08", "2026-08", 0, None, 1, 82419000),
    ]
    assert revised == [("2026-07", 784), ("2026-08", 900)]
    assert first.row_counts["jgp_group_statistic"] == groups
    assert 0 < suppressed < groups


def test_the_stored_release_must_be_the_manifest_release(
    built: tuple[Path, Path],
) -> None:
    statistics = read_artifact_manifest(built[0]).dataset.jgp_statistics
    assert statistics is not None
    connection = sqlite3.connect(f"file:{built[0] / 'minos.db'}?mode=ro", uri=True)
    try:
        validate_jgp_statistics_binding(connection, statistics)
        for other in (
            statistics.model_copy(update={"release_id": "jgp-statistics-other"}),
            None,
        ):
            with pytest.raises(ArtifactError, match="binding does not match"):
                validate_jgp_statistics_binding(connection, other)
    finally:
        connection.close()


def test_statistics_need_the_schema_11_dataset(tmp_path: Path) -> None:
    # Schemas are supersets: statistics extend a dataset with the ICD-11 maps.
    with pytest.raises(ValidationError, match="extend an ICD-11 map dataset"):
        _ = DatasetManifest.model_validate_json(_dataset(tmp_path, icd11_maps=None))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("month", "2020-01"),  # disagrees with its age at the report
        ("month", "2026-19"),
        ("hospital_level", "SZPITAL IV STOPNIA"),
        ("points_ten_thousandths", None),  # with no count either: no figure at all
    ],
)
def test_the_schema_refuses_rows_the_parser_never_writes(
    built: tuple[Path, Path], tmp_path: Path, column: str, value: object
) -> None:
    copy = tmp_path / "minos.db"
    _ = copy.write_bytes((built[0] / "minos.db").read_bytes())
    connection = sqlite3.connect(copy)
    try:
        row = cast(
            dict[str, object],
            dict(
                zip(
                    (
                        "release_id code hospital_level month report_month "
                        + "age_months section hospitalizations suppressed "
                        + "points_ten_thousandths"
                    ).split(),
                    cast(
                        tuple[object, ...],
                        connection.execute(
                            """SELECT * FROM jgp_group_statistic WHERE code = 'A22'
                            AND hospital_level = 'POZA PSZ' AND month = '2025-08'"""
                        ).fetchone(),
                    ),
                    strict=True,
                )
            ),
        )
        row |= {"code": "ZZ99", column: value}
        if column == "points_ten_thousandths":
            row["hospitalizations"] = None
        with pytest.raises(sqlite3.IntegrityError):
            _ = connection.execute(
                "INSERT INTO jgp_group_statistic VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                tuple(row.values()),
            )
    finally:
        connection.close()
