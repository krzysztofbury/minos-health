import hashlib
import json
import os
import re
import stat
from collections.abc import Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO, cast
from uuid import UUID
from xml.parsers import expat

import httpx
from pydantic import ValidationError

from minos.ingest.watchers.archive import BlobStagingArea, FilesystemBlobArchive
from minos.ingest.watchers.archive.files import open_verified_staged_blob
from minos.ingest.watchers.config import RskConfiguration, RskSettings
from minos.ingest.watchers.models import HttpObservation
from minos.ingest.watchers.rsk.capture_models import (
    CAPTURE_ID_PATTERN,
    CAPTURE_SCHEMA_VERSION,
    MAX_CAPTURE_BYTES,
    MAX_CAPTURE_FILES,
    MAX_CAPTURE_RECORDS,
    MAX_CAPTURE_RESOURCES,
    MAX_METADATA_BYTES,
    MAX_PRODUCT_BYTES,
    PrivateCaptureManifest,
    PrivateCaptureRecord,
    PrivateCaptureTotals,
    PrivateSelectedProduct,
    PublicChecksumIndex,
    PublicChecksumRecord,
    PublicChecksumTotals,
    public_record_sort_key,
)
from minos.ingest.watchers.rsk.client import (
    RskProtocolError,
    RskSourceDriftError,
    create_rsk_client,
    download_rsk_file,
    get_default_rsk_role_catalog,
    get_rsk_catalog_basic_info,
    parse_rsk_catalog_basic_info,
    parse_rsk_role_catalog,
)
from minos.ingest.watchers.rsk.models import (
    RSK_ARCHIVED_HEADER_VALUE_LIMITS,
    RSK_XML_EXTENSION,
    RskCatalogFile,
    RskCatalogMetadata,
    is_valid_rsk_archived_header,
)

REPOSITORY_ROOT = Path(__file__).absolute().parents[5]
CAPTURE_ROOT = REPOSITORY_ROOT / "data" / "private-fixtures" / "rsk"
CHECKED_PUBLIC_INDEX = (
    REPOSITORY_ROOT / "docs" / "validation" / "rsk-live-capture-checksums.json"
)
PRIVATE_MANIFEST_NAME = "private-manifest.json"
PUBLIC_INDEX_NAME = "public-checksums.json"
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_XML_ELEMENTS = 5_000_000
MAX_XML_DEPTH = 256
MAX_XML_ATTRIBUTES = 1024
MAX_XML_TEXT_CHARACTERS = 8 * 1024 * 1024
MAX_CAPTURE_ENTRIES = MAX_CAPTURE_RECORDS + 8
_CAPTURE_ID_FORMAT = "%Y%m%dT%H%M%SZ"
_CAPTURE_ID_RE = re.compile(CAPTURE_ID_PATTERN)
REVIEWED_CAPTURE_ID = "20260921T182646Z"
REVIEWED_PUBLIC_INDEX_SHA256 = (
    "16129d526f282f3f41909dc8f6227f55e07c8cb7664391960cbfbc5e31d891c6"
)
_ARCHIVED_HEADER_NAMES = frozenset(RSK_ARCHIVED_HEADER_VALUE_LIMITS)

CaptureRecord = PrivateCaptureRecord


class RskCaptureError(RuntimeError):
    pass


class RskCaptureRotationError(RskCaptureError):
    def __init__(self) -> None:
        super().__init__("RSK metadata rotated during private capture")
        self.quarantine_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RskCaptureResult:
    path: Path
    capture_id: str
    resource_count: int
    file_metadata_count: int
    captured_bytes: int
    xml_sha256: str
    xml_size_bytes: int
    public_index_sha256: str


@dataclass(frozen=True, slots=True)
class _CaptureDirectory:
    path: Path
    name: str
    descriptor: int
    parent_descriptor: int
    identity: tuple[int, int]


def fixed_rsk_settings() -> RskSettings:
    configuration = RskConfiguration()
    return RskSettings.model_validate(configuration.model_dump())


def capture_private_rsk(
    *,
    transport: object | None = None,
    captured_at: datetime | None = None,
) -> RskCaptureResult:
    if transport is None:
        mock_transport = None
    elif isinstance(transport, httpx.MockTransport):
        mock_transport = transport
    else:
        raise RskCaptureError("only an HTTPX mock transport may be injected")
    if captured_at is not None and mock_transport is None:
        raise RskCaptureError("a fixed capture time requires an HTTPX mock transport")
    capture_time = _utc_capture_time(captured_at)
    capture_id = capture_time.strftime(_CAPTURE_ID_FORMAT)
    settings = fixed_rsk_settings()
    with _create_capture_root(capture_id) as capture_directory:
        try:
            with create_rsk_client(settings, transport=mock_transport) as client:
                return _capture_into(
                    capture_directory, capture_id, capture_time, client, settings
                )
        except BaseException as error:
            quarantine_path = _quarantine(capture_directory, error)
            if isinstance(error, RskCaptureRotationError):
                error.quarantine_path = quarantine_path
            raise


