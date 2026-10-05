# Decode raw JSON independently of the production codec for contract assertions.
# pyright: reportAny=false

import json
from dataclasses import replace

import pytest

from minos.domain import jgp_conditions as c
from minos.domain.release_diff import Change, DiffInputError, compare_facts
from minos.store.diff_conditions import project_sheet
from minos.store.diff_records import Projection
from minos.store.query_budget import QueryBudget


def graph(
    *, shift: int = 0, age: int = 18, guard_second: bool = False
) -> c.Characteristics:
    evidence = (
        c.Evidence(4 + shift, "A", "A01", "A01 Synthetic", "title"),
        c.Evidence(5 + shift, "A", "A01", "Synthetic guarded source clause", "label"),
        c.Evidence(6 + shift, "A", "A01", "ICD-9", "marker"),
        c.Evidence(7 + shift, "A", "A01", "100.01 Synthetic duplicate", "member"),
        c.Evidence(8 + shift, "A", "A01", "100.01 Synthetic duplicate", "member"),
    )
    lists = (
        c.DirectionalList(
            1 + shift,
            "A01",
            "icd9-pl",
            "A01",
            6 + shift,
            (),
            (
                c.Member(7 + shift, "100.01", ("*",)),
                c.Member(8 + shift, "100.01", ("*",)),
            ),
        ),
    )
    selectors = (
        c.Selector(1 + shift, c.ListSelector((1 + shift,), "icd9-pl"), (6 + shift,)),
        c.Selector(2 + shift, c.UnionSelector((1 + shift,), "icd9-pl"), (5 + shift,)),
    )
    conditions = (
        c.Condition(
            1 + shift,
            "A01",
            "procedure_presence",
            c.Presence(2 + shift, None),
            (5 + shift,),
            "proposed",
            None,
        ),
        c.Condition(
            2 + shift,
            "A01",
            "patient_age",
            c.Age("lt", age, "completed_years"),
            (5 + shift,),
            "proposed",
            None,
        ),
        c.Condition(
            3 + shift,
            "A01",
            "procedure_count",
            c.Count(2 + shift, 2, "minimum", "distinct_codes", None, (1 + shift,)),
            (5 + shift,),
            "proposed",
            None,
        ),
        c.Condition(
            4 + shift,
            "A01",
            "when",
            c.Expression((3 + shift, 2 + shift), ((8 if guard_second else 7) + shift,)),
            (5 + shift,),
            "unsupported",
            "guard_occurrence_scope_unreviewed",
        ),
        c.Condition(
            5 + shift,
            "A01",
            None,
            c.Unsupported("source_scope", (4 + shift,)),
            (5 + shift,),
            "unsupported",
            "source_scope",
        ),
    )
    return c.Characteristics(
        "1a",
        "Charakterystyka",
        evidence,
        (c.Group("A01", 4 + shift, (5 + shift,)),),
        lists,
        selectors,
        conditions,
    )


def changes(before: c.Characteristics, after: c.Characteristics) -> tuple[Change, ...]:
    left, right = Projection(QueryBudget(seconds=5)), Projection(QueryBudget(seconds=5))
    project_sheet(before, left)
    project_sheet(after, right)
    return compare_facts("conditions:old:new", left.facts(), right.facts())


def test_all_nested_reference_ids_and_guard_rows_are_remapped() -> None:
    result = changes(graph(), graph(shift=100))
    assert [change.category for change in result] == ["evidence_location"]


def test_threshold_change_retains_old_new_units_and_anchor() -> None:
    result = [
        change
        for change in changes(graph(), graph(age=19))
        if change.category == "condition_graph"
    ]
    assert len(result) == 1
    assert result[0].before is not None and result[0].after is not None
    before, after = json.loads(result[0].before), json.loads(result[0].after)
    for value, age in ((before, 18), (after, 19)):
        node = next(node for node in value["nodes"] if node["kind"] == "patient_age")
        assert node["payload"] == {
            "comparison": "lt",
            "threshold": age,
            "unit": "completed_years",
            "anchor": "admission",
            "boundary_policy": "review_required",
        }


def test_identical_duplicate_member_occurrences_are_not_interchangeable_guards() -> (
    None
):
    result = changes(graph(), graph(guard_second=True))
    assert sum(change.category == "condition_graph" for change in result) == 1


def test_unsupported_source_text_cannot_disappear_when_typed_graph_is_unchanged() -> (
    None
):
    before = graph()
    after = replace(
        before,
        evidence=tuple(
            replace(item, text="Changed unsupported clause")
            if item.category == "label"
            else item
            for item in before.evidence
        ),
    )
    result = changes(before, after)
    changed = next(
        change for change in result if change.category == "condition_evidence"
    )
    assert (
        changed.before is not None
        and "Synthetic guarded source clause" in changed.before
    )
    assert changed.after is not None and "Changed unsupported clause" in changed.after
    assert any(change.category == "condition_evidence_binding" for change in result)


def test_evidence_association_changes_without_changing_cell_population_are_visible() -> (
    None
):
    before = graph()
    after = replace(
        before,
        conditions=(
            replace(before.conditions[0], source_rows=(4,)),
            *before.conditions[1:],
        ),
    )
    result = changes(before, after)
    assert not any(
        change.category in ("condition_graph", "condition_evidence")
        for change in result
    )
    binding = next(
        change for change in result if change.category == "condition_evidence_binding"
    )
    assert (
        binding.before is not None
        and "Synthetic guarded source clause" in binding.before
    )
    assert binding.after is not None and "A01 Synthetic" in binding.after


@pytest.mark.parametrize("alteration", ["cycle", "missing_child"])
def test_ambiguous_or_unresolved_graph_comparison_fails_explicitly(
    alteration: str,
) -> None:
    before = graph()
    target = 5 if alteration == "cycle" else 999
    after = replace(
        before,
        conditions=(
            *before.conditions[:-1],
            replace(
                before.conditions[-1],
                payload=c.Unsupported("source_scope", (target,)),
            ),
        ),
    )
    with pytest.raises(DiffInputError):
        _ = changes(before, after)


def test_repeated_list_declarations_preserve_population_and_explicit_ambiguity() -> (
    None
):
    before = graph()
    after = replace(before, lists=(*before.lists, replace(before.lists[0], identity=2)))
    result = changes(before, after)
    changed = next(change for change in result if change.category == "directional_list")
    assert changed.after is not None
    answer = json.loads(changed.after)
    assert len(answer["declarations"]) == 2
    assert (
        answer["correspondence"]
        == "ambiguous_source_ordered_population_not_semantic_identity"
    )
