import json
from collections.abc import Sequence
from dataclasses import replace
from io import BytesIO
from typing import TYPE_CHECKING, cast

import pytest
from pydantic import ValidationError

from minos.domain import jgp_conditions as c
from minos.domain.jgp_conditions_validation import validate_characteristics
from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.jgp_condition_phrases import interpret_sheet
from minos.ingest.jgp_conditions import parse_conditions_workbook
from minos.ingest.jgp_conditions_codec import decode_condition, encode
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from tests.fixtures.jgp_conditions import manifest, phrase_sheet, workbook

if TYPE_CHECKING:
    from openpyxl.worksheet.worksheet import Worksheet


def test_public_reader_keeps_separate_lists_duplicates_titles_and_source_coordinates() -> (
    None
):
    content = workbook()
    primary, ae = parse_conditions_workbook(BytesIO(content), manifest(content))
    assert (len(primary.groups), len(ae.groups)) == (3, 1)
    shared = next(li for li in primary.lists if li.label == "Lista Ogólna")
    ae_shared = next(li for li in ae.lists if li.label == "Lista Ogólna")
    assert {m.code for m in shared.members} == {"E11.0"}
    assert {m.code for m in ae_shared.members} == {"G60.4"}
    diagnosis = next(
        li for li in primary.lists if li.system == "icd10-pl" and li.context == "A01"
    )
    assert [(m.code, m.row) for m in diagnosis.members] == [
        ("G60.4", 10),
        ("G60.4", 11),
        ("E11.0", 12),
    ]
    assert not any(
        n.kind in ("patient_age", "episode_duration", "any") for n in primary.conditions
    )
    assert [m.code for m in primary.lists[0].members] == ["100.01", "99.97910"]
    assert all(n.source_rows for n in primary.conditions)
    assert {n.group for n in primary.conditions if n.kind == "program_membership"} == {
        "N09",
        "N11",
    }


def test_original_has_its_own_header_and_sheet_identity() -> None:
    content = workbook(original=True)
    parsed = parse_conditions_workbook(
        BytesIO(content), manifest(content, original=True)
    )
    assert len(parsed) == 1 and parsed[0].worksheet == "Charakterystyka"
    wrong = manifest(content)
    with pytest.raises(SourceError, match="worksheet identities"):
        _ = parse_conditions_workbook(BytesIO(content), wrong)


def test_missing_marker_preserves_code_titles_without_parsing_them_as_conditions() -> (
    None
):
    def remove_marker(sheet: Worksheet) -> None:
        if sheet.title == "Charakterystyka 1a":
            sheet["C6"] = None
            sheet["C7"] = (
                "100.01 Synthetic title wiek > 65 r.ż. lub czas pobytu > 2 dni"
            )

    content = workbook(edit=remove_marker)
    selected = manifest(content)
    expectations = selected.sheets["1a"].model_copy(
        update={
            "evidence_cells": 16,
            "unbound_member_occurrences": 2,
            "lists": 2,
            "member_occurrences": 4,
            "unique_members": 3,
            "conditions": 6,
            "kind_counts": {
                "all": 1,
                "diagnosis_presence": 1,
                "program_membership": 2,
                "unsupported": 2,
            },
            "unsupported_reasons": {
                "ancestor_interpretation_unsupported": 1,
                "list_reference_unresolved": 1,
                "child_interpretation_unsupported": 1,
                "list_definition_unbound": 1,
            },
            "unmatched_codes": 1,
        }
    )
    selected = selected.model_copy(
        update={"sheets": {**selected.sheets, "1a": expectations}}
    )
    primary, _ = parse_conditions_workbook(BytesIO(content), selected)
    assert {e.row for e in primary.evidence if e.category == "unbound_member"} == {7, 8}
    assert not any(
        n.kind in ("patient_age", "episode_duration") for n in primary.conditions
    )
    unbound = next(
        n for n in primary.conditions if n.reason == "list_definition_unbound"
    )
    assert unbound.source_rows == (7, 8)


def test_reader_retains_a_label_resuming_after_directional_lists() -> None:
    def resume(sheet: Worksheet) -> None:
        if sheet.title == "Charakterystyka 1a":
            sheet.append(("A", "A01", "wiek > 65 r.ż."))

    content = workbook(edit=resume)
    selected = manifest(content)
    expected = selected.sheets["1a"]
    changed = expected.model_copy(
        update={
            "evidence_cells": 17,
            "conditions": 6,
            "kind_counts": {**expected.kind_counts, "patient_age": 1},
        }
    )
    selected = selected.model_copy(
        update={"sheets": {**selected.sheets, "1a": changed}}
    )
    primary, _ = parse_conditions_workbook(BytesIO(content), selected)
    age = next(n for n in primary.conditions if n.kind == "patient_age")
    group = next(g for g in primary.groups if g.code == "A01")
    assert age.source_rows == (20,) and age.identity in group.roots
    assert len(group.roots) == 2  # separate reference roots, no implicit conjunction


