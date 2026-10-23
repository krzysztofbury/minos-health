"""The remembered dataset identity always equals the digest of its manifest."""

import gc
import hashlib
from pathlib import Path

from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, dataset_identity

MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "manifests/sources/reference-conditions-public-validation.json"
)


def digest(manifest: DatasetManifest) -> str:
    return hashlib.sha256(canonical_json(manifest)).hexdigest()


def test_identity_follows_content_even_when_object_ids_are_reused() -> None:
    base = DatasetManifest.model_validate_json(MANIFEST.read_bytes())
    assert dataset_identity(base) == dataset_identity(base) == digest(base)
    # Short-lived manifests free their ids for the next one; a remembered digest
    # must never answer for a different manifest at a reused id (#225).
    for index in range(40):
        changed = base.model_copy(update={"transform_version": f"variant-{index}"})
        assert dataset_identity(changed) == digest(changed) != digest(base)
        del changed
        _ = gc.collect()
    assert dataset_identity(base) == digest(base)
