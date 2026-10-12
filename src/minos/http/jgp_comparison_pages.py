"""Shareable catalog comparison pages; never encounter or final-tariff results."""

from html import escape

from minos.domain.reference import ArtifactError
from minos.http.jgp_pages import (
    BASE_POINTS_LABEL,
    LAYOUTS,
    _jgp_page,  # pyright: ignore[reportPrivateUsage]
    days_pl,
    marker_meaning,
    measure_labels,
    points_pl,
    provenance,
)
from minos.http.pages import _e  # pyright: ignore[reportPrivateUsage]
from minos.store.comparison_snapshot import Entry, GroupKey
from minos.store.jgp_comparison import (
    CatalogComparison,
    CatalogVersion,
    scope_identity,
)

ALL = "wszystkie"


def comparison_path(
    dataset: str,
    before: str,
    after: str,
    before_release: str,
    after_release: str,
    scope: str = ALL,
    group: str = ALL,
) -> str:
    return f"/jgp/porownaj/{dataset}/{before}/{before_release}/{after}/{after_release}/{scope}/{group}"


def comparison_link(
    day: str,
    release: str,
    versions: tuple[CatalogVersion, ...],
    dataset: str,
    scope: str | None,
) -> str:
    selected = next(item for item in versions if item.release == release)
    previous = selected.supersedes or release
    path = comparison_path(
        dataset, day, day, previous, release, scope_identity(scope) if scope else ALL
    )
    return f'<p><a class="dictionary-tab" href="{_e(path)}">Porównaj daty i wydania katalogu</a></p>'


def _value(entry: Entry | None, measure: str) -> str:
    if entry is None:
        return "grupa nieobecna"
    for code, unit, amount in entry.values:
        if code == measure:
            if unit == "points_hundredths":
                return points_pl(amount)
            if unit == "days":
                return days_pl(amount)
            raise ArtifactError("unsupported catalog comparison unit")
    return "brak wartości w katalogu"


def _definition(entry: Entry | None) -> str:
    if entry is None:
        return "<p>Grupa nieobecna w tym katalogu na wybrany dzień.</p>"
    definition = entry.definition
    scopes = "; ".join(
        f"{label}: {marker_meaning(marker)}" for label, marker in definition.scopes
    )
    window = entry.window
    return (
        f"<p>{_e(definition.name)}</p>"
        f"<p>Produkt: <code>{_e(definition.product_code)}</code></p>"
        f"<p>Rozdział: {_e(definition.chapter)}. Oznaczenie zabiegowe: {'tak' if definition.procedural else 'nie'}.</p>"
        f"<p>Zakresy wg oznaczeń źródłowych: {_e(scopes) if scopes else 'brak oznaczeń'}</p>"
        f"<p>Uwagi katalogu: {_e(definition.notes) if definition.notes else 'brak'}</p>"
        f"<p>Obowiązuje od {_e(window.service_from)} do {_e(window.service_to or 'bez określonego końca')}."
        f" Podstawa: {_e(window.evidence)}</p>"
    )


def _values_table(catalog: str, old: Entry | None, new: Entry | None) -> str:
    labels = measure_labels(LAYOUTS[catalog])
    measures = sorted(
        {measure for entry in (old, new) if entry for measure, _, _ in entry.values}
    )
    changed: list[str] = []
    unchanged: list[str] = []
    for measure in measures:
        before, after = _value(old, measure), _value(new, measure)
        row = (
            f'<tr><th scope="row">{_e(labels.get(measure, measure))}</th>'
            f"<td>{escape(before)}</td><td>{escape(after)}</td></tr>"
        )
        (unchanged if before == after else changed).append(row)

    def table(rows: list[str], caption: str) -> str:
        return (
            '<div class="table-scroll" tabindex="0" role="region" aria-label="Wartości przed i po">'
            f'<table><caption>{caption}</caption><thead><tr><th scope="col">Miara / tryb</th>'
            '<th scope="col">Przed</th><th scope="col">Po</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>"
        )

    return (table(changed, "Zmienione wartości katalogowe") if changed else "") + (
        "<details><summary>Wartości katalogowe bez zmian</summary>"
        + table(unchanged, "Niezmienione punkty bazowe i limity pobytu")
        + "</details>"
        if unchanged
        else ""
    )


