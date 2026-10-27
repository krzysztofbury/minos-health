"""Build and exercise source HTML through the actual API/edge and Chromium.

Run inside agent-scratch; fixtures and containers belong to this invocation.
Only screenshots, measurements and build receipts remain in --output.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from minos.ingest.reference_artifact import build_reference_artifact
from minos.ingest.reference_manifest import DatasetManifest
from scripts.build_validation_edge import build
from scripts.render_validation_compose import api_service
from tests.integration.test_canonical_reference import canonical_manifest
from tests.integration.test_source_pages import publish, rsk
from tests.unit.test_sources_page import (
    _baw_projection,  # pyright: ignore[reportPrivateUsage]
)


def command(*args: str, timeout: int = 900) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout[-10000:] + result.stderr[-10000:])
    return result.stdout.strip()


def smoke(root: Path, output: Path, selected_bundle: Path | None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    name = "minos-citations-" + uuid.uuid4().hex[:12]
    api_image, edge_image = name + "-api", name + "-edge"
    fixture = root / "fixture"
    fixture.mkdir()
    value = canonical_manifest(fixture).model_dump(mode="json")
    value["search_profile"] = "polish-folded-1"
    bundle = root / "bundle"
    if selected_bundle is None:
        _ = build_reference_artifact(
            DatasetManifest.model_validate_json(json.dumps(value)), fixture, bundle
        )
    else:
        _ = shutil.copytree(selected_bundle, bundle)
    bundle.chmod(0o755)
    for item in bundle.iterdir():
        item.chmod(0o644)
    manifest = cast(dict[str, str], json.loads((bundle / "manifest.json").read_bytes()))
    public = root / "public"
    _ = publish(public, "RSK", rsk())
    _ = publish(public / "baw", "BAW", _baw_projection())
    nfz = cast(
        dict[str, object],
        json.loads(Path("tests/fixtures/nfz_projection.json").read_bytes()),
    )
    _ = publish(public / "nfz", "NFZ", nfz)
    edge = root / "edge"
    build(edge)
    api = api_service(api_image, bundle, manifest["database_sha256"])
    mount: dict[str, object] = {
        "type": "bind",
        "source": str(public),
        "target": "/var/lib/minos/public",
        "read_only": True,
    }
    cast(list[dict[str, object]], api["volumes"]).append(mount)
    compose = root / "compose.json"
    _ = compose.write_text(
        json.dumps(
            {
                "services": {
                    "reference-api": api,
                    "edge": {
                        "image": edge_image,
                        "user": "101:101",
                        "read_only": True,
                        "cap_drop": ["ALL"],
                        "security_opt": ["no-new-privileges:true"],
                        "tmpfs": [
                            "/tmp:rw,noexec,nosuid,nodev,size=8m,uid=101,gid=101,mode=0700"
                        ],
                        "mem_limit": "64m",
                        "cpus": "0.50",
                        "volumes": [mount],
                        "ports": ["127.0.0.1::8080"],
                        "networks": {"reference": None, "browser": None},
                        "depends_on": {
                            "reference-api": {"condition": "service_healthy"}
                        },
                    },
                },
                "networks": {"reference": {"internal": True}, "browser": {}},
            }
        )
    )
    prefix = ("docker", "compose", "--project-name", name, "--file", str(compose))
    try:
        for image, dockerfile, extra in (
            (api_image, "packaging/api/Dockerfile", ()),
            (
                edge_image,
                "packaging/validation-edge/Dockerfile",
                ("--build-context", "edge-artifacts=" + str(edge)),
            ),
        ):
            print("Building " + image, flush=True)
            log = command(
                "docker",
                "build",
                "--platform",
                "linux/amd64",
                "--file",
                dockerfile,
                "--tag",
                image,
                *extra,
                ".",
            )
            _ = (output / (image.rsplit("-", 1)[1] + "-build.log")).write_text(
                log + "\n"
            )
        _ = command(*prefix, "up", "--wait", "--wait-timeout", "180")
        port = command(*prefix, "port", "edge", "8080").rsplit(":", 1)[1]
        print(
            command(
                "node",
                "tests/browser/source-citations-browser.mjs",
                "chromium",
                f"http://minoshealth.pl:{port}",
                str(output),
                timeout=180,
            ),
            flush=True,
        )
        for route in (
            "/zrodla?private-canary=1",
            "/citations",
            "/api/v1/artifacts/x",
            "/zrodla/rsk/%2e%2e/control.sqlite3",
        ):
            code = command(
                "curl",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--path-as-is",
                "-H",
                "Host: minoshealth.pl",
                f"http://127.0.0.1:{port}" + route,
            )
            assert code == "404", (route, code)

        def request(route: str) -> tuple[str, float, str]:
            start = time.perf_counter()
            code = command(
                "curl",
                "--silent",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "-H",
                "Host: minoshealth.pl",
                f"http://127.0.0.1:{port}" + route,
            )
            return route, (time.perf_counter() - start) * 1000, code

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    request,
                    (
                        "/zrodla",
                        "/zrodla/rsk/00CD10",
                        "/zrodla/baw/43868",
                        "/zrodla/nfz",
                    )
                    * 20,
                )
            )
        assert all(item[2] == "200" for item in responses), responses
        durations = sorted(item[1] for item in responses)
        # Exercise the input boundary under the actual API memory/CPU limits,
        # while its pinned dictionary/grouping data remains loaded.
        large = rsk()
        row = cast(list[dict[str, object]], large["entries"])[0]
        rows = [
            {**row, "hcd": f"{index:06X}", "resource_name": "x" * 512}
            for index in range(8192)
        ]
        large.update(
            through_state_revision=14, included_revisions=[10, 11, 12, 14], entries=rows
        )
        while (
            len(json.dumps(large, sort_keys=True, separators=(",", ":")).encode())
            > 8 * 1024 * 1024
        ):
            rows = rows[:-32]
            large["entries"] = rows
        _ = publish(public, "RSK", large)
        with ThreadPoolExecutor(max_workers=2) as pool:
            bounded = list(pool.map(request, ("/zrodla/rsk", "/zrodla/rsk/000000") * 4))
        assert all(item[2] == "200" for item in bounded), bounded
        api_container = command(*prefix, "ps", "--quiet", "reference-api")
        memory_peak = int(
            command(
                "docker", "exec", api_container, "cat", "/sys/fs/cgroup/memory.peak"
            )
        )
        assert (
            command(
                "docker",
                "inspect",
                api_container,
                "--format",
                "{{.RestartCount}} {{.State.OOMKilled}}",
            )
            == "0 false"
        )
        _ = request("/readyz")
        receipt = {
            "api_image": command(
                "docker", "image", "inspect", api_image, "--format", "{{.Id}}"
            ),
            "edge_image": command(
                "docker", "image", "inspect", edge_image, "--format", "{{.Id}}"
            ),
            "fixture": "public metadata fixtures with "
            + (
                "synthetic canonical bundle"
                if selected_bundle is None
                else "selected pinned bundle"
            ),
            "database_sha256": manifest["database_sha256"],
            "public_metadata_mount": "read-only",
            "private_and_query_paths": "refused",
            "browser": "passed",
            "local_container_requests": len(responses),
            "concurrency": 2,
            "request_p95_ms": round(durations[int(0.95 * len(durations)) - 1], 2),
            "request_max_ms": round(max(durations), 2),
            "near_bound_entries": len(rows),
            "near_bound_requests": len(bounded),
            "near_bound_max_ms": round(max(item[1] for item in bounded), 2),
            "api_cgroup_memory_peak_bytes": memory_peak,
            "api_restarts": 0,
            "measurement_scope": "local fixture container smoke, including curl process startup; no production capacity claim",
        }
        _ = (output / "container-acceptance.json").write_text(
            json.dumps(receipt, indent=2) + "\n"
        )
        print(json.dumps(receipt), flush=True)
    except BaseException:
        print(command(*prefix, "logs", "--no-color"), flush=True)
        raise
    finally:
        _ = command(*prefix, "down", "--volumes", "--remove-orphans")
        for image in (edge_image, api_image):
            _ = subprocess.run(["docker", "image", "rm", image], capture_output=True)


def cli() -> None:
    def terminate(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt

    _ = signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--bundle", type=Path)
    arguments = parser.parse_args()
    smoke(
        Path(os.environ["AGENT_SCRATCH_DIR"]),
        cast(Path, arguments.output).resolve(),
        cast(Path | None, arguments.bundle),
    )


if __name__ == "__main__":
    cli()
