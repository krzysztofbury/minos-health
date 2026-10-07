"""#34 entry point: one verified bundle, an explicit date and reporting month.

Preparation (qualification, exclusions, ranks, age, duration) runs through
the exact registry; candidates come from attachment 9 directional lists.
Conditions, selection and points remain #35.
"""

import sqlite3
from datetime import date
from typing import cast

from minos.domain.encounter import Encounter
from minos.domain.grouper import ExecutionKey, GrouperError
from minos.domain.grouper_candidates import CandidateResult, Catalog
from minos.grouper.candidates import derive
from minos.grouper.registry import EngineBinding, EngineRegistry
from minos.grouper.szp_2026 import ENGINE_VERSION
from minos.store.grouper_bundle import (
    directional_index,
    grouping_sources,
    load_bundle_algorithm,
)
from minos.store.query_budget import query_budget


def derive_candidates(
    connection: sqlite3.Connection,
    dataset_id: str,
    encounter: Encounter,
    *,
    service_date: date,
    reporting_month: date,
) -> CandidateResult:
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
    indexes = tuple(
        directional_index(connection, sources.characteristics_release_id, catalog)
        for (catalog,) in catalogs
    )
    return derive(preparation, indexes, sources)
