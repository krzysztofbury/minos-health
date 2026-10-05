"""Logical attachment-9 graphs with explicit remapping of every local reference."""

import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict
from graphlib import CycleError, TopologicalSorter
from typing import Literal, cast

from minos.domain import jgp_conditions as c
from minos.domain.release_diff import DiffInputError
from minos.store.diff_records import Projection, canonical, fingerprint, rows
from minos.store.jgp_conditions_build import load_characteristics


def project_conditions(
    connection: sqlite3.Connection, release: str, output: Projection
) -> None:
    catalogs = cast(
        list[tuple[str]],
        rows(
            connection,
            "SELECT catalog FROM characteristics_sheet WHERE release_id=? ORDER BY catalog",
            (release,),
        ),
    )
    for (catalog,) in catalogs:
        project_sheet(
            load_characteristics(connection, release, cast(Catalog, catalog)), output
        )
        for system, code, pinned, status in cast(
            list[tuple[str, str, str, str]],
            rows(
                connection,
                """
            SELECT system,code,reference_release_id,correspondence FROM characteristics_code
            WHERE release_id=? AND catalog=? ORDER BY system,code""",
                (release, catalog),
            ),
        ):
            output.add(
                "dictionary_correspondence",
                (catalog, system, code),
                {"reference_release_id": pinned, "status": status},
            )
        for code, catalog_pin, status in cast(
            list[tuple[str, str | None, str]],
            rows(
                connection,
                """
            SELECT code,catalog_release_id,correspondence FROM characteristics_group_catalog
            WHERE release_id=? AND catalog=? ORDER BY code""",
                (release, catalog),
            ),
        ):
            output.add(
                "catalog_correspondence",
                (catalog, code),
                {"catalog_release_id": catalog_pin, "status": status},
            )


type Catalog = Literal["1a", "1ae"]


def project_sheet(sheet: c.Characteristics, output: Projection) -> None:
    evidence = {item.row: item for item in sheet.evidence}
    list_keys: dict[int, str] = {}
    declarations: dict[tuple[str, ...], list[object]] = defaultdict(list)
    declaration_counts: Counter[tuple[str, ...]] = Counter()
    occurrences: dict[int, object] = {}
    for item in sheet.lists:
        declaration = (sheet.catalog, item.system, item.context, item.label)
        declaration_counts[declaration] += 1
        key = canonical((*declaration, declaration_counts[declaration]))
        list_keys[item.identity] = key
        members: list[object] = []
        counts: Counter[str] = Counter()
        for member in item.members:
            definition = {
                "code": member.code,
                "marks": member.marks,
                "text": evidence[member.row].text,
            }
            digest = fingerprint(definition)
            counts[digest] += 1
            occurrences[member.row] = {
                "list": key,
                **definition,
                "occurrence": counts[digest],
            }
            members.append(definition)
        declarations[declaration].append(members)
    for declaration_key, population in sorted(declarations.items()):
        output.add(
            "directional_list",
            declaration_key,
            {
                "declarations": population,
                "correspondence": "unique_label"
                if len(population) == 1
                else "ambiguous_source_ordered_population_not_semantic_identity",
            },
        )
    selectors, selector_definitions = _selectors(sheet, list_keys, output)
    nodes, node_definitions = _nodes(sheet, selectors, occurrences, output)
    _groups(sheet, nodes, node_definitions, selector_definitions, evidence, output)
    associations: dict[str, list[object]] = defaultdict(list)
    for node in sheet.conditions:
        associations["node:" + nodes[node.identity]].append(
            [
                (
                    evidence[row].section,
                    evidence[row].context,
                    evidence[row].category,
                    evidence[row].text,
                )
                for row in node.source_rows
            ]
        )
    for selector in sheet.selectors:
        associations["selector:" + selectors[selector.identity]].append(
            [
                (
                    evidence[row].section,
                    evidence[row].context,
                    evidence[row].category,
                    evidence[row].text,
                )
                for row in selector.source_rows
            ]
        )
    for ref, associated in sorted(associations.items()):
        output.add(
            "condition_evidence_binding",
            (sheet.catalog, ref),
            sorted(associated, key=canonical),
        )
    cells: dict[tuple[str, ...], list[object]] = defaultdict(list)
    for cell in sheet.evidence:
        cells[(sheet.catalog, cell.context or "", cell.category)].append(
            (cell.section, cell.text)
        )
    for cell_key, population in sorted(cells.items()):
        output.add("condition_evidence", cell_key, population)
    output.add(
        "evidence_location",
        (sheet.catalog, "conditions"),
        {
            "rows": [item.row for item in sheet.evidence],
            "lists": [
                (item.identity, item.marker_row, item.naming_rows)
                for item in sheet.lists
            ],
            "selectors": [
                (selectors[item.identity], item.identity, item.source_rows)
                for item in sheet.selectors
            ],
            "nodes": [
                (nodes[item.identity], item.identity, item.source_rows)
                for item in sheet.conditions
            ],
        },
    )


