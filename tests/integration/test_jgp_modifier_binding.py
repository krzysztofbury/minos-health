import json
import sqlite3
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient

from minos.domain.jgp_modifiers import LegalProvision
from minos.domain.reference import ArtifactError, SourceError
from minos.http.app import create_app
from minos.ingest.jgp_modifier_binding_manifest import LegalOverlayInput
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, file_identity
from minos.store.jgp_modifier_binding import (
    require_modifier_binding,
    reviewed_group_modifiers,
)
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from tests.integration.test_jgp_catalog_reference import catalog_manifest
from tests.integration.test_jgp_modifiers_reference import stage_modifiers
from tests.unit.test_jgp_modifiers import XML, overlay_for_source


def binding_dataset(root: Path, *, conflict: bool = False) -> DatasetManifest:
    base = catalog_manifest(root)
    modifier = stage_modifiers(root, XML.replace(b"A02", b"PZD02"))
    overlay = overlay_for_source()
    rows = tuple(
        replace(
            row,
            mapping=(
                *row.mapping[:6],
                "PZD02" if row.mapping[6] == "A02" else row.mapping[6],
            ),
        )
        for row in overlay.rows
    )
    if conflict:
        rows = rows[1:]
    overlay = replace(
        overlay,
        catalog_release_ids=(base.catalogs[0].release_id,),
        rows=rows,
        supplements=(
            LegalProvision(
                "manual-17-6a",
                "Synthetic section 17(6a)",
                "1.4",
                ("A01",),
                date(2026, 10, 1),
                date(2026, 12, 31),
                "Synthetic bilateral condition; not unconditional application.",
                "Synthetic governing act and October applicability.",
            ),
        ),
    )
    # A transport model validates the serialized dataclass, not publisher XML.
    from dataclasses import asdict

    value = LegalOverlayInput.model_validate_json(
        json.dumps(asdict(overlay), default=str)
    )
    content = canonical_json(value)
    import hashlib

    digest = hashlib.sha256(content).hexdigest()
    _ = (root / "blobs/sha256" / digest).write_bytes(content)
    data = base.model_dump(mode="json")
    catalogs = cast(list[dict[str, object]], data["catalogs"])
    catalogs[0]["service_from"] = "2026-01-01"
    data["modifiers"] = [modifier.model_dump(mode="json")]
    data["modifier_bindings"] = [
        {
            "catalog_release_id": base.catalogs[0].release_id,
            "modifier_release_id": modifier.release_id,
            "overlay": {"sha256": digest, "size_bytes": len(content)},
            "reviewed_on": "2026-10-03",
            "knowledge_from": "2026-08-20",
            "reviewer": "Synthetic fixture reviewer",
            "evidence": "Synthetic legal review, not NFZ approval.",
        }
    ]
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_reviewed_binding_survives_offline_build_and_respects_knowledge_and_year(
    tmp_path: Path,
) -> None:
    manifest = binding_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    built = build_reference_artifact(manifest, tmp_path, bundle)
    assert built.schema_version == 7
    assert built.row_counts["modifier_binding"] == 1
    assert "explicit reviewed overlays" in (bundle / "review.txt").read_text()
    with open_reference_bundle(bundle) as (connection, _):
        ruleset = resolve_ruleset(connection, date(2026, 10, 1), None)
        binding = require_modifier_binding(connection, ruleset)
        assert binding["binding"] == "reviewed"
        assert binding["generation"] == 100
        assert binding["overlay_sha256"] == manifest.modifier_bindings[0].overlay.sha256
        early = resolve_ruleset(connection, date(2026, 7, 1), date(2026, 8, 19))
        assert cast(dict[str, object], early["modifiers"])["binding"] == "unbound"
        with pytest.raises(SourceError, match="unbound"):
            _ = require_modifier_binding(connection, early)
        future = resolve_ruleset(connection, date(2027, 1, 1), None)
        with pytest.raises(SourceError, match="unbound"):
            _ = require_modifier_binding(connection, future)
        with pytest.raises(SourceError, match="mismatched"):
            _ = require_modifier_binding(
                connection, {**ruleset, "release_id": "different"}
            )
    second = build_reference_artifact(manifest, tmp_path, tmp_path / "second")
    assert second.database_sha256 == built.database_sha256


