"""Exercise tunnel-only activation, recovery and rollback with real Docker (#273).

The unit tests run the same host program against a fake Docker whose Compose
semantics the tests define. This smoke checks those semantics against Docker
Compose: an image change recreates only the tunnel, an unhealthy container on
unchanged configuration is left in place unless rollback forces recreation,
and every other service keeps its container. A stand-in image answers the
cloudflared commands, because the real healthcheck needs a connected tunnel;
staging's pull, platform, version and ingress checks stay with the real image.
"""

import argparse
import base64
import copy
import hashlib
import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import FrameType
from typing import cast

from scripts import deploy_tunnel, tunnel_host, validation_host
from scripts.deploy_validation import DeployError, parse_host_answer

ROOT = Path(__file__).resolve().parents[1]
STANDIN = ROOT / "tests/fixtures/cloudflared-standin"
VARIANTS = ("old", "new", "broken")
VERSION = "2026.10.4"


def terminate(_number: int, _frame: FrameType | None) -> None:
    raise SystemExit("tunnel deployment smoke interrupted")


def service(image: str) -> dict[str, object]:
    return {
        "image": image,
        "init": True,
        "user": tunnel_host.USER,
        "read_only": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "96m",
        "pids_limit": 64,
        "command": tunnel_host.COMMAND,
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=1m,uid=65532,gid=65532,mode=0700"],
        "healthcheck": {
            "test": [
                "CMD",
                "/usr/local/bin/cloudflared",
                *tunnel_host.COMMAND[:3],
                "ready",
            ],
            "interval": "1s",
            "timeout": "3s",
            "retries": 2,
        },
    }


