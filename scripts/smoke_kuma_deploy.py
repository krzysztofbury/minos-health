"""Exercise an Uptime Kuma release against real Kuma images and data (#274).

The unit tests run the host program against a fake Docker. This smoke runs it
against Docker Compose and real Kuma: the previous release creates its SQLite
data, a candidate that writes to that data but never becomes healthy is
recovered from the stopped copy, the pinned release activates on the same
data, and rollback returns the previous image to its own copy. The other four
services run the cloudflared stand-in and must keep their containers.
"""

import argparse
import base64
import copy
import hashlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import FrameType
from typing import cast

from scripts import deploy_kuma, kuma_host, tunnel_host, validation_host
from scripts.deploy_validation import DeployError, parse_host_answer
from scripts.smoke_tunnel_deploy import service as standin

ROOT = Path(__file__).resolve().parents[1]
STANDIN = ROOT / "tests/fixtures/cloudflared-standin"
BROKEN = ROOT / "tests/fixtures/kuma-broken"
# The release before the pinned one: 2.5.5-slim-rootless.
PREVIOUS = "docker.io/louislam/uptime-kuma@sha256:a292237a108fa8843d09bb4066e738f02e913ef893542740f80c26556601ab77"
STUB = re.compile(
    r"FROM docker\.io/louislam/uptime-kuma:([0-9.]+)-slim-rootless@(sha256:[0-9a-f]{64})"
)


def terminate(_number: int, _frame: FrameType | None) -> None:
    raise SystemExit("Uptime Kuma deployment smoke interrupted")


def kuma(image: str, data: Path) -> dict[str, object]:
    return {
        "image": image,
        "init": True,
        # The runner's own identity, so the smoke can copy what Kuma writes.
        "user": f"{os.getuid()}:{os.getgid()}",
        "read_only": True,
        "network_mode": "none",
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "mem_limit": "384m",
        "pids_limit": 128,
        "tmpfs": ["/tmp:rw,noexec,nosuid,nodev,size=16m"],
        "volumes": [{"type": "bind", "source": str(data), "target": "/app/data"}],
        "healthcheck": {
            "test": ["CMD", "/app/extra/healthcheck"],
            "interval": "2s",
            "timeout": "5s",
            "retries": 3,
            "start_period": "15s",
        },
    }


def secret(data: Path) -> str:
    """Kuma's JWT secret: generated once, so it identifies the data set."""
    with sqlite3.connect(f"file:{data / 'kuma.db'}?mode=ro", uri=True) as database:
        row = cast(
            tuple[str] | None,
            database.execute(
                "SELECT value FROM setting WHERE key = 'jwtSecret'"
            ).fetchone(),
        )
    assert row is not None
    return row[0]


