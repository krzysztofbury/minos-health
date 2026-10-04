"""Evaluate fixed, non-patient JGP gold cases; emit only query-free evidence.

Run with `uv run python -m scripts.evaluate_jgp_search --bundle PATH --output PATH`.
Known gaps are reported separately and never silently redefined as successes.
"""

import argparse
import json
import time
from pathlib import Path
from typing import ClassVar, cast, final

from pydantic import BaseModel, ConfigDict, Field

from minos.domain.morphology import PolishMorphology
from minos.domain.search import prepare_query
from minos.store.artifact import dataset_identity
from minos.store.jgp_search import build_group_index, search_groups
from minos.store.reference import open_reference_bundle


class Case(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")
    id: str
    kind: str
    query: str = Field(min_length=1, max_length=256)
    expected: str | None
    max_rank: int = Field(ge=1, le=20)
    known_gap: bool = False


@final
class Arguments(argparse.Namespace):
    bundle: Path = Path()
    output: Path = Path()


def main() -> None:
    from datetime import date

    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    gold = cast(
        dict[str, object],
        json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "tests/fixtures/jgp-search-gold.json"
            ).read_bytes()
        ),
    )
    day = date.fromisoformat(cast(str, gold["service_date"]))
    cases = [Case.model_validate(row) for row in cast(list[object], gold["cases"])]
    rows: list[dict[str, object]] = []
    failures = 0
    with open_reference_bundle(args.bundle) as (connection, manifest):
        analyzer = PolishMorphology()
        start = time.perf_counter()
        index = build_group_index(connection, analyzer)
        index_ms = (time.perf_counter() - start) * 1000
        for case in cases:
            start = time.perf_counter()
            answer = search_groups(
                connection, index, prepare_query(case.query), day, analyzer, limit=20
            )
            milliseconds = (time.perf_counter() - start) * 1000
            codes = [
                hit["code"] for hit in cast(list[dict[str, object]], answer["results"])
            ]
            rank = codes.index(case.expected) + 1 if case.expected in codes else None
            passed = (
                not codes
                if case.expected is None
                else rank is not None and rank <= case.max_rank
            )
            failures += int(not passed and not case.known_gap)
            rows.append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "passed": passed,
                    "known_gap": case.known_gap,
                    "rank": rank,
                    "milliseconds": round(milliseconds, 3),
                }
            )
        report = {
            "dataset_id": dataset_identity(manifest.dataset),
            "database_sha256": manifest.database_sha256,
            "service_date": day.isoformat(),
            "index_build_ms": round(index_ms, 3),
            "cases": rows,
            "unexpected_failures": failures,
        }
    _ = args.output.write_text(json.dumps(report, indent=2) + "\n")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