def test_contextual_below_list_uses_its_cited_declaration_not_a_case_folded_name() -> (
    None
):
    def contextual(sheet: Worksheet) -> None:
        if sheet.title == "Charakterystyka 1a":
            sheet["C5"] = (
                "wymagane wskazanie procedury z listy procedur A01a (poniższej) oraz rozpoznania zasadniczego z listy rozpoznań A01"
            )

    content = workbook(edit=contextual)
    selected = manifest(content)
    primary, _ = parse_conditions_workbook(BytesIO(content), selected)
    assert primary.lists[0].label == "A01a" and primary.lists[0].naming_rows == (5,)
    procedure = next(n for n in primary.conditions if n.kind == "procedure_presence")
    assert procedure.status == "proposed"
    assert isinstance(procedure.payload, c.Presence)
    selector = next(
        s for s in primary.selectors if s.identity == procedure.payload.selector_id
    )
    assert isinstance(selector.body, c.ListSelector) and selector.body.list_ids == (1,)
    assert selector.source_rows == (5, 6)


@pytest.mark.parametrize(
    "mutation", ["formula", "new_column", "header", "merge", "duplicate_title"]
)
def test_workbook_structural_drift_refuses_a_publishable_result(mutation: str) -> None:
    def edit(sheet: Worksheet) -> None:
        if mutation == "formula":
            sheet["C7"] = "=1+1"
        elif mutation == "new_column":
            sheet["D7"] = "new evidence"
        elif mutation == "header":
            sheet["C3"] = "changed"
        elif mutation == "merge":
            sheet.merge_cells("A4:C4")
        else:
            sheet.append(("A", "A01", "A01 Duplicate title"))

    content = workbook(edit=edit)
    with pytest.raises(SourceError):
        _ = parse_conditions_workbook(BytesIO(content), manifest(content))


@pytest.mark.parametrize(
    ("group", "text", "lists", "kind", "payload"),
    [
        (
            "A01",
            "wymagane wskazanie procedury z listy procedur A01",
            (("A01", "icd9-pl"),),
            "procedure_presence",
            c.Presence,
        ),
        (
            "H12",
            "wymagane wskazanie dwóch różnych procedur z listy procedur H12",
            (("H12", "icd9-pl"),),
            "procedure_count",
            c.Count,
        ),
        (
            "K47L",
            "co najmniej dwukrotne wszystkich procedur z listy procedur K47La",
            (("K47La", "icd9-pl"),),
            "procedure_all_members",
            c.AllMembers,
        ),
        (
            "N23",
            "co najmniej dwóch procedur pochodzących z rożnych list N23 (a-d)",
            tuple(("N23" + suffix, "icd9-pl") for suffix in "abcd"),
            "procedure_list_coverage",
            c.ListCoverage,
        ),
        (
            "E11",
            "procedury z listy procedur E12G i procedury z listy dodatkowej E1, a także wykonanie w innym terminie w czasie jednej hospitalizacji procedury z listy procedur E11 i z listy dodatkowej E1",
            (("E12G", "icd9-pl"), ("E1", "icd9-pl"), ("E11", "icd9-pl")),
            "procedure_date_relation",
            c.DateRelation,
        ),
        (
            "J02BO",
            "dwukrone wskazanie procedury z listy procedur J02BJa (jednokrotnie dla piersi prawej i jednokrotnie dla piersi lewej)",
            (("J02BJa", "icd9-pl"),),
            "procedure_laterality",
            c.Laterality,
        ),
        (
            "N24",
            "procedur: 99.831 Fototerapia noworodka (czas trwania min 24h)(min 1 raz)",
            (),
            "procedure_duration",
            c.ProcedureDuration,
        ),
        (
            "A01",
            "rozpoznania zasadniczego z listy rozpoznań A01",
            (("A01", "icd10-pl"),),
            "diagnosis_presence",
            c.Presence,
        ),
        (
            "E05G",
            "co najmniej 2 rozpoznań współistniejącychz listy rozpoznań E05G",
            (("E05G", "icd10-pl"),),
            "diagnosis_count",
            c.Count,
        ),
        ("N20", "wiek < 8 tyg. ż.", (), "patient_age", c.Age),
        (
            "S03",
            "czas pobytu > 1 dnia i < 11 dni",
            (),
            "episode_duration",
            c.EpisodeDuration,
        ),
        ("E50", "czas pobytu w OINK > 2 dni", (), "ward_duration", c.WardDuration),
        ("D45", "tryb wypisu 9 - zgon pacjenta", (), "discharge_mode", c.DischargeMode),
        ("D28C", "ECOG ≥ 3", (), "clinical_measure", c.ClinicalMeasure),
        (
            "N09",
            "(dotyczy wyłącznie KOC)",
            (),
            "program_membership",
            c.ProgramMembership,
        ),
        (
            "PZN03",
            "kod ORPHA 157826; wiek < 18 miesiąca życia",
            (),
            "external_disease_code",
            c.ExternalDiseaseCode,
        ),
    ],
)
def test_document_atomic_fixtures_are_typed_without_evaluation(
    group: str,
    text: str,
    lists: tuple[tuple[str, c.System], ...],
    kind: str,
    payload: type[object],
) -> None:
    result = interpret_sheet(phrase_sheet(group, text, lists=lists))
    validate_characteristics(result)
    selected = [node for node in result.conditions if node.kind == kind]
    assert len(selected) == 1 and type(selected[0].payload) is payload
    assert selected[0].source_rows == (5,)


