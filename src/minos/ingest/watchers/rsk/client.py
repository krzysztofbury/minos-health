from ..config import RskSettings
from .models import RskRoleUriModel, RskCatalogMetadata
import httpx


def get_default_rsk_role_catalog(
    client: httpx.Client, settings: RskSettings
) -> list[RskRoleUriModel]:
    response = client.get(str(settings.role_catalog_url))
    response.raise_for_status()

    return [RskRoleUriModel.model_validate(item) for item in response.json()]


def get_rsk_catalog_basic_info(
    client: httpx.Client, settings: RskSettings, resource: str
) -> RskCatalogMetadata:
    response = client.get(f"{settings.basic_info_base_url}{resource}")
    response.raise_for_status()

    return RskCatalogMetadata.model_validate(response.json())


def client_rsk(settings: RskSettings | None = None) -> None:
    settings = settings or RskSettings()
    with httpx.Client(
        headers={"User-Agent": settings.user_agent},
        timeout=httpx.Timeout(
            settings.timeout_seconds, connect=settings.connect_timeout_seconds
        ),
        follow_redirects=False,
    ) as client:
        catalog = get_default_rsk_role_catalog(client=client, settings=settings)

        print(catalog)

        for resource in catalog:
            metadata = get_rsk_catalog_basic_info(
                client=client, settings=settings, resource=resource.hcd
            )
            print(metadata)


client_rsk()
