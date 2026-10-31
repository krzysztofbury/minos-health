"""Bounded dated attachment 9 reference answers, never encounter qualification."""

import json
import sqlite3
from datetime import date
from typing import Literal, cast

from minos.domain import jgp_conditions as c
from minos.domain.jgp_conditions_validation import selector_ids
from minos.domain.jgp_ruleset import GroupNotOnDate, NoRulesetForDate
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchInputError
from minos.ingest.jgp_conditions_codec import decode_condition, decode_selector, encode
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.store.bundle_cache import BundleCache
from minos.store.jgp_conditions import resolve_characteristics
from minos.store.jgp_ruleset import (
    POINTS_KIND,
    POINTS_LABEL,
    catalog_group,
    points_display,
)
from minos.store.query_budget import query_budget

PAGE_SIZE = 100
MAX_GROUP_NODES = 2048
MAX_GROUP_EVIDENCE = 2048
MAX_SELECTORS = 20_000
type Catalog = Literal["1a", "1ae"]
type Json = dict[str, object]


def selected_source(
    connection: sqlite3.Connection, catalog: Catalog, service_date: date
) -> Json:
    identity = resolve_characteristics(connection, catalog, service_date)
    with query_budget(connection):
        row = cast(
            tuple[str, str, str],
            connection.execute(
                """SELECT r.manifest_json, s.worksheet, k.as_of
            FROM characteristics_release r JOIN characteristics_sheet s
              ON s.release_id=r.id CROSS JOIN characteristics_knowledge k
            WHERE r.id=? AND s.catalog=?""",
                (identity, catalog),
            ).fetchone(),
        )
    manifest = ConditionsManifest.model_validate_json(row[0])
    return {
        "release_id": identity,
        "catalog": catalog,
        "worksheet": row[1],
        "service_date": service_date.isoformat(),
        "knowledge_as_of": row[2],
        "service_date_after_knowledge": service_date.isoformat() > row[2],
        "act_number": manifest.act_number,
        "baw_document_id": manifest.baw_document_id,
        "published_on": manifest.published_on.isoformat(),
        "source_url": manifest.workbook.source_url,
        "sha256": manifest.workbook.sha256,
        "recorded_at": manifest.workbook.fetched_at.isoformat(),
        "applicability_evidence": manifest.applicability_evidence,
        "catalog_release_id": manifest.catalog_release_id,
        "icd9_release_id": manifest.icd9_release_id,
        "icd10_release_id": manifest.icd10_release_id,
        "publisher": manifest.publisher,
        "license": manifest.license,
        "notices": list(manifest.notices),
    }


def _bounded(rows: list[object], maximum: int) -> None:
    if len(rows) > maximum:
        raise ArtifactError("characteristics reference answer exceeds bound")


def _group_selectors(
    connection: sqlite3.Connection,
    key: tuple[str, Catalog],
    nodes: tuple[c.Condition, ...],
) -> tuple[c.Selector, ...]:
    # Decode this group's reachable selectors, not the whole release graph.
    selectors: dict[int, c.Selector] = {}
    pending: set[int] = {
        identity for node in nodes for identity in selector_ids(node.payload)
    }
    chosen: set[int] = set()
    for _ in range(33):
        if not pending:
            return tuple(selectors[i] for i in sorted(chosen))
        current = pending - chosen
        pending = set()
        rows = cast(
            list[tuple[str]],
            connection.execute(
                """SELECT payload_json FROM condition_selector WHERE release_id=? AND catalog=?
            AND id IN (SELECT value FROM json_each(?)) ORDER BY id LIMIT ?""",
                (*key, json.dumps(sorted(current)), MAX_SELECTORS + 1),
            ).fetchall(),
        )
        _bounded(cast(list[object], rows), MAX_SELECTORS)
        for (text,) in rows:
            item = decode_selector(text)
            selectors[item.identity] = item
        if not current <= selectors.keys():
            raise ArtifactError("characteristics selector is unresolved")
        for identity in current:
            item = selectors[identity]
            chosen.add(identity)
            if isinstance(item.body, c.UnionSelector):
                pending.update(item.body.selector_ids)
    raise ArtifactError("characteristics selector graph exceeds depth bound")