@pytest.mark.parametrize(
    "service_date,expected", [("2026-09-30", []), ("2026-10-01", ["manual-17-6a"])]
)
def test_offline_modifier_reference_keeps_october_manual_clause_separate(
    tmp_path: Path, service_date: str, expected: list[str]
) -> None:
    manifest = binding_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _):
        ruleset = resolve_ruleset(connection, date.fromisoformat(service_date), None)
        result = reviewed_group_modifiers(connection, manifest, ruleset, "A01")
        supplements = cast(list[dict[str, object]], result["legal_supplements"])
        assert [item["symbol"] for item in supplements] == expected
        if supplements:
            assert supplements[0]["coefficient_millionths"] == 1400000
            assert supplements[0]["provenance"] == "manual_legal_overlay"
            assert "not unconditional" in cast(str, supplements[0]["eligibility_text"])
        mappings = cast(list[dict[str, object]], result["dictionary_mappings"])
        assert mappings[0]["symbol"] == "M1"
        assert mappings[0]["coefficient_millionths"] == 1200000


def test_modifier_cli_uses_real_offline_binding_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from minos.cli.reference import reference_main

    manifest = binding_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    reference_main(
        [
            "jgp",
            "modifiers",
            "--code",
            "A01",
            "--data",
            str(bundle),
            "--service-date",
            "2026-10-01",
            "--known-on",
            "2026-10-02",
            "--json",
        ]
    )
    answer = cast(dict[str, object], json.loads(capsys.readouterr().out))
    result = cast(dict[str, object], answer["modifier_reference"])
    supplements = cast(list[dict[str, object]], result["legal_supplements"])
    assert supplements[0]["symbol"] == "manual-17-6a"
    assert answer["points_kind"] == "catalog_base_points_without_modifiers"


def test_incomplete_legal_overlay_never_publishes_a_bound_artifact(
    tmp_path: Path,
) -> None:
    manifest = binding_dataset(tmp_path, conflict=True)
    with pytest.raises(SourceError, match="missing, extra or conflicting"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_bound_reference_api_and_page_keep_base_only_labels(tmp_path: Path) -> None:
    manifest = binding_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with TestClient(create_app(bundle)) as client:
        response = client.get("/api/v1/jgp/2026-10-01/groups/A01")
        assert response.status_code == 200
        assert response.json()["ruleset"]["modifiers"]["binding"] == "reviewed"
        assert response.json()["points_kind"] == "catalog_base_points_without_modifiers"
        page = client.get("/jgp/2026-10-01/A01")
        assert page.status_code == 200
        assert "powiązane referencyjnie" in page.text
        assert "nie są tu oceniane" in page.text
        assert "punkty bazowe wg katalogu, bez modyfikatorów" in page.text


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE modifier_binding SET reviewer='different'",
        "UPDATE modifier_binding SET overlay_json=json_set(overlay_json,'$.generation',102)",
        "DELETE FROM modifier_binding_window",
        "DELETE FROM modifier_binding",
    ],
)
def test_bound_bundle_rejects_policy_or_overlay_tampering_after_rehash(
    tmp_path: Path, tamper: str
) -> None:
    manifest = binding_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with sqlite3.connect(bundle / "minos.db") as connection:
        _ = connection.execute(tamper)
    digest, size = file_identity(bundle / "minos.db")
    changed = read_artifact_manifest(bundle).model_copy(
        update={"database_sha256": digest, "database_size_bytes": size}
    )
    _ = (bundle / "manifest.json").write_bytes(canonical_json(changed))
    with pytest.raises(ArtifactError, match="MODTAR"):
        with open_reference_bundle(bundle):
            pass
