"""Write the reviewed per-report extracts of the NFZ monthly JGP reports (#283).

Run from the repository root after pinning a raw report in
`manifests/sources/jgp-statistics.json` (an `extract` may be absent for it):

    uv run python -m scripts.extract_jgp_statistics --archive-root data/archive

Each raw report is verified against its pin and parsed; its canonical extract
is written create-once to the archive by SHA-256, and the extract pin is set in
the reviewed manifest and in the public validation dataset. Rerunning gives the
same bytes and changes nothing.
"""

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import cast, final

from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_statistics import parse_report
from minos.ingest.jgp_statistics_extract import write_extract
from minos.ingest.jgp_statistics_manifest import JgpStatistics, ReportPin
from minos.ingest.reference_manifest import DatasetManifest

STATISTICS = Path("manifests/sources/jgp-statistics.json")
DATASET = Path("manifests/sources/reference-conditions-public-validation.json")

type Json = dict[str, object]


@final
class Arguments(argparse.Namespace):
    archive_root: Path = Path("data/archive")


def _archive(root: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    target = root / "blobs" / "sha256" / digest
    if target.exists():
        if target.read_bytes() != content:
            raise SystemExit(f"archive blob {digest} differs from its extract")
        return digest
    partial = target.with_name(target.name + ".partial")
    with partial.open("xb") as stream:
        _ = stream.write(content)
    os.chmod(partial, 0o444)
    _ = partial.rename(target)
    return digest


def _write_json(path: Path, value: Json) -> None:
    _ = path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--archive-root", type=Path, default=Path("data/archive"))
    arguments = Arguments()
    _ = parser.parse_args(namespace=arguments)
    statistics = cast(Json, json.loads(STATISTICS.read_text()))
    reports = cast(list[Json], statistics["reports"])
    with tempfile.TemporaryDirectory() as staging:
        for report in reports:
            raw = {key: value for key, value in report.items() if key != "extract"}
            pin = ReportPin.model_validate_json(json.dumps(raw))
            with verified_source(
                arguments.archive_root, pin, staging_directory=Path(staging)
            ) as source:
                figures = parse_report(source, pin)
            content = write_extract(figures, pin)
            report["extract"] = {
                "sha256": _archive(arguments.archive_root, content),
                "size_bytes": len(content),
                "group_figures": len(figures.groups),
                "section_figures": len(figures.sections),
            }
            print(json.dumps({"month": pin.month, "extract": report["extract"]}))
    _ = JgpStatistics.model_validate_json(json.dumps(statistics))
    dataset = cast(Json, json.loads(DATASET.read_text()))
    dataset["jgp_statistics"] = statistics
    _ = DatasetManifest.model_validate_json(json.dumps(dataset))
    _write_json(STATISTICS, statistics)
    _write_json(DATASET, dataset)


if __name__ == "__main__":
    main()
