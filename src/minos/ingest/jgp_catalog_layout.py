"""Reviewed attachment 1a worksheet layouts (46/2026/DSOZ lineage).

Each layout pins every header cell, header merge, scope label and footer legend
line observed in the pinned BAW workbooks. A future act that moves, renames or
adds a column must fail here until a new layout is reviewed; values are never
read by guessing column meaning.
"""

from dataclasses import dataclass

from minos.domain.jgp_catalog import CatalogName, ValueUnit

HEADER_ROWS = 5
FIRST_DATA_ROW = HEADER_ROWS + 1

# The 68 "Zakresy świadczeń" columns are identical in 1a and 1ae.
SCOPE_LABELS = (
    "alergologia / alegologia dla dzieci",
    "alergologia specjalistyczna",
    "alergologia specjalistyczna dla dzieci",
    "anestezjologia i intensywna terapia/AiIT dla dzieci/anestezjologia i intensywna terapia - drugi p. ref/AiIT-drugi p. ref. dla dzieci",
    "angiologia",
    "angiologia specjalistyczna",
    "audiologia i foniatria / audiologia i foniatria dla dzieci",
    "audiologia i foniatria dla dzieci specjalistyczna",
    "chirurgia dziecięca",
    "chirurgia klatki piersiowej/chirurgia klatki piersiowej dla dzieci",
    "chirurgia klatki piersiowej specjalistyczna",
    "chirurgia naczyniowa / chirurgia naczyniowa- drugi p. ref.",
    "chirurgia naczyniowa specjalistyczna",
    "chirurgia naczyniowa specjalistyczna - drugi poziom ref.",
    "chirurgia ogólna",
    "chirurgia onkologiczna / chirurgia onkologiczna dla dzieci",
    "chirurgia onkologiczna specjalistyczna",
    "chirurgia plastyczna / chirurgia plastyczna dla dzieci",
    "chirurgia plastyczna specjalistyczna",
    "chirurgia szczękowo-twarzowa/chirurgia szczękowo-twarzowa dla dzieci",
    "chirurgia szczękowo - twarzowa specjalistyczna",
    "choroby płuc / choroby płuc dla dzieci",
    "choroby płuc specjalistyczne",
    "choroby płuc dla dzieci specjalistyczne",
    "choroby wewnętrzne",
    "choroby zakaźne / choroby zakaźne dla dzieci",
    "dermatologia i wenerologia/dermatologia i wenerologia dla dzieci",
    "dermatologia i wenerologia specjalistyczna",
    "dermatologia i wenerologia dla dzieci specjalistyczna",
    "diabetologia / diabetologia dla dzieci",
    "diabetologia specjalistyczna",
    "diabetologia dla dzieci specjalistyczna",
    "endokrynologia / endokrynologia dla dzieci",
    "endokrynologia dla dzieci specjalistyczna",
    "gastroenterologia / gastroenterologia dla dzieci",
    "gastroenterologia specjalistyczna",
    "geriatria",
    "ginekologia onkologiczna",
    "hematologia",
    "immunologia kliniczna / immunologia kliniczna dla dzieci",
    "immunologia kliniczna dla dzieci specjalistyczna",
    "kardiochirurgia / kardiochirurgia dla dzieci",
    "kardiologia",
    "kardiologia specjalistyczna",
    "kardiologia dla dzieci",
    "kardiologia dla dzieci specjalistyczna",
    "nefrologia / nefrologia dla dzieci",
    "nefrologia specjalistyczna",
    "neonatologia/neonatologia-drugi p.ref./neonatologia-trzeci p. ref.",
    "neurochirurgia / neurochirurgia dla dzieci",
    "neurologia / neurologia dla dzieci",
    "neurologia specjalistyczna",
    "neurologia dla dzieci specjalistyczna",
    "okulistyka / okulistyka dla dzieci",
    "onkologia i hematologia dziecięca",
    "onkologia kliniczna",
    "onkologia kliniczna specjalistyczna",
    "ortopedia i traumat narz ruchu/ortopedia i traumat narz ruchu dla dzieci",
    "otorynolaryngologia / otorynolaryngologia dla dzieci",
    "pediatria",
    "położnictwo i ginekologia/poł. i gin.-drugi p.ref./poł. i gin.-trzeci p.ref.",
    "reumatologia / reumatologia dla dzieci",
    "reumatologia specjalistyczna",
    "reumatologia dla dzieci specjalistyczna",
    "toksykologia kliniczna / toksykologia kliniczna dla dzieci",
    "transplantologia kliniczna/transplantologia kliniczna dla dzieci",
    "urologia / urologia dla dzieci",
    "urologia spcjalistyczna",
)

