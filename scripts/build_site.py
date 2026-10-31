"""Render every public page from one layout so header, footer and styles match.

The maintenance edge (Ansible) receives the API-free subset, committed under
infra/ansible/files and verified with --check. The validation edge adds the
search, dictionary and error pages that need the reference API.
"""

import argparse
import html
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, final

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps/web"
SITE = WEB / "site"
MAINTENANCE_OUTPUT = ROOT / "infra/ansible/files"
PAGE_SHELL = ROOT / "src/minos/http/page_shell.html"
JGP_PAGE_SHELL = ROOT / "src/minos/http/page_shell_jgp.html"
SOURCES_PAGE_SHELL = ROOT / "src/minos/http/page_shell_sources.html"
SOURCES_CONTENT = ROOT / "src/minos/http/source_catalog.html"
type Mode = Literal["maintenance", "validation"]
PLAUSIBLE = (
    '    <script src="/plausible-init.js"></script>\n'
    + '    <script async src="https://plausible.io/js/pa-NFvtUdLPyyrZ5D9U4M1M9.js"></script>\n'
)
FONT_PRELOAD = (
    '    <link rel="preload" href="/fonts/atkinson-next-400.woff2" as="font"'
    + ' type="font/woff2" crossorigin>\n'
)
SOCIAL_IMAGE = """    <meta property="og:image" content="https://minoshealth.pl/social-preview-v1.png">
    <meta property="og:image:type" content="image/png">
    <meta property="og:image:width" content="1200">
    <meta property="og:image:height" content="630">
    <meta property="og:image:alt" content="Minos Health - rozpoznania ICD-10, procedury ICD-9 i grupy JGP">
    <meta name="twitter:image" content="https://minoshealth.pl/social-preview-v1.png">
    <meta name="twitter:image:alt" content="Minos Health - rozpoznania ICD-10, procedury ICD-9 i grupy JGP">
"""
FONTS = (
    "atkinson-next-400.woff2",
    "atkinson-next-600.woff2",
    "atkinson-next-700.woff2",
    "atkinson-mono-400.woff2",
    "atkinson-mono-600.woff2",
)
RETIRED_STYLESHEET = (
    b"/* Retired: every page uses /site.css. Kept only because the Ansible\n"
    + b"   runtime inventory still deploys this file name. */\n"
)
UNAVAILABLE_HELP = (
    "Wyszukiwarka jest chwilowo niedostępna. Słowniki i zarządzenia znajdziesz"
    + ' w <a href="/zrodla">Źródłach danych</a>.'
)


def _json_ld(value: object) -> str:
    # JSON-LD is a data block, not executed script; escape "<" for HTML safety.
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return (
        '    <script type="application/ld+json">'
        + text.replace("<", "\\u003c")
        + "</script>\n"
    )


SITE_JSON_LD = _json_ld(
    {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "Minos Health",
        "url": "https://minoshealth.pl/",
        "inLanguage": "pl",
        "description": "Bezpłatna wyszukiwarka rozpoznań ICD-10 PL, procedur ICD-9 PL"
        + " i grup JGP. Sprawdź warunki grupowania, punkty bazowe i źródła NFZ.",
        "publisher": {
            "@type": "Organization",
            "name": "Data Craze Krzysztof Bury",
            "email": "kontakt@minoshealth.pl",
        },
    }
)
LIVE_HELP = (
    "Nie wpisuj danych pacjentów. Wyniki pokazują treść słownika"
    + " i nie przesądzają o rozliczeniu świadczenia."
)


@dataclass(frozen=True)
class Page:
    output: str
    source: str
    path: str
    title: str
    description: str
    robots: str
    nav: str | None
    modes: tuple[Mode, ...]
    analytics: bool = True
    script: str | None = None
    body_class: str = ""
    head_extra: str = ""
    values: dict[str, str] = field(default_factory=dict[str, str])
    # Values computed only when the page renders (the MCP tool catalog).
    computed: Callable[[], dict[str, str]] | None = None


