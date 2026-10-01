import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from minos.domain.reference import QueryLimitError, SourceError
from minos.domain.search import SearchInputError, SearchRequest, prepare_query
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference
from tests.fixtures.icd9_workbook import (
    MAIN_ROWS,
    archive_bytes,
    workbook_manifest,
    workbook_parts,
    worksheet,
)
from tests.fixtures.icd9_xml import XML, source_manifest
from tests.integration.test_reference_artifact import stage_source


def stage_catalog(root: Path, content: bytes = XML) -> DatasetManifest:
    original = stage_source(root)
    source = source_manifest(content)
    blob = root / "blobs/sha256" / source.sha256
    _ = blob.write_bytes(content)
    return original.model_copy(update={"sources": (*original.sources, source)})


def test_mixed_build_preserves_release_identity_and_cli_source_status(
    tmp_path: Path,
) -> None:
    manifest = stage_catalog(tmp_path)
    bundle = tmp_path / "bundle"
    manifest_path = tmp_path / "source.json"
    _ = manifest_path.write_text(manifest.model_dump_json())
    built = subprocess.run(
        [
            sys.executable,
            "-m",
            "minos",
            "data",
            "build",
            "--manifest",
            str(manifest_path),
            "--archive-root",
            str(tmp_path),
            "--output",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert json.loads(built.stdout)["row_counts"]["source_release"] == 2
    with open_reference_bundle(bundle) as (connection, _manifest):
        assert connection.execute(
            "SELECT system, count(*) FROM source_release GROUP BY system ORDER BY system"
        ).fetchall() == [("icd10-pl", 1), ("icd9-pl", 1)]
        assert connection.execute(
            "SELECT count(*) FROM reference_relation WHERE source_release_id='icd9-czs-synthetic-v1'"
        ).fetchone() == (0,)
        for index in ("auto", "portable"):
            result = search_reference(
                connection,
                SearchRequest("icd9-pl", prepare_query("badania moczu"), index=index),
            )
            assert [(hit.code, hit.match_type) for hit in result.hits] == [
                ("A01", "morphology_tokens")
            ]
            assert dict(result.hits[0].source_attributes)["status"] == "N"
    command = [
        sys.executable,
        "-m",
        "minos",
        "search",
        "--system",
        "icd9-pl",
        "--query",
        "A01",
        "--data",
        str(bundle),
    ]
    output = subprocess.run(
        [*command, "--json"], capture_output=True, text=True, timeout=10, check=True
    )
    response = cast(dict[str, object], json.loads(output.stdout))
    hits = cast(list[dict[str, object]], response["results"])
    attributes = cast(dict[str, str], hits[0]["source_attributes"])
    assert response["release_id"] == "icd9-czs-synthetic-v1"
    assert attributes["status"] == "N"
    assert attributes["data_do"] == "2026-09-24"
    assert cast(dict[str, object], hits[0]["context"])["edges"] == []
    text = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=True
    )
    assert "status=N" in text.stdout
    assert "data_od=2008-07-01" in text.stdout
    assert "data_do=2026-09-24" in text.stdout
    assert "do not infer" in cast(str, response["disclaimer"])


def test_new_generation_is_a_distinct_immutable_release_and_requires_selection(
    tmp_path: Path,
) -> None:
    original = stage_catalog(tmp_path)
    old = build_reference_artifact(original, tmp_path, tmp_path / "old")
    content = (
        XML.replace(b'symbol="5.90"', b'symbol="5.91"')
        .replace(b'nr_gen="93"', b'nr_gen="94"')
        .replace(b"Synthetic chapter", b"Changed synthetic chapter")
    )
    next_source = source_manifest(content).model_copy(
        update={
            "release_id": "icd9-czs-synthetic-v2",
            "source_version": "5.91",
            "source_file_id": "ICD9:94",
            "source_url": "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=94",
        }
    )
    _ = (tmp_path / "blobs/sha256" / next_source.sha256).write_bytes(content)
    combined = original.model_copy(update={"sources": (*original.sources, next_source)})
    first = build_reference_artifact(
        combined, tmp_path, tmp_path / "first", previous_bundle=tmp_path / "old"
    )
    second = build_reference_artifact(combined, tmp_path, tmp_path / "second")
    assert first.database_sha256 == second.database_sha256
    assert (
        hashlib.sha256((tmp_path / "old/minos.db").read_bytes()).hexdigest()
        == old.database_sha256
    )
    with open_reference_bundle(tmp_path / "first") as (connection, _manifest):
        with pytest.raises(SearchInputError, match="explicit selection"):
            _ = search_reference(
                connection, SearchRequest("icd9-pl", prepare_query("A01"))
            )
        result = search_reference(
            connection,
            SearchRequest(
                "icd9-pl", prepare_query("A01"), release_id=next_source.release_id
            ),
        )
        assert result.release_id == next_source.release_id
        assert result.hits[0].code == "A01"


def test_matching_checksum_does_not_allow_conflicting_header_to_publish(
    tmp_path: Path,
) -> None:
    content = XML.replace(b'nr_gen="93"', b'nr_gen="92"')
    manifest = stage_catalog(tmp_path, content)
    with pytest.raises(SourceError, match="manifest"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".minos-reference-*"))


