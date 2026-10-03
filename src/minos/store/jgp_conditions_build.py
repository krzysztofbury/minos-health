"""Transactional reference-only attachment 9 import and symmetric validation."""

import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from minos.domain import jgp_conditions as c
from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_conditions import (
    check_counts,
    parse_conditions_workbook,
    validate_source_projection,
)
from minos.ingest.jgp_conditions_codec import decode_condition, decode_selector, encode
from minos.ingest.jgp_conditions_manifest import ConditionsManifest
from minos.ingest.reference_manifest import DatasetManifest, utc_text
from minos.store.artifact import canonical_json
from minos.store.jgp_conditions_catalog import (
    insert_catalog_correspondence,
    validate_catalog_correspondence,
)

INTS = TypeAdapter(tuple[int, ...])
STRINGS = TypeAdapter(tuple[str, ...])


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def source_codes(sheet: c.Characteristics) -> dict[tuple[c.System, str], set[int]]:
    result: dict[tuple[c.System, str], set[int]] = defaultdict(set)
    for item in sheet.lists:
        for member in item.members:
            result[(item.system, member.code)].add(member.row)
    for selector in sheet.selectors:
        body = selector.body
        if isinstance(body, c.CodeSelector):
            for code in body.codes:
                result[(body.system, code)].update(selector.source_rows)
        elif isinstance(body, c.FamilySelector):
            result[(body.system, body.category)].update(selector.source_rows)
    return dict(result)


def dictionaries(
    connection: sqlite3.Connection, manifest: ConditionsManifest
) -> dict[c.System, dict[str, int]]:
    result: dict[c.System, dict[str, int]] = {}
    for system, origin, identity in (
        ("icd9-pl", "czs", manifest.icd9_release_id),
        ("icd10-pl", "rsk", manifest.icd10_release_id),
    ):
        if connection.execute(
            "SELECT system, origin FROM source_release WHERE id=?", (identity,)
        ).fetchone() != (system, origin):
            raise ArtifactError(
                "characteristics dictionary pin is absent or incompatible"
            )
        rows = cast(
            list[tuple[str, int]],
            connection.execute(
                "SELECT lookup_code, node_ordinal FROM concept WHERE source_release_id=? ORDER BY node_ordinal LIMIT 100001",
                (identity,),
            ).fetchall(),
        )
        if not rows or len(rows) > 100_000:
            raise ArtifactError(
                "characteristics selected dictionary is empty or unbounded"
            )
        result[cast(c.System, system)] = dict(rows)
    return result


def import_characteristics(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("characteristics import requires a build transaction")
    references = dictionaries(connection, manifest)
    with verified_source(
        archive, manifest.workbook, staging_directory=staging
    ) as source:
        sheets = parse_conditions_workbook(source, manifest)
    _ = connection.execute(
        "INSERT INTO characteristics_release VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            manifest.release_id,
            canonical_json(manifest).decode().strip(),
            manifest.valid_from.isoformat(),
            manifest.valid_to.isoformat() if manifest.valid_to else None,
            manifest.published_on.isoformat(),
            utc_text(manifest.workbook.fetched_at),
            manifest.applicability_evidence,
            manifest.icd9_release_id,
            manifest.icd10_release_id,
            manifest.catalog_release_id,
            manifest.supersedes,
        ),
    )
    for sheet in sheets:
        _insert_sheet(connection, manifest, sheet, references)
        insert_catalog_correspondence(connection, manifest, sheet)
    validate_release(connection, manifest)


def _insert_sheet(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
    references: dict[c.System, dict[str, int]],
) -> None:
    key = (manifest.release_id, sheet.catalog)
    _ = connection.execute(
        "INSERT INTO characteristics_sheet VALUES (?,?,?)", (*key, sheet.worksheet)
    )
    _insert_physical(connection, key, sheet)
    _insert_codes(connection, key, manifest, sheet, references)
    _insert_nodes(connection, key, sheet)