def group_conditions(
    connection: sqlite3.Connection, catalog: Catalog, code: str, service_date: date
) -> Json:
    source = selected_source(connection, catalog, service_date)
    key = (cast(str, source["release_id"]), catalog)
    with query_budget(connection):
        group = cast(
            tuple[str] | None,
            connection.execute(
                "SELECT roots_json FROM characteristics_group WHERE release_id=? AND catalog=? AND code=?",
                (*key, code),
            ).fetchone(),
        )
        if group is None:
            return {
                "status": "group_not_in_characteristics",
                "source": source,
                "nodes": [],
                "selectors": [],
                "source_text": [],
                "lists": [],
            }
        rows = cast(
            list[tuple[str]],
            connection.execute(
                "SELECT payload_json FROM group_condition WHERE release_id=? AND catalog=? AND group_code=? ORDER BY source_order LIMIT ?",
                (*key, code, MAX_GROUP_NODES + 1),
            ).fetchall(),
        )
        _bounded(cast(list[object], rows), MAX_GROUP_NODES)
        nodes = tuple(decode_condition(text) for (text,) in rows)
        fully = bool(nodes) and all(node.status == "proposed" for node in nodes)
        selectors = _group_selectors(connection, key, nodes)
        physical_lists = sorted(
            {
                identity
                for item in selectors
                if isinstance(item.body, c.ListSelector)
                for identity in item.body.list_ids
            }
        )
        evidence = cast(
            list[tuple[int, str, str]],
            connection.execute(
                """SELECT row, source_text, category FROM characteristics_evidence
            WHERE release_id=? AND catalog=? AND (
              (trim(context)=? AND category IN ('title','label','connector','unbound_member'))
              OR row IN (SELECT value FROM json_each(?)))
            ORDER BY row LIMIT ?""",
                (
                    *key,
                    code,
                    json.dumps(
                        sorted(
                            {row for n in nodes for row in n.source_rows}
                            | {row for s in selectors for row in s.source_rows}
                        )
                    ),
                    MAX_GROUP_EVIDENCE + 1,
                ),
            ).fetchall(),
        )
        _bounded(cast(list[object], evidence), MAX_GROUP_EVIDENCE)
        lists = _list_summaries(connection, key, physical_lists)
        code_keys = sorted(
            {
                item.body.system + ":" + code
                for item in selectors
                if isinstance(item.body, c.CodeSelector)
                for code in item.body.codes
            }
        )
        codes = cast(
            list[tuple[str, str, str]],
            connection.execute(
                """SELECT system,code,correspondence FROM characteristics_code
            WHERE release_id=? AND catalog=? AND system || ':' || code
              IN (SELECT value FROM json_each(?)) ORDER BY system,code LIMIT 20001""",
                (*key, json.dumps(code_keys)),
            ).fetchall(),
        )
        _bounded(cast(list[object], codes), MAX_SELECTORS)
    return {
        "status": "fully_interpreted" if fully else "not_yet_interpreted",
        "meaning": "Reference fragments only. List membership is not encounter qualification.",
        "source": source,
        "roots": json.loads(group[0]) if fully else [],
        "nodes": [json.loads(encode(node)) for node in nodes] if fully else [],
        "selectors": [
            dict(cast(Json, json.loads(encode(item))), kind=item.body.kind)
            for item in selectors
        ]
        if fully
        else [],
        "source_text": [
            {"row": row, "text": text, "category": category}
            for row, text, category in evidence
        ],
        "lists": lists,
        "codes": [
            {"system": system, "code": code, "correspondence": correspondence}
            for system, code, correspondence in codes
        ]
        if fully
        else [],
    }


def _list_summaries(
    connection: sqlite3.Connection, key: tuple[str, Catalog], identities: list[int]
) -> list[Json]:
    if not identities:
        return []
    # Count only the selected declarations, in one bounded query.
    rows = cast(
        list[tuple[int, str, str, int, int]],
        connection.execute(
            """SELECT l.id,l.label,l.system,l.marker_row,count(m.row)
        FROM directional_list l JOIN directional_member m
          ON m.release_id=l.release_id AND m.catalog=l.catalog AND m.list_id=l.id
        WHERE l.release_id=? AND l.catalog=? AND l.id IN (SELECT value FROM json_each(?))
        GROUP BY l.id ORDER BY l.id LIMIT 3001""",
            (*key, json.dumps(identities)),
        ).fetchall(),
    )
    _bounded(cast(list[object], rows), 3000)
    chosen = set(identities)
    return [
        {
            "id": identity,
            "label": label,
            "system": system,
            "marker_row": marker,
            "occurrences": count,
        }
        for identity, label, system, marker, count in rows
        if identity in chosen
    ]


