"""Bounded, proven single-scalar documentation checks, not coding suggestions."""

from dataclasses import replace
from typing import Literal

from minos.domain import jgp_conditions as c
from minos.domain.grouper_rules import GroupEvaluation, GroupRules, NearMiss
from minos.grouper.conditions import Facts, evaluate_group


def near_misses(
    rules: GroupRules, facts: Facts, result: GroupEvaluation
) -> tuple[NearMiss, ...]:
    if result.status != "invalid" or rules.catalog_rows != 1:
        return ()
    answers: list[NearMiss] = []
    field: Literal["completed_years", "duration_days", "discharge_mode"]
    for event in result.nodes:
        node = event.rule
        if event.verdict != "not_met" or node is None:
            continue
        payload = node.payload
        trials: tuple[Facts, ...] = ()
        if isinstance(payload, c.Age) and payload.unit == "completed_years":
            field = "completed_years"
            trials = tuple(
                replace(facts, years=value)
                for value in (
                    payload.threshold - 1,
                    payload.threshold,
                    payload.threshold + 1,
                )
                if 0 <= value <= 200
            )
        elif isinstance(payload, c.EpisodeDuration):
            field = "duration_days"
            trials = tuple(
                replace(facts, days=value)
                for value in sorted(
                    {b.days + offset for b in payload.bounds for offset in (-1, 0, 1)}
                )
                if 1 <= value <= 3_652_058
            )
        elif isinstance(payload, c.DischargeMode):
            field = "discharge_mode"
            trials = tuple(
                replace(facts, discharge_mode=value)
                for value in payload.allowed_reported_codes
            )
        else:
            continue
        # A changed scalar may affect other conditions and conservative routing.
        # Only the complete reevaluation establishes sensitivity to that fact.
        if any(evaluate_group(rules, trial).status == "valid" for trial in trials):
            answers.append(NearMiss(rules.catalog, rules.code, node.identity, field))
    return tuple(answers)
