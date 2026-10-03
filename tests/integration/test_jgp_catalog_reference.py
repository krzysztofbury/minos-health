import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from typing import cast

import pytest

from minos.domain.jgp_ruleset import today_in_poland
from minos.domain.reference import ArtifactError, SourceError
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import canonical_json
from minos.store.reference import open_reference_bundle
from tests.fixtures.jgp_catalog import catalog_workbook, default_sheets, stage_catalog
from tests.integration.test_komunikat_reference import rank_manifest


def catalog_manifest(root: Path) -> DatasetManifest:
    base = rank_manifest(root)
    catalog = stage_catalog(root, catalog_workbook(default_sheets()))
    data = base.model_dump(mode="json")
    data["catalogs"] = [catalog.model_dump(mode="json")]
    data["catalog_knowledge_as_of"] = "2026-10-02"
    data["catalog_knowledge_evidence"] = "Synthetic lineage review on 2026-10-02."
    return DatasetManifest.model_validate_json(json.dumps(data))


def test_catalog_build_stores_rows_values_and_scopes_beside_ranks(
    tmp_path: Path,
) -> None:
    manifest = catalog_manifest(tmp_path)
    built = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    assert built.schema_version == 4
    assert built.row_counts["procedure_rank"] == 4  # schema 3 content kept
    assert built.row_counts["group_definition"] == 7
    assert built.row_counts["catalog_scope"] == 136
    with open_reference_bundle(tmp_path / "bundle") as (connection, _):
        values = connection.execute(
            """SELECT value.measure, value.unit, value.amount
            FROM group_base_value AS value JOIN group_definition AS grouped
              USING (release_id, catalog, source_row)
            WHERE grouped.code = 'PZD02' ORDER BY value.source_column"""
        ).fetchall()
        assert values == [
            ("hospitalization", "points_hundredths", 1399100),
            ("planned_hospitalization", "points_hundredths", 1287172),
            ("financed_stay", "days", 21),
        ]
        assert connection.execute(
            """SELECT count(*) FROM group_base_value JOIN group_definition
              USING (release_id, catalog, source_row)
            WHERE code = 'A01' AND measure = 'planned_hospitalization'"""
        ).fetchone() == (0,)  # empty source cell stays absent
        repeated = connection.execute(
            """SELECT grouped.source_row, group_concat(scope.label, '|')
            FROM group_definition AS grouped
            JOIN group_scope AS mark USING (release_id, catalog, source_row)
            JOIN catalog_scope AS scope
              ON scope.release_id = mark.release_id AND scope.catalog = mark.catalog
             AND scope.position = mark.position
            WHERE grouped.code = 'H21' GROUP BY grouped.source_row
            ORDER BY grouped.source_row"""
        ).fetchall()
        assert repeated == [
            (9, "chirurgia ogólna|ortopedia i traumat narz ruchu/ortopedia i traumat narz ruchu dla dzieci"),
            (10, "ortopedia i traumat narz ruchu/ortopedia i traumat narz ruchu dla dzieci"),
        ]  # fmt: skip
    again = build_reference_artifact(manifest, tmp_path, tmp_path / "again")
    assert again.database_sha256 == built.database_sha256


def test_catalog_manifest_and_database_correspondence_fail_closed(
    tmp_path: Path,
) -> None:
    manifest = catalog_manifest(tmp_path)
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(manifest, tmp_path, bundle)
    path = bundle / "manifest.json"
    value = cast(
        dict[str, dict[str, list[dict[str, object]]]], json.loads(path.read_bytes())
    )
    value["dataset"]["catalogs"][0]["act_date"] = "2026-07-22"
    _ = path.write_bytes(canonical_json(cast(dict[str, object], cast(object, value))))
    with pytest.raises(ArtifactError, match="catalog source policy"):
        with open_reference_bundle(bundle):
            pass