def _capture_into(
    capture_directory: _CaptureDirectory,
    capture_id: str,
    capture_time: datetime,
    client: httpx.Client,
    settings: RskSettings,
) -> RskCaptureResult:
    capture_root = capture_directory.path
    staging = BlobStagingArea(
        capture_root / "staging", maximum_size_bytes=settings.file_max_bytes
    )
    archive = FilesystemBlobArchive(capture_root)
    records: list[PrivateCaptureRecord] = []
    catalog_observation = get_default_rsk_role_catalog(client, settings)
    _append_record(
        records,
        _archive_observation(archive, staging, catalog_observation, "role_catalog"),
    )
    catalog = parse_rsk_role_catalog(catalog_observation)
    if len(catalog) > MAX_CAPTURE_RESOURCES:
        raise RskSourceDriftError("RSK capture resource count exceeds its bound")

    metadata_by_hcd: dict[str, RskCatalogMetadata] = {}
    file_count = 0
    for resource in catalog:
        observation = get_rsk_catalog_basic_info(client, settings, resource.hcd)
        _append_record(
            records,
            _archive_observation(
                archive, staging, observation, "basic_info", hcd=resource.hcd
            ),
        )
        metadata = parse_rsk_catalog_basic_info(observation, resource.hcd)
        metadata_by_hcd[resource.hcd] = metadata
        file_count += len(metadata.files)
        if file_count > MAX_CAPTURE_FILES:
            raise RskSourceDriftError("RSK capture file count exceeds its bound")

    _validate_global_file_identities(metadata_by_hcd)
    selected = select_active_icd10_xml(metadata_by_hcd, capture_time)
    selected_product = _selected_product(selected)
    if _captured_size(records) + selected_product.size_bytes > MAX_CAPTURE_BYTES:
        raise RskCaptureError("private capture byte count exceeds its bound")
    xml_record = _download_xml(archive, staging, client, settings, selected_product)
    _append_record(records, xml_record)

    refetch = get_rsk_catalog_basic_info(client, settings, "00CD10")
    _append_record(
        records,
        _archive_observation(
            archive, staging, refetch, "basic_info_refetch", hcd="00CD10"
        ),
    )
    if parse_rsk_catalog_basic_info(refetch, "00CD10") != metadata_by_hcd["00CD10"]:
        raise RskCaptureRotationError

    public_index = build_public_checksum_index(capture_id, records, file_count)
    public_bytes = public_checksum_index_bytes(public_index)
    private_manifest = _build_private_manifest(
        capture_id, records, len(catalog), file_count, selected_product
    )
    private_bytes = _private_manifest_bytes(private_manifest)
    _write_private_file(
        capture_directory.descriptor,
        PRIVATE_MANIFEST_NAME,
        private_bytes,
    )
    _assert_written_private_manifest(capture_directory.descriptor, private_bytes)
    _write_private_file(capture_directory.descriptor, PUBLIC_INDEX_NAME, public_bytes)
    _verify_capture_tree(
        capture_directory.descriptor, {record.sha256 for record in records}
    )
    _assert_capture_identity(capture_directory)
    return RskCaptureResult(
        path=capture_root,
        capture_id=capture_id,
        resource_count=len(catalog),
        file_metadata_count=file_count,
        captured_bytes=_captured_size(records),
        xml_sha256=xml_record.sha256,
        xml_size_bytes=xml_record.size_bytes,
        public_index_sha256=hashlib.sha256(public_bytes).hexdigest(),
    )


def select_active_icd10_xml(
    metadata_by_hcd: Mapping[str, RskCatalogMetadata], captured_at: datetime
) -> RskCatalogFile:
    metadata = metadata_by_hcd.get("00CD10")
    if metadata is None:
        raise RskSourceDriftError("RSK role catalog does not contain 00CD10")
    if not _is_active(metadata, captured_at):
        raise RskSourceDriftError("RSK 00CD10 metadata is not active")
    candidates = [
        file
        for file in metadata.files
        if file.extension == RSK_XML_EXTENSION
        and file.archived is False
        and file.education_file is False
    ]
    if len(candidates) != 1:
        raise RskSourceDriftError(
            "RSK 00CD10 must expose exactly one active non-education XML"
        )
    selected = candidates[0]
    if selected.size_bytes is None or selected.size_bytes < 1:
        raise RskSourceDriftError("RSK selected XML has no positive size")
    if selected.creation_date is None:
        raise RskSourceDriftError("RSK selected XML has no creation date")
    return selected


def _selected_product(selected: RskCatalogFile) -> PrivateSelectedProduct:
    if selected.creation_date is None:
        raise RskSourceDriftError("RSK selected XML has no creation date")
    if selected.size_bytes is None or selected.size_bytes < 1:
        raise RskSourceDriftError("RSK selected XML has no positive size")
    try:
        return PrivateSelectedProduct.model_validate(
            {
                "hcd": "00CD10",
                "uuid": str(selected.uuid),
                "creation_date": selected.creation_date,
                "size_bytes": selected.size_bytes,
                "extension": selected.extension,
            }
        )
    except ValidationError as error:
        raise RskSourceDriftError("RSK selected XML provenance is invalid") from error


