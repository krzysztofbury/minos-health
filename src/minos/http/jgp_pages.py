"""Server-rendered JGP catalog pages (#26): one resolved release per page.

Every page states the service date, the governing act and catalog release.
Values are catalog base points without modifiers; empty catalog cells read
"brak wartości w katalogu", never zero.
"""

from collections import Counter
from typing import Final, cast

from minos.domain.reference import ArtifactError
from minos.http.jgp_condition_pages import (
    code_link,
    conditions_section,
    source_facts,
    source_positions,
)
from minos.http.pages import (
    RETRY_HEAD,
    _e,  # pyright: ignore[reportPrivateUsage]
    _page,  # pyright: ignore[reportPrivateUsage]
    display_date,
    polish_date_input,
)
from minos.ingest.jgp_catalog_layout import (
    LEGEND,
    NFZ_1A_74_2026,
    NFZ_1AE_74_2026,
    CatalogLayout,
)

SHELL: Final = "page_shell_jgp.html"
ROBOTS: Final = "noindex, follow"
BASE_POINTS_LABEL: Final = "punkty bazowe wg katalogu, bez modyfikatorów"
MODIFIERS_NOTE: Final = (
    "To nie są punkty do rozliczenia. Modyfikatory (MODTAR) nie są jeszcze"
    + " powiązane, co nie oznacza, że nie mają zastosowania."
)
MARKERS: Final = {
    1: "pierwszy z zakresów wymienionych w nagłówku",
    2: "drugi z zakresów wymienionych w nagłówku",
    3: "wszystkie zakresy wymienione w nagłówku",
}
LAYOUTS: Final = {"1a": NFZ_1A_74_2026, "1ae": NFZ_1AE_74_2026}
# Measure headers sit in source row 3; row 5 holds column letters.
HEADER_ROW: Final = "3"
NBSP: Final = " "


def points_pl(hundredths: int) -> str:
    """Polish points text from exact hundredths: 1287172 -> "12 871,72"."""
    whole, fraction = divmod(hundredths, 100)
    grouped = f"{whole:,}".replace(",", NBSP)
    return f"{grouped},{fraction:02d}"


def days_pl(days: int) -> str:
    return f"{days}{NBSP}{'dzień' if days == 1 else 'dni'}"


def measure_labels(layout: CatalogLayout) -> dict[str, str]:
    """Measure code -> its source column header, quoted from the layout."""
    headers = {
        coordinate.rstrip("0123456789"): text
        for coordinate, text in layout.header_cells
        if isinstance(text, str)
        and coordinate[len(coordinate.rstrip("0123456789")) :] == HEADER_ROW
    }
    return {measure.code: headers[measure.column] for measure in layout.measures}


def _jgp_page(
    *, title: str, description: str, path: str, content: str, head: str = ""
) -> str:
    return _page(
        title=title,
        description=description,
        path=path,
        content=content,
        head='    <script src="/datepicker.js" defer></script>\n'
        + '    <script src="/jgp.js" defer></script>\n'
        + head,
        robots=ROBOTS,
        template=SHELL,
    )


def marker_meaning(marker: object) -> str:
    """The legend text of a scope marker; the bundle allows only 1-3."""
    if not isinstance(marker, int) or marker not in MARKERS:
        raise ArtifactError("catalog scope marker is outside the reviewed legend")
    return MARKERS[marker]


CHEVRON: Final = (
    '<span class="tree-toggle" aria-hidden="true">'
    + '<svg viewBox="0 0 12 12"><path d="M4 2l4 4-4 4"/></svg></span>'
)


def plural_pl(count: int, one: str, few: str, many: str) -> str:
    """Polish count agreement: 1 grupa, 2 grupy, 5 grup, 22 grupy, 12 grup."""
    if count == 1:
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many


