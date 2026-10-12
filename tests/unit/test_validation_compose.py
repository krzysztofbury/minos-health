from copy import deepcopy
from typing import cast

import pytest
from pydantic import ValidationError

from scripts.render_validation_compose import Deployment, render


def deployment() -> Deployment:
    return Deployment(
        api_image="ghcr.io/krzysztofbury/minos-health-reference-api@sha256:" + "a" * 64,
        edge_image="ghcr.io/krzysztofbury/minos-health-validation-edge@sha256:"
        + "b" * 64,
        source_revision="c" * 40,
        database_sha256="d" * 64,
    )


def maintenance() -> dict[str, object]:
    return {
        "name": "minos",
        "services": {
            "watcher": {
                "image": "unchanged-watcher",
                "environment": {"scheduler": "active"},
            },
            "cloudflared": {"image": "unchanged-tunnel"},
            "uptime-kuma": {"image": "unchanged-monitoring"},
            "edge": {
                "image": "docker.io/nginxinc/nginx-unprivileged@sha256:b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972",
                "volumes": [
                    {
                        "target": "/etc/nginx/nginx.conf",
                        "source": "/opt/minos/edge/nginx.conf",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "target": "/var/lib/minos/public",
                        "source": "/var/lib/minos/public",
                        "read_only": True,
                    },
                ],
                "networks": {"edge": None},
            },
        },
        "networks": {"edge": {"internal": True}},
    }


def test_validation_cutover_preserves_existing_operational_services() -> None:
    base = maintenance()
    rendered = render(deepcopy(base), deployment())
    before = cast(dict[str, dict[str, object]], base["services"])
    after = cast(dict[str, dict[str, object]], rendered["services"])
    for name in ("watcher", "cloudflared", "uptime-kuma"):
        assert after[name] == before[name]
    assert after["edge"]["volumes"] == [
        cast(list[dict[str, object]], before["edge"]["volumes"])[1]
    ]
    api = after["reference-api"]
    assert api["environment"] == {"MINOS_REFERENCE_DATABASE_SHA256": "d" * 64}
    assert (
        api["image"]
        == "ghcr.io/krzysztofbury/minos-health-reference-api@sha256:" + "a" * 64
    )
    assert api["volumes"] == [
        {
            "type": "bind",
            "source": "/opt/minos/reference/" + "d" * 64,
            "target": "/app/data",
            "read_only": True,
            "bind": {"create_host_path": False},
        },
        {
            "type": "bind",
            "target": "/var/lib/minos/public",
            "source": "/var/lib/minos/public",
            "read_only": True,
        },
    ]
    assert api["read_only"] is True
    assert api["user"] == "10004:10004"
    assert api["mem_limit"] == "512m"
    assert set(api) & {"ports", "privileged", "group_add"} == set()
    assert api["networks"] == {"reference": None}
    assert (
        cast(dict[str, dict[str, object]], rendered["networks"])["reference"][
            "internal"
        ]
        is True
    )


def test_validation_cutover_rejects_unexpected_or_already_active_base() -> None:
    base = maintenance()
    services = cast(dict[str, dict[str, object]], base["services"])
    services["edge"]["image"] = "mutable:latest"
    with pytest.raises(ValueError, match="edge identity"):
        _ = render(base, deployment())
    with pytest.raises(ValueError, match="restore the prior"):
        _ = render(render(maintenance(), deployment()), deployment())
    base = maintenance()
    services = cast(dict[str, dict[str, object]], base["services"])
    cast(list[dict[str, object]], services["edge"]["volumes"])[1]["read_only"] = False
    with pytest.raises(ValueError, match="read-only"):
        _ = render(base, deployment())


def test_validation_deployment_requires_exact_owned_image_identities() -> None:
    values = cast(dict[str, object], deployment().model_dump())
    for invalid in (
        "ghcr.io/krzysztofbury/minos-health-reference-api:latest",
        "ghcr.io/untrusted/api@sha256:" + "a" * 64,
    ):
        with pytest.raises(ValidationError):
            _ = Deployment.model_validate({**values, "api_image": invalid})
