import json
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from minos.domain.jgp_ruleset import (
    AmbiguousRuleset,
    NoRulesetForDate,
    ReleaseWindow,
    RowWindow,
    resolve_release,
)
from minos.domain.reference import SourceError
from minos.ingest.jgp_catalog import parse_catalog_workbook
from minos.ingest.jgp_catalog_applicability import row_windows
from minos.ingest.jgp_catalog_manifest import CatalogManifest, RowApplicability
from minos.ingest.reference_manifest import DatasetManifest
from tests.fixtures.jgp_catalog import (
    Row,
    catalog_workbook,
    default_sheets,
    stage_catalog,
)

ROOT = Path(__file__).parents[2]
# The reviewed 46/2026 lineage windows, as pinned in the catalog manifest.
ORIGINAL = ReleaseWindow("original", date(2026, 5, 1), date(2026, 4, 30), None)
AFTER_74 = ReleaseWindow("after-74", date(2026, 7, 1), date(2026, 7, 23), "original")
AFTER_102 = ReleaseWindow("after-102", date(2026, 7, 1), date(2026, 9, 23), "after-74")
LINEAGE = (ORIGINAL, AFTER_74, AFTER_102)


@pytest.mark.parametrize(
    ("service_date", "known_on", "expected"),
    [
        # Before 74/2026 is amended in, May-June services keep the original act.
        (date(2026, 6, 30), date(2026, 10, 2), "original"),
        # Retroactivity: published 2026-07-23, 74/2026 governs July services.
        (date(2026, 7, 10), date(2026, 8, 1), "after-74"),
        # Knowledge filter: before publication the earlier known state answers.
        (date(2026, 7, 10), date(2026, 7, 22), "original"),
        (date(2026, 7, 10), date(2026, 7, 23), "after-74"),
        # Overlap: 74 and 102 both cover July; 102 explicitly supersedes 74.
        (date(2026, 7, 10), date(2026, 10, 2), "after-102"),
        (date(2027, 1, 1), date(2026, 10, 2), "after-102"),
        # Inclusive boundaries: first covered service day and publication day.
        (date(2026, 7, 1), date(2026, 10, 2), "after-102"),
        (date(2026, 5, 1), date(2026, 4, 30), "original"),
        (date(2026, 7, 10), date(2026, 9, 22), "after-74"),
        (date(2026, 7, 10), date(2026, 9, 23), "after-102"),
    ],
)
def test_service_date_resolves_reviewed_release_as_known(
    service_date: date, known_on: date, expected: str
) -> None:
    assert resolve_release(LINEAGE, service_date, known_on).release_id == expected


@pytest.mark.parametrize(
    ("service_date", "known_on"),
    [
        (date(2026, 4, 30), date(2026, 10, 2)),  # 101/2025 lineage is not ingested
        (date(2026, 6, 1), date(2026, 4, 29)),  # nothing published yet
    ],
)
def test_uncovered_dates_never_fall_back_to_the_latest_release(
    service_date: date, known_on: date
) -> None:
    with pytest.raises(NoRulesetForDate, match="no reviewed ruleset covers"):
        _ = resolve_release(LINEAGE, service_date, known_on)


def test_unsuperseded_overlap_is_an_explicit_ambiguity() -> None:
    rival = ReleaseWindow("rival", date(2026, 7, 1), date(2026, 8, 1), None)
    with pytest.raises(AmbiguousRuleset, match="2 reviewed rulesets"):
        _ = resolve_release(
            (ORIGINAL, AFTER_74, rival), date(2026, 7, 10), date(2026, 9, 1)
        )
    # Before the rival is published only the reviewed chain applies.
    chosen = resolve_release(
        (ORIGINAL, AFTER_74, rival), date(2026, 7, 10), date(2026, 7, 31)
    )
    assert chosen.release_id == "after-74"


def test_reviewed_overrides_split_twin_rows_at_the_2027_boundary(
    tmp_path: Path,
) -> None:
    content = catalog_workbook(default_sheets())
    manifest = stage_catalog(tmp_path, content)
    sheets = parse_catalog_workbook(BytesIO(content), manifest)
    windows = row_windows(sheets, manifest)
    h21 = [group for group in sheets[0].groups if group.code == "H21"]
    until, deferred = (windows[("1a", group.row)][0] for group in h21)
    assert (until.covers(date(2026, 12, 31)), until.covers(date(2027, 1, 1))) == (
        True,
        False,
    )
    assert (deferred.covers(date(2026, 12, 31)), deferred.covers(date(2027, 1, 1))) == (
        False,
        True,
    )
    assert (
        windows[("1a", 7)][1] == manifest.service_evidence
    )  # untouched row keeps release evidence


def test_overrides_must_match_existing_rows_and_their_notes(tmp_path: Path) -> None:
    content = catalog_workbook(default_sheets())
    manifest = stage_catalog(tmp_path, content)
    sheets = parse_catalog_workbook(BytesIO(content), manifest)
    deferred, until = manifest.row_applicability
    for changed, message in (
        ((replace_item(deferred, ordinals=(99,)), until), "missing or ambiguous"),
        (
            (replace_item(deferred, note_marker="od 1.02.2027"), until),
            "does not confirm",
        ),
        ((), "dated catalog note has no reviewed applicability"),
        (
            (deferred, replace_item(until, service_to=date(2027, 6, 30))),
            "applies on two rows at once",
        ),
    ):
        with pytest.raises(SourceError, match=message):
            _ = row_windows(
                sheets, manifest.model_copy(update={"row_applicability": changed})
            )