def _insert_physical(
    connection: sqlite3.Connection, key: tuple[str, str], sheet: c.Characteristics
) -> None:
    _ = connection.executemany(
        "INSERT INTO characteristics_evidence VALUES (?,?,?,?,?,?,?)",
        (
            (*key, e.row, e.section, e.context, e.text, e.category)
            for e in sheet.evidence
        ),
    )
    _ = connection.executemany(
        "INSERT INTO characteristics_group VALUES (?,?,?,?,?)",
        ((*key, g.code, g.title_row, _json(g.roots)) for g in sheet.groups),
    )
    _ = connection.executemany(
        "INSERT INTO directional_list VALUES (?,?,?,?,?,?,?,?)",
        (
            (
                *key,
                item.identity,
                item.label,
                item.system,
                item.context,
                item.marker_row,
                _json(item.naming_rows),
            )
            for item in sheet.lists
        ),
    )


def _insert_codes(
    connection: sqlite3.Connection,
    key: tuple[str, str],
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
    references: dict[c.System, dict[str, int]],
) -> None:
    for (system, code), rows in sorted(source_codes(sheet).items()):
        reference = (
            manifest.icd9_release_id
            if system == "icd9-pl"
            else manifest.icd10_release_id
        )
        ordinal = references[system].get(code)
        _ = connection.execute(
            "INSERT INTO characteristics_code VALUES (?,?,?,?,?,?,?)",
            (
                *key,
                system,
                code,
                reference,
                ordinal,
                "matched" if ordinal is not None else "not_in_reference",
            ),
        )
        _ = connection.executemany(
            "INSERT INTO characteristics_code_evidence VALUES (?,?,?,?,?)",
            ((*key, system, code, row) for row in sorted(rows)),
        )


def _insert_nodes(
    connection: sqlite3.Connection, key: tuple[str, str], sheet: c.Characteristics
) -> None:
    _ = connection.executemany(
        "INSERT INTO directional_member VALUES (?,?,?,?,?,?,?)",
        (
            (
                *key,
                item.identity,
                member.row,
                item.system,
                member.code,
                _json(member.marks),
            )
            for item in sheet.lists
            for member in item.members
        ),
    )
    _ = connection.executemany(
        "INSERT INTO condition_selector VALUES (?,?,?,?)",
        ((*key, s.identity, encode(s)) for s in sheet.selectors),
    )
    _ = connection.executemany(
        "INSERT INTO group_condition VALUES (?,?,?,?,?,?,?,?)",
        (
            (
                *key,
                node.identity,
                order,
                node.group,
                node.kind,
                node.status,
                encode(node),
            )
            for order, node in enumerate(sheet.conditions, 1)
        ),
    )


def load_characteristics(
    connection: sqlite3.Connection, release_id: str, catalog: Literal["1a", "1ae"]
) -> c.Characteristics:
    try:
        return _load_characteristics(connection, release_id, catalog)
    except (ValidationError, ValueError, TypeError, SourceError) as error:
        raise ArtifactError("stored characteristics structure is invalid") from error


def _load_characteristics(
    connection: sqlite3.Connection, release_id: str, catalog: Literal["1a", "1ae"]
) -> c.Characteristics:
    key = (release_id, catalog)
    title = cast(
        tuple[str] | None,
        connection.execute(
            "SELECT worksheet FROM characteristics_sheet WHERE release_id=? AND catalog=?",
            key,
        ).fetchone(),
    )
    if title is None:
        raise ArtifactError(
            "characteristics context is not present in selected release"
        )
    evidence, groups, lists = _load_physical(connection, key)
    selectors, nodes = _load_nodes(connection, key)
    sheet = c.Characteristics(
        catalog, title[0], evidence, groups, lists, selectors, nodes
    )
    validate_source_projection(sheet)
    return sheet


