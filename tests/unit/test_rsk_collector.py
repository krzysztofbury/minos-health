import hashlib
import json
import sqlite3
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, cast, final

import httpx
import pytest

import minos.ingest.watchers.rsk.collector as rsk_collector
from minos.ingest.watchers.archive import BlobStagingArea, FilesystemBlobArchive
from minos.ingest.watchers.config import (
    RSK_SOURCE_CONFIGURATION_CODECS,
    RskConfiguration,
    RskSettings,
)
from minos.ingest.watchers.control import (
    ClaimIdentity,
    CommittedRun,
    ControlStorageError,
    ControlStore,
    JsonValue,
    ObservationDraft,
    RecoveryReceipt,
    RetryPolicy,
    RunClaim,
    SourceConfiguration,
)
from minos.ingest.watchers.control.models import RunMode
from minos.ingest.watchers.models import HttpObservation
from minos.ingest.watchers.orchestrator import WatcherOrchestrator
from minos.ingest.watchers.protocols import (
    CollectedRun,
    CollectionContext,
    CollectorRegistry,
    RetryableWatcherError,
    SourceDriftWatcherError,
    WatcherRuntimeError,
)
from minos.ingest.watchers.recovery import PublishedRecoveryImage, RecoveryClass
from minos.ingest.watchers.rsk.capture import (
    _validate_complete_xml,  # pyright: ignore[reportPrivateUsage]
)
from minos.ingest.watchers.rsk.client import (
    create_rsk_client,
    parse_rsk_role_catalog,
)
from minos.ingest.watchers.rsk.collector import (
    RskMetadataRotationError,
    RskSourceCollector,
)
from minos.ingest.watchers.rsk.models import RskRoleUriModel

_NOW_MS = 1_800_000_000_000
_XML_UUID = "00000000-0000-4000-8000-000000000011"
_PDF_UUID = "00000000-0000-4000-8000-000000000012"
_ICD11_UUID = "00000000-0000-4000-8000-000000000021"
_EXTRA_UUID = "00000000-0000-4000-8000-000000000031"
_XML = b'<?xml version="1.0"?><synthetic><item>invented</item></synthetic>'


def _catalog_entry(hcd: str, ordinal: int) -> dict[str, object]:
    return {
        "ordinalNumber": ordinal,
        "hcd": hcd,
        "name": f"Synthetic {hcd}",
        "description": None,
        "version": "013",
        "organizationName": "Synthetic Publisher",
        "creationDate": "2026-08-05T08:00:00Z",
        "resourceType": "SLOWNIK",
    }


def _file(
    uuid: str,
    extension: str,
    size_bytes: int,
    *,
    archived: bool = False,
    education: bool = False,
) -> dict[str, object]:
    return {
        "uuid": uuid,
        "file": None,
        "fileName": f"synthetic.{extension}",
        "sizeBytes": size_bytes,
        "creationDate": "2026-08-05T08:00:00Z",
        "extension": extension,
        "extensionName": None,
        "description": None,
        "archived": archived,
        "version": None,
        "archivizationDate": None,
        "educationFile": education,
    }


def _metadata(hcd: str, files: list[dict[str, object]]) -> dict[str, object]:
    return {
        "hcd": hcd,
        "name": f"Synthetic {hcd}",
        "shortName": hcd,
        "description": None,
        "validDateFrom": "2020-01-01T00:00:00Z",
        "validDateTo": None,
        "version": "013",
        "publisher": None,
        "files": files,
        "specifyDownload": False,
        "getAsRest": True,
        "resourceType": "SLOWNIK",
        "language": "pl",
        "fhirOrRestApiVersion": None,
        "educationLinks": [],
    }


def _publisher(name: str) -> dict[str, object]:
    return {
        "uuid": "00000000-0000-4000-8000-000000000041",
        "name": name,
        "shortName": "SYN",
        "addressCountry": "PL",
        "addressCity": "Synthetic City",
        "addressStreet": "Synthetic Street",
        "addressBuilding": None,
        "addressFlat": None,
        "addressPostcode": None,
        "contactEmail": None,
        "contactPhone": None,
        "contactFax": None,
        "nip": None,
        "regon": None,
        "url": None,
        "contactPerson": None,
    }


