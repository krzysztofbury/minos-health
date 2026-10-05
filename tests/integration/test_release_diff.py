# Raw JSON assertions intentionally do not reuse the production serializer/model.
# pyright: reportAny=false

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from minos.cli import release_diff as output_module
from minos.cli.reference import execute_reference
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store import diff_records
from minos.store import release_diff as store_diff
from tests.fixtures.icd9_xml import XML, source_manifest
from tests.fixtures.jgp_catalog import (
    catalog_workbook,
    default_sheets,
)
from tests.fixtures.jgp_catalog import (
    stage_catalog as stage_jgp,
)
from tests.integration.test_canonical_reference import canonical_manifest
from tests.integration.test_icd9_reference import stage_catalog
from tests.integration.test_jgp_conditions_reference import conditions_manifest
from tests.integration.test_reference_artifact import FIXTURES, stage_source


def run_diff(old: Path, new: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minos", "data", "diff", str(old), str(new), *arguments],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_cli_compares_explicit_releases_and_reports_selected_scope(
    tmp_path: Path,
) -> None:
    manifest = stage_catalog(tmp_path)
    old = tmp_path / "old"
    new = tmp_path / "new"
    _ = build_reference_artifact(manifest, tmp_path, old)
    _ = build_reference_artifact(manifest, tmp_path, new)
    pair = "terminology:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1"
    result = run_diff(old, new, "--pair", pair, "--json", "--full")
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert answer["version"] == 1 and answer["status"] == "ok"
    assert answer["summary"] == {}
    assert answer["changes"] == []
    assert answer["coverage"]["complete_candidate_audit"] is False
    assert answer["coverage"]["selected_pairs"] == [pair]
    assert answer["details_truncated"] is False
    assert str(tmp_path) not in result.stdout + result.stderr
    assert (
        run_diff(old, new, "--pair", pair, "--json", "--full").stdout == result.stdout
    )


