import hashlib
import json
import os
import stat
from contextlib import AbstractContextManager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from inspect import signature
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, cast

import httpx
import pytest
from pydantic import ValidationError

from minos.ingest.watchers.models import HttpObservation
from minos.ingest.watchers.rsk import capture as capture_module
from minos.ingest.watchers.rsk.capture import (
    PUBLIC_INDEX_NAME,
    REVIEWED_CAPTURE_ID,
    REVIEWED_PUBLIC_INDEX_SHA256,
    CaptureRecord,
    RskCaptureError,
    RskCaptureRotationError,
    attest_reviewed_rsk_evidence,
    build_public_checksum_index,
    capture_private_rsk,
    fixed_rsk_settings,
    public_checksum_index_bytes,
    validate_private_capture,
    validate_public_checksum_index,
)
from minos.ingest.watchers.rsk.capture_models import (
    MAX_CAPTURE_RECORDS,
    MAX_METADATA_BYTES,
    MAX_PRODUCT_BYTES,
    PrivateCaptureManifest,
    PublicChecksumIndex,
)
from minos.ingest.watchers.rsk.client import (
    RskProtocolError,
    RskSourceDriftError,
    parse_rsk_catalog_basic_info,
)
from minos.ingest.watchers.rsk.models import RskCatalogMetadata
from scripts import capture_rsk_fixtures as capture_script

_REPOSITORY = Path(__file__).parents[2]
_FIXTURES = Path(__file__).parents[1] / "fixtures" / "rsk"
_LIVE_PUBLIC_INDEX = (
    _REPOSITORY / "docs" / "validation" / "rsk-live-capture-checksums.json"
)
_CAPTURE_TIME = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
_XML_UUID = "00000000-0000-4000-8000-000000000011"


@pytest.fixture
def capture_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir(mode=0o700)
    root = repository / "data" / "private-fixtures" / "rsk"
    monkeypatch.setattr(capture_module, "REPOSITORY_ROOT", repository)
    monkeypatch.setattr(capture_module, "CAPTURE_ROOT", root)
    return root


def _fixture(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


def _response(body: bytes, media_type: str) -> httpx.Response:
    return httpx.Response(
        200,
        stream=httpx.ByteStream(body),
        headers={"Content-Type": media_type, "Content-Length": str(len(body))},
    )


def _capture_transport(
    requests: list[str], *, rotate: bool = False, set_cookie: bool = False
) -> httpx.MockTransport:
    basic_00_requests = 0

    def respond(body: bytes, media_type: str) -> httpx.Response:
        response = _response(body, media_type)
        if set_cookie:
            response.headers["Set-Cookie"] = "synthetic=never-send"
        return response

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal basic_00_requests
        requests.append(request.url.path)
        assert request.method == "GET"
        assert request.url.host == "rsk3.ezdrowie.gov.pl"
        assert "authorization" not in request.headers
        assert "proxy-authorization" not in request.headers
        assert "cookie" not in request.headers
        assert "range" not in request.headers
        path = request.url.path
        if path == "/api/rsk/resource/role/UZYNLG":
            return respond(_fixture("role-catalog.json"), "application/json")
        if path == "/api/rsk/resource/basic-info/00CD10":
            basic_00_requests += 1
            body = _fixture("basic-info-00CD10.json")
            if rotate and basic_00_requests == 2:
                parsed = cast(dict[str, object], json.loads(body))
                parsed["version"] = "SYN-ROTATED"
                body = json.dumps(parsed, separators=(",", ":")).encode()
            return respond(body, "application/json")
        if path == "/api/rsk/resource/basic-info/99ZZ99":
            return respond(_fixture("basic-info-99ZZ99.json"), "application/json")
        if path == f"/api/rsk/file/get/{_XML_UUID}":
            return respond(_fixture("product-00CD10.xml"), "application/xml")
        raise AssertionError("capture attempted a nonallowlisted request")

    return httpx.MockTransport(handler)


def _capture(capture_base: Path, requests: list[str] | None = None) -> Path:
    requested = [] if requests is None else requests
    result = capture_private_rsk(
        transport=_capture_transport(requested), captured_at=_CAPTURE_TIME
    )
    assert result.path.parent == capture_base
    return result.path


def _read_manifest(capture_root: Path) -> dict[str, object]:
    return cast(
        dict[str, object],
        json.loads((capture_root / "private-manifest.json").read_bytes()),
    )


def _write_manifest(capture_root: Path, manifest: dict[str, object]) -> None:
    path = capture_root / "private-manifest.json"
    _ = path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)