def _order(graph: dict[int, tuple[int, ...]]) -> tuple[int, ...]:
    try:
        order = tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        raise DiffInputError("cyclic logical condition graph") from None
    if len(order) != len(graph):
        raise DiffInputError("unresolved logical condition reference")
    return order


def _selectors(
    sheet: c.Characteristics, lists: dict[int, str], output: Projection
) -> tuple[dict[int, str], dict[str, dict[str, object]]]:
    indexed = {item.identity: item for item in sheet.selectors}
    graph = {
        identity: item.body.selector_ids
        if isinstance(item.body, c.UnionSelector)
        else ()
        for identity, item in indexed.items()
    }
    refs: dict[int, str] = {}
    definitions: dict[str, dict[str, object]] = {}
    for identity in _order(graph):
        output.budget.check()
        body = indexed[identity].body
        value: dict[str, object] = {"kind": body.kind, "system": body.system}
        if isinstance(body, c.ListSelector):
            value["lists"] = [lists[key] for key in body.list_ids]
        elif isinstance(body, c.UnionSelector):
            value["selectors"] = [refs[key] for key in body.selector_ids]
        elif isinstance(body, c.CodeSelector):
            value["codes"] = body.codes
        else:
            value.update(category=body.category, expansion_policy=body.expansion_policy)
        digest = fingerprint(value)
        refs[identity] = digest
        definitions[digest] = value
    return refs, definitions


def _dependencies(node: c.Condition) -> tuple[int, ...]:
    payload = node.payload
    if isinstance(payload, c.Expression):
        return payload.children
    if isinstance(payload, c.Unsupported):
        return payload.fragments
    if isinstance(payload, c.Count):
        return payload.included_node_ids
    return ()


def _nodes(
    sheet: c.Characteristics,
    selectors: dict[int, str],
    occurrences: dict[int, object],
    output: Projection,
) -> tuple[dict[int, str], dict[str, dict[str, object]]]:
    indexed = {item.identity: item for item in sheet.conditions}
    graph = {identity: _dependencies(item) for identity, item in indexed.items()}
    refs: dict[int, str] = {}
    definitions: dict[str, dict[str, object]] = {}
    for identity in _order(graph):
        output.budget.check()
        node = indexed[identity]
        payload = cast(dict[str, object], asdict(node.payload))
        if "selector_id" in payload:
            payload["selector"] = selectors[cast(int, payload.pop("selector_id"))]
        if "selector_ids" in payload:
            payload["selectors"] = [
                selectors[key]
                for key in cast(tuple[int, ...], payload.pop("selector_ids"))
            ]
        if isinstance(node.payload, c.DateRelation):
            payload["occurrence_sets"] = [
                [selectors[key] for key in group]
                for group in node.payload.occurrence_sets
            ]
        for name in ("children", "fragments", "included_node_ids"):
            if name in payload:
                payload[name] = [
                    refs[key] for key in cast(tuple[int, ...], payload[name])
                ]
        if isinstance(node.payload, c.Expression):
            payload["member_rows"] = [
                occurrences[row] for row in node.payload.member_rows
            ]
        value: dict[str, object] = {
            "group": node.group,
            "kind": node.kind,
            "payload": payload,
            "status": node.status,
            "reason": node.reason,
            "evidence_type": node.evidence_type,
        }
        digest = fingerprint(value)
        refs[identity] = digest
        definitions[digest] = value
    return refs, definitions


def _groups(
    sheet: c.Characteristics,
    nodes: dict[int, str],
    definitions: dict[str, dict[str, object]],
    selectors: dict[str, dict[str, object]],
    evidence: dict[int, c.Evidence],
    output: Projection,
) -> None:
    grouped: dict[str, list[str]] = defaultdict(list)
    for item in sheet.conditions:
        grouped[item.group].append(nodes[item.identity])
    for group in sheet.groups:
        group_nodes = grouped[group.code]
        # Include the selector DAG once, not recursive expansion of shared nodes.
        required: set[str] = set()
        for ref in group_nodes:
            payload = cast(dict[str, object], definitions[ref]["payload"])
            if "selector" in payload:
                required.add(cast(str, payload["selector"]))
            required.update(cast(list[str], payload.get("selectors", [])))
            for selection in cast(list[list[str]], payload.get("occurrence_sets", [])):
                required.update(selection)
        pending = list(required)
        for _ in range(len(selectors)):
            if not pending:
                break
            ref = pending.pop()
            for child in cast(list[str], selectors[ref].get("selectors", [])):
                if child not in required:
                    required.add(child)
                    pending.append(child)
        if pending:
            raise DiffInputError("selector traversal exceeds inventory")
        output.add(
            "condition_graph",
            (sheet.catalog, group.code),
            {
                "title": evidence[group.title_row].text,
                "roots": [nodes[key] for key in group.roots],
                "nodes": [
                    {"ref": ref, **definitions[ref]} for ref in sorted(set(group_nodes))
                ],
                "node_occurrences": dict(sorted(Counter(group_nodes).items())),
                "selectors": [
                    {"ref": ref, **selectors[ref]} for ref in sorted(required)
                ],
            },
        )