def date_bar(
    service_date: str, suffix: str, calendar: list[str], *, prefix: str = "/jgp/"
) -> str:
    """Date choice without a form or query string: links, enhanced by jgp.js.

    `calendar` lists the days on which the bundle's answer can change; the
    first is the earliest covered service date.
    """
    links = "".join(
        f'<li><a class="dictionary-tab" href="{_e(prefix)}{_e(day)}{_e(suffix)}">{_e(display_date(day))}</a></li>'
        for day in calendar
        if day != service_date
    )
    return (
        '        <div class="jgp-date">\n'
        + '          <label class="tree-search-label" for="jgp-date">Dzień udzielenia świadczenia</label>\n'
        + '          <div class="jgp-date-row">'
        + polish_date_input(
            "jgp-date",
            service_date,
            "Dzień udzielenia świadczenia",
            f' data-min="{_e(calendar[0])}" data-prefix="{_e(prefix)}"'
            + f' data-suffix="{_e(suffix)}"',
            input_class="tree-search-input",
        )
        + (
            f'<ul class="jgp-quick" aria-label="Dni zmian w katalogu">{links}</ul>'
            if links
            else ""
        )
        + "</div>\n"
        + "        </div>\n"
    )


def provenance(ruleset: dict[str, object], *, heading: str = "h2") -> str:
    binding = cast(dict[str, object], ruleset["modifiers"])
    modifier_status = (
        f"powiązane referencyjnie: {binding['release_id']}, generacja {binding['generation']}; Minos nie liczy punktów do rozliczenia"
        if binding["binding"] == "reviewed"
        else "niepowiązane (#46); Minos nie liczy punktów do rozliczenia"
    )
    facts = [
        ("Zarządzenie Prezesa NFZ", ruleset["act_number"]),
        ("Opublikowano", display_date(ruleset["published_on"])),
        ("Stosuje się do świadczeń od", display_date(ruleset["service_from"])),
        ("Podstawa", ruleset["service_evidence"]),
        ("SHA-256 skoroszytu", ruleset["workbook_sha256"]),
        ("Modyfikatory", modifier_status),
    ]
    rows = "".join(
        f"<div><dt>{_e(name)}</dt><dd>{_e(value)}</dd></div>" for name, value in facts
    )
    document = cast(str | None, ruleset.get("document_url"))
    link = (
        f'<p class="detail-source"><a href="{_e(document)}">Zarządzenie w Bazie Aktów Własnych NFZ'
        + f" (dokument {_e(ruleset['baw_document_id'])})</a></p>"
        if document
        else ""
    )
    return (
        f"<{heading}>Wydanie katalogu na ten dzień</{heading}>"
        + f'<dl class="detail-facts">{rows}</dl>{link}'
    )


def modifiers_note(ruleset: dict[str, object]) -> str:
    binding = cast(dict[str, object], ruleset["modifiers"])
    if binding["binding"] == "reviewed":
        return "To nie są punkty do rozliczenia. Powiązanie MODTAR jest referencyjne; warunki zastosowania i łączenia modyfikatorów nie są tu oceniane."
    return MODIFIERS_NOTE


def _head(crumbs: list[str]) -> str:
    return (
        '      <div class="shell page-head page-head-compact">\n'
        + '        <nav class="breadcrumbs" aria-label="Okruszki">'
        + '<span aria-hidden="true">/</span>'.join(crumbs)
        + "</nav>\n"
    )