def main(output: Path, tag: str) -> None:
    output.mkdir(parents=True, exist_ok=False)
    root = output.resolve()
    control = root / "control"
    # Bind mounts are read inside the container as 65532.
    control.mkdir(mode=0o755)
    control.chmod(0o755)
    images = {variant: f"{tag}:{variant}" for variant in VARIANTS}
    for variant, image in images.items():
        _ = subprocess.run(
            ["docker", "build", "--quiet", "--tag", image, "--build-arg",
             f"STANDIN_VARIANT={variant}", str(STANDIN)],
            check=True, timeout=300, capture_output=True,
        )  # fmt: skip
    mount = {
        "type": "bind",
        "source": str(control),
        "target": "/control",
        "read_only": True,
    }
    previous: dict[str, object] = {
        "services": {
            name: {**service(images["old"]), "volumes": [mount]}
            # One reference API colour beside the preserved services (#192).
            for name in ("cloudflared", *sorted(tunnel_host.PRESERVED), "reference-api")
        }
    }
    layout = validation_host.Layout(root, "minos-tunnel-smoke-" + uuid.uuid4().hex[:10])
    original = validation_host.canonical(previous)
    validation_host.write_private(layout.compose, original)
    prefix = [
        "docker",
        "compose",
        "--project-name",
        layout.project,
        "--file",
        str(layout.compose),
    ]

    def stage(candidate_image: str, marker: str) -> dict[str, object]:
        """Write the records `stage` would write, without pulling a registry image."""
        params: dict[str, object] = {
            # Records are keyed by the planned digest; the candidate Compose
            # names the local stand-in that the host actually runs.
            "image": "docker.io/cloudflare/cloudflared@sha256:" + marker * 64,
            "version": VERSION,
            "compose_sha256": hashlib.sha256(layout.compose.read_bytes()).hexdigest(),
            "root": str(root),
            "project": layout.project,
        }
        directory = tunnel_host.directory(
            layout, cast(str, params["image"]), cast(str, params["compose_sha256"])
        )
        directory.mkdir(parents=True)
        candidate = copy.deepcopy(previous)
        tunnel_host.service(candidate)["image"] = candidate_image
        validation_host.write_once(
            directory / "previous.json", layout.compose.read_bytes()
        )
        validation_host.write_once(
            directory / "candidate.json", validation_host.canonical(candidate)
        )
        validation_host.write_once(
            directory / "baseline.json",
            validation_host.canonical(tunnel_host.baseline(layout)),
        )
        validation_host.write_once(
            directory / "prepared.json", validation_host.canonical(params)
        )
        return params

    def call(command: str, params: dict[str, object]) -> dict[str, object]:
        encoded = base64.urlsafe_b64encode(json.dumps(params).encode()).decode()
        result = subprocess.run(
            [sys.executable, "-", command, encoded],
            input=deploy_tunnel.program(),
            capture_output=True,
            timeout=600,
            check=False,
        )
        return parse_host_answer(result.stdout.decode(), command, result.returncode)

    def failure(command: str, params: dict[str, object]) -> dict[str, object]:
        try:
            answer = call(command, params)
        except DeployError as error:
            return error.details
        raise AssertionError(f"{command} unexpectedly succeeded: {answer}")

    try:
        started = time.monotonic()
        boot = subprocess.run(
            [*prefix, "up", "--detach", "--wait", "--wait-timeout", "60"],
            check=False, timeout=90, capture_output=True,
        )  # fmt: skip
        if boot.returncode:
            raise RuntimeError("stand-in services did not become healthy")
        preserved = tunnel_host.baseline(layout)

        # A broken candidate: Compose recreates the tunnel for the new image,
        # health fails, and the automatic rollback restores the old image.
        details = failure("activate", stage(images["broken"], "a"))
        assert details["recovery"] == {
            "preserved": True,
            "healthy": True,
            "cloudflared_image_matches": True,
        }, details
        assert layout.compose.read_bytes() == original
        assert tunnel_host.baseline(layout) == preserved

        # The automatic rollback fails too: the old image refuses to start ready.
        (control / "refuse-old").touch(mode=0o644)
        params = stage(images["broken"], "b")
        assert failure("activate", params)["recovery"] == {"restored": False}
        assert layout.compose.read_bytes() == original
        (control / "refuse-old").unlink()
        # The premise of rollback's --force-recreate: on unchanged
        # configuration Compose keeps the unhealthy container.
        try:
            tunnel_host.up(layout, root / "plain-up.json")
        except validation_host.HostError:
            pass
        else:
            raise AssertionError("Compose recreated an unchanged unhealthy container")
        rolled_back = call("rollback", params)
        assert rolled_back["event"] == "tunnel_rolled_back", rolled_back
        assert rolled_back["cloudflared_image_matches"] is True, rolled_back
        assert tunnel_host.baseline(layout) == preserved

        # A good candidate activates, then rolls back to the original bytes.
        params = stage(images["new"], "c")
        activated = call("activate", params)
        assert activated["event"] == "tunnel_activated", activated
        assert activated["image"] == images["new"], activated
        assert tunnel_host.baseline(layout) == preserved
        assert call("rollback", params)["preserved"] is True
        assert layout.compose.read_bytes() == original
        assert tunnel_host.baseline(layout) == preserved
        print(
            json.dumps(
                {
                    "failed_activation_recovery": "passed",
                    "failed_recovery_then_forced_rollback": "passed",
                    "unchanged_unhealthy_container_kept": "passed",
                    "activation_and_rollback": "passed",
                    "preserved_containers": len(preserved),
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
        )
    finally:
        _ = subprocess.run(
            [*prefix, "down", "--volumes", "--remove-orphans"],
            check=True, timeout=90, capture_output=True,
        )  # fmt: skip
        _ = subprocess.run(
            ["docker", "image", "rm", *images.values()],
            check=False, timeout=90, capture_output=True,
        )  # fmt: skip


def cli() -> None:
    _ = signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--tag", default="minos-tunnel-standin")
    arguments = parser.parse_args()
    main(cast(Path, arguments.output), cast(str, arguments.tag))


if __name__ == "__main__":
    cli()