def _dictionary_tabs(current: str) -> str:
    tabs: list[str] = []
    for path, label in (
        ("/slowniki/icd-10", "ICD-10"),
        ("/slowniki/icd-9", "ICD-9"),
        ("/slowniki/icd-11", "ICD-11"),
    ):
        state = ' aria-current="page"' if path == current else ""
        tabs.append(
            f'          <a class="dictionary-tab" role="listitem" href="{path}"{state}>{label}</a>'
        )
    return "\n".join(tabs)


JGP_SHORTCUT = (
    '          <li><a class="start-link" href="/jgp"><strong>Grupy JGP</strong>'
    + "<span>Punkty bazowe grup na wybrany dzień</span></a></li>\n"
)

ICD10_NOTE = (
    "<p>Źródłem jest Rejestr Systemów Kodowania (RSK, Centrum e-Zdrowia) i Biuro"
    + " Światowej Organizacji Zdrowia w Polsce. Treść i jej przekształcenia są"
    + ' objęte licencją <a href="https://creativecommons.org/licenses/by-nc-sa/3.0/igo/"'
    + ' rel="noopener noreferrer">CC BY-NC-SA 3.0 IGO</a>.</p>'
    + "<p>Tłumaczenie polskie nie zostało przygotowane przez WHO; WHO nie odpowiada"
    + " za jego treść ani poprawność. Wiążące jest angielskie wydanie WHO 2019."
    + " Minos zachowuje tekst i układ źródła; żaden wydawca nie popiera Minos.</p>"
)
# The WHO translation agreement (TR/24/013, clause 7) fixes this disclaimer.
ICD11_NOTE = (
    "<p>Źródłem jest polska wersja językowa ICD-11 (MMS, wydanie WHO 2026-01)"
    + " z Rejestru Systemów Kodowania (RSK, Centrum e-Zdrowia). Jest to wersja"
    + " przedostateczna (pre-released), opublikowana do konsultacji. Jej"
    + " udostępnienie nie oznacza, że ICD-11 wchodzi w życie: nie służy do"
    + " rozliczeń NFZ, które nadal opierają się na ICD-10.</p>"
    + "<p>Tłumaczenie nie zostało dokonane przez Światową Organizację Zdrowia"
    + " (WHO) i WHO nie ponosi za nie odpowiedzialności. W przypadku jakichkolwiek"
    + " rozbieżności między wersją angielską a tłumaczeniem wersją wiążącą i"
    + " autentyczną jest oryginalna wersja angielska. Oryginalna wersja angielska"
    + ' i tłumaczenie są dostępne na platformie <a href="https://icd.who.int"'
    + ' rel="noopener noreferrer">WHO ICD-11</a>. Tłumaczenie jest dostępne na'
    + ' warunkach licencji <a href="https://creativecommons.org/licenses/by-nd/4.0/"'
    + ' rel="noopener noreferrer">CC BY-ND 4.0</a>; Minos pokazuje kody, nazwy,'
    + " identyfikatory WHO (URI) i teksty bez zmian. Żaden wydawca nie popiera"
    + " Minos.</p>"
    + "<p>International Classification of Diseases, Eleventh Revision (ICD-11),"
    + " World Health Organization (WHO) 2019 https://icd.who.int/browse11."
    + " Licensed under the Creative Commons Attribution-NoDerivatives 3.0 IGO"
    + " licence (CC BY-ND 3.0 IGO).</p>"
)
ICD9_NOTE = (
    "<p>Kody, nazwy, statusy i daty pochodzą z Centralnych Zasobów Słownikowych"
    + " NFZ (CZS). CZS nie podaje hierarchii, dlatego układ działów pochodzi ze"
    + " skoroszytu ICD-9 PL tej samej wersji opublikowanego przez NFZ i jest"
    + " powiązany z CZS wyłącznie po dokładnym kodzie.</p>"
    + "<p>Źródła nie podają licencji; Minos udostępnia je niekomercyjnie do"
    + " celów referencyjnych. Żaden wydawca nie popiera Minos.</p>"
)