def index_page(
    ruleset: dict[str, object],
    groups: list[dict[str, object]],
    scopes: list[dict[str, object]],
    scope: dict[str, object] | None,
    calendar: list[str],
    comparison: str = "",
) -> str:
    """The dictionary-tree layout of /slowniki: catalog, chapter, group."""
    service_date = cast(str, ruleset["service_date"])
    suffix = f"/zakres/{scope['position']}" if scope else ""
    crumbs = [
        '<a href="/jgp">Grupy JGP</a>',
        f'<a href="/jgp/{service_date}">{display_date(service_date)}</a>'
        if scope
        else f'<span aria-current="page">{display_date(service_date)}</span>',
    ]
    if scope:
        crumbs.append(f'<span aria-current="page">{_e(scope["label"])}</span>')
    lead = (
        f"Grupy oznaczone w katalogu dla zakresu „{_e(scope['label'])}”"
        + " na wybrany dzień udzielenia świadczenia."
        if scope
        else f"Załączniki 1a i 1ae do zarządzenia {_e(ruleset['act_number'])}:"
        + " grupy i punkty bazowe na wybrany dzień udzielenia świadczenia."
    )
    chapters = len({(item["catalog"], item["chapter_position"]) for item in groups})
    status = (
        f"{len(groups)} {plural_pl(len(groups), 'grupa', 'grupy', 'grup')} w {chapters}"
        + f" {'rozdziale' if chapters == 1 else 'rozdziałach'}."
        + " Rozwiń rozdział kliknięciem lub klawiszem Enter."
        if groups
        else "Brak grup dla tego wyboru w tym dniu."
    )
    parts = [
        _head(crumbs),
        f"        <h1>Katalog grup JGP na {_e(display_date(service_date))}</h1>\n",
        f'        <p class="page-lead">{lead}</p>\n',
        '        <div class="jgp-controls">\n',
        date_bar(service_date, suffix, calendar),
        _scope_picker(service_date, scopes, scope),
        comparison,
        "        </div>\n",
        "      </div>\n",
        '      <div class="shell jgp-catalog">\n',
        '        <div class="tree-search jgp-filter" hidden>'
        + '<label class="tree-search-label" for="jgp-filter">Szukaj w katalogu grup</label>'
        + '<div class="tree-search-row"><input class="tree-search-input" type="search"'
        + ' id="jgp-filter" placeholder="np. H21 albo endoproteza" autocomplete="off"'
        + ' spellcheck="false"></div></div>\n',
        f'        <p class="tree-status jgp-filter-status" role="status" aria-live="polite">{status}</p>\n',
        '        <div class="tree-layout">\n',
    ]
    # No groups: the status line says so; an empty bordered pane would not.
    if groups:
        parts.append('          <div class="tree-pane">\n')
        parts.extend(_catalog_lists(service_date, groups, scope is not None))
        parts.append("          </div>\n")
    parts.append(
        '          <aside class="detail jgp-release" aria-label="Wydanie katalogu">'
        + f'<p class="jgp-points-note"><strong>{_e(BASE_POINTS_LABEL[0].upper() + BASE_POINTS_LABEL[1:])}.</strong>'
        + f" {_e(modifiers_note(ruleset))}</p>"
        + provenance(ruleset)
        + f'<p class="detail-source">{_e(LEGEND[0])}</p>'
        + "</aside>\n"
        + "        </div>\n"
        + "      </div>"
    )
    title = f"Katalog grup JGP na {display_date(service_date)} - Minos Health"
    return _jgp_page(
        title=title,
        description=f"Grupy JGP i punkty bazowe wg katalogu, bez modyfikatorów, dla świadczeń z {display_date(service_date)}.",
        path=f"/jgp/{service_date}{suffix}",
        content="".join(parts),
    )


def _scope_picker(
    service_date: str, scopes: list[dict[str, object]], scope: dict[str, object] | None
) -> str:
    links = "".join(
        f'<li><a href="/jgp/{service_date}/zakres/{item["position"]}"'
        + (
            ' aria-current="page"'
            if scope and item["position"] == scope["position"]
            else ""
        )
        + f">{_e(item['label'])}</a></li>"
        for item in scopes
    )
    reset = (
        f'<li><a href="/jgp/{service_date}">Wszystkie zakresy</a></li>' if scope else ""
    )
    chosen = _e(scope["label"]) if scope else "Wszystkie zakresy"
    return (
        '        <div class="jgp-scope">\n'
        + '          <span class="tree-search-label" id="jgp-scope-label">Zakres świadczeń</span>\n'
        + '          <details class="jgp-scopes"><summary class="tree-search-input"'
        + ' aria-labelledby="jgp-scope-label jgp-scope-value">'
        + f'<span id="jgp-scope-value">{chosen}</span>{CHEVRON}</summary>'
        + f"<ul>{reset}{links}</ul></details>\n"
        + "        </div>\n"
    )


