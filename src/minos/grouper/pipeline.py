"""Grouping entry point: one verified bundle, explicit date and reporting month.

Preparation (qualification, exclusions, ranks, age, duration) runs through
the exact registry; candidates come from attachment 9 directional lists.
The approved condition subset determines eligibility, with source-bound points
and provider-choice ranking. Explainability retains every consulted condition.

`group_request` is the application service for the CLI and HTTP adapters: it
returns domain values and never a wire schema (decision 2026-10-08).
"""

import sqlite3
import threading
from datetime import date
from typing import cast, final

from minos.domain.encounter import Encounter
from minos.domain.grouper import (
    AlgorithmData,
    ExecutionKey,
    GrouperError,
    ProductCatalogUnavailable,
    ProductClassification,
)
from minos.domain.grouper_candidates import (
    CandidateResult,
    Catalog,
    DirectionalIndex,
)
from minos.domain.grouper_rules import (
    FinalPoints,
    GroupingResult,
    GroupRules,
    ModifierBindingUnbound,
    StayFacts,
)
from minos.domain.grouping_request import (
    GroupingOutcome,
    GroupingRequest,
    ProductCatalogMissing,
    UnsupportedProductReport,
)
from minos.domain.reference import SourceError
from minos.grouper.budget import checkpoint, evaluation_budget
from minos.grouper.candidates import derive
from minos.grouper.conditions import evaluate_group, facts_from
from minos.grouper.near_misses import near_misses
from minos.grouper.points import base_points
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.grouper.szp_2026 import ENGINE_VERSION
from minos.grouper.tariff import final_group_points
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import dataset_identity
from minos.store.grouper_bundle import (
    CharacteristicsGraph,
    grouping_sources,
    index_from_graph,
    load_bundle_algorithm,
    load_graph,
)
from minos.store.grouper_rules import group_rules, group_titles
from minos.store.jgp_modifier_binding import (
    require_modifier_binding,
    reviewed_group_modifiers,
)
from minos.store.jgp_ruleset import resolve_ruleset
from minos.store.product_catalogs import (
    product_names,
    read_product_classification,
)
from minos.store.query_budget import query_budget


