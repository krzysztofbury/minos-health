"""Grouper readers and the #34 pipeline over a synthetic bundle's real schema."""

import json
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.domain import jgp_conditions as c
from minos.domain.encounter import Diagnosis, Encounter, Procedure, WardStay
from minos.domain.grouper import GrouperError
from minos.domain.grouper_candidates import CandidateResult, GroupingSources
from minos.domain.grouper_rules import (
    GroupingResult,
    ModifierBindingUnbound,
    ModifierEligibilityUnsupported,
)
from minos.grouper.pipeline import derive_candidates, evaluate_candidates, final_points
from minos.grouper.szp_2026 import ALGORITHM_SHA256
from minos.ingest.jgp_conditions_codec import encode
from minos.ingest.reference_artifact import build_reference_artifact
from minos.store.grouper_bundle import directional_index, grouping_sources
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.reference import open_reference_bundle
from tests.fixtures.grouper import encounter, key, registry
from tests.integration.test_jgp_condition_consumers import consumer_bundle
from tests.integration.test_jgp_modifier_binding import binding_dataset

RELEASE = "conditions-current"


def _insert_condition(connection: sqlite3.Connection, node: c.Condition) -> None:
    _ = connection.execute(
        """INSERT INTO group_condition VALUES (?, '1a', ?, ?, ?, ?, ?, ?)""",
        (
            RELEASE,
            node.identity,
            node.identity,
            node.group,
            node.kind,
            node.status,
            encode(node),
        ),
    )


