"""Write/read invariants for bounded, non-executable characteristics evidence."""

import re

from minos.domain import jgp_conditions as c
from minos.domain.reference import SourceError

MAX_ROWS = 50_000
MAX_CONDITIONS = 20_000
MAX_SELECTORS = 20_000
MAX_CHILDREN = 64
MAX_DEPTH = 32
MAX_SOURCE_ROWS = 256
CODE = re.compile(r"(?:[0-9]{2,3}(?:\.[0-9]{1,5})?|[A-Z][0-9]{2}(?:\.[0-9A-Z]{1,3})?)")
PAYLOADS: dict[str, type[object]] = {
    "procedure_presence": c.Presence,
    "diagnosis_presence": c.Presence,
    "procedure_count": c.Count,
    "diagnosis_count": c.Count,
    "procedure_all_members": c.AllMembers,
    "procedure_list_coverage": c.ListCoverage,
    "procedure_date_relation": c.DateRelation,
    "procedure_laterality": c.Laterality,
    "procedure_duration": c.ProcedureDuration,
    "patient_age": c.Age,
    "episode_duration": c.EpisodeDuration,
    "ward_duration": c.WardDuration,
    "discharge_mode": c.DischargeMode,
    "clinical_measure": c.ClinicalMeasure,
    "program_membership": c.ProgramMembership,
    "external_disease_code": c.ExternalDiseaseCode,
    "all": c.Expression,
    "any": c.Expression,
    "not": c.Expression,
    "when": c.Expression,
}


def source_rows(rows: tuple[int, ...], evidence: dict[int, c.Evidence]) -> None:
    if not 1 <= len(rows) <= MAX_SOURCE_ROWS or len(set(rows)) != len(rows):
        raise SourceError("characteristics provenance is empty, repeated or unbounded")
    if any(row not in evidence for row in rows):
        raise SourceError("characteristics provenance target is missing")


def children(node: c.Condition) -> tuple[int, ...]:
    payload = node.payload
    if isinstance(payload, c.Expression):
        return payload.children
    if isinstance(payload, c.Unsupported):
        return payload.fragments
    if isinstance(payload, c.Count):
        return payload.included_node_ids
    return ()


def selector_ids(payload: c.Payload) -> tuple[int, ...]:
    if isinstance(
        payload, (c.Presence, c.Count, c.AllMembers, c.Laterality, c.ProcedureDuration)
    ):
        return (payload.selector_id,)
    if isinstance(payload, c.ListCoverage):
        return payload.selector_ids
    if isinstance(payload, c.DateRelation):
        return tuple(value for group in payload.occurrence_sets for value in group)
    return ()


def _node(node: c.Condition, selectors: dict[int, c.Selector]) -> None:
    payload = node.payload
    if node.kind is None:
        if not isinstance(payload, c.Unsupported) or node.status != "unsupported":
            raise SourceError("unsupported condition has an inconsistent payload")
        if payload.reason != node.reason:
            raise SourceError("unsupported condition reason conflicts")
    elif type(payload) is not PAYLOADS.get(node.kind):
        raise SourceError("condition kind conflicts with typed payload")
    if (node.status == "unsupported") != bool(node.reason):
        raise SourceError("condition interpretation reason is inconsistent")
    if node.evidence_type == "title_qualifier" and (
        node.kind is not None or node.status != "unsupported"
    ):
        raise SourceError("title qualifier cannot be promoted to a label condition")
    targets = selector_ids(payload)
    if any(identity not in selectors for identity in targets):
        raise SourceError("condition selector is unresolved")
    expected = (
        "icd10-pl"
        if node.kind in ("diagnosis_presence", "diagnosis_count")
        else "icd9-pl"
    )
    if any(selectors[t].body.system != expected for t in targets):
        raise SourceError("condition selector belongs to another classification")
    if node.status != "unsupported" and any(
        isinstance(selectors[t].body, c.FamilySelector) for t in targets
    ):
        raise SourceError("unbound classification family cannot be promoted")
    _payload(node)


