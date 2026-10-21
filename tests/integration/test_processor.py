# pyright: reportMissingTypeStubs=false
import base64
import hashlib
import io
import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast, final

import httpx
import pytest
from botocore.exceptions import ClientError  # type: ignore[import-untyped]

from minos.cli.processor import execute_processor
from minos.ingest import processor
from minos.ingest.archive_build import R2BlobReader
from minos.ingest.processor import BlobReader
from minos.ingest.watchers.nfz.public_models import Czs, NfzProjection
from minos.ingest.watchers.publication.filesystem import ImmutableGenerationPublisher
from minos.ingest.watchers.rsk.public_models import (
    RedistributionManifest,
    RskProvenanceEntry,
    RskProvenanceProjection,
)
from minos.ingest.watchers.uptime_kuma import UptimeKumaClient, UptimeKumaTargets
from tests.integration import test_icd9_candidate as icd9
from tests.integration import test_icd10_candidate as icd10

_POLICIES = Path(__file__).parents[2] / "manifests/policy"
_BUCKET = "minos-archive-synthetic"
_TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
_OBSERVED = "2026-10-15T12:00:00.000Z"


@final
class _R2:
    """Serves the test archive as R2 answers HEAD and checksum-mode GET."""

    def __init__(self, root: Path) -> None:
        self.blobs = {
            path.name: path.read_bytes() for path in (root / "blobs/sha256").iterdir()
        }
        self.reads: list[str] = []

    def _found(self, parameters: Mapping[str, object]) -> tuple[str, bytes]:
        assert parameters["Bucket"] == _BUCKET
        assert parameters["ChecksumMode"] == "ENABLED"
        digest = cast(str, parameters["Key"]).removeprefix("blobs/sha256/")
        if digest not in self.blobs:
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return digest, self.blobs[digest]

    def head_object(self, **parameters: object) -> Mapping[str, object]:
        digest, content = self._found(parameters)
        return {
            "ContentLength": len(content),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
        }

    def get_object(self, **parameters: object) -> Mapping[str, object]:
        digest, content = self._found(parameters)
        self.reads.append(digest)
        return {
            "Body": io.BytesIO(content),
            "ContentLength": len(content),
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
        }

    def close(self) -> None:
        pass


