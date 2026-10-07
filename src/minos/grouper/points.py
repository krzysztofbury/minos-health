"""Catalog base points per settlement mode (#35); modifiers are never applied.

Columns follow the attachment 1a headers: hospitalization (E), planned (F),
one-day treatment (G), financed stay (H), same-day (I), 1-day (J) and 2-day
(K) stays, and extra days over the financed stay for the hospitalization
contract type (L). Catalog 1ae has its own columns; its day stays under 12
hours depend on hours the encounter does not report.
"""

from minos.domain.grouper_rules import BasePoints, GroupRules, StayFacts


def _short_stay(stay: StayFacts) -> str | None:
    if stay.same_day:
        return "same_day_discharge"
    if stay.days == 1:
        return "one_day_stay"
    if stay.days == 2:
        return "two_day_stay"
    return None


def _extra(
    values: dict[str, int], stay: StayFacts, days: int, financed: str, extra: str
) -> tuple[int, int]:
    if stay.hospitalization_type != "hospitalization":
        return 0, 0
    if financed not in values or extra not in values or days <= values[financed]:
        return 0, 0
    over = days - values[financed]
    return over, over * values[extra]


def base_points(rules: GroupRules, stay: StayFacts) -> BasePoints:
    if rules.catalog_rows != 1:
        return BasePoints(False, "governing_catalog_row_not_unique")
    values = dict(rules.base_values)
    if stay.hospitalization_type == "one_day":
        if not stay.same_day:
            return BasePoints(False, "one_day_dates_inconsistent")
        if rules.catalog == "1ae":
            return BasePoints(False, "day_stay_hours_unknown")
        if "one_day_treatment" not in values:
            return BasePoints(False, "mode_value_absent")
        return BasePoints(
            True, "one_day_treatment", "one_day_treatment", values["one_day_treatment"]
        )
    days = stay.days
    if days is None:
        return BasePoints(False, "duration_unresolved")
    if days <= 0 and not stay.same_day:
        return BasePoints(False, "ward_duration_not_positive")
    standard = (
        "hospitalization"
        if stay.hospitalization_type == "hospitalization"
        else "planned_hospitalization"
    )
    if rules.catalog == "1ae":
        if stay.same_day:
            return BasePoints(False, "day_stay_hours_unknown")
        measure, reason = standard, "standard_value"
        financed, extra = (
            "financed_stay_excluding_day_stay",
            "extra_stay_day_excluding_day_stay",
        )
    else:
        short = _short_stay(stay)
        if short is not None and short in values:
            measure, reason = short, "short_stay_value"
        else:
            # A blank short-stay column leaves the group's standard value.
            measure = standard
            reason = "standard_value" if short is None else "short_stay_column_blank"
        financed, extra = "financed_stay", "extra_stay_day"
    if measure not in values:
        return BasePoints(False, "mode_value_absent")
    over, extra_points = (
        _extra(values, stay, days, financed, extra) if measure == standard else (0, 0)
    )
    return BasePoints(True, reason, measure, values[measure], over, extra_points)
