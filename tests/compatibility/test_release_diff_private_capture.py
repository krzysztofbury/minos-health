"""Pinned-source gold counts without committing licensed source rows or titles."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from minos.domain.release_diff import compare_facts
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_conditions import parse_conditions_workbook
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.diff_conditions import project_sheet
from minos.store.diff_records import Projection
from minos.store.query_budget import QueryBudget
from scripts.compose_conditions_manifest import Selection

ROOT = Path(__file__).parents[2]


@pytest.mark.icd9_live
def test_pinned_czs_589_to_590_logical_diff_golden(tmp_path: Path) -> None:
    archive = os.environ.get("MINOS_RELEASE_DIFF_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private paired ICD-9 archive opt-in is not configured")
    old = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources/icd9-czs-previous-reviewed.json").read_bytes()
    )
    new = DatasetManifest.model_validate_json(
        (ROOT / "manifests/sources/icd9-czs-reviewed.json").read_bytes()
    )
    paired = old.model_copy(update={"sources": (*old.sources, *new.sources)})
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(paired, Path(archive), bundle)
    command = [
        sys.executable,
        "-m",
        "minos",
        "data",
        "diff",
        str(bundle),
        str(bundle),
        "--pair",
        f"terminology:{old.sources[0].release_id}:{new.sources[0].release_id}",
        "--json",
        "--full",
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False, timeout=30
    )
    assert result.returncode == 0, (
        "pinned comparison must succeed without echoing private source output"
    )
    answer = cast(dict[str, object], json.loads(result.stdout))
    summary = cast(dict[str, object], answer["summary"])
    assert summary["concept"] == {"added": 1, "changed": 12, "removed": 0}
    assert answer["details_truncated"] is False
    changes = cast(list[dict[str, object]], answer["changes"])
    assert sum(change["category"] == "concept" for change in changes) == 13
    repeat = subprocess.run(
        command, capture_output=True, text=True, check=True, timeout=30
    )
    assert repeat.stdout == result.stdout
    assert str(tmp_path) not in result.stdout + result.stderr


@pytest.mark.jgp_live
def test_pinned_conditions_74_to_102_keeps_real_graph_and_list_gold(
    tmp_path: Path,
) -> None:
    archive = os.environ.get("MINOS_JGP_CONDITIONS_PRIVATE_ARCHIVE")
    if not archive:
        pytest.skip("private attachment 9 archive opt-in is not configured")
    selected = Selection.model_validate_json(
        (ROOT / "manifests/sources/jgp-conditions-reference.json").read_bytes()
    )
    projections: list[Projection] = []
    for source in selected.characteristics[1:]:
        output = Projection(QueryBudget(seconds=120))
        with verified_source(
            Path(archive), source.workbook, staging_directory=tmp_path
        ) as file:
            sheets = parse_conditions_workbook(file, source)
        for sheet in sheets:
            project_sheet(sheet, output)
        projections.append(output)
    changes = compare_facts(
        "conditions:after-74:after-102", projections[0].facts(), projections[1].facts()
    )
    graphs = [change for change in changes if change.category == "condition_graph"]
    assert len(graphs) == 15
    assert sum(change.action == "added" for change in graphs) == 2
    assert sum(change.action == "changed" for change in graphs) == 13
    assert all(change.identity[0] == "1a" for change in graphs)
    lists = [change for change in changes if change.category == "directional_list"]
    assert {
        action: sum(change.action == action for change in lists)
        for action in ("added", "changed", "removed")
    } == {"added": 9, "changed": 15, "removed": 4}
    assert any(change.category == "condition_evidence_binding" for change in changes)
