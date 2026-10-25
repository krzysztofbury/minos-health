import asyncio
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from minos.cli.main import main
from minos.http.app import create_app
from minos.ingest.icd10_candidate import (
    CandidateRejected,
    RskIcd10Policy,
    propose_icd10_release,
)
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import ArtifactManifest
from tests.integration.test_reference_artifact import FIXTURES, stage_source

_DESCRIPTION = "Synthetic RSK licence statement."
_PUBLISHER = "Synthetic Publisher"
_LIVE_FROM = datetime(2025, 1, 1, tzinfo=UTC)
_MINIMAL = (FIXTURES / "minimal.xml").read_bytes()
_CHANGED = _MINIMAL.replace(
    "<name>Druga kategoria próbna</name>".encode(),
    b"<name>Druga kategoria zmieniona</name>",
)
_REMOVED_NODE = (
    '<node code="U99.9"><name>Żółć próbna</name><attributes>'
    + '<attribute name="EN">Synthetic standalone label</attribute></attributes></node>'
)
_REMOVED = _MINIMAL.replace(_REMOVED_NODE.encode(), b"")
# The 2026-09-22 RSK re-export of 013 changed only these header bytes (#184).
_REEXPORT = _MINIMAL.replace(
    b'xmlns="http://rsk.rejestrymedyczne.csioz.gov.pl"',
    b'xmlns="https://rsk3.ezdrowie.gov.pl/"',
).replace(b'version="7"', b'version="13"')


