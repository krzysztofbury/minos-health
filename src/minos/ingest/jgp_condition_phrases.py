"""Conservative typed fragments, not a general Polish or executable-rule grammar.

Unconsumed text and uncertain scope remain explicit unsupported roots. Parsing
an atomic fragment never licenses discarding the rest of its source cell.
"""

import heapq
import itertools
import re
from dataclasses import replace
from typing import Literal, cast, final, override

from minos.domain import jgp_conditions as c
from minos.domain.jgp_conditions_validation import MAX_CONDITIONS, MAX_SELECTORS
from minos.domain.reference import SourceError

OP = r"(>=|<=|>|<|≥|≤)"
CODE = r"(?:[0-9]{2,3}(?:\.[0-9]{1,5})?|[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,3})?)"
NAME = r"(?:[A-Z]{1,4}[0-9]{1,3}[A-Za-z]{0,3}|Ogólnej)"
QUANTITY = r"(?:[0-9]{1,3}|jednej|jedną|dwóch|dwie|trzech|trzy|czterech|pięciu|sześciu|siedmiu|ośmiu|dziewięciu|dziesięciu)"
REPEAT = r"(?:dwukrotne|dwukrotnie|dwukrone|trzykrotne|trzykrotnie|pięciokrotne)"
MARK = re.compile(r"^(\(\*[0-9]{1,2}\)|\*{1,4})\s*(.*)$", re.S)
UNITS = r"(?:r\.\s*ż\.?|tyg\.\s*ż\.?|tyg\.\s*życia|miesiąca życia|miesięcy życia)"
AGE = re.compile(
    r"wiek(?: pacjenta)?\s*"
    + OP
    + r"\s*([0-9]{1,4})\.?\s*("
    + UNITS
    + r")"
    + r"(?:\s*(?:i\s*)?"
    + OP
    + r"\s*([0-9]{1,4})\.?\s*("
    + UNITS
    + r"))?"
)
DURATION = re.compile(
    r"czas (?:pobytu|hospitalizacji)\s*"
    + OP
    + r"\s*([0-9]{1,4})\s*d(?:ni|nia|zień)(?:\s*i\s*"
    + OP
    + r"\s*([0-9]{1,4})\s*d(?:ni|nia|zień))?"
)
WARD = re.compile(r"czas pobytu w OINK\s*" + OP + r"\s*([0-9]{1,4})\s*d(?:ni|nia|zień)")
CLINICAL = re.compile(
    r"(ECOG|mMRC|NYHA|LVEF|EGFR)\s*" + OP + r"\s*([IVX]+|[0-9]+(?:[.,][0-9]+)?)(\s*%)?"
)
PROCEDURE_LIST = re.compile(
    r"(?:(co najmniej|minimum)\s*)?(?:("
    + QUANTITY
    + r"|"
    + REPEAT
    + r")\s+(?:wskazanie\s+|różnych\s+|z\s+)?)?(wszystkich\s+)?procedur(?:y|ę|ze)?\s+z\s+[Ll]isty\s+(?:procedur\s+|dodatkowej\s+)?("
    + NAME
    + r")"
)
DIAGNOSIS_LIST = re.compile(
    r"(?:(co najmniej)\s*)?(?:("
    + QUANTITY
    + r")\s+)?rozpozna(?:nia|nie|ń)\s*(zasadniczego|współistniejącego|współistniejących)?\s*z\s+[Ll]isty\s+(?:rozpoznań\s+|dodatkowej\s+)?("
    + NAME
    + r")"
)
BELOW_DIAGNOSIS = re.compile(
    r"rozpoznania\s+(zasadniczego|współistniejącego)\s+z poniższej listy"
    + r"(?:\s+\("
    + r"("
    + NAME
    + r")"
    + r"\))?"
)
LITERAL = re.compile(
    r"(procedur(?:y|ę)|rozpoznania(?:\s+(zasadniczego|współistniejącego))?)\s+("
    + CODE
    + r")(?=\s|$)"
)
# A procedure title between a code and its qualifier: bounded, no digits,
# parentheses or clause separators, so no code or condition can hide in it.
TITLE_GAP = r"(?: [^0-9();,]{1,160}?)? ?"
CODE_LIST = CODE + r"(?:\s*(?:,|lub)\s*" + CODE + r")*"
COMPARISONS: dict[str, c.Comparison] = {
    ">": "gt",
    "<": "lt",
    ">=": "ge",
    "<=": "le",
    "≥": "ge",
    "≤": "le",
}


def quantity(text: str) -> int:
    if text.isdigit():
        return int(text)
    # Reviewed cardinal syntax, not a spelling/source-correction dictionary.
    match text:
        case "jednej" | "jedną":
            return 1
        case "dwóch" | "dwie" | "dwukrotne" | "dwukrotnie" | "dwukrone":
            return 2
        case "trzech" | "trzy" | "trzykrotne" | "trzykrotnie":
            return 3
        case "czterech":
            return 4
        case "pięciu" | "pięciokrotne":
            return 5
        case "sześciu":
            return 6
        case "siedmiu":
            return 7
        case "ośmiu":
            return 8
        case "dziewięciu":
            return 9
        case "dziesięciu":
            return 10
        case _:
            raise SourceError("unreviewed condition cardinal syntax")


