"""Bounded initial source HTML and exact-observation citation pages."""

import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

from minos.http.pages import (
    _e,  # pyright: ignore[reportPrivateUsage]
    _json_ld,  # pyright: ignore[reportPrivateUsage]
    _page,  # pyright: ignore[reportPrivateUsage]
    _shell,  # pyright: ignore[reportPrivateUsage]
    display_date,
)
from minos.http.source_reader import (
    IDENTITY,
    PublicSnapshot,
    Source,
    SourceUnavailable,
    load_snapshot,
)

SOURCE_ROUTE = re.compile(
    rf"/zrodla(?:/(rsk|baw|nfz)(?:/obserwacje/({IDENTITY}))?(?:/([0-9A-Z]{{6}}|[1-9][0-9]{{0,15}}))?(?:/strona/([1-9][0-9]{{0,2}}))?)?"
)
PAGE_SIZE = 20
MAX_HTML_BYTES = 1024 * 1024
NAMES = {
    "rsk": "Słowniki RSK",
    "baw": "Zarządzenia BAW NFZ",
    "nfz": "Publikacje NFZ / CZS / AOTMiT",
}
ROLES = {
    1: "Akt pierwotny",
    3: "Tekst po zmianach",
    4: "Porównanie zmian",
    5: "Skrócone porównanie zmian",
    6: "Akt zmieniający",
}
FIELDS = {
    "publisher_name": "Wydawca wg RSK",
    "resource_version": "Wersja wg RSK",
    "valid_from": "Ważność od wg RSK",
    "valid_to": "Data końcowa wg źródła",
    "first_observed_at": "Pierwsza obserwacja Minos (UTC)",
    "last_observed_at": "Ostatnia obserwacja Minos (UTC)",
    "observed_at": "Obserwacja Minos (UTC)",
    "act_date": "Data dokumentu",
    "published_date": "Data publikacji",
    "entry_into_force": "Wejście w życie wg BAW",
    "service_applicability_date": "Dotyczy świadczeń od",
    "service_applicability_evidence": "Podstawa daty stosowania",
    "legal_status": "Status wg BAW",
    "metadata_sha256": "SHA-256 metadanych",
    "metadata_observation_sha256": "SHA-256 metadanych",
    "product_sha256": "SHA-256 pliku",
    "file_name": "Nazwa pliku",
    "file_uuid": "Identyfikator pliku RSK",
    "extension": "Format pliku",
    "file_created_at": "Utworzenie pliku wg RSK",
    "source_archived_at": "Archiwizacja wg RSK",
    "version": "Wersja wg wydawcy",
    "generation": "Generacja techniczna",
    "generated_at": "Czas generacji wg źródła (strefa jak w zapisie)",
    "publisher_valid_from": "Ważność od wg wydawcy",
    "sha256": "SHA-256 odpowiedzi źródła",
}


def _link(path: str, label: str) -> str:
    return f'<a class="card-link" href="{_e(path)}">{_e(label)}</a>'


def _rows(snapshot: PublicSnapshot) -> list[dict[str, object]]:
    return cast(
        list[dict[str, object]],
        snapshot.data["documents" if snapshot.source == "baw" else "entries"],
    )


def _key(source: Source, row: dict[str, object]) -> str:
    return str(row["hcd"] if source == "rsk" else row["document_id"])


def _title(source: Source, row: dict[str, object]) -> str:
    if source == "rsk":
        return str(row["resource_name"])
    if source == "nfz":
        return str(row["title"])
    lineage = cast(dict[str, object] | None, row.get("lineage"))
    role = ROLES[cast(int, lineage["automatic_type"])] if lineage else "Dokument BAW"
    return f"Zarządzenie {row['act_number']} - {role}"


def _facts(row: dict[str, object], *, detailed: bool) -> str:
    summary = {
        "publisher_name",
        "resource_version",
        "last_observed_at",
        "observed_at",
        "published_date",
        "legal_status",
        "version",
    }
    fields = (
        FIELDS
        if detailed
        else {key: label for key, label in FIELDS.items() if key in summary}
    )
    return (
        '<dl class="card-facts">'
        + "".join(
            f"<div><dt>{_e(label)}</dt><dd>{_e(display_date(row[key])) if row[key] is not None else 'Nie podano'}</dd></div>"
            for key, label in fields.items()
            if key in row
        )
        + "</dl>"
    )