@dataclass(slots=True)
class _RskSource:
    calls: list[httpx.Request] = field(default_factory=list)
    reverse_catalog: bool = False
    reverse_keys: bool = False
    rotate_refetch: bool = False
    persistent_rotation: bool = False
    duplicate_uuid: bool = False
    include_00cd10: bool = True
    include_99cd11: bool = True
    include_extra: bool = False
    add_file: bool = False
    publisher_name: str | None = None
    valid_from: str = "2020-01-01T00:00:00Z"
    catalog_body: bytes | None = None
    catalog_status: int = 200
    product_status: int = 200
    product_body: bytes = _XML
    product_media_type: str = "application/xml"
    basic_00_count: int = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.method == "GET"
        assert request.url.host == "rsk3.ezdrowie.gov.pl"
        assert "range" not in request.headers
        path = request.url.path
        if path == "/api/rsk/resource/role/UZYNLG":
            if self.catalog_status != 200:
                return httpx.Response(self.catalog_status)
            body = self.catalog_body or self.catalog_bytes()
            return _response(body, "application/json")
        if path == "/api/rsk/resource/basic-info/00CD10":
            self.basic_00_count += 1
            value = self.metadata_00()
            if self.persistent_rotation:
                value["description"] = f"rotation-{self.basic_00_count}"
            elif self.rotate_refetch and self.basic_00_count > 1:
                value["description"] = "rotated during transfer"
            return _response(_json_bytes(value, self.reverse_keys), "application/json")
        if path == "/api/rsk/resource/basic-info/99CD11":
            return _response(
                _json_bytes(self.metadata_11(), self.reverse_keys),
                "application/json",
            )
        if path == "/api/rsk/resource/basic-info/88NEW1":
            return _response(
                _json_bytes(self.metadata_extra(), self.reverse_keys),
                "application/json",
            )
        if path == f"/api/rsk/file/get/{_XML_UUID}":
            if self.product_status != 200:
                return httpx.Response(self.product_status)
            return _response(self.product_body, self.product_media_type)
        raise AssertionError(f"nonallowlisted RSK request: {path}")

    def catalog_bytes(self) -> bytes:
        entries = [_catalog_entry("00CD10", 1), _catalog_entry("99CD11", 2)]
        if not self.include_00cd10:
            entries = entries[1:]
        if not self.include_99cd11:
            entries = [item for item in entries if item["hcd"] != "99CD11"]
        if self.include_extra:
            entries.append(_catalog_entry("88NEW1", 3))
        if self.reverse_catalog:
            entries.reverse()
        if self.reverse_keys:
            entries = [dict(reversed(tuple(item.items()))) for item in entries]
        return json.dumps(entries, separators=(",", ":")).encode()

    def metadata_00(self) -> dict[str, object]:
        files = [
            _file(_XML_UUID, "xml", len(self.product_body)),
            _file(_PDF_UUID, "pdf", 17, archived=True),
        ]
        if self.add_file:
            files.append(_file("00000000-0000-4000-8000-000000000013", "json", 23))
        metadata = _metadata("00CD10", files)
        metadata["validDateFrom"] = self.valid_from
        if self.publisher_name is not None:
            metadata["publisher"] = _publisher(self.publisher_name)
        return metadata

    def metadata_11(self) -> dict[str, object]:
        uuid = _XML_UUID if self.duplicate_uuid else _ICD11_UUID
        return _metadata("99CD11", [_file(uuid, "xml", 31)])

    def metadata_extra(self) -> dict[str, object]:
        return _metadata("88NEW1", [_file(_EXTRA_UUID, "json", 29)])


def _json_bytes(value: dict[str, object], reverse_keys: bool) -> bytes:
    if reverse_keys:
        value = dict(reversed(tuple(value.items())))
    return json.dumps(value, separators=(",", ":")).encode()


def _response(body: bytes, media_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        stream=httpx.ByteStream(body),
        headers={"Content-Type": media_type, "Content-Length": str(len(body))},
    )


def _configuration(**overrides: JsonValue) -> dict[str, JsonValue]:
    values = cast(
        dict[str, JsonValue],
        RskConfiguration().model_dump(mode="json"),
    )
    values.update(overrides)
    return values


