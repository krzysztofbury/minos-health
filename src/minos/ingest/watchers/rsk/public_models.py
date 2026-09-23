import re
import unicodedata
from datetime import UTC, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

MAX_PUBLIC_REVISIONS = 512
MAX_PUBLIC_TIMELINE_ENTRIES = 8192
MAX_PUBLIC_JSON_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_UNIQUE_NOTICES = 128
PUBLIC_SCHEMA_VERSION = 1
RSK_FILE_ORIGIN_BASE = "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/"
_PUBLIC_TIMESTAMP_PATTERN_TEXT = (
    r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):"
    r"([0-9]{2})(?:\.([0-9]{1,6}))?Z$"
)
_PUBLIC_TIMESTAMP_PATTERN = re.compile(_PUBLIC_TIMESTAMP_PATTERN_TEXT)


def _parse_public_timestamp(value: str) -> datetime:
    match = _PUBLIC_TIMESTAMP_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("public timestamp is not a valid UTC datetime")
    year, month, day, hour, minute, second, fraction = match.groups()
    try:
        return datetime(
            int(year),
            int(month),
            int(day),
            int(hour),
            int(minute),
            int(second),
            int((fraction or "").ljust(6, "0")),
            tzinfo=UTC,
        )
    except ValueError:
        raise ValueError("public timestamp is not a valid UTC datetime") from None


def _validate_public_timestamp(value: str) -> str:
    _ = _parse_public_timestamp(value)
    return value


Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Hcd = Annotated[str, Field(pattern=r"^[0-9A-Z]{6}$")]
Uuid = Annotated[
    str,
    Field(
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
    ),
]
Timestamp = Annotated[
    str,
    Field(pattern=_PUBLIC_TIMESTAMP_PATTERN_TEXT),
    AfterValidator(_validate_public_timestamp),
]
PublicText = Annotated[str, Field(min_length=1, max_length=512)]
FileExtension = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._+-]{0,31}$")]


class PublicBoundaryModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    @field_validator("*", mode="before")
    @classmethod
    def reject_unsafe_text(cls, value: object) -> object:
        if isinstance(value, str) and any(
            unicodedata.category(character).startswith("C") for character in value
        ):
            raise ValueError("public text contains a control character")
        return value


class RskPublicObservedFile(PublicBoundaryModel):
    uuid: Uuid
    file_name: PublicText | None
    extension: FileExtension | None
    type_label: PublicText | None
    size_bytes: int | None = Field(ge=0, le=512 * 1024 * 1024)
    file_created_at: Timestamp | None
    source_archived: bool | None
    source_archived_at: Timestamp | None
    education_file: bool | None
    product_sha256: Sha256 | None
    archive_status: Literal["metadata_only", "privately_archived_verified"]

    @model_validator(mode="after")
    def validate_archive_status(self) -> Self:
        archived = self.archive_status == "privately_archived_verified"
        if archived != (self.product_sha256 is not None):
            raise ValueError("public product digest and archive status disagree")
        return self


class RskPublicObservation(PublicBoundaryModel):
    schema_version: Literal[1]
    hcd: Hcd
    resource_name: PublicText
    resource_short_name: PublicText
    publisher_name: PublicText | None
    publisher_short_name: PublicText | None
    resource_type: PublicText | None
    resource_version: PublicText
    valid_from: Timestamp
    valid_to: Timestamp | None
    metadata_observation_sha256: Sha256
    files: list[RskPublicObservedFile] = Field(max_length=512)
    notices: list[PublicText] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_file_order(self) -> Self:
        _validate_validity_interval(self.valid_from, self.valid_to)
        if self.files != sorted(self.files, key=lambda item: item.uuid):
            raise ValueError("public RSK files are not deterministically ordered")
        if len({item.uuid for item in self.files}) != len(self.files):
            raise ValueError("public RSK file UUID values are not unique")
        return self