def test_source_attributes_cannot_escape_the_response_size_budget(
    tmp_path: Path,
) -> None:
    large = b"x" * 65_536
    root = XML[: XML.index(b"<pozycja_slownika")]
    entries = [
        b'<pozycja_slownika kod="A0'
        + str(number).encode()
        + b'" nazwa="Synthetic large record" status="A" opis="'
        + large
        + b'" wlaczenia="'
        + large
        + b'" wylaczenia="'
        + large
        + b'"/>'
        for number in range(1, 4)
    ]
    manifest = stage_catalog(tmp_path, root + b"".join(entries) + b"</komunikat>")
    source = manifest.sources[1]
    source = source.model_copy(
        update={
            "expected": source.expected.model_copy(
                update={"nodes": 3, "concepts": 3, "descriptions": 3}
            ),
        }
    )
    manifest = manifest.model_copy(update={"sources": (manifest.sources[0], source)})
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _manifest):
        with pytest.raises(QueryLimitError, match="output exceeds"):
            _ = search_reference(
                connection,
                SearchRequest("icd9-pl", prepare_query("Synthetic large record")),
            )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "minos",
            "search",
            "--system",
            "icd9-pl",
            "--query",
            "Synthetic large record",
            "--data",
            str(bundle),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 4
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "limit_exceeded"


def stage_companion(root: Path, content: bytes | None = None) -> DatasetManifest:
    manifest = stage_catalog(root)
    body = content if content is not None else archive_bytes()
    source = workbook_manifest(body)
    _ = (root / "blobs/sha256" / source.sha256).write_bytes(body)
    return manifest.model_copy(update={"sources": (*manifest.sources, source)})