def _publish(root: Path, source: str, content: bytes) -> None:
    """Publish through the watcher's own publisher, so the layout is real."""
    root.mkdir(exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    path = f"api/v1/provenance/{source.lower()}.json"
    manifest: dict[str, object] = {
        "projection_path": path,
        "projection_sha256": digest,
        "projection_size_bytes": len(content),
        "redistribution": {"artifacts": [], "schema_version": 1},
        "schema_version": 1,
        "source": source,
        "state_revision": 7,
    }
    encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    _ = ImmutableGenerationPublisher(root, source=source).publish(
        7, digest, {"manifest.json": encoded, path: content}
    )


def _nfz(public: Path, czs: bytes) -> None:
    entry = Czs(
        source_url="https://slowniki.nfz.gov.pl/WersjeSlownikow/PobierzXml?kodSlownika=ICD9&nrGen=94",
        sha256=hashlib.sha256(czs).hexdigest(),
        observed_at=_OBSERVED,
        title="ICD-9 PL",
        channel="czs",
        code="ICD9",
        generation=94,
        generated_at="2026-10-15T08:00:00",
        year=None,
        grouper_type=None,
        version="5.90",
        published_date=None,
        publisher_valid_from=None,
        display_evidence=None,
    )
    projection = NfzProjection(
        through_state_revision=7, observed_at=_OBSERVED, entries=(entry,)
    )
    _publish(public / "nfz", "NFZ", projection.model_dump_json().encode())


@final
class _Run:
    def __init__(self, root: Path, bundle: Path, policies: Path = _POLICIES) -> None:
        self.root = root
        self.bundle = bundle
        self.policies = policies
        self.public = root / "public"
        self.work = root / "work"
        self.state = root / "state.json"
        self.public.mkdir()
        self.work.mkdir()
        targets = root / "targets.json"
        _ = targets.write_text(
            json.dumps({"version": 1, "sources": {"data-update": _TOKEN}})
        )
        self.targets = targets
        self.r2 = _R2(root)
        self.pushes: list[tuple[str, str]] = []

    def __call__(self, capsys: pytest.CaptureFixture[str]) -> tuple[int, object]:
        def kuma(targets: UptimeKumaTargets) -> UptimeKumaClient:
            def handle(request: httpx.Request) -> httpx.Response:
                params = request.url.params
                self.pushes.append((params["status"], params["msg"]))
                return httpx.Response(200, json={"ok": True})

            return UptimeKumaClient(targets, transport=httpx.MockTransport(handle))

        def reader(bucket: str, _endpoint: str) -> BlobReader:
            return R2BlobReader(self.r2, bucket)

        code = execute_processor(
            {
                "public_directory": self.public,
                "live_bundle": self.bundle,
                "policy_directory": self.policies,
                "work": self.work,
                "state": self.state,
                "uptime_kuma_targets": self.targets,
                "archive_bucket": _BUCKET,
                "archive_endpoint": "https://synthetic.invalid",
            },
            reader_factory=reader,
            kuma_factory=kuma,
        )
        output = capsys.readouterr().out
        assert str(self.root) not in output
        assert list(self.work.iterdir()) == []
        return code, cast(object, json.loads(output))


def _icd9_run(root: Path, czs: bytes) -> _Run:
    _, bundle = icd9._live(root)  # pyright: ignore[reportPrivateUsage]
    _ = icd9._archive(root, czs)  # pyright: ignore[reportPrivateUsage]
    run = _Run(root, bundle)
    _nfz(run.public, czs)
    return run


def test_new_generation_is_judged_once_and_reported_every_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    czs = icd9._generation()  # pyright: ignore[reportPrivateUsage]
    run = _icd9_run(tmp_path, czs)
    release_id = f"icd9-czs-5-90-gen-94-{hashlib.sha256(czs).hexdigest()[:12]}"

    first = run(capsys)
    reads = len(run.r2.reads)
    second = run(capsys)

    summary = {
        "version": 1,
        "verdicts": [
            {
                "system": "icd9-pl",
                "decision": "would_activate",
                "release_id": release_id,
            }
        ],
    }
    assert first == second == (0, summary)
    assert run.pushes == [("up", f"would_activate {release_id}")] * 2
    # The live inputs and the new generation are fetched once, for the first run.
    assert reads == len(set(run.r2.reads)) > 1
    assert len(run.r2.reads) == reads


def test_a_rules_change_judges_stored_inputs_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    run = _icd9_run(
        tmp_path,
        icd9._generation(start="2026-01-01"),  # pyright: ignore[reportPrivateUsage]
    )
    _ = run(capsys)
    run.r2.reads.clear()

    monkeypatch.setattr(processor, "RULES_VERSION", processor.RULES_VERSION + 1)
    _ = run(capsys)

    assert run.r2.reads != []


def test_refused_generation_stays_down_without_a_rebuild(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _icd9_run(
        tmp_path,
        icd9._generation(start="2026-01-01"),  # pyright: ignore[reportPrivateUsage]
    )

    first = run(capsys)
    run.r2.reads.clear()
    second = run(capsys)

    verdict = {
        "system": "icd9-pl",
        "decision": "rejected",
        "criterion": "history",
        "code": "retroactive_amendment_exceeds_bound",
    }
    assert first == second == (0, {"version": 1, "verdicts": [verdict]})
    message = "rejected icd9-pl history/retroactive_amendment_exceeds_bound"
    assert run.pushes == [("down", message)] * 2
    assert run.r2.reads == []


def test_unchanged_republication_reports_idle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _icd9_run(
        tmp_path,
        icd9._generation(closes=None),  # pyright: ignore[reportPrivateUsage]
    )

    result = run(capsys)

    verdict = {
        "system": "icd9-pl",
        "decision": "rejected",
        "criterion": "history",
        "code": "release_content_unchanged",
    }
    assert result == (0, {"version": 1, "verdicts": [verdict]})
    # A down push would hold the monitor red and hide the next real alert.
    assert run.pushes == [("up", "idle")]


@pytest.mark.parametrize("fault", ["missing_blob", "altered_projection"])
def test_fault_is_reported_and_retried_without_a_stored_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fault: str
) -> None:
    czs = icd9._generation()  # pyright: ignore[reportPrivateUsage]
    run = _icd9_run(tmp_path, czs)
    if fault == "missing_blob":
        del run.r2.blobs[hashlib.sha256(czs).hexdigest()]
    else:
        projection = next((run.public / "nfz/generations").iterdir())
        path = projection / "api/v1/provenance/nfz.json"
        path.chmod(0o644)
        _ = path.write_bytes(path.read_bytes().replace(b"ICD-9 PL", b"ICD-9 XX"))

    code, summary = run(capsys)

    failure = "fetch" if fault == "missing_blob" else "projection"
    assert (code, summary) == (1, {"version": 1, "failed": failure})
    assert run.pushes == [("down", f"failed: {failure}")]
    assert not run.state.exists()


def test_inputs_already_live_report_idle_without_any_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base, bundle = icd9._live(tmp_path)  # pyright: ignore[reportPrivateUsage]
    run = _Run(tmp_path, bundle)
    live_czs = (tmp_path / "blobs/sha256" / base.sources[1].sha256).read_bytes()
    _nfz(run.public, live_czs)

    result = run(capsys)

    assert result == (0, {"version": 1, "verdicts": []})
    assert run.pushes == [("up", "idle")]
    assert run.r2.reads == []


def test_new_rsk_product_is_judged_from_its_projection_entry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, bundle = icd10._live(tmp_path)  # pyright: ignore[reportPrivateUsage]
    product = icd10._CHANGED  # pyright: ignore[reportPrivateUsage]
    info = json.dumps(icd10._basic_info(product)).encode()  # pyright: ignore[reportPrivateUsage]
    product_sha256 = icd10._archive(tmp_path, product)  # pyright: ignore[reportPrivateUsage]
    policies = tmp_path / "policies"
    policies.mkdir()
    synthetic = icd10._policy()  # pyright: ignore[reportPrivateUsage]
    _ = (policies / "rsk-icd10-automatic.json").write_text(synthetic.model_dump_json())
    run = _Run(tmp_path, bundle, policies)
    entry = RskProvenanceEntry(
        hcd="00CD10",
        resource_name="ICD-10",
        resource_short_name="ICD-10",
        publisher_name=None,
        publisher_short_name=None,
        resource_type=None,
        resource_version="002",
        origin_url="https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/00000000-0000-4000-8000-000000000002",
        valid_from="2026-01-01T00:00:00Z",
        valid_to=None,
        file_uuid="00000000-0000-4000-8000-000000000002",
        file_name=None,
        extension="xml",
        type_label=None,
        size_bytes=len(product),
        file_created_at=None,
        source_archived_at=None,
        metadata_observation_sha256=icd10._archive(tmp_path, info),  # pyright: ignore[reportPrivateUsage]
        product_sha256=product_sha256,
        status="current",
        first_observed_at="2025-12-02T08:00:00Z",
        last_observed_at="2026-10-15T12:00:00Z",
        archive_status="privately_archived_verified",
        downloadable=False,
        download_url=None,
        notices=[],
    )
    projection = RskProvenanceProjection(
        schema_version=1,
        source="RSK",
        through_state_revision=7,
        projected_at="2026-10-15T12:00:00Z",
        included_revisions=[7],
        history_truncated=False,
        entries=[entry],
        redistribution=RedistributionManifest(schema_version=1, artifacts=[]),
        notices=[],
    )
    _publish(run.public, "RSK", projection.model_dump_json().encode())
    run.r2 = _R2(tmp_path)

    code, summary = run(capsys)

    release_id = f"icd10-rsk-002-{product_sha256[:12]}"
    verdict = {
        "system": "icd10-pl",
        "decision": "would_activate",
        "release_id": release_id,
    }
    assert (code, summary) == (0, {"version": 1, "verdicts": [verdict]})
    assert run.pushes == [("up", f"would_activate {release_id}")]