def list_members(
    connection: sqlite3.Connection,
    catalog: Catalog,
    identity: int,
    service_date: date,
    page: int = 1,
) -> Json:
    if not 1 <= identity <= 3000 or not 1 <= page <= 500:
        raise SearchInputError("invalid characteristics list page")
    source = selected_source(connection, catalog, service_date)
    key = (source["release_id"], catalog, identity)
    with query_budget(connection):
        header = cast(
            tuple[str, str, int, int] | None,
            connection.execute(
                """SELECT l.label,l.system,l.marker_row,count(m.row)
            FROM directional_list l JOIN directional_member m
              ON m.release_id=l.release_id AND m.catalog=l.catalog AND m.list_id=l.id
            WHERE l.release_id=? AND l.catalog=? AND l.id=? GROUP BY l.id""",
                key,
            ).fetchone(),
        )
        if header is None or (page - 1) * PAGE_SIZE >= header[3]:
            raise SearchInputError("characteristics list page is absent")
        rows = cast(
            list[tuple[int, str, str, str, str]],
            connection.execute(
                """SELECT m.row,m.code,e.source_text,m.marks_json,c.correspondence
            FROM directional_member m JOIN characteristics_evidence e
              ON e.release_id=m.release_id AND e.catalog=m.catalog AND e.row=m.row
            JOIN characteristics_code c ON c.release_id=m.release_id AND c.catalog=m.catalog
              AND c.system=m.system AND c.code=m.code
            WHERE m.release_id=? AND m.catalog=? AND m.list_id=?
            ORDER BY m.row LIMIT ? OFFSET ?""",
                (*key, PAGE_SIZE, (page - 1) * PAGE_SIZE),
            ).fetchall(),
        )
    return {
        "source": source,
        "id": identity,
        "label": header[0],
        "system": header[1],
        "marker_row": header[2],
        "total_occurrences": header[3],
        "page": page,
        "page_size": PAGE_SIZE,
        "members": [
            {
                "row": row,
                "code": code,
                "source_text": text,
                "marks": json.loads(marks),
                "correspondence": correspondence,
            }
            for row, code, text, marks, correspondence in rows
        ],
    }


def attach_conditions(
    connection: sqlite3.Connection, answer: Json, *, available: bool
) -> Json:
    if not available:
        answer["conditions"] = {"status": "not_in_bundle"}
        return answer
    group = cast(Json, answer["group"])
    ruleset = cast(Json, answer["ruleset"])
    try:
        conditions = group_conditions(
            connection,
            cast(Catalog, group["catalog"]),
            cast(str, group["code"]),
            date.fromisoformat(cast(str, ruleset["service_date"])),
        )
    except NoRulesetForDate:
        conditions = {"status": "context_not_covered"}
    if "source" in conditions:
        source = cast(Json, conditions["source"])
        if source["catalog_release_id"] != ruleset["release_id"]:
            raise ArtifactError("selected characteristics and catalog are not linked")
    answer["conditions"] = conditions
    return answer


def reference_group(
    connection: sqlite3.Connection, ruleset: Json, code: str, *, available: bool
) -> Json:
    try:
        return attach_conditions(
            connection, catalog_group(connection, ruleset, code), available=available
        )
    except GroupNotOnDate:
        if not available:
            raise
    day = date.fromisoformat(cast(str, ruleset["service_date"]))
    candidates: list[Json] = []
    for catalog in ("1a", "1ae"):
        try:
            answer = group_conditions(connection, catalog, code, day)
        except NoRulesetForDate:
            continue
        if answer["status"] != "group_not_in_characteristics":
            candidates.append(answer)
    if not candidates:
        raise GroupNotOnDate("group is absent from catalog and characteristics")
    if len(candidates) != 1:
        raise ArtifactError("characteristics group occurs in multiple contexts")
    conditions = candidates[0]
    source = cast(Json, conditions["source"])
    if source["catalog_release_id"] != ruleset["release_id"]:
        raise ArtifactError("selected characteristics and catalog are not linked")
    title = next(
        item
        for item in cast(list[Json], conditions["source_text"])
        if item["category"] == "title"
    )
    return {
        "ruleset": ruleset,
        "conditions": conditions,
        "base_values": [],
        "scopes": [],
        "points_kind": POINTS_KIND,
        "points_label": POINTS_LABEL,
        "group": {
            "code": code,
            "catalog": source["catalog"],
            "name": cast(str, title["text"]).removeprefix(code).strip(),
            "product_code": None,
            "procedural": None,
            "notes": None,
            "applicability": None,
            "catalog_row_status": "not_on_date",
        },
    }


