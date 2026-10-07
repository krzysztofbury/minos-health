"""Final points from base points and reviewed modifiers (#35, owner rules).

Owner decisions 2026-10-07: MODTAR as published; provider and encounter facts
the encounter lacks are declared by the caller; applicable coefficients
multiply; one half-up rounding to 0.01 point at the end.
"""

from datetime import date
from fractions import Fraction
from typing import Literal

from minos.domain.grouper_candidates import Catalog
from minos.domain.grouper_rules import (
    BasePoints,
    FinalPoints,
    ModifierDecision,
    ModifierOutcome,
)

# Patient conditions stated by the clause itself (46/2026 section 17).
AGE_BOUNDS: dict[str, tuple[int, int]] = {
    "17.1": (0, 17),  # 17(1): "poniżej 18 r.ż."
    "17.10": (0, 2),  # 17(10): "do ukończenia 3 r.ż."
    "17.11": (3, 8),  # 17(11): "od rozpoczęcia 4. r.ż. do ukończenia 9. r.ż."
}
# #129 differences answered by assumption; shown next to affected answers.
KNOWN_RISKS: dict[tuple[str, str], str] = {
    **{("M5", g): "#129: M5 for this group follows MODTAR, not the act" for g in ("DU58", "Q16E")},
    **{("17.1", g): "#129: 17.1 for this group follows MODTAR" for g in ("S21", "S22", "S23")},
    **{("17.18", g): "#129: 17.18 for this group follows MODTAR" for g in ("F45R", "L31R", "M22R")},
    **{(s, g): f"#129: {s} for this group follows MODTAR" for s in ("17.10", "17.11") for g in ("PZA04", "PZF13", "PZD04A")},
}  # fmt: skip
FOOD_RATIO_END = date(2026, 8, 31)
FOOD_RATIO_NOTE = (
    "14/2026 section 2 food-standard ratio may apply to January-August services; "
    + "not computed (owner decision 2026-10-07)"
)


def _rounded(value: Fraction) -> int:
    return int(value + Fraction(1, 2)) if value >= 0 else -int(-value + Fraction(1, 2))


def _decide(
    mapping: dict[str, object],
    declared: frozenset[str],
    years: int | None,
    base: int,
) -> ModifierDecision:
    symbol = str(mapping["symbol"])
    coefficient = int(str(mapping["coefficient_millionths"]))
    provenance: Literal["MODTAR", "manual_legal_overlay"] = (
        "MODTAR" if mapping["provenance"] == "MODTAR" else "manual_legal_overlay"
    )
    citation = str(mapping["source_citation"])

    def decision(
        outcome: ModifierOutcome, reason: str, gain: int | None = None
    ) -> ModifierDecision:
        return ModifierDecision(
            symbol, provenance, citation, coefficient, outcome, reason, gain
        )

    bounds = AGE_BOUNDS.get(symbol)
    if bounds is not None:
        if years is None:
            return decision("undetermined", "patient_age_unknown")
        if not bounds[0] <= years <= bounds[1]:
            return decision("rejected", "patient_age_outside_clause")
    if symbol in declared:
        return decision("applied", "declared")
    gain = _rounded(Fraction(base * coefficient, 1_000_000)) - base
    return decision("possible", "not_declared", gain)


def final_group_points(
    catalog: Catalog,
    group: str,
    base: BasePoints,
    mappings: list[dict[str, object]],
    declared: frozenset[str],
    years: int | None,
    service_date: date,
) -> FinalPoints:
    notes: list[str] = []
    if service_date <= FOOD_RATIO_END:
        notes.append(FOOD_RATIO_NOTE)
    if not base.determined or base.base_hundredths is None:
        return FinalPoints(
            catalog, group, base, (), None, (*notes, "base points undetermined")
        )
    amount = base.total_hundredths or 0
    decisions = [_decide(m, declared, years, amount) for m in mappings]
    mapped = {d.symbol for d in decisions}
    for symbol in sorted(declared - mapped):
        notes.append(f"declared {symbol} is not mapped to {group} on this date")
    applied = [d for d in decisions if d.outcome == "applied"]
    citations = [d.source_citation for d in applied]
    if len(set(citations)) != len(citations):
        # Tiers of one clause are alternatives; two cannot both apply.
        return FinalPoints(
            catalog,
            group,
            base,
            tuple(decisions),
            None,
            (*notes, "declared alternatives from one clause"),
        )
    for decision in decisions:
        risk = KNOWN_RISKS.get((decision.symbol, group))
        if risk is not None:
            notes.append(risk)
    value = Fraction(amount)
    for decision in applied:
        value *= Fraction(decision.coefficient_millionths, 1_000_000)
    return FinalPoints(
        catalog,
        group,
        base,
        tuple(sorted(decisions, key=lambda d: (d.outcome, d.symbol))),
        _rounded(value),
        tuple(notes),
    )
