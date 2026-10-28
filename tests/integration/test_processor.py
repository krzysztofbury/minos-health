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
from minos.ingest.archive_build import ArchiveObject, R2BlobReader, source_files
from minos.ingest.processor import BlobReader
from minos.ingest.reference_manifest import MAX_ICD11_SOURCE_BYTES, DatasetManifest
from minos.ingest.watchers.nfz.public_models import Czs, Eli, Evidence, NfzProjection
from minos.ingest.watchers.publication.filesystem import ImmutableGenerationPublisher
from minos.ingest.watchers.rsk.public_models import (
    RedistributionManifest,
    RskProvenanceEntry,
    RskProvenanceProjection,
    public_entry_sort_key,
)
from minos.ingest.watchers.uptime_kuma import UptimeKumaClient, UptimeKumaTargets
from minos.store.artifact import ARTIFACT_MANIFEST_NAME, ArtifactManifest
from tests.integration import test_icd9_candidate as icd9
from tests.integration import test_icd10_candidate as icd10
from tests.integration.test_icd11_reference import icd11_bundle
from tests.integration.test_jgp_api import lineage_bundle
from tests.unit.test_sources_page import (
    _baw_projection,  # pyright: ignore[reportPrivateUsage]
)

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


def _nfz(public: Path, czs: bytes, *extra: Eli) -> None:
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
        through_state_revision=7, observed_at=_OBSERVED, entries=(entry, *extra)
    )
    _publish(public / "nfz", "NFZ", projection.model_dump_json().encode())


@final
class _Run:
    def __init__(
        self,
        root: Path,
        bundle: Path,
        policies: Path = _POLICIES,
        acceptance: Path | None = None,
    ) -> None:
        self.acceptance = acceptance
        self.accepted = root / "accepted"
        self.accepted.mkdir()
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
                "acceptance_directory": self.acceptance,
                "accepted": self.accepted if self.acceptance else None,
            },
            reader_factory=reader,
            kuma_factory=kuma,
        )
        output = capsys.readouterr().out
        assert str(self.root) not in output
        assert list(self.work.iterdir()) == []
        return code, cast(object, json.loads(output))


def _icd9_run(root: Path, czs: bytes, acceptance: Path | None = None) -> _Run:
    _, bundle = icd9._live(root)  # pyright: ignore[reportPrivateUsage]
    _ = icd9._archive(root, czs)  # pyright: ignore[reportPrivateUsage]
    run = _Run(root, bundle, acceptance=acceptance)
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


@pytest.mark.parametrize(
    "findings", [(), ("amendment:DU/2026/1500",)], ids=["reviewed", "amended"]
)
def test_an_amended_legal_dictionary_holds_the_monitor_down(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], findings: tuple[str, ...]
) -> None:
    base, bundle = icd9._live(tmp_path)  # pyright: ignore[reportPrivateUsage]
    run = _Run(tmp_path, bundle)
    live_czs = (tmp_path / "blobs/sha256" / base.sources[1].sha256).read_bytes()
    acts = "https://api.sejm.gov.pl/eli/acts/"
    eli = Eli(
        channel="eli",
        title="Kody trybu wypisu ze szpitala",
        source_url=acts + "DU/2025/1588",
        sha256="e" * 64,
        observed_at=_OBSERVED,
        dictionary="discharge_modes",
        eli="DU/2025/1588",
        base_eli="DU/2019/1207",
        in_force="IN_FORCE",
        findings=findings,
        base_evidence=Evidence(
            source_url=acts + "DU/2019/1207", sha256="f" * 64, observed_at=_OBSERVED
        ),
    )
    _nfz(run.public, live_czs, eli)

    code, summary = run(capsys)

    expected: dict[str, object] = {"version": 1, "verdicts": []}
    if findings:
        expected["manual_eli"] = ["discharge_modes"]
    assert (code, summary) == (0, expected)
    assert run.pushes == [
        ("down", "manual eli discharge_modes") if findings else ("up", "idle")
    ]


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


def _acceptance(root: Path) -> Path:
    """One search gold case and one real grouping case, as shipped in the image."""
    directory = root / "acceptance"
    directory.mkdir()
    gold = {
        "cases": [
            {
                "id": "moczu",
                "system": "icd9-pl",
                "query": "badanie moczu",
                "codes": ["A01"],
            }
        ]
    }
    _ = (directory / "search-gold-v1.json").write_text(json.dumps(gold))
    shipped = cast(
        dict[str, list[dict[str, object]]],
        json.loads(
            (Path(__file__).parents[1] / "golden/grouping-cases.json").read_text()
        ),
    )
    real = [case for case in shipped["cases"] if case["bundle"] == "real"][:1]
    _ = (directory / "grouping-cases.json").write_text(json.dumps({"cases": real}))
    return directory