def _private_records() -> list[CaptureRecord]:
    basic_digest = "b" * 64
    return [
        CaptureRecord(
            kind="role_catalog",
            hcd=None,
            source_url="https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG",
            sha256="a" * 64,
            size_bytes=10,
            status_code=200,
            media_type="application/json",
            headers={"content-type": "application/json"},
        ),
        CaptureRecord(
            kind="basic_info",
            hcd="00CD10",
            source_url="https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/00CD10",
            sha256=basic_digest,
            size_bytes=20,
            status_code=200,
            media_type="application/json",
            headers={"content-type": "application/json"},
        ),
        CaptureRecord(
            kind="product_xml",
            hcd="00CD10",
            source_url=f"https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{_XML_UUID}",
            sha256="c" * 64,
            size_bytes=30,
            status_code=200,
            media_type="application/xml",
            headers={"content-type": "application/xml"},
        ),
        CaptureRecord(
            kind="basic_info_refetch",
            hcd="00CD10",
            source_url="https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/00CD10",
            sha256=basic_digest,
            size_bytes=20,
            status_code=200,
            media_type="application/json",
            headers={"content-type": "application/json"},
        ),
    ]


def test_synthetic_fixture_manifest_matches_invented_payloads() -> None:
    manifest = cast(dict[str, object], json.loads(_fixture("manifest.json")))
    assert manifest["origin"] == (
        "invented payload values using public RSK protocol identifiers and field shapes"
    )
    for raw_entry in cast(list[dict[str, object]], manifest["files"]):
        content = _fixture(cast(str, raw_entry["path"]))
        assert len(content) == raw_entry["size_bytes"]
        assert hashlib.sha256(content).hexdigest() == raw_entry["sha256"]
    xml = _fixture("product-00CD10.xml")
    assert b"A00" not in xml
    assert b"NOT-A-MEDICAL-CODE" in xml
    assert b"00CD10" in _fixture("basic-info-00CD10.json")


