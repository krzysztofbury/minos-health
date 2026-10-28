import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from minos.domain.reference import ArtifactError
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.jgp_statistics import validate_jgp_statistics_binding
from minos.store.reference import read_artifact_manifest
from tests.integration.test_icd11_reference import icd11_bundle

EXCERPT = (
    Path(__file__).parents[1] / "fixtures/jgp_statistics/raport-2026-08-excerpt.xml"
)


def _statistics(root: Path) -> DatasetManifest:
    base = read_artifact_manifest(icd11_bundle(root)).dataset
    content = EXCERPT.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    _ = (root / "blobs/sha256" / digest).write_bytes(content)
    report = {
        "month": "2026-08",
        "resource_id": 2834329,
        "source_url": "https://api.dane.gov.pl/media/resources/20261020/raport_SZP_i_kardiologia_2026_8.xml",
        "published_on": "2026-10-20",
        "sha256": digest,
        "size_bytes": len(content),
        "fetched_at": "2026-10-28T12:00:00Z",
        "group_rows": 36,
        "section_rows": 4,
    }
    return DatasetManifest.model_validate_json(
        json.dumps(
            {
                **base.model_dump(mode="json"),
                "jgp_statistics": {
                    "release_id": "jgp-statistics-excerpt",
                    "dataset": "https://dane.gov.pl/pl/dataset/2581",
                    "publisher": "Narodowy Fundusz Zdrowia",
                    "license": "CC0 1.0",
                    "notices": ["Excerpt of the August 2026 report."],
                    "reports": [report],
                },
            }
        )
    )


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
    assert first.row_counts["jgp_statistics_report"] == 1
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
        groups, suppressed = cast(
            tuple[int, int],
            connection.execute(
                "SELECT count(*), sum(suppressed) FROM jgp_group_statistic"
            ).fetchone(),
        )
    finally:
        connection.close()
    # A14 outside PSZ: suppressed August and July 2026, nothing a year earlier.
    assert rows == [
        ("2026-07", "2026-08", 1, None, 1, 167696600),
        ("2026-08", "2026-08", 0, None, 1, 82419000),
    ]
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
