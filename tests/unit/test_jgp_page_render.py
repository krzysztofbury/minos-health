from html.parser import HTMLParser
from typing import final, override

import pytest

from minos.domain.reference import ArtifactError
from minos.http.jgp_pages import (
    days_pl,
    error_page,
    index_page,
    measure_labels,
    points_pl,
)
from minos.ingest.jgp_catalog_layout import NFZ_1A_74_2026, NFZ_1AE_74_2026

NBSP = " "
CALENDAR = ["2026-05-01", "2026-07-01", "2027-01-01"]
RULESET: dict[str, object] = {
    "service_date": "2026-07-10",
    "knowledge_as_of": "2026-10-02",
    "service_date_after_knowledge": False,
    "act_number": "46/2026/DSOZ",
    "published_on": "2026-07-23",
    "service_from": "2026-07-01",
    "service_evidence": "Synthetic evidence <b>",
    "workbook_sha256": "a" * 64,
    "baw_document_id": 43943,
    "document_url": "https://baw.nfz.gov.pl/NFZ/document/43943/X",
}


@pytest.mark.parametrize(
    ("hundredths", "text"),
    [
        (1287172, f"12{NBSP}871,72"),
        (64100, "641,00"),
        (5, "0,05"),
        (10_000_000, f"100{NBSP}000,00"),
    ],
)
def test_points_use_polish_format_from_exact_hundredths(
    hundredths: int, text: str
) -> None:
    assert points_pl(hundredths) == text


def test_financed_days_have_their_own_unit() -> None:
    assert (days_pl(1), days_pl(21)) == (f"1{NBSP}dzień", f"21{NBSP}dni")


def test_measure_labels_quote_the_reviewed_source_headers() -> None:
    labels = measure_labels(NFZ_1A_74_2026)
    assert labels["hospitalization"] == "Wartość punktowa - hospitalizacja"
    assert (
        labels["financed_stay"]
        == "Liczba dni pobytu finansowana grupą - typ umowy hospitalizacja"
    )
    assert len(labels) == len(NFZ_1A_74_2026.measures)
    assert (
        "nieprzekraczającego 12 godzin"
        in measure_labels(NFZ_1AE_74_2026)["day_stay_under_12_hours"]
    )


def test_source_text_is_escaped_and_labelled_as_base_points() -> None:
    group: dict[str, object] = {
        "catalog": "1a",
        "chapter_position": 1,
        "chapter": "Rozdział <i>",
        "code": "A01",
        "product_code": "5.51.01.0001001",
        "name": "<script>alert(1)</script> *",
        "procedural": True,
        "hospitalization_hundredths": None,
        "scope_marker": None,
    }
    page = index_page(RULESET, [group], [], None, CALENDAR)
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "Rozdział &lt;i&gt;" in page
    assert "brak wartości" in page  # absent points are not zero
    assert "Punkty bazowe wg katalogu, bez modyfikatorów." in page
    assert 'content="noindex, follow"' in page
    assert '<script src="/jgp.js" defer></script>' in page
    assert "jgp-horizon" not in page  # within the knowledge horizon


def test_after_horizon_dates_carry_a_visible_notice() -> None:
    page = index_page(
        {**RULESET, "service_date_after_knowledge": True}, [], [], None, CALENDAR
    )
    assert 'jgp-horizon">Stan wiedzy Minos: 2026-10-02' in page
    assert "Brak grup dla tego wyboru w tym dniu." in page


def test_error_pages_explain_without_substituting_another_release() -> None:
    page = error_page(404, "no_ruleset_for_date", "2026-04-30", "PZD02", CALENDAR)
    assert "Brak zweryfikowanego katalogu JGP dla tego dnia" in page
    assert "nie podstawia innego wydania" in page
    assert "obejmuje świadczenia od 2026-05-01" in page
    assert 'data-suffix="/PZD02"' in page  # the date picker keeps the group
    unknown = error_page(404, "not_found", None, None)
    assert "Nie znaleziono strony" in unknown and 'id="jgp-date"' not in unknown


def group(catalog: str, chapter: int, code: str, name: str) -> dict[str, object]:
    return {
        "catalog": catalog,
        "chapter_position": chapter,
        "chapter": f"Rozdział {chapter}",
        "code": code,
        "product_code": "5.51.01.0001001",
        "name": name,
        "procedural": False,
        "hospitalization_hundredths": 100,
        "scope_marker": 2,
    }


@final
class Balance(HTMLParser):
    VOID = frozenset({"meta", "link", "input", "br", "img"})

    def __init__(self) -> None:
        super().__init__()
        self.open: list[str] = []
        self.attributes: list[tuple[str, str | None]] = []

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.attributes.extend(attrs)
        if tag not in self.VOID:
            self.open.append(tag)

    @override
    def handle_endtag(self, tag: str) -> None:
        assert self.open.pop() == tag


def test_catalog_markup_is_balanced_and_quotes_stay_in_attributes() -> None:
    groups = [
        group("1a", 1, "A01", "Nazwa \"cudzysłów\" i 'apostrof'"),
        group("1a", 2, "H21", "Druga"),
        group("1ae", 1, "AU35C", "Trzecia"),
        group("1ae", 2, "SU52", "Czwarta"),
    ]
    scope: dict[str, object] = {"position": 9, "label": "chirurgia dziecięca"}
    page = index_page(RULESET, groups, [scope], scope, CALENDAR)
    parser = Balance()
    parser.feed(page)
    parser.close()
    assert parser.open == []
    assert ("data-search", "A01 Nazwa \"cudzysłów\" i 'apostrof'") in parser.attributes
    assert page.count('class="jgp-catalog-part"') == 2
    assert page.count("drugi z zakresów wymienionych w nagłówku") == 4


def test_unexpected_bundle_shapes_fail_instead_of_rendering() -> None:
    reopened = [
        group("1a", 1, "A01", "a"),
        group("1a", 2, "H21", "b"),
        group("1a", 1, "J01", "c"),
    ]
    with pytest.raises(ArtifactError):
        _ = index_page(RULESET, reopened, [], None, CALENDAR)
    scope: dict[str, object] = {"position": 9, "label": "x"}
    with pytest.raises(ArtifactError):
        _ = index_page(
            RULESET,
            [{**group("1a", 1, "A01", "a"), "scope_marker": 4}],
            [scope],
            scope,
            CALENDAR,
        )