def _catalog_lists(
    service_date: str, groups: list[dict[str, object]], with_markers: bool
) -> list[str]:
    if not groups:
        return []
    counts = Counter((item["catalog"], item["chapter_position"]) for item in groups)
    totals = Counter(item["catalog"] for item in groups)
    parts: list[str] = ['            <ul class="jgp-tree">\n']
    catalog: object = None
    chapter: object = None
    seen: set[tuple[object, object]] = set()
    for group in groups:
        if group["catalog"] != catalog:
            if chapter is not None:
                parts.append("</ul></details></li></ul></details></li>\n")
            catalog, chapter = group["catalog"], None
            # One open branch per catalog, so jgp.js can hide one left empty.
            parts.append(
                '            <li class="jgp-catalog-part"><details open>'
                + f'<summary class="tree-row">{CHEVRON}<span class="tree-text">'
                + f'<span class="tree-label jgp-catalog-title">Katalog grup {_e(catalog)}</span></span>'
                + f'<span class="result-count">{totals[catalog]}</span></summary>'
                + '<ul class="jgp-branch">\n'
            )
        if group["chapter_position"] != chapter:
            if chapter is not None:
                parts.append("</ul></details></li>\n")
            chapter = group["chapter_position"]
            # Rows arrive by chapter; a reopened chapter would duplicate its id.
            if (catalog, chapter) in seen:
                raise ArtifactError("catalog index rows are not grouped by chapter")
            seen.add((catalog, chapter))
            parts.append(
                f'              <li><details class="jgp-chapter" id="k-{_e(catalog)}-{chapter}">'
                + f'<summary class="tree-row">{CHEVRON}<span class="tree-text">'
                + f'<span class="tree-label">{_e(group["chapter"])}</span></span>'
                + f'<span class="result-count">{counts[(catalog, chapter)]}</span></summary>'
                + '<ul class="jgp-branch jgp-list">'
            )
        parts.append(_group_row(service_date, group, with_markers))
    parts.append("</ul></details></li></ul></details></li>\n            </ul>\n")
    return parts


def _group_row(service_date: str, group: dict[str, object], with_marker: bool) -> str:
    points = cast(int | None, group["hospitalization_hundredths"])
    shown = (
        f"{points_pl(points)}{NBSP}pkt"
        if points is not None
        else '<span class="jgp-absent">brak wartości</span>'
    )
    note = (
        f'<span class="jgp-marker">{_e(marker_meaning(group["scope_marker"]))}</span>'
        if with_marker
        else ""
    )
    search = f"{group['code']} {group['name']}"
    return (
        f'<li data-search="{_e(search)}"><a class="tree-row jgp-row" href="/jgp/{service_date}/{_e(group["code"])}">'
        + '<span class="tree-spacer"></span>'
        + f'<span class="code-chip">{_e(group["code"])}</span>'
        + f'<span class="jgp-name">{_e(group["name"])}{note}</span>'
        + f'<span class="jgp-points">{shown}</span></a></li>'
    )


