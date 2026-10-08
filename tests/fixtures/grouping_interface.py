"""Synthetic adapter bundle, not publisher/archive acceptance evidence."""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import cast

from minos.grouper.szp_2026 import ALGORITHM_SHA256
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import ArtifactManifest, canonical_json
from minos.store.reference import read_artifact_manifest
from tests.fixtures.komunikat import stage_komunikat
from tests.integration.test_jgp_condition_consumers import consumer_bundle


def grouping_bundle(root: Path) -> Path:
    source = consumer_bundle(root)
    manifest = read_artifact_manifest(source).dataset
    notice = stage_komunikat(root, "icd9-czs-synthetic-v1")
    manifest = DatasetManifest.model_validate_json(
        json.dumps(
            {
                **manifest.model_dump(mode="json"),
                "komunikaty": [notice.model_dump(mode="json")],
                "search_profile": "polish-folded-1",
            }
        )
    )
    output = root / "grouping"
    _ = build_reference_artifact(manifest, root, output)
    database = output / "minos.db"
    database.chmod(0o600)
    # The adapter tests trust a constructed domain snapshot, like the existing
    # pipeline fixtures. Only opt-in real-source tests prove publisher provenance.
    pins = notice.model_dump(mode="json")
    cast(dict[str, object], pins["algorithm"])["sha256"] = ALGORITHM_SHA256
    with sqlite3.connect(database) as connection:
        _ = connection.execute(
            "UPDATE algorithm_release SET manifest_json=?",
            (
                canonical_json(
                    KomunikatManifest.model_validate_json(json.dumps(pins))
                ).decode(),
            ),
        )
        _ = connection.execute(
            "UPDATE procedure_rank SET code='100.01', rank=3 WHERE grouper_type='SZP' AND source_row=4"
        )
    artifact_path = output / "manifest.json"
    artifact = cast(dict[str, object], json.loads(artifact_path.read_bytes()))
    cast(dict[str, object], artifact["dataset"])["komunikaty"] = [pins]
    artifact["database_sha256"] = hashlib.sha256(database.read_bytes()).hexdigest()
    artifact["database_size_bytes"] = database.stat().st_size
    _ = artifact_path.write_bytes(
        canonical_json(ArtifactManifest.model_validate_json(json.dumps(artifact)))
    )
    database.chmod(0o444)
    return output


def grouping_body() -> dict[str, object]:
    return {
        "schema_version": 1,
        "service_date": "2026-10-05",
        "reporting_month": "2026-10-01",
        "declared_modifiers": [],
        "encounter": {
            "admitted_on": "2026-10-01",
            "discharged_on": "2026-10-05",
            "hospitalization_type": "hospitalization",
            "age_completed_years": 40,
            "principal_diagnosis": {"code": "G60.4", "hospitalization_level": True},
            "stays": [
                {
                    "identifier": "ward",
                    "department": "4000",
                    "admitted_on": "2026-10-01",
                    "discharged_on": "2026-10-05",
                }
            ],
            "procedures": [
                {"code": "100.01", "performed_on": "2026-10-02", "stay_id": "ward"}
            ],
            "product_reporting": "complete",
            "settlement_products": [],
        },
    }