def build_public_checksum_index(
    capture_id: str,
    records: list[PrivateCaptureRecord],
    file_metadata_count: int,
) -> PublicChecksumIndex:
    ordered = sorted(records, key=public_record_sort_key)
    public_records = [
        PublicChecksumRecord.model_validate(
            {
                "kind": record.kind,
                "hcd": record.hcd,
                "sha256": record.sha256,
                "size_bytes": record.size_bytes,
                "status_code": record.status_code,
                "media_type": record.media_type,
            }
        )
        for record in ordered
    ]
    return PublicChecksumIndex(
        schema_version=1,
        capture_id=capture_id,
        source="RSK",
        status="complete",
        records=public_records,
        totals=PublicChecksumTotals(
            response_count=len(records),
            resource_count=sum(record.kind == "basic_info" for record in records),
            file_metadata_count=file_metadata_count,
            product_file_count=1,
            captured_bytes=_captured_size(records),
        ),
    )


def public_checksum_index_bytes(index: PublicChecksumIndex) -> bytes:
    reparsed = PublicChecksumIndex.model_validate(index.model_dump())
    return _canonical_json(reparsed.model_dump(mode="json", exclude_none=True))


def validate_public_checksum_index(path: str | Path) -> PublicChecksumIndex:
    content = _read_repository_file(path, MAX_MANIFEST_BYTES)
    try:
        raw_index = cast(object, json.loads(content))
    except json.JSONDecodeError as error:
        raise RskCaptureError("public checksum index is invalid") from error
    if content != _canonical_json(raw_index):
        raise RskCaptureError("public checksum index is not canonical")
    try:
        index = PublicChecksumIndex.model_validate_json(content)
    except ValidationError as error:
        raise RskCaptureError("public checksum index is invalid") from error
    return index


def attest_reviewed_rsk_evidence(capture_root: str | Path) -> PublicChecksumIndex:
    private_index = validate_private_capture(capture_root)
    if private_index.capture_id != REVIEWED_CAPTURE_ID:
        raise RskCaptureError("private capture is not the reviewed evidence")
    content = _read_repository_file(CHECKED_PUBLIC_INDEX, MAX_MANIFEST_BYTES)
    if hashlib.sha256(content).hexdigest() != REVIEWED_PUBLIC_INDEX_SHA256:
        raise RskCaptureError("reviewed public checksum file identity is invalid")
    checked_index = validate_public_checksum_index(CHECKED_PUBLIC_INDEX)
    if checked_index.capture_id != REVIEWED_CAPTURE_ID:
        raise RskCaptureError("reviewed public checksum capture identity is invalid")
    if checked_index != private_index:
        raise RskCaptureError("reviewed public checksum index does not match capture")
    return checked_index


def validate_private_capture(capture_root: str | Path) -> PublicChecksumIndex:
    with _existing_capture_root(capture_root) as capture_directory:
        manifest_bytes = _read_capture_file(
            capture_directory.descriptor, PRIVATE_MANIFEST_NAME, MAX_MANIFEST_BYTES
        )
        try:
            raw_manifest = cast(object, json.loads(manifest_bytes))
        except json.JSONDecodeError as error:
            raise RskCaptureError("private capture manifest is invalid") from error
        if manifest_bytes != _canonical_json(raw_manifest):
            raise RskCaptureError("private capture manifest is not canonical")
        try:
            manifest = PrivateCaptureManifest.model_validate_json(manifest_bytes)
        except ValidationError as error:
            raise RskCaptureError("private capture manifest is invalid") from error
        if manifest.capture_id != capture_directory.name:
            raise RskCaptureError("private capture directory identity is inconsistent")
        _validate_manifest_record_identities(manifest)
        digests = {record.sha256 for record in manifest.records}
        with _capture_digest_directory(
            capture_directory.descriptor, digests
        ) as digest_descriptor:
            file_count, product_record = _validate_captured_metadata(
                digest_descriptor, manifest
            )
            verified: dict[str, int] = {}
            _validate_blob(digest_descriptor, product_record, verified)
            with _open_blob(digest_descriptor, product_record) as source:
                _validate_complete_xml(source, product_record.size_bytes)
        expected_public = build_public_checksum_index(
            manifest.capture_id, manifest.records, file_count
        )
        public_bytes = _read_capture_file(
            capture_directory.descriptor, PUBLIC_INDEX_NAME, MAX_MANIFEST_BYTES
        )
        try:
            actual_public = PublicChecksumIndex.model_validate_json(public_bytes)
        except ValidationError as error:
            raise RskCaptureError("private public checksum index is invalid") from error
        if actual_public != expected_public:
            raise RskCaptureError(
                "public checksum index does not match private capture"
            )
        if public_bytes != public_checksum_index_bytes(actual_public):
            raise RskCaptureError("private public checksum index is not canonical")
        _assert_capture_identity(capture_directory)
        return expected_public


