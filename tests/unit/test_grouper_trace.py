"""Trace evidence and counterfactual sensitivity, not an implementation mirror."""

from dataclasses import replace

import pytest

from minos.domain import jgp_conditions as c
from minos.grouper.conditions import evaluate_group
from minos.grouper.near_misses import near_misses
from tests.unit.test_grouper_conditions import (
    BOTH,
    PRINCIPAL,
    PROCEDURE_A,
    facts,
    node,
    rules,
)


def test_trace_reports_complete_conjunction_and_source_parameters() -> None:
    selected = rules(10, PROCEDURE_A, PRINCIPAL, BOTH)
    result = evaluate_group(selected, facts(("10.1", "retained", 3)))
    assert result.roots == (10,)
    assert [(e.node, e.verdict, e.matched_codes) for e in result.nodes] == [
        (1, "met", ("10.1",)),
        (3, "met", ("S06.0",)),
        (10, "met", ()),
    ]
    assert all(e.rule and e.rule.source_rows == (e.node,) for e in result.nodes)


def test_near_miss_requires_complete_group_to_change_not_just_one_failed_leaf() -> None:
    age = node(4, "patient_age", c.Age("lt", 18, "completed_years"))
    conjunction = node(10, "all", c.Expression((1, 4)))
    selected = rules(10, PROCEDURE_A, age, conjunction)
    reported = facts(("10.1", "retained", 3), years=40)
    result = evaluate_group(selected, reported)
    assert [(m.node, m.fact) for m in near_misses(selected, reported, result)] == [
        (4, "completed_years")
    ]
    no_procedure = replace(reported, procedures=())
    assert (
        near_misses(selected, no_procedure, evaluate_group(selected, no_procedure))
        == ()
    )
    unknown_age = replace(reported, years=None)
    assert (
        near_misses(selected, unknown_age, evaluate_group(selected, unknown_age)) == ()
    )


def test_near_miss_rechecks_routing_and_does_not_suggest_a_death_code() -> None:
    duration = node(
        4, "episode_duration", c.EpisodeDuration((c.DurationBound("lt", 2),))
    )
    selected = rules(4, duration, procedural=False)
    reported = facts(("10.1", "retained", 2), days=5)
    assert near_misses(selected, reported, evaluate_group(selected, reported)) == ()
    discharge = node(5, "discharge_mode", c.DischargeMode((9,)))
    selected = rules(5, discharge)
    reported = replace(facts(), discharge_mode=1)
    misses = near_misses(selected, reported, evaluate_group(selected, reported))
    assert len(misses) == 1 and misses[0].fact == "discharge_mode"
    # The domain returns the fact/condition identity, never a suggested code/value.
    assert not hasattr(misses[0], "suggested_value")


@pytest.mark.parametrize(
    "years,expected", [(10, "valid"), (40, "invalid"), (None, "undetermined")]
)
def test_every_consulted_branch_has_deterministic_trace(
    years: int | None, expected: str
) -> None:
    young = node(4, "patient_age", c.Age("lt", 18, "completed_years"))
    old = node(5, "patient_age", c.Age("gt", 65, "completed_years"))
    either = node(6, "any", c.Expression((4, 5)))
    root = node(10, "all", c.Expression((1, 3, 6)))
    selected = rules(10, PROCEDURE_A, PRINCIPAL, young, old, either, root)
    reported = facts(("10.1", "retained", 3), years=years)
    answer = evaluate_group(selected, reported)
    assert answer.status == expected
    assert [event.node for event in answer.nodes] == [1, 3, 4, 5, 6, 10]
    assert answer.nodes[0].consulted == (("procedures", 0),)
    assert answer.nodes[1].consulted == (("principal_diagnosis", 0),)
    assert answer == evaluate_group(
        replace(selected, nodes=tuple(reversed(selected.nodes))), reported
    )
