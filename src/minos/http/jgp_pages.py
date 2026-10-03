"""Server-rendered JGP catalog pages (#26): one resolved release per page.

Every page states the service date, the governing act and catalog release and
the bundle's knowledge horizon. Values are catalog base points without
modifiers; empty catalog cells read "brak wartości w katalogu", never zero.
"""

from collections import Counter
from typing import Final, cast

from minos.domain.reference import ArtifactError
from minos.http.pages import _e, _page  # pyright: ignore[reportPrivateUsage]
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


def _jgp_page(*, title: str, description: str, path: str, content: str) -> str:
    return _page(
        title=title,
        description=description,
        path=path,
        content=content,
        head='    <script src="/jgp.js" defer></script>\n',
        robots=ROBOTS,
        template=SHELL,
    )


def marker_meaning(marker: object) -> str:
    """The legend text of a scope marker; the bundle allows only 1-3."""
    if not isinstance(marker, int) or marker not in MARKERS:
        raise ArtifactError("catalog scope marker is outside the reviewed legend")
    return MARKERS[marker]


def date_bar(service_date: str, suffix: str, calendar: list[str]) -> str:
    """Date choice without a form or query string: links, enhanced by jgp.js.

    `calendar` lists the days on which the bundle's answer can change; the
    first is the earliest covered service date.
    """
    links = "".join(
        f'<a href="/jgp/{_e(day)}{_e(suffix)}">{_e(day)}</a>'
        for day in calendar
        if day != service_date
    )
    return (
        '        <div class="jgp-datebar">\n'
        + '          <label for="jgp-date">Dzień udzielenia świadczenia</label>\n'
        + f'          <input type="date" id="jgp-date" value="{_e(service_date)}"'
        + f' min="{_e(calendar[0])}" data-suffix="{_e(suffix)}">\n'
        + f'          <p class="jgp-quick">Inny dzień: {links}</p>\n'
        + "        </div>\n"
    )


def ruleset_notice(ruleset: dict[str, object]) -> str:
    parts = [
        f'        <p class="jgp-points-note"><strong>{_e(BASE_POINTS_LABEL[0].upper() + BASE_POINTS_LABEL[1:])}.</strong>'
        + f" {_e(MODIFIERS_NOTE)}</p>\n"
    ]
    if ruleset["service_date_after_knowledge"] is True:
        parts.append(
            '        <p class="badge badge-warn jgp-horizon">Stan wiedzy Minos: '
            + f"{_e(ruleset['knowledge_as_of'])}. Dla tego dnia może obowiązywać"
            + " późniejsze zarządzenie, którego Minos jeszcze nie uwzględnia.</p>\n"
        )
    return "".join(parts)


def provenance(ruleset: dict[str, object]) -> str:
    facts = [
        ("Zarządzenie Prezesa NFZ", ruleset["act_number"]),
        ("Opublikowano", ruleset["published_on"]),
        ("Stosuje się do świadczeń od", ruleset["service_from"]),
        ("Podstawa", ruleset["service_evidence"]),
        ("Stan wiedzy Minos", ruleset["knowledge_as_of"]),
        ("SHA-256 skoroszytu", ruleset["workbook_sha256"]),
        ("Modyfikatory", "niepowiązane (#46); Minos nie liczy punktów do rozliczenia"),
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
        '        <section class="detail-notes jgp-provenance"><h2>Wydanie katalogu na ten dzień</h2>'
        + f'<dl class="detail-facts">{rows}</dl>{link}</section>\n'
    )


def _head(crumbs: list[str], title_html: str, lead: str) -> str:
    return (
        '      <div class="shell page-head page-head-compact">\n'
        + '        <nav class="breadcrumbs" aria-label="Okruszki">'
        + '<span aria-hidden="true">/</span>'.join(crumbs)
        + "</nav>\n"
        + f"        <h1>{title_html}</h1>\n"
        + (f'        <p class="page-lead">{lead}</p>\n' if lead else "")
    )


