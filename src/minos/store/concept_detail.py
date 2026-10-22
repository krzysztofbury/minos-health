"""Release-pinned terminology facts with separately attributed dated JGP context."""

import json
import sqlite3
from dataclasses import asdict
from datetime import date
from typing import Literal, cast

from minos.domain.jgp_ruleset import NoRulesetForDate
from minos.domain.reference import ArtifactError, QueryLimitError, ReferenceError
from minos.domain.search import SearchInputError
from minos.ingest.reference_manifest import SourceManifest
from minos.store.artifact import ArtifactManifest, dataset_identity
from minos.store.icd11_maps import map_rows
from minos.store.jgp_condition_reference import code_memberships
from minos.store.komunikat import procedure_rank_evidence
from minos.store.query_budget import query_budget
from minos.store.reference import ReferenceReader

MAX_RESPONSE_BYTES = 1024 * 1024
# ICD-11 NE61 has 484 index-term children (#53).
MAX_NEIGHBOURS = 1000


class ContextReleaseMismatch(ReferenceError):
    """Dated attachment 9 evidence is bound to a different terminology release."""


def provenance(source: SourceManifest) -> dict[str, object]:
    return {
        "release_id": source.release_id,
        "system": source.system,
        "origin": source.origin,
        "source_version": source.source_version,
        "source_sha256": source.sha256,
        "source_url": source.source_url,
        "publisher": source.publisher,
        "license": source.license,
        "notices": list(source.notices),
        "fetched_at": source.fetched_at.isoformat(),
        "source_created_at": source.source_created_at.isoformat()
        if source.source_created_at
        else None,
        "declared_valid_from": source.valid_from.isoformat()
        if source.valid_from
        else None,
        "declared_valid_to": source.valid_to.isoformat() if source.valid_to else None,
    }


def neighbours(
    connection: sqlite3.Connection, release: str, ordinal: int, *, parents: bool
) -> list[dict[str, object]]:
    rows = cast(
        list[tuple[int, str, str, str, str | None, str]],
        connection.execute(
            """SELECT n.ordinal,n.kind,n.source_code,n.source_text,c.lookup_code,r.relation_type
        FROM reference_relation r JOIN reference_node n
          ON n.source_release_id=r.source_release_id AND n.ordinal=r.parent_ordinal
        LEFT JOIN concept c ON c.source_release_id=n.source_release_id AND c.node_ordinal=n.ordinal
        WHERE r.source_release_id=? AND r.child_ordinal=?
        ORDER BY n.ordinal,r.relation_type LIMIT ?"""
            if parents
            else """SELECT n.ordinal,n.kind,n.source_code,n.source_text,c.lookup_code,r.relation_type
        FROM reference_relation r JOIN reference_node n
          ON n.source_release_id=r.source_release_id AND n.ordinal=r.child_ordinal
        LEFT JOIN concept c ON c.source_release_id=n.source_release_id AND c.node_ordinal=n.ordinal
        WHERE r.source_release_id=? AND r.parent_ordinal=?
        ORDER BY n.ordinal,r.relation_type LIMIT ?""",
            (release, ordinal, MAX_NEIGHBOURS + 1),
        ).fetchall(),
    )
    if len(rows) > MAX_NEIGHBOURS:
        raise QueryLimitError("concept neighbours exceed bound")
    return [
        {
            "ordinal": n,
            "kind": kind,
            "source_code": raw,
            "label": label,
            "code": code,
            "relation_type": relation,
        }
        for n, kind, raw, label, code, relation in rows
    ]


def detail(
    connection: sqlite3.Connection, source: SourceManifest, code: str
) -> dict[str, object] | None:
    concept = ReferenceReader(connection).concept(source.release_id, code)
    if concept is None:
        return None
    rows = cast(
        list[tuple[str, str]],
        connection.execute(
            "SELECT name,source_text FROM reference_node_attribute WHERE source_release_id=? AND node_ordinal=? ORDER BY source_order LIMIT 65",
            (source.release_id, concept.node_ordinal),
        ).fetchall(),
    )
    if len(rows) > 64:
        raise QueryLimitError("concept attributes exceed bound")
    return {
        **asdict(concept),
        "source_attributes": dict(rows),
        "parents": neighbours(
            connection, source.release_id, concept.node_ordinal, parents=True
        ),
        "children": neighbours(
            connection, source.release_id, concept.node_ordinal, parents=False
        ),
    }


