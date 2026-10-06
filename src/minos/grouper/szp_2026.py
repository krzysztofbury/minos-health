"""First SZP 1.1.2026 slice: specialty exclusions, ranks, age and ward duration.

Product exclusions, routing, contracts and characteristic predicates are not
bound here. The output is explicitly preparation-only, never JGP eligibility.
"""

import calendar
import re
from datetime import date

from minos.domain.encounter import Encounter, Procedure, WardStay
from minos.domain.grouper import (
    AlgorithmData,
    CountingPolicy,
    Decision,
    EvidencePointer,
    ExecutionKey,
    GrouperError,
    Outcome,
    PreparationResult,
    QualifiedDiagnosis,
    RankedProcedure,
    Reason,
    UnsupportedAlgorithm,
)
from minos.domain.komunikat import DepartmentSpecialty, ProcedureRank
from minos.grouper.trace import TraceAccumulator

ENGINE_VERSION = "szp-preparation-1"
# All hardcoded w:p coordinates below belong to these reviewed DOCX bytes.
# A matching version string cannot establish the same rules or coordinates.
ALGORITHM_SHA256 = "a233b231e2d20448f14d28187590803bce743371b727d3c7d6c5dd4fba8de51c"


def validate_preparation_binding(key: ExecutionKey, data: AlgorithmData) -> None:
    if type(key) is not ExecutionKey or type(data) is not AlgorithmData:
        raise GrouperError("invalid preparation binding types")
    if key.algorithm_version != data.metadata.version:
        raise GrouperError("binding conflicts with pinned algorithm version")
    if key.reporting_month < data.metadata.reporting_from:
        raise GrouperError("reporting month precedes the pinned algorithm")
    if (key.algorithm_version, key.engine_version) != ("1.1.2026", ENGINE_VERSION):
        raise UnsupportedAlgorithm("algorithm and engine combination is unsupported")
    if data.identity.algorithm_sha256 != ALGORITHM_SHA256:
        raise UnsupportedAlgorithm("engine requires reviewed algorithm bytes")
    if key.sources != data.identity:
        raise GrouperError("binding conflicts with exact algorithm source identities")


def _algorithm(*positions: int) -> tuple[EvidencePointer, ...]:
    return tuple(EvidencePointer("algorithm", p) for p in positions)


def _ward_exclusion(
    stay: WardStay, rule: DepartmentSpecialty | None
) -> tuple[Outcome, Reason]:
    if rule is None:
        return "retained", "no_specialty_exclusion"
    if rule.icd_codes != "WSZYSTKIE":
        return "unresolved", "specialty_predicate_unsupported"
    if rule.service_codes == "WSZYSTKIE":
        return "excluded", "all_codes_excluded"
    if rule.service_codes != "4":
        return "unresolved", "specialty_predicate_unsupported"
    if stay.service_code is None:
        return "unresolved", "service_code_missing"
    # These are reported service-type tokens, not product codes. Do not infer
    # a SWIAD token from a catalog product identifier such as 01.0000.001.02.
    if re.fullmatch(r"[1-9][0-9]?", stay.service_code) is None:
        return "unresolved", "service_code_uninterpreted"
    if stay.service_code == "4":
        return "excluded", "all_codes_excluded"
    return "retained", "service_not_excluded"


def _stays(
    encounter: Encounter, data: AlgorithmData, trace: TraceAccumulator
) -> dict[str, Outcome]:
    rules = {row.code: row for row in data.specialties}
    decisions: dict[str, Outcome] = {}
    ordered = sorted(
        encounter.stays, key=lambda s: (s.admitted_on, s.discharged_on, s.identifier)
    )
    for ordinal, stay in enumerate(ordered, 1):
        rule = rules.get(stay.department)
        outcome, reason = _ward_exclusion(stay, rule)
        pointers = _algorithm(119, 120, 125)
        if rule is not None:
            pointers += (EvidencePointer("specialties", rule.row),)
        trace.append(Decision("ward_exclusion", ordinal, outcome, reason, pointers))
        decisions[stay.identifier] = outcome
    return decisions


