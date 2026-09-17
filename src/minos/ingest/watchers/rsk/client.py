from os import write

import httpx
import hashlib
from typing import BinaryIO
from ..config import RskSettings
from .models import RskRoleUriModel, RskCatalogMetadata
from uuid import UUID

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


def get_rsk_catalog_item_file(
    client: httpx.Client, settings: RskSettings, file_uuid: UUID, destination: BinaryIO
):
    sha256 = hashlib.sha256()
    receibed_bytes = 0 

    with client.stream("GET". url) as response:
        response.raise_for_status()

        for chunk in response.iter_bytes():
            destination.write(chunk)
            sha256.update(chunk)
            received_bytes += len(chunk)

    # if received_bytes != file.size_bytes:
    #   raise ValueError("RSK download size does not match metadata")

    return sha256.hexdigest(), received_bytes


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

        sha256, received_bytes = :write()get_rsk_catalog_item_file(client=client, settings=settings, file_uuid="bf7b563c-2823-41db-a41d-94f7f5ac8418", destination=


client_rsk()
