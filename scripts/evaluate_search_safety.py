"""Observe source-based qualifier and negative probes, never infer clinical safety."""

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self, final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from minos.domain.morphology import PolishMorphology
from minos.domain.reference import ReferenceError
from minos.domain.search import IndexMode, SearchRequest, prepare_query
from minos.ingest.manifest_types import Identifier, SourceText
from minos.store.artifact import dataset_identity, read_regular_file
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference
from scripts.evaluate_search import EvaluationError

Code = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9.]{0,15}$")]


class Probe(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    id: Annotated[str, Field(pattern=r"^S[0-9]{2}$")]
    system: Literal["icd10-pl", "icd9-pl"]
    query: Annotated[str, Field(min_length=1, max_length=120)]
    kind: Literal["qualifier", "negation", "unrelated", "ambiguity", "typo"]
    acceptable: Annotated[tuple[Code, ...], Field(max_length=32)]
    contradictory: Annotated[tuple[Code, ...], Field(max_length=32)]
    expect_empty: bool = False
    source_url: Annotated[str, Field(pattern=r"^https://[^\s]{1,500}$")]
    rationale: SourceText

    @model_validator(mode="after")
    def consistent_targets(self) -> Self:
        if set(self.acceptable) & set(self.contradictory):
            raise ValueError("probe targets overlap")
        if self.expect_empty and self.acceptable:
            raise ValueError("empty probe cannot have acceptable targets")
        if not self.expect_empty and not (self.acceptable or self.contradictory):
            raise ValueError("probe has no observation target")
        return self


class SafetySet(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    version: Literal[1]
    review_status: Literal["source_based_pending_independent_and_practitioner_review"]
    service_date: date
    releases: dict[str, Identifier]
    cases: Annotated[tuple[Probe, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def consistent_inventory(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate probe")
        if set(self.releases) != {case.system for case in self.cases}:
            raise ValueError("probe systems conflict with pins")
        return self


def evaluate(bundle: Path, probes: SafetySet, index: IndexMode) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    with open_reference_bundle(bundle) as (connection, manifest):
        # Absent codes must not create a falsely clean contradiction report.
        for case in probes.cases:
            for code in (*case.acceptable, *case.contradictory):
                if (
                    connection.execute(
                        "SELECT 1 FROM concept JOIN source_release ON source_release.id=concept.source_release_id WHERE source_release.id=? AND source_release.system=? AND concept.lookup_code=?",
                        (probes.releases[case.system], case.system, code),
                    ).fetchone()
                    is None
                ):
                    raise EvaluationError("probe target absent from pinned release")
        analyzer = PolishMorphology()
        for case in probes.cases:
            failure: str | None = None
            codes: list[str | None] = []
            suggestions = 0
            try:
                result = search_reference(
                    connection,
                    SearchRequest(
                        case.system,
                        prepare_query(case.query),
                        probes.releases[case.system],
                        limit=20,
                        index=index,
                        service_date=probes.service_date,
                    ),
                    analyzer=analyzer,
                )
                codes = [hit.code for hit in result.hits]
                suggestions = len(result.suggestions)
            except ReferenceError as error:
                failure = type(error).__name__
            rank = next(
                (
                    position
                    for position, code in enumerate(codes, 1)
                    if code in case.acceptable
                ),
                None,
            )
            contradictions = [
                {"code": code, "rank": position}
                for position, code in enumerate(codes, 1)
                if code in case.contradictory
            ]
            rows.append(
                {
                    "id": case.id,
                    "kind": case.kind,
                    "rank": rank,
                    "top5": rank is not None and rank <= 5,
                    "contradictory_hits": contradictions,
                    "returned_codes": codes,
                    "suggestion_count": suggestions,
                    "empty_expectation_met": not codes and failure is None
                    if case.expect_empty
                    else None,
                    "failure": failure,
                }
            )
        return {
            "dataset_id": dataset_identity(manifest.dataset),
            "database_sha256": manifest.database_sha256,
            "service_date": probes.service_date.isoformat(),
            "index": index,
            "review_status": probes.review_status,
            "notice": "Observation only. Not clinical validation or an independently reviewed held-out gate. Suggestions are never accepted as search hits.",
            "cases": rows,
        }


@final
class Arguments(argparse.Namespace):
    bundle: Path = Path()
    fixture: Path = Path("tests/evaluation/search-safety-v1.json")
    index: IndexMode = "auto"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument(
        "--fixture", type=Path, default=Path("tests/evaluation/search-safety-v1.json")
    )
    _ = parser.add_argument("--index", choices=("auto", "portable"), default="auto")
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    try:
        content = read_regular_file(args.fixture)
        probes = SafetySet.model_validate_json(content)
        report = evaluate(args.bundle, probes, args.index)
        report["fixture_sha256"] = hashlib.sha256(content).hexdigest()
    except EvaluationError, ReferenceError, ValidationError, OSError:
        parser.exit(2, "Pinned safety evaluation inputs could not be verified.\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
