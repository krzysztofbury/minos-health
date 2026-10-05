"""Package a verified bundle as one OCI data layer without rebuilding application code."""

import argparse
from pathlib import Path
from typing import final

from pydantic import ValidationError

from minos.domain.reference import ReferenceError
from minos.ingest.processing.data_image import (
    DataImagePins,
    ImageContentError,
    package_data_image,
)
from minos.store.artifact import canonical_json, read_regular_file


@final
class Arguments(argparse.Namespace):
    application_manifest: Path = Path()
    application_config: Path = Path()
    application_sha256: str = ""
    artifact_manifest_sha256: str = ""
    bundle: Path = Path()
    output: Path = Path()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("application-manifest", "application-config", "bundle", "output"):
        _ = parser.add_argument("--" + option, type=Path, required=True)
    _ = parser.add_argument("--application-sha256", required=True)
    _ = parser.add_argument("--artifact-manifest-sha256", required=True)
    args = parser.parse_args(namespace=Arguments())
    try:
        result = package_data_image(
            application_manifest=read_regular_file(args.application_manifest),
            application_config=read_regular_file(args.application_config),
            bundle=args.bundle,
            pins=DataImagePins(
                application_manifest_sha256=args.application_sha256,
                artifact_manifest_sha256=args.artifact_manifest_sha256,
            ),
            output=args.output,
        )
        print(canonical_json(result).decode(), end="")
    except ImageContentError, ValidationError, ReferenceError, OSError:
        parser.exit(2, "Data-only image content could not be verified.\n")


if __name__ == "__main__":
    main()
