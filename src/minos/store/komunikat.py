"""Exact-code rank evidence, never a fallback rule or an inferred rank zero."""

import sqlite3
from typing import cast

from minos.domain.search import SearchInputError
from minos.ingest.komunikat_manifest import KomunikatManifest
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.query_budget import query_budget
from minos.store.reference_build import reference_sql


def procedure_rank_evidence(
    connection: sqlite3.Connection,
    manifest: DatasetManifest,
    icd9_release_id: str,
    code: str,
) -> dict[str, object]:
    source = next(
        (source for source in manifest.sources if source.release_id == icd9_release_id),
        None,
    )
    if source is None or (source.system, source.origin) != ("icd9-pl", "czs"):
        raise SearchInputError(
            "rank reference requires a selected canonical ICD-9 catalog"
        )
    if not code or len(code) > 16:
        raise SearchInputError("invalid procedure code")
    with query_budget(connection):
        canonical = cast(
            tuple[int] | None,
            connection.execute(
                "SELECT 1 FROM concept WHERE source_release_id=? AND lookup_code=?",
                (icd9_release_id, code),
            ).fetchone(),
        )
    if canonical is None:
        raise SearchInputError("code is not in the selected canonical ICD-9 catalog")
    evidence = next(
        (
            item
            for item in manifest.komunikaty
            if item.icd9_release_id == icd9_release_id
        ),
        None,
    )
    result: dict[str, object] = {
        "code": code,
        "icd9_release_id": icd9_release_id,
        "grouper_type": "SZP",
        "rank": None,
        "status": "not_selected",
        "ruleset_binding": "not_bound",
    }
    if evidence is None:
        return result
    with query_budget(connection):
        row = cast(
            tuple[int, str, str, int] | None,
            connection.execute(
                reference_sql("queries/procedure_rank.sql"), (evidence.release_id, code)
            ).fetchone(),
        )
    result["status"] = "recorded" if row else "not_listed"
    result.update(_source_metadata(evidence))
    if row is not None:
        result.update(
            {
                "rank": row[0],
                "source_title": row[1],
                "worksheet": row[2],
                "source_row": row[3],
            }
        )
    return result


def _source_metadata(evidence: KomunikatManifest) -> dict[str, object]:
    _, month, year = evidence.algorithm_version.split(".")
    return {
        "pairing": evidence.pairing,
        "algorithm_release_id": evidence.release_id,
        "algorithm_version": evidence.algorithm_version,
        "first_reporting_month": f"{year}-{int(month):02d}",
        "publisher": evidence.publisher,
        "license": evidence.license,
        "notices": evidence.notices,
        "algorithm_source": evidence.algorithm.model_dump(mode="json"),
        "rank_source": evidence.ranks.model_dump(mode="json"),
    }
