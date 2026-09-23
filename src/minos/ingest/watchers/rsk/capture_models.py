from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from minos.ingest.watchers.rsk.models import (
    MAX_RSK_ARCHIVED_HEADER_VALUE_CHARACTERS,
    RSK_ARCHIVED_HEADER_VALUE_LIMITS,
    RSK_XML_EXTENSION,
)

CAPTURE_SCHEMA_VERSION = 1
MAX_CAPTURE_RESOURCES = 128
MAX_CAPTURE_FILES = 4096
MAX_CAPTURE_RECORDS = MAX_CAPTURE_RESOURCES + 3
MAX_CAPTURE_BYTES = 96 * 1024 * 1024
MAX_METADATA_BYTES = 5 * 1024 * 1024
MAX_PRODUCT_BYTES = 64 * 1024 * 1024
CAPTURE_ID_PATTERN = r"^[0-9]{8}T[0-9]{6}Z$"
HCD_PATTERN = r"^[0-9A-Z]{6}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
_CAPTURE_ID_FORMAT = "%Y%m%dT%H%M%SZ"
_KIND_ORDER = {
    "role_catalog": 0,
    "basic_info": 1,
    "product_xml": 2,
    "basic_info_refetch": 3,
}

CaptureId = Annotated[str, Field(pattern=CAPTURE_ID_PATTERN)]
Hcd = Annotated[str, Field(pattern=HCD_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class CaptureBoundaryModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)


class PrivateCaptureRecord(CaptureBoundaryModel):
    kind: Literal["role_catalog", "basic_info", "product_xml", "basic_info_refetch"]
    hcd: Hcd | None
    source_url: Annotated[str, Field(min_length=1, max_length=4096)]
    sha256: Sha256
    size_bytes: int = Field(ge=1, le=MAX_PRODUCT_BYTES)
    status_code: Literal[200]
    media_type: Literal[
        "application/json", "application/octet-stream", "application/xml", "text/xml"
    ]
    headers: dict[
        Annotated[str, Field(min_length=1, max_length=64)],
        Annotated[str, Field(max_length=MAX_RSK_ARCHIVED_HEADER_VALUE_CHARACTERS)],
    ] = Field(max_length=len(RSK_ARCHIVED_HEADER_VALUE_LIMITS))

    @model_validator(mode="after")
    def validate_kind_shape(self) -> Self:
        is_metadata = self.kind != "product_xml"
        if is_metadata and self.size_bytes > MAX_METADATA_BYTES:
            raise ValueError("metadata record exceeds its size bound")
        if is_metadata and self.media_type != "application/json":
            raise ValueError("metadata record media type is invalid")
        if not is_metadata and self.media_type not in {
            "application/octet-stream",
            "application/xml",
            "text/xml",
        }:
            raise ValueError("product record media type is invalid")
        if self.kind == "role_catalog" and self.hcd is not None:
            raise ValueError("role catalog must not carry an HCD")
        if self.kind != "role_catalog" and self.hcd is None:
            raise ValueError("resource record requires an HCD")
        if self.kind in {"product_xml", "basic_info_refetch"} and self.hcd != "00CD10":
            raise ValueError("scoped record must identify 00CD10")
        return self


class PrivateSelectedProduct(CaptureBoundaryModel):
    hcd: Literal["00CD10"]
    uuid: Annotated[
        str,
        Field(
            pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
        ),
    ]
    creation_date: datetime
    size_bytes: int = Field(ge=1, le=MAX_PRODUCT_BYTES)
    extension: Annotated[str, Field(pattern=f"^{RSK_XML_EXTENSION}$")]


class PrivateCaptureTotals(CaptureBoundaryModel):
    resource_count: int = Field(ge=1, le=MAX_CAPTURE_RESOURCES)
    file_metadata_count: int = Field(ge=1, le=MAX_CAPTURE_FILES)
    captured_bytes: int = Field(ge=1, le=MAX_CAPTURE_BYTES)


class PrivateCaptureManifest(CaptureBoundaryModel):
    schema_version: Literal[1]
    capture_id: CaptureId
    status: Literal["complete"]
    records: list[PrivateCaptureRecord] = Field(
        min_length=4, max_length=MAX_CAPTURE_RECORDS
    )
    selected_product: PrivateSelectedProduct
    totals: PrivateCaptureTotals

    @model_validator(mode="after")
    def validate_totals_and_cardinality(self) -> Self:
        _validate_capture_id(self.capture_id)
        basic_records = [item for item in self.records if item.kind == "basic_info"]
        if len({item.hcd for item in basic_records}) != len(basic_records):
            raise ValueError("basic-info HCD values must be unique")
        for kind in ("role_catalog", "product_xml", "basic_info_refetch"):
            if sum(item.kind == kind for item in self.records) != 1:
                raise ValueError("private record cardinality is invalid")
        if len(self.records) != len(basic_records) + 3:
            raise ValueError("private record count is inconsistent")
        if self.totals.resource_count != len(basic_records):
            raise ValueError("private resource total is inconsistent")
        if self.totals.captured_bytes != sum(item.size_bytes for item in self.records):
            raise ValueError("private byte total is inconsistent")
        return self


class PublicChecksumRecord(CaptureBoundaryModel):
    kind: Literal["role_catalog", "basic_info", "product_xml", "basic_info_refetch"]
    hcd: Hcd | None = None
    sha256: Sha256
    size_bytes: int = Field(ge=1, le=MAX_PRODUCT_BYTES)
    status_code: Literal[200]
    media_type: Literal[
        "application/json", "application/octet-stream", "application/xml", "text/xml"
    ]

    @model_validator(mode="after")
    def validate_kind_shape(self) -> Self:
        is_metadata = self.kind != "product_xml"
        if is_metadata and self.size_bytes > MAX_METADATA_BYTES:
            raise ValueError("public metadata record exceeds its size bound")
        if is_metadata and self.media_type != "application/json":
            raise ValueError("public metadata media type is invalid")
        if not is_metadata and self.media_type not in {
            "application/octet-stream",
            "application/xml",
            "text/xml",
        }:
            raise ValueError("public product media type is invalid")
        if self.kind == "role_catalog" and self.hcd is not None:
            raise ValueError("public role catalog must not carry an HCD")
        if self.kind != "role_catalog" and self.hcd is None:
            raise ValueError("public resource record requires an HCD")
        if self.kind in {"product_xml", "basic_info_refetch"} and self.hcd != "00CD10":
            raise ValueError("public scoped record must identify 00CD10")
        return self


class PublicChecksumTotals(CaptureBoundaryModel):
    response_count: int = Field(ge=4, le=MAX_CAPTURE_RECORDS)
    resource_count: int = Field(ge=1, le=MAX_CAPTURE_RESOURCES)
    file_metadata_count: int = Field(ge=1, le=MAX_CAPTURE_FILES)
    product_file_count: Literal[1]
    captured_bytes: int = Field(ge=1, le=MAX_CAPTURE_BYTES)


class PublicChecksumIndex(CaptureBoundaryModel):
    schema_version: Literal[1]
    capture_id: CaptureId
    source: Literal["RSK"]
    status: Literal["complete"]
    records: list[PublicChecksumRecord] = Field(
        min_length=4, max_length=MAX_CAPTURE_RECORDS
    )
    totals: PublicChecksumTotals

    @model_validator(mode="after")
    def validate_totals_cardinality_and_order(self) -> Self:
        _validate_capture_id(self.capture_id)
        basic_records = [item for item in self.records if item.kind == "basic_info"]
        if len({item.hcd for item in basic_records}) != len(basic_records):
            raise ValueError("public basic-info HCD values must be unique")
        for kind in ("role_catalog", "product_xml", "basic_info_refetch"):
            if sum(item.kind == kind for item in self.records) != 1:
                raise ValueError("public record cardinality is invalid")
        if len(self.records) != len(basic_records) + 3:
            raise ValueError("public record count is inconsistent")
        expected_order = sorted(
            self.records,
            key=lambda item: (_KIND_ORDER[item.kind], item.hcd or "", item.sha256),
        )
        if self.records != expected_order:
            raise ValueError("public records are not deterministically ordered")
        if self.totals.response_count != len(self.records):
            raise ValueError("public response total is inconsistent")
        if self.totals.resource_count != len(basic_records):
            raise ValueError("public resource total is inconsistent")
        if self.totals.captured_bytes != sum(item.size_bytes for item in self.records):
            raise ValueError("public byte total is inconsistent")
        metadata_00 = [item for item in basic_records if item.hcd == "00CD10"]
        refetch = [item for item in self.records if item.kind == "basic_info_refetch"]
        if len(metadata_00) != 1:
            raise ValueError("public index requires one 00CD10 metadata record")
        if (
            metadata_00[0].sha256 != refetch[0].sha256
            or metadata_00[0].size_bytes != refetch[0].size_bytes
        ):
            raise ValueError("public 00CD10 refetch identity is inconsistent")
        return self


def public_record_sort_key(record: PrivateCaptureRecord) -> tuple[int, str, str]:
    return (_KIND_ORDER[record.kind], record.hcd or "", record.sha256)


def _validate_capture_id(capture_id: str) -> None:
    try:
        parsed = datetime.strptime(capture_id, _CAPTURE_ID_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise ValueError("capture identifier is not a valid UTC timestamp") from error
    if parsed.strftime(_CAPTURE_ID_FORMAT) != capture_id:
        raise ValueError("capture identifier is not canonical")