# Each seed is an explicitly stored condition selector. A union is followed
# only through its stored IDs; code prefixes and classification families never
# manufacture members. All nodes participate, including unsupported evidence,
# because this is a source-membership lookup rather than an eligibility filter.
MEMBERSHIP_SQL = """
WITH RECURSIVE seeds(group_code,selector_id) AS (
  SELECT group_code,json_extract(payload_json,'$.payload.selector_id')
    FROM group_condition WHERE release_id=:release AND catalog=:catalog
      AND json_type(payload_json,'$.payload.selector_id')='integer'
  UNION
  SELECT g.group_code,j.value FROM group_condition g,
    json_each(g.payload_json,'$.payload.selector_ids') j
    WHERE g.release_id=:release AND g.catalog=:catalog
  UNION
  SELECT g.group_code,j.value FROM group_condition g,
    json_each(g.payload_json,'$.payload.occurrence_sets') sets,
    json_each(sets.value) j WHERE g.release_id=:release AND g.catalog=:catalog
), reachable(group_code,selector_id,depth) AS (
  SELECT group_code,selector_id,0 FROM seeds
  UNION
  SELECT r.group_code,j.value,r.depth+1 FROM reachable r
    JOIN condition_selector s ON s.release_id=:release AND s.catalog=:catalog
      AND s.id=r.selector_id, json_each(s.payload_json,'$.body.selector_ids') j
    WHERE r.depth<32
), linked AS (
  SELECT DISTINCT r.group_code,m.list_id FROM reachable r
    JOIN condition_selector s ON s.release_id=:release AND s.catalog=:catalog
      AND s.id=r.selector_id, json_each(s.payload_json,'$.body.list_ids') lists
    JOIN directional_member m ON m.release_id=:release AND m.catalog=:catalog
       AND m.list_id=lists.value AND m.system=:system AND m.code=:code
  UNION
  SELECT g.code,m.list_id FROM directional_member m
    JOIN directional_list l ON l.release_id=m.release_id AND l.catalog=m.catalog AND l.id=m.list_id
    JOIN characteristics_group g ON g.release_id=l.release_id AND g.catalog=l.catalog AND g.code=trim(l.context)
    WHERE m.release_id=:release AND m.catalog=:catalog AND m.system=:system AND m.code=:code
), grouped AS (
  SELECT g.code,g.title_row,e.source_text,json_group_array(linked.list_id) AS list_ids,
    NOT EXISTS (SELECT 1 FROM group_condition n WHERE n.release_id=:release
      AND n.catalog=:catalog AND n.group_code=g.code
      AND n.interpretation_status='unsupported') AS interpreted
  FROM linked JOIN characteristics_group g ON g.release_id=:release
    AND g.catalog=:catalog AND g.code=linked.group_code
  JOIN characteristics_evidence e ON e.release_id=g.release_id
    AND e.catalog=g.catalog AND e.row=g.title_row
  GROUP BY g.code
)
SELECT code,title_row,source_text,list_ids,interpreted,count(*) OVER ()
FROM grouped ORDER BY title_row,code LIMIT :limit OFFSET :offset
"""


# The selector reachability above depends only on the release and catalog,
# yet it parsed every condition of the catalog on each request (#225). The
# cached path computes it once and keeps the per-code part of the query.
# Each split point must occur once, or a later edit could cut the query apart
# somewhere else without failing.
if MEMBERSHIP_SQL.count("), linked AS (") != 1:
    raise RuntimeError("membership query no longer splits at its reviewed points")
_REACHABLE, _LINKED = MEMBERSHIP_SQL.split("), linked AS (", 1)
if _LINKED.count("  UNION\n") != 1:
    raise RuntimeError("membership query no longer splits at its reviewed points")
