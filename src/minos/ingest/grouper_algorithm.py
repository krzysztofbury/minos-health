"""Verified private-archive adapter for the preparation-only algorithm slice."""

from pathlib import Path

from minos.domain.grouper import AlgorithmData, AlgorithmIdentity
from minos.domain.komunikat import DepartmentSpecialty, ProcedureRank
from minos.ingest.archive_source import verified_source
from minos.ingest.komunikat import parse_algorithm, parse_companion
from minos.ingest.komunikat_manifest import KomunikatManifest


def load_algorithm(
    manifest: KomunikatManifest, archive_root: Path, *, staging_directory: Path
) -> AlgorithmData:
    """Verify all three exact bytes before returning any usable algorithm data.

    Source pairing remains reference-only; this does not manufacture a legally
    reviewed composite ruleset from the communicating notice's publication date.
    """
    with verified_source(
        archive_root, manifest.algorithm, staging_directory=staging_directory
    ) as source:
        metadata = parse_algorithm(source, manifest)
    with verified_source(
        archive_root, manifest.ranks, staging_directory=staging_directory
    ) as source:
        ranks = tuple(
            row
            for row in parse_companion(source, manifest, ranks=True)
            if isinstance(row, ProcedureRank) and row.grouper_type == "SZP"
        )
    with verified_source(
        archive_root, manifest.specialties, staging_directory=staging_directory
    ) as source:
        specialties = tuple(
            row
            for row in parse_companion(source, manifest, ranks=False)
            if isinstance(row, DepartmentSpecialty) and row.grouper_type == "SZP"
        )
    return AlgorithmData(
        AlgorithmIdentity(
            manifest.release_id,
            manifest.algorithm.sha256,
            manifest.ranks.sha256,
            manifest.specialties.sha256,
        ),
        metadata,
        ranks,
        specialties,
    )