@final
class PreparedSources:
    """Release-static grouping inputs, decoded and validated once (#149).

    Own exactly one per opened, checksum-verified bundle and never share it
    across bundles: entries are keyed by release, not by database bytes. The
    first request builds an entry under the lock; later requests reuse it.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._algorithm: AlgorithmData | None = None
        self._sheets: dict[
            tuple[str, Catalog], tuple[CharacteristicsGraph, DirectionalIndex]
        ] = {}
        # Keyed by catalog release; None records that its act has no pins.
        self._products: dict[str, ProductClassification | None] = {}
        # Set once every sheet of the bundle is built; requests never wait on it.
        self.warmed = threading.Event()

    def algorithm(self, connection: sqlite3.Connection) -> AlgorithmData:
        with self._lock:
            if self._algorithm is None:
                self._algorithm = load_bundle_algorithm(connection)
            return self._algorithm

    def sheet(
        self, connection: sqlite3.Connection, release_id: str, catalog: Catalog
    ) -> tuple[CharacteristicsGraph, DirectionalIndex]:
        with self._lock:
            found = self._sheets.get((release_id, catalog))
            if found is None:
                graph = load_graph(connection, release_id, catalog)
                found = (graph, index_from_graph(graph))
                self._sheets[(release_id, catalog)] = found
            return found

    def products(
        self, connection: sqlite3.Connection, catalog_release_id: str
    ) -> ProductClassification | None:
        with self._lock:
            if catalog_release_id not in self._products:
                if len(self._products) >= 64:
                    raise GrouperError("product classifications exceed their bound")
                self._products[catalog_release_id] = read_product_classification(
                    connection, catalog_release_id
                )
            return self._products[catalog_release_id]


def _prepare(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    service_date: date,
    reporting_month: date,
    prepared: PreparedSources | None,
) -> tuple[CandidateResult, tuple[CharacteristicsGraph, ...]]:
    if type(encounter) is not Encounter or type(service_date) is not date:
        raise GrouperError("invalid candidate derivation request")
    # The governing date is the caller's explicit choice, but it must belong
    # to the episode; a date outside it would select unrelated rules.
    if not encounter.admitted_on <= service_date <= encounter.discharged_on:
        raise GrouperError("service date is outside the episode")
    prepared = prepared if prepared is not None else PreparedSources()
    algorithm = prepared.algorithm(connection)
    sources = grouping_sources(connection, dataset_id, service_date, algorithm)
    key = ExecutionKey(
        dataset_id,
        algorithm.metadata.version,
        ENGINE_VERSION,
        reporting_month,
        algorithm.identity,
    )
    # Products are classified by exactly the catalogs of the act governing
    # the service date, older acts included (owner decision 2026-10-09).
    classification = prepared.products(connection, sources.catalog_release_id)
    if classification is None and encounter.settlement_products:
        raise ProductCatalogUnavailable("no product catalogs govern the service date")
    preparation = EngineRegistry((EngineBinding(key, algorithm),)).execute(
        key, encounter, classification
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
    sheets = tuple(
        prepared.sheet(connection, sources.characteristics_release_id, catalog)
        for (catalog,) in catalogs
    )
    graphs = tuple(graph for graph, _ in sheets)
    return derive(preparation, tuple(index for _, index in sheets), sources), graphs


def derive_candidates(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    *,
    service_date: date,
    reporting_month: date,
    prepared: PreparedSources | None = None,
) -> CandidateResult:
    return _prepare(
        connection, dataset_id, encounter, service_date, reporting_month, prepared
    )[0]


def evaluate_candidates(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    *,
    service_date: date,
    reporting_month: date,
    prepared: PreparedSources | None = None,
) -> GroupingResult:
    """Candidates, approved-subset verdicts, base points and the valid ranking."""
    candidates, graphs = _prepare(
        connection, dataset_id, encounter, service_date, reporting_month, prepared
    )
    facts = facts_from(candidates.preparation, encounter.discharge_mode)
    rules: dict[tuple[str, str], GroupRules] = {}
    for graph in graphs:
        checkpoint()
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
        near_misses=tuple(
            miss
            for item, evaluation in zip(chosen, evaluations, strict=True)
            for miss in near_misses(item, facts, evaluation)
        ),
    )


def final_points(
    connection: sqlite3.Connection,
    dataset: DatasetManifest,
    result: GroupingResult,
    declared: frozenset[str],
) -> tuple[FinalPoints, ...]:
    """Final points for every valid group, highest total first (#35).

    Without a reviewed MODTAR binding for the service date this refuses with
    `ModifierBindingUnbound`; base points are never returned as final points.
    Provider and encounter facts the encounter lacks come from `declared`
    modifier symbols (owner decision 2026-10-07).
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
    points = {
        (c.catalog, c.group): p
        for c, p in zip(result.candidates.candidates, result.points, strict=True)
    }
    answers: list[FinalPoints] = []
    for catalog, group in result.ranking:
        checkpoint()
        reviewed = reviewed_group_modifiers(connection, dataset, ruleset, group)
        mappings = [
            *cast(list[dict[str, object]], reviewed["dictionary_mappings"]),
            *cast(list[dict[str, object]], reviewed["legal_supplements"]),
        ]
        answers.append(
            final_group_points(
                catalog,
                group,
                points[(catalog, group)],
                mappings,
                declared,
                result.candidates.preparation.completed_years,
                sources.service_date,
            )
        )
    answers.sort(
        key=lambda item: (
            item.total_hundredths is None,
            -(item.total_hundredths or 0),
            item.catalog,
            item.group,
        )
    )
    return tuple(answers)


def group_request(
    connection: sqlite3.Connection,
    dataset: DatasetManifest,
    request: GroupingRequest,
    prepared: PreparedSources | None = None,
) -> GroupingOutcome | UnsupportedProductReport | ProductCatalogMissing:
    """One caller-owned verified lease, bounded as a whole; nothing is stored."""
    facts = request.encounter
    if facts.product_reporting != "complete":
        return UnsupportedProductReport()
    if not dataset.characteristics or not dataset.komunikaty:
        raise GrouperError("grouping sources unavailable")
    with (
        evaluation_budget(),
        query_budget(connection, seconds=3, operations=20_000_000),
    ):
        try:
            result = evaluate_candidates(
                connection,
                dataset_identity(dataset),
                facts,
                service_date=request.service_date,
                reporting_month=request.reporting_month,
                prepared=prepared,
            )
        except ProductCatalogUnavailable:
            return ProductCatalogMissing()
        try:
            finals = final_points(
                connection, dataset, result, request.declared_modifiers
            )
            bound = True
        except ModifierBindingUnbound:
            finals, bound = (), False
        titles = group_titles(
            connection,
            result.candidates.sources.catalog_release_id,
            request.service_date,
        )
        preparation = result.candidates.preparation
        names = (
            product_names(
                connection,
                preparation.product_release_id,
                frozenset(p.code for p in preparation.settlement_products),
            )
            if preparation.product_release_id is not None
            else {}
        )
    return GroupingOutcome(result, finals, bound, titles, names)