def _coverage(encounter: Encounter, trace: TraceAccumulator) -> bool:
    intervals = sorted((s.admitted_on, s.discharged_on) for s in encounter.stays)
    end = encounter.admitted_on
    complete = True
    for start, stop in intervals:
        if start > end:
            complete = False
        end = max(end, stop)
    complete = complete and end == encounter.discharged_on
    reason: Reason = (
        "stay_coverage_complete" if complete else "stay_coverage_incomplete"
    )
    trace.append(
        Decision(
            "stay_coverage",
            1,
            "recorded" if complete else "unresolved",
            reason,
            _algorithm(67),
        )
    )
    return complete


def _qualify(
    stay_id: str | None, stays: dict[str, Outcome], covered: bool
) -> tuple[Outcome, Reason]:
    if stay_id is None:
        if covered and all(outcome == "retained" for outcome in stays.values()):
            return "retained", "no_excluded_stays"
        return "unresolved", "attribution_unresolved"
    outcome = stays[stay_id]
    if outcome == "excluded":
        return "excluded", "stay_excluded"
    if outcome == "unresolved":
        return "unresolved", "stay_unresolved"
    return "retained", "stay_retained"


def _diagnoses(
    encounter: Encounter,
    stays: dict[str, Outcome],
    covered: bool,
    trace: TraceAccumulator,
) -> tuple[QualifiedDiagnosis, tuple[QualifiedDiagnosis, ...]]:
    principal = encounter.principal_diagnosis
    outcome, reason = _qualify(principal.stay_id, stays, covered)
    trace.append(
        Decision("principal_diagnosis", 1, outcome, reason, _algorithm(119, 143))
    )
    secondary: list[QualifiedDiagnosis] = []
    for ordinal, fact in enumerate(
        sorted(encounter.secondary_diagnoses, key=lambda d: (d.code, d.stay_id or "")),
        1,
    ):
        state, why = _qualify(fact.stay_id, stays, covered)
        trace.append(
            Decision("secondary_diagnosis", ordinal, state, why, _algorithm(119, 143))
        )
        secondary.append(QualifiedDiagnosis(fact, state))
    return QualifiedDiagnosis(principal, outcome), tuple(secondary)


def _procedures(
    encounter: Encounter,
    data: AlgorithmData,
    stays: dict[str, Outcome],
    covered: bool,
    trace: TraceAccumulator,
) -> tuple[RankedProcedure, ...]:
    ranks = {row.code: row for row in data.ranks}
    result: list[RankedProcedure] = []
    ordered = sorted(
        encounter.procedures,
        key=lambda p: (
            p.code,
            p.performed_on is not None,
            p.performed_on or date.min,
            p.stay_id or "",
        ),
    )
    for ordinal, fact in enumerate(ordered, 1):
        outcome, reason = _qualify(fact.stay_id, stays, covered)
        trace.append(
            Decision("procedure", ordinal, outcome, reason, _algorithm(119, 129))
        )
        if outcome != "retained":
            trace.append(Decision("procedure_rank", ordinal, outcome, "not_ranked"))
            result.append(RankedProcedure(fact, outcome, None, "unknown"))
            continue
        row = ranks.get(fact.code)
        ranked, decision = _rank_fact(fact, row, ordinal)
        trace.append(decision)
        result.append(ranked)
    return tuple(result)


def _rank_fact(
    fact: Procedure, row: ProcedureRank | None, ordinal: int
) -> tuple[RankedProcedure, Decision]:
    if row is None:
        return (
            RankedProcedure(fact, "unresolved", None, "unknown"),
            Decision(
                "procedure_rank",
                ordinal,
                "unresolved",
                "rank_missing",
                _algorithm(74, 79),
            ),
        )
    outcome: Outcome = "retained"
    policy: CountingPolicy
    reason: Reason
    if row.rank == 0:
        outcome, policy, reason = "excluded", "ignored", "rank_zero"
        pointers = _algorithm(75)
    elif row.rank <= 3:
        policy, reason = "once_per_code", "rank_once_only"
        pointers = _algorithm(76, 77, 78)
    else:
        policy, reason = "label_dependent", "rank_label_dependent"
        pointers = _algorithm(79, 138)
    return (
        RankedProcedure(fact, outcome, row.rank, policy),
        Decision(
            "procedure_rank",
            ordinal,
            outcome,
            reason,
            (EvidencePointer("ranks", row.row), *pointers),
            row.rank,
        ),
    )