def _archive_observation(
    archive: FilesystemBlobArchive,
    staging: BlobStagingArea,
    observation: HttpObservation,
    kind: str,
    *,
    hcd: str | None = None,
) -> PrivateCaptureRecord:
    media_type = _media_type(observation.headers)
    with staging.stage() as writer:
        if writer.write(observation.body) != len(observation.body):
            raise OSError("private capture metadata staging was incomplete")
        receipt = archive.put(writer.seal(media_type=media_type))
    return PrivateCaptureRecord.model_validate(
        {
            "kind": kind,
            "hcd": hcd,
            "source_url": observation.source_url,
            "sha256": receipt.sha256,
            "size_bytes": receipt.size_bytes,
            "status_code": 200,
            "media_type": media_type,
            "headers": dict(observation.headers),
        }
    )


def _download_xml(
    archive: FilesystemBlobArchive,
    staging: BlobStagingArea,
    client: httpx.Client,
    settings: RskSettings,
    selected: PrivateSelectedProduct,
) -> PrivateCaptureRecord:
    with staging.stage() as writer:
        result = download_rsk_file(
            client,
            settings,
            UUID(selected.uuid),
            selected.size_bytes,
            selected.extension,
            writer,
        )
        media_type = _media_type(result.headers)
        blob = writer.seal(
            media_type=media_type,
            expected_sha256=result.sha256,
            expected_size_bytes=result.size_bytes,
        )
        with open_verified_staged_blob(blob) as source:
            _validate_complete_xml(source, result.size_bytes)
        receipt = archive.put(blob)
    return PrivateCaptureRecord.model_validate(
        {
            "kind": "product_xml",
            "hcd": "00CD10",
            "source_url": result.source_url,
            "sha256": receipt.sha256,
            "size_bytes": receipt.size_bytes,
            "status_code": 200,
            "media_type": media_type,
            "headers": dict(result.headers),
        }
    )


def _validate_complete_xml(source: BinaryIO, expected_size_bytes: int) -> None:
    if expected_size_bytes < 1 or expected_size_bytes > MAX_PRODUCT_BYTES:
        raise RskProtocolError("RSK XML size is outside its bound")
    parser = expat.ParserCreate()
    depth = 0
    element_count = 0
    text_counts: list[int] = []
    root_name: str | None = None

    def start_element(name: str, attributes: dict[str, str]) -> None:
        nonlocal depth, element_count, root_name
        depth += 1
        element_count += 1
        if depth > MAX_XML_DEPTH or element_count > MAX_XML_ELEMENTS:
            raise RskProtocolError("RSK XML structure exceeds its bound")
        if len(attributes) > MAX_XML_ATTRIBUTES:
            raise RskProtocolError("RSK XML attribute count exceeds its bound")
        if root_name is None:
            root_name = name.rsplit(":", 1)[-1].casefold()
            if root_name == "html":
                raise RskProtocolError("RSK returned HTML instead of XML")
        text_counts.append(0)

    def end_element(_name: str) -> None:
        nonlocal depth
        if not text_counts:
            raise RskProtocolError("RSK XML depth is inconsistent")
        _ = text_counts.pop()
        depth -= 1

    def character_data(value: str) -> None:
        if not text_counts:
            return
        text_counts[-1] += len(value)
        if text_counts[-1] > MAX_XML_TEXT_CHARACTERS:
            raise RskProtocolError("RSK XML text exceeds its bound")

    def reject_declaration(*_arguments: object) -> None:
        raise RskProtocolError("RSK XML contains a prohibited declaration")

    def reject_external_entity(
        _context: str,
        _base: str | None,
        _system_id: str | None,
        _public_id: str | None,
    ) -> int:
        raise RskProtocolError("RSK XML contains a prohibited external entity")

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = character_data
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_external_entity
    consumed_bytes = 0
    carry = b""
    try:
        while chunk := source.read(1024 * 1024):
            consumed_bytes += len(chunk)
            if consumed_bytes > expected_size_bytes:
                raise RskProtocolError("RSK XML exceeded its verified size")
            searchable = (carry + chunk).lower()
            if b"<!doctype" in searchable or b"<!entity" in searchable:
                raise RskProtocolError("RSK XML contains a prohibited declaration")
            carry = searchable[-16:]
            _ = parser.Parse(chunk, False)
        _ = parser.Parse(b"", True)
    except expat.ExpatError as error:
        raise RskProtocolError("RSK XML is not well formed") from error
    if consumed_bytes != expected_size_bytes:
        raise RskProtocolError("RSK XML size changed during validation")
    if depth != 0 or element_count == 0 or root_name is None:
        raise RskProtocolError("RSK XML has an invalid element structure")


def _validate_manifest_record_identities(manifest: PrivateCaptureManifest) -> None:
    settings = fixed_rsk_settings()
    catalog_url = str(settings.role_catalog_url)
    basic_base = str(settings.basic_info_base_url)
    product_url = f"{settings.file_download_base_url}{manifest.selected_product.uuid}"
    for visit_count, record in enumerate(manifest.records, start=1):
        if visit_count > MAX_CAPTURE_RECORDS:
            raise RskCaptureError("private capture record count exceeds its bound")
        if set(record.headers) - _ARCHIVED_HEADER_NAMES:
            raise RskCaptureError("private capture contains a forbidden header")
        if any(
            not is_valid_rsk_archived_header(name, value)
            for name, value in record.headers.items()
        ):
            raise RskCaptureError("private capture header is outside its bound")
        if _media_type(tuple(record.headers.items())) != record.media_type:
            raise RskCaptureError("private capture media identity is inconsistent")
        content_length = record.headers.get("content-length")
        if content_length is not None and content_length != str(record.size_bytes):
            raise RskCaptureError("private capture content length is inconsistent")
        content_encoding = record.headers.get("content-encoding", "identity").lower()
        if content_encoding not in {"", "identity"}:
            raise RskCaptureError("private capture content encoding is inconsistent")
        expected_url = _expected_record_url(
            record, catalog_url, basic_base, product_url
        )
        if record.source_url != expected_url:
            raise RskCaptureError("private capture source identity is inconsistent")


