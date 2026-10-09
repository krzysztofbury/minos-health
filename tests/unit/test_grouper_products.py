"""Type W settlement products in preparation (#145, decisions 2026-10-09).

Expected values are hand-computed from the algorithm: a 1w product excludes
procedures dated within its start and end dates inclusive, and each W product
subtracts end - start from the duration (owner interpretation).
"""

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st

from minos.domain.encounter import (
    Encounter,
    Procedure,
    ProductReporting,
    SettlementProduct,
    WardStay,
)
from minos.domain.grouper import Outcome, PreparationResult, ProductClassification
from minos.grouper.products import ProductContext, merge, procedure_effect
from tests.fixtures.grouper import encounter, key, registry

W = "5.52.01.0000001"
W2 = "5.52.01.0000002"
NEUTRAL = "5.53.01.0000003"
UNKNOWN = "5.99.99.9999999"
CLASSIFICATION = ProductClassification(
    "products-act", "catalog-act", frozenset({W, W2}), frozenset({NEUTRAL})
)


def _day(day: int) -> date:
    return date(2026, 10, day)


def _dated() -> Encounter:
    """Episode 1-10 October (9 days); 00.0 has rank 0, the rest are ranked."""
    return replace(
        encounter(),
        procedures=(
            Procedure("00.1", _day(1), "main"),
            Procedure("00.2", _day(2), "main"),
            Procedure("00.3", _day(4), "main"),
            Procedure("00.4", _day(5), "main"),
        ),
        product_reporting="complete",
    )


def _prepare(
    facts: Encounter,
    *products: SettlementProduct,
    classification: ProductClassification | None = CLASSIFICATION,
    reporting: ProductReporting = "complete",
) -> PreparationResult:
    return registry().execute(
        key(),
        replace(facts, settlement_products=products, product_reporting=reporting),
        classification,
    )


def _outcomes(result: PreparationResult) -> list[Outcome]:
    return [p.outcome for p in result.procedures]


def test_w_excludes_procedures_on_its_inclusive_dates_and_subtracts_its_length() -> (
    None
):
    result = _prepare(_dated(), SettlementProduct(W, _day(2), _day(4)))
    assert _outcomes(result) == ["retained", "excluded", "excluded", "retained"]
    assert result.ward_adjusted_days == 9 - 2
    assert result.product_binding == "bound"
    assert result.product_release_id == "products-act"
    assert result.product_classes == ("W",)
    excluded = [p for p in result.procedures if p.outcome == "excluded"]
    assert all(p.rank is None for p in excluded)


def test_neutral_products_and_an_empty_report_change_nothing() -> None:
    plain = _prepare(_dated())
    neutral = _prepare(_dated(), SettlementProduct(NEUTRAL, _day(1), _day(10)))
    assert _outcomes(plain) == _outcomes(neutral) == ["retained"] * 4
    assert plain.ward_adjusted_days == neutral.ward_adjusted_days == 9
    assert neutral.product_classes == ("neutral",)


def test_unknown_product_leaves_covered_procedures_and_duration_unresolved() -> None:
    result = _prepare(_dated(), SettlementProduct(UNKNOWN, _day(4), _day(5)))
    assert _outcomes(result) == ["retained", "retained", "unresolved", "unresolved"]
    assert result.ward_adjusted_days is None
    assert result.product_classes == ("unknown",)


@pytest.mark.parametrize(
    ("product", "outcomes"),
    [
        (SettlementProduct(UNKNOWN), ["unresolved"] * 4),
        # Without an end, a W from day 2 cannot cover the day-1 procedure.
        (SettlementProduct(W, _day(2)), ["retained"] + ["unresolved"] * 3),
        (SettlementProduct(W, None, _day(4)), ["unresolved"] * 3 + ["retained"]),
    ],
)
def test_a_product_without_both_dates_covers_only_the_days_it_could_reach(
    product: SettlementProduct, outcomes: list[Outcome]
) -> None:
    result = _prepare(_dated(), product)
    assert _outcomes(result) == outcomes
    assert result.ward_adjusted_days is None


def test_undated_procedure_is_bounded_by_its_stay_then_by_the_episode() -> None:
    stays = (
        WardStay("first", "4000", _day(1), _day(4)),
        WardStay("second", "4000", _day(4), _day(10)),
    )
    facts = replace(
        _dated(),
        stays=stays,
        principal_diagnosis=replace(encounter().principal_diagnosis, stay_id="first"),
        secondary_diagnoses=(),
        procedures=(
            Procedure("00.1", None, "first"),
            Procedure("00.2", None, "second"),
            Procedure("00.3", None, None),
        ),
    )
    result = _prepare(facts, SettlementProduct(W, _day(1), _day(4)))
    # Stay "first" lies wholly inside the W product; "second" and the whole
    # episode extend past it.
    assert _outcomes(result) == ["excluded", "unresolved", "unresolved"]