PAGES = (
    Page(
        "grouper.html",
        "grouper.html",
        "/gruper",
        "Gruper JGP - Minos Health",
        "Sprawdź kwalifikację i prześledź warunki grupowania zgłoszonego przypadku.",
        "noindex, nofollow",
        "/gruper",
        ("validation",),
        analytics=False,
        script="/grouper.js",
        body_class="page-grouper",
    ),
    Page(
        "maintenance-index.html",
        "home.html",
        "/",
        "Minos Health - przerwa techniczna",
        "Wyszukiwarka jest chwilowo niedostępna. Sprawdź oficjalne słowniki"
        + " i zarządzenia NFZ na stronie Źródła danych.",
        "noindex, nofollow",
        "/",
        ("maintenance",),
        body_class="page-home",
        values={
            "search_disabled": " disabled",
            "search_help": UNAVAILABLE_HELP,
            # The maintenance edge has no reference API, so no JGP pages.
            "jgp_shortcut": "",
        },
    ),
    Page(
        "index.html",
        "home.html",
        "/",
        "Minos Health - wyszukiwarka ICD-10, ICD-9 i grup JGP",
        "Bezpłatna wyszukiwarka rozpoznań ICD-10 PL, procedur ICD-9 PL i grup JGP."
        + " Sprawdź warunki grupowania, punkty bazowe i źródła NFZ.",
        "index, follow",
        "/",
        ("validation",),
        script="/search.js",
        body_class="page-home",
        head_extra=SITE_JSON_LD,
        values={
            "search_disabled": "",
            "search_help": LIVE_HELP,
            "jgp_shortcut": JGP_SHORTCUT,
        },
    ),
    Page(
        "dictionaries.html",
        "dictionaries.html",
        "/slowniki",
        "Słowniki - Minos Health",
        "Przeglądaj pełne słowniki ICD-10, ICD-9 i ICD-11 w układzie źródła.",
        "index, follow",
        "/slowniki",
        ("validation",),
        analytics=False,
    ),
    Page(
        "dictionary-icd-10.html",
        "dictionary.html",
        "/slowniki/icd-10",
        "Słownik ICD-10 - Minos Health",
        "Pełny słownik ICD-10: rozdziały, grupy i kody rozpoznań z uwagami źródła.",
        "index, follow",
        "/slowniki",
        ("validation",),
        analytics=False,
        script="/dictionary.js",
        body_class="page-dictionary",
        values={
            "system": "icd10-pl",
            "dictionary_short": "ICD-10",
            "dictionary_title": "Słownik ICD-10",
            "dictionary_lead": "Międzynarodowa Statystyczna Klasyfikacja Chorób i"
            + " Problemów Zdrowotnych z Rejestru Systemów Kodowania.",
            "tree_placeholder": "np. J18 albo zapalenie płuc",
            "dictionary_tabs": _dictionary_tabs("/slowniki/icd-10"),
            "source_note": ICD10_NOTE,
        },
    ),
    Page(
        "dictionary-icd-9.html",
        "dictionary.html",
        "/slowniki/icd-9",
        "Słownik ICD-9 - Minos Health",
        "Pełny słownik ICD-9: działy i kody procedur medycznych z uwagami źródła.",
        "index, follow",
        "/slowniki",
        ("validation",),
        analytics=False,
        script="/dictionary.js",
        body_class="page-dictionary",
        values={
            "system": "icd9-pl",
            "dictionary_short": "ICD-9",
            "dictionary_title": "Słownik ICD-9",
            "dictionary_lead": "Międzynarodowa Klasyfikacja Procedur Medycznych"
            + " z Centralnych Zasobów Słownikowych NFZ.",
            "tree_placeholder": "np. 89.00 albo badanie moczu",
            "dictionary_tabs": _dictionary_tabs("/slowniki/icd-9"),
            "source_note": ICD9_NOTE,
        },
    ),
    Page(
        "dictionary-icd-11.html",
        "dictionary.html",
        "/slowniki/icd-11",
        "Słownik ICD-11 - Minos Health",
        "Polska wersja przedostateczna ICD-11: rozdziały, bloki i kody z"
        + " włączeniami i terminami indeksu.",
        "index, follow",
        "/slowniki",
        ("validation",),
        analytics=False,
        script="/dictionary.js",
        body_class="page-dictionary",
        values={
            "system": "icd11-pl",
            "dictionary_short": "ICD-11",
            "dictionary_title": "Słownik ICD-11",
            "dictionary_lead": "Międzynarodowa Statystyczna Klasyfikacja Chorób i"
            + " Problemów Zdrowotnych, 11. rewizja: polska wersja przedostateczna,"
            + " jeszcze nieobowiązująca.",
            "tree_placeholder": "np. BA00 albo nadciśnienie",
            "dictionary_tabs": _dictionary_tabs("/slowniki/icd-11"),
            "source_note": ICD11_NOTE,
        },
    ),
    Page(
        "sources.html",
        "sources.html",
        "/zrodla",
        "Źródła danych medycznych - Minos Health",
        "Przejrzyj słowniki medyczne i zarządzenia NFZ. Sprawdź wersję, historię zmian"
        + " i daty dokumentów oraz otwórz pliki w oficjalnym źródle.",
        "index, follow",
        "/zrodla",
        ("maintenance", "validation"),
        script="/sources.js",
        values={
            "initial_catalog": "<p>Katalog online jest dostępny po włączeniu JavaScript. Oficjalne źródła są podlinkowane poniżej.</p>"
        },
    ),
    Page(
        "privacy-policy.html",
        "privacy.html",
        "/polityka-prywatnosci",
        "Polityka prywatności - Minos Health",
        "Jakie dane przetwarza minoshealth.pl i w jakim celu.",
        "noindex, follow",
        None,
        ("maintenance", "validation"),
    ),
    Page(
        "agents.html",
        "agents.html",
        "/dla-agentow",
        "Minos Health dla agentów AI (MCP) - Minos Health",
        "Serwer MCP dla agentów AI: wyszukiwanie ICD-10, ICD-9, ICD-11 i JGP, karty"
        + " kodów, mapy WHO i Gruper. Bezpłatnie, bez klucza, tylko do odczytu.",
        "index, follow",
        None,
        ("validation",),
        analytics=False,
        body_class="page-agents",
        computed=lambda: _mcp_values(),
    ),
    Page(
        "terms.html",
        "terms.html",
        "/regulamin",
        "Regulamin - Minos Health",
        "Zasady korzystania z minoshealth.pl.",
        "noindex, follow",
        None,
        ("maintenance", "validation"),
    ),
    Page(
        "404.html",
        "not-found.html",
        "/404",
        "Nie znaleziono strony - Minos Health",
        "Nie ma takiej strony w serwisie Minos Health.",
        "noindex, nofollow",
        None,
        ("validation",),
        analytics=False,
    ),
    Page(
        "unavailable.html",
        "unavailable.html",
        "/unavailable",
        "Ponów odczyt - Minos Health",
        "Serwis jest chwilowo niedostępny. Spróbuj ponownie.",
        "noindex, nofollow",
        None,
        ("validation",),
        analytics=False,
    ),
)


