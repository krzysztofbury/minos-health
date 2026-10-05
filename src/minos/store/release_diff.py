"""Explicit pinned-release comparisons over verified, read-only bundle leases."""

import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from minos.domain.reference import QueryLimitError
from minos.domain.release_diff import Change, DiffInputError, compare_facts
from minos.ingest.jgp_catalog_manifest import CatalogManifest
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.ingest.reference_manifest import DatasetManifest, SourceManifest
from minos.store.artifact import ArtifactManifest
from minos.store.diff_catalog import project_catalog
from minos.store.diff_conditions import project_conditions
from minos.store.diff_records import Projection, fingerprint, rows
from minos.store.diff_terminology import project_terminology
from minos.store.query_budget import QueryBudget, query_budget
from minos.store.reference import open_reference_bundle

MAX_PAIRS = 16
MAX_CHANGES = 100_000
MAX_CHANGE_BYTES = 32 * 1024 * 1024
DIFF_SECONDS = 120.0
DIFF_SQL_OPERATIONS = 100_000_000


@dataclass(frozen=True)
class Comparison:
    before: ArtifactManifest
    after: ArtifactManifest
    pairs: tuple[str, ...]
    changes: tuple[Change, ...]
    budget: QueryBudget


def compare_bundles(before: Path, after: Path, pairs: tuple[str, ...]) -> Comparison:
    if not 1 <= len(pairs) <= MAX_PAIRS or len(set(pairs)) != len(pairs):
        raise DiffInputError("invalid release pair inventory")
    with ExitStack() as stack:
        left, left_manifest = stack.enter_context(open_reference_bundle(before))
        if before.resolve() == after.resolve():
            right, right_manifest = left, left_manifest
        else:
            right, right_manifest = stack.enter_context(open_reference_bundle(after))
        # Bundle verification has its own existing input/integrity bounds.
        # The shared computation deadline begins after both verified leases open.
        budget = QueryBudget(seconds=DIFF_SECONDS, operations=DIFF_SQL_OPERATIONS)
        changes: list[Change] = []
        retained_bytes = 0
        for pair in pairs:
            parts = pair.split(":")
            if len(parts) != 3:
                raise DiffInputError("invalid release pair")
            family, old, new = parts
            first = _selected(left_manifest.dataset, family, old)
            second = _selected(right_manifest.dataset, family, new)
            if isinstance(first, SourceManifest) and isinstance(second, SourceManifest):
                if (first.system, first.origin, first.context_for is None) != (
                    second.system,
                    second.origin,
                    second.context_for is None,
                ):
                    raise DiffInputError("incompatible terminology namespaces or roles")
            with query_budget(left, shared=budget):
                old_facts = _project(
                    left, left_manifest.dataset, first, family, old, budget
                )
            with query_budget(right, shared=budget):
                new_facts = _project(
                    right, right_manifest.dataset, second, family, new, budget
                )
            for change in compare_facts(pair, old_facts.facts(), new_facts.facts()):
                retained_bytes += sum(
                    len(value.encode())
                    for value in (
                        change.pair,
                        change.category,
                        *change.identity,
                        change.before,
                        change.after,
                        change.before_context,
                        change.after_context,
                    )
                    if value is not None
                )
                if retained_bytes > MAX_CHANGE_BYTES:
                    raise QueryLimitError("release diff retained byte bound exceeded")
                changes.append(change)
            if len(changes) > MAX_CHANGES:
                raise QueryLimitError("release diff change bound exceeded")
            budget.check()
        return Comparison(left_manifest, right_manifest, pairs, tuple(changes), budget)


def _selected(dataset: DatasetManifest, family: str, identity: str) -> BaseModel:
    inventories: dict[
        str, tuple[SourceManifest | CatalogManifest | ConditionsManifest, ...]
    ] = {
        "terminology": dataset.sources,
        "catalog": dataset.catalogs,
        "conditions": dataset.characteristics,
    }
    if family not in inventories:
        raise DiffInputError("unsupported comparison family")
    for item in inventories[family]:
        if item.release_id == identity:
            return item
    raise DiffInputError("selected release is not pinned in this bundle")


