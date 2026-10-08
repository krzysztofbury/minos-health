"""Bounded, proven single-scalar documentation checks, not coding suggestions."""

from dataclasses import replace
from typing import Literal

from minos.domain import jgp_conditions as c
from minos.domain.grouper_rules import GroupEvaluation, GroupRules, NearMiss
from minos.grouper.conditions import Facts, evaluate_group

# Owner decision 2026-10-08: a check is plausible only for an off-by-one
# documentation error, never for a distant age or stay (51 years vs "< 18").
MAX_SCALAR_DISTANCE = 1
# Discharge mode 9 is the patient's death (jgp-conditions.md, D45); asking
# whether a patient died is never offered as a documentation check.
DEATH_DISCHARGE_MODE = 9


def near_misses(
    rules: GroupRules, facts: Facts, result: GroupEvaluation
) -> tuple[NearMiss, ...]:
    if result.status != "invalid" or rules.catalog_rows != 1:
        return ()
    answers: list[NearMiss] = []
    field: Literal["completed_years", "duration_days", "discharge_mode"]
    nearby = range(-MAX_SCALAR_DISTANCE, MAX_SCALAR_DISTANCE + 1)
    for event in result.nodes:
        node = event.rule
        if event.verdict != "not_met" or node is None:
            continue
        payload = node.payload
        trials: tuple[Facts, ...] = ()
        if (
            isinstance(payload, c.Age)
            and payload.unit == "completed_years"
            and facts.years is not None
        ):
            field = "completed_years"
            trials = tuple(
                replace(facts, years=facts.years + offset)
                for offset in nearby
                if offset and 0 <= facts.years + offset <= 200
            )
        elif isinstance(payload, c.EpisodeDuration) and facts.days is not None:
            field = "duration_days"
            trials = tuple(
                replace(facts, days=facts.days + offset)
                for offset in nearby
                if offset and 1 <= facts.days + offset <= 3_652_058
            )
        elif isinstance(payload, c.DischargeMode):
            field = "discharge_mode"
            trials = tuple(
                replace(facts, discharge_mode=value)
                for value in payload.allowed_reported_codes
                if value != DEATH_DISCHARGE_MODE
            )
        else:
            continue
        # A changed scalar may affect other conditions and conservative routing.
        # Only the complete reevaluation establishes sensitivity to that fact.
        if any(evaluate_group(rules, trial).status == "valid" for trial in trials):
            answers.append(NearMiss(rules.catalog, rules.code, node.identity, field))
    return tuple(answers)
