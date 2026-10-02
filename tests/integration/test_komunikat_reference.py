import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minos.domain.reference import ArtifactError, SourceError
from minos.domain.search import SearchRequest, prepare_query
from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json
from minos.store.komunikat import procedure_rank_evidence
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference
from tests.fixtures.komunikat import stage_komunikat
from tests.integration.test_canonical_reference import canonical_manifest


class MutableDataset(TypedDict):
    komunikaty: list[dict[str, object]]


class MutableArtifact(TypedDict):
    dataset: MutableDataset


def rank_manifest(root: Path) -> DatasetManifest:
    base = canonical_manifest(root)
    evidence = stage_komunikat(root, base.sources[1].release_id)
    data = base.model_dump(mode="json")
    data["komunikaty"] = [evidence.model_dump(mode="json")]
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_real_build_query_api_page_and_cli_keep_ranks_separate_from_catalog(
    tmp_path: Path,
) -> None:
    manifest = rank_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    built = build_reference_artifact(manifest, tmp_path, bundle)
    assert built.schema_version == 3
    assert built.row_counts["procedure_rank"] == 4
    assert built.row_counts["department_specialty"] == 3
    selected = manifest.sources[1].release_id
    with open_reference_bundle(bundle) as (connection, _):
        rank = procedure_rank_evidence(connection, manifest, selected, "A01")
        assert rank["rank"] == 4
        assert rank["grouper_type"] == "SZP"
        assert rank["source_title"] == "  Synthetic rank title  "
        assert rank["ruleset_binding"] == "not_bound"
        assert rank["first_reporting_month"] == "2026-01"
        assert rank["pairing"] == "reference-only"
        assert (
            procedure_rank_evidence(connection, manifest, selected, "00.00001")["rank"]
            == 0
        )
        missing = procedure_rank_evidence(connection, manifest, selected, "A")
        assert missing["status"] == "not_listed" and missing["rank"] is None
        hit = search_reference(
            connection, SearchRequest("icd9-pl", prepare_query("A01"))
        ).hits[0]
        assert hit.label == "  Badanie moczu  "
        assert not search_reference(
            connection, SearchRequest("icd9-pl", prepare_query("05.01"))
        ).hits
        assert connection.execute(
            "SELECT code,service_codes FROM department_specialty WHERE grouper_type='SZP'"
        ).fetchall() == [("0010", "4")]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _ = connection.execute("DELETE FROM procedure_rank")
    with TestClient(create_app(bundle)) as client:
        response = client.get(f"/api/v1/procedure-ranks/{selected}/A01")
        assert response.status_code == 200
        assert response.json()["evidence"]["rank"] == 4
        assert (
            response.json()["evidence"]["rank_source"]["sha256"]
            == manifest.komunikaty[0].ranks.sha256
        )
        page = client.get("/slowniki/icd-9/A01")
        assert page.status_code == 200
        assert "Ranga 4" in page.text and "Badanie moczu" in page.text
        assert "nie dobór rulesetu dla daty świadczenia" in page.text
        assert "Pierwszy okres sprawozdawczy wersji" in page.text
        assert 'href="/slowniki/icd-9#kod=A01"' in page.text
        assert 'href="/site.css"' in page.text
        assert "Ranga 0" in client.get("/slowniki/icd-9/00.00001").text
        assert "Nie oznacza to rangi 0" in client.get("/slowniki/icd-9/A").text
        assert "procedure-rank" not in client.get("/slowniki/icd-10/E11.0").text
        assert (
            client.get(f"/api/v1/procedure-ranks/{selected}/05.01").status_code == 404
        )
        assert (
            client.get(
                f"/api/v1/procedure-ranks/{selected}/A01?query=private"
            ).status_code
            == 400
        )
    source = tmp_path / "dataset.json"
    _ = source.write_bytes(canonical_json(manifest))
    output = subprocess.run(
        [
            sys.executable,
            "-m",
            "minos",
            "data",
            "build",
            "--manifest",
            str(source),
            "--archive-root",
            str(tmp_path),
            "--output",
            str(tmp_path / "cli-bundle"),
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(output.stdout)["row_counts"]["procedure_rank"] == 4
    repeated = build_reference_artifact(manifest, tmp_path, tmp_path / "repeat")
    assert repeated.database_sha256 == built.database_sha256


def test_schema_two_bundles_stay_readable_and_rank_evidence_is_explicitly_absent(
    tmp_path: Path,
) -> None:
    manifest = canonical_manifest(tmp_path)
    assert "komunikaty" not in manifest.model_dump(mode="json")
    bundle = tmp_path / "legacy"
    built = build_reference_artifact(manifest, tmp_path, bundle)
    assert built.schema_version == 2
    with open_reference_bundle(bundle) as (connection, _):
        assert procedure_rank_evidence(
            connection, manifest, manifest.sources[1].release_id, "A01"
        ) == {
            "code": "A01",
            "icd9_release_id": manifest.sources[1].release_id,
            "grouper_type": "SZP",
            "rank": None,
            "status": "not_selected",
            "ruleset_binding": "not_bound",
        }
    with TestClient(create_app(bundle)) as client:
        assert "Nie wybrano wykazu rang" in client.get("/slowniki/icd-9/A01").text


def test_corrupt_companion_leaves_no_publishable_bundle(tmp_path: Path) -> None:
    manifest = rank_manifest(tmp_path)
    digest = manifest.komunikaty[0].ranks.sha256
    _ = (tmp_path / "blobs/sha256" / digest).write_bytes(b"corrupt")
    with pytest.raises(SourceError, match="byte count"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_komunikat_manifest_and_database_correspondence_fail_closed(
    tmp_path: Path,
) -> None:
    manifest = rank_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    manifest_file = bundle / "manifest.json"
    value = cast(MutableArtifact, json.loads(manifest_file.read_bytes()))
    value["dataset"]["komunikaty"][0]["algorithm_version"] = "2.1.2026"
    _ = manifest_file.write_bytes(
        canonical_json(cast(dict[str, object], cast(object, value)))
    )
    with pytest.raises(ArtifactError, match="komunikat source policy"):
        with open_reference_bundle(bundle):
            pass


def test_binding_cannot_select_supporting_workbook_or_duplicate_catalog_correspondence(
    tmp_path: Path,
) -> None:
    manifest = rank_manifest(tmp_path)
    for target in (
        manifest.sources[0].release_id,
        manifest.sources[2].release_id,
        "missing-catalog",
    ):
        value = manifest.model_dump(mode="json")
        value["komunikaty"][0]["icd9_release_id"] = target
        with pytest.raises(ValidationError, match="canonical CZS"):
            _ = DatasetManifest.model_validate_json(json.dumps(value))
    duplicated = cast(MutableDataset, cast(object, manifest.model_dump(mode="json")))
    second = dict(duplicated["komunikaty"][0])
    second["release_id"] = "second-komunikat"
    duplicated["komunikaty"].append(second)
    with pytest.raises(ValidationError, match="ambiguous"):
        _ = DatasetManifest.model_validate_json(json.dumps(duplicated))
