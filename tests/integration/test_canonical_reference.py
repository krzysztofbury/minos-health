import json
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from minos.domain.reference import ArtifactError
from minos.domain.search import SearchInputError, SearchRequest, prepare_query
from minos.http.app import create_app
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference
from tests.fixtures.icd9_workbook import (
    MAIN_ROWS,
    archive_bytes,
    workbook_parts,
    worksheet,
)
from tests.integration.test_icd9_reference import stage_companion


class Context(TypedDict):
    edges: list[dict[str, object]]


class Evidence(TypedDict):
    release_id: str
    source_sha256: str
    node_ordinal: int
    context: Context


class Hit(TypedDict):
    supporting_contexts: list[Evidence]


class Release(TypedDict):
    system: str
    origin: str
    supporting_sources: list[Evidence]


class Answer(TypedDict):
    results: list[Hit]
    releases: list[Release]


def canonical_manifest(root: Path, content: bytes | None = None) -> DatasetManifest:
    original = stage_companion(root, content)
    value = original.model_dump(mode="json")
    value["sources"][2]["context_for"] = original.sources[1].release_id
    return DatasetManifest.model_validate_json(json.dumps(value))


def test_canonical_catalog_keeps_titles_status_and_separate_complete_context(
    tmp_path: Path,
) -> None:
    parts = workbook_parts()
    parts["xl/worksheets/sheet1.xml"] = worksheet(
        (
            *MAIN_ROWS[:2],
            (
                "AA",
                "Workbook first parent",
                "A",
                "First branch",
                "A01",
                "Different title",
            ),
            (
                "BB",
                "Workbook second parent",
                "B",
                "Second branch",
                "A01",
                "Different title",
            ),
            (None, None, "C", "No supplied parent", "A04", "Workbook-only child"),
        ),
        main=True,
    )
    original = stage_companion(tmp_path, archive_bytes(parts))
    value = original.model_dump(mode="json")
    value["sources"][2]["context_for"] = original.sources[1].release_id
    value["sources"][2]["expected"] = {
        "nodes": 9,
        "relations": 6,
        "concepts": 7,
        "descriptions": 7,
    }
    manifest = DatasetManifest.model_validate_json(json.dumps(value))
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _):
        for index in ("auto", "portable"):
            result = search_reference(
                connection, SearchRequest("icd9-pl", prepare_query("A01"), index=index)
            )
            hit = result.hits[0]
            assert result.release_id == manifest.sources[1].release_id
            assert hit.label == "  Badanie moczu  "
            assert dict(hit.source_attributes)["status"] == "N"
            assert hit.context_edges == ()
            supporting = hit.supporting_contexts[0]
            assert supporting.release_id == manifest.sources[2].release_id
            codes = {node.ordinal: node.code for node in supporting.nodes}
            assert {
                (codes[parent], codes[child]) for parent, child in supporting.edges
            } == {("AA", "A"), ("A", "A01"), ("BB", "B"), ("B", "A01")}
            assert supporting.nodes[-1].label == "Different title"
            for query in ("A04", "Different title"):
                assert not search_reference(
                    connection,
                    SearchRequest("icd9-pl", prepare_query(query), index=index),
                ).hits
        czs_only = search_reference(
            connection, SearchRequest("icd9-pl", prepare_query("00.00001"))
        ).hits[0]
        assert czs_only.supporting_contexts == ()
        with pytest.raises(SearchInputError):
            _ = search_reference(
                connection,
                SearchRequest(
                    "icd9-pl",
                    prepare_query("A01"),
                    release_id=manifest.sources[2].release_id,
                ),
            )
    response = subprocess.run(
        [
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
            "--json",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    answer = cast(Answer, json.loads(response.stdout))
    evidence = answer["results"][0]["supporting_contexts"][0]
    assert evidence["source_sha256"] == manifest.sources[2].sha256
    assert evidence["node_ordinal"] == supporting.node_ordinal
    assert len(evidence["context"]["edges"]) == 4


def test_http_exposes_one_catalog_per_system_and_retains_icd10_notices(
    tmp_path: Path,
) -> None:
    manifest = canonical_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with TestClient(create_app(bundle)) as client:
        assert client.get("/readyz").status_code == 200
        metadata = cast(Answer, client.get("/api/v1/releases").json())
        assert [
            (source["system"], source["origin"]) for source in metadata["releases"]
        ] == [("icd10-pl", "rsk"), ("icd9-pl", "czs")]
        support = metadata["releases"][1]["supporting_sources"][0]
        assert support["release_id"] == manifest.sources[2].release_id
        assert support["source_sha256"] == manifest.sources[2].sha256
        result = client.post(
            "/api/v1/search",
            json={
                "system": "icd10-pl",
                "release_id": manifest.sources[0].release_id,
                "query": "E11.0",
            },
        )
        assert result.status_code == 200
        assert result.json()["results"][0]["code"] == "E11.0"
        assert result.json()["results"][0]["context"]["edges"]
        assert result.json()["provenance"]["license"] == manifest.sources[0].license
        assert result.json()["provenance"]["notices"] == list(
            manifest.sources[0].notices
        )
        assert (
            client.post(
                "/api/v1/search",
                json={
                    "system": "icd9-pl",
                    "release_id": manifest.sources[2].release_id,
                    "query": "A01",
                },
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "target", ["absent", "icd10-synthetic-v1", "icd9-nfz-workbook-synthetic-v1"]
)
def test_invalid_supporting_source_binding_is_rejected(
    tmp_path: Path, target: str
) -> None:
    manifest = stage_companion(tmp_path)
    value = manifest.model_dump(mode="json")
    value["sources"][2]["context_for"] = target
    with pytest.raises(ValidationError, match="supporting source"):
        _ = DatasetManifest.model_validate_json(json.dumps(value))


def test_wrong_version_support_and_manifest_policy_drift_fail_closed(
    tmp_path: Path,
) -> None:
    manifest = canonical_manifest(tmp_path)
    value = manifest.model_dump(mode="json")
    value["sources"][2]["source_version"] = "5.91"
    with pytest.raises(ValidationError, match="supporting source"):
        _ = DatasetManifest.model_validate_json(json.dumps(value))
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    path = bundle / "manifest.json"
    content = cast(
        dict[str, dict[str, list[dict[str, object]]]], json.loads(path.read_text())
    )
    del content["dataset"]["sources"][2]["context_for"]
    _ = path.write_bytes(canonical_json(cast(dict[str, object], content)))
    with pytest.raises(ArtifactError, match="policy does not match"):
        with TestClient(create_app(bundle)):
            pytest.fail("manifest drift produced a ready public service")