_SELECTOR_LISTS_TAIL = """)
SELECT DISTINCT r.group_code,lists.value,typeof(lists.value) FROM reachable r
  JOIN condition_selector s ON s.release_id=:release AND s.catalog=:catalog
    AND s.id=r.selector_id, json_each(s.payload_json,'$.body.list_ids') lists
LIMIT 200001
"""
_PAIRS_HEAD = """WITH pairs(group_code,list_id) AS (
  SELECT json_extract(value,'$[0]'),json_extract(value,'$[1]') FROM json_each(:pairs)
), linked AS (
  SELECT DISTINCT p.group_code,m.list_id FROM pairs p
    JOIN directional_member m ON m.release_id=:release AND m.catalog=:catalog
       AND m.list_id=p.list_id AND m.system=:system AND m.code=:code
  UNION
"""
# Both are composed from this module's constant SQL only, never from input.
SELECTOR_LISTS_SQL = _REACHABLE + _SELECTOR_LISTS_TAIL  # nosec B608
CACHED_MEMBERSHIP_SQL = _PAIRS_HEAD + _LINKED.split("  UNION\n", 1)[1]  # nosec B608


def selector_lists(
    connection: sqlite3.Connection, release: str, catalog: Catalog
) -> dict[int, frozenset[str]]:
    """Each directional list id with the groups whose selectors reach it."""
    with query_budget(connection):
        rows = cast(
            list[tuple[str, object, str]],
            connection.execute(
                SELECTOR_LISTS_SQL, {"release": release, "catalog": catalog}
            ).fetchall(),
        )
    if len(rows) > 200_000:
        raise ArtifactError("selector list reachability exceeds bound")
    groups: dict[int, set[str]] = {}
    for group, list_id, kind in rows:
        # Integer ids compare in Python exactly as the SQL join compared them.
        # The join would also coerce a text "5"; the cache refuses it instead.
        if kind != "integer" or not isinstance(list_id, int):
            raise ArtifactError("selector list id is not an integer")
        groups.setdefault(list_id, set()).add(group)
    return {list_id: frozenset(names) for list_id, names in groups.items()}


def _membership_rows(
    connection: sqlite3.Connection,
    parameters: dict[str, object],
    cache: BundleCache | None,
) -> list[tuple[str, int, str, str, int, int]]:
    if cache is None:
        return cast(
            list[tuple[str, int, str, str, int, int]],
            connection.execute(MEMBERSHIP_SQL, parameters).fetchall(),
        )
    release, catalog = (
        cast(str, parameters["release"]),
        cast(Catalog, parameters["catalog"]),
    )
    reach = cache.get(
        ("selector_lists", release, catalog),
        lambda: selector_lists(connection, release, catalog),
    )
    lists = cast(
        list[tuple[int]],
        connection.execute(
            """SELECT DISTINCT list_id FROM directional_member WHERE release_id=:release
            AND catalog=:catalog AND system=:system AND code=:code LIMIT 100001""",
            parameters,
        ).fetchall(),
    )
    pairs = sorted(
        (group, list_id) for (list_id,) in lists for group in reach.get(list_id, ())
    )
    return cast(
        list[tuple[str, int, str, str, int, int]],
        connection.execute(
            CACHED_MEMBERSHIP_SQL, parameters | {"pairs": json.dumps(pairs)}
        ).fetchall(),
    )


def _condition_labels(
    connection: sqlite3.Connection, release: str, catalog: Catalog
) -> dict[int, tuple[str, ...]]:
    """Each group title row with the source clauses printed under it."""
    labels: dict[int, list[str]] = {}
    current: list[str] | None = None
    for row, category, text in cast(
        list[tuple[int, str, str]],
        connection.execute(
            """SELECT row, category, source_text FROM characteristics_evidence
            WHERE release_id=? AND catalog=? AND category IN ('title', 'label')
            ORDER BY row""",
            (release, catalog),
        ).fetchall(),
    ):
        if category == "title":
            current = labels.setdefault(row, [])
        elif current is not None:
            current.append(" ".join(text.split()))
    return {row: tuple(clauses) for row, clauses in labels.items()}


