import json
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
    expected = selected.sheets["1a"]
    changed = expected.model_copy(
        update={
            "kind_counts": {
                "procedure_presence": 1,
                "diagnosis_presence": 1,
                "program_membership": 2,
                "unsupported": 1,
            },
            "unsupported_reasons": {"unconsumed_source_text": 1},
        }
    )
    selected = selected.model_copy(
        update={"sheets": {**selected.sheets, "1a": changed}}
    )
    primary, _ = parse_conditions_workbook(BytesIO(content), selected)
    assert primary.lists[0].label == "A01a" and primary.lists[0].naming_rows == (5,)
    procedure = next(n for n in primary.conditions if n.kind == "procedure_presence")
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
        ("wiek > 17 r.ż. lub tryb wypisu 9", "boolean_scope_unreviewed"),
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


def test_missing_unit_and_role_are_explicit_unsupported_evidence() -> None:
    sheet = interpret_sheet(
        phrase_sheet(
            "A47",
            "rozpoznania z listy rozpoznań A47; LVEF <32; EGFR <62; NYHA > II",
            lists=(("A47", "icd10-pl"),),
        )
    )
    assert {
        node.reason for node in sheet.conditions if node.kind not in (None, "all")
    } == {"diagnosis_role_unspecified", "clinical_unit_missing", None}
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
