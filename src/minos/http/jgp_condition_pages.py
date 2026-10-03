"""Source-backed reference display with a whole-group interpretation gate."""

from typing import cast

from minos.domain import jgp_conditions as c
from minos.domain.reference import ArtifactError
from minos.http.pages import _e  # pyright: ignore[reportPrivateUsage]
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
        (
            '<p class="badge badge-warn">Dzień świadczenia wykracza poza zweryfikowany stan wiedzy pakietu.</p>'
            if source["service_date_after_knowledge"]
            else ""
        )
        + f'<p class="detail-source">Załącznik 9, {_e(source["act_number"])}, '
        + f"{_e(source['worksheet'])}. Stan wiedzy: {_e(source['knowledge_as_of'])}. "
        + f"Wydanie: {_e(source['release_id'])}. SHA-256: {_e(source['sha256'])}.</p>"
        + f'<p><a href="{_e(source["source_url"])}">Oficjalny skoroszyt NFZ</a></p>'
        + f"<details><summary>Pochodzenie i warunki źródła</summary><p>{_e(source['publisher'])}. {_e(source['license'])}</p>"
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
                + f"{_e(lists[i]['label'])} ({lists[i]['occurrences']} pozycji źródłowych)</a>"
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


def conditions_section(answer: Json) -> str:
    status = answer["status"]
    if status not in ("fully_interpreted", "not_yet_interpreted"):
        labels = {
            "not_in_bundle": "Ten pakiet nie zawiera charakterystyki JGP.",
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
        parts.append(
            '<p>Wszystkie zapisy grupy mają strukturę. Zasady zliczania, granic i wykonania nadal wymagają osobnej weryfikacji przed uruchomieniem grupera.</p><ol class="jgp-rules">'
        )
        rendered_characters = sum(map(len, parts))
        for raw in cast(list[Json], answer["nodes"]):
            node = decode_condition(
                json.dumps(
                    raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
            )
            if node.status != "proposed" or node.kind is None:
                raise ArtifactError("whole-group interpretation gate is inconsistent")
            details = _payload(node, selectors)
            children = (
                node.payload.children
                if isinstance(node.payload, c.Expression)
                else node.payload.included_node_ids
                if isinstance(node.payload, c.Count)
                else ()
            )
            links = ", ".join(f'<a href="#warunek-{i}">{i}</a>' for i in children)
            parts.append(
                f'<li id="warunek-{node.identity}" value="{node.identity}"><strong>{_e(LABELS[node.kind])}</strong>: {details}'
                + (f" - zapisy {links}" if links else "")
                + f' <span class="detail-source">(wiersze {_e(", ".join(map(str, node.source_rows)))})</span></li>'
            )
            rendered_characters += len(parts[-1])
            if rendered_characters > MAX_SECTION_CHARACTERS:
                raise ArtifactError("condition section display exceeds bound")
        parts.append(
            "</ol><p>Korzenie zapisów źródłowych: "
            + _e(", ".join(map(str, cast(list[int], answer["roots"]))))
            + ". Oddzielne korzenie nie tworzą domyślnej koniunkcji.</p>"
        )
    else:
        parts.append(
            '<p class="badge badge-warn">Jeszcze nie zinterpretowano całej grupy. Pokazujemy zapis źródłowy, bez częściowych reguł strukturalnych.</p>'
        )
    parts.append("<details open><summary>Zapis źródłowy</summary><ul>")
    rendered_characters = sum(map(len, parts))
    for item in cast(list[Json], answer["source_text"]):
        text = f'<li><span class="detail-source">Wiersz {_e(item["row"])}</span>: {_e(item["text"])}</li>'
        rendered_characters += len(text)
        if rendered_characters > MAX_SECTION_CHARACTERS:
            raise ArtifactError("condition source display exceeds bound")
        parts.append(text)
    parts.append("</ul></details>")
    parts.append(source_facts(source) + "</section>")
    return "".join(parts)


def membership_section(answers: list[Json], *, slug: str, code: str, day: str) -> str:
    parts = [
        '<section class="code-children jgp-memberships"><h2>Występuje w grupach</h2>',
        f"<p>Dzień świadczenia: {_e(day)}. Dowód obecności w jawnych listach załącznika 9, nie kwalifikacja przypadku. Brak pozycji nie dowodzi wykluczenia.</p>",
    ]
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
