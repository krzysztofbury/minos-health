"""Deterministic preparation behavior, not final clinical grouping acceptance."""

from dataclasses import replace
from datetime import date
from typing import cast

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minos.domain.encounter import (
    MAX_PROCEDURES,
    MAX_SECONDARY_DIAGNOSES,
    MAX_SETTLEMENT_PRODUCTS,
    MAX_STAYS,
    Diagnosis,
    Procedure,
    ProductReporting,
    SettlementProduct,
    WardStay,
)
from minos.domain.grouper import (
    Decision,
    GrouperError,
    UnknownEngineBinding,
    UnsupportedAlgorithm,
)
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.grouper.szp_2026 import prepare
from minos.grouper.trace import MAX_TRACE_DECISIONS, TraceAccumulator
from tests.fixtures.grouper import algorithm, encounter, key, registry


def test_direct_preparation_does_not_claim_registry_dispatch() -> None:
    unregistered = replace(key(), ruleset_id="never-registered")
    result = prepare(encounter(), unregistered, algorithm())
    assert all(event.stage != "dispatch" for event in result.trace)
    dispatched = registry().execute(key(), encounter())
    assert dispatched.trace[0] == Decision("dispatch", 1, "recorded", "exact_binding")


@pytest.mark.parametrize("component", ["ranks_sha256", "specialties_sha256"])
def test_source_rotation_requires_a_different_exact_request(component: str) -> None:
    original = algorithm()
    changed = replace(
        original,
        identity=replace(original.identity, **{component: "4" * 64}),
    )
    with pytest.raises(GrouperError, match="exact algorithm source identities"):
        _ = EngineBinding(key(), changed)
    with pytest.raises(GrouperError, match="exact algorithm source identities"):
        _ = prepare(encounter(), key(), changed)
    rotated = registry(changed)
    with pytest.raises(UnknownEngineBinding):
        _ = rotated.execute(key(), encounter())
    request = key(changed.identity)
    assert request != key()
    assert rotated.execute(request, encounter()).sources == changed.identity


def test_same_version_with_unreviewed_description_cannot_cite_reviewed_paragraphs() -> (
    None
):
    original = algorithm()
    changed = replace(
        original, identity=replace(original.identity, algorithm_sha256="4" * 64)
    )
    request = key(changed.identity)
    with pytest.raises(UnsupportedAlgorithm, match="reviewed algorithm bytes"):
        _ = EngineBinding(request, changed)
    with pytest.raises(UnsupportedAlgorithm, match="reviewed algorithm bytes"):
        _ = prepare(encounter(), request, changed)


def test_preparation_preserves_rank_classes_and_has_no_settlement_result() -> None:
    result = registry().execute(key(), encounter())
    assert result.scope == "algorithm_preparation_only"
    assert result.completed_years == 17
    assert result.ward_adjusted_days == 9
    assert result.principal_diagnosis.outcome == "retained"
    assert [p.rank for p in result.procedures] == [0, 1, 2, 3, 4]
    assert [p.counting_policy for p in result.procedures] == [
        "ignored",
        "once_per_code",
        "once_per_code",
        "once_per_code",
        "label_dependent",
    ]
    assert result.procedures[0].outcome == "excluded"
    assert result.trace[-1].reason == "preparation_only"
    assert result.trace[-1].outcome == "unresolved"
    rank_events = [d for d in result.trace if d.stage == "procedure_rank"]
    assert [d.evidence[0].source for d in rank_events] == ["ranks"] * 5
    assert [d.evidence[0].position for d in rank_events] == [4, 5, 6, 7, 8]
    assert "00.3" not in repr(result) and "2008" not in repr(result)
    assert all("00.3" not in repr(d) for d in result.trace)


@pytest.mark.parametrize("reporting", ["unknown", "complete"])
def test_product_reports_remain_unbound_even_when_empty(
    reporting: ProductReporting,
) -> None:
    facts = replace(encounter(), product_reporting=reporting)
    result = registry().execute(key(), facts)
    assert result.product_reporting == reporting
    assert result.product_binding == "unbound"
    assert result.settlement_products == ()
    assert (
        next(d for d in result.trace if d.stage == "product_binding").outcome
        == "unresolved"
    )
    assert result.scope == "algorithm_preparation_only"