def _project(
    connection: sqlite3.Connection,
    dataset: DatasetManifest,
    source: BaseModel,
    family: str,
    release: str,
    budget: QueryBudget,
) -> Projection:
    output = Projection(budget)
    metadata = source.model_dump(mode="json", exclude={"release_id"})
    output.add(
        "selected_manifest_identity", ("release",), {"sha256": fingerprint(metadata)}
    )
    output.add(
        "interpretation",
        ("release",),
        source.model_dump(mode="json", include={"layout", "sheets"}),
    )
    output.add(
        "release_provenance",
        ("release",),
        source.model_dump(
            mode="json",
            include={
                "source_version": True,
                "source_file_id": True,
                "publisher": True,
                "license": True,
                "notices": True,
                "sha256": True,
                "size_bytes": True,
                "fetched_at": True,
                "workbook": {"sha256", "size_bytes", "fetched_at"},
                "baw_document_id": True,
                "act_number": True,
            },
        ),
    )
    output.add(
        "release_applicability",
        ("release",),
        source.model_dump(
            mode="json",
            include={
                "source_created_at",
                "valid_from",
                "valid_to",
                "act_date",
                "entry_into_force",
                "published_on",
                "service_from",
                "service_evidence",
                "applicability_evidence",
                "supersedes",
                "source_document_id",
                "lineage_document_id",
                "introductory_document_id",
            },
        ),
    )
    output.add(
        "release_pairing",
        ("release",),
        source.model_dump(
            mode="json",
            include={
                "system",
                "origin",
                "context_for",
                "icd9_release_id",
                "icd10_release_id",
                "catalog_release_id",
                "modifier_binding",
                "correspondence",
            },
        ),
    )
    output.add(
        "build_policy",
        ("dataset",),
        {
            "transform_version": dataset.transform_version,
            "lockfile_sha256": dataset.lockfile_sha256,
            "search_profile": dataset.search_profile,
        },
    )
    if family == "terminology":
        headers = rows(
            connection, "SELECT header_json FROM source_release WHERE id=?", (release,)
        )
        output.add(
            "source_header_identity", ("release",), {"sha256": fingerprint(headers)}
        )
        project_terminology(connection, release, output)
        output.add(
            "supporting_pairing",
            ("terminology",),
            [
                item.release_id
                for item in dataset.sources
                if item.context_for == release
            ],
        )
        output.add(
            "algorithm_pairing",
            ("terminology",),
            [
                item.model_dump(
                    mode="json",
                    include={
                        "release_id": True,
                        "algorithm_version": True,
                        "pairing": True,
                        "algorithm": {"sha256"},
                        "ranks": {"sha256"},
                        "specialties": {"sha256"},
                    },
                )
                for item in dataset.komunikaty
                if item.icd9_release_id == release
            ],
        )
    elif family == "catalog":
        project_catalog(connection, release, output)
        output.add(
            "knowledge",
            ("catalog",),
            {
                "as_of": str(dataset.catalog_knowledge_as_of),
                "evidence": dataset.catalog_knowledge_evidence,
            },
        )
        bindings = [
            item.model_dump(mode="json", exclude={"catalog_release_id"})
            for item in dataset.modifier_bindings
            if item.catalog_release_id == release
        ]
        candidates = [
            item.model_dump(mode="json", exclude={"catalog_release_id"})
            for item in dataset.modifier_candidates
            if item.catalog_release_id == release
        ]
        output.add(
            "modifier_pairing",
            ("catalog",),
            {
                "binding": "reviewed" if bindings else "unbound",
                "reviewed": bindings,
                "pending": candidates,
            },
        )
    else:
        project_conditions(connection, release, output)
        output.add(
            "knowledge",
            ("conditions",),
            {
                "as_of": str(dataset.characteristics_knowledge_as_of),
                "evidence": dataset.characteristics_knowledge_evidence,
            },
        )
    return output
