from datetime import datetime
from typing import Annotated, ClassVar, Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SHORT_TEXT_MAX_LENGTH = 512
DESCRIPTION_MAX_LENGTH = 100_000
RESOURCE_FILES_MAX_COUNT = 512
EDUCATION_LINKS_MAX_COUNT = 128
RSK_XML_EXTENSION = "xml"
RSK_ARCHIVED_HEADER_VALUE_LIMITS: Final = {
    "content-type": 256,
    "content-length": 20,
    "content-encoding": 64,
    "date": 128,
    "etag": 512,
    "last-modified": 128,
}
MAX_RSK_ARCHIVED_HEADER_VALUE_CHARACTERS = max(
    RSK_ARCHIVED_HEADER_VALUE_LIMITS.values()
)

ShortText = Annotated[str, Field(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH)]
NullableShortText = Annotated[
    str | None, Field(min_length=1, max_length=SHORT_TEXT_MAX_LENGTH)
]
NullablePossiblyEmptyShortText = Annotated[
    str | None, Field(max_length=SHORT_TEXT_MAX_LENGTH)
]
NullableDescription = Annotated[str | None, Field(max_length=DESCRIPTION_MAX_LENGTH)]
Hcd = Annotated[str, Field(min_length=6, max_length=6, pattern=r"^[0-9A-Z]+$")]


def is_valid_rsk_archived_header(name: str, value: str) -> bool:
    maximum_characters = RSK_ARCHIVED_HEADER_VALUE_LIMITS.get(name)
    return (
        maximum_characters is not None
        and len(value) <= maximum_characters
        and all(" " <= character <= "~" for character in value)
    )


class RskModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)


class RskRoleUriModel(RskModel):
    ordinal_number: int = Field(alias="ordinalNumber", ge=0, le=10_000)
    hcd: Hcd = Field(alias="hcd")
    name: ShortText = Field(alias="name")
    description: NullableDescription = Field(alias="description")
    version: ShortText = Field(alias="version")
    organization_name: NullableShortText = Field(alias="organizationName")
    creation_date: datetime = Field(alias="creationDate")
    resource_type: ShortText = Field(alias="resourceType")


class RskCatalogPublisher(RskModel):
    uuid: UUID = Field(alias="uuid")
    name: ShortText = Field(alias="name")
    short_name: ShortText = Field(alias="shortName")
    address_country: ShortText = Field(alias="addressCountry")
    address_city: ShortText = Field(alias="addressCity")
    address_street: ShortText = Field(alias="addressStreet")
    address_building: NullableShortText = Field(alias="addressBuilding")
    address_flat: NullableShortText = Field(alias="addressFlat")
    address_postcode: NullableShortText = Field(alias="addressPostcode")
    contact_email: NullableShortText = Field(alias="contactEmail")
    contact_phone: NullableShortText = Field(alias="contactPhone")
    contact_fax: NullableShortText = Field(alias="contactFax")
    nip: NullableShortText = Field(alias="nip")
    regon: NullableShortText = Field(alias="regon")
    url: Annotated[str | None, Field(min_length=1, max_length=4096)] = Field(
        alias="url"
    )
    contact_person: NullableShortText = Field(alias="contactPerson")


class RskEducationLink(RskModel):
    description: ShortText = Field(alias="description")
    url: Annotated[str, Field(min_length=1, max_length=4096)] = Field(alias="url")


class RskCatalogFile(RskModel):
    uuid: UUID = Field(alias="uuid")
    file: NullableShortText = Field(alias="file")
    file_name: NullableShortText = Field(alias="fileName")
    size_bytes: int | None = Field(alias="sizeBytes", ge=0, le=512 * 1024 * 1024)
    creation_date: datetime | None = Field(alias="creationDate")
    extension: NullableShortText = Field(alias="extension")
    extension_name: NullableShortText = Field(alias="extensionName")
    description: NullableDescription = Field(alias="description")
    archived: bool | None = Field(alias="archived")
    version: NullableShortText = Field(alias="version")
    archivization_date: datetime | None = Field(alias="archivizationDate")
    education_file: bool | None = Field(alias="educationFile")

    @property
    def file_change_trigger(self) -> tuple[UUID, datetime, int] | None:
        if self.creation_date is None or self.size_bytes is None:
            return None
        return (self.uuid, self.creation_date, self.size_bytes)


class RskCatalogMetadata(RskModel):
    hcd: Hcd = Field(alias="hcd")
    name: ShortText = Field(alias="name")
    short_name: ShortText = Field(alias="shortName")
    description: NullableDescription = Field(alias="description")
    valid_date_from: datetime = Field(alias="validDateFrom")
    valid_date_to: datetime | None = Field(alias="validDateTo")
    version: ShortText = Field(alias="version")
    publisher: RskCatalogPublisher | None = Field(alias="publisher")
    files: list[RskCatalogFile] = Field(
        alias="files", max_length=RESOURCE_FILES_MAX_COUNT
    )
    specify_download: bool | None = Field(alias="specifyDownload")
    get_as_rest: bool | None = Field(alias="getAsRest")
    resource_type: NullableShortText = Field(alias="resourceType")
    language: NullableShortText = Field(alias="language")
    fhir_or_rest_api_version: NullablePossiblyEmptyShortText = Field(
        alias="fhirOrRestApiVersion"
    )
    education_links: list[RskEducationLink] | None = Field(
        alias="educationLinks", max_length=EDUCATION_LINKS_MAX_COUNT
    )