def _expected_record_url(
    record: PrivateCaptureRecord,
    catalog_url: str,
    basic_base: str,
    product_url: str,
) -> str:
    if record.kind == "role_catalog":
        return catalog_url
    if record.kind == "product_xml":
        return product_url
    if record.hcd is None:
        raise RskCaptureError("private capture resource identity is missing")
    return f"{basic_base}{record.hcd}"


def _validate_blob(
    digest_descriptor: int,
    record: PrivateCaptureRecord,
    verified: dict[str, int],
) -> None:
    prior_size = verified.get(record.sha256)
    if prior_size is not None:
        if prior_size != record.size_bytes:
            raise RskCaptureError("checksum is reused with a different size")
        return
    sha256 = hashlib.sha256()
    size_bytes = 0
    with _open_blob(digest_descriptor, record) as source:
        while chunk := source.read(1024 * 1024):
            size_bytes += len(chunk)
            if size_bytes > record.size_bytes:
                raise RskCaptureError("private blob exceeds manifest size")
            sha256.update(chunk)
    if size_bytes != record.size_bytes:
        raise RskCaptureError("private blob size does not match manifest")
    if sha256.hexdigest() != record.sha256:
        raise RskCaptureError("private blob checksum does not match manifest")
    verified[record.sha256] = record.size_bytes


def _validate_captured_metadata(
    digest_descriptor: int, manifest: PrivateCaptureManifest
) -> tuple[int, PrivateCaptureRecord]:
    verified: dict[str, int] = {}
    catalog_record = _one_record(manifest.records, "role_catalog")
    catalog = parse_rsk_role_catalog(
        _observation_from_record(digest_descriptor, catalog_record, verified)
    )
    metadata_by_hcd: dict[str, RskCatalogMetadata] = {}
    file_count = 0
    for record in manifest.records:
        if record.kind != "basic_info":
            continue
        if record.hcd is None:
            raise RskCaptureError("private capture resource identity is missing")
        metadata = parse_rsk_catalog_basic_info(
            _observation_from_record(digest_descriptor, record, verified), record.hcd
        )
        metadata_by_hcd[record.hcd] = metadata
        file_count += len(metadata.files)
        if file_count > MAX_CAPTURE_FILES:
            raise RskCaptureError("captured file metadata count exceeds its bound")
    if set(metadata_by_hcd) != {resource.hcd for resource in catalog}:
        raise RskCaptureError("private capture does not cover every catalog HCD")
    _validate_global_file_identities(metadata_by_hcd)
    refetch_record = _one_record(manifest.records, "basic_info_refetch")
    refetch = parse_rsk_catalog_basic_info(
        _observation_from_record(digest_descriptor, refetch_record, verified),
        "00CD10",
    )
    if refetch != metadata_by_hcd["00CD10"]:
        raise RskCaptureError("private capture contains rotated metadata")
    capture_time = datetime.strptime(manifest.capture_id, _CAPTURE_ID_FORMAT).replace(
        tzinfo=UTC
    )
    selected = select_active_icd10_xml(metadata_by_hcd, capture_time)
    _validate_selected_product(manifest.selected_product, selected)
    xml_record = _one_record(manifest.records, "product_xml")
    if xml_record.size_bytes != selected.size_bytes:
        raise RskCaptureError("captured product size does not match metadata")
    if manifest.totals.file_metadata_count != file_count:
        raise RskCaptureError("private file metadata total is inconsistent")
    return file_count, xml_record


def _validate_selected_product(
    captured: PrivateSelectedProduct, selected: RskCatalogFile
) -> None:
    if selected.creation_date is None:
        raise RskCaptureError("selected product creation date is missing")
    if (
        captured.uuid != str(selected.uuid)
        or captured.size_bytes != selected.size_bytes
        or captured.extension != selected.extension
        or captured.creation_date != selected.creation_date
    ):
        raise RskCaptureError("selected product does not match metadata")


def _observation_from_record(
    digest_descriptor: int,
    record: PrivateCaptureRecord,
    verified: dict[str, int],
) -> HttpObservation:
    if record.kind == "product_xml":
        raise RskCaptureError("product XML cannot be loaded as metadata")
    _validate_blob(digest_descriptor, record, verified)
    with _open_blob(digest_descriptor, record) as source:
        body = _read_stream_exact(source, record.size_bytes)
    return HttpObservation(
        source_url=record.source_url,
        body=body,
        headers=tuple(record.headers.items()),
    )


