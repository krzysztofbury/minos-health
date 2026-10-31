"""Source-backed reference display with a whole-group interpretation gate."""

from typing import Final, NamedTuple, cast

from minos.domain import jgp_conditions as c
from minos.domain.reference import ArtifactError
from minos.http.pages import (
    CHEVRON_MARK,
    JgpPanel,
    _e,  # pyright: ignore[reportPrivateUsage]
    display_date,
    fold,
)
from minos.ingest.jgp_conditions_codec import decode_condition

type Json = dict[str, object]
LABELS = {
    "procedure_presence": "Wskazanie procedury",
    "procedure_count": "Liczba procedur",
    "procedure_all_members": "Wszystkie procedury ze zbioru",
    "procedure_list_coverage": "Procedury z różnych list",
    "procedure_date_relation": "Procedury w różnych terminach",
    "procedure_laterality": "Procedury dla obu stron",
    "procedure_duration": "Czas trwania procedury",
    "diagnosis_presence": "Wskazanie rozpoznania",
    "diagnosis_count": "Liczba rozpoznań",
    "patient_age": "Wiek pacjenta przy przyjęciu",
    "episode_duration": "Czas pobytu",
    "ward_duration": "Czas pobytu w OINK",
    "discharge_mode": "Tryb wypisu",
    "clinical_measure": "Parametr kliniczny",
    "program_membership": "Program",
    "external_disease_code": "Kod ORPHA",
    "all": "Wszystkie poniższe elementy",
    "any": "Alternatywy w źródle",
    "not": "Wykluczenie",
    "when": "Warunek dodatkowy",
}
COMPARISONS = {"gt": ">", "ge": "≥", "lt": "<", "le": "≤"}
UNITS = {
    "completed_years": "r.ż.",
    "elapsed_weeks": "tyg.ż.",
    "calendar_months": "miesięcy życia",
}
MAX_SELECTOR_CHARACTERS = 65_536
MAX_SECTION_CHARACTERS = 1_048_576


def code_link(system: str, code: str, day: str, matched: bool) -> str:
    label = f'<span class="code-chip">{_e(code)}</span>'
    if not matched:
        return (
            label
            + ' <span class="badge badge-muted">brak odpowiednika w wybranym słowniku</span>'
        )
    slug = "icd-9" if system == "icd9-pl" else "icd-10"
    return f'<a href="/slowniki/{slug}/{_e(code)}/jgp/{_e(day)}">{label}</a>'


def source_facts(source: Json) -> str:
    return (
        f'<p class="detail-source">Źródło: załącznik 9 do zarządzenia {_e(source["act_number"])}, '
        + f"{_e(source['worksheet'])}. "
        + f'<a href="{_e(source["source_url"])}">Oficjalny skoroszyt NFZ</a></p>'
        + "<details><summary>Pochodzenie i warunki źródła</summary>"
        + f"<p>Wydanie: {_e(source['release_id'])}. SHA-256 skoroszytu: {_e(source['sha256'])}.</p>"
        + f"<p>{_e(source['publisher'])}. {_e(source['license'])}</p>"
        + "<ul>"
        + "".join(
            f"<li>{_e(notice)}</li>" for notice in cast(list[str], source["notices"])
        )
        + "</ul></details>"
    )


def _selector_links(answer: Json) -> dict[int, str]:
    source = cast(Json, answer["source"])
    day, catalog = cast(str, source["service_date"]), cast(str, source["catalog"])
    codes = {
        (item["system"], item["code"]): item["correspondence"] == "matched"
        for item in cast(list[Json], answer["codes"])
    }
    lists = {item["id"]: item for item in cast(list[Json], answer["lists"])}
    result: dict[int, str] = {}
    for item in cast(list[Json], answer["selectors"]):
        body = cast(Json, item["body"])
        identity = cast(int, item["identity"])
        if item["kind"] == "list":
            result[identity] = ", ".join(
                f'<a href="/jgp/{_e(day)}/listy/{_e(catalog)}/{i}/1">'
                + f"{_e(lists[i]['label'])} ({source_positions(cast(int, lists[i]['occurrences']))})</a>"
                for i in cast(list[int], body["list_ids"])
            )
        elif item["kind"] == "codes":
            result[identity] = ", ".join(
                code_link(
                    cast(str, body["system"]),
                    code,
                    day,
                    codes.get((body["system"], code), False),
                )
                for code in cast(list[str], body["codes"])
            )
    # Unions cite their stored selector IDs and stay one count, not two counts.
    for _ in range(32):
        before = len(result)
        for item in cast(list[Json], answer["selectors"]):
            if item["kind"] != "union" or item["identity"] in result:
                continue
            children = cast(list[int], cast(Json, item["body"])["selector_ids"])
            if all(child in result for child in children):
                if (
                    sum(len(result[i]) for i in children) + 3 * len(children)
                    > MAX_SELECTOR_CHARACTERS
                ):
                    raise ArtifactError("condition selector display exceeds bound")
                result[cast(int, item["identity"])] = " ∪ ".join(
                    result[i] for i in children
                )
        if len(result) == before:
            break
    return result


