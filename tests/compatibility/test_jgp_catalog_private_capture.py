import os
from pathlib import Path

import pytest

from minos.domain.jgp_catalog import CatalogGroup, CatalogSheet
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_catalog import parse_catalog_workbook
from minos.ingest.reference_manifest import DatasetManifest

ROOT = Path(__file__).parents[2]
MANIFEST = ROOT / "manifests/sources/reference-catalog-public-validation.json"

# Read by hand from the pinned XLSX cells (values as printed, points before
# conversion to hundredths) on 2026-10-02.
SPOT_CHECKS = {
    ("jgp-catalog-46-2026-after-102-32dc947fc6ac", "1a", 7): (
        "A01",
        "5.51.01.0001001",
        {"hospitalization": 1358600, "financed_stay": 25, "extra_stay_day": 64100},
        {15: 1, 50: 1, 58: 1, 59: 1},
    ),
    ("jgp-catalog-46-2026-after-102-32dc947fc6ac", "1a", 535): (
        "PZD02",
        "5.51.01.0018028",
        {
            "hospitalization": 1399100,
            "planned_hospitalization": 1287172,
            "financed_stay": 21,
            "extra_stay_day": 91100,
        },
        {9: 1, 10: 2, 16: 3, 42: 2, 58: 2},
    ),
    ("jgp-catalog-46-2026-after-102-32dc947fc6ac", "1a", 258): (
        "H21",
        "5.51.01.0008021",
        {
            "hospitalization": 397400,
            "planned_hospitalization": 365600,
            "one_day_treatment": 335700,
            "same_day_discharge": 365700,
            "one_day_stay": 376300,
            "two_day_stay": 386800,
        },
        {18: 1, 58: 1},
    ),
    ("jgp-catalog-46-2026-after-102-32dc947fc6ac", "1ae", 7): (
        "AU35C",
        "5.51.02.0001001",
        {
            "hospitalization": 352600,
            "day_stay_under_12_hours": 352600,
            "financed_stay_excluding_day_stay": 19,
            "extra_stay_day_excluding_day_stay": 55900,
        },
        {25: 1, 37: 1, 51: 3, 53: 1},
    ),
    ("jgp-catalog-46-2026-original-3d4197ceb459", "1a", 389): (
        "K40",
        "5.51.01.0010040",
        {
            "hospitalization": 353400,
            "planned_hospitalization": 325100,
            "financed_stay": 20,
            "same_day_discharge": 176800,
            "one_day_stay": 235700,
            "two_day_stay": 294500,
            "extra_stay_day": 30700,
        },
        {22: 3, 25: 1, 30: 3, 32: 1, 33: 3, 34: 1, 37: 1, 60: 1},
    ),
}


@pytest.mark.jgp_live
def test_pinned_catalog_workbooks_parse_and_match_hand_checked_groups(
    tmp_path: Path,
) -> None:
    archive = os.environ.get("MINOS_JGP_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private JGP catalog archive opt-in is not configured")
    manifest = DatasetManifest.model_validate_json(MANIFEST.read_bytes())
    rows: dict[tuple[str, str, int], CatalogGroup] = {}
    for catalog in manifest.catalogs:
        with verified_source(
            Path(archive), catalog.workbook, staging_directory=tmp_path
        ) as snapshot:
            sheets: tuple[CatalogSheet, ...] = parse_catalog_workbook(snapshot, catalog)
        assert {sheet.catalog for sheet in sheets} == set(catalog.sheets)
        for sheet in sheets:
            for group in sheet.groups:
                rows[(catalog.release_id, sheet.catalog, group.row)] = group
    for key, (code, product, values, scopes) in SPOT_CHECKS.items():
        group = rows[key]
        assert (group.code, group.product_code) == (code, product)
        assert {value.measure: value.amount for value in group.values} == values
        assert {mark.position: mark.marker for mark in group.scopes} == scopes
    # The 2027 twin of H21 drops chirurgia ogólna (scope 15) and nothing else.
    twin = rows[("jgp-catalog-46-2026-after-102-32dc947fc6ac", "1a", 257)]
    assert {mark.position for mark in twin.scopes} == {15, 18, 58}
    assert "31.12.2026" in (twin.notes or "")
    # PZD02's planned-hospitalization base points change with 74/2026.
    planned = {
        key[0]: {value.measure: value.amount for value in group.values}.get(
            "planned_hospitalization"
        )
        for key, group in rows.items()
        if group.code == "PZD02"
    }
    assert planned == {
        "jgp-catalog-46-2026-original-3d4197ceb459": 804000,
        "jgp-catalog-46-2026-after-74-fbe73d5fb7a3": 1287172,
        "jgp-catalog-46-2026-after-102-32dc947fc6ac": 1287172,
    }
    # Every 2026/2027 twin pair after 102/2026 shares its base points and
    # differs only in scopes and notes.
    after_102 = [
        group
        for key, group in rows.items()
        if key[0] == "jgp-catalog-46-2026-after-102-32dc947fc6ac"
    ]
    codes = {group.code for group in after_102}
    twins = [
        [group for group in after_102 if group.code == code]
        for code in sorted(codes)
        if sum(group.code == code for group in after_102) == 2
    ]
    assert len(twins) == 25
    for first, second in twins:
        assert first.values == second.values
        assert first.notes != second.notes
