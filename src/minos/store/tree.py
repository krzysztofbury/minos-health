"""Bounded, read-only browsing of one release's source hierarchy."""

import json
import sqlite3
from dataclasses import dataclass
from typing import Final, cast

from minos.domain.search import SearchInputError
from minos.store.query_budget import query_budget
from minos.store.reference_build import reference_sql

STRUCTURAL_KINDS: Final = frozenset(("range", "classification"))
MAX_CHILDREN: Final = 400
# Every ICD-11 index term is shown (NE61 has 484); ND text is not trimmed (#53).
MAX_NOTES: Final = 500
MAX_UNPLACED: Final = 400
MAX_DEPTH: Final = 8
# CZS and RSK attribute names stay source-faithful; only the label is translated.
ATTRIBUTE_LABELS: Final = {
    "EN": "Nazwa angielska",
    "status": "Status w źródle",
    "data_od": "Obowiązuje od",
    "data_do": "Obowiązuje do",
    "wlaczenia": "Obejmuje",
    "wylaczenia": "Nie obejmuje",
    "opis": "Opis",
    "uri": "Identyfikator WHO (URI)",
    "annotationPL": "Uwaga",
}
NOTE_KINDS: Final = {
    "Obejmuje": "includes",
    "Nie obejmuje": "excludes",
    "note": "note",
}

type Row = tuple[int, int, str, str, str, str | None]


@dataclass(frozen=True)
class Hierarchy:
    release_id: str
    hierarchy_release_id: str

    @property
    def supporting(self) -> bool:
        return self.release_id != self.hierarchy_release_id


def resolve_hierarchy(connection: sqlite3.Connection, release_id: str) -> Hierarchy:
    row = cast(
        tuple[str] | None,
        connection.execute(
            "SELECT value FROM build_metadata WHERE key='supporting_sources'"
        ).fetchone(),
    )
    supporting = cast(dict[str, str], json.loads(row[0])) if row else {}
    exists = cast(
        tuple[int] | None,
        connection.execute(
            "SELECT 1 FROM source_release WHERE id=?", (release_id,)
        ).fetchone(),
    )
    if release_id in supporting or exists is None:
        raise SearchInputError("release is not a selectable catalog")
    context = [key for key, owner in supporting.items() if owner == release_id]
    if len(context) > 1:
        raise SearchInputError("release has ambiguous supporting hierarchies")
    return Hierarchy(release_id, context[0] if context else release_id)


def tree_roots(
    connection: sqlite3.Connection, hierarchy: Hierarchy
) -> dict[str, object]:
    """Unbudgeted startup work: one release scan whose answer is immutable."""
    rows = cast(
        list[Row],
        connection.execute(
            reference_sql("queries/tree_roots.sql"),
            {"release_id": hierarchy.hierarchy_release_id, "row_limit": MAX_CHILDREN},
        ).fetchall(),
    )
    children = _flatten(connection, hierarchy, rows)
    answer = _listing(connection, hierarchy, None, children)
    if hierarchy.supporting:
        unplaced = cast(
            list[tuple[int, str, str]],
            connection.execute(
                reference_sql("queries/tree_unplaced.sql"),
                {
                    "release_id": hierarchy.release_id,
                    "hierarchy_release_id": hierarchy.hierarchy_release_id,
                    "row_limit": MAX_UNPLACED + 1,
                },
            ).fetchall(),
        )
        attributes = _attributes(
            connection, hierarchy.release_id, [row[0] for row in unplaced]
        )
        answer["unplaced"] = [
            {
                "code": code,
                "label": label,
                "canonical": True,
                "attributes": attributes.get(ordinal, []),
            }
            for ordinal, code, label in unplaced[:MAX_UNPLACED]
        ]
        answer["unplaced_truncated"] = len(unplaced) > MAX_UNPLACED
    return answer


def tree_node(
    connection: sqlite3.Connection, hierarchy: Hierarchy, ordinal: int
) -> dict[str, object]:
    with query_budget(connection) as budget:
        row = cast(
            tuple[int, str, str, str, str | None] | None,
            connection.execute(
                reference_sql("queries/tree_node.sql"),
                {"release_id": hierarchy.hierarchy_release_id, "ordinal": ordinal},
            ).fetchone(),
        )
        if row is None or row[1] not in STRUCTURAL_KINDS:
            raise SearchInputError("tree node is not in the release")
        budget.check()
        children = _flatten(
            connection,
            hierarchy,
            _children(connection, hierarchy.hierarchy_release_id, [ordinal]),
        )
        budget.check()
        answer = _listing(connection, hierarchy, row, children)
        budget.check()
        return answer


