"""Grouper inputs read from one opened, verified reference bundle.

The dataset manifest is the reviewed composite: it pins the catalog,
attachment 9, terminologies and the algorithm notice together. These readers
re-check that the dated selections agree instead of trusting the pairing.
"""

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import cast

from minos.domain import jgp_conditions as c
from minos.domain.grouper import AlgorithmData, AlgorithmIdentity, GrouperError
from minos.domain.grouper_candidates import (
    Catalog,
    DirectionalIndex,
    DirectionalRequirement,
    FactKind,
    GroupDirections,
    GroupingSources,
)
from minos.domain.jgp_conditions_validation import selector_ids
from minos.domain.komunikat import AlgorithmMetadata, DepartmentSpecialty, ProcedureRank
from minos.domain.reference import ArtifactError
from minos.ingest.jgp_conditions_codec import decode_condition, decode_selector
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.store.jgp_conditions import resolve_characteristics
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.query_budget import query_budget

MAX_CONDITIONS = 20_000
MAX_SELECTORS = 20_000
MAX_MEMBERS = 200_000


def load_bundle_algorithm(connection: sqlite3.Connection) -> AlgorithmData:
    """The bundle's single algorithm notice with its SZP tables only."""
    with query_budget(connection):
        releases = cast(
            list[tuple[str, str, str, str, int, str]],
            connection.execute(
                """SELECT id, algorithm_version, reporting_from, version_text,
                version_paragraph, manifest_json FROM algorithm_release LIMIT 2"""
            ).fetchall(),
        )
        if len(releases) != 1:
            raise GrouperError("bundle must pin exactly one algorithm notice")
        release_id, version, reporting_from, text, paragraph, manifest = releases[0]
        ranks = cast(
            list[tuple[str, str, int, str, int]],
            connection.execute(
                """SELECT code, source_title, rank, worksheet, source_row
                FROM procedure_rank WHERE release_id=? AND grouper_type='SZP'
                ORDER BY source_row LIMIT 20001""",
                (release_id,),
            ).fetchall(),
        )
        specialties = cast(
            list[tuple[str, str, str, str, int]],
            connection.execute(
                """SELECT code, service_codes, icd_codes, worksheet, source_row
                FROM department_specialty WHERE release_id=? AND grouper_type='SZP'
                ORDER BY source_row LIMIT 20001""",
                (release_id,),
            ).fetchall(),
        )
    pins = cast(dict[str, dict[str, object]], json.loads(manifest))
    return AlgorithmData(
        AlgorithmIdentity(
            release_id,
            cast(str, pins["algorithm"]["sha256"]),
            cast(str, pins["ranks"]["sha256"]),
            cast(str, pins["specialties"]["sha256"]),
        ),
        AlgorithmMetadata(version, date.fromisoformat(reporting_from), text, paragraph),
        tuple(ProcedureRank("SZP", *row) for row in ranks),
        tuple(DepartmentSpecialty("SZP", *row) for row in specialties),
    )


def grouping_sources(
    connection: sqlite3.Connection,
    dataset_id: str,
    service_date: date,
    algorithm: AlgorithmData,
) -> GroupingSources:
    """Resolve the dated catalog and attachment 9 release and prove they pair."""
    characteristics_id = resolve_characteristics(connection, "1a", service_date)
    with query_budget(connection):
        row = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT manifest_json FROM characteristics_release WHERE id=?",
                (characteristics_id,),
            ).fetchone(),
        )
        algorithm_icd9 = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT icd9_release_id FROM algorithm_release WHERE id=?",
                (algorithm.identity.release_id,),
            ).fetchone(),
        )
    if row is None or algorithm_icd9 is None:
        raise ArtifactError("grouping sources are absent from the bundle")
    manifest = ConditionsManifest.model_validate_json(row[0])
    catalog_id = cast(
        str, resolve_ruleset(connection, service_date, None)["release_id"]
    )
    if manifest.catalog_release_id != catalog_id:
        raise GrouperError("attachment 9 and catalog releases disagree on the date")
    if manifest.icd9_release_id != algorithm_icd9[0]:
        raise GrouperError("attachment 9 and algorithm ICD-9 releases disagree")
    if "1ae" in manifest.sheets and (
        resolve_characteristics(connection, "1ae", service_date) != characteristics_id
    ):
        raise GrouperError("attachment 9 catalogs resolve to different releases")
    return GroupingSources(
        dataset_id,
        service_date,
        catalog_id,
        characteristics_id,
        manifest.icd9_release_id,
        manifest.icd10_release_id,
        algorithm.identity.release_id,
    )


