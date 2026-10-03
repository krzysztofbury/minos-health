"""Transactional modifier evidence import and symmetric row reconstruction."""

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import cast

from pydantic import TypeAdapter, ValidationError

from minos.domain.jgp_modifiers import ModifierRelease, validate_modifier_release
from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_modifiers import parse_modifiers
from minos.ingest.jgp_modifiers_manifest import ModifierManifest
from minos.ingest.reference_manifest import DatasetManifest
from minos.ingest.watchers.czs.client import CzsSourceError, CzsVersion
from minos.ingest.watchers.czs.display import parse_display_page
from minos.store.artifact import canonical_json

RELEASE = TypeAdapter(ModifierRelease)


def source_json(release: ModifierRelease) -> str:
    return json.dumps(
        asdict(release), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _counts(release: ModifierRelease, manifest: ModifierManifest) -> None:
    observed = (
        len(release.modifiers),
        sum(len(m.periods) for m in release.modifiers),
        sum(len(p.groups) for m in release.modifiers for p in m.periods),
        sum(m.status == "N" and not m.periods for m in release.modifiers),
    )
    expected = (
        manifest.expected_modifiers,
        manifest.expected_periods,
        manifest.expected_group_mappings,
        manifest.expected_inactive_empty,
    )
    if observed != expected:
        raise SourceError("reviewed MODTAR counts do not match")


def _rows(
    release: ModifierRelease,
) -> tuple[
    list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]
]:
    modifiers: list[tuple[object, ...]] = []
    periods: list[tuple[object, ...]] = []
    groups: list[tuple[object, ...]] = []
    for modifier in release.modifiers:
        modifiers.append(
            (modifier.ordinal, modifier.symbol, modifier.description, modifier.status)
        )
        for period in modifier.periods:
            periods.append(
                (
                    modifier.ordinal,
                    period.ordinal,
                    period.month_from,
                    period.month_to,
                    period.coefficient_text,
                    period.coefficient_millionths,
                )
            )
            groups.extend(
                (modifier.ordinal, period.ordinal, ordinal, group)
                for ordinal, group in enumerate(period.groups, 1)
            )
    return modifiers, periods, groups