# The footer defines the procedural marker and the scope-matrix values.
LEGEND = (
    "(*) oznaczenie grup o charakterze zabiegowym",
    '1 - możliwość realizacji danej JGP w pierwszym z zakresów wskazanych w kolumnie "zakresy świadczeń"',
    '2 - możliwość realizacji danej JGP w drugim z zakresów wskazanych w kolumnie "zakresy świadczeń"',
    '3 - możliwość realizacji danej JGP we wszystkich zakresach wskazanych w kolumnie "zakresy świadczeń"',
)

HOSPITALIZATION = "Wartość punktowa - hospitalizacja"
PLANNED = "Wartość punktowa - hospitalizacja planowa"
ONE_DAY_TREATMENT = 'Wartość punktowa - "leczenie jednego dnia"'
FINANCED_STAY = "Liczba dni pobytu finansowana grupą - typ umowy hospitalizacja"
SAME_DAY = "Wartość punktowa hospitalizacji dzień przyjęcia = dzień wypisu - typ umowy hospitalizacja/ hospitalizacja planowa"
ONE_DAY_STAY = "Wartość punktowa hospitalizacji 1-dniowej - typ umowy hospitalizacja/ hospitalizacja planowa"
TWO_DAY_STAY = "Wartość punktowa hospitalizacji 2-dniowej - typ umowy hospitalizacja/ hospitalizacja planowa"
EXTRA_DAY = "Wartość punktowa osobodnia ponad ryczałt finansowany grupą - typ umowy hospitalizacja"
DAY_STAY = "Wartość punktowa - hospitalizacja/ hospitalizacja planowa/ leczenie jednego dnia w ramach pobytu dziennego w oddziale szpitalnym nieprzekraczającego 12 godzin"
EXCLUDING_DAY_STAY = " (z wyłączeniem pobytu dziennego w oddziale szpitalnym nieprzekraczającego 12 godzin)"


@dataclass(frozen=True)
class Measure:
    column: str
    code: str
    unit: ValueUnit


@dataclass(frozen=True)
class CatalogLayout:
    version: str
    catalog: CatalogName
    worksheet: str
    titles: frozenset[str]
    last_column: str
    measures: tuple[Measure, ...]
    first_scope_column: str
    last_scope_column: str
    notes_column: str
    product_prefix: str
    # Rows 1-5 except the title (A2) and the scope labels (row 4).
    header_cells: tuple[tuple[str, str | int], ...]
    header_merges: frozenset[str]


_IDENTITY_HEADERS: tuple[tuple[str, str | int], ...] = (
    ("A3", "Lp."),
    ("B3", "Kod grupy"),
    ("C3", "Kod produktu"),
    ("D3", "Nazwa grupy"),
)
_1A_NUMBERS: tuple[tuple[str, str | int], ...] = tuple(
    (f"{column}5", number)
    for column, number in zip(
        ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "CC"),
        (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15),
        strict=True,
    )
)
_1A_MEASURES = (
    Measure("E", "hospitalization", "points_hundredths"),
    Measure("F", "planned_hospitalization", "points_hundredths"),
    Measure("G", "one_day_treatment", "points_hundredths"),
    Measure("H", "financed_stay", "days"),
    Measure("I", "same_day_discharge", "points_hundredths"),
    Measure("J", "one_day_stay", "points_hundredths"),
    Measure("K", "two_day_stay", "points_hundredths"),
    Measure("L", "extra_stay_day", "points_hundredths"),
)
_1A_COMMON_MERGES = frozenset(
    {
        "A1:CC1",
        "A2:CC2",
        "A3:A4",
        "B3:B4",
        "C3:C4",
        "D3:D4",
        "CC3:CC4",
        "M3:CB3",
        "M5:CB5",
    }
)

# Original act 46/2026: E, J and K headers sit on row 4 without merges.
NFZ_1A_46_2026 = CatalogLayout(
    version="nfz-1a-46-2026",
    catalog="1a",
    worksheet="1a - katalog grup",
    titles=frozenset({"Katalog grup"}),
    last_column="CC",
    measures=_1A_MEASURES,
    first_scope_column="M",
    last_scope_column="CB",
    notes_column="CC",
    product_prefix="5.51.01.",
    header_cells=_IDENTITY_HEADERS
    + (
        ("F3", PLANNED),
        ("G3", ONE_DAY_TREATMENT),
        ("H3", FINANCED_STAY),
        ("I3", SAME_DAY),
        ("L3", EXTRA_DAY),
        ("M3", "Zakresy świadczeń"),
        ("CC3", "Uwagi"),
        ("E4", HOSPITALIZATION),
        ("J4", ONE_DAY_STAY),
        ("K4", TWO_DAY_STAY),
    )
    + _1A_NUMBERS,
    header_merges=_1A_COMMON_MERGES
    | frozenset({"F3:F4", "G3:G4", "H3:H4", "I3:I4", "L3:L4"}),
)

