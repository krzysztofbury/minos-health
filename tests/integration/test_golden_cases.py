"""Reviewed golden grouping cases (#37) through the HTTP boundary.

Synthetic rule fixtures run in CI against the synthetic bundle. Cases derived
from the pinned real sources run only with MINOS_GROUPER_BUNDLE, since the real
bundle carries licensed generated data and is not part of CI.
"""
# pyright: reportAny=false

import json
import os
from pathlib import Path
from typing import Annotated, ClassVar, Literal

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, Field

from minos.http.app import create_app
from tests.fixtures.grouping_interface import grouping_bundle

CASES = Path(__file__).parents[1] / "golden" / "grouping-cases.json"
# not_candidate: no retained or unresolved fact reached the group's directional
# lists, so the answer lists it as not evaluated instead of judging it.
type Status = Literal["valid", "invalid", "undetermined", "not_candidate"]


class _Strict(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)


class Expected(_Strict):
    status: Literal[
        "evaluated", "unsupported_product_report", "product_catalog_unavailable"
    ]
    groups: dict[str, Status] = Field(default_factory=dict[str, Status])
    procedures: list[Literal["retained", "excluded", "unresolved"]] | None = None
    # Required: leaving it out must not silently mean "expect undetermined".
    ward_adjusted_days: int | None
    product_classes: list[Literal["W", "neutral", "unknown"]] | None = None
    # Prefix of the product catalog release that must have classified them.
    product_release: str | None = None


class Case(_Strict):
    id: Annotated[str, Field(pattern=r"^(synthetic|real)-[a-z0-9-]+$")]
    evidence: Literal[
        "public_verified", "derived_from_pinned_source", "synthetic_rule_fixture"
    ]
    interpretation: Literal["algorithm_text", "owner_decision_2026_10_09"]
    bundle: Literal["synthetic", "real"]
    sources: Annotated[list[str], Field(min_length=1)]
    rule_paths: Annotated[list[str], Field(min_length=1)]
    request: dict[str, object]
    expected: Expected


class Suite(_Strict):
    schema_: Annotated[Literal["minos.golden-grouping-cases.v1"], Field(alias="schema")]
    notes: list[str]
    cases: list[Case]


SUITE = Suite.model_validate(json.loads(CASES.read_text(encoding="utf-8")))


def _cases(bundle: str) -> list[Case]:
    return [case for case in SUITE.cases if case.bundle == bundle]


def test_case_identities_and_evidence_levels_are_consistent() -> None:
    ids = [case.id for case in SUITE.cases]
    assert len(set(ids)) == len(ids)
    for case in SUITE.cases:
        assert case.id.startswith(case.bundle + "-")
        # A synthetic bundle cannot verify a public or real-source claim.
        assert (case.bundle == "synthetic") == (
            case.evidence == "synthetic_rule_fixture"
        )


@pytest.fixture(scope="module")
def synthetic_bundle(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return grouping_bundle(tmp_path_factory.mktemp("golden"), products=True)


def _check(client: TestClient, case: Case) -> None:
    response = client.post("/api/v1/group", json=case.request)
    assert response.status_code == 200, response.text
    answer = response.json()
    expected = case.expected
    assert answer["status"] == expected.status, case.id
    if expected.status != "evaluated":
        assert answer["groups"] == [], case.id
        return
    statuses = {g["group"]: g["status"] for g in answer["groups"]}
    ranked = {r["group"] for r in answer["ranking"]}
    skipped = {
        group
        for entry in answer["not_evaluated"]
        for groups in entry["groups"].values()
        for group in groups
    }
    for group, status in expected.groups.items():
        if status == "not_candidate":
            assert group not in statuses and group in skipped, (case.id, group)
        else:
            assert statuses.get(group) == status, (case.id, group, statuses.get(group))
        assert (group in ranked) == (status == "valid"), (case.id, group)
    preparation = answer["preparation"]
    if expected.procedures is not None:
        assert [p["outcome"] for p in preparation["procedures"]] == expected.procedures
    assert preparation["ward_adjusted_days"] == expected.ward_adjusted_days, case.id
    if expected.product_classes is not None:
        assert [p["class"] for p in preparation["products"]] == expected.product_classes
    if expected.product_release is not None:
        release = answer["sources"]["product_catalogs"]["release_id"]
        assert release.startswith(expected.product_release), (case.id, release)


@pytest.mark.parametrize("case", _cases("synthetic"), ids=lambda case: case.id)
def test_synthetic_rule_fixture(synthetic_bundle: Path, case: Case) -> None:
    # A fresh app per case: the admission limiter is not under test here.
    with TestClient(create_app(synthetic_bundle)) as client:
        _check(client, case)


@pytest.mark.jgp_live
@pytest.mark.parametrize("case", _cases("real"), ids=lambda case: case.id)
def test_case_derived_from_pinned_sources(case: Case) -> None:
    bundle = os.environ.get("MINOS_GROUPER_BUNDLE")
    if not bundle:
        pytest.skip("real pinned conditions bundle opt-in is not configured")
    with TestClient(create_app(Path(bundle))) as client:
        _check(client, case)