def _group(
    comparison: CatalogComparison, key: GroupKey, dataset: str, scope: str
) -> str:
    catalog, code = key
    old, new = comparison.left.get(key), comparison.right.get(key)
    displayed = new if new is not None else old
    if displayed is None:
        raise ArtifactError("changed group is absent on both sides")
    status = "Dodana" if old is None else "Usunięta" if new is None else "Zmieniona"
    before, after = (
        str(comparison.before["service_date"]),
        str(comparison.after["service_date"]),
    )
    columns = "".join(
        f"<section><h3>{title}: {_e(day)}</h3>{_definition(entry)}"
        + (
            f'<a href="{_e(side["document_url"])}">Wybrane wydanie w BAW NFZ</a>'
            if entry
            else ""
        )
        + "</section>"
        for title, day, entry, side in (
            ("Przed", before, old, comparison.before),
            ("Po", after, new, comparison.after),
        )
    )
    changed_fields: list[str] = []
    if old is not None and new is not None:
        for before_value, after_value, label in (
            (old.definition.name, new.definition.name, "nazwa"),
            (old.definition.product_code, new.definition.product_code, "kod produktu"),
            (old.definition.chapter, new.definition.chapter, "rozdział"),
            (
                old.definition.procedural,
                new.definition.procedural,
                "oznaczenie zabiegowe",
            ),
            (
                old.definition.scopes,
                new.definition.scopes,
                "oznaczenia zakresów świadczeń",
            ),
            (old.definition.notes, new.definition.notes, "uwagi katalogu"),
        ):
            if before_value != after_value:
                changed_fields.append(label)
    same_values = old is not None and new is not None and old.values == new.values
    summary = (
        ("Zmieniono: " + ", ".join(changed_fields) + ". ") if changed_fields else ""
    )
    if same_values:
        summary += "Wartości punktowe i limity pobytu bez zmian."
    else:
        summary += "Wartości katalogowe przed i po pokazano w tabeli."
    path = comparison_path(
        dataset,
        before,
        after,
        str(comparison.before["release_id"]),
        str(comparison.after["release_id"]),
        scope,
        code,
    )
    return (
        f'<article class="compare-change" data-group="{_e(code)}" data-action="{status}">'
        + f'<h2>{_e(code)} <span class="badge">{status} · {_e(catalog)}</span></h2><p>{_e(summary)}</p>'
        + f'<p class="compare-name">{_e(displayed.definition.name)}</p>'
        + _values_table(catalog, old, new)
        + f'<details><summary>Definicje, zakresy i podstawy źródłowe przed i po</summary><div class="compare-sides">{columns}</div></details>'
        + f'<p><a href="{_e(path)}">Link do porównania grupy {_e(code)}</a></p></article>'
    )


