"""Reviewed legal tables match the authentic text; derived codes follow § 9 ust. 4."""

import re
from datetime import UTC, date, datetime
from pathlib import Path

from minos.ingest.grouping_dictionaries_manifest import (
    LegalActFile,
    SpecialtyDictionary,
    SpecialtyEntry,
)
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.grouping_dictionaries import specialty_codes

ROOT = Path(__file__).resolve().parents[2]
EXTRACTS = ROOT / "tests/fixtures/legal"
# The acts print Polish quotes and an en dash; built here to keep sources ASCII.
OPEN, CLOSE, DASH = chr(0x201E), chr(0x201D), chr(0x2013)


def _manifest() -> DatasetManifest:
    return DatasetManifest.model_validate_json(
        (
            ROOT / "manifests/sources/reference-conditions-public-validation.json"
        ).read_bytes()
    )


def _variants(path: Path) -> tuple[str, ...]:
    """The extract as printed, plus both readings of a line-final hyphen."""
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    text = " ".join(" ".join(lines).split()).replace(",,", OPEN)
    letter = "a-ząćęłńóśźż"
    broken = re.sub(rf"(?<=[{letter}])- (?=[{letter}])", "", text)
    compound = re.sub(rf"(?<=[{letter}])- (?=[{letter}])", "-", text)
    return text, broken, compound


def test_specialty_table_is_the_complete_annex_2_transcription() -> None:
    dictionaries = _manifest().grouping_dictionaries
    assert dictionaries is not None
    extract = EXTRACTS / "du-2019-173-annex-2.txt"
    variants = _variants(extract)
    # A name ends at the next code, a section heading, a page break or footnote.
    after = r"(?= [0-9]{4} | [0-9]\. | Dziennik Ustaw | [0-9]+\) |$)"
    for entry in dictionaries.specialties.entries:
        exact = re.compile(re.escape(f"{entry.code} {entry.name}") + after)
        assert any(exact.search(text) for text in variants), entry.code
    printed = re.findall(
        r"^([0-9]{4})\s", extract.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert [entry.code for entry in dictionaries.specialties.entries] == printed


def test_discharge_table_is_the_complete_annex_4_transcription() -> None:
    dictionaries = _manifest().grouping_dictionaries
    assert dictionaries is not None
    extract = EXTRACTS / "du-2025-1588-annex-4.txt"
    variants = _variants(extract)
    for mode in dictionaries.discharge_modes.modes:
        quoted = f"{mode.point}) {OPEN}{mode.code}{CLOSE} {DASH} {mode.name}"
        assert any(quoted + ";" in text or quoted + "." in text for text in variants), (
            mode.code
        )
    printed = [
        int(match.group(1))
        for match in re.finditer(
            r"^\s*[0-9]+\)\s*,,([0-9]+)" + CLOSE,
            extract.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]
    assert [mode.code for mode in dictionaries.discharge_modes.modes] == printed


def test_children_codes_are_derived_only_for_unlisted_odd_neighbours() -> None:
    source = LegalActFile(
        source_url="https://api.sejm.gov.pl/eli/acts/DU/2019/173/text.pdf",
        sha256="0" * 64,
        size_bytes=1,
        fetched_at=datetime(2026, 10, 8, tzinfo=UTC),
    )
    manifest = SpecialtyDictionary(
        release_id="specialty-test",
        act="act",
        annex="Załącznik nr 2",
        publisher="publisher",
        license="licence",
        notices=("notice",),
        source=source,
        reviewed_on=date(2026, 10, 8),
        children_rule="§ 9 ust. 4",
        entries=(
            SpecialtyEntry(code="4400", name="Oddział A", section="S"),
            SpecialtyEntry(code="4401", name="Oddział A dla dzieci", section="S"),
            SpecialtyEntry(code="4500", name="Oddział B", section="S"),
            SpecialtyEntry(code="4583", name="Oddział C", section="S"),
        ),
    )
    codes = {item.code: item for item in specialty_codes(manifest)}
    # 4401 is printed, so it is not derived; odd listed codes derive nothing.
    assert codes["4401"].derivation == "listed"
    assert codes["4501"].derivation == "children_rule"
    assert codes["4501"].name == "Oddział B"
    assert "4584" not in codes and len(codes) == 5