def dated_memberships(
    connection: sqlite3.Connection,
    manifest: ArtifactManifest,
    source: SourceManifest,
    code: str,
    day: date,
    catalog: Literal["1a", "1ae"] | None,
    page: int,
) -> dict[str, object]:
    answers: list[dict[str, object]] = []
    for selected in (catalog,) if catalog else ("1a", "1ae"):
        try:
            if not manifest.dataset.characteristics:
                raise NoRulesetForDate("no characteristics in bundle")
            result = code_memberships(
                connection,
                cast(Literal["icd10-pl", "icd9-pl"], source.system),
                code,
                selected,
                day,
                page,
            )
            evidence = cast(dict[str, object], result["source"])
            bound = next(
                (
                    item
                    for item in manifest.dataset.characteristics
                    if item.release_id == evidence["release_id"]
                ),
                None,
            )
            if bound is None:
                raise ArtifactError("characteristics are absent from manifest")
            expected = (
                bound.icd9_release_id
                if source.system == "icd9-pl"
                else bound.icd10_release_id
            )
            if expected != source.release_id:
                raise ContextReleaseMismatch(
                    "selected terminology differs from characteristics binding"
                )
            answers.append(
                {"status": "covered", **result, "terminology_release_id": expected}
            )
        except NoRulesetForDate:
            answers.append({"status": "context_not_covered", "catalog": selected})
    if all(answer["status"] != "covered" for answer in answers):
        raise NoRulesetForDate("no characteristics cover selected date")
    horizon = manifest.dataset.characteristics_knowledge_as_of
    return {
        "service_date": day.isoformat(),
        "knowledge_as_of": horizon.isoformat() if horizon else None,
        "beyond_knowledge_horizon": day > horizon if horizon else None,
        "catalogs": answers,
    }


def concept_detail(
    connection: sqlite3.Connection,
    manifest: ArtifactManifest,
    system: str,
    release_id: str,
    code: str,
    *,
    service_date: date | None = None,
    catalog: Literal["1a", "1ae"] | None = None,
    page: int = 1,
) -> bytes:
    """One shared SQL/Python deadline; fail closed instead of truncating facts."""
    with query_budget(connection) as budget:
        source = next(
            (
                s
                for s in manifest.dataset.sources
                if s.release_id == release_id
                and s.system == system
                and s.context_for is None
            ),
            None,
        )
        if source is None:
            raise SearchInputError("release is not a selectable catalog")
        concept = detail(connection, source, code)
        if concept is None:
            raise SearchInputError("concept is not in selected release")
        supporting: list[dict[str, object]] = []
        for evidence in manifest.dataset.sources:
            if evidence.context_for != release_id:
                continue
            context = detail(connection, evidence, code)
            if context is not None:
                supporting.append(
                    {
                        "release": provenance(evidence),
                        "concept": context,
                        "meaning": "Source-local exact-code context; does not override the selected dictionary.",
                    }
                )
        ranks = (
            procedure_rank_evidence(connection, manifest.dataset, release_id, code)
            if (source.system, source.origin) == ("icd9-pl", "czs")
            else None
        )
        result = {
            "api": 1,
            "status": "ok",
            "system": system,
            "dataset_id": dataset_identity(manifest.dataset),
            "database_sha256": manifest.database_sha256,
            "transform_version": manifest.dataset.transform_version,
            "release": provenance(source),
            "concept": concept,
            "supporting_contexts": supporting,
            "date_semantics": "Dictionary selected by exact release, not service date. Source dates/status do not establish settlement applicability.",
            "rank_evidence": {
                "scope": "release",
                "applicable_to_service_date": None,
                "evidence": ranks,
            }
            if ranks is not None
            else None,
            "icd11_map": {
                "release_id": maps.release_id,
                "notices": list(maps.notices),
                "rows": map_rows(connection, maps, system, code),
            }
            if (maps := manifest.dataset.icd11_maps) is not None
            and system in ("icd10-pl", "icd11-pl")
            else None,
            "jgp": dated_memberships(
                connection, manifest, source, code, service_date, catalog, page
            )
            if service_date
            else None,
        }
        payload = bytearray()
        for chunk in json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).iterencode(result):
            budget.check()
            encoded = chunk.encode()
            if len(payload) + len(encoded) > MAX_RESPONSE_BYTES:
                raise QueryLimitError("concept response exceeds byte bound")
            payload.extend(encoded)
        return bytes(payload)
