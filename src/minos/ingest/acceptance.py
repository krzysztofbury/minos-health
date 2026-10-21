"""Criterion 4 for a candidate bundle: reviewed questions keep their answers.

Phase C of #141 activates a terminology release only if the reviewed search
gold cases and the golden grouping cases answer at least as well from the
candidate as from the live bundle. JGP catalogs keep the terminology release
they were built with, so a grouping answer must not move at all; a search
case found in the live top five must stay there on the new release's first
day. Each bundle is asked through the real HTTP application in its own child
process: a closed pool does not return its native dictionaries' memory, so
two pools in one process would exceed the processor's memory bound.
"""

import json
import subprocess  # nosec B404 - runs this module itself, never a shell.
import sys
import time
from datetime import date
from pathlib import Path
from typing import ClassVar, Protocol, cast

from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from minos.http.app import create_app
from minos.ingest.icd10_candidate import CandidateRejected

# A busy or timed-out answer is asked again; it is not a regression.
_TRANSIENT = frozenset((429, 503, 504))
_ATTEMPTS = 40
_RETRY_DELAY_S = 0.5
# Pool load, warm-up and the cases at 0.5 CPU, with room for a 1 GiB bundle.
_CHILD_TIMEOUT_S = 900
SEARCH_GOLD = "search-gold-v1.json"
GROUPING_CASES = "grouping-cases.json"
_MAX_CASE_FILE_BYTES = 1024 * 1024
_TOP = 5


class AcceptanceUnavailable(Exception):
    """An answer stayed unavailable; judge the candidate again next run."""


class _Loose(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="ignore", frozen=True)


class _SearchCase(_Loose):
    id: str
    system: str
    query: str
    codes: tuple[str, ...] = Field(min_length=1)


class _SearchGold(_Loose):
    cases: tuple[_SearchCase, ...] = Field(min_length=1, max_length=1000)


class _GroupingCase(_Loose):
    id: str
    bundle: str
    request: dict[str, object]


class _GroupingCases(_Loose):
    cases: tuple[_GroupingCase, ...] = Field(min_length=1, max_length=1000)


def accept_candidate(
    live: Path, candidate: Path, *, system: str, effective: date, cases: Path
) -> None:
    """Refuse the candidate when a reviewed answer regresses."""
    try:
        gold = _SearchGold.model_validate_json(_read(cases / SEARCH_GOLD))
        grouping = _GroupingCases.model_validate_json(_read(cases / GROUPING_CASES))
    except ValidationError, OSError:
        raise CandidateRejected("regression", "acceptance_cases_unreadable") from None
    searches = tuple(case for case in gold.cases if case.system == system)
    # Synthetic cases need the synthetic fixture bundle, not real data.
    groups = tuple(case for case in grouping.cases if case.bundle == "real")
    before = _answers_in_child(live, system, effective, cases)
    after = _answers_in_child(candidate, system, effective, cases)
    expected = {case.id for case in searches} | {group.id for group in groups}
    if set(before) != expected or set(after) != expected:
        raise AcceptanceUnavailable("acceptance answers are incomplete")
    for case in searches:
        if before[case.id] and not after[case.id]:
            raise CandidateRejected("regression", "search_gold_case_lost")
    for group in groups:
        if before[group.id] != after[group.id]:
            raise CandidateRejected("regression", "grouping_golden_case_changed")


def _answers_in_child(
    bundle: Path, system: str, effective: date, cases: Path
) -> dict[str, object]:
    try:
        completed = subprocess.run(  # nosec B603 - fixed argv, this interpreter.
            [
                sys.executable,
                "-I",
                "-m",
                "minos.ingest.acceptance",
                str(bundle),
                system,
                effective.isoformat(),
                str(cases),
            ],
            capture_output=True,
            timeout=_CHILD_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AcceptanceUnavailable("acceptance child timed out") from None
    if completed.returncode != 0:
        raise AcceptanceUnavailable("acceptance child failed")
    try:
        return cast(dict[str, object], json.loads(completed.stdout))
    except ValueError:
        raise AcceptanceUnavailable("acceptance child answer is invalid") from None


def _answers(
    bundle: Path,
    searches: tuple[_SearchCase, ...],
    groups: tuple[_GroupingCase, ...],
    effective: date,
) -> dict[str, object]:
    answers: dict[str, object] = {}
    # One lease: the questions are sequential, and each lease carries its own
    # morphology dictionary, which the processor's memory bound cannot hold four of.
    with TestClient(create_app(bundle, pool_capacity=1)) as client:
        for case in searches:
            response = _ask(
                client,
                "/api/v1/search",
                {
                    "system": case.system,
                    "query": case.query,
                    "service_date": effective.isoformat(),
                    "limit": _TOP,
                },
            )
            if response.status_code != 200:
                answers[case.id] = False
                continue
            body = cast(dict[str, object], response.json())
            results = cast(list[dict[str, object]], body["results"])
            answers[case.id] = any(item.get("code") in case.codes for item in results)
        for group in groups:
            response = _ask(client, "/api/v1/group", group.request)
            answers[group.id] = (
                _grouping(cast(dict[str, object], response.json()))
                if response.status_code == 200
                else f"http {response.status_code}"
            )
    return answers


class _Answer(Protocol):
    @property
    def status_code(self) -> int: ...

    def json(self) -> object: ...


def _ask(client: TestClient, path: str, payload: dict[str, object]) -> _Answer:
    for _ in range(_ATTEMPTS):
        response = client.post(path, json=payload)
        if response.status_code not in _TRANSIENT:
            return response
        time.sleep(_RETRY_DELAY_S)
    raise AcceptanceUnavailable(path)


def _grouping(answer: dict[str, object]) -> object:
    """The judged parts of a grouping answer, as the golden cases assert them."""
    groups = cast(list[dict[str, object]], answer.get("groups", []))
    preparation = cast(dict[str, object], answer.get("preparation", {}))
    return json.dumps(
        {
            "status": answer.get("status"),
            "groups": sorted((str(g["group"]), str(g["status"])) for g in groups),
            "ranking": [
                r["group"]
                for r in cast(list[dict[str, object]], answer.get("ranking", []))
            ],
            "not_evaluated": answer.get("not_evaluated"),
            "procedures": [
                p.get("outcome")
                for p in cast(
                    list[dict[str, object]], preparation.get("procedures", [])
                )
            ],
            "ward_adjusted_days": preparation.get("ward_adjusted_days"),
            "products": [
                p.get("class")
                for p in cast(list[dict[str, object]], preparation.get("products", []))
            ],
        },
        sort_keys=True,
    )


def _read(path: Path) -> bytes:
    with path.open("rb") as source:
        content = source.read(_MAX_CASE_FILE_BYTES + 1)
    if len(content) > _MAX_CASE_FILE_BYTES:
        raise OSError("acceptance case file exceeds its bound")
    return content


def _child(arguments: list[str]) -> int:
    """Print one bundle's answers as JSON; the parent compares two bundles."""
    bundle, system, day, cases = arguments
    gold = _SearchGold.model_validate_json(_read(Path(cases) / SEARCH_GOLD))
    grouping = _GroupingCases.model_validate_json(_read(Path(cases) / GROUPING_CASES))
    answers = _answers(
        Path(bundle),
        tuple(case for case in gold.cases if case.system == system),
        tuple(case for case in grouping.cases if case.bundle == "real"),
        date.fromisoformat(day),
    )
    print(json.dumps(answers, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(_child(sys.argv[1:]))
