import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from minos.cli.main import main
from minos.ingest.icd9_candidate import CzsIcd9Policy, propose_icd9_release
from minos.ingest.icd10_candidate import CandidateRejected
from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from minos.store.artifact import ArtifactManifest
from tests.fixtures.icd9_xml import XML
from tests.integration.test_canonical_reference import canonical_manifest

_POLICY = Path(__file__).parents[2] / "manifests/policy/czs-icd9-automatic.json"
_HEADER = 'czas_pub="2026-09-24T08:35:45" nr_gen="93"'
_ADDED = (
    '<pozycja_slownika kod="A02" nazwa="Synthetic added procedure" status="A">'
    + '<daty_proc data_od="{start}"/></pozycja_slownika>\n</komunikat>'
)


def _generation(
    *,
    generation: str = "94",
    start: str | None = None,
    closes: str | None = "2026-10-05",
    version: str = 'symbol="5.90" edycja="5"',
) -> bytes:
    """CZS generation published 2026-10-15.

    ``closes`` moves the end of A01 (2026-09-24 in generation 93); ``start``
    adds one code from that day.
    """
    text = XML.decode().replace(
        _HEADER, f'czas_pub="2026-10-15T08:00:00" nr_gen="{generation}"'
    )
    text = text.replace('symbol="5.90" edycja="5"', version)
    if closes is not None:
        text = text.replace('data_do="2026-09-24"', f'data_do="{closes}"')
    if start is not None:
        text = text.replace("</komunikat>", _ADDED.format(start=start))
    return text.encode()


def _archive(root: Path, content: bytes) -> str:
    digest = hashlib.sha256(content).hexdigest()
    _ = (root / "blobs/sha256" / digest).write_bytes(content)
    return digest


def _live(root: Path) -> tuple[DatasetManifest, Path]:
    base = canonical_manifest(root)
    bundle = root / "live"
    _ = build_reference_artifact(base, root, bundle)
    return base, bundle


def test_new_generation_governs_from_its_earliest_changed_date(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, bundle = _live(tmp_path)
    content = _generation()
    arguments = [
        "data",
        "candidate-icd9",
        "--live-bundle",
        str(bundle),
        "--archive-root",
        str(tmp_path),
        "--czs-sha256",
        _archive(tmp_path, content),
        "--fetched-at",
        "2026-10-15T12:00:00Z",
        "--policy",
        str(_POLICY),
        "--work",
        str(tmp_path / "work"),
    ]

    main(arguments)

    output = capsys.readouterr().out
    assert str(tmp_path) not in output
    report = cast(dict[str, object], json.loads(output))
    release_id = f"icd9-czs-5-90-gen-94-{hashlib.sha256(content).hexdigest()[:12]}"
    assert report["decision"] == "would_activate"
    assert (report["previous_release_id"], report["release_id"]) == (
        base.sources[1].release_id,
        release_id,
    )
    assert (report["added_codes"], report["removed_codes"]) == (0, 0)
    built = ArtifactManifest.model_validate_json(
        (tmp_path / "work/first/manifest.json").read_bytes()
    )
    *_, previous_workbook, release, companion = built.dataset.sources
    previous = built.dataset.sources[1]
    # A01 used to end on 2026-09-24, 21 days before publication, so the
    # generation governs from Polish midnight that day; earlier service dates
    # keep generation 93.
    starts = datetime(2026, 9, 23, 22, tzinfo=UTC)
    assert (previous.valid_to, release.valid_from) == (starts, starts)
    assert previous_workbook.context_for == previous.release_id
    assert companion.context_for == release.release_id
    assert companion.sha256 == previous_workbook.sha256
    assert release.notices[0].startswith("Source: NFZ Centralne Zasoby Slownikowe")
    assert "generation 94" in release.notices[0]


def _case(name: str) -> bytes:
    if name == "backdated_beyond_bound":
        return _generation(start="2026-01-01")
    if name == "older_generation":
        return _generation(generation="92")
    if name == "republished_unchanged":
        return _generation(closes=None)
    if name == "new_edition":
        return _generation(version='symbol="6.01" edycja="6"')
    if name == "new_version_without_workbook":
        return _generation(version='symbol="5.91" edycja="5"')
    return b"<komunikat"


@pytest.mark.parametrize(
    "name,criterion,code",
    [
        ("backdated_beyond_bound", "history", "retroactive_amendment_exceeds_bound"),
        ("older_generation", "history", "not_newer_than_live_release"),
        ("republished_unchanged", "history", "release_content_unchanged"),
        ("new_edition", "licence", "edition_changed"),
        ("new_version_without_workbook", "bytes", "workbook_companion_missing"),
        ("malformed", "parse", "product_rejected_by_parser"),
    ],
)
def test_proposal_names_the_failed_criterion(
    tmp_path: Path, name: str, criterion: str, code: str
) -> None:
    base = canonical_manifest(tmp_path)

    with pytest.raises(CandidateRejected) as refused:
        _ = propose_icd9_release(
            base,
            tmp_path,
            czs_sha256=_archive(tmp_path, _case(name)),
            fetched_at=datetime(2026, 10, 15, tzinfo=UTC),
            policy=CzsIcd9Policy.load(_POLICY),
        )

    assert (refused.value.criterion, refused.value.code) == (criterion, code)


@pytest.mark.parametrize("notice", ["Generation {nr_gen}.", "Version {version!r}."])
def test_policy_refuses_unsupported_notice_fields(notice: str) -> None:
    value = cast(dict[str, object], json.loads(_POLICY.read_text()))
    value["czs_notices"] = [notice]

    with pytest.raises(ValidationError):
        _ = CzsIcd9Policy.model_validate_json(json.dumps(value))
