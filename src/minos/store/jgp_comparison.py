"""Date-selected catalog comparisons using the logical release-diff projection."""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import TypedDict, cast

from minos.domain.reference import QueryLimitError
from minos.domain.release_diff import DiffInputError, Fact, compare_facts
from minos.store.diff_catalog import project_catalog
from minos.store.diff_records import Projection, canonical
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.query_budget import QueryBudget, query_budget


class Definition(TypedDict):
    product_code: str
    name: str
    procedural: bool
    chapter: str
    notes: str | None
    scopes: list[tuple[str, int]]


class Window(TypedDict):
    source_occurrence: int
    service_from: str
    service_to: str | None
    evidence: str


class Entry(TypedDict):
    definition: Definition
    values: list[tuple[str, str, int]]
    window: Window


type GroupKey = tuple[str, str]


@dataclass(frozen=True)
class CatalogComparison:
    before: dict[str, object]
    after: dict[str, object]
    left: dict[GroupKey, Entry]
    right: dict[GroupKey, Entry]
    changed: tuple[GroupKey, ...]
    scopes: dict[str, str]


@dataclass(frozen=True)
class CatalogVersion:
    release: str
    published_on: date
    service_from: date
    supersedes: str | None
    label: str


def catalog_versions(connection: sqlite3.Connection) -> tuple[CatalogVersion, ...]:
    with query_budget(connection):
        rows = cast(
            list[tuple[str, str, str, str | None, str]],
            connection.execute(
                """SELECT id, published_on, service_from, supersedes,
                service_evidence FROM catalog_release ORDER BY published_on, id LIMIT 65"""
            ).fetchall(),
        )
    if len(rows) > 64:
        raise QueryLimitError("comparison release inventory exceeds bound")
    return tuple(
        CatalogVersion(
            identity,
            date.fromisoformat(published),
            date.fromisoformat(start),
            previous,
            # Quote the reviewed applicability evidence, not a guessed amendment
            # number extracted from a filename or the internal release identifier.
            f"{evidence.partition(':')[0][:180]} ({published})",
        )
        for identity, published, start, previous, evidence in rows
    )


def _selected(
    connection: sqlite3.Connection,
    day: date,
    release: str,
    versions: tuple[CatalogVersion, ...],
) -> dict[str, object]:
    version = next((item for item in versions if item.release == release), None)
    if version is None:
        raise DiffInputError("selected comparison release is not pinned")
    # Retroactive amendments can share service_from. Resolve at the explicitly
    # chosen release's publication date, then prove that exact release governs.
    result = resolve_ruleset(connection, day, version.published_on)
    if result["release_id"] != release:
        raise DiffInputError("selected catalog does not cover the selected day")
    return result


def scope_identity(label: str) -> str:
    # Source positions can move between acts. Only an identical source label
    # shares a filter; renames remain separate, never guessed synonyms.
    return hashlib.sha256(label.encode()).hexdigest()


def _dated(
    projection: Projection, day: date, budget: QueryBudget
) -> dict[GroupKey, Entry]:
    selected: dict[GroupKey, Entry] = {}
    for fact in projection.facts():
        budget.check()
        if fact.category != "catalog_schedule":
            continue
        population = cast(list[Entry], json.loads(fact.value))
        active = [
            entry
            for entry in population
            if entry["window"]["service_from"] <= day.isoformat()
            and (
                entry["window"]["service_to"] is None
                or day.isoformat() <= entry["window"]["service_to"]
            )
        ]
        if len(active) > 1:
            raise DiffInputError("several catalog rows apply to one group and date")
        if active:
            selected[cast(GroupKey, fact.identity)] = active[0]
    if len(selected) > 2000:
        raise QueryLimitError("dated comparison exceeds group bound")
    return selected


def _facts(entries: dict[GroupKey, Entry]) -> tuple[Fact, ...]:
    # Applicability determines selection and remains evidence. A shifted window
    # alone is not a changed base value or definition on the two selected days.
    return tuple(
        Fact(
            "dated_catalog_group",
            key,
            canonical({"definition": entry["definition"], "values": entry["values"]}),
            canonical(entry["window"]),
        )
        for key, entry in sorted(entries.items())
    )


def compare_catalog_dates(
    connection: sqlite3.Connection,
    before: date,
    after: date,
    before_release: str,
    after_release: str,
) -> CatalogComparison:
    # Unlike the offline full-diff command, a web read retains the ordinary
    # one-second / two-million-operation budget across both selected sides.
    with query_budget(connection) as budget:
        versions = catalog_versions(connection)
        old = _selected(connection, before, before_release, versions)
        new = _selected(connection, after, after_release, versions)
        projections: dict[str, Projection] = {}
        for ruleset in (old, new):
            release = cast(str, ruleset["release_id"])
            if release not in projections:
                output = Projection(budget)
                project_catalog(connection, release, output, schedule_only=True)
                projections[release] = output
        left = _dated(projections[cast(str, old["release_id"])], before, budget)
        right = _dated(projections[cast(str, new["release_id"])], after, budget)
        changes = compare_facts(
            f"catalog:{old['release_id']}:{new['release_id']}",
            _facts(left),
            _facts(right),
        )
        labels = {
            label
            for entries in (left, right)
            for entry in entries.values()
            for label, _ in entry["definition"]["scopes"]
        }
        budget.check()
        return CatalogComparison(
            old,
            new,
            left,
            right,
            tuple(cast(GroupKey, change.identity) for change in changes),
            {scope_identity(label): label for label in sorted(labels)},
        )
