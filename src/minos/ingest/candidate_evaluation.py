"""Build a shadow dataset candidate and evaluate it against the live bundle.

Evaluation never activates anything (#141 phase B). It proves a deterministic
build, bounds the terminology change, and checks that everything the live
bundle answers for earlier periods is unchanged in the candidate.
"""

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import ClassVar, Literal, Protocol, cast

from pydantic import BaseModel, ConfigDict

from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.icd10_candidate import CandidateRejected, Criterion
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import (
    ARTIFACT_MANIFEST_NAME,
    DATABASE_NAME,
    ArtifactManifest,
    dataset_identity,
)

# Tables whose rows a new terminology release adds to. Every other table holds
# catalogs, conditions or dictionaries and must keep its live row count.
_TERMINOLOGY_TABLES = frozenset(
    {
        "concept",
        "concept_description",
        "reference_node",
        "reference_node_attribute",
        "reference_relation",
        "search_document",
        "search_owner",
        "search_token",
        "search_word",
        "search_word_folded",
        "search_word_lemma",
        "source_release",
    }
)
_RELEASE_ROWS = (
    """SELECT ordinal,source_code,source_text,kind FROM reference_node
    WHERE source_release_id=? ORDER BY ordinal""",
    """SELECT node_ordinal,source_order,name,source_text FROM reference_node_attribute
    WHERE source_release_id=? ORDER BY node_ordinal,source_order""",
    """SELECT parent_ordinal,child_ordinal,relation_type FROM reference_relation
    WHERE source_release_id=? ORDER BY parent_ordinal,child_ordinal,relation_type""",
    """SELECT node_ordinal,lookup_code FROM concept
    WHERE source_release_id=? ORDER BY node_ordinal""",
    """SELECT node_ordinal,language,description_type,source_text FROM concept_description
    WHERE source_release_id=? ORDER BY node_ordinal,language,description_type""",
)


class RegressionBounds(Protocol):
    @property
    def max_removed_code_ratio(self) -> float: ...
    @property
    def max_concept_count_change_ratio(self) -> float: ...