def group_page(
    answer: dict[str, object], calendar: list[str], neighbours: list[tuple[str, str]]
) -> str:
    """The /slowniki code-page layout. `neighbours` are (label, day) links
    already verified to have this group."""
    ruleset = cast(dict[str, object], answer["ruleset"])
    group = cast(dict[str, object], answer["group"])
    service_date = cast(str, ruleset["service_date"])
    code = cast(str, group["code"])
    catalog = cast(str, group["catalog"])
    crumbs = [
        '<a href="/jgp">Grupy JGP</a>',
        f'<a href="/jgp/{service_date}">{display_date(service_date)}</a>',
        f'<span aria-current="page">{_e(code)}</span>',
    ]
    badges = [
        f'<span class="code-chip">{_e(code)}</span>',
        f'<span class="badge badge-muted">Katalog {_e(catalog)}'
        + (
            f", produkt {_e(group['product_code'])}"
            if group["product_code"]
            else ", bez wiersza katalogu na ten dzień"
        )
        + "</span>",
    ]
    if group["procedural"] is True:
        badges.append('<span class="badge badge-muted">Grupa zabiegowa (*)</span>')
    parts = [
        _head(crumbs),
        f'        <p class="detail-code">{" ".join(badges)}</p>\n',
        f"        <h1>{_e(group['name'])}</h1>\n",
        '        <div class="jgp-controls">\n',
        date_bar(service_date, f"/{code}", calendar),
        "        </div>\n",
        '        <p class="code-actions">'
        + f'<a class="button" href="/jgp/{service_date}#k-{_e(catalog)}-{_e(group.get("chapter_position", ""))}">Pokaż w katalogu</a>'
        + f'<a class="button button-quiet" href="/jgp/{service_date}">Wszystkie grupy na {display_date(service_date)}</a></p>\n',
        "      </div>\n",
        '      <div class="shell code-page jgp-group">\n',
        _values(answer, catalog),
        statistics_section(cast(dict[str, object], answer["statistics"]))
        if "statistics" in answer
        else "",
        _row_window(code, group, neighbours),
        _scopes(cast(list[dict[str, object]], answer["scopes"])),
        _notes(cast(str | None, group["notes"])),
        conditions_section(cast(dict[str, object], answer["conditions"]))
        if "conditions" in answer
        else "",
        f'        <section class="detail-notes jgp-provenance">{provenance(ruleset)}</section>\n',
        f'        <p class="detail-source">{_e(LEGEND[0])}</p>\n',
        "      </div>",
    ]
    return _jgp_page(
        title=f"{code} {group['name']} - grupa JGP na {display_date(service_date)} - Minos Health",
        description=f"Grupa JGP {code}: punkty bazowe wg katalogu, bez modyfikatorów, dla świadczeń z {display_date(service_date)}.",
        path=f"/jgp/{service_date}/{code}",
        content="".join(parts),
    )


def _values(answer: dict[str, object], catalog: str) -> str:
    ruleset = cast(dict[str, object], answer["ruleset"])
    values = {
        cast(str, value["measure"]): value
        for value in cast(list[dict[str, object]], answer["base_values"])
    }
    rows: list[str] = []
    for measure, label in measure_labels(LAYOUTS[catalog]).items():
        value = values.get(measure)
        # Built from integers, so not escaped: _e would fold the NBSP to a space.
        if value is None:
            shown = '<dd class="jgp-absent">brak wartości w katalogu</dd>'
        elif value["unit"] == "days":
            shown = f'<dd class="jgp-value">{days_pl(cast(int, value["amount"]))}</dd>'
        else:
            shown = f'<dd class="jgp-value">{points_pl(cast(int, value["amount"]))}{NBSP}pkt</dd>'
        rows.append(f"<div><dt>{_e(label)}</dt>{shown}</div>")
    return (
        '        <section class="detail-notes jgp-values">'
        + f"<h2>{_e(BASE_POINTS_LABEL[0].upper() + BASE_POINTS_LABEL[1:])}</h2>"
        + f'<p class="jgp-note">{_e(modifiers_note(ruleset))}</p>'
        + f'<dl class="detail-facts">{"".join(rows)}</dl></section>\n'
    )


def _month_pl(month: object) -> str:
    text = cast(str, month)
    return f"{text[5:]}.{text[:4]}"


def _count_pl(value: object) -> str:
    """A count, or the range a suppressed NFZ cell leaves (one to four each)."""
    bounds = cast(dict[str, int], value)
    low, high = (f"{bounds[key]:,}".replace(",", NBSP) for key in ("min", "max"))
    return low if low == high else f"od {low} do {high}"


def _settled_points(ten_thousandths: object, *, unit: bool = True) -> str:
    # Whole points: the figures are statistics, and they fit a phone column.
    if ten_thousandths is None:
        return "brak danych"
    whole = (cast(int, ten_thousandths) + 5_000) // 10_000
    return f"{whole:,}".replace(",", NBSP) + (f"{NBSP}pkt" if unit else "")