@final
class _State:
    def __init__(self, sheet: c.Characteristics) -> None:
        self.sheet = sheet
        self.nodes: list[c.Condition] = []
        self.selectors: list[c.Selector] = []
        evidence = {e.row: e for e in sheet.evidence}
        # Exact code/title pairs attested by any ICD-9 list in this sheet.
        self.procedure_titles: dict[str, set[str]] = {}
        for item in sheet.lists:
            if item.system != "icd9-pl":
                continue
            for member in item.members:
                text = " ".join(evidence[member.row].text.split())
                if text.startswith(member.code + " "):
                    self.procedure_titles.setdefault(member.code, set()).add(
                        text[len(member.code) :].strip()
                    )

    def add(
        self,
        group: str,
        kind: c.Kind | None,
        payload: c.Payload,
        rows: tuple[int, ...],
        reason: str | None = None,
        evidence_type: Literal["label", "annotation", "title_qualifier"] = "label",
    ) -> int:
        if len(self.nodes) >= MAX_CONDITIONS:
            raise SourceError("characteristics condition count exceeds bound")
        if (
            isinstance(payload, c.Expression)
            and reason is None
            and any(
                self.nodes[child - 1].status == "unsupported"
                for child in payload.children
            )
        ):
            reason = "child_interpretation_unsupported"
        identity = len(self.nodes) + 1
        self.nodes.append(
            c.Condition(
                identity,
                group,
                kind,
                payload,
                tuple(dict.fromkeys(rows)),
                "unsupported" if reason else "proposed",
                reason,
                evidence_type,
            )
        )
        return identity

    def selector(self, body: c.SelectorBody, rows: tuple[int, ...]) -> int:
        if len(self.selectors) >= MAX_SELECTORS:
            raise SourceError("characteristics selector count exceeds bound")
        identity = len(self.selectors) + 1
        self.selectors.append(c.Selector(identity, body, rows))
        return identity

    def codes(self, codes: tuple[str, ...], system: c.System, row: int) -> int:
        return self.selector(
            c.CodeSelector(tuple(dict.fromkeys(codes)), system), (row,)
        )

    def named_list(self, name: str, system: c.System, row: int) -> int | None:
        label = "Lista Ogólna" if name == "Ogólnej" else name
        candidates = [
            item
            for item in self.sheet.lists
            if item.label == label and item.system == system
        ]
        if len(candidates) != 1:
            return None
        item = candidates[0]
        return self.selector(
            c.ListSelector((item.identity,), system),
            tuple(dict.fromkeys((row, item.marker_row, *item.naming_rows))),
        )

    def declared_below(self, group: str, name: str, system: c.System, row: int) -> bool:
        """`(poniższej)` holds only for the uniquely named list of this same
        group that physically follows the label; a list above it or in another
        group's context leaves the suffix unconsumed."""
        label = "Lista Ogólna" if name == "Ogólnej" else name
        candidates = [
            item
            for item in self.sheet.lists
            if item.label == label and item.system == system
        ]
        return (
            len(candidates) == 1
            and candidates[0].context.strip() == group
            and candidates[0].marker_row > row
        )

    def below_list(self, group: str, row: int, name: str | None) -> int | None:
        """Bind a physical declaration, never a guessed list name or prefix."""
        definitions = {item.marker_row: item for item in self.sheet.lists}
        for evidence in self.sheet.evidence:
            if evidence.row <= row:
                continue
            if (evidence.context or "").strip() != group or evidence.category in (
                "title",
                "label",
                "connector",
                "unbound_member",
            ):
                return None
            if evidence.category == "section" and evidence.text.strip():
                return None
            if evidence.category == "list_heading" and (
                name is None or evidence.text.strip() != name
            ):
                return None  # a named list in between is not "the list below"
            item = definitions.get(evidence.row)
            # Procedure lists conventionally precede the diagnosis list, so the
            # scan may pass them; the reader's recorded declaration row (below)
            # is what ties this label to the diagnosis list it binds.
            if item is not None and item.system == "icd10-pl":
                if (name is not None and item.label != name) or row not in (
                    item.naming_rows
                ):
                    return None
                return self.selector(
                    c.ListSelector((item.identity,), item.system),
                    tuple(dict.fromkeys((row, item.marker_row, *item.naming_rows))),
                )
        return None

    def title_end(self, code: str, text: str, end: int) -> int:
        titles = self.procedure_titles.get(code, ())
        for title in sorted(titles, key=len, reverse=True):
            if not _plain_title(title) or re.search(
                r"[0-9<>≥≤]|\b(?:wiek|czas|tryb|rozpoznania|procedury|wymagane|minimum|min)\b",
                title,
                re.IGNORECASE,
            ):
                continue
            suffix = " " + title
            following = end + len(suffix)
            if text.startswith(suffix, end) and (
                following == len(text) or text[following] in " ,;."
            ):
                return following
        return end

    def unknown(
        self,
        group: str,
        row: int,
        reason: str,
        fragments: tuple[int, ...] = (),
        evidence_type: Literal["label", "annotation", "title_qualifier"] = "label",
    ) -> int:
        return self.add(
            group, None, c.Unsupported(reason, fragments), (row,), reason, evidence_type
        )