def _load_physical(
    connection: sqlite3.Connection, key: tuple[str, str]
) -> tuple[tuple[c.Evidence, ...], tuple[c.Group, ...], tuple[c.DirectionalList, ...]]:
    raw_evidence = cast(
        list[tuple[int, str | None, str | None, str, c.EvidenceCategory]],
        connection.execute(
            "SELECT row, section, context, source_text, category FROM characteristics_evidence WHERE release_id=? AND catalog=? ORDER BY row LIMIT 50001",
            key,
        ).fetchall(),
    )
    evidence = tuple(c.Evidence(*row) for row in raw_evidence)
    raw_groups = cast(
        list[tuple[str, int, str]],
        connection.execute(
            "SELECT code, title_row, roots_json FROM characteristics_group WHERE release_id=? AND catalog=? ORDER BY title_row LIMIT 2001",
            key,
        ).fetchall(),
    )
    groups = tuple(
        c.Group(code, row, INTS.validate_json(roots, strict=True))
        for code, row, roots in raw_groups
    )
    raw_members = cast(
        list[tuple[int, int, str, str, c.System]],
        connection.execute(
            "SELECT list_id, row, code, marks_json, system FROM directional_member WHERE release_id=? AND catalog=? ORDER BY row LIMIT 50001",
            key,
        ).fetchall(),
    )
    members: dict[int, list[c.Member]] = defaultdict(list)
    member_systems: dict[int, set[c.System]] = defaultdict(set)
    for identity, row, code, marks, system in raw_members:
        members[identity].append(
            c.Member(row, code, STRINGS.validate_json(marks, strict=True))
        )
        member_systems[identity].add(system)
    raw_lists = cast(
        list[tuple[int, str, c.System, str, int, str]],
        connection.execute(
            "SELECT id, label, system, context, marker_row, naming_rows_json FROM directional_list WHERE release_id=? AND catalog=? ORDER BY id LIMIT 3001",
            key,
        ).fetchall(),
    )
    lists = tuple(
        c.DirectionalList(
            identity,
            label,
            system,
            context,
            row,
            INTS.validate_json(naming, strict=True),
            tuple(members[identity]),
        )
        for identity, label, system, context, row, naming in raw_lists
    )
    if any(member_systems[item.identity] != {item.system} for item in lists) or set(
        members
    ) != {item.identity for item in lists}:
        raise ArtifactError("characteristics membership system or list conflicts")
    return evidence, groups, lists


def _load_nodes(
    connection: sqlite3.Connection, key: tuple[str, str]
) -> tuple[tuple[c.Selector, ...], tuple[c.Condition, ...]]:
    raw_selectors = cast(
        list[tuple[int, str]],
        connection.execute(
            "SELECT id, payload_json FROM condition_selector WHERE release_id=? AND catalog=? ORDER BY id LIMIT 20001",
            key,
        ).fetchall(),
    )
    selectors = tuple(decode_selector(text) for _, text in raw_selectors)
    if any(
        identity != selector.identity
        for (identity, _), selector in zip(raw_selectors, selectors, strict=True)
    ):
        raise ArtifactError("characteristics selector row identity conflicts")
    raw_nodes = cast(
        list[tuple[int, str, str | None, str, str, int]],
        connection.execute(
            "SELECT id, group_code, kind, interpretation_status, payload_json, source_order FROM group_condition WHERE release_id=? AND catalog=? ORDER BY source_order LIMIT 20001",
            key,
        ).fetchall(),
    )
    nodes = tuple(decode_condition(text) for _, _, _, _, text, _ in raw_nodes)
    if any(
        (identity, group, kind, status, order)
        != (node.identity, node.group, node.kind, node.status, position)
        for position, ((identity, group, kind, status, _, order), node) in enumerate(
            zip(raw_nodes, nodes, strict=True), 1
        )
    ):
        raise ArtifactError("characteristics condition envelope conflicts")
    return selectors, nodes


def validate_release(
    connection: sqlite3.Connection, manifest: ConditionsManifest
) -> None:
    expected_metadata = (
        canonical_json(manifest).decode().strip(),
        manifest.valid_from.isoformat(),
        manifest.valid_to.isoformat() if manifest.valid_to else None,
        manifest.published_on.isoformat(),
        utc_text(manifest.workbook.fetched_at),
        manifest.applicability_evidence,
        manifest.icd9_release_id,
        manifest.icd10_release_id,
        manifest.catalog_release_id,
        manifest.supersedes,
    )
    stored = cast(
        tuple[str, str, str | None, str, str, str, str, str, str | None, str | None]
        | None,
        connection.execute(
            "SELECT manifest_json, valid_from, valid_to, published_on, recorded_at, applicability_evidence, icd9_release_id, icd10_release_id, catalog_release_id, supersedes FROM characteristics_release WHERE id=?",
            (manifest.release_id,),
        ).fetchone(),
    )
    if stored != expected_metadata:
        raise ArtifactError("characteristics release provenance or dates conflict")
    contexts = {
        row[0]
        for row in cast(
            list[tuple[str]],
            connection.execute(
                "SELECT catalog FROM characteristics_sheet WHERE release_id=?",
                (manifest.release_id,),
            ).fetchall(),
        )
    }
    if contexts != set(manifest.sheets):
        raise ArtifactError("characteristics catalog contexts conflict")
    references = dictionaries(connection, manifest)
    for context, expected in manifest.sheets.items():
        sheet = load_characteristics(connection, manifest.release_id, context)
        expected_sheet = (
            "Charakterystyka"
            if manifest.layout == "nfz-9-46-2026"
            else "Charakterystyka " + context
        )
        if sheet.worksheet != expected_sheet:
            raise ArtifactError("characteristics worksheet identity conflicts")
        try:
            check_counts(sheet, expected)
        except SourceError as error:
            raise ArtifactError("characteristics reviewed counts conflict") from error
        _validate_codes(connection, manifest, sheet, references)
        validate_catalog_correspondence(connection, manifest, sheet)


