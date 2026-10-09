"""First SZP 1.1.2026 slice: specialty exclusions, ranks, age and ward duration.

Product exclusions, routing, contracts and characteristic predicates are not
bound here. The output is explicitly preparation-only, never JGP eligibility.
"""

import calendar
import re
from datetime import date

from minos.domain.encounter import (
    Diagnosis,
    Encounter,
    Procedure,
    SettlementProduct,
    WardStay,
)
from minos.domain.grouper import (
    AlgorithmData,
    CountingPolicy,
    Decision,
    EvidencePointer,
    ExecutionKey,
    GrouperError,
    Outcome,
    PreparationResult,
    ProductClassification,
    QualifiedDiagnosis,
    RankedProcedure,
    Reason,
    UnsupportedAlgorithm,
)
from minos.domain.komunikat import DepartmentSpecialty, ProcedureRank
from minos.grouper.products import (
    ProductContext,
    analyse,
    procedure_effect,
    touching,
    unclassified,
)
from minos.grouper.trace import TraceAccumulator

ENGINE_VERSION = "szp-preparation-2"
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


def _qualify_diagnosis(
    fact: Diagnosis, stays: dict[str, Outcome], covered: bool
) -> tuple[Outcome, Reason]:
    # Paragraph 143 groups on the hospitalization-level SWIAD causes. They are
    # not codes of a ward stay, so paragraph 119 stay exclusion cannot drop them.
    if fact.hospitalization_level:
        return "retained", "hospitalization_level_diagnosis"
    return _qualify(fact.stay_id, stays, covered)