@pytest.mark.parametrize("different_title", [False, True])
def test_companion_build_preserves_attribution_and_reconciles_without_corrections(
    tmp_path: Path,
    different_title: bool,
) -> None:
    parts = workbook_parts()
    if different_title:
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b"  Badanie moczu  ", b"Changed preferred title"
        )
    manifest = stage_companion(tmp_path, archive_bytes(parts))
    first = build_reference_artifact(manifest, tmp_path, tmp_path / "first")
    second = build_reference_artifact(manifest, tmp_path, tmp_path / "second")
    assert first.database_sha256 == second.database_sha256
    assert (tmp_path / "first/review.txt").read_bytes() == (
        tmp_path / "second/review.txt"
    ).read_bytes()
    review = (tmp_path / "first/review.txt").read_text()
    assert "CZS-only codes: 1; workbook-only codes: 2" in review
    assert (
        f"Shared codes with different preferred source text: {int(different_title)}"
        in review
    )
    assert "Workbook conflicting title/code-form evidence nodes: 1" in review
    assert "CZS-only status A entries: 1" in review
    with open_reference_bundle(tmp_path / "first") as (connection, _manifest):
        assert connection.execute("SELECT count(*) FROM source_release").fetchone() == (
            3,
        )
        assert connection.execute(
            "SELECT count(*) FROM reference_node_attribute WHERE source_release_id=? AND name='status'",
            (manifest.sources[2].release_id,),
        ).fetchone() == (0,)
        with pytest.raises(SearchInputError, match="explicit selection"):
            _ = search_reference(
                connection, SearchRequest("icd9-pl", prepare_query("A01"))
            )
        selected = search_reference(
            connection,
            SearchRequest(
                "icd9-pl",
                prepare_query("A01"),
                release_id=manifest.sources[2].release_id,
            ),
        )
        assert selected.hits[0].source_code == "A01 "
        assert selected.hits[0].context_edges == ((1, 2), (2, 3))
        assert [node.code for node in selected.hits[0].context_nodes] == [
            "AA",
            "A",
            "A01",
        ]
        for index in ("auto", "portable"):
            result = search_reference(
                connection,
                SearchRequest(
                    "icd9-pl",
                    prepare_query("Different synthetic title"),
                    release_id=manifest.sources[2].release_id,
                    index=index,
                ),
            )
            assert result.hits[0].code == "A01"
            assert result.hits[0].label == (
                "Changed preferred title" if different_title else "  Badanie moczu  "
            )
            assert result.hits[0].description_type == "context"
            assert (
                dict(result.hits[0].matched_source_attributes)["evidence_kind"]
                == "conflicting_title_or_code_form"
            )


def test_exclusion_note_search_exposes_its_marker_not_a_synonym(tmp_path: Path) -> None:
    parts = workbook_parts()
    parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
        b'name="Uwagi"', 'name="wł_wył_kat gl"'.encode()
    )
    parts["xl/worksheets/sheet3.xml"] = worksheet(
        (
            ("Numer:", "TD:", "Wł/Wył:", "tytuł"),
            ("A01", "Synthetic title", "Nie obejmuje", "Synthetic exclusion phrase"),
        ),
        main=False,
    )
    manifest = stage_companion(tmp_path, archive_bytes(parts))
    source = manifest.sources[2].model_copy(
        update={
            "expected": manifest.sources[2].expected.model_copy(update={"nodes": 7})
        }
    )
    manifest = manifest.model_copy(update={"sources": (*manifest.sources[:2], source)})
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    command = [
        sys.executable,
        "-m",
        "minos",
        "search",
        "--system",
        "icd9-pl",
        "--release",
        source.release_id,
        "--query",
        "Synthetic exclusion phrase",
        "--data",
        str(bundle),
    ]
    output = subprocess.run(
        [*command, "--json"], capture_output=True, text=True, timeout=10, check=True
    )
    response = cast(dict[str, object], json.loads(output.stdout))
    hits = cast(list[dict[str, object]], response["results"])
    match = cast(dict[str, object], hits[0]["match"])
    assert (
        cast(dict[str, str], match["source_attributes"])["evidence_kind"]
        == "Nie obejmuje"
    )
    assert match["description_type"] == "context"
    text = subprocess.run(
        command, capture_output=True, text=True, timeout=10, check=True
    )
    assert "Match source attributes: evidence_kind=Nie obejmuje" in text.stdout