def _nav(mode: Mode, current: str | None) -> str:
    items = [("/", "Wyszukiwarka")]
    if mode == "validation":
        items.append(("/slowniki", "Słowniki"))
        items.append(("/jgp", "Grupy JGP"))
        items.append(("/gruper", "Gruper"))
    items.append(("/zrodla", "Źródła danych"))
    links: list[str] = []
    for path, label in items:
        state = ' aria-current="page"' if path == current else ""
        links.append(f'            <a href="{path}"{state}>{label}</a>')
    return "\n".join(links)


def _mcp_values() -> dict[str, str]:
    from minos.http.mcp_docs import ENDPOINT, catalog, reference_html

    tools = catalog()
    return {
        "mcp_endpoint": ENDPOINT,
        "tool_count": str(len(tools)),
        "tool_reference": reference_html(tools),
    }


def _footer_nav(mode: Mode) -> str:
    links = (
        ("/zrodla", "Źródła danych"),
        *((("/dla-agentow", "Dla agentów AI (MCP)"),) if mode == "validation" else ()),
        ("/polityka-prywatnosci", "Polityka prywatności"),
        ("/regulamin", "Regulamin"),
        ("mailto:kontakt@minoshealth.pl", "kontakt@minoshealth.pl"),
    )
    return "\n".join(f'          <a href="{href}">{label}</a>' for href, label in links)


