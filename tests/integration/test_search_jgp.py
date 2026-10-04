from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.search import SearchRequest, prepare_query
from minos.http.app import create_app
from minos.ingest.jgp_conditions_manifest import ConditionsExpectation
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from minos.store.search import search_reference
from minos.store.search_jgp import shared_procedures
from tests.fixtures.jgp_conditions import manifest as source_manifest
from tests.fixtures.jgp_conditions import workbook
from tests.integration.test_jgp_condition_consumers import consumer_bundle


@pytest.fixture
def ranked_bundle(tmp_path: Path) -> Path:
    old = consumer_bundle(tmp_path)
    dataset = read_artifact_manifest(old).dataset

    def edit(sheet: object) -> None:
        from openpyxl.worksheet.worksheet import Worksheet

        assert isinstance(sheet, Worksheet)
        if sheet.title == "Charakterystyka 1a":
            sheet["C7"] = "00.00001 Synthetic known procedure"

    content = workbook(edit=edit)
    source = source_manifest(content)
    expected = source.sheets["1a"].model_dump()
    expected["unmatched_codes"] = 2
    source = source.model_copy(
        update={
            "catalog_release_id": dataset.catalogs[0].release_id,
            "sheets": {
                **source.sheets,
                "1a": ConditionsExpectation.model_validate(expected),
            },
        }
    )
    _ = (tmp_path / "blobs/sha256" / source.workbook.sha256).write_bytes(content)
    manifest = dataset.model_copy(
        update={"search_profile": "polish-folded-1", "characteristics": (source,)}
    )
    bundle = tmp_path / "ranked"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    return bundle


def test_search_counts_are_dated_exact_membership_not_classification_families(
    ranked_bundle: Path,
) -> None:
    with open_reference_bundle(ranked_bundle) as (connection, _):
        result = search_reference(
            connection,
            SearchRequest(
                "icd10-pl", prepare_query("wariant"), service_date=date(2026, 7, 1)
            ),
        )
        by_code = {hit.code: hit for hit in result.hits}
        assert by_code["E11.0"].jgp_group_count == 1
        assert by_code["E11.0"].jgp_service_date == date(2026, 7, 1)
        assert by_code["E11.2"].jgp_group_count == 0
        early = search_reference(
            connection,
            SearchRequest(
                "icd10-pl", prepare_query("wariant"), service_date=date(2026, 1, 1)
            ),
        )
        assert all(
            hit.jgp_group_count == 0 and hit.jgp_service_date is None
            for hit in early.hits
        )
        assert (
            shared_procedures(
                connection, "E11", date(2026, 7, 1), "icd10-synthetic-v1"
            )[0]["procedures"]
            == []
        )
        answers = shared_procedures(
            connection, "E11.0", date(2026, 7, 1), "icd10-synthetic-v1"
        )
        procedures = cast(list[dict[str, object]], answers[0]["procedures"])
        assert [entry["code"] for entry in procedures] == ["00.00001"]
        assert procedures[0]["shared_groups"] == ["A01"]
        assert (
            shared_procedures(
                connection, "E11.0", date(2026, 7, 1), "different-release"
            )
            == []
        )


def test_shared_procedure_page_has_dated_links_and_reference_only_caveat(
    ranked_bundle: Path,
) -> None:
    with TestClient(create_app(ranked_bundle)) as client:
        page = client.get("/slowniki/icd-10/E11.0/jgp/2026-07-01")
        assert page.status_code == 200
        assert "Procedury współdzielące jawne grupy JGP" in page.text
        assert "nie typowe leczenie ani kwalifikacja hospitalizacji" in page.text
        assert "/slowniki/icd-9/00.00001/jgp/2026-07-01" in page.text
        assert "/jgp/2026-07-01/A01" in page.text