@pytest.mark.parametrize("damage", ["truncated", "unknown_sheet", "conflicting_level"])
def test_corrupt_companion_cannot_publish_after_a_valid_catalog(
    tmp_path: Path, damage: str
) -> None:
    parts = workbook_parts()
    if damage == "unknown_sheet":
        parts["xl/workbook.xml"] = parts["xl/workbook.xml"].replace(
            b'name="Uwagi"', b'name="Unexpected sheet"'
        )
    elif damage == "conflicting_level":
        parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
            b'<c r="A6" t="n"><v>88</v></c>',
            b'<c r="A6" t="inlineStr"><is><t>A01</t></is></c>',
        )
    content = archive_bytes(parts)
    manifest = stage_companion(
        tmp_path, content[:-10] if damage == "truncated" else content
    )
    with pytest.raises(SourceError, match="workbook"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not list(tmp_path.glob(".minos-reference-*"))


def test_workbook_multiple_parents_missing_context_and_orphan_notes(
    tmp_path: Path,
) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = worksheet(
        (
            *MAIN_ROWS[:2],
            ("AA", "First parent", "A", "First branch", "A01", "Shared child"),
            ("BB", "Second parent", "B", "Second branch", "A01", "Shared child"),
            (None, None, "C", "No supplied parent", "A04", "Explicit child"),
        ),
        main=True,
    )
    manifest = stage_companion(tmp_path, archive_bytes(parts))
    source = manifest.sources[2].model_copy(
        update={
            "expected": manifest.sources[2].expected.model_copy(
                update={"nodes": 9, "relations": 6, "concepts": 7, "descriptions": 7}
            )
        }
    )
    manifest = manifest.model_copy(update={"sources": (*manifest.sources[:2], source)})
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _):
        for query, expected in (
            ("A01", {("AA", "A"), ("A", "A01"), ("BB", "B"), ("B", "A01")}),
            ("A04", {("C", "A04")}),
        ):
            hit = search_reference(
                connection,
                SearchRequest(
                    "icd9-pl", prepare_query(query), release_id=source.release_id
                ),
            ).hits[0]
            codes = {node.ordinal: node.code for node in hit.context_nodes}
            assert {
                (codes[parent], codes[child]) for parent, child in hit.context_edges
            } == expected
        orphan = search_reference(
            connection,
            SearchRequest(
                "icd9-pl",
                prepare_query("Synthetic orphan note"),
                release_id=source.release_id,
            ),
        ).hits[0]
        assert orphan.code is None
        assert not orphan.context_edges
        assert dict(orphan.source_attributes)["source_code"] == "A99"


def test_new_workbook_version_leaves_older_bundle_immutable(tmp_path: Path) -> None:
    original = stage_companion(tmp_path)
    old = build_reference_artifact(original, tmp_path, tmp_path / "old")
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        b"Synthetic parent", b"Changed synthetic parent"
    )
    content = archive_bytes(parts)
    updated = workbook_manifest(content).model_copy(
        update={
            "release_id": "icd9-nfz-workbook-synthetic-v2",
            "source_version": "5.91",
            "source_file_id": "icd-9_pl_cm_v_5.91.xlsx#ICD-9_PL ",
            "source_url": "https://www.nfz.gov.pl/download/gfx/nfz/pl/defaultstronaopisowa/782/1/2/icd-9_pl_cm_v_5.91.xlsx",
        }
    )
    _ = (tmp_path / "blobs/sha256" / updated.sha256).write_bytes(content)
    combined = original.model_copy(update={"sources": (*original.sources, updated)})
    _ = build_reference_artifact(
        combined, tmp_path, tmp_path / "new", previous_bundle=tmp_path / "old"
    )
    assert (
        hashlib.sha256((tmp_path / "old/minos.db").read_bytes()).hexdigest()
        == old.database_sha256
    )
    assert (
        "no same-version CZS release selected; not reconciled"
        in (tmp_path / "new/review.txt").read_text()
    )
    with open_reference_bundle(tmp_path / "new") as (connection, _):
        for source, expected in (
            (original.sources[2], "Synthetic parent"),
            (updated, "Changed synthetic parent"),
        ):
            hit = search_reference(
                connection,
                SearchRequest(
                    "icd9-pl", prepare_query("AA"), release_id=source.release_id
                ),
            ).hits[0]
            assert hit.label == expected