def interpret_sheet(sheet: c.Characteristics) -> c.Characteristics:
    state = _State(sheet)
    groups: list[c.Group] = []
    for group in sheet.groups:
        items = [e for e in sheet.evidence if (e.context or "").strip() == group.code]
        branches: list[int] = []
        other: list[int] = []
        connectors: list[int] = []
        for item in items:
            if item.category == "connector":
                connectors.append(item.row)
            elif item.category == "label":
                annotation = MARK.match(item.text.strip())
                if annotation:
                    other.extend(_guard(state, group.code, item, annotation, items))
                elif item.text.strip().startswith("W przypadku sprawozdania"):
                    other.append(
                        state.unknown(
                            group.code,
                            item.row,
                            "guard_intro_scope_unreviewed",
                            evidence_type="annotation",
                        )
                    )
                else:
                    branches.append(_cell(state, group.code, item))
        if not branches:
            branches.append(state.unknown(group.code, group.title_row, "missing_label"))
        if connectors and _connectors_separate(state, branches, connectors):
            branch_rows = tuple(
                sorted(
                    {
                        row
                        for identity in branches
                        for row in state.nodes[identity - 1].source_rows
                    }
                    | set(connectors)
                )
            )
            roots = [
                state.add(group.code, "any", c.Expression(tuple(branches)), branch_rows)
            ]
        else:
            roots = branches
            if connectors:
                roots.append(
                    state.unknown(
                        group.code, connectors[0], "alternative_scope_unreviewed"
                    )
                )
        roots.extend(other)
        unbound = tuple(e.row for e in items if e.category == "unbound_member")
        if unbound:
            roots.append(
                state.add(
                    group.code,
                    None,
                    c.Unsupported("list_definition_unbound"),
                    unbound,
                    "list_definition_unbound",
                )
            )
        roots.extend(_title_qualifiers(state, group, items))
        groups.append(replace(group, roots=tuple(roots)))
    _propagate_unsupported(state.nodes)
    return replace(
        sheet,
        groups=tuple(groups),
        selectors=tuple(state.selectors),
        conditions=_ordered_nodes(state.nodes),
    )


def _propagate_unsupported(nodes: list[c.Condition]) -> None:
    """Typed fragments under an unsupported node keep their payload as evidence
    but are unsupported too: a consumer selecting `proposed` rows must never
    receive part of a requirement whose whole is not interpreted."""
    from minos.domain.jgp_conditions_validation import children

    # Walk down from every unsupported node; a shared child (guard consequence)
    # can have a higher identity than one of its parents, so no single ordered
    # pass is enough. Each node is demoted at most once, bounding the walk.
    pending = [node.identity for node in nodes if node.status == "unsupported"]
    while pending:
        for child in children(nodes[pending.pop() - 1]):
            if nodes[child - 1].status == "proposed":
                nodes[child - 1] = replace(
                    nodes[child - 1],
                    status="unsupported",
                    reason="ancestor_interpretation_unsupported",
                )
                pending.append(child)


def _connectors_separate(
    state: _State, branches: list[int], connectors: list[int]
) -> bool:
    if len(connectors) != len(branches) - 1:
        return False
    spans = sorted(
        (min(state.nodes[b - 1].source_rows), max(state.nodes[b - 1].source_rows))
        for b in branches
    )
    return all(
        spans[index][1] < row < spans[index + 1][0]
        for index, row in enumerate(sorted(connectors))
    )


def _ordered_nodes(nodes: list[c.Condition]) -> tuple[c.Condition, ...]:
    from minos.domain.jgp_conditions_validation import children

    by_id = {node.identity: node for node in nodes}
    remaining = {node.identity: len(children(node)) for node in nodes}
    dependents: dict[int, list[int]] = {node.identity: [] for node in nodes}
    for node in nodes:
        for child in children(node):
            if child not in dependents:
                raise SourceError("condition child is unresolved")
            dependents[child].append(node.identity)
    ready = [identity for identity, count in remaining.items() if not count]
    heapq.heapify(ready)
    result: list[c.Condition] = []
    while ready:
        identity = heapq.heappop(ready)
        result.append(by_id[identity])
        for parent in dependents[identity]:
            remaining[parent] -= 1
            if not remaining[parent]:
                heapq.heappush(ready, parent)
    if len(result) != len(nodes):
        raise SourceError("condition graph is cyclic")
    return tuple(result)


class _Spans(list[tuple[int, int]]):
    """Records which fragments each phrase span produced (fragment first)."""

    fragments: list[int]
    owned: list[tuple[tuple[int, int], tuple[int, ...]]]
    mark: int

    def __init__(self, fragments: list[int]) -> None:
        super().__init__()
        self.fragments = fragments
        self.owned = []
        self.mark = 0

    @override
    def append(self, value: tuple[int, int], /) -> None:
        super().append(value)
        self.owned.append((value, tuple(self.fragments[self.mark :])))
        self.mark = len(self.fragments)


_FILLER = re.compile(r"\b(?:wymagane wskazanie|wskazanie)\b|-")
_TOKEN = re.compile(r";|,|\b(?:oraz|i|lub|albo)\b|\S+")