def _build_private_manifest(
    capture_id: str,
    records: list[PrivateCaptureRecord],
    resource_count: int,
    file_count: int,
    selected: PrivateSelectedProduct,
) -> PrivateCaptureManifest:
    return PrivateCaptureManifest(
        schema_version=1,
        capture_id=capture_id,
        status="complete",
        records=records,
        selected_product=selected,
        totals=PrivateCaptureTotals(
            resource_count=resource_count,
            file_metadata_count=file_count,
            captured_bytes=_captured_size(records),
        ),
    )


def _private_manifest_bytes(manifest: PrivateCaptureManifest) -> bytes:
    content = _canonical_json(manifest.model_dump(mode="json"))
    if len(content) > MAX_MANIFEST_BYTES:
        raise RskCaptureError("private capture manifest exceeds its byte bound")
    return content


def _assert_written_private_manifest(capture_descriptor: int, expected: bytes) -> None:
    actual = _read_capture_file(
        capture_descriptor, PRIVATE_MANIFEST_NAME, MAX_MANIFEST_BYTES
    )
    if actual != expected:
        raise RskCaptureError("written private capture manifest identity changed")


def _verify_capture_tree(root_descriptor: int, digests: set[str]) -> None:
    with _capture_digest_directory(root_descriptor, digests):
        pass


@contextmanager
def _capture_digest_directory(
    root_descriptor: int, digests: set[str]
) -> Generator[int]:
    expected_root = {
        "blobs",
        "temporary",
        "staging",
        PRIVATE_MANIFEST_NAME,
        PUBLIC_INDEX_NAME,
    }
    _verify_private_status(os.fstat(root_descriptor), directory=True)
    _verify_directory_names(root_descriptor, expected_root)
    with _open_private_directory_at(root_descriptor, "blobs") as blobs_descriptor:
        _verify_directory_names(blobs_descriptor, {"sha256"})
        with _open_private_directory_at(
            blobs_descriptor, "sha256"
        ) as digest_descriptor:
            _verify_directory_names(digest_descriptor, digests)
            with _open_private_directory_at(
                root_descriptor, "temporary"
            ) as temporary_descriptor:
                _verify_directory_names(temporary_descriptor, set())
            with _open_private_directory_at(
                root_descriptor, "staging"
            ) as staging_descriptor:
                _verify_directory_names(staging_descriptor, set())
            yield digest_descriptor


def _verify_directory_names(directory_descriptor: int, expected: set[str]) -> None:
    seen: set[str] = set()
    with os.scandir(directory_descriptor) as entries:
        for visit_count, entry in enumerate(entries, start=1):
            if visit_count > MAX_CAPTURE_ENTRIES:
                raise RskCaptureError("private capture entry count exceeds its bound")
            if entry.name not in expected:
                raise RskCaptureError("private capture contains an unknown entry")
            seen.add(entry.name)
    if seen != expected:
        raise RskCaptureError("private capture is missing an expected entry")


def _verify_private_status(status: os.stat_result, *, directory: bool) -> None:
    expected_type = (
        stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)
    )
    if not expected_type:
        raise RskCaptureError("private capture entry type is invalid")
    expected_mode = 0o700 if directory else 0o600
    if stat.S_IMODE(status.st_mode) != expected_mode:
        raise RskCaptureError("private capture entry permissions are unsafe")
    if status.st_uid != os.geteuid():
        raise RskCaptureError("private capture entry owner is invalid")
    if not directory and status.st_nlink != 1:
        raise RskCaptureError("private capture file has an unsafe link count")


@contextmanager
def _create_capture_root(capture_id: str) -> Generator[_CaptureDirectory]:
    with _open_capture_base(create=True) as parent_descriptor:
        try:
            os.mkdir(capture_id, mode=0o700, dir_fd=parent_descriptor)
        except FileExistsError as error:
            raise RskCaptureError("private capture output already exists") from error
        with _open_private_directory_at(
            parent_descriptor, capture_id
        ) as capture_descriptor:
            status = os.fstat(capture_descriptor)
            directory = _CaptureDirectory(
                path=CAPTURE_ROOT / capture_id,
                name=capture_id,
                descriptor=capture_descriptor,
                parent_descriptor=parent_descriptor,
                identity=(status.st_dev, status.st_ino),
            )
            yield directory


@contextmanager
def _existing_capture_root(value: str | Path) -> Generator[_CaptureDirectory]:
    root = _repository_path(value)
    _require_within(root, CAPTURE_ROOT)
    if root.parent != CAPTURE_ROOT or _CAPTURE_ID_RE.fullmatch(root.name) is None:
        raise RskCaptureError("private capture path has an invalid layout")
    with _open_capture_base(create=False) as parent_descriptor:
        with _open_private_directory_at(
            parent_descriptor, root.name
        ) as capture_descriptor:
            status = os.fstat(capture_descriptor)
            directory = _CaptureDirectory(
                path=root,
                name=root.name,
                descriptor=capture_descriptor,
                parent_descriptor=parent_descriptor,
                identity=(status.st_dev, status.st_ino),
            )
            yield directory
            _assert_capture_identity(directory)