# Consolidated after 74/2026 (also after 102/2026, whose title adds "1a").
NFZ_1A_74_2026 = CatalogLayout(
    version="nfz-1a-74-2026",
    catalog="1a",
    worksheet="1a - katalog grup",
    titles=frozenset({"Katalog grup", "Katalog grup 1a"}),
    last_column="CC",
    measures=_1A_MEASURES,
    first_scope_column="M",
    last_scope_column="CB",
    notes_column="CC",
    product_prefix="5.51.01.",
    header_cells=_IDENTITY_HEADERS
    + (
        ("E3", HOSPITALIZATION),
        ("F3", PLANNED),
        ("G3", ONE_DAY_TREATMENT),
        ("H3", FINANCED_STAY),
        ("I3", SAME_DAY),
        ("J3", ONE_DAY_STAY),
        ("K3", TWO_DAY_STAY),
        ("L3", EXTRA_DAY),
        ("M3", "Zakresy świadczeń"),
        ("CC3", "Uwagi"),
    )
    + _1A_NUMBERS,
    header_merges=_1A_COMMON_MERGES
    | frozenset(
        {"E3:E4", "F3:F4", "G3:G4", "H3:H4", "I3:I4", "J3:J4", "K3:K4", "L3:L4"}
    ),
)

# Catalog 1ae inside attachment 1a, introduced by 74/2026. Its stay measures
# explicitly exclude day stays under 12 hours, so they keep distinct codes.
NFZ_1AE_74_2026 = CatalogLayout(
    version="nfz-1ae-74-2026",
    catalog="1ae",
    worksheet="1ae - katalog grup",
    titles=frozenset({"Katalog grup", "Katalog grup 1ae"}),
    last_column="CA",
    measures=(
        Measure("E", "hospitalization", "points_hundredths"),
        Measure("F", "planned_hospitalization", "points_hundredths"),
        Measure("G", "one_day_treatment", "points_hundredths"),
        Measure("H", "day_stay_under_12_hours", "points_hundredths"),
        Measure("I", "financed_stay_excluding_day_stay", "days"),
        Measure("J", "extra_stay_day_excluding_day_stay", "points_hundredths"),
    ),
    first_scope_column="K",
    last_scope_column="BZ",
    notes_column="CA",
    product_prefix="5.51.02.",
    header_cells=_IDENTITY_HEADERS
    + (
        ("E3", HOSPITALIZATION),
        ("F3", PLANNED),
        ("G3", ONE_DAY_TREATMENT),
        ("H3", DAY_STAY),
        ("I3", FINANCED_STAY + EXCLUDING_DAY_STAY),
        ("J3", EXTRA_DAY + EXCLUDING_DAY_STAY),
        ("K3", "Zakresy świadczeń"),
        ("CA3", "Uwagi"),
    )
    + tuple(
        (f"{column}5", number)
        for column, number in zip(
            ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "CA"),
            (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13),
            strict=True,
        )
    ),
    header_merges=frozenset(
        {
            "A1:CA1",
            "A2:CA2",
            "A3:A4",
            "B3:B4",
            "C3:C4",
            "D3:D4",
            "E3:E4",
            "F3:F4",
            "G3:G4",
            "H3:H4",
            "I3:I4",
            "J3:J4",
            "K3:BZ3",
            "K5:BZ5",
            "CA3:CA4",
        }
    ),
)


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


def check_layout(layout: CatalogLayout) -> CatalogLayout:
    """Every column from A to the last one has exactly one reviewed meaning."""
    first_scope = _column_number(layout.first_scope_column)
    last_scope = _column_number(layout.last_scope_column)
    if last_scope - first_scope + 1 != len(SCOPE_LABELS):
        raise ValueError(f"{layout.version}: scope columns do not match labels")
    assigned = [1, 2, 3, 4]  # Lp., code, product code, name
    assigned += [_column_number(measure.column) for measure in layout.measures]
    assigned += list(range(first_scope, last_scope + 1))
    assigned.append(_column_number(layout.notes_column))
    if sorted(assigned) != list(range(1, _column_number(layout.last_column) + 1)):
        raise ValueError(f"{layout.version}: columns are not covered exactly once")
    if any(int(cell[len(cell.rstrip("0123456789")) :]) > HEADER_ROWS
           for cell, _ in layout.header_cells):  # fmt: skip
        raise ValueError(f"{layout.version}: header cell below the header rows")
    return layout


LAYOUTS = {
    layout.version: check_layout(layout)
    for layout in (NFZ_1A_46_2026, NFZ_1A_74_2026, NFZ_1AE_74_2026)
}