def _connector(gap: str) -> str | None:
    """`part`, `and` or `or` for the text between two phrases, else None."""
    tokens: list[str] = _TOKEN.findall(_FILLER.sub(" ", gap))
    if any(token not in {";", ",", "oraz", "i", "lub", "albo"} for token in tokens):
        return None
    if ";" in tokens:
        return None if {"lub", "albo"} & set(tokens) else "part"
    if {"lub", "albo"} & set(tokens):
        return None if {",", "oraz", "i"} & set(tokens) else "or"
    return "and" if tokens else None


def _grammar(
    state: _State, group: str, row: int, text: str, spans: _Spans
) -> int | None:
    """Owner rule 2026-10-07: semicolons separate required parts; within a
    part `oraz`/`i`/commas are AND and `lub`/`albo` join adjacent alternatives."""
    owned = sorted(spans.owned)
    claimed = [f for _, fragments in owned for f in fragments]
    if sorted(claimed) != sorted(spans.fragments) or len(set(claimed)) != len(claimed):
        return None
    items = [(span, fragments) for span, fragments in owned if fragments]
    if not items or any(a[0][1] > b[0][0] for a, b in itertools.pairwise(items)):
        return None
    if _TOKEN.findall(_FILLER.sub(" ", text[: items[0][0][0]])) or _TOKEN.findall(
        _FILLER.sub(" ", text[items[-1][0][1] :]).strip(" .;")
    ):
        return None
    nodes = [
        f[0] if len(f) == 1 else state.add(group, "all", c.Expression(f), (row,))
        for _, f in items
    ]
    parts: list[list[list[int]]] = [[[nodes[0]]]]
    for (left, _), (right, _), node in zip(items, items[1:], nodes[1:], strict=False):
        connector = _connector(text[left[1] : right[0]])
        if connector is None:
            return None
        if connector == "part":
            parts.append([[node]])
        elif connector == "and":
            parts[-1].append([node])
        else:
            parts[-1][-1].append(node)
    conjuncts = [
        alternatives[0]
        if len(alternatives) == 1
        else state.add(group, "any", c.Expression(tuple(alternatives)), (row,))
        for part in parts
        for alternatives in part
    ]
    if len(conjuncts) == 1:
        return conjuncts[0]
    return state.add(group, "all", c.Expression(tuple(conjuncts)), (row,))