def main(output: Path, tag: str) -> None:
    match = STUB.search((ROOT / "packaging/runtime-pins/Dockerfile").read_text())
    assert match is not None
    version, pinned = match.group(1), f"docker.io/louislam/uptime-kuma@{match.group(2)}"
    output.mkdir(parents=True, exist_ok=False)
    root = output.resolve()
    data = root / "uptime-kuma"
    data.mkdir(mode=0o700)
    _ = (data / "db-config.json").write_text('{"type":"sqlite"}\n')
    _ = (data / kuma_host.MARKER).write_text(PREVIOUS + "\n")
    (data / kuma_host.MARKER).chmod(0o444)
    images = {"standin": f"{tag}:standin", "broken": f"{tag}:broken"}
    for image, argument, context in (
        (images["standin"], "STANDIN_VARIANT=old", STANDIN),
        (images["broken"], f"BASE={pinned}", BROKEN),
    ):
        _ = subprocess.run(
            ["docker", "build", "--quiet", "--tag", image,
             "--build-arg", argument, str(context)],
            check=True, timeout=600, capture_output=True,
        )  # fmt: skip
    previous: dict[str, object] = {
        "services": {
            **{
                name: standin(images["standin"])
                for name in (
                    *sorted(tunnel_host.SERVICES - {kuma_host.NAME}),
                    "reference-api",
                )
            },  # fmt: skip
            kuma_host.NAME: kuma(PREVIOUS, data),
        }
    }
    layout = validation_host.Layout(root, "minos-kuma-smoke-" + uuid.uuid4().hex[:10])
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

    def params(image: str) -> dict[str, object]:
        return {
            "image": image,
            "version": version,
            "compose_sha256": hashlib.sha256(layout.compose.read_bytes()).hexdigest(),
            "root": str(root),
            "project": layout.project,
        }

    def stage_locally(candidate_image: str) -> dict[str, object]:
        """Write the records `stage` would write for a local, unpulled image."""
        values = params("docker.io/louislam/uptime-kuma@sha256:" + "c" * 64)
        target = kuma_host.directory(
            layout, cast(str, values["image"]), cast(str, values["compose_sha256"])
        )
        target.mkdir(parents=True)
        candidate = copy.deepcopy(previous)
        kuma_host.service(candidate)["image"] = candidate_image
        for name, content in (
            ("previous.json", layout.compose.read_bytes()),
            ("candidate.json", validation_host.canonical(candidate)),
            (
                "baseline.json",
                validation_host.canonical(tunnel_host.baseline(layout, kuma_host.NAME)),
            ),
            ("prepared.json", validation_host.canonical(values)),
        ):
            validation_host.write_once(target / name, content)
        return values

    def call(command: str, values: dict[str, object]) -> dict[str, object]:
        encoded = base64.urlsafe_b64encode(json.dumps(values).encode()).decode()
        result = subprocess.run(
            [sys.executable, "-", command, encoded],
            input=deploy_kuma.program(),
            capture_output=True,
            timeout=900,
            check=False,
        )
        return parse_host_answer(result.stdout.decode(), command, result.returncode)

    try:
        started = time.monotonic()
        boot = subprocess.run(
            [*prefix, "up", "--detach", "--wait", "--wait-timeout", "120"],
            check=False, timeout=180, capture_output=True,
        )  # fmt: skip
        if boot.returncode:
            raise RuntimeError("smoke services did not become healthy")
        preserved = tunnel_host.baseline(layout, kuma_host.NAME)
        identity = secret(data)

        # The candidate runs on the live data, then is replaced by the copy.
        values = stage_locally(images["broken"])
        try:
            answer = call("activate", values)
        except DeployError as error:
            recovery = error.details["recovery"]
        else:
            raise AssertionError(f"broken activation succeeded: {answer}")
        assert recovery == {
            "preserved": True,
            "healthy": True,
            "image_matches": True,
            "data_restored": True,
        }, recovery
        assert layout.compose.read_bytes() == original
        assert (data / kuma_host.MARKER).read_text() == PREVIOUS + "\n"
        assert not (data / "candidate-ran").exists()
        target = kuma_host.directory(
            layout, cast(str, values["image"]), cast(str, values["compose_sha256"])
        )
        assert [path.name for path in target.glob("replaced-*/candidate-ran")]
        assert secret(data) == identity
        assert tunnel_host.baseline(layout, kuma_host.NAME) == preserved

        # The pinned release: real staging, activation on the same data, and
        # rollback of the previous image onto its stopped copy.
        values = params(pinned)
        assert call("stage", values)["event"] == "kuma_staged"
        activated = call("activate", values)
        assert activated["event"] == "kuma_activated", activated
        assert (data / kuma_host.MARKER).read_text() == pinned + "\n"
        assert secret(data) == identity
        assert tunnel_host.baseline(layout, kuma_host.NAME) == preserved
        rolled_back = call("rollback", values)
        assert rolled_back["data_restored"] is True, rolled_back
        assert rolled_back["image_matches"] is True, rolled_back
        assert layout.compose.read_bytes() == original
        assert (data / kuma_host.MARKER).read_text() == PREVIOUS + "\n"
        assert secret(data) == identity
        assert tunnel_host.baseline(layout, kuma_host.NAME) == preserved
        print(
            json.dumps(
                {
                    "failed_activation_restored_data": "passed",
                    "activation_kept_data": "passed",
                    "rollback_restored_data": "passed",
                    "version": version,
                    "preserved_containers": len(preserved),
                    "seconds": round(time.monotonic() - started, 2),
                }
            )
        )
    finally:
        _ = subprocess.run(
            [*prefix, "down", "--volumes", "--remove-orphans"],
            check=True, timeout=120, capture_output=True,
        )  # fmt: skip
        _ = subprocess.run(
            ["docker", "image", "rm", *images.values()],
            check=False, timeout=90, capture_output=True,
        )  # fmt: skip


def cli() -> None:
    _ = signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("output", type=Path)
    _ = parser.add_argument("--tag", default="minos-kuma-smoke")
    arguments = parser.parse_args()
    main(cast(Path, arguments.output), cast(str, arguments.tag))


if __name__ == "__main__":
    cli()