@pytest.fixture
def edited(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """A writable copy: union, family and negated nodes plus an SZP algorithm."""
    bundle = consumer_bundle(tmp_path)
    copy = tmp_path / "edited.db"
    _ = shutil.copyfile(bundle / "minos.db", copy)
    copy.chmod(0o600)
    connection = sqlite3.connect(copy)
    selectors = (
        c.Selector(10, c.UnionSelector((1,), "icd9-pl"), (14,)),
        c.Selector(11, c.FamilySelector("E10", "icd10-pl"), (14,)),
    )
    for selector in selectors:
        _ = connection.execute(
            "INSERT INTO condition_selector VALUES (?, '1a', ?, ?)",
            (RELEASE, selector.identity, encode(selector)),
        )
    nodes = (
        c.Condition(20, "N09", "procedure_presence", c.Presence(10, None), (14,), "proposed", None),
        c.Condition(21, "N09", "diagnosis_presence", c.Presence(11, "secondary"), (14,), "proposed", None),
        c.Condition(22, "N09", "procedure_presence", c.Presence(1, None), (14,), "proposed", None),
        c.Condition(23, "N09", "not", c.Expression((22,)), (14,), "proposed", None),
    )  # fmt: skip
    for node in nodes:
        _insert_condition(connection, node)
    pins = {
        "algorithm": {"sha256": ALGORITHM_SHA256},
        "ranks": {"sha256": "2" * 64},
        "specialties": {"sha256": "3" * 64},
    }
    _ = connection.execute(
        """INSERT INTO algorithm_release VALUES ('synthetic-komunikat',
        'icd9-czs-synthetic-v1', '1.1.2026', '2026-01-01', 'wersja 1.1.2026', 8,
        'reference-only', ?)""",
        (json.dumps(pins),),
    )
    for row, (code, rank) in enumerate((("100.01", 3), ("99.97910", 0)), 4):
        _ = connection.execute(
            "INSERT INTO procedure_rank VALUES ('synthetic-komunikat', 'SZP', ?, 'synthetic', ?, 'SZP 2026', ?)",
            (code, rank, row),
        )
    _ = connection.execute(
        """INSERT INTO department_specialty VALUES ('synthetic-komunikat', 'SZP',
        '4260', 'WSZYSTKIE', 'WSZYSTKIE', 'SZP 2026', 4)"""
    )
    connection.commit()
    yield connection
    connection.close()


def test_bundle_index_reads_lists_roles_and_sheet_scope(tmp_path: Path) -> None:
    with open_reference_bundle(consumer_bundle(tmp_path)) as (connection, _):
        first = directional_index(connection, RELEASE, "1a")
        emergency = directional_index(connection, RELEASE, "1ae")
    groups = {group.code: group for group in first.groups}
    assert [
        (r.node, r.kind, r.role, sorted(r.codes), r.list_ids)
        for r in groups["A01"].requirements
    ] == [
        (1, "procedure", "any", ["100.01", "99.97910"], (1,)),
        (2, "diagnosis", "principal", ["E11.0", "G60.4"], (2,)),
    ]
    # Program-membership-only groups have no directional list to reach.
    assert groups["N09"].requirements == groups["N11"].requirements == ()
    # 1ae lists are scoped to their sheet even where list identities repeat.
    assert [(g.code, sorted(g.requirements[0].codes)) for g in emergency.groups] == [
        ("AU01", ["G60.4"])
    ]


def test_index_follows_unions_keeps_families_and_drops_negated_lists(
    edited: sqlite3.Connection,
) -> None:
    groups = {g.code: g for g in directional_index(edited, RELEASE, "1a").groups}
    assert [
        (r.node, r.role, sorted(r.codes), r.families)
        for r in groups["N09"].requirements
    ] == [(20, "any", ["100.01", "99.97910"], ()), (21, "any", [], ("E10",))]


def _episode(*procedures: Procedure) -> Encounter:
    return Encounter(
        date(2026, 10, 1),
        date(2026, 10, 5),
        "hospitalization",
        Diagnosis("G60.4", hospitalization_level=True),
        (
            WardStay("ward", "4000", date(2026, 10, 1), date(2026, 10, 3)),
            WardStay("icu", "4260", date(2026, 10, 3), date(2026, 10, 5)),
        ),
        (Diagnosis("E10.9", "ward"),),
        procedures,
        birth_date=date(1980, 1, 1),
    )


def test_pipeline_prepares_and_derives_from_one_bundle(
    edited: sqlite3.Connection,
) -> None:
    facts = _episode(
        Procedure("100.01", date(2026, 10, 4), "icu"),
        Procedure("99.97910", date(2026, 10, 2), "ward"),
    )
    result = derive_candidates(
        edited,
        "synthetic-dataset",
        facts,
        service_date=date(2026, 10, 5),
        reporting_month=date(2026, 10, 1),
    )
    assert result.sources.catalog_release_id == "jgp-catalog-synthetic"
    assert result.sources.characteristics_release_id == RELEASE
    assert result.sources.algorithm_release_id == "synthetic-komunikat"
    # ICU-excluded 100.01 and rank-0 99.97910 cannot reach a list; the
    # hospitalization-level principal and the E10 family still can.
    assert [
        (x.catalog, x.group, x.basis, x.matched_nodes) for x in result.candidates
    ] == [
        ("1a", "A01", "retained_fact", (2,)),
        ("1a", "N09", "unresolved_fact", (21,)),
        ("1ae", "AU01", "retained_fact", (1,)),
    ]
    assert result.underivable == (("1a", "N11", "no_directional_requirement"),)
    assert result.preparation.ward_adjusted_days == 2
    reversed_facts = _episode(*reversed(facts.procedures))
    assert (
        derive_candidates(
            edited,
            "synthetic-dataset",
            reversed_facts,
            service_date=date(2026, 10, 5),
            reporting_month=date(2026, 10, 1),
        ).candidates
        == result.candidates
    )


def test_composite_sources_must_agree(edited: sqlite3.Connection) -> None:
    from minos.store.grouper_bundle import load_bundle_algorithm

    algorithm = load_bundle_algorithm(edited)
    sources = grouping_sources(edited, "d", date(2026, 10, 5), algorithm)
    assert sources.icd9_release_id == "icd9-czs-synthetic-v1"
    _ = edited.execute("UPDATE algorithm_release SET icd9_release_id='icd9-other'")
    with pytest.raises(GrouperError, match="ICD-9 releases disagree"):
        _ = grouping_sources(edited, "d", date(2026, 10, 5), algorithm)
    _ = edited.execute(
        """UPDATE characteristics_release SET manifest_json =
        json_set(manifest_json, '$.catalog_release_id', 'jgp-catalog-other')"""
    )
    with pytest.raises(GrouperError, match="catalog releases disagree"):
        _ = grouping_sources(edited, "d", date(2026, 10, 5), algorithm)


@pytest.mark.parametrize(
    "service_date,message",
    [(date(2026, 9, 30), "outside the episode"), (date(2026, 10, 5), "exactly one")],
)
def test_pipeline_refuses_unbound_requests(
    tmp_path: Path, service_date: date, message: str
) -> None:
    with open_reference_bundle(consumer_bundle(tmp_path)) as (connection, _):
        with pytest.raises(GrouperError, match=message):
            _ = derive_candidates(
                connection,
                "synthetic-dataset",
                _episode(),
                service_date=service_date,
                reporting_month=date(2026, 10, 1),
            )


@pytest.mark.parametrize(
    ("stay", "status"),
    [("ward", "valid"), ("icu", "invalid")],
)
def test_pipeline_evaluates_candidates_against_the_governing_catalog(
    edited: sqlite3.Connection, stay: str, status: str
) -> None:
    # A01 needs a procedure from list 1 and a principal diagnosis from list 2.
    facts = replace(
        _episode(Procedure("100.01", date(2026, 10, 2 if stay == "ward" else 4), stay)),
        product_reporting="complete",
    )
    result = evaluate_candidates(
        edited,
        "synthetic-dataset",
        facts,
        service_date=date(2026, 10, 5),
        reporting_month=date(2026, 10, 1),
    )
    by_group = {(e.catalog, e.group): e for e in result.evaluations}
    assert by_group[("1a", "A01")].status == status
    assert result.ranking == ((("1a", "A01"),) if status == "valid" else ())
    a01 = result.points[[c.group for c in result.candidates.candidates].index("A01")]
    assert a01.determined and a01.measure == "hospitalization"
    assert result.scope == "base_points_without_modifiers"
    # AU01 and N09 have attachment 9 rows but no catalog row on the date.
    for group in (("1ae", "AU01"), ("1a", "N09")):
        assert by_group[group].status == "invalid"
        assert by_group[group].reasons == ("not_in_governing_catalog",)


def test_final_points_refuse_an_unbound_modifier_binding(
    edited: sqlite3.Connection,
) -> None:
    result = evaluate_candidates(
        edited,
        "synthetic-dataset",
        _episode(Procedure("100.01", date(2026, 10, 2), "ward")),
        service_date=date(2026, 10, 5),
        reporting_month=date(2026, 10, 1),
    )
    assert result.points[0].kind == "catalog_base_points_without_modifiers"
    with pytest.raises(ModifierBindingUnbound):
        final_points(edited, result)


@pytest.mark.parametrize(
    ("service_date", "error"),
    [
        (date(2026, 10, 1), ModifierEligibilityUnsupported),
        (date(2027, 1, 1), ModifierBindingUnbound),
    ],
)
def test_reviewed_binding_still_refuses_unevaluated_eligibility(
    tmp_path: Path, service_date: date, error: type[Exception]
) -> None:
    # A reviewed binding is necessary, not sufficient: eligibility facts are
    # not evaluated, which is distinct from "no modifiers apply".
    bundle = tmp_path / "bound"
    _ = build_reference_artifact(binding_dataset(tmp_path), tmp_path, bundle)
    with open_reference_bundle(bundle) as (connection, _):
        catalog = cast(
            str, resolve_ruleset(connection, service_date, None)["release_id"]
        )
        sources = GroupingSources(
            "d", service_date, catalog, "characteristics", "icd9", "icd10", "algorithm"
        )
        result = GroupingResult(
            CandidateResult(sources, registry().execute(key(), encounter()), (), ()),
            (),
            (),
            (),
        )
        with pytest.raises(error):
            final_points(connection, result)
