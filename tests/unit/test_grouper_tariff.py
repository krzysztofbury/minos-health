"""Final points (#35) under the owner rules of 2026-10-07, synthetic mappings."""

from datetime import date

import pytest

from minos.domain.grouper_rules import BasePoints, FinalPoints
from minos.grouper.tariff import final_group_points

OCTOBER = date(2026, 10, 2)


def base(hundredths: int = 400000) -> BasePoints:
    return BasePoints(True, "standard_value", "hospitalization", hundredths)


def mapping(
    symbol: str, coefficient: str, citation: str | None = None
) -> dict[str, object]:
    whole, _, fraction = coefficient.partition(".")
    return {
        "symbol": symbol,
        "source_citation": citation or f"clause {symbol}",
        "coefficient_millionths": int(whole) * 1_000_000 + int(fraction.ljust(6, "0")),
        "provenance": "manual_legal_overlay"
        if symbol.startswith("manual")
        else "MODTAR",
    }


def points(
    mappings: list[dict[str, object]],
    declared: set[str],
    *,
    amount: int = 400000,
    years: int | None = 40,
    day: date = OCTOBER,
    group: str = "G01",
) -> FinalPoints:
    return final_group_points(
        "1a", group, base(amount), mappings, frozenset(declared), years, day
    )


@pytest.mark.parametrize(
    ("amount", "coefficients", "total"),
    [
        (400000, ("1.2",), 480000),
        (400000, ("1.2", "1.1"), 528000),  # multiplied, not added (528000 vs 520000)
        (100001, ("1.025",), 102501),  # 102501.025 rounds down
        (100001, ("1.5",), 150002),  # 150001.5 rounds half-up
    ],
)
def test_declared_coefficients_multiply_and_round_half_up_once(
    amount: int, coefficients: tuple[str, ...], total: int
) -> None:
    mappings = [mapping(f"S{i}", value) for i, value in enumerate(coefficients)]
    declared = {f"S{i}" for i in range(len(coefficients))}
    result = points(mappings, declared, amount=amount)
    assert result.total_hundredths == total


def test_undeclared_modifier_is_possible_with_its_own_gain_never_applied() -> None:
    result = points([mapping("17.18", "1.17")], set())
    assert result.total_hundredths == 400000
    (decision,) = result.decisions
    assert (decision.outcome, decision.would_add_hundredths) == ("possible", 68000)


@pytest.mark.parametrize(
    ("symbol", "years", "outcome"),
    [
        ("17.10", 2, "applied"),
        ("17.10", 3, "rejected"),  # "do ukończenia 3 r.ż."
        ("17.11", 3, "applied"),  # "od rozpoczęcia 4. r.ż."
        ("17.11", 8, "applied"),
        ("17.11", 9, "rejected"),  # "do ukończenia 9. r.ż."
        ("17.1", 17, "applied"),
        ("17.1", 18, "rejected"),  # "poniżej 18 r.ż."
        ("17.1", None, "undetermined"),
        ("M5", 90, "applied"),  # no patient condition in the clause
    ],
)
def test_clause_age_conditions_are_checked_from_the_encounter(
    symbol: str, years: int | None, outcome: str
) -> None:
    result = points([mapping(symbol, "1.2")], {symbol}, years=years)
    assert result.decisions[0].outcome == outcome
    assert result.total_hundredths == (480000 if outcome == "applied" else 400000)


def test_declared_alternatives_from_one_clause_give_no_total() -> None:
    tiers = [
        mapping("N01-N02_1", "0.9", "§ 17 ust. 19"),
        mapping("N01-N02_4", "1.1", "§ 17 ust. 19"),
    ]
    result = points(tiers, {"N01-N02_1", "N01-N02_4"})
    assert result.total_hundredths is None
    assert "declared alternatives from one clause" in result.notes


def test_unmapped_declaration_is_noted_and_never_applied() -> None:
    result = points([mapping("M5", "1.1")], {"M14"})
    assert result.total_hundredths == 400000
    assert "declared M14 is not mapped to G01 on this date" in result.notes


def test_food_ratio_and_known_risks_are_visible_notes() -> None:
    august = points([], set(), day=date(2026, 8, 31))
    assert any("food-standard ratio" in note for note in august.notes)
    assert not any("food" in note for note in points([], set()).notes)
    risky = points([mapping("M5", "1.1")], set(), group="DU58")
    assert any(note.startswith("#129") for note in risky.notes)


def test_undetermined_base_points_give_no_final_total() -> None:
    result = final_group_points(
        "1a",
        "G01",
        BasePoints(False, "duration_unresolved"),
        [mapping("M5", "1.1")],
        frozenset({"M5"}),
        40,
        OCTOBER,
    )
    assert result.total_hundredths is None
