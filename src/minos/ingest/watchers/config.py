from typing import Annotated, ClassVar, Self, cast, final

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from minos.ingest.watchers.control.configuration import (
    SourceConfigurationCodecError,
    SourceConfigurationCodecRegistry,
)
from minos.ingest.watchers.control.models import JsonValue


class RskConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    role_catalog_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG"
    )
    basic_info_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/"
    )
    file_download_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/"
    )

    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    metadata_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0, le=5 * 1024 * 1024)
    file_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        gt=0,
        le=512 * 1024 * 1024,
    )
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: float = Field(default=0.5, gt=0, le=30)
    retry_max_seconds: float = Field(default=30.0, gt=0, le=300)
    operation_deadline_seconds: float = Field(default=300.0, ge=0.001, le=3600)
    user_agent: str = "minos-health/0.0.1 rsk-watcher"

    @model_validator(mode="after")
    def validate_source_urls(self) -> Self:
        _validate_source_urls(self)
        return self


class BawConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    document_ids: Annotated[list[int], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: [43868]
    )
    operation_deadline_seconds: int = Field(default=1800, ge=60, le=3600)

    @model_validator(mode="after")
    def validate_document_ids(self) -> Self:
        if any(type(value) is not int or value < 1 for value in self.document_ids):
            raise ValueError("BAW document IDs must be positive integers")
        if len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("BAW document IDs must be unique")
        return self


class RskSettings(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="MINOS_RSK_",
        env_file=".env",
        extra="ignore",
        strict=True,
    )

    role_catalog_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG"
    )
    basic_info_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/"
    )
    file_download_base_url: AnyHttpUrl = AnyHttpUrl(
        "https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/"
    )

    timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    metadata_max_bytes: int = Field(default=5 * 1024 * 1024, gt=0, le=5 * 1024 * 1024)
    file_max_bytes: int = Field(
        default=512 * 1024 * 1024,
        gt=0,
        le=512 * 1024 * 1024,
    )
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_seconds: float = Field(default=0.5, gt=0, le=30)
    retry_max_seconds: float = Field(default=30.0, gt=0, le=300)
    operation_deadline_seconds: float = Field(default=300.0, ge=0.001, le=3600)
    user_agent: str = "minos-health/0.0.1 rsk-watcher"

    @model_validator(mode="after")
    def validate_source_urls(self) -> Self:
        _validate_source_urls(self)
        return self


def _validate_source_urls(configuration: RskConfiguration | RskSettings) -> None:
    configured_urls: tuple[tuple[str, AnyHttpUrl, str], ...] = (
        (
            "role_catalog_url",
            configuration.role_catalog_url,
            "/api/rsk/resource/role/UZYNLG",
        ),
        (
            "basic_info_base_url",
            configuration.basic_info_base_url,
            "/api/rsk/resource/basic-info/",
        ),
        (
            "file_download_base_url",
            configuration.file_download_base_url,
            "/api/rsk/file/get/",
        ),
    )
    for field_name, source_url, expected_path in configured_urls:
        if source_url.scheme != "https":
            raise ValueError(f"{field_name} must use HTTPS")
        if source_url.host != "rsk3.ezdrowie.gov.pl":
            raise ValueError(f"{field_name} must use the official RSK host")
        if source_url.username is not None or source_url.password is not None:
            raise ValueError(f"{field_name} must not contain user information")
        if source_url.query is not None or source_url.fragment is not None:
            raise ValueError(f"{field_name} must not contain query or fragment data")
        if source_url.path != expected_path:
            raise ValueError(f"{field_name} has an unexpected RSK path")


@final
class RskSourceConfigurationCodec:
    def canonicalize(
        self,
        configuration: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        try:
            validated = RskConfiguration.model_validate(configuration)
        except ValidationError:
            pass
        else:
            return cast(dict[str, JsonValue], validated.model_dump(mode="json"))
        raise SourceConfigurationCodecError("RSK source configuration is invalid")

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int:
        try:
            configuration = RskConfiguration.model_validate_json(
                canonical_configuration_json
            )
        except ValidationError:
            pass
        else:
            deadline_ms = int(configuration.operation_deadline_seconds * 1000)
            if 1 <= deadline_ms <= 3_600_000:
                return deadline_ms
        raise SourceConfigurationCodecError(
            "stored RSK source configuration is invalid"
        )


@final
class BawSourceConfigurationCodec:
    def canonicalize(self, configuration: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            validated = BawConfiguration.model_validate(configuration)
        except ValidationError:
            raise SourceConfigurationCodecError(
                "BAW source configuration is invalid"
            ) from None
        return cast(dict[str, JsonValue], validated.model_dump(mode="json"))

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int:
        try:
            validated = BawConfiguration.model_validate_json(
                canonical_configuration_json
            )
        except ValidationError:
            raise SourceConfigurationCodecError(
                "stored BAW configuration is invalid"
            ) from None
        return validated.operation_deadline_seconds * 1000


class NfzConfiguration(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)

    operation_deadline_seconds: int = Field(default=600, ge=60, le=900)


@final
class NfzSourceConfigurationCodec:
    def canonicalize(self, configuration: dict[str, JsonValue]) -> dict[str, JsonValue]:
        try:
            validated = NfzConfiguration.model_validate(configuration)
        except ValidationError:
            raise SourceConfigurationCodecError(
                "NFZ source configuration is invalid"
            ) from None
        return cast(dict[str, JsonValue], validated.model_dump(mode="json"))

    def claim_timeout_ms(self, canonical_configuration_json: str) -> int:
        try:
            validated = NfzConfiguration.model_validate_json(
                canonical_configuration_json
            )
        except ValidationError:
            raise SourceConfigurationCodecError(
                "stored NFZ configuration is invalid"
            ) from None
        return validated.operation_deadline_seconds * 1000


RSK_SOURCE_CONFIGURATION_CODECS = SourceConfigurationCodecRegistry(
    {"rsk": RskSourceConfigurationCodec()}
)
WATCHER_SOURCE_CONFIGURATION_CODECS = SourceConfigurationCodecRegistry(
    {
        "rsk": RskSourceConfigurationCodec(),
        "baw": BawSourceConfigurationCodec(),
        "nfz": NfzSourceConfigurationCodec(),
    }
)
