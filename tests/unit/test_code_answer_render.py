from typing import cast

import pytest

from minos.http.jgp_condition_pages import (
    conditions_section,
    membership_panel,
    points_short,
)
from minos.http.pages import JgpPanel

NBSP = " "
Json = dict[str, object]


def _group(code: str, points: int | None, *, procedural: bool = True) -> Json:
    return {
        "code": code,
        "source_title": f"{code} Tytuł źródłowy",
        "fully_interpreted": True,
        "name": None if points is None else f"Nazwa {code} *",
        "procedural": None if points is None else procedural,
        "base_values": []
        if points is None
        else [
            {
                "measure": "hospitalization",
                "unit": "points_hundredths",
                "amount": points,
            },
            {
                "measure": "planned_hospitalization",
                "unit": "points_hundredths",
                "amount": 200000,
            },
        ],
        "catalog_notes": None,
        "conditions": [
            {"text": f"wymagane wskazanie procedury z listy {code}", "connector": None}
        ],
    }


def _answer(
    groups: list[Json], *, catalog: str = "1a", total: int | None = None
) -> Json:
    return {
        "status": "covered",
        "page": 1,
        "total": len(groups) if total is None else total,
        "groups": groups,
        "source": {
            "catalog": catalog,
            "act_number": "46/2026/DSOZ",
            "worksheet": "Charakterystyka",
            "source_url": "https://baw.nfz.gov.pl/api/file/4/1",
            "release_id": "r",
            "sha256": "a" * 64,
            "publisher": "NFZ",
            "license": "L",
            "notices": [],
            "service_date_after_knowledge": False,
            "knowledge_as_of": "2026-10-13",
        },
    }


def _panel(answers: list[Json], *, slug: str = "icd-9") -> JgpPanel:
    return membership_panel(
        answers, slug=slug, code="52.191", day="2026-10-31", date_bar="", shared=""
    )


def test_points_keep_a_real_fraction_and_drop_an_empty_one() -> None:
    assert points_short(317700) == f"3{NBSP}177"
    assert points_short(317750) == f"3{NBSP}177,50"


def test_a_few_groups_open_with_points_condition_and_the_value_range() -> None:
    panel = _panel([_answer([_group("G35", 317700), _group("Z01", 354000)])])

    assert (
        f"hospitalizacja <strong>od 3{NBSP}177 do 3{NBSP}540{NBSP}pkt</strong>"
        in panel.lead
    )
    assert "2 grupach JGP" in panel.lead
    assert panel.groups.count('class="answer-group" open') == 2
    # The condition travels with the points: the bare number would mislead.
    assert "wymagane wskazanie procedury z listy G35" in panel.groups
    assert 'href="/jgp/2026-10-31/G35"' in panel.groups
    assert "Nazwa G35 *" not in panel.groups
    assert "Brak pozycji nie dowodzi wykluczenia" in panel.lead


def test_a_long_list_closes_its_groups_and_splits_them_by_kind() -> None:
    groups = [_group(f"A{index:02}", 100000) for index in range(5)]
    groups += [_group(f"B{index:02}", 100000, procedural=False) for index in range(4)]
    panel = _panel([_answer(groups)])

    assert 'class="answer-group" open' not in panel.groups
    assert panel.groups.count('class="answer-group"') == 9
    assert "Grupy zabiegowe <span>5</span>" in panel.groups
    assert "Grupy zachowawcze <span>4</span>" in panel.groups
    assert "9 grupach JGP" in panel.lead
    # A span across this many groups says nothing.
    assert "hospitalizacja" not in panel.lead


def test_a_group_without_a_catalog_row_shows_no_points_not_zero() -> None:
    panel = _panel([_answer([_group("N09", None)])])

    assert "brak wiersza katalogu" in panel.groups
    assert "hospitalizacja" not in panel.lead
    assert "Tytuł źródłowy" in panel.groups
    assert 'class="pts"' not in panel.groups


def test_no_listed_group_is_not_called_an_exclusion_proof() -> None:
    panel = _panel([_answer([])])

    assert "Brak na jawnych listach grup JGP" in panel.lead
    assert "Nie jest to dowód wykluczenia" in panel.lead
    assert panel.has_groups is False


def test_the_count_is_the_total_not_the_page() -> None:
    panel = _panel([_answer([_group("A01", 100000)], total=120)])

    assert "120 grupach JGP" in panel.lead
    assert "/slowniki/icd-9/52.191/jgp/2026-10-31/1a/2" in panel.groups


def test_a_diagnosis_page_points_to_the_procedure_page() -> None:
    panel = _panel([_answer([_group("A01", 100000)])], slug="icd-10")

    assert "To rozpoznanie występuje" in panel.lead
    assert "Sprawdź także stronę wykonanej procedury ICD-9" in panel.lead


