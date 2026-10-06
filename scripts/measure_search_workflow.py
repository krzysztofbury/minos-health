"""Measure a pinned full bundle through disposable packaged API/edge/Chromium."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import uuid
from pathlib import Path
from typing import cast, final

from minos.domain.reference import ReferenceError
from minos.store.artifact import dataset_identity
from minos.store.reference import read_artifact_manifest
from scripts.check_reference_web import get_json
from scripts.render_validation_compose import api_service
from scripts.smoke_reference_web import ROOT, docker


def measure(
    bundle: Path, output: Path, api_image: str, edge_image: str, chromium: str
) -> None:
    manifest = read_artifact_manifest(bundle)
    digest = manifest.database_sha256
    output.mkdir(parents=True, exist_ok=False)
    tasks = ROOT / "tests/evaluation/search-workflow-v1.json"
    configuration = {
        "services": {
            "reference-api": api_service(api_image, bundle.resolve(), digest),
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
                "pids_limit": 64,
                "networks": {"reference": None, "browser": None},
                "ports": ["127.0.0.1::8080"],
                "depends_on": {"reference-api": {"condition": "service_healthy"}},
            },
        },
        "networks": {"reference": {"internal": True}, "browser": {}},
    }
    compose = output / "compose.json"
    _ = compose.write_text(json.dumps(configuration))
    prefix = (
        "compose",
        "--project-name",
        "minos-workflow-" + uuid.uuid4().hex[:12],
        "--file",
        str(compose),
    )
    try:
        _ = docker(*prefix, "up", "--wait", "--wait-timeout", "180")
        api = docker(*prefix, "ps", "--quiet", "reference-api")
        edge = docker(*prefix, "ps", "--quiet", "edge")
        inspected = cast(list[dict[str, object]], json.loads(docker("inspect", edge)))
        settings = cast(dict[str, object], inspected[0]["NetworkSettings"])
        ports = cast(dict[str, list[dict[str, str]]], settings["Ports"])
        port = int(ports["8080/tcp"][0]["HostPort"])
        base = f"http://127.0.0.1:{port}"
        status, metadata, _ = get_json(base, "/api/v1/releases")
        if status != 200:
            raise ValueError("workflow metadata unavailable")
        if metadata.get("dataset_id") != dataset_identity(manifest.dataset):
            raise ValueError("workflow runtime dataset conflicts with bundle")
        source = next(
            source
            for source in cast(list[dict[str, str]], metadata["releases"])
            if source["system"] == "icd10-pl"
        )
        status, body, cold_ms = get_json(
            base,
            "/api/v1/search",
            {
                "system": "icd10-pl",
                "release_id": source["release_id"],
                "query": "zawal serca",
                "limit": 10,
            },
        )
        if status != 200 or not body["results"]:
            raise ValueError("cold workflow search failed")
        _ = subprocess.run(
            [
                "node",
                str(ROOT / "tests/browser/search-workflow-browser.mjs"),
                chromium,
                f"http://minoshealth.pl:{port}/",
                str(output),
                str(tasks),
            ],
            timeout=360,
            check=True,
        )
        for name in ("filter-preferences-browser.mjs",):
            _ = subprocess.run(
                [
                    "node",
                    str(ROOT / "tests/browser" / name),
                    chromium,
                    f"http://minoshealth.pl:{port}/",
                    str(output),
                ],
                timeout=180,
                check=True,
            )
        search_status_counts: dict[str, int] = {}
        for container in (api, edge):
            logs = docker("logs", container)
            task_data = cast(
                dict[str, list[dict[str, str]]], json.loads(tasks.read_bytes())
            )
            for task in task_data["tasks"]:
                if task["query"] in logs:
                    raise ValueError("workflow query appeared in logs")
            if container == api:
                for line in logs.splitlines():
                    _, separator, tail = line.partition("{")
                    if not separator:
                        continue
                    event = cast(dict[str, object], json.loads("{" + tail))
                    if event.get("route") == "/api/v1/search":
                        status_code = str(event["status_code"])
                        search_status_counts[status_code] = (
                            search_status_counts.get(status_code, 0) + 1
                        )
        if not search_status_counts:
            raise ValueError("workflow search telemetry was not observed")
        peak = int(
            docker(
                "exec",
                api,
                "/build/.venv/bin/python",
                "-c",
                "from pathlib import Path; print(Path('/sys/fs/cgroup/memory.peak').read_text())",
            )
        )
        workflow = cast(
            dict[str, object], json.loads((output / "workflow.json").read_bytes())
        )
        workflow.update(
            {
                "database_sha256": digest,
                "dataset_id": dataset_identity(manifest.dataset),
                "task_fixture_sha256": hashlib.sha256(tasks.read_bytes()).hexdigest(),
                "source_revision": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], text=True
                ).strip(),
                "tracked_diff_sha256": hashlib.sha256(
                    subprocess.check_output(["git", "diff", "HEAD"])
                ).hexdigest(),
                "harness_sha256": {
                    name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                    for name in (
                        "scripts/measure_search_workflow.py",
                        "tests/browser/search-workflow-browser.mjs",
                    )
                },
                "api_image_id": docker(
                    "image", "inspect", api_image, "--format", "{{.Id}}"
                ),
                "edge_image_id": docker(
                    "image", "inspect", edge_image, "--format", "{{.Id}}"
                ),
                "platform": platform.platform(),
                "logical_cpus": os.cpu_count(),
                "api_limits": {
                    "cpus": 1,
                    "memory_mib": 512,
                    "workers": 1,
                    "pool_size": 4,
                },
                "edge_limits": {"cpus": 0.5, "memory_mib": 64},
                "first_server_text_http_ms": cold_ms,
                "api_peak_memory_bytes": peak,
                "query_free_container_logs": True,
                "api_search_status_counts": search_status_counts,
            }
        )
        _ = (output / "workflow.json").write_text(json.dumps(workflow, indent=2) + "\n")
    finally:
        _ = docker(*prefix, "down", "--volumes")


@final
class Arguments(argparse.Namespace):
    bundle: Path = Path()
    output: Path = Path()
    api_image: str = ""
    edge_image: str = ""
    chromium: str = "chromium"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--bundle", type=Path, required=True)
    _ = parser.add_argument("--output", type=Path, required=True)
    _ = parser.add_argument("--api-image", required=True)
    _ = parser.add_argument("--edge-image", required=True)
    _ = parser.add_argument("--chromium", default="chromium")
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    try:
        measure(
            args.bundle, args.output, args.api_image, args.edge_image, args.chromium
        )
    except ReferenceError, ValueError, OSError, subprocess.SubprocessError:
        parser.exit(
            2, "Local workflow observation failed; no acceptance report established.\n"
        )


if __name__ == "__main__":
    main()