def _record(
    snapshot: PublicSnapshot, row: dict[str, object], *, detailed: bool, base: str
) -> str:
    source = snapshot.source
    parts = [
        f'<article class="source-card"><span class="source-tag">{_e(NAMES[source])}</span><h3>{_e(_title(source, row))}</h3>'
    ]
    parts.append(_facts(row, detailed=detailed))
    if source != "nfz":
        parts.append(_link(base + "/" + _key(source, row), "Zestaw i obserwacje"))
    if source == "rsk":
        status = {
            "current": "Obecny w ostatnim zapisie RSK",
            "historical": "Historyczna obserwacja",
            "missing": "Brak w ostatnim sprawdzeniu; zachowany poprzedni zapis",
        }[cast(str, row["status"])]
        parts.append(
            f'<p class="card-notice">{_e(status)}. Ważność słownika nie ustala okresu rozliczeń.</p>'
        )
        if detailed:
            parts.append(_link(cast(str, row["origin_url"]), "Plik w oficjalnym RSK"))
            parts.extend(
                f'<p class="card-notice">{_e(notice)}</p>'
                for notice in cast(list[str], row["notices"])
            )
    elif source == "baw":
        url = cast(str | None, row["source_url"])
        if url and "/Zarzadzenie-" in url:
            parts.append(_link(url, "Dokument w oficjalnej bazie NFZ"))
        else:
            parts.append(
                '<p class="card-notice">Brak zweryfikowanego adresu dokumentu w tym zapisie.</p>'
            )
        if detailed:
            lineage = cast(dict[str, object] | None, row.get("lineage"))
            if lineage:
                for key, label in (
                    ("source_document_id", "Akt pierwotny"),
                    ("introductory_document_id", "Akt wprowadzający zmianę"),
                ):
                    if lineage[key] is not None:
                        parts.append(
                            "<p>"
                            + _link(base + "/" + str(lineage[key]), label)
                            + "</p>"
                        )
            parts.append(
                '<p class="card-notice">Pliki w prywatnym archiwum Minos. Dostępność oryginałów sprawdź w oficjalnej bazie.</p><ul class="file-list">'
            )
            for file in cast(list[dict[str, object]], row["files"]):
                parts.append(
                    f'<li><strong>{_e(file["name"])}</strong><span class="technical">{_e(file["size_bytes"])} B · SHA-256: {_e(file["sha256"])}</span></li>'
                )
            parts.append("</ul>")
        if row["service_applicability_date"] is None:
            parts.append(
                '<p class="card-notice">Okres stosowania do rozliczeń nie został tu ustalony.</p>'
            )
    else:
        parts.append(_link(cast(str, row["source_url"]), "Otwórz w oficjalnym źródle"))
        parts.append(
            '<p class="card-notice">Obserwacja publikacji, nie ustalona reguła rozliczeniowa. Projekty i sygnały AOTMiT nie potwierdzają obowiązywania.</p>'
        )
    parts.append("</article>")
    return "".join(parts)


def _notice(snapshot: PublicSnapshot, historical: bool) -> str:
    timestamp = str(snapshot.data.get("projected_at", snapshot.data.get("observed_at")))
    text = f"Zapis obserwacji Minos (UTC): {timestamp}. "
    if historical:
        text += "Cytowana obserwacja historyczna; nie jest zapewnieniem aktualności źródła. "
    else:
        age = (
            datetime.now(UTC) - datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        ).total_seconds()
        if age > 36 * 3600 or age < -3600:
            text += "Zapis wymaga odświeżenia; może być nieaktualny. "
        text += "To ostatni opublikowany zapis, nie informacja o powodzeniu późniejszych prób sprawdzenia. "
    if snapshot.source == "nfz":
        text += "Tylko ostatnie sprawdzenie; niepełna historia publikacji. "
    if snapshot.data.get("history_truncated"):
        text += "Zakres historii RSK w tej obserwacji jest ograniczony. "
    return (
        '<p class="source-summary">'
        + _e(text)
        + "</p>"
        + "".join(
            '<p class="card-notice">' + _e(notice) + "</p>"
            for notice in cast(list[str], snapshot.data.get("notices", []))
        )
    )