def replace_item(item: RowApplicability, **changes: object) -> RowApplicability:
    return item.model_copy(update=changes)


def test_manifest_rejects_unsound_windows_and_supersession(tmp_path: Path) -> None:
    manifest = stage_catalog(tmp_path, catalog_workbook(default_sheets()))
    data = manifest.model_dump(mode="python")
    override = cast(list[dict[str, object]], data["row_applicability"])[0]
    for change, message in (
        ({"published_on": date(2026, 7, 22)}, "publication precedes the act date"),
        ({"modifier_binding": "bound"}, "modifier_binding"),
        ({"supersedes": data["release_id"]}, "cannot supersede itself"),
        ({"row_applicability": ({**override, "service_from": None},)}, "must narrow"),
        ({"row_applicability": ({**override, "service_from": date(2026, 7, 1)},)}, "start after the release"),
        ({"row_applicability": (override, {**override, "service_from": date(2027, 2, 1)})}, "two applicability overrides"),
        ({"row_applicability": ({**override, "ordinals": (4, 4)},)}, "lists an ordinal twice"),
        ({"row_applicability": ({**override, "service_to": date(2026, 12, 31)},)}, "ends before it starts"),
        ({"row_applicability": ({**override, "service_from": None, "service_to": date(2026, 6, 30)},)}, "ends before the release applies"),
        ({"sheets": {"1a": data["sheets"]["1a"]}, "row_applicability": ({**override, "catalog": "1ae"},)}, "absent catalog"),
    ):  # fmt: skip
        with pytest.raises(ValidationError, match=message):
            _ = CatalogManifest.model_validate({**data, **change}, strict=False)


def test_dataset_supersession_must_point_back_within_one_lineage() -> None:
    reviewed = cast(
        dict[str, list[dict[str, object]]],
        json.loads(
            (
                ROOT / "manifests/sources/reference-catalog-public-validation.json"
            ).read_text()
        ),
    )
    # The reviewed lineage: 101/2025 -> 14/2026 consolidation, repealed going
    # forward by 46/2026 from 2026-05-01, then 74/2026 and 102/2026.
    _ = DatasetManifest.model_validate_json(json.dumps(reviewed))
    ids = [cast(str, item["release_id"]) for item in reviewed["catalogs"]]
    after_14 = next(i for i in ids if "101-2025-after-14" in i)
    original_46 = next(i for i in ids if "46-2026-original" in i)
    after_102 = next(i for i in ids if "46-2026-after-102" in i)
    for target, field, value, message in (
        (after_102, "supersedes", "jgp-catalog-missing", "is not pinned"),
        (after_14, "supersedes", after_102, "later publication"),
        # Another act may supersede only going forward, never from the same
        # (or an earlier) service start: that would rewrite the repealed act.
        (original_46, "service_from", "2026-01-01", "another lineage"),
    ):
        changed = cast(
            dict[str, list[dict[str, object]]], json.loads(json.dumps(reviewed))
        )
        item = next(i for i in changed["catalogs"] if i["release_id"] == target)
        item[field] = value
        with pytest.raises(ValidationError, match=message):
            _ = DatasetManifest.model_validate_json(json.dumps(changed))


def test_row_windows_include_both_ends() -> None:
    window = RowWindow(date(2026, 7, 1), date(2026, 12, 31))
    assert [window.covers(day) for day in (
        date(2026, 6, 30), date(2026, 7, 1), date(2026, 12, 31), date(2027, 1, 1)
    )] == [False, True, True, False]  # fmt: skip
    assert RowWindow(date(2027, 1, 1), None).covers(date(2099, 1, 1))


def test_override_on_a_repeated_ordinal_is_refused(tmp_path: Path) -> None:
    rows = (
        Row(1, "A01", "5.51.01.0001001", "x", {"E": 1}, notes="- od 1.01.2027 r."),
        Row(1, "A02", "5.51.01.0001002", "y", {"E": 1}),
    )
    sheets: dict[str, tuple[Row, ...]] = {"nfz-1a-74-2026": rows}
    content = catalog_workbook(sheets)
    override = {"catalog": "1a", "ordinals": (1,), "service_from": date(2027, 1, 1),
                "service_to": None, "note_marker": "od 1.01.2027", "evidence": "x"}  # fmt: skip
    manifest = stage_catalog(
        tmp_path, content, sheets=sheets, release={"row_applicability": (override,)}
    )
    parsed = parse_catalog_workbook(BytesIO(content), manifest)
    with pytest.raises(SourceError, match="missing or ambiguous"):
        _ = row_windows(parsed, manifest)


def test_a_repealing_act_governs_only_from_its_own_start() -> None:
    releases = (
        ReleaseWindow("101-original", date(2026, 1, 1), date(2025, 12, 23), None),
        ReleaseWindow(
            "101-after-14", date(2026, 1, 1), date(2026, 2, 9), "101-original"
        ),
        ReleaseWindow(
            "46-original", date(2026, 5, 1), date(2026, 4, 30), "101-after-14"
        ),
    )
    horizon = date(2026, 10, 2)

    def governing(day: date, known_on: date = horizon) -> str:
        return resolve_release(releases, day, known_on).release_id

    # 14/2026 is retroactive to January once published; before that, the
    # original 101/2025 still answered.
    assert governing(date(2026, 1, 15), date(2026, 2, 8)) == "101-original"
    assert governing(date(2026, 1, 15)) == "101-after-14"
    assert governing(date(2026, 4, 30)) == "101-after-14"
    assert governing(date(2026, 5, 1)) == "46-original"
    with pytest.raises(NoRulesetForDate):
        _ = governing(date(2025, 12, 31))