@pytest.mark.parametrize(
    ("second", "days"),
    [
        ((_day(4), _day(6)), None),  # shares a day
        ((_day(5), _day(6)), None),  # adjacent day
        ((_day(7), _day(8)), 9 - 2 - 1),  # disjoint: each subtracted
    ],
)
def test_overlapping_or_adjacent_w_products_exclude_their_union(
    second: tuple[date, date], days: int | None
) -> None:
    facts = replace(
        _dated(),
        procedures=(*_dated().procedures, Procedure("00.1", _day(6), "main")),
    )
    result = _prepare(
        facts,
        SettlementProduct(W, _day(2), _day(4)),
        SettlementProduct(W2, *second),
    )
    union_covers_day_6 = second[0] <= _day(6)
    assert result.ward_adjusted_days == days
    assert _outcomes(result) == [
        "retained",
        "excluded" if union_covers_day_6 else "retained",
        "excluded",
        "excluded",
        "excluded" if second[0] <= _day(5) else "retained",
    ]


def _ward_excluded(ward_from: int, *procedures: Procedure) -> Encounter:
    """Department 1220 with service 4 is an excluded stay from `ward_from`."""
    return replace(
        _dated(),
        procedures=procedures or _dated().procedures,
        stays=(
            WardStay("main", "4000", _day(1), _day(ward_from)),
            WardStay("icu", "1220", _day(ward_from), _day(10), "4"),
        ),
    )


@pytest.mark.parametrize(
    ("product", "days"),
    [
        ((_day(2), _day(3)), 9 - 3 - 1),  # disjoint from the 7-10 ward stay
        ((_day(5), _day(6)), None),  # adjacent to it
        ((_day(6), _day(8)), None),  # overlapping it
    ],
)
def test_w_next_to_an_excluded_ward_stay_leaves_the_duration_undetermined(
    product: tuple[date, date], days: int | None
) -> None:
    result = _prepare(_ward_excluded(7), SettlementProduct(W, *product))
    assert result.ward_adjusted_days == days


def test_ward_exclusion_outranks_unresolved_and_w_outranks_unresolved_ward() -> None:
    icu_procedure = _ward_excluded(4, Procedure("00.1", _day(8), "icu"))
    assert _outcomes(_prepare(icu_procedure, SettlementProduct(UNKNOWN))) == [
        "excluded"
    ]
    unattributed = replace(
        _dated(),
        stays=(WardStay("main", "4000", _day(1), _day(8)),),
        procedures=(Procedure("00.1", _day(9), None),),
    )
    # Incomplete stay coverage leaves the procedure unresolved before products.
    assert _outcomes(_prepare(unattributed)) == ["unresolved"]
    assert _outcomes(
        _prepare(unattributed, SettlementProduct(W, _day(9), _day(10)))
    ) == ["excluded"]


def test_w_covering_the_whole_episode_leaves_no_positive_duration() -> None:
    result = _prepare(_dated(), SettlementProduct(W, _day(1), _day(10)))
    assert _outcomes(result) == ["excluded"] * 4
    assert result.ward_adjusted_days is None


def test_same_day_w_product_leaves_the_duration_undetermined() -> None:
    facts = replace(
        _dated(),
        discharged_on=_day(1),
        stays=(WardStay("main", "4000", _day(1), _day(1)),),
        procedures=(Procedure("00.1", _day(1), "main"),),
    )
    assert _prepare(facts).ward_adjusted_days == 1
    result = _prepare(facts, SettlementProduct(W, _day(1), _day(1)))
    assert _outcomes(result) == ["excluded"]
    assert result.ward_adjusted_days is None


def test_products_are_unbound_without_a_classification_or_a_complete_report() -> None:
    product = SettlementProduct(W, _day(2), _day(4))
    for result in (
        _prepare(_dated(), product, classification=None),
        _prepare(_dated(), product, reporting="unknown"),
    ):
        assert result.product_binding == "unbound"
        assert result.product_release_id is None
        assert result.product_classes == ()
        # Unclassified, the product could cover any day: nothing is retained.
        assert _outcomes(result) == ["unresolved"] * 4
        assert result.ward_adjusted_days is None


_DAYS = st.integers(min_value=1, max_value=30)


@st.composite
def _intervals(draw: st.DrawFn) -> list[tuple[date, date]]:
    pairs = draw(st.lists(st.tuples(_DAYS, _DAYS), max_size=6))
    return [(_day(min(a, b)), _day(max(a, b))) for a, b in pairs]


@given(_intervals(), _intervals(), st.tuples(_DAYS, _DAYS))
def test_procedure_effect_matches_a_day_by_day_reading(
    w: list[tuple[date, date]],
    uncertain: list[tuple[date, date]],
    bounds: tuple[int, int],
) -> None:
    possible = (_day(min(bounds)), _day(max(bounds)))
    context = ProductContext((), tuple(w), merge(w), merge(uncertain), False)
    days = [_day(d) for d in range(possible[0].day, possible[1].day + 1)]

    def inside(day: date, intervals: list[tuple[date, date]]) -> bool:
        return any(start <= day <= end for start, end in intervals)

    if all(inside(day, w) for day in days):
        expected = "excluded"
    elif any(inside(day, w + uncertain) for day in days):
        expected = "unresolved"
    else:
        expected = None
    assert procedure_effect(context, possible) == expected
