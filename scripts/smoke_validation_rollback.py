"""Exercise colour activation and partial rollback with real containers.

A delayed candidate API must start beside the live one while public readiness
keeps answering (#192), and a partial rollback must stay recoverable (#162).
"""

import argparse
import base64
import copy
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import FrameType
from typing import cast

ROOT = Path(__file__).resolve().parents[1]
REVISION = "2" * 40
API_DELAY_S = 10
# Only the edge recreation may interrupt readiness, never the API startup.
MAX_OUTAGE_S = 5.0


def terminate(_number: int, _frame: FrameType | None) -> None:
    raise SystemExit("rollback smoke interrupted")


def main(output: Path, api_image: str, edge_image: str) -> None:
    sys.path.insert(0, str(ROOT))
    from scripts.check_reference_web import get_json
    from scripts.render_validation_compose import api_service
    from scripts.smoke_reference_web import docker, fixture, get_page
    from scripts.validation_host import Layout, compose_result, operational, recolour

    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    bundle = fixture(output)
    manifest = cast(
        dict[str, object], json.loads((bundle / "manifest.json").read_bytes())
    )
    digest = cast(str, manifest["database_sha256"])
    root = output / "host"
    (root / "reference").mkdir(parents=True)
    _ = shutil.move(str(bundle), root / "reference" / digest)
    project = "minos-recovery-" + uuid.uuid4().hex[:12]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = cast(tuple[str, int], probe.getsockname())[1]
    compose = root / "compose.yaml"
    services: dict[str, object] = {
        "reference-api": api_service(api_image, root / "reference" / digest, digest),
        "edge": {
            "image": edge_image,
            "user": "101:101",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=8m,uid=101,gid=101,mode=0700"],
            "mem_limit": "64m",
            "cpus": "0.50",
            "pids_limit": 64,
            "networks": {"edge": None, "reference": None},
            # A fixed port lets one poller follow readiness across the edge swap.
            "ports": [f"127.0.0.1:{port}:8080"],
            "depends_on": {"reference-api": {"condition": "service_healthy"}},
            "healthcheck": {
                "test": [
                    "CMD",
                    "wget",
                    "-q",
                    "-O",
                    "/dev/null",
                    "--header",
                    "Host: minoshealth.pl",
                    "http://127.0.0.1:8080/healthz",
                ],
                "interval": "2s",
                "timeout": "3s",
                "retries": 3,
            },
        },
    }
    for service in ("watcher", "cloudflared", "uptime-kuma"):
        # Inert sentinels establish isolation; no real credentials or upstreams.
        services[service] = {
            "image": api_image,
            "entrypoint": [
                "/build/.venv/bin/python",
                "-c",
                "import time; time.sleep(600)",
            ],
            "healthcheck": {
                "test": [
                    "CMD",
                    "/build/.venv/bin/python",
                    "-c",
                    "import os; os.kill(1, 0)",
                ],
                "interval": "2s",
                "timeout": "3s",
                "retries": 3,
            },
            "network_mode": "none",
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": "32m",
            "pids_limit": 16,
        }
    previous: dict[str, object] = {
        "services": services,
        "networks": {"reference": {"internal": True}, "edge": {}},
    }
    candidate = copy.deepcopy(previous)
    recolour(candidate, "reference-api")
    candidate_services = cast(dict[str, dict[str, object]], candidate["services"])
    entrypoint = cast(
        list[str],
        json.loads(
            docker(
                "image", "inspect", "--format", "{{json .Config.Entrypoint}}", api_image
            )
        ),
    )
    candidate_services["reference-api-blue"]["entrypoint"] = [
        "/build/.venv/bin/python",
        "-c",
        f"import os,time; time.sleep({API_DELAY_S}); os.execv({entrypoint[0]!r}, {entrypoint!r})",
    ]
    health = cast(
        dict[str, object], candidate_services["reference-api-blue"]["healthcheck"]
    )
    health.update({"start_period": "0s", "interval": "1s", "retries": 1})
    for name in ("reference-api-blue", "edge"):
        candidate_services[name]["labels"] = {"minos.test.revision": REVISION}
    _ = compose.write_text(json.dumps(candidate))
    stage = root / "validation-stage" / REVISION
    stage.mkdir(parents=True)
    _ = (stage / "compose.json").write_bytes(compose.read_bytes())
    rollback = root / "validation-rollback" / REVISION / "compose.yaml"
    rollback.parent.mkdir(parents=True)
    _ = rollback.write_text(json.dumps(previous))
    prefix = ("compose", "--project-name", project, "--file", str(compose))
    real_docker = shutil.which("docker")
    assert real_docker is not None
    shim = output / "bin"
    shim.mkdir()
    marker = output / "fault-once"
    _ = marker.write_text("owned disposable fault")
    # Intercept only Compose up, restoring the real API while leaving a real
    # dependent edge in created state. All inspect calls use the actual daemon.
    _ = (shim / "docker").write_text(f"""#!{sys.executable}
import os, pathlib, subprocess, sys
docker={real_docker!r}
args=sys.argv[1:]
marker=pathlib.Path({str(marker)!r})
if args[:1] == ['compose'] and 'up' in args and args[-1] == 'edge' and marker.exists():
    marker.unlink()
    prefix=args[:args.index('up')]
    subprocess.run([docker,*prefix,'rm','--stop','--force','edge'],check=True)
    subprocess.run([docker,*prefix,'create','edge'],check=True)
    print('injected failure after API restore PRIVATE_SENTINEL',file=sys.stderr)
    sys.exit(1)
os.execv(docker,[docker,*args])
""")
    (shim / "docker").chmod(0o755)
    params = base64.urlsafe_b64encode(
        json.dumps(
            {"root": str(root), "project": project, "revision": REVISION}
        ).encode()
    ).decode()

    def rollback_call() -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [sys.executable, "-", "rollback", params],
            input=(ROOT / "scripts/validation_host.py").read_bytes(),
            capture_output=True,
            timeout=240,
            env={**os.environ, "PATH": str(shim) + os.pathsep + os.environ["PATH"]},
        )

    base = f"http://127.0.0.1:{port}"
    outcomes: list[tuple[float, bool]] = []
    polling = threading.Event()

    def poll() -> None:
        while not polling.is_set():
            try:
                status, answer, _ = get_json(base, "/readyz")
                ready = status == 200 and answer.get("status") == "ready"
            except OSError, AssertionError, ValueError:
                ready = False
            outcomes.append((time.monotonic(), ready))
            _ = polling.wait(0.1)

    def longest_outage() -> float:
        longest, began = 0.0, None
        for moment, ready in outcomes:
            if ready:
                if began is not None:
                    longest = max(longest, moment - began)
                began = None
            elif began is None:
                began = moment
        assert began is None, "readiness did not recover"
        return longest

    try:
        _ = docker(
            *prefix,
            "up",
            "--wait",
            "--wait-timeout",
            "60",
            "watcher",
            "cloudflared",
            "uptime-kuma",
        )
        baseline = operational(Layout(root, project))
        _ = compose.write_text(json.dumps(previous))
        initial = compose_result(Layout(root, project))
        assert initial["status"] == "completed", initial
        _ = compose.write_text(json.dumps(candidate))
        poller = threading.Thread(target=poll, daemon=True)
        poller.start()
        delayed = compose_result(Layout(root, project))
        time.sleep(1)
        polling.set()
        poller.join(10)
        assert delayed["status"] == "completed", delayed
        assert "reference-api-blue" in cast(
            list[str], delayed["startup_unhealthy_seen"]
        ), delayed
        assert delayed["retired"] == ["reference-api"], delayed
        assert cast(int, delayed["duration_ms"]) >= API_DELAY_S * 1000, delayed
        outage = longest_outage()
        assert outcomes and outage < MAX_OUTAGE_S, (outage, len(outcomes))
        assert operational(Layout(root, project)) == baseline
        first = rollback_call()
        failed = cast(dict[str, object], json.loads(first.stdout))
        assert first.returncode == 2 and failed["ok"] is False, failed
        details = cast(dict[str, object], failed["details"])
        assert details["validation_health"] == {
            "edge": "created",
            "reference-api": "healthy",
            "reference-api-blue": "healthy",
        }, details
        assert details["operational"] == "unchanged"
        assert (
            "PRIVATE_SENTINEL"
            not in (stage / "rollback-operations.log").read_text()
            + first.stdout.decode()
        )
        second = rollback_call()
        succeeded = cast(dict[str, object], json.loads(second.stdout))
        assert second.returncode == 0 and succeeded["ok"] is True, succeeded
        result = cast(dict[str, object], succeeded["result"])
        assert result["validation_health"] == {
            "edge": "healthy",
            "reference-api": "healthy",
        }
        assert (
            result["identities_match"] is True and result["operational"] == "unchanged"
        )
        assert operational(Layout(root, project)) == baseline
        status, answer, _ = get_json(base, "/readyz")
        assert status == 200 and answer["status"] == "ready"
        assert get_page(base, "/")[0] == 200
        assert compose.read_bytes() == rollback.read_bytes()
        print(
            json.dumps(
                {
                    "partial_rollback": details,
                    "delayed_readiness": delayed,
                    "activation_longest_outage_s": round(outage, 2),
                    "activation_readiness_probes": len(outcomes),
                    "recovery": result,
                    "edge_http": "passed",
                    "operational_sentinels": 3,
                },
                indent=2,
            )
        )
    finally:
        _ = docker(*prefix, "down", "--volumes", "--remove-orphans")
        # The rolled-back Compose no longer defines the blue network.
        _ = docker(
            "network",
            "prune",
            "--force",
            "--filter",
            f"label=com.docker.compose.project={project}",
        )


if __name__ == "__main__":
    _previous_handler = signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument(
        "--api-image", default="minos-reference-api:validation-local"
    )
    _ = parser.add_argument(
        "--edge-image", default="minos-reference-edge:validation-local"
    )
    arguments = parser.parse_args()
    main(
        cast(Path, arguments.output),
        cast(str, arguments.api_image),
        cast(str, arguments.edge_image),
    )