def test_corrupt_catalog_leaves_no_publishable_bundle(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    digest = manifest.catalogs[0].workbook.sha256
    _ = (tmp_path / "blobs/sha256" / digest).write_bytes(b"corrupt")
    with pytest.raises(SourceError, match="byte count"):
        _ = build_reference_artifact(manifest, tmp_path, tmp_path / "failed")
    assert not (tmp_path / "failed").exists()


def test_datasets_refuse_duplicate_catalog_documents(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    data = cast(dict[str, list[dict[str, object]]], manifest.model_dump(mode="json"))
    second = dict(data["catalogs"][0])
    second["release_id"] = "jgp-catalog-second"
    data["catalogs"].append(second)
    with pytest.raises(ValueError, match="pinned twice"):
        _ = DatasetManifest.model_validate_json(json.dumps(data))


NO_POINTS = "DELETE FROM group_base_value WHERE catalog = '1a' AND source_row = 7"
NO_SCOPE = "DELETE FROM group_scope; DELETE FROM catalog_scope WHERE position = 68"
OTHER_PRODUCT = (
    "UPDATE group_definition SET product_code = '5.51.01.0009999' WHERE source_row = 10"
)
NEW_REPEAT = "UPDATE group_definition SET code = 'A01' WHERE code = 'J01AJ'"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (NO_POINTS, "has no base points"),
        (NO_SCOPE, "68 scope columns"),
        (OTHER_PRODUCT, "several product codes"),
        # A new repeated code breaks the reviewed count before the mapping.
        (NEW_REPEAT, "do not match the reviewed manifest"),
    ],
)
def test_open_time_invariants_mirror_parser_rules(
    tmp_path: Path, tamper: str, message: str
) -> None:
    from minos.store.jgp_catalog_build import validate_catalog_bindings

    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    copy = tmp_path / "tampered.db"
    _ = copy.write_bytes((tmp_path / "bundle/minos.db").read_bytes())
    with closing(sqlite3.connect(copy)) as connection:
        validate_catalog_bindings(connection, manifest)  # untouched passes
        _ = connection.executescript(tamper)
        with pytest.raises(ArtifactError, match=message):
            validate_catalog_bindings(connection, manifest)