def _children(
    connection: sqlite3.Connection, release_id: str, parents: list[int]
) -> list[Row]:
    return cast(
        list[Row],
        connection.execute(
            reference_sql("queries/tree_children.sql"),
            {
                "release_id": release_id,
                "parents": json.dumps(parents),
                "row_limit": MAX_CHILDREN * 4,
            },
        ).fetchall(),
    )


def _flatten(
    connection: sqlite3.Connection, hierarchy: Hierarchy, rows: list[Row]
) -> list[Row]:
    """Expose structural nodes through anonymous source containers, in source order."""
    result: list[Row] = []
    frontier = rows
    for _depth in range(MAX_DEPTH):
        containers = [row[1] for row in frontier if row[2] == "container"]
        result.extend(row for row in frontier if row[2] in STRUCTURAL_KINDS)
        if not containers or len(result) >= MAX_CHILDREN:
            break
        frontier = _children(connection, hierarchy.hierarchy_release_id, containers)
    return sorted(result, key=lambda row: row[1])[:MAX_CHILDREN]


def _listing(
    connection: sqlite3.Connection,
    hierarchy: Hierarchy,
    node: tuple[int, str, str, str, str | None] | None,
    children: list[Row],
) -> dict[str, object]:
    release = hierarchy.hierarchy_release_id
    parents_with_children = _owners(connection, release, children)
    codes = [_code(row[5], row[3]) for row in children]
    if node is not None:
        codes.append(_code(node[4], node[2]))
    canonical = _canonical(connection, hierarchy, [code for code in codes if code])
    answer: dict[str, object] = {
        "children": [
            {
                "ordinal": row[1],
                "code": _code(row[5], row[3]),
                "label": canonical.get(_code(row[5], row[3]) or "", (0, row[4]))[1],
                "kind": row[2],
                "has_children": row[1] in parents_with_children,
                "canonical": not hierarchy.supporting
                or (_code(row[5], row[3]) or "") in canonical,
            }
            for row in children
        ],
        "truncated": len(children) >= MAX_CHILDREN,
    }
    if node is None:
        answer["node"] = None
        return answer
    code = _code(node[4], node[2])
    canonical_ordinal = canonical.get(code or "", (0, ""))[0]
    attributes = (
        _attributes(connection, hierarchy.release_id, [canonical_ordinal]).get(
            canonical_ordinal, []
        )
        if hierarchy.supporting and canonical_ordinal
        else _attributes(connection, release, [node[0]]).get(node[0], [])
    )
    notes = _notes(connection, hierarchy, node[0])
    # Prefer canonical inclusion/exclusion text; workbook copies stay for gaps only.
    canonical_kinds = {
        {"wlaczenia": "includes", "wylaczenia": "excludes"}.get(item["key"], "")
        for item in attributes
    }
    notes = [
        note
        for note in notes
        if note["source"] == "canonical" or note["kind"] not in canonical_kinds
    ]
    answer["node"] = {
        "ordinal": node[0],
        "code": code,
        "label": canonical.get(code or "", (0, node[3]))[1],
        "kind": node[1],
        "canonical": not hierarchy.supporting or bool(canonical_ordinal),
        "attributes": attributes,
        "notes": notes,
        "path": _path(connection, hierarchy, node[0], canonical),
    }
    return answer


def _owners(
    connection: sqlite3.Connection, release_id: str, children: list[Row]
) -> set[int]:
    # Map flattened grandchildren back to the listed child that owns them.
    listed = {row[1] for row in children}
    parent_of: dict[int, int] = {}
    frontier = _children(connection, release_id, sorted(listed))
    owners: set[int] = set()
    for _depth in range(MAX_DEPTH):
        next_containers: list[int] = []
        for parent, ordinal, kind, *_rest in frontier:
            owner = parent if parent in listed else parent_of.get(parent)
            if owner is None:
                continue
            if kind in STRUCTURAL_KINDS:
                owners.add(owner)
            elif kind == "container":
                parent_of[ordinal] = owner
                next_containers.append(ordinal)
        if not next_containers:
            break
        frontier = _children(connection, release_id, next_containers)
    return owners


