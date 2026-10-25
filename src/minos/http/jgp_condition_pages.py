"""Source-backed reference display with a whole-group interpretation gate."""

from typing import cast

from minos.domain import jgp_conditions as c
from minos.domain.reference import ArtifactError
from minos.http.pages import _e, display_date  # pyright: ignore[reportPrivateUsage]
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
            '<section class="detail-notes"><h2>Warunki grupy</h2><p>'
            + _e(labels[cast(str, status)])
            + "</p></section>"
        )
    source = cast(Json, answer["source"])
    parts = [
        '<section class="detail-notes jgp-conditions"><h2>Warunki grupy</h2>',
        "<p>Charakterystyka referencyjna, nie wynik grupowania. Obecność kodu na liście nie oznacza kwalifikacji przypadku ani możliwości rozliczenia.</p>",
    ]
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
        parts.append(
            "<p>Wszystkie zapisy grupy mają strukturę. Zasady zliczania, granic i wykonania nadal wymagają osobnej weryfikacji przed uruchomieniem grupera.</p>"
        )
        roots = cast(list[int], answer["roots"])
        if len(roots) > 1:
            parts.append(
                "<p>Grupa ma kilka odrębnych zapisów źródłowych; nie łączymy ich domyślnie w jeden warunek.</p>"
            )
        parts.append(_rule_tree(roots, nodes, selectors, sum(map(len, parts))))
    else:
        parts.append(
            '<p class="badge badge-warn">Jeszcze nie zinterpretowano całej grupy. Pokazujemy zapis źródłowy, bez częściowych reguł strukturalnych.</p>'
        )
    parts.append("<details open><summary>Zapis źródłowy</summary><ul>")
    rendered_characters = sum(map(len, parts))
    for item in cast(list[Json], answer["source_text"]):
        text = f'<li><span class="jgp-rows">Wiersz {_e(item["row"])}</span>: {_e(item["text"])}</li>'
        rendered_characters += len(text)
        if rendered_characters > MAX_SECTION_CHARACTERS:
            raise ArtifactError("condition source display exceeds bound")
        parts.append(text)
    parts.append("</ul></details>")
    parts.append(source_facts(source) + "</section>")
    return "".join(parts)


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
        '<section class="code-children shared-procedures"><h2>Procedury współdzielące jawne grupy JGP</h2><p>To wyłącznie wspólna obecność na listach załącznika 9, nie typowe leczenie ani kwalifikacja hospitalizacji.</p><ul>'
        + "".join(rows)
        + "</ul></section>"
    )


def membership_section(answers: list[Json], *, slug: str, code: str, day: str) -> str:
    parts = [
        '<section class="code-children jgp-memberships"><h2>Występuje w grupach</h2>',
        f"<p>Dzień świadczenia: {_e(display_date(day))}. Dowód obecności w jawnych listach załącznika 9, nie kwalifikacja przypadku. Brak pozycji nie dowodzi wykluczenia.</p>",
    ]
    if slug == "icd-10":
        # Procedure-led groups (for example hernia repair) often have no
        # diagnosis list, so a diagnosis alone never shows them here.
        parts.append(
            '<p class="jgp-note">Grupy zabiegowe kierowane procedurą często nie wymieniają rozpoznania na liście. Sprawdź także stronę wykonanej procedury ICD-9.</p>'
        )
    for answer in answers:
        if answer["status"] != "covered":
            parts.append(
                f"<p>Katalog {_e(answer['catalog'])}: brak zweryfikowanej charakterystyki dla tego dnia.</p>"
            )
            continue
        source = cast(Json, answer["source"])
        parts.append(f'<h3>Katalog {_e(source["catalog"])}</h3><ul class="code-list">')
        for group in cast(list[Json], answer["groups"]):
            parts.append(
                f'<li><a href="/jgp/{_e(day)}/{_e(group["code"])}">{_e(group["source_title"])}</a>'
                + (
                    ""
                    if group["fully_interpreted"]
                    else ' <span class="badge badge-muted">jeszcze nie zinterpretowano</span>'
                )
                + "</li>"
            )
        parts.append(
            "</ul>"
            if answer["total"]
            else "</ul><p>Brak obecności w jawnych listach tego wydania. Nie jest to dowód wykluczenia.</p>"
        )
        page = cast(int, answer["page"])
        for label, target in (
            ("Poprzednie grupy", page - 1),
            ("Następne grupy", page + 1),
        ):
            if target >= 1 and (target - 1) * 100 < cast(int, answer["total"]):
                parts.append(
                    f'<a class="button" href="/slowniki/{_e(slug)}/{_e(code)}/jgp/{_e(day)}/{_e(source["catalog"])}/{target}">{label}</a>'
                )
        parts.append(source_facts(source))
    parts.append("</section>")
    return "".join(parts)