def test_product_occurrences_are_canonical_not_deduplicated_or_classified() -> None:
    products = (
        SettlementProduct("5.52.01.0001363", date(2026, 10, 3), date(2026, 10, 5)),
        SettlementProduct("5.52.01.0001363"),
        SettlementProduct("5.52.01.0001363", ended_on=date(2026, 10, 4)),
        SettlementProduct("5.52.01.0001363", date(2026, 10, 3), date(2026, 10, 5)),
    )
    facts = replace(
        encounter(), settlement_products=products, product_reporting="complete"
    )
    engine = registry()
    result = engine.execute(key(), facts)
    assert result.settlement_products == (
        products[1],
        products[2],
        products[0],
        products[3],
    )
    assert result.product_binding == "unbound"
    # This remains ward-only duration and qualification, not a product-adjusted
    # value or a declaration that these products have no effect.
    assert result.ward_adjusted_days == 9
    assert result.procedures == engine.execute(key(), encounter()).procedures
    assert result == engine.execute(
        key(), replace(facts, settlement_products=tuple(reversed(products)))
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ruleset_id", "other-ruleset"),
        ("algorithm_version", "2.1.2026"),
        ("engine_version", "other-engine"),
        ("reporting_month", date(2026, 11, 1)),
    ],
)
def test_unknown_combinations_never_fall_back(field: str, value: object) -> None:
    original = key()
    if field == "ruleset_id":
        request = replace(original, ruleset_id=cast(str, value))
    elif field == "algorithm_version":
        request = replace(original, algorithm_version=cast(str, value))
    elif field == "engine_version":
        request = replace(original, engine_version=cast(str, value))
    else:
        request = replace(original, reporting_month=cast(date, value))
    with pytest.raises(UnknownEngineBinding):
        _ = registry().execute(request, encounter())
    with pytest.raises(UnknownEngineBinding):
        _ = EngineRegistry().execute(key(), encounter())


def test_registry_rejects_conflicting_or_unsupported_registrations() -> None:
    binding = EngineBinding(key(), algorithm())
    with pytest.raises(GrouperError, match="duplicate engine registration"):
        _ = EngineRegistry((binding, binding))
    with pytest.raises(UnsupportedAlgorithm):
        _ = EngineBinding(replace(key(), engine_version="new-engine"), algorithm())
    with pytest.raises(GrouperError, match="conflicts"):
        _ = EngineBinding(replace(key(), algorithm_version="2.1.2026"), algorithm())
    with pytest.raises(GrouperError, match="precedes"):
        _ = EngineBinding(
            replace(key(), reporting_month=date(2025, 12, 1)), algorithm()
        )
    with pytest.raises(GrouperError, match="explicit reporting month"):
        _ = replace(key(), reporting_month=date(2026, 10, 2))
    with pytest.raises(GrouperError, match="bounded engine registry"):
        _ = EngineRegistry((binding,) * 65)


def test_source_tables_reject_duplicate_missing_and_cross_scope_evidence() -> None:
    original = algorithm()
    with pytest.raises(GrouperError, match="duplicate algorithm table code"):
        _ = replace(
            original, ranks=(*original.ranks, replace(original.ranks[0], row=100))
        )
    with pytest.raises(GrouperError, match="duplicate algorithm evidence row"):
        _ = replace(
            original, ranks=(replace(original.ranks[0], row=5), *original.ranks[1:])
        )
    with pytest.raises(GrouperError, match="another algorithm scope"):
        _ = replace(original, ranks=(replace(original.ranks[0], grouper_type="AOS"),))
    with pytest.raises(GrouperError, match="rank table"):
        _ = replace(original, ranks=())
    with pytest.raises(GrouperError, match="specialty table"):
        _ = replace(original, specialties=())
    with pytest.raises(GrouperError, match="invalid procedure rank"):
        _ = replace(original, ranks=(replace(original.ranks[0], rank=5),))
    with pytest.raises(GrouperError, match="source digests conflict"):
        _ = replace(original.identity, ranks_sha256=original.identity.algorithm_sha256)


def test_missing_rank_does_not_become_zero_or_borrow_another_scope() -> None:
    facts = replace(encounter(), procedures=(Procedure("L91", stay_id="main"),))
    result = registry().execute(key(), facts)
    procedure = result.procedures[0]
    assert (procedure.outcome, procedure.rank, procedure.counting_policy) == (
        "unresolved",
        None,
        "unknown",
    )
    assert (
        next(d for d in result.trace if d.stage == "procedure_rank").reason
        == "rank_missing"
    )


@pytest.mark.parametrize(
    ("service", "expected", "days"),
    [
        ("4", "excluded", 0),
        ("1", "retained", 9),
        (None, "unresolved", None),
        ("01.0000.001.02", "unresolved", None),
        ("04", "unresolved", None),
    ],
)
def test_service_condition_is_exact_not_inferred_from_a_product(
    service: str | None,
    expected: str,
    days: int | None,
) -> None:
    original = encounter()
    facts = replace(
        original,
        stays=(replace(original.stays[0], department="1220", service_code=service),),
    )
    result = registry().execute(key(), facts)
    assert result.principal_diagnosis.outcome == expected
    assert result.ward_adjusted_days == days
    assert [p.outcome for p in result.procedures] == (
        [expected] * 5
        if expected != "retained"
        else ["excluded", "retained", "retained", "retained", "retained"]
    )
    # An episode-level secondary without stay attribution cannot be silently
    # kept when its originating stay may be excluded.
    assert result.secondary_diagnoses[1].outcome == (
        "retained" if expected == "retained" else "unresolved"
    )