@pytest.mark.parametrize("code", ["N03", "N03A", "N06A", "N06B", "PD01"])
def test_five_title_only_groups_cannot_become_eligibility_conditions(code: str) -> None:
    suffix = "< 18 r.ż." if code == "PD01" else "> 2 dni"
    sheet = interpret_sheet(
        phrase_sheet(
            code,
            "rozpoznania zasadniczego E11.0 Synthetic diagnosis",
            title=code + " Synthetic group " + suffix,
        )
    )
    qualifier = next(
        node for node in sheet.conditions if node.evidence_type == "title_qualifier"
    )
    assert qualifier.kind is None and qualifier.status == "unsupported"
    assert qualifier.reason == "title_only_qualifier" and qualifier.source_rows == (4,)
    assert not any(
        node.kind in ("patient_age", "episode_duration") for node in sheet.conditions
    )


def test_unknown_remainder_and_inline_scope_are_not_an_invented_conjunction() -> None:
    for clause, reason in (
        ("wiek > 17 r.ż. and unreviewed clinical magic", "unconsumed_source_text"),
        # Mixed connectors stay unreviewed even under the 2026-10-07 grammar.
        ("wiek > 17 r.ż. lub, tryb wypisu 9", "boolean_scope_unreviewed"),
    ):
        sheet = interpret_sheet(phrase_sheet("A01", clause))
        root = next(
            node
            for node in sheet.conditions
            if node.identity == sheet.groups[0].roots[0]
        )
        assert root.status == "unsupported" and root.reason == reason
        assert isinstance(root.payload, c.Unsupported) and root.payload.fragments
        assert root.source_rows == (5,)


def test_semicolons_join_parts_and_lub_binds_adjacent_alternatives() -> None:
    # Owner grammar 2026-10-07 (D45 shape): age AND (stay OR death discharge).
    sheet = interpret_sheet(
        phrase_sheet("A01", "wiek > 17 r.ż.; czas pobytu >7 dni lub tryb wypisu 9")
    )
    nodes = {node.identity: node for node in sheet.conditions}
    root = nodes[sheet.groups[0].roots[0]]
    assert (root.kind, root.status) == ("all", "proposed")
    assert isinstance(root.payload, c.Expression)
    first, second = (nodes[i] for i in root.payload.children)
    assert first.kind == "patient_age"
    assert second.kind == "any" and isinstance(second.payload, c.Expression)
    assert [nodes[i].kind for i in second.payload.children] == [
        "episode_duration",
        "discharge_mode",
    ]


def test_missing_unit_and_role_are_explicit_unsupported_evidence() -> None:
    sheet = interpret_sheet(
        phrase_sheet(
            "A47",
            "rozpoznania z listy rozpoznań A47; LVEF <32; EGFR <62; NYHA > II",
            lists=(("A47", "icd10-pl"),),
        )
    )
    # NYHA is typed, but it sits under the unsupported conjunction, so it is
    # unsupported evidence too rather than a stand-alone proposed requirement.
    assert {
        node.reason for node in sheet.conditions if node.kind not in (None, "all")
    } == {
        "diagnosis_role_unspecified",
        "clinical_unit_missing",
        "ancestor_interpretation_unsupported",
    }
    assert all(node.status == "unsupported" for node in sheet.conditions)
    assert any(
        node.kind == "all" and node.status == "unsupported" for node in sheet.conditions
    )
    measures = [
        node.payload
        for node in sheet.conditions
        if isinstance(node.payload, c.ClinicalMeasure)
    ]
    assert [(m.exact_threshold, m.unit_or_scale) for m in measures] == [
        ("32", None),
        ("62", None),
        ("II", "source_ordinal"),
    ]


def test_count_union_is_one_count_and_included_subset_is_not_an_extra_count() -> None:
    union = interpret_sheet(
        phrase_sheet(
            "DD01",
            "3 procedur ze zbioru złożonego z list procedur DD01d i DD01e",
            lists=(("DD01d", "icd9-pl"), ("DD01e", "icd9-pl")),
        )
    )
    assert len([n for n in union.conditions if n.kind == "procedure_count"]) == 1
    assert any(isinstance(s.body, c.UnionSelector) for s in union.selectors)
    included = interpret_sheet(
        phrase_sheet(
            "N03",
            "co najmniej trzech procedur z listy dodatkowej N1 (w tym: 89.393 oraz (88.781 lub 88.789))",
            lists=(("N1", "icd9-pl"),),
        )
    )
    validate_characteristics(included)
    counts = [n for n in included.conditions if n.kind == "procedure_count"]
    assert len(counts) == 1 and isinstance(counts[0].payload, c.Count)
    assert (
        counts[0].payload.required_count == 3
        and len(counts[0].payload.included_node_ids) == 1
    )
    assert {n.kind for n in included.conditions} >= {"all", "any"}


