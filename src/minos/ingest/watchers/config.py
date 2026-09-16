from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import AnyHttpUrl


class RskSettings(BaseSettings):
    model_config=SettingsConfigDict(
        env_prefix="MINOS_RSK_",
        env_file=".env",
        extra="ignore",
    )

    role_catalog_url: AnyHttpUrl=("https://rsk3.ezdrowie.gov.pl/api/rsk/resource/role/UZYNLG")
    basic_info_base_url: AnyHttpUrl=("https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/")  # https://rsk3.ezdrowie.gov.pl/api/rsk/resource/basic-info/{hcd}
    file_download_base_url: AnyHttpUrl=("https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/")  # https://rsk3.ezdrowie.gov.pl/api/rsk/file/get/{uuid}

    timeout_seconds: float=20.0
    connect_timeout_seconds: float=5.0 
    user_agent: str="minos-health-rsk-watcher"

