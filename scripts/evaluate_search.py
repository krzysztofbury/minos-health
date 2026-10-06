"""Report the pinned search gold set offline, without printing query text."""

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Annotated, ClassVar, Literal, Self, final

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from minos.domain.morphology import PolishMorphology
from minos.domain.normalization import normalize_description
from minos.domain.reference import ReferenceError
from minos.domain.search import (
    IndexMode,
    SearchHit,
    SearchRequest,
    fold_polish,
    prepare_query,
)
from minos.ingest.manifest_types import Identifier, SourceText
from minos.ingest.search_synonyms import SearchSynonym
from minos.store.artifact import dataset_identity, read_regular_file
from minos.store.reference import open_reference_bundle
from minos.store.search import search_reference
from minos.store.search_synonyms import rewrite_requests


class Case(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    id: Annotated[str, Field(pattern=r"^G[0-9]{2}$")]
    system: Literal["icd10-pl", "icd9-pl"]
    query: Annotated[str, Field(min_length=1, max_length=256)]
    codes: Annotated[
        tuple[Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9.]{0,15}$")], ...],
        Field(min_length=1, max_length=32),
    ]
    category: SourceText
    source: Literal["practitioner", "proposed"]
    baseline_rank: Annotated[int, Field(ge=1, le=100)] | None


class GoldSet(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid", strict=True, frozen=True
    )
    version: Literal[1]
    source: Annotated[
        str,
        Field(
            pattern=r"^https://github\.com/krzysztofbury/minos-health/issues/[0-9]+$"
        ),
    ]
    notice: SourceText
    releases: dict[str, Identifier]
    cases: Annotated[tuple[Case, ...], Field(min_length=1, max_length=100)]
    service_date: date = date(2026, 10, 4)

    @model_validator(mode="after")
    def unique_cases(self) -> Self:
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("duplicate evaluation case")
        if set(self.releases) != {case.system for case in self.cases}:
            raise ValueError("evaluation systems and release pins conflict")
        return self


class EvaluationError(Exception):
    pass


def _validate_codes(connection: sqlite3.Connection, gold: GoldSet) -> None:
    for case in gold.cases:
        release = gold.releases[case.system]
        for code in case.codes:
            if (
                connection.execute(
                    "SELECT 1 FROM concept JOIN source_release ON source_release.id=concept.source_release_id WHERE source_release.id=? AND source_release.system=? AND concept.lookup_code=?",
                    (release, case.system, code),
                ).fetchone()
                is None
            ):
                raise EvaluationError(
                    "evaluation code is absent from its exact pinned release"
                )


def evaluate(
    bundle: Path,
    gold: GoldSet,
    *,
    index: IndexMode = "auto",
    candidates: tuple[SearchSynonym, ...] = (),
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    categories: dict[str, dict[str, int]] = {}
    regressions: list[str] = []
    reviewed_regressions: list[str] = []
    with open_reference_bundle(bundle) as (connection, manifest):
        _validate_codes(connection, gold)
        analyzer = PolishMorphology()
        for case in gold.cases:
            rank: int | None = None
            failure: str | None = None
            started = time.perf_counter()
            hits: tuple[SearchHit, ...] = ()
            try:
                result = search_reference(
                    connection,
                    SearchRequest(
                        case.system,
                        prepare_query(case.query),
                        gold.releases[case.system],
                        limit=20,
                        index=index,
                        service_date=gold.service_date,
                    ),
                    analyzer=analyzer,
                )
                rank = next(
                    (
                        position
                        for position, hit in enumerate(result.hits, 1)
                        if hit.code in case.codes
                    ),
                    None,
                )
                hits = result.hits
            except ReferenceError as error:
                failure = type(error).__name__
            top1, top5 = rank == 1, rank is not None and rank <= 5
            candidate_rank = (
                _candidate_rank(
                    connection, case, gold, index, analyzer, hits, candidates
                )
                if candidates
                else rank
            )
            category = categories.setdefault(
                case.category, {"cases": 0, "top1": 0, "top5": 0}
            )
            category["cases"] += 1
            category["top1"] += int(top1)
            category["top5"] += int(top5)
            regression = (
                case.baseline_rank is not None and case.baseline_rank <= 5 and not top5
            )
            if regression:
                regressions.append(case.id)
                if case.source == "practitioner":
                    reviewed_regressions.append(case.id)
            rows.append(
                {
                    "duration_ms": (time.perf_counter() - started) * 1000,
                    "candidate_rank": candidate_rank,
                    "id": case.id,
                    "category": case.category,
                    "source": case.source,
                    "rank": rank,
                    "top1": top1,
                    "top5": top5,
                    "failure": failure,
                }
            )
        return {
            "fixture_version": gold.version,
            "service_date": gold.service_date.isoformat(),
            "fixture_source": gold.source,
            "dataset_id": dataset_identity(manifest.dataset),
            "database_sha256": manifest.database_sha256,
            "cases": rows,
            "categories": categories,
            "total": len(rows),
            "top1": sum(c["top1"] for c in categories.values()),
            "top5": sum(c["top5"] for c in categories.values()),
            "baseline_regressions": regressions,
            "reviewed_baseline_regressions": reviewed_regressions,
            "gate_policy": "Only practitioner-reviewed baseline regressions may block; proposed cases are reported, not release gates.",
            "candidate_evaluation": bool(candidates),
            "candidate_top1": sum(row["candidate_rank"] == 1 for row in rows),
            "candidate_top5": sum(
                isinstance(row["candidate_rank"], int) and row["candidate_rank"] <= 5
                for row in rows
            ),
            "candidate_notice": "Hypothetical terminology proposals only; this does not approve synonyms or a release.",
        }


def _candidate_rank(
    connection: sqlite3.Connection,
    case: Case,
    gold: GoldSet,
    index: IndexMode,
    analyzer: PolishMorphology,
    hits: tuple[SearchHit, ...],
    entries: tuple[SearchSynonym, ...],
) -> int | None:
    if len(entries) > 500:
        raise EvaluationError("candidate synonym inventory exceeds bound")
    codes = [
        hit.code
        for hit in hits
        if hit.match_type in ("exact_code", "exact_description")
    ]
    if len(codes) < 20:
        phrases = [
            (
                fold_polish(normalize_description(entry.term)),
                normalize_description(entry.source_wording),
                int(entry.whole_query_only),
            )
            for entry in sorted(entries, key=lambda entry: (-len(entry.term), entry.id))
            if entry.system == case.system
            and entry.release_id in (None, gold.releases[case.system])
        ]
        request = SearchRequest(
            case.system,
            prepare_query(case.query),
            gold.releases[case.system],
            limit=20,
            index=index,
            service_date=gold.service_date,
        )
        for rewritten, _wording in rewrite_requests(request, phrases):
            try:
                result = search_reference(connection, rewritten, analyzer=analyzer)
            except ReferenceError:
                continue
            for hit in result.hits:
                if hit.code not in codes and len(codes) < 20:
                    codes.append(hit.code)
    codes.extend(hit.code for hit in hits if hit.code not in codes)
    return next(
        (position for position, code in enumerate(codes[:20], 1) if code in case.codes),
        None,
    )


@final
class Arguments(argparse.Namespace):
    bundle: Path = Path()
    fixture: Path = Path("tests/evaluation/search-gold-v1.json")
    index: IndexMode = "auto"
    gate_reviewed: bool = False
    candidate_synonyms: Path | None = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument("--fixture", type=Path, default=Arguments.fixture)
    _ = parser.add_argument("--index", choices=("auto", "portable"), default="auto")
    _ = parser.add_argument("--gate-reviewed", action="store_true")
    _ = parser.add_argument("--candidate-synonyms", type=Path)
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    try:
        fixture_content = read_regular_file(args.fixture)
        gold = GoldSet.model_validate_json(fixture_content)
        if args.gate_reviewed and args.candidate_synonyms is not None:
            raise EvaluationError("candidate evaluation cannot be a release gate")
        candidates: tuple[SearchSynonym, ...] = ()
        content = b""
        if args.candidate_synonyms is not None:
            content = read_regular_file(args.candidate_synonyms)
            candidates = TypeAdapter(tuple[SearchSynonym, ...]).validate_json(content)
        report = evaluate(args.bundle, gold, index=args.index, candidates=candidates)
        report["fixture_sha256"] = hashlib.sha256(fixture_content).hexdigest()
        report["index"] = args.index
        if content:
            report["candidate_input_sha256"] = hashlib.sha256(content).hexdigest()
    except EvaluationError, ReferenceError, ValidationError, OSError:
        print(
            json.dumps(
                {"error": "evaluation input or pinned bundle could not be verified"}
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.gate_reviewed and report["reviewed_baseline_regressions"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
