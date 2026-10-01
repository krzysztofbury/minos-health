"""Run the validation edge and API locally with a verified reference bundle.

Uses the same images, limits and nginx configuration as the deployed
validation stack; the only local change lets the edge answer to localhost.

    uv run python scripts/run_local_site.py --bundle /path/to/bundle \
        --provenance-from https://minoshealth.pl
    uv run python scripts/run_local_site.py --down
"""

import argparse
import json
import shutil
import subprocess
import sys
import urllib.request
from http.client import HTTPResponse
from pathlib import Path
from typing import cast, final

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_validation_edge import build  # noqa: E402
from scripts.render_validation_compose import api_service  # noqa: E402

PROJECT = "minos-local-site"
STATE = Path.home() / ".cache/minos-local-site"
API_IMAGE = "minos-reference-api:validation-local"
EDGE_IMAGE = "minos-reference-edge:validation-local"


@final
class Arguments(argparse.Namespace):
    bundle: Path | None = None
    provenance_from: str | None = None
    port: int = 8080
    down: bool = False


def docker(*arguments: str) -> None:
    _ = subprocess.run(["docker", *arguments], check=True, timeout=900)


def snapshot(origin: str) -> Path:
    """Copy the public RSK/BAW projections so /zrodla has data without a watcher."""
    public = STATE / "public"
    if public.exists():
        shutil.rmtree(public)
    for source, prefix in (("rsk", public), ("baw", public / "baw")):
        target = prefix / "generations/local/api/v1/provenance" / f"{source}.json"
        target.parent.mkdir(parents=True)
        request = urllib.request.Request(
            f"{origin}/api/v1/provenance/{source}",
            headers={"User-Agent": "minos-health local-site"},
        )
        # The operator chooses the public origin; only two fixed JSON paths are read.
        with cast(
            HTTPResponse,
            urllib.request.urlopen(request, timeout=30),  # noqa: S310
        ) as response:
            _ = target.write_bytes(response.read())
        (prefix / "current").symlink_to("generations/local")
    return public


def main(arguments: Arguments) -> None:
    compose = STATE / "compose.json"
    if arguments.down:
        if compose.is_file():
            docker("compose", "--project-name", PROJECT, "--file", str(compose), "down")
        return
    if arguments.bundle is None:
        raise SystemExit("--bundle is required")
    bundle = arguments.bundle.resolve()
    manifest = cast(
        dict[str, object], json.loads((bundle / "manifest.json").read_text())
    )
    artifacts = STATE / "edge-artifacts"
    if artifacts.exists():
        shutil.rmtree(artifacts)
    STATE.mkdir(parents=True, exist_ok=True)
    build(artifacts)
    config = artifacts / "nginx.conf"
    text = config.read_text()
    marker = "        server_name minoshealth.pl;"
    if text.count(marker) != 1:
        raise SystemExit("edge server layout changed")
    _ = config.write_text(
        text.replace(marker, "        server_name minoshealth.pl localhost 127.0.0.1;")
    )
    docker(
        "build",
        "--platform",
        "linux/amd64",
        "--file",
        "packaging/api/Dockerfile",
        "--tag",
        API_IMAGE,
        str(ROOT),
    )
    docker(
        "build",
        "--platform",
        "linux/amd64",
        "--file",
        "packaging/validation-edge/Dockerfile",
        "--build-context",
        f"edge-artifacts={artifacts}",
        "--tag",
        EDGE_IMAGE,
        str(ROOT),
    )
    configuration = {
        "services": {
            "reference-api": api_service(
                API_IMAGE, bundle, cast(str, manifest["database_sha256"])
            ),
            "edge": {
                "image": EDGE_IMAGE,
                "user": "101:101",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "tmpfs": [
                    "/tmp:rw,noexec,nosuid,nodev,size=8m,uid=101,gid=101,mode=0700"
                ],
                "mem_limit": "64m",
                "cpus": "0.50",
                "pids_limit": 64,
                "networks": {"reference": None, "browser": None},
                "ports": [f"127.0.0.1:{arguments.port}:8080"],
                "depends_on": {"reference-api": {"condition": "service_healthy"}},
                "volumes": (
                    [f"{snapshot(arguments.provenance_from)}:/var/lib/minos/public:ro"]
                    if arguments.provenance_from
                    else []
                ),
            },
        },
        "networks": {"reference": {"internal": True}, "browser": {}},
    }
    _ = compose.write_text(json.dumps(configuration, indent=2))
    docker(
        "compose",
        "--project-name",
        PROJECT,
        "--file",
        str(compose),
        "up",
        "--detach",
        "--wait",
        "--wait-timeout",
        "90",
    )
    print(f"Minos Health is running at http://localhost:{arguments.port}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path)
    _ = parser.add_argument(
        "--provenance-from",
        help="Public origin whose RSK/BAW projections are copied for /zrodla.",
    )
    _ = parser.add_argument("--port", type=int, default=8080)
    _ = parser.add_argument("--down", action="store_true")
    parsed = Arguments()
    _parsed_arguments = parser.parse_args(namespace=parsed)
    main(parsed)