def _listing(
    snapshot: PublicSnapshot,
    *,
    base: str,
    dataset: str | None,
    page: int,
    preview: bool = False,
) -> str:
    rows = _rows(snapshot)
    if dataset is not None:
        rows = [row for row in rows if _key(snapshot.source, row) == dataset]
    elif snapshot.source != "nfz":
        groups: dict[str, dict[str, object]] = {}
        for row in rows:
            _ = groups.setdefault(_key(snapshot.source, row), row)
        rows = list(groups.values())
    if not rows and dataset is not None:
        raise LookupError("dataset absent")
    size = 6 if preview else PAGE_SIZE
    if page > max(1, (len(rows) + size - 1) // size):
        raise LookupError("page absent")
    parts = [
        f'<p>Liczba {"obserwacji plików" if dataset and snapshot.source == "rsk" else "dokumentów" if snapshot.source == "baw" else "publikacji" if snapshot.source == "nfz" else "zestawów"} w tym zapisie: {len(rows)}.</p><div class="cards">'
    ]
    parts.extend(
        _record(
            snapshot,
            row,
            detailed=dataset is not None or snapshot.source == "nfz",
            base=base,
        )
        for row in rows[(page - 1) * size : page * size]
    )
    parts.append("</div>")
    if dataset and snapshot.source == "baw":
        related = [
            row
            for row in _rows(snapshot)
            if row.get("lineage")
            and cast(dict[str, object], row["lineage"])["source_document_id"]
            == int(dataset)
        ]
        if related:
            parts.append("<h2>Dokumenty powiązane z aktem pierwotnym</h2><ul>")
            parts.extend(
                "<li>"
                + _link(base + "/" + _key("baw", row), _title("baw", row))
                + "</li>"
                for row in related
            )
            parts.append("</ul>")
    path = base + ("/" + dataset if dataset else "")
    if preview:
        parts.append(
            "<p>" + _link(path, "Przejrzyj wszystkie zestawy tego źródła") + "</p>"
        )
    else:
        links: list[str] = []
        if page > 1:
            links.append(
                _link(
                    path if page == 2 else path + f"/strona/{page - 1}",
                    "Poprzednia strona",
                )
            )
        if page * size < len(rows):
            links.append(_link(path + f"/strona/{page + 1}", "Następna strona"))
        parts.append(
            '<nav class="code-actions" aria-label="Strony katalogu">'
            + " · ".join(links)
            + "</nav>"
        )
    return "".join(parts)


def source_page(root: Path, path: str) -> tuple[int, str]:
    match = SOURCE_ROUTE.fullmatch(path)
    if match is None:
        raise LookupError("route absent")
    source_name, identity, dataset, page_text = match.groups()
    if page_text == "1":
        raise LookupError("noncanonical page")
    page = int(page_text or "1")
    if source_name is None:
        sections: list[str] = []
        for source in ("rsk", "baw", "nfz"):
            parts = [
                f'<section data-fallback-source="{source}"><h2>{_e(NAMES[source])}</h2>'
                + '<p class="card-notice" data-fallback-note hidden>Zapis początkowy tego źródła pokazujemy bez filtra. Filtr obejmuje źródła odświeżone w przeglądarce.</p>'
            ]
            try:
                snapshot = load_snapshot(root, source)
                parts.extend(
                    (
                        _notice(snapshot, False),
                        _listing(
                            snapshot,
                            base="/zrodla/" + source,
                            dataset=None,
                            page=1,
                            preview=True,
                        ),
                    )
                )
            except SourceUnavailable:
                parts.append(
                    '<p class="load-state is-error">Dane tego źródła są niedostępne. Pozostałe źródła są pokazane niezależnie.</p>'
                )
            parts.append("</section>")
            sections.append("".join(parts))
        content = _shell("source_catalog.html").replace(
            "{{initial_catalog}}", "".join(sections)
        )
        content = content.replace(
            "Wczytywanie słowników i zarządzeń...",
            "Zapis źródeł poniżej. JavaScript dodaje lokalny filtr i odświeżanie.",
        )
        title = "Źródła danych medycznych"
        head = '<script defer src="/sources.js"></script>'
    else:
        source = cast(Source, source_name)
        if source == "nfz" and (identity or dataset):
            raise LookupError("NFZ citation route absent")
        if dataset and not re.fullmatch(
            r"[0-9A-Z]{6}" if source == "rsk" else r"[1-9][0-9]{0,15}", dataset
        ):
            raise LookupError("dataset identity invalid")
        snapshot = load_snapshot(root, source, identity)
        base = "/zrodla/" + source + ("/obserwacje/" + identity if identity else "")
        title = NAMES[source]
        if dataset:
            selected = [row for row in _rows(snapshot) if _key(source, row) == dataset]
            if not selected:
                raise LookupError("dataset absent")
            title = _title(source, selected[0])
        if identity:
            title += " - obserwacja " + identity.split("-", 1)[0]
        content = (
            '<div class="shell page-head"><nav class="breadcrumbs" aria-label="Okruszki">'
            + _link("/zrodla", "Źródła danych")
            + " / "
            + _link("/zrodla/" + source, NAMES[source])
            + f"</nav><h1>{_e(title)}</h1>"
        )
        content += (
            "<p>Dystrybutor: "
            + _e(
                {
                    "rsk": "RSK / Centrum e-Zdrowia",
                    "baw": "BAW / Narodowy Fundusz Zdrowia",
                    "nfz": "NFZ, CZS i AOTMiT",
                }[source]
            )
            + ". Wydawca zasobu jest wskazany osobno, gdy źródło go podaje.</p>"
        )
        content += _notice(snapshot, identity is not None)
        if snapshot.retained:
            citation = f"/zrodla/{source}/obserwacje/{snapshot.identity}" + (
                "/" + dataset if dataset else ""
            )
            content += (
                "<p>"
                + _link(citation, "Stały adres tej obserwacji do cytowania")
                + f'</p><p class="technical">Identyfikator obserwacji: {_e(snapshot.identity)}</p>'
            )
        elif source != "nfz":
            content += '<p class="card-notice">Trwały zapis tej obserwacji jest chwilowo niedostępny.</p>'
        content += (
            '</div><section class="shell catalog">'
            + _listing(snapshot, base=base, dataset=dataset, page=page)
            + "</section>"
        )
        content += (
            '<section class="shell how-to"><h2>Źródła i zakres</h2><p>Daty publikacji, ważności źródła, obserwacji Minos i stosowania do świadczeń opisują różne zdarzenia. Obserwacja nie zmienia wersji danych przypiętej w gruperze. Kopie plików pozostają prywatne. Żaden wydawca nie zatwierdził Minos.</p>'
            + _link("/regulamin", "Warunki korzystania")
            + "</section>"
        )
        head = _json_ld(
            {
                "@context": "https://schema.org",
                "@type": "CollectionPage",
                "name": title,
                "url": "https://minoshealth.pl" + path,
                "inLanguage": "pl",
            }
        )
    html = _page(
        title=title + " - Minos Health",
        description="Źródła, wersje i dokładne obserwacje publicznych metadanych. Oryginalne pliki w oficjalnych serwisach.",
        path=path,
        content=content,
        head=head,
        robots="index, follow",
        template="page_shell_sources.html",
    )
    if len(html.encode()) > MAX_HTML_BYTES:
        raise SourceUnavailable("source page exceeds its output bound")
    return 200, html


def register_source_pages(app: FastAPI, public_directory: Path) -> None:
    slots = threading.BoundedSemaphore(2)

    def error(status: int, title: str) -> HTMLResponse:
        return HTMLResponse(
            _page(
                title=title + " - Minos Health",
                description=title,
                path="/zrodla",
                content='<div class="shell page-head"><h1>'
                + _e(title)
                + "</h1>"
                + _link("/zrodla", "Pozostałe źródła i ponowny odczyt")
                + "</div>",
                head="",
                robots="noindex, follow",
                template="page_shell_sources.html",
            ),
            status_code=status,
            headers={
                "Cache-Control": "no-store",
                **({"Retry-After": "1"} if status == 503 else {}),
            },
        )

    def page(request: Request) -> HTMLResponse:
        if not slots.acquire(blocking=False):
            return error(503, "Katalog jest zajęty. Spróbuj ponownie.")
        try:
            try:
                status, html = source_page(public_directory, request.url.path)
            except LookupError:
                return error(404, "Nie znaleziono zestawu lub obserwacji")
            except SourceUnavailable:
                return error(503, "Dane tego źródła są chwilowo niedostępne")
            return HTMLResponse(
                html,
                status_code=status,
                headers={"Cache-Control": "no-cache" if status == 200 else "no-store"},
            )
        finally:
            slots.release()

    app.add_api_route("/zrodla", page, methods=["GET", "HEAD"])
    app.add_api_route("/zrodla/{source_path:path}", page, methods=["GET", "HEAD"])
