"""Directional candidate derivation over prepared facts (#34), synthetic tables."""

from dataclasses import replace
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from minos.domain.encounter import Diagnosis, Encounter, Procedure, WardStay
from minos.domain.grouper import GrouperError, PreparationResult
from minos.domain.grouper_candidates import (
    DirectionalIndex,
    DirectionalRequirement,
    GroupDirections,
    GroupingSources,
    MatchRole,
)
from minos.grouper.candidates import derive
from tests.fixtures.grouper import encounter, key, registry

SOURCES = GroupingSources(
    "synthetic-dataset",
    date(2026, 10, 5),
    "catalog",
    "characteristics",
    "icd9",
    "icd10",
    "algorithm",
)


def requirement(
    node: int,
    codes: set[str],
    *,
    kind: str = "procedure",
    role: MatchRole = "any",
    families: tuple[str, ...] = (),
) -> DirectionalRequirement:
    return DirectionalRequirement(
        node,
        "diagnosis" if kind == "diagnosis" else "procedure",
        role,
        frozenset(codes),
        families,
        (node,),
        (100 + node,),
    )


def index(*groups: GroupDirections) -> DirectionalIndex:
    return DirectionalIndex("characteristics", "1a", groups)


def prepared(facts: Encounter | None = None) -> PreparationResult:
    return registry().execute(key(), facts or encounter())


def test_principal_role_lists_match_only_the_principal_diagnosis() -> None:
    # The fixture's principal is A00 and its stay-attributed secondary is B00.
    groups = index(
        GroupDirections("P01", 1, (requirement(1, {"B00"}, kind="diagnosis", role="principal"),), True),
        GroupDirections("P02", 2, (requirement(2, {"B00"}, kind="diagnosis"),), True),
        GroupDirections("P03", 3, (requirement(3, {"A00"}, kind="diagnosis", role="principal"),), True),
    )  # fmt: skip
    result = derive(prepared(), (groups,), SOURCES)
    assert [(c.group, c.basis) for c in result.candidates] == [
        ("P02", "retained_fact"),
        ("P03", "retained_fact"),
    ]
    assert result.candidates[0].conditions == "not_evaluated"
    assert result.scope == "directional_candidates_only"


def test_excluded_facts_never_count_and_unresolved_facts_only_flag() -> None:
    original = encounter()
    icu = WardStay("icu", "4260", date(2026, 10, 3), date(2026, 10, 5))
    facts = replace(
        original,
        stays=(*original.stays, icu),
        procedures=(
            Procedure("00.1", date(2026, 10, 4), "icu"),  # excluded stay
            Procedure("00.2"),  # unattributed while an exclusion applies
            Procedure("99.99", date(2026, 10, 2), "main"),  # no SZP rank
            Procedure("00.0", date(2026, 10, 2), "main"),  # rank 0 is ignored
            Procedure("00.3", date(2026, 10, 2), "main"),
        ),
    )
    groups = index(
        GroupDirections("E01", 1, (requirement(1, {"00.1"}),), True),
        GroupDirections("E02", 2, (requirement(2, {"00.2"}),), True),
        GroupDirections("E03", 3, (requirement(3, {"99.99"}),), False),
        GroupDirections("E04", 4, (requirement(4, {"00.0"}),), True),
        GroupDirections("E05", 5, (requirement(5, {"00.2"}), requirement(6, {"00.3"})), True),
    )  # fmt: skip
    result = derive(prepared(facts), (groups,), SOURCES)
    assert [(c.group, c.basis, c.matched_nodes) for c in result.candidates] == [
        ("E02", "unresolved_fact", (2,)),
        ("E03", "unresolved_fact", (3,)),
        # A retained match outranks the unresolved one and is the only cited node.
        ("E05", "retained_fact", (6,)),
    ]
    assert result.candidates[1].fully_interpreted is False
    assert result.candidates[2].evidence_rows == (106,)


def test_classification_family_is_never_expanded_into_a_retained_match() -> None:
    family = requirement(1, set(), kind="diagnosis", families=("A00",))
    result = derive(
        prepared(replace(encounter(), principal_diagnosis=Diagnosis("A00.1", "main"))),
        (index(GroupDirections("F01", 1, (family,), True)),),
        SOURCES,
    )
    assert [(c.group, c.basis) for c in result.candidates] == [
        ("F01", "unresolved_fact")
    ]
    unrelated = derive(
        prepared(replace(encounter(), principal_diagnosis=Diagnosis("A001", "main"))),
        (index(GroupDirections("F01", 1, (family,), True)),),
        SOURCES,
    )
    assert unrelated.candidates == ()


def test_group_without_directional_list_is_reported_not_dropped() -> None:
    result = derive(
        prepared(),
        (index(GroupDirections("N09", 1, (), True)),),
        SOURCES,
    )
    assert result.candidates == ()
    assert result.underivable == (("1a", "N09", "no_directional_requirement"),)


def test_indexes_must_belong_to_the_resolved_sources() -> None:
    other = DirectionalIndex(
        "other-release", "1a", (GroupDirections("A01", 1, (), True),)
    )
    with pytest.raises(GrouperError, match="conflict"):
        _ = derive(prepared(), (other,), SOURCES)
    twice = index(GroupDirections("A01", 1, (), True))
    with pytest.raises(GrouperError, match="conflict"):
        _ = derive(prepared(), (twice, twice), SOURCES)


def test_requirement_without_codes_or_families_is_refused() -> None:
    with pytest.raises(GrouperError, match="no codes"):
        _ = requirement(1, set())


GROUPS = (
    GroupDirections("D01", 1, (requirement(1, {"00.1", "00.4"}),), True),
    GroupDirections("D02", 2, (requirement(2, {"B00"}, kind="diagnosis"),), True),
    GroupDirections("D03", 3, (requirement(3, {"C00"}, kind="diagnosis"),), False),
    GroupDirections("D04", 4, (), True),
)


@settings(max_examples=50, database=None, deadline=None)
@given(
    groups=st.permutations(GROUPS),
    procedures=st.permutations(encounter().procedures),
    secondary=st.permutations(encounter().secondary_diagnoses),
)
def test_candidates_are_identical_for_any_input_order(
    groups: list[GroupDirections],
    procedures: list[Procedure],
    secondary: list[Diagnosis],
) -> None:
    expected = derive(prepared(), (index(*GROUPS),), SOURCES)
    shuffled = derive(
        prepared(
            replace(
                encounter(),
                procedures=tuple(procedures),
                secondary_diagnoses=tuple(secondary),
            )
        ),
        (index(*groups),),
        SOURCES,
    )
    assert shuffled.candidates == expected.candidates
    assert shuffled.underivable == expected.underivable
    assert shuffled.preparation.trace == expected.preparation.trace
