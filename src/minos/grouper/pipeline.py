"""#34 entry point: one verified bundle, an explicit date and reporting month.

Preparation (qualification, exclusions, ranks, age, duration) runs through
the exact registry; candidates come from attachment 9 directional lists.
Conditions, selection and points remain #35.
"""

import sqlite3
from datetime import date
from typing import NoReturn, cast

from minos.domain.encounter import Encounter
from minos.domain.grouper import ExecutionKey, GrouperError
from minos.domain.grouper_candidates import CandidateResult, Catalog
from minos.domain.grouper_rules import (
    GroupingResult,
    GroupRules,
    ModifierBindingUnbound,
    ModifierEligibilityUnsupported,
    StayFacts,
)
from minos.domain.reference import SourceError
from minos.grouper.candidates import derive
from minos.grouper.conditions import evaluate_group, facts_from
from minos.grouper.points import base_points
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.grouper.szp_2026 import ENGINE_VERSION
from minos.store.grouper_bundle import (
    CharacteristicsGraph,
    grouping_sources,
    index_from_graph,
    load_bundle_algorithm,
    load_graph,
)
from minos.store.grouper_rules import group_rules
from minos.store.jgp_modifier_binding import require_modifier_binding
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.query_budget import query_budget


def _prepare(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    service_date: date,
    reporting_month: date,
) -> tuple[CandidateResult, tuple[CharacteristicsGraph, ...]]:
    if type(encounter) is not Encounter or type(service_date) is not date:
        raise GrouperError("invalid candidate derivation request")
    # The governing date is the caller's explicit choice, but it must belong
    # to the episode; a date outside it would select unrelated rules.
    if not encounter.admitted_on <= service_date <= encounter.discharged_on:
        raise GrouperError("service date is outside the episode")
    algorithm = load_bundle_algorithm(connection)
    sources = grouping_sources(connection, dataset_id, service_date, algorithm)
    key = ExecutionKey(
        dataset_id,
        algorithm.metadata.version,
        ENGINE_VERSION,
        reporting_month,
        algorithm.identity,
    )
    preparation = EngineRegistry((EngineBinding(key, algorithm),)).execute(
        key, encounter
    )
    with query_budget(connection):
        catalogs = cast(
            list[tuple[Catalog]],
            connection.execute(
                """SELECT catalog FROM characteristics_sheet WHERE release_id=?
                ORDER BY catalog LIMIT 3""",
                (sources.characteristics_release_id,),
            ).fetchall(),
        )
    graphs = tuple(
        load_graph(connection, sources.characteristics_release_id, catalog)
        for (catalog,) in catalogs
    )
    indexes = tuple(index_from_graph(graph) for graph in graphs)
    return derive(preparation, indexes, sources), graphs


def derive_candidates(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    *,
    service_date: date,
    reporting_month: date,
) -> CandidateResult:
    return _prepare(connection, dataset_id, encounter, service_date, reporting_month)[0]


def evaluate_candidates(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    *,
    service_date: date,
    reporting_month: date,
) -> GroupingResult:
    """Candidates, approved-subset verdicts, base points and the valid ranking."""
    candidates, graphs = _prepare(
        connection, dataset_id, encounter, service_date, reporting_month
    )
    facts = facts_from(candidates.preparation)
    rules: dict[tuple[str, str], GroupRules] = {}
    for graph in graphs:
        codes = [c.group for c in candidates.candidates if c.catalog == graph.catalog]
        for item in group_rules(
            connection,
            graph,
            candidates.sources.catalog_release_id,
            service_date,
            codes,
        ):
            rules[(item.catalog, item.code)] = item
    stay = StayFacts(
        encounter.hospitalization_type,
        encounter.admitted_on == encounter.discharged_on,
        facts.days,
    )
    chosen = [rules[(c.catalog, c.group)] for c in candidates.candidates]
    evaluations = tuple(evaluate_group(item, facts) for item in chosen)
    points = tuple(base_points(item, stay) for item in chosen)
    valid = [
        (evaluation, amount.total_hundredths)
        for evaluation, amount in zip(evaluations, points, strict=True)
        if evaluation.status == "valid"
    ]
    # Unknown points sort after known ones; ties keep catalog and code order.
    valid.sort(
        key=lambda item: (
            item[1] is None,
            -(item[1] or 0),
            item[0].catalog,
            item[0].group,
        )
    )
    return GroupingResult(
        candidates,
        evaluations,
        points,
        tuple((evaluation.catalog, evaluation.group) for evaluation, _ in valid),
    )


def final_points(connection: sqlite3.Connection, result: GroupingResult) -> NoReturn:
    """#35 step 4: base points are never returned as final points.

    An unbound, missing or mismatched MODTAR binding refuses explicitly. A
    reviewed binding still refuses until modifier eligibility is evaluated;
    that is a distinct error, never "no modifiers apply".
    """
    sources = result.candidates.sources
    ruleset = resolve_ruleset(connection, sources.service_date, None)
    if ruleset["release_id"] != sources.catalog_release_id:
        raise GrouperError("final points ruleset conflicts with grouping sources")
    try:
        _ = require_modifier_binding(connection, ruleset)
    except SourceError as error:
        raise ModifierBindingUnbound(
            "final points need a reviewed MODTAR binding for the service date"
        ) from error
    raise ModifierEligibilityUnsupported(
        "modifier eligibility is not evaluated; no final points"
    )
