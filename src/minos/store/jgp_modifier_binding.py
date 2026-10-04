"""Offline reviewed binding validation, separate from encounter eligibility."""

import calendar
import hashlib
import sqlite3
from datetime import date
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from minos.domain.jgp_modifiers import (
    LegalOverlay,
    exact_millionths,
    reconcile_legal_overlay,
    resolve_group_reference,
    select_legal_provisions,
    select_reviewed_mappings,
)
from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.archive_source import verified_source
from minos.ingest.jgp_modifier_binding_manifest import (
    LegalOverlayInput,
    ModifierBinding,
)
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json
from minos.store.jgp_modifiers import catalog_windows
from minos.store.jgp_modifiers_build import validate_modifier_rows


def _overlay(
    content: bytes,
    binding: ModifierBinding,
    dataset: DatasetManifest,
    connection: sqlite3.Connection,
) -> LegalOverlay:
    if len(content) != binding.overlay.size_bytes:
        raise SourceError("MODTAR legal overlay byte count conflicts")
    if hashlib.sha256(content).hexdigest() != binding.overlay.sha256:
        raise SourceError("MODTAR legal overlay checksum conflicts")
    try:
        value = LegalOverlayInput.model_validate_json(content)
    except ValidationError as error:
        raise SourceError("MODTAR legal overlay has invalid structure") from error
    if canonical_json(value) != content:
        raise SourceError("MODTAR legal overlay is not canonical")
    if value.reviewed_on != binding.reviewed_on:
        raise SourceError("MODTAR legal overlay review date conflicts")
    if binding.catalog_release_id not in value.catalog_release_ids:
        raise SourceError("MODTAR legal overlay does not cover the selected catalog")
    pins = [
        item
        for item in dataset.modifiers
        if item.release_id == binding.modifier_release_id
    ]
    if len(pins) != 1:
        raise SourceError("MODTAR binding has no unique dictionary pin")
    release = validate_modifier_rows(connection, pins[0])
    if binding.knowledge_from < date.fromisoformat(release.published_at_text[:10]):
        raise SourceError("MODTAR binding predates dictionary publication")
    return reconcile_legal_overlay(
        release, value.domain(), catalog_windows=catalog_windows(connection)
    )


