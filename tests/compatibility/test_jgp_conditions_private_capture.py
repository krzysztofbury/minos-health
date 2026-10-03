"""Opt-in real-source acceptance. Source bytes never enter the repository."""

import json
import os
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_conditions import parse_conditions_workbook
from minos.ingest.reference_manifest import DatasetManifest

ROOT = Path(__file__).parents[2]


@pytest.mark.jgp_live
def test_reviewed_original_and_current_sources_keep_exact_cells_and_unsupported_gaps(
    tmp_path: Path,
) -> None:
    archive = os.environ.get("MINOS_JGP_CONDITIONS_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private attachment 9 archive opt-in is not configured")
    base = DatasetManifest.model_validate_json(
        (
            ROOT / "manifests/sources/reference-catalog-public-validation.json"
        ).read_bytes()
    )
    data = base.model_dump(mode="json")
    data.update(
        cast(
            dict[str, object],
            json.loads(
                (ROOT / "manifests/sources/jgp-conditions-reference.json").read_bytes()
            ),
        )
    )
    selected = DatasetManifest.model_validate_json(json.dumps(data))
    for manifest in selected.characteristics:
        with verified_source(
            Path(archive), manifest.workbook, staging_directory=tmp_path
        ) as source:
            sheets = parse_conditions_workbook(source, manifest)
        primary = sheets[0]
        assert primary.catalog == "1a"
        if manifest.baw_document_id != 43943:
            assert {g.code for g in primary.groups} >= {"N09", "N11"}
        assert manifest.valid_from == (
            date(2026, 5, 1) if manifest.baw_document_id == 43868 else date(2026, 7, 1)
        )
        if len(sheets) == 2:
            primary_general = next(
                li for li in primary.lists if li.label == "Lista Ogólna"
            )
            ae_general = next(
                li for li in sheets[1].lists if li.label == "Lista Ogólna"
            )
            difference = {m.code for m in primary_general.members} ^ {
                m.code for m in ae_general.members
            }
            assert difference == (
                {"J98.4", "J98.8"} if manifest.baw_document_id == 43943 else set()
            )
        if manifest.baw_document_id != 44042:
            continue
        assert len(primary.groups) == 629 and len(sheets[1].groups) == 121
        assert {
            n.group for n in primary.conditions if n.evidence_type == "title_qualifier"
        } == {"N03", "N03A", "N06A", "N06B", "PD01"}
        evidence = {e.row: e for e in primary.evidence}
        assert (
            evidence[19981].text
            == "(*1) brak możliwości wykazania łącznie z procedurą 39.951"
        )
        assert evidence[10986].category == "unbound_member"  # H84O has no ICD marker
        assert evidence[10986].text.startswith("04.499 ")
        assert any(
            n.group == "H84O"
            and n.reason == "list_definition_unbound"
            and 10986 in n.source_rows
            for n in primary.conditions
        )
        definitions = [li for li in primary.lists if li.label == "Lista Ogólna"]
        ae_definitions = [li for li in sheets[1].lists if li.label == "Lista Ogólna"]
        assert len(definitions) == len(ae_definitions) == 1
        assert {m.code for m in definitions[0].members} == {
            m.code for m in ae_definitions[0].members
        }
