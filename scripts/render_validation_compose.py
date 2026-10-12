"""Render the same bounded validation services for local or production Compose."""

import argparse
import json
from pathlib import Path
from typing import Annotated, ClassVar, cast, final

from pydantic import BaseModel, ConfigDict, Field


class Deployment(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid", strict=True)
    api_image: Annotated[
        str,
        Field(
            pattern=r"^ghcr\.io/krzysztofbury/minos-health-reference-api@sha256:[a-f0-9]{64}$"
        ),
    ]
    edge_image: Annotated[
        str,
        Field(
            pattern=r"^ghcr\.io/krzysztofbury/minos-health-validation-edge@sha256:[a-f0-9]{64}$"
        ),
    ]
    source_revision: Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    database_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


def api_service(image: str, bundle: Path, digest: str) -> dict[str, object]:
    return {
        "image": image,
        "pull_policy": "missing",
        "platform": "linux/amd64",
        "restart": "unless-stopped",
        "user": "10004:10004",
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "environment": {"MINOS_REFERENCE_DATABASE_SHA256": digest},
        "volumes": [
            {
                "type": "bind",
                "source": str(bundle),
                "target": "/app/data",
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        ],
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=64m,uid=10004,gid=10004,mode=0700"],
        "networks": {"reference": None},
        "init": True,
        "cpus": "1.0",
        "mem_limit": "512m",
        "pids_limit": 64,
        "stop_grace_period": "10s",
        "logging": {"driver": "local", "options": {"max-file": "3", "max-size": "10m"}},
        "healthcheck": {
            "test": [
                "CMD",
                "/build/.venv/bin/python",
                "-c",
                "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/readyz', timeout=2).close()",
            ],
            "interval": "10s",
            "timeout": "3s",
            "retries": 3,
            # The full conditions bundle took 96-110 s in isolated VPS probes.
            "start_period": "120s",
        },
    }


def render(base: dict[str, object], deployment: Deployment) -> dict[str, object]:
    services = cast(dict[str, dict[str, object]], base["services"])
    if "reference-api" in services:
        raise ValueError("restore the prior validation deployment before changing it")
    edge = services["edge"]
    if (
        edge["image"]
        != "docker.io/nginxinc/nginx-unprivileged@sha256:b54ac358b83fc6c965793fd271839b4ea4cdb6e99895bb19618cbc2ca152d972"
    ):
        raise ValueError("unexpected maintenance edge identity")
    volumes = cast(list[dict[str, object]], edge["volumes"])
    public_volumes = [
        volume for volume in volumes if volume["target"] == "/var/lib/minos/public"
    ]
    if len(public_volumes) != 1 or public_volumes[0].get("read_only") is not True:
        raise ValueError("public metadata bind must remain read-only")
    if (
        public_volumes[0].get("type") != "bind"
        or public_volumes[0].get("source") != "/var/lib/minos/public"
    ):
        raise ValueError("public metadata bind source differs")
    edge["volumes"] = public_volumes
    edge["image"] = deployment.edge_image
    networks = cast(dict[str, object], edge["networks"])
    networks["reference"] = None
    edge["depends_on"] = {"reference-api": {"condition": "service_healthy"}}
    services["reference-api"] = api_service(
        deployment.api_image,
        Path("/opt/minos/reference") / deployment.database_sha256,
        deployment.database_sha256,
    )
    cast(list[dict[str, object]], services["reference-api"]["volumes"]).append(
        dict(public_volumes[0])
    )
    definitions = cast(dict[str, object], base["networks"])
    definitions["reference"] = {"name": "minos_reference", "internal": True}
    return base


@final
class Arguments(argparse.Namespace):
    base: Path = Path()
    deployment: Path = Path()
    output: Path = Path()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("base", type=Path)
    _ = parser.add_argument("deployment", type=Path)
    _ = parser.add_argument("output", type=Path)
    arguments = Arguments()
    _parsed_arguments = parser.parse_args(namespace=arguments)
    if arguments.base.stat().st_size > 1_048_576:
        raise ValueError("base Compose file exceeds limit")
    deployment = Deployment.model_validate_json(arguments.deployment.read_bytes())
    base = cast(dict[str, object], json.loads(arguments.base.read_bytes()))
    with arguments.output.open("x") as stream:
        _written = stream.write(json.dumps(render(base, deployment), indent=2) + "\n")