def _payload(node: c.Condition, selectors: dict[int, str]) -> str:
    payload = node.payload
    if isinstance(
        payload, (c.Presence, c.Count, c.AllMembers, c.Laterality, c.ProcedureDuration)
    ):
        text = selectors[payload.selector_id]
        if isinstance(payload, (c.Presence, c.Count)) and payload.role:
            text += (
                " - "
                + {
                    "principal": "rozpoznanie zasadnicze",
                    "secondary": "rozpoznanie współistniejące",
                    "source_unspecified": "rola nieokreślona",
                }[payload.role]
            )
        if isinstance(payload, c.Count):
            text += f"; {'co najmniej' if payload.comparison == 'minimum' else 'liczba podana w źródle:'} {payload.required_count}"
            text += (
                "; "
                + {
                    "distinct_codes": "różne kody",
                    "repeat_occurrences": "powtarzane wystąpienia",
                    "review_required": "sposób zliczania wymaga weryfikacji",
                }[payload.counting_policy]
            )
        elif isinstance(payload, c.AllMembers):
            text += (
                f"; co najmniej {payload.minimum_per_member} wystąpienie każdej pozycji"
            )
        elif isinstance(payload, c.ProcedureDuration):
            text += f"; ≥ {payload.threshold_hours} h, co najmniej {payload.qualifying_occurrences} raz"
        elif isinstance(payload, c.Laterality):
            text += "; jednokrotnie dla piersi prawej i lewej"
        return text
    if isinstance(payload, c.Age):
        return _e(
            f"{COMPARISONS[payload.comparison]} {payload.threshold} {UNITS[payload.unit]}"
        )
    if isinstance(payload, c.EpisodeDuration):
        return _e(
            " i ".join(
                f"{COMPARISONS[b.comparison]} {b.days} dni" for b in payload.bounds
            )
        )
    if isinstance(payload, c.WardDuration):
        return _e(f"{COMPARISONS[payload.comparison]} {payload.days} dni")
    if isinstance(payload, c.DischargeMode):
        return _e(", ".join(str(code) for code in payload.allowed_reported_codes))
    if isinstance(payload, c.ClinicalMeasure):
        return _e(
            f"{payload.measure} {COMPARISONS[payload.comparison]} {payload.exact_threshold} "
            + (
                "skala źródłowa"
                if payload.unit_or_scale == "source_ordinal"
                else payload.unit_or_scale or "jednostka niewskazana"
            )
        )
    if isinstance(payload, c.ProgramMembership):
        return _e(payload.program)
    if isinstance(payload, c.ExternalDiseaseCode):
        return _e(payload.exact_code)
    if isinstance(payload, c.ListCoverage):
        return (
            ", ".join(selectors[i] for i in payload.selector_ids)
            + f"; co najmniej {payload.minimum_distinct_lists} różnych list i {payload.minimum_procedures} procedur"
        )
    if isinstance(payload, c.DateRelation):
        return "W różnych terminach: " + "; ".join(
            ", ".join(selectors[i] for i in side) for side in payload.occurrence_sets
        )
    if isinstance(payload, c.Expression):
        if node.kind == "when":
            return (
                "Jeżeli zachodzi pierwszy zapis, wymagany jest drugi; tylko dla oznaczonych pozycji, wiersze "
                + _e(", ".join(map(str, payload.member_rows)))
            )
        return ""
    raise ArtifactError("unsupported condition reached structured display")


def source_positions(count: int) -> str:
    """Polish agreement: 1 pozycja, 2-4 pozycje, 5+ pozycji (12-14 pozycji)."""
    if count == 1:
        return "1 pozycja źródłowa"
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return f"{count} pozycje źródłowe"
    return f"{count} pozycji źródłowych"