def test_unsupported_specialty_predicate_blocks_instead_of_becoming_empty() -> None:
    data = algorithm()
    data = replace(data, specialties=(replace(data.specialties[0], icd_codes="A00"),))
    facts = replace(
        encounter(), stays=(replace(encounter().stays[0], department="4260"),)
    )
    result = registry(data).execute(key(), facts)
    assert result.principal_diagnosis.outcome == "unresolved"
    assert result.ward_adjusted_days is None
    assert (
        next(d for d in result.trace if d.stage == "ward_exclusion").reason
        == "specialty_predicate_unsupported"
    )


def test_exclusions_precede_ranking_and_repetitions_are_not_destroyed() -> None:
    original = encounter()
    stays = (
        *original.stays,
        WardStay("icu", "4260", date(2026, 10, 3), date(2026, 10, 5)),
    )
    facts = replace(
        original,
        stays=stays,
        procedures=(
            Procedure("00.3", date(2026, 10, 3), "main"),
            Procedure("00.3", date(2026, 10, 4), "main"),
            Procedure("00.4", None, "main"),
            Procedure("00.4", None, "main"),
            Procedure("00.3", date(2026, 10, 4), "icu"),
            Procedure("00.3"),
        ),
    )
    result = registry().execute(key(), facts)
    assert len(result.procedures) == 6
    assert sum(p.counting_policy == "once_per_code" for p in result.procedures) == 2
    assert sum(p.counting_policy == "label_dependent" for p in result.procedures) == 2
    assert [
        (p.outcome, p.rank) for p in result.procedures if p.fact.stay_id == "icu"
    ] == [("excluded", None)]
    assert [
        (p.outcome, p.rank) for p in result.procedures if p.fact.stay_id is None
    ] == [("unresolved", None)]
    assert result.ward_adjusted_days == 7


def test_incomplete_stay_coverage_cannot_prove_duration_or_unattributed_facts() -> None:
    original = encounter()
    partial = replace(
        original.stays[0],
        admitted_on=date(2026, 10, 2),
        discharged_on=date(2026, 10, 8),
    )
    result = registry().execute(key(), replace(original, stays=(partial,)))
    assert result.ward_adjusted_days is None
    assert result.secondary_diagnoses[1].outcome == "unresolved"
    assert result.principal_diagnosis.outcome == "retained"


def test_source_duration_example_merges_overlap_and_adjacent_excluded_stays() -> None:
    # Pinned algorithm paragraphs 169-184 independently specify 25 - 11 - 4 = 10.
    stays = tuple(
        WardStay(f"ward-{i}", department, date(2026, 9, start), date(2026, 9, end))
        for i, (department, start, end) in enumerate(
            (
                ("4000", 5, 10),
                ("4260", 10, 21),
                ("4000", 21, 24),
                ("4260", 24, 25),
                ("4000", 25, 26),
                ("4260", 26, 28),
                ("4000", 28, 30),
            )
        )
    )
    facts = replace(
        encounter(),
        admitted_on=date(2026, 9, 5),
        discharged_on=date(2026, 9, 30),
        stays=stays,
        principal_diagnosis=Diagnosis("A00", "ward-0"),
        secondary_diagnoses=(),
        procedures=(),
    )
    result = registry().execute(key(), facts)
    assert result.ward_adjusted_days == 10
    overlapping = replace(stays[1], identifier="overlap", admitted_on=date(2026, 9, 11))
    assert (
        registry()
        .execute(key(), replace(facts, stays=(*stays, overlapping)))
        .ward_adjusted_days
        == 10
    )


@pytest.mark.parametrize(
    ("admitted", "birth", "expected"),
    [
        (date(2026, 10, 1), date(2008, 10, 2), 17),
        (date(2026, 10, 2), date(2008, 10, 2), 18),
        (date(2026, 2, 28), date(2008, 2, 29), None),
        (date(2026, 3, 1), date(2008, 2, 29), 18),
    ],
)
def test_completed_years_use_admission_not_discharge_and_block_leap_ambiguity(
    admitted: date,
    birth: date,
    expected: int | None,
) -> None:
    stay = WardStay("main", "4000", admitted, date(2026, 10, 10))
    facts = replace(encounter(), admitted_on=admitted, birth_date=birth, stays=(stay,))
    assert registry().execute(key(), facts).completed_years == expected