def statistics_section(statistics: dict[str, object]) -> str:
    """NFZ settlement figures of the group (#283): statistics, never rules."""
    source = cast(dict[str, str], statistics["source"])
    months = cast(list[dict[str, object]], statistics["months"])
    mature = cast(dict[str, object], statistics["mature"])
    note = (
        "Statystyka rozliczonych hospitalizacji z miesięcznych raportów NFZ "
        + f'(<a href="{_e(source["dataset"])}">dane.gov.pl</a>, licencja '
        + f"{_e(source['license'])}). To nie reguła grupowania: punkty rozliczone "
        + "według NFZ, nie punkty bazowe katalogu. NFZ ukrywa liczby mniejsze niż 5, "
        + "dlatego część wartości to przedziały."
    )
    parts = ['        <section class="detail-notes jgp-statistics">',
             "<h2>Rozliczenia NFZ</h2>", f'<p class="jgp-note">{note}</p>']  # fmt: skip
    if not months:
        parts.append("<p>Przypięte raporty NFZ nie wykazują rozliczeń tej grupy.</p>")
    if mature["months"]:
        window = (
            f"od {_month_pl(mature['from'])} do {_month_pl(mature['to'])}"
            + f" ({mature['months']} mies.)"
        )
        parts.append(
            '<dl class="detail-facts">'
            + f"<div><dt>Hospitalizacje, {window}</dt>"
            + f"<dd>{_count_pl(mature['hospitalizations'])}</dd></div>"
            + "<div><dt>Punkty rozliczone</dt>"
            + f"<dd>{_settled_points(mature['points_ten_thousandths'])}</dd></div>"
            + "</dl>"
        )
        rows = "".join(
            f"<tr><td>{_e(level['hospital_level'])}</td>"
            + f"<td>{_count_pl(level['hospitalizations'])}</td>"
            + f"<td>{_settled_points(level['points_ten_thousandths'], unit=False)}</td></tr>"
            for level in cast(list[dict[str, object]], mature["levels"])
        )
        parts.append(
            '<div class="table-scroll"><table class="jgp-statistics-levels"><caption>Według poziomu szpitala '
            + f"(PSZ), {window}</caption><thead><tr><th>Poziom</th>"
            + "<th>Hospitalizacje</th><th>Punkty (pkt)</th></tr></thead>"
            + f"<tbody>{rows}</tbody></table></div>"
        )
    if months:
        rows = "".join(
            f"<tr><td>{_month_pl(item['month'])}</td>"
            + f"<td>{_count_pl(item['hospitalizations'])}</td>"
            + f"<td>{_settled_points(item['points_ten_thousandths'], unit=False)}</td>"
            + f"<td>{_month_pl(item['report_month'])}"
            + (" (dane wstępne)" if item["provisional"] else "")
            + "</td></tr>"
            for item in reversed(months)
        )
        parts.append(
            "<details><summary>Miesiące</summary>"
            + '<p class="jgp-note">Każdy miesiąc z najnowszego raportu, który go '
            + "obejmuje; grupa nieobecna w tym raporcie nie miała w tym miesiącu "
            + "rozliczeń. Dane wstępne: raport wydany mniej niż 12 miesięcy po "
            + "miesiącu, NFZ uzupełnia je jeszcze o późne rozliczenia.</p>"
            + '<div class="table-scroll"><table class="jgp-statistics-months"><thead><tr><th>Miesiąc</th>'
            + "<th>Hospitalizacje</th><th>Punkty (pkt)</th><th>Raport</th></tr></thead>"
            + f"<tbody>{rows}</tbody></table></div></details>"
        )
    parts.append("</section>\n")
    return "".join(parts)