def _rule_tree(
    roots: list[int],
    nodes: dict[int, c.Condition],
    selectors: dict[int, str],
    rendered: int,
) -> str:
    """Nested rules from each source root, without internal node numbers.

    An explicit stack instead of recursion; depth is already bounded by
    validation and output by MAX_SECTION_CHARACTERS.
    """
    out: list[str] = ['<ul class="jgp-rules">']
    stack: list[int | str] = ["</ul>", *reversed(roots)]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
            continue
        node = nodes.get(item)
        if node is None or node.kind is None:
            raise ArtifactError("structured rule tree references a missing node")
        children = (
            node.payload.children
            if isinstance(node.payload, c.Expression)
            else node.payload.included_node_ids
            if isinstance(node.payload, c.Count)
            else ()
        )
        details = "" if node.kind in ("all", "any") else _payload(node, selectors)
        rows = ", ".join(map(str, node.source_rows))
        out.append(
            f"<li><strong>{_e(LABELS[node.kind])}</strong>"
            + (f": {details}" if details else "")
            + f' <span class="jgp-rows">(wiersz {_e(rows)})</span>'
        )
        if children:
            out.append("<ul>")
            stack.append("</ul></li>")
            stack.extend(reversed(children))
        else:
            out.append("</li>")
        rendered += len(out[-1]) + len(out[-2])
        if rendered > MAX_SECTION_CHARACTERS:
            raise ArtifactError("condition section display exceeds bound")
    return "".join(out)