def import_modifiers(
    connection: sqlite3.Connection,
    manifest: ModifierManifest,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("MODTAR import requires a build transaction")
    with verified_source(archive, manifest.xml, staging_directory=staging) as source:
        release = parse_modifiers(
            source, year=manifest.year, generation=manifest.generation
        )
    _counts(release, manifest)
    if manifest.display is not None:
        observed = manifest.display
        with verified_source(
            archive, observed.file, staging_directory=staging
        ) as source:
            body = source.read(256 * 1024 + 1)
        try:
            display = parse_display_page(
                body,
                observed.page_id,
                CzsVersion(
                    "MODTAR",
                    release.generation,
                    release.generated_at_text,
                    release.year,
                    "SZP",
                ),
            )
        except CzsSourceError as error:
            raise SourceError(
                "MODTAR display observation conflicts with source"
            ) from error
        if (display.label, display.published_date, display.effective_from) != (
            observed.label,
            observed.published_on,
            observed.dictionary_valid_from,
        ):
            raise SourceError("MODTAR display values conflict with manifest")
    _ = connection.execute(
        "INSERT INTO modifier_release VALUES (?,?,?,?,?,?,?)",
        (
            manifest.release_id,
            canonical_json(manifest).decode().strip(),
            source_json(release),
            release.year,
            release.generation,
            release.grouper_type,
            manifest.xml.sha256,
        ),
    )
    modifiers, periods, groups = _rows(release)
    for table, placeholders, rows in (
        ("tariff_modifier", "?,?,?,?,?", modifiers),
        ("modifier_period", "?,?,?,?,?,?,?", periods),
        ("modifier_group", "?,?,?,?,?", groups),
    ):
        _ = connection.executemany(
            f"INSERT INTO {table} VALUES ({placeholders})",  # nosec B608 - fixed table whitelist
            ((manifest.release_id, *row) for row in rows),
        )
    _ = validate_modifier_rows(connection, manifest)


def validate_modifier_rows(
    connection: sqlite3.Connection, manifest: ModifierManifest
) -> ModifierRelease:
    row = cast(
        tuple[str, str, int, int, str, str] | None,
        connection.execute(
            "SELECT manifest_json,source_json,year,generation,grouper_type,source_sha256 FROM modifier_release WHERE id=?",
            (manifest.release_id,),
        ).fetchone(),
    )
    if (
        row is None
        or row[0] != canonical_json(manifest).decode().strip()
        or row[2:]
        != (
            manifest.year,
            manifest.generation,
            manifest.grouper_type,
            manifest.xml.sha256,
        )
    ):
        raise ArtifactError("MODTAR source pin conflicts with artifact")
    try:
        if len(row[1].encode()) > 8 * 1024 * 1024:
            raise SourceError("MODTAR source envelope exceeds bound")
        release = RELEASE.validate_json(row[1], strict=True)
        if source_json(release) != row[1]:
            raise SourceError("MODTAR source envelope is not canonical")
        if (release.year, release.generation, release.grouper_type) != row[2:5]:
            raise SourceError("MODTAR source header conflicts with pin")
        validate_modifier_release(release)
        _counts(release, manifest)
    except (SourceError, ValidationError, ValueError, TypeError) as error:
        raise ArtifactError("MODTAR source envelope is invalid") from error
    expected_modifiers, expected_periods, expected_groups = _rows(release)
    for table, columns, expected, order in (
        (
            "tariff_modifier",
            "source_order,symbol,description,status",
            expected_modifiers,
            "source_order",
        ),
        (
            "modifier_period",
            "modifier_order,source_order,month_from,month_to,coefficient_text,coefficient_millionths",
            expected_periods,
            "modifier_order,source_order",
        ),
        (
            "modifier_group",
            "modifier_order,period_order,source_order,code",
            expected_groups,
            "modifier_order,period_order,source_order",
        ),
    ):
        actual = connection.execute(
            f"SELECT {columns} FROM {table} WHERE release_id=? ORDER BY {order} LIMIT 50001",  # nosec B608 - fixed schema identifiers
            (manifest.release_id,),
        ).fetchall()
        if actual != expected:
            raise ArtifactError("MODTAR rows conflict with preserved source projection")
    return release


def record_modifier_candidates(
    connection: sqlite3.Connection, dataset: DatasetManifest
) -> None:
    _ = connection.executemany(
        "INSERT INTO modifier_candidate VALUES (?,?,?,?)",
        (
            (
                item.catalog_release_id,
                item.modifier_release_id,
                item.status,
                item.evidence,
            )
            for item in dataset.modifier_candidates
        ),
    )


def validate_modifiers_bindings(
    connection: sqlite3.Connection, dataset: DatasetManifest
) -> None:
    stored = connection.execute(
        "SELECT id FROM modifier_release ORDER BY id LIMIT 9"
    ).fetchall()
    if stored != [
        (item.release_id,)
        for item in sorted(dataset.modifiers, key=lambda m: m.release_id)
    ]:
        raise ArtifactError("MODTAR release inventory conflicts")
    for item in dataset.modifiers:
        _ = validate_modifier_rows(connection, item)
    expected = [
        (item.catalog_release_id, item.modifier_release_id, item.status, item.evidence)
        for item in sorted(
            dataset.modifier_candidates, key=lambda c: c.catalog_release_id
        )
    ]
    if (
        connection.execute(
            "SELECT catalog_release_id,modifier_release_id,status,evidence FROM modifier_candidate ORDER BY catalog_release_id LIMIT 9"
        ).fetchall()
        != expected
    ):
        raise ArtifactError("MODTAR review-candidate inventory conflicts")