def _row_window(
    code: str, group: dict[str, object], neighbours: list[tuple[str, str]]
) -> str:
    if group["applicability"] is None:
        return '<section class="detail-notes"><h2>Wiersz katalogu</h2><p>Charakterystyka zawiera grupę, lecz katalog nie ma dla niej wiersza na wybrany dzień. Nie oznacza to wartości zero ani możliwości rozliczenia.</p></section>'
    window = cast(dict[str, object], group["applicability"])
    start = cast(str, window["service_from"])
    end = cast(str | None, window["service_to"])
    text = (
        f"Wiersz Lp. {_e(group['ordinal'])} katalogu stosuje się do świadczeń od {_e(display_date(start))}"
        + (f" do {_e(display_date(end))}" if end else "")
        + "."
    )
    links = "".join(
        f'<a class="button button-quiet" href="/jgp/{_e(day)}/{_e(code)}">{_e(label)}</a>'
        for label, day in neighbours
    )
    return (
        '        <section class="detail-notes jgp-window"><h2>Obowiązywanie wiersza</h2>'
        + f"<p>{text}</p>"
        + (f'<p class="code-actions">{links}</p>' if links else "")
        + f'<p class="detail-source">{_e(window["evidence"])}</p>'
        + "</section>\n"
    )


def _scopes(scopes: list[dict[str, object]]) -> str:
    items = "".join(
        f'<li><span class="result-label">{_e(scope["label"])}'
        + f'<span class="jgp-marker">{_e(marker_meaning(scope["marker"]))}</span></span></li>'
        for scope in scopes
    )
    return (
        '        <section class="code-children"><h2>Zakresy świadczeń</h2>'
        + (
            f'<ul class="code-list jgp-scope-list">{items}</ul>'
            if items
            else '<p class="detail-empty">Brak oznaczeń w katalogu.</p>'
        )
        + "</section>\n"
    )


def _notes(notes: str | None) -> str:
    if not notes:
        return ""
    lines = "".join(f"<p>{_e(line)}</p>" for line in notes.splitlines() if line.strip())
    return f'        <section class="detail-notes"><h2>Uwagi</h2>{lines}</section>\n'


def _without_code(text: str, code: str) -> str:
    """The code is already shown as a chip; keep only the source title."""
    text = " ".join(text.split())
    return text[len(code) :].strip() if text.startswith(code + " ") else text


def directional_list_page(answer: dict[str, object]) -> str:
    source = cast(dict[str, object], answer["source"])
    day, catalog = cast(str, source["service_date"]), cast(str, source["catalog"])
    identity, page = cast(int, answer["id"]), cast(int, answer["page"])
    title = f"Lista {_e(answer['label'])} - katalog {_e(catalog)}"
    rows = "".join(
        "<li>"
        + code_link(
            cast(str, answer["system"]),
            cast(str, member["code"]),
            day,
            member["correspondence"] == "matched",
        )
        + f" <span>{_e(_without_code(cast(str, member['source_text']), cast(str, member['code'])))}</span>"
        + f' <span class="jgp-rows">(wiersz {_e(member["row"])})</span></li>'
        for member in cast(list[dict[str, object]], answer["members"])
    )
    links: list[str] = []
    for label, target in (
        ("Poprzednia strona", page - 1),
        ("Następna strona", page + 1),
    ):
        if target >= 1 and (target - 1) * cast(int, answer["page_size"]) < cast(
            int, answer["total_occurrences"]
        ):
            links.append(
                f'<a class="button" href="/jgp/{day}/listy/{catalog}/{identity}/{target}">{label}</a>'
            )
    content = (
        _head(
            [
                '<a href="/jgp">Grupy JGP</a>',
                f'<a href="/jgp/{day}">{display_date(day)}</a>',
                '<span aria-current="page">Lista źródłowa</span>',
            ]
        )
        + f'<h1>{title}</h1><p class="page-lead">Dzień świadczenia: {_e(display_date(day))}. '
        + f"{_e(source_positions(cast(int, answer['total_occurrences'])))}, strona {page}.</p></div>"
        + f'<div class="shell code-page"><section class="code-children"><ul class="code-list">{rows}</ul></section>'
        + "<p>Powtórzenia i oznaczenia pozostają dowodami źródłowymi, nie dodatkowym zliczeniem procedur. "
        + "Obecność kodu na liście nie oznacza kwalifikacji przypadku. Numery list są lokalne dla wydania.</p>"
        + f'<nav class="code-actions" aria-label="Strony listy">{" ".join(links)}</nav>'
        + source_facts(source)
        + "</div>"
    )
    return _jgp_page(
        title=f"Lista {answer['label']} na {display_date(day)} - Minos Health",
        description=f"Jawna lista załącznika 9 dla świadczeń z {display_date(day)}.",
        path=f"/jgp/{day}/listy/{catalog}/{identity}/{page}",
        content=content,
    )


