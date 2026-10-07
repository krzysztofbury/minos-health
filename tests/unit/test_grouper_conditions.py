"""Approved attachment 9 subset (#35): one test per rule, synthetic rules."""

from dataclasses import replace

import pytest

from minos.domain import jgp_conditions as c
from minos.domain.encounter import ProductReporting, SettlementProduct
from minos.domain.grouper import Outcome
from minos.domain.grouper_rules import GroupRules, SelectorCodes
from minos.grouper.conditions import Facts, evaluate_group, facts_from
from tests.fixtures.grouper import encounter, key, registry

LIST_A = SelectorCodes(1, frozenset({"10.1", "10.2"}), ())
LIST_B = SelectorCodes(2, frozenset({"10.2", "20.1"}), ())
DIAGNOSES = SelectorCodes(3, frozenset({"S06.0"}), ())
FAMILY = SelectorCodes(4, frozenset(), ("J95",))


def node(identity: int, kind: c.Kind, payload: c.Payload) -> c.Condition:
    return c.Condition(identity, "G01", kind, payload, (identity,), "proposed", None)


def rules(
    root: int,
    *nodes: c.Condition,
    procedural: bool = True,
    code: str = "G01",
    catalog_rows: int = 1,
) -> GroupRules:
    return GroupRules(
        "1a",
        code,
        catalog_rows,
        procedural,
        (root,),
        nodes,
        (LIST_A, LIST_B, DIAGNOSES, FAMILY),
    )


def facts(
    *procedures: tuple[str, Outcome, int | None],
    principal: tuple[str, Outcome] = ("S06.0", "retained"),
    years: int | None = 40,
    days: int | None = 5,
) -> Facts:
    return Facts(principal, (), procedures, years, days)


PROCEDURE_A = node(1, "procedure_presence", c.Presence(1, None))
PROCEDURE_B = node(2, "procedure_presence", c.Presence(2, None))
PRINCIPAL = node(3, "diagnosis_presence", c.Presence(3, "principal"))
BOTH = node(10, "all", c.Expression((1, 3)))


@pytest.mark.parametrize(
    ("procedure", "status", "conditions"),
    [
        (("10.1", "retained", 3), "valid", "met"),
        (("10.1", "excluded", 3), "invalid", "not_met"),
        (("10.1", "unresolved", None), "undetermined", "undetermined"),
        (("99.9", "retained", 3), "invalid", "not_met"),
    ],
)
def test_procedure_and_principal_conjunction_is_three_valued(
    procedure: tuple[str, Outcome, int | None], status: str, conditions: str
) -> None:
    result = evaluate_group(rules(10, PROCEDURE_A, PRINCIPAL, BOTH), facts(procedure))
    assert (result.status, result.conditions) == (status, conditions)
    assert [n.node for n in result.nodes] == [1, 3, 10]


@pytest.mark.parametrize(
    ("rank", "days", "verdict", "reason"),
    [
        (4, 5, "met", "list_member"),
        (1, 5, "undetermined", "rank_1_role_unreviewed"),  # paragraph 76
        (2, 1, "met", "list_member"),  # paragraph 77: stays under 2 days
        (2, 2, "not_met", "no_list_member"),
        (2, None, "undetermined", "rank_2_duration_unknown"),
    ],
)
def test_procedure_rank_controls_whether_a_list_member_directs(
    rank: int, days: int | None, verdict: str, reason: str
) -> None:
    result = evaluate_group(
        rules(1, PROCEDURE_A), facts(("10.1", "retained", rank), days=days)
    )
    assert (result.conditions, result.nodes[0].reason) == (verdict, reason)


def test_two_required_procedures_never_share_one_code() -> None:
    pair = node(10, "all", c.Expression((1, 2)))
    shared = evaluate_group(
        rules(10, PROCEDURE_A, PROCEDURE_B, pair), facts(("10.2", "retained", 3))
    )
    assert (shared.conditions, shared.nodes[-1].reason) == (
        "not_met",
        "same_fact_counted_twice",
    )
    distinct = evaluate_group(
        rules(10, PROCEDURE_A, PROCEDURE_B, pair),
        facts(("10.2", "retained", 3), ("20.1", "retained", 3)),
    )
    assert distinct.conditions == "met"
    # Paragraph 134: E11 and N24 may count one code on different dates.
    dated = evaluate_group(
        rules(10, PROCEDURE_A, PROCEDURE_B, pair, code="E11"),
        facts(("10.2", "retained", 3)),
    )
    assert dated.nodes[-1].reason == "distinct_date_rule_unreviewed"
    # A fact a nested expression might reuse is not paired by guesswork.
    either = node(11, "any", c.Expression((2,)))
    nested = node(12, "all", c.Expression((1, 11)))
    result = evaluate_group(
        rules(12, PROCEDURE_A, PROCEDURE_B, either, nested),
        facts(("10.2", "retained", 3)),
    )
    assert (result.conditions, result.nodes[-1].reason) == (
        "undetermined",
        "shared_fact_unreviewed",
    )


