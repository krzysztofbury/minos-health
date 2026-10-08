"""Build and read the grouping builder's legal dictionaries (#151)."""

import sqlite3
from pathlib import Path
from typing import cast

from minos.domain.grouping_dictionaries import DischargeModeCode, SpecialtyCode
from minos.domain.reference import ArtifactError
from minos.ingest.archive_source import verified_source
from minos.ingest.grouping_dictionaries_manifest import (
    GroupingDictionaries,
    SpecialtyDictionary,
)
from minos.store.artifact import canonical_json
from minos.store.query_budget import query_budget

MAX_SPECIALTIES = 4_000


def specialty_codes(manifest: SpecialtyDictionary) -> tuple[SpecialtyCode, ...]:
    """Listed codes in annex order, then § 9 ust. 4 children's codes."""
    listed = [
        SpecialtyCode(entry.code, entry.name, entry.section, "listed", index)
        for index, entry in enumerate(manifest.entries, 1)
    ]
    codes = {item.code for item in listed}
    adults = [
        item
        for item in listed
        if int(item.code) % 2 == 0 and f"{int(item.code) + 1:04d}" not in codes
    ]
    return (
        *listed,
        *(
            SpecialtyCode(
                f"{int(item.code) + 1:04d}",
                item.name,
                item.section,
                "children_rule",
                len(listed) + offset,
            )
            for offset, item in enumerate(adults, 1)
        ),
    )


def import_grouping_dictionaries(
    connection: sqlite3.Connection,
    manifest: GroupingDictionaries,
    archive: Path,
    staging: Path,
) -> None:
    if not connection.in_transaction:
        raise ArtifactError("dictionary import requires a build transaction")
    # The tables are reviewed transcriptions; the pinned PDFs must still verify
    # so a release never cites bytes the archive does not hold.
    for source in (manifest.specialties.source, manifest.discharge_modes.source):
        with verified_source(archive, source, staging_directory=staging):
            pass
    specialties, modes = manifest.specialties, manifest.discharge_modes
    _ = connection.executemany(
        "INSERT INTO grouping_dictionary_release VALUES (?, ?, ?)",
        (
            (specialties.release_id, "specialty", canonical_json(specialties).decode()),
            (modes.release_id, "discharge_mode", canonical_json(modes).decode()),
        ),
    )
    _ = connection.executemany(
        "INSERT INTO specialty_code VALUES (?, ?, ?, ?, ?, ?)",
        (
            (
                specialties.release_id,
                item.code,
                item.name,
                item.section,
                item.derivation,
                item.position,
            )
            for item in specialty_codes(specialties)
        ),
    )
    _ = connection.executemany(
        "INSERT INTO discharge_mode VALUES (?, ?, ?, ?)",
        ((modes.release_id, mode.code, mode.name, mode.point) for mode in modes.modes),
    )


def read_grouping_dictionaries(
    connection: sqlite3.Connection, manifest: GroupingDictionaries
) -> tuple[tuple[SpecialtyCode, ...], tuple[DischargeModeCode, ...]]:
    """Rows of exactly the manifest's releases; anything else fails closed."""
    with query_budget(connection):
        releases = cast(
            list[tuple[str, str, str]],
            connection.execute(
                "SELECT id, kind, manifest_json FROM grouping_dictionary_release ORDER BY id LIMIT 3"
            ).fetchall(),
        )
        specialties = cast(
            list[tuple[str, str, str, str, int]],
            connection.execute(
                """SELECT code, name, section, derivation, position FROM specialty_code
                WHERE release_id=? ORDER BY position LIMIT ?""",
                (manifest.specialties.release_id, MAX_SPECIALTIES + 1),
            ).fetchall(),
        )
        modes = cast(
            list[tuple[int, str, int]],
            connection.execute(
                """SELECT code, name, point FROM discharge_mode WHERE release_id=?
                ORDER BY point LIMIT 100""",
                (manifest.discharge_modes.release_id,),
            ).fetchall(),
        )
    expected = sorted(
        (
            (
                manifest.specialties.release_id,
                "specialty",
                canonical_json(manifest.specialties).decode(),
            ),
            (
                manifest.discharge_modes.release_id,
                "discharge_mode",
                canonical_json(manifest.discharge_modes).decode(),
            ),
        )
    )
    if releases != expected or len(specialties) > MAX_SPECIALTIES:
        raise ArtifactError("grouping dictionaries do not match the manifest")
    if len(modes) != len(manifest.discharge_modes.modes):
        raise ArtifactError("discharge modes do not match the manifest")
    return (
        tuple(
            SpecialtyCode(code, name, section, "listed", position)
            if derivation == "listed"
            else SpecialtyCode(code, name, section, "children_rule", position)
            for code, name, section, derivation, position in specialties
        ),
        tuple(DischargeModeCode(code, name, point) for code, name, point in modes),
    )