@contextmanager
def _open_capture_base(*, create: bool) -> Generator[int]:
    try:
        relative = CAPTURE_ROOT.relative_to(REPOSITORY_ROOT)
    except ValueError as error:
        raise RskCaptureError("capture root is outside the repository") from error
    if relative.parts != ("data", "private-fixtures", "rsk"):
        raise RskCaptureError("capture root is not the fixed private RSK root")
    with _open_absolute_directory(REPOSITORY_ROOT) as repository_descriptor:
        descriptors: list[int] = []
        parent_descriptor = repository_descriptor
        try:
            for component in relative.parts:
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=parent_descriptor)
                    except FileExistsError:
                        pass
                descriptor = _open_directory_descriptor(parent_descriptor, component)
                _verify_private_status(os.fstat(descriptor), directory=True)
                descriptors.append(descriptor)
                parent_descriptor = descriptor
            yield parent_descriptor
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


@contextmanager
def _open_absolute_directory(path: Path) -> Generator[int]:
    absolute = Path(os.path.abspath(path))
    descriptor = os.open(absolute.anchor, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for component in absolute.parts[1:]:
            next_descriptor = _open_directory_descriptor(descriptor, component)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _open_directory_descriptor(parent_descriptor: int, name: str) -> int:
    try:
        return os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
    except FileNotFoundError as error:
        raise RskCaptureError("required private capture path is missing") from error
    except OSError as error:
        raise RskCaptureError(
            "path contains a symbolic-link component or invalid entry type"
        ) from error


@contextmanager
def _open_private_directory_at(parent_descriptor: int, name: str) -> Generator[int]:
    descriptor = _open_directory_descriptor(parent_descriptor, name)
    try:
        _verify_private_status(os.fstat(descriptor), directory=True)
        yield descriptor
    finally:
        os.close(descriptor)


def _assert_capture_identity(directory: _CaptureDirectory) -> None:
    try:
        status = os.stat(
            directory.name,
            dir_fd=directory.parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError as error:
        raise RskCaptureError("private capture directory identity changed") from error
    if not stat.S_ISDIR(status.st_mode):
        raise RskCaptureError("private capture directory identity changed")
    if (status.st_dev, status.st_ino) != directory.identity:
        raise RskCaptureError("private capture directory identity changed")


def _repository_path(value: str | Path) -> Path:
    path = Path(value)
    candidate = path if path.is_absolute() else REPOSITORY_ROOT / path
    return Path(os.path.abspath(candidate))


def _require_within(path: Path, boundary: Path) -> None:
    try:
        _ = path.relative_to(boundary)
    except ValueError as error:
        raise RskCaptureError(
            "path is outside the allowed repository boundary"
        ) from error


def _read_repository_file(path: str | Path, maximum_bytes: int) -> bytes:
    absolute_path = _repository_path(path)
    _require_within(absolute_path, REPOSITORY_ROOT)
    relative = absolute_path.relative_to(REPOSITORY_ROOT)
    if not relative.parts:
        raise RskCaptureError("repository file path is invalid")
    with _open_absolute_directory(REPOSITORY_ROOT) as repository_descriptor:
        descriptors: list[int] = []
        parent_descriptor = repository_descriptor
        try:
            for component in relative.parts[:-1]:
                descriptor = _open_directory_descriptor(parent_descriptor, component)
                descriptors.append(descriptor)
                parent_descriptor = descriptor
            return _read_regular_file_at(
                parent_descriptor,
                relative.parts[-1],
                maximum_bytes,
                private=False,
            )
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)


@contextmanager
def _open_blob(
    digest_descriptor: int, record: PrivateCaptureRecord
) -> Generator[BinaryIO]:
    if re.fullmatch(r"[0-9a-f]{64}", record.sha256) is None:
        raise RskCaptureError("blob checksum grammar is invalid")
    with _open_regular_file_at(
        digest_descriptor, record.sha256, record.size_bytes, private=True
    ) as source:
        yield source


def _read_capture_file(root_descriptor: int, name: str, maximum_bytes: int) -> bytes:
    if name not in {PRIVATE_MANIFEST_NAME, PUBLIC_INDEX_NAME}:
        raise RskCaptureError("capture filename is not allowlisted")
    return _read_regular_file_at(root_descriptor, name, maximum_bytes, private=True)


def _read_regular_file_at(
    parent_descriptor: int,
    name: str,
    maximum_bytes: int,
    *,
    private: bool,
) -> bytes:
    with _open_regular_file_at(
        parent_descriptor, name, maximum_bytes, private=private
    ) as source:
        return _read_stream_bounded(source, maximum_bytes)


@contextmanager
def _open_regular_file_at(
    parent_descriptor: int,
    name: str,
    maximum_bytes: int,
    *,
    private: bool,
) -> Generator[BinaryIO]:
    if maximum_bytes < 1 or maximum_bytes > MAX_PRODUCT_BYTES:
        raise RskCaptureError("file read bound is invalid")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        raise RskCaptureError(
            "bounded file is missing, symbolic-link, or has an unsafe entry type"
        ) from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size > maximum_bytes:
            raise RskCaptureError("bounded file is invalid")
        if status.st_nlink != 1:
            raise RskCaptureError("bounded file has an unsafe link count")
        if status.st_uid != os.geteuid():
            raise RskCaptureError("bounded file owner is invalid")
        if private and stat.S_IMODE(status.st_mode) != 0o600:
            raise RskCaptureError("private file permissions are unsafe")
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            yield source
    finally:
        os.close(descriptor)


def _read_stream_bounded(source: BinaryIO, maximum_bytes: int) -> bytes:
    content = bytearray()
    while chunk := source.read(min(64 * 1024, maximum_bytes + 1 - len(content))):
        content.extend(chunk)
        if len(content) > maximum_bytes:
            raise RskCaptureError("bounded file exceeds its size limit")
    return bytes(content)


def _read_stream_exact(source: BinaryIO, expected_size_bytes: int) -> bytes:
    if expected_size_bytes < 1 or expected_size_bytes > MAX_METADATA_BYTES:
        raise RskCaptureError("metadata read size is outside its bound")
    content = _read_stream_bounded(source, expected_size_bytes)
    if len(content) != expected_size_bytes:
        raise RskCaptureError("metadata read size is inconsistent")
    return content


def _write_private_file(directory_descriptor: int, name: str, content: bytes) -> None:
    if name not in {PRIVATE_MANIFEST_NAME, PUBLIC_INDEX_NAME, "quarantine.json"}:
        raise RskCaptureError("private output filename is not allowlisted")
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=directory_descriptor,
        )
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            if output.write(content) != len(content):
                raise OSError("private capture manifest write was incomplete")
            output.flush()
        os.fsync(descriptor)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _quarantine(directory: _CaptureDirectory, error: BaseException) -> Path:
    reason = (
        "metadata_rotation"
        if isinstance(error, RskCaptureRotationError)
        else "capture_failure"
    )
    try:
        _write_private_file(
            directory.descriptor,
            "quarantine.json",
            _canonical_json(
                {
                    "schema_version": CAPTURE_SCHEMA_VERSION,
                    "status": "quarantined",
                    "reason": reason,
                }
            ),
        )
    except BaseException as marker_error:
        error.add_note(f"quarantine marker failed: {type(marker_error).__name__}")
    quarantine_name = f"{directory.name}.quarantine"
    quarantine_path = directory.path.with_name(quarantine_name)
    try:
        _ = os.stat(
            quarantine_name,
            dir_fd=directory.parent_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        pass
    else:
        error.add_note("capture quarantine output already exists")
        return directory.path
    try:
        os.rename(
            directory.name,
            quarantine_name,
            src_dir_fd=directory.parent_descriptor,
            dst_dir_fd=directory.parent_descriptor,
        )
    except BaseException as rename_error:
        error.add_note(
            f"capture quarantine rename failed: {type(rename_error).__name__}"
        )
        return directory.path
    return quarantine_path


def _append_record(
    records: list[PrivateCaptureRecord], record: PrivateCaptureRecord
) -> None:
    if len(records) >= MAX_CAPTURE_RECORDS:
        raise RskCaptureError("private capture record count exceeds its bound")
    if _captured_size(records) + record.size_bytes > MAX_CAPTURE_BYTES:
        raise RskCaptureError("private capture byte count exceeds its bound")
    records.append(record)


def _captured_size(records: list[PrivateCaptureRecord]) -> int:
    return sum(record.size_bytes for record in records)


def _media_type(headers: tuple[tuple[str, str], ...]) -> str | None:
    content_type = dict(headers).get("content-type")
    if content_type is None:
        return None
    media_type = content_type.partition(";")[0].strip().lower()
    return media_type or None


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()


def _utc_capture_time(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    return _require_aware(value).astimezone(UTC).replace(microsecond=0)


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise RskSourceDriftError("RSK timestamp must include a UTC offset")
    return value.astimezone(UTC)


def _validate_global_file_identities(
    metadata_by_hcd: Mapping[str, RskCatalogMetadata],
) -> None:
    seen: dict[UUID, str] = {}
    for hcd, metadata in metadata_by_hcd.items():
        for file in metadata.files:
            previous_hcd = seen.setdefault(file.uuid, hcd)
            if previous_hcd != hcd:
                raise RskSourceDriftError(
                    "RSK file UUID is reused across resource metadata"
                )


def _is_active(metadata: RskCatalogMetadata, captured_at: datetime) -> bool:
    start = _require_aware(metadata.valid_date_from)
    end = (
        _require_aware(metadata.valid_date_to)
        if metadata.valid_date_to is not None
        else None
    )
    return start <= captured_at and (end is None or captured_at <= end)


def _one_record(records: list[PrivateCaptureRecord], kind: str) -> PrivateCaptureRecord:
    match: PrivateCaptureRecord | None = None
    for visit_count, record in enumerate(records, start=1):
        if visit_count > MAX_CAPTURE_RECORDS:
            raise RskCaptureError("private capture record count exceeds its bound")
        if record.kind != kind:
            continue
        if match is not None:
            raise RskCaptureError("private record cardinality is invalid")
        match = record
    if match is None:
        raise RskCaptureError("private record cardinality is invalid")
    return match