def _resign_cursor(cursor: dict[str, JsonValue]) -> None:
    state = {
        "schema_version": cursor["schema_version"],
        "catalog": cursor["catalog"],
        "resources": cursor["resources"],
        "product": cursor["product"],
    }
    canonical = json.dumps(
        state,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    cursor["state_sha256"] = hashlib.sha256(canonical).hexdigest()


def _claim(
    run_id: str,
    mode: RunMode = "scheduled",
    observed_at_ms: int = _NOW_MS,
) -> RunClaim:
    return RunClaim(
        ClaimIdentity("rsk", run_id, "a" * 64, 1, 1, observed_at_ms + 300_000),
        mode,
        observed_at_ms,
        observed_at_ms,
        1,
        RetryPolicy(2, 0.01, 0.01),
    )


def _factory(source: _RskSource) -> Callable[[RskSettings], httpx.Client]:
    def create(settings: RskSettings) -> httpx.Client:
        return create_rsk_client(
            settings,
            transport=httpx.MockTransport(source.handler),
        )

    return create


def _collect(
    tmp_path: Path,
    source: _RskSource,
    *,
    cursor: dict[str, JsonValue] | None = None,
    mode: RunMode = "scheduled",
    run_id: str = "run-collector-0001",
    configuration: dict[str, JsonValue] | None = None,
    now_ms: int | Callable[[], int] | None = None,
    observed_at_ms: int = _NOW_MS,
) -> CollectedRun:
    current_ms = observed_at_ms if now_ms is None else now_ms
    collector = RskSourceCollector(
        BlobStagingArea(tmp_path / "staging"),
        client_factory=_factory(source),
        now_ms=current_ms if callable(current_ms) else lambda: current_ms,
    )
    context = CollectionContext(
        "rsk",
        configuration or _configuration(),
        cursor or {},
        _claim(run_id, mode, observed_at_ms),
        FilesystemBlobArchive(tmp_path / "archive"),
    )
    with collector.collect(context) as collected:
        return collected


def test_first_collection_fetches_all_metadata_and_only_allowlisted_product(
    tmp_path: Path,
) -> None:
    source = _RskSource()

    collected = _collect(tmp_path, source)

    assert [item.source_identity for item in collected.observations] == [
        "UZYNLG",
        "00CD10",
        "99CD11",
        _XML_UUID,
        "00CD10",
    ]
    assert [request.url.path for request in source.calls] == [
        "/api/rsk/resource/role/UZYNLG",
        "/api/rsk/resource/basic-info/00CD10",
        "/api/rsk/resource/basic-info/99CD11",
        f"/api/rsk/file/get/{_XML_UUID}",
        "/api/rsk/resource/basic-info/00CD10",
    ]
    assert collected.changes[0].change_category == "rsk_baseline"
    assert collected.observations[-1].observation_kind == "rsk_basic_info_refetch"
    assert cast(dict[str, object], collected.cursor["product"])["sha256"] == (
        hashlib.sha256(_XML).hexdigest()
    )
    assert list((tmp_path / "staging").iterdir()) == []


def test_collection_archives_exact_response_bytes(tmp_path: Path) -> None:
    source = _RskSource(reverse_keys=True)
    catalog_bytes = source.catalog_bytes()

    collected = _collect(tmp_path, source)

    catalog = collected.observations[0]
    assert catalog.response_sha256 == hashlib.sha256(catalog_bytes).hexdigest()
    archived = (
        tmp_path / "archive" / "blobs" / "sha256" / catalog.response_sha256
    ).read_bytes()
    assert archived == catalog_bytes
    assert archived != json.dumps(json.loads(archived), sort_keys=True).encode()


@pytest.mark.parametrize("mode", ["scheduled", "catch_up"])
def test_daily_semantics_skip_unchanged_product(
    tmp_path: Path,
    mode: RunMode,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    source = _RskSource()
    later_ms = _NOW_MS + 86_400_000

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        mode=mode,
        run_id=f"run-collector-{mode}",
        observed_at_ms=later_ms,
    )

    assert all("/file/get/" not in request.url.path for request in source.calls)
    assert [item.observation_kind for item in second.observations] == [
        "rsk_catalog",
        "rsk_basic_info",
        "rsk_basic_info",
    ]
    assert second.cursor == first.cursor
    assert second.cursor["state_sha256"] == first.cursor["state_sha256"]
    assert second.changes == ()
    assert all(item.observed_at_ms == later_ms for item in second.observations)
    assert second.observations[0].response_sha256 == (
        first.observations[0].response_sha256
    )


def test_manual_run_forces_unchanged_product_download_at_later_clock(
    tmp_path: Path,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    source = _RskSource()
    later_ms = _NOW_MS + 86_400_000

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        mode="manual",
        run_id="run-collector-manual",
        observed_at_ms=later_ms,
    )

    assert sum("/file/get/" in request.url.path for request in source.calls) == 1
    assert second.cursor == first.cursor
    assert second.changes == ()
    assert all(item.observed_at_ms == later_ms for item in second.observations)


def test_weekly_verification_forces_unchanged_product_download(tmp_path: Path) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    source = _RskSource()

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        mode="weekly_verification",
        run_id="run-collector-weekly",
        observed_at_ms=_NOW_MS + 7 * 86_400_000,
    )

    assert sum("/file/get/" in request.url.path for request in source.calls) == 1
    assert second.cursor == first.cursor
    assert second.changes == ()


