import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.jgp_modifiers_manifest import ModifierManifest
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, file_identity
from minos.store.jgp_modifiers_build import validate_modifier_rows
from minos.store.reference import open_reference_bundle, read_artifact_manifest
from tests.integration.test_canonical_reference import canonical_manifest
from tests.unit.test_jgp_modifiers import XML


def stage_modifiers(root: Path, content: bytes = XML) -> ModifierManifest:
    digest = hashlib.sha256(content).hexdigest()
    blob = root / "blobs/sha256" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    _ = blob.write_bytes(content)
    return ModifierManifest.model_validate_json(
        json.dumps(
            {
                "release_id": "modtar-synthetic-100",
                "year": 2026,
                "generation": 100,
                "grouper_type": "SZP",
                "xml": {
                    "source_url": "https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=MODTAR&nrGen=100",
                    "sha256": digest,
                    "size_bytes": len(content),
                    "fetched_at": "2026-10-03T10:00:00Z",
                },
                "expected_modifiers": 4,
                "expected_periods": 4,
                "expected_group_mappings": 5,
                "expected_inactive_empty": 1,
                "publisher": "Synthetic source",
                "license": "Synthetic fixture",
                "notices": ["Source mapping, never an approved legal binding"],
            }
        )
    )


def modifier_dataset(root: Path, content: bytes = XML) -> DatasetManifest:
    base = canonical_manifest(root)
    modifier = stage_modifiers(root, content)
    data = base.model_dump(mode="json")
    data["modifiers"] = [modifier.model_dump(mode="json")]
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_modifier_bundle_persists_exact_rows_empty_inactive_record_and_provenance(
    tmp_path: Path,
) -> None:
    manifest = modifier_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    built = build_reference_artifact(manifest, tmp_path, bundle)
    assert built.schema_version == 6
    assert built.row_counts["tariff_modifier"] == 4
    assert built.row_counts["modifier_group"] == 5
    assert built.row_counts["modifier_candidate"] == 0
    with open_reference_bundle(bundle) as (connection, opened):
        source = validate_modifier_rows(connection, manifest.modifiers[0])
        assert source.modifiers[-1].periods == ()
        assert connection.execute(
            "SELECT coefficient_text,coefficient_millionths FROM modifier_period ORDER BY modifier_order,source_order"
        ).fetchall() == [
            ("1.025", 1025000),
            ("1.20", 1200000),
            ("1.20", 1200000),
            ("1.1", 1100000),
        ]
        assert opened.dataset.modifiers[0].xml.sha256 == hashlib.sha256(XML).hexdigest()
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            _ = connection.execute("DELETE FROM tariff_modifier")
    second = build_reference_artifact(manifest, tmp_path, tmp_path / "second")
    assert built.database_sha256 == second.database_sha256
    assert (
        "all catalog modifier bindings remain unbound"
        in (bundle / "review.txt").read_text()
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "UPDATE modifier_period SET coefficient_millionths=1025001 WHERE modifier_order=1 AND source_order=1",
        "UPDATE modifier_group SET code='Z01_S' WHERE modifier_order=1 AND period_order=1 AND source_order=1",
        "DELETE FROM tariff_modifier WHERE symbol='INACTIVE'",
        "UPDATE modifier_release SET generation=102",
    ],
)
def test_bundle_open_refuses_modifier_row_tampering_after_checksums_are_recomputed(
    tmp_path: Path, tamper: str
) -> None:
    manifest = modifier_dataset(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    with open_reference_bundle(bundle):
        pass
    with sqlite3.connect(bundle / "minos.db") as connection:
        _ = connection.execute(tamper)
    checksum, size = file_identity(bundle / "minos.db")
    changed = read_artifact_manifest(bundle).model_copy(
        update={"database_sha256": checksum, "database_size_bytes": size}
    )
    _ = (bundle / "manifest.json").write_bytes(canonical_json(changed))
    with pytest.raises(ArtifactError, match="MODTAR"):
        with open_reference_bundle(bundle):
            pass


def test_failed_modifier_source_import_never_publishes_a_partial_bundle(
    tmp_path: Path,
) -> None:
    manifest = modifier_dataset(tmp_path, XML.replace(b'status="A"', b'status="X"', 1))
    with pytest.raises(SourceError):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()
    assert not tuple(tmp_path.glob(".minos-reference-*"))