def _age(encounter: Encounter, trace: TraceAccumulator) -> int | None:
    value = encounter.age_completed_years
    reason: Reason = "reported_years"
    born = encounter.birth_date
    admitted = encounter.admitted_on
    if born is not None:
        if (
            (born.month, born.day) == (2, 29)
            and not calendar.isleap(admitted.year)
            and (admitted.month, admitted.day) == (2, 28)
        ):
            trace.append(
                Decision(
                    "age", 1, "unresolved", "leap_boundary_unreviewed", _algorithm(189)
                )
            )
            return None
        value = (
            admitted.year
            - born.year
            - ((admitted.month, admitted.day) < (born.month, born.day))
        )
        reason = "birth_date_years"
    trace.append(Decision("age", 1, "recorded", reason, _algorithm(189), value))
    return value


def _ward_duration(
    encounter: Encounter,
    stays: dict[str, Outcome],
    covered: bool,
    trace: TraceAccumulator,
) -> int | None:
    if not covered or "unresolved" in stays.values():
        trace.append(
            Decision(
                "ward_duration",
                1,
                "unresolved",
                "ward_duration_unresolved",
                _algorithm(151, 152),
            )
        )
        return None
    intervals = sorted(
        (s.admitted_on, s.discharged_on)
        for s in encounter.stays
        if stays[s.identifier] == "excluded"
    )
    merged: list[tuple[date, date]] = []
    for ordinal, (start, end) in enumerate(intervals, 1):
        # Paragraph 186 joins adjacent days as well as overlapping intervals.
        if merged and (start - merged[-1][1]).days <= 1:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            reason: Reason = "excluded_interval_merged"
        else:
            merged.append((start, end))
            reason = "excluded_interval_added"
        trace.append(
            Decision(
                "duration_interval",
                ordinal,
                "recorded",
                reason,
                _algorithm(152, 186),
                (merged[-1][1] - merged[-1][0]).days,
            )
        )
    raw_days = (encounter.discharged_on - encounter.admitted_on).days
    if raw_days == 0:
        raw_days = 1
    days = raw_days - sum((end - start).days for start, end in merged)
    trace.append(
        Decision(
            "ward_duration",
            1,
            "recorded",
            "ward_duration_calculated",
            _algorithm(145, 147, 149, 152, 186),
            days,
        )
    )
    return days


def prepare(
    encounter: Encounter, key: ExecutionKey, data: AlgorithmData
) -> PreparationResult:
    validate_preparation_binding(key, data)
    if type(encounter) is not Encounter:
        raise GrouperError("invalid preparation encounter type")
    trace = TraceAccumulator()
    covered = _coverage(encounter, trace)
    stays = _stays(encounter, data, trace)
    principal, secondary = _diagnoses(encounter, stays, covered, trace)
    procedures = _procedures(encounter, data, stays, covered, trace)
    years = _age(encounter, trace)
    days = _ward_duration(encounter, stays, covered, trace)
    products = tuple(
        sorted(
            encounter.settlement_products,
            key=lambda p: (
                p.code,
                p.started_on is not None,
                p.started_on or date.min,
                p.ended_on is not None,
                p.ended_on or date.min,
            ),
        )
    )
    # Completeness reports facts only. Without the product catalog binding,
    # neither a code nor an empty list proves exclusion-free eligibility.
    trace.append(
        Decision(
            "product_binding",
            1,
            "unresolved",
            "product_catalog_unbound",
            _algorithm(129, 140, 141, 153, 154),
            len(products),
        )
    )
    trace.append(
        Decision(
            "scope", 1, "unresolved", "preparation_only", _algorithm(98, 129, 200, 201)
        )
    )
    return PreparationResult(
        key,
        data.identity,
        principal,
        secondary,
        procedures,
        years,
        days,
        trace.freeze(),
        products,
        encounter.product_reporting,
    )
