"""Server-rendered dictionary code pages and sitemaps for crawlers and agents.

Pages reuse the site layout rendered by scripts/build_site.py into
page_shell.html, so header, footer and styles stay identical to static pages.
Every source value is HTML-escaped; JSON-LD escapes "<" to stay a data block.
"""

import html
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Final, cast

SITE: Final = "https://minoshealth.pl"
CODE_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]{0,15}")
NOTE_TITLES: Final = {
    "includes": "Obejmuje",
    "excludes": "Nie obejmuje",
    "term": "Terminy w źródle",
    "note": "Uwagi",
    "modifier": "Podział dodatkowy",
}


@dataclass(frozen=True)
class Dictionary:
    slug: str
    system: str
    name: str
    coding_system: str
    source: str


DICTIONARIES: Final = {
    "icd-10": Dictionary(
        "icd-10",
        "icd10-pl",
        "ICD-10",
        "ICD-10",
        "Treść i układ: Rejestr Systemów Kodowania (RSK) i Biuro Światowej"
        + " Organizacji Zdrowia w Polsce, licencja CC BY-NC-SA 3.0 IGO."
        + " Tłumaczenie polskie nie pochodzi od WHO.",
    ),
    "icd-9": Dictionary(
        "icd-9",
        "icd9-pl",
        "ICD-9",
        "ICD-9-PL",
        "Nazwa, status i daty: Centralne Zasoby Słownikowe NFZ. Miejsce w"
        + " hierarchii i uwagi oznaczone jako skoroszyt: skoroszyt ICD-9 PL NFZ"
        + " tej samej wersji.",
    ),
    "icd-11": Dictionary(
        "icd-11",
        "icd11-pl",
        "ICD-11",
        "ICD-11",
        "Treść: polska wersja językowa ICD-11 (MMS) z Rejestru Systemów Kodowania"
        + " (RSK), wersja przedostateczna, licencja CC BY-ND 4.0. Tłumaczenie nie"
        + " zostało dokonane przez WHO i WHO nie ponosi za nie odpowiedzialności;"
        + " wiążąca jest oryginalna wersja angielska (https://icd.who.int). ICD-11"
        + " nie obowiązuje jeszcze w Polsce i nie służy do rozliczeń NFZ.",
    ),
}


def _shell(name: str = "page_shell.html") -> str:
    return files("minos.http").joinpath(name).read_text(encoding="utf-8")


def _e(value: object) -> str:
    return html.escape(" ".join(str(value).split()))


DATE_ATTRIBUTES = frozenset({"data_od", "data_do"})


def display_date(value: object) -> str:
    """Visible ISO dates and months in the Polish DD.MM.RRRR / MM.RRRR form.

    URLs, attributes and machine-readable values keep ISO; anything that is
    not exactly an ISO date or month is returned unchanged.
    """
    text = str(value)
    if match := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text):
        return f"{match[3]}.{match[2]}.{match[1]}"
    if match := re.fullmatch(r"(\d{4})-(\d{2})", text):
        return f"{match[2]}.{match[1]}"
    return text


def _json_ld(value: object) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        '    <script type="application/ld+json">'
        + text.replace("<", "\\u003c")
        + "</script>\n"
    )


def _page(
    *,
    title: str,
    description: str,
    path: str,
    content: str,
    head: str,
    robots: str,
    template: str = "page_shell.html",
) -> str:
    values = {
        "title": html.escape(title),
        "description": html.escape(description[:300]),
        "robots": robots,
        "path": path,
        "head_extra": head,
        "content": content,
    }
    return re.sub(
        r"\{\{([a-z_]+)\}\}", lambda match: values[match.group(1)], _shell(template)
    )


def code_path(dictionary: Dictionary, code: str) -> str:
    return f"/slowniki/{dictionary.slug}/{code}"


def _item(dictionary: Dictionary, code: str | None, label: str) -> str:
    text = (f'<span class="code-chip">{_e(code)}</span> ' if code else "") + (
        f'<span class="result-label">{_e(label)}</span>'
    )
    if not code:
        return f"<li>{text}</li>"
    return f'<li><a class="code-link" href="{_e(code_path(dictionary, code))}">{text}</a></li>'