def conditions_section(answer: Json) -> str:
    status = answer["status"]
    if status == "not_in_bundle":
        # Nothing to say to a visitor when the deployed data has no
        # characteristics; the API still reports the state explicitly.
        return ""
    if status not in ("fully_interpreted", "not_yet_interpreted"):
        labels = {
            "context_not_covered": "Brak zweryfikowanej charakterystyki dla tego dnia i katalogu.",
            "group_not_in_characteristics": "Grupy nie ma w wybranym wydaniu charakterystyki.",
        }
        return (
            '<section class="jgp-conditions"><h2>Warunki grupy</h2><p>'
            + _e(labels[cast(str, status)])
            + "</p></section>"
        )
    source = cast(Json, answer["source"])
    source_text = cast(list[Json], answer["source_text"])
    parts = ['<section class="jgp-conditions"><h2>Warunki grupy</h2>']
    # Rows cited from another group's section stay in the source record below.
    for item in source_text:
        if item["own"] and item["category"] == "label":
            parts.append(f'<p class="cond-clause">{_e(item["text"])}</p>')
        elif item["own"] and item["category"] == "connector":
            parts.append('<p class="cond-or">lub</p>')
    parts.append(_list_links(answer))
    parts.append(
        '<p class="answer-caveat">Charakterystyka referencyjna, nie wynik grupowania. Obecność kodu na liście nie oznacza kwalifikacji przypadku ani możliwości rozliczenia.</p>'
    )
    if status == "fully_interpreted":
        import json

        selectors = _selector_links(answer)
        nodes: dict[int, c.Condition] = {}
        for raw in cast(list[Json], answer["nodes"]):
            node = decode_condition(
                json.dumps(
                    raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
            if node.status != "proposed" or node.kind is None:
                raise ArtifactError("whole-group interpretation gate is inconsistent")
            nodes[node.identity] = node
        structure = [
            "<p>Wszystkie zapisy grupy mają strukturę. Zasady zliczania, granic i wykonania nadal wymagają osobnej weryfikacji przed uruchomieniem grupera.</p>"
        ]
        roots = cast(list[int], answer["roots"])
        if len(roots) > 1:
            structure.append(
                "<p>Grupa ma kilka odrębnych zapisów źródłowych; nie łączymy ich domyślnie w jeden warunek.</p>"
            )
        structure.append(
            _rule_tree(roots, nodes, selectors, sum(map(len, parts + structure)))
        )
        parts.append(fold("Struktura warunku", "".join(structure), level=3))
    else:
        parts.append(
            '<p class="badge badge-warn">Jeszcze nie zinterpretowano całej grupy. Pokazujemy zapis źródłowy, bez częściowych reguł strukturalnych.</p>'
        )
    rows: list[str] = []
    rendered_characters = sum(map(len, parts))
    for item in source_text:
        text = f'<li><span class="jgp-rows">Wiersz {_e(item["row"])}</span>: {_e(item["text"])}</li>'
        rendered_characters += len(text)
        if rendered_characters > MAX_SECTION_CHARACTERS:
            raise ArtifactError("condition source display exceeds bound")
        rows.append(text)
    parts.append(fold("Zapis źródłowy", f"<ul>{''.join(rows)}</ul>", level=3))
    parts.append(fold("Źródło", source_facts(source), level=3))
    parts.append("</section>")
    return "".join(parts)


def _list_links(answer: Json) -> str:
    """The lists a condition names: the way from a group back to its codes."""
    source = cast(Json, answer["source"])
    day, catalog = cast(str, source["service_date"]), cast(str, source["catalog"])
    names = {"icd9-pl": "procedury", "icd10-pl": "rozpoznania"}
    links = [
        f'<li><a href="/jgp/{_e(day)}/listy/{_e(catalog)}/{_e(item["id"])}/1">'
        + f"{_e(names.get(cast(str, item['system']), item['system']))} {_e(item['label'])}</a>"
        + f' <span class="jgp-rows">{cast(int, item["occurrences"])} poz.</span></li>'
        for item in cast(list[Json], answer["lists"])
    ]
    return f'<ul class="jgp-lists">{"".join(links)}</ul>' if links else ""


def shared_procedure_section(answers: list[Json]) -> str:
    rows: list[str] = []
    for answer in answers:
        day = cast(str, answer["service_date"])
        for procedure in cast(list[Json], answer["procedures"]):
            code = cast(str, procedure["code"])
            groups = cast(list[str], procedure["shared_groups"])
            links = ", ".join(
                f'<a href="/jgp/{_e(day)}/{_e(group)}">{_e(group)}</a>'
                for group in groups
            )
            if cast(int, procedure["shared_group_count"]) > len(groups):
                links += f" (pierwsze {len(groups)} z {procedure['shared_group_count']} grup)"
            rows.append(
                f'<li><a href="/slowniki/icd-9/{_e(code)}/jgp/{_e(day)}">{_e(code)} {_e(procedure["label"])}</a> - wspólne grupy ({_e(answer["catalog"])}): {links}</li>'
            )
        if answer["truncated"]:
            rows.append(
                "<li>Pokazano pierwsze 20 procedur; lista jest ograniczona.</li>"
            )
    if not rows:
        return ""
    return (
        "<p>To wyłącznie wspólna obecność na listach załącznika 9, nie typowe leczenie ani kwalifikacja hospitalizacji.</p><ul>"
        + "".join(rows)
        + "</ul>"
    )


# All open at this size: procedures usually sit in a handful of groups, and the
# condition belongs beside the points. Longer lists open group by group.
OPEN_LIMIT: Final = 6
# A value range is an answer for a few groups and noise for a hundred.
RANGE_LIMIT: Final = 8
CAVEAT: Final = (
    "Punkty bazowe z katalogu, bez modyfikatorów. Obecność na jawnej liście to nie"
    + " kwalifikacja przypadku: liczy się warunek przy grupie. Brak pozycji nie"
    + " dowodzi wykluczenia."
)
HOSPITALIZATION: Final = "hospitalization"
# The source columns' full headers stay on the group page.
MEASURES: Final = {
    HOSPITALIZATION: "hospitalizacja",
    "planned_hospitalization": "planowa",
    "one_day_treatment": "leczenie 1 dnia",
    "same_day_discharge": "przyjęcie = wypis",
    "one_day_stay": "1-dniowa",
    "two_day_stay": "2-dniowa",
    "day_stay_under_12_hours": "pobyt dzienny do 12 h",
    "extra_stay_day": "osobodzień ponad ryczałt",
    "extra_stay_day_excluding_day_stay": "osobodzień ponad ryczałt poza pobytem dziennym",
}
NBSP: Final = "\u00a0"


def points_short(hundredths: int) -> str:
    """Polish points without an empty fraction: 317700 -> "3 177"."""
    whole, fraction = divmod(hundredths, 100)
    grouped = f"{whole:,}".replace(",", NBSP)
    return grouped if fraction == 0 else f"{grouped},{fraction:02d}"


def _values(group: Json) -> dict[str, Json]:
    return {
        cast(str, value["measure"]): value
        for value in cast(list[Json], group["base_values"])
    }


def _headline(group: Json) -> int | None:
    value = _values(group).get(HOSPITALIZATION)
    return cast(int, value["amount"]) if value else None


def _condition(group: Json) -> str:
    clauses = cast(list[Json], group["conditions"])
    if not clauses:
        return ""
    return (
        '<div class="cond"><span class="cond-tag">Warunek NFZ</span>'
        + "".join(
            ('<p class="cond-or">lub</p>' if clause["connector"] else "")
            + f"<p>{_e(clause['text'])}</p>"
            for clause in clauses
        )
        + "</div>"
    )


def _days(days: object) -> str:
    return f"{_e(days)}{NBSP}{'dzień' if days == 1 else 'dni'}"


def _group_card(group: Json, catalog: str, day: str, opened: bool) -> str:
    code = cast(str, group["code"])
    priced = group["name"] is not None
    name = cast(str | None, group["name"]) or cast(str, group["source_title"])
    headline = _headline(group)
    points = (
        f'<span class="pts"><b>{points_short(headline)}</b> pkt</span>'
        if headline is not None
        else f'<span class="jgp-absent">{"brak wartości" if priced else "brak wiersza katalogu"}</span>'
    )
    badge = (
        f' <span class="badge badge-muted">Katalog {_e(catalog)}</span>'
        if catalog != "1a"
        else ""
    )
    pending = (
        ""
        if group["fully_interpreted"]
        else '<span class="answer-flag">jeszcze nie zinterpretowano</span>'
    )
    values = _values(group)
    tiles = "".join(
        f"<div><dt>{_e(label)}</dt><dd>{points_short(cast(int, values[measure]['amount']))}</dd></div>"
        for measure, label in MEASURES.items()
        if measure in values
    )
    days = next(
        (
            values[measure]["amount"]
            for measure in ("financed_stay", "financed_stay_excluding_day_stay")
            if measure in values
        ),
        None,
    )
    if days is not None:
        tiles += f"<div><dt>ryczałt</dt><dd>{_days(days)}</dd></div>"
    notes = (
        f'<div class="cond"><span class="cond-tag">Uwagi w katalogu</span><p>{_e(group["catalog_notes"])}</p></div>'
        if group.get("catalog_notes")
        else ""
    )
    return (
        f'<li class="answer-card"><details class="answer-group"{" open" if opened else ""}>'
        + f'<summary class="answer-row"><span class="code-chip">{_e(code)}</span>'
        + f'<span class="answer-name">{_e(name.removeprefix(code + " ").rstrip(" *"))}{badge}{pending}</span>'
        + f"{points}{CHEVRON_MARK}</summary>"
        + '<div class="answer-body">'
        + (f'<dl class="answer-tiles">{tiles}</dl>' if tiles else "")
        + _condition(group)
        + notes
        + f'<p class="answer-open"><a href="/jgp/{_e(day)}/{_e(code)}">Otwórz grupę {_e(code)}</a></p>'
        + "</div></details></li>"
    )


class _Listed(NamedTuple):
    group: Json
    catalog: str


def _flow(items: list[_Listed], day: str, opened: bool) -> str:
    return (
        '<ul class="answer-flow">'
        + "".join(_group_card(item.group, item.catalog, day, opened) for item in items)
        + "</ul>"
    )


def _groups(items: list[_Listed], code: str, day: str, total: int) -> str:
    root = f'<span class="code-chip answer-root">{_e(code)}</span>'
    opened = total <= OPEN_LIMIT
    if opened:
        return root + _flow(items, day, True)
    parts = [
        root,
        '<p class="jgp-note">Warunek NFZ i pozostałe wartości po rozwinięciu grupy.</p>',
    ]
    paged = len(items) < total
    for title, wanted in (
        ("Grupy zabiegowe", True),
        ("Grupy zachowawcze", False),
        ("Bez wiersza katalogu na ten dzień", None),
    ):
        chosen = [item for item in items if item.group["procedural"] is wanted]
        if chosen:
            count = f"{len(chosen)} na tej stronie" if paged else len(chosen)
            parts.append(
                f'<details class="answer-section" open><summary><h3 class="answer-section-title">{title}'
                + f" <span>{count}</span></h3>{CHEVRON_MARK}</summary>"
                + _flow(chosen, day, False)
                + "</details>"
            )
    return "".join(parts)


def _lead(slug: str, items: list[_Listed], total: int, day: str, covered: bool) -> str:
    if not covered:
        return '<p class="answer-sentence">Brak zweryfikowanej charakterystyki JGP dla tego dnia.</p>'
    if not total:
        return f'<p class="answer-sentence">Brak na jawnych listach grup JGP na {_e(display_date(day))}. Nie jest to dowód wykluczenia.</p>'
    noun = "Ta procedura" if slug == "icd-9" else "To rozpoznanie"
    points = [
        headline
        for headline in (_headline(item.group) for item in items)
        if headline is not None
    ]
    worth = ""
    if points and total <= RANGE_LIMIT:
        low, high = min(points), max(points)
        span = (
            points_short(low)
            if low == high
            else f"od {points_short(low)} do {points_short(high)}"
        )
        part = f" w {len(points)} z nich" if len(points) < total else ""
        # The hospitalization tariff, not the range: other stays can pay less.
        worth = f":{part} hospitalizacja <strong>{span}{NBSP}pkt</strong> bazowych"
    place = "grupie" if total == 1 else "grupach"
    return (
        f'<p class="answer-sentence">{noun} występuje w <strong>{total} {place} JGP</strong>{worth}.</p>'
        + f'<p class="answer-caveat">{CAVEAT}</p>'
    )


def _sources(answers: list[Json]) -> str:
    covered = [
        cast(Json, answer["source"])
        for answer in answers
        if answer["status"] == "covered"
    ]
    if not covered:
        return ""
    return "".join(
        f'<details class="fold"><summary>Źródła: katalog {_e(source["catalog"])}</summary>'
        + f'<div class="fold-body">{source_facts(source)}</div></details>'
        for source in covered
    )


def membership_panel(
    answers: list[Json], *, slug: str, code: str, day: str, date_bar: str, shared: str
) -> JgpPanel:
    """The page's answer: which groups list this code, and what each is worth."""
    items = [
        _Listed(group, cast(str, cast(Json, answer["source"])["catalog"]))
        for answer in answers
        if answer["status"] == "covered"
        for group in cast(list[Json], answer["groups"])
    ]
    covered = [
        cast(Json, answer["source"])
        for answer in answers
        if answer["status"] == "covered"
    ]
    total = sum(
        cast(int, answer["total"])
        for answer in answers
        if answer["status"] == "covered"
    )
    notes: list[str] = [
        f'<p class="jgp-note">Katalog {_e(answer["catalog"])}: brak zweryfikowanej charakterystyki dla tego dnia.</p>'
        for answer in answers
        if answer["status"] != "covered"
    ]
    if any(source["service_date_after_knowledge"] for source in covered):
        notes.append(
            f'<p class="jgp-note">Według źródeł sprawdzonych do {_e(display_date(covered[0]["knowledge_as_of"]))}.</p>'
        )
    if slug == "icd-10":
        # Procedure-led groups (for example hernia repair) often have no
        # diagnosis list, so a diagnosis alone never shows them here.
        notes.append(
            '<p class="jgp-note">Grupy zabiegowe kierowane procedurą często nie wymieniają rozpoznania na liście. Sprawdź także stronę wykonanej procedury ICD-9.</p>'
        )
    pages: list[str] = []
    for answer in answers:
        if answer["status"] != "covered":
            continue
        page = cast(int, answer["page"])
        catalog = cast(str, cast(Json, answer["source"])["catalog"])
        for label, target in (
            ("Poprzednie grupy", page - 1),
            ("Następne grupy", page + 1),
        ):
            if target >= 1 and (target - 1) * 100 < cast(int, answer["total"]):
                pages.append(
                    f'<a class="button button-quiet" href="/slowniki/{_e(slug)}/{_e(code)}/jgp/{_e(day)}/{_e(catalog)}/{target}">{label} ({_e(catalog)})</a>'
                )
    toggle = (
        '<button class="answer-toggle" type="button" aria-expanded="false" hidden>Rozwiń wszystkie</button>'
        if len(items) > 1
        else ""
    )
    return JgpPanel(
        date_bar=date_bar,
        lead=_lead(slug, items, total, day, bool(covered))
        + "".join(notes)
        # Phones show the date picker below the groups, so the day stays in view.
        + f'<a class="answer-day" href="#jgp-date">Stan na {_e(display_date(day))}, zmień dzień</a>',
        groups=(
            '<section class="jgp-memberships">'
            + (
                '<h2 class="visually-hidden">Grupy JGP</h2>'
                + toggle
                + _groups(items, code, day, total)
                if items
                else ""
            )
            + (
                f'<nav class="code-actions" aria-label="Strony grup">{"".join(pages)}</nav>'
                if pages
                else ""
            )
            + "</section>"
        ),
        sources=_sources(answers),
        shared=shared,
        has_groups=bool(items),
    )
