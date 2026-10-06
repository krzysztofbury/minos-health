"""Verified source loading and real-boundary failures with synthetic Office bytes."""

from pathlib import Path

import pytest

from minos.domain.grouper import UnsupportedAlgorithm
from minos.domain.reference import SourceError
from minos.grouper.registry import EngineBinding
from minos.ingest.grouper_algorithm import load_algorithm
from tests.fixtures.grouper import key
from tests.fixtures.komunikat import stage_komunikat


def test_archive_adapter_runs_only_after_all_three_sources_are_verified(
    tmp_path: Path,
) -> None:
    manifest = stage_komunikat(tmp_path, "icd9-czs-synthetic-v1")
    data = load_algorithm(manifest, tmp_path, staging_directory=tmp_path)
    assert data.identity.algorithm_sha256 == manifest.algorithm.sha256
    assert data.identity.ranks_sha256 == manifest.ranks.sha256
    assert data.identity.specialties_sha256 == manifest.specialties.sha256
    # This six-paragraph fixture has version metadata, not the reviewed rules
    # or the paragraph coordinates used by the execution engine.
    with pytest.raises(UnsupportedAlgorithm, match="reviewed algorithm bytes"):
        _ = EngineBinding(key(data.identity), data)


@pytest.mark.parametrize("file", ["algorithm", "ranks", "specialties"])
def test_rotated_archive_bytes_never_emit_partially_loaded_tables(
    tmp_path: Path, file: str
) -> None:
    manifest = stage_komunikat(tmp_path, "icd9-czs-synthetic-v1")
    source = {
        "algorithm": manifest.algorithm,
        "ranks": manifest.ranks,
        "specialties": manifest.specialties,
    }[file]
    blob = tmp_path / "blobs" / "sha256" / source.sha256
    payload = blob.read_bytes()
    _ = blob.write_bytes(b"!" + payload[1:])
    with pytest.raises(SourceError, match="checksum"):
        _ = load_algorithm(manifest, tmp_path, staging_directory=tmp_path)