def _rank_section(evidence: dict[str, object]) -> list[str]:
    parts = [
        '        <section class="detail-notes procedure-rank"><h2>Ranga procedury w JGP (SZP)</h2>'
    ]
    if evidence["status"] == "not_selected":
        parts.append(
            "<p>Nie wybrano wykazu rang dla tego wydania słownika. Brak danych nie oznacza rangi 0.</p></section>"
        )
        return parts
    if evidence["status"] == "recorded":
        parts.append(
            f"<p><strong>Ranga {_e(evidence['rank'])}</strong> w wybranym wykazie NFZ.</p>"
        )
        parts.append(f"<p>Nazwa w wykazie rang: {_e(evidence['source_title'])}</p>")
    else:
        parts.append(
            "<p>Brak pozycji w wybranym wykazie rang SZP. Nie oznacza to rangi 0 ani braku znaczenia w grupowaniu.</p>"
        )
    values = [
        ("Algorytm", evidence["algorithm_version"]),
        (
            "Pierwszy okres sprawozdawczy wersji",
            display_date(evidence["first_reporting_month"]),
        ),
        ("Wydanie dowodu", evidence["algorithm_release_id"]),
    ]
    if evidence["status"] == "recorded":
        values.append(
            (
                "Pozycja źródłowa",
                f"{evidence['worksheet']}, wiersz {evidence['source_row']}",
            )
        )
    rank_source = cast(dict[str, object], evidence["rank_source"])
    algorithm_source = cast(dict[str, object], evidence["algorithm_source"])
    values.append(("SHA-256 wykazu", rank_source["sha256"]))
    parts.append(
        '<dl class="detail-facts code-facts">'
        + "".join(
            f"<div><dt>{_e(name)}</dt><dd>{_e(value)}</dd></div>"
            for name, value in values
        )
        + "</dl>"
    )
    parts.append(
        "<p>Dowód referencyjny powiązany po dokładnym kodzie, nie dobór rulesetu dla daty świadczenia. Ranga opisuje rolę w algorytmie, nie priorytet kliniczny ani możliwość rozliczenia.</p>"
    )
    parts.append(
        f'<p class="detail-source">{_e(evidence["publisher"])}. {_e(evidence["license"])}</p>'
    )
    parts.append(
        f'<p><a href="{_e(rank_source["source_url"])}">Oficjalny wykaz rang NFZ</a> · <a href="{_e(algorithm_source["source_url"])}">Opis algorytmu NFZ</a></p></section>'
    )
    return parts


MAP_TABLES: Final = {
    "10to11_one": "ICD-10 do ICD-11, jedna kategoria",
    "10to11_many": "ICD-10 do ICD-11, wiele kategorii",
    "11to10_one": "ICD-11 do ICD-10, jedna kategoria",
}
_PLAIN_CODE: Final = re.compile(r"[0-9A-Z]{1,6}(?:\.[0-9A-Z]{1,4})?")


def icd11_map_section(system: str, rows: list[dict[str, object]]) -> str:
    """WHO map rows as published: codes, titles and URIs, no match type added."""
    if not rows:
        return ""
    target, field, title_field = (
        ("icd-11", "icd11_code", "icd11_title")
        if system == "icd10-pl"
        else ("icd-10", "icd10_code", "icd10_title")
    )
    heading = (
        "Odpowiedniki w ICD-11" if system == "icd10-pl" else "Odpowiedniki w ICD-10"
    )
    parts = [f'        <section class="detail-notes icd11-map"><h2>{heading}</h2>']
    for table, label in MAP_TABLES.items():
        group = [row for row in rows if row["table"] == table]
        if not group:
            continue
        items: list[str] = []
        for row in group:
            code = cast(str | None, row[field])
            title = cast(str | None, row[title_field])
            chip = ""
            if code is not None:
                chip = (
                    f'<a class="code-chip" href="/slowniki/{target}/{_e(code)}">{_e(code)}</a> '
                    if _PLAIN_CODE.fullmatch(code)
                    else f'<span class="code-chip">{_e(code)}</span> '
                )
            uri = cast(str | None, row["icd11_uri"])
            items.append(
                "<li>"
                + chip
                + _e(title or "")
                + (f' <span class="map-uri">{_e(uri)}</span>' if uri else "")
                + "</li>"
            )
        parts.append(f"<h3>Tabela WHO: {label}</h3><ul>{''.join(items)}</ul>")
    parts.append(
        '<p class="detail-source">Wiersze tabel WHO z zasobu ICD-11 PL w RSK (wydanie'
        + " 2026-01) bez zmian. Strona ICD-10 tabel to ICD-10 WHO 2019, nie polskie"
        + " wydanie 2008. Tabele nie określają rodzaju odpowiedniości i nie służą do"
        + " rozliczeń NFZ.</p></section>"
    )
    return "".join(parts)


