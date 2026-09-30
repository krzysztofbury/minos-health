"""Issue #11-#13 local Linux validation, not the #14 product CLI."""

import argparse
import json
import time
from pathlib import Path
from typing import final

from pydantic import ValidationError

from minos.domain.reference import ReferenceError
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.reference import open_reference, open_reference_connection


@final
class _Arguments(argparse.Namespace):
    manifest: Path = Path()
    archive_root: Path = Path()
    output: Path = Path()
    previous_bundle: Path | None = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--manifest", required=True, type=Path)
    _ = parser.add_argument("--archive-root", required=True, type=Path)
    _ = parser.add_argument("--output", required=True, type=Path)
    _ = parser.add_argument("--previous-bundle", type=Path)
    arguments = _Arguments()
    _ = parser.parse_args(namespace=arguments)
    start = time.perf_counter()
    try:
        manifest_path = Path(arguments.manifest)
        with manifest_path.open("rb") as source:
            content = source.read(1024 * 1024 + 1)
        if len(content) > 1024 * 1024:
            raise ValueError("input manifest exceeds size limit")
        manifest = DatasetManifest.model_validate_json(content)
        bundle = Path(arguments.output)
        result = build_reference_artifact(
            manifest,
            Path(arguments.archive_root),
            bundle,
            previous_bundle=arguments.previous_bundle,
        )
        build_seconds = time.perf_counter() - start
        with open_reference_connection(bundle) as connection:
            assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        with open_reference(bundle) as reader:
            for source in manifest.sources:
                assert reader.concept(source.release_id, "E11") is not None
                assert reader.hierarchy(source.release_id, 1, max_nodes=10_000)
        print(
            json.dumps(
                {
                    "row_counts": result.row_counts,
                    "database_sha256": result.database_sha256,
                    "database_size_bytes": result.database_size_bytes,
                    "python_version": result.python_version,
                    "sqlite_version": result.sqlite_version,
                    "build_seconds": build_seconds,
                    "total_seconds": time.perf_counter() - start,
                    "peak_rss_kib": _peak_rss_kib(),
                },
                sort_keys=True,
            )
        )
        return 0
    except ReferenceError, ValidationError, ValueError, OSError:
        print("reference validation failed; no data, queries or local paths logged")
        return 1


def _peak_rss_kib() -> int:
    # ru_maxrss can include the launcher image's pre-exec peak; VmHWM measures
    # this Linux process image rather than attributing harness memory to Minos.
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmHWM:"):
            return int(line.split()[1])
    raise ValueError("Linux process peak RSS is unavailable")


if __name__ == "__main__":
    raise SystemExit(main())
