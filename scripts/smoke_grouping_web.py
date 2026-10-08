"""Exercise grouping through actual immutable API and edge images, using artificial facts."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast, final

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_reference_web import get_json  # noqa: E402
from scripts.render_validation_compose import api_service  # noqa: E402
from scripts.smoke_reference_web import docker  # noqa: E402


def smoke(
    output: Path, bundle: Path | None, api_image: str, edge_image: str, chromium: str
) -> None:
    from tests.fixtures.grouping_interface import grouping_bundle

    with tempfile.TemporaryDirectory(prefix="minos-grouping-runtime-") as directory:
        root = Path(directory)
        runtime = grouping_bundle(root) if bundle is None else root / "bundle"
        if bundle is not None:
            _ = shutil.copytree(bundle, runtime)
        runtime.chmod(0o755)
        for item in runtime.iterdir():
            item.chmod(0o644)
        _smoke(
            output, runtime, api_image, edge_image, chromium, synthetic=bundle is None
        )


def _smoke(
    output: Path,
    bundle: Path,
    api_image: str,
    edge_image: str,
    chromium: str,
    *,
    synthetic: bool,
) -> None:
    from minos.store.reference import read_artifact_manifest
    from tests.fixtures.grouping_interface import grouping_body

    output.mkdir(parents=True, exist_ok=False)
    body = grouping_body()
    expected = "A01"
    if not synthetic:
        expected = "F72"
        body = {
            "schema_version": 1,
            "service_date": "2026-10-02",
            "reporting_month": "2026-10-01",
            "encounter": {
                "admitted_on": "2026-09-30",
                "discharged_on": "2026-10-02",
                "age_completed_years": 51,
                "hospitalization_type": "hospitalization",
                "product_reporting": "complete",
                "principal_diagnosis": {"code": "K40.9", "hospitalization_level": True},
                "stays": [
                    {
                        "identifier": "PRIVATE_ENCOUNTER_SENTINEL",
                        "department": "4500",
                        "admitted_on": "2026-09-30",
                        "discharged_on": "2026-10-02",
                    }
                ],
                "procedures": [
                    {
                        "code": "53.03",
                        "performed_on": "2026-10-01",
                        "stay_id": "PRIVATE_ENCOUNTER_SENTINEL",
                    }
                ],
            },
        }
    manifest = read_artifact_manifest(bundle)
    compose = output / "compose.json"
    _ = compose.write_text(
        json.dumps(
            {
                "services": {
                    "reference-api": api_service(
                        api_image, bundle.resolve(), manifest.database_sha256
                    ),
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
    prefix = (
        "compose",
        "--project-name",
        "minos-grouping-" + uuid.uuid4().hex[:12],
        "--file",
        str(compose),
    )
    try:
        _ = docker(*prefix, "up", "--wait", "--wait-timeout", "180")
        edge = docker(*prefix, "ps", "--quiet", "edge")
        api = docker(*prefix, "ps", "--quiet", "reference-api")
        inspected = cast(list[dict[str, object]], json.loads(docker("inspect", edge)))
        ports = cast(
            dict[str, list[dict[str, str]]],
            cast(dict[str, object], inspected[0]["NetworkSettings"])["Ports"],
        )
        port = ports["8080/tcp"][0]["HostPort"]
        base = f"http://127.0.0.1:{port}"
        status, first, cold = get_json(base, "/api/v1/group", body)
        assert status == 200, (status, first)
        assert any(
            g["group"] == expected and g["status"] == "valid"
            for g in cast(list[dict[str, object]], first["groups"])
        )
        cli = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--interactive",
                "--volume",
                f"{bundle.resolve()}:/app/data:ro",
                "--entrypoint",
                "/build/.venv/bin/minos",
                api_image,
                "group",
                "--data",
                "/app/data",
                "--json",
            ],
            input=json.dumps(body),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        assert json.loads(cli.stdout) == first
        assert (
            get_json(base, "/api/v1/group?private=PRIVATE_ENCOUNTER_SENTINEL", body)[0]
            == 404
        )
        assert get_json(base, "/api/v1/group")[0] == 405
        assert (
            get_json(
                base, "/api/v1/group", {**body, "private": "PRIVATE_ENCOUNTER_SENTINEL"}
            )[0]
            == 400
        )
        assert (
            get_json(
                base, "/api/v1/group", {"private": "PRIVATE_ENCOUNTER_SENTINEL" * 20000}
            )[0]
            == 413
        )

        def evaluate(_index: int) -> tuple[int, dict[str, object], float]:
            return get_json(base, "/api/v1/group", body)

        with ThreadPoolExecutor(max_workers=4) as executor:
            burst = list(executor.map(evaluate, range(4)))
        assert all(s in (200, 429) for s, _, _ in burst), [(s, a) for s, a, _ in burst]
        assert any(s == 200 for s, _, _ in burst)
        assert get_json(base, "/readyz")[0] == 200
        fixture = output / "artificial-case.json"
        _ = fixture.write_text(json.dumps(body))
        _ = subprocess.run(
            [
                "node",
                str(ROOT / "tests/browser/grouping-browser.mjs"),
                chromium,
                f"http://minoshealth.pl:{port}",
                str(output),
                str(fixture),
                expected,
            ],
            timeout=120,
            check=True,
            env=os.environ.copy(),
        )
        for container in (api, edge):
            logs = docker("logs", container)
            assert all(
                token not in logs
                for token in (
                    "PRIVATE_ENCOUNTER_SENTINEL",
                    "K40.9",
                    "53.03",
                    "principal_diagnosis",
                )
            )
        peak = int(
            docker(
                "exec",
                api,
                "/build/.venv/bin/python",
                "-c",
                "from pathlib import Path; print(Path('/sys/fs/cgroup/memory.peak').read_text())",
            )
        )
        assert peak < 512 * 1024 * 1024
        report = {
            "cold_ms": round(cold, 2),
            "four_concurrent": [
                {"status": s, "ms": round(ms, 2)} for s, _, ms in burst
            ],
            "database_sha256": manifest.database_sha256,
            "api_peak_bytes": peak,
            "browser": "passed",
            "log_privacy": "passed",
        }
        _ = (output / "grouping-acceptance.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        print(json.dumps(report))
    finally:
        _ = docker(*prefix, "down", "--volumes")


@final
class Arguments(argparse.Namespace):
    output: Path = Path()
    bundle: Path | None = None
    api_image: str = "minos-reference-api:grouping-local"
    edge_image: str = "minos-reference-edge:grouping-local"
    chromium: str = "chromium"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--bundle", type=Path)
    _ = parser.add_argument("--api-image", default="minos-reference-api:grouping-local")
    _ = parser.add_argument(
        "--edge-image", default="minos-reference-edge:grouping-local"
    )
    _ = parser.add_argument("--chromium", default="chromium")
    args = Arguments()
    _ = parser.parse_args(namespace=args)
    smoke(args.output, args.bundle, args.api_image, args.edge_image, args.chromium)