def test_schema_ties_each_measure_to_its_unit(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    copy = tmp_path / "tampered.db"
    _ = copy.write_bytes((tmp_path / "bundle/minos.db").read_bytes())
    with closing(sqlite3.connect(copy)) as connection:
        for statement in (
            "UPDATE group_base_value SET unit = 'points_hundredths' WHERE measure = 'financed_stay'",
            "UPDATE group_base_value SET unit = 'days' WHERE measure = 'hospitalization'",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                _ = connection.execute(statement)


def _built(tmp_path: Path) -> Path:
    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    return tmp_path / "bundle"


def test_service_date_selects_the_twin_row_and_keeps_modifiers_unbound(
    tmp_path: Path,
) -> None:
    from minos.store.jgp_ruleset import catalog_group, resolve_ruleset

    bundle = _built(tmp_path)
    with open_reference_bundle(bundle) as (connection, _):
        answers = {
            day: catalog_group(
                connection, resolve_ruleset(connection, day, date(2026, 10, 2)), "H21"
            )
            for day in (date(2026, 12, 31), date(2027, 1, 1))
        }
    before, after = answers[date(2026, 12, 31)], answers[date(2027, 1, 1)]
    before_group = cast(dict[str, object], before["group"])
    after_group = cast(dict[str, object], after["group"])
    assert (before_group["ordinal"], after_group["ordinal"]) == (3, 4)
    assert [
        scope["label"] for scope in cast(list[dict[str, object]], before["scopes"])
    ][0] == "chirurgia ogólna"
    assert len(cast(list[object], after["scopes"])) == 1
    assert before["base_values"] == after["base_values"]
    assert (
        cast(dict[str, object], after_group["applicability"])["evidence"]
        == "Synthetic § 4 ust. 5."
    )
    ruleset = cast(dict[str, object], after["ruleset"])
    assert cast(dict[str, object], ruleset["modifiers"])["binding"] == "unbound"
    assert after["points_kind"] == "catalog_base_points_without_modifiers"


def _cli(bundle: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minos", "jgp", "group", "--data", str(bundle), "--json", *arguments],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )  # fmt: skip


def test_cli_reports_dates_unbound_modifiers_and_explicit_errors(
    tmp_path: Path,
) -> None:
    bundle = _built(tmp_path)
    answer = _cli(
        bundle,
        "--code",
        "PZD02",
        "--service-date",
        "2026-07-10",
        "--known-on",
        "2026-08-01",
    )
    assert answer.returncode == 0, answer.stderr
    body = cast(dict[str, object], json.loads(answer.stdout))
    assert body["date_selection"] == {
        "known_on": "explicit",
        "service_date": "explicit",
    }
    values = {
        value["measure"]: value["amount"]
        for value in cast(list[dict[str, object]], body["base_values"])
    }
    assert values["planned_hospitalization"] == 1287172
    ruleset = cast(dict[str, object], body["ruleset"])
    assert ruleset["modifiers"] == {
        "binding": "unbound",
        "meaning": "No reviewed MODTAR binding yet; this is not an empty set of"
        + " applicable modifiers and final points cannot be calculated.",
    }
    defaulted_run = _cli(bundle, "--code", "PZD02")
    assert defaulted_run.returncode == 0, defaulted_run.stderr
    defaulted = cast(dict[str, object], json.loads(defaulted_run.stdout))
    # The real clock passes the bundle's 2026-10-02 horizon; the CLI then caps
    # knowledge at the horizon and says so instead of claiming today.
    capped = today_in_poland() > date(2026, 10, 2)
    assert defaulted["date_selection"] == {
        "known_on": "default_capped_to_bundle_knowledge"
        if capped
        else "default_today_europe_warsaw",
        "service_date": "default_today_europe_warsaw",
    }
    uncovered = _cli(bundle, "--code", "PZD02", "--service-date", "2026-06-30")
    assert uncovered.returncode == 5 and uncovered.stdout == ""
    assert json.loads(uncovered.stderr)["error"]["code"] == "no_ruleset_for_date"
    for arguments in (
        ("--code", "PZD02", "--service-date", "2026-02-30"),
        ("--code", "pzd02"),
    ):
        assert _cli(bundle, *arguments).returncode == 2
    missing = _cli(bundle, "--code", "Z99", "--service-date", "2026-07-10")
    assert missing.returncode == 1
    assert json.loads(missing.stderr)["error"]["code"] == "group_not_on_date"


TWIN_BOUNDARY = (
    "UPDATE group_definition SET service_to = '2026-09-30' WHERE ordinal = 3"
)
EARLY_ROW = (
    "UPDATE group_definition SET service_from = '2026-06-01' WHERE source_row = 7"
)
EVIDENCE = (
    "UPDATE group_definition SET applicability_evidence = 'edited' WHERE ordinal = 4"
)
NOTE = "UPDATE group_definition SET notes = 'edited' WHERE ordinal = 4"
RELEASE_START = "UPDATE catalog_release SET service_from = '2026-05-01'"
PUBLISHED = "UPDATE catalog_release SET published_on = '2026-07-24'"
HORIZON = "UPDATE catalog_knowledge SET as_of = '2027-01-01'"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        (TWIN_BOUNDARY, "row window does not match the manifest"),
        (EARLY_ROW, "row window does not match the manifest"),
        (EVIDENCE, "row window does not match the manifest"),
        (NOTE, "no longer confirms its window"),
        (RELEASE_START, "source policy does not match"),
        (PUBLISHED, "source policy does not match"),
        (HORIZON, "knowledge horizon does not match"),
    ],
)
def test_open_time_invariants_cover_row_windows(
    tmp_path: Path, tamper: str, message: str
) -> None:
    from minos.store.jgp_catalog_build import validate_catalog_bindings

    manifest = catalog_manifest(tmp_path)
    _ = build_reference_artifact(manifest, tmp_path, tmp_path / "bundle")
    copy = tmp_path / "tampered.db"
    _ = copy.write_bytes((tmp_path / "bundle/minos.db").read_bytes())
    with closing(sqlite3.connect(copy)) as connection:
        _ = connection.executescript(tamper)
        with pytest.raises(ArtifactError, match=message):
            validate_catalog_bindings(connection, manifest)
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            _ = connection.execute(
                "UPDATE catalog_release SET modifier_binding = 'none'"
            )


def test_knowledge_is_bounded_by_the_reviewed_bundle_horizon(tmp_path: Path) -> None:
    manifest = catalog_manifest(tmp_path)
    data = manifest.model_dump(mode="json")
    data["catalog_knowledge_as_of"] = "2026-09-30"
    narrowed = DatasetManifest.model_validate_json(json.dumps(data))
    bundle = tmp_path / "bundle"
    _ = build_reference_artifact(narrowed, tmp_path, bundle)
    beyond = _cli(
        bundle,
        "--code",
        "PZD02",
        "--service-date",
        "2026-07-10",
        "--known-on",
        "2026-10-01",
    )
    assert beyond.returncode == 7
    assert json.loads(beyond.stderr)["error"]["code"] == "known_on_beyond_bundle"
    # Today is after 2026-09-30, so the default knowledge date is capped.
    capped = _cli(bundle, "--code", "PZD02", "--service-date", "2026-07-10")
    assert capped.returncode == 0, capped.stderr
    body = cast(dict[str, dict[str, object]], json.loads(capped.stdout))
    assert body["date_selection"]["known_on"] == "default_capped_to_bundle_knowledge"
    assert (
        body["ruleset"]["known_on"]
        == body["ruleset"]["knowledge_as_of"]
        == "2026-09-30"
    )
    data["catalog_knowledge_as_of"] = "2026-07-01"  # before the 2026-07-23 publication
    with pytest.raises(ValueError, match="published after the horizon"):
        _ = DatasetManifest.model_validate_json(json.dumps(data))


def _cli_without_time_zone(
    bundle: Path, *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "minos", "jgp", "group", "--data", str(bundle), "--json", *arguments],
        capture_output=True, text=True, timeout=60, check=False,
        env={**os.environ, "PYTHONTZPATH": "/nonexistent"},
    )  # fmt: skip


