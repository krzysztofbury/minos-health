from typing import ClassVar, Self

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