def import_modifier_bindings(
    connection: sqlite3.Connection,
    dataset: DatasetManifest,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("MODTAR binding import requires a build transaction")
    for binding in sorted(
        dataset.modifier_bindings, key=lambda item: item.catalog_release_id
    ):
        with verified_source(
            archive, binding.overlay, staging_directory=staging
        ) as source:
            content = source.read(binding.overlay.size_bytes + 1)
        overlay = _overlay(content, binding, dataset, connection)
        _ = connection.execute(
            "INSERT INTO modifier_binding VALUES (?,?,?,?,?,?,?,?)",
            (
                binding.catalog_release_id,
                binding.modifier_release_id,
                binding.overlay.sha256,
                content.decode(),
                binding.reviewed_on.isoformat(),
                binding.knowledge_from.isoformat(),
                binding.reviewer,
                binding.evidence,
            ),
        )
        _ = connection.executemany(
            "INSERT INTO modifier_binding_window VALUES (?,?,?)",
            (
                (binding.catalog_release_id, start, end)
                for start, end in _windows(overlay)
            ),
        )
    validate_modifier_bindings(connection, dataset)


def validate_modifier_bindings(
    connection: sqlite3.Connection, dataset: DatasetManifest
) -> None:
    rows = cast(
        list[tuple[str, str, str, str, str, str, str, str]],
        connection.execute(
            "SELECT catalog_release_id,modifier_release_id,overlay_sha256,overlay_json,reviewed_on,knowledge_from,reviewer,evidence FROM modifier_binding ORDER BY catalog_release_id LIMIT 9"
        ).fetchall(),
    )
    expected = sorted(
        dataset.modifier_bindings, key=lambda item: item.catalog_release_id
    )
    if len(rows) != len(expected):
        raise ArtifactError("MODTAR binding inventory conflicts")
    for row, binding in zip(rows, expected, strict=True):
        if row[:3] + row[4:] != (
            binding.catalog_release_id,
            binding.modifier_release_id,
            binding.overlay.sha256,
            binding.reviewed_on.isoformat(),
            binding.knowledge_from.isoformat(),
            binding.reviewer,
            binding.evidence,
        ):
            raise ArtifactError("MODTAR binding source policy conflicts")
        try:
            overlay = _overlay(row[3].encode(), binding, dataset, connection)
        except SourceError as error:
            raise ArtifactError(
                "MODTAR reviewed binding conflicts with legal evidence"
            ) from error
        if connection.execute(
            "SELECT service_from,service_to FROM modifier_binding_window WHERE catalog_release_id=? ORDER BY service_from,service_to LIMIT 50001",
            (binding.catalog_release_id,),
        ).fetchall() != _windows(overlay):
            raise ArtifactError(
                "MODTAR binding service windows conflict with legal evidence"
            )


def _windows(overlay: LegalOverlay) -> list[tuple[str, str]]:
    windows: set[tuple[str, str]] = set()
    for row in overlay.rows:
        first, last = row.mapping[3], row.mapping[4]
        if row.mapping[6] is None or first is None or last is None:
            continue
        start = max(row.service_from, date(overlay.year, first, 1))
        end = min(
            row.service_to,
            date(overlay.year, last, calendar.monthrange(overlay.year, last)[1]),
        )
        if start <= end:
            windows.add((start.isoformat(), end.isoformat()))
    windows.update(
        (rule.service_from.isoformat(), rule.service_to.isoformat())
        for rule in overlay.supplements
    )
    return sorted(windows)


def require_modifier_binding(
    connection: sqlite3.Connection, ruleset: dict[str, object]
) -> dict[str, object]:
    if connection.execute("PRAGMA user_version").fetchone()[0] < 7:
        raise SourceError("MODTAR binding is unbound; final-tariff capability refused")
    from minos.store.jgp_ruleset import resolve_ruleset

    resolved = resolve_ruleset(
        connection,
        date.fromisoformat(cast(str, ruleset["service_date"])),
        date.fromisoformat(cast(str, ruleset["known_on"])),
    )
    if resolved["release_id"] != ruleset["release_id"]:
        raise SourceError("MODTAR ruleset binding is mismatched")
    binding = cast(dict[str, object], resolved["modifiers"])
    if binding.get("binding") != "reviewed":
        raise SourceError("MODTAR binding is unbound; final-tariff capability refused")
    return binding


def reviewed_group_modifiers(
    connection: sqlite3.Connection,
    dataset: DatasetManifest,
    ruleset: dict[str, object],
    code: str,
) -> dict[str, object]:
    from minos.store.jgp_ruleset import catalog_group

    _ = catalog_group(connection, ruleset, code)
    binding = require_modifier_binding(connection, ruleset)
    pin = next(
        item for item in dataset.modifiers if item.release_id == binding["release_id"]
    )
    release = validate_modifier_rows(connection, pin)
    row = cast(
        tuple[str],
        connection.execute(
            "SELECT overlay_json FROM modifier_binding WHERE catalog_release_id=?",
            (ruleset["release_id"],),
        ).fetchone(),
    )
    overlay = LegalOverlayInput.model_validate_json(row[0]).domain()
    service_date = date.fromisoformat(cast(str, ruleset["service_date"]))
    selected = select_reviewed_mappings(
        release, overlay, service_date, catalog_windows=catalog_windows(connection)
    )
    mappings: list[dict[str, object]] = []
    for mapping, resolved in selected:
        if resolved == code:
            mappings.append(
                {
                    "symbol": mapping[0],
                    "source_citation": mapping[1],
                    "coefficient_text": mapping[5],
                    "coefficient_millionths": exact_millionths(cast(str, mapping[5])),
                    "literal_group": mapping[6],
                    "provenance": "MODTAR",
                }
            )
    supplements = [
        {
            "symbol": rule.symbol,
            "source_citation": rule.clause,
            "coefficient_text": rule.coefficient_text,
            "coefficient_millionths": exact_millionths(rule.coefficient_text),
            "eligibility_text": rule.eligibility_text,
            "source_evidence": rule.source_evidence,
            "provenance": "manual_legal_overlay",
        }
        for rule in select_legal_provisions(overlay, service_date)
        if any(
            resolve_group_reference(group, frozenset({code})) is not None
            for group in rule.groups
        )
    ]
    return {
        "binding": binding,
        "dictionary_mappings": mappings,
        "legal_supplements": supplements,
        "meaning": "Service-date reference mappings only. Clinical/provider qualifiers and combination rules must be evaluated before final points.",
    }