def test_accepted_bundle_is_handed_over_with_its_request(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _icd9_run(
        tmp_path,
        icd9._generation(),  # pyright: ignore[reportPrivateUsage]
        _acceptance(tmp_path),
    )

    code, summary = run(capsys)

    assert code == 0
    [verdict] = cast(
        list[dict[str, object]], cast(dict[str, object], summary)["verdicts"]
    )
    assert verdict["decision"] == "would_activate"
    request = cast(
        dict[str, str], json.loads((run.accepted / "request.json").read_text())
    )
    bundle = run.accepted / request["database_sha256"]
    assert (request["system"], request["release_id"]) == (
        "icd9-pl",
        verdict["release_id"],
    )
    assert sorted(path.name for path in bundle.iterdir()) == [
        "manifest.json",
        "minos.db",
        "review.txt",
    ]
    assert (
        hashlib.sha256((bundle / "minos.db").read_bytes()).hexdigest()
        == (request["database_sha256"])
    )


def test_a_lost_search_gold_case_refuses_the_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    renamed = icd9._generation(closes=None).replace(  # pyright: ignore[reportPrivateUsage]
        b'nazwa="  Badanie moczu  "', b'nazwa="Synthetic renamed"'
    )
    run = _icd9_run(tmp_path, renamed, _acceptance(tmp_path))

    code, summary = run(capsys)

    assert code == 0
    assert cast(dict[str, object], summary)["verdicts"] == [
        {
            "system": "icd9-pl",
            "decision": "rejected",
            "criterion": "regression",
            "code": "search_gold_case_lost",
        }
    ]
    assert list(run.accepted.iterdir()) == []


def test_an_accepted_bundle_without_an_accepting_verdict_is_dropped(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _icd9_run(
        tmp_path,
        icd9._generation(start="2026-01-01"),  # pyright: ignore[reportPrivateUsage]
        _acceptance(tmp_path),
    )
    stale = run.accepted / ("c" * 64)
    stale.mkdir()
    request = {"version": 1, "database_sha256": "c" * 64}
    _ = (run.accepted / "request.json").write_text(json.dumps(request))

    code, _ = run(capsys)

    assert code == 0
    assert list(run.accepted.iterdir()) == []


def _jgp_run(
    root: Path, workbooks: dict[int, str], *, repealed: int | None = None
) -> _Run:
    """The 46/2026 lineage on BAW beside a live bundle with two of its catalogs.

    Every document gets its act ZIPX and a 1a workbook; ``workbooks`` names a
    digest per document, and the token ``live:<n>`` the nth live catalog's.
    """
    bundle = lineage_bundle(root)
    manifest = ArtifactManifest.model_validate_json(
        (bundle / ARTIFACT_MANIFEST_NAME).read_bytes()
    )
    live = [item.workbook.sha256 for item in manifest.dataset.catalogs]
    run = _Run(root, bundle)
    projection = _baw_projection()
    for document in cast(list[dict[str, object]], projection["documents"]):
        identity = cast(int, document["document_id"])
        digest = workbooks.get(identity, "e" * 64)
        if digest.startswith("live:"):
            digest = live[int(digest.removeprefix("live:"))]
        document["files"] = [
            {"file_id": identity * 10, "name": "Akt_prawny.zipx",
             "size_bytes": 1, "sha256": "c" * 64},
            {"file_id": identity * 10 + 1, "name": "Załącznik 1a.xlsx",
             "size_bytes": 1, "sha256": digest},
        ]  # fmt: skip
        if identity == repealed:
            document["legal_status"] = "Uchylony"
    _publish(run.public / "baw", "BAW", json.dumps(projection).encode())
    return run


# Types 4, 5 and 6 (43944, 43945, 44041, 44043, 44044) carry workbooks the
# live bundle lacks in every case; only an original act or a consolidated
# text in force names a catalog.
_SERVED = {43868: "live:0", 43943: "live:1"}


@pytest.mark.parametrize(
    "workbooks,repealed,manual",
    [
        ({**_SERVED, 44042: "d" * 64}, None, [44042]),
        ({**_SERVED, 44042: "d" * 64}, 44042, []),
        ({**_SERVED, 44042: "live:1"}, None, []),
        ({43868: "d" * 64, 43943: "live:1", 44042: "f" * 64}, None, [44042, 43868]),
    ],
    ids=["new_consolidation", "repealed", "served", "two_newest_first"],
)
def test_a_jgp_workbook_the_live_bundle_lacks_holds_the_monitor_down(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    workbooks: dict[int, str],
    repealed: int | None,
    manual: list[int],
) -> None:
    run = _jgp_run(tmp_path, workbooks, repealed=repealed)

    code, summary = run(capsys)

    expected: dict[str, object] = {"version": 1, "verdicts": []}
    if manual:
        expected["manual_jgp"] = manual
    assert (code, summary) == (0, expected)
    message = "; ".join(f"manual jgp {document}" for document in manual)
    assert run.pushes == [("down", message) if manual else ("up", "idle")]
    assert run.r2.reads == []


def test_a_baw_projection_without_lineage_fails_instead_of_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run = _Run(tmp_path, lineage_bundle(tmp_path))
    projection = _baw_projection()
    projection["schema_version"] = 1
    for document in cast(list[dict[str, object]], projection["documents"]):
        del document["lineage"]
    _publish(run.public / "baw", "BAW", json.dumps(projection).encode())

    code, summary = run(capsys)

    assert (code, summary) == (1, {"version": 1, "failed": "projection"})
    assert run.pushes == [("down", "failed: projection")]


def _icd11_entry(name: str, uuid: str, size: int, extension: str) -> RskProvenanceEntry:
    return RskProvenanceEntry(
        hcd="99ICD1",
        resource_name="ICD-11 PL",
        resource_short_name="ICD-11 PL",
        publisher_name=None,
        publisher_short_name=None,
        resource_type=None,
        resource_version="002",
        origin_url=f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{uuid}",
        valid_from="2026-01-01T00:00:00Z",
        valid_to=None,
        file_uuid=uuid,
        file_name=name,
        extension=extension,
        type_label=None,
        size_bytes=size,
        file_created_at=None,
        source_archived_at=None,
        metadata_observation_sha256="d" * 64,
        product_sha256=None,
        status="current",
        first_observed_at="2026-09-24T08:00:00Z",
        last_observed_at="2026-10-22T08:00:00Z",
        archive_status="metadata_only",
        downloadable=False,
        download_url=None,
        notices=[],
    )


@pytest.mark.parametrize(
    "change,manual",
    [("none", None), ("xml_size", "002"), ("new_map", "002")],
)
def test_icd11_files_no_live_release_pins_hold_the_monitor_down(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], change: str, manual: str | None
) -> None:
    bundle = icd11_bundle(tmp_path)
    dataset = ArtifactManifest.model_validate_json(
        (bundle / ARTIFACT_MANIFEST_NAME).read_bytes()
    ).dataset
    xml = next(source for source in dataset.sources if source.system == "icd11-pl")
    assert dataset.icd11_maps is not None
    entries = [
        _icd11_entry(
            "icd11_2026-01_pl_in",
            xml.source_file_id,
            xml.size_bytes + (1 if change == "xml_size" else 0),
            "xml",
        ),
        # The resource's PDFs are not read by the bundle.
        _icd11_entry("Przewodnik", "00000000-0000-4000-8000-0000000000aa", 9, "pdf"),
    ]
    for item in dataset.icd11_maps.files:
        name, uuid = (
            item.file_name.removesuffix(".xlsx"),
            item.source_url.rsplit("/", 1)[1],
        )
        if change == "new_map" and item.kind == "11to10_one":
            name, uuid = (
                "11To10MapdoJednejKategorii_2027-01",
                "00000000-0000-4000-8000-0000000000bb",
            )
        entries.append(_icd11_entry(name, uuid, item.size_bytes, "xlsx"))
    run = _Run(tmp_path, bundle)
    projection = RskProvenanceProjection(
        schema_version=1,
        source="RSK",
        through_state_revision=7,
        projected_at="2026-10-22T08:00:00Z",
        included_revisions=[7],
        history_truncated=False,
        entries=sorted(entries, key=public_entry_sort_key),
        redistribution=RedistributionManifest(schema_version=1, artifacts=[]),
        notices=[],
    )
    _publish(run.public, "RSK", projection.model_dump_json().encode())

    code, summary = run(capsys)

    expected: dict[str, object] = {"version": 1, "verdicts": []}
    if manual is not None:
        expected["manual_icd11"] = manual
    assert (code, summary) == (0, expected)
    assert run.pushes == [
        ("down", f"manual icd11 {manual}") if manual else ("up", "idle")
    ]


def test_every_reviewed_source_size_can_be_fetched_as_a_live_input() -> None:
    # The 117 MB ICD-11 PL source once failed every run with failed: fetch.
    manifest = DatasetManifest.model_validate_json(
        (
            Path(__file__).parents[2]
            / "manifests/sources/reference-conditions-public-validation.json"
        ).read_text()
    )
    objects = [
        ArchiveObject(sha256=blob.sha256, size_bytes=blob.size_bytes)
        for blob in source_files(manifest)
    ]
    assert max(item.size_bytes for item in objects) > 64 * 1024 * 1024
    assert ArchiveObject(sha256="a" * 64, size_bytes=MAX_ICD11_SOURCE_BYTES)