def comparison_page(
    comparison: CatalogComparison,
    dataset: str,
    scope: str,
    group: str,
    versions: tuple[CatalogVersion, ...],
) -> str:
    before, after = (
        str(comparison.before["service_date"]),
        str(comparison.after["service_date"]),
    )
    selected_scope = comparison.scopes.get(scope)
    before_release, after_release = (
        str(comparison.before["release_id"]),
        str(comparison.after["release_id"]),
    )
    version_labels = {item.release: item.label for item in versions}
    keys = [
        key
        for key in comparison.changed
        if (group == ALL or key[1] == group)
        and (
            scope == ALL
            or any(
                selected_scope == label
                for entry in (comparison.left.get(key), comparison.right.get(key))
                if entry
                for label, _ in entry.definition.scopes
            )
        )
    ]

    def options(items: dict[str, str], selected: str) -> str:
        return "".join(
            f'<option value="{_e(value)}"{" selected" if value == selected else ""}>{_e(label)}</option>'
            for value, label in items.items()
        )

    codes = sorted({key[1] for key in comparison.left.keys() | comparison.right.keys()})
    controls = (
        f'<div class="compare-controls" data-dataset="{dataset}">'
        f'<label>Przed: dzień świadczenia<input id="compare-before" type="date" value="{before}"></label>'
        f'<label>Po: dzień świadczenia<input id="compare-after" type="date" value="{after}"></label>'
        f'<label>Przed: wydanie źródłowe<select id="compare-before-release">{options(version_labels, before_release)}</select></label>'
        f'<label>Po: wydanie źródłowe<select id="compare-after-release">{options(version_labels, after_release)}</select></label>'
        f'<label>Zakres ze źródła<select id="compare-scope">{options({ALL: "Wszystkie zakresy", **comparison.scopes}, scope)}</select></label>'
        f'<label>Grupa<select id="compare-group">{options({ALL: "Wszystkie grupy", **{code: code for code in codes}}, group)}</select></label>'
        '<button id="compare-go" type="button" hidden>Pokaż porównanie</button></div>'
    )
    quick = "".join(
        f'<li><a href="{_e(comparison_path(dataset, item.service_from.isoformat(), item.service_from.isoformat(), item.supersedes, item.release))}">{_e(item.label)}</a></li>'
        for item in versions
        if item.supersedes in version_labels
    )
    scope_links = "".join(
        f'<li><a href="{_e(comparison_path(dataset, before, after, before_release, after_release, identity, group))}">{_e(label)}</a></li>'
        for identity, label in comparison.scopes.items()
    )
    body = (
        '<div class="shell page-head page-head-compact"><nav class="breadcrumbs" aria-label="Okruszki"><a href="/jgp">Grupy JGP</a> / Porównanie</nav>'
        + "<h1>Co zmieniło się w katalogu JGP?</h1>"
        + f'<p class="page-lead">{_e(BASE_POINTS_LABEL)}. To nie są punkty do rozliczenia.</p>'
        + "<p>Porównujemy wskazane wydania według stanu z dnia ich publikacji, również przy zmianach wstecznych. Daty świadczeń wybierają właściwe wiersze każdego wydania.</p>"
        + controls
        + "<p>Zakres porównania: obecność grup, definicje, oznaczenia zakresów i wartości katalogowe na wybrane dni. "
        + "Nie obejmuje zmian algorytmu, warunków kwalifikacji ani MODTAR; nie jest pełnym audytem wydania.</p>"
        + f"<details><summary>Porównaj kolejne wydania źródłowe</summary><ul>{quick}</ul></details>"
        + f"<noscript><p>Wybierz okres powyżej lub zakres poniżej. Porównanie nie wymaga JavaScript.</p><ul>{scope_links}</ul></noscript>"
        + '</div><div class="shell">'
        + f'<p id="compare-summary">Liczba zmienionych grup dla tego wyboru: {len(keys)}.'
        + (f" Zakres: {_e(selected_scope)}." if selected_scope else "")
        + "</p>"
        + (
            "".join(_group(comparison, key, dataset, scope) for key in keys)
            if keys
            else "<p>Brak zmian w porównywanym zakresie. Nie oznacza to braku zmian poza zakresem tego porównania.</p>"
        )
        + '<details class="compare-provenance"><summary>Źródła obu stron i tożsamość porównania</summary><div class="compare-sides">'
        + "".join(
            f"<section><h2>{title}: {_e(side['service_date'])}</h2><p>Stan na dzień publikacji: {_e(side['known_on'])}</p>{provenance(side, heading='h3')}<p>Wydanie: <code>{_e(side['release_id'])}</code></p></section>"
            for title, side in (("Przed", comparison.before), ("Po", comparison.after))
        )
        + f"</div><p>Zbiór: <code>{dataset}</code>. Link zachowuje obie daty, wydania i filtry oraz tę tożsamość zbioru. "
        + "Po wycofaniu zbioru porównanie jest jawnie niedostępne, bez zamiany na nowsze wydanie.</p></details></div>"
    )
    return _jgp_page(
        title="Porównanie katalogów JGP - Minos",
        description="Źródłowe zmiany grup i punktów bazowych między dwiema datami.",
        path=comparison_path(
            dataset, before, after, before_release, after_release, scope, group
        ),
        content=body,
    )
