import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import cast

import pytest

from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json
from minos.store.reference import open_reference_bundle
from tests.fixtures.jgp_catalog import catalog_workbook, default_sheets, stage_catalog
from tests.integration.test_komunikat_reference import rank_manifest


def catalog_manifest(root: Path) -> DatasetManifest:
    base = rank_manifest(root)
    catalog = stage_catalog(root, catalog_workbook(default_sheets()))
    data = base.model_dump(mode="json")
    data["catalogs"] = [catalog.model_dump(mode="json")]
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_catalog_build_stores_rows_values_and_scopes_beside_ranks(
    tmp_path: Path,
) -> None:
    manifest = catalog_manifest(tmp_path)
    built = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    assert built.schema_version == 4
    assert built.row_counts["procedure_rank"] == 4  # schema 3 content kept
    assert built.row_counts["group_definition"] == 7
    assert built.row_counts["catalog_scope"] == 136
    with open_reference_bundle(tmp_path / "bundle") as (connection, _):
        values = connection.execute(
            """SELECT value.measure, value.unit, value.amount
            FROM group_base_value AS value JOIN group_definition AS grouped
              USING (release_id, catalog, source_row)
            WHERE grouped.code = 'PZD02' ORDER BY value.source_column"""
        ).fetchall()
        assert values == [
            ("hospitalization", "points_hundredths", 1399100),
            ("planned_hospitalization", "points_hundredths", 1287172),
            ("financed_stay", "days", 21),
        ]
        assert connection.execute(
            """SELECT count(*) FROM group_base_value JOIN group_definition
              USING (release_id, catalog, source_row)
            WHERE code = 'A01' AND measure = 'planned_hospitalization'"""
        ).fetchone() == (0,)  # empty source cell stays absent
        repeated = connection.execute(
            """SELECT grouped.source_row, group_concat(scope.label, '|')
            FROM group_definition AS grouped
            JOIN group_scope AS mark USING (release_id, catalog, source_row)
            JOIN catalog_scope AS scope
              ON scope.release_id = mark.release_id AND scope.catalog = mark.catalog
             AND scope.position = mark.position
            WHERE grouped.code = 'H21' GROUP BY grouped.source_row
            ORDER BY grouped.source_row"""
        ).fetchall()
        assert repeated == [
            (9, "chirurgia ogólna|ortopedia i traumat narz ruchu/ortopedia i traumat narz ruchu dla dzieci"),
            (10, "ortopedia i traumat narz ruchu/ortopedia i traumat narz ruchu dla dzieci"),
        ]  # fmt: skip
    again = build_reference_artifact(manifest, tmp_path, tmp_path / "again")
    assert again.database_sha256 == built.database_sha256


def test_catalog_manifest_and_database_correspondence_fail_closed(
    tmp_path: Path,
) -> None:
    manifest = catalog_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    path = bundle / "manifest.json"
    value = cast(
        dict[str, dict[str, list[dict[str, object]]]], json.loads(path.read_bytes())
    )
    value["dataset"]["catalogs"][0]["act_date"] = "2026-07-22"
    _ = path.write_bytes(canonical_json(cast(dict[str, object], cast(object, value))))
    with pytest.raises(ArtifactError, match="catalog source policy"):
        with open_reference_bundle(bundle):
            pass


def test_corrupt_catalog_leaves_no_publishable_bundle(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    digest = manifest.catalogs[0].workbook.sha256
    _ = (tmp_path / "blobs/sha256" / digest).write_bytes(b"corrupt")
    with pytest.raises(SourceError, match="byte count"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_datasets_refuse_duplicate_catalog_documents(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    data = cast(dict[str, list[dict[str, object]]], manifest.model_dump(mode="json"))
    second = dict(data["catalogs"][0])
    second["release_id"] = "jgp-catalog-second"
    data["catalogs"].append(second)
    with pytest.raises(ValueError, match="pinned twice"):
        _ = DatasetManifest.model_validate_json(json.dumps(data))


NO_POINTS = "DELETE FROM group_base_value WHERE catalog = '1a' AND source_row = 7"
NO_SCOPE = "DELETE FROM group_scope; DELETE FROM catalog_scope WHERE position = 68"
OTHER_PRODUCT = (
    "UPDATE group_definition SET product_code = '5.51.01.0009999' WHERE source_row = 10"
)
NEW_REPEAT = "UPDATE group_definition SET code = 'A01' WHERE code = 'J01AJ'"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (NO_POINTS, "has no base points"),
        (NO_SCOPE, "68 scope columns"),
        (OTHER_PRODUCT, "several product codes"),
        # A new repeated code breaks the reviewed count before the mapping.
        (NEW_REPEAT, "do not match the reviewed manifest"),
    ],
)
def test_open_time_invariants_mirror_parser_rules(
    tmp_path: Path, tamper: str, message: str
) -> None:
    from minos.store.jgp_catalog_build import validate_catalog_bindings

    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    copy = tmp_path / "tampered.db"
    _ = copy.write_bytes((tmp_path / "bundle/minos.db").read_bytes())
    with closing(sqlite3.connect(copy)) as connection:
        validate_catalog_bindings(connection, manifest.catalogs)  # untouched passes
        _ = connection.executescript(tamper)
        with pytest.raises(ArtifactError, match=message):
            validate_catalog_bindings(connection, manifest.catalogs)


def test_schema_ties_each_measure_to_its_unit(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    copy = tmp_path / "tampered.db"
    _ = copy.write_bytes((tmp_path / "bundle/minos.db").read_bytes())
    with closing(sqlite3.connect(copy)) as connection:
        for statement in (
            "UPDATE group_base_value SET unit = 'points_hundredths' WHERE measure = 'financed_stay'",
            "UPDATE group_base_value SET unit = 'days' WHERE measure = 'hospitalization'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _ = connection.execute(statement)