def _payload(node: c.Condition) -> None:
    payload = node.payload
    if isinstance(payload, (c.Count, c.AllMembers)):
        count = (
            payload.required_count
            if isinstance(payload, c.Count)
            else payload.minimum_per_member
        )
        if not 1 <= count <= 1000:
            raise SourceError("condition count exceeds its reviewed bound")
    if isinstance(payload, (c.Presence, c.Count)):
        if (node.kind in ("diagnosis_presence", "diagnosis_count")) != (
            payload.role is not None
        ):
            raise SourceError("diagnosis role and procedure payload conflict")
        if payload.role == "source_unspecified" and node.status != "unsupported":
            raise SourceError("unspecified diagnosis role cannot be promoted")
    if (
        isinstance(payload, c.ClinicalMeasure)
        and payload.unit_or_scale is None
        and node.status != "unsupported"
    ):
        raise SourceError("missing clinical unit cannot be promoted")
    if isinstance(payload, c.Expression):
        arity = len(payload.children)
        if not 1 <= arity <= MAX_CHILDREN or (
            node.kind in ("all", "any") and arity < 2
        ):
            raise SourceError("condition expression arity is invalid")
        if node.kind == "not" and arity != 1:
            raise SourceError("condition negation arity is invalid")
        if node.kind == "when" and arity != 2:
            raise SourceError(
                "condition guard requires ordered trigger and consequence"
            )
        if node.kind != "when" and payload.member_rows:
            raise SourceError("membership guard binding belongs only to when")
    _scalar_bounds(payload)


def _scalar_bounds(payload: c.Payload) -> None:
    if isinstance(payload, c.Age) and not 0 <= payload.threshold <= 2400:
        raise SourceError("condition age threshold exceeds its bound")
    if isinstance(payload, c.EpisodeDuration) and (
        not 1 <= len(payload.bounds) <= 2
        or any(not 0 <= b.days <= 3660 for b in payload.bounds)
    ):
        raise SourceError("condition episode duration is invalid")
    if isinstance(payload, c.WardDuration) and not 0 <= payload.days <= 3660:
        raise SourceError("condition ward duration exceeds its bound")
    if isinstance(payload, c.ProcedureDuration) and not (
        1 <= payload.threshold_hours <= 8760
        and 1 <= payload.qualifying_occurrences <= 1000
    ):
        raise SourceError("condition procedure duration is invalid")
    if isinstance(payload, c.ListCoverage) and not (
        2 <= payload.minimum_distinct_lists <= len(payload.selector_ids) <= MAX_CHILDREN
        and 2 <= payload.minimum_procedures <= 1000
    ):
        raise SourceError("condition list coverage is invalid")
    if isinstance(payload, c.DateRelation) and (
        len(payload.occurrence_sets) != 2
        or any(not 1 <= len(group) <= MAX_CHILDREN for group in payload.occurrence_sets)
    ):
        raise SourceError("condition date relation lacks two occurrence sets")
    if isinstance(payload, c.Laterality) and (
        payload.required_sites != ("right_breast", "left_breast")
        or payload.minimum_per_site != 1
    ):
        raise SourceError("condition laterality allocation is invalid")
    if isinstance(payload, c.DischargeMode) and (
        not 1 <= len(payload.allowed_reported_codes) <= 16
        or len(set(payload.allowed_reported_codes))
        != len(payload.allowed_reported_codes)
        or any(not 1 <= code <= 99 for code in payload.allowed_reported_codes)
    ):
        raise SourceError("condition discharge modes are invalid")
    if (
        isinstance(payload, c.ClinicalMeasure)
        and re.fullmatch(
            r"[IVX]{1,8}|[0-9]{1,6}(?:[.,][0-9]{1,6})?", payload.exact_threshold
        )
        is None
    ):
        raise SourceError("condition clinical threshold is invalid")
    if (
        isinstance(payload, c.ExternalDiseaseCode)
        and re.fullmatch(r"[0-9]{1,12}", payload.exact_code) is None
    ):
        raise SourceError("condition external disease identity is invalid")