def test_weekly_verification_detects_product_digest_change_with_same_metadata(
    tmp_path: Path,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    changed_xml = _XML.replace(b"invented", b"modified")
    assert len(changed_xml) == len(_XML)
    source = _RskSource(product_body=changed_xml)

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        mode="weekly_verification",
        run_id="run-collector-weekly-change",
    )

    change = second.changes[0]
    assert change.summary["previous_product_sha256"] == hashlib.sha256(_XML).hexdigest()
    assert (
        change.summary["current_product_sha256"]
        == hashlib.sha256(changed_xml).hexdigest()
    )
    assert (
        change.summary["current_state_sha256"]
        != change.summary["previous_state_sha256"]
    )


def test_absent_product_cursor_fails_closed_before_request(tmp_path: Path) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    cursor = dict(first.cursor)
    cursor["product"] = None
    source = _RskSource()

    with pytest.raises(WatcherRuntimeError, match="stored RSK cursor is invalid"):
        _ = _collect(
            tmp_path,
            source,
            cursor=cursor,
            run_id="run-collector-missing-product",
        )

    assert source.calls == []


@pytest.mark.parametrize(
    "mutation",
    ["state-digest", "catalog-membership", "product-file-set", "product-identity"],
)
def test_corrupt_persisted_cursor_fails_closed_before_request(
    tmp_path: Path,
    mutation: str,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    cursor = deepcopy(first.cursor)
    if mutation == "state-digest":
        cursor["state_sha256"] = "0" * 64
    elif mutation == "catalog-membership":
        catalog = cast(dict[str, JsonValue], cursor["catalog"])
        catalog["hcds"] = ["00CD10"]
        _resign_cursor(cursor)
    elif mutation == "product-file-set":
        product = cast(dict[str, JsonValue], cursor["product"])
        product["file_set_sha256"] = "f" * 64
        _resign_cursor(cursor)
    elif mutation == "product-identity":
        product = cast(dict[str, JsonValue], cursor["product"])
        product["uuid"] = "00000000-0000-4000-8000-000000000099"
        _resign_cursor(cursor)
    else:
        raise AssertionError("unsupported cursor mutation")
    source = _RskSource()

    with pytest.raises(WatcherRuntimeError, match="stored RSK cursor is invalid"):
        _ = _collect(tmp_path, source, cursor=cursor, run_id=f"run-{mutation}")

    assert source.calls == []


def test_same_version_file_set_change_downloads_only_allowlisted_xml(
    tmp_path: Path,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    source = _RskSource(add_file=True)

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        run_id="run-collector-0002",
    )

    requested_products = [
        request.url.path for request in source.calls if "/file/get/" in request.url.path
    ]
    assert requested_products == [f"/api/rsk/file/get/{_XML_UUID}"]
    assert second.changes[0].summary["file_sets_changed"] == ["00CD10"]
    resources = cast(dict[str, object], second.cursor["resources"])
    assert cast(dict[str, object], resources["00CD10"])["version"] == "013"


@pytest.mark.parametrize(
    ("source", "expected_added", "expected_removed"),
    [
        (_RskSource(include_extra=True), ["88NEW1"], []),
        (_RskSource(include_99cd11=False), [], ["99CD11"]),
    ],
    ids=["resource-added", "resource-removed"],
)
def test_catalog_resource_membership_changes_are_reported(
    tmp_path: Path,
    source: _RskSource,
    expected_added: list[str],
    expected_removed: list[str],
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        run_id="run-collector-membership-change",
        observed_at_ms=_NOW_MS + 86_400_000,
    )

    change = second.changes[0]
    assert change.summary["catalog_changed"] is True
    assert change.summary["added_hcds"] == expected_added
    assert change.summary["removed_hcds"] == expected_removed
    assert all("/file/get/" not in request.url.path for request in source.calls)


@pytest.mark.parametrize(
    "source",
    [
        _RskSource(publisher_name="Changed Publisher"),
        _RskSource(valid_from="2021-01-01T00:00:00Z"),
    ],
    ids=["publisher-only", "validity-only"],
)
def test_non_file_metadata_changes_are_reported_without_product_download(
    tmp_path: Path,
    source: _RskSource,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        run_id="run-collector-metadata-change",
        observed_at_ms=_NOW_MS + 86_400_000,
    )

    change = second.changes[0]
    assert change.summary["metadata_changed"] == ["00CD10"]
    assert change.summary["file_sets_changed"] == []
    assert all("/file/get/" not in request.url.path for request in source.calls)


def test_semantic_cursor_is_stable_across_json_key_and_result_order(
    tmp_path: Path,
) -> None:
    first = _collect(tmp_path, _RskSource(), run_id="run-collector-0001")
    source = _RskSource(reverse_catalog=True, reverse_keys=True)

    second = _collect(
        tmp_path,
        source,
        cursor=first.cursor,
        run_id="run-collector-0002",
    )

    assert second.cursor == first.cursor
    assert second.changes == ()
    assert [item.source_identity for item in second.observations] == [
        "UZYNLG",
        "00CD10",
        "99CD11",
    ]
    assert second.observations[0].response_sha256 != (
        first.observations[0].response_sha256
    )


def test_global_duplicate_uuid_and_missing_00cd10_fail_before_download(
    tmp_path: Path,
) -> None:
    for index, source in enumerate(
        (_RskSource(duplicate_uuid=True), _RskSource(include_00cd10=False))
    ):
        with pytest.raises(SourceDriftWatcherError):
            _ = _collect(tmp_path / str(index), source)
        assert all("/file/get/" not in request.url.path for request in source.calls)


@pytest.mark.parametrize(
    "source",
    [
        _RskSource(catalog_body=b"{}"),
        _RskSource(product_body=b'<?xml version="1.0"?><synthetic>'),
        _RskSource(
            product_body=b"<html>failure</html>", product_media_type="text/html"
        ),
        _RskSource(product_media_type="application/pdf"),
    ],
)
def test_malformed_metadata_and_product_corruption_are_source_drift(
    tmp_path: Path,
    source: _RskSource,
) -> None:
    with pytest.raises(SourceDriftWatcherError):
        _ = _collect(tmp_path, source)
    assert list((tmp_path / "staging").iterdir()) == []


def test_oversized_metadata_is_rejected_before_archive(tmp_path: Path) -> None:
    source = _RskSource(catalog_body=b"[" + (b" " * 128) + b"]")

    with pytest.raises(SourceDriftWatcherError):
        _ = _collect(
            tmp_path,
            source,
            configuration=_configuration(metadata_max_bytes=64),
        )

    digest_directory = tmp_path / "archive" / "blobs" / "sha256"
    assert not digest_directory.exists() or list(digest_directory.iterdir()) == []


def test_aggregate_budget_includes_product_and_metadata_refetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _RskSource()
    metadata_00 = _json_bytes(source.metadata_00(), False)
    initial_bytes = (
        len(source.catalog_bytes())
        + len(metadata_00)
        + len(_json_bytes(source.metadata_11(), False))
    )
    monkeypatch.setattr(
        rsk_collector,
        "MAX_CAPTURE_BYTES",
        initial_bytes + len(_XML) + len(metadata_00) - 1,
    )

    with pytest.raises(SourceDriftWatcherError, match="collection byte count"):
        _ = _collect(tmp_path, source)

    assert source.basic_00_count == 2
    assert list((tmp_path / "staging").iterdir()) == []


def test_transient_status_is_retryable(tmp_path: Path) -> None:
    transient = _RskSource(catalog_status=503)
    with pytest.raises(RetryableWatcherError):
        _ = _collect(
            tmp_path / "transient",
            transient,
            configuration=_configuration(retry_max_attempts=1),
        )
    assert len(transient.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_and_missing_statuses_are_nonretryable_source_drift(
    tmp_path: Path,
    status: int,
) -> None:
    permanent = _RskSource(catalog_status=status)
    with pytest.raises(SourceDriftWatcherError):
        _ = _collect(tmp_path, permanent)
    assert len(permanent.calls) == 1


def test_expired_claim_stops_before_request(tmp_path: Path) -> None:
    source = _RskSource()
    with pytest.raises(RetryableWatcherError, match="transient"):
        _ = _collect(tmp_path, source, now_ms=_NOW_MS + 300_000)
    assert source.calls == []


def test_claim_deadline_is_checked_after_metadata_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [_NOW_MS]
    original = parse_rsk_role_catalog

    def parse_and_expire(observation: HttpObservation) -> list[RskRoleUriModel]:
        parsed = original(observation)
        clock[0] = _NOW_MS + 300_000
        return parsed

    monkeypatch.setattr(rsk_collector, "parse_rsk_role_catalog", parse_and_expire)
    source = _RskSource()

    with pytest.raises(RetryableWatcherError, match="transient"):
        _ = _collect(tmp_path, source, now_ms=lambda: clock[0])

    assert [request.url.path for request in source.calls] == [
        "/api/rsk/resource/role/UZYNLG"
    ]


def test_claim_deadline_is_checked_after_xml_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [_NOW_MS]
    original = cast(Callable[[BinaryIO, int], None], _validate_complete_xml)

    def validate_and_expire(source: BinaryIO, size_bytes: int) -> None:
        original(source, size_bytes)
        clock[0] = _NOW_MS + 300_000

    monkeypatch.setattr(rsk_collector, "_validate_complete_xml", validate_and_expire)
    source = _RskSource()

    with pytest.raises(RetryableWatcherError, match="transient"):
        _ = _collect(tmp_path, source, now_ms=lambda: clock[0])

    assert source.calls[-1].url.path == f"/api/rsk/file/get/{_XML_UUID}"
    assert list((tmp_path / "staging").iterdir()) == []


def test_claim_deadline_is_checked_after_publication_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [_NOW_MS]
    original = cast(
        Callable[..., tuple[ObservationDraft, ...]],
        rsk_collector._observations,  # pyright: ignore[reportPrivateUsage]
    )

    def prepare_and_expire(
        *args: object, **kwargs: object
    ) -> tuple[ObservationDraft, ...]:
        observations = original(*args, **kwargs)
        clock[0] = _NOW_MS + 300_000
        return observations

    monkeypatch.setattr(rsk_collector, "_observations", prepare_and_expire)
    source = _RskSource()

    with pytest.raises(RetryableWatcherError, match="transient"):
        _ = _collect(tmp_path, source, now_ms=lambda: clock[0])

    assert source.calls[-1].url.path == "/api/rsk/resource/basic-info/00CD10"


@final
class _UnusedRecoveryPublisher:
    def publish_run(self, store: ControlStore, run: CommittedRun) -> RecoveryReceipt:
        _ = store, run
        raise AssertionError("failed collection must not publish recovery")

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        _ = store, recovery_class
        raise AssertionError("not used")

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        _ = store, recovery_class
        return None


@final
class _RecoveryPublisher:
    def publish_run(self, store: ControlStore, run: CommittedRun) -> RecoveryReceipt:
        _ = store
        digest = "b" * 64
        return RecoveryReceipt(
            run.run_id,
            run.committed_state_revision,
            run.committed_state_revision,
            digest,
            4096,
            (
                "control-recovery/run/"
                f"{run.committed_state_revision:020d}/{digest}.sqlite3"
            ),
            "test-version-1",
            digest,
            4096,
        )

    def publish_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage:
        _ = store, recovery_class
        raise AssertionError("not used")

    def ensure_periodic(
        self,
        store: ControlStore,
        recovery_class: RecoveryClass,
    ) -> PublishedRecoveryImage | None:
        _ = store, recovery_class
        return None


@final
class _Heartbeat:
    def start(self, source_id: str) -> None:
        _ = source_id

    def success(self, source_id: str) -> None:
        _ = source_id

    def failure(self, source_id: str) -> None:
        _ = source_id


def test_one_off_metadata_rotation_restarts_complete_collection_and_succeeds(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    source = _RskSource(rotate_refetch=True)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            RskSourceCollector(
                BlobStagingArea(tmp_path / "staging"),
                client_factory=_factory(source),
            ),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(tmp_path / "archive"),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(),
            heartbeat=_Heartbeat(),
            sleep=lambda _: None,
        )

        completed = orchestrator.run_claim(
            store.claim_manual("rsk"),
            RetryPolicy(2, 0.01, 0.01),
        )

        assert completed.source_id == "rsk"
        assert store.load_source_state("rsk").cursor["schema_version"] == 1
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(5,)]
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(4,)]
        assert _query(database, "SELECT status FROM watcher_runs") == [("successful",)]
        assert _query(database, "SELECT COUNT(*) FROM incidents") == [(0,)]
        assert sum("/file/get/" in request.url.path for request in source.calls) == 2
        assert (
            sum(
                request.url.path == "/api/rsk/resource/role/UZYNLG"
                for request in source.calls
            )
            == 2
        )
        assert list((tmp_path / "staging").iterdir()) == []
    finally:
        store.close()