def _notes(
    connection: sqlite3.Connection, hierarchy: Hierarchy, ordinal: int
) -> list[dict[str, str]]:
    release = hierarchy.hierarchy_release_id
    notes: list[tuple[int, str, str, str]] = []
    frontier = _children(connection, release, [ordinal])
    for depth in range(MAX_DEPTH):
        expand = [
            row[1] for row in frontier if row[2] in ("container", "text", "modifier")
        ]
        notes.extend(
            (row[1], row[2], row[3], row[4])
            for row in frontier
            if row[2] in ("text", "modifier") and row[4].strip()
        )
        if not expand or len(notes) >= MAX_NOTES or depth == MAX_DEPTH - 1:
            break
        frontier = _children(connection, release, expand)
    notes = sorted(notes)[:MAX_NOTES]
    attributes = _attributes(connection, release, [note[0] for note in notes])
    result: list[dict[str, str]] = []
    for note_ordinal, kind, source_code, text in notes:
        evidence = {
            item["key"]: item["value"] for item in attributes.get(note_ordinal, [])
        }
        label = evidence.get("evidence_kind", "").strip().rstrip(":").strip()
        if label == "conflicting_title_or_code_form":
            continue
        result.append(
            {
                "kind": "modifier"
                if kind == "modifier"
                else NOTE_KINDS.get(label, "term"),
                "code": source_code,
                "text": text,
                "source": "supporting" if hierarchy.supporting else "canonical",
            }
        )
    return result


def _path(
    connection: sqlite3.Connection,
    hierarchy: Hierarchy,
    ordinal: int,
    canonical: dict[str, tuple[int, str]],
) -> list[dict[str, object]]:
    rows = cast(
        list[tuple[int, int, str, str, str]],
        connection.execute(
            reference_sql("queries/ancestors.sql"),
            {
                "release_id": hierarchy.hierarchy_release_id,
                "ordinal": ordinal,
                "relation_type": "contains",
                "max_depth": 32,
                "row_limit": 33,
            },
        ).fetchall(),
    )
    codes = cast(
        dict[int, str],
        dict(
            connection.execute(
                reference_sql("queries/tree_codes.sql"),
                {
                    "ordinals": json.dumps([row[0] for row in rows]),
                    "release_id": hierarchy.hierarchy_release_id,
                },
            ).fetchall()
        ),
    )
    path: list[dict[str, object]] = []
    for node_ordinal, _depth, kind, source_code, text in sorted(
        ((row[0], row[1], row[4], row[2], row[3]) for row in rows),
        key=lambda row: -row[1],
    ):
        if kind not in STRUCTURAL_KINDS:
            continue
        code = codes.get(node_ordinal) or source_code or None
        missing = (0, text)
        path.append(
            {
                "ordinal": node_ordinal,
                "code": code,
                "label": canonical.get(code or "", missing)[1]
                if hierarchy.supporting
                else text,
            }
        )
    return path


def _canonical(
    connection: sqlite3.Connection, hierarchy: Hierarchy, codes: list[str]
) -> dict[str, tuple[int, str]]:
    if not hierarchy.supporting or not codes:
        return {}
    rows = cast(
        list[tuple[str, int, str]],
        connection.execute(
            reference_sql("queries/tree_canonical.sql"),
            {
                "release_id": hierarchy.release_id,
                "codes": json.dumps(sorted(set(codes))),
                "row_limit": MAX_CHILDREN + 1,
            },
        ).fetchall(),
    )
    return {code: (ordinal, label) for code, ordinal, label in rows}


def _attributes(
    connection: sqlite3.Connection, release_id: str, ordinals: list[int]
) -> dict[int, list[dict[str, str]]]:
    if not ordinals:
        return {}
    rows = cast(
        list[tuple[int, str, str]],
        connection.execute(
            reference_sql("queries/tree_attributes.sql"),
            {
                "release_id": release_id,
                "ordinals": json.dumps(ordinals),
                "row_limit": 16 * len(ordinals),
            },
        ).fetchall(),
    )
    result: dict[int, list[dict[str, str]]] = {}
    for ordinal, name, text in rows:
        if name == "evidence_kind":
            label = "Rodzaj w źródle"
        elif name in ATTRIBUTE_LABELS:
            label = ATTRIBUTE_LABELS[name]
        else:
            continue
        result.setdefault(ordinal, []).append(
            {"name": label, "key": name, "value": text}
        )
    return result


def _code(lookup_code: str | None, source_code: str) -> str | None:
    return lookup_code or source_code or None


def find_code(
    connection: sqlite3.Connection, hierarchy: Hierarchy, code: str
) -> int | None:
    row = cast(
        tuple[int] | None,
        connection.execute(
            reference_sql("queries/tree_find_code.sql"),
            {"release_id": hierarchy.hierarchy_release_id, "code": code},
        ).fetchone(),
    )
    return row[0] if row else None


def all_codes(connection: sqlite3.Connection, hierarchy: Hierarchy) -> list[str]:
    """Unbudgeted startup work; bounded by the sitemap protocol's 50,000 URLs."""
    rows = cast(
        list[tuple[str]],
        connection.execute(
            reference_sql("queries/tree_all_codes.sql"),
            {"release_id": hierarchy.hierarchy_release_id, "row_limit": 49_000},
        ).fetchall(),
    )
    return [row[0] for row in rows]