def validate_characteristics(sheet: c.Characteristics) -> None:
    if (
        not 1 <= len(sheet.evidence) <= MAX_ROWS
        or len(sheet.conditions) > MAX_CONDITIONS
        or len(sheet.selectors) > MAX_SELECTORS
    ):
        raise SourceError("characteristics collection exceeds its bound")
    evidence = {item.row: item for item in sheet.evidence}
    lists = {item.identity: item for item in sheet.lists}
    selectors = {item.identity: item for item in sheet.selectors}
    nodes = {item.identity: item for item in sheet.conditions}
    groups = {item.code: item for item in sheet.groups}
    if any(
        len(mapping) != len(items)
        for mapping, items in (
            (evidence, sheet.evidence),
            (lists, sheet.lists),
            (selectors, sheet.selectors),
            (nodes, sheet.conditions),
            (groups, sheet.groups),
        )
    ):
        raise SourceError("characteristics identity is duplicated")
    if not 1 <= len(groups) <= 2000 or not 1 <= len(lists) <= 3000:
        raise SourceError("characteristics group/list count exceeds bounds")
    if any(not 1 <= e.row <= MAX_ROWS or len(e.text) > 8192 for e in sheet.evidence):
        raise SourceError("characteristics cell exceeds its bound")
    if (
        any(not 1 <= n.identity <= MAX_CONDITIONS for n in sheet.conditions)
        or any(not 1 <= s.identity <= MAX_SELECTORS for s in sheet.selectors)
        or any(not 1 <= li.identity <= 3000 for li in sheet.lists)
    ):
        raise SourceError("characteristics identity exceeds its bound")
    members = _lists(sheet, evidence)
    _selectors(sheet, evidence, lists, selectors)
    _graph(sheet, evidence, selectors, nodes, groups, members)


def _lists(
    sheet: c.Characteristics, evidence: dict[int, c.Evidence]
) -> dict[int, c.Member]:
    members: dict[int, c.Member] = {}
    for item in sheet.lists:
        source_rows((item.marker_row, *item.naming_rows), evidence)
        if evidence[item.marker_row].category != "marker" or not item.members:
            raise SourceError("characteristics list marker or members missing")
        for member in item.members:
            source_rows((member.row,), evidence)
            if (
                member.row in members
                or evidence[member.row].category != "member"
                or CODE.fullmatch(member.code) is None
            ):
                raise SourceError("characteristics membership identity is invalid")
            members[member.row] = member
            text = evidence[member.row].text.strip()
            source_code = re.match("(" + CODE.pattern + r")(?=[*+\s(])", text)
            source_marks = tuple(
                re.findall(r"\(\*[0-9]{1,2}\)|(?<!\*)\*{1,4}(?!\*)", text)
            )
            if (
                source_code is None
                or source_code[1] != member.code
                or source_marks != member.marks
            ):
                raise SourceError(
                    "characteristics member code or marks conflict with source cell"
                )
    if {e.row for e in sheet.evidence if e.category == "member"} != set(members):
        raise SourceError("characteristics member evidence is not completely allocated")
    return members


def _selectors(
    sheet: c.Characteristics,
    evidence: dict[int, c.Evidence],
    lists: dict[int, c.DirectionalList],
    selectors: dict[int, c.Selector],
) -> None:
    depths: dict[int, int] = {}
    for selector in sheet.selectors:
        source_rows(selector.source_rows, evidence)
        body = selector.body
        if isinstance(body, c.ListSelector):
            if not 1 <= len(body.list_ids) <= MAX_CHILDREN or any(
                t not in lists or lists[t].system != body.system for t in body.list_ids
            ):
                raise SourceError("characteristics list selector is unresolved")
        elif isinstance(body, c.CodeSelector):
            if (
                not 1 <= len(body.codes) <= MAX_CHILDREN
                or len(set(body.codes)) != len(body.codes)
                or any(CODE.fullmatch(code) is None for code in body.codes)
            ):
                raise SourceError("characteristics literal selector is invalid")
        elif isinstance(body, c.UnionSelector):
            if not 2 <= len(body.selector_ids) <= MAX_CHILDREN or any(
                t not in depths or selectors[t].body.system != body.system
                for t in body.selector_ids
            ):
                raise SourceError(
                    "characteristics selector union is cyclic or unresolved"
                )
        elif CODE.fullmatch(body.category) is None:
            raise SourceError("characteristics family category is invalid")
        depth = (
            1 + max((depths[t] for t in body.selector_ids), default=0)
            if isinstance(body, c.UnionSelector)
            else 1
        )
        if depth > MAX_DEPTH:
            raise SourceError("characteristics selector depth exceeds bound")
        depths[selector.identity] = depth


