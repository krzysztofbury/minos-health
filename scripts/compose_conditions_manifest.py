"""Compose reviewed #30 inputs without changing the production catalog selector."""

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Annotated, ClassVar, final

from pydantic import BaseModel, ConfigDict, Field

from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.ingest.jgp_modifiers_manifest import ModifierCandidate, ModifierManifest
from minos.ingest.manifest_types import SourceText
from minos.ingest.reference_manifest import DatasetManifest


class Selection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    characteristics: Annotated[
        tuple[ConditionsManifest, ...], Field(min_length=1, max_length=8)
    ]
    characteristics_knowledge_as_of: date
    characteristics_knowledge_evidence: SourceText


class ModifierSelection(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    modifiers: Annotated[
        tuple[ModifierManifest, ...], Field(min_length=1, max_length=8)
    ]
    modifier_candidates: Annotated[tuple[ModifierCandidate, ...], Field(max_length=8)]


def bounded_read(path: Path) -> bytes:
    with path.open("rb") as source:
        content = source.read(128 * 1024 + 1)
    if len(content) > 128 * 1024:
        raise ValueError("reviewed manifest exceeds byte bound")
    return content


@final
class Arguments(argparse.Namespace):
    base: Path = Path("manifests/sources/reference-catalog-public-validation.json")
    conditions: Path = Path("manifests/sources/jgp-conditions-reference.json")
    output: Path = Path()
    modifiers: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "--base",
        type=Path,
        default=Path("manifests/sources/reference-catalog-public-validation.json"),
    )
    _ = parser.add_argument(
        "--conditions",
        type=Path,
        default=Path("manifests/sources/jgp-conditions-reference.json"),
    )
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--modifiers", type=Path)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    base = DatasetManifest.model_validate_json(bounded_read(args.base))
    conditions = Selection.model_validate_json(bounded_read(args.conditions))
    data = base.model_dump(mode="json")
    data.update(conditions.model_dump(mode="json"))
    data["transform_version"] = "jgp-conditions-reference-v2"
    if args.modifiers is not None:
        data.update(
            ModifierSelection.model_validate_json(
                bounded_read(args.modifiers)
            ).model_dump(mode="json")
        )
        data["transform_version"] = "jgp-reference-conditions-modifiers-v3"
    manifest = DatasetManifest.model_validate_json(json.dumps(data))
    with args.output.open("x", encoding="utf-8") as target:
        _ = target.write(manifest.model_dump_json(indent=2) + "\n")


if __name__ == "__main__":
    main()