def _negated(nodes: dict[int, c.Condition]) -> set[int]:
    """Nodes under a `not`: their lists must never create a candidate."""
    pending = [
        child
        for node in nodes.values()
        if node.kind == "not" and isinstance(node.payload, c.Expression)
        for child in node.payload.children
    ]
    seen: set[int] = set()
    for _ in range(MAX_CONDITIONS):
        if not pending:
            return seen
        identity = pending.pop()
        if identity in seen or identity not in nodes:
            continue
        seen.add(identity)
        payload = nodes[identity].payload
        if isinstance(payload, c.Expression):
            pending.extend(payload.children)
    raise ArtifactError("negated condition graph exceeds bound")


def _expand(
    identity: int,
    selectors: dict[int, c.Selector],
    members: dict[int, frozenset[str]],
) -> tuple[set[str], set[str], set[int], set[int]]:
    codes: set[str] = set()
    families: set[str] = set()
    lists: set[int] = set()
    rows: set[int] = set()
    pending = [identity]
    for _ in range(64):
        if not pending:
            return codes, families, lists, rows
        selector = selectors.get(pending.pop())
        if selector is None:
            raise ArtifactError("characteristics selector is unresolved")
        rows.update(selector.source_rows)
        body = selector.body
        if isinstance(body, c.ListSelector):
            lists.update(body.list_ids)
            for list_id in body.list_ids:
                codes.update(members.get(list_id, frozenset()))
        elif isinstance(body, c.CodeSelector):
            codes.update(body.codes)
        elif isinstance(body, c.UnionSelector):
            pending.extend(body.selector_ids)
        else:
            families.add(body.category)
    raise ArtifactError("characteristics selector graph exceeds depth bound")


def _requirement(
    node: c.Condition,
    selectors: dict[int, c.Selector],
    members: dict[int, frozenset[str]],
) -> DirectionalRequirement | None:
    identities = selector_ids(node.payload)
    if not identities or node.kind is None:
        return None
    kind: FactKind = "diagnosis" if node.kind.startswith("diagnosis") else "procedure"
    expected = "icd10-pl" if kind == "diagnosis" else "icd9-pl"
    codes: set[str] = set()
    families: set[str] = set()
    lists: set[int] = set()
    rows: set[int] = set(node.source_rows)
    for identity in identities:
        if selectors[identity].body.system != expected:
            raise ArtifactError("condition kind conflicts with selector system")
        more = _expand(identity, selectors, members)
        codes |= more[0]
        families |= more[1]
        lists |= more[2]
        rows |= more[3]
    if not codes and not families:
        return None
    role = getattr(node.payload, "role", None)
    return DirectionalRequirement(
        node.identity,
        kind,
        "principal" if role == "principal" else "any",
        frozenset(codes),
        tuple(sorted(families)),
        tuple(sorted(lists)),
        tuple(sorted(rows)),
    )


@dataclass(frozen=True)
class CharacteristicsGraph:
    """One attachment 9 sheet, decoded once and shared by its readers."""

    release_id: str
    catalog: Catalog
    # (group code, title row, ordered reference roots)
    groups: tuple[tuple[str, int, tuple[int, ...]], ...]
    nodes: dict[int, c.Condition]
    selectors: dict[int, c.Selector]
    members: dict[int, frozenset[str]]
    # Directional list id -> source label (for example F72), for explanations.
    labels: dict[int, str] = field(default_factory=dict)