def code_page(
    dictionary: Dictionary,
    node: dict[str, object],
    children: list[dict[str, object]],
    version: str,
    supporting: bool,
    rank_evidence: dict[str, object] | None = None,
    jgp_section: str = "",
    dated_path: str | None = None,
    map_section: str = "",
    children_truncated: bool = False,
) -> str:
    code = cast(str, node["code"])
    label = " ".join(cast(str, node["label"]).split())
    path = cast(list[dict[str, object]], node.get("path", []))
    attributes = cast(list[dict[str, str]], node.get("attributes", []))
    notes = cast(list[dict[str, str]], node.get("notes", []))
    crumbs = [
        '<a href="/slowniki">Słowniki</a>',
        f'<a href="/slowniki/{dictionary.slug}">{_e(dictionary.name)}</a>',
    ]
    for step in path:
        step_code = cast(str | None, step.get("code"))
        if step_code:
            crumbs.append(
                f'<a href="{_e(code_path(dictionary, step_code))}">{_e(step_code)}</a>'
            )
    parts = [
        '      <div class="shell page-head page-head-compact">',
        '        <nav class="breadcrumbs" aria-label="Okruszki">'
        + '<span aria-hidden="true">/</span>'.join(crumbs)
        + "</nav>",
        f'        <p class="detail-code"><span class="code-chip">{_e(code)}</span>'
        + f' <span class="badge badge-muted">{_e(dictionary.name)}, wersja {_e(version)}</span></p>',
        f"        <h1>{_e(label)}</h1>",
    ]
    status = next(
        (item["value"] for item in attributes if item["key"] == "status"), None
    )
    if status and status != "A":
        parts.append(
            f'        <p class="badge badge-warn">Status {_e(status)} w CZS</p>'
        )
    if dictionary.system == "icd11-pl":
        parts.append(
            '        <p class="badge badge-warn">Wersja przedostateczna ICD-11:'
            + " nie obowiązuje i nie służy do rozliczeń NFZ</p>"
        )
    if node.get("canonical") is False:
        parts.append(
            '        <p class="badge badge-muted">Pozycja tylko w skoroszycie NFZ,'
            + " bez odpowiednika w CZS</p>"
        )
    parts.append(
        '        <p class="code-actions">'
        + f'<a class="button" href="/slowniki/{dictionary.slug}#kod={_e(code)}">Pokaż w drzewie słownika</a>'
        + '<a class="button button-quiet" href="/">Wyszukaj inny kod</a></p>'
    )
    parts.append("      </div>")
    parts.append('      <div class="shell code-page">')
    facts = [
        f"<div><dt>{_e(item['name'])}</dt><dd>{_e(display_date(item['value']) if item['key'] in DATE_ATTRIBUTES else item['value'])}</dd></div>"
        for item in attributes
        if item["key"] != "evidence_kind" and item["value"].strip()
    ]
    if facts:
        parts.append(
            '        <dl class="detail-facts code-facts">' + "".join(facts) + "</dl>"
        )
    if rank_evidence is not None:
        parts.extend(_rank_section(rank_evidence))
    if jgp_section:
        parts.append(jgp_section)
    if map_section:
        parts.append(map_section)
    for kind in ("includes", "excludes", "term", "modifier", "note"):
        group = [note for note in notes if note["kind"] == kind]
        if not group:
            continue
        suffix = " (skoroszyt NFZ)" if supporting and kind != "modifier" else ""
        items = "".join(
            "<li>"
            + (
                f'<span class="code-chip">{_e(note["code"])}</span> '
                if note["code"]
                else ""
            )
            + _e(note["text"])
            + "</li>"
            for note in group
        )
        parts.append(
            f'        <section class="detail-notes"><h2>{NOTE_TITLES[kind]}{suffix}</h2><ul>{items}</ul></section>'
        )
    if children:
        items = "".join(
            _item(
                dictionary, cast(str | None, child["code"]), cast(str, child["label"])
            )
            for child in children
        )
        more = (
            '<p class="detail-source">Pokazano pierwsze pozycje. Użyj wyszukiwania,'
            + " aby znaleźć pozostałe.</p>"
            if children_truncated
            else ""
        )
        parts.append(
            '        <section class="code-children"><h2>Kody podrzędne</h2>'
            + f'<ul class="code-list">{items}</ul>{more}</section>'
        )
    if path:
        items = "".join(
            _item(
                dictionary, cast(str | None, step.get("code")), cast(str, step["label"])
            )
            for step in path
        )
        parts.append(
            '        <section class="code-children"><h2>Miejsce w słowniku</h2>'
            + f'<ul class="code-list">{items}</ul></section>'
        )
    parts.append(f'        <p class="detail-source">{_e(dictionary.source)}</p>')
    parts.append("      </div>")
    url = SITE + code_path(dictionary, code)
    breadcrumb = [
        {
            "@type": "ListItem",
            "position": 1,
            "name": "Słowniki",
            "item": SITE + "/slowniki",
        },
        {
            "@type": "ListItem",
            "position": 2,
            "name": dictionary.name,
            "item": f"{SITE}/slowniki/{dictionary.slug}",
        },
    ]
    for position, step in enumerate(
        [step for step in path if step.get("code")], start=3
    ):
        breadcrumb.append(
            {
                "@type": "ListItem",
                "position": position,
                "name": f"{step['code']} {' '.join(cast(str, step['label']).split())}",
                "item": SITE + code_path(dictionary, cast(str, step["code"])),
            }
        )
    head = _json_ld(
        [
            {
                "@context": "https://schema.org",
                "@type": "MedicalCode",
                "codeValue": code,
                "codingSystem": dictionary.coding_system,
                "name": label,
                "url": url,
                "inLanguage": "pl",
            },
            {
                "@context": "https://schema.org",
                "@type": "BreadcrumbList",
                "itemListElement": breadcrumb,
            },
        ]
    )
    if jgp_section:
        head += '    <script src="/jgp.js" defer></script>\n'
    context = next(
        (cast(str, step["label"]) for step in path if step.get("code")), dictionary.name
    )
    description = (
        f"Kod {dictionary.name} {code}: {label}. "
        + f"{' '.join(context.split())}. Uwagi źródła, kody podrzędne i miejsce w słowniku."
    )
    return _page(
        title=f"{code} {label} - {dictionary.name} - Minos Health",
        description=description,
        path=dated_path or code_path(dictionary, code),
        content="\n".join(parts),
        head=head,
        robots="noindex, follow" if dated_path else "index, follow",
    )


