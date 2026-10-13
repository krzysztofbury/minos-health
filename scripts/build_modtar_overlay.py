"""Write the owner-assumed MODTAR overlay into the content-addressed archive.

Owner decision 2026-10-07 (docs/v0.0.1/decisions.md): MODTAR as published wins
and binds every 2026 catalog (generation 102, then 103 from 2026-10-13, #184),
and section 17(6a) is a separately attributed supplement the caller must declare. The overlay is deterministic:
the same pinned XML and catalog windows always give the same bytes and digest.
"""

import argparse
import calendar
import hashlib
import json
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import cast, final

from minos.domain.jgp_modifiers import (
    LegalMapping,
    LegalOverlay,
    LegalProvision,
    governing_windows,
    mappings,
    reconcile_legal_overlay,
)
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_modifier_binding_manifest import LegalOverlayInput
from minos.ingest.jgp_modifiers import parse_modifiers
from minos.ingest.jgp_modifiers_manifest import ModifierManifest
from minos.store.artifact import canonical_json
from minos.store.jgp_modifiers import catalog_windows
from minos.store.reference import open_reference_bundle

REVIEWED_ON = date(2026, 10, 7)
EVIDENCE = (
    "Owner assumption 2026-10-07 (decisions.md, Tariff Assumptions Without NFZ "
    + "Answers): MODTAR as published wins; its months define the service window."
)
SECTION_17_6A = LegalProvision(
    "manual-17-6a",
    "46/2026/DSOZ after 102/2026, section 17(6a)",
    "1.4",
    ("F72", "F73"),
    date(2026, 10, 1),
    date(2026, 12, 31),
    "Simultaneous bilateral inguinal hernia operations settled as F72 or F73. "
    + "An encounter fact the caller must declare; never applied by default.",
    "BAW 44042 consolidated act, XML attachment 32991, SHA-256 "
    + "4ae6874619f5330732de798fdd4e0861a4c5179a7350feff794365e322812877; "
    + "102/2026 section 4(4) starts it on 2026-10-01.",
)


def overlay(
    manifest: ModifierManifest,
    archive: Path,
    bundle: Path,
    reviewed_on: date = REVIEWED_ON,
) -> LegalOverlay:
    with tempfile.TemporaryDirectory(dir=archive) as staging:
        with verified_source(
            archive, manifest.xml, staging_directory=Path(staging)
        ) as source:
            release = parse_modifiers(
                source, year=manifest.year, generation=manifest.generation
            )
    with open_reference_bundle(bundle) as (connection, _):
        windows = catalog_windows(connection)
    rows: list[LegalMapping] = []
    for mapping in mappings(release):
        first = mapping[3] or 1
        last = mapping[4] or 12
        end = calendar.monthrange(manifest.year, last)[1]
        rows.append(
            LegalMapping(
                mapping,
                date(manifest.year, first, 1),
                date(manifest.year, last, end),
                EVIDENCE,
            )
        )
    governing = sorted(
        {
            window.release_id
            for row in rows
            if row.mapping[6] is not None
            for window in governing_windows(windows, row.service_from, row.service_to)
        }
    )
    result = LegalOverlay(
        manifest.year,
        manifest.generation,
        "SZP",
        tuple(governing),
        reviewed_on,
        (EVIDENCE,),
        tuple(rows),
        (SECTION_17_6A,),
    )
    return reconcile_legal_overlay(release, result, catalog_windows=windows)


@final
class Arguments(argparse.Namespace):
    modifiers: Path = Path()
    generation: int = 0
    archive_root: Path = Path()
    catalog_bundle: Path = Path()
    reviewed_on: date = REVIEWED_ON


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--modifiers", type=Path, required=True)
    _ = parser.add_argument("--generation", type=int, required=True)
    _ = parser.add_argument("--archive-root", type=Path, required=True)
    _ = parser.add_argument("--catalog-bundle", type=Path, required=True)
    _ = parser.add_argument(
        "--reviewed-on", type=date.fromisoformat, default=REVIEWED_ON
    )
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    selection = cast(
        dict[str, list[dict[str, object]]], json.loads(args.modifiers.read_bytes())
    )
    manifest = next(
        ModifierManifest.model_validate_json(json.dumps(item))
        for item in selection["modifiers"]
        if item["generation"] == args.generation
    )
    value = overlay(manifest, args.archive_root, args.catalog_bundle, args.reviewed_on)
    content = canonical_json(
        LegalOverlayInput.model_validate_json(json.dumps(asdict(value), default=str))
    )
    digest = hashlib.sha256(content).hexdigest()
    target = args.archive_root / "blobs/sha256" / digest
    if not target.exists():
        _ = target.write_bytes(content)
    print(
        json.dumps(
            {
                "modifier_release_id": manifest.release_id,
                "catalog_release_ids": list(value.catalog_release_ids),
                "overlay": {"sha256": digest, "size_bytes": len(content)},
                "rows": len(value.rows),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