def test_default_capture_root_is_repository_anchored_from_foreign_cwd(
    capture_base: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    foreign_cwd = tmp_path / "foreign"
    foreign_cwd.mkdir(mode=0o700)
    monkeypatch.chdir(foreign_cwd)
    requests: list[str] = []
    capture_root = _capture(capture_base, requests)

    assert capture_root.parent == capture_base
    assert not (foreign_cwd / "data").exists()
    assert requests == [
        "/api/rsk/resource/role/UZYNLG",
        "/api/rsk/resource/basic-info/00CD10",
        "/api/rsk/resource/basic-info/99ZZ99",
        f"/api/rsk/file/get/{_XML_UUID}",
        "/api/rsk/resource/basic-info/00CD10",
    ]


def test_capture_has_no_output_root_override(capture_base: Path) -> None:
    assert "destination_root" not in signature(capture_private_rsk).parameters
    assert not capture_base.exists()


def test_capture_rejects_symlinked_path_component(
    capture_base: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    capture_base.parents[1].symlink_to(outside, target_is_directory=True)
    with pytest.raises(RskCaptureError, match="symbolic-link"):
        _ = capture_private_rsk(
            transport=_capture_transport([]), captured_at=_CAPTURE_TIME
        )


def test_capture_accepts_only_mock_transport(capture_base: Path) -> None:
    with pytest.raises(RskCaptureError, match="only an HTTPX mock"):
        _ = capture_private_rsk(transport=object(), captured_at=_CAPTURE_TIME)
    assert not capture_base.exists()


def test_capture_accepts_fixed_time_only_with_mock_transport(
    capture_base: Path,
) -> None:
    with pytest.raises(RskCaptureError, match="fixed capture time"):
        _ = capture_private_rsk(captured_at=_CAPTURE_TIME)
    assert not capture_base.exists()


def test_capture_ignores_cookie_state_without_archiving_it(capture_base: Path) -> None:
    requests: list[str] = []
    captured = capture_private_rsk(
        transport=_capture_transport(requests, set_cookie=True),
        captured_at=_CAPTURE_TIME,
    )
    records = cast(list[dict[str, object]], _read_manifest(captured.path)["records"])
    assert records
    assert all(
        "set-cookie" not in cast(dict[str, object], record["headers"])
        for record in records
    )
    assert len(requests) >= 4
    assert not list(capture_base.glob("*.quarantine"))


def test_capture_permissions_public_privacy_and_stream_validator(
    capture_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_root = _capture(capture_base)
    for path in [capture_root, *capture_root.rglob("*")]:
        mode = stat.S_IMODE(path.lstat().st_mode)
        assert mode == (0o700 if path.is_dir() else 0o600)
        assert not path.is_symlink()

    public_bytes = (capture_root / PUBLIC_INDEX_NAME).read_bytes()
    for canary in (
        b"private-canary@example.invalid",
        b"SYNTHETIC_PRIVATE_CANARY_PERSON",
        b"SYNTHETIC_PRIVATE_CANARY_DESCRIPTION",
        b"synthetic.xml",
        b"source_url",
        b"headers",
        b"uuid",
    ):
        assert canary not in public_bytes

    def reject_read_bytes(_self: Path) -> bytes:
        raise AssertionError("validator must use bounded streamed reads")

    monkeypatch.setattr(Path, "read_bytes", reject_read_bytes)
    assert validate_private_capture(capture_root).status == "complete"


def test_private_validator_rejects_noncanonical_manifest(
    capture_base: Path,
) -> None:
    capture_root = _capture(capture_base)
    manifest_path = capture_root / "private-manifest.json"
    parsed = cast(dict[str, object], json.loads(manifest_path.read_bytes()))
    _ = manifest_path.write_text(json.dumps(parsed, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    with pytest.raises(RskCaptureError, match="not canonical"):
        _ = validate_private_capture(capture_root)


def test_capture_quarantines_metadata_rotation(capture_base: Path) -> None:
    with pytest.raises(RskCaptureRotationError) as raised:
        _ = capture_private_rsk(
            transport=_capture_transport([], rotate=True), captured_at=_CAPTURE_TIME
        )
    quarantine = raised.value.quarantine_path
    assert quarantine is not None
    assert quarantine.parent == capture_base
    marker = cast(
        dict[str, object],
        json.loads((quarantine / "quarantine.json").read_bytes()),
    )
    assert marker["reason"] == "metadata_rotation"
    assert not (quarantine / PUBLIC_INDEX_NAME).exists()


def test_capture_rejects_existing_output(capture_base: Path) -> None:
    first = _capture(capture_base)
    with pytest.raises(RskCaptureError, match="already exists"):
        _ = capture_private_rsk(
            transport=_capture_transport([]), captured_at=_CAPTURE_TIME
        )
    assert first.exists()


def test_capture_bounds_canonical_private_manifest_before_write(
    capture_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _capture(capture_base)
    manifest_size_bytes = (baseline / "private-manifest.json").stat().st_size

    exact_time = _CAPTURE_TIME + timedelta(seconds=1)
    monkeypatch.setattr(capture_module, "MAX_MANIFEST_BYTES", manifest_size_bytes)
    exact = capture_private_rsk(
        transport=_capture_transport([]), captured_at=exact_time
    )
    assert (exact.path / "private-manifest.json").stat().st_size == manifest_size_bytes

    oversized_time = _CAPTURE_TIME + timedelta(seconds=2)
    monkeypatch.setattr(capture_module, "MAX_MANIFEST_BYTES", manifest_size_bytes - 1)
    with pytest.raises(RskCaptureError, match="manifest exceeds its byte bound"):
        _ = capture_private_rsk(
            transport=_capture_transport([]), captured_at=oversized_time
        )
    quarantine = capture_base / (
        oversized_time.strftime("%Y%m%dT%H%M%SZ") + ".quarantine"
    )
    assert quarantine.is_dir()
    assert not (quarantine / "private-manifest.json").exists()
    assert not (quarantine / PUBLIC_INDEX_NAME).exists()


@pytest.mark.parametrize(
    ("kind", "field", "value"),
    [
        ("role_catalog", "source_url", "https://attacker.invalid/catalog"),
        ("basic_info", "hcd", "88ZZ88"),
        ("basic_info_refetch", "source_url", "https://attacker.invalid/refetch"),
        ("product_xml", "source_url", "https://attacker.invalid/product"),
        ("product_xml", "media_type", "application/json"),
        ("basic_info", "status_code", 201),
        ("basic_info", "media_type", "text/xml"),
    ],
)
def test_private_manifest_rejects_every_record_identity_mismatch(
    capture_base: Path, kind: str, field: str, value: object
) -> None:
    capture_root = _capture(capture_base)
    manifest = _read_manifest(capture_root)
    records = cast(list[dict[str, object]], manifest["records"])
    target = next(record for record in records if record["kind"] == kind)
    target[field] = value
    _write_manifest(capture_root, manifest)
    with pytest.raises(RskCaptureError, match="invalid|identity"):
        _ = validate_private_capture(capture_root)


@pytest.mark.parametrize("kind", ["basic_info", "product_xml"])
def test_private_manifest_rejects_coordinated_false_source_identity(
    capture_base: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    capture_root = _capture(capture_base)
    manifest = _read_manifest(capture_root)
    records = cast(list[dict[str, object]], manifest["records"])
    target = next(record for record in records if record["kind"] == kind)
    if kind == "basic_info":
        target["hcd"] = "88ZZ88"
        target["source_url"] = (
            "https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/88ZZ88"
        )
    else:
        replacement_uuid = "00000000-0000-4000-8000-000000000013"
        target["source_url"] = (
            "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/" + replacement_uuid
        )
        selected = cast(dict[str, object], manifest["selected_product"])
        selected["uuid"] = replacement_uuid
    _write_manifest(capture_root, manifest)
    original_open_blob = capture_module._open_blob  # pyright: ignore[reportPrivateUsage]

    def reject_product_open(
        digest_descriptor: int, record: CaptureRecord
    ) -> AbstractContextManager[BinaryIO]:
        if record.kind == "product_xml":
            pytest.fail("product blob opened before metadata provenance was validated")
        return original_open_blob(digest_descriptor, record)

    monkeypatch.setattr(capture_module, "_open_blob", reject_product_open)
    with pytest.raises((RskCaptureError, RskSourceDriftError)):
        _ = validate_private_capture(capture_root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("sha256", "../" + ("a" * 61)),
        ("sha256", "A" * 64),
        ("size_bytes", MAX_METADATA_BYTES + 1),
    ],
)
def test_manifest_rejects_unsafe_record_before_blob_open(
    capture_base: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    capture_root = _capture(capture_base)
    manifest = _read_manifest(capture_root)
    records = cast(list[dict[str, object]], manifest["records"])
    metadata_record = next(item for item in records if item["kind"] == "basic_info")
    metadata_record[field] = value
    _write_manifest(capture_root, manifest)

    def reject_blob_open(*_arguments: object, **_keywords: object) -> None:
        pytest.fail("invalid manifest reached blob open")

    monkeypatch.setattr(capture_module, "_open_blob", reject_blob_open)
    with pytest.raises(RskCaptureError, match="manifest"):
        _ = validate_private_capture(capture_root)


def test_manifest_bounds_record_visits_before_blob_open(
    capture_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_root = _capture(capture_base)
    manifest = _read_manifest(capture_root)
    records = cast(list[dict[str, object]], manifest["records"])
    manifest["records"] = records + [deepcopy(records[0])] * MAX_CAPTURE_RECORDS
    _write_manifest(capture_root, manifest)

    def reject_blob_open(*_arguments: object, **_keywords: object) -> None:
        pytest.fail("oversized manifest reached blob open")

    monkeypatch.setattr(capture_module, "_open_blob", reject_blob_open)
    with pytest.raises(RskCaptureError, match="manifest"):
        _ = validate_private_capture(capture_root)


def test_validator_rejects_symlinked_digest_file(capture_base: Path) -> None:
    capture_root = _capture(capture_base)
    manifest = _read_manifest(capture_root)
    record = cast(list[dict[str, object]], manifest["records"])[0]
    blob = capture_root / "blobs" / "sha256" / cast(str, record["sha256"])
    blob.unlink()
    blob.symlink_to("/etc/passwd")
    with pytest.raises(RskCaptureError, match="symbolic-link|entry type"):
        _ = validate_private_capture(capture_root)


def test_validator_detects_capture_directory_replacement(
    capture_base: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture_root = _capture(capture_base)
    original_validator = capture_module._validate_manifest_record_identities  # pyright: ignore[reportPrivateUsage]

    def replace_capture_directory(manifest: PrivateCaptureManifest) -> None:
        original_validator(manifest)
        _ = capture_root.rename(capture_root.with_name(capture_root.name + ".replaced"))
        capture_root.mkdir(mode=0o700)

    monkeypatch.setattr(
        capture_module,
        "_validate_manifest_record_identities",
        replace_capture_directory,
    )
    with pytest.raises(RskCaptureError, match="identity changed"):
        _ = validate_private_capture(capture_root)


def test_validator_rejects_capture_outside_fixed_root(
    capture_base: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "20260921T120000Z"
    outside.mkdir(mode=0o700)
    with pytest.raises(RskCaptureError, match="outside"):
        _ = validate_private_capture(outside)
    assert not capture_base.exists()


def test_public_index_is_deterministic_and_has_stable_digest() -> None:
    records = _private_records()
    first = build_public_checksum_index("20260921T120000Z", records, 1)
    second = build_public_checksum_index("20260921T120000Z", list(reversed(records)), 1)
    first_bytes = public_checksum_index_bytes(first)
    assert first == second
    assert first_bytes == public_checksum_index_bytes(second)
    assert hashlib.sha256(first_bytes).hexdigest() == (
        "335bb9dfcdf13f1630ff0d8d8dc98b6f3baf7ef6df827f73198cedede2d48a45"
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("capture_id",), "20261399T999999Z"),
        (("records", 0, "sha256"), "A" * 64),
        (("records", 0, "size_bytes"), MAX_METADATA_BYTES + 1),
        (("totals", "response_count"), 99),
        (("totals", "captured_bytes"), 1),
    ],
)
def test_public_index_schema_rejects_scalar_and_total_drift(
    path: tuple[str | int, ...], value: object
) -> None:
    data = cast(
        dict[str, object],
        json.loads(
            public_checksum_index_bytes(
                build_public_checksum_index("20260921T120000Z", _private_records(), 1)
            )
        ),
    )
    _set_nested_value(data, path, value)
    with pytest.raises(ValidationError):
        _ = PublicChecksumIndex.model_validate(data)


def test_public_index_schema_rejects_cardinality_and_order() -> None:
    index = build_public_checksum_index("20260921T120000Z", _private_records(), 1)
    data = index.model_dump(mode="json", exclude_none=True)
    records = cast(list[dict[str, object]], data["records"])
    data["records"] = list(reversed(records))
    with pytest.raises(ValidationError, match="deterministically ordered"):
        _ = PublicChecksumIndex.model_validate(data)

    data = index.model_dump(mode="json", exclude_none=True)
    data["records"] = cast(list[object], data["records"])[:-1]
    with pytest.raises(ValidationError, match="cardinality|count|at least"):
        _ = PublicChecksumIndex.model_validate(data)

    data = index.model_dump(mode="json", exclude_none=True)
    cast(list[dict[str, object]], data["records"])[0]["headers"] = {}
    with pytest.raises(ValidationError, match="Extra inputs"):
        _ = PublicChecksumIndex.model_validate(data)


def test_checked_live_index_always_validates_and_has_canonical_digest() -> None:
    checked = validate_public_checksum_index(_LIVE_PUBLIC_INDEX)
    assert checked.status == "complete"
    assert checked.capture_id == REVIEWED_CAPTURE_ID
    assert hashlib.sha256(_LIVE_PUBLIC_INDEX.read_bytes()).hexdigest() == (
        REVIEWED_PUBLIC_INDEX_SHA256
    )


def test_public_validator_rejects_noncanonical_bytes(capture_base: Path) -> None:
    index = build_public_checksum_index("20260921T120000Z", _private_records(), 1)
    path = capture_base.parents[2] / "noncanonical.json"
    _ = path.write_bytes(b" " + public_checksum_index_bytes(index))
    with pytest.raises(RskCaptureError, match="not canonical"):
        _ = validate_public_checksum_index(path)


def test_checked_index_validates_when_private_capture_id_differs(
    capture_base: Path,
) -> None:
    private = validate_private_capture(_capture(capture_base))
    capture_module.REPOSITORY_ROOT = _REPOSITORY
    checked = validate_public_checksum_index(_LIVE_PUBLIC_INDEX)
    assert private.capture_id == "20260921T120000Z"
    assert checked.capture_id != private.capture_id


def test_xml_validator_accepts_the_element_cap_and_refuses_one_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cap is the validator's real bound on a streamed document; the root
    # counts as one element (#237).
    monkeypatch.setattr(capture_module, "MAX_XML_ELEMENTS", 1_000)

    def document(items: int) -> bytes:
        return (
            b'<?xml version="1.0"?><synthetic>'
            + (b"<item>invented</item>" * items)
            + b"</synthetic>"
        )

    at_cap = document(999)
    capture_module._validate_complete_xml(  # pyright: ignore[reportPrivateUsage]
        BytesIO(at_cap), len(at_cap)
    )
    over = document(1_000)
    with pytest.raises(RskProtocolError, match="structure exceeds"):
        capture_module._validate_complete_xml(  # pyright: ignore[reportPrivateUsage]
            BytesIO(over), len(over)
        )


def test_xml_validator_rejects_dtd_and_bounded_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dtd = b'<?xml version="1.0"?><!DOCTYPE x><x/>'
    with pytest.raises(RskProtocolError, match="prohibited"):
        capture_module._validate_complete_xml(  # pyright: ignore[reportPrivateUsage]
            BytesIO(dtd), len(dtd)
        )
    monkeypatch.setattr(capture_module, "MAX_XML_TEXT_CHARACTERS", 4)
    text = b'<?xml version="1.0"?><x>invented</x>'
    with pytest.raises(RskProtocolError, match="text exceeds"):
        capture_module._validate_complete_xml(  # pyright: ignore[reportPrivateUsage]
            BytesIO(text), len(text)
        )


def test_xml_validator_uses_reviewed_product_bound() -> None:
    assert MAX_PRODUCT_BYTES == 64 * 1024 * 1024
    assert 12_738_027 < MAX_PRODUCT_BYTES
    with pytest.raises(RskProtocolError, match="outside its bound"):
        capture_module._validate_complete_xml(  # pyright: ignore[reportPrivateUsage]
            BytesIO(b"<x/>"), MAX_PRODUCT_BYTES + 1
        )


def test_models_bound_text_collections_and_require_nullable_fields() -> None:
    body = cast(dict[str, object], json.loads(_fixture("basic-info-00CD10.json")))
    body["name"] = "x" * 513
    with pytest.raises(ValidationError, match="string_too_long"):
        _ = RskCatalogMetadata.model_validate(body)
    body = cast(dict[str, object], json.loads(_fixture("basic-info-00CD10.json")))
    del body["validDateTo"]
    with pytest.raises(ValidationError, match="validDateTo"):
        _ = RskCatalogMetadata.model_validate(body)


def test_global_duplicate_file_uuid_is_quarantined(capture_base: Path) -> None:
    basic_other = cast(
        dict[str, object], json.loads(_fixture("basic-info-99ZZ99.json"))
    )
    files = cast(list[dict[str, object]], basic_other["files"])
    files[0]["uuid"] = _XML_UUID
    modified_other = json.dumps(basic_other, separators=(",", ":")).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rsk/resource/role/UZYNLG":
            return _response(_fixture("role-catalog.json"), "application/json")
        if request.url.path == "/api/rsk/resource/basic-info/00CD10":
            return _response(_fixture("basic-info-00CD10.json"), "application/json")
        if request.url.path == "/api/rsk/resource/basic-info/99ZZ99":
            return _response(modified_other, "application/json")
        raise AssertionError("product request must not occur after identity failure")

    with pytest.raises(RskSourceDriftError, match="reused across"):
        _ = capture_private_rsk(
            transport=httpx.MockTransport(handler), captured_at=_CAPTURE_TIME
        )
    assert list(capture_base.glob("*.quarantine"))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("extension", ".XML", "exactly one"),
        ("sizeBytes", MAX_PRODUCT_BYTES + 1, "provenance"),
    ],
)
def test_invalid_product_provenance_is_rejected_before_download(
    capture_base: Path, field: str, value: object, message: str
) -> None:
    basic = cast(dict[str, object], json.loads(_fixture("basic-info-00CD10.json")))
    files = cast(list[dict[str, object]], basic["files"])
    files[0][field] = value
    modified_basic = json.dumps(basic, separators=(",", ":")).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rsk/resource/role/UZYNLG":
            return _response(_fixture("role-catalog.json"), "application/json")
        if request.url.path == "/api/rsk/resource/basic-info/00CD10":
            return _response(modified_basic, "application/json")
        if request.url.path == "/api/rsk/resource/basic-info/99ZZ99":
            return _response(_fixture("basic-info-99ZZ99.json"), "application/json")
        raise AssertionError("product request must not occur for invalid provenance")

    with pytest.raises(RskSourceDriftError, match=message):
        _ = capture_private_rsk(
            transport=httpx.MockTransport(handler), captured_at=_CAPTURE_TIME
        )
    assert list(capture_base.glob("*.quarantine"))


def test_fixed_settings_ignore_environment_source_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINOS_RSK_ROLE_CATALOG_URL", "https://attacker.invalid/")
    assert fixed_rsk_settings().role_catalog_url.host == "rsk3.ezdrowie.gov.pl"


def test_capture_cli_sanitizes_failure_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_capture() -> None:
        raise ValueError("SYNTHETIC_PRIVATE_CANARY_PERSON")

    monkeypatch.setattr(capture_script, "capture_private_rsk", fail_capture)
    monkeypatch.setattr("sys.argv", ["capture_rsk_fixtures"])
    assert capture_script.main() == 1
    captured = capsys.readouterr()
    assert "SYNTHETIC_PRIVATE_CANARY_PERSON" not in captured.err
    assert "ValueError" in captured.err


def test_nullable_current_forms_remain_valid() -> None:
    observation = HttpObservation(
        source_url="https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/99ZZ99",
        body=_fixture("basic-info-99ZZ99.json"),
        headers=(("content-type", "application/json"),),
    )
    metadata = parse_rsk_catalog_basic_info(observation, "99ZZ99")
    assert metadata.publisher is None
    assert metadata.education_links is None
    assert metadata.fhir_or_rest_api_version == ""


def _set_nested_value(
    root: dict[str, object], path: tuple[str | int, ...], value: object
) -> None:
    target: object = root
    for part in path[:-1]:
        if isinstance(part, str) and isinstance(target, dict):
            mapping = cast(  # pyright: ignore[reportUnnecessaryCast]
                dict[str, object], target
            )
            target = mapping[part]
        elif isinstance(part, int) and isinstance(target, list):
            target = cast(list[object], target)[part]
        else:
            raise AssertionError("invalid test mutation path")
    final = path[-1]
    if isinstance(final, str) and isinstance(target, dict):
        cast(dict[str, object], target)[final] = value
    elif isinstance(final, int) and isinstance(target, list):
        cast(list[object], target)[final] = value
    else:
        raise AssertionError("invalid test mutation target")


@pytest.mark.rsk_live
def test_private_live_capture_compatibility() -> None:
    capture_root = os.environ.get("MINOS_RSK_PRIVATE_CAPTURE_ROOT")
    if capture_root is None:
        pytest.skip("MINOS_RSK_PRIVATE_CAPTURE_ROOT is not set")
    checked = attest_reviewed_rsk_evidence(capture_root)
    assert checked.capture_id == REVIEWED_CAPTURE_ID
    assert checked.status == "complete"