@pytest.mark.parametrize(
    "arguments",
    [
        (),
        ("--pair", "terminology:missing:icd9-czs-synthetic-v1"),
        ("--pair", "terminology:icd10-synthetic-v1:icd9-czs-synthetic-v1"),
        ("--pair", "catalog:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1"),
        (
            "--pair",
            "terminology:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1",
            "--pair",
            "terminology:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1",
        ),
    ],
)
def test_bad_or_ambiguous_pairs_never_print_success(
    tmp_path: Path, arguments: tuple[str, ...]
) -> None:
    manifest = stage_catalog(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    result = run_diff(bundle, bundle, *arguments, "--json")
    assert result.returncode == 2
    assert result.stdout == ""
    assert "invalid" in result.stderr.lower()
    assert str(tmp_path) not in result.stderr


def test_bare_or_tampered_database_is_not_a_comparison_input(tmp_path: Path) -> None:
    manifest = stage_catalog(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    pair = "terminology:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1"
    bare = run_diff(bundle / "minos.db", bundle, "--pair", pair, "--json")
    assert bare.returncode == 3 and bare.stdout == ""
    with (bundle / "minos.db").open("ab") as file:
        _ = file.write(b"tampered")
    broken = run_diff(bundle, bundle, "--pair", pair, "--json")
    assert broken.returncode == 3 and broken.stdout == ""
    assert str(tmp_path) not in broken.stderr


def test_original_and_amended_conditions_ignore_physical_row_shifts(
    tmp_path: Path,
) -> None:
    old_manifest = conditions_manifest(tmp_path, original=True)
    new_manifest = conditions_manifest(tmp_path)
    old, new = tmp_path / "old", tmp_path / "new"
    _ = build_reference_artifact(old_manifest, tmp_path, old)
    _ = build_reference_artifact(new_manifest, tmp_path, new)
    result = run_diff(
        old,
        new,
        "--pair",
        "conditions:conditions-original:conditions-current",
        "--json",
        "--full",
    )
    assert result.returncode == 0, result.stderr
    changes = json.loads(result.stdout)["changes"]
    # The 1a sheet is logically identical; 1ae is new and must not merge into it.
    content = [
        change
        for change in changes
        if change["category"]
        in ("condition_graph", "directional_list", "condition_evidence")
    ]
    assert content
    assert all(change["identity"][0] == "1ae" for change in content)
    assert all(change["action"] == "added" for change in content)


def test_czs_parser_boundary_reports_add_remove_label_status_and_dates(
    tmp_path: Path,
) -> None:
    before = stage_catalog(tmp_path)
    old, new = tmp_path / "old", tmp_path / "new"
    _ = build_reference_artifact(before, tmp_path, old)
    content = (
        XML.replace(b"  Badanie moczu  ", b"Changed procedure title")
        .replace(b'status="N"', b'status="A"')
        .replace(b"2008-07-01", b"2009-01-01")
        .replace(
            b'<pozycja_slownika kod="AA" nazwa="Synthetic parent" status="A"/>',
            b'<pozycja_slownika kod="B03" nazwa="New synthetic concept" status="A"/>',
        )
    )
    source = source_manifest(content).model_copy(update={"release_id": "icd9-next"})
    _ = (tmp_path / "blobs" / "sha256" / source.sha256).write_bytes(content)
    after = before.model_copy(update={"sources": (before.sources[0], source)})
    _ = build_reference_artifact(after, tmp_path, new)
    result = run_diff(
        old,
        new,
        "--pair",
        "terminology:icd9-czs-synthetic-v1:icd9-next",
        "--json",
        "--full",
    )
    assert result.returncode == 0, result.stderr
    concepts = {
        change["identity"][0]: change
        for change in json.loads(result.stdout)["changes"]
        if change["category"] == "concept"
    }
    assert set(concepts) == {"AA", "A01", "B03"}
    assert concepts["AA"]["action"] == "removed"
    assert concepts["B03"]["action"] == "added"
    assert concepts["A01"]["after"]["text"] == "Changed procedure title"
    assert dict(concepts["A01"]["before"]["attributes"])["status"] == "N"
    assert dict(concepts["A01"]["after"]["attributes"])["status"] == "A"
    assert dict(concepts["A01"]["after"]["attributes"])["data_od"] == "2009-01-01"


def test_catalog_preserves_twins_absent_cells_exact_deltas_and_applicability(
    tmp_path: Path,
) -> None:
    base = canonical_manifest(tmp_path)
    sheets = default_sheets()
    original = stage_jgp(tmp_path, catalog_workbook(sheets))
    old_manifest = base.model_copy(
        update={
            "catalogs": (original,),
            "catalog_knowledge_as_of": original.published_on,
            "catalog_knowledge_evidence": "Synthetic independent review",
        }
    )
    old, new = tmp_path / "old", tmp_path / "new"
    _ = build_reference_artifact(old_manifest, tmp_path, old)
    first = sheets["nfz-1a-74-2026"][0]
    sheets["nfz-1a-74-2026"] = (
        replace(first, values={**first.values, "E": 13586.25}),
        *sheets["nfz-1a-74-2026"][1:],
    )
    changed = stage_jgp(tmp_path, catalog_workbook(sheets), sheets=sheets)
    windows = tuple(
        window.model_copy(
            update={"service_to": original.published_on.replace(month=12, day=30)}
        )
        if window.service_to is not None
        else window.model_copy(
            update={"service_from": original.published_on.replace(month=12, day=31)}
        )
        for window in changed.row_applicability
    )
    changed = changed.model_copy(update={"row_applicability": windows})
    after = old_manifest.model_copy(update={"catalogs": (changed,)})
    _ = build_reference_artifact(after, tmp_path, new)
    result = run_diff(
        old,
        new,
        "--pair",
        "catalog:jgp-catalog-synthetic:jgp-catalog-synthetic",
        "--json",
        "--full",
    )
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    values = next(
        change for change in answer["changes"] if change["category"] == "base_values"
    )
    assert values["identity"] == ["1a", "A01"]
    assert values["exact_deltas"] == {
        "status": "comparable_single_rows",
        "measures": [
            {"measure": "hospitalization", "unit": "points_hundredths", "delta": 25}
        ],
    }
    assert "planned_hospitalization" not in str(values["after"])
    twins = next(
        change
        for change in answer["changes"]
        if change["category"] == "catalog_applicability"
        and change["identity"] == ["1a", "H21"]
    )
    assert len(twins["before"]) == len(twins["after"]) == 2
    assert any(window["service_to"] == "2026-12-31" for window in twins["before"])
    assert any(window["service_to"] == "2026-12-30" for window in twins["after"])
    assert answer["points_kind"] == "catalog_base_without_modifiers"


@pytest.mark.parametrize("boundary", ["rows", "projection", "output"])
def test_bounds_fail_without_a_partial_success_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    boundary: str,
) -> None:
    manifest = stage_catalog(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    if boundary == "rows":
        monkeypatch.setattr(diff_records, "MAX_ROWS", 1)
    elif boundary == "projection":
        monkeypatch.setattr(diff_records, "MAX_PROJECTION_BYTES", 1)
    else:
        monkeypatch.setattr(output_module, "MAX_OUTPUT_BYTES", 1)
    code = execute_reference(
        {
            "command": "data",
            "data_command": "diff",
            "before": bundle,
            "after": bundle,
            "pairs": ["terminology:icd9-czs-synthetic-v1:icd9-czs-synthetic-v1"],
            "output_json": True,
        }
    )
    captured = capsys.readouterr()
    assert code == 4 and captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "limit_exceeded"


def test_reparenting_between_identical_anonymous_containers_is_not_no_change(
    tmp_path: Path,
) -> None:
    content = (FIXTURES / "minimal.xml").read_bytes()
    content = content.replace(
        b"<name></name>", b"<name>Synthetic anonymous structure</name>"
    )
    empty = b'<node code=""><name>Synthetic anonymous structure</name></node>'
    before_bytes = content.replace(
        b"          </nodes>\n        </node>",
        b"          </nodes>\n        </node>" + empty,
    )
    after_bytes = content.replace(b'<node code="">', empty + b'<node code="">', 1)
    bundles: list[Path] = []
    for name, payload in (("old", before_bytes), ("new", after_bytes)):
        manifest = stage_source(tmp_path, payload)
        source = manifest.sources[0]
        expected = source.expected.model_copy(
            update={
                "nodes": source.expected.nodes + 1,
                "relations": source.expected.relations + 1,
            }
        )
        manifest = manifest.model_copy(
            update={"sources": (source.model_copy(update={"expected": expected}),)}
        )
        bundle = tmp_path / name
        _ = build_reference_artifact(manifest, tmp_path, bundle)
        bundles.append(bundle)
    result = run_diff(
        bundles[0],
        bundles[1],
        "--pair",
        "terminology:icd10-synthetic-v1:icd10-synthetic-v1",
        "--json",
        "--full",
    )
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert any(
        change["category"] == "hierarchy" and change["identity"] == ["E11"]
        for change in answer["changes"]
    )
    assert not any(change["category"] == "concept" for change in answer["changes"])


def test_provenance_only_manifest_changes_remain_visible_and_markdown_has_pair_and_digests(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = stage_catalog(tmp_path)
    old, new = tmp_path / "old", tmp_path / "new"
    _ = build_reference_artifact(manifest, tmp_path, old)
    sources = tuple(
        source.model_copy(
            update={"source_url": "https://example.invalid/changed?secret=do-not-print"}
            if source.system == "icd10-pl"
            else {}
        )
        for source in manifest.sources
    )
    _ = build_reference_artifact(
        manifest.model_copy(update={"sources": sources}), tmp_path, new
    )
    pair = "terminology:icd10-synthetic-v1:icd10-synthetic-v1"
    result = run_diff(old, new, "--pair", pair, "--json", "--full")
    assert result.returncode == 0, result.stderr
    answer = json.loads(result.stdout)
    assert [change["category"] for change in answer["changes"]] == [
        "selected_manifest_identity"
    ]
    assert "do-not-print" not in result.stdout
    markdown = run_diff(old, new, "--pair", pair)
    assert markdown.returncode == 0
    assert answer["before"]["database_sha256"] in markdown.stdout
    assert answer["after"]["dataset_id"] in markdown.stdout
    assert pair in markdown.stdout.replace("\\", "")
    # Two digest values fit in 155 bytes, but their pair/category/identity do not.
    # A value-only accumulator would incorrectly accept this comparison.
    monkeypatch.setattr(store_diff, "MAX_CHANGE_BYTES", 155)
    code = execute_reference(
        {
            "command": "data",
            "data_command": "diff",
            "before": old,
            "after": new,
            "pairs": [pair],
            "output_json": True,
        }
    )
    captured = capsys.readouterr()
    assert code == 4 and captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "limit_exceeded"