def _cell(state: _State, group: str, item: c.Evidence) -> int:
    text = " ".join(item.text.split())
    fragments: list[int] = []
    spans = _Spans(fragments)
    _secondary_exemption(state, group, item.row, text, fragments, spans)
    _scalar_fragments(state, group, item.row, text, fragments, spans)
    _list_fragments(state, group, item.row, text, fragments, spans)
    _special_fragments(state, group, item.row, text, fragments, spans)
    for match in LITERAL.finditer(text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        diagnosis = match[1].startswith("rozpoznania")
        system: c.System = "icd10-pl" if diagnosis else "icd9-pl"
        role: c.Role | None = (
            (
                "principal"
                if match[2] == "zasadniczego"
                else "secondary"
                if match[2]
                else "source_unspecified"
            )
            if diagnosis
            else None
        )
        selector = state.codes((match[3],), system, item.row)
        reason = "diagnosis_role_unspecified" if role == "source_unspecified" else None
        fragments.append(
            state.add(
                group,
                "diagnosis_presence" if diagnosis else "procedure_presence",
                c.Presence(selector, role),
                (item.row,),
                reason,
            )
        )
        end = match.end() if diagnosis else state.title_end(match[3], text, match.end())
        spans.append((match.start(), end))
    remaining = list(text)
    for a, b in spans:
        remaining[a:b] = " " * (b - a)
    residue = "".join(remaining)
    residue = re.sub(r"\b(?:wymagane wskazanie|wskazanie|oraz|i)\b", " ", residue)
    if not re.search(r"\blub\b|\balbo\b", text):
        residue = residue.replace(",", " ")
    residue = residue.strip(" ;.\n")
    if residue or not fragments:
        if re.search(r"\blub\b|\balbo\b", text):
            parsed = _grammar(state, group, item.row, text, spans)
            if parsed is not None:
                return parsed
            reason = "boolean_scope_unreviewed"
        else:
            reason = "unconsumed_source_text"
        return state.unknown(group, item.row, reason, tuple(fragments))
    if len(fragments) == 1:
        return fragments[0]
    return state.add(group, "all", c.Expression(tuple(fragments)), (item.row,))


def _scalar_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    for match in AGE.finditer(text):
        if match[4] is not None and not _age_range(match):
            result.append(state.unknown(group, row, "age_bounds_unreviewed"))
            spans.append(match.span())
            continue
        for offset in (0, 3):
            if match[1 + offset] is None:
                continue
            unit = match[3 + offset]
            age_unit: Literal["completed_years", "elapsed_weeks", "calendar_months"] = (
                "elapsed_weeks"
                if unit.startswith("tyg")
                else "calendar_months"
                if unit.startswith("mies")
                else "completed_years"
            )
            result.append(
                state.add(
                    group,
                    "patient_age",
                    c.Age(
                        COMPARISONS[match[1 + offset]], int(match[2 + offset]), age_unit
                    ),
                    (row,),
                )
            )
        spans.append(match.span())
    for match in DURATION.finditer(text):
        bounds: tuple[c.DurationBound, ...] = (
            c.DurationBound(COMPARISONS[match[1]], int(match[2])),
        )
        if match[3]:
            bounds += (c.DurationBound(COMPARISONS[match[3]], int(match[4])),)
        result.append(
            state.add(group, "episode_duration", c.EpisodeDuration(bounds), (row,))
        )
        spans.append(match.span())
    for match in WARD.finditer(text):
        result.append(
            state.add(
                group,
                "ward_duration",
                c.WardDuration(COMPARISONS[match[1]], int(match[2])),
                (row,),
            )
        )
        spans.append(match.span())
    _measurement_fragments(state, group, row, text, result, spans)


def _measurement_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    for match in CLINICAL.finditer(text):
        measure = cast(Literal["ECOG", "mMRC", "NYHA", "LVEF", "EGFR"], match[1])
        unit: Literal["source_ordinal", "%"] | None = (
            "source_ordinal"
            if measure in ("ECOG", "mMRC", "NYHA")
            else "%"
            if match[4]
            else None
        )
        result.append(
            state.add(
                group,
                "clinical_measure",
                c.ClinicalMeasure(measure, COMPARISONS[match[2]], match[3], unit),
                (row,),
                "clinical_unit_missing" if unit is None else None,
            )
        )
        spans.append(match.span())
    for match in re.finditer(
        r"tryb wypisu\s+([1-9][0-9]?)(?:\s*-\s*zgon pacjenta)?", text
    ):
        result.append(
            state.add(
                group, "discharge_mode", c.DischargeMode((int(match[1]),)), (row,)
            )
        )
        spans.append(match.span())
    for match in re.finditer(r"\(dotyczy wyłącznie KOC\)", text):
        result.append(
            state.add(group, "program_membership", c.ProgramMembership(), (row,))
        )
        spans.append(match.span())
    for match in re.finditer(r"kod ORPHA\s+([0-9]{1,12})", text):
        result.append(
            state.add(
                group, "external_disease_code", c.ExternalDiseaseCode(match[1]), (row,)
            )
        )
        spans.append(match.span())


def _list_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    for match in PROCEDURE_LIST.finditer(text):
        selector = state.named_list(match[4], "icd9-pl", row)
        if selector is None:
            result.append(state.unknown(group, row, "list_reference_unresolved"))
        elif match[3]:
            result.append(
                state.add(
                    group,
                    "procedure_all_members",
                    c.AllMembers(selector, quantity(match[2]) if match[2] else 1),
                    (row,),
                )
            )
        elif match[2]:
            repeated = re.fullmatch(REPEAT, match[2]) is not None
            policy: Literal[
                "distinct_codes", "repeat_occurrences", "review_required"
            ] = (
                "repeat_occurrences"
                if repeated
                else "distinct_codes"
                if "różnych" in match[0]
                else "review_required"
            )
            result.append(
                state.add(
                    group,
                    "procedure_count",
                    c.Count(
                        selector,
                        quantity(match[2]),
                        "minimum" if match[1] else "source_cardinality",
                        policy,
                        None,
                    ),
                    (row,),
                )
            )
        else:
            result.append(
                state.add(
                    group, "procedure_presence", c.Presence(selector, None), (row,)
                )
            )
        end = match.end()
        if text[end:].startswith(" (poniższej)") and state.declared_below(
            group, match[4], "icd9-pl", row
        ):
            end += len(" (poniższej)")
        spans.append((match.start(), end))
        if selector is not None and text[match.end() :].startswith(
            " (jednokrotnie dla piersi prawej i jednokrotnie dla piersi lewej)"
        ):
            result.append(
                state.add(
                    group,
                    "procedure_laterality",
                    c.Laterality(selector, ("right_breast", "left_breast")),
                    (row,),
                )
            )
    _diagnosis_fragments(state, group, row, text, result, spans)
    below = list(BELOW_DIAGNOSIS.finditer(text))
    for match in below:
        # Two "lists below" in one cell do not say which list each one means.
        selector = None if len(below) > 1 else state.below_list(group, row, match[2])
        if selector is None:
            result.append(state.unknown(group, row, "list_reference_unresolved"))
        else:
            result.append(
                state.add(
                    group,
                    "diagnosis_presence",
                    c.Presence(
                        selector,
                        "principal" if match[1] == "zasadniczego" else "secondary",
                    ),
                    (row,),
                )
            )
        spans.append(match.span())


def _diagnosis_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    for match in DIAGNOSIS_LIST.finditer(text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        selector = state.named_list(match[4], "icd10-pl", row)
        if selector is None:
            result.append(state.unknown(group, row, "list_reference_unresolved"))
        else:
            role: c.Role = (
                "principal"
                if match[3] == "zasadniczego"
                else "secondary"
                if match[3]
                else "source_unspecified"
            )
            reason = (
                "diagnosis_role_unspecified" if role == "source_unspecified" else None
            )
            if match[2]:
                result.append(
                    state.add(
                        group,
                        "diagnosis_count",
                        c.Count(
                            selector,
                            quantity(match[2]),
                            "minimum" if match[1] else "source_cardinality",
                            "review_required",
                            role,
                        ),
                        (row,),
                        reason,
                    )
                )
            else:
                result.append(
                    state.add(
                        group,
                        "diagnosis_presence",
                        c.Presence(selector, role),
                        (row,),
                        reason,
                    )
                )
        spans.append(match.span())


def _special_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    coverage = re.search(
        r"co najmniej ("
        + QUANTITY
        + r") procedur pochodzących z r[oó]żnych list ("
        + NAME
        + r") \(([a-z])-([a-z])\)",
        text,
    )
    if coverage:
        names = tuple(
            coverage[2] + chr(letter)
            for letter in range(ord(coverage[3]), ord(coverage[4]) + 1)
        )
        targets = tuple(state.named_list(name, "icd9-pl", row) for name in names)
        if 2 <= len(targets) <= 26 and all(t is not None for t in targets):
            result.append(
                state.add(
                    group,
                    "procedure_list_coverage",
                    c.ListCoverage(
                        cast(tuple[int, ...], targets),
                        quantity(coverage[1]),
                        quantity(coverage[1]),
                    ),
                    (row,),
                )
            )
        else:
            result.append(state.unknown(group, row, "list_reference_unresolved"))
        spans.append(coverage.span())
    union = re.search(
        r"("
        + QUANTITY
        + r") procedur ze zbioru złożonego z list procedur ("
        + NAME
        + r") i ("
        + NAME
        + r")",
        text,
    )
    if union:
        left, right = (
            state.named_list(union[2], "icd9-pl", row),
            state.named_list(union[3], "icd9-pl", row),
        )
        if left is not None and right is not None:
            selector = state.selector(c.UnionSelector((left, right), "icd9-pl"), (row,))
            result.append(
                state.add(
                    group,
                    "procedure_count",
                    c.Count(
                        selector,
                        quantity(union[1]),
                        "source_cardinality",
                        "review_required",
                        None,
                    ),
                    (row,),
                )
            )
        else:
            result.append(state.unknown(group, row, "list_reference_unresolved"))
        spans.append(union.span())
    _literal_special_fragments(state, group, row, text, result, spans)


def _literal_special_fragments(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    for match in re.finditer(
        r"("
        + CODE
        + r")("
        + TITLE_GAP
        + r")\(czas trwania min ([0-9]{1,3})h\)"
        + r"\(min ([0-9]{1,3}) raz\)",
        text,
    ):
        if not _plain_title(match[2]):
            continue
        selector = state.codes((match[1],), "icd9-pl", row)
        result.append(
            state.add(
                group,
                "procedure_duration",
                c.ProcedureDuration(selector, int(match[3]), int(match[4])),
                (row,),
            )
        )
        spans.append(match.span())
    for match in re.finditer(
        r"(?:co najmniej )?(" + REPEAT + r") wskazanie procedury (" + CODE + r")", text
    ):
        selector = state.codes((match[2],), "icd9-pl", row)
        result.append(
            state.add(
                group,
                "procedure_count",
                c.Count(
                    selector,
                    quantity(match[1]),
                    "minimum"
                    if match[0].startswith("co najmniej")
                    else "source_cardinality",
                    "repeat_occurrences",
                    None,
                ),
                (row,),
            )
        )
        spans.append(match.span())
    all_members = re.search(
        r"obu procedur ze zbioru \(("
        + CODE
        + r")("
        + TITLE_GAP
        + r")i ("
        + CODE
        + r")("
        + TITLE_GAP
        + r")\)",
        text,
    )
    if all_members and _plain_title(all_members[2]) and _plain_title(all_members[4]):
        selector = state.codes((all_members[1], all_members[3]), "icd9-pl", row)
        result.append(
            state.add(group, "procedure_all_members", c.AllMembers(selector, 1), (row,))
        )
        spans.append(all_members.span())
    _included_subset(state, group, row, text, result, spans)
    _date_relation(state, group, row, text, result)
    # Family evidence is explicitly unexpanded, never prefix-derived membership.
    for match in re.finditer(r"(" + CODE + r") z rozwinięciami", text):
        if any(a <= match.start() < b for a, b in spans):
            continue
        selector = state.selector(c.FamilySelector(match[1], "icd10-pl"), (row,))
        result.append(
            state.add(
                group,
                "diagnosis_presence",
                c.Presence(selector, "source_unspecified"),
                (row,),
                "classification_family_unbound",
            )
        )
        spans.append(match.span())


def _age_unit(unit: str) -> str:
    return (
        "weeks"
        if unit.startswith("tyg")
        else "months"
        if unit.startswith("mies")
        else "years"
    )


def _age_range(match: re.Match[str]) -> bool:
    """A reviewed two-bound age: opposite directions, one unit, non-empty."""
    first, second = COMPARISONS[match[1]], COMPARISONS[match[4]]
    if _age_unit(match[3]) != _age_unit(match[6]):
        return False
    bounds = {first[0]: (first, int(match[2])), second[0]: (second, int(match[5]))}
    if set(bounds) != {"g", "l"}:
        return False
    (low_op, low), (high_op, high) = bounds["g"], bounds["l"]
    # gt 17 and lt 66 is a range; gt 65 and lt 2 is empty.
    smallest = low + (1 if low_op == "gt" else 0)
    largest = high - (1 if high_op == "lt" else 0)
    return smallest <= largest


def _plain_title(gap: str) -> bool:
    return not re.search(r"\b(?:lub|albo|oraz|nie|bez|chyba)\b", gap)


def _included_subset(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    match = re.search(
        r"\(w tym: (" + CODE + r") oraz \((" + CODE + r") lub (" + CODE + r")\)\)", text
    )
    if match is None:
        return
    counts = [
        i
        for i in result
        if isinstance(state.nodes[i - 1].payload, c.Count)
        and state.nodes[i - 1].kind == "procedure_count"
    ]
    if len(counts) != 1:
        result.append(state.unknown(group, row, "included_count_allocation_unreviewed"))
        return
    nodes = tuple(
        state.add(
            group,
            "procedure_presence",
            c.Presence(state.codes((code,), "icd9-pl", row), None),
            (row,),
        )
        for code in (match[1], match[2], match[3])
    )
    alternative = state.add(group, "any", c.Expression(nodes[1:]), (row,))
    mandatory = state.add(group, "all", c.Expression((nodes[0], alternative)), (row,))
    old = state.nodes[counts[0] - 1]
    payload = cast(c.Count, old.payload)
    state.nodes[counts[0] - 1] = replace(
        old, payload=replace(payload, included_node_ids=(mandatory,))
    )
    spans.append(match.span())


def _date_relation(
    state: _State, group: str, row: int, text: str, result: list[int]
) -> None:
    if "wykonanie w innym terminie w czasie jednej hospitalizacji" not in text:
        return
    sides = text.split("wykonanie w innym terminie w czasie jednej hospitalizacji", 1)
    names: list[list[str]] = [
        re.findall(
            r"(?:procedury )?z listy (?:procedur |dodatkowej )?(" + NAME + r")", side
        )
        for side in sides
    ]
    if len(names[0]) < 2 or len(names[1]) != 2:
        result.append(state.unknown(group, row, "date_occurrence_sets_unresolved"))
        return
    targets = [
        tuple(state.named_list(name, "icd9-pl", row) for name in side[:2])
        for side in names
    ]
    if any(t is None for side in targets for t in side):
        result.append(state.unknown(group, row, "date_occurrence_sets_unresolved"))
        return
    result.append(
        state.add(
            group,
            "procedure_date_relation",
            c.DateRelation(cast(tuple[tuple[int, ...], ...], tuple(targets))),
            (row,),
            "date_allocation_unreviewed",
        )
    )


def _guard(
    state: _State,
    group: str,
    item: c.Evidence,
    annotation: re.Match[str],
    evidence: list[c.Evidence],
) -> list[int]:
    mark, text = annotation[1], annotation[2]
    marked = [
        m
        for item_list in state.sheet.lists
        if item_list.context == group
        for m in item_list.members
        if mark in m.marks
    ]
    if not marked:
        return [
            state.unknown(
                group,
                item.row,
                "annotation_trigger_unresolved",
                evidence_type="annotation",
            )
        ]
    intro = [
        e.row
        for e in evidence
        if e.row < item.row and e.text.strip().startswith("W przypadku sprawozdania")
    ]
    rows = (item.row, intro[-1]) if intro else (item.row,)
    consequence = _guard_consequence(state, group, item, text, rows)
    return _guard_members(state, group, item, rows, marked, consequence)


def _guard_consequence(
    state: _State, group: str, item: c.Evidence, text: str, rows: tuple[int, ...]
) -> int:
    consequence: int
    text = " ".join(text.split())
    exclusion = re.fullmatch(
        r"brak możliwości wykazania ł[aą]cznie z (?:z )?procedur(?:ą|ami):? ("
        + CODE_LIST
        + r")",
        text,
    )
    only_procedures = re.fullmatch(
        r"wykazanie wyłącznie z procedur(?:ą|ami):? (" + CODE_LIST + r")", text
    )
    only_diagnoses = re.fullmatch(
        r"wykazanie wyłącznie z rozpoznaniami:? (" + CODE_LIST + r")", text
    )
    required = re.fullmatch(
        r"wymagane sprawozdanie procedury wg\. ICD 9: ("
        + CODE
        + r")("
        + TITLE_GAP
        + r")",
        text,
    )
    if exclusion:
        codes = tuple(re.findall(CODE, exclusion[1]))
        present = state.add(
            group,
            "procedure_presence",
            c.Presence(state.codes(codes, "icd9-pl", item.row), None),
            (item.row,),
        )
        consequence = state.add(group, "not", c.Expression((present,)), rows)
    elif only_procedures:
        codes = tuple(re.findall(CODE, only_procedures[1]))
        consequence = state.add(
            group,
            "procedure_presence",
            c.Presence(state.codes(codes, "icd9-pl", item.row), None),
            rows,
        )
    elif only_diagnoses:
        codes = tuple(re.findall(CODE, only_diagnoses[1]))
        consequence = state.add(
            group,
            "diagnosis_presence",
            c.Presence(state.codes(codes, "icd10-pl", item.row), "source_unspecified"),
            rows,
            "diagnosis_role_unspecified",
        )
    elif re.fullmatch(r"wykazanie min\. 3 krotnie podczas hospitalizacji", text):
        # Each guard gets its own repeat-sensitive consequence below.
        consequence = 0
    elif required and _plain_title(required[2]):
        consequence = state.add(
            group,
            "procedure_presence",
            c.Presence(state.codes((required[1],), "icd9-pl", item.row), None),
            rows,
        )
    else:
        consequence = _cell(state, group, replace(item, text=text))
    return consequence


def _guard_members(
    state: _State,
    group: str,
    item: c.Evidence,
    rows: tuple[int, ...],
    marked: list[c.Member],
    consequence: int,
) -> list[int]:
    roots: list[int] = []
    member_evidence = {e.row: e for e in state.sheet.evidence}
    for member in marked:
        system: c.System = next(
            li.system
            for li in state.sheet.lists
            if any(m.row == member.row for m in li.members)
        )
        selector = state.codes((member.code,), system, member.row)
        role: c.Role | None = "source_unspecified" if system == "icd10-pl" else None
        trigger = state.add(
            group,
            "diagnosis_presence" if role else "procedure_presence",
            c.Presence(selector, role),
            (member.row,),
            "diagnosis_role_unspecified" if role else None,
        )
        current = consequence
        if consequence == 0:
            current = state.add(
                group,
                "procedure_count",
                c.Count(selector, 3, "minimum", "repeat_occurrences", None),
                (item.row, member.row),
                "rank_compatibility_unbound",
            )
        if member_evidence[member.row].category != "member":
            raise SourceError("guard member lacks verified source evidence")
        roots.append(
            state.add(
                group,
                "when",
                c.Expression((trigger, current), (member.row,)),
                (*rows, member.row),
                "guard_occurrence_scope_unreviewed",
                "annotation",
            )
        )
    return roots


def _secondary_exemption(
    state: _State,
    group: str,
    row: int,
    text: str,
    result: list[int],
    spans: list[tuple[int, int]],
) -> None:
    match = re.search(
        r"rozpoznania współistniejącego z listy (?:rozpoznań )?("
        + NAME
        + r") \(przy czym rozpoznanie zasadnicze (.*?) nie wymagają podawania rozpoznania współistniejącego z listy ("
        + NAME
        + r")\)",
        text,
    )
    if match is None or match[1] != match[3]:
        return
    selector = state.named_list(match[1], "icd10-pl", row)
    if selector is None:
        result.append(state.unknown(group, row, "list_reference_unresolved"))
    else:
        # The exception makes the secondary requirement conditional. It does NOT
        # forbid a secondary diagnosis for an excepted principal diagnosis.
        pieces: list[int] = []
        for code in cast(list[str], re.findall(CODE, match[2])):
            source_selector = (
                state.selector(c.FamilySelector(code, "icd10-pl"), (row,))
                if code + " z rozwinięciami" in match[2]
                else state.codes((code,), "icd10-pl", row)
            )
            pieces.append(
                state.add(
                    group,
                    "diagnosis_presence",
                    c.Presence(source_selector, "principal"),
                    (row,),
                    "exemption_trigger_unbound",
                )
            )
        exception = state.unknown(
            group, row, "exemption_trigger_unbound", tuple(pieces)
        )
        outside_exception = state.add(
            group,
            "not",
            c.Expression((exception,)),
            (row,),
            "exemption_trigger_unbound",
        )
        consequence = state.add(
            group, "diagnosis_presence", c.Presence(selector, "secondary"), (row,)
        )
        result.append(
            state.add(
                group,
                "when",
                c.Expression((outside_exception, consequence)),
                (row,),
                "secondary_exemption_scope_unbound",
            )
        )
    spans.append(match.span())


def _bounds(text: str) -> set[tuple[str, int, str]]:
    found: set[tuple[str, int, str]] = set()
    for match in re.finditer(
        OP + r"\s*([0-9]{1,4})\.?\s*(r\.\s*ż|tyg|mies|d(?:ni|nia|zień)\b)", text
    ):
        unit = match[3]
        found.add(
            (
                COMPARISONS[match[1]],
                int(match[2]),
                "years"
                if unit.startswith("r")
                else "weeks"
                if unit == "tyg"
                else "months"
                if unit == "mies"
                else "days",
            )
        )
    return found


def _title_qualifiers(
    state: _State, group: c.Group, evidence: list[c.Evidence]
) -> list[int]:
    title = next(e.text for e in evidence if e.row == group.title_row)
    labels = " ".join(e.text for e in evidence if e.category == "label")
    # Compare (comparison, threshold, unit) bounds, not merely "some age": a
    # title `> 65 r.ż.` beside a label `wiek > 17 r.ż.` is still title-only.
    if not _bounds(title) <= _bounds(labels):
        return [
            state.unknown(
                group.code,
                group.title_row,
                "title_only_qualifier",
                evidence_type="title_qualifier",
            )
        ]
    return []