def _graph(
    sheet: c.Characteristics,
    evidence: dict[int, c.Evidence],
    selectors: dict[int, c.Selector],
    nodes: dict[int, c.Condition],
    groups: dict[str, c.Group],
    members: dict[int, c.Member],
) -> None:
    depths: dict[int, int] = {}
    incoming: set[int] = set()
    for node in sheet.conditions:
        source_rows(node.source_rows, evidence)
        _node(node, selectors)
        if node.group not in groups:
            raise SourceError("condition group is unresolved")
        targets = children(node)
        if (
            len(targets) > MAX_CHILDREN
            or len(set(targets)) != len(targets)
            or any(t not in depths or nodes[t].group != node.group for t in targets)
        ):
            raise SourceError("condition graph is cyclic, duplicated or cross-group")
        if node.status == "proposed" and any(
            nodes[t].status == "unsupported" for t in targets
        ):
            raise SourceError("unsupported child cannot be hidden by proposed parent")
        depth = 1 + max((depths[t] for t in targets), default=0)
        if depth > MAX_DEPTH:
            raise SourceError("condition graph exceeds depth bound")
        depths[node.identity] = depth
        incoming.update(targets)
        _guard(node, nodes, selectors, members, evidence)
    roots: set[int] = set()
    for group in sheet.groups:
        source_rows((group.title_row,), evidence)
        if evidence[group.title_row].category != "title" or not group.roots:
            raise SourceError("characteristics group lacks title or reference roots")
        if len(set(group.roots)) != len(group.roots) or len(group.roots) > MAX_CHILDREN:
            raise SourceError("condition roots are repeated or unbounded")
        if any(t not in nodes or nodes[t].group != group.code for t in group.roots):
            raise SourceError("condition root belongs to another group")
        roots.update(group.roots)
    if roots & incoming or roots | incoming != set(nodes):
        raise SourceError(
            "condition reference roots are inconsistent or nodes unreachable"
        )


def _guard(
    node: c.Condition,
    nodes: dict[int, c.Condition],
    selectors: dict[int, c.Selector],
    members: dict[int, c.Member],
    evidence: dict[int, c.Evidence],
) -> None:
    if isinstance(node.payload, c.Expression) and node.payload.member_rows:
        rows = node.payload.member_rows
        if not 1 <= len(rows) <= MAX_SOURCE_ROWS or any(
            r not in members
            or r not in node.source_rows
            or evidence[r].context != node.group
            for r in rows
        ):
            raise SourceError("condition guard member evidence is unresolved")
        trigger = nodes[node.payload.children[0]]
        if not isinstance(trigger.payload, c.Presence):
            raise SourceError("membership guard trigger is not a presence fragment")
        selected = selectors[trigger.payload.selector_id]
        if (
            not isinstance(selected.body, c.CodeSelector)
            or set(selected.body.codes) != {members[r].code for r in rows}
            or not set(rows) <= set(trigger.source_rows)
            or not set(rows) <= set(selected.source_rows)
        ):
            raise SourceError(
                "membership guard trigger does not bind exact source occurrences"
            )
        annotations = [
            re.match(r"^\s*(\(\*[0-9]{1,2}\)|\*{1,4})", evidence[r].text)
            for r in node.source_rows
            if evidence[r].category == "label"
        ]
        marks = {match[1] for match in annotations if match is not None}
        if any(not marks.intersection(members[r].marks) for r in rows):
            raise SourceError("membership guard annotation does not match source mark")