def test_any_is_met_by_one_child_and_unknown_otherwise() -> None:
    either = node(10, "any", c.Expression((1, 2)))
    assert (
        evaluate_group(
            rules(10, PROCEDURE_A, PROCEDURE_B, either), facts(("20.1", "retained", 3))
        ).conditions
        == "met"
    )
    assert (
        evaluate_group(
            rules(10, PROCEDURE_A, PROCEDURE_B, either),
            facts(("10.1", "unresolved", None)),
        ).conditions
        == "undetermined"
    )
    assert (
        evaluate_group(rules(10, PROCEDURE_A, PROCEDURE_B, either), facts()).conditions
        == "not_met"
    )


@pytest.mark.parametrize(
    ("age", "years", "verdict"),
    [
        (c.Age("lt", 18, "completed_years"), 17, "met"),
        (c.Age("lt", 18, "completed_years"), 18, "not_met"),
        (c.Age("gt", 17, "completed_years"), None, "undetermined"),
        (c.Age("lt", 6, "elapsed_weeks"), 0, "undetermined"),
    ],
)
def test_age_compares_completed_years_only(
    age: c.Age, years: int | None, verdict: str
) -> None:
    result = evaluate_group(rules(5, node(5, "patient_age", age)), facts(years=years))
    assert result.conditions == verdict


@pytest.mark.parametrize(
    ("days", "verdict"), [(3, "met"), (2, "not_met"), (None, "undetermined")]
)
def test_stay_length_needs_a_product_free_known_duration(
    days: int | None, verdict: str
) -> None:
    bound = c.EpisodeDuration((c.DurationBound("gt", 2),))
    result = evaluate_group(
        rules(6, node(6, "episode_duration", bound)), facts(days=days)
    )
    assert result.conditions == verdict


def test_family_and_unapproved_kinds_stay_undetermined() -> None:
    family = node(7, "diagnosis_presence", c.Presence(4, None))
    assert (
        evaluate_group(
            rules(7, family), facts(principal=("J95.8", "retained"))
        ).conditions
        == "undetermined"
    )
    negated = node(8, "not", c.Expression((1,)))
    assert (
        evaluate_group(rules(8, PROCEDURE_A, negated), facts()).nodes[-1].reason
        == "not_executable:not"
    )
    unsupported = replace(PROCEDURE_A, status="unsupported")
    assert (
        evaluate_group(rules(1, unsupported), facts(("10.1", "retained", 3))).conditions
        == "undetermined"
    )


@pytest.mark.parametrize(
    ("procedures", "days", "routing"),
    [
        ((), 5, "met"),
        ((("10.1", "retained", 1),), 5, "met"),  # paragraph 109: rank < 2
        ((("10.1", "retained", 2),), 5, "met"),  # rank 2 and over 1 day
        ((("10.1", "retained", 2),), 1, "not_met"),
        ((("10.1", "retained", 3),), 5, "undetermined"),  # paragraph 110
        ((("10.1", "excluded", 3),), 5, "met"),
        ((("10.1", "unresolved", None),), 5, "undetermined"),
    ],
)
def test_conservative_group_routing_follows_paragraphs_107_to_110(
    procedures: tuple[tuple[str, Outcome, int | None], ...], days: int, routing: str
) -> None:
    result = evaluate_group(
        rules(3, PRINCIPAL, procedural=False), facts(*procedures, days=days)
    )
    assert result.routing == routing
    marker = evaluate_group(
        rules(3, PRINCIPAL, procedural=False, code="N24"),
        facts(("10.1", "retained", 3)),
    )
    assert marker.routing == "met"  # paragraph 111


def test_catalog_and_root_ambiguity_never_produce_a_valid_group() -> None:
    absent = evaluate_group(
        rules(1, PROCEDURE_A, catalog_rows=0), facts(("10.1", "retained", 3))
    )
    assert (absent.status, absent.reasons) == ("invalid", ("not_in_governing_catalog",))
    twice = evaluate_group(
        rules(1, PROCEDURE_A, catalog_rows=2), facts(("10.1", "retained", 3))
    )
    assert twice.status == "undetermined"
    roots = replace(rules(1, PROCEDURE_A, PROCEDURE_B), roots=(1, 2))
    assert (
        evaluate_group(roots, facts(("10.1", "retained", 3))).reasons[0]
        == "multiple_roots_unreviewed"
    )


@pytest.mark.parametrize(
    ("reporting", "products", "days"),
    [
        ("unknown", (), None),
        ("complete", (), 9),
        ("complete", ("5.52.01.0001363",), None),
    ],
)
def test_stay_length_is_usable_only_with_a_complete_product_free_report(
    reporting: ProductReporting, products: tuple[str, ...], days: int | None
) -> None:
    # Settlement products can shorten the algorithm duration (#145), so an
    # omitted report or any product leaves duration conditions undetermined.
    prepared = registry().execute(
        key(),
        replace(
            encounter(),
            product_reporting=reporting,
            settlement_products=tuple(SettlementProduct(code) for code in products),
        ),
    )
    assert prepared.ward_adjusted_days == 9
    assert facts_from(prepared).days == days