def test_persistent_metadata_rotation_exhausts_bounded_retry_without_commit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    store = ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    source = _RskSource(persistent_rotation=True)
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            RskSourceCollector(
                BlobStagingArea(tmp_path / "staging"),
                client_factory=_factory(source),
            ),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(tmp_path / "archive"),
            collectors=registry,
            recovery_publisher=_UnusedRecoveryPublisher(),
            heartbeat=_Heartbeat(),
            sleep=lambda _: None,
        )

        with pytest.raises(RskMetadataRotationError, match="changed during"):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(2, 0.01, 0.01),
            )

        assert store.load_source_state("rsk").cursor == {}
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(0,)]
        assert _query(database, "SELECT status FROM watcher_runs") == [("failed",)]
        assert _query(database, "SELECT incident_category FROM incidents") == [
            ("collection_failure",)
        ]
        assert sum("/file/get/" in request.url.path for request in source.calls) == 2
        assert list((tmp_path / "staging").iterdir()) == []
    finally:
        store.close()


def test_sqlite_commit_failure_preserves_cursor_and_recovers_archive_orphans(
    tmp_path: Path,
) -> None:
    database = tmp_path / "control.sqlite3"
    archive_path = tmp_path / "archive"
    staging_path = tmp_path / "staging"
    store = ControlStore.open(
        database,
        source_configuration_codecs=RSK_SOURCE_CONFIGURATION_CODECS,
    )
    source = _RskSource()
    try:
        _ = store.configure_source(SourceConfiguration("rsk", "rsk", {}, True))
        registry = CollectorRegistry()
        registry.register(
            "rsk",
            RskSourceCollector(
                BlobStagingArea(staging_path),
                client_factory=_factory(source),
            ),
        )
        orchestrator = WatcherOrchestrator(
            store=store,
            archive=FilesystemBlobArchive(archive_path),
            collectors=registry,
            recovery_publisher=_RecoveryPublisher(),
            heartbeat=_Heartbeat(),
        )
        with sqlite3.connect(database) as connection:
            _ = connection.execute(
                """
                CREATE TRIGGER fail_rsk_observation_insert
                BEFORE INSERT ON observations
                BEGIN
                    SELECT RAISE(ABORT, 'injected RSK observation failure');
                END
                """
            )

        with pytest.raises(ControlStorageError, match="failed and was rolled back"):
            _ = orchestrator.run_claim(
                store.claim_manual("rsk"),
                RetryPolicy(1, 0.01, 0.01),
            )

        assert store.load_source_state("rsk").cursor == {}
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(0,)]
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(0,)]
        assert _query(database, "SELECT incident_category FROM incidents") == [
            ("control_commit_failure",)
        ]
        archived = list((archive_path / "blobs" / "sha256").iterdir())
        assert len(archived) == 4
        assert list(staging_path.iterdir()) == []

        with sqlite3.connect(database) as connection:
            _ = connection.execute("DROP TRIGGER fail_rsk_observation_insert")
        completed = orchestrator.run_claim(
            store.claim_manual("rsk"),
            RetryPolicy(1, 0.01, 0.01),
        )

        assert completed.source_id == "rsk"
        assert store.load_source_state("rsk").cursor["schema_version"] == 1
        assert _query(database, "SELECT COUNT(*) FROM observations") == [(5,)]
        assert _query(database, "SELECT COUNT(*) FROM blobs") == [(4,)]
        assert len(list((archive_path / "blobs" / "sha256").iterdir())) == 4
        assert list(staging_path.iterdir()) == []
    finally:
        store.close()


def _query(database: Path, sql: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()