def test_an_uncovered_catalog_says_so_beside_the_covered_one() -> None:
    uncovered = cast(Json, {"status": "context_not_covered", "catalog": "1ae"})
    panel = _panel([_answer([_group("A01", 100000)]), uncovered])

    assert "Katalog 1ae: brak zweryfikowanej charakterystyki" in panel.lead


@pytest.mark.parametrize(("count", "opened"), [(6, True), (7, False)])
def test_groups_open_up_to_six(count: int, opened: bool) -> None:
    panel = _panel([_answer([_group(f"A{i:02}", 100000) for i in range(count)])])

    assert ('class="answer-group" open' in panel.groups) is opened
    assert ("po rozwinięciu grupy" in panel.groups) is not opened


@pytest.mark.parametrize(("count", "shown"), [(8, True), (9, False)])
def test_the_value_range_stops_above_eight_groups(count: int, shown: bool) -> None:
    panel = _panel([_answer([_group(f"A{i:02}", 100000) for i in range(count)])])

    assert ("hospitalizacja" in panel.lead) is shown


def test_the_range_says_when_it_covers_only_some_groups() -> None:
    panel = _panel([_answer([_group("A01", 100000), _group("N09", None)])])

    assert f": w 1 z nich hospitalizacja <strong>1{NBSP}000{NBSP}pkt" in panel.lead


def test_a_group_without_a_row_is_neither_procedural_nor_conservative() -> None:
    groups = [_group(f"A{i:02}", 100000, procedural=False) for i in range(7)]
    panel = _panel([_answer([*groups, _group("N09", None)])])

    assert "Grupy zachowawcze <span>7</span>" in panel.groups
    assert "Bez wiersza katalogu na ten dzień <span>1</span>" in panel.groups


def test_the_source_lub_stays_between_its_clauses_and_all_clauses_show() -> None:
    group = _group("A03", 100000)
    group["conditions"] = [
        {"text": "lista A03 i A05", "connector": None},
        {"text": "lista A03A", "connector": "lub"},
        {"text": "NYHA > II", "connector": None},
    ]
    groups = _panel([_answer([group])]).groups

    assert groups.index("lista A03 i A05") < groups.index(">lub<")
    assert groups.index(">lub<") < groups.index("lista A03A")
    assert groups.count(">lub<") == 1
    assert "NYHA &gt; II" in groups


def test_catalog_notes_sit_beside_the_points_and_source_text_is_escaped() -> None:
    group = _group("A01", 100000)
    group["catalog_notes"] = "nie może być rozliczana < 2 dni"
    group["name"] = "Zabiegi <b> *"
    groups = _panel([_answer([group])]).groups

    assert "nie może być rozliczana &lt; 2 dni" in groups
    assert "Zabiegi &lt;b&gt;<" in groups


def test_a_closed_row_still_says_it_is_not_interpreted() -> None:
    group = _group("A01", 100000)
    group["fully_interpreted"] = False
    groups = _panel([_answer([group])]).groups
    summary = groups[: groups.index("</summary>")]

    assert "jeszcze nie zinterpretowano" in summary


def test_a_diagnosis_day_past_the_sources_says_so() -> None:
    answer = _answer([_group("A01", 100000)])
    cast(Json, answer["source"])["service_date_after_knowledge"] = True
    panel = _panel([answer])

    assert "Według źródeł sprawdzonych do 13.10.2026" in panel.lead


def test_nothing_covered_is_not_reported_as_not_listed() -> None:
    uncovered = cast(Json, {"status": "context_not_covered", "catalog": "1a"})
    panel = _panel([uncovered])

    assert "Brak zweryfikowanej charakterystyki JGP" in panel.lead
    assert "Brak na jawnych listach" not in panel.lead


def test_a_paged_list_counts_its_sections_on_the_page() -> None:
    groups = [_group(f"A{i:02}", 100000) for i in range(7)]
    panel = _panel([_answer(groups, total=120)])

    assert "7 na tej stronie" in panel.groups
    assert "120 grupach JGP" in panel.lead


def test_a_group_page_shows_only_its_own_clauses_as_the_condition() -> None:
    def row(number: int, text: str, category: str, *, own: bool = True) -> Json:
        return {"row": number, "text": text, "category": category, "own": own}

    page = conditions_section(
        {
            "status": "not_yet_interpreted",
            "source": cast(Json, _answer([])["source"])
            | {"service_date": "2026-10-31", "catalog": "1a"},
            "lists": [],
            "source_text": [
                row(1, "cytowana lista C22B", "label", own=False),
                row(2, "lista C22", "label"),
                row(3, "lub", "connector"),
                row(4, "lista C22A", "label"),
            ],
        }
    )
    head = page[: page.index("Zapis źródłowy")]

    assert '<p class="cond-clause">lista C22</p>' in head
    assert '<p class="cond-or">lub</p>' in head
    assert "cytowana lista C22B" not in head
    # The cited row stays in the source record.
    assert "cytowana lista C22B" in page