def index_page(
    ruleset: dict[str, object],
    groups: list[dict[str, object]],
    scopes: list[dict[str, object]],
    scope: dict[str, object] | None,
    calendar: list[str],
) -> str:
    service_date = cast(str, ruleset["service_date"])
    suffix = f"/zakres/{scope['position']}" if scope else ""
    crumbs = [
        '<a href="/jgp">Grupy JGP</a>',
        f'<a href="/jgp/{service_date}">{service_date}</a>',
    ]
    if scope:
        crumbs.append(_e(scope["label"]))
    lead = (
        f"Grupy z oznaczeniem w zakresie świadczeń „{_e(scope['label'])}”."
        if scope
        else "Załącznik 1a do zarządzenia Prezesa NFZ: katalog grup na wybrany dzień"
        + " udzielenia świadczenia."
    )
    parts = [
        _head(crumbs, "Katalog grup JGP", lead),
        date_bar(service_date, suffix, calendar),
        ruleset_notice(ruleset),
        "      </div>\n",
        '      <div class="shell jgp-catalog">\n',
        _scope_picker(service_date, scopes, scope),
        '        <div class="jgp-filter" hidden><label for="jgp-filter">Filtruj: kod lub nazwa grupy</label>'
        + '<input type="search" id="jgp-filter" autocomplete="off" spellcheck="false">'
        + '<p class="jgp-filter-status" role="status" aria-live="polite"></p></div>\n',
    ]
    parts.extend(_catalog_lists(service_date, groups, scope is not None))
    parts.append(
        f'        <p class="detail-source">{_e(LEGEND[0])}</p>\n'
        + provenance(ruleset)
        + "      </div>"
    )
    title = f"Katalog grup JGP na {service_date} - Minos Health"
    return _jgp_page(
        title=title,
        description=f"Grupy JGP i punkty bazowe wg katalogu, bez modyfikatorów, dla świadczeń z {service_date}.",
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
        f'<p><a class="button button-quiet" href="/jgp/{service_date}">Pokaż wszystkie zakresy</a></p>'
        if scope
        else ""
    )
    return (
        '        <details class="jgp-scopes"><summary>Zakres świadczeń'
        + (f": {_e(scope['label'])}" if scope else "")
        + f"</summary>{reset}<ul>{links}</ul></details>\n"
    )


def _catalog_lists(
    service_date: str, groups: list[dict[str, object]], with_markers: bool
) -> list[str]:
    if not groups:
        return [
            '        <p class="empty-state">Brak grup dla tego wyboru w tym dniu.</p>\n'
        ]
    counts = Counter((item["catalog"], item["chapter_position"]) for item in groups)
    parts: list[str] = []
    catalog: object = None
    chapter: object = None
    seen: set[tuple[object, object]] = set()
    for group in groups:
        if group["catalog"] != catalog:
            if chapter is not None:
                parts.append("</ul></details></section>\n")
            catalog, chapter = group["catalog"], None
            # A section per catalog, so jgp.js can hide a heading left empty.
            parts.append(
                '        <section class="jgp-catalog-part">'
                + f'<h2 class="jgp-catalog-title">Katalog grup {_e(catalog)}</h2>\n'
            )
        if group["chapter_position"] != chapter:
            if chapter is not None:
                parts.append("</ul></details>\n")
            chapter = group["chapter_position"]
            # Rows arrive by chapter; a reopened chapter would duplicate its id.
            if (catalog, chapter) in seen:
                raise ArtifactError("catalog index rows are not grouped by chapter")
            seen.add((catalog, chapter))
            count = counts[(catalog, chapter)]
            parts.append(
                f'        <details class="jgp-chapter" id="k-{_e(catalog)}-{chapter}">'
                + f'<summary>{_e(group["chapter"])} <span class="result-count">{count}</span></summary>'
                + '<ul class="jgp-list">'
            )
        parts.append(_group_row(service_date, group, with_markers))
    parts.append("</ul></details></section>\n")
    return parts


def _group_row(service_date: str, group: dict[str, object], with_marker: bool) -> str:
    points = cast(int | None, group["hospitalization_hundredths"])
    shown = f"{points_pl(points)}{NBSP}pkt" if points is not None else "brak wartości"
    note = (
        f'<span class="jgp-marker">{_e(marker_meaning(group["scope_marker"]))}</span>'
        if with_marker
        else ""
    )
    search = f"{group['code']} {group['name']}"
    return (
        f'<li data-search="{_e(search)}"><a class="jgp-row" href="/jgp/{service_date}/{_e(group["code"])}">'
        + f'<span class="code-chip">{_e(group["code"])}</span>'
        + f'<span class="jgp-name">{_e(group["name"])}{note}</span>'
        + f'<span class="jgp-points">{shown}</span></a></li>'
    )