class CandidateReport(BaseModel):
    """Privacy-safe outcome of one candidate; it carries no paths or source text."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    decision: Literal["would_activate", "rejected"]
    criterion: Criterion | None = None
    code: str | None = None
    previous_release_id: str | None = None
    release_id: str | None = None
    dataset_id: str | None = None
    database_sha256: str | None = None
    database_size_bytes: int | None = None
    previous_concepts: int | None = None
    concepts: int | None = None
    added_codes: int | None = None
    removed_codes: int | None = None
    changed_preferred_descriptions: int | None = None


def rejected(error: CandidateRejected) -> CandidateReport:
    return CandidateReport(
        decision="rejected", criterion=error.criterion, code=error.code
    )


def evaluate_candidate(
    candidate: DatasetManifest,
    *,
    terminology: tuple[str, str],
    live_bundle: Path,
    archive_root: Path,
    work: Path,
    policy: RegressionBounds,
) -> CandidateReport:
    """Build the candidate twice in ``work`` and judge it against the live bundle.

    ``terminology`` is the (system, origin) of the one new canonical release.
    """
    live = ArtifactManifest.model_validate_json(
        (live_bundle / ARTIFACT_MANIFEST_NAME).read_bytes()
    )
    previous_id, release_id = _release_pair(live.dataset, candidate, terminology)
    first = _build(candidate, archive_root, work / "first")
    second = _build(candidate, archive_root, work / "second")
    # Only the first bundle is kept; the second exists to prove determinism.
    shutil.rmtree(work / "second")
    if first.database_sha256 != second.database_sha256:
        raise CandidateRejected("build", "build_is_not_deterministic")
    database = work / "first" / DATABASE_NAME
    with (
        closing(_read_only(database)) as built,
        closing(_read_only(live_bundle / DATABASE_NAME)) as served,
    ):
        _require_unchanged_history(built, served, live, first)
        previous_codes = _codes(built, previous_id)
        codes = _codes(built, release_id)
        removed = previous_codes.keys() - codes.keys()
        added = codes.keys() - previous_codes.keys()
        changed = sum(
            1
            for code in previous_codes.keys() & codes.keys()
            if previous_codes[code] != codes[code]
        )
    if len(removed) > policy.max_removed_code_ratio * len(previous_codes):
        raise CandidateRejected("regression", "removed_codes_exceed_bound")
    change = abs(len(codes) - len(previous_codes))
    if change > policy.max_concept_count_change_ratio * len(previous_codes):
        raise CandidateRejected("regression", "concept_count_change_exceeds_bound")
    return CandidateReport(
        decision="would_activate",
        previous_release_id=previous_id,
        release_id=release_id,
        dataset_id=dataset_identity(candidate),
        database_sha256=first.database_sha256,
        database_size_bytes=first.database_size_bytes,
        previous_concepts=len(previous_codes),
        concepts=len(codes),
        added_codes=len(added),
        removed_codes=len(removed),
        changed_preferred_descriptions=changed,
    )


def _release_pair(
    live: DatasetManifest, candidate: DatasetManifest, terminology: tuple[str, str]
) -> tuple[str, str]:
    live_ids = {source.release_id for source in live.sources}
    added = [
        source for source in candidate.sources if source.release_id not in live_ids
    ]
    new = [source for source in added if source.context_for is None]
    # Only the new release's own supporting workbook may arrive beside it.
    if (
        len(new) != 1
        or (new[0].system, new[0].origin) != terminology
        or any(
            source.context_for != new[0].release_id
            for source in added
            if source is not new[0]
        )
    ):
        raise CandidateRejected("history", "candidate_is_not_one_new_release")
    closed = [
        source
        for source in candidate.sources
        if source.release_id in live_ids
        and (source.system, source.origin) == terminology
        and source.context_for is None
        and source.valid_to == new[0].valid_from
    ]
    if len(closed) != 1:
        raise CandidateRejected("history", "previous_release_is_not_closed")
    return closed[0].release_id, new[0].release_id


def _build(
    manifest: DatasetManifest, archive_root: Path, output: Path
) -> ArtifactManifest:
    try:
        return build_reference_artifact(manifest, archive_root, output)
    except ArtifactError, SourceError:
        raise CandidateRejected("build", "candidate_build_failed") from None


def _read_only(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _require_unchanged_history(
    built: sqlite3.Connection,
    served: sqlite3.Connection,
    live: ArtifactManifest,
    candidate: ArtifactManifest,
) -> None:
    for table, count in live.row_counts.items():
        if (
            table not in _TERMINOLOGY_TABLES
            and candidate.row_counts.get(table) != count
        ):
            raise CandidateRejected("history", "non_terminology_rows_changed")
    for release in (source.release_id for source in live.dataset.sources):
        if _release_digest(built, release) != _release_digest(served, release):
            raise CandidateRejected("history", "live_release_rows_changed")


def _release_digest(connection: sqlite3.Connection, release_id: str) -> str:
    digest = hashlib.sha256()
    for query in _RELEASE_ROWS:
        cursor = connection.execute(query, (release_id,))
        while rows := cast(list[tuple[object, ...]], cursor.fetchmany(4096)):
            for row in rows:
                digest.update(repr(row).encode())
                digest.update(b"\n")
        digest.update(b"\x00")
    return digest.hexdigest()


def _codes(connection: sqlite3.Connection, release_id: str) -> dict[str, str]:
    rows = cast(
        list[tuple[str, str]],
        connection.execute(
            """SELECT c.lookup_code, min(d.source_text) FROM concept c
            JOIN concept_description d ON d.source_release_id=c.source_release_id
            AND d.node_ordinal=c.node_ordinal AND d.description_type='preferred'
            WHERE c.source_release_id=? GROUP BY c.lookup_code""",
            (release_id,),
        ).fetchall(),
    )
    return dict(rows)