def _fill(template: str, values: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in values:
            raise SystemExit(f"Unknown template value: {name}")
        return values[name]

    return re.sub(r"\{\{([a-z_]+)\}\}", replace, template)


def render_page(page: Page, mode: Mode) -> str:
    layout = (SITE / "layout.html").read_text(encoding="utf-8")
    content = _fill(
        (SITE / "pages" / page.source).read_text(encoding="utf-8"),
        {**page.values, **(page.computed() if page.computed else {})},
    )
    scripts = PLAUSIBLE if page.analytics else ""
    if page.script:
        if page.script in ("/search.js", "/dictionary.js", "/grouper.js"):
            scripts += '    <script defer src="/request.js"></script>\n'
        if page.script == "/grouper.js":
            scripts += '    <script defer src="/datepicker.js"></script>\n'
        scripts += f'    <script defer src="{page.script}"></script>\n'
    rendered = _fill(
        layout,
        {
            "title": html.escape(page.title),
            "description": html.escape(page.description),
            "robots": page.robots,
            "path": page.path,
            "preload": FONT_PRELOAD if mode == "validation" else "",
            "scripts": scripts,
            "body_class": page.body_class,
            "head_extra": page.head_extra,
            "social_image": SOCIAL_IMAGE if mode == "validation" else "",
            "twitter_card": "summary_large_image"
            if mode == "validation"
            else "summary",
            "nav": _nav(mode, page.nav),
            "footer_nav": _footer_nav(mode),
            "content": content.rstrip("\n"),
        },
    )
    lines = [line.rstrip() for line in rendered.splitlines()]
    return "\n".join(lines) + "\n"


def render_site(mode: Mode) -> dict[str, bytes]:
    """Every file one edge serves from its static root, keyed by file name."""
    files = {
        page.output: render_page(page, mode).encode()
        for page in PAGES
        if mode in page.modes
    }
    stylesheet = (WEB / "site.css").read_bytes()
    if mode == "maintenance":
        # The Ansible runtime inventory still deploys these three names; nginx
        # serves /site.css from legal.css there (try_files fallback). Retire them
        # together with the inventory entries, not separately.
        files["legal.css"] = stylesheet
        for name in ("home.css", "sources.css"):
            files[name] = RETIRED_STYLESHEET
    else:
        files["site.css"] = stylesheet
    if mode == "validation":
        files["social-preview-v1.png"] = (
            WEB / "social" / "social-preview-v1.png"
        ).read_bytes()
        # The MCP server and its registry listing name this raster icon (#266).
        files["icon-512.png"] = (WEB / "social" / "icon-512.png").read_bytes()
        for name in (
            "request.js",
            "datepicker.js",
            "search.js",
            "dictionary.js",
            "jgp.js",
            "grouper.js",
        ):
            files[name] = (WEB / name).read_bytes()
        for name in FONTS:
            files[f"fonts/{name}"] = (WEB / "fonts" / name).read_bytes()
        files["fonts/OFL.txt"] = (WEB / "fonts/OFL.txt").read_bytes()
        files.update(_crawler_files())
    _reject_typography(files)
    return files


SITEMAP_PATHS = (
    "/",
    "/slowniki",
    "/slowniki/icd-10",
    "/slowniki/icd-9",
    "/slowniki/icd-11",
    "/zrodla",
    "/dla-agentow",
)
# The reference API generates one sitemap per dictionary from the verified bundle.
CODE_SITEMAPS = ("/sitemap-icd-10.xml", "/sitemap-icd-9.xml", "/sitemap-icd-11.xml")
LLMS = """# Minos Health

> Bezpłatna, niekomercyjna wyszukiwarka i przeglądarka oficjalnych polskich
> słowników medycznych: ICD-10 (rozpoznania, Rejestr Systemów Kodowania),
> ICD-9 PL (procedury, Centralne Zasoby Słownikowe NFZ) i grup JGP z warunkami
> grupowania i punktami bazowymi. Treść referencyjna,
> nie porada medyczna ani decyzja rozliczeniowa NFZ.

## Strony

- [Wyszukiwarka](https://minoshealth.pl/): kody i opisy ICD-10, ICD-9 i grup JGP.
- [Słownik ICD-10](https://minoshealth.pl/slowniki/icd-10): pełne drzewo rozdziałów i kodów.
- [Słownik ICD-9](https://minoshealth.pl/slowniki/icd-9): pełne drzewo działów i procedur.
- [Słownik ICD-11](https://minoshealth.pl/slowniki/icd-11): polska wersja
  przedostateczna ICD-11 (MMS), jeszcze nieobowiązująca i nie do rozliczeń NFZ.
- Strona kodu: `https://minoshealth.pl/slowniki/icd-10/{KOD}` lub
  `https://minoshealth.pl/slowniki/icd-9/{KOD}`, np. /slowniki/icd-10/J18.9.
  Zawiera nazwę, miejsce w hierarchii, kody podrzędne, uwagi „obejmuje” i
  „nie obejmuje” oraz źródło. Pełne listy kodów są w mapach witryny:
  https://minoshealth.pl/sitemap-icd-10.xml i https://minoshealth.pl/sitemap-icd-9.xml.
- [Grupy JGP](https://minoshealth.pl/jgp): katalog grup JGP (załącznik 1a do
  zarządzenia Prezesa NFZ 46/2026/DSOZ) na wybrany dzień udzielenia świadczenia.
  Strona grupy: `https://minoshealth.pl/jgp/{RRRR-MM-DD}/{KOD}`, np.
  /jgp/2026-10-02/A01. Wartości to punkty bazowe wg katalogu, bez modyfikatorów,
  nie punkty do rozliczenia.
- [Źródła danych](https://minoshealth.pl/zrodla): wersje słowników i zarządzenia NFZ.
- [Dla agentów AI](https://minoshealth.pl/dla-agentow): serwer MCP
  https://minoshealth.pl/mcp (Streamable HTTP, bez klucza, tylko odczyt) z
  wyszukiwaniem ICD-10, ICD-9, ICD-11 i JGP, kartami kodów i grup, mapami WHO
  ICD-10/ICD-11 i Gruperem dla zanonimizowanego pobytu.

## API (JSON, bez klucza)

- `GET /api/v1/releases`: wersje słowników, wydawcy, licencje i uwagi.
- `POST /api/v1/search` z JSON `{"system":"icd10-pl"|"icd9-pl","release_id":"...","query":"...","limit":1-20}`.
  Zapytanie tylko w treści żądania, nigdy w adresie URL.
- `GET /api/v1/tree/{release_id}/{node}`: dzieci pozycji drzewa (`0` to poziom główny).
- `GET /api/v1/jgp/{RRRR-MM-DD}/groups/{KOD}`: grupa JGP z wydaniem katalogu
  obowiązującym w tym dniu; `GET /api/v1/jgp/{RRRR-MM-DD}/sections` oraz
  `/sections/{1a|1ae}/{n}`: rozdziały katalogu. Data bez dopasowanego wydania
  zwraca błąd `no_ruleset_for_date`, nigdy innego wydania.

## Zasady

- Nie wysyłaj danych pacjentów.
- ICD-10: CC BY-NC-SA 3.0 IGO; tłumaczenie polskie nie pochodzi od WHO.
  ICD-9 PL: źródło NFZ bez wskazanej licencji. Wykorzystanie niekomercyjne,
  z podaniem źródła. Żaden wydawca nie popiera Minos.
"""


def _crawler_files() -> dict[str, bytes]:
    robots = (
        "User-agent: *\n"
        + "Allow: /\n"
        + "Disallow: /api/\n"
        # Noindex pages: crawling them gave nothing and cost most of the API's
        # load (#296). The undated /slowniki code pages stay open.
        + "Disallow: /jgp/\n"
        + "Disallow: /slowniki/*/jgp/\n"
        + "Sitemap: https://minoshealth.pl/sitemap.xml\n"
    )
    pages = "".join(
        f"  <url><loc>https://minoshealth.pl{path}</loc></url>\n"
        for path in SITEMAP_PATHS
    )
    sitemaps = "".join(
        f"  <sitemap><loc>https://minoshealth.pl{path}</loc></sitemap>\n"
        for path in ("/sitemap-pages.xml", *CODE_SITEMAPS)
    )
    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    namespace = "http://www.sitemaps.org/schemas/sitemap/0.9"
    return {
        "robots.txt": robots.encode(),
        "llms.txt": LLMS.encode(),
        "sitemap.xml": (
            header
            + f'<sitemapindex xmlns="{namespace}">\n'
            + sitemaps
            + "</sitemapindex>\n"
        ).encode(),
        "sitemap-pages.xml": (
            header + f'<urlset xmlns="{namespace}">\n' + pages + "</urlset>\n"
        ).encode(),
    }


def render_page_shell(nav: str = "/slowniki", body_class: str = "page-code") -> str:
    """Layout for API-rendered pages; {{...}} values stay for the API.

    One shell per section keeps the navigation defined only in this script.
    """
    page = Page(
        "page_shell.html",
        "",
        "{{path}}",
        "{{title}}",
        "{{description}}",
        "index, follow",
        nav,
        ("validation",),
        analytics=False,
        body_class=body_class,
        head_extra="{{head_extra}}",
    )
    layout = (SITE / "layout.html").read_text(encoding="utf-8")
    rendered = _fill(
        layout,
        {
            "title": "{{title}}",
            "description": "{{description}}",
            "robots": "{{robots}}",
            "path": "{{path}}",
            "preload": FONT_PRELOAD,
            "scripts": "",
            "body_class": page.body_class,
            "head_extra": "{{head_extra}}",
            "social_image": SOCIAL_IMAGE,
            "twitter_card": "summary_large_image",
            "nav": _nav("validation", page.nav),
            "footer_nav": _footer_nav("validation"),
            "content": "{{content}}",
        },
    )
    return "\n".join(line.rstrip() for line in rendered.splitlines()) + "\n"


def _reject_typography(files: dict[str, bytes]) -> None:
    # House style: plain hyphens only in authored copy.
    for name, content in files.items():
        if name.endswith((".html", ".css", ".js")) and re.search(
            r"[\u2013\u2014]", content.decode()
        ):
            raise SystemExit(f"{name} contains an en or em dash; use a hyphen.")


@final
class _Arguments(argparse.Namespace):
    check: bool = False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--check", action="store_true")
    arguments = _Arguments()
    _ = parser.parse_args(namespace=arguments)
    stale: list[str] = []
    outputs = {
        MAINTENANCE_OUTPUT / name: content
        for name, content in render_site("maintenance").items()
    }
    outputs[PAGE_SHELL] = render_page_shell().encode()
    outputs[JGP_PAGE_SHELL] = render_page_shell("/jgp", "page-jgp").encode()
    outputs[SOURCES_PAGE_SHELL] = render_page_shell("/zrodla", "page-sources").encode()
    outputs[SOURCES_CONTENT] = (SITE / "pages/sources.html").read_bytes()
    for target, content in outputs.items():
        name = target.name
        if arguments.check:
            if not target.is_file() or target.read_bytes() != content:
                stale.append(name)
        else:
            _ = target.write_bytes(content)
    if stale:
        raise SystemExit(
            "Rendered pages are stale; run scripts/build_site.py: " + ", ".join(stale)
        )


if __name__ == "__main__":
    main()
