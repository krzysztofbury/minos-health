"""Bounded immutable catalog schedules, owned by one verified bundle pool."""

import json
import sqlite3
import sys
from collections.abc import Mapping
from types import MappingProxyType
from typing import NamedTuple, TypedDict, cast

from minos.domain.reference import QueryLimitError
from minos.domain.release_diff import DiffInputError, Fact
from minos.store.diff_catalog import project_catalog
from minos.store.diff_records import Projection, canonical
from minos.store.query_budget import QueryBudget, query_budget

# These additional web-cache limits do not change the offline diff's bounds.
# Source buffers are sequential, at most three 20k-row / 2 MiB populations per
# release. Only one release's projection and decoded population exist at a time.
MAX_SOURCE_ROWS = 20_000
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_GROUPS = 2000
MAX_SCHEDULE_ROWS = 4000
MAX_RETAINED_BYTES = 64 * 1024 * 1024
# Startup work, not a request: the host took 3.5 s alone and crossed 5 s while
# the previous colour shared its one CPU (#198). Still well inside the 120 s
# health start period, so a stuck preload fails closed before readiness.
MAX_PRELOAD_SECONDS = 30.0

type GroupKey = tuple[str, str]


class Definition(NamedTuple):
    product_code: str
    name: str
    procedural: bool
    chapter: str
    notes: str | None
    scopes: tuple[tuple[str, int], ...]


class Window(NamedTuple):
    source_occurrence: int
    service_from: str
    service_to: str | None
    evidence: str


class Entry(NamedTuple):
    definition: Definition
    values: tuple[tuple[str, str, int], ...]
    window: Window


class _RawDefinition(TypedDict):
    product_code: str
    name: str
    procedural: bool
    chapter: str
    notes: str | None
    scopes: list[tuple[str, int]]


class _RawWindow(TypedDict):
    source_occurrence: int
    service_from: str
    service_to: str | None
    evidence: str


class _RawEntry(TypedDict):
    definition: _RawDefinition
    values: list[tuple[str, str, int]]
    window: _RawWindow


type Snapshot = tuple[tuple[GroupKey, tuple[tuple[Entry, Fact], ...]], ...]


class ComparisonCache(NamedTuple):
    releases: Mapping[str, Snapshot]
    charged_bytes: int


def _size(value: object) -> int:
    # The retained graph contains only fixed-depth tuples, frozen Facts and
    # scalar values. Charge shared objects repeatedly: conservative, bounded
    # accounting without a growing identity set or UTF-8-only size estimates.
    result = sys.getsizeof(value)
    if isinstance(value, tuple):
        return result + sum(_size(item) for item in cast(tuple[object, ...], value))
    if isinstance(value, Fact):
        return (
            result
            + sys.getsizeof(vars(value))
            + sum(
                sys.getsizeof(name)
                for name in ("category", "identity", "value", "context")
            )
            + _size((value.category, value.identity, value.value, value.context))
        )
    return result


def _release(connection: sqlite3.Connection, release: str) -> Snapshot:
    with query_budget(connection) as budget:
        projection = Projection(
            budget, max_facts=MAX_GROUPS, max_bytes=MAX_SOURCE_BYTES
        )
        project_catalog(
            connection,
            release,
            projection,
            schedule_only=True,
            max_rows=MAX_SOURCE_ROWS,
            max_bytes=MAX_SOURCE_BYTES,
        )
        result: list[tuple[GroupKey, tuple[tuple[Entry, Fact], ...]]] = []
        count = 0
        for schedule in projection.facts():
            # This JSON is the local projection above, not an external payload;
            # its source has already crossed the verified bundle boundary.
            population = cast(list[_RawEntry], json.loads(schedule.value))
            count += len(population)
            if count > MAX_SCHEDULE_ROWS:
                raise QueryLimitError("comparison schedule population exceeds bound")
            entries: list[tuple[Entry, Fact]] = []
            for raw in population:
                budget.check()
                definition, window = raw["definition"], raw["window"]
                entry = Entry(
                    Definition(
                        definition["product_code"],
                        definition["name"],
                        definition["procedural"],
                        definition["chapter"],
                        definition["notes"],
                        tuple(
                            (label, marker) for label, marker in definition["scopes"]
                        ),
                    ),
                    tuple(
                        (measure, unit, amount)
                        for measure, unit, amount in raw["values"]
                    ),
                    Window(**window),
                )
                fact = Fact(
                    "dated_catalog_group",
                    schedule.identity,
                    canonical({"definition": definition, "values": raw["values"]}),
                    canonical(window),
                )
                entries.append((entry, fact))
            result.append((cast(GroupKey, schedule.identity), tuple(entries)))
        return tuple(result)


def build_comparison_cache(
    connection: sqlite3.Connection, releases: tuple[str, ...]
) -> ComparisonCache:
    if len(releases) > 64 or len(set(releases)) != len(releases):
        raise QueryLimitError("comparison release inventory exceeds bound")
    completed: dict[str, Snapshot] = {}
    charged = sys.getsizeof(ComparisonCache(MappingProxyType({}), 0)) + sys.getsizeof(
        MAX_RETAINED_BYTES
    )
    with query_budget(connection, seconds=MAX_PRELOAD_SECONDS):
        for release in releases:
            snapshot = _release(connection, release)
            charged += _size(release) + _size(snapshot)
            if charged + sys.getsizeof(completed) > MAX_RETAINED_BYTES:
                raise QueryLimitError("comparison cache exceeds retained byte bound")
            completed[release] = snapshot
        charged += sys.getsizeof(completed) + sys.getsizeof(MappingProxyType(completed))
        if charged > MAX_RETAINED_BYTES:
            raise QueryLimitError("comparison cache exceeds retained byte bound")
    return ComparisonCache(MappingProxyType(completed), charged)


def select_snapshot(
    snapshot: Snapshot, day: str, budget: QueryBudget
) -> tuple[dict[GroupKey, Entry], tuple[Fact, ...]]:
    selected: dict[GroupKey, Entry] = {}
    facts: list[Fact] = []
    for key, population in snapshot:
        budget.check()
        for entry, fact in population:
            window = entry.window
            if window.service_from <= day and (
                window.service_to is None or day <= window.service_to
            ):
                if key in selected:
                    raise DiffInputError(
                        "several catalog rows apply to one group and date"
                    )
                selected[key] = entry
                facts.append(fact)
    return selected, tuple(facts)