class RskProvenanceEntry(PublicBoundaryModel):
    hcd: Hcd
    resource_name: PublicText
    resource_short_name: PublicText
    publisher_name: PublicText | None
    publisher_short_name: PublicText | None
    resource_type: PublicText | None
    resource_version: PublicText
    origin_url: Annotated[str, Field(max_length=256)]
    valid_from: Timestamp
    valid_to: Timestamp | None
    file_uuid: Uuid
    file_name: PublicText | None
    extension: FileExtension | None
    type_label: PublicText | None
    size_bytes: int | None = Field(ge=0, le=512 * 1024 * 1024)
    file_created_at: Timestamp | None
    source_archived_at: Timestamp | None
    metadata_observation_sha256: Sha256
    product_sha256: Sha256 | None
    status: Literal["current", "historical", "missing"]
    first_observed_at: Timestamp
    last_observed_at: Timestamp
    archive_status: Literal["metadata_only", "privately_archived_verified"]
    downloadable: Literal[False]
    download_url: None
    notices: list[PublicText] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_entry(self) -> Self:
        if self.origin_url != f"{RSK_FILE_ORIGIN_BASE}{self.file_uuid}":
            raise ValueError("public RSK origin URL is not canonical")
        archived = self.archive_status == "privately_archived_verified"
        if archived != (self.product_sha256 is not None):
            raise ValueError("public product digest and archive status disagree")
        _validate_validity_interval(self.valid_from, self.valid_to)
        first_observed_at = _parse_public_timestamp(self.first_observed_at)
        last_observed_at = _parse_public_timestamp(self.last_observed_at)
        if first_observed_at > last_observed_at:
            raise ValueError("public observation interval is reversed")
        return self


class RedistributionManifest(PublicBoundaryModel):
    schema_version: Literal[1]
    artifacts: list[object] = Field(max_length=0)


class RskProvenanceProjection(PublicBoundaryModel):
    schema_version: Literal[1]
    source: Literal["RSK"]
    through_state_revision: int = Field(ge=1)
    projected_at: Timestamp
    included_revisions: list[int] = Field(
        min_length=1,
        max_length=MAX_PUBLIC_REVISIONS,
    )
    history_truncated: bool
    entries: list[RskProvenanceEntry] = Field(max_length=MAX_PUBLIC_TIMELINE_ENTRIES)
    redistribution: RedistributionManifest
    notices: list[PublicText] = Field(max_length=16)

    @model_validator(mode="after")
    def validate_projection(self) -> Self:
        if self.included_revisions != sorted(set(self.included_revisions)):
            raise ValueError("public revisions are not strictly ordered")
        if self.included_revisions[-1] != self.through_state_revision:
            raise ValueError("public projection does not reach its revision fence")
        expected_entries = sorted(self.entries, key=public_entry_sort_key)
        if self.entries != expected_entries:
            raise ValueError("public entries are not deterministically ordered")
        unique_notices = set(self.notices)
        for entry in self.entries:
            unique_notices.update(entry.notices)
            if len(unique_notices) > MAX_PUBLIC_UNIQUE_NOTICES:
                raise ValueError("public unique notice count exceeds its bound")
        return self


class PublicGenerationManifest(PublicBoundaryModel):
    schema_version: Literal[1]
    source: Literal["RSK"]
    state_revision: int = Field(ge=1)
    projection_sha256: Sha256
    projection_path: Literal["api/v1/provenance/rsk.json"]
    projection_size_bytes: int = Field(ge=1, le=MAX_PUBLIC_JSON_BYTES)
    redistribution: RedistributionManifest


def public_timestamp(value: datetime | int) -> str:
    timestamp = (
        datetime.fromtimestamp(value / 1000, UTC)
        if isinstance(value, int)
        else value.astimezone(UTC)
    )
    return timestamp.isoformat().replace("+00:00", "Z")


def public_entry_sort_key(entry: RskProvenanceEntry) -> tuple[object, ...]:
    return (
        -_timestamp_microseconds(entry.last_observed_at),
        entry.hcd,
        entry.file_uuid,
        entry.metadata_observation_sha256,
        entry.product_sha256 or "",
    )


def _timestamp_microseconds(value: str) -> int:
    timestamp = _parse_public_timestamp(value)
    delta = timestamp - datetime.min.replace(tzinfo=UTC)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000 + delta.microseconds


def _validate_validity_interval(valid_from: str, valid_to: str | None) -> None:
    if valid_to is None:
        return
    if _parse_public_timestamp(valid_from) > _parse_public_timestamp(valid_to):
        raise ValueError("public validity interval is reversed")
