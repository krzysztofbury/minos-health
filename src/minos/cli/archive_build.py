"""Trusted archive build adapter, intentionally outside the offline entry point."""

import hashlib
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError

from minos.cli.reference import print_build_result, reference_data_error
from minos.domain.reference import ReferenceError
from minos.ingest.archive_build import ArchiveSelection, S3ArchiveReader
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json, read_regular_file


class _ArchiveArguments(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    manifest: Path
    archive_s3_selection: Path
    output: Path
    previous_bundle: Path | None = None
    output_json: bool = False


def execute_archive_build(arguments: dict[str, object]) -> int:
    try:
        selected = _ArchiveArguments.model_validate(
            {
                name: arguments[name]
                for name in _ArchiveArguments.model_fields
                if name in arguments
            }
        )
        manifest = DatasetManifest.model_validate_json(
            read_regular_file(selected.manifest)
        )
        pins = ArchiveSelection.model_validate_json(
            read_regular_file(selected.archive_s3_selection)
        )
        pins.require_dataset(manifest)
        if selected.output.exists() or selected.output.is_symlink():
            raise ReferenceError("archive build output already exists")
        reader = S3ArchiveReader.create(pins)
        try:
            with reader.materialize(
                manifest, staging_directory=selected.output.parent
            ) as archive:
                result = build_reference_artifact(
                    manifest,
                    archive,
                    selected.output,
                    previous_bundle=selected.previous_bundle,
                )
        finally:
            reader.close()
        print_build_result(
            result,
            output_json=selected.output_json,
            archive_selection_sha256=hashlib.sha256(canonical_json(pins)).hexdigest(),
        )
        return 0
    except ReferenceError, ValidationError, OSError:
        # Reuse the reference adapter's public error schema without passing
        # arbitrary network/SDK exception text through to stderr.
        return reference_data_error(arguments.get("output_json") is True)