def test_cli_fails_explicitly_without_time_zone_data_or_catalog(tmp_path: Path) -> None:
    bundle = _built(tmp_path)
    defaulted = _cli_without_time_zone(bundle, "--code", "PZD02")
    assert defaulted.returncode == 2
    assert json.loads(defaulted.stderr)["error"]["code"] == "timezone_unavailable"
    explicit = _cli_without_time_zone(
        bundle,
        "--code",
        "PZD02",
        "--service-date",
        "2026-07-10",
        "--known-on",
        "2026-08-01",
    )
    assert explicit.returncode == 0, explicit.stderr
    legacy = tmp_path / "legacy"
    ranks = tmp_path / "ranks"
    _ = build_reference_artifact(rank_manifest(ranks), ranks, legacy)
    no_catalog = _cli(
        legacy,
        "--code",
        "PZD02",
        "--service-date",
        "2026-07-10",
        "--known-on",
        "2026-08-01",
    )
    assert no_catalog.returncode == 8
    assert json.loads(no_catalog.stderr)["error"]["code"] == "no_jgp_catalog"


def test_cli_text_output_labels_base_points_and_unbound_modifiers(
    tmp_path: Path,
) -> None:
    bundle = _built(tmp_path)
    text = subprocess.run(
        [sys.executable, "-m", "minos", "jgp", "group", "--data", str(bundle), "--code", "PZD02",
         "--service-date", "2026-07-10", "--known-on", "2026-08-01"],
        capture_output=True, text=True, timeout=60, check=True,
    ).stdout  # fmt: skip
    assert "punkty bazowe wg katalogu, bez modyfikatorów:" in text
    assert "planned_hospitalization: 12871.72" in text
    assert "financed_stay: 21 days" in text
    assert "Modifiers: unbound" in text