def _catalog_rows(
    connection: sqlite3.Connection,
    release: str,
    catalog: Catalog,
    codes: list[str],
    day: date,
) -> dict[str, Json]:
    """The catalog row of each code on the day, read in one pass for the page.

    Single-table statements for the reason in store/hierarchy_walk.py (#294).
    """
    rows = cast(
        list[tuple[str, int, str, int]],
        connection.execute(
            """SELECT code, source_row, name, procedural FROM group_definition
            WHERE release_id=:release AND catalog=:catalog
              AND code IN (SELECT value FROM json_each(:codes))
              AND service_from<=:day AND (service_to IS NULL OR :day<=service_to)""",
            {
                "release": release,
                "catalog": catalog,
                "codes": json.dumps(codes),
                "day": day.isoformat(),
            },
        ).fetchall(),
    )
    if len({row[0] for row in rows}) != len(rows):
        raise ArtifactError("several catalog rows apply to one code and date")
    values: dict[int, list[Json]] = {}
    for source_row, measure, unit, amount in cast(
        list[tuple[int, str, str, int]],
        connection.execute(
            """SELECT source_row, measure, unit, amount FROM group_base_value
            WHERE release_id=:release AND catalog=:catalog
              AND source_row IN (SELECT value FROM json_each(:rows))
            ORDER BY source_column""",
            {
                "release": release,
                "catalog": catalog,
                "rows": json.dumps([row[1] for row in rows]),
            },
        ).fetchall(),
    ):
        values.setdefault(source_row, []).append(
            {
                "measure": measure,
                "unit": unit,
                "amount": amount,
                "display": points_display(amount)
                if unit == "points_hundredths"
                else str(amount),
            }
        )
    return {
        code: {
            "name": name,
            "procedural": bool(procedural),
            "base_values": values.get(source_row, []),
        }
        for code, source_row, name, procedural in rows
    }


def code_memberships(
    connection: sqlite3.Connection,
    system: c.System,
    code: str,
    catalog: Catalog,
    service_date: date,
    page: int = 1,
    cache: BundleCache | None = None,
) -> Json:
    if not 1 <= page <= 20:
        raise SearchInputError("invalid characteristics membership page")
    source = selected_source(connection, catalog, service_date)
    with query_budget(connection):
        rows = _membership_rows(
            connection,
            {
                "release": source["release_id"],
                "catalog": catalog,
                "system": system,
                "code": code,
                "limit": PAGE_SIZE,
                "offset": (page - 1) * PAGE_SIZE,
            },
            cache,
        )
    if not rows and page != 1:
        raise SearchInputError("characteristics membership page is absent")
    release = cast(str, source["release_id"])
    with query_budget(connection):
        catalog_rows = _catalog_rows(
            connection,
            cast(str, source["catalog_release_id"]),
            catalog,
            [row[0] for row in rows],
            service_date,
        )
        labels = (
            cache.get(
                ("condition_labels", release, catalog),
                lambda: _condition_labels(connection, release, catalog),
            )
            if cache is not None
            else _condition_labels(connection, release, catalog)
        )
    return {
        "source": source,
        "system": system,
        "code": code,
        "page": page,
        "page_size": PAGE_SIZE,
        "total": rows[0][5] if rows else 0,
        "coverage": "Selected attachment 9 explicit directional lists only; absence is not exclusion.",
        "groups": [
            {
                "code": group,
                "title_row": row,
                "source_title": title,
                "list_ids": sorted(cast(list[int], json.loads(lists))),
                "fully_interpreted": bool(interpreted),
                # A group only in the characteristics has no catalog row that day.
                "name": catalog_rows.get(group, {}).get("name"),
                "procedural": catalog_rows.get(group, {}).get("procedural"),
                "base_values": catalog_rows.get(group, {}).get("base_values", []),
                "points_kind": POINTS_KIND,
                "conditions": list(labels.get(row, ())),
            }
            for group, row, title, lists, interpreted, _ in rows
        ],
    }


def characteristics_calendar(connection: sqlite3.Connection) -> list[str]:
    with query_budget(connection):
        rows = cast(
            list[tuple[str]],
            connection.execute(
                "SELECT DISTINCT valid_from FROM characteristics_release ORDER BY valid_from LIMIT 9"
            ).fetchall(),
        )
    if not rows or len(rows) > 8:
        raise ArtifactError("characteristics calendar is absent or exceeds bound")
    return [day for (day,) in rows]