def _archive(root: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    blob = root / "blobs" / "sha256" / digest
    blob.parent.mkdir(parents=True, exist_ok=True)
    _ = blob.write_bytes(content)
    return digest


def _basic_info(product: bytes, **changes: object) -> dict[str, object]:
    info: dict[str, object] = {
        "hcd": "00CD10",
        "version": "002",
        "description": _DESCRIPTION,
        "validDateFrom": "2026-01-01T00:00:00.000+00:00",
        "validDateTo": None,
        "publisher": {"name": _PUBLISHER, "uuid": "ignored"},
        "files": [
            {
                "uuid": "cfddde09-bf26-4c0a-8cd9-843cc62cc96f",
                "sizeBytes": 4,
                "creationDate": "2022-06-06T09:28:13.397+00:00",
                "extension": "pdf",
                "archived": False,
            },
            {
                "uuid": "00000000-0000-4000-8000-000000000002",
                "sizeBytes": len(product),
                "creationDate": "2025-12-01T10:00:00.000+00:00",
                "extension": "xml",
                "archived": False,
            },
        ],
    }
    info.update(changes)
    return info


def _policy(*, max_removed: float = 0.2) -> RskIcd10Policy:
    return RskIcd10Policy(
        schema_version=1,
        source_class="rsk-icd10",
        hcd="00CD10",
        approved_on=date(2026, 10, 20),
        approval="Synthetic owner approval.",
        description_sha256=hashlib.sha256(_DESCRIPTION.encode()).hexdigest(),
        publisher=_PUBLISHER,
        license="Synthetic licence",
        notices=("Synthetic notice.",),
        max_removed_code_ratio=max_removed,
        max_concept_count_change_ratio=0.2,
    )


def _live(root: Path) -> tuple[DatasetManifest, Path]:
    staged = stage_source(root)
    source = staged.sources[0].model_copy(
        update={"valid_from": _LIVE_FROM, "source_version": "002"}
    )
    base = staged.model_copy(update={"sources": (source,)})
    bundle = root / "live"
    _ = build_reference_artifact(base, root, bundle)
    return base, bundle


def _run_cli(
    root: Path,
    bundle: Path,
    product: bytes,
    policy: RskIcd10Policy,
    capsys: pytest.CaptureFixture[str],
) -> tuple[int, dict[str, object]]:
    info = json.dumps(_basic_info(product)).encode()
    policy_path = root / "policy.json"
    _ = policy_path.write_text(policy.model_dump_json())
    arguments = [
        "data",
        "candidate-icd10",
        "--live-bundle",
        str(bundle),
        "--archive-root",
        str(root),
        "--product-sha256",
        _archive(root, product),
        "--basic-info-sha256",
        _archive(root, info),
        "--fetched-at",
        "2025-12-02T08:00:00Z",
        "--policy",
        str(policy_path),
        "--work",
        str(root / "work"),
    ]
    try:
        main(arguments)
        code = 0
    except SystemExit as exit_error:
        code = cast(int, exit_error.code)
    output = capsys.readouterr().out
    assert str(root) not in output
    return code, cast(dict[str, object], json.loads(output))


def test_new_release_is_added_beside_the_closed_live_release(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle = _live(tmp_path)

    code, report = _run_cli(tmp_path, bundle, _CHANGED, _policy(), capsys)

    assert code == 0
    assert report["decision"] == "would_activate"
    assert report["previous_release_id"] == "icd10-synthetic-v1"
    release_id = f"icd10-rsk-002-{hashlib.sha256(_CHANGED).hexdigest()[:12]}"
    assert report["release_id"] == release_id
    assert (report["added_codes"], report["removed_codes"]) == (0, 0)
    assert report["changed_preferred_descriptions"] == 1
    built = ArtifactManifest.model_validate_json(
        (tmp_path / "work" / "first" / "manifest.json").read_bytes()
    )
    assert report["database_sha256"] == built.database_sha256
    previous, release = built.dataset.sources
    assert previous.valid_from == _LIVE_FROM
    assert previous.valid_to == release.valid_from == datetime(2026, 1, 1, tzinfo=UTC)
    assert release.release_id == release_id
    assert (release.source_version, release.publisher) == ("002", _PUBLISHER)
    assert release.source_url.endswith("00000000-0000-4000-8000-000000000002")
    assert not (tmp_path / "work" / "second").exists()


def test_removing_codes_beyond_the_policy_bound_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle = _live(tmp_path)

    code, report = _run_cli(
        tmp_path, bundle, _REMOVED, _policy(max_removed=0.1), capsys
    )

    assert code == 9
    assert report == {
        "version": 1,
        "decision": "rejected",
        "criterion": "regression",
        "code": "removed_codes_exceed_bound",
    }


def _case(name: str) -> tuple[bytes, dict[str, object]]:
    product = _CHANGED
    if name == "reworded_licence":
        return product, _basic_info(product, description="Reworded licence statement.")
    if name == "other_publisher":
        return product, _basic_info(product, publisher={"name": "Another Publisher"})
    if name == "unlisted_size":
        listed = {
            "uuid": "00000000-0000-4000-8000-000000000002",
            "sizeBytes": len(product) + 1,
            "creationDate": "2025-12-01T10:00:00.000+00:00",
            "extension": "xml",
            "archived": False,
        }
        return product, _basic_info(product, files=[listed])
    if name == "backdated":
        return product, _basic_info(
            product, validDateFrom="2025-01-01T00:00:00.000+00:00"
        )
    if name == "reexport":
        return _REEXPORT, _basic_info(
            _REEXPORT, validDateFrom="2025-01-01T00:00:00.000+00:00"
        )
    if name == "live_bytes":
        return _MINIMAL, _basic_info(_MINIMAL)
    if name == "malformed_xml":
        return b"<hcd", _basic_info(b"<hcd")
    info = _basic_info(product)
    del info["version"]
    return product, info


@pytest.mark.parametrize(
    "name,criterion,code",
    [
        ("reworded_licence", "licence", "licence_statement_changed"),
        ("other_publisher", "licence", "publisher_changed"),
        ("unlisted_size", "bytes", "product_not_listed_by_rsk"),
        # Same version and start as the live release, with changed content.
        ("backdated", "history", "not_newer_than_live_release"),
        ("reexport", "history", "release_content_unchanged"),
        ("live_bytes", "history", "release_already_present"),
        ("malformed_xml", "parse", "product_rejected_by_parser"),
        ("metadata_without_version", "parse", "basic_info_schema_changed"),
    ],
)
def test_proposal_names_the_failed_criterion(
    tmp_path: Path, name: str, criterion: str, code: str
) -> None:
    base, _ = _live(tmp_path)
    product, info = _case(name)

    with pytest.raises(CandidateRejected) as refused:
        _ = propose_icd10_release(
            base,
            tmp_path,
            product_sha256=_archive(tmp_path, product),
            basic_info_sha256=_archive(tmp_path, json.dumps(info).encode()),
            fetched_at=datetime(2025, 12, 2, tzinfo=UTC),
            policy=_policy(),
        )

    assert (refused.value.criterion, refused.value.code) == (criterion, code)


def test_unarchived_product_is_refused_before_parsing(tmp_path: Path) -> None:
    base, _ = _live(tmp_path)
    info = json.dumps(_basic_info(_CHANGED)).encode()

    with pytest.raises(CandidateRejected) as refused:
        _ = propose_icd10_release(
            base,
            tmp_path,
            product_sha256=hashlib.sha256(_CHANGED).hexdigest(),
            basic_info_sha256=_archive(tmp_path, info),
            fetched_at=datetime(2025, 12, 2, tzinfo=UTC),
            policy=_policy(),
        )

    assert (refused.value.criterion, refused.value.code) == (
        "bytes",
        "product_not_archived",
    )


def _two_releases(root: Path, valid_from: str) -> tuple[Path, str]:
    base, _ = _live(root)
    info = _basic_info(_CHANGED, validDateFrom=valid_from)
    candidate = propose_icd10_release(
        base,
        root,
        product_sha256=_archive(root, _CHANGED),
        basic_info_sha256=_archive(root, json.dumps(info).encode()),
        fetched_at=datetime(2025, 12, 2, tzinfo=UTC),
        policy=_policy(),
    )
    bundle = root / "candidate"
    _ = build_reference_artifact(candidate, root, bundle)
    return bundle, candidate.sources[-1].release_id


def _same_day(root: Path, later: str) -> tuple[Path, str]:
    """Two canonical ICD-10 releases starting at the same instant, apart only
    in source creation time; ``later`` names the one created last."""
    base, _ = _live(root)
    info = _basic_info(_CHANGED, validDateFrom="2026-01-01T00:00:00.000+00:00")
    candidate = propose_icd10_release(
        base,
        root,
        product_sha256=_archive(root, _CHANGED),
        basic_info_sha256=_archive(root, json.dumps(info).encode()),
        fetched_at=datetime(2025, 12, 2, tzinfo=UTC),
        policy=_policy(),
    )
    start = datetime(2025, 12, 31, 23, tzinfo=UTC)  # 2026-01-01 in Poland
    early, late = datetime(2025, 11, 1, tzinfo=UTC), datetime(2025, 11, 2, tzinfo=UTC)
    new_id = candidate.sources[-1].release_id
    sources = tuple(
        source.model_copy(
            update={
                "valid_from": start,
                "valid_to": None,
                "source_created_at": late
                if (source.release_id == new_id) == (later == "new")
                else early,
            }
        )
        if source.system == "icd10-pl" and source.context_for is None
        else source
        for source in candidate.sources
    )
    bundle = root / "same-day"
    _ = build_reference_artifact(
        candidate.model_copy(update={"sources": sources}), root, bundle
    )
    return bundle, new_id


@pytest.mark.parametrize("later", ["new", "old"])
def test_same_day_releases_resolve_to_the_latest_everywhere(
    tmp_path: Path, later: str
) -> None:
    # Owner decision 2026-10-25 (#235): the latest release leads, and pages,
    # MCP, the release list and dated search all name the same one.
    from minos.http.mcp import _Api  # pyright: ignore[reportPrivateUsage]

    bundle, new_id = _same_day(tmp_path, later)
    winner = new_id if later == "new" else "icd10-synthetic-v1"

    with TestClient(create_app(bundle)) as client:
        listed = cast(
            list[dict[str, object]], client.get("/api/v1/releases").json()["releases"]
        )
        page = client.get("/slowniki/icd-10/M00")
        # After the shared start and before it: the same release both times.
        dated = {
            day: client.post(
                "/api/v1/search",
                json={
                    "system": "icd10-pl",
                    "query": "druga kategoria",
                    "service_date": day,
                },
            )
            for day in ("2026-02-01", "2025-06-01")
        }
        api = _Api(cast(FastAPI, client.app))
        try:
            mcp = [
                asyncio.run(api.release("icd10-pl", day))
                for day in (date(2026, 2, 1), date(2025, 6, 1))
            ]
        finally:
            asyncio.run(api.close())

    icd10 = [item for item in listed if item["system"] == "icd10-pl"]
    assert [item["release_id"] for item in icd10 if item["in_force"]] == [winner]
    assert icd10[0]["release_id"] == winner
    assert {
        day: (r.status_code, r.json()["release_id"]) for day, r in dated.items()
    } == {
        "2026-02-01": (200, winner),
        "2025-06-01": (200, winner),
    }
    assert [item["release_id"] for item in mcp] == [winner, winner]
    wording = "zmieniona" if later == "new" else "próbna"
    assert page.status_code == 200
    assert f"Druga kategoria {wording}" in page.text


@pytest.mark.parametrize(
    "valid_from,in_force",
    [
        ("2026-01-01T00:00:00.000+00:00", "new"),
        ("2099-01-01T00:00:00.000+00:00", "old"),
    ],
)
def test_releases_list_the_release_in_force_today_first(
    tmp_path: Path, valid_from: str, in_force: str
) -> None:
    bundle, release_id = _two_releases(tmp_path, valid_from)
    expected = release_id if in_force == "new" else "icd10-synthetic-v1"

    with TestClient(create_app(bundle)) as client:
        listed = cast(
            list[dict[str, object]], client.get("/api/v1/releases").json()["releases"]
        )
        page = client.get("/slowniki/icd-10/M00")

    icd10 = [item for item in listed if item["system"] == "icd10-pl"]
    assert [item["release_id"] for item in icd10 if item["in_force"]] == [expected]
    assert icd10[0]["release_id"] == expected
    assert {item["valid_to"] for item in icd10} == {
        None,
        valid_from[:10] + "T00:00:00Z",
    }
    wording = "zmieniona" if in_force == "new" else "próbna"
    assert page.status_code == 200
    assert f"Druga kategoria {wording}" in page.text


def test_dated_search_answers_from_the_release_in_force_that_day(
    tmp_path: Path,
) -> None:
    bundle, release_id = _two_releases(tmp_path, "2026-01-01T00:00:00.000+00:00")

    def search(**fields: object) -> tuple[int, dict[str, object]]:
        response = client.post(
            "/api/v1/search",
            json={"system": "icd10-pl", "query": "druga kategoria", **fields},
        )
        return response.status_code, cast(dict[str, object], response.json())

    with TestClient(create_app(bundle)) as client:
        # Midnight in Poland is 23:00 UTC on 2025-12-31, so 2026-01-01 is new.
        answers = {
            day: search(service_date=day)
            for day in ("2024-03-01", "2025-12-31", "2026-01-01")
        }
        mismatch = search(release_id=release_id, service_date="2025-12-31")
        undated = search()

    selected = {
        day: (code, answer["release_id"]) for day, (code, answer) in answers.items()
    }
    assert selected == {
        "2024-03-01": (200, "icd10-synthetic-v1"),
        "2025-12-31": (200, "icd10-synthetic-v1"),
        "2026-01-01": (200, release_id),
    }
    labels = [
        cast(list[dict[str, object]], answers[day][1]["results"])[0]["label"]
        for day in ("2025-12-31", "2026-01-01")
    ]
    assert labels == ["Druga kategoria próbna", "Druga kategoria zmieniona"]
    assert (mismatch[0], undated[0]) == (400, 400)
