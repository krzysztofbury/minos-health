from typing import cast

from minos.http.jgp_condition_pages import membership_panel, points_short
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
        "conditions": [f"wymagane wskazanie procedury z listy {code}"],
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

    assert f"od 3{NBSP}177 do 3{NBSP}540{NBSP}pkt" in panel.lead
    assert "2 grupach JGP" in panel.lead
    assert panel.groups.count('class="answer-group" open') == 2
    # The condition travels with the points: the bare number would mislead.
    assert "wymagane wskazanie procedury z listy G35" in panel.groups
    assert 'href="/jgp/2026-10-31/G35"' in panel.groups
    assert "Nazwa G35 *" not in panel.groups


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
    assert "o wartości" not in panel.lead


def test_a_group_without_a_catalog_row_shows_no_points_not_zero() -> None:
    panel = _panel([_answer([_group("N09", None)])])

    assert "brak wiersza katalogu" in panel.groups
    assert "o wartości" not in panel.lead
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
