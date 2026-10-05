"""Code-anchored terminology and anonymous structural populations."""

import sqlite3
from collections import Counter, defaultdict
from graphlib import CycleError, TopologicalSorter
from typing import cast

from minos.domain.release_diff import DiffInputError
from minos.store.diff_records import Projection, canonical, fingerprint, rows


def project_terminology(
    connection: sqlite3.Connection, release: str, output: Projection
) -> None:
    nodes = cast(
        list[tuple[int, str, str, str, str | None]],
        rows(
            connection,
            """
        SELECT n.ordinal,n.source_code,n.source_text,n.kind,c.lookup_code
        FROM reference_node n LEFT JOIN concept c
        ON c.source_release_id=n.source_release_id AND c.node_ordinal=n.ordinal
        WHERE n.source_release_id=? ORDER BY n.ordinal""",
            (release,),
        ),
    )
    attributes: dict[int, list[tuple[str, str]]] = defaultdict(list)
    for ordinal, name, text in cast(
        list[tuple[int, str, str]],
        rows(
            connection,
            """
        SELECT node_ordinal,name,source_text FROM reference_node_attribute
        WHERE source_release_id=? ORDER BY node_ordinal,source_order""",
            (release,),
        ),
    ):
        attributes[ordinal].append((name, text))
    descriptions: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for ordinal, language, kind, text in cast(
        list[tuple[int, str, str, str]],
        rows(
            connection,
            """
        SELECT node_ordinal,language,description_type,source_text FROM concept_description
        WHERE source_release_id=? ORDER BY node_ordinal,language,description_type""",
            (release,),
        ),
    ):
        descriptions[ordinal].append((language, kind, text))
    parents: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for parent, child, relation in cast(
        list[tuple[int, int, str]],
        rows(
            connection,
            """
        SELECT parent_ordinal,child_ordinal,relation_type FROM reference_relation
        WHERE source_release_id=? ORDER BY child_ordinal,parent_ordinal,relation_type""",
            (release,),
        ),
    ):
        parents[child].append((parent, relation))
    _project_nodes(nodes, attributes, descriptions, parents, output)


def _project_nodes(
    nodes: list[tuple[int, str, str, str, str | None]],
    attributes: dict[int, list[tuple[str, str]]],
    descriptions: dict[int, list[tuple[str, str, str]]],
    parents: dict[int, list[tuple[int, str]]],
    output: Projection,
) -> None:
    indexed = {node[0]: node for node in nodes}
    graph = {
        ordinal: tuple(parent for parent, _ in parents[ordinal]) for ordinal in indexed
    }
    try:
        order = tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        raise DiffInputError("cyclic terminology hierarchy") from None
    anchors: dict[int, str] = {}
    anonymous: dict[str, dict[str, object]] = {}
    populations: Counter[str] = Counter()
    for ordinal in order:
        output.budget.check()
        if ordinal not in indexed:
            raise DiffInputError("unresolved terminology hierarchy")
        _, code, text, kind, lookup = indexed[ordinal]
        links = sorted(
            (anchors[parent], relation) for parent, relation in parents[ordinal]
        )
        locations = [
            (name, value)
            for name, value in attributes[ordinal]
            if name in ("cell", "sheet", "worksheet")
        ]
        logical_attributes = [
            (name, value)
            for name, value in attributes[ordinal]
            if name not in ("cell", "sheet", "worksheet")
        ]
        definition: dict[str, object] = {
            "source_code": code,
            "text": text,
            "kind": kind,
            "attributes": logical_attributes,
        }
        if lookup is not None:
            anchors[ordinal] = canonical(("code", lookup))
            output.add(
                "concept",
                (lookup,),
                {**definition, "descriptions": descriptions[ordinal]},
            )
            output.add("hierarchy", (lookup,), links)
            output.add("evidence_location", (lookup,), locations)
        else:
            definition["parents"] = links
            base = fingerprint(definition)
            populations[base] += 1
            occurrence = populations[base]
            # Identical anonymous siblings are distinct source occurrences.
            # Matching their semantics would require evidence we do not have.
            digest = fingerprint((base, occurrence))
            anchors[ordinal] = canonical(("anonymous", digest))
            anonymous[digest] = {
                **definition,
                "occurrence": occurrence,
                "correspondence": "source_ordered_occurrence_not_semantic_identity",
            }
            output.add("evidence_location", ("anonymous", digest), locations)
    for digest, definition in sorted(anonymous.items()):
        output.add(
            "anonymous_structure",
            (digest,),
            definition,
        )
    output.add("source_order", ("nodes",), [anchors[node[0]] for node in nodes])
