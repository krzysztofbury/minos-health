"""Catalog base points per settlement mode (#35), synthetic catalog rows."""

import pytest

from minos.domain.encounter import HospitalizationType
from minos.domain.grouper_rules import GroupRules, StayFacts
from minos.grouper.points import base_points

FULL = (
    ("hospitalization", 400000),
    ("planned_hospitalization", 370000),
    ("one_day_treatment", 340000),
    ("financed_stay", 7),
    ("same_day_discharge", 300000),
    ("one_day_stay", 310000),
    ("two_day_stay", 320000),
    ("extra_stay_day", 10000),
)


def rules(
    values: tuple[tuple[str, int], ...] = FULL,
    *,
    catalog: str = "1a",
    catalog_rows: int = 1,
) -> GroupRules:
    return GroupRules(
        "1ae" if catalog == "1ae" else "1a",
        "G01",
        catalog_rows,
        True,
        (),
        (),
        (),
        10 if catalog_rows == 1 else None,
        values,
    )


def stay(
    kind: HospitalizationType = "hospitalization",
    *,
    days: int | None = 5,
    same_day: bool = False,
) -> StayFacts:
    return StayFacts(kind, same_day, days)


@pytest.mark.parametrize(
    ("facts", "measure", "total", "extra_days"),
    [
        (stay(), "hospitalization", 400000, 0),
        (stay("planned"), "planned_hospitalization", 370000, 0),
        (stay("one_day", days=1, same_day=True), "one_day_treatment", 340000, 0),
        (stay(days=1, same_day=True), "same_day_discharge", 300000, 0),
        (stay(days=1), "one_day_stay", 310000, 0),
        (stay("planned", days=2), "two_day_stay", 320000, 0),
        (stay(days=7), "hospitalization", 400000, 0),
        # Days over the financed stay (H) are paid per day (L), header:
        # "osobodnia ponad ryczałt finansowany grupą - typ umowy hospitalizacja".
        (stay(days=10), "hospitalization", 430000, 3),
        (stay("planned", days=10), "planned_hospitalization", 370000, 0),
    ],
)
def test_settlement_mode_selects_the_catalog_column(
    facts: StayFacts, measure: str, total: int, extra_days: int
) -> None:
    result = base_points(rules(), facts)
    assert result.determined
    assert (result.measure, result.total_hundredths, result.extra_days) == (
        measure,
        total,
        extra_days,
    )
    assert result.kind == "catalog_base_points_without_modifiers"


def test_blank_short_stay_column_keeps_the_labelled_standard_value() -> None:
    standard_only = (("hospitalization", 400000),)
    result = base_points(rules(standard_only), stay(days=2))
    assert (result.measure, result.reason) == (
        "hospitalization",
        "short_stay_column_blank",
    )


@pytest.mark.parametrize(
    ("values", "facts", "catalog", "rows", "reason"),
    [
        (FULL, stay(days=None), "1a", 1, "duration_unresolved"),
        (FULL, stay("one_day", days=None), "1a", 1, "one_day_dates_inconsistent"),
        ((("hospitalization", 1),), stay("planned"), "1a", 1, "mode_value_absent"),
        ((("hospitalization", 1),), stay("one_day", same_day=True), "1a", 1, "mode_value_absent"),
        (FULL, stay(days=0), "1a", 1, "ward_duration_not_positive"),
        (FULL, stay(), "1a", 2, "governing_catalog_row_not_unique"),
        (FULL, stay(days=1, same_day=True), "1ae", 1, "day_stay_hours_unknown"),
        (FULL, stay("one_day", same_day=True), "1ae", 1, "day_stay_hours_unknown"),
    ],
)  # fmt: skip
def test_unknown_or_absent_inputs_leave_points_undetermined(
    values: tuple[tuple[str, int], ...],
    facts: StayFacts,
    catalog: str,
    rows: int,
    reason: str,
) -> None:
    result = base_points(rules(values, catalog=catalog, catalog_rows=rows), facts)
    assert (result.determined, result.reason, result.total_hundredths) == (
        False,
        reason,
        None,
    )


def test_1ae_uses_its_own_financed_stay_columns() -> None:
    values = (
        ("hospitalization", 500000),
        ("financed_stay_excluding_day_stay", 3),
        ("extra_stay_day_excluding_day_stay", 20000),
        # A 1a column name must not leak into 1ae arithmetic.
        ("financed_stay", 1),
    )
    result = base_points(rules(values, catalog="1ae"), stay(days=5))
    assert (result.extra_days, result.total_hundredths) == (2, 540000)