def load_graph(
    connection: sqlite3.Connection, release_id: str, catalog: Catalog
) -> CharacteristicsGraph:
    key = (release_id, catalog)
    with query_budget(connection):
        groups = cast(
            list[tuple[str, int, str]],
            connection.execute(
                """SELECT code, title_row, roots_json FROM characteristics_group
                WHERE release_id=? AND catalog=? ORDER BY code LIMIT 2001""",
                key,
            ).fetchall(),
        )
        conditions = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT payload_json FROM group_condition WHERE release_id=?
                AND catalog=? ORDER BY group_code, source_order LIMIT ?""",
                (*key, MAX_CONDITIONS + 1),
            ).fetchall(),
        )
        selector_rows = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT payload_json FROM condition_selector WHERE release_id=?
                AND catalog=? ORDER BY id LIMIT ?""",
                (*key, MAX_SELECTORS + 1),
            ).fetchall(),
        )
        member_rows = cast(
            list[tuple[int, str]],
            connection.execute(
                """SELECT list_id, code FROM directional_member WHERE release_id=?
                AND catalog=? ORDER BY list_id, row LIMIT ?""",
                (*key, MAX_MEMBERS + 1),
            ).fetchall(),
        )
        label_rows = cast(
            list[tuple[int, str]],
            connection.execute(
                """SELECT id, label FROM directional_list WHERE release_id=?
                AND catalog=? ORDER BY id LIMIT 3001""",
                key,
            ).fetchall(),
        )
    if (
        len(groups) > 2000
        or len(conditions) > MAX_CONDITIONS
        or len(selector_rows) > MAX_SELECTORS
        or len(member_rows) > MAX_MEMBERS
        or len(label_rows) > 3000
    ):
        raise ArtifactError("characteristics graph exceeds grouper bound")
    grouped: dict[int, set[str]] = {}
    for list_id, code in member_rows:
        grouped.setdefault(list_id, set()).add(code)
    return CharacteristicsGraph(
        release_id,
        catalog,
        tuple(
            (code, row, tuple(cast(list[int], json.loads(roots))))
            for code, row, roots in groups
        ),
        {item.identity: item for item in (decode_condition(t) for (t,) in conditions)},
        {
            item.identity: item
            for item in (decode_selector(t) for (t,) in selector_rows)
        },
        {list_id: frozenset(codes) for list_id, codes in grouped.items()},
        dict(label_rows),
    )


def owned_nodes(graph: CharacteristicsGraph) -> dict[str, list[c.Condition]]:
    by_group: dict[str, list[c.Condition]] = {}
    for node in sorted(graph.nodes.values(), key=lambda item: item.identity):
        by_group.setdefault(node.group, []).append(node)
    return by_group


def selector_codes(
    graph: CharacteristicsGraph, identity: int
) -> tuple[set[str], set[str], set[int], set[int]]:
    """Codes, unexpanded families, list ids and source rows of one selector."""
    return _expand(identity, graph.selectors, graph.members)


def index_from_graph(graph: CharacteristicsGraph) -> DirectionalIndex:
    negated = _negated(graph.nodes)
    by_group = owned_nodes(graph)
    result: list[GroupDirections] = []
    for code, title_row, _roots in graph.groups:
        owned = by_group.get(code, [])
        requirements = tuple(
            requirement
            for node in owned
            # Unsupported nodes still name their lists; keeping them errs towards
            # inclusion, and the candidate reports fully_interpreted=False.
            if node.identity not in negated
            and (requirement := _requirement(node, graph.selectors, graph.members))
            is not None
        )
        result.append(
            GroupDirections(
                code,
                title_row,
                requirements,
                bool(owned) and all(node.status == "proposed" for node in owned),
            )
        )
    return DirectionalIndex(graph.release_id, graph.catalog, tuple(result))


def directional_index(
    connection: sqlite3.Connection, release_id: str, catalog: Catalog
) -> DirectionalIndex:
    return index_from_graph(load_graph(connection, release_id, catalog))