def group_page(
    answer: dict[str, object], calendar: list[str], neighbours: list[tuple[str, str]]
) -> str:
    """`neighbours` are (label, day) links already verified to have this group."""
    ruleset = cast(dict[str, object], answer["ruleset"])
    group = cast(dict[str, object], answer["group"])
    service_date = cast(str, ruleset["service_date"])
    code = cast(str, group["code"])
    catalog = cast(str, group["catalog"])
    crumbs = [
        '<a href="/jgp">Grupy JGP</a>',
        f'<a href="/jgp/{service_date}">{service_date}</a>',
        _e(code),
    ]
    badges = [
        f'<span class="code-chip">{_e(code)}</span>',
        f'<span class="badge badge-muted">Katalog {_e(catalog)}</span>',
        f'<span class="badge badge-muted">Produkt {_e(group["product_code"])}</span>',
    ]
    if group["procedural"] is True:
        badges.append('<span class="badge badge-muted">Grupa zabiegowa (*)</span>')
    parts = [
        _head(crumbs, _e(group["name"]), ""),
        f'        <p class="detail-code">{" ".join(badges)}</p>\n',
        date_bar(service_date, f"/{code}", calendar),
        ruleset_notice(ruleset),
        "      </div>\n",
        '      <div class="shell code-page jgp-group">\n',
        _values_table(answer, catalog),
        _row_window(code, group, neighbours),
        _scopes(cast(list[dict[str, object]], answer["scopes"])),
        _notes(cast(str | None, group["notes"])),
        provenance(ruleset),
        f'        <p class="code-actions"><a class="button" href="/jgp/{service_date}#k-{_e(catalog)}-{_e(group.get("chapter_position", ""))}">'
        + "Pokaż w katalogu</a></p>\n",
        "      </div>",
    ]
    return _jgp_page(
        title=f"{code} {group['name']} - grupa JGP na {service_date} - Minos Health",
        description=f"Grupa JGP {code}: punkty bazowe wg katalogu, bez modyfikatorów, dla świadczeń z {service_date}.",
        path=f"/jgp/{service_date}/{code}",
        content="".join(parts),
    )


def _values_table(answer: dict[str, object], catalog: str) -> str:
    values = {
        cast(str, value["measure"]): value
        for value in cast(list[dict[str, object]], answer["base_values"])
    }
    rows: list[str] = []
    for measure, label in measure_labels(LAYOUTS[catalog]).items():
        value = values.get(measure)
        if value is None:
            shown = '<span class="jgp-absent">brak wartości w katalogu</span>'
        # Built from integers, so not escaped: _e would fold the NBSP to a space.
        elif value["unit"] == "days":
            shown = days_pl(cast(int, value["amount"]))
        else:
            shown = f"{points_pl(cast(int, value['amount']))}{NBSP}pkt"
        rows.append(f'<tr><th scope="row">{_e(label)}</th><td>{shown}</td></tr>')
    return (
        '        <table class="jgp-values"><caption>'
        + f"{_e(BASE_POINTS_LABEL[0].upper() + BASE_POINTS_LABEL[1:])}</caption>"
        + f"<tbody>{''.join(rows)}</tbody></table>\n"
    )


def _row_window(
    code: str, group: dict[str, object], neighbours: list[tuple[str, str]]
) -> str:
    window = cast(dict[str, object], group["applicability"])
    start = cast(str, window["service_from"])
    end = cast(str | None, window["service_to"])
    text = (
        f"Wiersz Lp. {_e(group['ordinal'])} katalogu stosuje się do świadczeń od {_e(start)}"
        + (f" do {_e(end)}" if end else "")
        + "."
    )
    links = [
        f'<a href="/jgp/{_e(day)}/{_e(code)}">{_e(label)}</a>'
        for label, day in neighbours
    ]
    return (
        '        <section class="detail-notes jgp-window"><h2>Obowiązywanie wiersza</h2>'
        + f'<p>{text}</p><p class="detail-source">{_e(window["evidence"])}</p>'
        + (f"<p>{' '.join(links)}</p>" if links else "")
        + "</section>\n"
    )


def _scopes(scopes: list[dict[str, object]]) -> str:
    items = "".join(
        f'<li>{_e(scope["label"])} <span class="jgp-marker">{_e(marker_meaning(scope["marker"]))}</span></li>'
        for scope in scopes
    )
    return (
        '        <section class="detail-notes"><h2>Zakresy świadczeń</h2>'
        + (
            f'<ul class="jgp-scope-list">{items}</ul>'
            if items
            else "<p>Brak oznaczeń w katalogu.</p>"
        )
        + "</section>\n"
    )


def _notes(notes: str | None) -> str:
    if not notes:
        return ""
    lines = "".join(f"<p>{_e(line)}</p>" for line in notes.splitlines() if line.strip())
    return f'        <section class="detail-notes"><h2>Uwagi</h2>{lines}</section>\n'


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
        "unavailable": (
            "Katalog JGP jest chwilowo niedostępny",
            "Dane referencyjne nie przeszły kontroli spójności. Spróbuj później.",
        ),
    }
    title, lead = messages.get(
        code, ("Nie znaleziono strony", "Sprawdź adres strony grupy JGP.")
    )
    actions = ['<a class="button" href="/jgp">Katalog grup JGP</a>']
    if service_date and code == "group_not_on_date":
        actions.insert(
            0,
            f'<a class="button" href="/jgp/{_e(service_date)}">Katalog na {_e(service_date)}</a>',
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
    )