def test_guard_binds_the_exact_marked_member_not_every_use_of_a_code() -> None:
    base = phrase_sheet(
        "N22",
        "(*1) brak możliwości wykazania łącznie z procedurą 39.951",
        lists=(("N22a", "icd9-pl"),),
    )
    evidence = (
        *base.evidence[:2],
        c.Evidence(6, "X", "N22", "ICD-9a", "marker"),
        c.Evidence(7, "X", "N22", "39.271 Synthetic member (*1)", "member"),
        c.Evidence(8, "X", "N22", "39.271 Same code unmarked", "member"),
    )
    definition = c.DirectionalList(
        1,
        "N22a",
        "icd9-pl",
        "N22",
        6,
        (),
        (c.Member(7, "39.271", ("(*1)",)), c.Member(8, "39.271", ())),
    )
    parsed = interpret_sheet(replace(base, evidence=evidence, lists=(definition,)))
    validate_characteristics(parsed)
    guard = next(n for n in parsed.conditions if n.kind == "when")
    assert isinstance(guard.payload, c.Expression) and guard.payload.member_rows == (7,)
    assert guard.status == "unsupported" and guard.source_rows == (5, 7)
    assert any(n.kind == "not" for n in parsed.conditions)


def test_payload_codec_and_graph_refuse_inconsistent_or_unbounded_states() -> None:
    valid = interpret_sheet(phrase_sheet("A01", "wiek > 17 r.ż."))
    node = valid.conditions[0]
    assert decode_condition(encode(node)) == node
    wrong = cast(dict[str, object], json.loads(encode(node)))
    wrong["kind"] = "all"
    altered = decode_condition(
        json.dumps(wrong, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )
    with pytest.raises(SourceError, match="kind conflicts"):
        validate_characteristics(replace(valid, conditions=(altered,)))
    for changed in (
        replace(node, source_rows=()),
        replace(node, payload=c.Age("gt", -1, "completed_years")),
        replace(node, kind="all", payload=c.Expression(())),
        replace(node, kind="not", payload=c.Expression((node.identity,))),
    ):
        with pytest.raises(SourceError):
            validate_characteristics(replace(valid, conditions=(changed,)))
    wrong["extra"] = "ignored would be unsafe"
    with pytest.raises(ArtifactError, match="canonical"):
        _ = decode_condition(
            json.dumps(wrong, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


def test_manifest_counts_do_not_coerce_booleans_and_strings() -> None:
    data = (
        manifest(workbook()).model_dump_json().replace('"groups":3', '"groups":true', 1)
    )
    with pytest.raises(ValidationError):
        _ = ConditionsManifest.model_validate_json(data)


def test_secondary_exemption_is_not_a_secondary_prohibition_or_family_expansion() -> (
    None
):
    sheet = interpret_sheet(
        phrase_sheet(
            "D52A",
            "rozpoznania współistniejącego z listy rozpoznań D52A (przy czym rozpoznanie zasadnicze J80, J95 z rozwinięciami oraz R09.2 nie wymagają podawania rozpoznania współistniejącego z listy D52A)",
            lists=(("D52A", "icd10-pl"),),
        )
    )
    validate_characteristics(sheet)
    guard = next(n for n in sheet.conditions if n.kind == "when")
    assert isinstance(guard.payload, c.Expression) and guard.status == "unsupported"
    nodes = {n.identity: n for n in sheet.conditions}
    outside, secondary = (nodes[i] for i in guard.payload.children)
    assert outside.kind == "not" and outside.status == "unsupported"
    assert (
        secondary.kind == "diagnosis_presence"
        and isinstance(secondary.payload, c.Presence)
        and secondary.payload.role == "secondary"
    )
    family = next(
        s.body for s in sheet.selectors if isinstance(s.body, c.FamilySelector)
    )
    assert (
        isinstance(family, c.FamilySelector)
        and family.category == "J95"
        and family.expansion_policy == "unbound"
    )
    assert not any(
        n.kind == "not"
        and isinstance(n.payload, c.Expression)
        and secondary.identity in n.payload.children
        for n in sheet.conditions
    )


@pytest.mark.parametrize(
    ("annotation", "kind", "reason"),
    [
        (
            "(*11) wykazanie wyłącznie z rozpoznaniami P22.0, P22.1",
            "diagnosis_presence",
            "diagnosis_role_unspecified",
        ),
        (
            "(*13) wykazanie min. 3 krotnie podczas hospitalizacji",
            "procedure_count",
            "rank_compatibility_unbound",
        ),
        ("(*1) LVEF <32", "clinical_measure", "clinical_unit_missing"),
    ],
)
def test_guard_families_preserve_missing_roles_units_and_repeat_rank_binding(
    annotation: str, kind: str, reason: str
) -> None:
    mark = annotation.split()[0]
    group = "E05G" if kind == "clinical_measure" else "N24"
    system: c.System = "icd10-pl" if group == "E05G" else "icd9-pl"
    base = phrase_sheet(group, annotation, lists=((group, system),))
    old = base.lists[0]
    evidence = tuple(
        replace(e, context=group, text=e.text + " " + mark)
        if e.category == "member"
        else e
        for e in base.evidence
    )
    definition = replace(old, members=(replace(old.members[0], marks=(mark,)),))
    parsed = interpret_sheet(replace(base, evidence=evidence, lists=(definition,)))
    validate_characteristics(parsed)
    guard = next(n for n in parsed.conditions if n.kind == "when")
    assert isinstance(guard.payload, c.Expression) and guard.payload.member_rows == (7,)
    consequence = next(
        n for n in parsed.conditions if n.identity == guard.payload.children[1]
    )
    assert (
        consequence.kind == kind
        and consequence.status == "unsupported"
        and consequence.reason == reason
    )


def test_exact_suffix_case_ambiguous_lists_and_dangling_selectors_are_not_guessed() -> (
    None
):
    sheet = interpret_sheet(
        phrase_sheet(
            "A46R",
            "procedury z listy procedur A46r",
            lists=(("A46R", "icd9-pl"), ("A46r", "icd9-pl")),
        )
    )
    validate_characteristics(sheet)
    selected = sheet.selectors[0]
    assert isinstance(selected.body, c.ListSelector) and selected.body.list_ids == (2,)
    broken = replace(
        sheet, selectors=(replace(selected, body=c.ListSelector((999,), "icd9-pl")),)
    )
    with pytest.raises(SourceError, match="unresolved"):
        validate_characteristics(broken)
    duplicate = phrase_sheet(
        "A01",
        "procedury z listy procedur A01",
        lists=(("A01", "icd9-pl"), ("A01", "icd9-pl")),
    )
    unresolved = interpret_sheet(duplicate)
    assert not unresolved.selectors and any(
        n.reason == "list_reference_unresolved" for n in unresolved.conditions
    )


def test_bounded_flat_graph_rejects_depth_and_boolean_thresholds() -> None:
    parsed = interpret_sheet(phrase_sheet("A01", "wiek > 17 r.ż."))
    nodes = [parsed.conditions[0]]
    for identity in range(2, 35):
        nodes.append(
            c.Condition(
                identity,
                "A01",
                "not",
                c.Expression((identity - 1,)),
                (5,),
                "proposed",
                None,
            )
        )
    too_deep = replace(
        parsed,
        conditions=tuple(nodes),
        groups=(replace(parsed.groups[0], roots=(34,)),),
    )
    with pytest.raises(SourceError, match="depth bound"):
        validate_characteristics(too_deep)
    malformed = cast(dict[str, object], json.loads(encode(parsed.conditions[0])))
    cast(dict[str, object], malformed["payload"])["threshold"] = True
    with pytest.raises(ArtifactError, match="stored"):
        _ = decode_condition(json.dumps(malformed))


def _guard_sheet(annotation: str) -> c.Characteristics:
    base = phrase_sheet("N22", annotation, lists=(("N22a", "icd9-pl"),))
    evidence = (
        *base.evidence[:2],
        c.Evidence(6, "X", "N22", "ICD-9a", "marker"),
        c.Evidence(7, "X", "N22", "39.271 Synthetic member (*1)", "member"),
    )
    definition = c.DirectionalList(
        1, "N22a", "icd9-pl", "N22", 6, (), (c.Member(7, "39.271", ("(*1)",)),)
    )
    return interpret_sheet(replace(base, evidence=evidence, lists=(definition,)))


def _reachable_from_unsupported(sheet: c.Characteristics) -> set[int]:
    from minos.domain.jgp_conditions_validation import children

    nodes = {n.identity: n for n in sheet.conditions}
    pending = [n.identity for n in sheet.conditions if n.status == "unsupported"]
    seen: set[int] = set()
    while pending:
        for child in children(nodes[pending.pop()]):
            if child not in seen:
                seen.add(child)
                pending.append(child)
    return seen


@pytest.mark.parametrize(
    "clause",
    [
        "wiek > 17 r.ż. and unreviewed clinical magic",
        "wiek > 17 r.ż. lub, tryb wypisu 9",
        "rozpoznania z listy rozpoznań A01; NYHA > II",
    ],
)
def test_no_proposed_requirement_hides_under_an_unsupported_one(clause: str) -> None:
    sheet = interpret_sheet(phrase_sheet("A01", clause, lists=(("A01", "icd10-pl"),)))
    validate_characteristics(sheet)
    nodes = {n.identity: n for n in sheet.conditions}
    below = _reachable_from_unsupported(sheet)
    assert below  # each clause leaves typed fragments under an unsupported root
    assert all(nodes[i].status == "unsupported" for i in below)
    assert all(n.kind is not None for i, n in nodes.items() if i in below)


def test_validation_refuses_a_proposed_child_of_an_unsupported_parent() -> None:
    sheet = interpret_sheet(phrase_sheet("A01", "wiek > 17 r.ż. and unreviewed"))
    fragment = next(n for n in sheet.conditions if n.kind == "patient_age")
    reopened = replace(fragment, status="proposed", reason=None)
    tampered = tuple(
        reopened if n.identity == fragment.identity else n for n in sheet.conditions
    )
    with pytest.raises(SourceError, match="under unsupported parent"):
        validate_characteristics(replace(sheet, conditions=tampered))


def test_commas_enumerate_clauses_like_oraz_and_lub_binds_tighter() -> None:
    lists: tuple[tuple[str, c.System], ...] = (
        ("A01a", "icd9-pl"),
        ("A01", "icd10-pl"),
    )
    enumerated = interpret_sheet(
        phrase_sheet(
            "A01",
            "wymagane wskazanie procedury z listy procedur A01a, rozpoznania zasadniczego z listy rozpoznań A01, wiek > 17 r.ż.",
            lists=lists,
        )
    )
    validate_characteristics(enumerated)
    root = next(
        n for n in enumerated.conditions if n.identity == enumerated.groups[0].roots[0]
    )
    assert root.kind == "all" and root.status == "proposed"
    alternative = interpret_sheet(
        phrase_sheet(
            "A01",
            "procedury z listy procedur A01a, wiek > 17 r.ż. lub rozpoznania zasadniczego z listy rozpoznań A01",
            lists=lists,
        )
    )
    root = next(
        n
        for n in alternative.conditions
        if n.identity == alternative.groups[0].roots[0]
    )
    # Owner grammar 2026-10-07: procedure AND (age OR principal diagnosis).
    validate_characteristics(alternative)
    nodes = {n.identity: n for n in alternative.conditions}
    assert root.kind == "all" and isinstance(root.payload, c.Expression)
    assert [nodes[i].kind for i in root.payload.children] == [
        "procedure_presence",
        "any",
    ]


@pytest.mark.parametrize(
    "annotation",
    [
        "(*1) brak możliwości wykazania łącznie z procedurą 39.951 w ciągu 30 dni",
        "(*1) brak możliwości wykazania łącznie z procedurą 39.951, chyba że 99.152",
        "(*1) wykazanie wyłącznie z procedurą 93.90 lub 93.91 po 7 dniach",
    ],
)
def test_guard_consequence_needs_the_whole_reviewed_shape(annotation: str) -> None:
    sheet = _guard_sheet(annotation)
    validate_characteristics(sheet)
    # Extra wording must not turn numbers into codes or drop an exception.
    assert not any(n.kind == "not" for n in sheet.conditions)
    selectors = {s.identity: s for s in sheet.selectors}
    assert not any(
        "30" in s.body.codes or "7" in s.body.codes
        for s in selectors.values()
        if isinstance(s.body, c.CodeSelector)
    )


def test_reviewed_guard_shapes_keep_their_exact_codes() -> None:
    sheet = _guard_sheet(
        "(*1) brak możliwości wykazania łacznie z z procedurami: 39.651,39.652, 93.91"
    )
    negated = next(n for n in sheet.conditions if n.kind == "not")
    assert isinstance(negated.payload, c.Expression)
    present = next(
        n for n in sheet.conditions if n.identity == negated.payload.children[0]
    )
    assert isinstance(present.payload, c.Presence)
    selector = next(
        s for s in sheet.selectors if s.identity == present.payload.selector_id
    )
    assert isinstance(selector.body, c.CodeSelector)
    assert selector.body.codes == ("39.651", "39.652", "93.91")


@pytest.mark.parametrize(
    "gap",
    [
        "Fototerapia oraz inna terapia",
        "Fototerapia bez nadzoru",
        "Fototerapia nie dłużej niż",
        "Fototerapia 99.832",
        "Fototerapia; wiek > 2 dni",
    ],
)
def test_procedure_title_gaps_cannot_hide_codes_or_clauses(gap: str) -> None:
    # The gap between a code and its duration qualifier may only be a title;
    # otherwise 99.831 gets no duration node at all (not merely an unsupported
    # one). A code adjacent to the qualifier may still bind to it.
    sheet = interpret_sheet(
        phrase_sheet("N24", f"procedur: 99.831 {gap} (czas trwania min 24h)(min 1 raz)")
    )
    selectors = {s.identity: s.body for s in sheet.selectors}
    timed = {
        code
        for n in sheet.conditions
        if isinstance(n.payload, c.ProcedureDuration)
        for code in cast(c.CodeSelector, selectors[n.payload.selector_id]).codes
    }
    assert "99.831" not in timed


@pytest.mark.parametrize(
    ("title", "label", "title_only"),
    [
        ("A32E Synthetic > 65 r.ż.", "wiek > 17 r.ż.", True),
        ("D10EF Synthetic > 17 r.ż.", "wiek > 17. r.ż.", False),
        ("A48T Synthetic > 3 dni < 8 dni", "czas pobytu > 3 dni i < 8 dni", False),
        ("A48T Synthetic > 3 dni < 8 dni", "czas pobytu > 3 dni", True),
    ],
)
def test_title_bounds_must_each_appear_in_the_label(
    title: str, label: str, title_only: bool
) -> None:
    sheet = interpret_sheet(phrase_sheet(title.split()[0], label, title=title))
    flagged = any(n.reason == "title_only_qualifier" for n in sheet.conditions)
    assert flagged is title_only


def test_a_connector_outside_its_alternatives_is_not_an_any() -> None:
    base = phrase_sheet("A01", "wiek > 17 r.ż.")
    evidence = (
        base.evidence[0],
        c.Evidence(5, "X", "A01", "lub", "connector"),
        c.Evidence(6, "X", "A01", "wiek > 17 r.ż.", "label"),
        c.Evidence(7, "X", "A01", "czas pobytu > 2 dni", "label"),
        *base.evidence[2:],
    )
    shifted = tuple(
        replace(e, row=e.row + 2) if e.category in ("marker", "member") else e
        for e in evidence
    )
    lists = tuple(
        replace(
            item,
            marker_row=item.marker_row + 2,
            members=tuple(replace(m, row=m.row + 2) for m in item.members),
        )
        for item in base.lists
    )
    sheet = interpret_sheet(replace(base, evidence=shifted, lists=lists))
    assert not any(n.kind == "any" for n in sheet.conditions)
    assert any(n.reason == "alternative_scope_unreviewed" for n in sheet.conditions)


@pytest.mark.parametrize("separator", [" i ", " "])
def test_second_age_bound_is_preserved_with_its_explicit_unit(separator: str) -> None:
    sheet = interpret_sheet(phrase_sheet("A01", f"wiek > 17 r.ż.{separator}< 66 r.ż."))
    validate_characteristics(sheet)
    assert [
        (n.payload.comparison, n.payload.threshold)
        for n in sheet.conditions
        if isinstance(n.payload, c.Age)
    ] == [("gt", 17), ("lt", 66)]
    assert all(n.status == "proposed" for n in sheet.conditions)
    assert sheet.conditions[-1].kind == "all"


def test_below_list_binds_the_next_explicit_marker_in_the_same_group() -> None:
    base = phrase_sheet(
        "A01",
        "rozpoznania współistniejącego z poniższej listy",
        lists=(("A01", "icd10-pl"),),
    )
    # The reader records the declaring label row (see the public-reader test).
    sheet = interpret_sheet(
        replace(base, lists=(replace(base.lists[0], naming_rows=(5,)),))
    )
    validate_characteristics(sheet)
    assert sheet.conditions[0].status == "proposed"
    assert sheet.conditions[0].payload == c.Presence(1, "secondary")
    assert sheet.selectors[0].body == c.ListSelector((1,), "icd10-pl")
    assert sheet.selectors[0].source_rows == (5, 6)


@pytest.mark.parametrize("barrier", ["lub", "unreviewed requirement"])
def test_below_list_cannot_skip_a_connector_or_another_requirement(
    barrier: str,
) -> None:
    base = phrase_sheet(
        "A01",
        "rozpoznania współistniejącego z poniższej listy",
        lists=(("A01", "icd10-pl"),),
    )
    shifted = tuple(
        replace(e, row=e.row + 1) if e.row >= 6 else e for e in base.evidence
    )
    extra = c.Evidence(
        6, "X", "A01", barrier, "connector" if barrier == "lub" else "label"
    )
    definition = replace(
        base.lists[0], marker_row=7, members=(replace(base.lists[0].members[0], row=8),)
    )
    sheet = interpret_sheet(
        replace(base, evidence=(*shifted[:2], extra, *shifted[2:]), lists=(definition,))
    )
    assert any(n.reason == "list_reference_unresolved" for n in sheet.conditions)
    assert not sheet.selectors


def test_literal_procedure_title_requires_exact_source_local_evidence() -> None:
    base = phrase_sheet(
        "A01",
        "procedury 00.36 Synthetic member; wiek > 17 r.ż.",
        lists=(("A01", "icd9-pl"),),
    )
    sheet = interpret_sheet(base)
    validate_characteristics(sheet)
    assert all(n.status == "proposed" for n in sheet.conditions)
    assert {n.kind for n in sheet.conditions} == {
        "procedure_presence",
        "patient_age",
        "all",
    }
    unknown = interpret_sheet(
        replace(
            base,
            evidence=tuple(
                replace(e, text=e.text.replace("Synthetic member", "Unreviewed title"))
                if e.category == "label"
                else e
                for e in base.evidence
            ),
        )
    )
    assert all(n.status == "unsupported" for n in unknown.conditions)


def test_public_reader_gives_an_unnamed_below_list_a_physical_identity() -> None:
    def edit(sheet: Worksheet) -> None:
        if sheet.title == "Charakterystyka 1a":
            sheet["C5"] = (
                "procedury z listy procedur A01 oraz rozpoznania zasadniczego z poniższej listy"
            )

    content = workbook(edit=edit)
    primary, _ = parse_conditions_workbook(BytesIO(content), manifest(content))
    diagnosis = next(
        item
        for item in primary.lists
        if item.system == "icd10-pl" and item.context == "A01"
    )
    assert diagnosis.label == "poniższa lista"
    assert diagnosis.naming_rows == (5,)
    assert all(n.status == "proposed" for n in primary.conditions)


@pytest.mark.parametrize(
    "suffix", [" i < 66", " lub < 66 r.ż.", " i < 66 r.ż. chyba że"]
)
def test_second_age_bound_never_consumes_missing_units_or_exceptions(
    suffix: str,
) -> None:
    sheet = interpret_sheet(phrase_sheet("A01", "wiek > 17 r.ż." + suffix))
    assert all(n.status == "unsupported" for n in sheet.conditions)


def _sheet(
    group: str,
    rows: Sequence[tuple[int, str, str, str]],
    lists: list[c.DirectionalList],
) -> c.Characteristics:
    """Hand-built evidence (row, context, text, category) for list placement."""
    evidence = tuple(
        c.Evidence(r, "X", ctx, text, cast(c.EvidenceCategory, cat))
        for r, ctx, text, cat in rows
    )
    return c.Characteristics(
        "1a",
        "Charakterystyka 1a",
        evidence,
        (c.Group(group, 4, ()),),
        tuple(lists),
        (),
        (),
    )


def _icd9_list(
    identity: int, label: str, context: str, marker: int
) -> c.DirectionalList:
    return c.DirectionalList(
        identity,
        label,
        "icd9-pl",
        context,
        marker,
        (),
        (c.Member(marker + 1, "00.36", ()),),
    )


@pytest.mark.parametrize(
    "placement", ["below_same_group", "above_label", "other_group"]
)
def test_ponizszej_suffix_needs_the_named_list_below_in_the_same_group(
    placement: str,
) -> None:
    label = "procedury z listy procedur A01a (poniższej)"
    title = (4, "A01", "A01 Synthetic group", "title")
    if placement == "below_same_group":
        rows = [
            title,
            (5, "A01", label, "label"),
            (6, "A01", "ICD-9", "marker"),
            (7, "A01", "00.36 Synthetic member", "member"),
        ]
        lists = [_icd9_list(1, "A01a", "A01", 6)]
    elif placement == "above_label":
        rows = [
            title,
            (5, "A01", "ICD-9", "marker"),
            (6, "A01", "00.36 Synthetic member", "member"),
            (7, "A01", label, "label"),
        ]
        lists = [_icd9_list(1, "A01a", "A01", 5)]
    else:
        rows = [
            title,
            (5, "A01", label, "label"),
            (6, "B02", "ICD-9", "marker"),
            (7, "B02", "00.36 Synthetic member", "member"),
        ]
        lists = [_icd9_list(1, "A01a", "B02", 6)]
    sheet = interpret_sheet(_sheet("A01", rows, lists))
    validate_characteristics(sheet)
    statuses = {n.status for n in sheet.conditions}
    assert statuses == (
        {"proposed"} if placement == "below_same_group" else {"unsupported"}
    )


@pytest.mark.parametrize(
    "case", ["two_references", "named_heading_between", "declared_elsewhere"]
)
def test_below_list_never_guesses_which_list_is_meant(case: str) -> None:
    text = "rozpoznania zasadniczego z poniższej listy"
    if case == "two_references":
        text += " oraz rozpoznania współistniejącego z poniższej listy"
    rows = [(4, "A01", "A01 Synthetic group", "title"), (5, "A01", text, "label")]
    marker = 6
    if case == "named_heading_between":
        rows.append((6, "A01", "A01b", "list_heading"))
        marker = 7
    rows += [
        (marker, "A01", "ICD-10", "marker"),
        (marker + 1, "A01", "E11.0 Synthetic", "member"),
    ]
    naming = (4,) if case == "declared_elsewhere" else (5,)
    definition = c.DirectionalList(
        1,
        "poniższa lista",
        "icd10-pl",
        "A01",
        marker,
        naming,
        (c.Member(marker + 1, "E11.0", ()),),
    )
    sheet = interpret_sheet(_sheet("A01", rows, [definition]))
    validate_characteristics(sheet)
    assert not any(n.status == "proposed" for n in sheet.conditions)
    assert not sheet.selectors


@pytest.mark.parametrize(
    "clause",
    [
        "wiek < 2 r.ż. i > 65 r.ż.",
        "wiek > 17 r.ż. > 18 r.ż.",
        "wiek > 17 r.ż. < 18 miesięcy życia",
        "wiek > 17 r.ż. i < 18 r.ż.",
    ],
)
def test_two_age_bounds_must_form_a_reviewed_range(clause: str) -> None:
    sheet = interpret_sheet(phrase_sheet("A01", clause))
    validate_characteristics(sheet)
    assert all(n.status == "unsupported" for n in sheet.conditions)
    assert any(n.reason == "age_bounds_unreviewed" for n in sheet.conditions)


def test_a_valid_two_bound_age_range_stays_proposed() -> None:
    sheet = interpret_sheet(phrase_sheet("A01", "wiek > 17 r.ż. i < 66 r.ż."))
    ages = [n for n in sheet.conditions if n.kind == "patient_age"]
    assert len(ages) == 2 and all(n.status == "proposed" for n in ages)


def test_procedure_title_with_a_capitalised_condition_word_is_not_consumed() -> None:
    rows = [
        (4, "A01", "A01 Synthetic group", "title"),
        (5, "A01", "wymagane wskazanie procedury 00.36 Zabieg Wiek dziecięcy", "label"),
        (6, "A01", "ICD-9", "marker"),
        (7, "A01", "00.36 Zabieg Wiek dziecięcy", "member"),
    ]
    sheet = interpret_sheet(_sheet("A01", rows, [_icd9_list(1, "A01a", "A01", 6)]))
    assert all(n.status == "unsupported" for n in sheet.conditions)