def _validate_codes(
    connection: sqlite3.Connection,
    manifest: ConditionsManifest,
    sheet: c.Characteristics,
    references: dict[c.System, dict[str, int]],
) -> None:
    key = (manifest.release_id, sheet.catalog)
    expected = source_codes(sheet)
    stored = cast(
        list[tuple[c.System, str, str, int | None, str]],
        connection.execute(
            "SELECT system, code, reference_release_id, reference_node, correspondence FROM characteristics_code WHERE release_id=? AND catalog=? ORDER BY system, code LIMIT 50001",
            key,
        ).fetchall(),
    )
    if {(system, code) for system, code, _, _, _ in stored} != set(expected):
        raise ArtifactError("characteristics source-local code registry conflicts")
    unmatched = 0
    for system, code, identity, node, status in stored:
        selected = (
            manifest.icd9_release_id
            if system == "icd9-pl"
            else manifest.icd10_release_id
        )
        ordinal = references[system].get(code)
        if (identity, node, status) != (
            selected,
            ordinal,
            "matched" if ordinal is not None else "not_in_reference",
        ):
            raise ArtifactError(
                "characteristics exact dictionary correspondence conflicts"
            )
        unmatched += ordinal is None
    evidence = cast(
        list[tuple[c.System, str, int]],
        connection.execute(
            "SELECT system, code, row FROM characteristics_code_evidence WHERE release_id=? AND catalog=? ORDER BY system, code, row LIMIT 100001",
            key,
        ).fetchall(),
    )
    actual: dict[tuple[c.System, str], set[int]] = defaultdict(set)
    for system, code, row in evidence:
        actual[(system, code)].add(row)
    if dict(actual) != expected or len(evidence) > 100_000:
        raise ArtifactError("characteristics source-local code evidence conflicts")
    if unmatched != manifest.sheets[sheet.catalog].unmatched_codes:
        raise ArtifactError("characteristics unmatched-code review count conflicts")


def validate_conditions_bindings(
    connection: sqlite3.Connection, dataset: DatasetManifest
) -> None:
    horizon = dataset.characteristics_knowledge_as_of
    expected_knowledge = (
        []
        if horizon is None
        else [(horizon.isoformat(), dataset.characteristics_knowledge_evidence)]
    )
    if (
        connection.execute(
            "SELECT as_of, evidence FROM characteristics_knowledge"
        ).fetchall()
        != expected_knowledge
    ):
        raise ArtifactError("characteristics knowledge horizon conflicts")
    stored = {
        row[0]
        for row in cast(
            list[tuple[str]],
            connection.execute("SELECT id FROM characteristics_release").fetchall(),
        )
    }
    if stored != {item.release_id for item in dataset.characteristics}:
        raise ArtifactError("characteristics release inventory conflicts")
    for item in dataset.characteristics:
        validate_release(connection, item)


def record_conditions_knowledge(
    connection: sqlite3.Connection, dataset: DatasetManifest
) -> None:
    if dataset.characteristics_knowledge_as_of is not None:
        _ = connection.execute(
            "INSERT INTO characteristics_knowledge VALUES (1,?,?)",
            (
                dataset.characteristics_knowledge_as_of.isoformat(),
                dataset.characteristics_knowledge_evidence,
            ),
        )