def test_reported_age_and_zero_day_episode_are_not_reinterpreted() -> None:
    original = encounter()
    facts = replace(
        original,
        birth_date=None,
        age_completed_years=18,
        discharged_on=original.admitted_on,
        stays=(replace(original.stays[0], discharged_on=original.admitted_on),),
        procedures=(),
    )
    result = registry().execute(key(), facts)
    assert result.completed_years == 18
    assert result.ward_adjusted_days == 1
    assert next(d for d in result.trace if d.stage == "age").reason == "reported_years"


def test_trace_has_capacity_for_maximum_encounter_and_fails_on_overflow() -> None:
    original = encounter()
    facts = replace(
        original,
        procedures=(Procedure("00.3", stay_id="main"),) * MAX_PROCEDURES,
        settlement_products=(SettlementProduct("5.52.01.0001363"),)
        * MAX_SETTLEMENT_PRODUCTS,
        secondary_diagnoses=(Diagnosis("B00", "main"),) * MAX_SECONDARY_DIAGNOSES,
        stays=(
            *original.stays,
            *(
                replace(original.stays[0], identifier=f"icu-{i}", department="4260")
                for i in range(MAX_STAYS - 1)
            ),
        ),
    )
    result = registry().execute(key(), facts)
    assert len(result.procedures) == MAX_PROCEDURES
    assert len(result.settlement_products) == MAX_SETTLEMENT_PRODUCTS
    assert len(result.trace) <= MAX_TRACE_DECISIONS
    assert (
        len([d for d in result.trace if d.stage == "procedure_rank"]) == MAX_PROCEDURES
    )
    accumulator = TraceAccumulator()
    for _ in range(MAX_TRACE_DECISIONS):
        accumulator.append(Decision("scope", 1, "unresolved", "preparation_only"))
    with pytest.raises(GrouperError, match="trace exceeds"):
        accumulator.append(Decision("scope", 1, "unresolved", "preparation_only"))


def test_unknown_date_cannot_tie_a_known_minimum_date_in_canonical_order() -> None:
    original = encounter()
    facts = replace(
        original,
        admitted_on=date.min,
        discharged_on=date.min,
        birth_date=None,
        age_completed_years=0,
        stays=(
            replace(original.stays[0], admitted_on=date.min, discharged_on=date.min),
        ),
        procedures=(
            Procedure("00.3", None, "main"),
            Procedure("00.3", date.min, "main"),
        ),
        settlement_products=(
            SettlementProduct("5.52.01.0001363"),
            SettlementProduct("5.52.01.0001363", date.min),
            SettlementProduct("5.52.01.0001363", ended_on=date.min),
        ),
    )
    engine = registry()
    assert engine.execute(key(), facts) == engine.execute(
        key(),
        replace(
            facts,
            procedures=tuple(reversed(facts.procedures)),
            settlement_products=tuple(reversed(facts.settlement_products)),
        ),
    )


@given(
    st.lists(
        st.tuples(
            st.integers(0, 5), st.integers(1, 10), st.booleans(), st.integers(0, 2)
        ),
        max_size=40,
    ),
    st.data(),
)
@settings(max_examples=100, derandomize=True, database=None)
def test_preparation_and_full_trace_are_identical_under_irrelevant_ordering(
    events: list[tuple[int, int, bool, int]],
    choices: st.DataObject,
) -> None:
    original = encounter()
    facts = replace(
        original,
        stays=(
            *original.stays,
            replace(original.stays[0], identifier="icu", department="4260"),
        ),
        procedures=tuple(
            Procedure(
                f"00.{code}",
                date(2026, 10, day) if dated else None,
                ("main", "icu", None)[attribution],
            )
            for code, day, dated, attribution in events
        ),
        settlement_products=tuple(
            SettlementProduct(
                f"5.52.01.{code}",
                date(2026, 10, day) if dated else None,
                date(2026, 10, day) if attribution else None,
            )
            for code, day, dated, attribution in events
        ),
    )
    engine = registry()
    result = engine.execute(key(), facts)
    reordered = replace(
        facts,
        procedures=tuple(choices.draw(st.permutations(facts.procedures))),
        secondary_diagnoses=tuple(reversed(facts.secondary_diagnoses)),
        stays=tuple(reversed(facts.stays)),
        settlement_products=tuple(
            choices.draw(st.permutations(facts.settlement_products))
        ),
    )
    data = algorithm()
    tables = replace(
        data,
        ranks=tuple(reversed(data.ranks)),
        specialties=tuple(reversed(data.specialties)),
    )
    assert registry(tables).execute(key(), reordered) == result
    assert engine.execute(key(), facts) == result