def error_page(
    status: int,
    code: str,
    service_date: str | None,
    group_code: str | None,
    calendar: list[str] | None = None,
) -> str:
    """Branded explicit errors; no other release or date is substituted.

    The date bar appears only when the bundle's calendar was read.
    """
    covered = f" Minos obejmuje świadczenia od {calendar[0]}." if calendar else ""
    messages = {
        "no_ruleset_for_date": (
            "Brak zweryfikowanego katalogu JGP dla tego dnia",
            "Minos nie podstawia innego wydania." + covered,
        ),
        "scope_not_found": (
            "Nie ma takiego zakresu świadczeń",
            "Wybierz zakres świadczeń z listy w katalogu grup.",
        ),
        "group_not_on_date": (
            "Tej grupy nie ma w katalogu na ten dzień",
            "Grupa nie występuje w wydaniu katalogu obowiązującym w wybranym dniu.",
        ),
        "ambiguous_ruleset": (
            "Niejednoznaczne wydanie katalogu",
            "Dla tego dnia obowiązują sprzeczne wydania; Minos nie wybiera żadnego z nich.",
        ),
        "timezone_unavailable": (
            "Nie można ustalić dzisiejszej daty",
            "Wybierz dzień udzielenia świadczenia w adresie, np. /jgp/2026-10-02.",
        ),
        "busy": ("Serwis jest chwilowo zajęty", "Spróbuj ponownie za chwilę."),
        "query_timeout": (
            "Przygotowanie danych trwało zbyt długo",
            "Możesz ponowić odczyt. To nie oznacza braku grupy lub danych dla tego dnia.",
        ),
        "limit_exceeded": (
            "Nie udało się przygotować pełnego opisu",
            "Opis przekroczył bezpieczny limit przetwarzania. Wróć do katalogu lub spróbuj ponownie.",
        ),
        "unavailable": (
            "Katalog JGP jest chwilowo niedostępny",
            "Dane referencyjne nie przeszły kontroli spójności. Spróbuj później.",
        ),
    }
    title, lead = messages.get(
        code, ("Nie znaleziono strony", "Sprawdź adres strony grupy JGP.")
    )
    actions = ['<a class="button" href="/jgp">Katalog grup JGP</a>']
    if code in ("busy", "query_timeout", "limit_exceeded", "unavailable"):
        actions.insert(0, '<a class="button" href="">Spróbuj ponownie</a>')
    if service_date and code == "group_not_on_date":
        actions.insert(
            0,
            f'<a class="button" href="/jgp/{_e(service_date)}">Katalog na {_e(display_date(service_date))}</a>',
        )
    content = (
        '      <div class="shell page-head not-found">\n'
        + f'        <p class="not-found-code">{status}</p>\n'
        + f"        <h1>{_e(title)}</h1>\n"
        + f'        <p class="page-lead">{_e(lead)}</p>\n'
        + (
            date_bar(service_date, f"/{group_code}" if group_code else "", calendar)
            if service_date and calendar
            else ""
        )
        + f'        <p class="not-found-actions">{"".join(actions)}</p>\n'
        + "      </div>"
    )
    return _jgp_page(
        title=f"{title} - Grupy JGP - Minos Health",
        description=lead,
        path="/jgp",
        content=content,
        head=RETRY_HEAD if code == "busy" else "",
    )
