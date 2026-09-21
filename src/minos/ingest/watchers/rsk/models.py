from datetime import datetime
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RskRoleUriModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    ordinal_number: int = Field(alias="ordinalNumber")
    hcd: str = Field(alias="hcd", min_length=6, max_length=6, pattern=r"^[0-9A-Z]+$")
    name: str = Field(alias="name")
    description: str | None = Field(default=None, alias="description")
    version: str = Field(alias="version")
    organization_name: str | None = Field(default=None, alias="organizationName")
    creation_date: datetime = Field(alias="creationDate")
    resource_type: str = Field(alias="resourceType")


class RskCatalogPublisher(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    uuid: UUID = Field(alias="uuid")
    name: str = Field(alias="name")
    short_name: str = Field(alias="shortName")
    address_country: str = Field(alias="addressCountry")
    address_city: str = Field(alias="addressCity")
    address_street: str = Field(alias="addressStreet")
    address_building: str | None = Field(default=None, alias="addressBuilding")
    address_flat: str | None = Field(default=None, alias="addressFlat")
    address_postcode: str | None = Field(default=None, alias="addressPostcode")
    contact_email: str | None = Field(default=None, alias="contactEmail")
    contact_phone: str | None = Field(default=None, alias="contactPhone")
    contact_fax: str | None = Field(default=None, alias="contactFax")
    nip: str | None = Field(default=None, alias="nip")
    regon: str | None = Field(default=None, alias="regon")
    url: str | None = Field(default=None, alias="url")
    contact_person: str | None = Field(default=None, alias="contactPerson")


class RskCatalogFile(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    uuid: UUID = Field(alias="uuid")
    file: str | None = Field(default=None, alias="file")
    file_name: str | None = Field(default=None, alias="fileName")
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    creation_date: datetime | None = Field(default=None, alias="creationDate")
    extension: str | None = Field(default=None, alias="extension")
    extension_name: str | None = Field(default=None, alias="extensionName")
    description: str | None = Field(default=None, alias="description")
    archived: bool | None = Field(default=False, alias="archived")
    version: str | None = Field(default=None, alias="version")
    archivization_date: datetime | None = Field(default=None, alias="archivizationDate")
    education_file: bool | None = Field(default=False, alias="educationFile")

    @property
    def file_change_trigger(self) -> tuple[UUID, datetime, int] | None:
        if self.creation_date is None or self.size_bytes is None:
            return None

        return (self.uuid, self.creation_date, self.size_bytes)


class RskCatalogMetadata(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    hcd: str = Field(alias="hcd", min_length=6, max_length=6, pattern=r"^[0-9A-Z]+$")
    name: str = Field(alias="name")
    short_name: str = Field(alias="shortName")
    description: str | None = Field(default=None, alias="description")
    valid_date_from: datetime = Field(alias="validDateFrom")
    valid_date_to: datetime | None = Field(default=None, alias="validDateTo")
    version: str = Field(alias="version")
    publisher: RskCatalogPublisher | None
    files: list[RskCatalogFile] = Field(default_factory=list, alias="files")
    specify_download: bool | None = Field(default=False, alias="specifyDownload")
    get_as_rest: bool | None = Field(default=False, alias="getAsRest")
    resource_type: str | None = Field(default=None, alias="resourceType")
    language: str | None = Field(default=None, alias="language")
    fhir_or_rest_api_version: str | None = Field(
        default=None, alias="fhirOrRestApiVersion"
    )
    education_links: list[dict[str, object]] | None = Field(
        default_factory=list, alias="educationLinks"
    )