def _diagnoses(
    encounter: Encounter,
    stays: dict[str, Outcome],
    covered: bool,
    trace: TraceAccumulator,
) -> tuple[QualifiedDiagnosis, tuple[QualifiedDiagnosis, ...]]:
    principal = encounter.principal_diagnosis
    outcome, reason = _qualify_diagnosis(principal, stays, covered)
    trace.append(
        Decision("principal_diagnosis", 1, outcome, reason, _algorithm(119, 143))
    )
    secondary: list[QualifiedDiagnosis] = []
    for ordinal, fact in enumerate(
        sorted(
            encounter.secondary_diagnoses,
            key=lambda d: (d.code, d.hospitalization_level, d.stay_id or ""),
        ),
        1,
    ):
        state, why = _qualify_diagnosis(fact, stays, covered)
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
    products: ProductContext | None,
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
    episodes = {stay.identifier: stay for stay in encounter.stays}
    for ordinal, fact in enumerate(ordered, 1):
        outcome, reason = _qualify(fact.stay_id, stays, covered)
        trace.append(
            Decision("procedure", ordinal, outcome, reason, _algorithm(119, 129))
        )
        if products is not None:
            outcome = _product_outcome(
                fact, outcome, episodes, encounter, products, ordinal, trace
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
        birthday = (born.month, born.day)
        reason = "birth_date_years"
        if birthday == (2, 29) and not calendar.isleap(admitted.year):
            # Civil Code art. 112: a missing day means the month's last day, and
            # age is reached at its start (owner decision 2026-10-07).
            birthday = (2, 28)
            if (admitted.month, admitted.day) == birthday:
                reason = "birth_date_years_leap_day"
        value = admitted.year - born.year - ((admitted.month, admitted.day) < birthday)
    trace.append(Decision("age", 1, "recorded", reason, _algorithm(189), value))
    return value


def _product_outcome(
    fact: Procedure,
    ward: Outcome,
    stays: dict[str, WardStay],
    encounter: Encounter,
    products: ProductContext,
    ordinal: int,
    trace: TraceAccumulator,
) -> Outcome:
    """Combine a ward outcome with type W products (owner decision 2026-10-09).

    A ward exclusion outranks a product "unresolved" and a W exclusion
    outranks a ward "unresolved". An undated procedure happened on some day of
    its attributed stay, else of the episode.
    """
    stay = stays.get(fact.stay_id or "")
    possible = (
        (fact.performed_on, fact.performed_on)
        if fact.performed_on is not None
        else (stay.admitted_on, stay.discharged_on)
        if stay is not None
        else (encounter.admitted_on, encounter.discharged_on)
    )
    effect = procedure_effect(products, possible)
    if ward == "excluded" or effect is None:
        trace.append(
            Decision(
                "procedure_product",
                ordinal,
                ward,
                "stay_excluded" if ward == "excluded" else "product_outside_intervals",
                _algorithm(129, 140),
            )
        )
        return ward
    if effect == "excluded":
        trace.append(
            Decision(
                "procedure_product",
                ordinal,
                "excluded",
                "product_w_interval",
                _algorithm(129, 140),
            )
        )
        return "excluded"
    trace.append(
        Decision(
            "procedure_product",
            ordinal,
            "unresolved",
            "product_interval_possible",
            _algorithm(129, 140),
        )
    )
    return "unresolved"


def _products(
    products: tuple[SettlementProduct, ...],
    classification: ProductClassification,
    episode: tuple[date, date],
    trace: TraceAccumulator,
) -> ProductContext:
    context = analyse(products, classification, episode)
    for ordinal, (product, kind) in enumerate(
        zip(products, context.classes, strict=True), 1
    ):
        start, end = product.started_on, product.ended_on
        if kind == "neutral":
            decision = Decision(
                "product", ordinal, "retained", "product_neutral", _algorithm(129)
            )
        elif kind == "W" and start is not None and end is not None:
            decision = Decision(
                "product",
                ordinal,
                "excluded",
                "product_w",
                _algorithm(129, 140),
                (end - start).days,
            )
        elif kind == "W":
            decision = Decision(
                "product",
                ordinal,
                "unresolved",
                "product_w_incomplete",
                _algorithm(129, 140),
            )
        else:
            decision = Decision(
                "product",
                ordinal,
                "unresolved",
                "product_unknown",
                _algorithm(129, 140, 141),
            )
        trace.append(decision)
    return context


def _ward_duration(
    encounter: Encounter,
    stays: dict[str, Outcome],
    covered: bool,
    trace: TraceAccumulator,
    products: ProductContext | None,
) -> int | None:
    if (
        encounter.hospitalization_type == "one_day"
        and encounter.discharged_on != encounter.admitted_on
    ):
        # Paragraphs 146-147 give the one-day formula only for dw = dp.
        trace.append(
            Decision(
                "ward_duration",
                1,
                "unresolved",
                "one_day_dates_inconsistent",
                _algorithm(146, 147),
            )
        )
        return None
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
    same_day = raw_days == 0
    if same_day:
        raw_days = 1
    subtracted = 0
    if products is not None and (products.unknown or products.w_intervals):
        blocker = _product_duration_blocker(products, merged, same_day)
        if blocker is not None:
            trace.append(
                Decision(
                    "product_duration",
                    1,
                    "unresolved",
                    blocker,
                    _algorithm(129, 153, 154),
                )
            )
            return None
        subtracted = sum((end - start).days for start, end in products.w_intervals)
        trace.append(
            Decision(
                "product_duration",
                1,
                "recorded",
                "product_duration_subtracted",
                _algorithm(129, 153, 154),
                subtracted,
            )
        )
    days = raw_days - sum((end - start).days for start, end in merged) - subtracted
    if days <= 0 and not same_day:
        # A multi-day stay cannot last no time; points already refuse it.
        trace.append(
            Decision(
                "ward_duration",
                1,
                "unresolved",
                "duration_not_positive",
                _algorithm(145, 152),
            )
        )
        return None
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


def _product_duration_blocker(
    products: ProductContext, wards: list[tuple[date, date]], same_day: bool
) -> Reason | None:
    """Why W products leave the duration undetermined, if they do."""
    if products.unknown:
        return "product_duration_unknown_product"
    if same_day:
        # The same-day formula and the subtraction disagree for dw = dp.
        return "product_duration_same_day"
    intervals = sorted(products.w_intervals)
    if any(touching(a, b) for a, b in zip(intervals, intervals[1:], strict=False)):
        return "product_duration_overlap"
    if any(touching(w, ward) for w in intervals for ward in wards):
        return "product_duration_overlap"
    return None


def prepare(
    encounter: Encounter,
    key: ExecutionKey,
    data: AlgorithmData,
    classification: ProductClassification | None = None,
) -> PreparationResult:
    validate_preparation_binding(key, data)
    if type(encounter) is not Encounter:
        raise GrouperError("invalid preparation encounter type")
    trace = TraceAccumulator()
    covered = _coverage(encounter, trace)
    stays = _stays(encounter, data, trace)
    principal, secondary = _diagnoses(encounter, stays, covered, trace)
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
    bound = classification is not None and encounter.product_reporting == "complete"
    episode = (encounter.admitted_on, encounter.discharged_on)
    context: ProductContext | None = None
    if products and bound and classification is not None:
        context = _products(products, classification, episode, trace)
    elif products:
        # Unclassified products could exclude any procedure; the engine itself
        # never answers "retained" for them, whatever its caller checks.
        context = unclassified(episode)
    procedures = _procedures(encounter, data, stays, covered, trace, context)
    years = _age(encounter, trace)
    days = _ward_duration(encounter, stays, covered, trace, context)
    # Completeness reports facts only. Without the product catalog binding,
    # neither a code nor an empty list proves exclusion-free eligibility.
    trace.append(
        Decision(
            "product_binding",
            1,
            "recorded" if bound else "unresolved",
            "product_catalog_bound" if bound else "product_catalog_unbound",
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
        "bound" if bound else "unbound",
        classification.release_id if bound and classification is not None else None,
        context.classes if context is not None else (),
    )