def missing_page(dictionary: Dictionary, code: str) -> str:
    content = (
        '      <div class="shell page-head not-found">\n'
        + '        <p class="not-found-code">404</p>\n'
        + f"        <h1>Nie ma kodu {_e(code)} w słowniku {_e(dictionary.name)}</h1>\n"
        + '        <p class="page-lead">Sprawdź pisownię kodu albo wyszukaj go po opisie.</p>\n'
        + '        <p class="not-found-actions"><a class="button" href="/">Przejdź do wyszukiwarki</a>'
        + f'<a class="button button-quiet" href="/slowniki/{dictionary.slug}">Przeglądaj słownik</a></p>\n'
        + "      </div>"
    )
    return _page(
        title=f"Nie znaleziono kodu - {dictionary.name} - Minos Health",
        description=f"Kodu {code} nie ma w słowniku {dictionary.name}.",
        path=f"/slowniki/{dictionary.slug}",
        content=content,
        head="",
        robots="noindex, follow",
    )


def recovery_page(slug: str, code: str, reason: str, retry_path: str) -> str:
    dictionary = DICTIONARIES[slug]
    message = {
        "busy": "Wszystkie stanowiska wyszukiwania są chwilowo zajęte. Spróbuj ponownie za chwilę.",
        "query_timeout": "Przygotowanie opisu przekroczyło czas oczekiwania. To nie oznacza, że kodu nie ma w słowniku.",
        "limit_exceeded": "Opis przekroczył bezpieczny limit przetwarzania. Możesz wrócić do wyszukiwarki lub przeglądać słownik.",
    }.get(reason, "Nie udało się teraz odczytać słownika. Spróbuj ponownie za chwilę.")
    content = (
        '<section class="shell page-head not-found" aria-live="polite">'
        f"<h1>Nie udało się wczytać opisu {_e(code)}</h1>"
        f'<p class="page-lead">{_e(message)}</p>'
        '<p class="not-found-actions">'
        f'<a class="button" href="{_e(retry_path)}">Spróbuj ponownie</a>'
        '<a class="button button-quiet" href="/">Wróć do wyszukiwarki</a>'
        f'<a class="button button-quiet" href="/slowniki/{dictionary.slug}">Przeglądaj słownik</a>'
        "</p></section>"
    )
    return _page(
        title=f"Ponów odczyt - {dictionary.name} - Minos Health",
        description=message,
        path=retry_path,
        content=content,
        head="",
        robots="noindex, follow",
    )


def sitemap(dictionary: Dictionary, codes: list[str]) -> bytes:
    entries = "".join(
        f"  <url><loc>{html.escape(SITE + code_path(dictionary, code))}</loc></url>\n"
        for code in codes
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + entries
        + "</urlset>\n"
    ).encode()
