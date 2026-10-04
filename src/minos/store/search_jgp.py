"""Dated explicit source membership as an explainable, non-clinical signal."""

import json
import sqlite3
from datetime import date
from typing import cast

from pydantic import ValidationError

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    NoRulesetForDate,
    ReleaseWindow,
    TimeZoneUnavailable,
    resolve_release,
    today_in_poland,
)
from minos.domain.reference import ArtifactError
from minos.domain.search import SearchRequest
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.store.query_budget import query_budget


def _selected_manifest(
    connection: sqlite3.Connection, day: date
) -> ConditionsManifest | None:
    # The caller owns its query budget. Entering the store selector's nested
    # budget would replace and then clear the search SQL progress handler.
    horizon = cast(
        tuple[str] | None,
        connection.execute("SELECT as_of FROM characteristics_knowledge").fetchone(),
    )
    if horizon is None:
        return None
    rows = cast(
        list[tuple[str]],
        connection.execute(
            "SELECT manifest_json FROM characteristics_release ORDER BY id LIMIT 9"
        ).fetchall(),
    )
    if len(rows) > 8:
        raise ArtifactError("characteristics releases exceed bound")
    try:
        manifests = tuple(
            ConditionsManifest.model_validate_json(row[0]) for row in rows
        )
    except ValidationError as error:
        # Bundle data, not the user's request, is wrong here.
        raise ArtifactError("characteristics release manifest is invalid") from error
    windows = tuple(
        ReleaseWindow(
            item.release_id, item.valid_from, item.published_on, item.supersedes
        )
        for item in manifests
    )
    try:
        chosen = resolve_release(windows, day, date.fromisoformat(horizon[0]))
    except NoRulesetForDate:
        return None
    selected = next(item for item in manifests if item.release_id == chosen.release_id)
    return (
        None if selected.valid_to is not None and day > selected.valid_to else selected
    )


def membership_counts(
    connection: sqlite3.Connection, request: SearchRequest
) -> tuple[dict[str, int], date | None]:
    if (
        request.query.code_like
        or connection.execute("PRAGMA user_version").fetchone()[0] < 8
    ):
        return {}, None
    # An optional ranking signal: a missing time zone or an ambiguous dated
    # source drops the counts instead of failing the whole search.
    try:
        day = request.service_date or today_in_poland()
        manifest = _selected_manifest(connection, day)
    except TimeZoneUnavailable, AmbiguousRuleset:
        return {}, None
    if manifest is None:
        return {}, None
    sources: list[dict[str, str]] = []
    for catalog in ("1a", "1ae"):
        if catalog not in manifest.sheets:
            continue
        classification = (
            manifest.icd10_release_id
            if request.system == "icd10-pl"
            else manifest.icd9_release_id
        )
        if classification == request.release_id:
            sources.append({"release": manifest.release_id, "catalog": catalog})
    if not sources:
        return {}, None
    rows = cast(
        list[tuple[str, int]],
        connection.execute(
            """SELECT c.code,sum(c.group_count) FROM json_each(?) s
        JOIN search_jgp_count c ON c.release_id=json_extract(s.value,'$.release') AND c.catalog=json_extract(s.value,'$.catalog')
        WHERE c.system=? GROUP BY c.code ORDER BY c.code LIMIT 32001""",
            (json.dumps(sources), request.system),
        ).fetchall(),
    )
    if len(rows) > 32000:
        raise ArtifactError("search membership signal exceeds bound")
    return dict(rows), day


def shared_procedures(
    connection: sqlite3.Connection, code: str, day: date, icd10_release: str
) -> list[dict[str, object]]:
    with query_budget(connection):
        return _shared_procedures(connection, code, day, icd10_release)


def _shared_procedures(
    connection: sqlite3.Connection, code: str, day: date, icd10_release: str
) -> list[dict[str, object]]:
    if connection.execute("PRAGMA user_version").fetchone()[0] < 8:
        return []
    result: list[dict[str, object]] = []
    manifest = _selected_manifest(connection, day)
    if manifest is None or manifest.icd10_release_id != icd10_release:
        return []
    for catalog in ("1a", "1ae"):
        if catalog not in manifest.sheets:
            continue
        identity = manifest.release_id
        rows = cast(
            list[tuple[str, str, str]],
            connection.execute(
                """SELECT p.code,n.source_text,json_group_array(DISTINCT p.group_code)
            FROM search_jgp_member d JOIN search_jgp_member p ON p.release_id=d.release_id AND p.catalog=d.catalog AND p.group_code=d.group_code AND p.system='icd9-pl'
            JOIN concept c ON c.source_release_id=? AND c.lookup_code=p.code
            JOIN reference_node n ON n.source_release_id=c.source_release_id AND n.ordinal=c.node_ordinal
            WHERE d.release_id=? AND d.catalog=? AND d.system='icd10-pl' AND d.code=?
            GROUP BY p.code ORDER BY count(DISTINCT p.group_code) DESC,p.code LIMIT 21""",
                (manifest.icd9_release_id, identity, catalog, code),
            ).fetchall(),
        )
        procedures: list[dict[str, object]] = []
        for procedure, label, groups in rows[:20]:
            shared = cast(list[str], json.loads(groups))
            if len(shared) > 2000:
                raise ArtifactError("shared procedure groups exceed bound")
            procedures.append(
                {
                    "code": procedure,
                    "label": label,
                    "shared_groups": shared[:20],
                    "shared_group_count": len(shared),
                }
            )
        result.append(
            {
                "source_release_id": identity,
                "catalog": catalog,
                "service_date": day.isoformat(),
                "truncated": len(rows) > 20,
                "procedures": procedures,
            }
        )
    return result
